"""The one vocabulary every PII source speaks: a labeled, scored character span.

The rules, presidio and guard-omni each name their findings differently
(``IMPRINT``, ``IBAN_CODE``, ``bank_account``); here they land in one small set of
labels, so everything downstream is written once. Free of heavy imports.
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


# Span sources besides the classifiers' own names ("presidio", "guard-omni").
LABELED_VALUE = "labeled-value"
NAME_MEMORY = "name-memory"
BIRTHDATE_MEMORY = "birthdate-memory"
_RULE_PREFIX = "rule "


def rule_source(name: str) -> str:
    """The source of a span a named rule drew: ``rule SALUT``."""
    return _RULE_PREFIX + name


@dataclass(frozen=True)
class Span:
    """One finding, in character offsets of the text it was found in.

    Offsets are always relative to *the whole page's* joined text by the time
    the pipeline sees a span: a detector working per line is responsible for
    shifting its own offsets (see :func:`backend.rules.rule_spans`), so that
    :meth:`backend.document.Document.per_line` only ever has one coordinate system
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

    @property
    def is_rule(self) -> bool:
        """Drawn by a deterministic rule, on its line or across cells."""
        return self.source.startswith(_RULE_PREFIX) or self.source == LABELED_VALUE

    @property
    def is_memory(self) -> bool:
        """Drawn by the name or birthdate memory."""
        return self.source in (NAME_MEMORY, BIRTHDATE_MEMORY)


# -- what the detectors call things ---------------------------------------- #

# Presidio entity type -> our label. No ``LOCATION`` on purpose (see presidio.PII_ENTITIES).
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


# GLiNER2 label -> our label, for `backend.classifiers.guard_omni`. The keys are
# also what the model is asked for, so a label is requested and mapped or neither.
# Cut to what measurably helps (docs/Redaction.md, Classifiers): no `title`/`alias`
# (they poison the name memory), no ID labels (only the rules tell an insurance
# number from an invoice number), no SALUTATION (no such label: `SALUT` covers it).
GUARD_OMNI_LABELS: dict[str, PiiLabel] = {
    "person": PiiLabel.PERSON,
    "first_name": PiiLabel.PERSON,
    "last_name": PiiLabel.PERSON,
    "date_of_birth": PiiLabel.DATE_OF_BIRTH,
    "country": PiiLabel.ADDRESS,
    "city": PiiLabel.ADDRESS,
    "district": PiiLabel.ADDRESS,
    "street": PiiLabel.ADDRESS,
    "building": PiiLabel.ADDRESS,
    "unit": PiiLabel.ADDRESS,
    "postal_code": PiiLabel.ADDRESS,
    "landmark": PiiLabel.ADDRESS,
    "address": PiiLabel.ADDRESS,
    "company": PiiLabel.ORG,
    "government": PiiLabel.ORG,
    "education": PiiLabel.ORG,
    "product": PiiLabel.ORG,
    "email": PiiLabel.CONTACT,
    "phone": PiiLabel.CONTACT,
    "social_account": PiiLabel.CONTACT,
    "messenger": PiiLabel.CONTACT,
    "bank_account": PiiLabel.BANK,
    "crypto_wallet": PiiLabel.BANK,
}
