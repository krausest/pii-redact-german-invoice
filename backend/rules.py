"""Deterministic redaction rules the NER/NLP stack misses.

These regexes and the spatial birthdate matcher are shared by every classifier:
they are applied uniformly in :func:`backend.pipeline.RedactionPipeline.compute_boxes`
*before* the model-based classifier runs. Applying the German street / ZIP+city
patterns uniformly is behavior-preserving for the Presidio path — its DE_ADDRESS
recognizer uses the same regexes at score 0.7/0.6 with threshold 0.4, so it
already always fires on those matches; the GLiNER path relied on them explicitly
because its zero-shot "address" label is unreliable.

The sender-identity patterns (``ORG_LEGAL`` / ``CONTACT`` / ``IMPRINT``) are the
one group that *adds* detections rather than restating what a classifier already
finds: NER treats a clearing house or a bank as an ORGANIZATION, which is not a
PII entity, so nothing ever flagged the letterhead or the footer imprint.
"""

from __future__ import annotations

import re

from backend.models import Line

# Anrede: any line containing a salutation word is redacted — lone ("Herrn" above
# the address) or with a name ("Herr Mustermann", where NER only tags the single
# token "Mustermann", which the PERSON guard drops). A lone salutation carries no
# information, so over-redacting it is harmless and needs just one regex.
SALUT = re.compile(r"\b(?:Herrn?|Frau|Fräulein|Frl|Familie|Fam|Eheleute)\b")

# Academic/medical title(s) followed by a capitalized name ("Dr. Weber",
# "Prof. Dr. med. Hans Müller", "Dr. Dr. Daphne Schlegel-Lippert"). The NER
# model is unreliable around titles — it misses the name entirely after a
# doubled "Dr. Dr.", and for "Dr. Weber" tags only the single token the PERSON
# guard drops — while a title is by itself strong evidence of a person.
TITLE_NAME = re.compile(
    r"\b(?:(?:Prof|Priv\.-Doz|Dr(?:es)?|med|dent|vet|univ|habil)\.\s*)+"  # title(s)
    r"(?:[A-ZÄÖÜ]\.\s*)*"  # optional initials: "Dr. A. Meier"
    r"[A-ZÄÖÜ][a-zäöüß]+"
)

# German street: "<Street>strasse 23".
DE_STREET = re.compile(
    r"\b[A-ZÄÖÜ][a-zäöüß.\-]+(?:stra(?:ße|sse)|str\.?|weg|platz|gasse|allee|ring|damm)\s*\d+[a-zA-Z]?\b"
)
# German ZIP + city: "12345 Musterstadt". The city token must contain lowercase
# letters so we don't match spec noise like "15118 MID".
DE_PLZ_CITY = re.compile(r"\b\d{5}\s+[A-ZÄÖÜ][a-zäöüß]{2,}(?:[ \-][A-ZÄÖÜ][a-zäöüß]+)?\b")

# Date of birth: the "Geburtstag/Geburtsdatum/geboren" label and the date sit in
# different columns, so they are separate OCR lines — matched spatially below.
# Only full birth words: the bare abbreviation "geb." also means "Gebühren"
# ("Geb.Nr." fee-number column), which would redact treatment dates by mistake.
BIRTH_LABEL = re.compile(r"(?i)geburt|geboren")
DATE_RE = re.compile(r"\b\d{1,2}\.\s?\d{1,2}\.\s?\d{2,4}\b")

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
CONTACT = re.compile(
    r"(?i)\bhttps?://\S+"
    r"|\bwww\.[\w\-]+(?:\.[\w\-]+)+"
    r"|\b[\w\-]{2,}(?:\.[\w\-]+)*\.(?:de|com|net|org|eu|at|ch)\b"
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
ORG_MEDICAL = re.compile(
    r"(?i)\b(?:MVZ|Gemeinschaftspraxis|Praxisgemeinschaft|Praxisklinik|Praxis"
    r"|Klinik(?:um)?|Krankenhaus|Ärztehaus|Rechenzentrum|Zentrum|Institut|Labor"
    r"|Berufsausübungsgemeinschaft)\b"
    r"|\bBehandl\w*\s+durch\b"
)


def line_matches_static_rule(text: str) -> bool:
    """True if a line is redactable by the per-line deterministic rules
    (salutation, titled name, German street / ZIP+city, or sender identity:
    legal form, contact details, registry/banking identifiers). Birthdates need
    the whole page, so they are handled separately by :func:`birthdate_indices`."""
    return bool(
        SALUT.search(text)
        or TITLE_NAME.search(text)
        or DE_STREET.search(text)
        or DE_PLZ_CITY.search(text)
        or ORG_LEGAL.search(text)
        or CONTACT.search(text)
        or IMPRINT.search(text)
    )


def birthdate_indices(lines: list[Line]) -> set[int]:
    """Indices of lines holding a date of birth: a date line sharing a row with a
    "Geburtstag/geb." label line (two-column layout), or label+date on one line."""
    label_spans = [
        (ln.top, ln.top + ln.height) for ln in lines if BIRTH_LABEL.search(ln.text)
    ]

    idx: set[int] = set()
    for i, ln in enumerate(lines):
        if not DATE_RE.search(ln.text):
            continue
        if BIRTH_LABEL.search(ln.text):  # label + date merged on one line
            idx.add(i)
            continue
        center = ln.top + ln.height / 2
        tol = ln.height / 2
        if any(y0 - tol <= center <= y1 + tol for y0, y1 in label_spans):
            idx.add(i)
    return idx
