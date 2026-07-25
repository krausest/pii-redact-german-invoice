"""Request-shape validation for the two endpoints: query options and the
``/api/assemble`` body.

Nothing is configurable through the ``/api/redact`` body — it is a raw file — so
options have exactly one home, the query string. Parsing is deliberately strict:
an unrecognised parameter is an error rather than a silent no-op, because a typo
(``?unwrap=false``) would otherwise look like it worked.

Query names are hyphenated (``json-output``, ``jpeg-quality``) and the underscored
field names are **not** accepted as an alternative spelling — one wire name per
option. Every option a server default exists for is required here and supplied
from :class:`~backend.config.Config` by ``from_query``, so defaults live in the
config and nowhere else.

Everything raises ``ValueError`` with a one-line message ready to become an HTTP
``400`` detail; :mod:`backend.api` never has to know about pydantic.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Literal, Self

from pydantic import Base64Bytes, BaseModel, ConfigDict, Field, ValidationError

from backend.config import Config

Quality = Annotated[int, Field(ge=1, le=100)]
Dpi = Annotated[int, Field(ge=36, le=1200)]
# A box is exactly four integers, [x0, y0, x1, y1]. Spelled as a bounded list
# rather than a 4-tuple so a short box reports "should have at least 4 items"
# instead of pydantic's per-index "Field required".
BoxList = Annotated[list[int], Field(min_length=4, max_length=4)]


def _detail(exc: ValidationError, valid: list[str] | None = None) -> str:
    """Flatten a pydantic error into the single sentence the API reports.

    ``valid`` is the list of accepted query names; when given, unknown keys are
    collected into one message that names them and lists what was expected.
    """
    unknown: list[str] = []
    parts: list[str] = []
    for err in exc.errors():
        where = ".".join(str(p) for p in err["loc"]) or "body"
        if err["type"] == "extra_forbidden" and valid is not None:
            unknown.append(where)
        else:
            parts.append(f"{where}: {err['msg']}")
    if unknown:
        names = ", ".join(repr(name) for name in unknown)
        plural = "s" if len(unknown) > 1 else ""
        parts.insert(0, f"unknown parameter{plural} {names}; expected one of {valid}")
    return "; ".join(parts)


class _QueryModel(BaseModel):
    """Options parsed from the query string: hyphenated names, unknown ones fatal.

    ``validate_by_name`` stays off so ``?json_output=true`` is rejected like any
    other misspelling — which also means these are built through ``from_query``,
    not by keyword.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        alias_generator=lambda name: name.replace("_", "-"),
        validate_by_name=False,
        validate_by_alias=True,
    )

    @classmethod
    def wire_names(cls) -> list[str]:
        return sorted(f.alias or name for name, f in cls.model_fields.items())

    @classmethod
    def _validate(cls, defaults: Mapping[str, Any], raw: Mapping[str, Any]) -> Self:
        try:
            return cls.model_validate({**defaults, **raw})
        except ValidationError as e:
            raise ValueError(_detail(e, cls.wire_names())) from e


class RedactOptions(_QueryModel):
    """``POST /api/redact``."""

    unwarp: bool
    json_output: bool = False
    pdf_dpi: Dpi
    jpeg_quality: Quality

    @classmethod
    def from_query(cls, raw: Mapping[str, Any], config: Config) -> RedactOptions:
        red = config.redaction
        return cls._validate(
            {"unwarp": red.unwarp, "pdf-dpi": red.pdf_dpi, "jpeg-quality": red.jpeg_quality},
            raw,
        )


class AssembleOptions(_QueryModel):
    """``POST /api/assemble``."""

    format: Literal["pdf", "jpeg"] = "pdf"
    dpi: Dpi
    jpeg_quality: Quality

    @classmethod
    def from_query(cls, raw: Mapping[str, Any], config: Config) -> AssembleOptions:
        red = config.redaction
        return cls._validate({"dpi": red.pdf_dpi, "jpeg-quality": red.jpeg_quality}, raw)


class PageIn(BaseModel):
    """One page of an ``/api/assemble`` body. ``data`` is base64, decoded here;
    ``boxes`` are in that image's own pixel space; ``content_type`` is purely
    informational — the real format is whatever the bytes decode to."""

    model_config = ConfigDict(extra="forbid")

    data: Base64Bytes
    content_type: str | None = None
    boxes: list[BoxList] = []


class AssembleBody(BaseModel):
    """The ``POST /api/assemble`` request body."""

    model_config = ConfigDict(extra="forbid")

    pages: Annotated[list[PageIn], Field(min_length=1)]

    @classmethod
    def from_json(cls, data: Any) -> AssembleBody:
        try:
            return cls.model_validate(data)
        except ValidationError as e:
            raise ValueError(_detail(e)) from e
