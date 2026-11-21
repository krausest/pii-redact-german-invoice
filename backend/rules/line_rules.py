"""Rules complete on one OCR line: a regex match is the finding."""

from __future__ import annotations

import re
from dataclasses import dataclass

from backend.document import Document
from backend.pii import PiiLabel, Span, rule_source
from backend.rules.patterns import (
    BIRTH_MARK,
    CONTACT,
    DE_PLZ_CITY,
    DE_STREET,
    IMPRINT_VALUES,
    NAME_DATE,
    ORG_NAME,
    PATIENT_NAME,
    PHONE,
    REF_MERGED,
    SALUT,
    TAX_NUMBER,
    TITLE_NAME,
)

@dataclass(frozen=True)
class LineRule:
    """A ``pattern`` whose every match on a line is a ``label``; ``name`` is what
    the trace calls it (``rule NAME``)."""

    label: PiiLabel
    name: str
    pattern: re.Pattern[str]


# In the order they are tried.
STATIC_RULES: list[LineRule] = [
    LineRule(PiiLabel.SALUTATION, "SALUT", SALUT),
    LineRule(PiiLabel.PERSON, "PATIENT_NAME", PATIENT_NAME),
    LineRule(PiiLabel.PERSON, "TITLE_NAME", TITLE_NAME),
    LineRule(PiiLabel.PERSON, "NAME_DATE", NAME_DATE),
    LineRule(PiiLabel.DATE_OF_BIRTH, "BIRTH_MARK", BIRTH_MARK),
    LineRule(PiiLabel.ID, "REF_MERGED", REF_MERGED),
    LineRule(PiiLabel.ADDRESS, "DE_STREET", DE_STREET),
    LineRule(PiiLabel.ADDRESS, "DE_PLZ_CITY", DE_PLZ_CITY),
    LineRule(PiiLabel.ORG, "ORG_LEGAL", ORG_NAME),
    LineRule(PiiLabel.CONTACT, "CONTACT", CONTACT),
    LineRule(PiiLabel.CONTACT, "PHONE", PHONE),
    LineRule(PiiLabel.ID, "TAX_NUMBER", TAX_NUMBER),
]


def line_spans(doc: Document) -> list[Span]:
    """Every match of every static rule, as spans in document coordinates."""
    spans: list[Span] = []
    for line in doc.lines:
        if not line.text.strip():
            continue
        offset = doc.range_of(line).start
        for rule in STATIC_RULES:
            for m in rule.pattern.finditer(line.text):
                start, end = offset + m.start(), offset + m.end()
                spans.append(Span(rule.label, start, end, m.group(), rule_source(rule.name)))
    return spans


def imprint_spans(doc: Document) -> list[Span]:
    """The value after each imprint marker ("IBAN", "Konto"), not the marker;
    it may sit on the next line in reading order."""
    spans: list[Span] = []
    for pattern in IMPRINT_VALUES:
        for m in pattern.finditer(doc.text):
            start, end = m.span("value")
            spans.append(Span(PiiLabel.BANK, start, end, m.group("value"), rule_source("IMPRINT")))
    return spans
