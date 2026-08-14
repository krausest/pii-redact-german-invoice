"""Deterministic rule corner cases the code comments call out."""

from __future__ import annotations

from collections import namedtuple

from backend.classifiers.presidio import _redactable as _redactable_impl
from backend.models import Line
from backend.trace import Trace
from backend.rules import (
    BIRTH_LABEL,
    CONTACT,
    DE_PLZ_CITY,
    DE_STREET,
    GEB_DATE,
    IMPRINT,
    NAME_DATE,
    ORG_LEGAL,
    ORG_MEDICAL,
    PATIENT_NAME,
    SALUT,
    TITLE_NAME,
    harvest_names,
    item_table_indices,
    labeled_value_indices,
    line_matches_static_rule,
    mentions_name,
    static_rule_match,
)

# One OCR line off a sample invoice, verbatim: the patient's name and birthdate
# together, with "Max," and "geb." glued into "Max'geb". Presidio finds nothing
# usable in it — two PERSON spans of one capitalized token each ("Mustermann",
# "Max'geb"), which the caps guard drops, and the date as a PHONE_NUMBER, which
# _DOTTED_DATE drops. It is the deterministic rules that have to catch this line.
PATIENT_LINE = "Patient Mustermann, Max'geb. 30.09.1954"


def test_salutation_matches():
    assert SALUT.search("Herrn")
    assert SALUT.search("Frau Dr. Müller")
    assert not SALUT.search("Rechnung")


def test_de_plz_city_excludes_uppercase_noise():
    assert DE_PLZ_CITY.search("12345 Musterstadt")
    assert not DE_PLZ_CITY.search("15118 MID")  # all-caps spec noise, not a city


def test_de_plz_city_matches_the_glued_form():
    # A narrow address column prints postcode and city flush, and OCR returns
    # them as one token.
    assert DE_PLZ_CITY.search("12345Musterstadt")
    assert DE_PLZ_CITY.search("54321MUSTERSTADT")
    assert DE_PLZ_CITY.search("D-12345Musterhausen")
    assert line_matches_static_rule("12345Musterstadt")
    # ...without letting the position number back in through the missing space.
    assert not DE_PLZ_CITY.search("44/20101Massage")
    assert not DE_PLZ_CITY.search("15118MID")


def test_de_plz_city_ignores_a_postcode_inside_a_position_number():
    # A Heilmittel position number ends in five digits and is followed by its
    # Leistungstext, which is the ZIP+city shape exactly — 20101 is even a real
    # Hamburg postcode. Only the token boundary tells them apart.
    for text in (
        "44/20101 Massage (Teil-/Großmassage) auch",
        "49/21520 Naturmoor / Wärmepackung",
        "44-20101 Massage",
    ):
        assert not DE_PLZ_CITY.search(text), text
        assert not line_matches_static_rule(text), text
    # ...while a real address still matches, including the country-prefixed form
    # and a postcode following a house number.
    assert DE_PLZ_CITY.search("D-12345 Musterhausen")
    assert DE_PLZ_CITY.search("Musterstraße 7 - 12345 Musterhausen")


def test_de_plz_city_matches_the_all_caps_form():
    # A letterhead prints the city in capitals; four letters is what tells such a
    # city apart from the short all-caps noise a Leistungstext is full of.
    assert DE_PLZ_CITY.search("54321 MUSTERSTADT")
    assert DE_PLZ_CITY.search("12345 MUSTERHÖHE")  # umlauts in the all-caps class
    assert DE_PLZ_CITY.search("12345 Bad Homburg")
    assert DE_PLZ_CITY.search("12345 Baden-Baden")
    assert not DE_PLZ_CITY.search("15118 ABC")
    assert line_matches_static_rule("54321 MUSTERSTADT")


def test_presidio_imports_the_city_pattern_instead_of_restating_it():
    # The deterministic rules are supposed to restate what DE_ADDRESS already
    # fires on; written out twice, the two had drifted apart — presidio's copy
    # matched the "15118 MID" its own comment promised to keep out. This fails
    # loudly if the import is dropped for a literal again.
    from backend.classifiers.presidio import DE_PLZ_CITY as PRESIDIO_DE_PLZ_CITY

    assert PRESIDIO_DE_PLZ_CITY is DE_PLZ_CITY


