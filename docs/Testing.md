# Testing

```bash
uv sync --group dev                 # pytest + httpx

uv run pytest -m 'not slow'         # fast suite: no model loads, ~2 s
uv run pytest -m slow               # end-to-end with the real models (minutes)
uv run pytest --regression          # + OCR-replay snapshots (loads the classifier only)
uv run pytest tests/test_rules.py   # one file
```

Frontend: `cd frontend && npm run check` (type check; also fails on a missing
translation). It has no unit tests.

## Three kinds of tests

**Fast** (default). [`tests/conftest.py`](../tests/conftest.py) injects
`FakePipeline`, `StubOCR` and `StubClassifier`, so no model is ever loaded. They
cover validation, the rules, the pipeline's composition and both endpoints.

**Slow** (`-m slow`). Real OCR, layout model and classifier on
`example/GOÄ_Rechnung1.{png,pdf}`.

**Regression** (`--regression`). Every frozen sample against its snapshot — see
below. It is a flag, not a marker, so it combines with either `-m` selection.

| File | Covers |
|---|---|
| `test_redact.py` | `POST /api/redact`: validation matrix (415/413/400), options, both response shapes, `/health` |
| `test_assemble.py` | `POST /api/assemble`: filling boxes, packaging pages |
| `test_options.py` | query-parameter parsing: names, values, spelling |
| `test_config.py` | config loading, defaults, env overrides, rejection of bad values |
| `test_cli.py` | file collection, flags mirroring the API parameters |
| `test_concurrency.py` | the per-worker concurrency limit |
| `test_factory.py` | engine construction, lazy classifier build |
| `test_pipeline.py` | `compute_boxes`: rules, item table, name memory, regions together |
| `test_rules.py` | the deterministic German patterns, as one table of cases |
| `test_document.py` | joining a page into one text and mapping spans back to lines |
| `test_layout.py` | line-to-region assignment, reading order, region boxes |
| `test_presidio.py` | presidio-specific guards (wrapped spans, PERSON token count) |
| `test_trace.py` | the debug trace collector |
| `test_pdf.py` | PDF rasterization and assembly (PyMuPDF only) |
| `test_integration.py`, `test_redact_integration.py` | *slow* — end-to-end with real models |
| `test_regression.py` | *regression* — frozen OCR replays |

## Regression tests on frozen OCR

OCR is slow and machine-dependent, and it is not what changes when a rule is tuned.
So it is run **once** per sample into `<stem>_ocr.txt`; everything after it — rules,
classifier, name memory, item table — is replayed against that text. A corpus
replays in seconds, and the snapshot `<stem>_ocr.expected.txt` holds **one verdict
per OCR line**:

- `REDACT` — boxes cover ≥ 90 % of the line
- `keep` — ≤ 10 %
- `PARTIAL` — anything in between; matches neither expectation and needs a look

Each verdict names its reason (`static-rule`, `labeled-value`, `name-memory`,
`classifier`, or `overlap` for a neighbour's padding), so a diff shows what changed. Asserting `keep`
matters as much as `REDACT`: it catches over-redaction.

```bash
uv run python -m backend.replay dump example/GOÄ_Rechnung1.png --out-dir tests/regression  # slow, once
uv run python -m backend.replay check tests/regression             # exit 1 on a diff
uv run python -m backend.replay check tests/regression --update    # rewrite snapshots
uv run python -m backend.replay check <dir> --ignore-text          # after scrubbing
```

- Replay runs **no OCR, no dewarping and no layout-region pass** — regions are
  detected from pixels, and the replayed page is blank. Snapshots therefore pin the
  per-line detection only; region boxes are covered by `-m slow` and
  `test_layout.py`.
- Re-run `dump` after a change that alters what OCR reads (e.g. `det_box_thresh`).
- Samples: `tests/regression/` (synthetic, committed) and `tests/Arztrechnungen/`
  (real layouts, scrubbed, git-ignored).
- **A dump and its snapshot contain the document's full text — that is PII.** To
  make a real invoice committable, replace every name, address and identifier in
  the `_ocr.txt` with a placeholder of the same shape, check with `--ignore-text`
  that every verdict survived, then run `--update` so the snapshot loses the
  original text too.

## Before proposing a change

1. `uv run pytest -m 'not slow'` is green.
2. For a detection change: `uv run pytest --regression` — "no verdict changed on
   the corpus" is the argument, a passing unit test on one line is not.
3. For a frontend change: `npm run check` and `npm run build`.
