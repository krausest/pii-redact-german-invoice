"""Regression testing on frozen OCR: freeze the text once, replay the decisions.

OCR is the slow, model-heavy, machine-dependent half of the pipeline, and it is
*not* the half that changes when a heuristic is tuned. So it is run once per
sample and its output written to a ``<stem>_ocr.txt``::

    # document: invoice.png
    # ocr: paddle
    # unwarp: true
    === page 0 (1654x2339) ===
    line @(1873,2869 195x49 conf=99.98890757560730): '195,18'

Everything downstream — the deterministic rules, the labeled-value geometry, the
item table, the name memory, the classifier and the region bands — is then
replayed against that text alone, with a blank page of the recorded size standing
in for the image. The result is a ``<stem>_ocr.expected.txt`` snapshot holding one
asserted verdict per OCR line, so a whole document is pinned rather than the few
PII snippets someone thought to annotate, and the suite runs in seconds.

**Verdicts are effective, not per-line.** ``compute_boxes`` decides per line, but
a line it kept can still be blackened by a header/footer band drawn over it. What
matters is whether the pixels end up covered, so each line is scored against
*every* box: ``>= COVERED`` is ``REDACT``, ``<= UNTOUCHED`` is ``keep``, and the
deliberate gap between them is ``PARTIAL``, which satisfies neither expectation
and so forces a human to look at a line some neighbour's padding merely nicked.
The verdict carries *why*, which is how a snapshot diff distinguishes a rule that
stopped firing from a band that moved off the line it used to cover.
Asserting ``keep`` is the point of the exercise: without it the suite could not
tell a working pipeline from one that blackens the page.

**The reasons are read back out of the trace**, not returned from
``compute_boxes``. :mod:`backend.trace` already narrates every decision, so
parsing it here makes that narration the contract — if its format drifts, the
replay notices immediately (:func:`_outcome` compares each header against
:func:`format_line`) instead of silently reporting different reasons.

**No OCR, no unwarp, no QR pass.** The pipeline is built with an OCR backend that
raises if called and with ``codes=None``: a QR code is pixels, and the page here
is blank by construction, so the pass is switched off rather than left to find
nothing (``tests/test_codes.py`` covers it). The consequence to remember is that
a frozen dump can go stale — re-run ``dump`` after anything that changes what OCR
reads, ``det_box_thresh`` above all.

Both commands take files or whole directories, since a corpus is the normal unit
of work::

    uv run python -m backend.replay dump samples/ --out-dir samples/regression
    uv run python -m backend.replay check samples/regression
    uv run python -m backend.replay check samples/regression --update

A dump is a document's whole text, so a real one is PII and cannot be committed
as it stands. Scrubbing it — swapping each real name, address and identifier for
a placeholder of the same shape — makes it committable, and ``--ignore-text``
is how that edit is checked: it compares geometry, verdicts and regions while
letting the text differ, so it answers the one question a scrub raises, whether
the placeholder still redacts where the real value did::

    $EDITOR samples/regression/x_ocr.txt              # replace PII with placeholders
    ... check samples/regression --ignore-text        # no verdict may have moved
    ... check samples/regression --update             # scrub the snapshots too
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from backend.classifiers.base import Classifier
from backend.config import Config
from backend.models import Box, Line
from backend.pipeline import RedactionPipeline
from backend.regions import RegionParams
from backend.trace import Trace

# A line counts as redacted at 90% covered and as kept at 10%. The gap is
# deliberate: a line neither the rules nor a band meant to touch, but which a
# neighbour's padding clipped, matches no expectation and has to be looked at.
COVERED = 0.9
UNTOUCHED = 0.1

OCR_SUFFIX = "_ocr.txt"


# -- types ----------------------------------------------------------------- #
@dataclass(frozen=True)
class Page:
    """One page of a frozen dump: the OCR lines plus the size they were read at.

    The size is not decoration — ``region_boxes`` takes its search windows as
    *fractions* of the page, so replaying at a different height moves every band."""

    index: int
    width: int
    height: int
    lines: tuple[Line, ...]


@dataclass(frozen=True)
class Verdict:
    line: str  # the `line @(...)` header, verbatim
    kind: str  # REDACT | keep | PARTIAL
    # What put ink on the line: one of compute_boxes' own reasons (labeled-value,
    # static-rule, name-memory, classifier), or `region` for a band drawn over a
    # line nothing flagged, or `overlap` for a neighbour's padding spilling onto
    # it. `item table` on a `keep` records that the classifier was off there.
    reason: str | None
    coverage: float

    def render(self) -> str:
        if self.kind == "keep":
            return "    -> keep" + (f" ({self.reason})" if self.reason else "")
        if self.kind == "PARTIAL":
            # The percentage is commentary (it is not compared) but it is the
            # first thing you want to see when a line lands in the gap.
            return f"    -> PARTIAL {round(self.coverage * 100)}% ({self.reason})"
        return f"    -> REDACT ({self.reason})"


@dataclass(frozen=True)
class Outcome:
    index: int
    verdicts: tuple[Verdict, ...]
    regions: tuple[Box, ...]


# -- reading the dump ------------------------------------------------------ #
_PAGE_RE = re.compile(r"^=== page (\d+)(?: \((\d+)x(\d+)\))? ===$")
_LINE_RE = re.compile(r"^line @\((-?\d+),(-?\d+) (\d+)x(\d+) conf=(\S+)\): (.+)$")
_VERDICT_RE = re.compile(r"^\s+-> (REDACT|keep|PARTIAL)\b(.*)$")
_REGION_RE = re.compile(r"^region -> REDACT \[(-?\d+), (-?\d+), (-?\d+), (-?\d+)\]$")
_REASON_RE = re.compile(r"\(([^)]*)\)\s*$")


def format_line(line: Line) -> str:
    """The one-line form of an OCR line — byte-identical to what the trace emits.

    ``compute_boxes`` builds the same text through ``trace.add(fmt, *args)``
    (which defers the interpolation, since it runs per line on pages nobody
    reads), so the format string lives in two places by necessity. Nothing drifts
    unnoticed: :func:`_outcome` compares this against the trace's own header."""
    return "line @(%d,%d %dx%d conf=%s): %r" % (
        line.left,
        line.top,
        line.width,
        line.height,
        line.conf,
        line.text,
    )