def test_de_street_matches():
    assert DE_STREET.search("Musterstrasse 23")
    assert DE_STREET.search("Bahnhofweg 5a")


def test_de_street_matches_the_all_caps_form():
    # A letterhead or a form prints the address in capitals, and the house number
    # can be glued to the abbreviated suffix.
    assert DE_STREET.search("AUGSBURGERSTR.23")
    assert DE_STREET.search("MUSTERSTRASSE 23")
    assert DE_STREET.search("GOETHEALLEE 12a")
    assert line_matches_static_rule("AUGSBURGERSTR.23")


def test_de_street_does_not_match_invoice_vocabulary():
    # The leading capital is still required and the suffix must be a real street
    # suffix, so an all-caps or capitalized word plus a number is not an address.
    for text in ("Gesamtbetrag 23", "MwSt. 19", "Beleg 12", "RECHNUNG Nr. 2026-1234-001"):
        assert not DE_STREET.search(text), text


def test_static_rule_aggregate():
    assert line_matches_static_rule("Herr Max")
    assert line_matches_static_rule("Musterstrasse 23")
    assert not line_matches_static_rule("Behandlung Zahn")


def test_patient_label_with_name():
    assert PATIENT_NAME.search("Patient Mustermann, Max")
    assert PATIENT_NAME.search("Patientin Erika Muster")
    assert PATIENT_NAME.search("Pat.: Mustermann")
    assert PATIENT_NAME.search("Pat. M. Mustermann")
    # A patient *label* without a name is not PII — and neither is the word in a
    # Leistungstext.
    assert not PATIENT_NAME.search("Patient")
    assert not PATIENT_NAME.search("Aufklaerung des Patienten ueber die Risiken")


def test_person_labels_of_neighbouring_document_types():
    # Dental / hospital / insurance paper labels the same person differently.
    assert PATIENT_NAME.search("Versicherter Max Mustermann")
    assert PATIENT_NAME.search("Versicherte: Erika Muster")
    assert PATIENT_NAME.search("Mitglied: Mustermann, Max")
    assert PATIENT_NAME.search("Rechnungsempfänger: Max Muster")
    assert PATIENT_NAME.search("Zahlungspflichtiger Max Muster")
    # ...but the label mid-compound or mid-sentence is not a name.
    assert not PATIENT_NAME.search("Versichertennummer 123456")
    assert not PATIENT_NAME.search("Versicherte erhalten eine Kopie")
    assert not PATIENT_NAME.search("Mitgliedsbeitrag 12,00")


def test_titles_match_dipl_med():
    assert TITLE_NAME.search("Dipl.-Med. Max Muster")
    assert TITLE_NAME.search("Prof. Dr. med. Hans Muster")


def test_org_medical_matches_compounds():
    # \bPraxis\b never matches "Zahnarztpraxis" — the compound forms must.
    for text in (
        "Zahnarztpraxis Dr. Weber",
        "Gemeinschaftspraxis Muster & Weber",
        "Praxisgemeinschaft am Muster",
        "Tagesklinik Musterstadt",
        "Klinikum Musterstadt",
        "Städtisches Krankenhaus",
        "Gesundheitszentrum Mitte",
        "Dentallabor Muster GmbH",
        "Privatärztliche Verrechnungsstelle Muster GmbH",
        "Muster Krankenversicherung a.G.",
        "Sanitätshaus Beispiel",
    ):
        assert ORG_MEDICAL.search(text), text


def test_org_medical_leaves_leistungstext_compounds_alone():
    # Suffix wildcards only: a noun *starting* a compound is body text.
    for text in (
        "Laboruntersuchung von Blut",
        "Laborkosten gemäß GOÄ §10",
        "praxisübliche Vergütung",
        "Krankenhausaufenthalt vom 01.02.",
    ):
        assert not ORG_MEDICAL.search(text), text


