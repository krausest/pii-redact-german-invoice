"""FastAPI service exposing the redaction pipeline.

Two endpoints that share nothing but the box format:

* ``POST /api/redact``   — raw PNG/JPEG/PDF bytes in; the redacted file out, or
  (``?json-output=true``) a JSON report of every page with its boxes. The file
  response carries no metadata, so ask for the report when you need to know what
  was found.
* ``POST /api/assemble`` — page images + boxes in as JSON; one file out. No models,
  no OCR, no unwarping: it fills rectangles, so the pixels it covers are exactly
  the pixels the caller sent. This is what a client calls once a human has edited
  the boxes.
* ``GET /health``        — ``{"status": "ok", "engine": {...}}``.

All options are query parameters (see :mod:`backend.options`). The engine is fixed
per process by config; it is not selectable per request.

Concurrency is process-level: run under Gunicorn with N uvicorn workers, each of
which builds its own pipeline in the lifespan handler. Within a worker the
CPU-bound work runs in a thread so the event loop stays free to answer ``/health``
and reject bad uploads while a redaction is in flight, and a capacity limiter caps
how many run at once (``api.max_concurrent_per_worker``).
"""

from __future__ import annotations

import io
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError

from backend.config import Config, load_config
from backend.factory import build_pipeline, resolve_engine
from backend.models import Box
from backend.options import AssembleBody, AssembleOptions, RedactOptions
from backend.pipeline import RedactionPipeline
from backend.service import (
    MEDIA_TYPE_BY_FORMAT,
    PDF_MEDIA_TYPE,
    assemble,
    produce_output,
    run_redaction,
)

REDACT_DESCRIPTION = """
Find the PII in a document and black it out. Send the **raw file** — PNG, JPEG or
PDF bytes, not multipart — and pass options as query parameters:

    curl -X POST --data-binary @invoice.pdf -H 'Content-Type: application/pdf' \\
         'http://localhost:8000/api/redact?unwarp=false' -o redacted.pdf

| param | values | default | meaning |
|---|---|---|---|
| `unwarp` | bool | `redaction.unwarp` | flatten the photographed page before OCR |
| `json-output` | bool | `false` | return the JSON report, which embeds the file, instead of the bare file |
| `pdf-dpi` | int | `redaction.pdf_dpi` | rasterization DPI for PDF input |
| `jpeg-quality` | 1-100 | `redaction.jpeg_quality` | quality of every JPEG produced |

By default you get the file back, the same kind you sent: a PDF for a PDF, a JPEG
for any image. With `json-output=true` you get
`{unwarped, pages: [{index, width, height, boxes, image}], redacted}` — where
`boxes` are always in the pixel space of the `image` in the same entry, that image
is **not** redacted (it is the page for review), and `redacted` is the finished
document: the very bytes this endpoint would have returned without `json-output`,
`application/pdf` for PDF input and `image/jpeg` for an image. Ask for the report
when you need to know *what* was found; the file alone does not say.
"""

ASSEMBLE_DESCRIPTION = """
Turn page images and boxes into one document. No models, no OCR, no unwarping —
this fills rectangles and packages the result, so the boxes cannot drift from the
pixels. Call it after a human has reviewed what `/api/redact` reported.

    {"pages": [{"content_type": "image/jpeg", "data": "<base64>",
                "boxes": [[10, 5, 30, 25]]}]}

Boxes are in the pixel space of the image in the same entry.

| param | values | default | meaning |
|---|---|---|---|
| `format` | pdf \\| jpeg | `pdf` | `jpeg` requires exactly one page |
| `dpi` | int | `redaction.pdf_dpi` | the resolution the images represent, so PDF pages get their true physical size |
| `jpeg-quality` | 1-100 | `redaction.jpeg_quality` | |
"""


logger = logging.getLogger("backend.api")


