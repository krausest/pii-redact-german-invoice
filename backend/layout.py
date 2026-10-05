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
(``text``/``table``/``header``/``footer``/``image``/``seal``/…). A region whose
*type* is page furniture or a graphic (:data:`_ALWAYS_BLACKEN`) is blackened
whole, whatever it holds; every other region only shapes the reading order.
``image`` is what pays for the QR/DataMatrix pass being switched off on this
branch: a Girocode is a graphic, and the detector sees graphics.

Only :class:`PaddleLayoutDetector` touches the model; everything else here is
pure functions over ``LayoutRegion``/``Line``, so the fast suite never loads
paddle.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

from backend.models import Box, Line

# Blackened on sight, whatever they contain. A logo and a practice stamp are
# pixels holding no OCR line, so for those the type is the only evidence there
# is. `footer` is here because the imprint carries the sender's identity;
# `footnote` is the detector's other name for that same fine print at the foot
# of a page. `header` is not: the detector draws it around the whole letterhead,
# specialty and practice name included.
_ALWAYS_BLACKEN = frozenset({"image", "seal", "footer", "footnote", "aside_text"})

# Graphics are blackened without holes: a stamp prints the doctor's name and
# address around the specialty, and punching out a kept line would show them.
_NO_HOLES = frozenset({"image", "seal"})


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
        # (backend/ocr/paddle.py) and handed down from the same `engine.ocr_backend`:
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
    line no region contains. Only the debug view needs the partition."""
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


def subtract(box: Box, holes: list[Box]) -> list[Box]:
    """``box`` minus ``holes``, as rectangles: cut into horizontal bands at the
    holes' edges, and each band into the x-intervals no hole covers."""
    holes = [h for h in holes if h.x0 < box.x1 and h.x1 > box.x0 and h.y0 < box.y1 and h.y1 > box.y0]
    if not holes:
        return [box]
    ys = sorted({box.y0, box.y1} | {min(max(y, box.y0), box.y1) for h in holes for y in (h.y0, h.y1)})
    out: list[Box] = []
    for y0, y1 in zip(ys, ys[1:]):
        if y0 >= y1:
            continue
        x = box.x0
        for h in sorted((h for h in holes if h.y0 <= y0 and h.y1 >= y1), key=lambda h: h.x0):
            if h.x0 > x:
                out.append(Box(x, y0, h.x0, y1))
            x = max(x, h.x1)
        if x < box.x1:
            out.append(Box(x, y0, box.x1, y1))
    return out


def region_boxes(
    lines: list[Line],
    regions: list[LayoutRegion],
    redacted: set[int],
    padding: int,
    keep: set[int] | None = None,
) -> list[tuple[int, Box, str]]:
    """Whole-region boxes as ``(region index, box, why)``, one region of
    :data:`_ALWAYS_BLACKEN` type at a time.

    ``keep`` holds the lines that must stay readable; they are cut out of every
    region box but a graphic's, so one region may yield several boxes.

    The index is what lets the trace say which *region* a box came from: the box
    itself is padded outwards and so no longer equals the region's own box.

    ``redacted`` holds the indices into ``lines`` that the per-line pass
    flagged: a kept line that was redacted all the same is not cut out.
    """
    holes = [
        Box(ln.left, ln.top, ln.left + ln.width, ln.top + ln.height)
        for i, ln in enumerate(lines)
        if i in (keep or set()) and i not in redacted
    ]
    out: list[tuple[int, Box, str]] = []

    for r, region in enumerate(regions):
        if region.label not in _ALWAYS_BLACKEN:
            continue
        box = _padded(region.box, padding)
        parts = [box] if region.label in _NO_HOLES else subtract(box, holes)
        out.extend((r, part, region.label) for part in parts)
    return out


# A closing formula; the signature is the gap between it and the typed name.
SIGN_OFF = re.compile(
    r"(?i)\bmit\s+(?:freundlichen|besten|herzlichen|kollegialen)\s+Gr(?:ü|u|ue)(?:ß|ss)en"
    r"|\bHochachtungsvoll\b"
)
# How far below the closing formula a signature may reach, in its line heights,
# when no typed name closes the gap — or the gap is wider than that.
_SIGNATURE_HEIGHTS = 6


def signature_boxes(lines: list[Line], page_width: int, padding: int) -> list[tuple[int, Box]]:
    """``(closing line index, box)`` over the handwritten signature under each
    closing formula: the band down to the next line in its column, as wide as
    the wider of the two and at least half again the formula's width. OCR has
    no line for a signature and the layout model no class, so the gap the
    letter leaves for it is the only evidence."""
    out: list[tuple[int, Box]] = []
    for i, ln in enumerate(lines):
        if not SIGN_OFF.search(ln.text):
            continue
        bottom = ln.top + ln.height
        x0, x1 = ln.left, ln.left + ln.width
        below = [
            other
            for other in lines
            if other.top >= bottom and other.text.strip()
            and other.left < x1 and other.left + other.width > x0
        ]
        limit = bottom + _SIGNATURE_HEIGHTS * ln.height
        nxt = min(below, key=lambda other: other.top, default=None)
        if nxt is not None and nxt.top <= limit:
            y1 = nxt.top
            x0, x1 = min(x0, nxt.left), max(x1, nxt.left + nxt.width)
        else:
            y1 = limit
        if y1 - bottom < ln.height / 2:  # no room was left for a signature
            continue
        x1 = min(max(x1, ln.left + int(1.5 * ln.width)), page_width)
        out.append((i, Box(x0 - padding, bottom, x1 + padding, y1)))
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
