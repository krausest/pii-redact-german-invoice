# Third-party licenses

This project itself is MIT-licensed (see [`LICENSE`](LICENSE)). It depends on the
third-party packages and pretrained models below, which carry their own licenses.
Versions are the ones pinned in [`uv.lock`](uv.lock) / [`frontend/package.json`](frontend/package.json)
at the time of writing; re-check on upgrade.

## Python — `backend` (default install, ships in the Docker image)

| Package | License |
|---|---|
| fastapi | MIT |
| gunicorn | MIT |
| onnxruntime | MIT |
| paddleocr | Apache-2.0 |
| paddlepaddle | Apache-2.0 |
| pillow | MIT-CMU |
| presidio-analyzer | MIT |
| **pymupdf** | **GNU AGPL-3.0-or-later**, dual-licensed — a commercial license is available from Artifex Software |
| requests (transitive: paddleocr/paddlex, presidio-analyzer/spacy) | Apache-2.0 |
| uvicorn[standard] (incl. httptools, websockets, watchfiles, uvloop, python-dotenv) | BSD-3-Clause |

## Python — `dev` dependency group (**not** installed in the Docker image)

The image is built with `uv sync --frozen --no-default-groups`, so nothing declared
here ships. Note that a package can be listed above *and* here: `requests` is a
direct import of the notebooks and also arrives transitively through paddleocr and
presidio-analyzer, so it does ship.

| Package | License |
|---|---|
| httpx | BSD-3-Clause |
| ipykernel | BSD-3-Clause |
| pytest | MIT |
| requests | Apache-2.0 |

## Python — optional `gliner` extra (`uv sync --extra gliner`; **not** installed in the Docker image)

| Package | License |
|---|---|
| gliner | Apache-2.0 |
| torch (pulled in transitively) | BSD-3-Clause |

## JavaScript — `frontend/` (build-time only; only the compiled static assets ship, not the source deps)

| Package | License |
|---|---|
| svelte | MIT |
| @sveltejs/vite-plugin-svelte | MIT |
| svelte-check | MIT |
| typescript | Apache-2.0 |
| vite | MIT |

## Build tooling

| Tool | License |
|---|---|
| uv (astral-sh) | Apache-2.0 OR MIT (dual, your choice) |

## Pretrained models

| Model | Used by | License |
|---|---|---|
| `de_core_news_lg` (spaCy German pipeline, v3.8.0) | Presidio's NLP engine (`presidio`/`onnx` engine presets) | MIT |
| PaddleOCR PP-OCRv6 German detection + recognition models | `PaddleOCRBackend` (both `paddle` and `onnxruntime` OCR backends) | Apache-2.0 |
| PaddleX `doc_preprocessor` / UVDoc unwarping + doc-orientation models | `DocUnwarper` | Apache-2.0 |
| `urchade/gliner_multi_pii-v1` (GLiNER + mDeBERTa backbone, HuggingFace) | `GlinerClassifier` (`gliner` engine preset only, optional extra) | Apache-2.0 |

## What license applies to the built Dockerfile image

The built Docker image is **AGPL-3.0**, because it bundles **PyMuPDF**, which is
AGPL-3.0 (dual-licensed, with a paid Artifex commercial alternative) — one AGPL
dependency makes the whole combined image AGPL, regardless of every other
dependency here being permissive (MIT/BSD/Apache-2.0).

This project's own source code stays **MIT** (see [`LICENSE`](LICENSE)) — AGPL
doesn't relicense the code it's combined with, it only requires that anyone
served by a deployment of the image can get the complete source of the
combined work. That's satisfied by this repository being public on GitHub.