def test_geb_abbreviation_only_counts_in_front_of_a_date():
    assert GEB_DATE.search("geb. 13.04.1954")
    assert GEB_DATE.search("Max'geb. 13.04.1954")  # OCR glued name and label
    assert GEB_DATE.search("geb.13.04.1954")
    assert GEB_DATE.search("geb. am 13.04.1954")
    # "Geb.Nr." is the fee-number column, whatever follows it.
    assert not GEB_DATE.search("Geb.Nr. 1234")
    assert not GEB_DATE.search("Geb.-Nr.")


def test_patient_line_with_name_and_birthdate():
    # Both halves of the line are caught, either one on its own being enough.
    assert PATIENT_NAME.search(PATIENT_LINE)
    assert GEB_DATE.search(PATIENT_LINE)
    assert line_matches_static_rule(PATIENT_LINE)


def test_org_legal_matches_company_suffixes():
    assert ORG_LEGAL.search("Muster VerrechnungsSysteme GmbH")
    assert ORG_LEGAL.search("Muster Dental UG")
    assert ORG_LEGAL.search("Sanitaetshaus e. K.")
    assert not ORG_LEGAL.search("Betrag")  # lowercase "ag" is not \bAG\b


def test_contact_matches_urls_mail_and_phone():
    assert CONTACT.search("www.dr-mueller-huber-muster.de")
    assert CONTACT.search("https://praxis-muster.de/kontakt")
    assert CONTACT.search("info@praxis-muster.de")
    assert CONTACT.search("praxis-muster.de")  # bare host, no scheme and no www.
    assert CONTACT.search("Telefon: 01234 123456")
    assert CONTACT.search("Fax 01234/12 34 57")
    assert not CONTACT.search("Faktor 2,30")


def test_bare_host_is_not_a_german_abbreviation():
    # German glues abbreviations with the same dot, and OCR keeps them glued to
    # the next word: the token then ends in something that reads as a TLD. A
    # hostname with no scheme, no "www." and no "@" in front of it is told apart
    # by being lower case and by the TLD ending the token.
    for text in (
        "Vollständige Untersuchung (Haut oder Stütz-Bew.org.oder Brust oder",
        "Untersuchung der Stütz-Bew.org.",
        "Beratung einschl.der Auslagen",
        "Leistung zzgl.der Sachkosten",
    ):
        assert not CONTACT.search(text), text


def test_imprint_matches_registry_and_bank_identifiers():
    assert IMPRINT.search("HRB 1234 Musterstadt")
    assert IMPRINT.search("IK: 12356784 - USt-IdNr: DE 123457767")
    assert IMPRINT.search("Postfach 12 12 - 12345 Musterstadt")
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


def test_static_rule_match_names_the_rule_and_quotes_the_match():
    # What the trace prints under a line: which pattern fired, and on what.
    assert static_rule_match("Herrn") == ("SALUT", "Herrn")
    assert static_rule_match("Musterstrasse 23") == ("DE_STREET", "Musterstrasse 23")
    assert static_rule_match("Internet: praxis-muster.de") == ("CONTACT", "praxis-muster.de")
    assert static_rule_match("Behandlung Zahn") is None


def _line(text, top=0, height=10):
    return Line(text=text, left=0, top=top, width=100, height=height)


def test_birthdate_same_row_two_columns():
    lines = [
        _line("Geburtstag", top=100, height=10),
        _line("01.02.1980", top=101, height=10),  # same row, different column
        _line("Rechnungsdatum 05.06.2024", top=200, height=10),
    ]
    assert labeled_value_indices(lines) == {1}


def test_birthdate_merged_line():
    lines = [_line("geboren am 1.2.1980", top=0, height=10)]
    assert labeled_value_indices(lines) == {0}


def test_birthdate_merged_line_abbreviated():
    # The label is neither spelled out nor its own line: only "geb." directly in
    # front of the date marks it.
    assert labeled_value_indices([_line(PATIENT_LINE)]) == {0}


def test_geb_nr_fee_column_not_a_birthdate():
    # "Geb.Nr." is a fee-number column, not "geboren/Geburt" — a treatment date
    # on its row must not be redacted.
    lines = [
        _line("Geb.Nr.", top=50, height=10),
        _line("11.03.2024", top=51, height=10),
    ]
    assert labeled_value_indices(lines) == set()


