# PII redaction for German invoices

**Black out names, addresses, birth dates and bank details on German invoices —
locally, in seconds, with an optional UI.**

![Python 3.13](https://img.shields.io/badge/python-3.13-blue)
![Code: MIT](https://img.shields.io/badge/code-MIT-green)
![Runs offline](https://img.shields.io/badge/inference-100%25%20local-informational)

<table>
<tr>
<td width="50%">
<img src="docs/screenshot.jpeg" alt="Web UI with suggested redaction boxes on an invoice">
<sub>Review: every suggested box can be kept, removed or redrawn</sub>
</td>
<td width="50%">
<img src="docs/redacted.jpeg" alt="The redacted invoice">
<sub>Result: the redacted document</sub>
</td>
</tr>
</table>

Share a doctor's bill with your insurer, your tax advisor or a support forum without
sharing the patient. The tool protects the people an invoice is *about* — recipient,
patient, insured person — and the doctor's identity, and leaves the medical content,
the dates, the specialty and the clearing house readable.

## Why this one

- **Nothing leaves your machine.** OCR and detection run on CPU; no cloud API, no
  telemetry. The Docker image even runs with `--network none`.
- **Built for German invoices.** GOÄ fee tables, `Anrede` lines, PLZ + city blocks,
  birth dates in a separate column, insurance numbers next to their label.
- **You stay in control.** The web UI shows every suggested box; add, delete, then
  download. The final file is drawn exactly from what you approved.
- **Many documents at once.** Drop a stack of PDFs or a ZIP; all are analysed in the
  background, reviewed one by one and downloaded as one ZIP.
- **Safe output.** Pages are rasterized with filled rectangles — there is no hidden
  text layer to copy the original from.
- **Three ways in.** Web UI, batch CLI and a REST API, all on the same pipeline.

## Quick start

With the published Docker image (web UI and API on port 8000):

```bash
docker run --rm --platform linux/amd64 -p 8000:8000 ghcr.io/krausest/pii-redact-german-invoice
# open http://localhost:8000
```

Or build it yourself — see [Docker](docs/Docker.md).

Without Docker ([uv](https://docs.astral.sh/uv/), Python 3.13):

```bash
uv sync
uv run python -m spacy download de_core_news_lg
uv run pii-redact example/GOÄ_Rechnung1.pdf   # -> example/GOÄ_Rechnung1_redacted.pdf
```

## API at a glance

```bash
curl -X POST --data-binary @invoice.pdf -H "Content-Type: application/pdf" \
     http://localhost:8000/api/redact -o redacted.pdf
```

| Route | |
|---|---|
| `POST /api/redact` | file in, redacted file out — or `?json-output=true` for pages and boxes to review |
| `POST /api/assemble` | reviewed pages + boxes in, final PDF/JPEG out (no models involved) |
| `GET /health` | status and active engine |

## Documentation

| | |
|---|---|
| [Running](docs/Running.md) | development and production setup |
| [Docker](docs/Docker.md) | build, run, parameters |
| [Configuration](docs/Configuration.md) | `config.toml` and environment variables |
| [CLI](docs/CLI.md) | batch redaction from the command line |
| [API](docs/API.md) | REST API reference |
| [Redaction](docs/Redaction.md) | how detection works, rules, models, code structure |
| [Frontend](docs/Frontend.md) | the web UI |
| [Testing](docs/Testing.md) | test suites and regression snapshots |

## Limitations

> [!WARNING]
> Automated detection is not a guarantee. **Review every document before you share
> it.**

- Rules and models are tuned for **German** invoices; other languages and unusual
  layouts will miss PII.
- Region redaction blackens whole layout blocks — sometimes more than needed, and a
  block without any detected PII survives.
- QR and barcodes are covered only when the layout model reports them as images.
- Redacted PDFs are images and no longer searchable.
- The JSON report's page images are the **unredacted** originals.

## License

The source code is **MIT** ([`LICENSE`](LICENSE)). Third-party packages and models
are listed in [`LICENSE.md`](LICENSE.md) — note that the **Docker image is
AGPL-3.0** because it bundles PyMuPDF.
