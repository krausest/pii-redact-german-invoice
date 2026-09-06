"""Layout-model regions: what gets blackened wholesale, and the debug view of it.

Replaces ``backend/regions.py``' hand-measured anchor-growth geometry (header
band, footer band, sender column, recipient address block). That pass could
only reason about distances between OCR lines: it seeded on an anchor, grew a
block by left-edge alignment and a signed vertical gap, and every constant in
it was measured against the sample scans. It could never cover what OCR
returns no line for — a letterhead logo is a graphic — which is why its bands
were full-width strips gated on a sender anchor rather than boxes around
anything.

Here a trained layout detector (PP-DocLayout, via the already-installed
``paddleocr.LayoutDetection`` — no new dependency) finds typed regions
(``text``/``table``/``header``/``footer``/``image``/``seal``/…) and two rules
turn them into boxes:

* a region whose *type* is page furniture or a graphic
  (:data:`_ALWAYS_BLACKEN`) is blackened whole, whatever it holds. ``image`` is
  what pays for the QR/DataMatrix pass being switched off on this branch: a
  Girocode is a graphic, and the detector sees graphics.
* every other region is blackened whole once at least
  :data:`_MIN_REDACTED_RATIO` of its OCR lines were flagged by the per-line
  pass. This is the generic replacement for block growth: a recipient address
  block is a ``text`` region whose street and ZIP+city lines already match a
  static rule, so those hits carry the c/o line, the company name and the
  garbled name line with them — no gap factor anywhere, and it works the same
  for a sender column, which is what let ``regions.py`` go entirely.

Only :class:`PaddleLayoutDetector` touches the model; everything else here is
pure functions over ``LayoutRegion``/``Line``, so the fast suite never loads
paddle.
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

from backend.models import Box, Line

# Blackened on sight, whatever they contain. The ratio rule below could never
# reach `image` or `seal` anyway — a logo and a practice stamp are pixels, they
# hold no OCR line, and 0 of 0 clears no bar — so for those two the type is the
# only evidence there is. `header`/`footer` are here because page furniture
# carries the sender's identity wherever it appears on the sheet.
_ALWAYS_BLACKEN = frozenset({"image", "seal", "header", "footer","aside_text"})

# How much of a region has to be flagged before the whole of it goes. Below a
# half deliberately: a two-line sender block where only the line naming the
# company matched is the common shape, and at a strict majority it survived.
# Measured on the corpus at 0.4, 92 of 179 regions are blackened; the ones left
# under the bar are 1/3 and 1/8 cases, and reaching those means going near 0.15,
# where a fee table holding a couple of names goes black with them.
_MIN_REDACTED_RATIO = 0.4

_DEBUG_LINE_WIDTH = 4
_DEBUG_LABEL_PAD = 4
# The label size follows the page, because the page size varies by two orders of
# magnitude here (a 200 px test fixture, a 3000 px scan). The bounds keep it
# legible on the former and from swamping the latter.
_DEBUG_LABEL_HEIGHT_DIVISOR = 90
_DEBUG_LABEL_MIN = 14
_DEBUG_LABEL_MAX = 40

# Cycles via palette[n % len(palette)] — region count is unbounded.
_DEBUG_PALETTE: list[tuple[int, int, int]] = [
    (220, 30, 30),
    (30, 90, 220),
    (30, 160, 60),
    (230, 140, 20),
    (150, 60, 200),
    (20, 170, 170),
    (200, 200, 30),
    (140, 90, 40),
]

# Translucent red behind exactly the labels in `_ALWAYS_BLACKEN`, derived from
# that set rather than listed again, so the debug image cannot drift from what
# the pipeline actually does.
_ALWAYS_FILL = (220, 30, 30, 60)


@dataclass(frozen=True)
class LayoutRegion:
    """One detected layout region, in the pixel space of the analyzed image —
    the same coordinate rule every box in this codebase follows."""

    label: str  # "text" | "table" | "header" | "footer" | "image" | ...
    score: float
    box: Box


class PaddleLayoutDetector:
    """Adapts ``paddleocr.LayoutDetection`` (PP-DocLayout) to
    ``regions(image) -> list[LayoutRegion]``.

    Constructing this loads the model — build it once per process, like every
    other model in ``backend.factory.build_pipeline`` (eagerly, so
    ``docker/warmup.py`` bakes the weights the same way it bakes the OCR
    models). The paddle import is deferred to construction time so importing
    this module for its pure functions never pulls paddle in.
    """

    def __init__(
        self,
        model_name: str = "PP-DocLayout_plus-L",
        threshold: float = 0.35,
        layout_nms: bool = True,
        engine: str = "paddle",
        cpu_threads: int = 10,
    ):
        # Sets PADDLE_PDX_CACHE_HOME (the repo-local .paddle_cache) before
        # anything imports paddle — backend/unwarp.py replicates the same
        # env lines for the same reason; importing the module is enough here.
        import backend.ocr.paddle  # noqa: F401

        from paddleocr import LayoutDetection

        # threshold/layout_nms defaults mirror LayoutConfig — see the
        # measurement notes there (photos score far below scans).
        kwargs = dict(model_name=model_name, threshold=threshold, layout_nms=layout_nms)
        # `engine` is the same axis the OCR backend runs on, set the same way
        # (backend/ocr/paddle.py) and handed down from the same resolved preset:
        # it says which runtime executes paddle models on this machine, and a
        # page whose text models are on ONNX Runtime while its layout model is
        # not is a configuration that disagrees with itself. paddlex ships an
        # ONNX build of this checkpoint; it lands in `.paddle_cache` beside the
        # native one, so a process only ever downloads the flavour it runs.
        if engine == "onnxruntime":
            kwargs.update(engine="onnxruntime", cpu_threads=cpu_threads)
        self._model = LayoutDetection(**kwargs)

    def regions(self, image: Image.Image) -> list[LayoutRegion]:
        import numpy as np

        results = self._model.predict(np.array(image.convert("RGB")))
        if not results:
            return []
        out: list[LayoutRegion] = []
        for det in results[0]["boxes"]:
            x1, y1, x2, y2 = (int(v) for v in det["coordinate"])
            out.append(
                LayoutRegion(label=det["label"], score=float(det["score"]), box=Box(x1, y1, x2, y2))
            )
        return out


def _center_in(line: Line, box: Box) -> bool:
    cx = line.left + line.width / 2
    cy = line.top + line.height / 2
    return box.x0 <= cx < box.x1 and box.y0 <= cy < box.y1


def _area(box: Box) -> int:
    return max(box.x1 - box.x0, 0) * max(box.y1 - box.y0, 0)


def lines_by_region(lines: list[Line], regions: list[LayoutRegion]) -> list[list[int]]:
    """Line indices per region, positionally aligned with ``regions`` (a region
    that claimed nothing keeps an empty list).

    A line joins the region containing its center point; when several contain
    it, the smallest-area one wins — detected regions overlap (a table region
    inside a page-wide text region), and the tighter box is the more specific
    claim.
    """
    per_region: list[list[int]] = [[] for _ in regions]
    for i, line in enumerate(lines):
        best: int | None = None
        for r, region in enumerate(regions):
            if _center_in(line, region.box) and (
                best is None or _area(region.box) < _area(regions[best].box)
            ):
                best = r
        if best is not None:
            per_region[best].append(i)
    return per_region


def assign_lines(lines: list[Line], regions: list[LayoutRegion]) -> list[list[int]]:
    """Full partition of line indices into groups: one group per region that
    claimed at least one line (in ``regions`` order), then one singleton per
    line no region contains. Only the debug view needs the partition —
    :func:`region_boxes` works off :func:`lines_by_region` directly."""
    per_region = lines_by_region(lines, regions)
    claimed = {i for group in per_region for i in group}
    return [g for g in per_region if g] + [[i] for i in range(len(lines)) if i not in claimed]


# -- reading order --------------------------------------------------------- #

# How much of a region has to lie inside another before it counts as nested.
# A threshold rather than true containment because detector boxes overhang each
# other by a few pixels routinely — a table's box pokes out of the text block it
# sits in, and demanding strict containment would make it a sibling instead of a
# child, which is exactly the nesting that has to be recognized.
_CONTAINMENT = 0.8


def _contained_frac(inner: Box, outer: Box) -> float:
    """How much of ``inner``'s area lies inside ``outer``, 0.0 to 1.0."""
    w = max(min(inner.x1, outer.x1) - max(inner.x0, outer.x0), 0)
    h = max(min(inner.y1, outer.y1) - max(inner.y0, outer.y0), 0)
    area = _area(inner)
    return (w * h) / area if area else 0.0


