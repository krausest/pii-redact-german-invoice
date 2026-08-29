# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

Removes PII from German invoices (OCR → NER → box fill). One Python package,
`backend` (redaction pipeline + CLI + FastAPI service), plus a Svelte 5 SPA in
`frontend/` that calls the REST API directly. In production `backend` serves the
built SPA as static files (one origin, port 8000); there is no separate web backend.

## Commands

```bash
uv sync                                          # single package — NOT --all-packages
uv sync --group dev                              # + pytest/httpx
uv run python -m spacy download de_core_news_lg  # spaCy model for the Presidio classifier

uv run uvicorn backend.api:app --port 8000       # dev: API + (if built) SPA
cd frontend && npm run dev                       # Vite :5173, proxies /api + /health -> 127.0.0.1:8000
uv run pii-redact Arztrechnung/                  # batch CLI, writes <stem>_redacted.{pdf,jpg}

uv run pytest -m 'not slow'                      # fast loop, no models load
uv run pytest -m slow                            # loads real ML models (minutes)
uv run pytest tests/test_redact.py::test_png_in_jpeg_out   # a single test
```

Production is Gunicorn with N uvicorn workers (`gunicorn backend.api:app -k
uvicorn.workers.UvicornWorker`); see the `CMD` in `Dockerfile`.

## Architecture

**Three primitives, composed differently per caller** (`backend/pipeline.py`):
`unwarp(image)` flattens a photographed page, `compute_boxes(image)` returns the
boxes to redact *in the pixel space of the image passed in*, `apply_boxes` draws
them (in place — copy first if you need the clean page too). `redact()` composes all
three for a single image and is kept as the reference composition (pinned by
`tests/test_integration.py`); both real callers go through `backend/service.py`
instead — `run_redaction` then `produce_output` — since only that path handles PDFs.

**`run_redaction` → `Redaction` → `produce_output`.** `run_redaction` returns a
`Redaction` (pages + `is_pdf`), not a bare page list: it is the one place that knows
whether the input was a PDF, so the fact travels *with* the pages instead of each
caller re-deriving it (api.py from the content type, cli.py from the suffix) and
handing it back down. `produce_output` owns the `json_output` fork and returns
`(media_type, bytes)` — `render_document` and `build_report` are its two arms. So
api.py is one `Response(...)` with no sentinel, and the CLI names its output file
from `EXTENSION_BY_MEDIA_TYPE[media_type]` rather than re-deciding ".pdf vs .jpg".
Serializing the report inside `produce_output` also keeps that (tens of MB of
base64) off the event loop.

**Layering.** `backend/service.py` is the framework-free core (no HTTP);
`backend/api.py` is only request reading, byte-level checks and response shaping,
with a custom `ApiError` → `{"detail": "..."}` handler. Bodies are read raw from
`Request` (a file upload is bytes, not a model) and never through FastAPI's own
body/query binding, so error shape and status codes stay ours — 400, not 422.

**Validation is pydantic, in two places only** — `backend/options.py` (query
options for both endpoints, plus the `/api/assemble` JSON body) and
`backend/config.py` (the TOML). Both are frozen models with `extra="forbid"`, so
an unknown query parameter *or* an unknown `config.toml` key is a hard error
rather than a silent no-op. Query models use a kebab alias generator with
`validate_by_name=False` — `?json-output=` is the only accepted spelling of
`json_output`, which also means **options are built via `from_query()`, never by
keyword**. `options._detail()` flattens a `ValidationError` into the one-line
string the API reports; `from_query` / `from_json` re-raise as plain `ValueError`,
so api.py never imports pydantic. Option defaults are *not* class defaults: every
config-backed field is required on the model and filled from `Config` in
`from_query`, so a default exists in exactly one place.

**The CLI's flags are the `/api/redact` query parameters**, same names
(`--unwarp/--no-unwarp`, `--json-output`, `--pdf-dpi`, `--jpeg-quality`) and the
same `RedactOptions`. argparse only collects *strings*, every flag defaulting to
`None`; `cli.query_from_args` reads the wire names off `RedactOptions.wire_names()`
and drops the untouched ones, and `from_query` does the rest — so range checks and
their messages are not duplicated and the config stays the only source of defaults.
`build_parser` stays hand-listed on purpose (help text and `metavar` have no home
on the model); a new `RedactOptions` field needs a matching flag, and
`tests/test_cli.py::test_every_redact_query_parameter_has_a_flag` fails otherwise.

**Two endpoints that share only the box format:**
- `POST /api/redact` — raw PNG/JPEG/PDF bytes in (not multipart); the redacted
  file out, same kind that came in, or with `?json-output=true` a JSON report
  `{unwarped, pages:[{index,width,height,boxes,image}], redacted}` (the engine is
  not in the report — it is fixed per process, so `/health` is where to read it).
