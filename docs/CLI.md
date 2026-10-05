# Command line

`pii-redact` redacts files or folders in place, with the same pipeline and the same
output as [`POST /api/redact`](API.md#post-apiredact). Setup: see
[Running › Prerequisites](Running.md#prerequisites).

```bash
uv run pii-redact example/GOÄ_Rechnung1.pdf          # one file
uv run pii-redact scans/ more.png                    # folders and files mixed
uv run pii-redact --no-unwarp --pdf-dpi 300 scans/   # with options
PII_OCR_BACKEND=paddle uv run pii-redact scans/      # other engine for this run
```

- **Input:** `.pdf`, `.png`, `.jpg`, `.jpeg`. A folder is scanned one level deep.
  Files whose name contains `_redacted` or `_layout` are skipped, so a rerun does
  not redact its own output.
- **Output** is written next to each input:
  - PDF → `<stem>_redacted.pdf`
  - image → `<stem>_redacted.jpg` (images always come back as JPEG)
  - with `--json-output` → `<stem>_redacted.json`, the API's [JSON report](API.md#json-report-json-outputtrue)
- **Errors:** an unreadable file, or one over the page/pixel limits, is reported on
  stderr and the batch continues. Exit code `1` if any file failed, `2` for an
  invalid option (nothing is processed then).

## Flags

The flags are the `/api/redact` query parameters — same names, same validation, same
error messages. A flag you leave out takes its default from
[`config.toml`](Configuration.md).

| Flag | Default | Meaning |
|---|---|---|
| `--unwarp` / `--no-unwarp` | `redaction.unwarp` | dewarp photographed pages before OCR |
| `--json-output` | off | write the JSON report instead of the document |
| `--pdf-dpi N` | `redaction.pdf_dpi` | rasterization DPI for PDF input (36–1200) |
| `--jpeg-quality N` | `redaction.jpeg_quality` | quality of every JPEG produced (1–100) |
| `--classifier NAME` | `engine.classifier` | `presidio` \| `guard-omni` (needs `PII_GUARD_OMNI=true`) |
| `--debug` | off | add the detection trace to the report (needs `--json-output`) |
| `--debug-layout` | off | CLI only: write `<stem>_layout.jpg` (per page `_layout_pN.jpg`) with the detected layout regions drawn |

`PII_LOG_LEVEL=DEBUG uv run pii-redact …` prints the full detection trace to the
console — every OCR line, every match and the verdict.
