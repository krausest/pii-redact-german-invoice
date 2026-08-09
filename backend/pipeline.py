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

from PIL import Image, ImageDraw

from backend.classifiers.base import Classifier
from backend.codes import CodeParams, code_boxes
from backend.models import Box, Line
from backend.ocr.base import OCRBackend
from backend.regions import RegionParams, region_boxes
from backend.rules import (
    harvest_names,
    item_table_indices,
    labeled_value_indices,
    mentions_name,
    static_rule_match,
)
from backend.trace import Trace
from backend.unwarp import DocUnwarper


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
        regions: RegionParams | None = None,
        codes: CodeParams | None = None,
    ) -> None:
        self._ocr = ocr
        self._classifier = classifier
        self._unwarper = unwarper
        self._unwarper_factory = unwarper_factory
        self._fill = fill
        self._padding = padding
        self._unwarp_enabled = unwarp_enabled
        # Whole-region redaction is off unless geometry is supplied — `None` is
        # both "no params" and "don't run it", so there is no second flag to
        # keep in sync. `build_pipeline` decides from `redaction.redact_regions`.
        self._regions = regions
        # Same convention for the QR/DataMatrix pass, from `redaction.redact_codes`.
        self._codes = codes

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

    def read_lines(self, image: Image.Image) -> list[Line]:
        """The OCR lines of ``image`` — the text ``compute_boxes`` classifies.
        Exposed for :mod:`backend.replay`, which freezes that text to a file and
        replays every later stage against it; pass lines back via ``lines=`` to
        skip a second OCR pass."""
        return self._ocr.lines(image)

    def compute_boxes(
        self,
        image: Image.Image,
        lines: list[Line] | None = None,
        known_names: set[str] | None = None,
        trace: Trace | None = None,
    ) -> list[Box]:
        """Boxes to redact, in the pixel space of ``image`` (no unwarp): one per
        flagged OCR line, plus — when configured — the header / footer /
        sender-column boxes and the QR / DataMatrix boxes, which are the ones not
        tied to a line.

        ``lines`` skips the OCR call when the caller already holds
        :meth:`read_lines` output for this exact ``image``.

        ``known_names`` is the name-memory accumulator: names harvested from this
        page are added *to the passed set*, so a caller looping over a document's
        pages shares one set and a surname labeled on page 1 is redacted bare on
        page 2. The carry is forward-only — a name first seen on page 2 does not
        re-redact page 1 — which suffices because the labeled occurrence leads.
        ``None`` keeps the memory page-local.

        ``trace`` collects the per-line commentary — why each box exists — for a
        caller that was asked for it (``?debug=true``). Omitting it still logs
        everything at DEBUG; a :class:`Trace` that collects nothing is the same
        code path, not a second one."""
        if lines is None:
            lines = self._ocr.lines(image)
        trace = trace or Trace()
        # The item table: the deterministic rules and the name memory run there
        # as everywhere else, the classifier does not (see item_table_indices).
        # Computed first because the labeled-value pass needs it too — its column
        # walks stop at the table rather than chaining down into the invoice body.
        table_idx = item_table_indices(lines)
        if table_idx:
            trace.add("item table: classifier off for %d line(s)", len(table_idx))
        labeled_idx = labeled_value_indices(lines, table_idx)
        names = known_names if known_names is not None else set()
        for line in lines:
            names |= harvest_names(line.text)
        pad = self._padding
        boxes: list[Box] = []
        for i, line in enumerate(lines):
            if not line.text.strip():
                continue
            # Every OCR line with its pixel box, then any classifier matches
            # (indented, added by the classifier), then the verdict.
            trace.add(
                "line @(%d,%d %dx%d conf=%s): %r",
                line.left,
                line.top,
                line.width,
                line.height,
                line.conf,
                line.text,
            )
            # Named and quoted under the line, the way the classifier reports its
            # matches: the verdict says an arm fired, this says which pattern and
            # on what — the two halves of diagnosing a box nobody expected. Only
            # computed when a static rule can still decide, so the trace never
            # names a rule that lost to `labeled-value`.
            static = None if i in labeled_idx else static_rule_match(line.text)
            if static is not None:
                trace.add("    rule %s matched %r", *static)
            reason = (
                "labeled-value"
                if i in labeled_idx
                else "static-rule"
                if static is not None
                else "name-memory"
                if mentions_name(line.text, names)
                else "classifier"
                if i not in table_idx and self._classifier.is_pii(line.text, trace)
                else None
            )
            if reason is None and i in table_idx:
                # Why no classifier verdict was reported for this line.
                trace.add("    -> keep (item table)")
            if reason is not None:
                trace.add("    -> REDACT (%s)", reason)
                boxes.append(
                    Box(
                        line.left - pad,
                        line.top - pad,
                        line.left + line.width + pad,
                        line.top + line.height + pad,
                    )
                )
        if self._regions is not None:
            # Appended, never merged: these cover whole strips of the page,
            # including the pixels OCR returned nothing for (a letterhead logo),
            # and `apply_boxes` is happy to draw overlapping rectangles.
            for box in region_boxes(lines, image.width, image.height, self._regions):
                trace.add("region -> REDACT %s", box.as_list())
                boxes.append(box)
        if self._codes is not None:
            # The only source that reads pixels rather than `lines`: a QR code is a
            # graphic, so OCR never reports it, yet it is PII in the clear.
            for box in code_boxes(image, self._codes):
                trace.add("code -> REDACT %s", box.as_list())
                boxes.append(box)
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