def _parse_line(text: str) -> Line | None:
    m = _LINE_RE.match(text)
    if m is None:
        return None
    conf = None if m.group(5) == "None" else float(m.group(5))
    body = ast.literal_eval(m.group(6))
    if not isinstance(body, str):
        raise ValueError(f"line text is not a string: {text!r}")
    return Line(
        text=body,
        left=int(m.group(1)),
        top=int(m.group(2)),
        width=int(m.group(3)),
        height=int(m.group(4)),
        conf=conf,
    )


def parse_ocr(text: str) -> list[Page]:
    """Read a ``<stem>_ocr.txt`` back into pages. Page headers must carry a size."""
    pages: list[Page] = []
    index = width = height = -1
    lines: list[Line] = []

    def flush() -> None:
        if index >= 0:
            pages.append(Page(index=index, width=width, height=height, lines=tuple(lines)))

    for raw in text.splitlines():
        if not raw.strip() or raw.startswith("#"):
            continue
        page = _PAGE_RE.match(raw)
        if page is not None:
            if page.group(2) is None:
                raise ValueError(f"page header without a size: {raw!r}")
            flush()
            index, width, height = int(page.group(1)), int(page.group(2)), int(page.group(3))
            lines = []
            continue
        parsed = _parse_line(raw)
        if parsed is None:
            raise ValueError(f"not an OCR line: {raw!r}")
        if index < 0:
            raise ValueError(f"OCR line before any page header: {raw!r}")
        lines.append(parsed)
    flush()
    if not pages:
        raise ValueError("no pages found")
    return pages