- `POST /api/assemble` — page images + boxes in as JSON, one file out. **No
  models, no OCR, no unwarping** — it only fills rectangles, which is why a client
  can safely send back pages a human edited. This split exists so box/pixel
  alignment never depends on ML inference being reproducible.

**Invariants worth not breaking:**
- *The coordinate rule.* Boxes are always in the pixel space of the image returned
  alongside them — never the uploaded file's, which differs after dewarping or PDF
  rasterization at `pdf-dpi`.
- *EXIF orientation is applied at the boundary*, by `ImageOps.exif_transpose` at
  each of the three places an image is decoded (`api._decode_image`, `cli.main`,
  `evaltool.read_document`) — never deeper, so the raster stays the only truth.
  A phone photo stores its pixels sideways plus a tag saying so; PIL applies
  neither, and `encode_jpeg` writes no EXIF, so without this the page reached OCR
  rotated 90° *and* came back visibly rotated. PDFs need nothing: PyMuPDF
  rasterizes with the page rotation already applied.
- `pages[].image` in the report is **not** redacted; it is the clean page for
  review. Only `redacted` and the default file response are redacted — and they are
  the same bytes, because `build_report` fills `redacted` with `render_document`,
  the call the file response itself makes. A PDF for a PDF, else a JPEG, always
  present.
- `assemble_pdf(pages, jpeg_quality, dpi)` sizes pages `pixels × 72 / dpi`. Pass
  the DPI the pages were rasterized at or an A4 scan comes back 2.8× oversized.
- The file response carries no metadata at all — a clean document and a failed
  detection look identical. Use `json-output=true` to learn what was found.

**Engine selection** (`backend/config.py`): a friendly preset name (`native` |
`onnx` | `wholetext`) resolves to an (OCR backend, classifier) pair, with optional
per-axis overrides. `native`/`onnx` differ only in the OCR inference engine — both
classify with Presidio, so `presidio` is a *classifier* name, never a preset. It is
the only real classifier (a GLiNER engine was tried and dropped: worse on this
corpus, and it pulled torch), but the pair is kept as a pair because `/health` and
the report publish `{name, ocr, classifier}` and that is the API contract.
Fixed per process, *not* selectable per request; the API builds one pipeline per
worker in the lifespan handler, the CLI one per run. Note the committed
`config.toml` runs `onnx` while the code default is `native`. `det_box_thresh`
(0.5, under PaddleOCR's 0.6) rides along here because it is the same
build-time-only kind of knob: it is a *mean* detector score over the box, so a
full-page-width line of 6 pt type averages under 0.6 even at the same ink
darkness as the table above it — a photographed invoice's imprint footer was not
detected at all, which also cost the footer *band*, since `_band_block` needs a
sender anchor and an undetected line is not an anchor.

**`wholetext` is a third, structurally different engine** (`backend/wholetext/`),
not a third `Classifier`. It exists to test a fundamentally different detection
strategy alongside the one above without touching it: whole-document zero-shot NER
(`guard-omni`, a GLiNER2 model — see `GUARD_OMNI.md`) over the page's OCR lines
joined into one text, instead of `backend.rules`' per-line regex plus a per-line
`Classifier`. `"wholetext"` is registered as a pseudo-`ClassifierName` purely so
`EngineConfig.resolve()`/`/health` keep publishing the same `{name, ocr,
classifier}` shape; `backend/factory.py::build_pipeline` branches on it to
construct `backend.wholetext.pipeline.WholetextPipeline` instead of
`RedactionPipeline`. The two pipelines are structural siblings (same
`unwarp`/`compute_boxes`/`apply_boxes`/`redact` shape, no shared base class) —
`backend/pipeline.py`, `backend/rules.py`, `backend/regions.py` and
`backend/classifiers/*` are untouched by its existence, and nothing in
`backend/wholetext/` imports them. Its own detection is documented in
`backend/wholetext/pipeline.py`, `backend/wholetext/windowing.py` and
`backend/wholetext/harvest.py`, not repeated here — whole-document span
extraction with a harvest-token cross-line recall pass. Before joining a
page's lines into one text, `backend/wholetext/layout.py` groups them by the
typed regions a trained layout detector finds (`PP-DocLayout_plus-L` via the
already-installed `paddleocr.LayoutDetection` — a standalone model, NOT the
heavy `PPStructureV3` pipeline, which would re-run its own OCR) and reorders
them group by group: a line joins the region containing its center (smallest
region wins on nesting), lines no region claims become singletons, groups and
their lines linearize by `(top, left)`. This exists because PaddleOCR's own
line order is a plain global `(y, x)` sort with only a `<10px`-gap local swap
(`paddlex/inference/pipelines/components/common/sort_boxes.py`), which
interleaves side-by-side blocks — a sender column and a recipient block at
similar page height — whenever they don't sit at pixel-identical y; windowing
then hands guard-omni a window mixing two unrelated blocks instead of one
coherent one. (A gap-factor flood-fill was tried first and replaced: even
generously widened factors split table rows into per-cell singletons while a
model knows what a table *is*.) The regions carry semantic labels
(`text`/`table`/`header`/`footer`/`doc_title`/`seal`/…) — the hook a future
wholesale region-blackening pass would key on; that pass is not implemented.
The model is picked in `[redaction.wholetext_layout]` (`model_name`,
`threshold`, `layout_nms`); the threshold default is 0.35, well under the
model's own 0.5, because phone photos depress every score — the corpus'
photographed fee tables detect at 0.38–0.43 vs 0.9+ for scans, so at 0.5
exactly the pages that need grouping most got none. Its weights land in
`.paddle_cache` like the OCR models, which
is why `PaddleLayoutDetector.__init__` imports `backend.ocr.paddle` before
paddle (the cache env vars must be set first). `--debug-layout` (CLI only,
not a query parameter) writes `<stem>_layout.jpg` with the detected regions
outlined and labeled plus each line group's bounding box, cyclically colored;
the wholesale-blackening candidates (`header`/`footer`/`seal`/`image`) get a
translucent red background and the `table` (the body that must NOT be
blackened) a translucent green one, so both stand out on a sample page.

**Concurrency — the thread and the limiter do opposite jobs.** CPU-bound work runs
via `anyio.to_thread.run_sync` so the event loop stays free to answer `/health`
(Docker probes every 30 s) and fast-fail bad uploads *while* a redaction runs — not
for parallelism. `anyio.CapacityLimiter(api.max_concurrent_per_worker)`, built in
`create_app`, is what makes it safe: one shared `RedactionPipeline` serves the
whole worker, the models aren't known to be thread-safe (`pipeline.unwarp`'s lazy
init is an unguarded race), and a 30-page PDF holds ~700 MB of page images —
without the limiter anyio's pool would allow 40 at once. `tests/test_concurrency.py`
pins the bound. Real parallelism comes from worker processes.

