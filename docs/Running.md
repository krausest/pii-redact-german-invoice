# Running

Three ways to run the service: a development setup with hot reload, a production
setup without Docker, and [Docker](Docker.md). The batch CLI is described in
[CLI](CLI.md).

## Prerequisites

- [uv](https://docs.astral.sh/uv/) and **Python 3.13** (PaddlePaddle has no 3.14 wheels; pinned in `.python-version`)
- Node.js 22+ for the web UI

```bash
uv sync                                          # Python dependencies (incl. the guard-omni group)
uv run python -m spacy download de_core_news_lg  # German model for the presidio classifier
cd frontend && npm install && cd ..              # web UI dependencies
```

The first run downloads the Paddle models (~180 MB) into `.paddle_cache/`;
`guard-omni` is off unless `PII_GUARD_OMNI=true`, and is then fetched from
Hugging Face the first time it is selected. See
[Redaction › Models](Redaction.md#models).

## Development

Two processes with hot reload:

```bash
# Shell 1 — API on :8000
uv run uvicorn backend.api:app --reload --port 8000

# Shell 2 — web UI on :5173
cd frontend && npm run dev
```

Open **http://localhost:5173**. Vite proxies `/api` and `/health` to the API
([`vite.config.ts`](../frontend/vite.config.ts)), so there is no CORS setup.

> [!NOTE]
> The proxy targets `127.0.0.1:8000`, not `localhost`: on macOS Node resolves
> `localhost` to IPv6 `::1` first while uvicorn binds IPv4.

## Production without Docker

The API serves the built web UI itself — one process group, one port.

```bash
cd frontend && npm run build && cd ..            # -> frontend/dist/

PII_STATIC_DIR=frontend/dist \
  uv run gunicorn backend.api:app -k uvicorn.workers.UvicornWorker \
      -w 2 -b 0.0.0.0:8000 --timeout 120
```

Open **http://localhost:8000** for the UI; the API is on the same origin. Run the
command from the repo root — `PII_STATIC_DIR` is resolved against the current
directory. If `/` returns 404, the UI is not mounted: the variable is unset or
`frontend/dist/` does not exist.

**Scaling.** Each Gunicorn worker (`-w`) is a process with its own copy of the
models (~4 GB RAM with both classifiers loaded). Inside a worker, redactions run
one at a time ([`max_concurrent_per_worker`](Configuration.md#api)) on a background
thread, so `/health` stays responsive. More throughput means more workers or more
containers. Keep `--timeout` above the slowest document you expect — a long PDF on
CPU takes well over a minute.

## API only

Leave `PII_STATIC_DIR` unset and only the [REST API](API.md) is served:

```bash
uv run uvicorn backend.api:app --host 0.0.0.0 --port 8000
```

Settings for all of these: [Configuration](Configuration.md).