def parse_expected(text: str) -> list[Outcome]:
    """Read a ``.expected.txt`` snapshot.

    Deliberately lenient about everything it does not recognise — ``match ...``
    and ``Ignoring ...`` lines, blanks, comments — so a ``PII_LOG_LEVEL=DEBUG``
    trace pasted in as a starting point parses. A ``code -> REDACT`` line does
    *not* pass: the code pass is off here, so an expectation naming one could
    never be met and silently ignoring it would hide that."""
    outcomes: list[Outcome] = []
    index = -1
    verdicts: list[Verdict] = []
    regions: list[Box] = []

    def flush() -> None:
        if index >= 0 or verdicts or regions:
            outcomes.append(
                Outcome(index=max(index, 0), verdicts=tuple(verdicts), regions=tuple(regions))
            )

    for raw in text.splitlines():
        page = _PAGE_RE.match(raw)
        if page is not None:
            flush()
            index, verdicts, regions = int(page.group(1)), [], []
            continue
        if raw.startswith("code -> REDACT"):
            raise ValueError("code boxes are not replayed; remove the `code -> REDACT` line")
        region = _REGION_RE.match(raw)
        if region is not None:
            regions.append(Box(*(int(g) for g in region.groups())))
            continue
        if raw.startswith("line @"):
            verdicts.append(Verdict(line=raw, kind="keep", reason=None, coverage=0.0))
            continue
        verdict = _VERDICT_RE.match(raw)
        if verdict is not None:
            if not verdicts:
                raise ValueError(f"verdict before any line: {raw!r}")
            reason = _REASON_RE.search(verdict.group(2))
            verdicts[-1] = Verdict(
                line=verdicts[-1].line,
                kind=verdict.group(1),
                reason=reason.group(1) if reason else None,
                coverage=0.0,
            )
    flush()
    return outcomes


# -- replaying ------------------------------------------------------------- #
def coverage(line: Line, boxes: Sequence[Box]) -> float:
    """Fraction of ``line``'s own rectangle that ``boxes`` cover.

    A boolean mask rather than summed intersections, because boxes overlap by
    design — a band is drawn over the line boxes underneath it — and adding the
    areas up would report well over 100%."""
    if line.width <= 0 or line.height <= 0:
        return 0.0
    mask = np.zeros((line.height, line.width), dtype=bool)
    for box in boxes:
        x0, y0 = max(line.left, box.x0), max(line.top, box.y0)
        x1 = min(line.left + line.width, box.x1)
        y1 = min(line.top + line.height, box.y1)
        if x0 >= x1 or y0 >= y1:
            continue
        mask[y0 - line.top : y1 - line.top, x0 - line.left : x1 - line.left] = True
    return float(mask.mean())


class _NoOCR:
    """The OCR seam, wired shut. Replay is only meaningful on the frozen text."""

    def lines(self, image: Image.Image) -> list[Line]:  # noqa: ARG002 - never reached
        raise AssertionError("replay must not OCR — pass lines= from the frozen dump")


def build_replay_pipeline(config: Config, classifier: Classifier) -> RedactionPipeline:
    """A pipeline with everything pixel-bound removed.

    Built directly rather than through :func:`backend.factory.build_pipeline`,
    which always constructs the OCR backend — the model load this whole module
    exists to skip. Region geometry and padding still come from ``config``, so
    tuning ``[redaction.regions]`` shows up in the snapshots where you want it."""
    return RedactionPipeline(
        ocr=_NoOCR(),
        classifier=classifier,
        fill=config.redaction.fill,
        padding=config.redaction.padding,
        unwarp_enabled=False,
        regions=(
            RegionParams(
                **config.redaction.regions.model_dump(),
                padding=config.redaction.padding,
            )
            if config.redaction.redact_regions
            else None
        ),
        codes=None,
    )


