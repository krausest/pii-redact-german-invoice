"""The name, birthdate and identifier memory: what one page said about a person,
applied everywhere else.

A surname recurs on lines no detector reaches on its own (a subject line, a
greeting, a footer). So a page is read twice: the detectors find what they find,
then every bare recurrence of a name or birthdate they witnessed is redacted too.

A *pattern* (patient label, title, salutation, birth mark) points at a person:
the name tokens running from it are harvested, not the rest of its line. A
classifier *span* says where the person is, so only the span is harvested. Only
``PERSON`` spans count, and every token must be shaped like a name.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import NamedTuple

from backend.document import Document
from backend.models import Line
from backend.ocr_variants import VariantIndex
from backend.pii import BIRTHDATE_MEMORY, ID_MEMORY, LABELED_VALUE, NAME_MEMORY, PiiLabel, Span
from backend.rules.birth_year import DateKey, birth_date_in, date_key
from backend.rules.patterns import (
    BIRTH_DATE,
    BIRTH_LABEL,
    BIRTH_MARK,
    DATE_KEEP_LABEL,
    LABEL_WORD,
    NAME_AT,
    NAME_DATE,
    NAME_TOKEN,
    ORG_LEGAL,
    PERSON_LABEL,
    PERSON_LABEL_CELL,
    SALUT,
    SPECIALTY_WORD,
    STREET_END,
    TITLE_WORD,
    TITLES,
)
from backend.trace import Trace

# Shorter forenames ("Max") are not worth the false hits; the surname is what recurs.
_MIN_NAME_LENGTH = 4

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
    "Kollegen", "Kolleginnen", "Partner",
})

# Compared case-folded: an all-caps letterhead must not teach "FACHARZT".
_NAME_STOPWORDS_CF = frozenset(w.casefold() for w in _NAME_STOPWORDS)

# How many name tokens one pattern may point at: "Prof. Dr. Andrea Muster-Beispiel".
_MAX_RUN = 4
# What joins two names in one run: "Muster & Beispiel", "Muster und Beispiel".
_JOIN = re.compile(r"\s*(?:&|\bund\b)")
_VOWEL = re.compile(r"(?i)[aeiouyäöü]")
# A capital after a small letter: OCR junk ("DrSaer") or a degree ("MSc").
_INNER_CAPITAL = re.compile(r"[a-zäöüß][A-ZÄÖÜ]")


def _is_label(token: str) -> bool:
    """Whether ``token`` is a label the rules know ("Datum", "Patientin")."""
    return bool(
        DATE_KEEP_LABEL.search(token)
        or BIRTH_LABEL.search(token)
        or LABEL_WORD.match(token)
        or PERSON_LABEL_CELL.search(token)
    )


def _tokens(text: str) -> set[str]:
    """The name tokens in ``text`` (:data:`~backend.rules.patterns.NAME_TOKEN`),
    long enough to learn, without labels and stopwords."""
    return {
        part
        for token in NAME_TOKEN.findall(text)
        if _name_shaped(token)
        for part in token.split("-")  # each half of a double name recurs alone
        if len(part) >= _MIN_NAME_LENGTH
        and part.casefold() not in _NAME_STOPWORDS_CF
        and not _is_label(part)
        and _name_shaped(part)
    }


def _name_shaped(token: str) -> bool:
    """Not a street, legal form, specialty, title or OCR junk."""
    return not (
        not _VOWEL.search(token)
        or _INNER_CAPITAL.search(token)
        or STREET_END.search(token)
        or ORG_LEGAL.fullmatch(token)
        or SPECIALTY_WORD.fullmatch(token)
        or TITLE_WORD.fullmatch(token)
    )


def _is_name(token: str) -> bool:
    return _name_shaped(token) and token.casefold() not in _NAME_STOPWORDS_CF and not _is_label(token)


def _run_after(text: str, pos: int, comma: bool = False) -> list[str]:
    """The name tokens from ``pos`` on, until anything else; ``comma`` allows
    one after the first ("Muster, Andrea")."""
    names: list[str] = []
    while len(names) < _MAX_RUN:
        if comma and len(names) == 1 and text.startswith(",", pos):
            pos += 1
        m = NAME_AT.match(text, pos)
        if m is None or not _is_name(m.group(1)):
            break
        names.append(m.group(1))
        pos = m.end()
        if (j := _JOIN.match(text, pos)) is not None:
            pos = j.end()
    return names


def _run_before(text: str, pos: int) -> list[str]:
    """The name tokens directly before ``pos``, one comma allowed before it."""
    names: list[str] = []
    for i, word in enumerate(reversed(text[:pos].split())):
        word = word.rstrip(",") if i == 0 else word
        if word in ("&", "und"):
            continue
        if len(names) == _MAX_RUN or not NAME_TOKEN.fullmatch(word) or not _is_name(word):
            break
        names.append(word)
    return names


def _named_by_patterns(text: str) -> list[str]:
    """The names the person patterns on a line point at."""
    found: list[str] = []
    for m in TITLES.finditer(text):
        found += _run_after(text, m.end())
    for m in SALUT.finditer(text):
        found += _run_after(text, m.end())
    for m in PERSON_LABEL.finditer(text):
        found += _run_after(text, m.end(), comma=True)
    for m in NAME_DATE.finditer(text):
        found += NAME_TOKEN.findall(m.group())
    for m in BIRTH_MARK.finditer(text):
        found += _run_before(text, m.start())
    if BIRTH_DATE.search(text):
        for m in BIRTH_LABEL.finditer(text):
            found += _run_before(text, m.start())
    return found


def _harvest_names(doc: Document, hits: Mapping[Line, Sequence[Span]]) -> dict[str, str]:
    """The names this page witnesses, each mapped to the first line naming it."""
    names: dict[str, str] = {}
    for line in doc.lines:
        found = _tokens(" ".join(_named_by_patterns(line.text)))
        for span in hits.get(line, []):
            if span.label is PiiLabel.PERSON:
                found |= _tokens(span.text)
        for name in sorted(found):
            names.setdefault(name, line.text)
    return names


def mentions_name(text: str, names: set[str]) -> str | None:
    """The first remembered name in ``text`` as a whole word, in any casing that
    starts with a capital ("MUSTER" for "Muster", never "klein" for "Klein")."""
    found = name_mentions(text, names)
    return found[0].group() if found else None


def name_mentions(text: str, names: set[str]) -> list[re.Match[str]]:
    """Every remembered name in ``text``, by :func:`mentions_name`'s rule."""
    return [
        m
        for name in names
        for m in re.finditer(rf"\b{re.escape(name)}\b", text, re.IGNORECASE)
        if m.group()[:1].isupper()
    ]


