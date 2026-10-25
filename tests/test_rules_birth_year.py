"""Which date is the birthdate, and the year printed over it (``backend.rules.birth_year``)."""

from __future__ import annotations

import pytest

from backend.models import Line
from backend.pii import PiiLabel, Span
from backend.rules.birth_year import birth_date_in, birth_year_note


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
    found = birth_date_in(text)
    assert (found.group() if found else None) == expected


# -- the birth year ---------------------------------------------------------- #
def _note(text):
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
    line = Line(text="Rechnungsdatum: 19.03.2026", left=0, top=0, width=100, height=10)
    assert birth_year_note(line, [Span(PiiLabel.ID, 0, 5, "x", "labeled-value")]) is None