**Detection is rules + model + regions.** `backend/rules.py` runs deterministic
German patterns (salutation — the address words that stand alone above a
recipient block *and* the modern letter openers "Guten Tag"/"Hallo", which are
case-sensitive so running text's "einen guten Tag" is not one —
person label + name — patient/Versicherte/Mitglied —
street, ZIP+city, sender identity — legal form, URL/e-mail/phone,
registry+banking identifiers) before the classifier, uniformly for every engine.
The label rules exist because NER splits "Mustermann, Max" into two one-token
PERSON spans that the PERSON guard in `_redactable` then drops — a patient line is
never caught by the model alone. (`_name_token_count` recovers the *comma* form,
"Muster,Andrea": across a comma a capitalized token counts even when the tagger
calls it a noun, which is what German surnames like "Bauer"/"Jäger"/"Wolf" tag as.
Safe because a `PERSON` still has to have been recognized somewhere on the line,
and the Leistungstexte the guard rejects produce no `PERSON` at all. *Inside* the
span the test is the reverse: a proper noun counts whatever its case, because the
"German capitalizes every noun" argument is about capitals appearing where they
mean nothing, not about a missing one meaning something — OCR reads the capital I
of a name as a lowercase l, and the tagger that still called it `PROPN` is the
better witness than the pixel that got lost. That leniency cannot reach a
Leistungstext: `compute_boxes` is the only caller of `is_pii` and skips it for
item-table lines entirely, which `tests/test_pipeline.py` asserts directly.)
`NAME_DATE` covers the unlabeled variant
("Muster,Andrea 05.03.11", a patient table row): NER returns only the forename,
and the date has no birth label to pair with, so *neither* the model nor
`labeled_value_indices` can see that line. It requires both halves — a
Leistungstext also holds comma-joined capitalized nouns, and a bare date in an item
row is a treatment date. Three page-level passes ride on top:

- `labeled_value_indices` is a *spatial* label↔value matcher, table-driven
  (`LABELED_IDS`): a "Geburtsdatum" label line pairs with the date line in the
  neighbouring column (plus the merged one-line forms — `geb. 30.09.1954` and the
  `*26.06.1975` birth mark, together `BIRTH_MARK`), and the same geometry
  covers Versicherten-/Patienten-/Fall-/Aufnahme-/Mitglieds-/Vertrags-Nummern.
  A rule may also carry a `header`: the label alone in a cell at the *top of a
  column*, with `_column_below` walking the values downwards (center inside the
  header's x-range, stopping at the first non-value, a gap over
  `_COLUMN_GAP_FACTOR`, or the item table). Birthdate and name have one — a
  column headed "Geburtsdatum" holds birthdates by definition and one headed
  "Patient:" holds names, which a "Datum" column does not.
  `Rechnungs-Nr` is deliberately absent — the invoice number is the reference a
  redacted document is shared *for*. The third row points the geometry at the
  **name** column: "Patient:" alone in its cell makes the two-token name beside it
  (`NAME_VALUE`) a value. That is the only thing that sees such a name —
  `PATIENT_NAME` needs the label on the *same* line, and a bare "Wolf,Uwe" gives
  NER nothing. Its label is anchored (`PERSON_LABEL_CELL`) and that anchor is
  load-bearing: an unanchored "Patient" would make a Leistungstext sentence a
  label and blacken whatever capitalized pair shares its row. The label may carry
  a **modifier** ("Behandelte Person:", a third of the sample invoices) that has
  to inflect like a German adjective — that ending is what keeps a cell merely
  *ending* in the label noun ("Beratung Person") from becoming a label.
  Because the vocabulary is then itself name-shaped, `labeled_value_indices` has
  to say out loud that a cell holding nothing but a label is never a value.
- **The label licenses two directions, and the cells past the first one.** A
  matched value is the start of another `_column_below` walk, with
  `PERSON_DETAIL` — a patient block prints the name beside the label and the
  birthdate under the *name*, and that date carries no birth label of its own, so
  nothing else can reach it. Both the value pattern and the detail vocabulary are
  **anchored to the whole cell**: unanchored, `DATE_RE` matches inside
  "Behandlungszeitraum vom 15.04.2026 bis 28.04.2026" and blackens the treatment
  period. `NAME_VALUE` additionally accepts one **OCR-decapitalized** half
  (`_NAME_PART_OCR`) — OCR reads the capital I of "Ioanna" as a lowercase l — but
  demands the other half keep its capital. None of this is a confusion table
  (l/I, 0/O): what makes the leniency safe is the label beside the cell, never
  the shape of the damage. The walks take `item_table_indices` and stop there:
  a Leistungstext is two capitalized words, i.e. exactly the shape of a name, so
  the geometry alone could never say where the invoice body starts.
- **Name memory** (`harvest_names`/`mentions_name`): surnames harvested from
  deterministic person evidence (patient label, title, salutation, merged
  birthdate line) are redacted on bare recurrence — a Diagnose line, a subject
  line — where NER drops the single token. **A name the page labels from the
  neighbouring column counts too** (`harvest_names(..., labeled=True)`, passed by
  `compute_boxes` for the `NAME_VALUE` cells in `labeled_idx`): the evidence is
  the one `PATIENT_NAME` reads off "Patient: Max Mustermann", only spread over two
  OCR lines by the layout, and without it a person named in a two-column patient
  block is redacted there and nowhere else — a Diagnose or subject line repeating
  the surname gives NER nothing. Evidence is rules-only (no
  classifier feedback loop); matching is whole-word — that, not the letter case, is what
  keeps "Allgemeine" from matching Dr. Allgemein. **Casing is compared loosely,
  but the occurrence must start with a capital**: one document prints the same
  person as "Andrea Muster" in the address block and "MUSTER, ANDREA" in the
  patient row, so a memory holding one casing is half a memory — while a
  lowercase hit is the ordinary German word the surname collides with ("Klein"
  the person vs "klein gedruckt"), which is why this is not `IGNORECASE`. `_NAME_TOKEN` accepts all-caps
  tokens for the same reason, and the stopwords are compared case-folded so an
  all-caps evidence line cannot harvest "PATIENT" as a name.
  `run_redaction` shares one accumulator per document, so a name
  labeled on page 1 is caught bare on page 2 (forward-only carry).