# A first glyph OCR prints for the capital "I".
_LOOKS_CAPITAL = frozenset("l1|!")


def _variant_ok(word: str) -> bool:
    """A page word that may be an OCR variant of a name: shaped like one."""
    return (
        len(word) >= _MIN_NAME_LENGTH
        and (word[0].isupper() or word[0] in _LOOKS_CAPITAL)
        and _is_name(word)
    )


def _name_spans(doc: Document, names: set[str], trace: Trace | None = None) -> list[Span]:
    """A span over every mention of a remembered name, and of its OCR variants."""
    spans: list[Span] = []
    variants = VariantIndex(sorted(names))
    for line in doc.lines:
        if not line.text.strip():
            continue
        for m in name_mentions(line.text, names):
            spans.append(doc.part_of(line, m.start(), m.end(), PiiLabel.PERSON, NAME_MEMORY))
        for start, end, name in variants.find(line.text, _variant_ok):
            if trace is not None:
                trace.add("    name variant %r ~ %r", line.text[start:end], name)
            spans.append(doc.part_of(line, start, end, PiiLabel.PERSON, NAME_MEMORY))
    return spans


# -- the birthdate memory ----------------------------------------------------- #
@dataclass
class Memory:
    """What a document has said so far, carried from page to page: the names
    and the birthdates."""

    names: set[str] = field(default_factory=set)
    birthdates: set[DateKey] = field(default_factory=set)
    ids: set[str] = field(default_factory=set)

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
        for ident, line_text in _harvest_ids(hits).items():
            if ident not in self.ids:
                trace.add("    id memory %r <- %r", ident, line_text)
                self.ids.add(ident)

    def recall(self, doc: Document, trace: Trace | None = None) -> list[Span]:
        """Spans over every remembered name, its OCR variants, and every
        remembered birthdate and identifier in ``doc``."""
        return (
            _name_spans(doc, self.names, trace)
            + _birthdate_spans(doc, self.birthdates)
            + _id_spans(doc, self.ids)
        )

    def dump(self, trace: Trace) -> None:
        """Every name, birthdate and identifier remembered so far."""
        trace.add("=== memory dump ===")
        dates = [f"{d.day:02}.{d.month:02}.{d.year}" for d in sorted(self.birthdates)]
        for kind, entries in (("names", sorted(self.names)), ("birthdates", dates), ("ids", sorted(self.ids))):
            trace.add("  %s (%d):", kind, len(entries))
            for entry in entries:
                trace.add("    %r", entry)