def _outcome(page: Page, boxes: list[Box], trace_text: str) -> Outcome:
    """Pair the trace's per-line reasons with the effective coverage."""
    headers: list[str] = []
    reasons: list[str | None] = []
    kept_by_table: list[bool] = []
    regions: list[Box] = []
    for raw in trace_text.splitlines():
        if raw.startswith("line @"):
            headers.append(raw)
            reasons.append(None)
            kept_by_table.append(False)
        elif raw.startswith("    -> REDACT ("):
            reasons[-1] = raw[len("    -> REDACT (") : -1]
        elif raw.startswith("    -> keep (item table)"):
            kept_by_table[-1] = True
        else:
            region = _REGION_RE.match(raw)
            if region is not None:
                regions.append(Box(*(int(g) for g in region.groups())))

    # compute_boxes skips blank lines silently, so pair against the same subset.
    spoken = [ln for ln in page.lines if ln.text.strip()]
    if len(spoken) != len(headers):
        raise RuntimeError(f"page {page.index}: {len(spoken)} lines but {len(headers)} traced")

    all_boxes = tuple(boxes)
    verdicts: list[Verdict] = []
    for line, header, reason, table in zip(spoken, headers, reasons, kept_by_table):
        if format_line(line) != header:
            raise RuntimeError(f"trace format drifted:\n  {format_line(line)}\n  {header}")
        cov = coverage(line, all_boxes)
        if cov <= UNTOUCHED:
            verdicts.append(Verdict(header, "keep", "item table" if table else None, cov))
            continue
        # Ink on a line nothing flagged comes from one of two places, and they
        # want different fixes: a band drawn across it, or the padded box of the
        # line above or below spilling over.
        source = reason or ("region" if coverage(line, regions) > UNTOUCHED else "overlap")
        verdicts.append(Verdict(header, "REDACT" if cov >= COVERED else "PARTIAL", source, cov))
    return Outcome(index=page.index, verdicts=tuple(verdicts), regions=tuple(regions))


def replay(pipeline: RedactionPipeline, pages: list[Page]) -> list[Outcome]:
    """Run the detection passes over frozen pages, in document order.

    One ``known_names`` set spans the pages exactly as ``run_redaction`` does it,
    which is what makes the forward-only name memory (labeled on page 1, bare on
    page 2) part of what the snapshot pins."""
    known_names: set[str] = set()
    outcomes: list[Outcome] = []
    for page in pages:
        trace = Trace(collect=True)
        # Blank, but the recorded size: compute_boxes reads only width/height off
        # it here, since `lines` is supplied and the code pass is off.
        canvas = Image.new("RGB", (page.width, page.height))
        boxes = pipeline.compute_boxes(
            canvas,
            lines=list(page.lines),
            known_names=known_names,
            trace=trace,
        )
        outcomes.append(_outcome(page, boxes, trace.collected or ""))
    return outcomes


# -- rendering and comparing ----------------------------------------------- #
def render(outcomes: list[Outcome], classifier: str) -> str:
    out = [f"# classifier: {classifier}"]
    for outcome in outcomes:
        out.append(f"=== page {outcome.index} ===")
        for verdict in outcome.verdicts:
            out.append(verdict.line)
            out.append(verdict.render())
        out.extend(f"region -> REDACT {box.as_list()}" for box in outcome.regions)
    return "\n".join(out) + "\n"


def _excerpt(header: str, width: int = 48) -> str:
    text = header.split(": ", 1)[-1]
    return text if len(text) <= width else text[: width - 1] + "…"


def _verdict_text(verdict: Verdict) -> str:
    return verdict.kind + (f" ({verdict.reason})" if verdict.reason else "")


def _geometry(header: str) -> str:
    """A ``line @(...)`` header without its recognized text — the part a scrub
    leaves alone. The geometry can hold no ``"): "``, so the first one splits."""
    return header.split("): ", 1)[0]


