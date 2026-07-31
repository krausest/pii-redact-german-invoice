"""Deterministic rule corner cases the code comments call out."""

from __future__ import annotations

from backend.classifiers.presidio import _redactable
from backend.models import Line
from backend.rules import (
    CONTACT,
    DE_PLZ_CITY,
    DE_STREET,
    IMPRINT,
    ORG_LEGAL,
    SALUT,
    birthdate_indices,
    line_matches_static_rule,
)


def test_salutation_matches():
    assert SALUT.search("Herrn")
    assert SALUT.search("Frau Dr. Müller")
    assert not SALUT.search("Rechnung")


def test_de_plz_city_excludes_uppercase_noise():
    assert DE_PLZ_CITY.search("12345 Musterstadt")
    assert not DE_PLZ_CITY.search("15118 MID")  # all-caps spec noise, not a city


def test_de_street_matches():
    assert DE_STREET.search("Musterstrasse 23")
    assert DE_STREET.search("Bahnhofweg 5a")


def test_static_rule_aggregate():
    assert line_matches_static_rule("Herr Max")
    assert line_matches_static_rule("Musterstrasse 23")
    assert not line_matches_static_rule("Behandlung Zahn")


def test_org_legal_matches_company_suffixes():
    assert ORG_LEGAL.search("Muster VerrechnungsSysteme GmbH")
    assert ORG_LEGAL.search("Muster Dental UG")
    assert ORG_LEGAL.search("Sanitaetshaus e. K.")
    assert not ORG_LEGAL.search("Betrag")  # lowercase "ag" is not \bAG\b


def test_contact_matches_urls_mail_and_phone():
    assert CONTACT.search("www.dr-mueller-huber-sonstwas.de")
    assert CONTACT.search("https://praxis-muster.de/kontakt")
    assert CONTACT.search("info@praxis-muster.de")
    assert CONTACT.search("Telefon: 04131 123456")
    assert CONTACT.search("Fax 04131/12 34 57")
    assert not CONTACT.search("Faktor 2,30")


def test_imprint_matches_registry_and_bank_identifiers():
    assert IMPRINT.search("HRB 1824 Lueneburg")
    assert IMPRINT.search("IK: 123434672 - USt-IdNr: DE 123457767")
    assert IMPRINT.search("Postfach 15 60 - 21305 Lueneburg")
    assert IMPRINT.search("IBAN DE00 0000 0000 0000 0000 00 - BIC MUSTDEXXX")
    assert IMPRINT.search("LAN-Nr.: 123498761")


def test_sender_rules_leave_leistungstext_alone():
    # Real body lines from the sample invoices: none of them may be redacted, or
    # the invoice itself becomes unreadable.
    for text in (
        "Videodokumentation. Entsprechend Ziffer 612 der GOAe -",
        "Ganzkoerperplethysmographische Bestimmung,",
        "Sekundenkapazitaet/Atemwegwiderstand nach P. 6 Abs. 2 der",
        "Begruendung zu 605: Aufgrund der vorliegenden kieferorthopaedischen",
        "Summe der Auslagen / Sachkosten:",
        "Datum Ziffer Begruendung und Leistungstext Faktor Betrag",
        "18.02.2026",
        "150,68 EUR",
        "Beratung, auch mittels Fernsprecher (inkl. Ausstellung",
    ):
        assert not line_matches_static_rule(text), text


def _line(text, top=0, height=10):
    return Line(text=text, left=0, top=top, width=100, height=height)


def test_birthdate_same_row_two_columns():
    lines = [
        _line("Geburtstag", top=100, height=10),
        _line("01.02.1980", top=101, height=10),  # same row, different column
        _line("Rechnungsdatum 05.06.2024", top=200, height=10),
    ]
    assert birthdate_indices(lines) == {1}


def test_birthdate_merged_line():
    lines = [_line("geboren am 1.2.1980", top=0, height=10)]
    assert birthdate_indices(lines) == {0}


def test_geb_nr_fee_column_not_a_birthdate():
    # "Geb.Nr." is a fee-number column, not "geboren/Geburt" — a treatment date
    # on its row must not be redacted.
    lines = [
        _line("Geb.Nr.", top=50, height=10),
        _line("11.03.2024", top=51, height=10),
    ]
    assert birthdate_indices(lines) == set()


def _person_result(start, end):
    class R:
        entity_type = "PERSON"

    r = R()
    r.start = start
    r.end = end
    return r


def test_redactable_drops_single_token_person():
    line = "5.0016"
    assert not _redactable([_person_result(0, len(line))], line)


def test_redactable_keeps_multi_token_person():
    line = "Max Mustermann"
    assert _redactable([_person_result(0, len(line))], line)


def test_redactable_whitelist_token_not_counted():
    line = "Anzahl Anz"  # both whitelisted -> not a person
    assert not _redactable([_person_result(0, len(line))], line)