def region_parents(regions: list[LayoutRegion]) -> list[int | None]:
    """For each region, the index of the region it nests in, or ``None`` for a
    top-level one — positionally aligned with ``regions``.

    "Nests in" is *mostly contained* (:data:`_CONTAINMENT`), and among several
    containers the smallest wins, so a table inside a text block inside the page
    attaches to the text block rather than skipping a level. Equal-area mutual
    containment (two detections of the same thing) is broken by index, which
    keeps the relation a forest and not a cycle.
    """
    parents: list[int | None] = [None] * len(regions)
    for i, inner in enumerate(regions):
        best: int | None = None
        for j, outer in enumerate(regions):
            if i == j or _contained_frac(inner.box, outer.box) < _CONTAINMENT:
                continue
            # A pair that contains each other equally would otherwise make both
            # the other's parent; the later index yields to the earlier one.
            if _area(outer.box) == _area(inner.box) and j > i:
                continue
            if best is None or _area(outer.box) < _area(regions[best].box):
                best = j
        parents[i] = best
    return parents


def _bands(items: list[tuple[int, int, int, int]]) -> list[list[tuple[int, int, int, int]]]:
    """Group ``(top, bottom, left, payload)`` items into reading bands.

    Items whose vertical extents overlap belong to the same band, and a band is
    read left to right. This is what makes a table row read *across*: on a
    photographed page the cells of one row differ by a few pixels in ``top``, and
    a plain ``(top, left)`` sort would order the whole page column by column
    instead — pulling "Datum" and "Betrag" apart and gluing each to the value
    below it.

    The band grows with its members: a tall item (a nested block) pulls in
    everything beside it, which is the intent — those things really are on the
    same visual row.
    """
    bands: list[list[tuple[int, int, int, int]]] = []
    for item in sorted(items, key=lambda it: (it[0], it[2])):
        if bands and item[0] < max(b[1] for b in bands[-1]):
            bands[-1].append(item)
        else:
            bands.append([item])
    return bands


