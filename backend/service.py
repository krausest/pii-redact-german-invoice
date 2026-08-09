"""The work behind the two endpoints, with no HTTP in it.

* :func:`run_redaction` — a document (a PDF's bytes or one image) plus
  :class:`~backend.options.RedactOptions` in, a :class:`Redaction` out: every page
  with the boxes found on it, and whether the document was a PDF.
* :func:`assemble` — page images plus boxes in, one file out. No models: it fills
  rectangles and packages the result, which is why a client can safely send back
  pages a human has reviewed.
* :func:`produce_output` — a :class:`Redaction` in, ``(media_type, bytes)`` out:
  the redacted file, or (``json_output``) the serialized report describing it.
  Both callers hand the result straight to their transport, so the CLI writes
  exactly the bytes the API returns.

**The coordinate rule:** boxes always belong to the page image in the same
:class:`PageResult` — computed on it, at its width and height. Never the uploaded
file's coordinate space, which differs whenever the page was dewarped or a PDF was
rasterized.

Everything here is CPU-bound and must run off the event loop.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any

from PIL import Image

from backend.config import Config
from backend.models import Box
from backend.options import AssembleOptions, RedactOptions
from backend.pdf import assemble_pdf, encode_jpeg, rasterize_pdf
from backend.pipeline import RedactionPipeline
from backend.trace import Trace

# The image media types we accept on input. Output images are always JPEG.
FORMAT_BY_MEDIA_TYPE = {"image/png": "PNG", "image/jpeg": "JPEG"}
MEDIA_TYPE_BY_FORMAT = {v: k for k, v in FORMAT_BY_MEDIA_TYPE.items()}
PDF_MEDIA_TYPE = "application/pdf"
JPEG_MEDIA_TYPE = MEDIA_TYPE_BY_FORMAT["JPEG"]
JSON_MEDIA_TYPE = "application/json"

# What to call a file holding each of the three things we can produce. The CLI
# names its outputs from this, so it never re-derives "PDF in → PDF out".
EXTENSION_BY_MEDIA_TYPE = {
    PDF_MEDIA_TYPE: ".pdf",
    JPEG_MEDIA_TYPE: ".jpg",
    JSON_MEDIA_TYPE: ".json",
}


@dataclass
class PageResult:
    """One page: the image the boxes were found on, and the same image redacted."""

    index: int
    image: Image.Image
    boxes: list[Box]
    redacted: Image.Image

    @property
    def width(self) -> int:
        return self.image.width

    @property
    def height(self) -> int:
        return self.image.height


@dataclass
class Redaction:
    """One redacted document: its pages, and whether it came in as a PDF.

    ``is_pdf`` travels *with* the pages because it decides what comes back out (a
    PDF for a PDF, else a JPEG). Callers determine it when they read the input —
    from a content type, from a file suffix — and would otherwise each have to
    hand it back down to the renderer, where it could disagree with the pages.

    ``debug`` is the detection trace when ``?debug=true`` asked for one, and
    travels the same way and for the same reason: it is produced while the pages
    are, and only the report knows what to do with it.
    """

    pages: list[PageResult]
    is_pdf: bool
    debug: str | None = None


def run_redaction(
    pipeline: RedactionPipeline,
    source: bytes | Image.Image,
    opts: RedactOptions,
    config: Config,
) -> Redaction:
    """Redact ``source`` — a PDF's bytes, or a single decoded image.

    Raises ``ValueError`` for unusable input (the rasterization guards); the caller
    maps that to a 400.
    """
    is_pdf = isinstance(source, bytes)
    pages = (
        rasterize_pdf(
            source,
            dpi=opts.pdf_dpi,
            max_pages=config.redaction.max_pages,
            max_pixels=config.api.max_image_pixels,
        )
        if is_pdf
        else [source]
    )

    results: list[PageResult] = []
    # Name memory is per document: a surname the rules label on page 1 is
    # redacted bare on every following page (see compute_boxes).
    known_names: set[str] = set()
    # One trace per document, for the same reason: read as one narrative, with
    # the pages marked off inside it rather than split across N of them.
    trace = Trace(collect=opts.debug)
    for index, page in enumerate(pages):
        if len(pages) > 1:
            trace.add("=== page %d ===", index)
        if opts.unwarp:
            image = pipeline.unwarp(page)
        else:
            # convert() copies even when the mode already matches, and a page is
            # ~12 MB at 200 dpi — only pay for it when there is a conversion to do.
            image = page if page.mode == "RGB" else page.convert("RGB")
        boxes = pipeline.compute_boxes(image, known_names=known_names, trace=trace)
        # apply_boxes fills in place, so redact a copy — `image` is the clean page
        # the boxes refer to, and callers may want both.
        redacted = pipeline.apply_boxes(image.copy(), boxes)
        results.append(PageResult(index=index, image=image, boxes=boxes, redacted=redacted))
    return Redaction(pages=results, is_pdf=is_pdf, debug=trace.collected)


def assemble(
    pipeline: RedactionPipeline,
    pages: list[Image.Image],
    boxes: list[list[Box]],
    opts: AssembleOptions,
) -> tuple[str, bytes]:
    """Fill ``boxes`` on ``pages`` and package them. Returns ``(media_type, bytes)``.

    The boxes for a page are in that page image's own pixel space — this never
    dewarps or re-detects, so the pixels filled are exactly the pixels the caller
    sent.
    """
    if opts.format == "jpeg" and len(pages) != 1:
        raise ValueError(f"format 'jpeg' expects exactly one page, got {len(pages)}")

    filled = [pipeline.apply_boxes(image, page_boxes) for image, page_boxes in zip(pages, boxes)]
    if opts.format == "pdf":
        return PDF_MEDIA_TYPE, assemble_pdf(filled, opts.jpeg_quality, opts.dpi)
    return JPEG_MEDIA_TYPE, encode_jpeg(filled[0], opts.jpeg_quality)


def render_document(redaction: Redaction, opts: RedactOptions) -> tuple[str, bytes]:
    """The redacted pages as one file, of the same kind that came in: a PDF for a
    PDF (even a one-page one), else a JPEG. Images always come back as JPEG."""
    if redaction.is_pdf:
        return PDF_MEDIA_TYPE, assemble_pdf(
            [r.redacted for r in redaction.pages], opts.jpeg_quality, opts.pdf_dpi
        )
    return JPEG_MEDIA_TYPE, encode_jpeg(redaction.pages[0].redacted, opts.jpeg_quality)


def _artifact(media_type: str, data: bytes) -> dict[str, str]:
    return {"content_type": media_type, "data": base64.b64encode(data).decode("ascii")}


def build_report(redaction: Redaction, opts: RedactOptions) -> dict[str, Any]:
    """The ``json-output=true`` body, and what the CLI's ``--json-output`` writes.

    Page images are the *clean* pages the boxes refer to — only ``redacted`` is
    redacted. That entry is built by :func:`render_document`, the same call the
    file response makes, so the report always carries exactly the bytes
    ``json-output=false`` would have returned and the two cannot drift apart: a
    PDF for a PDF, else a JPEG.

    The report does not name the engine: that is fixed per process, so
    ``GET /health`` is where to read it.

    ``debug`` appears only when it was asked for, so an ordinary report is
    exactly what it always was.
    """
    report: dict[str, Any] = {
        "unwarped": opts.unwarp,
        "pages": [
            {
                "index": r.index,
                "width": r.width,
                "height": r.height,
                "boxes": [box.as_list() for box in r.boxes],
                "image": _artifact(JPEG_MEDIA_TYPE, encode_jpeg(r.image, opts.jpeg_quality)),
            }
            for r in redaction.pages
        ],
        "redacted": _artifact(*render_document(redaction, opts)),
    }
    if redaction.debug is not None:
        report["debug"] = redaction.debug
    return report


def produce_output(redaction: Redaction, opts: RedactOptions) -> tuple[str, bytes]:
    """What the caller hands to its transport: ``(media_type, bytes)``.

    The ``json_output`` fork lives here rather than in each caller, so the CLI
    writes exactly the bytes the endpoint returns — and so the report, which
    carries every page as base64, is serialized in the worker thread with the rest
    of the CPU-bound work rather than on the event loop.
    """
    if opts.json_output:
        return JSON_MEDIA_TYPE, json.dumps(build_report(redaction, opts)).encode()
    return render_document(redaction, opts)
