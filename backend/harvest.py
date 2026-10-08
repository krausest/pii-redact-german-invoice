"""The name and birthdate memory: what one page said about a person, applied
everywhere else.

A surname recurs on lines no detector reaches on its own (a subject line, a
greeting, a footer). So a page is read twice: the detectors find what they find,
then every bare recurrence of a name or birthdate they witnessed is redacted too.

A *pattern* (patient label, title, salutation, birth line) names a person on its
line, so the whole line is harvested. A classifier *span* says where the person
is, so only the span is harvested: a letterhead line holds a person and a company.
Only ``PERSON`` spans count.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import NamedTuple

from backend.document import Document
from backend.models import Line
from backend.pii import BIRTHDATE_MEMORY, LABELED_VALUE, NAME_MEMORY, PiiLabel, Span
from backend.rules.birth_year import DateKey, birth_date_in, date_key
from backend.rules.patterns import (
    BIRTH_LABEL,
    BIRTH_MARK,
    DATE_KEEP_LABEL,
    DATE_RE,
    NAME_DATE,
    PATIENT_NAME,
    PERSON_LABEL_CELL,
    SALUT,
    TITLE_NAME,
)
from backend.trace import Trace

# A name-shaped token: a capital, four letters at least, maybe all caps. Shorter
# forenames ("Max") are not worth the false hits; the surname is what recurs.
_NAME_TOKEN = re.compile(r"\b[A-ZÄÖÜ][A-ZÄÖÜa-zäöüß]{3,}\b")

# Words on evidence lines that are never names: labels, salutations, months,
# professions.
_NAME_STOPWORDS = frozenset({
    "Sehr", "Geehrte", "Geehrter", "Geehrtes", "Herr", "Herrn", "Frau",
    "Fräulein", "Familie", "Eheleute", "Patient", "Patientin", "Patienten",
    "Versicherte", "Versicherter", "Versicherten", "Mitglied",
    "Rechnungsempfänger", "Rechnungsempfängerin", "Zahlungspflichtige",
    "Zahlungspflichtiger", "Geburtsdatum", "Geburtstag", "Geboren",
    "Januar", "Februar", "März", "April", "Juni", "Juli", "August",
    "September", "Oktober", "November", "Dezember",
    "Arzt", "Ärztin", "Zahnarzt", "Zahnärztin", "Facharzt", "Fachärztin",
    "Praxis", "Medizin", "Innere", "Allgemeinmedizin",
})

# Compared case-folded: an all-caps letterhead must not teach "FACHARZT".
_NAME_STOPWORDS_CF = frozenset(w.casefold() for w in _NAME_STOPWORDS)

# A line matching one of these names a person outright, whatever any span says.
_NAME_EVIDENCE = (PATIENT_NAME, TITLE_NAME, SALUT, BIRTH_MARK, NAME_DATE)


def _is_label(token: str) -> bool:
    """Whether ``token`` is a label the rules know ("Datum", "Patientin")."""
    return bool(
        DATE_KEEP_LABEL.search(token)
        or BIRTH_LABEL.search(token)
        or PERSON_LABEL_CELL.search(token)
    )


def _tokens(text: str) -> set[str]:
    """The name-shaped tokens in ``text``, without labels and stopwords."""
    return {
        tok
        for tok in _NAME_TOKEN.findall(text)
        if tok.casefold() not in _NAME_STOPWORDS_CF and not _is_label(tok)
    }


def _harvest_names(doc: Document, hits: Mapping[Line, Sequence[Span]]) -> dict[str, str]:
    """The names this page witnesses, each mapped to the first line naming it."""
    names: dict[str, str] = {}
    for line in doc.lines:
        found: set[str] = set()
        if any(p.search(line.text) for p in _NAME_EVIDENCE) or (
            BIRTH_LABEL.search(line.text) and DATE_RE.search(line.text)
        ):
            found |= _tokens(line.text)
        for span in hits.get(line, []):
            if span.label is PiiLabel.PERSON:
                found |= _tokens(span.text)
        for name in sorted(found):
            names.setdefault(name, line.text)
    return names


def mentions_name(text: str, names: set[str]) -> str | None:
    """The first remembered name in ``text`` as a whole word, in any casing that
    starts with a capital ("MUSTER" for "Muster", never "klein" for "Klein")."""
    for name in names:
        for m in re.finditer(rf"\b{re.escape(name)}\b", text, re.IGNORECASE):
            if m.group()[:1].isupper():
                return m.group()
    return None


def _name_spans(doc: Document, names: set[str]) -> list[Span]:
    """A whole-line span for every line mentioning a remembered name, quoting it."""
    spans: list[Span] = []
    for line in doc.lines:
        name = mentions_name(line.text, names) if line.text.strip() else None
        if name is not None:
            spans.append(doc.whole_line(line, PiiLabel.PERSON, name, NAME_MEMORY))
    return spans


# -- the birthdate memory ----------------------------------------------------- #
@dataclass
class Memory:
    """What a document has said so far, carried from page to page: the names
    and the birthdates."""

    names: set[str] = field(default_factory=set)
    birthdates: set[DateKey] = field(default_factory=set)

    def learn(self, doc: Document, hits: Mapping[Line, Sequence[Span]], trace: Trace) -> None:
        """Remember the names and birthdates ``hits`` witness on this page.

        Only spans that survived their line's keep reason may be passed: a model's
        find in the item table would otherwise spread across the document."""
        for name, source in _harvest_names(doc, hits).items():
            if name not in self.names:
                trace.add("    name memory %r <- %r", name, source)
                self.names.add(name)
        for key, witness in _harvest_birthdates(doc, hits).items():
            if key not in self.birthdates:
                trace.add("    birthdate memory %r <- %r", witness.printed, witness.line_text)
                self.birthdates.add(key)

    def recall(self, doc: Document) -> list[Span]:
        """Whole-line spans for every line of ``doc`` mentioning a remembered name
        or birthdate."""
        return _name_spans(doc, self.names) + _birthdate_spans(doc, self.birthdates)


def _is_birth_evidence(line: Line, spans: Sequence[Span]) -> bool:
    """A deterministic witness that ``line`` holds a birthdate; a model's
    DATE_OF_BIRTH is no witness (a treatment date would recur in the table)."""
    text = line.text
    return (
        any(s.label is PiiLabel.DATE_OF_BIRTH and s.source == LABELED_VALUE for s in spans)
        or bool(BIRTH_MARK.search(text) or NAME_DATE.search(text))
        or bool(BIRTH_LABEL.search(text) and DATE_RE.search(text))
    )


class Witness(NamedTuple):
    """Where a remembered birthdate was learned: the date as printed, and its line."""

    printed: str
    line_text: str


def _harvest_birthdates(doc: Document, hits: Mapping[Line, Sequence[Span]]) -> dict[DateKey, Witness]:
    """The birthdates this page witnesses; a line whose dates leave the birthdate
    open teaches none."""
    found: dict[DateKey, Witness] = {}
    for line in doc.lines:
        if not _is_birth_evidence(line, hits.get(line, [])):
            continue
        match = birth_date_in(line.text)
        if match is not None:
            found.setdefault(date_key(match.group()), Witness(match.group(), line.text))
    return found


def _birthdate_spans(doc: Document, birthdates: set[DateKey]) -> list[Span]:
    """A whole-line span for every line holding a remembered birthdate."""
    spans: list[Span] = []
    for line in doc.lines:
        for m in DATE_RE.finditer(line.text):
            if date_key(m.group()) in birthdates:
                spans.append(doc.whole_line(line, PiiLabel.DATE_OF_BIRTH, m.group(), BIRTHDATE_MEMORY))
                break
    return spans
