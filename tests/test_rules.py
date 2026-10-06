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
    # A letterhead prints title and name in capitals.
    ("DR. MED. ANDREA MUSTER, FEBO*", {P}),
    ("PROF. DR. A. MUSTER", {P}),
    ("Musterstrasse 23", {A}),
    ("Bahnhofweg 5a", {A}),
    # A letterhead prints its address in capitals, and OCR glues the house
    # number to the abbreviated suffix.
    ("AUGSBURGERSTR.23", {A}),
    ("GOETHEALLEE 12a", {A}),
    ("12345 Musterhausen", {A}),
    # The suffix written as a word of its own, and OCR reading ß as B.
    ("Musterer Straße 12", {A}),
    ("MUSTERER PLATZ 17", {A}),
    ("Muster-StraBe 10", {A}),
    ("Musterstrabe 5", {A}),
    ("Äußere Musterer Straße 8 - 10", {A}),
    # ...but a capitalized word plus a number is not a street. These are the
    # invoice's own vocabulary and must survive.
    ("Gesamtbetrag 23", set()),
    ("die Straße 3 mal überqueren", set()),
    ("Gehtraining Weg 200 m", set()),
    ("MwSt. 19", set()),
    ("Beleg 12", set()),
    # -- the invoice's references ---------------------------------------------
    ("RECHNUNG Nr. 2026-1234-001", {I}),
    ("Rechnungsnummer: 248", {I}),
    ("Rg.-Nr.: 000123/045678", {I}),
    ("Kd.-Nr. 4711", {I}),
    ("Kundennummer: A-12", {I}),
    ("Rechnung 123456-123456", {I}),
    ("RECHNUNG: AB-248", {I}),
    # Mid-sentence, or too short to be a number rather than a count.
    ("Bitte begleichen Sie die Rechnung 123456 umgehend", set()),
    ("Rechnung 1 von 2", set()),
    ("Re.-Nr.: 123456", {I}),
    ("BFS-Nr. 1-23456-12345678", {I}),
    ("RechnNr:1234", {I}),
    ("Rechn.Nr. 1234 5678 9012 34", {I}),
    ("Nummer: 12/3456", {I}),
    ("Nummer 3 der Anlage", set()),
    # OCR damages the "r" of "Nr" and reads the dot as a comma.
    ("Rechnungs-Nz, 12345/2026-1234", {I}),
    ("Rechnungs-Nr, 4711", {I}),
    ("Rechnungsnachweis 2026", set()),
    # A tax number is identified by its slash groups, whatever OCR did to its label.
    ("Steuez-Nz. 123/456/78901", {I}),
    ("St.-Nr.123/456/78901", {I}),
    ("123/4567/8901", {I}),
    ("Pos. 12/345/1", set()),
    ("Faktor 2,3 / 1,8 / 2,5", set()),
    ("Re.-Datum: 12.03.2026", set()),
    ("Rechnung 12.03.2026", set()),
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
    ("Genossenschaftsregisternummer: GnR 123456", {B}),
    ("Unser IK-Zeichen: 123456 789", {B}),
    ("IK-Nummer: 1234 567 89", {B}),
    ("Ust.-ID:DE123456789", {B}),
    ("USt.-IdNr.: DE123456789", {B}),
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


def test_a_diagnosis_beside_its_label_cell_is_kept():
    from backend.rules import keep_indices

    lines = [
        _cell("Diagnose:", 10, 120, width=80),
        _cell("V.a. Akute Bronchitis, Verruca vulgaris", 100, 120, width=400),
        _cell("Lumbago, Zephalgie", 103, 142, width=200),
        _cell("Andrea Muster", 10, 164, width=150),
        _cell("Musterstraße 1", 103, 250, width=150),
    ]
    assert keep_indices(lines) == {0: "DIAG", 1: "DIAG", 2: "DIAG"}


def test_a_diagnosis_beside_its_label_ends_at_the_next_row_of_cells():
    from backend.rules import keep_indices

    lines = [
        _cell("Diagnose:", 10, 120, width=80),
        _cell("Lumbago", 100, 120, width=200),
        _cell("Nr.", 10, 142, width=40),
        _cell("Muster", 102, 142, width=100),
    ]
    assert keep_indices(lines) == {0: "DIAG", 1: "DIAG"}


def test_a_merged_diagnosis_label_keeps_nothing_beside_it():
    from backend.rules import keep_indices

    lines = [
        _cell("Diagnose: Lumbago", 10, 120, width=150),
        _cell("Andrea Muster", 300, 120, width=150),
    ]
    assert keep_indices(lines) == {0: "DIAG"}


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
        ("IK-Nummer:", "123456789"),
        # The label names whose IK it is.
        ("IK Musterstelle Musterstadt", "123456789"),
        ("USt.-IdNr.:", "DE 123 456 789"),
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


