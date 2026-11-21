# REST API

Three routes, all on one origin (port 8000 by default):

| Route | Purpose |
|---|---|
| `GET /health` | liveness and which engine the process runs |
| `POST /api/redact` | detect PII in a PNG/JPEG/PDF and black it out |
| `POST /api/assemble` | turn page images + (edited) boxes into a file — no models involved |

All options are **query parameters**, except the word lists of `/api/redact`, which
go in the body. An unknown parameter, a misspelled one (`?unwrap=false`) or an
out-of-range value is a `400`, never silently ignored. Errors are always
`{"detail": "…"}`.

Every response carries a strict `Content-Security-Policy` (same origin only, no
framing), `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY` and
`Referrer-Policy: no-referrer`; API responses are `Cache-Control: no-store`. There
is no Swagger/ReDoc page (it would load from a CDN); `GET /openapi.json` remains.

This page is the API contract — change it together with the code.

## `GET /health`

```bash
curl http://localhost:8000/health
```

```json
{
  "status": "ok",
  "engine": { "ocr": "onnxruntime", "classifier": "presidio" },
  "classifiers": ["presidio"]
}
```

- `engine.ocr` — the OCR inference runtime, fixed per process.
- `engine.classifier` — the classifier a request gets when it does not name one.
- `classifiers` — every value `?classifier=` accepts on this server. `guard-omni`
  is listed only when enabled (`[engine].guard_omni`, off by default).

