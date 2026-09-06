# PII redaction for German invoices

Blacks out personally identifiable information on scanned German invoices — names,
addresses, dates of birth, IBAN/BIC, e-mail, phone and account numbers — protecting
the people the invoice is *about* (the recipient, the patient), not the issuing
company's own details. **Everything runs locally on CPU; no page ever leaves the
machine.**

![Python 3.13](https://img.shields.io/badge/python-3.13-blue)
![Code: MIT](https://img.shields.io/badge/code-MIT-green)
![Runs offline](https://img.shields.io/badge/inference-100%25%20local-informational)

<table>
<tr>
<td width="50%">
<img src="docs/screenshot.jpeg" alt="ui screenshot">
<sub>The web UI: an uploaded German invoice with seven suggested redaction boxes highlighted over the sender line, the recipient address block, and the patient's name and date of birth, plus a toolbar to add, remove and download</sub>
</td>
<td width="50%">
<img src="docs/redacted.jpeg" alt="redacted image">
<sub>Final result of the redaction</sub>
</td>
</tr>
</table>

Upload a scan, review what was found, fix the boxes by hand, download the redacted
file. The same pipeline is available as a batch **CLI** and a **REST API**.

- **Local and offline.** No cloud API, no telemetry. The Docker image bakes every
  model in and runs with `--network none`.
- **Human in the loop.** Detection proposes boxes; you correct them before the
  document is written. Assembly never re-runs the models, so what you saw is what
  gets filled.
- **German-specific.** Tuned for the layouts these invoices actually use — two-column
  dates of birth, `Anrede` lines, PLZ + city blocks, GOÄ fee tables.
- **Three engines**, one pipeline: swap the OCR backend or the classifier from config.

> [!WARNING]
> Automated detection is not a guarantee. Always review the result before sharing a
> redacted document — see [Limitations & safety](#limitations--safety).

## Contents

[Quick start](#quick-start) · [Running](#running) ([Web UI](#web-ui) · [CLI](#cli-batch) · [Docker](#docker)) ·
[REST API](#rest-api) · [Configuration](#configuration) · [How it works](#how-it-works) ·
[Performance](#performance) · [Project layout](#project-layout) ·
[Development](#development) · [Limitations & safety](#limitations--safety) · [License](#license)

## Quick start

Requires [uv](https://docs.astral.sh/uv/) and **Python 3.13** (PaddlePaddle has no
`cp314` wheels yet — pinned in `.python-version` / `pyproject.toml`).

```bash
# 1. Install dependencies
uv sync

# 2. Install the spaCy German model (needed by the Presidio classifier; not on PyPI)
uv run python -m spacy download de_core_news_lg

# 3. Redact the bundled example — writes example/GOÄ_Rechnung1_redacted.pdf
uv run pii-redact example/GOÄ_Rechnung1.pdf
```

The first run downloads ~180 MB of Paddle models into `.paddle_cache/` (see
[Where the models live](#where-the-models-live)). To get the browser app in the
screenshot instead, jump to [Web UI](#web-ui).

## Running

### Web UI

The Svelte SPA in [`frontend/`](frontend/) calls the REST API. Run it two ways.

> [!NOTE]
> The SPA picks the rasterization DPI (150/200/300) and unwarping in its own UI and
> sends both on every request, so `[redaction].pdf_dpi` / `[redaction].unwarp` (and
> `PII_UNWARP`) do **not** steer browser requests — they remain the defaults for the
> CLI and for API calls that omit the parameters. Changing either setting after a
> document is loaded re-runs detection, asking first if boxes were edited by hand.

> [!TIP]
> **Debug log.** The footer's *Debug log* button re-runs detection on the loaded
> document with [`debug=true`](#post-apiredact--find-the-pii-and-black-it-out) and
> shows the trace — why every box was suggested, and what was considered and
> dropped — with Copy and Download. It leaves the pages and your edited boxes
> untouched, so it costs one extra analysis and nothing else. This is what to
> attach to a bug report about a wrong box when the document itself cannot be
> shared.

**Development** (hot reload; two processes, two ports):

```bash
# Shell 1 — the API on :8000
uv run uvicorn backend.api:app --reload --port 8000

# Shell 2 — the Vite dev server on :5173
cd frontend && npm install && npm run dev
```

Open **http://localhost:5173**. Vite serves the SPA and proxies `/api` + `/health`
to the API on :8000 (configured in [`frontend/vite.config.ts`](frontend/vite.config.ts)),
so there is no CORS setup and no build step.

> [!NOTE]
> The proxy targets `127.0.0.1:8000`, not `localhost:8000`, on purpose: uvicorn binds
> IPv4 loopback while Node resolves `localhost` to IPv6 `::1` first on macOS — a
> `localhost` target would fail with `ECONNREFUSED`. If you bind the API elsewhere,
> update the proxy target to match.

**Production (single origin, no Docker).** The backend serves the *built* SPA itself,
so it is one process on one port. Build the static assets, then point the
`PII_STATIC_DIR` environment variable at the build output when starting the backend:

```bash
# 1. Build the SPA -> frontend/dist/
cd frontend && npm install && npm run build && cd ..

# 2. Start the backend with PII_STATIC_DIR pointing at the build output.
#    The path is resolved against the current directory — run this from the repo root.
PII_STATIC_DIR=frontend/dist \
  uv run gunicorn backend.api:app -k uvicorn.workers.UvicornWorker -w 2 -b 0.0.0.0:8000 --timeout 120
```

Open **http://localhost:8000** — the web UI *and* the API are served from the same
origin. (`uv run uvicorn backend.api:app --port 8000` works too for a single worker.)

> [!TIP]
> **If `/` returns 404**, `PII_STATIC_DIR` is unset or not pointing at a real
> directory: the API runs fine but no web UI is mounted. Make sure you ran
> `npm run build` (so `frontend/dist/` exists) and started the backend from the repo
> root with `PII_STATIC_DIR=frontend/dist`. This is exactly what the Docker image
> does — it bakes `dist/` in and sets `PII_STATIC_DIR` for you.

### CLI (batch)

```bash
# Redact files or directories in place (uses engine.name from config.toml)
uv run pii-redact example/GOÄ_Rechnung1.pdf
uv run pii-redact example/            # every jpg/jpeg/png/pdf in the folder

# Override the engine per run
PII_ENGINE=onnx uv run pii-redact example/
```

Output is written next to each input: a PDF comes back as `<name>_redacted.pdf`,
an image as `<name>_redacted.jpg` — images are always re-encoded as JPEG, exactly
as `POST /api/redact` returns them. Already-redacted files are skipped, and a file
that cannot be read (or that trips the page/pixel limits) is reported on stderr
without stopping the rest of the batch.

**The flags are the [`POST /api/redact`](#post-apiredact--find-the-pii-and-black-it-out)
query parameters**, same names, same defaults, same validation — so a run can be
translated into a request (and back) without a lookup table:

```bash
uv run pii-redact --no-unwarp --pdf-dpi 300 example/GOÄ_Rechnung1.pdf
uv run pii-redact --json-output example/   # -> <name>_redacted.json, the API's report
```

| flag | default | meaning |
|---|---|---|
| `--unwarp` / `--no-unwarp` | `redaction.unwarp` | flatten the photographed page before OCR |
| `--json-output` | off | write the JSON report to `<name>_redacted.json` *instead of* the document — the report embeds it as `redacted` |
| `--pdf-dpi N` | `redaction.pdf_dpi` | rasterization DPI for PDF input |
| `--jpeg-quality N` | `redaction.jpeg_quality` | quality of every JPEG produced |
| `--debug` | off | add the detection trace to the report as `debug` (needs `--json-output`) |

A flag you leave off is not sent, so its value comes from `config.toml` — the
single home of every default, for the CLI as for the service. Values are checked
by the same model the endpoint uses, so `--pdf-dpi 10` fails before any file is
touched, with the message the API would have returned as its `400` detail.

### Docker

One image serves both the redaction API and the web UI. `backend` serves the
built Svelte SPA as static files on the same origin, and **all model files are baked
in at build time**, so the container runs fully offline.

```bash
# Build for linux/amd64 (paddle has no linux-arm64 wheel; on Apple Silicon this
# runs under emulation).
docker build --platform linux/amd64 -t pii-redact .
docker run --rm -p 8000:8000 pii-redact
# open http://localhost:8000  (web UI)  ·  GET /health  ·  POST /api/...
```

- Multi-stage build: a Node stage builds the SPA; a `python:3.13-slim` stage runs
  the service. Only runtime deps are installed (`uv sync --no-default-groups` — no
  pytest, no notebook tooling).
- **Engines**: the image ships **native + onnx**, which is every engine there is.
- A build-time warmup ([docker/warmup.py](docker/warmup.py)) bakes the Paddle
  (native + ONNX) and UVDoc/doc-orientation models into the image (the spaCy model is
  a pip package). Runtime is offline — verify by running with **no network** and
  exec-ing a health check:
  ```bash
  docker run -d --name pii --network none pii-redact
  docker exec pii curl -fsS http://localhost:8000/health   # {"status":"ok",...}
  ```
- Tune workers with `-e WEB_CONCURRENCY=N` (default 1 — each worker loads the full
  model set into RAM; scale via container replicas, needs ~4 GB RAM each).
- Three config keys are overridable per container, without rebuilding or mounting a
  `config.toml`:
  ```bash
  docker run --rm -p 8000:8000 \
      -e PII_ENGINE=native -e PII_UNWARP=false -e PII_REDACT_REGIONS=false pii-redact
  ```
  The two booleans accept `true|false|1|0|yes|no|on|off`; anything else fails at
  startup rather than being silently ignored. For any other key, mount a file and
  point `PII_CONFIG` at it (`-v ./my.toml:/app/my.toml -e PII_CONFIG=/app/my.toml`).

### Server (API only)

```bash
# Dev (single worker, auto-reload off)
uv run uvicorn backend.api:app --host 0.0.0.0 --port 8000

# Production: N worker processes, each preloads the configured engine. This is
# how the service scales across the GIL — the heavy OCR/NER work runs in native
# code, and process-level workers give true parallelism (no external LB needed).
uv run gunicorn backend.api:app -k uvicorn.workers.UvicornWorker -w 2 -b 0.0.0.0:8000 --timeout 120
```

## REST API

Two endpoints, plus `GET /health` → `{"status":"ok","engine":{"name":"onnx",
"ocr":"onnxruntime","classifier":"presidio"}}`. The engine is fixed by `config.toml`
(or `PII_ENGINE`) and is **not** selectable per request. All options are query
parameters; an unknown one is a `400`, so a typo (`?unwrap=false`) cannot silently
do nothing.

### `POST /api/redact` — find the PII and black it out

Body: the **raw file** — PNG, JPEG or PDF bytes, not multipart.

| param | values | default | meaning |
|---|---|---|---|
| `unwarp` | `true` \| `false` | `redaction.unwarp` | flatten the photographed page before OCR |
| `json-output` | `true` \| `false` | `false` | return the JSON report, which embeds the file, instead of the bare file |
| `pdf-dpi` | integer | `redaction.pdf_dpi` | rasterization DPI for PDF input |
| `jpeg-quality` | 1–100 | `redaction.jpeg_quality` | quality of every JPEG produced |
| `debug` | `true` \| `false` | `false` | add the detection trace to the report; **requires `json-output=true`** |

**By default the file comes back, the same kind you sent:**

```bash
curl -X POST --data-binary @example/GOÄ_Rechnung1.pdf \
  -H "Content-Type: application/pdf" \
  http://localhost:8000/api/redact -o redacted.pdf
```

- PDF in → `application/pdf`, every page redacted, pages keeping their original
  physical size.
- PNG or JPEG in → `image/jpeg`, redacted. **Images always come back as JPEG.**

The file response carries no metadata: a document with nothing to redact and one
where detection failed both come back as a file. Use `json-output=true` when you
need to know *what* was found.

**`json-output=true` — the same work, reported instead of returned:**

```jsonc
{
  "unwarped": true,                          // whether dewarping actually ran
  "pages": [{
    "index": 0, "width": 1654, "height": 2339,
    "boxes": [[120, 88, 410, 118]],
    "image": { "content_type": "image/jpeg", "data": "<base64>" }
  }],
  "redacted": { "content_type": "application/pdf", "data": "<base64>" }  // image/jpeg for an image
}
```

- **`pages[].boxes` are always in the pixel space of `pages[].image`** — the image
  in the same entry, at its stated `width`/`height`. Never the uploaded file's
  coordinates: if `unwarp=true` the geometry changed, a PDF page was rasterized
  at `pdf-dpi`, and an uploaded image carrying an EXIF orientation tag was rotated
  upright on the way in. This is the one coordinate rule in the API.
- **`pages[].image` is NOT redacted.** It is the page the boxes describe, for review
  and editing — show it only to someone allowed to see the original.
- **`redacted` is the finished document**, always present: exactly the bytes the
  same request without `json-output` would have returned — `application/pdf` for a
  PDF, `image/jpeg` for an image. So one call gets you both the report *and* the
  result; you never re-run the models just to fetch the file.
- The report does not name the engine — it is fixed per process, so `GET /health`
  is where to read it.

**`debug=true` — why each box exists.** The report gains one more key, `debug`:
the detection trace as plain text, the same stream `PII_LOG_LEVEL=DEBUG` writes to
the log.

```
--- region 2  text  0.93  [134, 175, 368, 286] ---
  wholetext: 'Herrn | Max Mustermann | Musterstr. 13 | 12345 Musterhausen'
  line @(134,175 234x28 conf=99.40): 'Herrn'
      SALUTATION 'Herrn' [rule SALUT 1.00]
      -> REDACT
  line @(134,210 234x28 conf=99.99): 'Max Mustermann'
      PERSON 'Max Mustermann' [presidio 0.85]
      -> REDACT
  -> region REDACT (text 4/4 lines)
```

Region first, then what is inside it — because a line's fate depends on the block
it was read in. Each region carries its type, the detector's score and its pixel
box, then the text it contributed to the document (`wholetext`, the reading order
the classifier actually saw), then every line with the spans that touched it and
its verdict, then the region's own. A span prints as `LABEL 'text' [source score]`,
and the source names the detector to go and fix when the box is wrong: a rule by
name (`rule DE_STREET`), `labeled-value` for the spatial label↔value matcher,
`presidio` for the classifier, `name-memory` for pass two. Regions holding no OCR
line at all — a logo, a stamp — are narrated too, since they are exactly the black
rectangles a text-only trace could never account for; lines no region claimed
follow under `--- unclaimed lines ---`. It is how a wrong box is diagnosed without sending the document
anywhere; pages are marked off with `=== page N ===`. `debug=true` on its own is a
`400`: the file response carries no metadata, so there would be nowhere to put it.

### `POST /api/assemble` — turn (edited) boxes into a document

No models, no OCR, no unwarping: it fills rectangles and packages the result. Call
it once a human has reviewed what `/api/redact` reported.

```bash
curl -X POST -H "Content-Type: application/json" \
  -d '{"pages":[{"data":"<base64 jpeg>","boxes":[[10,5,30,25]]}]}' \
  "http://localhost:8000/api/assemble?format=pdf&dpi=200" -o redacted.pdf
```

```jsonc
{ "pages": [ { "content_type": "image/jpeg",   // optional, informational
               "data": "<base64 PNG or JPEG>",
               "boxes": [[10, 5, 30, 25]] } ] }
```

| param | values | default | meaning |
|---|---|---|---|
| `format` | `pdf` \| `jpeg` | `pdf` | `jpeg` requires exactly one page |
| `dpi` | integer | `redaction.pdf_dpi` | the resolution the images represent, so PDF pages get their true physical size |
| `jpeg-quality` | 1–100 | `redaction.jpeg_quality` | |

Boxes are in the pixel space of the image in the same entry. Returns
`application/pdf` or `image/jpeg`.

The two endpoints share only the box format: `/api/redact` reports what it found,
`/api/assemble` turns findings — reviewed, corrected, whatever — into a document.
Because assembly never runs the unwarper, the boxes cannot drift from the pixels;
the client fills the exact image it was given.

### Errors

Inputs are validated: unsupported media types are rejected (`415`), oversized bodies
by both the `Content-Length` header and a streaming cap (`413`, limit
`api.max_upload_bytes`), and images must decode within `api.max_image_pixels` (`400`,
decompression-bomb guard). Documents over `redaction.max_pages` are rejected (`400`),
as are malformed bodies and bad parameters. Errors are `{"detail": "…"}`.

## Configuration

All knobs live in [`config.toml`](config.toml) (engine, redaction fill/padding,
upload limits, worker count). Five environment variables override it, for
containers where editing the file is awkward:

| Variable | Overrides |
|---|---|
| `PII_CONFIG` | the path to the config file itself |
| `PII_ENGINE` | `[engine].name` |
| `PII_UNWARP` | `[redaction].unwarp` |
| `PII_REDACT_REGIONS` | `[redaction].redact_regions` |

`PII_UNWARP` differs in reach from the other one, because `unwarp` also has a
wire name: `PII_UNWARP` sets the **default** for `?unwarp=` and `--unwarp`, so a
request that names the parameter still wins, while `redact_regions` is neither a
query parameter nor a flag and so that variable is absolute. `PII_LOG_LEVEL=DEBUG`
(API and CLI) logs every OCR line with its box, plus each classifier match with its
score and the recognizer/context that produced it, followed by the redact verdict —
useful for seeing exactly why a line was or wasn't redacted.

Every key is optional — delete any of them and the default applies — but the file
is validated on load and a bad one **fails at startup rather than on the first
request**: an unknown key or section, a number out of range (`jpeg_quality = 500`),
or an engine that doesn't exist all raise immediately, naming the offender. A
mistyped key is a mistake, not a no-op.

### Region redaction

The sender of an invoice — the practice, the clearing house — identifies itself in
places no per-line detector can reach: a letterhead is usually a **logo**, and OCR
returns no line for a graphic. So `[redaction].redact_regions` (on by default) runs
a trained **layout detector** (PP-DocLayout, through the already-installed
`paddleocr.LayoutDetection` — no extra dependency) over the page and adds boxes
that are not tied to any single OCR line.

| Key (`[redaction.layout]`) | Default | Meaning |
|---|---|---|
| `model_name` | `PP-DocLayout_plus-L` | the detector checkpoint; `PP-DocLayout-{S,M,L}` and `PP-DocLayoutV2/V3` swap in by name |
| `threshold` | `0.35` | minimum detection confidence |
| `layout_nms` | `true` | prune near-duplicate overlapping detections |

The detector returns typed regions — `text`, `table`, `header`, `footer`, `image`,
`seal`, `doc_title` and more — and two rules turn them into boxes:

- a region typed **`image`, `seal`, `header`, `footer` or `aside_text` is
  blackened on sight**, whatever it holds. `image` and `seal` cover the letterhead
  logo, a practice stamp and a payment QR code — graphics that OCR never reports,
  so the detected type is the only evidence they exist.
- **every other region is blackened once at least 40 % of its OCR lines** were
  flagged by the per-line pass. This is what covers the lines *between* the hits: a
  recipient address block is a `text` region whose street and ZIP+city lines
  already match a static rule, so those hits carry the c/o line, the company
  recipient and the name line OCR garbled. The bar sits **below a half**
  deliberately: a two-line sender block where only the line naming the company
  matched is the common shape, and at a strict majority it survived.

A line belongs to the region containing its **center point**; where regions nest,
the smallest one wins, since the tighter box is the more specific claim. Blank
lines are not counted — nothing can ever redact one, so counting them would only
drag a block below the threshold.

One consequence worth knowing: the ratio rule has **no exception for `table`**.
On a page where most item rows carry a patient name, the fee table goes black with
them.

The threshold sits **below the model's own 0.5 default** because photographed pages
depress every confidence score: on the sample corpus a scanned page's fee table
detects at 0.62–0.99, the phone photos at 0.38–0.43. Detection costs ~0.22 s per
page on ONNX Runtime (~0.58 s native) and is near-constant in page size — the
model resizes the page to its own fixed input, so a 3024×4032 photo costs what a
960×1280 one does.

`--debug-layout` (a CLI flag, not a query parameter) writes `<stem>_layout.jpg`
with every detected region outlined and labeled, the always-blackened ones tinted
red, so you can see what the model saw before trusting what it blackened. It is
drawn *during* the redaction, from that pass's own raster, OCR lines and regions,
so it costs a JPEG write and nothing else — the flag does not read the page a
second time.

`redact_regions = false` (or `PII_REDACT_REGIONS=false` in the environment) drops
the pass entirely. This is a **config-only** setting — unlike `unwarp` it is not a
query parameter, so it is fixed per process like the engine. The boxes it produces
are ordinary boxes: they appear in the JSON report and are editable (and deletable)
in the web UI like any other.

### Engine presets

A single pipeline (unwarp → OCR → read the page in layout order → classify → draw
a box) is configured
along two independent axes, selected by an **engine preset** in `config.toml`:

| Preset (`engine.name`) | Inference engine | PII classifier |
|---|---|---|
| `native` *(default)* | Paddle native | Presidio (spaCy NER + custom regex recognizers) |
| `onnx` | ONNX Runtime, multi-core | Presidio — *the same classifier as `native`* |

The two presets differ **only** in the inference engine: same detection,
recognition and layout models, same Presidio classifier, same results. The engine
is one machine-level choice, so it drives **every** Paddle model the page passes
through — text detection, text recognition and the layout detector alike; a page
whose text models run on ONNX Runtime while its layout model does not would be a
configuration disagreeing with itself. `native` is the baseline; `onnx` is the
fastest (~3.3× faster OCR and ~2.6× faster layout detection, at identical output —
measured over 12 sample pages, all 194 detected regions identical in type and
pixel box).
Either axis can be overridden explicitly (`engine.ocr_backend` /
`engine.classifier`) instead of naming a preset. Presidio is currently the only
classifier — a zero-shot NER engine (GLiNER) was tried and dropped, since it was
worse on this corpus and pulled `torch` plus ~4.6 GB of CUDA libraries CPU
inference never uses.

**`engine.det_box_thresh`** (default `0.5`, below PaddleOCR's own `0.6`) is the
minimum detector score for a text box. It is a *mean* over the box, so a shape,
not faintness, is what pushes a line under it: one line of 6 pt type running the
full width of the page averages lower than the 11 pt table above it even where
its ink is just as dark. A photographed A4 invoice's imprint footer measured
0.5–0.6 and so was not detected **at all** — no box, no text, and hence no footer
band either, since the band needs a sender anchor to exist. Raise it if
background texture on a page is being read as text.

## How it works

Every engine runs the same pipeline per page ([`backend/pipeline.py`](backend/pipeline.py)),
built from three composable primitives — `unwarp()`, `compute_boxes()` (OCR +
classify), and `apply_boxes()` (fill):

1. **Unwarp** the page with UVDoc. Photographed paper curls at the edges, tilting the
   marginal lines so the detector misses them (e.g. the second bank line, the bottom
   managing-director line). Unwarping runs as an **explicit separate step**
   (`create_pipeline("doc_preprocessor")` with `use_doc_unwarping=True`) that returns the
   flattened image; PaddleOCR's own `use_doc_unwarping` is deliberately **disabled** —
   if OCR unwarped internally, the returned boxes would refer to an unwarped image we
   never get back, with no way to map them onto the curled original. By flattening
   first ourselves we hold that image, OCR + redact it, and the boxes align by
   construction.
2. **OCR per line, then read the page as one text.** PaddleOCR returns one box +
   text per line, in a plain top-to-bottom order that interleaves the two columns of
   these invoices. The layout regions fix that: lines are grouped by the region that
   holds them, ordered in bands within it (so a table row reads *across* even when its
   cells sit a few pixels apart), and nested blocks enter whole at their own position.
   The joined text is what gets classified — which is what lets a name broken across a
   wrap ("Max" / "Mustermann" on two lines) be seen as one entity at all. Classifying
   per line, as this used to, could not.
3. **Classify** the page (this is where the engines differ, see below). Every
   detector — the regex rules and the model alike — returns labeled character
   spans, and a line is redacted when a span touches it. A pattern match that only
   exists because two lines were joined is discarded: `\s` in a regex matches the
   joining newline, so a recognizer would otherwise glue a number at one line's end
   to the first word of the next. A person's name is the one entity allowed to
   wrap. The
   shared deterministic rules — salutation, titled name (`Dr. Weber`), German
   street / PLZ+city, sender identity (legal form, URL/e-mail/phone, registry and
   banking identifiers), and the spatial date-of-birth matcher
   ([`backend/rules.py`](backend/rules.py)) — are applied uniformly first, then
   the model-based classifier.
4. **Draw** a filled black rectangle over the line's box (with a 2 px pad) if it is judged
   to contain PII.
5. **Add the region boxes** — whole layout regions the detector typed as page
   furniture or a graphic, plus any region enough of whose lines were flagged
   ([`backend/layout.py`](backend/layout.py)) — the only boxes not derived from an
   OCR line, and therefore the only ones that can cover a letterhead logo or a
   payment QR code. See [Region redaction](#region-redaction).

### `presidio` classifier

Redacts these entities: `PERSON`, `IBAN_CODE`, `BIC_CODE`, `DE_ADDRESS`, `EMAIL_ADDRESS`,
`KONTO`, `PHONE_NUMBER`, `CREDIT_CARD`.
`LOCATION` is deliberately excluded — the NLP model fires it on the letterhead, and the
recipient's street/city is already covered precisely by `DE_ADDRESS`. Custom recognizers
on top of the built-in IBAN/e-mail/credit-card ones:
- **BIC/SWIFT** — `AAAABBCC[DDD]`, case-sensitive, low base score + `bic`/`swift` context
  so all-caps German words like `RECHNUNG` don't match.
- **PHONE_NUMBER** — `python-phonenumbers`, restricted to the `DE` region only; the
  default region list runs 8 regional matchers per line and lets foreign formats match
  random digit columns. Two shapes are dropped: a dotted date (`09.07.2026`, optionally
  swallowed together with the code column beside it, `12.12.15 51-61`) — dates aren't PII
  except the birthdate, which is handled spatially (see below) — and an **undelimited
  digit run** (`2106315267`), which is a lab or order number; a real number here is
  written with a separator (`0231 000000- 000`) or carries a `Tel`/`Fax` label, and a
  labeled one is already caught deterministically before the classifier runs.
- **KONTO** — a German account number or bank code *with its label* (`Kto.`, `Konto-Nr.`,
  `BLZ`, `Bankleitzahl` followed by digits), since lines are classified one at a time and
  the label is always on the same line as the number.
- **DE_ADDRESS** — a German street pattern (suffix attached, `Musterstrasse 23`, or its
  own capitalized word, `Muster Straße 23`) and a PLZ + city pattern. Both accept the
  all-caps form a letterhead prints. For the street, the *suffix* is case-insensitive and
  the name part may be all caps (`MUSTERSTR.23`, house number glued on), with the
  leading capital still required. A city is either capitalized (`Musterstadt`, `Ulm`) or
  **all caps with at least four letters** (`MUSTERSTADT`) — four is what separates a real
  city from the short all-caps noise a Leistungstext is full of (`15118 MID`), at the
  price of `ULM`/`HOF`/`AUE` written that way. The postcode must also **start a token**:
  a Heilmittel position number ends in five digits and is followed by its Leistungstext
  (`44/20101 Massage`, `49/21520 Naturmoor`), which is the ZIP+city shape exactly — down
  to `20101` being a real Hamburg postcode, so only the token boundary can tell them
  apart. A hyphen is still admitted after a letter (`D-12345 Musterhausen`) and refused
  after a digit (`44-20101`). The space between postcode and city is **optional** — a
  narrow address column prints them flush and OCR returns `12345Musterstadt` as one token.
  The city pattern is *imported* from `backend/rules.py` rather than restated, since the
  two are meant to agree.

### Special cases handled

- **Name in the next column.** A patient block often prints `Patient:` (or `Versicherte`,
  `Name`, `Person`, `Mitglied`, …) alone in one cell with the name beside it — two
  separate OCR lines, so no same-line rule can pair them, and a bare `Muster,Uwe` gives the
  NER model nothing to hold on to. The same spatial matcher used for birth dates carries
  a **name** row: a label standing alone in its cell makes a two-token name (`Max
  Mustermann`, `MUSTER, ANDREA`, `Muster,Uwe`) on its row a value. The label must be the
  *whole* cell — an unanchored `Patient` would turn a Leistungstext sentence into a label
  and blacken whatever capitalized pair happened to share its row.
- **Date of birth (three layouts).** The label and the date are usually separate OCR
  lines, so a same-line rule can't see the label; birth dates are matched **spatially**
  instead, in whichever of three arrangements the document uses:
  1. *Beside* — the date's vertical center shares a row with a birth-label line
     (`Geburtstag`, `Geburtsdatum`, `geboren`, and the abbreviations `Geb.Dat.` /
     `Geb.-Dat.` / `GebDat`). The bare `geb.` is still **not** a label on its own: it also
     means *Gebühren* (the `Geb.Nr.` fee column) and would redact treatment dates.
     `Geb.Dat.` is safe because *Gebührendatum* is not a word on an invoice.
  2. *Below* — `Geburtsdatum` alone in a cell at the top of a column, the dates running
     down beneath it, so nothing shares a row with any of them. The header claims a line
     whose horizontal *center* falls in its own x-range (a header is wider than its
     dates, so overlap would reach into neighbouring columns), and the walk stops at the
     first non-date or a vertical gap over 3× the median line height — a header may never
     claim the rest of the page.
  3. *Merged* — label and date in one line, marked by the abbreviation (`geb. 30.09.1954`)
     or by the **`*` birth mark** (`*21.01.1975`, `* 21.01.1975`), which on German
     paperwork reads *geboren*. The date must follow the star directly, so a footnote
     marker introducing a sentence (`* Leistungen ab 01.01.2024`) doesn't match.

  Any of the three also makes the names on that line *name evidence*, since a name beside
  a birthdate is there because of it.
- **Salutation (Anrede).** Any line containing a salutation word (`Herr(n)`, `Frau`,
  `Familie`, …) is redacted — lone (the `Herrn` line above the address) or with a name
  (`Herr Muster`, where NER tags only the single surname token, which the multi-word guard
  drops). A lone salutation carries no information, so over-redacting it is harmless and
  keeps the rule to a single regex (`SALUT`).
- **Titled name.** An academic/medical title followed by a capitalized name
  (`Dr. Weber`, `Prof. Dr. med. Hans Müller`) is redacted by a deterministic rule —
  the NER model is unreliable around titles, missing the name after a doubled
  `Dr. Dr.` and tagging only the single token after `Dr. Weber`, which the PERSON
  multi-word guard then drops.
- **Surname, forename and birthdate on one unlabeled line.** `Muster,Andrea 05.03.11`
  — how a patient table row is written — is invisible to everything else: NER returns
  only the forename (the surname falls outside the `PERSON` span, leaving a single
  token the guard drops), and the date has no `geb.`/`Geburtsdatum` to pair with, so
  the spatial birthdate matcher can't reach it either. A deterministic rule matches the
  pair — `Surname,Forename` immediately followed by a date — which redacts the whole
  line, name and date together, and feeds the surname to the name memory. Both halves
  are required: a Leistungstext can hold two comma-joined capitalized nouns
  (`Mikroskopie,Kultur`), and a bare date in an item row is a treatment date.
- **Name memory, in two passes.** The page is read twice: pass one lets every detector
  say what it found, pass two redacts every bare recurrence of the names those findings
  named — a subject line, a Diagnose, a footer signature, where a lone surname is a token
  no model calls a person and no pattern describes. The witness may be either kind of
  detector, and the two harvest differently. A *pattern* names a person on its line, so
  the whole line is harvested: the label rules stop after a single name part
  (`Patient Mustermann` never reaches the `Max` behind it) and OCR glues the pair around
  its label in either order. A *span* says where the person is, so only the span is
  harvested — reading the whole line around a model's find made a letterhead's company
  name a remembered "name" that then blackened body text and an invoice number. Only
  `PERSON` counts as evidence; an address, a company or an identifier names no one to
  look for. And only spans that were allowed to redact their own line feed the memory,
  so a model hit dropped inside the item table cannot come back as a name and spread
  over the whole document. The match is whole-word
  (that, not letter case, is what keeps `Allgemeine` from matching a Dr. Allgemein) and
  **case-insensitive with one condition: the occurrence must start with a capital**. One
  document prints the same person as `Andrea Muster` in the address block and
  `MUSTER, ANDREA` in the patient row, so a memory holding a single casing would miss
  half of them — while a lowercase hit is the ordinary German word a surname collides
  with (`Klein` the person vs `klein gedruckt`), which is why this isn't a plain
  case-insensitive match.
- **Address block completeness.** The street / PLZ+city regexes run on every line before
  the classifier, so the Adressfeld's coverage is a property of those patterns rather than
  of the model. A model-only path leaves the standalone PLZ+city line of a recipient block
  visible — a zero-shot `address` label dropped it outright, and Presidio finds it only
  because its DE_ADDRESS recognizer is these same regexes.
- **The item table.** An invoice's body is a table of fee numbers, service texts and
  amounts; the PII sits *above* it (recipient, patient block) or *below* it (imprint,
  bank details). So the classifier — the one detector with no anchor of its own — does
  not run on table lines at all. The band is found geometrically: lines holding a German
  amount merge into rows, rows cluster by vertical gap, and a cluster of ≥2 rows spans
  the table (so a lone `Zahlbetrag` in a footer gates nothing, and a stray amount above
  the recipient block can't stretch the band over it). Every deterministic rule and the
  name memory keep working inside the table — only an *unlabeled, never-before-seen*
  name in an item row is given up.
- **PERSON false positives.** A `PERSON` needs **≥2 proper-noun tokens** ("First Last") to be
  redacted. Capitalization alone is not evidence in German — every noun is capitalized, so a
  two-word Leistungstext (`Orientierende Testuntersuchg.`) is shaped just like a name to the
  NER model, while spaCy's tagger calls `Orientierende` a NOUN. The coarse tag (`pos_`) is
  what's read, not the NER tag: `Cleed` (of the culture medium `Cleed Agar`) is `tag_=NE`
  but `pos_=ADV`. The rule also still drops single-token noise (`5.0016`).
  **Across a comma the bar is only capitalization**, because `Surname,Forename` is how a
  patient row is written and NER returns just one half of it — while the surnames it omits
  (`Bauer`, `Jäger`, `Wolf`) are ordinary German words the tagger calls nouns. That is safe
  because something on the line must still have been recognized as a `PERSON`, and the
  Leistungstexte this guard exists to reject (`Mikroskopie,Kultur`, `Summe,Betrag`,
  `Ferritin,CRP`) produce no `PERSON` entity at all — so there is no span to extend.
- **Service dates vs. birth dates.** Treatment/invoice dates (`18.02.2026`, `Rechnungsdatum
  09.07.2026`) are never in a birth-label row, so they stay visible — no over-redaction.

### Models & libraries

- **PaddleOCR 3.x** (`paddleocr`, `paddlepaddle`) — text detection + recognition,
  German model. Returns axis-aligned pixel boxes per text line (`rec_texts` / `rec_scores`
  / `rec_boxes`).
- **PaddleX `doc_preprocessor` (UVDoc)** — document unwarping, to flatten curled
  photos before OCR.
- **Presidio** (`presidio-analyzer`) — PII analysis, driven by
  a **spaCy `de_core_news_lg`** German NLP model plus custom recognizers.
- **PyMuPDF** (`pymupdf`) — renders PDF pages to images at 200 dpi.
- **Pillow** — draws the redaction rectangles.

### Where the models live

| Model | Stored in | Used by |
|---|---|---|
| PaddleOCR + UVDoc weights (see table below) | `.paddle_cache/official_models/` (project, git-ignored) | every engine |
| `de_core_news_lg` (spaCy German) | `.venv/lib/python3.13/site-packages/de_core_news_lg/` (a pip package, **not** a cache) | every engine |

The Paddle weights (~180 MB) live in **`.paddle_cache/`**; the code sets
`PADDLE_PDX_CACHE_HOME` there automatically. On first run Paddle downloads them; if you
already have them under `~/.paddlex`, copy that folder to `.paddle_cache` to skip the
download.

### The four Paddle models

Every engine shares the same OCR + unwarp front end, so each run loads the **same
four** models:

| Model in `official_models/` | Role | Loaded by |
|---|---|---|
| `PP-OCRv6_medium_det` | Text **detection** (finds line boxes) | `PaddleOCR(...)` |
| `PP-OCRv6_medium_rec` | Text **recognition** (reads the German text) | `PaddleOCR(...)` |
| `UVDoc` | Document **unwarping** (flattens curled pages) | `create_pipeline("doc_preprocessor")` |
| `PP-LCNet_x1_0_doc_ori` | Page-orientation classifier — instantiated by the `doc_preprocessor` pipeline but **disabled at predict time** (`use_doc_orientation_classify=False`), so it is loaded but not applied | `create_pipeline("doc_preprocessor")` |

The code sets its offline / quiet environment automatically (`HF_HUB_OFFLINE`,
`TRANSFORMERS_OFFLINE`, `PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK`, and paddlex log level), so
no environment variables need to be passed on the command line.


## Project layout

```
backend/            the whole Python package — pipeline, CLI and REST service
  pipeline.py       the three primitives: unwarp / compute_boxes / apply_boxes
  service.py        framework-free core shared by the CLI and the API
  api.py            FastAPI app: request reading, response shaping, static SPA
  cli.py            batch CLI (flags mirror the /api/redact query parameters)
  options.py        pydantic validation for query options and the assemble body
  config.py         config.toml schema + engine preset resolution
  rules.py          deterministic German patterns (salutation, street, birthdate)
  layout.py         PP-DocLayout regions: whole-region boxes (not line-derived)
  ocr/ classifiers/ the two swappable axes behind an engine preset
frontend/           Svelte 5 + Vite SPA, calls the REST API directly
tests/              fast tests stub the models; `-m slow` runs the real ones
docker/warmup.py    bakes the models into the image at build time
example/            a sample invoice as PDF and PNG
```

## Development

```bash
uv sync --group dev           # + pytest, httpx

uv run pytest -m 'not slow'   # fast: no ML models load (validation, rules, composition)
uv run pytest -m slow         # end-to-end on example/GOÄ_Rechnung1.pdf (loads real models)
uv run pytest --regression    # + the OCR-replay snapshots (loads the classifier only)
```

### Regression testing on frozen OCR

OCR is the slow half of the pipeline and not the half that changes when a rule is
tuned, so it is run **once** per sample into a `<stem>_ocr.txt` and everything
downstream — rules, labeled values, item table, name memory, classifier, region
bands — is replayed against that text alone. A corpus replays in seconds, and the
snapshot beside it (`<stem>_ocr.expected.txt`) holds **one asserted verdict per OCR
line**, both `REDACT` and `keep`, so over-redaction is caught as readily as a miss.

```bash
uv run python -m backend.replay dump example/GOÄ_Rechnung1.png --out-dir tests/regression
uv run python -m backend.replay check tests/regression            # exit 1 on a diff
uv run python -m backend.replay check tests/regression --update   # rewrite the snapshots
uv run python -m backend.replay check <dir> --ignore-text         # after scrubbing PII
```

A verdict is *effective*: a line counts as redacted when any box covers ≥90% of it
(a band drawn over a line the per-line pass kept still redacts it) and as kept at
≤10%; the gap between is reported as `PARTIAL` and matches neither expectation.
Snapshots are generated, and a dump goes stale if OCR starts reading the page
differently — re-run `dump` after changing `det_box_thresh` or the unwarp step.

A dump is a document's full text, and the snapshot beside it repeats every line,
so both hold whatever the original did. A real invoice is made committable by
**scrubbing** the dump — every real name, address and identifier replaced by a
placeholder of the same shape, keeping the layout the geometry passes are tuned
on. `--ignore-text` checks that edit: it compares geometry, verdicts and regions
while letting the text differ, so it answers whether the placeholder still
redacts where the real value did. Then `--update` rewrites the snapshot from the
scrubbed text — without that second step the snapshot still carries the original.

Adding or removing a dependency? Update [`LICENSE.md`](LICENSE.md) in the same
change — it is the third-party license inventory, and it determines the license of
the built image.

## Limitations & safety

- **Detection is best-effort.** It is statistical NER plus hand-written rules, not a
  guarantee. Missed PII is possible on layouts unlike the ones it was tuned for.
  **Review every document before releasing it.** The web UI exists for exactly this.
- **Region redaction covers whole regions, not lines.** Once a region qualifies,
  everything inside it goes — a page number sharing the region with a bank line
  included. That is the price of covering what no text rule can reach: a
  letterhead graphic, a stamp, a payment QR code. It cuts the other way too, since
  a region only qualifies by its *type* or by enough of its lines being flagged:
  a two-line letterhead whose tagline matches nothing survives if the practice
  name did not match either. Tune `[redaction.layout]` or set
  `redact_regions = false` (or `PII_REDACT_REGIONS=false`) if it costs you more
  than it buys.
- **Machine-readable codes are covered only as graphics.** There is no dedicated
  barcode pass: a payment QR is blackened when the layout detector reports an
  `image` region over it, which it does on the sample corpus, but a code the
  detector misses stays readable — and a scanner does not need the surrounding
  text to be legible. Check `--debug-layout` on a page whose codes matter.
- **Redaction is destructive drawing, not text removal**, which is what makes it safe:
  output pages are rasterized images with filled rectangles, so there is no selectable
  text layer left underneath to recover. The trade-off is that redacted PDFs are images
  and are no longer searchable.
- **The file response tells you nothing.** A clean document and a failed detection are
  indistinguishable — use `json-output=true` if you need to know what was found.
- **`pages[].image` in the JSON report is the *un*redacted page.** Treat that payload
  as sensitive as the original.
- **German invoices only.** The rules encode German salutations, street forms and
  PLZ patterns; other languages and layouts are out of scope.

## License

This project's own source code is **MIT** — see [`LICENSE`](LICENSE).

Third-party packages and pretrained models carry their own licenses, inventoried in
[`LICENSE.md`](LICENSE.md). Note one consequence there: **the built Docker image is
AGPL-3.0**, because it bundles PyMuPDF (AGPL-3.0, dual-licensed with a paid Artifex
commercial alternative). That does not relicense this repository's code; it means
anyone served by a deployment of the image can request the complete source of the
combined work.
