"""The regex vocabulary of German invoices, one topic per section.

Naming: ``X_LABEL`` finds a label anywhere on a line, ``X_LABEL_CELL`` a label
alone in its OCR cell, ``X_VALUE`` a cell holding nothing but a value. Any other
name is a rule complete on one line. Why a pattern looks the way it does is pinned
by ``PATTERN_CASES`` in ``tests/test_rules_patterns.py``.
"""

from __future__ import annotations

import re

# --- dates ------------------------------------------------------------------- #
DATE_RE = re.compile(r"\b\d{1,2}\.\s?\d{1,2}\.\s?\d{2,4}\b")
# A cell holding one date and nothing else.
DATE_VALUE = re.compile(rf"^\s*{DATE_RE.pattern}\s*$")

# A date or an amount is never an identifier: "12.03.2026", "1.234,56".
_NOT_DATE_OR_MONEY = (
    r"(?!\d{1,2}\.\s?\d{1,2}\.\s?\d{2,4}(?![\d,]))(?!\d{1,3}(?:\.\d{3})*,\d{2}(?!\d))"
)

# --- persons ----------------------------------------------------------------- #
# "Herrn", "Frau", "Familie": the recipient block.
SALUT = re.compile(r"\b(?:Herrn?|Frau|Fräulein|Frl|Familie|Fam|Eheleute)\b")

# One name token, capitalized or all caps, optionally double ("Muster-Beispiel").
_NAME_PART = r"[A-ZÄÖÜ][A-ZÄÖÜa-zäöüß]+(?:-[A-ZÄÖÜ][A-ZÄÖÜa-zäöüß]+)?"
# The same with its capital possibly lost to OCR ("loanna" for "Ioanna").
_NAME_PART_OCR = r"[A-ZÄÖÜa-zäöüß][A-ZÄÖÜa-zäöüß]{2,}(?:-[A-ZÄÖÜa-zäöüß][A-ZÄÖÜa-zäöüß]+)?"
# "Andrea Muster" or "Muster, Andrea".
_NAME_SEP = r"(?:\s*,\s*|\s+)"

_PERSON_LABEL = (
    r"(?:Pat(?:ient(?:in|en)?)?"
    r"|Versicherte[rn]?"
    r"|Mitglied"
    r"|Person"
    r"|Name"
    r"|Rechnungsempfänger(?:in)?"
    r"|Zahlungspflichtige[rn]?)"
)

# A person label and the name after it: "Pat.: A. Muster".
PATIENT_NAME = re.compile(
    rf"\b{_PERSON_LABEL}\b\.?\s*:?\s*"
    r"(?:[A-ZÄÖÜ]\.\s*)*"  # initials
    rf"{_NAME_PART}"
)
# "Patient:" or "Behandelte Person:" alone in its cell; a modifier must inflect.
PERSON_LABEL_CELL = re.compile(
    rf"^\s*(?:[A-ZÄÖÜ][a-zäöüß]+e[rnsm]?\s+)?{_PERSON_LABEL}\s*\.?\s*:?\s*$"
)
# A cell of two name tokens, at least one with its capital intact.
NAME_VALUE = re.compile(
    rf"^\s*(?:{_NAME_PART}{_NAME_SEP}{_NAME_PART_OCR}"
    rf"|{_NAME_PART_OCR}{_NAME_SEP}{_NAME_PART})\s*$"
)
# Title(s) and a name: "Prof. Dr. med. Andrea Muster", "DR. MED. ANDREA MUSTER".
TITLE_NAME = re.compile(
    r"\b(?i:(?:Prof|Priv\.-Doz|Dr(?:es)?|med|dent|vet|univ|habil|Dipl\.-?Med)\.\s*)+"
    r"(?:[A-ZÄÖÜ]\.\s*)*"  # initials
    r"[A-ZÄÖÜ](?:[a-zäöüß]+|[A-ZÄÖÜß]+\b)"
)
# Surname, forename and birthdate as one unlabeled row: "Muster,Andrea 01.02.80".
NAME_DATE = re.compile(rf"{_NAME_PART}\s*,\s*{_NAME_PART}\s+{DATE_RE.pattern}")