def compare(
    outcomes: list[Outcome], expected: list[Outcome], ignore_text: bool = False
) -> list[str]:
    """Every way the replay differs from the snapshot, as readable one-liners.

    ``ignore_text`` compares a line by its geometry alone. It exists for one job:
    checking that **scrubbing PII out of a dump did not change any decision**.
    Swapping a real name for a placeholder rewrites the text the snapshot was
    written against, so the ordinary staleness guard fires on every edited line
    and buries the one thing worth knowing — whether the replacement still
    redacts. Position, size, confidence, verdicts and regions are all still
    compared, so a line going missing or a rule going quiet is still caught."""
    failures: list[str] = []
    if len(outcomes) != len(expected):
        return [f"expected {len(expected)} page(s), replayed {len(outcomes)}"]
    for actual, want in zip(outcomes, expected):
        page = f"page {actual.index}"
        if len(actual.verdicts) != len(want.verdicts):
            failures.append(
                f"{page}: expected {len(want.verdicts)} OCR line(s), replayed "
                f"{len(actual.verdicts)} — the snapshot is stale, regenerate it"
            )
            continue
        for i, (got, wanted) in enumerate(zip(actual.verdicts, want.verdicts)):
            same = (
                _geometry(got.line) == _geometry(wanted.line)
                if ignore_text
                else got.line == wanted.line
            )
            if not same:
                failures.append(
                    f"{page} line {i}: the snapshot was written for different OCR "
                    f"output, regenerate it\n    dump:     {got.line}\n"
                    f"    snapshot: {wanted.line}"
                )
            elif (got.kind, got.reason) != (wanted.kind, wanted.reason):
                failures.append(
                    f"{page} line {i} {_excerpt(got.line)}: expected "
                    f"{_verdict_text(wanted)}, got {_verdict_text(got)}"
                )
        # Regions are a multiset: `_grow` yields the same block from any member,
        # so their order carries no meaning and only the set of boxes does.
        got_regions, want_regions = list(actual.regions), list(want.regions)
        for box in actual.regions:
            if box in want_regions:
                want_regions.remove(box)
                got_regions.remove(box)
        failures.extend(f"{page}: unexpected region {box.as_list()}" for box in got_regions)
        failures.extend(f"{page}: missing region {box.as_list()}" for box in want_regions)
    return failures


# -- CLI ------------------------------------------------------------------- #
def expected_path(ocr: Path) -> Path:
    return ocr.with_suffix(".expected.txt")


def _ocr_files(targets: list[str]) -> list[Path]:
    files: list[Path] = []
    for raw in targets:
        path = Path(raw)
        if path.is_dir():
            files.extend(sorted(p for p in path.iterdir() if p.name.endswith(OCR_SUFFIX)))
        elif path.name.endswith(OCR_SUFFIX):
            files.append(path)
        else:
            raise ValueError(f"not a {OCR_SUFFIX} file or a directory: {path}")
    return files


def _plan_dumps(targets: list[str], out_dir: str | None) -> tuple[list[tuple[Path, Path]], int]:
    """Pair each sample with the dump it will be written to.

    Worked out *before* the models load, because a whole corpus is the normal
    argument and the two ways a directory bites both want reporting up front: two
    samples sharing a stem (``x.pdf`` and ``x.png`` are one document under two
    names) would otherwise have the second silently overwrite the first, and an
    ``--out-dir`` that does not exist yet would fail on the first write, after
    paying for its OCR."""
    from backend.cli import collect_input_files

    plan: list[tuple[Path, Path]] = []
    claimed: dict[Path, Path] = {}
    status = 0
    for sample in collect_input_files(targets):
        out = (Path(out_dir) if out_dir else sample.parent) / (sample.stem + OCR_SUFFIX)
        if out in claimed:
            print(f"{sample.name}: skipped — {claimed[out].name} already dumps to {out.name}")
            status = 1
            continue
        claimed[out] = sample
        plan.append((sample, out))
    for directory in {out.parent for _, out in plan}:
        directory.mkdir(parents=True, exist_ok=True)
    return plan, status


def _read_pages(sample: Path, config: Config) -> list[Image.Image]:
    """The rasterized pages of a sample, decoded the way every other entry point
    decodes them — EXIF orientation applied at the boundary, PDFs at ``pdf_dpi``."""
    from PIL import ImageOps

    from backend.pdf import rasterize_pdf

    if sample.suffix.lower() == ".pdf":
        return rasterize_pdf(
            sample.read_bytes(),
            dpi=config.redaction.pdf_dpi,
            max_pages=config.redaction.max_pages,
            max_pixels=config.api.max_image_pixels,
        )
    with Image.open(sample) as src:
        return [ImageOps.exif_transpose(src).convert("RGB")]