- **The item table** (`item_table_indices`) is the one pass that *suppresses*:
  inside it `compute_boxes` skips the classifier, and only the classifier. An
  invoice's body is a table of fee numbers, service texts and amounts, and the PII
  sits above it (recipient, patient) or below it (imprint, bank details) — while
  German capitalizes every noun, so a two-word Leistungstext ("Orientierende
  Testuntersuchg.", "Cleed Agar") is shaped exactly like a forename/surname pair
  and NER reads it as one. Recognition and extent are split as in `regions.py`:
  `MONEY` lines merge into rows (one item row is three OCR lines, one per amount
  column), rows cluster by vertical gap, and a cluster of ≥`_MIN_TABLE_ROWS` spans
  a band that gates every line whose center falls in it — which is how a wrapped
  description carrying no amount ("Folgerezept)") is covered. **The clustering is
  load-bearing**: without it a single amount printed above the recipient block
  ("Rechnungsbetrag 195,18") would stretch the band down over the address. The
  accepted cost is an *unlabeled, never-before-seen* name in an item row;
  a titled, labeled or already-harvested one still redacts, since every
  deterministic rule and the name memory keep running there.

`backend/regions.py` then adds the **only boxes not derived from an OCR line**:
the header band, the footer band, the sender column and the recipient address
block. The bands are content-
*blind across the page width* on purpose — a letterhead is a logo, and OCR returns
no `Line` for a graphic, so nothing text-based could ever cover it — but their
*height* is the height of the sender block they found, so `header_frac`/`footer_frac`
are a search window, not the band height (padding a band out to the fraction only
blackens whitespace; measured on the sample scans). `_BAND_STRETCH` caps the height
at 1.5× the window because nothing else bounds it — growth follows the block past
the window, and a merged OCR box holding a sender anchor would otherwise swallow the
page. Bands are gated on *naming a sender* (`_band_block` → `is_sender_anchor`), not
merely holding text. That gate is load-bearing: a continuation page's item table can
start at y=0 and its totals can sit in the bottom tenth, and "has content" would
blacken both.

**A band's height comes from `_grow`, not from the window**, which is why
`_band_block` seeds on anchors and then hands off to the sender column's machinery:
the window says *look here*, `_adjacent` says *how far the block reaches*. Taking
the topmost line that merely dips into the footer window blackened the item table of
every full page — the last table rows cross the window edge, and one imprint line
below them dragged the strip up over them. The table cells and the imprint are
told apart by left edge (x=219/288/744/870 vs x=129), exactly what `_adjacent`
tests. The accepted cost: a band line that is neither an anchor nor left-aligned
with one (a centred "Vielen Dank für Ihren Besuch") is no longer covered.

**All four regions are one search** — `_components(lines, p, in_window, is_anchor)`,
a deliberately naive connected-component walk. An anchor line inside the region's
window seeds a block, `_grow` absorbs every line `_adjacent` to *any* member until a
pass adds nothing, and `taken` is shared across seeds so a letterhead holding half a
dozen anchors still yields one block. The four differ only in what they hand it and
what they do with the result: bands take the union of the blocks and span a
full-width strip, the two columns emit one `_bbox` each. Resist re-splitting that
into a function per region (it was, three times over) *and* resist going the other
way into a table-driven descriptor — the bands' output is a different shape.
Because a component is order-free it is the same set from any member — which is why
nothing here sorts, and why the window bounds only seeding: a top-to-bottom walk has
to cope with the two columns of a letter interleaving, where a recipient-address
line sorting between two sender lines splits the block and leaves a hole in it.

Four things in `_adjacent` were measured on the sample scans, not guessed, so
don't "simplify" them back:
- **the AND is load-bearing** — one page's payment table touches the block (only
  alignment cuts it), another's is exactly aligned (only the gap does);
- **the gap is signed** — OCR line boxes routinely overlap, and `abs()` would turn
  that strongest-possible evidence of one block into a large positive number;
- **alignment is left-edge only** — a right-edge arm changed no box on the first
  sample scans, and on the full corpus only pulled right-aligned label/value columns
  in for no gain, while numeric table columns are right-aligned to a pixel;
- **the gap bridges a blank line** (`vgap_factor` 1.2, not a line spacing) — a
  letterhead prints its branch one empty line under the address, and the block
  stopped short of it. The ceiling is ~3.0, where blocks chain into the item table.
  Nothing in between separates that from the cost: an invoice-number column a blank
  line under the letterhead joins at 0.6 and gets blackened. Accepted — it costs a
  reference, not a secret. Alignment is deliberately *not* tightened to compensate:
  German invoices are set flush left, `dx` is 0.00 on most candidate pairs, and a
  two-tier rule (tighter `dx` for the wider gap) gave identical boxes on 47 of 48
  sample pages and a worse one on the 48th.

The **recipient address block** (`_recipient_column`) is the same machinery
pointed at the other window: seeded left of `column_x_frac` inside
`recipient_y_min_frac`/`recipient_y_max_frac` (`max <= min` disables), grown by
the same `_components`. It exists for the lines *between* the per-line hits — c/o, a
company recipient, a garbled name line. Seeds are street/ZIP+city **only**
(`is_recipient_anchor`): a salutation also tops left-aligned body paragraphs,
where growth would swallow the text, while every deliverable address contains its
street and city lines and the block reaches the name lines above them from there.

