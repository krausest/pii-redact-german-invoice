"""The name memory: what one page said about a person, applied to the rest of it.

A surname recurs on lines nothing else catches — a subject line, a greeting
split across OCR lines, a footer signature. No detector reaches those on their
own: a lone capitalized token is not a name to a model, and no pattern describes
it either. So the page is read **twice**. Pass one lets every detector say what
it found; pass two blackens every bare recurrence of the names those findings
name.

What changed with the whole-document switch is *who may be the witness*. The
memory used to be fed by five regexes, because a rule was the only detector
whose word could be trusted without a feedback loop. Now every detector speaks
the same :class:`~backend.pii.Span`, so the evidence is a **label**, not a
pattern — which is what lets the classifier's find on the first occurrence of a
name redact the third one, in a footer, that it did not tag itself.

Only ``PERSON`` counts. ``ADDRESS``, ``ORG``, ``CONTACT``, ``BANK`` and ``ID``
name a place, a company or a number: a page's own street recurring in its
letterhead is not a person to look for, and a bare number matching an invoice
figure elsewhere is a box drawn over the very reference the document is shared
for.

**Evidence comes in two kinds, and they harvest differently** — measured, not
assumed:

* A *pattern* names a person on this line, so the whole line is harvested. The
  label rules stop after a single name part by construction ("Patient
  Mustermann" never reaches the "Max" behind it), and OCR glues the pair around
  its label in either order, so the surname alone would be half a memory.
* A *span* says where the person is, so only the span is harvested. Reading the
  whole line around a model's find is what a letterhead punishes: one line
  carries a managing director's name **and** the company's, and taking the line
  whole made the company a remembered "name" that then blackened body text,
  a legal footnote and an invoice number elsewhere on the page (measured on the
  corpus: 12 new boxes, 9 of them wrong). The span carries the name and stops.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from backend.models import Line
from backend.pii import PiiLabel, Span
from backend.rules import (
    BIRTH_LABEL,
    BIRTH_MARK,
    DATE_RE,
    NAME_DATE,
    PATIENT_NAME,
    SALUT,
    TITLE_NAME,
)

# A name-shaped token: starts with a capital, at least four letters, and may be
# all caps — a lab prints its patient row "MUSTER, MAX", and a name only
# harvestable in one of its two casings is half a memory. The length floor drops
# both OCR shrapnel and most non-name capitalized words; a three-letter forename
# ("Max") is not worth the false-positive surface, the surname is what recurs.
_NAME_TOKEN = re.compile(r"\b[A-ZÄÖÜ][A-ZÄÖÜa-zäöüß]{3,}\b")

# Words that pass the shape test on evidence lines but are never names: the
# labels and salutations themselves, their sentence dressing, months, and the
# profession vocabulary that shares a letterhead line with a titled name.
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

# Compared case-folded, because the tokens are not: an all-caps evidence line
# ("PATIENT MUSTER, MAX") would otherwise harvest "PATIENT" as a name and go on
# to redact every line mentioning a patient.
_NAME_STOPWORDS_CF = frozenset(w.casefold() for w in _NAME_STOPWORDS)

# The deterministic witness: a line matching one of these names a person outright,
# whatever any span says. Every pattern but the last two also becomes a ``PERSON``
# span; ``BIRTH_MARK`` and the spelled-out birth line ("Max Mustermann, geboren
# am ...") only do so when the spatial label/value pass happens to pair them, and
# a name is no less named when it does not.
_NAME_EVIDENCE = (PATIENT_NAME, TITLE_NAME, SALUT, BIRTH_MARK, NAME_DATE)


def _tokens(text: str) -> set[str]:
    """The name-shaped tokens in ``text``. The stopword list is what keeps the
    label vocabulary itself — "Patient", "Geburtsdatum", a month — from being
    taken for a name and redacting every line that mentions one."""
    return {
        tok for tok in _NAME_TOKEN.findall(text) if tok.casefold() not in _NAME_STOPWORDS_CF
    }


def harvest(lines: Sequence[Line], hits: Sequence[Sequence[Span]]) -> set[str]:
    """The names this page has named, from pass one's findings.

    ``hits`` is index-aligned with ``lines``: the spans each line was found to
    carry. See the module docstring for why a pattern harvests its whole line
    and a span only itself.

    Only spans that were allowed to *redact their own line* should be passed in.
    A model hit inside the item table is dropped there because a German two-word
    service text reads exactly like a forename/surname pair; letting it feed the
    memory would spread that mistake across the whole document instead of
    confining it to the one line it was already stopped on.
    """
    names: set[str] = set()
    for line, spans in zip(lines, hits):
        if any(p.search(line.text) for p in _NAME_EVIDENCE) or (
            BIRTH_LABEL.search(line.text) and DATE_RE.search(line.text)
        ):
            names |= _tokens(line.text)
        for span in spans:
            if span.label is PiiLabel.PERSON:
                names |= _tokens(span.text)
    return names


def mentions_name(text: str, names: set[str]) -> bool:
    """Whether the line contains any harvested name as a whole word, in any
    casing that still *looks* like a name.

    Whole-word is what keeps "Allgemeine" from matching a Dr. Allgemein — the
    word boundary does that work, not the letter case. Case is compared loosely
    because one document prints the same person both ways ("Andrea Muster" in the
    address block, "MUSTER, ANDREA" in the patient row), and a memory that holds
    only the casing it first met is half a memory. What is still required is that
    the occurrence *starts with a capital*: that is the line between a surname and
    the ordinary German word it may collide with ("Klein" the person vs "klein
    gedruckt"), and it is the reason this is not simply IGNORECASE.
    """
    for name in names:
        for m in re.finditer(rf"\b{re.escape(name)}\b", text, re.IGNORECASE):
            if m.group()[:1].isupper():
                return True
    return False


def name_spans(
    lines: Sequence[Line], bounds: Sequence[tuple[int, int]], names: set[str]
) -> list[Span]:
    """Pass two: a whole-line span for every bare recurrence of a known name.

    The line is taken whole rather than just the matched token because that is
    what the box has always been — the recurrence is evidence about the line, and
    a name printed on it is rarely the only thing worth losing.
    """
    spans: list[Span] = []
    for i, line in enumerate(lines):
        if line.text.strip() and mentions_name(line.text, names):
            lo, hi = bounds[i]
            spans.append(Span(PiiLabel.PERSON, lo, hi, line.text, "name-memory"))
    return spans
