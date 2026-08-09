"""Configuration loading (TOML) and engine-preset resolution.

Config is read once at startup from a ``config.toml`` file (see the repo root for
the committed defaults). Every field has a default, so a missing or partial file
still yields a usable config — but an *unknown* key is an error, on the same
principle as a typo'd query parameter: silently ignoring it looks like it worked.

The ``[engine]`` section is expressed as a friendly preset name (``native`` |
``onnx`` | ``gliner``) that resolves to a concrete (OCR backend, classifier) pair;
advanced users can override either axis explicitly to unlock combinations the
presets don't name. ``native`` and ``onnx`` differ only in the OCR inference
engine — both classify with Presidio.

The models are frozen, so they are safe to share across threads and workers.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

EnginePreset = Literal["native", "onnx", "gliner"]
OCRBackend = Literal["paddle", "onnxruntime"]
ClassifierName = Literal["presidio", "gliner"]

# preset name -> (ocr_backend, classifier)
ENGINE_PRESETS: dict[str, tuple[OCRBackend, ClassifierName]] = {
    "native": ("paddle", "presidio"),
    "onnx": ("onnxruntime", "presidio"),
    "gliner": ("paddle", "gliner"),
}

# Frozen (immutable, hashable) and strict about unknown keys.
_STRICT = ConfigDict(frozen=True, extra="forbid")


class EngineConfig(BaseModel):
    model_config = _STRICT

    name: EnginePreset = "native"
    # Explicit overrides; when None the preset named by ``name`` supplies them.
    ocr_backend: OCRBackend | None = None
    classifier: ClassifierName | None = None
    # Minimum mean detector score for a text box (PaddleOCR's own default is 0.6).
    # It is a *mean* over the box, so a long line of thin small type dilutes it:
    # a full-width imprint footer scored just under 0.6 and was not detected at
    # all — not too faint to read (its contrast matches the item table's), just
    # too thin over too wide a box. See `backend/ocr/paddle.py`.
    det_box_thresh: Annotated[float, Field(gt=0.0, le=1.0)] = 0.5

    def resolve(self) -> tuple[str, str]:
        """The concrete ``(ocr_backend, classifier)`` pair, applying any explicit
        overrides on top of the named preset."""
        preset_ocr, preset_clf = ENGINE_PRESETS[self.name]
        return self.ocr_backend or preset_ocr, self.classifier or preset_clf


class RegionsConfig(BaseModel):
    """Geometry of the whole-region redaction pass (:mod:`backend.regions`), as
    fractions of the page. A zero fraction switches that region off, so there is
    no per-region boolean; ``[redaction].redact_regions`` turns off all three."""

    model_config = _STRICT

    header_frac: Annotated[float, Field(ge=0.0, le=0.5)] = 0.12
    footer_frac: Annotated[float, Field(ge=0.0, le=0.5)] = 0.10
    # The sender column is looked for right of `column_x_frac` and above
    # `column_y_frac`; `gap_factor` is the vertical gap (in line heights) that ends
    # a block, which is what keeps the invoice-number table out of it.
    column_x_frac: Annotated[float, Field(ge=0.0, le=1.0)] = 0.50
    column_y_frac: Annotated[float, Field(ge=0.0, le=1.0)] = 0.50
    # Two lines join the same sender block only if they are BOTH near-touching and
    # column-aligned, each in units of the smaller line's height. Neither test
    # alone works across the samples: one page's payment table touches the block
    # (only alignment cuts it), another's is perfectly aligned (only the gap does).
    # The gap is set wide enough to bridge a blank line, not just a line spacing —
    # letterheads put one between the address and the branch below it.
    vgap_factor: Annotated[float, Field(gt=0.0, le=10.0)] = 1.2
    align_factor: Annotated[float, Field(gt=0.0, le=2.0)] = 0.4
    # The recipient address block is seeded left of `column_x_frac`, between
    # these two fractions of the page height (the DIN 5008 address-field area,
    # with slack for photographed pages). An empty window (max <= min) disables
    # the pass — the same "geometry is the toggle" convention as the bands.
    recipient_y_min_frac: Annotated[float, Field(ge=0.0, le=1.0)] = 0.05
    recipient_y_max_frac: Annotated[float, Field(ge=0.0, le=1.0)] = 0.45


class RedactionConfig(BaseModel):
    model_config = _STRICT

    fill: tuple[int, int, int] = (0, 0, 0)
    padding: Annotated[int, Field(ge=0)] = 2
    score_threshold: Annotated[float, Field(ge=0.0, le=1.0)] = 0.4
    unwarp: bool = True
    # PDF rasterization DPI, max pages accepted, and JPEG quality for the rendered
    # output (visually lossless at ~90, much smaller than PNG). The bounds are the
    # same ones the matching query parameters are held to.
    pdf_dpi: Annotated[int, Field(ge=36, le=1200)] = 200
    max_pages: Annotated[int, Field(ge=1)] = 30
    jpeg_quality: Annotated[int, Field(ge=1, le=100)] = 90
    # Blacken the letterhead/footer bands and the sender column as well as the
    # lines the rules and the classifier flag. Config only — unlike `unwarp` this
    # is not a query parameter, so it is fixed per process like the engine.
    redact_regions: bool = True
    regions: RegionsConfig = Field(default_factory=RegionsConfig)
    # Blacken QR, DataMatrix and 1D barcodes (backend.codes). A Girocode carries IBAN,
    # BIC and the account holder's name, a lab barcode the order number, so a page
    # that still scans is not redacted. One toggle covers both passes deliberately:
    # they differ in policy, not in what the reader wants turned on.
    # Config only, like `redact_regions`. One knob does not earn a sub-section the
    # way `[redaction.regions]`' six interacting fractions do; `code_margin_frac`
    # grows each box by that fraction of its own longer side, as headroom over a
    # detection that already lands on the symbol edge.
    redact_codes: bool = True
    code_margin_frac: Annotated[float, Field(ge=0.0, le=0.5)] = 0.08


class ApiConfig(BaseModel):
    model_config = _STRICT

    # 30 MiB: PDF uploads and JSON page payloads are larger than single images.
    max_upload_bytes: Annotated[int, Field(ge=1)] = 30 * 1024 * 1024
    input_content_types: tuple[str, ...] = ("image/png", "image/jpeg", "application/pdf")
    max_image_pixels: Annotated[int, Field(ge=1)] = 40_000_000
    host: str = "0.0.0.0"
    port: Annotated[int, Field(ge=1, le=65535)] = 8000
    workers: Annotated[int, Field(ge=1)] = 2
    request_timeout_s: Annotated[int, Field(ge=1)] = 120
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
    "PII_ENGINE": ("engine", "name"),
    "PII_UNWARP": ("redaction", "unwarp"),
    "PII_REDACT_REGIONS": ("redaction", "redact_regions"),
    "PII_REDACT_CODES": ("redaction", "redact_codes"),
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
    an unknown key, an out-of-range number, an engine preset that doesn't exist, a
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
