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

from collections.abc import Callable

from PIL import Image, ImageDraw, ImageFont

from backend.classifiers.base import Classifier
from backend.harvest import Memory
from backend.layout import LayoutRegion, PaddleLayoutDetector, assign_lines, draw_layout_debug
from backend.models import Box, Line
from backend.ocr.base import OCRBackend
from backend.redactor import PageRedactor
from backend.registry import ModelRegistry
from backend.trace import Trace, trace_page
from backend.unwarp import DocUnwarper
from backend.verdict import PageVerdict


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
        self._models = ModelRegistry(
            classifier, classifier_factories, default_classifier, unwarper, unwarper_factory
        )
        self._redactor = PageRedactor(padding)
        self._fill = fill
        self._unwarp_enabled = unwarp_enabled
        # No detector: no layout regions, and no whole-region redaction.
        self._layout = layout

    # -- primitives -------------------------------------------------------- #
    def unwarp(self, image: Image.Image) -> Image.Image:
        """Flatten a photographed page. The unwarper is built on first use."""
        return self._models.unwarper().unwarp(image.convert("RGB"))

    def classifier(self, name: str | None = None) -> Classifier:
        """The classifier called ``name`` (``None``: the default), built on first use."""
        return self._models.classifier(name)

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
        """Boxes to redact, in the pixel space of ``image``; see :meth:`compute_verdict`."""
        return self.compute_verdict(image, lines, memory, trace, regions, classifier).boxes

    def compute_verdict(
        self,
        image: Image.Image,
        lines: list[Line] | None = None,
        memory: Memory | None = None,
        trace: Trace | None = None,
        regions: list[LayoutRegion] | None = None,
        classifier: str | None = None,
    ) -> PageVerdict:
        """What the page decides: which lines go black and why, and the boxes.

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
        verdict = self._redactor.decide(
            image.width, lines, regions, self.classifier(classifier), memory, trace
        )
        trace_page(trace, verdict)
        return verdict

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