An earlier version also seeded runs on lines the per-line pass had already
redacted. Removed: it added 9 boxes over 12 pages, 8 of them a single already-black
line, and it forced a `redacted: set[int]` through `compute_boxes` → `region_boxes`
that nothing else wanted. Configured by
`[redaction].redact_regions` + `[redaction.regions]` and passed to the pipeline as
a single `regions: RegionParams | None` kwarg — `None` means "don't run it", so the
toggle and the geometry never disagree. **Config-only, by design**: not a query
parameter, not a CLI flag, so `options.py` / `cli.py` / `api.py` / the frontend are
untouched and the boxes just flow through the report as ordinary boxes.

**`backend/codes.py` trades a checksum for a look.** The matrix pass reads with
`return_errors=True`, so a symbol counts once it is *located* — which is the only way
the 60 px Girocode on a phone photo gets covered, since it does not verify. What that
gives up is the one thing vouching for a candidate being a symbol, so an undecoded one
has to earn it back: `_MIN_INK` requires it to be at least 30% dark, because a matrix
code is about half ink *by construction* (QR's masking keeps the module balance near
even; DataMatrix's timing border and ITF's bars do the same), and paper is not. Shape
alone had let through blank margin, a hole-punch, a logo's halftone screen and one
2813×1647 quad at 1.7:1 that blackened 80% of a page carrying no code. Two things
about the measurement are results, not choices: the reference is the paper *around*
the candidate rather than Otsu — on a blank crop Otsu splits sensor noise down the
middle and calls half of it ink, scoring the phantoms *above* the real symbols — and
it is taken over the middle 70% of the rect, since `Box` is axis-aligned and a tilted
code fills only part of its own bounding rect (45° costs half of it). There is no
upper bound on purpose: an underexposed photo pushes a real symbol toward solid and
dropping it there leaks the IBAN, while a solid black rectangle costs blank paper. The
test applies at the three places unverified geometry appears (`_read`'s error rows,
`_qr_outlines`, the fused fragments) and nowhere a decode already happened —
the linear pass never sees it, being decode-only for the opposite reason.

**The detection trace is threaded, not scraped** (`backend/trace.py`). `Trace.add`
always logs at DEBUG — `PII_LOG_LEVEL=DEBUG` is byte-identical to before — and
*additionally* keeps the text when `?debug=true` asked for a copy, which
`run_redaction` then hands on as `Redaction.debug` for `build_report` to add to
the report (only when not None, so an ordinary report is unchanged). One `Trace`
per document, pages marked `=== page N ===`, mirroring the name-memory
accumulator beside it.

**A verdict names the arm, the line under it names the rule.** `static-rule` is
nine patterns, and knowing a box came from one of them says nothing about which,
so `compute_boxes` prints `rule <NAME> matched <text>` under the line —
`static_rule_match` returns the pair instead of a bool for exactly that, and the
names are the module-level ones in `rules.py`, so a trace line is a grep from the
regex that drew the box. It is an *extra* line, not a change to the verdict
token: `-> REDACT (static-rule)` is what the regression snapshots record, and the
replay parser ignores lines it does not recognize.

A `Trace` is passed *down* through `compute_boxes` into `Classifier.is_pii`
rather than captured off the logger by a temporary handler. The handler version
works, but it has to answer two questions this does not: which in-flight request
a record belongs to (it needed a thread-id filter, since the CPU work runs in an
`anyio` worker thread), and how to make presidio emit its per-match explanations
at all — those hang on `return_decision_process`, which was gated on the logger's
level, so capturing them meant flipping that level process-wide mid-request.
`trace.wanted` replaces that gate: **true when anyone will read the line, a
collector *or* the log**, so the expensive explanations are built for a
`?debug=true` request even at `PII_LOG_LEVEL=WARNING` and skipped otherwise.
`add(fmt, *args)` keeps logging's lazy interpolation — it runs several times per
OCR line. The trace is required on `is_pii` and optional on `compute_boxes`:
required where a default would silently drop the *reasons* (the half that
explains a missing box), optional where `Trace()` collecting nothing is the same
code path, not a second one. `codes.py` keeps its own `logger` — `code_boxes` has
20+ test call sites and its boxes reach the trace through `pipeline.py` anyway.
`debug=true` without `json-output=true` is a 400: the file response carries no
metadata, so the option would otherwise be a silent no-op.

**Frontend flow** (`frontend/src/lib/api.ts`): `analyze()` → `/api/redact?json-output=true`,
the user edits boxes on the returned page images, `render()` → `/api/assemble`.
`fetchDebugLog()` is a *third* call to the same endpoint with `debug=true`,
behind the footer's Debug-log button: a separate request because the trace makes
the server do work a normal upload has no use for, and because leaving pages and
boxes untouched is what lets the button skip the discard-edits confirmation.

