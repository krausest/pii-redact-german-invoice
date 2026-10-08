"""The redaction pipeline: three composable primitives shared by CLI and API.

* ``unwarp(image)``        — flatten a photographed page (the only unwarp user).
* ``compute_boxes(image)`` — OCR + classify, returning the boxes to redact **in
  the pixel space of the image passed in** (no unwarp).
* ``apply_boxes(image, boxes)`` — draw the filled rectangles.

``redact(image)`` is the full CLI path: unwarp (when enabled) then
``apply_boxes(compute_boxes(...))``. The API composes the primitives itself in
:mod:`backend.service`, because ``POST /api/redact`` decides per request which of
them to run.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from PIL import Image, ImageDraw, ImageFont

from backend.classifiers.base import Classifier
from backend.document import Document
from backend.models import Box, Line
from backend.ocr.base import OCRBackend
from backend.layout import (
    LayoutRegion,
    PaddleLayoutDetector,
    assign_lines,
    document_order,
    draw_layout_debug,
    region_boxes,
    signature_boxes,
    subtract,
)
from backend.harvest import Memory
from backend.pii import Span
from backend.rules import birth_year_note, item_table_lines, kept_lines, rejected, rule_spans
from backend.trace import Trace, trace_page
from backend.unwarp import DocUnwarper
from backend.verdict import PageVerdict


def _sift(
    candidates: dict[Line, list[Span]],
    keep: dict[Line, str],
    dropped: dict[Line, list[Span]],
) -> dict[Line, list[Span]]:
    """The candidate spans per line that survive the line's keep reason
    (:func:`~backend.rules.rejected`); the rest go to ``dropped``."""
    survivors: dict[Line, list[Span]] = {}
    for line, spans in candidates.items():
        why = keep.get(line)
        survivors[line] = [s for s in spans if not rejected(s, why)]
        lost = [s for s in spans if rejected(s, why)]
        if lost:
            dropped.setdefault(line, []).extend(lost)
    return survivors


def _read(lines: list[Line], regions: list[LayoutRegion]) -> Document:
    """The page's readable lines in reading order, with its item table (found on
    *all* OCR lines: it is page geometry, not text)."""
    return Document(
        [ln for ln in document_order(lines, regions) if ln.text.strip()],
        item_table=item_table_lines(lines),
    )


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


def _print_note(draw: ImageDraw.ImageDraw, box: Box) -> None:
    """``box.text`` left-aligned and vertically centred on ``box``, as large as
    the box's height allows and shrunk until it fits its width."""
    height = box.y1 - box.y0
    size = max(6, int(height * 0.85))
    font = ImageFont.load_default(size=size)
    while size > 6 and draw.textlength(box.text, font=font) > box.x1 - box.x0:
        size -= 1
        font = ImageFont.load_default(size=size)
    draw.text((box.x0, box.y0 + height / 2), box.text, fill=(255, 255, 255), font=font, anchor="lm")


