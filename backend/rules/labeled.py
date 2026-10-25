"""Values whose label sits in another OCR cell: "Geburtsdatum:" | "01.02.1980".

Pass one: each label line claims one value — itself when it holds one too, else
the nearest in its row, else, for a label alone in its cell, the nearest beneath
it. Pass two: a rule's ``below`` claims the line directly under each value.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from backend.models import Line
from backend.pii import PiiLabel
from backend.rules.geometry import below, column_gap, left_aligned, same_row
from backend.rules.patterns import (
    BIRTH_LABEL,
    BIRTH_LABEL_CELL,
    DATE_RE,
    DATE_VALUE,
    ID_LABEL,
    ID_VALUE,
    NAME_VALUE,
    PERSON_LABEL_CELL,
    PHONE_LABEL_CELL,
    PHONE_VALUE,
    REF_LABEL_CELL,
    REF_VALUE,
    SENDER_LABEL_CELL,
    SENDER_VALUE,
)

if TYPE_CHECKING:
    from backend.rules.keep import KeepReason


@dataclass(frozen=True)
class LabeledValue:
    """``label`` marks a line, ``value`` is what gets claimed as ``kind``.

    ``header``: the label alone in its cell — only then may its value sit beneath it.
    ``cell``: the value is too plain to stand alone ("11"), so in the row it must be
    right of the label, and beneath it may share the label's left edge.
    ``below``: a line directly under each claimed value, claimed as ``below_kind``."""

    kind: PiiLabel | KeepReason
    label: re.Pattern[str]
    value: re.Pattern[str]
    header: re.Pattern[str] | None = None
    cell: bool = False
    below: re.Pattern[str] | None = None
    below_kind: PiiLabel | None = None


LABELED_VALUES: list[LabeledValue] = [
    # "Geburtsdatum:" | "01.02.1980"
    LabeledValue(PiiLabel.DATE_OF_BIRTH, label=BIRTH_LABEL, value=DATE_RE, header=BIRTH_LABEL_CELL),
    # "Versicherten-Nr.:" | "A123456789"
    LabeledValue(PiiLabel.ID, label=ID_LABEL, value=ID_VALUE),
    # "Rg.-Nr.:" | "AB-12-3456"
    LabeledValue(PiiLabel.ID, label=REF_LABEL_CELL, value=REF_VALUE, header=REF_LABEL_CELL, cell=True),
    # "Steuernummer:" | "123/456/78901"
    LabeledValue(
        PiiLabel.ID, label=SENDER_LABEL_CELL, value=SENDER_VALUE, header=SENDER_LABEL_CELL, cell=True
    ),
    # "Telefon:" | "0123 456789"
    LabeledValue(
        PiiLabel.CONTACT, label=PHONE_LABEL_CELL, value=PHONE_VALUE, header=PHONE_LABEL_CELL, cell=True
    ),
    # "Patient:" | "Andrea Muster", and "01.02.1980" alone under the name
    LabeledValue(
        PiiLabel.PERSON,
        label=PERSON_LABEL_CELL,
        value=NAME_VALUE,
        header=PERSON_LABEL_CELL,
        below=DATE_VALUE,
        below_kind=PiiLabel.DATE_OF_BIRTH,
    ),
]


def labeled_values(
    lines: list[Line],
    table: set[Line] | None = None,
    rules: list[LabeledValue] = LABELED_VALUES,
) -> dict[Line, PiiLabel | KeepReason]:
    """Lines holding a labeled value, mapped to the ``kind`` of the first rule
    that claimed them. Nothing beneath a label reaches into ``table``."""
    table = table or set()
    gap = column_gap(lines)
    found: dict[Line, PiiLabel | KeepReason] = {}
    for rule in rules:
        values = _paired_values(lines, rule, table, gap)
        for i in sorted(values):
            found.setdefault(lines[i], rule.kind)
        if rule.below is not None and rule.below_kind is not None:
            for i in sorted(_lines_below(lines, values, rule.below, table, gap)):
                found.setdefault(lines[i], rule.below_kind)
    return found


def _paired_values(lines: list[Line], rule: LabeledValue, table: set[Line], gap: float) -> set[int]:
    """Pass one: the one value each label line of ``rule`` claims."""
    found: set[int] = set()
    for i, ln in enumerate(lines):
        if not rule.label.search(ln.text):
            continue
        if _is_value(ln, rule):
            found.add(i)
            continue
        j = _row_value(lines, ln, rule)
        if j is None and _is_header(ln, rule):
            j = _column_value(lines, ln, rule, table, gap)
        if j is not None:
            found.add(j)
    return found


def _row_value(lines: list[Line], label: Line, rule: LabeledValue) -> int | None:
    """The value in ``label``'s row nearest to it."""
    row = [
        i
        for i, ln in enumerate(lines)
        if ln is not label
        and _is_value(ln, rule)
        and same_row(label, ln)
        and not (rule.cell and ln.left < label.right - label.height)
    ]

    def distance(i: int) -> tuple[float, float]:
        ln = lines[i]
        return max(0, ln.left - label.right, label.left - ln.right), abs(ln.mid_y - label.mid_y)

    return min(row, key=distance) if row else None


def _column_value(
    lines: list[Line], label: Line, rule: LabeledValue, table: set[Line], gap: float
) -> int | None:
    """The value nearest beneath ``label`` — centred under it, or for a ``cell``
    rule sharing its left edge — within ``gap`` and outside ``table``."""
    column = [
        i
        for i, ln in enumerate(lines)
        if ln is not label
        and ln not in table
        and _is_value(ln, rule)
        and below(label, ln, gap)
        and (
            label.left <= ln.mid_x <= label.right
            or rule.cell and left_aligned(label, ln, label.height)
        )
    ]
    return min(column, key=lambda i: lines[i].top) if column else None


def _lines_below(
    lines: list[Line], values: set[int], pattern: re.Pattern[str], table: set[Line], gap: float
) -> set[int]:
    """Pass two: the line directly under each value, where it matches ``pattern``."""
    found: set[int] = set()
    for i in values:
        above = lines[i]
        under = [
            j
            for j, ln in enumerate(lines)
            if below(above, ln, gap) and left_aligned(above, ln, above.height)
        ]
        if not under:
            continue
        j = min(under, key=lambda j: lines[j].top)
        if lines[j] not in table and pattern.search(lines[j].text):
            found.add(j)
    return found


def _is_header(ln: Line, rule: LabeledValue) -> bool:
    return rule.header is not None and bool(rule.header.search(ln.text))


def _is_value(ln: Line, rule: LabeledValue) -> bool:
    """A value of ``rule`` — never a cell holding nothing but its label."""
    return not _is_header(ln, rule) and bool(rule.value.search(ln.text))