# --- birthdates -------------------------------------------------------------- #
# "Geburtsdatum", "geboren", "Geb.Dat." — but not a bare "Geb." (Gebühren).
BIRTH_LABEL = re.compile(r"(?i)geburt|geboren|geb[.\s-]*dat")
BIRTH_LABEL_CELL = re.compile(r"(?i)^\s*(?:geburts(?:datum|tag)|geb[.\s-]*dat(?:um)?)\.?\s*:?\s*$")
# "geb. 01.02.1980": the abbreviation directly before a date.
GEB_DATE = re.compile(rf"(?i)\bgeb\.?\s*(?:am\s+)?{DATE_RE.pattern}")
# "*01.02.1980": the star reads "geboren".
STAR_DATE = re.compile(rf"\*\s*{DATE_RE.pattern}")
BIRTH_MARK = re.compile(rf"{GEB_DATE.pattern}|{STAR_DATE.pattern}")

# --- addresses --------------------------------------------------------------- #
# "Musterstraße 1", "MUSTERSTR.23", "Musterer Straße 12" (OCR reads ß as B).
_STREET_WORD = r"stra(?:ße|sse|be)|str|platz|gasse|allee"
DE_STREET = re.compile(
    r"\b[A-ZÄÖÜ][A-ZÄÖÜa-zäöüß.\-]+"
    rf"(?:(?i:{_STREET_WORD}|weg|ring|damm)|\s+(?=[A-ZÄÖÜ])(?i:{_STREET_WORD})\b)"
    r"\.?\s*\d+[a-zA-Z]?\b"
)
# A city, capitalized or in caps of four letters or more.
_CITY = r"(?:[A-ZÄÖÜ][a-zäöüß]{2,}|[A-ZÄÖÜ]{4,})"
# The postcode starts its token: not the tail of "44/20101" or "44-20101".
_NOT_MID_TOKEN = r"(?<![\d/])(?<!\d-)"
# "12345 Musterstadt", "12345Musterstadt", "D-12345 Musterstadt".
DE_PLZ_CITY = re.compile(rf"{_NOT_MID_TOKEN}\b\d{{5}}\s*{_CITY}(?:[ \-]{_CITY})?\b")
# A cell holding only postcode and city, and one shaped like a street above it.
CITY_CELL = re.compile(rf"^\s*(?:D\s?-\s?)?{DE_PLZ_CITY.pattern}\s*$")
STREET_CELL = re.compile(
    r"^\s*[A-ZÄÖÜ][A-ZÄÖÜa-zäöüß.\-]*(?:\s+[A-ZÄÖÜa-zäöüß.\-]+){0,4}"
    r"\s*\d{1,4}\s?[a-zA-Z]?(?:\s*[-/–]\s*\d{1,4}\s?[a-zA-Z]?)?\s*$"
)

# --- patient identifiers ----------------------------------------------------- #
# "Nr", "Nummer", or "Nr" damaged by OCR ("Nz").
_NR = r"(?:Nr|Nummer|(?-i:N[a-z]))"
# "Versicherten-Nr.", "Fallnummer", "Pat.-Nr:".
ID_LABEL = re.compile(
    r"(?i)\b(?:Versicherten|Versicherungs(?:schein)?|Patienten|Pat\.?"
    r"|Fall|Aufnahme|Mitglieds?|Vertrags)"
    rf"[-\s.]*{_NR}\b"
)
# Four digits or more, maybe grouped or led by a letter: "A123456789".
ID_VALUE = re.compile(rf"(?<![\d./-]){_NOT_DATE_OR_MONEY}\b[A-Z]?\d(?:[ ./-]?\d){{3,}}\b")

