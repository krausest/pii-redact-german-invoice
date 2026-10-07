"""A street without a street suffix, recognized by the postal block it heads."""

from __future__ import annotations

from backend.models import Line
from backend.rules.geometry import box_is_above
from backend.rules.patterns import CITY_CELL, STREET_CELL


def street_above_city_indices(lines: list[Line], table: set[int] | None = None) -> set[int]:
    """Street-shaped lines directly above a ZIP+city cell, left-aligned with it
    ("Am Musteranger 3a" over "12345 Musterstadt"). Not in ``table``."""
    table = table or set()
    found: set[int] = set()
    for c, city in enumerate(lines):
        if c in table or not CITY_CELL.search(city.text):
            continue
        above = [
            (i, ln)
            for i, ln in enumerate(lines)
            if ln.text.strip() and box_is_above(ln, city, city.height, city.height)
        ]
        if not above:
            continue
        i, street = max(above, key=lambda pair: pair[1].bottom)
        if i not in table and STREET_CELL.search(street.text):
            found.add(i)
    return found
