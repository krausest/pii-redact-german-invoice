"""Whole-region redaction: the letterhead band, the footer band, the sender
column and the recipient address block.

Every other box in this codebase is derived from one OCR line, which means it can
only ever cover text the OCR read. A letterhead is usually a *logo* — the strongest
company identifier on the page and one that never appears in ``OCRBackend.lines``.
So the two bands here are deliberately content-*blind* horizontally: they fill the
strip edge-to-edge across the full page width, covering the graphics beside and
above the text along with the text itself. Their *height*, though, is the height of
the sender block they found — ``header_frac`` says how far down to *look*, not how
tall the strip is, so a two-line letterhead gets a two-line strip instead of a fixed
slab of whitespace.

Blind, but not unconditional. A band is emitted only when the text inside it names
a sender — a company, a URL, a titled name, an address. That gate is what keeps the
pass safe on continuation pages, where the item table can start at the very top of
the sheet and the totals can sit in the bottom tenth: no sender there, no strip, so
nothing is destroyed. The trade is stated plainly: a header that is *only* a logo,
with no text at all, is skipped, because there is nothing to recognise it by.

The sender column is the same problem seen from the other side — it has no fixed
extent, so it is not given one. Every line that looks like a sender (company, URL,
phone, "Behandlung durch", a titled name, an address) seeds a block, which then
absorbs any line adjoining one already in it until nothing more does; two lines
adjoin when they are both near-touching and left-aligned. Recognition and extent are
thus separate: an anchor says *this is the sender*, the layout says *this is how far
it goes*.

The bands measure their height the same way, through the same block growth: a
window's anchors seed a block and the band spans it. The window alone cannot say how
tall the strip is, because the bottom tenth of a full page holds the last rows of
the item table as often as it holds an imprint.

All boxes are in the pixel space of the page the ``lines`` were read from — the
same coordinate rule the rest of the pipeline follows.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from backend.models import Box, Line
from backend.rules import (
    DE_PLZ_CITY,
    DE_STREET,
    ORG_MEDICAL,
    TITLE_NAME,
    line_matches_static_rule,
)

# A band's height comes from its content, so the fraction it is looked for in is a
# search window rather than the height itself: a header of two lines gets a
# two-line strip, not a fixed 12% of the page (that would only blacken whitespace).
# This is the ceiling on that, as a multiple of the window. It exists because
# nothing else bounds the height: block growth deliberately follows the sender past
# the window, and OCR does occasionally return a single tall box for a merged block
# on a skewed photo. If such a box holds a sender anchor it seeds the band on its
# own, and the band would swallow most of the page. Better to cut that box than to
# lose the invoice.
_BAND_STRETCH = 1.5


@dataclass(frozen=True)
class RegionParams:
    """Geometry for :func:`region_boxes`, as fractions of the page.

    A zero fraction disables that region, which is why there is no separate
    per-region boolean: ``header_frac = 0.0`` means "no header band".

    ``header_frac`` / ``footer_frac`` bound where a band's *anchors* are looked for;
    the band is then as tall as the sender block those anchors belong to (up to
    ``_BAND_STRETCH`` times the window). Widening one finds more letterhead, it does
    not blacken more paper.

    ``vgap_factor`` / ``align_factor`` therefore shape the bands as well as the
    sender column: they are what joins a line to a block.

    ``recipient_y_min_frac`` / ``recipient_y_max_frac`` bound where the recipient
    address block may be *seeded* (left of ``column_x_frac``, mirroring the
    sender column); an empty window — the default — disables that pass.
    """

    header_frac: float
    footer_frac: float
    column_x_frac: float
    column_y_frac: float
    vgap_factor: float
    align_factor: float
    recipient_y_min_frac: float = 0.0
    recipient_y_max_frac: float = 0.0
    padding: int = 0


def is_sender_anchor(text: str) -> bool:
    """True if a line is evidence that the block it sits in belongs to the sender.

    Wider than :func:`~backend.rules.line_matches_static_rule` on purpose: inside
    the sender column the loose organisation nouns (``Praxis``, ``Zentrum``,
    ``Labor``) are reliable, while page-wide they would fire on Leistungstexte.
    """
    return bool(
        line_matches_static_rule(text) or ORG_MEDICAL.search(text) or TITLE_NAME.search(text)
    )


def region_boxes(lines: list[Line], width: int, height: int, p: RegionParams) -> list[Box]:
    """The header/footer/sender-column/recipient-block boxes for a page of
    ``width`` x ``height``."""
    text_lines = [ln for ln in lines if ln.text.strip()]
    boxes: list[Box] = []
    for box in (
        _header_band(text_lines, width, height, p),
        _footer_band(text_lines, width, height, p),
        *_sender_column(text_lines, width, height, p),
        *_recipient_column(text_lines, width, height, p),
    ):
        if box is not None:
            boxes.append(box)
    return boxes


# -- bands ------------------------------------------------------------------- #
def _band_block(
    lines: list[Line], p: RegionParams, in_window: Callable[[Line], bool]
) -> list[Line]:
    """The sender block(s) anchored inside a band's search window — the lines the
    band has to cover, empty when there is no sender to cover.

    Both halves of that matter. A band exists to remove sender identity, so it
    demands evidence of one: mere presence of text is not enough, since a
    continuation page's table can start at the very top of the sheet and its totals
    can sit in the bottom tenth, and there a blanket strip destroys the invoice
    while removing nothing. And once a sender *is* found, the band must reach as far
    as that block does — not as far as everything that happens to dip into the
    window. The footer window of a full page holds the last rows of the item table
    as often as not, and taking the topmost of *those* pulled the strip up over the
    table (a 1407px page: imprint at y=1383, strip from y=1254).

    So the block is seeded by anchors only and extended by :func:`_adjacent`, the
    same relation the sender column grows with — near-touching *and* left-aligned.
    That is what tells the two apart on a real page: the imprint line runs from
    x=129, the table cells above it start at x=219/288/744/870.

    The trade is that a band line which is neither an anchor nor left-aligned with
    one is no longer covered — a centred "Vielen Dank für Ihren Besuch" above the
    imprint stays readable. It is not PII, and the alternative is blackening the
    item table.
    """
    block: set[int] = set()
    for seed, ln in enumerate(lines):
        # `block` is shared across seeds, so a letterhead holding half a dozen
        # anchors is still grown once — same reason as `taken` in _sender_column.
        if seed not in block and in_window(ln) and is_sender_anchor(ln.text):
            block |= _grow(seed, lines, p)
    return [lines[i] for i in block]


def _header_band(lines: list[Line], width: int, height: int, p: RegionParams) -> Box | None:
    edge = p.header_frac * height
    if edge <= 0:
        return None
    # A line is in the window on *overlap*, not centre: on a real letterhead the
    # last line — a long organisation name spanning most of the page — starts
    # just inside the window and ends outside it. On centre it would not seed the
    # band, and the letterhead would come out sliced in half and still readable.
    covered = _band_block(lines, p, lambda ln: ln.top < edge)
    if not covered:
        return None
    # Growth is not confined to the window (nor is it in _sender_column): a
    # letterhead running past the fraction is followed, and _BAND_STRETCH is the
    # only bound on how far the strip may then reach.
    bottom = min(max(ln.top + ln.height for ln in covered), edge * _BAND_STRETCH)
    return Box(0, 0, width, round(bottom) + p.padding)


def _footer_band(lines: list[Line], width: int, height: int, p: RegionParams) -> Box | None:
    if p.footer_frac <= 0:
        return None
    edge = height - p.footer_frac * height
    covered = _band_block(lines, p, lambda ln: ln.top + ln.height > edge)
    if not covered:
        return None
    top = max(min(ln.top for ln in covered), height - (height - edge) * _BAND_STRETCH)
    return Box(0, round(top) - p.padding, width, height)

# -- recipient block --------------------------------------------------------- #
def is_recipient_anchor(text: str) -> bool:
    """True if a line is evidence of a postal address field: a street line or a
    ZIP+city line.

    Deliberately *not* the salutation or a patient label, although both sit in
    the address field too: they also occur inside left-aligned, tightly spaced
    letter body text ("Sehr geehrter Herr ..." over a paragraph), where growth
    would absorb the whole paragraph. Every deliverable address field contains
    its street and city lines, and the block grown from those reaches the
    salutation, the name and a c/o line above them — the lines nothing per-line
    catches when OCR garbles them or NER drops the single token.
    """
    return bool(DE_STREET.search(text) or DE_PLZ_CITY.search(text))


def _recipient_column(lines: list[Line], width: int, height: int, p: RegionParams) -> list[Box]:
    """Bounding boxes of the recipient address block(s) in the left column —
    the sender column's machinery pointed at the other window.

    The per-line rules already blacken the lines they recognize; this pass
    exists for the lines *between* them — a c/o line, a company recipient, a
    name line OCR mangled — which sit inside the block but match nothing on
    their own. Same seed-then-:func:`_grow` shape as :func:`_sender_column`:
    the window (below ``recipient_y_min_frac``, above ``recipient_y_max_frac``,
    left of ``column_x_frac``) bounds only where a block may be seeded, and the
    block reaches as far as :func:`_adjacent` carries it.
    """
    if p.recipient_y_max_frac <= p.recipient_y_min_frac:
        return []
    x_max = p.column_x_frac * width
    y_min = p.recipient_y_min_frac * height
    y_max = p.recipient_y_max_frac * height

    boxes: list[Box] = []
    taken: set[int] = set()
    for seed, line in enumerate(lines):
        if seed in taken or line.left >= x_max or not (y_min <= line.top <= y_max):
            continue
        if not is_recipient_anchor(line.text):
            continue
        # `taken` shared across seeds: street and ZIP+city are one block.
        block = _grow(seed, lines, p)
        taken |= block
        member = [lines[i] for i in block]
        boxes.append(
            Box(
                min(ln.left for ln in member) - p.padding,
                min(ln.top for ln in member) - p.padding,
                max(ln.left + ln.width for ln in member) + p.padding,
                max(ln.top + ln.height for ln in member) + p.padding,
            )
        )
    return boxes


# -- sender column ----------------------------------------------------------- #
def _sender_column(lines: list[Line], width: int, height: int, p: RegionParams) -> list[Box]:
    """Bounding boxes of the sender blocks in the right-hand column.

    Deliberately naive: start from every line that *looks* like a sender, and keep
    absorbing lines that adjoin one already in the block until nothing more does.
    Each block is the connected component of :func:`_adjacent` around its seed, so
    recognition and extent stay separate — an anchor says *this is the sender*, the
    layout says *how far it goes* — and a component is the same set whichever of its
    members you start from.

    That order-independence is the point. Anything that walks the page in
    top-to-bottom order has to reckon with the two columns of a letter
    interleaving, where a recipient-address line sorting between two sender lines
    splits the block and leaves a hole in the middle of it. A component search never
    sees an order, so ``column_x_frac`` and ``column_y_frac`` bound only where a
    block may be *seeded*.
    """
    if p.column_x_frac >= 1.0 or p.column_y_frac <= 0.0:
        return []
    x_min = p.column_x_frac * width
    y_max = p.column_y_frac * height

    boxes: list[Box] = []
    taken: set[int] = set()
    for seed, line in enumerate(lines):
        if seed in taken or line.left < x_min or line.top > y_max:
            continue
        if not is_sender_anchor(line.text):
            continue
        # `taken` is shared across seeds, so a block is built once no matter how
        # many of its lines are anchors — the sample letterheads hold half a dozen.
        block = _grow(seed, lines, p)
        taken |= block
        member = [lines[i] for i in block]
        boxes.append(
            Box(
                min(ln.left for ln in member) - p.padding,
                min(ln.top for ln in member) - p.padding,
                max(ln.left + ln.width for ln in member) + p.padding,
                max(ln.top + ln.height for ln in member) + p.padding,
            )
        )
    return boxes


def _grow(seed: int, lines: list[Line], p: RegionParams) -> set[int]:
    """The connected component of :func:`_adjacent` containing ``seed``.

    A frontier queue, so each member is expanded exactly once. The obvious
    alternative — rescan every line against every member until a pass adds
    nothing — computes the same set (both are the transitive closure of a
    symmetric relation) but does it in ~3x the comparisons.
    """
    block, frontier = {seed}, [seed]
    while frontier:
        j = frontier.pop()
        for i, ln in enumerate(lines):
            if i not in block and _adjacent(lines[j], ln, p):
                block.add(i)
                frontier.append(i)
    return block


def _adjacent(a: Line, b: Line, p: RegionParams) -> bool:
    """Whether two lines belong to the same block: near-touching *and* sharing a
    left edge, both in units of the smaller line's height so they scale with the
    raster. Symmetric, which is what lets the block grow in both directions.

    Neither test is sufficient alone. On one sample page the payment table touches
    the sender block (gap 0.00) and only misalignment cuts it; on another the table
    is exactly aligned (dx 0.00) and only the gap does. Inside a real block the
    measured worst case is gap 0.41 and dx 0.14, against thresholds of 0.5 and 0.4.

    A right-edge arm was measured and dropped: across the sample scans it changed
    no box on any page, every real letterhead being left-aligned, while numeric
    table columns *are* right-aligned to a pixel and would have been linked by it.

    The gap is signed on purpose. OCR line boxes routinely overlap, and an
    overlap — a negative gap — is the strongest evidence of one block there is; an
    absolute value would turn it into a large positive number and split the block.

    Alignment is tested first because it rejects nearly every pair on a real page
    and costs one subtraction, where the gap needs a ``max`` and a ``min``.
    """
    h = min(a.height, b.height)
    if abs(a.left - b.left) > p.align_factor * h:
        return False
    return max(a.top, b.top) - min(a.top + a.height, b.top + b.height) <= p.vgap_factor * h