# --- invoice references ------------------------------------------------------ #
# Invoice, receipt and customer number: "Rg.-Nr.", "Kd.-Nr.", "BFS-Nr.".
_REF = rf"(?:Rechnungs?|Rechn|Re|Rg|BFS|Beleg|Kunden|Kd)[-\s.]*{_NR}"
# A token holding a digit: "248", "AB-12-3456".
_REF_TOKEN = rf"(?<![^\s:]){_NOT_DATE_OR_MONEY}(?=[^\s\d]*\d)[A-Za-z0-9][\w/.:-]*"
REF_LABEL_CELL = re.compile(rf"(?i)^\s*{_REF}\.?\s*:?\s*$")
REF_VALUE = re.compile(rf"^\s*{_REF_TOKEN}\s*$")
# "Rechnung 2026-0815" or "Nummer: 24/0815" at the line's start, three digits at least.
_REF_TITLE = rf"^\s*(?:Rechnung(?:\s*:)?\s+|Nummer\s*:\s*)(?=(?:[^\s\d]*\d){{3}}){_REF_TOKEN}"
# Label and number on one line; OCR may read the label's dot as a comma.
REF_MERGED = re.compile(rf"(?i)\b{_REF}\b[.,]?\s*:?\s*{_REF_TOKEN}|{_REF_TITLE}")

# --- sender identifiers ------------------------------------------------------ #
# "IK", "IK-Zeichen", or "IK Musterstelle" naming its holder.
_IK = r"(?-i:\bIK)(?:[-\s.]?(?:Nr|Nummer|Zeichen))?\.?(?:\s+[A-Za-zÄÖÜäöüß]+){0,3}"
_UST_ID = r"USt[-.\s]*Id"
# "Steuernummer:", "USt-IdNr.:", "LANR:" closing its cell, maybe after its holder.
SENDER_LABEL_CELL = re.compile(
    rf"(?i)(?:^|,)\s*(?:Steuer[-\s]?(?:nummer|Nr)|{_UST_ID}(?:[-.\s]?(?:Nr|Nummer))?"
    rf"|{_IK}|LANR|BSNR|Vertragsarzt[-\s]?(?:nummer|Nr))\.?\s*:?\s*$"
)
# A reference token, or digits in groups led by a country code: "DE 123 456 789".
SENDER_VALUE = re.compile(
    rf"{REF_VALUE.pattern}|^\s*(?:[A-Z]{{2}}\s?)?{_NOT_DATE_OR_MONEY}\d(?:[ /.-]?\d){{4,}}\s*$"
)
# A tax number in slash groups, whatever its label: "123/456/78901".
TAX_NUMBER = re.compile(r"(?<![\d/])\d{2,3}/\d{3,4}/\d{4,5}(?!\d)")
# Registry and bank identifiers of the footer imprint.
IMPRINT = re.compile(
    r"(?i)\bHR[AB]\s*\d|(?-i:\b(?:GnR|VR|PR)\s?\d)|\w*register(?:nummer|[-\s.]*Nr)\b"
    rf"|\b(?:{_UST_ID}|Umsatzsteuer[-.\s]*Id)"
    r"|\bSteuer[\-\s]?(?:nummer|Nr)"
    rf"|{_IK}\s*:?\s*\d"
    r"|\b(?:LANR|LAN\-Nr|BSNR|IBAN|BIC|BLZ)\b"
    r"|\bBankverbindung\b|\bKonto(?:\-?Nr)?\b|\bPostfach\b"
)
# A legal form: "GmbH", "e. K.".
ORG_LEGAL = re.compile(
    r"\b(?:g?GmbH|mbH|UG|AG|KG|OHG|GbR|PartG(?:mbB)?|Ltd|Inc)\b|\be\.\s?[KV]\."
)