def test_labeled_identifier_merged_line():
    for text in (
        "Versichertennummer: A123456789",
        "Patienten-Nr. 12345",
        "Fallnummer 2024/12345",
        "Mitgliedsnummer 987654321",
        "Versicherungsschein-Nr.: 4 399 267 00",
    ):
        assert labeled_value_indices([_line(text)]) == {0}, text


def test_labeled_identifier_two_columns():
    lines = [
        _line("Aufnahme-Nr.", top=100, height=10),
        _line("2024-004711", top=101, height=10),  # same row, different column
        _line("580,66", top=200, height=10),
    ]
    assert labeled_value_indices(lines) == {1}


def test_labeled_identifier_bare_header_not_redacted():
    # A column header without a value is not PII, and an amount is not a value
    # (comma-grouped, under four plain digits).
    lines = [
        _line("Fall-Nr.", top=50, height=10),
        _line("Betrag", top=51, height=10),
        _line("774,22", top=52, height=10),
    ]
    assert labeled_value_indices(lines) == set()


def _col(text, top, left=200, width=100, height=10):
    return Line(text=text, left=left, top=top, width=width, height=height)


def test_star_marks_a_birth_date():
    # On German paperwork "*" in front of a date reads "geboren" — the one birth
    # marker that needs no word, which is why a form prints it where there is no
    # room for a label.
    for text in ("*16.03.1978", "* 16.03.1978", "Muster, Andrea *16.03.1978"):
        assert labeled_value_indices([_line(text, top=100)]) == {0}, text
    # ...and the name on such a line is there because of the date, so it is
    # harvested like any other person evidence.
    assert harvest_names("Muster, Andrea *13.03.1978") == {"Muster", "Andrea"}


def test_star_needs_the_date_directly_after_it():
    # A footnote marker introducing a sentence is not a birth mark, and a bare
    # treatment date stays visible.
    for text in ("* Leistungen ab 01.01.2024", "* siehe Hinweis vom 12.03.2024", "24.07.2026"):
        assert labeled_value_indices([_line(text, top=100)]) == set(), text


def test_abbreviated_birth_label_pairs_with_the_date():
    # "Geb.Dat." is unambiguous — "Gebührendatum" is not a word on an invoice —
    # so it counts as a birth label even though bare "geb." does not.
    for label in ("Geb.Dat.", "Geb.-Dat.:", "GebDat", "Geb. Dat."):
        lines = [_line(label, top=100), _col("13.03.1978", top=101)]
        assert labeled_value_indices(lines) == {1}, label


def test_bare_geb_is_still_the_fee_column():
    # The reason the bare abbreviation stays out: "Geb.Nr." is a fee number, and
    # the date on its row is a treatment date.
    for text in ("Geb.Nr.", "Gebühren", "Geb.-Nr. 3306"):
        assert not BIRTH_LABEL.search(text), text
    assert labeled_value_indices([_line("Geb.Nr.", top=100), _col("11.03.2024", top=101)]) == set()


def test_birth_label_heads_the_column_below_it():
    # A patient table prints "Geburtsdatum" once, at the top of the column, so no
    # label shares a row with any of the dates.
    lines = [
        _col("Geburtsdatum", top=100, width=120),
        _col("13.03.1978", top=130, left=205, width=90),
        _col("05.03.2011", top=160, left=205, width=90),
    ]
    assert labeled_value_indices(lines) == {1, 2}  # the header itself is not PII


def test_birth_header_claims_only_its_own_column():
    lines = [
        _col("Geburtsdatum", top=100, width=120),
        _col("11.03.2025", top=130, left=900, width=90),  # a different column
    ]
    assert labeled_value_indices(lines) == set()


def test_birth_header_stops_at_a_gap_and_at_a_non_value():
    far = [_col("Geburtsdatum", top=100, width=120), _col("11.03.2025", top=400, width=90)]
    assert labeled_value_indices(far) == set()
    interrupted = [
        _col("Geburtsdatum", top=100, width=120),
        _col("Rechnungsnummer", top=130, width=90),  # the column has ended
        _col("11.03.2025", top=160, width=90),
    ]
    assert labeled_value_indices(interrupted) == set()


