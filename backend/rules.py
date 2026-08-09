"""Deterministic redaction rules the NER/NLP stack misses.

These regexes and the spatial birthdate matcher are applied uniformly in
:func:`backend.pipeline.RedactionPipeline.compute_boxes` *before* the model-based
classifier runs, so a box exists whether or not the model saw anything. Running
the German street / ZIP+city patterns here is behavior-preserving for Presidio —
its DE_ADDRESS recognizer uses the same regexes at score 0.7/0.6 against a 0.4
threshold, so it already always fires on those matches — but it is what makes the
address block's coverage a property of *these* patterns rather than of whichever
model is plugged in: a zero-shot "address" label drops the standalone ZIP+city
line of a recipient block, and that would leave it visible.

The sender-identity patterns (``ORG_LEGAL`` / ``CONTACT`` / ``IMPRINT``) are the
one group that *adds* detections rather than restating what a classifier already
finds: NER treats a clearing house or a bank as an ORGANIZATION, which is not a
PII entity, so nothing ever flagged the letterhead or the footer imprint.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from statistics import median

from backend.models import Line

# Anrede: any line containing a salutation word is redacted — lone ("Herrn" above
# the address) or with a name ("Herr Mustermann", where NER only tags the single
# token "Mustermann", which the PERSON guard drops). A lone salutation carries no
# information, so over-redacting it is harmless and needs just one regex.
SALUT = re.compile(r"\b(?:Herrn?|Frau|Fräulein|Frl|Familie|Fam|Eheleute)\b")

# "Patient Mustermann, Max", "Pat.: Erika Muster" — a person label followed by a
# capitalized name. Same reason as SALUT: NER tags the surname and the forename
# as two *separate* PERSON spans of one token each ("Mustermann" / "Max"), and the
# PERSON guard drops both. The following capitalized token is required — a bare
# "Patient" in a Leistungstext is not PII. The label alternation carries the
# labels of the neighbouring document types too (dental, hospital, insurance):
# "Versicherte(r)", "Mitglied", "Rechnungsempfänger" mark the same person the
# same way. "Versicherte" does not fire inside "Versichertennummer" (no word
# boundary mid-compound) — labeled identifiers are a separate matcher.
# A name-shaped token, shared by every rule below that has to recognize one:
# capitalized or all caps (a lab prints "MUSTER, ANDREA"), with an optional
# second half for double names ("Müller-Lüdenscheidt").
_NAME_PART = r"[A-ZÄÖÜ][A-ZÄÖÜa-zäöüß]+(?:-[A-ZÄÖÜ][A-ZÄÖÜa-zäöüß]+)?"

# The label vocabulary on its own — used both glued to a name (below) and, as a
# cell of its own, to find the name in the *next column* (see PERSON_LABEL_CELL).
_PERSON_LABEL = (
    r"(?:Pat(?:ient(?:in|en)?)?"  # "Patient", "Patientin", "Pat.:"
    r"|Versicherte[rn]?"  # "Versicherter Max Muster"
    r"|Mitglied"
    r"|Person"
    r"|Name"
    r"|Rechnungsempfänger(?:in)?"
    r"|Zahlungspflichtige[rn]?)"
)

PATIENT_NAME = re.compile(
    r"\b" + _PERSON_LABEL + r"\b\.?\s*:?\s*"
    r"(?:[A-ZÄÖÜ]\.\s*)*"  # optional initials: "Pat. M. Mustermann"
    + _NAME_PART
)

# The same label standing *alone in its cell* ("Patient:", "Versicherte"), which
# is what makes the neighbouring column a name — see LABELED_IDS. Anchored on
# purpose: a Leistungstext that merely mentions "des Patienten" is a sentence, and
# using it as a label would redact whatever capitalized pair sits beside it.
#
# The optional leading modifier is the spelling a third of the sample invoices
# use — "Behandelte Person:", "Versicherte Person", "Zahlungspflichtige Person:".
# It has to inflect like a German adjective or participle (-e/-er/-es/-en/-em),
# and that ending is the whole guard: it keeps a cell that merely *ends* in the
# label noun ("Beratung Person", "Leistung Name") from becoming a label and
# blackening whatever sits beside it.
PERSON_LABEL_CELL = re.compile(
    r"^\s*(?:[A-ZÄÖÜ][a-zäöüß]+e[rnsm]?\s+)?" + _PERSON_LABEL + r"\s*\.?\s*:?\s*$"
)

# Surname and forename are written spaced or comma-joined: "Max Mustermann",
# "Muster, Andrea", "MUSTER,ANDREA".
_NAME_SEP = r"(?:\s*,\s*|\s+)"

# A name token whose capital OCR destroyed: "Ioanna" comes back as "loanna" and
# "Ilona" as "llona" — a capital I read as a lowercase l. Deliberately *not* a
# confusion table (l/I, 0/O, rn/m): the general statement is that one half of a
# name may have lost its case, and what makes believing that safe is the label
# beside the cell, never the shape of the damage.
_NAME_PART_OCR = r"[A-ZÄÖÜa-zäöüß][A-ZÄÖÜa-zäöüß]{2,}(?:-[A-ZÄÖÜa-zäöüß][A-ZÄÖÜa-zäöüß]+)?"

# The value that pairs with the label: the *whole cell* is two name tokens, of
# which at least one kept its capital. Two are required — a single capitalized
# word beside a label is too weak on its own — and both further halves are
# load-bearing. Anchoring mirrors the label ("nothing but a name" beside "nothing
# but a label") and is what stops the column walks in labeled_value_indices from
# running into a wrapped Leistungstext; requiring one intact capital is what stops
# two ordinary German words from qualifying. In isolation this still matches
# things that are not names ("eingehende Beratung") — it is never consulted in
# isolation.
NAME_VALUE = re.compile(
    rf"^\s*(?:{_NAME_PART}{_NAME_SEP}{_NAME_PART_OCR}"
    rf"|{_NAME_PART_OCR}{_NAME_SEP}{_NAME_PART})\s*$"
)

# Academic/medical title(s) followed by a capitalized name ("Dr. Weber",
# "Prof. Dr. med. Hans Müller", "Dr. Dr. Daphne Schlegel-Lippert"). The NER
# model is unreliable around titles — it misses the name entirely after a
# doubled "Dr. Dr.", and for "Dr. Weber" tags only the single token the PERSON
# guard drops — while a title is by itself strong evidence of a person.
TITLE_NAME = re.compile(
    r"\b(?:(?:Prof|Priv\.-Doz|Dr(?:es)?|med|dent|vet|univ|habil|Dipl\.-?Med)\.\s*)+"  # title(s)
    r"(?:[A-ZÄÖÜ]\.\s*)*"  # optional initials: "Dr. A. Meier"
    r"[A-ZÄÖÜ][a-zäöüß]+"
)

# German street: "<Street>strasse 23", and the all-caps form a letterhead or a
# form prints ("MUSTERSTR.23"). The name part therefore allows upper case
# throughout and the suffix is matched case-insensitively — an initial capital
# plus a street suffix plus a house number is what identifies the line, not its
# case. Only the suffix is `(?i:...)`, so the leading capital is still required
# and a lowercase word in running text cannot match.
DE_STREET = re.compile(
    r"\b[A-ZÄÖÜ][A-ZÄÖÜa-zäöüß.\-]+"
    r"(?i:stra(?:ße|sse)|str|weg|platz|gasse|allee|ring|damm)\.?\s*\d+[a-zA-Z]?\b"
)
# German ZIP + city: "12345 Musterstadt", "54321 MUSTERSTADT". A city is written
# either capitalized ("Musterstadt", "Ulm") or all caps, and the two need
# different floors: the capitalized form is already distinctive at three letters,
# while an all-caps run that short is spec noise a Leistungstext is full of
# ("15118 MID"). Four is what separates "MID" from a real all-caps city — at the
# price of ULM, HOF and AUE, which no sample has ever printed that way.
_CITY = r"(?:[A-ZÄÖÜ][a-zäöüß]{2,}|[A-ZÄÖÜ]{4,})"

# The postcode has to *start* a token. A Heilmittel position number ends in five
# digits and is followed by its Leistungstext — "44/20101 Massage",
# "49/21520 Naturmoor" — which is the ZIP+city shape exactly, down to 20101 being
# a real Hamburg postcode. Nothing about the number or the word can tell them
# apart; what can is that the digits there are the tail of a larger token.
# The hyphen is admitted after a letter, for the country-prefixed "D-12345
# Musterhausen", and refused after a digit, for the "44-20101" spelling.
_NOT_MID_TOKEN = r"(?<![\d/])(?<!\d-)"
# The space between postcode and city is optional: a narrow address column prints
# them flush and OCR returns "12345Musterstadt" as one token. This widens nothing —
# "12345 Stück" already matched with the space, so the glued spelling is the same
# shape, not a new one — while the token boundary above still keeps the tail of a
# position number out.
DE_PLZ_CITY = re.compile(rf"{_NOT_MID_TOKEN}\b\d{{5}}\s*{_CITY}(?:[ \-]{_CITY})?\b")

# Date of birth: the "Geburtstag/Geburtsdatum/geboren" label and the date sit in
# different columns, so they are separate OCR lines — matched spatially below.
# The *bare* abbreviation "geb." stays out: it also means "Gebühren" ("Geb.Nr."
# fee-number column), and taking it as a birth label would redact treatment dates.
# "Geb.Dat." is a different matter — "Gebührendatum" is not a word on an invoice,
# so the abbreviation is unambiguous once "Dat" follows it. The separator class
# holds only punctuation and space, so "Gebühren Datum" and "Geb.Nr. 5 Datum"
# cannot bridge it.
BIRTH_LABEL = re.compile(r"(?i)geburt|geboren|geb[.\s-]*dat")
DATE_RE = re.compile(r"\b\d{1,2}\.\s?\d{1,2}\.\s?\d{2,4}\b")

# The same label standing alone in its cell, which is what makes the column
# *below* it a column of birthdates (see LABELED_IDS and the header pass in
# labeled_value_indices).
BIRTH_LABEL_CELL = re.compile(r"(?i)^\s*(?:geburts(?:datum|tag)|geb[.\s-]*dat(?:um)?)\.?\s*:?\s*$")

# The abbreviation BIRTH_LABEL deliberately leaves out, but only where it cannot
# be read as "Gebühren": *directly* in front of a date. A fee number is
# "Geb.Nr. 5" or "Geb.-Nr. 3306", never "geb. 13.08.1964". Nothing anchors this at
# a word start beyond \b, because OCR routinely glues the abbreviation to the name
# in front of it — "Patient Mustermann, Max'geb. 13.08.1964" is one OCR line.
GEB_DATE = re.compile(r"(?i)\bgeb\.?\s*(?:am\s+)?" + DATE_RE.pattern)

# The genealogical birth mark: "*13.03.1975", "* 13.03.1975". On German
# paperwork an asterisk in front of a date reads "geboren", and it is the one
# birth marker that needs no word at all — which is why a form prints it where
# there is no room for a label. The date must follow the star directly (only
# whitespace between), so a footnote marker introducing a sentence does not match.
STAR_DATE = re.compile(r"\*\s*" + DATE_RE.pattern)

# Same-line evidence that a date is a *birth* date rather than a treatment date:
# the abbreviation glued to it, or the star. Both are "merged" forms — the label
# and its value in one OCR line — so they share the one slot for that in
# LABELED_IDS, and both count as name evidence for the same reason: a name on the
# line is there *because* the birthdate is.
BIRTH_MARK = re.compile(GEB_DATE.pattern + r"|" + STAR_DATE.pattern)

# "Muster,Andrea 05.03.11" — surname, forename and birthdate as one OCR line
# carrying no label of any kind, the way a patient table row is written. Nothing
# else can see this line: NER tags only the forename (the surname stays *outside*
# the PER span, so the PERSON guard drops the lone token it does return), and the
# date has no "geb." or "Geburtsdatum" to pair with, so neither GEB_DATE nor the
# spatial matcher reaches it.
#
# Both halves are required. "Muster,Andrea" on its own is a shape a Leistungstext
# also takes ("Mikroskopie,Kultur"), and a bare date is far more often a treatment
# date than a birthdate — it is the *pair* that is unambiguous. Double surnames
# are allowed on either side ("Müller-Lüdenscheidt,Anna-Lena"), and either name
# may be all caps, which is how a lab prints the row ("MUSTER, ANDREA 26.06.75").
NAME_DATE = re.compile(_NAME_PART + r"\s*,\s*" + _NAME_PART + r"\s+" + DATE_RE.pattern)

# Patient-side identifier labels: the numbers that tie the paper to a person or a
# treatment episode, across the neighbouring document types (dental, hospital,
# insurance). ``[-\s.]*`` also covers the closed compound ("Fallnummer") and the
# hyphenated/abbreviated forms ("Fall-Nr.", "Pat.-Nr:"). Sender-side identifiers
# (IK, LANR, BSNR, Steuer-Nr, ...) live in IMPRINT; ``Rechnungs-Nr`` is
# deliberately absent from *both* — the invoice number is the reference a
# redacted document is usually shared for.
ID_LABEL = re.compile(
    r"(?i)\b(?:Versicherten|Versicherungs(?:schein)?|Patienten|Pat\.?"
    r"|Fall|Aufnahme|Mitglieds?|Vertrags)"
    r"[-\s.]*(?:Nr|Nummer)\b"
)
# An identifier value: at least four digits, optionally grouped ("4 399 267 00"),
# optionally led by a letter (a KVNR is "A123456789").
ID_VALUE = re.compile(r"\b[A-Z]?\d(?:[ ./-]?\d){3,}\b")

# --- sender identity ------------------------------------------------------- #
# The three below identify the *sender* (practice, clearing house, bank) rather
# than the patient. They are page-wide because the letterhead and the imprint sit
# at opposite ends of the page and neither is reliably inside a region band.
# Each is deliberately restricted to markers that cannot occur in a GOÄ
# Leistungstext — see the negative cases in tests/test_rules.py.

# Legal form. Unambiguous on an invoice; a bare noun like "Zentrum" or "Labor" is
# not, so those live in ORG_MEDICAL below and are only used as a region anchor.
ORG_LEGAL = re.compile(
    r"\b(?:g?GmbH|mbH|UG|AG|KG|OHG|GbR|PartG(?:mbB)?|Ltd|Inc)\b|\be\.\s?[KV]\."
)

# Contact details: URL (with scheme, "www." or a bare host on a common TLD),
# email, and a phone/fax label followed by enough digits to be a number.
#
# The bare host — no scheme, no "www.", no "@" in front of it — is the one form
# with nothing but the dot to go on, and German writes abbreviations the same
# way: "Stütz-Bew.org.oder", "einschl.der Kosten". Two things tell a hostname
# from an abbreviation chain, and both are needed:
#   * it is **lower case** (`(?-i:...)`, against the rest of the pattern's
#     `(?i)`) — a German abbreviation shortens a capitalized noun, "Bew.org";
#   * the TLD **ends the token** — "einschl.der" would otherwise match its first
#     six letters as a host, and "Bew.org.oder" its middle.
# The cost is a letterhead printing a capitalized bare host ("Musterpraxis.de")
# or ending a sentence with one; in the sample corpus every bare host also
# carried a "www." or an "@" on the same line, so neither has ever been the only
# evidence.
CONTACT = re.compile(
    r"(?i)\bhttps?://\S+"
    r"|\bwww\.[\w\-]+(?:\.[\w\-]+)+"
    r"|(?-i:\b[a-z\d][a-z\d\-]+(?:\.[a-z\d\-]+)*\.(?:de|com|net|org|eu|at|ch))(?![\w.\-])"
    r"|[\w.\-+]+@[\w\-]+(?:\.[\w\-]+)+"
    r"|\b(?:Tel(?:efon)?|Telefax|Fax|Mobil)\b\.?\s*:?\s*(?=[\d\s()/+\-]{6,})[\d(+]"
)

# Registry / banking identifiers — the footer imprint block.
IMPRINT = re.compile(
    r"(?i)\bHR[AB]\s*\d"
    r"|\b(?:USt|Umsatzsteuer)[\-.\s]?Id"
    r"|\bSteuer[\-\s]?(?:nummer|Nr)"
    r"|\bIK[\-\s.]?(?:Nr\.?)?\s*:?\s*\d"
    r"|\b(?:LANR|LAN\-Nr|BSNR|IBAN|BIC|BLZ)\b"
    r"|\bBankverbindung\b|\bKonto(?:\-?Nr)?\b|\bPostfach\b"
)

# Loose organisation nouns: strong evidence of a sender *in the sender column*,
# but too common in body text to redact page-wide ("Zentrum", "Labor", "Institut"
# all show up in Leistungstexte). Exported for :mod:`backend.regions` only.
#
# German compounds defeat a plain word list — ``\bPraxis\b`` never matches
# "Zahnarztpraxis" because there is no word boundary mid-compound. So the nouns
# that habitually take a qualifying prefix allow one (``\w*praxis``); the
# noun-initial compounds that a suffix form doesn't reach ("Praxisgemeinschaft",
# "Laborgemeinschaft") stay listed. "Labor" deliberately gets no *suffix*
# wildcard: "Laboruntersuchung"/"Laborkosten" are ordinary Leistungstext words,
# and a footer line holding one must not seed the footer band.
ORG_MEDICAL = re.compile(
    r"(?i)\b(?:MVZ"
    r"|\w*praxis|Praxisgemeinschaft"  # Praxis, Zahnarzt-/Gemeinschaftspraxis
    r"|\w*klinik(?:um)?"  # Klinik(um), Zahn-/Tages-/Praxisklinik
    r"|\w*krankenhaus"
    r"|\w*ärztehaus"
    r"|\w*zentrum"  # Zentrum, Rechen-/Gesundheitszentrum
    r"|Institut"
    r"|\w*labor|Laborgemeinschaft"  # Labor, Dental-/Zahnlabor
    r"|Apotheke"
    r"|Sanitätshaus"
    r"|Krankenkasse|\w*versicherung"  # Kranken-/Zahnzusatzversicherung
    r"|\w*verrechnungsstelle"  # die PVS — the classic GOÄ biller
    r"|Abrechnungsstelle|Abrechnungsgesellschaft"
    r"|Berufsausübungsgemeinschaft)\b"
    r"|\bBehandl\w*\s+durch\b"
)


# The per-line deterministic rules, in the order they are tried. Named because
# "static-rule" alone says nine different things in a trace, and the one thing
# worth knowing about a wrong box is *which* pattern drew it. Keys are the
# module-level names on purpose — a trace line is then a grep away from the
# regex that produced it.
STATIC_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("SALUT", SALUT),
    ("PATIENT_NAME", PATIENT_NAME),
    ("TITLE_NAME", TITLE_NAME),
    ("NAME_DATE", NAME_DATE),
    ("DE_STREET", DE_STREET),
    ("DE_PLZ_CITY", DE_PLZ_CITY),
    ("ORG_LEGAL", ORG_LEGAL),
    ("CONTACT", CONTACT),
    ("IMPRINT", IMPRINT),
)


def static_rule_match(text: str) -> tuple[str, str] | None:
    """The first per-line deterministic rule that fires on ``text``, as
    ``(rule name, matched text)`` — or ``None`` if none does.

    The rules are salutation, patient label, titled name,
    surname+forename+birthdate, German street / ZIP+city, and sender identity
    (legal form, contact details, registry/banking identifiers). Birthdates and
    labeled identifiers need the whole page, so they are handled separately by
    :func:`labeled_value_indices`.

    Returning the match rather than a bool is what lets the trace name the rule
    *and* quote the substring it fired on: those two together are the whole
    diagnosis of an unexpected box."""
    for name, pattern in STATIC_RULES:
        match = pattern.search(text)
        if match is not None:
            return name, match.group()
    return None


def line_matches_static_rule(text: str) -> bool:
    """Whether any per-line deterministic rule fires — see
    :func:`static_rule_match`, which callers that report *why* use instead."""
    return static_rule_match(text) is not None


@dataclass(frozen=True)
class LabeledId:
    """One kind of labeled value: a ``label`` marks the line (or column
    neighbour) and ``value`` is what actually gets redacted. ``merged`` is
    extra same-line-only evidence — the abbreviated form that is only
    unambiguous glued directly to its value (``geb. 13.08.1954``).

    ``header`` is the label in its *other* role: alone in a cell at the top of a
    column, with the values running **downwards** beneath it rather than beside
    it. It is a separate pattern because that role has to be anchored — a label
    found mid-sentence heads nothing."""

    label: re.Pattern[str]
    value: re.Pattern[str]
    merged: re.Pattern[str] | None = None
    header: re.Pattern[str] | None = None


# The label ↔ value pairings labeled_value_indices matches. Birthdate is row
# one; the identifier row covers Versicherten-/Patienten-/Fall-/Aufnahme-/
# Mitglieds-/Vertrags-Nummern; the name row is the same geometry pointed at the
# *name* column, for the layout where "Patient:" is a cell of its own and the
# name sits beside it. Without it that name is invisible: PATIENT_NAME needs the
# label on the same line, and a bare "Wolf,Uwe" gives NER nothing to work with.
# The label there is anchored (PERSON_LABEL_CELL) — a sentence mentioning a
# patient is not a label, or every capitalized pair on its row would go black.
# The birthdate row is also the one with a `header`: "Geburtsdatum" is printed at
# the top of a column often enough (a patient table, a lab form) that nothing
# shares a row with the dates under it. A column headed that way holds birthdates
# by definition, which is what makes the downward pass safe here and not for,
# say, a "Datum" column. The name row carries one for the same reason — a column
# headed "Patient:" holds names — so a name reaches its label in either
# direction, beside it or below it.
LABELED_IDS: tuple[LabeledId, ...] = (
    LabeledId(
        label=BIRTH_LABEL, value=DATE_RE, merged=BIRTH_MARK, header=BIRTH_LABEL_CELL
    ),
    LabeledId(label=ID_LABEL, value=ID_VALUE),
    LabeledId(label=PERSON_LABEL_CELL, value=NAME_VALUE, header=PERSON_LABEL_CELL),
)

# What a person's *other* details look like: a cell holding nothing but one name,
# date or identifier. This is the vocabulary the column under a matched value may
# continue with — a patient block prints the name and then the birthdate beneath
# it, in the same column, labeled only by the person label beside the first row.
# Anchored for the same reason NAME_VALUE is: a bare DATE_RE matches *inside*
# "Behandlungszeitraum vom 15.04.2026 bis 28.04.2026", and three sample invoices
# print exactly that line under the patient block.
PERSON_DETAIL = re.compile(
    rf"{NAME_VALUE.pattern}|^\s*{DATE_RE.pattern}\s*$|^\s*{ID_VALUE.pattern}\s*$"
)

# How far a column may jump between two of its own entries before it counts as
# ended, as a multiple of the median line height — the same reasoning as the item
# table's row clustering, and the reason a header cannot blacken a whole page.
_COLUMN_GAP_FACTOR = 3.0


# --- name memory ------------------------------------------------------------ #
# A person's surname recurs on lines nothing else catches — a subject line, a
# Diagnose, a greeting split across OCR lines — and NER drops the single-token
# mention (the caps guard exists for good reason). So names are harvested from
# lines that *label* a person deterministically, and their bare recurrences are
# redacted. Evidence stays deterministic on purpose: no classifier feedback loop.

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

# What makes a line name *evidence*: a person label, a title, a salutation, or a
# birthdate next to the name — marked by the abbreviation
# ("Mustermann, Max'geb. 13.08.1964") or the star ("Muster, Andrea *13.08.1964"),
# or bare after a "Surname,Forename" pair ("Muster,Andrea 05.03.11"). In every
# case the name is why the date is there.
_NAME_EVIDENCE = (PATIENT_NAME, TITLE_NAME, SALUT, BIRTH_MARK, NAME_DATE)


def harvest_names(text: str) -> set[str]:
    """The name tokens on a line that deterministically labels a person; empty
    for any other line. The whole line is harvested, not just the match — OCR
    routinely glues "Mustermann, Max" around the label in either order — and the
    stopword list is what keeps the label vocabulary itself out."""
    evidence = any(p.search(text) for p in _NAME_EVIDENCE) or (
        # the spelled-out merged birthdate line: "Max Mustermann, geboren am ..."
        BIRTH_LABEL.search(text) and DATE_RE.search(text)
    )
    if not evidence:
        return set()
    return {
        tok for tok in _NAME_TOKEN.findall(text) if tok.casefold() not in _NAME_STOPWORDS_CF
    }


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


# --- the item table --------------------------------------------------------- #
# An invoice's body is a table of fee numbers, service texts, amounts and
# factors, and PII does not live in it: the person and address material sits
# *above* it (recipient block, patient block) or *below* it (imprint, bank
# details). That is a fact about the document, so it belongs here rather than in
# any one classifier — the table is simply where the model does not run.
#
# It matters because the classifier is the one detector with no anchor of its
# own, and German capitalizes every noun: a two-word Leistungstext
# ("Orientierende Testuntersuchg.", "Cleed Agar") is shaped exactly like a
# first-name/surname pair, and NER reads it as one. Everything deterministic
# keeps working inside the table — a labeled patient line, a titled name, a
# salutation, a birthdate sharing a row with its label, and any surname already
# harvested elsewhere in the document. What is given up is an *unlabeled,
# never-before-seen* name in an item row.

# German money: "4,66 €", "1.234,56", "EUR 10,72". The Faktor column ("2,30")
# has the same shape — harmless, it is inside the table we are looking for.
MONEY = re.compile(r"\b\d{1,3}(?:\.\d{3})*,\d{2}\b")

# How far apart two money rows may sit and still count as one table, as a
# multiple of the page's median line height. It is what stops a single amount
# printed in a letterhead ("Rechnungsbetrag 195,18") from stretching the band
# down over the recipient block: that amount forms its own cluster instead.
_TABLE_GAP_FACTOR = 3.0

# A table is a *repeated* structure. One amount on its own — a "Zahlbetrag" in a
# footer — is not a table and gates nothing.
_MIN_TABLE_ROWS = 2


def item_table_indices(lines: list[Line]) -> set[int]:
    """Indices of the lines inside an item table, for which
    :meth:`~backend.pipeline.RedactionPipeline.compute_boxes` skips the
    classifier (and only the classifier).

    Recognition and extent are separated the way :mod:`backend.regions` separates
    them for the sender column, and for the same reason: what *marks* the table
    (an amount) is not what *bounds* it. Money lines are merged into rows — one
    item row is three OCR lines, one per amount column — the rows are clustered
    by vertical gap, and each cluster of at least ``_MIN_TABLE_ROWS`` rows spans
    a band. A line is in the table if its vertical center falls in a band, which
    is how a wrapped description carrying no amount of its own ("Folgerezept)",
    sitting between two money rows) is covered.
    """
    rows: list[list[int]] = []
    for top, bottom in sorted(
        (ln.top, ln.top + ln.height) for ln in lines if MONEY.search(ln.text)
    ):
        if rows and top <= rows[-1][1]:  # overlaps the row above: same row
            rows[-1][1] = max(rows[-1][1], bottom)
        else:
            rows.append([top, bottom])
    if not rows:
        return set()

    gap = _TABLE_GAP_FACTOR * median(ln.height for ln in lines)
    clusters: list[list[list[int]]] = [[rows[0]]]
    for row in rows[1:]:
        if row[0] - clusters[-1][-1][1] <= gap:
            clusters[-1].append(row)
        else:
            clusters.append([row])
    bands = [(c[0][0], c[-1][1]) for c in clusters if len(c) >= _MIN_TABLE_ROWS]

    return {
        i
        for i, ln in enumerate(lines)
        if any(top <= ln.top + ln.height / 2 <= bottom for top, bottom in bands)
    }


def _column_below(
    lines: list[Line],
    start: Line,
    value: re.Pattern[str],
    gap: float,
    table: set[int],
) -> set[int]:
    """The value lines of the column running below ``start``.

    A line belongs to the column when its horizontal *center* falls within
    ``start``'s x-range — centers rather than overlap, because a "Geburtsdatum"
    header is wider than the dates under it and would otherwise reach into the
    columns on either side. The walk stops at the first line in that column that
    is not a value, as soon as the vertical gap exceeds ``gap``, or at the item
    table: a header may only claim what runs on directly beneath it, never the
    rest of the page.

    The table stop is the one bound the geometry cannot supply. A column of cells
    ends where the invoice body begins, but nothing in a line's position says so —
    and a Leistungstext is two capitalized words, which is the shape of a name
    ("Eingehende Untersuchung"). So the walk is told where the table is instead of
    being made to guess.

    ``start`` is either the label cell (the column-header form) or a value cell
    already matched beside its label (the person's remaining details below it).
    """
    x0, x1 = start.left, start.left + start.width
    found: set[int] = set()
    bottom = start.top + start.height
    for i, ln in sorted(enumerate(lines), key=lambda pair: pair[1].top):
        if ln.top < bottom or not x0 <= ln.left + ln.width / 2 <= x1:
            continue
        if i in table or ln.top - bottom > gap or not value.search(ln.text):
            break
        found.add(i)
        bottom = ln.top + ln.height
    return found


def labeled_value_indices(lines: list[Line], table: set[int] | None = None) -> set[int]:
    """Indices of lines holding a labeled personal value (birthdate,
    Versicherten-Nr., Fall-Nr., name, ...).

    Labels and values routinely sit in different columns — separate OCR lines —
    which no per-line regex can pair. A value line is redacted when its line
    also matches the label (the merged one-line form), when it shares a row with
    a line matching the label (the two-column form), when it runs *below* a
    line holding the label alone in its cell (the column-header form), or when it
    continues the column under a value that was matched that way (a patient block
    prints the name beside the label and the birthdate under the name). The last
    two are the same :func:`_column_below` walk from a different starting cell.

    The *label* line itself is only redacted if it contains a value; a bare column
    header is not PII. ``table`` is ``item_table_indices`` — the column walks stop
    there; ``None`` means "no table", which is what a caller testing the rule in
    isolation wants."""
    idx: set[int] = set()
    table = table or set()
    gap = _COLUMN_GAP_FACTOR * median([ln.height for ln in lines] or [0])
    for rule in LABELED_IDS:
        label_spans = [
            (ln.top, ln.top + ln.height) for ln in lines if rule.label.search(ln.text)
        ]
        for i, ln in enumerate(lines):
            if not rule.value.search(ln.text):
                continue
            # A cell holding nothing but the label is never a value. Implicit
            # until the person labels grew a modifier — "Versicherte Person" is
            # two name-shaped tokens, so the label cell now matches the value
            # pattern too, and "a bare column header is not PII" has to be stated
            # rather than left to the patterns not overlapping.
            if rule.header is not None and rule.header.search(ln.text):
                continue
            # label + value merged on one line, spelled out or abbreviated
            if rule.label.search(ln.text) or (
                rule.merged is not None and rule.merged.search(ln.text)
            ):
                idx.add(i)
                continue
            center = ln.top + ln.height / 2
            tol = ln.height / 2
            if any(y0 - tol <= center <= y1 + tol for y0, y1 in label_spans):
                idx.add(i)
                if rule.header is not None:
                    # The label licensed this cell, so it licenses the person's
                    # other details running on beneath it — the birthdate under
                    # the name, which carries no label of its own.
                    idx |= _column_below(lines, ln, PERSON_DETAIL, gap, table)
        if rule.header is not None:
            for ln in lines:
                if rule.header.search(ln.text):
                    idx |= _column_below(lines, ln, rule.value, gap, table)
    return idx
