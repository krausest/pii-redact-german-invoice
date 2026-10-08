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

from backend.document import Document
from backend.pii import PiiLabel, Span
from backend.rules.birth_year import birth_year_note
from backend.rules.item_table import item_table_lines
from backend.rules.keep import kept_lines, rejected
from backend.rules.labeled import labeled_values
from backend.rules.line_rules import line_spans
from backend.rules.postal import streets_above_city

__all__ = ["birth_year_note", "item_table_lines", "kept_lines", "rejected", "rule_spans"]


def rule_spans(doc: Document) -> list[Span]:
    """Every deterministic finding in ``doc``, as spans in document coordinates.
    The regexes run per line, since several are anchored to a whole cell; a
    labeled value or a street above its city becomes a span over its whole line."""
    spans = line_spans(doc)
    for line, kind in labeled_values(doc.lines, doc.item_table).items():
        spans.append(doc.whole_line(line, PiiLabel(kind), line.text, "labeled-value"))
    streets = streets_above_city(doc.lines, doc.item_table)
    for line in doc.lines:
        if line in streets:
            spans.append(doc.whole_line(line, PiiLabel.ADDRESS, line.text, "rule STREET_ABOVE_CITY"))
    return spans
