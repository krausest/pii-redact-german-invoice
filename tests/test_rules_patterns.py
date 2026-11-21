"""What the deterministic rules judge about German invoice text.

``CASES``: one line in, the labels out, through ``rule_spans`` as the pipeline
calls it. Every "must stay readable" row was once a wrong box on a sample page.
``PATTERN_CASES``: why each pattern in ``backend.rules.patterns`` has its shape.
"""

from __future__ import annotations

import pytest

from backend.document import Document
from backend.models import Line
from backend.pii import PiiLabel
from backend.rules import patterns, rule_spans

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
    # An order number identifies the invoice as well.
    ("Auftragsnummer 123.456 vom 01.02.2026", {I}),
    ("Auftrags-Nr.: 4711", {I}),
    ("Auftragsnummer bitte angeben", set()),
    # Customer and client numbers, also without "Nr".
    ("Kunde 12345", {I}),
    ("Mandanten-Nr. 4711", {I}),
    ("Unser Mandant: 012345/678901234 Musterpraxis", {I}),
    ("Kunde seit 2015", set()),
    ("angegebener Mandant hat die Forderung abgetreten", set()),
    ("um unseren Mandanten Musterpraxis von", set()),
    # A barcode's printed text, between its start and stop stars.
    ("Musterdienst*123456-7890123*", {I}),
    ("*Hinweis*", set()),
    ("Im Auftrag von: MVZ Musterstadt", set()),
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
    return {s.label for s in rule_spans(Document([line]))}


@pytest.mark.parametrize("text,expected", CASES, ids=[c[0][:40] for c in CASES])
def test_the_rules_judgement(text: str, expected: set[PiiLabel]) -> None:
    assert _labels(text) == expected


def test_a_span_quotes_what_it_matched_and_names_the_rule() -> None:
    """The trace's whole diagnostic value: which pattern fired, and on what."""
    line = Line(text="Internet: praxis-muster.de", left=0, top=0, width=100, height=10)
    (span,) = rule_spans(Document([line]))
    assert (span.label, span.text, span.source) == (C, "praxis-muster.de", "rule CONTACT")


@pytest.mark.parametrize(
    "text,rule,value",
    [
        ("Behandelte Person: Andrea Muster", "PATIENT_NAME", "Andrea"),
        ("Pat.: A. Muster", "PATIENT_NAME", "A. Muster"),
        ("Rechn.Nr. 1234 5678", "REF_MERGED", "1234 5678"),
        ("Re.-Nr.: 123456", "REF_MERGED", "123456"),
        ("Rechnung 2026-0815", "REF_MERGED", "2026-0815"),
        ("Nummer: 12/3456", "REF_MERGED", "12/3456"),
        ("Kunde 12345", "REF_MERGED", "12345"),
        ("Unser Mandant: 012345/678", "REF_MERGED", "012345/678"),
        ("Andrea Muster, geb. 01.02.1980", "BIRTH_MARK", "01.02.1980"),
        ("Andrea Muster *01.02.1980", "BIRTH_MARK", "01.02.1980"),
        ("Telefon: 0123 456789", "PHONE", "0123 456789"),
        ("Tel.(0123) 45678", "PHONE", "(0123) 45678"),
        ("+49 911 1234567", "PHONE", "+49 911 1234567"),
    ],
)
def test_a_labeled_rule_claims_the_value_not_its_label(text, rule, value):
    line = Line(text=text, left=0, top=0, width=100, height=10)
    spans = [s for s in rule_spans(Document([line])) if s.source == f"rule {rule}"]
    assert [s.text for s in spans] == [value]