def test_person_label_cell_pairs_with_the_name_column():
    # "Patient:" alone in its cell, the name beside it — separate OCR lines, so
    # PATIENT_NAME (same-line) cannot see it, and a bare "Wolf,Uwe" gives NER
    # nothing: this geometry is all there is.
    for label, value in (
        ("Patient:", "Wolf,Uwe"),
        ("Versicherte", "MUSTER, ANDREA"),
        ("Name:", "Max Mustermann"),
        ("Pat.", "Muster,Andrea"),
        ("Person", "Uwe Wolf"),
    ):
        lines = [
            _line(label, top=100),
            Line(text=value, left=200, top=101, width=100, height=10),
        ]
        assert labeled_value_indices(lines) == {1}, (label, value)


def test_person_label_in_a_sentence_is_not_a_label_cell():
    # The anchor is load-bearing: a Leistungstext mentioning a patient must not
    # turn whatever capitalized pair shares its row into a name.
    lines = [
        _line("Beratung des Patienten nach GOÄ", top=100),
        Line(text="Gonokokken Kultur", left=200, top=101, width=100, height=10),
    ]
    assert labeled_value_indices(lines) == set()


def test_person_label_needs_a_name_shaped_value():
    # A label cell beside an amount is not a name.
    lines = [
        _line("Patient:", top=100),
        Line(text="13,41", left=200, top=101, width=100, height=10),
    ]
    assert labeled_value_indices(lines) == set()


def test_person_label_cell_takes_a_modifier():
    # "Behandelte Person:" — a third of the sample invoices label the patient with
    # a participle in front of the noun, and the name beside it is then invisible
    # to everything else on the page.
    for label in ("Behandelte Person:", "Versicherte Person", "Zahlungspflichtige Person:"):
        lines = [
            _line(label, top=100),
            Line(text="Max Mustermann", left=200, top=101, width=100, height=10),
        ]
        assert labeled_value_indices(lines) == {1}, label


def test_a_modifier_must_inflect_like_an_adjective():
    # The -e/-er/-es/-en/-em ending is the whole guard: without it any cell ending
    # in the label noun would be a label and blacken its row.
    for label in ("Beratung Person", "Leistung Name", "Behandlung der Person"):
        lines = [
            _line(label, top=100),
            Line(text="Gonokokken Kultur", left=200, top=101, width=100, height=10),
        ]
        assert labeled_value_indices(lines) == set(), label


def test_label_cell_itself_is_never_a_value():
    # "Versicherte Person" is two name-shaped tokens, so the label cell matches the
    # value pattern as well. It is still a caption, not PII.
    assert labeled_value_indices([_line("Versicherte Person", top=100)]) == set()


def test_person_value_survives_a_decapitalized_half():
    # OCR reads the capital I of "Ioanna" as a lowercase l. The label beside the
    # cell is what makes believing the other half safe.
    lines = [
        _line("Behandelte Person:", top=100),
        Line(text="loanna Mustermann", left=200, top=101, width=100, height=10),
    ]
    assert labeled_value_indices(lines) == {1}
    # ...but two ordinary words are not a name, however close the label is.
    lines[1] = Line(text="eingehende beratung", left=200, top=101, width=100, height=10)
    assert labeled_value_indices(lines) == set()


def test_person_label_heads_the_column_below_it():
    # The other geometry: the label is a column header and the name runs beneath
    # it rather than beside it.
    lines = [
        _line("Patient:", top=100),
        _line("loanna Mustermann", top=115),
        _line("Rechnungsbetrag", top=300),  # too far below to be claimed
    ]
    assert labeled_value_indices(lines) == {1}


def test_the_persons_details_run_on_below_the_name():
    # A patient block prints the name beside the label and the birthdate under the
    # name — that date carries no birth label of its own, so the column walk from
    # the value cell is the only thing that sees it.
    lines = [
        _line("Behandelte Person:", top=100),
        Line(text="loanna Mustermann", left=200, top=101, width=100, height=10),
        Line(text="01.01.1990", left=200, top=115, width=100, height=10),
        Line(text="A123456789", left=200, top=129, width=100, height=10),
        Line(text="Rechnung", left=200, top=143, width=100, height=10),  # not a detail
    ]
    assert labeled_value_indices(lines) == {1, 2, 3}


