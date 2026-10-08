"""The fee table: rows of service texts and amounts that hold no PII.

Its two-word service texts look like names to a model, so the classifier's
findings are rejected there (see :func:`backend.rules.keep.rejected`); the
deterministic rules still apply.
"""

from __future__ import annotations

import re
from statistics import median
from typing import NamedTuple

from backend.models import Line

# German money: "4,66", "1.234,56".
MONEY = re.compile(r"\b\d{1,3}(?:\.\d{3})*,\d{2}\b")

# Money rows further apart than this, in median line heights, are separate tables.
_TABLE_GAP_FACTOR = 3.0
# One amount alone ("Zahlbetrag") is no table.
_MIN_TABLE_ROWS = 2


class _Band(NamedTuple):
    """A vertical stretch of the page, ``top`` to ``bottom``."""

    top: int
    bottom: int

    def contains(self, y: float) -> bool:
        return self.top <= y <= self.bottom


def item_table_lines(lines: list[Line]) -> set[Line]:
    """Lines whose middle falls in a band spanned by two or more money rows —
    so a wrapped service text between amounts is covered too."""
    rows = _money_rows(lines)
    if not rows:
        return set()
    bands = _table_bands(rows, _TABLE_GAP_FACTOR * median(ln.height for ln in lines))
    return {ln for ln in lines if any(band.contains(ln.mid_y) for band in bands)}


def _money_rows(lines: list[Line]) -> list[_Band]:
    """Each row holding an amount; overlapping lines form one row."""
    rows: list[_Band] = []
    for top, bottom in sorted((ln.top, ln.bottom) for ln in lines if MONEY.search(ln.text)):
        if rows and top <= rows[-1].bottom:
            rows[-1] = _Band(rows[-1].top, max(rows[-1].bottom, bottom))
        else:
            rows.append(_Band(top, bottom))
    return rows


def _table_bands(rows: list[_Band], gap: float) -> list[_Band]:
    """Rows clustered by vertical gap; each cluster of enough rows is a band."""
    clusters: list[list[_Band]] = [[rows[0]]]
    for row in rows[1:]:
        if row.top - clusters[-1][-1].bottom <= gap:
            clusters[-1].append(row)
        else:
            clusters.append([row])
    return [_Band(c[0].top, c[-1].bottom) for c in clusters if len(c) >= _MIN_TABLE_ROWS]