**Frontend i18n is hand-rolled and dependency-free** (`frontend/src/lib/i18n.svelte.ts`
+ `messages/{en,de}.ts`): `en` is the source of truth and `de` is annotated
`: Messages` (= `typeof en`), so a missing or misspelled key fails `npm run check`
rather than rendering `undefined` — that annotation *is* the test suite, since the
frontend has none. Components read strings with `const m = $derived(t())`; `i18n`
is the app's only shared reactive state, everything else still flows through props.
The locale comes from `localStorage` first, then `navigator.languages`, else `en`.
Two consequences worth keeping: interpolation and plurals are *functions* in the
catalogue (each locale writes its own rule — no ICU library for two languages), and
`upload.ts` returns error *codes* that `FileDrop` translates, so that module stays
framework-free. The language control lives in the footer, deliberately **not** in
`Settings.svelte`, whose every change re-runs backend detection. API error `detail`
strings are still shown verbatim, so a backend rejection reads English under a German
UI — by decision, since translating them would put the wording in two repos.

## Non-obvious constraints (these have bitten us)

- **Docker must build `--platform linux/amd64`.** paddlepaddle ships no linux-arm64
  wheel. On Apple Silicon this (and any amd64 step) runs under qemu emulation.
- **qemu can't run paddle OCR *inference*** (oneDNN PIR crash). On an arm64 Mac you
  can verify build/boot/health/SPA/offline-models in-container, but **not** actual
  in-container redaction — that path is covered by the slow tests natively and works
  on a real amd64 host.
- **Engines: native (default), onnx, and wholetext** — three, still no optional
  extra, so `uv sync` installs everything every engine needs, `gliner2`/torch
  included. `wholetext` is a structurally different pipeline
  (`backend/wholetext/`), not a `Classifier` variant of `RedactionPipeline` — see
  the "Engine selection" section above.
- **Python 3.13 only** — paddle has no cp314 wheels (pinned in `.python-version`).
- **Docker bakes all models at build** (`docker/warmup.py` constructs each engine
  *plus* the `DocUnwarper` — the pipeline only builds that lazily on first
  `unwarp()`, so engine construction alone misses it; no inference — see the qemu
  note). Runtime sets `HF_HUB_OFFLINE=1` — this is now a real network guard again,
  not a no-op: `wholetext`'s guard-omni checkpoint downloads from HuggingFace
  during warmup (`HF_HUB_OFFLINE=0` for that one step) the same way Paddle's
  models do; warmup itself has no try/except, so a model it cannot fetch fails the
  build rather than every request.
- **Paddle caches live in the repo** (`.paddle_cache`), set via env vars that
  `backend/ocr/paddle.py` and `backend/unwarp.py` write *before* importing paddle.
- The Vite proxy targets `127.0.0.1`, not `localhost` — on macOS Node resolves
  `localhost` to `::1` first and uvicorn binds IPv4.

## Testing

Fast tests inject `FakePipeline` / `StubOCR` / `StubClassifier` from
`tests/conftest.py` and set `app.state.pipeline` before the lifespan runs, so no
model ever loads (`test_cli.py` monkeypatches `cli.build_pipeline` for the same
reason). Anything marked `slow` uses the real models on
`example/GOÄ_Rechnung1.{png,pdf}`. The endpoint tests (`test_redact.py`,
`test_assemble.py`) carry the whole validation matrix — 415 / 413 (both the
Content-Length and streaming caps) / 400 decode + bomb + page-limit — plus the
invariants above; keep them pinned when changing the API.

**Regression testing runs on frozen OCR** (`backend/replay.py` +
`tests/test_regression.py`). OCR is the slow, model-heavy, machine-dependent half
of the pipeline and *not* the half that changes when a heuristic is tuned, so it
is run once per sample into a `<stem>_ocr.txt` and everything downstream is
replayed against that text with a blank page of the recorded size. A whole corpus
replays in seconds, which buys the thing snippet annotations could not: **one
asserted verdict per OCR line**, so the page is pinned rather than the few PII
strings someone thought to write down.

```bash
uv run python -m backend.replay dump <file-or-dir> --out-dir tests/regression  # slow, once
uv run python -m backend.replay check tests/regression            # exit 1 on a diff
uv run python -m backend.replay check tests/regression --update   # rewrite snapshots
uv run python -m backend.replay check <dir> --ignore-text         # after scrubbing PII
uv run pytest --regression                                        # the same, as tests
```

- **Verdicts are effective, not per-line.** `compute_boxes` decides per line, but
  a line it kept can still be blackened by a band drawn over it, so each line is
  scored against *every* box: ≥90% is `REDACT`, ≤10% is `keep`, and the gap
  between them is `PARTIAL`, which matches no expectation and forces a look at a
  line some neighbour's padding merely nicked. Asserting `keep` is the point —
  without it nothing tells a working pipeline from one that blackens the page.