Both come from [`config.toml`](Configuration.md#engine) or the matching environment
variables.

## `POST /api/redact`

Body, one of:

- the **raw file bytes** (not multipart) with a matching `Content-Type`:
  `image/png`, `image/jpeg` or `application/pdf`;
- `Content-Type: application/json` with the file as base64 and the word lists —
  the only way to send word lists:

  ```jsonc
  {
    "content_type": "application/pdf",   // image/png | image/jpeg | application/pdf
    "data": "<base64>",                   // decoded size ≤ api.max_upload_bytes
    "redact_words": ["Andrea Muster"],    // optional
    "keep_words": ["Musterpraxis"]        // optional
  }
  ```

| Parameter | Values | Default | Meaning |
|---|---|---|---|
| `unwarp` | `true` \| `false` | `redaction.unwarp` | flatten a photographed page before OCR |
| `json-output` | `true` \| `false` | `false` | return the JSON report instead of the bare file |
| `pdf-dpi` | 36–1200 | `redaction.pdf_dpi` | rasterization DPI for PDF input |
| `jpeg-quality` | 1–100 | `redaction.jpeg_quality` | quality of every JPEG produced |
| `classifier` | `presidio` \| `guard-omni` | `engine.classifier` | which classifier runs; the first request naming one loads it. `guard-omni` only if enabled, otherwise `400` |
| `debug` | `true` \| `false` | `false` | add the detection trace to the report — **requires `json-output=true`** |
| `granularity` | `word` \| `line` | `redaction.granularity` | `word` boxes only the redacted words of a line, `line` the whole line |

**Word lists** (`redact_words`, `keep_words` in the JSON body; ≤ 200 entries of
1–100 chars each). They name people, so they are never query parameters — a URL
lands in access and proxy logs — and the server never logs them. An entry matches as a whole word or phrase, case-insensitively;
whitespace inside a phrase matches any whitespace, line breaks included. Entries are
folded like the OCR text (`René` meets `Rene`), and from four letters on an entry also
matches its OCR variants (`llja` for `Ilja`, `Andréa` for `Andrea`).
`redact_words` wins over every kept line (item table, dates, …) and over
`keep_words`. `keep_words` vetoes rule, classifier and memory findings; it does not
lift whole-region or signature boxes. An entry in both lists is a `400`.

```bash
jq -n --rawfile data <(base64 < example/GOÄ_Rechnung1.pdf | tr -d '\n') \
   '{content_type: "application/pdf", data: $data,
     redact_words: ["Andrea Muster"], keep_words: ["Musterpraxis"]}' |
curl -X POST --data-binary @- -H "Content-Type: application/json" \
     http://localhost:8000/api/redact -o redacted.pdf
```

### File response (default)

```bash
curl -X POST --data-binary @example/GOÄ_Rechnung1.pdf \
     -H "Content-Type: application/pdf" \
     "http://localhost:8000/api/redact?unwarp=false" -o redacted.pdf
```

- PDF in → `application/pdf` out, every page redacted at its original physical size.
- PNG or JPEG in → `image/jpeg` out. **Images always come back as JPEG.**
- The response carries **no metadata**: a document with nothing to redact and one
  where detection found nothing look identical. Use `json-output=true` to see what
  was found.

### JSON report (`json-output=true`)

```bash
curl -X POST --data-binary @example/GOÄ_Rechnung1.png \
     -H "Content-Type: image/png" \
     "http://localhost:8000/api/redact?json-output=true&classifier=presidio" -o report.json
```

```jsonc
{
  "unwarped": false,               // whether dewarping ran
  "classifier": "presidio",        // the classifier that produced these boxes
  "pages": [{
    "index": 0, "width": 1654, "height": 2339,
    "boxes": [[120, 88, 410, 118]],                               // [x0, y0, x1, y1]
    "notes": [{ "box": [300, 88, 410, 118], "text": "1975" }],   // printed on top
    "image": { "content_type": "image/jpeg", "data": "<base64>" } // NOT redacted
  }],
  "redacted": { "content_type": "application/pdf", "data": "<base64>" },
  "debug": "…"                     // only with debug=true
}
```

- **Coordinate rule:** `pages[].boxes` and `pages[].notes` are in the pixel space of
  `pages[].image` of the same entry — never in the uploaded file's. Dewarping, PDF rasterization at
  `pdf-dpi` and EXIF rotation all change the geometry.
- **`pages[].notes`** are text printed white on their own black box, after every box
  is filled — today the birth year where a redacted birthdate stood. Usually empty.
- **`pages[].image` is the clean page**, for review and editing. Treat it like the
  original document.
- **`redacted`** is exactly the file the same request without `json-output` returns
  (PDF for a PDF, else JPEG) — one call gives you the report *and* the result.

### Debug trace (`debug=true`)

`debug` holds the detection trace as plain text — the same stream
`PII_LOG_LEVEL=DEBUG` writes to the log. It is organised region first, because a
line's fate depends on the block it was read in:

```
--- region 2  text  0.93  [134,175,368,286] ---
  wholetext: 'Herrn | Max Mustermann | Musterstr. 7 | 12345 Musterhausen'
  line @(134,175 234x28 conf=99.40): 'Herrn'
      SALUTATION 'Herrn' [rule SALUT 1.00]
      -> REDACT
  -> region keep
```

A region of a blackened-whole type (`footer`, `image`, …) ends with
`-> region REDACT (footer)` instead.

A span prints as `LABEL 'text' [source score]`; the source names what to look at
when a box is wrong — a rule (`rule DE_STREET`), `labeled-value`, `name-memory`,
`birthdate-memory`, `user-list` (a `redact_words` entry), or the classifier (`presidio`, `guard-omni`). A span a kept
line rejected prints the same way after `rejected `. A `name-memory`
or `birthdate-memory` span quotes the remembered name or date it matched; each
enters the memory with a page-level line `name memory 'Muster' <- '<line>'` or
`birthdate memory '01.02.1980' <- '<line>'` before the regions. Lines in no region follow under
`--- unclaimed lines ---`; pages are separated by `=== page N ===`. See
[Redaction](Redaction.md) for what each detector does.

## `POST /api/assemble`

Fills rectangles and packages the pages — **no OCR, no models, no dewarping**. Call
it after a human has reviewed and edited the boxes from `/api/redact`; the boxes
land exactly on the pixels the client sent.

Body (`Content-Type: application/json`):

```jsonc
{
  "pages": [
    { "data": "<base64 PNG or JPEG>",
      "boxes": [[10, 5, 30, 25]],          // in this image's pixel space
      "notes": [{ "box": [40, 5, 80, 25], "text": "1975" }],  // optional, 1-40 chars
      "content_type": "image/jpeg" }       // optional, informational
  ]
}
```

| Parameter | Values | Default | Meaning |
|---|---|---|---|
| `format` | `pdf` \| `jpeg` | `pdf` | `jpeg` requires exactly one page |
| `dpi` | 36–1200 | `redaction.pdf_dpi` | the DPI the images were rasterized at — sets the PDF's physical page size |
| `jpeg-quality` | 1–100 | `redaction.jpeg_quality` | |

```bash
curl -X POST -H "Content-Type: application/json" \
     -d '{"pages":[{"data":"<base64 jpeg>","boxes":[[10,5,30,25]]}]}' \
     "http://localhost:8000/api/assemble?format=pdf&dpi=200" -o redacted.pdf
```

Pass the `pdf-dpi` the pages came from, or an A4 page comes back at the wrong size.

### Review round trip

```bash
# 1. Detect, keep the report
curl -sX POST --data-binary @invoice.pdf -H "Content-Type: application/pdf" \
     "http://localhost:8000/api/redact?json-output=true&pdf-dpi=200" -o report.json

# 2. Edit boxes in report.json (add, remove, move), then send pages back
jq '{pages: [.pages[] | {data: .image.data, boxes: .boxes, notes: .notes}]}' report.json \
  | curl -sX POST -H "Content-Type: application/json" --data-binary @- \
         "http://localhost:8000/api/assemble?format=pdf&dpi=200" -o redacted.pdf
```

## Errors

| Status | When |
|---|---|
| `400` | bad or unknown parameter, empty or malformed body, undecodable image or PDF, image over `api.max_image_pixels` (decompression-bomb guard), more pages than `redaction.max_pages`, `debug=true` without `json-output=true`, a classifier not enabled on this server |
| `413` | body larger than `api.max_upload_bytes` (`/api/redact`) or `api.max_assemble_bytes` (`/api/assemble`) — checked on `Content-Length` and while streaming |
| `415` | unsupported content type or image format |
| `503` | the worker already holds one running and `api.max_queued_per_worker` waiting requests; retry after `Retry-After` seconds |

Limits are set in [`config.toml`](Configuration.md#api).