class ApiError(Exception):
    """Raised by the validation helpers; mapped to a JSON error response."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load the models — the one thing that must *not* happen at import time.

    ``app = create_app()`` at the bottom of this module runs when anything imports
    it (gunicorn does, and so does every test); building the pipeline here instead
    keeps that import from costing ~2 GB of model weights. Tests exploit the same
    seam by setting ``app.state.pipeline`` before the lifespan runs.
    """
    if not getattr(app.state, "pipeline", None):
        app.state.pipeline = build_pipeline(app.state.config)
    yield


def create_app(config: Config | None = None) -> FastAPI:
    # Neither uvicorn nor gunicorn touch the root logger (they configure their own
    # "uvicorn.*"/"gunicorn.*" loggers, and uvicorn's format carries no timestamp),
    # so our records would otherwise fall through to logging's WARNING-only last
    # resort handler. basicConfig is a no-op if the root logger is already set up.
    # PII_LOG_LEVEL=DEBUG logs every OCR line and match while classifying.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s [pid %(process)d] %(message)s",
    )
    logging.getLogger("backend").setLevel(os.environ.get("PII_LOG_LEVEL", "INFO").upper())

    app = FastAPI(title="pii-redact", lifespan=lifespan)
    config = config or load_config()
    app.state.config = config

    # Bounds the CPU-bound work: one shared RedactionPipeline serves the whole
    # worker, the models are not known to be thread-safe, and a 30-page PDF holds
    # ~700 MB of page images. anyio's own thread pool would otherwise allow 40 at
    # once. The thread is for responsiveness (health checks, fast 4xx rejections
    # while a redaction runs), not parallelism — that comes from worker processes.
    limiter = anyio.CapacityLimiter(config.api.max_concurrent_per_worker)

    def get_config() -> Config:
        return app.state.config

    def get_pipeline() -> RedactionPipeline:
        return app.state.pipeline

    # -- request reading ---------------------------------------------------- #
    def _content_type(request: Request) -> str:
        return (request.headers.get("content-type") or "").split(";")[0].strip().lower()

    async def _read_capped(request: Request, limit: int) -> bytes:
        """Buffer the body, rejecting anything over ``limit`` — both by the
        Content-Length header and by the actual streamed size (it can lie)."""
        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                if int(declared) > limit:
                    raise ApiError(413, "payload too large")
            except ValueError:
                raise ApiError(400, "invalid Content-Length")
        chunks: list[bytes] = []
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > limit:
                raise ApiError(413, "payload too large")
            chunks.append(chunk)
        data = b"".join(chunks)
        if not data:
            raise ApiError(400, "empty request body")
        return data

    def _decode_image(data: bytes, api) -> Image.Image:
        """Verify bytes are a real PNG/JPEG within the pixel cap; return the RGB image."""
        Image.MAX_IMAGE_PIXELS = api.max_image_pixels
        try:
            with Image.open(io.BytesIO(data)) as probe:
                probe.verify()  # cheap integrity + bomb check; invalidates the object
            image = Image.open(io.BytesIO(data))  # reopen: verify() leaves it unusable
            image.load()
            fmt = image.format
        except Image.DecompressionBombError:
            raise ApiError(400, "image exceeds the maximum allowed pixel count")
        except (UnidentifiedImageError, OSError, ValueError):
            raise ApiError(400, "request body is not a valid image")
        if fmt not in MEDIA_TYPE_BY_FORMAT:
            raise ApiError(415, f"unsupported image format: {fmt}")
        return image.convert("RGB")

    def _options(cls, request: Request, config: Config):
        try:
            return cls.from_query(dict(request.query_params), config)
        except ValueError as e:
            raise ApiError(400, str(e))

    async def _run(func):
        """Run CPU-bound work in a thread, bounded by ``limiter``."""
        return await anyio.to_thread.run_sync(func, limiter=limiter)

    # -- routes -------------------------------------------------------------- #
    @app.get("/health")
    async def health(config: Config = Depends(get_config)):
        return {"status": "ok", "engine": resolve_engine(config)}

    @app.post("/api/redact", description=REDACT_DESCRIPTION)
    async def redact(
        request: Request,
        config: Config = Depends(get_config),
        pipeline: RedactionPipeline = Depends(get_pipeline),
    ):
        started = time.perf_counter()
        api = config.api
        content_type = _content_type(request)
        logger.info(
            "POST /api/redact enter: content-type=%s query=%s",
            content_type or "none",
            dict(request.query_params) or "{}",
        )
        if content_type not in api.input_content_types:
            raise ApiError(415, f"unsupported content type: {content_type or 'none'!r}")
        opts = _options(RedactOptions, request, config)
        data = await _read_capped(request, api.max_upload_bytes)

        if content_type == PDF_MEDIA_TYPE:
            if not data.lstrip()[:5].startswith(b"%PDF"):
                raise ApiError(400, "request body is not a valid PDF")
            source: bytes | Image.Image = data
        else:
            source = _decode_image(data, api)

        def work():
            # run_redaction reports back whether this was a PDF, so the response
            # kind is decided once, next to the pages it describes.
            return produce_output(run_redaction(pipeline, source, opts, config), opts)

        try:
            media_type, body = await _run(work)
        except ValueError as e:  # rasterization guards (too many pages / oversized)
            raise ApiError(400, str(e))

        logger.info(
            "POST /api/redact exit: %s, %d bytes in %.2fs",
            media_type,
            len(body),
            time.perf_counter() - started,
        )
        return Response(content=body, media_type=media_type)

    @app.post("/api/assemble", description=ASSEMBLE_DESCRIPTION)
    async def assemble_route(
        request: Request,
        config: Config = Depends(get_config),
        pipeline: RedactionPipeline = Depends(get_pipeline),
    ):
        started = time.perf_counter()
        logger.info(
            "POST /api/assemble enter: query=%s", dict(request.query_params) or "{}"
        )
        if _content_type(request) != "application/json":
            raise ApiError(415, "expected application/json")
        opts = _options(AssembleOptions, request, config)
        raw = await _read_capped(request, config.api.max_upload_bytes)
        try:
            body = AssembleBody.from_json(json.loads(raw))
        except json.JSONDecodeError:
            raise ApiError(400, "invalid JSON body")
        except ValueError as e:  # the model's own validation
            raise ApiError(400, str(e))
        if len(body.pages) > config.redaction.max_pages:
            raise ApiError(400, f"too many pages ({len(body.pages)} > {config.redaction.max_pages})")

        # The model decoded the base64; _decode_image still vets the bytes as a
        # real, non-abusive PNG/JPEG.
        images: list[Image.Image] = [_decode_image(p.data, config.api) for p in body.pages]
        boxes: list[list[Box]] = [[Box(*b) for b in p.boxes] for p in body.pages]

        try:
            media_type, out = await _run(lambda: assemble(pipeline, images, boxes, opts))
        except ValueError as e:
            raise ApiError(400, str(e))
        logger.info(
            "POST /api/assemble exit: %d pages -> %s, %d bytes in %.2fs",
            len(images),
            media_type,
            len(out),
            time.perf_counter() - started,
        )
        return Response(content=out, media_type=media_type)

    @app.exception_handler(ApiError)
    async def _api_error_handler(_request: Request, exc: ApiError):
        # Rejected requests never reach their handler's exit line; log it here so
        # every "enter" has a counterpart.
        logger.warning(
            "%s failed: %d %s", _request.url.path, exc.status_code, exc.detail
        )
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    # Optionally serve the built web UI (Svelte SPA) from the same origin. Set
    # PII_STATIC_DIR to the dist/ directory (the Docker image does this). Mounted
    # last at "/", so the API routes and /health above take precedence.
    static_dir = os.environ.get("PII_STATIC_DIR")
    if static_dir and Path(static_dir).is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="spa")

    return app


# Module-level app for `gunicorn backend.api:app` / `uvicorn backend.api:app`.
app = create_app()
logger.info("App started")
