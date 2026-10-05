# Configuration

All settings live in [`config.toml`](../config.toml) in the repo root. Every key is
optional — a missing key takes the code default from
[`backend/config.py`](../backend/config.py). The file is **validated at startup**: an
unknown key or section, a value out of range or an unknown engine name stops the
process with a message naming the field. A typo is an error, not a no-op.

## Environment variables

| Variable | Effect |
|---|---|
| `PII_CONFIG` | path to the config file (default: `config.toml` in the repo root) |
| `PII_OCR_BACKEND` | overrides `[engine].ocr_backend` |
| `PII_CLASSIFIER` | overrides `[engine].classifier` |
| `PII_GUARD_OMNI` | overrides `[engine].guard_omni` |
| `PII_UNWARP` | overrides `[redaction].unwarp` |
| `PII_REDACT_REGIONS` | overrides `[redaction].redact_regions` |
| `PII_LOG_LEVEL` | log level of the `backend` logger (`INFO`); `DEBUG` logs the full detection trace |
| `PII_STATIC_DIR` | directory of the built web UI the API serves at `/` (see [Running](Running.md)) |

The overriding variables are validated like the file. Booleans accept
`true|false|1|0|yes|no|on|off`.

**Defaults vs. absolute values.** `unwarp`, `classifier`, `pdf_dpi`
and `jpeg_quality` are only *defaults*: a request (`?unwarp=…`) or a CLI flag
(`--unwarp`) that names the option wins. `ocr_backend`, `det_box_thresh`,
`guard_omni`, `redact_regions` and everything under `[api]` are fixed per process.

## `[engine]`

| Key | Default | Meaning |
|---|---|---|
| `ocr_backend` | `onnxruntime` | `paddle` \| `onnxruntime` — runtime for every Paddle model (OCR detection, recognition, layout) |
| `classifier` | `presidio` | `presidio` \| `guard-omni` — default for `?classifier=` / `--classifier` |
| `guard_omni` | `false` | offer the `guard-omni` classifier; needs the `guard-omni` uv group (not in the Docker image) |
| `det_box_thresh` | `0.5` | minimum mean detector score for a text box (PaddleOCR's own default is 0.6) |

The two axes combine freely. Both OCR backends run the same models and produce the
same output; `onnxruntime` is about 3× faster. Lower `det_box_thresh` finds faint
or thin full-width lines (an imprint footer in small type); raise it if background
texture is read as text. See [Redaction › Classifiers](Redaction.md#classifiers).

## `[redaction]`

| Key | Default | Meaning |
|---|---|---|
| `fill` | `[0, 0, 0]` | RGB colour of the redaction boxes |
| `padding` | `2` | pixels added around each box |
| `score_threshold` | `0.4` | minimum Presidio entity score |
| `unwarp` | `false` | dewarp photographed pages before OCR |
| `pdf_dpi` | `200` | rasterization DPI for PDF input (36–1200) |
| `max_pages` | `30` | reject documents with more pages |
| `jpeg_quality` | `90` | quality of every JPEG produced (1–100) |
| `redact_regions` | `true` | also blacken whole layout regions (see below) |

## `[redaction.layout]`

The layout detector behind the region pass ([details](Redaction.md#layout-regions)).

| Key | Default | Meaning |
|---|---|---|
| `model_name` | `PP-DocLayout_plus-L` | detector checkpoint; other PP-DocLayout variants swap in by name |
| `threshold` | `0.35` | minimum detection confidence — below the model's 0.5 because photos score low |
| `layout_nms` | `true` | drop near-duplicate overlapping detections |

## `[api]`

| Key | Default | Meaning |
|---|---|---|
| `max_upload_bytes` | `31457280` (30 MiB) | request body limit (`413` above it) |
| `input_content_types` | PNG, JPEG, PDF | accepted `Content-Type`s for `/api/redact` |
| `max_image_pixels` | `40000000` | per-page pixel limit (decompression-bomb guard) |
| `max_concurrent_per_worker` | `1` | redactions running at once inside one worker |

Bind address, worker count and request timeout are not config keys: pass them to
the server (`-b`, `-w`, `--timeout`; in Docker `WEB_CONCURRENCY` and
`REQUEST_TIMEOUT`).

`max_concurrent_per_worker` bounds memory: a 30-page PDF holds several hundred MB of
page images. Parallelism comes from worker processes, not from this value — see
[Running › Production](Running.md#production-without-docker).
