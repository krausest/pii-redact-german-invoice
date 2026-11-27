"""A label in one OCR cell, its value in another (``backend.rules.labeled``)."""

from __future__ import annotations

import pytest

from backend.pii import PiiLabel
from backend.rules.labeled import (
    LABELED_VALUES,
    LabeledValue,
    _column_value,
    _row_value,
    labeled_claims,
    labeled_values,
)
from backend.rules.patterns import ID_LABEL, ID_VALUE, REF_VALUE
from tests.conftest import cell


def test_an_invoice_number_pairs_with_its_own_row_only():
    """Rows twenty pixels apart: the half-line tolerance of the other labeled
    values would hand the next row's plain "11" to the label above it."""
    lines = [
        cell("Rechnungsnummer:", 10, 100, height=21),
        cell("26001234p", 130, 100),
        cell("Abschlagsnummer:", 10, 118),
        cell("11", 130, 121, width=22, height=18),
    ]
    assert set(labeled_values(lines)) == {lines[1]}


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
    lines = [cell(label, 10, 100), cell(value, 130, 100), cell("12.03.2026", 300, 100)]
    assert labeled_values(lines) == {lines[1]: PiiLabel.ID}


def test_a_sender_label_does_not_take_the_date_beside_it():
    lines = [cell("Steuernummer:", 10, 100), cell("12.03.2026", 130, 100)]
    assert labeled_values(lines) == {}


def test_an_invoice_title_takes_the_number_cell_beside_it():
    """OCR split "RECHNUNG Nr. 123456" into two cells; the "Nr." stays readable."""
    lines = [cell("RECHNUNG", 10, 100), cell("Nr. 123456", 300, 102)]
    assert labeled_values(lines) == {lines[1]: PiiLabel.ID}
    assert labeled_claims(lines)[lines[1]][1:] == (4, 10)


@pytest.mark.parametrize("value", ["Nr. 12", "Nr. 123456"])
def test_a_bare_number_cell_without_an_invoice_title_is_kept(value):
    lines = [cell("Leistung", 10, 100), cell(value, 300, 102)]
    assert labeled_values(lines) == {}


def test_an_invoice_title_takes_no_short_count():
    lines = [cell("Rechnung", 10, 100), cell("Nr. 2", 300, 102)]
    assert labeled_values(lines) == {}


def test_an_invoice_number_under_a_narrower_labelcell():
    lines = [
        cell("Rg.-Nr.:", 462, 487, width=41, height=15),
        cell("000123/045678", 462, 502, width=85, height=14),
    ]
    assert labeled_values(lines) == {lines[1]: PiiLabel.ID}

# -- a date belongs to its nearest date label --------------------------------- #
def _grid():
    """A patient block as a two-row grid: the birth label beside its date, the
    invoice date under its own column header — but in the birth label's row."""
    return [
        cell("Patient:", 145, 596, width=63, height=21),
        cell("Geb.datum:", 148, 618, width=92, height=18),
        cell("Andrea Muster", 270, 596, width=143, height=23),
        cell("01.02.1980", 271, 618, width=94, height=18),
        cell("Rechnungs-Nr.:", 589, 596, width=107, height=20),
        cell("AB1234", 599, 620, width=87, height=18),
        cell("Rechnungsdatum:", 821, 597, width=124, height=20),
        cell("01.03.2026", 836, 621, width=93, height=18),
    ]


def _birthdates(lines):
    return {
        ln.text for ln, k in labeled_values(lines).items() if k is PiiLabel.DATE_OF_BIRTH
    }


def test_the_invoice_date_under_its_header_is_no_birthdate():
    assert _birthdates(_grid()) == {"01.02.1980"}


def test_in_one_row_each_date_belongs_to_the_label_left_of_it():
    lines = [
        cell("Geburtsdatum:", 10, 100),
        cell("01.02.1980", 120, 100),
        cell("Rechnungsdatum:", 240, 100, width=120),
        cell("01.03.2026", 370, 100),
    ]
    assert _birthdates(lines) == {"01.02.1980"}


@pytest.mark.parametrize(
    "lines",
    [
        [cell("Geburtsdatum:", 10, 100), cell("01.02.1980", 120, 100)],
        # the label printed just above the date, the rows overlapping
        [cell("geboren am", 503, 381, width=73, height=17), cell("01.02.1980", 507, 396, width=69, height=17)],
        # a treatment-period sentence under the birth row owns nothing
        [
            cell("Geburtsdatum:", 315, 1546, width=331, height=51),
            cell("01.02.1980", 806, 1541, width=252, height=53),
            cell("Behandlungszeitraum von 01.03.2026 bis 05.03.2026", 312, 1661, width=1179, height=64),
        ],
    ],
    ids=["beside", "above", "sentence-below"],
)
def test_a_birthdate_still_pairs_with_its_label(lines):
    assert "01.02.1980" in _birthdates(lines)


def test_a_birthdate_alone_under_the_labeled_name():
    lines = [
        cell("Behandelte Person:", 100, 100, width=175),
        cell("Andrea Muster", 289, 100, width=161),
        cell("01.02.1980", 289, 128, width=102),
        cell("Behandlungszeitraum vom 15.04.2026 bis 28.04.2026", 289, 156, width=300),
    ]
    assert labeled_values(lines) == {lines[1]: PiiLabel.PERSON, lines[2]: PiiLabel.DATE_OF_BIRTH}


def _card_header(*rows):
    """The insurance-card header of a statutory form: labels above, values beneath."""
    return [
        cell("Name, Vorname des Versicherten", 100, 220, width=220, height=21),
        cell("geb. am", 500, 220, width=64, height=25),
        *rows,
    ]


