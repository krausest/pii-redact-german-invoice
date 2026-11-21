"""FastAPI service exposing the redaction pipeline.

Two endpoints that share nothing but the box format:

* ``POST /api/redact``   — raw PNG/JPEG/PDF bytes, or a JSON body carrying the
  file and the user's word lists, in; the redacted file out, or
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
and reject bad uploads while a redaction is in flight, and a capacity limiter lets
exactly one run at a time; the rest queue in arrival order.
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
from PIL import Image, ImageOps, UnidentifiedImageError
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.config import Config, load_config
from backend.factory import build_pipeline, classifier_names, resolve_engine
from backend.models import Box
from backend.options import AssembleBody, AssembleOptions, RedactBody, RedactOptions
from backend.pipeline import RedactionPipeline
from backend.service import IMAGE_FORMATS, FileKind, assemble, produce_output, run_redaction
from backend.wordlists import EMPTY

REDACT_DESCRIPTION = """
Find the PII in a document and black it out. Send the **raw file** — PNG, JPEG or
PDF bytes, not multipart — and pass options as query parameters:

    curl -X POST --data-binary @invoice.pdf -H 'Content-Type: application/pdf' \\
         'http://localhost:8000/api/redact?unwarp=false' -o redacted.pdf

Word lists name people, so they never go in the URL: send `application/json`
instead, `{"content_type": "application/pdf", "data": "<base64>",
"redact_words": [...], "keep_words": [...]}`.

| param | values | default | meaning |
|---|---|---|---|
| `unwarp` | bool | `redaction.unwarp` | flatten the photographed page before OCR |
| `json-output` | bool | `false` | return the JSON report, which embeds the file, instead of the bare file |
| `pdf-dpi` | int | `redaction.pdf_dpi` | rasterization DPI for PDF input |
| `jpeg-quality` | 1-100 | `redaction.jpeg_quality` | quality of every JPEG produced |
| `debug` | bool | `false` | add the per-line detection trace to the report; requires `json-output=true` |

By default you get the file back, the same kind you sent: a PDF for a PDF, a JPEG
for any image. With `json-output=true` you get
`{unwarped, pages: [{index, width, height, boxes, notes, image}], redacted}` — where
`boxes` and `notes` are always in the pixel space of the `image` in the same entry
(a note is `{box, text}`: text printed white on its own black box, the birth year
over a redacted birthdate), that image
is **not** redacted (it is the page for review), and `redacted` is the finished
document: the very bytes this endpoint would have returned without `json-output`,
`application/pdf` for PDF input and `image/jpeg` for an image. Ask for the report
when you need to know *what* was found; the file alone does not say.

With `debug=true` the report carries one more key, `debug`: the detection trace as
plain text — every OCR line with its pixel box, each classifier match and the
verdict that produced (or did not produce) a box. It is what diagnoses a wrong
box without the file. `debug=true` alone is a `400`: the file response carries no
metadata, so there would be nowhere to put it.
"""

ASSEMBLE_DESCRIPTION = """
Turn page images and boxes into one document. No models, no OCR, no unwarping —
this fills rectangles and packages the result, so the boxes cannot drift from the
pixels. Call it after a human has reviewed what `/api/redact` reported.

    {"pages": [{"content_type": "image/jpeg", "data": "<base64>",
                "boxes": [[10, 5, 30, 25]],
                "notes": [{"box": [40, 5, 80, 25], "text": "1975"}]}]}

Boxes and notes are in the pixel space of the image in the same entry; `notes` is
optional and is printed after every box is filled.

