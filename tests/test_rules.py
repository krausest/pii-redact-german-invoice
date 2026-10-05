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

P, A, C, B, S, I = (
    PiiLabel.PERSON,
    PiiLabel.ADDRESS,
    PiiLabel.CONTACT,
    PiiLabel.BANK,
    PiiLabel.SALUTATION,
    PiiLabel.ID,
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
    # -- the invoice's references ---------------------------------------------
    ("RECHNUNG Nr. 2026-1234-001", {I}),
    ("Rechnungsnummer: 248", {I}),
    ("Rg.-Nr.: 000123/045678", {I}),
    ("Kd.-Nr. 4711", {I}),
    ("Kundennummer: A-12", {I}),
    # A sentence asking for the number carries none.
    ("Bitte bei Zahlung stets Rechnungs-Nr. angeben!", set()),
    ("Rechnungsdatum: 19.03.2026", set()),
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
    ("Tel.:0123|456789-10", {C}),  # OCR reads the printed separator bar as "|"
    ("Fax 0123-45678901", {C}),
    ("+49 911 1234567", {C}),
    ("0049 (0)911 123456", {C}),
    ("Zentrale +43 1 5321234", {C}),
    ("Beratung, auch telefonisch", set()),
    ("Telefonische Beratung 3", set()),
    # An IBAN group can read "0043 0000 00" — a country code it is not.
    ("DE00 1234 0000 0043 0000 00", set()),
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


# -- what must stay readable ------------------------------------------------- #
def _cell(text, left, top, width=100, height=20):
    return Line(text=text, left=left, top=top, width=width, height=height)


@pytest.mark.parametrize(
    "text,kept",
    [
        ("Facharzt für Orthopädie", True),
        ("Hautarzt-Allergologie-Lasermedizin", True),
        ("Privatärztliche Praxis für", True),
        ("Chirotherapie – Sportmedizin", True),
        # The unknown word is the practice's name.
        ("Kieferorthopädie Muster", False),
        ("Dr. med. Andrea Muster, Fachärztin für Innere Medizin", False),
        ("Beratung", False),
    ],
)
def test_a_specialty_is_kept_only_on_its_own(text, kept):
    from backend.rules import is_specialty_line

    assert is_specialty_line(text) is kept


def test_keep_names_dates_diagnoses_and_the_clearing_house():
    from backend.rules import keep_indices

    lines = [
        _cell("Muster VerrechnungsSysteme GmbH", 10, 10, width=300),
        _cell("Rechnungsdatum:", 10, 60),
        _cell("19.03.2026", 130, 60),
        _cell("Diagnosen:", 10, 120),
        _cell("1. Akute Bronchitis", 10, 142, width=250),
        _cell("2. Lumbago", 12, 164, width=120),
    ]
    assert keep_indices(lines) == {0: "PVS", 1: "DATE", 2: "DATE", 3: "DIAG", 4: "DIAG", 5: "DIAG"}


def test_a_date_beside_a_name_is_not_kept_for_its_label():
    from backend.rules import keep_indices

    lines = [_cell("Datum:", 10, 10), _cell("Muster, Andrea 12.03.2026", 130, 10, width=200)]
    assert 1 not in keep_indices(lines)


def test_an_invoice_number_pairs_with_its_own_row_only():
    """Rows twenty pixels apart: the half-line tolerance of the other labeled
    values would hand the next row's plain "11" to the label above it."""
    from backend.rules import labeled_value_indices

    lines = [
        _cell("Rechnungsnummer:", 10, 100, height=21),
        _cell("26001234p", 130, 100),
        _cell("Abschlagsnummer:", 10, 118),
        _cell("11", 130, 121, width=22, height=18),
    ]
    assert set(labeled_value_indices(lines)) == {1}


@pytest.mark.parametrize(
    "label,value",
    [
        ("Steuernummer:", "123/456/78901"),
        ("USt-IdNr.:", "DE 123 456 789"),
        ("IK:", "123456789"),
        ("LANR:", "123456789"),
        ("BSNR:", "123456700"),
    ],
)
def test_a_sender_identifier_in_the_cell_beside_its_label(label, value):
    from backend.rules import labeled_value_indices

    lines = [_cell(label, 10, 100), _cell(value, 130, 100), _cell("12.03.2026", 300, 100)]
    assert labeled_value_indices(lines) == {1: PiiLabel.ID}


def test_a_sender_label_does_not_take_the_date_beside_it():
    from backend.rules import labeled_value_indices

    lines = [_cell("Steuernummer:", 10, 100), _cell("12.03.2026", 130, 100)]
    assert labeled_value_indices(lines) == {}


def test_an_invoice_number_under_a_narrower_label_cell():
    from backend.rules import labeled_value_indices

    lines = [
        _cell("Rg.-Nr.:", 462, 487, width=41, height=15),
        _cell("000123/045678", 462, 502, width=85, height=14),
    ]
    assert labeled_value_indices(lines) == {1: PiiLabel.ID}


# -- the birth year ---------------------------------------------------------- #
def _note(text):
    from backend.pii import Span
    from backend.rules import birth_year_note

    line = Line(text=text, left=0, top=10, width=len(text) * 10, height=20)
    return birth_year_note(line, [Span(PiiLabel.DATE_OF_BIRTH, 0, len(text), text, "labeled-value")])


def test_the_birth_year_is_printed_where_the_date_stood():
    note = _note("Geburtsdatum: 26.06.1975")
    assert (note.text, note.x0, note.x1, note.y0, note.y1) == ("1975", 140, 240, 10, 30)


def test_a_two_digit_birth_year_is_written_out():
    assert _note("geb. 05.03.11").text == "2011"
    assert _note("geb. 05.03.75").text == "1975"


def test_no_year_when_the_birthdate_is_ambiguous():
    assert _note("Geburtsdatum Behandlung 03.04.2026 01.02.1980") is None


def test_no_year_without_a_birthdate():
    from backend.pii import Span
    from backend.rules import birth_year_note

    line = Line(text="Rechnungsdatum: 19.03.2026", left=0, top=0, width=100, height=10)
    assert birth_year_note(line, [Span(PiiLabel.ID, 0, 5, "x", "labeled-value")]) is None
