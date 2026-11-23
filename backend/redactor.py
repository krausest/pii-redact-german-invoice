"""What one page decides: candidates from every detector, what kept lines
reject, what the memory learns and recalls, and the boxes that follow."""

from __future__ import annotations

import re
from collections.abc import Iterable

from backend.classifiers.base import Classifier
from backend.codes import CodeBox
from backend.detectors import Detector, ListDetector, MemoryDetector, ModelDetector, RuleDetector
from backend.document import Document
from backend.harvest import Memory
from backend.layout import LayoutRegion, document_order, region_boxes, signature_boxes, subtract
from backend.models import Box, Line
from backend.pii import Span
from backend.rules import birth_year_note, item_table_lines, kept_lines
from backend.trace import Trace
from backend.verdict import PageVerdict
from backend.wordlists import EMPTY, WordLists

# What may sit between two redacted ranges and still join them into one box.
_GLUE = re.compile(r"[\W_]*")


class PageRedactor:
    """Turns one page's OCR lines and layout regions into a :class:`PageVerdict`."""

    def __init__(self, padding: int) -> None:
        self._padding = padding

    def decide(
        self,
        page_width: int,
        lines: list[Line],
        regions: list[LayoutRegion],
        classifier: Classifier,
        memory: Memory,
        trace: Trace,
        lists: WordLists = EMPTY,
        codes: list[CodeBox] | None = None,
    ) -> PageVerdict:
        text_lines = [ln for ln in lines if ln.text.strip()]
        doc = _read(text_lines, regions)
        keep = kept_lines(text_lines, doc.item_table)
        dropped: dict[Line, list[Span]] = {}
        hits = _merge(
            [
                _sift(doc, RuleDetector(), keep, dropped, trace, lists),
                _sift(doc, ModelDetector(classifier), keep, dropped, trace, lists),
                _sift(doc, ListDetector(lists), keep, dropped, trace),
            ]
        )
        # Learned from the survivors only: a model's find in the item table must
        # not spread across the document.
        memory.learn(doc, hits, trace)
        recalled = _sift(doc, MemoryDetector(memory), keep, dropped, trace, lists)
        found = {ln: spans for ln, spans in _merge([hits, recalled]).items() if spans}
        pad = self._padding
        return PageVerdict(
            lines=lines,
            regions=regions,
            reading=doc.lines,
            found=found,
            keep=keep,
            dropped=dropped,
            line_boxes=_word_boxes(found, keep, pad),
            notes=_birth_year_notes(found),
            region_boxes=region_boxes(lines, regions, set(found), pad, set(keep)),
            signatures=signature_boxes(lines, page_width, pad),
            codes=codes or [],
        )


def _read(lines: list[Line], regions: list[LayoutRegion]) -> Document:
    """The page's lines in reading order, with its item table."""
    return Document(document_order(lines, regions), item_table=item_table_lines(lines))


def _sift(
    doc: Document,
    detector: Detector,
    keep: dict[Line, str],
    dropped: dict[Line, list[Span]],
    trace: Trace,
    veto: WordLists = EMPTY,
) -> dict[Line, list[Span]]:
    """``detector``'s spans per line that its line's keep reason admits, with
    ``veto``'s keep entries cut out; a rejected or cut span goes to ``dropped``."""
    survivors: dict[Line, list[Span]] = {}
    for line, spans in doc.per_line(detector.spans(doc, trace)).items():
        why = keep.get(line)
        survivors[line] = []
        for s in spans:
            rest = veto.carve(s) if detector.admits(s, why) else []
            survivors[line] += rest
            if rest != [s]:
                dropped.setdefault(line, []).append(s)
    return survivors


def _merge(per_line: Iterable[dict[Line, list[Span]]]) -> dict[Line, list[Span]]:
    """The spans of several sources, per line, in the order given."""
    merged: dict[Line, list[Span]] = {}
    for spans_by_line in per_line:
        for line, spans in spans_by_line.items():
            merged.setdefault(line, []).extend(spans)
    return merged


def _word_boxes(found: dict[Line, list[Span]], keep: dict[Line, str], pad: int) -> list[Box]:
    """A padded box over every run of redacted words. A kept line clips the
    padding, never the box itself: the kept line is content, the padding is not."""
    holes = [ln.box for ln in keep if ln not in found]
    boxes: list[Box] = []
    for line, spans in found.items():
        for start, end in redacted_runs(line, spans):
            own = line.span_box(start, end)
            padded = own.padded(pad)
            parts = subtract(padded, holes)
            if parts != [padded]:
                parts = [p for p in parts if not own.contains(p)] + [own]
            boxes.extend(parts)
    return boxes


def redacted_runs(line: Line, spans: list[Span]) -> list[tuple[int, int]]:
    """The spans' ranges on ``line``, widened to whole tokens and joined where
    only whitespace or punctuation separates them."""
    runs: list[tuple[int, int]] = []
    for start, end in sorted(line.tokens(s.start, s.end) for s in spans):
        if runs and (start <= runs[-1][1] or _GLUE.fullmatch(line.text, runs[-1][1], start)):
            runs[-1] = (runs[-1][0], max(runs[-1][1], end))
        else:
            runs.append((start, end))
    return runs


def _birth_year_notes(found: dict[Line, list[Span]]) -> dict[Line, Box]:
    """The birth year to print over each redacted birthdate."""
    notes: dict[Line, Box] = {}
    for line, spans in found.items():
        note = birth_year_note(line, spans)
        if note is not None:
            notes[line] = note
    return notes