def document_order(lines: list[Line], regions: list[LayoutRegion]) -> list[int]:
    """Line indices in reading order, derived from the region hierarchy.

    Within a region its own lines and its child regions are laid out together in
    bands (see :func:`_bands`) — a child enters as a *whole* at the position of
    its box, so a nested block stays contiguous instead of interleaving with the
    parent's own lines. The same rule then applies inside each child, and to the
    top-level regions, which is what makes the order recursive.

    Lines no region claimed are ordered among the top-level items by their own
    box, so nothing is dropped: the result is a permutation of
    ``range(len(lines))``.
    """
    per_region = lines_by_region(lines, regions)
    parents = region_parents(regions)
    children: list[list[int]] = [[] for _ in regions]
    roots: list[int] = []
    for r, parent in enumerate(parents):
        (children[parent] if parent is not None else roots).append(r)

    def walk(region_index: int) -> list[int]:
        """Line indices of one region's subtree, in reading order."""
        items: list[tuple[int, int, int, int]] = []
        for i in per_region[region_index]:
            items.append((lines[i].top, lines[i].top + lines[i].height, lines[i].left, i))
        for c in children[region_index]:
            box = regions[c].box
            # Encoded as ~child so a band item can say "this is a subtree, not a
            # line" without a second parallel list.
            items.append((box.y0, box.y1, box.x0, ~c))
        out: list[int] = []
        for band in _bands(items):
            for _, _, _, payload in sorted(band, key=lambda it: it[2]):
                out.extend(walk(~payload) if payload < 0 else [payload])
        return out

    claimed = {i for group in per_region for i in group}
    top: list[tuple[int, int, int, int]] = [
        (lines[i].top, lines[i].top + lines[i].height, lines[i].left, i)
        for i in range(len(lines))
        if i not in claimed
    ]
    for r in roots:
        box = regions[r].box
        top.append((box.y0, box.y1, box.x0, ~r))

    order: list[int] = []
    for band in _bands(top):
        for _, _, _, payload in sorted(band, key=lambda it: it[2]):
            order.extend(walk(~payload) if payload < 0 else [payload])
    return order


def _padded(box: Box, padding: int) -> Box:
    return Box(box.x0 - padding, box.y0 - padding, box.x1 + padding, box.y1 + padding)


def region_boxes(
    lines: list[Line],
    regions: list[LayoutRegion],
    redacted: set[int],
    padding: int,
) -> list[tuple[int, Box, str]]:
    """Whole-region boxes as ``(region index, box, why)``.

    The index is what lets the trace say which *region* a box came from: the box
    itself is padded outwards and so no longer equals the region's own box.

    ``redacted`` holds the indices into ``lines`` that the per-line pass
    flagged. Blank lines are not counted toward the ratio: OCR emits them,
    nothing can ever redact one, so counting them would only dilute a block
    below the threshold.

    Note that ``table`` takes part in the ratio rule like any other region.
    That is a deliberate trade, not an oversight: it keeps the rule without
    exceptions, and it costs the invoice body on a page where enough of the item
    rows carry a patient name — the case to watch on the corpus.
    """
    out: list[tuple[int, Box, str]] = []
    for r, (region, idx) in enumerate(zip(regions, lines_by_region(lines, regions))):
        if region.label in _ALWAYS_BLACKEN:
            out.append((r, _padded(region.box, padding), region.label))
            continue
        counted = [i for i in idx if lines[i].text.strip()]
        if not counted:
            continue
        hits = sum(1 for i in counted if i in redacted)
        if hits >= _MIN_REDACTED_RATIO * len(counted):
            out.append(
                (r, _padded(region.box, padding), f"{region.label} {hits}/{len(counted)} lines")
            )
    return out


