"""Configuration loading (TOML) and environment overrides.

Config is read once at startup from a ``config.toml`` file (see the repo root for
the committed defaults). Every field has a default, so a missing or partial file
still yields a usable config — but an *unknown* key is an error, on the same
principle as a typo'd query parameter: silently ignoring it looks like it worked.

The ``[engine]`` section names two independent axes, combinable freely: the
``ocr_backend`` (the inference runtime) and the default ``classifier``. The OCR
backend is not only the OCR's: it is the one answer to "what runs paddle models
on this machine", so ``build_pipeline`` hands the same value to the layout
detector (:mod:`backend.layout`). The layout model is deliberately *not* a third
axis here — it has no reason to disagree.

The models are frozen, so they are safe to share across threads and workers.
"""

from __future__ import annotations

import os
import tomllib
import typing
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

OCRBackend = Literal["paddle", "onnxruntime"]
# The model half of the engine. Unlike the OCR backend this is selectable *per
# request* (`?classifier=`), because the two are genuinely different detectors
# rather than two runtimes for one model — so what `[engine]` names here is the
# default a request gets when it does not ask.
ClassifierName = Literal["presidio", "guard-omni"]

# Frozen (immutable, hashable) and strict about unknown keys.
_STRICT = ConfigDict(frozen=True, extra="forbid")


class EngineConfig(BaseModel):
    model_config = _STRICT

    # `onnxruntime` gives the same output as native `paddle`, ~3x faster.
    ocr_backend: OCRBackend = "onnxruntime"
    classifier: ClassifierName = "presidio"
    # Minimum mean detector score for a text box (PaddleOCR's own default is 0.6).
    # It is a *mean* over the box, so a long line of thin small type dilutes it:
    # a full-width imprint footer scored just under 0.6 and was not detected at
    # all — not too faint to read (its contrast matches the item table's), just
    # too thin over too wide a box. See `backend/ocr/paddle.py`.
    det_box_thresh: Annotated[float, Field(gt=0.0, le=1.0)] = 0.5
    # Opt-in: its runtime (torch) is a separate uv group and not in the image.
    guard_omni: bool = False

    @property
    def classifiers(self) -> tuple[ClassifierName, ...]:
        """Every classifier this process offers, in `ClassifierName` order."""
        return tuple(
            n for n in typing.get_args(ClassifierName) if n != "guard-omni" or self.guard_omni
        )

    @model_validator(mode="after")
    def _default_is_enabled(self) -> EngineConfig:
        if self.classifier not in self.classifiers:
            raise ValueError(
                f"classifier {self.classifier!r} is disabled; set guard_omni = true (PII_GUARD_OMNI)"
            )
        return self


class LayoutConfig(BaseModel):
    """The layout detector behind the whole-region pass (:mod:`backend.layout`).

    A trained detector needs a checkpoint and a confidence bar, nothing else —
    which region is a header, a table or a graphic is the model's judgement, not
    a page fraction. ``[redaction].redact_regions`` turns the pass off."""

    model_config = _STRICT

    # PP-DocLayout-{S,M,L} and PP-DocLayoutV2/V3 are in the same registry and
    # swap in by name. Measured on the corpus, the small ones are not a real
    # option: -S runs 7x faster but loses the fee table on half the pages that
    # have one, -M and -L find none at all on photographed pages.
    model_name: str = "PP-DocLayout_plus-L"
    # Below the model's own 0.5 default because phone photos depress every
    # score: on the sample corpus a scanned page's fee table detects at 0.9+,
    # the photographed ones at 0.38-0.43 — invisible at 0.5. NOTE this was
    # tuned when regions only shaped reading order, where a false region cost
    # nothing; here a region draws a box, so the trade is the other way round
    # and per-class thresholds (as PP-StructureV3 uses) are the next knob.
    threshold: Annotated[float, Field(gt=0.0, le=1.0)] = 0.35
    # Prunes the near-duplicate overlapping detections a low threshold lets
    # through — without it the same region comes back 3-4 times.
    layout_nms: bool = True


