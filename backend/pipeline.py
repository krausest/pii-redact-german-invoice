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

from PIL import Image, ImageDraw

from backend.classifiers.base import Classifier
from backend.document import build_document, spans_to_lines
from backend.models import Box, Line
from backend.ocr.base import OCRBackend
from backend.layout import (
    LayoutRegion,
    PaddleLayoutDetector,
    assign_lines,
    document_order,
    draw_layout_debug,
    region_boxes,
)
from backend.harvest import harvest, name_spans
from backend.pii import Span
from backend.rules import item_table_indices, rule_spans
from backend.trace import Trace, trace_page
from backend.unwarp import DocUnwarper


class RedactionPipeline:
    def __init__(
        self,
        ocr: OCRBackend,
        classifier: Classifier | None = None,
        unwarper: DocUnwarper | None = None,
        fill: tuple[int, int, int] = (0, 0, 0),
        padding: int = 2,
        unwarp_enabled: bool = True,
        unwarper_factory: Callable[[], DocUnwarper] | None = None,
        layout: PaddleLayoutDetector | None = None,
        classifier_factories: dict[str, Callable[[], Classifier]] | None = None,
        default_classifier: str | None = None,
    ) -> None:
        self._ocr = ocr
        # Two ways in, one store. `classifier=` is the direct one — a test hands
        # a stub, `backend.replay` hands the one classifier it wants — and it
        # registers under the name a request would ask for, or under "" when
        # nobody named it. `classifier_factories=` is what `build_pipeline`
        # passes: a classifier per selectable name, none of them built yet,
        # because a worker that is never asked for guard-omni must not pay a
        # gigabyte of weights for the option.
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
        # Whole-region redaction is off unless a detector is supplied — `None` is
        # both "no model" and "don't run it", so there is no second flag to
        # keep in sync. `build_pipeline` decides from `redaction.redact_regions`.
        self._layout = layout
        # Guards both lazy model builds below. The unwarper's was an unguarded
        # race, safe only because `api.max_concurrent_per_worker` defaults to 1;
        # a second one of the same kind would be repeating a known bug rather
        # than deciding anything, and building a model twice wastes a gigabyte.
        self._build_lock = threading.Lock()

    # -- primitives -------------------------------------------------------- #
    def unwarp(self, image: Image.Image) -> Image.Image:
        """Flatten a photographed page. The unwarper is built on first use (it
        loads a model), so a process that is only ever asked for ``unwarp=false``
        never pays for it."""
        if self._unwarper is None:
            with self._build_lock:
                if self._unwarper is None:
                    if self._unwarper_factory is None:
                        raise RuntimeError("unwarp requested but no DocUnwarper is configured")
                    self._unwarper = self._unwarper_factory()
        return self._unwarper.unwarp(image.convert("RGB"))

    def classifier(self, name: str | None = None) -> Classifier:
        """The classifier called ``name`` (``None`` = this process's default),
        built on first use like the unwarper and kept for the process.

        The cost of the lazy build is a cold start on the first request that
        names a model nobody has asked for yet; what it buys is that selecting a
        classifier per request does not mean every worker holding every model.

        A pipeline built with a single ``classifier=`` and no factories ignores
        the name: there is nothing to choose between, so the name is not a
        choice. That is what `backend.replay` and every test double are — they
        hand over one classifier on purpose, and refusing the request's default
        name would only mean repeating it at each of those call sites.
        """
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
        """The OCR lines of ``image`` — the text ``compute_boxes`` classifies.
        Exposed for :mod:`backend.replay`, which freezes that text to a file and
        replays every later stage against it; pass lines back via ``lines=`` to
        skip a second OCR pass."""
        return self._ocr.lines(image)

    def regions(self, image: Image.Image) -> list[LayoutRegion]:
        """The layout regions of ``image`` — empty when no detector is
        configured. The sibling of :meth:`read_lines`, and there for the same
        reason: it is the other model pass over a page, and a caller that needs
        the regions for itself (``--debug-layout``) should not make the detector
        run twice. Pass them back via ``regions=``."""
        return self._layout.regions(image) if self._layout is not None else []

    def compute_boxes(
        self,
        image: Image.Image,
        lines: list[Line] | None = None,
        known_names: set[str] | None = None,
        trace: Trace | None = None,
        regions: list[LayoutRegion] | None = None,
        classifier: str | None = None,
    ) -> list[Box]:
        """Boxes to redact, in the pixel space of ``image`` (no unwarp): one per
        flagged OCR line, plus — when configured — the whole-region boxes the
        layout detector earns (see :mod:`backend.layout`), which are the only
        ones not tied to a line.

        ``lines`` skips the OCR call when the caller already holds
        :meth:`read_lines` output for this exact ``image``, and ``regions`` does
        the same for :meth:`regions`. Both are the *same* image's — a page read
        at one size and boxed at another is the one way to get a silently
        misplaced rectangle.

        ``known_names`` is the name-memory accumulator: names harvested from this
        page are added *to the passed set*, so a caller looping over a document's
        pages shares one set and a surname labeled on page 1 is redacted bare on
        page 2. The carry is forward-only — a name first seen on page 2 does not
        re-redact page 1 — which suffices because the labeled occurrence leads.
        ``None`` keeps the memory page-local.

        ``classifier`` names which model half to run (``None`` = the process
        default). It is the one option here that selects a *model*, which is why
        it arrives as a name rather than an object: the pipeline owns the
        registry and builds on first use, so a caller can offer the choice
        without every worker holding every model.

        ``trace`` collects the per-line commentary — why each box exists — for a
        caller that was asked for it (``?debug=true``). Omitting it still logs
        everything at DEBUG; a :class:`Trace` that collects nothing is the same
        code path, not a second one."""
        if lines is None:
            lines = self._ocr.lines(image)
        trace = trace or Trace()
        if regions is None:
            regions = self.regions(image)

        # Reading order first: everything below reads the page as one text, and
        # that text is only worth classifying if the layout put each block's
        # lines next to their own neighbours rather than the next column's.
        spoken = [i for i, ln in enumerate(lines) if ln.text.strip()]
        order = [i for i in document_order(lines, regions) if lines[i].text.strip()]
        ordered = [lines[i] for i in order]
        text, bounds = build_document(ordered)

        # The item table: the deterministic rules and the name memory run there
        # as everywhere else, the classifier does not (see item_table_indices).
        # Computed on the *unordered* lines because it is a purely geometric
        # pass over pixel boxes — reading order neither helps nor hinders it.
        table_idx = item_table_indices(lines)

        # --- pass one: what each line carries on its own ------------------- #
        # Two span sources, kept apart on purpose: inside the item table the
        # classifier's findings are dropped and the rules' are not. A fee table
        # is a grid of two-word service texts, which in German look exactly like
        # a forename/surname pair to a model — while a rule that fires there
        # (a labeled patient name) is still evidence.
        rules_by_line = spans_to_lines(
            rule_spans(ordered, bounds, self._table_in_order(order, table_idx)), bounds
        )
        model = self.classifier(classifier)
        model_by_line = spans_to_lines(model.spans(text, trace), bounds)
        hits: list[list[Span]] = [
            list(rules_by_line[pos]) + ([] if i in table_idx else model_by_line[pos])
            for pos, i in enumerate(order)
        ]

        # --- pass two: the names pass one named, everywhere else ----------- #
        # Harvested from the *surviving* hits, so a model's find inside the item
        # table cannot spread across the document the way it was just stopped
        # from spreading down its own line. `names` is the caller's accumulator
        # when there is one, mutated in place: a name labeled on page 1 is caught
        # bare on page 2.
        names = known_names if known_names is not None else set()
        names |= harvest(ordered, hits)
        memory_by_line = spans_to_lines(name_spans(ordered, bounds, names), bounds)
        for pos in range(len(order)):
            hits[pos] += memory_by_line[pos]

        pad = self._padding
        boxes: list[Box] = []
        redacted: set[int] = set()
        found: dict[int, list[Span]] = {}
        for pos, i in enumerate(order):
            if hits[pos]:
                found[i] = hits[pos]
                redacted.add(i)
                line = lines[i]
                boxes.append(
                    Box(
                        line.left - pad,
                        line.top - pad,
                        line.left + line.width + pad,
                        line.top + line.height + pad,
                    )
                )

        # No gate on the detector: without one there are no regions, and
        # `region_boxes` over none is empty.
        region_hits = region_boxes(lines, regions, redacted, pad)
        trace_page(trace, lines, regions, order, spoken, found, table_idx, region_hits)
        boxes.extend(box for _, box, _ in region_hits)
        return boxes

    @staticmethod
    def _table_in_order(order: list[int], table_idx: set[int]) -> set[int]:
        """``table_idx`` re-expressed as positions in ``order``, which is what
        the rules see: they are handed the reordered lines, so an index into the
        original list would point at the wrong row."""
        return {pos for pos, i in enumerate(order) if i in table_idx}

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

    # -- debug-only extra --------------------------------------------------- #
    def layout_debug_image(
        self,
        image: Image.Image,
        lines: list[Line] | None = None,
        regions: list[LayoutRegion] | None = None,
    ) -> Image.Image | None:
        """``None`` when no layout detector is configured; otherwise ``image``
        copied with the detected regions outlined and labeled and each line
        group's bounding box drawn — what ``--debug-layout`` writes to
        ``<stem>_layout.jpg``. It shows what the detector *saw*, not which
        regions the majority rule then blackened; only ``backend/cli.py``
        calls it."""
        if self._layout is None:
            return None
        if lines is None:
            lines = self._ocr.lines(image)
        if regions is None:
            regions = self.regions(image)
        text_lines = [ln for ln in lines if ln.text.strip()]
        return draw_layout_debug(image, text_lines, assign_lines(text_lines, regions), regions)