def _tight_bbox(lines: list[Line], idx: list[int]) -> Box:
    left = min(lines[i].left for i in idx)
    top = min(lines[i].top for i in idx)
    right = max(lines[i].left + lines[i].width for i in idx)
    bottom = max(lines[i].top + lines[i].height for i in idx)
    return Box(left, top, right, bottom)


def _label_font(image_height: int) -> ImageFont.FreeTypeFont:
    """A label font scaled to the page.

    ``load_default(size=…)`` scales Pillow's own bundled face, so this needs no
    font file from the host and looks the same on every platform — unlike a
    hard-coded ``DejaVuSans.ttf`` path. The unsized default is a ~11 px bitmap,
    which on a 3000 px scan is a smudge.
    """
    size = min(
        _DEBUG_LABEL_MAX,
        max(_DEBUG_LABEL_MIN, round(image_height / _DEBUG_LABEL_HEIGHT_DIVISOR)),
    )
    return ImageFont.load_default(size=size)


def _draw_label(
    draw: ImageDraw.ImageDraw,
    box: Box,
    text: str,
    color: tuple[int, int, int],
    font: ImageFont.FreeTypeFont,
) -> None:
    """The region's label as a filled chip in the region's own color, sitting on
    the region's top-left corner.

    Filled rather than bare text because the label lies on top of the page:
    over an invoice's own print, unbacked glyphs in a mid-saturation color are
    unreadable whatever their size. Filled means opaque, though, so the chip
    goes *above* the box wherever the page has room — inside, it hides the
    region's own first line, i.e. exactly the text the box was drawn to
    explain."""
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    width = right - left + 2 * _DEBUG_LABEL_PAD
    height = bottom - top + 2 * _DEBUG_LABEL_PAD
    x = box.x0
    y = box.y0 - height if box.y0 >= height else box.y0 + _DEBUG_LINE_WIDTH
    draw.rectangle((x, y, x + width, y + height), fill=color)
    draw.text(
        (x + _DEBUG_LABEL_PAD - left, y + _DEBUG_LABEL_PAD - top),
        text,
        font=font,
        fill=(255, 255, 255),
    )


def draw_layout_debug(
    image: Image.Image,
    lines: list[Line],
    groups: list[list[int]],
    regions: list[LayoutRegion],
) -> Image.Image:
    """A copy of ``image`` with the detected regions outlined in a cyclically
    repeating color and labeled with a chip naming the detected class
    (``text``/``table``/``header``/…) at their top-left corner, plus each
    group's tight line bounding box in the same cycle (this also outlines the
    singleton groups no region claimed) — what ``--debug-layout`` writes to
    ``<stem>_layout.jpg``.

    Regions in :data:`_ALWAYS_BLACKEN` get a translucent red background: those
    are the ones blackened on sight. Everything else is left untinted because
    whether it gets blackened depends on the per-line verdicts, which this view
    does not compute."""
    out = image.convert("RGBA")
    overlay = Image.new("RGBA", out.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    for region in regions:
        if region.label in _ALWAYS_BLACKEN:
            overlay_draw.rectangle(region.box.as_list(), fill=_ALWAYS_FILL)
    out = Image.alpha_composite(out, overlay)
    draw = ImageDraw.Draw(out)
    font = _label_font(out.height)
    for n, region in enumerate(regions):
        color = _DEBUG_PALETTE[n % len(_DEBUG_PALETTE)]
        draw.rectangle(region.box.as_list(), outline=color, width=_DEBUG_LINE_WIDTH)
        _draw_label(draw, region.box, region.label, color, font)
    for n, idx in enumerate(groups):
        draw.rectangle(
            _tight_bbox(lines, idx).as_list(),
            outline=_DEBUG_PALETTE[n % len(_DEBUG_PALETTE)],
            width=1,
        )
    # Back to RGB: callers save this as JPEG, and the alpha channel was only
    # ever a compositing vehicle.
    return out.convert("RGB")
