"""What the deterministic rules know about German invoices, as one table.

This is an acceptance test, not a unit test: it does not check that a regex is
written a particular way, it checks the *judgements* the project has committed
to about real invoice text — and above all the negative ones. Every "must stay
readable" row below was once a wrong box on a sample page, and the corpus replay
(`pytest --regression`) cannot replace them: it pins 14 specific documents, while
these say what happens to a *shape* of line that those documents happen not to
contain.

One line in, the labels out. That is exactly what `rule_spans` gives the
pipeline, so the table asserts against the interface the pipeline actually uses.
"""

from __future__ import annotations

import pytest

from backend.document import build_document
from backend.models import Line
from backend.pii import PiiLabel
from backend.rules import rule_spans

P, A, C, B, S = (
    PiiLabel.PERSON,
    PiiLabel.ADDRESS,
    PiiLabel.CONTACT,
    PiiLabel.BANK,
    PiiLabel.SALUTATION,
)

# (text, labels it must produce). An empty set means: this line must survive.
CASES: list[tuple[str, set[PiiLabel]]] = [
    # -- the recipient block --------------------------------------------------
    ("Herrn", {S}),
    ("Frau Dr. Erika Muster", {S, P}),
    ("Musterstrasse 23", {A}),
    ("Bahnhofweg 5a", {A}),
    # A letterhead prints its address in capitals, and OCR glues the house
    # number to the abbreviated suffix.
    ("AUGSBURGERSTR.23", {A}),
    ("GOETHEALLEE 12a", {A}),
    ("12345 Musterhausen", {A}),
    # ...but a capitalized word plus a number is not a street. These are the
    # invoice's own vocabulary and must survive.
    ("Gesamtbetrag 23", set()),
    ("MwSt. 19", set()),
    ("Beleg 12", set()),
    ("RECHNUNG Nr. 2026-1234-001", set()),
    # -- the patient ----------------------------------------------------------
    ("Patient Mustermann, Max", {P}),
    ("Patientin Erika Muster", {P}),
    ("Pat.: Mustermann", {P}),
    ("Muster,Andrea 05.03.11", {P}),
    # -- the sender -----------------------------------------------------------
    ("Muster VerrechnungsSysteme GmbH", {PiiLabel.ORG}),
    ("Sanitaetshaus e. K.", {PiiLabel.ORG}),
    # "Betrag" ends in a lowercase "ag" and is not the legal form AG.
    ("Betrag", set()),
    ("www.dr-mueller-huber-muster.de", {C}),
    ("info@praxis-muster.de", {C}),
    ("praxis-muster.de", {C}),  # a bare host: no scheme, no www.
    ("Telefon: 01234 123456", {C}),
    ("Faktor 2,30", set()),
    # German glues abbreviations with the same dot OCR leaves attached to the
    # next word, so a body line can end in something shaped like a TLD.
    ("Untersuchung der Stuetz-Bew.org.", set()),
    ("Beratung einschl.der Auslagen", set()),
    ("Leistung zzgl.der Sachkosten", set()),
    ("HRB 1234 Musterstadt", {B}),
    ("IBAN DE00 0000 0000 0000 0000 00 - BIC MUSTDEXXX", {B}),
    # -- the invoice body, which must stay readable ---------------------------
    ("Videodokumentation. Entsprechend Ziffer 612 der GOAe -", set()),
    ("Ganzkoerperplethysmographische Bestimmung,", set()),
    ("Sekundenkapazitaet/Atemwegwiderstand nach P. 6 Abs. 2 der", set()),
    ("Summe der Auslagen / Sachkosten:", set()),
    ("Datum Ziffer Begruendung und Leistungstext Faktor Betrag", set()),
    ("Beratung, auch mittels Fernsprecher (inkl. Ausstellung", set()),
    ("18.02.2026", set()),
    ("150,68 EUR", set()),
    ("Behandlung Zahn", set()),
    # A compound *starting* with an organisation noun is body text; only the
    # suffix form ("Zahnarztpraxis") names a sender.
    ("Laboruntersuchung von Blut", set()),
    ("Laborkosten gemaess GOAe §10", set()),
    ("Krankenhausaufenthalt vom 01.02.", set()),
    # "Geb.Nr." heads the fee-number column, whatever follows it.
    ("Geb.Nr. 1234", set()),
    ("Geb.-Nr.", set()),
]


def _labels(text: str) -> set[PiiLabel]:
    """The labels the rules put on one line, through the same call the pipeline
    makes — no private helper, so the table cannot drift from production."""
    line = Line(text=text, left=0, top=0, width=100, height=10)
    _, bounds = build_document([line])
    return {s.label for s in rule_spans([line], bounds)}


@pytest.mark.parametrize("text,expected", CASES, ids=[c[0][:40] for c in CASES])
def test_the_rules_judgement(text: str, expected: set[PiiLabel]) -> None:
    assert _labels(text) == expected


def test_a_span_quotes_what_it_matched_and_names_the_rule() -> None:
    """The trace's whole diagnostic value: which pattern fired, and on what."""
    line = Line(text="Internet: praxis-muster.de", left=0, top=0, width=100, height=10)
    _, bounds = build_document([line])
    (span,) = rule_spans([line], bounds)
    assert (span.label, span.text, span.source) == (C, "praxis-muster.de", "rule CONTACT")


def test_every_rule_has_a_label() -> None:
    """A new pattern added to STATIC_RULES without a row in RULE_LABELS would
    raise a KeyError on the first page carrying it, in production."""
    from backend.pii import RULE_LABELS
    from backend.rules import STATIC_RULES

    assert {name for name, _ in STATIC_RULES} == set(RULE_LABELS)
