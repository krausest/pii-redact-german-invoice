"""A street without a street suffix, recognized by the postal block it heads."""

from __future__ import annotations

from backend.models import Line
from backend.rules.geometry import below, left_aligned
from backend.rules.patterns import CITY_CELL, STREET_CELL


def streets_above_city(lines: list[Line], table: set[Line] | None = None) -> set[Line]:
    """Street-shaped lines directly above a ZIP+city cell, left-aligned with it
    ("Am Musteranger 3a" over "12345 Musterstadt"). Not in ``table``."""
    table = table or set()
    found: set[Line] = set()
    for city in lines:
        if city in table or not CITY_CELL.search(city.text):
            continue
        above = [
            ln
            for ln in lines
            if ln.text.strip() and below(ln, city, city.height) and left_aligned(ln, city, city.height)
        ]
        if not above:
            continue
        street = max(above, key=lambda ln: ln.bottom)
        if street not in table and STREET_CELL.search(street.text):
            found.add(street)
    return found