# -- the street above its city ----------------------------------------------- #
def _block(street, city="12345 Musterstadt", left=10, gap=4):
    return [
        _cell("Frau", 10, 100),
        _cell("Andrea Muster", 10, 124),
        _cell(street, left, 148),
        _cell(city, 10, 168 + gap),
    ]


@pytest.mark.parametrize(
    "street", ["Musterau 12", "Am Musteranger 3a", "Unter den Mustern 4", "Musterhof 8 - 10"]
)
def test_a_street_without_suffix_above_its_city_is_an_address(street):
    from backend.rules import street_above_city_indices

    assert street_above_city_indices(_block(street)) == {2}


def test_the_street_above_its_city_is_a_named_rule_span():
    lines = _block("Musterau 12")
    _, bounds = build_document(lines)
    spans = [s for s in rule_spans(lines, bounds) if s.source == "rule STREET_ABOVE_CITY"]
    assert [(s.label, s.text) for s in spans] == [(A, "Musterau 12")]


@pytest.mark.parametrize(
    "lines",
    [
        # in another column
        _block("Musterau 12", left=300),
        # too far above
        _block("Musterau 12", gap=40),
        # no house number
        _block("Musterau"),
        # the city is only part of its line
        _block("Musterau 12", city="Praxis Muster, 12345 Musterstadt"),
        # headings and running text above a city line
        _block("Rechnung"),
        _block("Liquidation vom 12.03.2026 für"),
        _block("Gesamtbetrag 23,40"),
    ],
)
def test_no_street_above_a_city_without_the_shape(lines):
    from backend.rules import street_above_city_indices

    assert street_above_city_indices(lines) == set()


def test_no_street_above_a_city_inside_the_item_table():
    from backend.rules import street_above_city_indices

    assert street_above_city_indices(_block("Musterau 12"), table={2}) == set()
    assert street_above_city_indices(_block("Musterau 12"), table={3}) == set()


# -- a date belongs to its nearest date label --------------------------------- #
def _grid():
    """A patient block as a two-row grid: the birth label beside its date, the
    invoice date under its own column header — but in the birth label's row."""
    return [
        _cell("Patient:", 145, 596, width=63, height=21),
        _cell("Geb.datum:", 148, 618, width=92, height=18),
        _cell("Andrea Muster", 270, 596, width=143, height=23),
        _cell("01.02.1980", 271, 618, width=94, height=18),
        _cell("Rechnungs-Nr.:", 589, 596, width=107, height=20),
        _cell("AB1234", 599, 620, width=87, height=18),
        _cell("Rechnungsdatum:", 821, 597, width=124, height=20),
        _cell("01.03.2026", 836, 621, width=93, height=18),
    ]


def _birthdates(lines):
    from backend.rules import labeled_value_indices

    return {
        lines[i].text for i, k in labeled_value_indices(lines).items() if k is PiiLabel.DATE_OF_BIRTH
    }


def test_the_invoice_date_under_its_header_is_no_birthdate():
    assert _birthdates(_grid()) == {"01.02.1980"}


def test_in_one_row_each_date_belongs_to_the_label_left_of_it():
    lines = [
        _cell("Geburtsdatum:", 10, 100),
        _cell("01.02.1980", 120, 100),
        _cell("Rechnungsdatum:", 240, 100, width=120),
        _cell("01.03.2026", 370, 100),
    ]
    assert _birthdates(lines) == {"01.02.1980"}


@pytest.mark.parametrize(
    "lines",
    [
        [_cell("Geburtsdatum:", 10, 100), _cell("01.02.1980", 120, 100)],
        # the label printed just above the date, the rows overlapping
        [_cell("geboren am", 503, 381, width=73, height=17), _cell("01.02.1980", 507, 396, width=69, height=17)],
        # a treatment-period sentence under the birth row owns nothing
        [
            _cell("Geburtsdatum:", 315, 1546, width=331, height=51),
            _cell("01.02.1980", 806, 1541, width=252, height=53),
            _cell("Behandlungszeitraum von 01.03.2026 bis 05.03.2026", 312, 1661, width=1179, height=64),
        ],
    ],
    ids=["beside", "above", "sentence-below"],
)
def test_a_birthdate_still_pairs_with_its_label(lines):
    assert "01.02.1980" in _birthdates(lines)


# -- which date on a birth line is the birthdate ------------------------------ #
@pytest.mark.parametrize(
    "text,expected",
    [
        ("Geb.-Datum: 01.02.1980", "01.02.1980"),
        ("Muster,Andrea 01.02.80", "01.02.80"),
        # the mark says which date: the treatment date before it does not count
        ("12.03.2026 Andrea Muster *01.02.1980", "01.02.1980"),
        ("Geb.-Datum: 01.02.1980 Re.-Datum: 01.03.2026", None),
        ("Geburtsdatum", None),
    ],
)
def test_the_birthdate_on_a_birth_line(text, expected):
    from backend.rules import birth_date_in

    found = birth_date_in(text)
    assert (found.group() if found else None) == expected


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
