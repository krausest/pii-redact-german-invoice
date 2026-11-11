"""The fee table (``backend.rules.item_table``)."""

from __future__ import annotations

from backend.rules.item_table import item_table_lines
from tests.conftest import cell


def test_rows_whose_boxes_touch_are_still_separate_rows():
    # Each row's box ends where the next begins, as OCR boxes of tightly set
    # rows do; chained by overlap they would count as one row and no table.
    lines = []
    for n in range(3):
        top = 100 + 15 * n
        lines += [cell("Beratung", 10, top, height=16), cell("4,66", 300, top, height=16)]
    assert item_table_lines(lines) == set(lines)


def test_one_amount_is_no_table():
    lines = [cell("Zahlbetrag", 10, 100), cell("10,72", 300, 100)]
    assert item_table_lines(lines) == set()
