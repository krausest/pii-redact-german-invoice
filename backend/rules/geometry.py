"""Where OCR lines sit relative to each other."""

from __future__ import annotations

from statistics import median

from backend.models import Line

# How far a column may run on below a cell, in median line heights.
_COLUMN_GAP_FACTOR = 3.0


def column_gap(lines: list[Line]) -> float:
    """The largest vertical gap a column may have on this page."""
    return _COLUMN_GAP_FACTOR * median([ln.height for ln in lines] or [0])


def in_row(anchor: Line, ln: Line) -> bool:
    """Whether ``ln``'s middle lies within ``anchor``'s height, give or take
    half of ``ln``'s own. Looser than :func:`left_neighbour`'s row test, which
    compares the two middles."""
    return anchor.top - ln.height / 2 <= ln.mid_y <= anchor.bottom + ln.height / 2


def box_is_above(upper: Line, lower: Line, gap: float, tol: float) -> bool:
    """Whether ``lower`` sits directly beneath ``upper``: it starts below
    ``upper``'s middle (OCR boxes of adjacent rows overlap by a pixel), no more
    than ``gap`` under its bottom, and shares its left edge within ``tol``."""
    return (
        upper.mid_y <= lower.top
        and lower.top - upper.bottom <= gap
        and abs(upper.left - lower.left) <= tol
    )


def left_neighbour(lines: list[Line], cell: Line) -> Line | None:
    """The nearest cell left of ``cell`` whose middle is level with its own
    within half a line height."""
    row = [
        ln
        for ln in lines
        if ln is not cell and ln.text.strip()
        and ln.right <= cell.left + cell.height
        and abs(ln.mid_y - cell.mid_y) <= max(ln.height, cell.height) / 2
    ]
    return min(
        row,
        key=lambda ln: (cell.left - ln.right, abs(ln.mid_y - cell.mid_y)),
        default=None,
    )
