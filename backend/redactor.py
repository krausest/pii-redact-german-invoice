"""What one page decides: candidates from every detector, what kept lines
reject, what the memory learns and recalls, and the boxes that follow."""

from __future__ import annotations

from collections.abc import Iterable

from backend.classifiers.base import Classifier
from backend.detectors import Detector, MemoryDetector, ModelDetector, RuleDetector
from backend.document import Document
from backend.harvest import Memory
from backend.layout import LayoutRegion, document_order, region_boxes, signature_boxes, subtract
from backend.models import Box, Line
from backend.pii import Span
from backend.rules import birth_year_note, item_table_lines, kept_lines
from backend.trace import Trace
from backend.verdict import PageVerdict


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
    ) -> PageVerdict:
        text_lines = [ln for ln in lines if ln.text.strip()]
        doc = _read(text_lines, regions)
        keep = kept_lines(text_lines, doc.item_table)
        dropped: dict[Line, list[Span]] = {}
        hits = _merge(
            _sift(doc, detector, keep, dropped, trace)
            for detector in (RuleDetector(), ModelDetector(classifier))
        )
        # Learned from the survivors only: a model's find in the item table must
        # not spread across the document.
        memory.learn(doc, hits, trace)
        recalled = _sift(doc, MemoryDetector(memory), keep, dropped, trace)
        found = {ln: spans for ln, spans in _merge([hits, recalled]).items() if spans}
        pad = self._padding
        return PageVerdict(
            lines=lines,
            regions=regions,
            reading=doc.lines,
            found=found,
            keep=keep,
            dropped=dropped,
            line_boxes=_line_boxes(found, keep, pad),
            notes=_birth_year_notes(found),
            region_boxes=region_boxes(lines, regions, set(found), pad, set(keep)),
            signatures=signature_boxes(lines, page_width, pad),
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
) -> dict[Line, list[Span]]:
    """``detector``'s spans per line that its line's keep reason admits; the
    rest go to ``dropped``."""
    survivors: dict[Line, list[Span]] = {}
    for line, spans in doc.per_line(detector.spans(doc, trace)).items():
        why = keep.get(line)
        survivors[line] = [s for s in spans if detector.admits(s, why)]
        lost = [s for s in spans if not detector.admits(s, why)]
        if lost:
            dropped.setdefault(line, []).extend(lost)
    return survivors


def _merge(per_line: Iterable[dict[Line, list[Span]]]) -> dict[Line, list[Span]]:
    """The spans of several sources, per line, in the order given."""
    merged: dict[Line, list[Span]] = {}
    for spans_by_line in per_line:
        for line, spans in spans_by_line.items():
            merged.setdefault(line, []).extend(spans)
    return merged


def _line_boxes(found: dict[Line, list[Span]], keep: dict[Line, str], pad: int) -> list[Box]:
    """A padded box over every redacted line. A kept line clips its neighbours'
    padding, never their own box: the kept line is content, the padding is not."""
    holes = [ln.box for ln in keep if ln not in found]
    boxes: list[Box] = []
    for line in found:
        padded = line.box.padded(pad)
        parts = subtract(padded, holes)
        if parts != [padded]:
            parts = [p for p in parts if not line.box.contains(p)] + [line.box]
        boxes.extend(parts)
    return boxes


def _birth_year_notes(found: dict[Line, list[Span]]) -> dict[Line, Box]:
    """The birth year to print over each redacted birthdate."""
    notes: dict[Line, Box] = {}
    for line, spans in found.items():
        note = birth_year_note(line, spans)
        if note is not None:
            notes[line] = note
    return notes