# (pattern, text, whether it matches): why each pattern has its shape.
PATTERN_CASES: list[tuple[str, str, bool]] = [
    # A person label needs the name after it; a compound or a sentence is no label.
    ("PATIENT_NAME", "Pat. M. Mustermann", True),
    ("PATIENT_NAME", "Versichertennummer Muster", False),
    ("PATIENT_NAME", "Beratung des Patienten", False),
    # A label cell's modifier must inflect like an adjective.
    ("PERSON_LABEL_CELL", "Behandelte Person:", True),
    ("PERSON_LABEL_CELL", "Versicherte Person", True),
    ("PERSON_LABEL_CELL", "Beratung Person", False),
    ("PERSON_LABEL_CELL", "Leistung Name", False),
    ("PERSON_LABEL_CELL", "Beratung des Patienten", False),
    # Two name tokens, one may have lost its capital to OCR, not both.
    ("NAME_VALUE", "Muster, Andrea", True),
    ("NAME_VALUE", "loanna Mustermann", True),
    ("NAME_VALUE", "eingehende beratung", False),
    ("NAME_VALUE", "Andrea", False),
    ("TITLE_NAME", "Dr. Dr. Andrea Muster-Beispiel", True),
    ("TITLE_NAME", "Dr. A. Muster", True),
    # Name and date together; a comma-joined pair alone is a service text.
    ("NAME_DATE", "MUSTER, ANDREA 26.06.75", True),
    ("NAME_DATE", "Muster-Beispiel,Anna-Lena 01.02.80", True),
    ("NAME_DATE", "Mikroskopie,Kultur", False),
    # "Geb." alone means Gebühren; "Geb.Dat." does not.
    ("BIRTH_LABEL", "geboren am", True),
    ("BIRTH_LABEL", "Geb.Dat.", True),
    ("BIRTH_LABEL", "Geb.Nr. 5", False),
    ("BIRTH_LABEL", "Gebühren Datum", False),
    # The mark directly before the date, even glued to the name before it.
    ("BIRTH_MARK", "Mustermann, Max'geb. 13.08.1964", True),
    ("BIRTH_MARK", "* 01.02.1980", True),
    ("BIRTH_MARK", "* Bitte beachten: Termin am 01.02.2026", False),
    ("BIRTH_MARK", "Geb.Nr. 5 13.08.2026", False),
    # OCR reads the dots of the mark and the date as commas.
    ("BIRTH_MARK", "Andrea Muster, geb, 01,02.1980", True),
    ("BIRTH_MARK", "geb. 01.02,1980", True),
    ("BIRTH_MARK", "Geb. 12,50 EUR", False),
    # "Weg" counts glued to the name, not as a word of its own.
    ("DE_STREET", "Musterweg 3", True),
    ("DE_STREET", "Langer Weg 12", False),
    # The postcode starts its token; an all-caps city has four letters at least.
    ("DE_PLZ_CITY", "12345Musterstadt", True),
    ("DE_PLZ_CITY", "D-12345 Musterstadt", True),
    ("DE_PLZ_CITY", "12345 MUSTERSTADT", True),
    ("DE_PLZ_CITY", "44/20101 Massage", False),
    ("DE_PLZ_CITY", "44-20101 Massage", False),
    ("DE_PLZ_CITY", "15118 MID", False),
    # Closed, hyphenated, abbreviated and OCR-damaged "Nr".
    ("ID_LABEL", "Fallnummer", True),
    ("ID_LABEL", "Fall-Nr.", True),
    ("ID_LABEL", "Pat.-Nr:", True),
    ("ID_LABEL", "Fall-Nz.", True),
    ("ID_LABEL", "Fallpauschale", False),
    # An identifier is never a date or an amount.
    ("ID_VALUE", "A123456789", True),
    ("ID_VALUE", "4 399 267 00", True),
    ("ID_VALUE", "12.03.2026", False),
    ("ID_VALUE", "1.234,56", False),
    # A sender label closes its cell, maybe after its holder.
    ("SENDER_LABEL_CELL", "Dr. Andrea Muster, Vertragsarztnr.:", True),
    ("SENDER_LABEL_CELL", "Bitte Steuernummer angeben", False),
    ("DATE_VALUE", "01.02.1980", True),
    ("DATE_VALUE", "Behandlungszeitraum vom 15.04.2026 bis 28.04.2026", False),
    ("DATE_KEEP_LABEL", "Rechnungsdatum", True),
    ("DATE_KEEP_LABEL", "Geburtsdatum", False),
]