| param | values | default | meaning |
|---|---|---|---|
| `format` | pdf \\| jpeg | `pdf` | `jpeg` requires exactly one page |
| `dpi` | int | `redaction.pdf_dpi` | the resolution the images represent, so PDF pages get their true physical size |
| `jpeg-quality` | 1-100 | `redaction.jpeg_quality` | |
"""


logger = logging.getLogger("backend.api")


# Room for the word lists beside the base64 file in a JSON /api/redact body.
_ENVELOPE_SLACK = 1024 * 1024
_JSON = "application/json"

# The UI loads nothing but its own assets and talks to nothing but this origin.
# Styles stay inline-capable: Svelte injects transition keyframes at runtime.
_SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
}


# What a client is told to wait before retrying a 503.
_RETRY_AFTER_SECONDS = 5


class ApiError(Exception):
    """Raised by the validation helpers; mapped to a JSON error response.

    ``log_detail=False`` keeps the detail out of the log below DEBUG: it may
    quote the request body (a word-list entry)."""

    def __init__(
        self,
        status_code: int,
        detail: str,
        log_detail: bool = True,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.log_detail = log_detail
        self.headers = headers


class SecurityHeaders:
    """Adds :data:`_SECURITY_HEADERS` to every response, and ``no-store`` to any
    that set no caching policy of their own — every API answer holds a document."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in _SECURITY_HEADERS.items():
                    headers[name] = value
                if "cache-control" not in headers:
                    headers["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_with_headers)


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

    # No Swagger/ReDoc pages: they load their scripts from a CDN, which the CSP
    # forbids and which would be the one request leaving the machine.
    # /openapi.json stays.
    app = FastAPI(title="pii-redact", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.add_middleware(SecurityHeaders)
    config = config or load_config()
    app.state.config = config

    # One redaction at a time per worker, not configurable: every request shares
    # this worker's models, which are not known to be thread-safe, so two users'
    # documents must never be inside them at once. anyio's own pool would allow
    # 40. The thread keeps /health and 4xx answers responsive; parallelism comes
    # from worker processes (WEB_CONCURRENCY), which share nothing.
    limiter = anyio.CapacityLimiter(1)
    # Requests admitted to this worker: the running one plus those queued behind
    # it. Counted from before the body is read, since the body is what costs
    # memory. One event loop, so a plain counter is safe.
    admitted = 0

    async def admit():
        """Dependency of the two heavy routes: a 503 once the queue is full."""
        nonlocal admitted
        if admitted > app.state.config.api.max_queued_per_worker:
            raise ApiError(
                503, "server busy, retry later", headers={"Retry-After": str(_RETRY_AFTER_SECONDS)}
            )
        admitted += 1
        try:
            yield
        finally:
            admitted -= 1

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
        if fmt not in IMAGE_FORMATS:
            raise ApiError(415, f"unsupported image format: {fmt}")
        # Apply the EXIF orientation before anything else sees the page: a phone
        # photo stores its pixels sideways, and neither PIL nor our JPEG output
        # carries the tag. Straightening here keeps the raster the only truth, so
        # the boxes in the report are in the coordinate space of an upright page.
        upright = ImageOps.exif_transpose(image).convert("RGB")
        upright.info = {}  # pixels only; a JPEG comment or PNG text can name the patient
        return upright

    def _loggable_query(request: Request, cls) -> dict[str, str] | str:
        """The query for the log: values of known options only, so a stray
        parameter cannot carry a name into it."""
        known = set(cls.wire_names())
        query = {k: (v if k in known else "…") for k, v in request.query_params.multi_items()}
        return query or "{}"

    def _parse_json(held: list[bytes], model):
        """Parse the body in ``held`` into ``model``, releasing the raw bytes and
        the parsed dict as soon as each is consumed. Its errors stay out of the log."""
        try:
            data = json.loads(held.pop())
        except json.JSONDecodeError:
            raise ApiError(400, "invalid JSON body")
        try:
            return model.from_json(data)
        except ValueError as e:  # the model's own validation
            raise ApiError(400, str(e), log_detail=False)

    def _options(cls, request: Request, config: Config):
        try:
            return cls.from_query(cls.raw_query(request.query_params.multi_items()), config)
        except ValueError as e:
            raise ApiError(400, str(e))

    async def _run(func):
        """Run CPU-bound work in a thread, bounded by ``limiter``.

        Everything a body turns into — parsed JSON, decoded base64, decoded
        pixels — is made in here, so a request waiting for its turn holds only
        its raw bytes. The price: a malformed body gets its 400 after the wait."""
        return await anyio.to_thread.run_sync(func, limiter=limiter)

    # -- routes -------------------------------------------------------------- #
    @app.get("/health")
    async def health(config: Config = Depends(get_config)):
        # `engine` is the process default; `classifiers` is what a request may
        # name instead with `?classifier=`. Additive on purpose — the engine
        # block is a published shape and readers of it should not have to change.
        return {
            "status": "ok",
            "engine": resolve_engine(config),
            "classifiers": list(classifier_names(config)),
        }

    @app.post("/api/redact", description=REDACT_DESCRIPTION, dependencies=[Depends(admit)])
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
            _loggable_query(request, RedactOptions),
        )
        if content_type != _JSON and content_type not in api.input_content_types:
            raise ApiError(415, f"unsupported content type: {content_type or 'none'!r}")
        opts = _options(RedactOptions, request, config)
        # base64 grows the file by a third; the decoded size is held to the cap.
        limit = api.max_upload_bytes * 4 // 3 + _ENVELOPE_SLACK if content_type == _JSON else api.max_upload_bytes
        held = [await _read_capped(request, limit)]

        def work():
            kind, lists = content_type, EMPTY
            if kind == _JSON:
                body = _parse_json(held, RedactBody)
                kind, data, lists = body.content_type, body.data, body.lists()
                del body
                if kind not in api.input_content_types:
                    raise ApiError(415, f"unsupported content type: {kind!r}")
                if len(data) > api.max_upload_bytes:
                    raise ApiError(413, "payload too large")
            else:
                data = held.pop()
            if kind == FileKind.PDF.media_type:
                if not data.lstrip()[:5].startswith(b"%PDF"):
                    raise ApiError(400, "request body is not a valid PDF")
                source: bytes | Image.Image = data
            else:
                source = _decode_image(data, api)
            del data
            # run_redaction reports back whether this was a PDF, so the response
            # kind is decided once, next to the pages it describes.
            return produce_output(run_redaction(pipeline, source, opts, config, lists=lists), opts)

        try:
            output = await _run(work)
        except ValueError as e:  # rasterization guards (too many pages / oversized)
            raise ApiError(400, str(e))

        logger.info(
            "POST /api/redact exit: %s, %d bytes in %.2fs",
            output.kind.media_type,
            len(output.data),
            time.perf_counter() - started,
        )
        return Response(content=output.data, media_type=output.kind.media_type)

    @app.post("/api/assemble", description=ASSEMBLE_DESCRIPTION, dependencies=[Depends(admit)])
    async def assemble_route(
        request: Request,
        config: Config = Depends(get_config),
        pipeline: RedactionPipeline = Depends(get_pipeline),
    ):
        started = time.perf_counter()
        logger.info(
            "POST /api/assemble enter: query=%s", _loggable_query(request, AssembleOptions)
        )
        if _content_type(request) != _JSON:
            raise ApiError(415, "expected application/json")
        opts = _options(AssembleOptions, request, config)
        held = [await _read_capped(request, config.api.max_assemble_bytes)]

        def work():
            pages = _parse_json(held, AssembleBody).pages
            if len(pages) > config.redaction.max_pages:
                raise ApiError(400, f"too many pages ({len(pages)} > {config.redaction.max_pages})")
            boxes: list[list[Box]] = [
                [Box(*b) for b in p.boxes] + [Box(*n.box, text=n.text) for n in p.notes]
                for p in pages
            ]
            # The model decoded the base64; _decode_image still vets the bytes as
            # a real, non-abusive PNG/JPEG. Each page's bytes go once decoded.
            images: list[Image.Image] = []
            for page in pages:
                images.append(_decode_image(page.data, config.api))
                page.data = b""
            return len(images), assemble(pipeline, images, boxes, opts)

        try:
            count, output = await _run(work)
        except ValueError as e:
            raise ApiError(400, str(e))
        logger.info(
            "POST /api/assemble exit: %d pages -> %s, %d bytes in %.2fs",
            count,
            output.kind.media_type,
            len(output.data),
            time.perf_counter() - started,
        )
        return Response(content=output.data, media_type=output.kind.media_type)

    @app.exception_handler(ApiError)
    async def _api_error_handler(_request: Request, exc: ApiError):
        # Rejected requests never reach their handler's exit line; log it here so
        # every "enter" has a counterpart.
        path = _request.url.path
        if exc.log_detail:
            logger.warning("%s failed: %d %s", path, exc.status_code, exc.detail)
        else:
            logger.warning("%s failed: %d (detail withheld)", path, exc.status_code)
            logger.debug("%s failed: %d %s", path, exc.status_code, exc.detail)
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)

    # Optionally serve the built web UI (Svelte SPA) from the same origin. Set
    # PII_STATIC_DIR to the dist/ directory (the Docker image does this). Mounted
    # last at "/", so the API routes and /health above take precedence.
    static_dir = os.environ.get("PII_STATIC_DIR")
    if static_dir and Path(static_dir).is_dir():
        app.mount("/", SpaStaticFiles(directory=static_dir, html=True), name="spa")

    return app


class SpaStaticFiles(StaticFiles):
    """Vite hashes asset names, so they are immutable; everything else
    (index.html) must be revalidated or a release is not picked up."""

    async def get_response(self, path: str, scope) -> Response:
        response = await super().get_response(path, scope)
        if path.startswith("assets/") and response.status_code in (200, 304):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "no-cache"
        return response


# Module-level app for `gunicorn backend.api:app` / `uvicorn backend.api:app`.
app = create_app()
logger.info("App started")