def _dump(targets: list[str], out_dir: str | None) -> int:
    """Re-OCR samples and write the frozen dumps. The one slow command.

    A file or a directory (one level deep, already-redacted outputs skipped —
    :func:`backend.cli.collect_input_files` decides, so the CLIs agree on what
    counts as a sample)."""
    from backend.config import load_config
    from backend.factory import build_pipeline

    plan, status = _plan_dumps(targets, out_dir)
    if not plan:
        print("no samples found")
        return 1

    config = load_config()
    pipeline = build_pipeline(config)
    ocr_backend, _ = config.engine.resolve()
    for sample, out in plan:
        try:
            pages = _read_pages(sample, config)
            text = [
                f"# document: {sample.name}",
                f"# ocr: {ocr_backend}",
                f"# unwarp: {str(config.redaction.unwarp).lower()}",
            ]
            for index, page in enumerate(pages):
                image = pipeline.unwarp(page) if config.redaction.unwarp else page.convert("RGB")
                text.append(f"=== page {index} ({image.width}x{image.height}) ===")
                text.extend(format_line(line) for line in pipeline.read_lines(image))
        except Exception as e:  # noqa: BLE001 - one bad file must not end the run
            # A corpus run is minutes of OCR; losing all of it to a single
            # unreadable PDF at file 28 is the wrong trade.
            print(f"{sample.name}: FAILED — {type(e).__name__}: {e}")
            status = 1
            continue
        out.write_text("\n".join(text) + "\n", encoding="utf-8")
        print(f"{sample.name}: {len(pages)} page(s) -> {out}")
    return status


def _check(targets: list[str], update: bool, ignore_text: bool = False) -> int:
    from backend.config import load_config
    from backend.factory import build_classifier

    config = load_config()
    _, classifier_name = config.engine.resolve()
    pipeline = build_replay_pipeline(config, build_classifier(config))
    status = 0
    for ocr in _ocr_files(targets):
        outcomes = replay(pipeline, parse_ocr(ocr.read_text(encoding="utf-8")))
        text = render(outcomes, classifier_name)
        out = expected_path(ocr)
        if update:
            out.write_text(text, encoding="utf-8")
            print(f"{ocr.name}: wrote {out}")
            continue
        if not out.is_file():
            print(f"{ocr.name}: no snapshot at {out} — run `check --update`")
            status = 1
            continue
        failures = compare(
            outcomes, parse_expected(out.read_text(encoding="utf-8")), ignore_text
        )
        if failures:
            status = 1
            print(f"{ocr.name}: {len(failures)} difference(s)")
            for failure in failures:
                print(f"  {failure}")
        else:
            print(f"{ocr.name}: ok")
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m backend.replay",
        description="Freeze OCR output, then replay redaction decisions against it.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    dump = sub.add_parser("dump", help="OCR samples into <stem>_ocr.txt (loads models)")
    dump.add_argument(
        "targets",
        nargs="+",
        metavar="FILE|DIR",
        help="a sample, or a directory of them (one level deep)",
    )
    dump.add_argument(
        "--out-dir", metavar="DIR", help="created if missing; default: beside each sample"
    )

    check = sub.add_parser("check", help="replay <stem>_ocr.txt against its snapshot")
    check.add_argument("targets", nargs="+", metavar="OCR_TXT|DIR")
    check.add_argument("--update", action="store_true", help="rewrite the snapshots")
    check.add_argument(
        "--ignore-text",
        action="store_true",
        help="compare lines by geometry only — for checking that scrubbing PII "
        "out of a dump changed no verdict",
    )

    args = parser.parse_args(argv)
    if args.command == "dump":
        return _dump(args.targets, args.out_dir)
    return _check(args.targets, args.update, args.ignore_text)


if __name__ == "__main__":
    sys.exit(main())