def _is_birth_evidence(line: Line, spans: Sequence[Span]) -> bool:
    """A deterministic witness that ``line`` holds a birthdate; a model's
    DATE_OF_BIRTH is no witness (a treatment date would recur in the table)."""
    text = line.text
    return (
        any(s.label is PiiLabel.DATE_OF_BIRTH and s.source == LABELED_VALUE for s in spans)
        or bool(BIRTH_MARK.search(text) or NAME_DATE.search(text))
        or bool(BIRTH_LABEL.search(text) and BIRTH_DATE.search(text))
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
    """A span over every remembered birthdate."""
    spans: list[Span] = []
    for line in doc.lines:
        for m in BIRTH_DATE.finditer(line.text):
            if date_key(m.group()) in birthdates:
                spans.append(doc.part_of(line, m.start(), m.end(), PiiLabel.DATE_OF_BIRTH, BIRTHDATE_MEMORY))
    return spans


# -- the identifier memory ---------------------------------------------------- #
# A token of an identifier a rule found; six digits make it unmistakable.
_ID_TOKEN = re.compile(r"[\w/.-]*\d[\w/.-]*")
_ID_MIN_DIGITS = 6


def _harvest_ids(hits: Mapping[Line, Sequence[Span]]) -> dict[str, str]:
    """The identifiers a rule or a labeled value found on this page, each with its
    line; a model's guess is no witness."""
    found: dict[str, str] = {}
    for line, spans in hits.items():
        for span in spans:
            if span.label is not PiiLabel.ID or not (span.source.startswith("rule ") or span.source == LABELED_VALUE):
                continue
            for token in _ID_TOKEN.findall(span.text):
                token = token.strip("./-")
                if sum(ch.isdigit() for ch in token) >= _ID_MIN_DIGITS:
                    found.setdefault(token, line.text)
    return found


def _id_spans(doc: Document, ids: set[str]) -> list[Span]:
    """A span over every bare recurrence of a remembered identifier."""
    if not ids:
        return []
    alts = "|".join(map(re.escape, sorted(ids, key=len, reverse=True)))
    pattern = re.compile(rf"(?<![\w/.-])(?:{alts})(?![\w/-]|\.\w)")
    return [
        doc.part_of(line, m.start(), m.end(), PiiLabel.ID, ID_MEMORY)
        for line in doc.lines
        for m in pattern.finditer(line.text)
    ]