- The verdict carries *why* (`static-rule`, `labeled-value`, `name-memory`,
  `classifier`, `region` for a band, `overlap` for a neighbour's padding), so a
  diff says whether a rule stopped firing or a band moved.
- **The reasons are read back out of the trace**, not returned from
  `compute_boxes` — which makes `backend/trace.py`'s format the contract, and
  `format_line` asserts against it rather than drifting silently.
- Snapshots are **generated** (`--update`), not hand-written: a `PII_LOG_LEVEL=DEBUG`
  trace is the same shape and parses as a starting point, but its `-> keep` is the
  per-line decision, which for a line under a band is not the outcome.
- Replay uses **no OCR, no unwarp, no code pass** (`codes=None`; QR is pixels and
  the page is blank — `tests/test_codes.py` covers it). So a frozen dump can go
  stale: re-run `dump` after anything that changes what OCR reads,
  `det_box_thresh` above all. `tests/test_ocr_dump.py` (`-m slow`) is the guard —
  it re-reads the committed sample and asserts every frozen line is still
  produced, comparing text only (geometry shifts a pixel between platforms) with
  spaces removed on both sides (OCR respaces a line when its detector box changes
  size: `Postfach 1560- 21305` became `Postfach 1560 - 21305` over a threshold
  change, which is not a line going missing).
- `--regression` is a **flag, not a marker expression**: the documented selectors
  are `-m 'not slow'` and `-m slow`, and a command-line `-m` replaces anything in
  `addopts`, so a marker alone would always drag the suite into one of the two.
- A dump is the document's full text, i.e. PII — **and so is the snapshot beside
  it**, which repeats every line. A real invoice becomes committable by *scrubbing*
  the dump: each real name/address/identifier swapped for a placeholder of the same
  shape, keeping the layout that the geometry passes are actually tuned on
  (`tests/Arztrechnungen/`). `--ignore-text` is what checks that edit — it compares
  geometry, verdicts and regions while letting the text differ, answering the one
  question a scrub raises: does the placeholder still redact where the real value
  did? **Scrubbing is two steps**: edit the `_ocr.txt`, then `check --update`, or
  the snapshot keeps the unscrubbed text. The synthetic example lives in
  `tests/regression/`; an unscrubbed private corpus goes in
  `Arztrechnung/regression/`, gitignored with the invoices. New document types (GOZ,
  hospital, insurance letters) are added as samples + snapshots, no code.

Do not use Arztrechnung2 unless I allow you to do so.

## Conventions

- Only commit when asked. Keep the fast test suite green before proposing changes.
- **Fix the rule, never the document.** A wrong box on one invoice is a symptom;
  the change that answers it has to be a statement about *German invoices* — what
  distinguishes a hostname from an abbreviation, a birthdate from a treatment
  date — not a literal from the page in front of you. No blacklisted strings, no
  "unless the line says X", no carve-out that only the reported sample can trip.
  If the generic form cannot be found, widen the evidence a rule needs rather
  than subtracting one case from it, and say in the comment what the change
  *costs* — every rule here is a trade, and the comments record which one.
  Then measure it on the corpus (the `_ocr.txt` dumps replay in seconds) before
  claiming it is free: "no verdict changed on 46 documents" is the argument, a
  passing unit test on the one line is not.
- Config lives in `config.toml`; `PII_CONFIG` picks the file and the
  `config._ENV_OVERRIDES` table maps `PII_ENGINE` / `PII_UNWARP` /
  `PII_REDACT_REGIONS` to the `[section].key` each replaces. Values go into the
  parsed TOML dict as raw *strings* and are validated by pydantic like any other,
  so a bad one fails at startup naming the field — add a row to the table, never
  hand-rolled env parsing. `PII_UNWARP` only sets the *default* for `?unwarp=` /
  `--unwarp` (a caller naming the option still wins); `PII_REDACT_REGIONS` has no
  wire name, so it is absolute.
- The README's API section is the contract — update it in the same change.
- **Cutting a release means bumping `frontend/package.json`'s `version` to
  match the new git tag, in the same commit that gets tagged.** The SPA
  footer reads that field directly (`frontend/src/App.svelte`); nothing
  computes it from git automatically, so a missed bump shows a stale version.
- **Adding or removing a dependency means updating `LICENSE.md` in the same change**
  (`pyproject.toml` *and* `frontend/package.json`). It is a legal inventory, not
  docs: the built image is AGPL-3.0 because PyMuPDF is. Its sections are grouped by
  what actually *ships*, and the image is built `--no-default-groups`, so check
  before moving a row — `uv tree --package <name> --invert --no-default-groups`. A
  dev-group package can still ship transitively (`requests` does, via paddleocr and
  presidio-analyzer).
- **Prefer short comments in code** and **avoid PII and real data in comments, tests and texts like README.md**