class RedactionPipeline:
    def __init__(
        self,
        ocr: OCRBackend,
        classifier: Classifier | None = None,
        unwarper: DocUnwarper | None = None,
        fill: tuple[int, int, int] = (0, 0, 0),
        padding: int = 2,
        unwarp_enabled: bool = False,
        unwarper_factory: Callable[[], DocUnwarper] | None = None,
        layout: PaddleLayoutDetector | None = None,
        classifier_factories: dict[str, Callable[[], Classifier]] | None = None,
        default_classifier: str | None = None,
    ) -> None:
        self._ocr = ocr
        # `classifier=` hands over one built classifier (tests, replay);
        # `classifier_factories=` builds each selectable one on first use.
        self._factories = dict(classifier_factories or {})
        self._classifiers: dict[str, Classifier] = {}
        self._default = default_classifier or ""
        if classifier is not None:
            self._classifiers[self._default] = classifier
        self._unwarper = unwarper
        self._unwarper_factory = unwarper_factory
        self._fill = fill
        self._padding = padding
        self._unwarp_enabled = unwarp_enabled
        # No detector: no layout regions, and no whole-region redaction.
        self._layout = layout
        # Guards the lazy model builds: building a model twice costs a gigabyte.
        self._build_lock = threading.Lock()

    # -- primitives -------------------------------------------------------- #
    def unwarp(self, image: Image.Image) -> Image.Image:
        """Flatten a photographed page. The unwarper is built on first use."""
        if self._unwarper is None:
            with self._build_lock:
                if self._unwarper is None:
                    if self._unwarper_factory is None:
                        raise RuntimeError("unwarp requested but no DocUnwarper is configured")
                    self._unwarper = self._unwarper_factory()
        return self._unwarper.unwarp(image.convert("RGB"))

    def classifier(self, name: str | None = None) -> Classifier:
        """The classifier called ``name`` (``None``: the default), built on first
        use. A pipeline holding a single handed-over classifier ignores the name."""
        if not self._factories and len(self._classifiers) == 1:
            return next(iter(self._classifiers.values()))
        key = self._default if name is None else name
        found = self._classifiers.get(key)
        if found is not None:
            return found
        with self._build_lock:
            if key not in self._classifiers:
                factory = self._factories.get(key)
                if factory is None:
                    raise RuntimeError(f"no classifier configured under {key!r}")
                self._classifiers[key] = factory()
        return self._classifiers[key]

    def read_lines(self, image: Image.Image) -> list[Line]:
        """The OCR lines of ``image``; pass them back via ``lines=`` to skip a
        second OCR pass."""
        return self._ocr.lines(image)

    def regions(self, image: Image.Image) -> list[LayoutRegion]:
        """The layout regions of ``image`` (none without a detector); pass them
        back via ``regions=`` to skip a second detection."""
        return self._layout.regions(image) if self._layout is not None else []

    def compute_boxes(
        self,
        image: Image.Image,
        lines: list[Line] | None = None,
        memory: Memory | None = None,
        trace: Trace | None = None,
        regions: list[LayoutRegion] | None = None,
        classifier: str | None = None,
    ) -> list[Box]:
        """Boxes to redact, in the pixel space of ``image``.

        ``lines`` and ``regions`` must be this very image's, if given. ``memory``
        carries names and birthdates from page to page (forward only); ``None``
        keeps it page-local. ``classifier`` names the model to run. ``trace``
        collects why each box exists."""
        if lines is None:
            lines = self._ocr.lines(image)
        trace = trace or Trace()
        if regions is None:
            regions = self.regions(image)
        memory = memory if memory is not None else Memory()
        verdict = self._decide(image.width, lines, regions, memory, trace, classifier)
        trace_page(trace, verdict)
        return verdict.boxes

    def _decide(
        self,
        page_width: int,
        lines: list[Line],
        regions: list[LayoutRegion],
        memory: Memory,
        trace: Trace,
        classifier: str | None,
    ) -> PageVerdict:
        """Read the page, collect candidates, let kept lines reject some, add
        what the memory recalls, and turn the survivors into boxes."""
        doc = _read(lines, regions)
        keep = kept_lines(lines, doc.item_table)
        dropped: dict[Line, list[Span]] = {}
        hits = self._candidates(doc, keep, dropped, trace, classifier)
        # Learned from the survivors only: a model's find in the item table must
        # not spread across the document.
        memory.learn(doc, hits, trace)
        recalled = _sift(doc.per_line(memory.recall(doc)), keep, dropped)
        merged = {ln: hits[ln] + recalled[ln] for ln in hits}
        found = {ln: spans for ln, spans in merged.items() if spans}
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

    def _candidates(
        self,
        doc: Document,
        keep: dict[Line, str],
        dropped: dict[Line, list[Span]],
        trace: Trace,
        classifier: str | None,
    ) -> dict[Line, list[Span]]:
        """Every line's spans from the rules and the classifier that survive its
        keep reason; the rejected ones go to ``dropped``."""
        rules = doc.per_line(rule_spans(doc))
        model = doc.per_line(self.classifier(classifier).spans(doc.text, trace))
        return _sift({ln: rules[ln] + model[ln] for ln in rules}, keep, dropped)

    def apply_boxes(
        self,
        image: Image.Image,
        boxes: list[Box],
        fill: tuple[int, int, int] | None = None,
    ) -> Image.Image:
        """Fill ``boxes``; a note's text is printed in white on its box, after
        every box is filled so none covers it."""
        draw = ImageDraw.Draw(image)
        for box in boxes:
            draw.rectangle(box.as_list(), fill=fill if fill is not None else self._fill)
        for box in boxes:
            if box.text:
                _print_note(draw, box)
        return image

    # -- full path --------------------------------------------------------- #
    def redact(self, image: Image.Image) -> Image.Image:
        """Unwarp (when enabled) then blacken the computed boxes."""
        work = self.unwarp(image) if self._unwarp_enabled else image.convert("RGB")
        return self.apply_boxes(work, self.compute_boxes(work))

    # -- debug-only extra --------------------------------------------------- #
    def layout_debug_image(
        self,
        image: Image.Image,
        lines: list[Line] | None = None,
        regions: list[LayoutRegion] | None = None,
    ) -> Image.Image | None:
        """``image`` with the detected regions and line groups outlined, for
        ``--debug-layout``; ``None`` without a layout detector."""
        if self._layout is None:
            return None
        if lines is None:
            lines = self._ocr.lines(image)
        if regions is None:
            regions = self.regions(image)
        text_lines = [ln for ln in lines if ln.text.strip()]
        return draw_layout_debug(image, assign_lines(text_lines, regions), regions)
