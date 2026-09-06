"""The one vocabulary every PII source speaks: a labeled, scored character span.

Three detectors have to agree on what they found before the pipeline can treat
them alike — the deterministic German regexes in :mod:`backend.rules`, the
Presidio classifier, and (later) the guard-omni zero-shot model. Each one names
its findings differently: presidio says ``IBAN_CODE``, a regex is called
``IMPRINT``, guard-omni says ``bank_account``. This module is where all three
land in the same small set of labels, so that everything downstream — the
redaction decision, the trace, and the cross-line harvest — is written once
rather than per detector.

Deliberately free of heavy imports: :mod:`backend.rules` and the tests use it
without pulling in presidio, spaCy or torch.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PiiLabel(str, Enum):
    """What a span *is*. Small on purpose: this is not an ontology, it is the
    set of distinctions the redaction decision and the harvest actually make.

    ``str`` base so a label formats as its own name in the trace and compares
    equal to the plain string in a test table.
    """

    PERSON = "PERSON"
    SALUTATION = "SALUTATION"
    DATE_OF_BIRTH = "DATE_OF_BIRTH"
    ADDRESS = "ADDRESS"
    ORG = "ORG"
    CONTACT = "CONTACT"
    BANK = "BANK"
    ID = "ID"

    def __str__(self) -> str:  # keeps "%s" in the trace from printing PiiLabel.PERSON
        return self.value


@dataclass(frozen=True)
class Span:
    """One finding, in character offsets of the text it was found in.

    Offsets are always relative to *the whole page's* joined text by the time
    the pipeline sees a span: a detector working per line is responsible for
    shifting its own offsets (see :func:`backend.rules.rule_spans`), so that
    :func:`backend.document.spans_to_lines` only ever has one coordinate system
    to map back from.

    ``source`` names *who* found it ("rule SALUT", "presidio", "guard-omni") and
    exists for the trace: the label says what was found, the source says which
    detector to go and fix when the box is wrong. ``score`` is 1.0 for a
    deterministic rule — a regex either matched or it did not.
    """

    label: PiiLabel
    start: int
    end: int
    text: str
    source: str
    score: float = 1.0


# -- what the detectors call things ---------------------------------------- #

# Presidio entity type -> our label. Note what is NOT here: ``LOCATION``.
# `presidio.PII_ENTITIES` excludes it deliberately, because the NLP model fires
# it on letterhead vocabulary and jurisdiction cities, while a real recipient
# address is already caught precisely by the custom ``DE_ADDRESS`` recognizer.
# Adding a row for it here would quietly undo that decision.
PRESIDIO_LABELS: dict[str, PiiLabel] = {
    "PERSON": PiiLabel.PERSON,
    "DE_ADDRESS": PiiLabel.ADDRESS,
    "EMAIL_ADDRESS": PiiLabel.CONTACT,
    "PHONE_NUMBER": PiiLabel.CONTACT,
    "IBAN_CODE": PiiLabel.BANK,
    "BIC_CODE": PiiLabel.BANK,
    "KONTO": PiiLabel.BANK,
    "CREDIT_CARD": PiiLabel.BANK,
}

# `backend.rules.STATIC_RULES` name -> our label. The rule names stay as they
# are: a trace line still quotes the regex that fired, because "ADDRESS" does
# not tell you whether to go and look at DE_STREET or DE_PLZ_CITY.
#
# SALUT gets its own label rather than folding into PERSON: "Herrn" on its own
# is not a person, it marks the recipient block — and the harvest must never
# take it for a name to look for elsewhere on the page.
RULE_LABELS: dict[str, PiiLabel] = {
    "SALUT": PiiLabel.SALUTATION,
    "PATIENT_NAME": PiiLabel.PERSON,
    "TITLE_NAME": PiiLabel.PERSON,
    "NAME_DATE": PiiLabel.PERSON,
    "DE_STREET": PiiLabel.ADDRESS,
    "DE_PLZ_CITY": PiiLabel.ADDRESS,
    "ORG_LEGAL": PiiLabel.ORG,
    "CONTACT": PiiLabel.CONTACT,
    "IMPRINT": PiiLabel.BANK,
}
