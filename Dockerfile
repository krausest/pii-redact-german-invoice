# syntax=docker/dockerfile:1

# =========================================================================== #
# Stage 1 — build the Svelte SPA
# =========================================================================== #
FROM node:22-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build            # -> /build/dist

# =========================================================================== #
# Stage 2 — Python runtime: serves the API and the built SPA on one port
#
# Build for linux/amd64: paddlepaddle ships no linux-aarch64 wheel (only
# manylinux1_x86_64 / macOS-arm64 / win). On Apple Silicon build with
# `docker build --platform linux/amd64 .` (runs under emulation).
# =========================================================================== #
FROM python:3.13-slim AS runtime

# The *image* is AGPL because it bundles PyMuPDF; the project source itself is
# MIT — see LICENSE.md ("License of the built Docker image").
LABEL org.opencontainers.image.source=https://github.com/krausest/pii-redact-german-invoice
LABEL org.opencontainers.image.description="Removes PII from German invoices. FastAPI service with bundled web UI and REST API; all models baked in, runs fully offline."
LABEL org.opencontainers.image.licenses=AGPL-3.0-or-later

# Native libs required at runtime by opencv (a paddlex dep), paddle and
# onnxruntime; curl is for the HEALTHCHECK.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

# uv (pinned build tool — a specific version for reproducible group handling)
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PADDLE_PDX_CACHE_HOME=/app/.paddle_cache \
    PII_STATIC_DIR=/app/static

# --- Dependency layer (cached across source changes) ---
# `--no-default-groups` drops the dev group (pytest).
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-default-groups --no-install-project

# --- Application source + built SPA ---
COPY backend/ backend/
COPY config.toml ./
COPY docker/warmup.py ./
COPY --from=frontend /build/dist /app/static
RUN uv sync --frozen --no-default-groups

# --- Bake every model into the image (offline runtime) ---
# HF_HUB_OFFLINE=0 is set for this step only so the models may download; the ENV
# below forces offline at runtime. Populates /app/.paddle_cache. Nothing reaches
# HuggingFace any more, so the offline flags are now a plain network guard.
# `--no-sync`: don't let `uv run` re-sync (which would re-add the dev group).
RUN HF_HUB_OFFLINE=0 TRANSFORMERS_OFFLINE=0 uv run --no-sync python warmup.py

# --- Non-root runtime (own the baked caches so they're readable) ---
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
# Offline is enforced, not assumed: warmup.py baked every model above, so any
# download attempt at runtime is a missed bake — make it fail loudly.
#
# Three config keys are overridable per container without rebuilding or mounting a
# config.toml: `-e PII_ENGINE=native|onnx`, `-e PII_UNWARP=false` (skip the
# dewarping model — much faster on flat scans and PDFs, and it is what makes a
# CPU-only container usable) and `-e PII_REDACT_REGIONS=false` (the
# letterhead/footer/sender-column pass). All are left unset here so the baked
# config.toml stays the single source of the defaults; an unparseable value fails
# at startup rather than being ignored. Note PII_UNWARP only sets the *default* for
# `?unwarp=` — a request that names the parameter still wins.
ENV WEB_CONCURRENCY=1 REQUEST_TIMEOUT=120 \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# One worker loads the full model set (~2 GB RAM); scale with container replicas.
CMD uv run --no-sync gunicorn backend.api:app -k uvicorn.workers.UvicornWorker \
    -w ${WEB_CONCURRENCY} -b 0.0.0.0:8000 --timeout ${REQUEST_TIMEOUT}
