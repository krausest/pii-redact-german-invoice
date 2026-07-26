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

import logging
from collections.abc import Callable

from PIL import Image, ImageDraw

from backend.classifiers.base import Classifier
from backend.models import Box, Line
from backend.ocr.base import OCRBackend
from backend.rules import birthdate_indices, line_matches_static_rule
from backend.unwarp import DocUnwarper

logger = logging.getLogger(__name__)


class RedactionPipeline:
    def __init__(
        self,
        ocr: OCRBackend,
        classifier: Classifier,
        unwarper: DocUnwarper | None = None,
        fill: tuple[int, int, int] = (0, 0, 0),
        padding: int = 2,
        unwarp_enabled: bool = True,
        unwarper_factory: Callable[[], DocUnwarper] | None = None,
    ) -> None:
        self._ocr = ocr
        self._classifier = classifier
        self._unwarper = unwarper
        self._unwarper_factory = unwarper_factory
        self._fill = fill
        self._padding = padding
        self._unwarp_enabled = unwarp_enabled

    # -- primitives -------------------------------------------------------- #
    def unwarp(self, image: Image.Image) -> Image.Image:
        """Flatten a photographed page. The unwarper is built on first use (it
        loads a model), so a process that is only ever asked for ``unwarp=false``
        never pays for it."""
        if self._unwarper is None:
            if self._unwarper_factory is None:
                raise RuntimeError("unwarp requested but no DocUnwarper is configured")
            self._unwarper = self._unwarper_factory()
        return self._unwarper.unwarp(image.convert("RGB"))

    def compute_boxes(self, image: Image.Image) -> list[Box]:
        """Boxes to redact, in the pixel space of ``image`` (no unwarp)."""
        lines: list[Line] = self._ocr.lines(image)
        birth_idx = birthdate_indices(lines)
        pad = self._padding
        boxes: list[Box] = []
        for i, line in enumerate(lines):
            if not line.text.strip():
                continue
            # DEBUG: every OCR line with its pixel box, then any classifier
            # matches (indented, logged by the classifier), then the verdict.
            logger.debug(
                "line @(%d,%d %dx%d conf=%s): %r",
                line.left,
                line.top,
                line.width,
                line.height,
                line.conf,
                line.text,
            ) 
            reason = (
                "birthdate"
                if i in birth_idx
                else "static-rule"
                if line_matches_static_rule(line.text)
                else "classifier"
                if self._classifier.is_pii(line.text)
                else None
            )
            if reason is not None:
                logger.debug("    -> REDACT (%s)", reason)
                boxes.append(
                    Box(
                        line.left - pad,
                        line.top - pad,
                        line.left + line.width + pad,
                        line.top + line.height + pad,
                    )
                )
        return boxes

    def apply_boxes(
        self,
        image: Image.Image,
        boxes: list[Box],
        fill: tuple[int, int, int] | None = None,
    ) -> Image.Image:
        draw = ImageDraw.Draw(image)
        for box in boxes:
            draw.rectangle(box.as_list(), fill=fill if fill is not None else self._fill)
        return image

    # -- full path --------------------------------------------------------- #
    def redact(self, image: Image.Image) -> Image.Image:
        """Unwarp (when enabled) then blacken the computed boxes."""
        work = self.unwarp(image) if self._unwarp_enabled else image.convert("RGB")
        return self.apply_boxes(work, self.compute_boxes(work))
