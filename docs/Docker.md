# Docker

One image serves the web UI and the REST API on port 8000. All models are baked in
at build time, so the container runs **fully offline**.

## Use a published image

Every push to `main` and every release tag is published to the GitHub Container
Registry:

```bash
docker run --rm --platform linux/amd64 -p 8000:8000 ghcr.io/krausest/pii-redact-german-invoice
# open http://localhost:8000
```

| Tag | Image |
|---|---|
| `latest` | the newest release |
| `0.7`, `0.7.0` | a release (`v0.7.0`); `0.7` follows the latest patch |
| `17d0c8c` | the commit a release was built from (short SHA) |

Images are built only for release tags (`v*.*.*`); pushes to `main` build nothing.

`--platform linux/amd64` is only needed on Apple Silicon and other ARM hosts, which
run the image under emulation (see [below](#limits-on-apple-silicon)). Replace
`pii-redact` with `ghcr.io/krausest/pii-redact-german-invoice[:tag]` in the examples
on this page to use a published image.

## Build

```bash
docker build --pull --platform linux/amd64 -t pii-redact .
```

`--platform linux/amd64` is required: PaddlePaddle ships no Linux ARM64 wheel. On
Apple Silicon the build runs under emulation and takes a while. `--pull` fetches
the current `wolfi-base` instead of a stale local copy, so security patches in the
base image are picked up.

What the [`Dockerfile`](../Dockerfile) does:

1. A Node stage builds the web UI (`frontend/dist`).
2. A [Wolfi](https://github.com/wolfi-dev) stage (`cgr.dev/chainguard/wolfi-base`, a
   minimal rolling glibc distro with few known CVEs) installs Python 3.13 and the
   runtime dependencies only (`uv sync --no-default-groups`) — no dev tools, no
   `guard-omni` runtime (torch), no pip, curl or uv in the final image.
3. [`docker/warmup.py`](../docker/warmup.py) constructs every engine, the
   presidio classifier and the dewarping model, which downloads all weights into the image.
   A model that cannot be fetched fails the build.
4. The service runs as an unprivileged user, with `HF_HUB_OFFLINE=1` and a
   built-in `HEALTHCHECK` on `/health`.

## Run

```bash
docker run --rm -p 8000:8000 pii-redact
# UI: http://localhost:8000 · API: http://localhost:8000/api/... · GET /health
```

Verify it needs no network:

```bash
docker run -d --name pii --network none pii-redact
docker exec pii python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health').read())"
```

## Parameters

| Variable | Default | Meaning |
|---|---|---|
| `WEB_CONCURRENCY` | `1` | Gunicorn worker processes; each loads the full model set (~4 GB RAM) |
| `REQUEST_TIMEOUT` | `120` | Gunicorn request timeout in seconds |
| `PII_OCR_BACKEND` | from `config.toml` | `paddle` \| `onnxruntime` |
| `PII_CLASSIFIER` | from `config.toml` | default classifier: `presidio` (the image does not ship `guard-omni`) |
| `PII_UNWARP` | from `config.toml` | default for `?unwarp=`; `true` dewarps photographed pages (slower) |
| `PII_REDACT_REGIONS` | from `config.toml` | `false` disables the layout-region pass |
| `PII_LOG_LEVEL` | `INFO` | `DEBUG` logs the detection trace |

```bash
docker run --rm -p 8000:8000 \
    -e WEB_CONCURRENCY=2 -e PII_UNWARP=false -e PII_CLASSIFIER=presidio \
    pii-redact
```

Any other setting: mount your own config file and point `PII_CONFIG` at it.

```bash
docker run --rm -p 8000:8000 \
    -v ./my-config.toml:/app/my-config.toml:ro -e PII_CONFIG=/app/my-config.toml \
    pii-redact
```

All keys: [Configuration](Configuration.md). An invalid value stops the container at
startup with a message naming the field.