def test_the_detail_column_does_not_eat_a_treatment_period():
    # A bare date pattern matches *inside* this line, which is why the detail
    # vocabulary is anchored to the whole cell.
    lines = [
        _line("Behandelte Person:", top=100),
        Line(text="Max Mustermann", left=200, top=101, width=100, height=10),
        Line(
            text="Behandlungszeitraum vom 15.04.2026 bis 28.04.2026",
            left=200, top=115, width=100, height=10,
        ),
    ]
    assert labeled_value_indices(lines) == {1}


def test_the_detail_column_stops_at_the_item_table():
    # A Leistungstext is two capitalized words, i.e. the shape of a name, so
    # nothing in the geometry could tell the walk where the invoice body starts.
    lines = [
        _line("Behandelte Person:", top=100),
        Line(text="Max Mustermann", left=200, top=101, width=100, height=10),
        Line(text="Eingehende Untersuchung", left=200, top=115, width=100, height=10),
    ]
    assert labeled_value_indices(lines) == {1, 2}  # without a table it is claimed
    assert labeled_value_indices(lines, table={2}) == {1}


def test_invoice_number_is_not_a_labeled_identifier():
    # The invoice number is the reference a redacted document is shared for.
    assert labeled_value_indices([_line("Rechnungs-Nr. 2026-0724-001")]) == set()


def test_surname_forename_birthdate_line():
    # The shape of an OCR line off a sample invoice: a patient row with no label
    # at all. NER returns only the forename (the surname is outside the PER
    # span), and the date has no birth label to pair with — this rule is all
    # there is.
    for text in (
        "Muster,Andrea 05.03.11",
        "Muster, Andrea 05.03.2011",
        "MUSTER, ANDREA 13.03.1978",  # the all-caps form a lab prints
        "Müller-Lüdenscheidt,Anna-Lena 1.1.90",
    ):
        assert NAME_DATE.search(text), text
        assert line_matches_static_rule(text), text


def test_surname_forename_birthdate_needs_both_halves():
    # A Leistungstext can hold two comma-joined capitalized nouns, and a bare date
    # in an item row is a treatment date — only the pair is evidence of a person.
    assert not NAME_DATE.search("Mikroskopie,Kultur")
    assert not NAME_DATE.search("Beratung, auch mittels Fernsprecher")
    assert not NAME_DATE.search("Zahlung bis zum 23.08.2026")
    assert not NAME_DATE.search("Summe, Betrag")


def test_surname_forename_birthdate_feeds_name_memory():
    # ...so the surname is redacted on a later bare mention, as for every other
    # kind of person evidence.
    names = harvest_names("Muster,Andrea 05.03.11")
    assert names == {"Muster", "Andrea"}
    assert mentions_name("Diagnose Muster", names)


def test_harvest_names_from_evidence_lines():
    assert "Mustermann" in harvest_names("Patient Mustermann, Max'geb. 30.09.1954")
    assert harvest_names("Max Mustermann, geboren am 15.01.1990") == {"Mustermann"}
    assert harvest_names("Sehr geehrter Herr Mustermann,") == {"Mustermann"}
    assert harvest_names("Dr. med. Weber") == {"Weber"}
    # No deterministic person evidence -> nothing harvested, however name-like.
    assert harvest_names("Max Mustermann") == set()
    # A lone salutation labels nobody.
    assert harvest_names("Herrn") == set()


def test_mentions_name_is_whole_word():
    names = {"Allgemein", "Mustermann"}
    assert mentions_name("Diagnose für Mustermann", names)
    assert not mentions_name("Allgemeine Hinweise", names)  # not the Dr.
    assert not mentions_name("anything", set())


def test_mentions_name_matches_across_casings_but_needs_a_capital():
    # One document prints the same person both ways — "Muster, Andrea" in the
    # patient row, "Andrea Muster" in the address block — so the memory may not
    # hold only the casing it met first.
    assert mentions_name("MUSTER, ANDREA", {"Muster"})
    assert mentions_name("Andrea Muster", {"MUSTER"})
    # ...but a lowercase occurrence is an ordinary German word, not the surname
    # ("Klein" the person vs "klein gedruckt"), which is why this is not IGNORECASE.
    assert not mentions_name("mustermann", {"Mustermann"})