# --- contact ----------------------------------------------------------------- #
# URL, e-mail, or a bare lower-case host whose TLD ends the token.
CONTACT = re.compile(
    r"(?i)\bhttps?://\S+"
    r"|\bwww\.[\w\-]+(?:\.[\w\-]+)+"
    r"|(?-i:\b[a-z\d][a-z\d\-]+(?:\.[a-z\d\-]+)*\.(?:de|com|net|org|eu|at|ch))(?![\w.\-])"
    r"|[\w.\-+]+@[\w\-]+(?:\.[\w\-]+)+"
)
# Six digits or more; OCR reads a printed separator bar as "|".
_PHONE_DIGITS = r"(?:[\s()/+\-.|]*\d){6,}"
# Behind a phone label, or with a DACH country code ("+49", "0043").
PHONE = re.compile(
    r"(?i)\b(?:Tel(?:efon)?|Telefax|Fax|Mobil|Handy|Fon)\b\.?\s*(?:Nr\.?)?\s*:?\s*"
    rf"(?=[(+\d]){_PHONE_DIGITS}"
    rf"|(?<![\w+])(?<!\d\s)(?:\+|00)\s?(?:49|43|41){_PHONE_DIGITS}"
)
PHONE_LABEL_CELL = re.compile(r"(?i)^\s*(?:Tel(?:efon)?|Telefax|Fax|Mobil)\.?\s*:?\s*$")
PHONE_VALUE = re.compile(r"^\s*[(+]?\d(?:[\s()/\-.|]*\d){5,}\s*$")

# --- what stays readable ----------------------------------------------------- #
# Invoice and treatment dates; "Geburtsdatum" does not match.
_DATE_KEEP = r"(?:Rechnungs|Behandlungs|Leistungs|Ausstellungs)?datum|Behandlungs(?:zeitraum|tag)"
DATE_KEEP_LABEL = re.compile(rf"(?i)\b(?:{_DATE_KEEP})\b")
DATE_KEEP_CELL = re.compile(rf"(?i)^\s*(?:{_DATE_KEEP})\s*:?\s*$")
# "Rechnungsdatum: 12.03.2026" or a date cell, also "vom 15.04.2026 bis 28.04.2026".
DATE_KEEP_VALUE = re.compile(
    rf"(?i)^\s*(?:(?:{_DATE_KEEP})\s*:?\s*)?(?:vom\s+)?{DATE_RE.pattern}"
    rf"(?:\s*(?:-|–|bis)\s*{DATE_RE.pattern})?\s*$"
)
# "Diagnose(n):", "ICD-10".
_DIAG = r"(?:Diagnose(?:n|\(n\))?|ICD(?:-?10)?)"
DIAG_LABEL = re.compile(rf"(?i)^\s*{_DIAG}\b")
DIAG_LABEL_CELL = re.compile(rf"(?i)^\s*{_DIAG}\s*:?\s*$")
# The clearing house's name: "Muster Verrechnungsstelle", "Rechenzentrum".
PVS_NAME = re.compile(
    r"(?i)\w*verrechnung\w*|\bAbrechnungs(?:stelle|gesellschaft|zentrum)\w*|\bPVS\b|\bRechenzentrum\b"
)
# Specialty words, and the glue allowed between them: "Facharzt für Orthopädie".
SPECIALTY = re.compile(
    r"(?i)\b(?:\w*ärzt\w*|\w*arzt\w*|\w*(?:logie|logisch|medizin|medizinisch|chirurgie"
    r"|pädie|iatrie|heilkunde|therapie|pathie|diagnostik)\w*"
    r"|Praxis|Privatpraxis|Gemeinschaftspraxis|Innere|Allgemein\w*|Zahn\w*|Kiefer\w*"
    r"|ambulante[nr]?|Operationen|Akupunktur|Naturheilverfahren|Homöopathie)\b"
)
SPECIALTY_GLUE = re.compile(r"(?i)\b(?:für|und|u|sowie|der|des|die|im|in)\b|[\W\d_]")