def test_the_card_header_claims_surname_forename_and_birthdate():
    lines = _card_header(
        cell("Musterfrau", 100, 254, width=160, height=34),
        cell("01.02.1980", 502, 255, width=140, height=28),
        cell("Andrea", 98, 289, width=90, height=40),
        cell("VK gültig bis", 502, 291, width=90),
        cell("Musterstr. 12", 100, 334, width=170, height=28),
    )
    assert labeled_values(lines) == {
        lines[2]: PiiLabel.PERSON,
        lines[3]: PiiLabel.DATE_OF_BIRTH,
        lines[4]: PiiLabel.PERSON,
    }


def test_the_card_header_takes_a_name_in_one_cell_but_not_the_street_beneath():
    lines = _card_header(
        cell("Muster, Andrea", 100, 254, width=180, height=34),
        cell("Musterstr. 12", 100, 289, width=170, height=34),
    )
    assert labeled_values(lines) == {lines[2]: PiiLabel.PERSON}


def test_the_card_header_takes_no_column_header_in_its_row():
    lines = _card_header(cell("Status", 700, 220, width=60))
    assert labeled_values(lines) == {}


def test_a_plain_name_header_still_needs_two_name_tokens():
    lines = [cell("Name", 100, 220, width=60), cell("Musterfrau", 100, 250, width=120)]
    assert labeled_values(lines) == {}


def test_a_bare_geb_column_over_amounts_owns_nothing():
    lines = [cell("Geb.", 100, 220, width=60), cell("01.02.1980", 100, 250, width=120)]
    assert labeled_values(lines) == {}


def test_a_sender_identifier_under_a_label_closing_a_holder_line():
    lines = [
        cell("Dr. Andrea Muster, Vertragsarztnr.:", 400, 100, width=500),
        cell("2106315267", 402, 130, width=120),
    ]
    assert labeled_values(lines) == {lines[1]: PiiLabel.ID}


# -- every labeled row, every layout ------------------------------------------ #
# (label, value, kind, whether the label also heads a column)
_LABELED = {
    "birthdate": ("Geburtsdatum:", "01.02.1980", PiiLabel.DATE_OF_BIRTH, True),
    "insured-id": ("Versicherten-Nr.:", "A123456789", PiiLabel.ID, False),
    "invoice-ref": ("Rechnungsnummer:", "AB-12-3456", PiiLabel.ID, True),
    "sender-id": ("Steuernummer:", "123/456/78901", PiiLabel.ID, True),
    "phone": ("Telefon:", "0123 456789", PiiLabel.CONTACT, True),
    "person": ("Patient:", "Andrea Muster", PiiLabel.PERSON, True),
}


def _beside(label, value):
    return [cell(label, 10, 100, width=150), cell(value, 200, 100, width=150)]


def _under(label, value):
    return [cell(label, 10, 100, width=150), cell(value, 10, 124, width=150)]


@pytest.mark.parametrize("row", _LABELED)
def test_a_labeled_value_beside_its_label(row):
    label, value, kind, _ = _LABELED[row]
    lines = _beside(label, value)
    assert labeled_values(lines) == {lines[1]: kind}


@pytest.mark.parametrize("row", _LABELED)
def test_a_labeled_value_under_its_label_only_where_it_heads_a_column(row):
    label, value, kind, header = _LABELED[row]
    lines = _under(label, value)
    assert labeled_values(lines) == ({lines[1]: kind} if header else {})


@pytest.mark.parametrize("row", _LABELED)
def test_a_column_under_a_label_stops_at_the_item_table(row):
    label, value, _, _ = _LABELED[row]
    lines = _under(label, value)
    assert labeled_values(lines, table={lines[1]}) == {}


@pytest.mark.parametrize("row", _LABELED)
def test_a_bare_label_is_no_value(row):
    label, _, _, _ = _LABELED[row]
    assert labeled_values([cell(label, 10, 100, width=150)]) == {}


def test_the_first_rule_claiming_a_value_wins():
    rules = [
        LabeledValue(PiiLabel.ID, label=ID_LABEL, value=ID_VALUE),
        LabeledValue(PiiLabel.CONTACT, label=ID_LABEL, value=ID_VALUE),
    ]
    lines = _beside("Versicherten-Nr.:", "A123456789")
    assert labeled_values(lines, rules=rules) == {lines[1]: PiiLabel.ID}


def _ref_rule():
    return next(r for r in LABELED_VALUES if r.value is REF_VALUE)


def test_the_row_value_is_the_nearest_right_of_a_cell_label():
    label = cell("Rg.-Nr.:", 200, 100)
    lines = [
        cell("AB-1", 10, 100, width=80),
        label,
        cell("AB-2", 450, 100, width=80),
        cell("AB-3", 320, 100, width=80),
    ]
    assert _row_value(lines, label, _ref_rule()) == 3


def test_the_row_value_ignores_the_next_row():
    label = cell("Rg.-Nr.:", 10, 100)
    assert _row_value([label, cell("AB-1", 150, 130)], label, _ref_rule()) is None


def test_the_column_value_is_the_nearest_beneath():
    label = cell("Rg.-Nr.:", 10, 100)
    lines = [label, cell("AB-2", 10, 150), cell("AB-1", 10, 124)]
    assert _column_value(lines, label, _ref_rule(), set(), 60) == 2


@pytest.mark.parametrize(
    "value,in_table,gap",
    [
        (cell("AB-1", 10, 124), True, 60),
        (cell("AB-1", 10, 200), False, 60),  # too far down
        (cell("AB-1", 300, 124), False, 60),  # another column
    ],
    ids=["table", "gap", "column"],
)
def test_no_column_value(value, in_table, gap):
    label = cell("Rg.-Nr.:", 10, 100)
    table = {value} if in_table else set()
    assert _column_value([label, value], label, _ref_rule(), table, gap) is None