def test_all_caps_evidence_line_does_not_harvest_its_label():
    # The stopwords are compared case-folded: harvesting "PATIENT" would go on to
    # redact every line mentioning a patient.
    assert harvest_names("PATIENT MUSTER, ANDREA 13.03.1978") == {"MUSTER", "ANDREA"}


# --- the item table --------------------------------------------------------- #
# _line() gives every line height 10, so the median height is 10 and two money
# rows belong to one table while the gap between them stays within 30px.


def test_item_table_spans_from_first_to_last_money_row():
    # The committed sample's layout in miniature: recipient block, a column
    # header, one item row, a wrapped description, the totals, then the footer.
    lines = [
        _line("Max Mustermann", top=100),
        _line("Musterstraße 7", top=120),
        _line("GNr Bezeichnung Betrag", top=200),  # header carries no amount
        _line("Beratung, auch mittels Fernsprecher", top=220),
        _line("4,66 €", top=220),
        _line("Folgerezept)", top=235),  # wrapped description, no amount
        _line("10,72 €", top=250),
        _line("Summe", top=252),
        _line("Bankverbindung:", top=400),
    ]
    idx = item_table_indices(lines)
    assert idx == {3, 4, 5, 6, 7}
    assert 5 in idx  # a line between two money rows is inside the table
    assert not {0, 1, 8} & idx  # the PII above and below it is not


def test_item_table_needs_more_than_one_money_row():
    # A lone amount is not a table.
    lines = [_line("Zahlbetrag", top=100), _line("195,18", top=100)]
    assert item_table_indices(lines) == set()


def test_stray_amount_does_not_stretch_the_band_over_the_recipient():
    # The failure the clustering exists to prevent: an amount printed above the
    # recipient block must form its own cluster, not a band reaching the table.
    lines = [
        _line("Rechnungsbetrag 195,18", top=100),
        _line("Max Mustermann", top=150),
        _line("Musterstraße 7", top=170),
        _line("4,66 €", top=400),
        _line("10,72 €", top=420),
    ]
    assert item_table_indices(lines) == {3, 4}


def test_item_table_of_a_page_without_amounts():
    assert item_table_indices([]) == set()
    assert item_table_indices([_line("Sehr geehrter Herr Mustermann,")]) == set()


def _redactable(results, line, tokens):
    """``_redactable`` minus the trace it reports its reasoning through — these
    tests are about the verdict. A non-collecting :class:`Trace` is the do-nothing
    one; the trace's *content* is pinned by
    :func:`test_redactable_reports_why_it_dropped_a_person` below."""
    return _redactable_impl(results, line, tokens, Trace())


def _result(entity_type, start, end):
    class R:
        pass

    r = R()
    r.entity_type = entity_type
    r.start = start
    r.end = end
    return r


def _person_result(start, end):
    return _result("PERSON", start, end)


# The structural subset of a spaCy Token that _redactable reads. Building them by
# hand keeps these tests model-free; the POS values are the ones de_core_news_lg
# actually produces for the lines they are named after.
Tok = namedtuple("Tok", "idx text pos_")


def _tokens(*pairs):
    """Tokens laid out left to right, one space apart: ("Max", "PROPN"), ..."""
    toks, idx = [], 0
    for text, pos in pairs:
        toks.append(Tok(idx, text, pos))
        idx += len(text) + 1
    return toks


def test_redactable_drops_single_token_person():
    line = "5.0016"
    assert not _redactable([_person_result(0, len(line))], line, _tokens(("5.0016", "NUM")))


def test_redactable_keeps_multi_token_person():
    line = "Max Mustermann"
    toks = _tokens(("Max", "PROPN"), ("Mustermann", "PROPN"))
    assert _redactable([_person_result(0, len(line))], line, toks)


def test_redactable_keeps_a_person_ocr_decapitalized():
    # "Ioanna Mustermann" read with the capital I as a lowercase l. The tagger
    # still calls both tokens proper nouns, and it is the better witness than the
    # pixel that got lost — the tags here are the ones de_core_news_lg produces.
    line = "loanna Mustermann"
    toks = _tokens(("loanna", "PROPN"), ("Mustermann", "PROPN"))
    assert _redactable([_person_result(0, len(line))], line, toks)