class RedactionConfig(BaseModel):
    model_config = _STRICT

    fill: tuple[int, int, int] = (0, 0, 0)
    padding: Annotated[int, Field(ge=0)] = 2
    score_threshold: Annotated[float, Field(ge=0.0, le=1.0)] = 0.4
    unwarp: bool = False
    # PDF rasterization DPI, max pages accepted, and JPEG quality for the rendered
    # output (visually lossless at ~90, much smaller than PNG). The bounds are the
    # same ones the matching query parameters are held to.
    pdf_dpi: Annotated[int, Field(ge=36, le=1200)] = 200
    max_pages: Annotated[int, Field(ge=1)] = 30
    jpeg_quality: Annotated[int, Field(ge=1, le=100)] = 90
    # Blacken whole detected layout regions as well as the lines the rules and
    # the classifier flag. Config only — unlike `unwarp` this is not a query
    # parameter, so it is fixed per process like the engine.
    redact_regions: bool = True
    layout: LayoutConfig = Field(default_factory=LayoutConfig)


class ApiConfig(BaseModel):
    model_config = _STRICT

    # 30 MiB: PDF uploads and JSON page payloads are larger than single images.
    max_upload_bytes: Annotated[int, Field(ge=1)] = 30 * 1024 * 1024
    input_content_types: tuple[str, ...] = ("image/png", "image/jpeg", "application/pdf")
    max_image_pixels: Annotated[int, Field(ge=1)] = 40_000_000
    max_concurrent_per_worker: Annotated[int, Field(ge=1)] = 1


class Config(BaseModel):
    model_config = _STRICT

    engine: EngineConfig = EngineConfig()
    redaction: RedactionConfig = RedactionConfig()
    api: ApiConfig = ApiConfig()


# Env var -> the ``[section].key`` it overrides. These exist for containers, where
# editing the baked config.toml means rebuilding; anything not listed here needs a
# mounted file and ``PII_CONFIG``. Values are injected into the parsed TOML as the
# raw strings they are and validated by pydantic like any other value, so
# ``PII_UNWARP=yes|1|off`` all work and a typo fails at startup naming the field —
# don't add hand-rolled parsing here.
_ENV_OVERRIDES: dict[str, tuple[str, str]] = {
    "PII_OCR_BACKEND": ("engine", "ocr_backend"),
    "PII_CLASSIFIER": ("engine", "classifier"),
    "PII_GUARD_OMNI": ("engine", "guard_omni"),
    "PII_UNWARP": ("redaction", "unwarp"),
    "PII_REDACT_REGIONS": ("redaction", "redact_regions"),
}


def _default_config_path() -> Path:
    """``$PII_CONFIG`` if set, else ``config.toml`` in the repo root."""
    env = os.environ.get("PII_CONFIG")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "config.toml"


def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    """Load config from TOML, filling any missing field with its default.

    The variables in :data:`_ENV_OVERRIDES` win over the file. Anything unusable —
    an unknown key, an out-of-range number, an OCR backend that doesn't exist, a
    value that is not a boolean — raises here, so a bad config fails at startup
    rather than on the first request.

    Note what ``PII_UNWARP`` does and does not do. ``unwarp`` is *also* a query
    parameter and a CLI flag, and the config value is their **default**, so
    ``PII_UNWARP=false`` stops the unwarper running for callers that say nothing —
    a request with ``?unwarp=true`` still gets one. ``PII_REDACT_REGIONS`` has no
    wire name, so it is absolute.
    """
    cfg_path = Path(path) if path is not None else _default_config_path()
    data: dict = {}
    if cfg_path.is_file():
        with cfg_path.open("rb") as fh:
            data = tomllib.load(fh)

    for env_name, (section, key) in _ENV_OVERRIDES.items():
        if value := os.environ.get(env_name):
            data[section] = {**data.get(section, {}), key: value}

    return Config.model_validate(data)
