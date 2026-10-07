"""Rules complete on one OCR line: a regex match is the finding."""

from __future__ import annotations

import re

from backend.models import Line
from backend.pii import PiiLabel, Span
from backend.rules.patterns import (
    BIRTH_MARK,
    CONTACT,
    DE_PLZ_CITY,
    DE_STREET,
    IMPRINT,
    NAME_DATE,
    ORG_LEGAL,
    PATIENT_NAME,
    PHONE,
    REF_MERGED,
    SALUT,
    TAX_NUMBER,
    TITLE_NAME,
)

# (label, name in the trace as "rule NAME", pattern), in the order they are tried.
STATIC_RULES: list[tuple[PiiLabel, str, re.Pattern[str]]] = [
    (PiiLabel.SALUTATION, "SALUT", SALUT),
    (PiiLabel.PERSON, "PATIENT_NAME", PATIENT_NAME),
    (PiiLabel.PERSON, "TITLE_NAME", TITLE_NAME),
    (PiiLabel.PERSON, "NAME_DATE", NAME_DATE),
    (PiiLabel.DATE_OF_BIRTH, "BIRTH_MARK", BIRTH_MARK),
    (PiiLabel.ID, "REF_MERGED", REF_MERGED),
    (PiiLabel.ADDRESS, "DE_STREET", DE_STREET),
    (PiiLabel.ADDRESS, "DE_PLZ_CITY", DE_PLZ_CITY),
    (PiiLabel.ORG, "ORG_LEGAL", ORG_LEGAL),
    (PiiLabel.CONTACT, "CONTACT", CONTACT),
    (PiiLabel.CONTACT, "PHONE", PHONE),
    (PiiLabel.BANK, "IMPRINT", IMPRINT),
    (PiiLabel.ID, "TAX_NUMBER", TAX_NUMBER),
]


def line_spans(lines: list[Line], bounds: list[tuple[int, int]]) -> list[Span]:
    """Every match of every static rule, as spans in document coordinates."""
    spans: list[Span] = []
    for line, (lo, _) in zip(lines, bounds):
        if not line.text.strip():
            continue
        for label, name, pattern in STATIC_RULES:
            for m in pattern.finditer(line.text):
                spans.append(Span(label, lo + m.start(), lo + m.end(), m.group(), f"rule {name}"))
    return spans