def test_redactable_whitelist_token_not_counted():
    line = "Anzahl Anz"  # both whitelisted -> not a person
    toks = _tokens(("Anzahl", "PROPN"), ("Anz", "PROPN"))
    assert not _redactable([_person_result(0, len(line))], line, toks)


def test_redactable_drops_person_whose_token_is_a_common_noun():
    # The reported false positive: German capitalizes nouns, so caps alone let a
    # Leistungstext through. The tagger calls "Orientierende" a NOUN.
    line = "Orientierende Testuntersuchg."
    toks = _tokens(("Orientierende", "NOUN"), ("Testuntersuchg", "PROPN"))
    assert not _redactable([_person_result(0, len(line) - 1)], line, toks)


def test_redactable_drops_person_on_coarse_tag_not_ner_tag():
    # "Cleed Agar" (a culture medium): "Cleed" is tag_=NE but pos_=ADV, which is
    # why the guard reads the coarse tag.
    line = "Cleed Agar"
    toks = _tokens(("Cleed", "ADV"), ("Agar", "PROPN"))
    assert not _redactable([_person_result(0, len(line))], line, toks)


def test_redactable_keeps_person_beside_a_common_noun():
    # A label in front of the name is not part of the PERSON span, and the two
    # name tokens still count.
    line = "Geschäftsführer: Dietmar Muster"
    toks = _tokens(
        ("Geschäftsführer", "NOUN"), (":", "PUNCT"), ("Dietmar", "PROPN"), ("Muster", "PROPN")
    )
    assert _redactable([_person_result(line.index("Dietmar"), len(line))], line, toks)


def test_redactable_counts_a_name_joined_by_a_comma():
    # "Muster,Andrea": NER returns only the forename, and the surname is a token
    # the tagger reads as a common noun — which is the normal case for German
    # surnames ("Bauer", "Jäger", "Wolf"). The comma is what pairs them.
    line = "Muster,Andrea"
    toks = [Tok(0, "Muster", "NOUN"), Tok(6, ",", "PUNCT"), Tok(7, "Andrea", "PROPN")]
    assert _redactable([_person_result(7, len(line))], line, toks)


def test_redactable_comma_neighbour_must_still_be_capitalized():
    line = "mikroskopisch,Andrea"
    toks = [Tok(0, "mikroskopisch", "ADJ"), Tok(13, ",", "PUNCT"), Tok(14, "Andrea", "PROPN")]
    assert not _redactable([_person_result(14, len(line))], line, toks)


def test_redactable_does_not_reach_past_a_plain_space():
    # "Muster Praxis ..." — only a comma pairs two names; an adjacent noun does
    # not, or every capitalized word beside a false PERSON would count.
    line = "Muster Praxis"
    toks = _tokens(("Muster", "PROPN"), ("Praxis", "NOUN"))
    assert not _redactable([_person_result(0, 8)], line, toks)


def _phone_result(line):
    return [_result("PHONE_NUMBER", 0, len(line))]


def test_redactable_ignores_phone_shaped_date():
    assert not _redactable(_phone_result("09.07.2026"), "09.07.2026", [])


def test_redactable_ignores_date_with_trailing_code_column():
    line = "12.12.15 51-61"  # a date the recognizer swallowed with the code beside it
    assert not _redactable(_phone_result(line), line, [])


def test_redactable_ignores_undelimited_digit_run():
    line = "2106315267"  # a lab/order number, not a phone number
    assert not _redactable(_phone_result(line), line, [])


def test_redactable_keeps_formatted_phone_number():
    line = "0231 000000- 000"
    assert _redactable(_phone_result(line), line, [])


def test_redactable_reports_why_it_dropped_a_person():
    # The guards are the half of the trace that explains a *missing* box, so the
    # reason has to reach the collector, not just the log.
    trace = Trace(collect=True)
    line = "Orientierende Testuntersuchg"
    toks = _tokens(("Orientierende", "NOUN"), ("Testuntersuchg", "PROPN"))
    assert not _redactable_impl([_person_result(0, len(line))], line, toks, trace)
    assert trace.collected == "      Ignoring PERSON with only 1 name token(s)"
