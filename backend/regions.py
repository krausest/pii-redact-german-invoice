"""Whole-region redaction: the letterhead band, the footer band and the sender
column.

Every other box in this codebase is derived from one OCR line, which means it can
only ever cover text the OCR read. A letterhead is usually a *logo* — the strongest
company identifier on the page and one that never appears in ``OCRBackend.lines``.
So the two bands here are deliberately content-*blind* horizontally: they fill the
strip edge-to-edge across the full page width, covering the graphics beside and
above the text along with the text itself. Their *height*, though, is the height of
the text they found — ``header_frac`` says how far down to look, not how tall the
strip is, so a two-line letterhead gets a two-line strip instead of a fixed slab of
whitespace.

Blind, but not unconditional. A band is emitted only when the text inside it names
a sender — a company, a URL, a titled name, an address. That gate is what keeps the
pass safe on continuation pages, where the item table can start at the very top of
the sheet and the totals can sit in the bottom tenth: no sender there, no strip, so
nothing is destroyed. The trade is stated plainly: a header that is *only* a logo,
with no text at all, is skipped, because there is nothing to recognise it by.

The sender column is the opposite problem — it has no fixed extent. It is found by
anchor: a line in the right part of the page that looks like a sender (company,
URL, phone, "Behandlung durch", a titled name, an address), then grown over the
lines vertically adjacent to it, stopping at the first *table row*. Both stops
matter: on the sample invoice the practice block ends 10 px above the payment table
while its own lines sit 13 px apart, so the vertical gap alone cannot separate
them — the structural difference (a stack of single cells vs. rows of two or three)
can, and it survives a skewed scan where a pixel threshold would not.

All boxes are in the pixel space of the page the ``lines`` were read from — the
same coordinate rule the rest of the pipeline follows.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.models import Box, Line
from backend.rules import ORG_MEDICAL, TITLE_NAME, line_matches_static_rule

# A band's height comes from its content, so the fraction it is looked for in is a
# search window rather than the height itself: a header of two lines gets a
# two-line strip, not a fixed 12% of the page (that would only blacken whitespace).
# This is the ceiling on that, as a multiple of the window. It exists because
# nothing else bounds the height — one line with `top < edge` sets it, and OCR does
# occasionally return a single tall box for a merged block on a skewed photo. If
# such a box also holds a sender anchor, the gate passes and the band would swallow
# most of the page. Better to cut that box than to lose the invoice.
_BAND_STRETCH = 1.5


@dataclass(frozen=True)
class RegionParams:
    """Geometry for :func:`region_boxes`, as fractions of the page.

    A zero fraction disables that region, which is why there is no separate
    per-region boolean: ``header_frac = 0.0`` means "no header band".

    ``header_frac`` / ``footer_frac`` bound where a band's lines are *looked for*;
    the band is then as tall as those lines (up to ``_BAND_STRETCH`` times the
    window). Widening one finds more letterhead, it does not blacken more paper.
    """

    header_frac: float
    footer_frac: float
    column_x_frac: float
    column_y_frac: float
    gap_factor: float
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
    """The header/footer/sender-column boxes for a page of ``width`` x ``height``."""
    text_lines = [ln for ln in lines if ln.text.strip()]
    boxes: list[Box] = []
    for box in (
        _header_band(text_lines, width, height, p),
        _footer_band(text_lines, width, height, p),
        *_sender_column(text_lines, width, height, p),
    ):
        if box is not None:
            boxes.append(box)
    return boxes


# -- bands ------------------------------------------------------------------- #
def _is_letterhead(covered: list[Line]) -> bool:
    """Whether a band's contents justify blackening the whole strip.

    A band exists to remove sender identity, so it demands evidence of one. Mere
    presence of text is not enough: a continuation page's table can start at the
    very top of the sheet and its totals can sit in the bottom tenth, and on such
    a page a blanket strip destroys the invoice while removing nothing.
    """
    return any(is_sender_anchor(ln.text) for ln in covered)


def _header_band(lines: list[Line], width: int, height: int, p: RegionParams) -> Box | None:
    edge = p.header_frac * height
    if edge <= 0:
        return None
    # Membership is *overlap*, not centre: on a real letterhead the last line
    # ("Rechenzentrum für Ärzte und Kliniken") starts just inside the window and
    # ends outside it. On centre it would fall out of the band, and since the band
    # reaches down to its lowest member the line would come out sliced in half and
    # still readable.
    covered = [ln for ln in lines if ln.top < edge]
    if not _is_letterhead(covered):
        return None
    bottom = min(max(ln.top + ln.height for ln in covered), edge * _BAND_STRETCH)
    return Box(0, 0, width, round(bottom) + p.padding)


def _footer_band(lines: list[Line], width: int, height: int, p: RegionParams) -> Box | None:
    if p.footer_frac <= 0:
        return None
    edge = height - p.footer_frac * height
    covered = [ln for ln in lines if ln.top + ln.height > edge]
    if not _is_letterhead(covered):
        return None
    top = max(min(ln.top for ln in covered), height - (height - edge) * _BAND_STRETCH)
    return Box(0, round(top) - p.padding, width, height)

def _sender_column_old(lines: list[Line], width: int, height: int, p: RegionParams) -> list[Box]:
    """Bounding boxes of the anchored text blocks in the upper-right of the page.

    Candidates are cut off at the first table row (see :func:`_first_table_row`),
    then split into vertical blocks at any gap wider than ``gap_factor`` line
    heights, and a block is kept only if one of its lines is a sender anchor. On a
    typical Arztrechnung this keeps the "Behandlung durch: … www.praxis.de" block
    and drops both the page-number line above it and the "Bitte bei Zahlung
    angeben" table below.
    """
    if p.column_x_frac >= 1.0 or p.column_y_frac <= 0.0:
        return []
    x_min = p.column_x_frac * width
    y_max = p.column_y_frac * height
    candidates = sorted(
        (ln for ln in lines if ln.left >= x_min and ln.top <= y_max),
        key=lambda ln: ln.top,
    )
    print(f"{candidates=}")
    r = _first_table_row(candidates)
    candidates = candidates[: r]
    print(f"candidates2 {candidates}")

    boxes: list[Box] = []
    print(f"{ _vertical_blocks(candidates, p.gap_factor)=}")
    for block in _vertical_blocks(candidates, p.gap_factor):
        if not any(is_sender_anchor(ln.text) for ln in block):
            continue
        boxes.append(
            Box(
                min(ln.left for ln in block) - p.padding,
                min(ln.top for ln in block) - p.padding,
                max(ln.left + ln.width for ln in block) + p.padding,
                max(ln.top + ln.height for ln in block) + p.padding,
            )
        )
    return boxes


def _first_table_row(candidates: list[Line]) -> int:
    """Index of the first line that is part of a multi-column row, or ``len`` if
    there is none. Everything from there down is a table, not a letterhead.

    This exists because the gap heuristic alone is not enough. On the sample
    invoice the practice block ends 10 px above the "Bitte bei Zahlung angeben"
    table while its own lines sit 13 px apart, so no ``gap_factor`` separates
    them — but the table's rows have two or three cells side by side and the
    letterhead is a single stack, and *that* difference survives a skewed scan.

    Two lines are side by side when they overlap vertically and not horizontally.
    Vertical overlap alone would fire on ordinary stacked lines, whose OCR boxes
    routinely overlap by a pixel or two.
    """
    for i, line in enumerate(candidates):
        if any(
            other is not line
            and other.top < line.top + line.height
            and line.top < other.top + other.height
            and (
                other.left >= line.left + line.width or line.left >= other.left + other.width
            )
            for other in candidates
        ):
            return i
    return len(candidates)


def _vertical_blocks(lines: list[Line], gap_factor: float) -> list[list[Line]]:
    """Split top-sorted lines wherever the vertical gap exceeds ``gap_factor``
    times the preceding line's height. Lines that overlap vertically (two columns
    on one row) yield a negative gap and always stay together."""
    blocks: list[list[Line]] = []
    bottom = 0.0
    for line in lines:
        if blocks and line.top - bottom <= gap_factor * blocks[-1][-1].height:
            blocks[-1].append(line)
        else:
            blocks.append([line])
        bottom = max(bottom, line.top + line.height)
    return blocks
