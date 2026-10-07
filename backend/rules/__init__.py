"""Deterministic redaction rules for German invoices.

- ``patterns``: the regex vocabulary
- ``line_rules``: rules complete on one OCR line
- ``labeled``: a label in one cell, its value in another
- ``postal``: a street recognized by the ZIP+city line beneath it
- ``item_table``: the fee table
- ``keep``: lines that must stay readable, and what they reject
- ``birth_year``: the year printed over a redacted birthdate

:func:`rule_spans` is what the pipeline calls.
"""

from __future__ import annotations

from backend.models import Line
from backend.pii import PiiLabel, Span
from backend.rules.birth_year import birth_year_note
from backend.rules.item_table import item_table_indices
from backend.rules.keep import keep_indices, rejected
from backend.rules.labeled import labeled_value_indices
from backend.rules.line_rules import line_spans
from backend.rules.postal import street_above_city_indices

__all__ = ["birth_year_note", "item_table_indices", "keep_indices", "rejected", "rule_spans"]


def rule_spans(
    lines: list[Line], bounds: list[tuple[int, int]], table: set[int] | None = None
) -> list[Span]:
    """Every deterministic finding on ``lines``, as spans in document coordinates
    (``bounds`` from :func:`backend.document.build_document`). The regexes run per
    line, since several are anchored to a whole cell; a labeled value or a street
    above its city becomes a span over its whole line."""
    spans = line_spans(lines, bounds)
    for i, kind in labeled_value_indices(lines, table).items():
        spans.append(_whole_line(lines, bounds, i, PiiLabel(kind), "labeled-value"))
    for i in sorted(street_above_city_indices(lines, table)):
        spans.append(_whole_line(lines, bounds, i, PiiLabel.ADDRESS, "rule STREET_ABOVE_CITY"))
    return spans


def _whole_line(
    lines: list[Line], bounds: list[tuple[int, int]], i: int, label: PiiLabel, source: str
) -> Span:
    lo, hi = bounds[i]
    return Span(label, lo, hi, lines[i].text, source)