@pytest.mark.parametrize(
    "name,text,matches", PATTERN_CASES, ids=[f"{n}:{t[:30]}" for n, t, _ in PATTERN_CASES]
)
def test_the_pattern_judgement(name: str, text: str, matches: bool) -> None:
    assert bool(getattr(patterns, name).search(text)) is matches


@pytest.mark.parametrize(
    "text,ref",
    [
        ("Rechn.Nr. 1234 5678 9012 34", "1234 5678 9012 34"),
        ("Rg.-Nr.: 12/3456 7890", "12/3456 7890"),
        ("Rechnung 2026 0815 4711", "2026 0815 4711"),
        # A date, an amount or a word after the number is not part of it.
        ("Rechnungs-Nr. 4711 12.03.2026", "4711"),
        ("Rechnungs-Nr. 4711 150,68 EUR", "4711"),
        ("Re-Nr. 4711 vom 12.03.2026", "4711"),
    ],
)
def test_an_invoice_number_takes_all_its_digit_groups(text, ref):
    (m,) = patterns.REF_MERGED.finditer(text)
    assert m.group().endswith(ref)


@pytest.mark.parametrize(
    "text,name",
    [
        ("Muster Beispiel GmbH", "Muster Beispiel GmbH"),
        ("Die Beträge wurden an die Muster health finance GmbH abgetreten.", "Muster health finance GmbH"),
        ("Zweigniederlassung der Muster MVZ Beispielstadt GmbH", "Muster MVZ Beispielstadt GmbH"),
        ("Im Auftrag von: MVZ Muster GmbH Beispielhof", "MVZ Muster GmbH"),
        ("Dr. Muster & Kollegen GmbH & Co. KG", "Dr. Muster & Kollegen GmbH & Co. KG"),
        ("Ktoinh. Muster factoring GmbH", "Muster factoring GmbH"),
        ("Kontoinhaber: Muster GmbH - Beispielbank AG Musterstadt", "Muster GmbH"),
        ("Rechtsform GmbH, Sitz Musterstadt", "GmbH"),
        ("Muster GmbH·Musterweg 1", "Muster GmbH"),
    ],
)
def test_a_legal_form_claims_the_company_name_before_it(text, name):
    assert patterns.ORG_NAME.search(text).group() == name


@pytest.mark.parametrize(
    "text,values",
    [
        ("IBAN: DE00 1234 5678 9012 3456 78 BIC MUSTDEFFXXX", ["DE00 1234 5678 9012 3456 78", "MUSTDEFFXXX"]),
        ("IBAN\nDE00123456789012345678", ["DE00123456789012345678"]),
        ("IBAN DE00123456789012345678\n01.02.20", ["DE00123456789012345678"]),  # stops at the line
        ("Konto-Nr.: 520 22 111", ["520 22 111"]),
        ("Steuernummer: DE123456789", ["DE123456789"]),
        ("IK: 123456789 UST-1dNr: DE 123456789", ["DE 123456789", "123456789"]),
        ("Steuernummer: 123/456/78901", ["123/456/78901"]),
        ("BSNR:12/3456789", ["12/3456789"]),
        ("Amtsgericht Musterstadt HRB-Nr. 12345", ["12345"]),
        ("Postfach 01.02.20", []),  # a date is no box number
        ("Konto bei der Musterbank", []),
        ("Bankverbindung: Musterbank", []),
    ],
)
def test_an_imprint_marker_claims_only_a_value_of_its_format(text, values):
    assert [m.group("value") for p in patterns.IMPRINT_VALUES for m in p.finditer(text)] == values
