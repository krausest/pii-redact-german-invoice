"""Where OCR lines sit relative to each other."""

from __future__ import annotations

from statistics import median

from backend.models import Line

# How far a column may run on below a cell, in median line heights.
_COLUMN_GAP_FACTOR = 3.0


def column_gap(lines: list[Line]) -> float:
    """The largest vertical gap a column may have on this page."""
    return _COLUMN_GAP_FACTOR * median([ln.height for ln in lines] or [0])


def same_row(a: Line, b: Line) -> bool:
    """Whether two lines sit in one row: their middles closer than half their
    heights together. A photographed row is a few pixels off level."""
    return abs(a.mid_y - b.mid_y) <= (a.height + b.height) / 2


def left_aligned(a: Line, b: Line, tol: float) -> bool:
    """Whether two lines share their left edge within ``tol``."""
    return abs(a.left - b.left) <= tol


def below(upper: Line, lower: Line, gap: float) -> bool:
    """Whether ``lower`` sits under ``upper``: its middle below ``upper``'s
    bottom (adjacent OCR boxes overlap by a pixel), at most ``gap`` further down."""
    return upper.bottom <= lower.mid_y and lower.top - upper.bottom <= gap


def left_neighbour(lines: list[Line], cell: Line) -> Line | None:
    """The nearest cell left of ``cell`` in its row."""
    row = [
        ln
        for ln in lines
        if ln is not cell and ln.text.strip()
        and ln.right <= cell.left + cell.height
        and same_row(ln, cell)
    ]
    return min(
        row,
        key=lambda ln: (cell.left - ln.right, abs(ln.mid_y - cell.mid_y)),
        default=None,
    )
