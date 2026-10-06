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
    "PHONE": PiiLabel.CONTACT,
    "IMPRINT": PiiLabel.BANK,
    "TAX_NUMBER": PiiLabel.ID,
}


# GLiNER2 label -> our label, for `backend.classifiers.guard_omni`. The keys are
# also *what we ask the model for* (`GLINER2_LABELS` is this table's keys), so a
# label is either requested and mapped or neither — there is no third state to
# get wrong. That is the fix for the shape of bug the wholetext experiment
# carried: its harvest table keyed on `full_name`/`street_address`/`iban`, none
# of which are in GLiNER2's fixed vocabulary, so four of its nine rules could
# never fire and nothing said so.
#
# The table was first written with all 30 labels and then cut by measuring each
# one over the 48-document corpus: how many spans it produces, how many lines it
# helps redact, and — the number that decides — how many lines *only* it redacts.
# Four groups did not survive, and two survived against expectation:
#
# * `title` and `alias` are out because they feed the **name memory**, where a
#   wrong entry blackens every recurrence in the whole document rather than one
#   line. Measured: 9 of the 12 tokens `title` harvested are not names
#   ("Prof", "Kollegen", "Partner", "Geschäftsführer", "Leiter") and one is
#   "Allgemein", the exact collision `backend.harvest` documents; `alias`
#   harvested a BIC and OCR shrapnel. `person`/`first_name`/`last_name` were
#   clean over 95/70/42 distinct tokens, which is why they carry PERSON alone.
# * `passport`/`national_id`/`document_id` are out, so **`ID` has no model
#   source at all**. They uniquely drew 11 boxes, one of them an invoice number
#   and one a postage line. Telling an insurance number from an invoice number
#   is not a language question — `backend.rules.LABELED_IDS` answers it by the
#   label printed beside the value, and no zero-shot extractor can. The two
#   genuine catches were sender identifiers (an IK number, a VAT ID) that
#   `IMPRINT` is meant to cover; the fix for those is that rule, not a label
#   that would also fire on amounts and fee numbers.
# * `media` produced 12 spans and helped redact nothing at all; `region`
#   produced 5 and was never the only reason for a box. Dead weight.
# * `product` stays, against the prediction that it would fire on Leistungstexte:
#   all 10 lines only it redacted were letterhead specialty lines and
#   clearing-house names. Both are now kept (`backend.rules.keep_indices`), which
#   drops the classifier there, as the item-table gate does for Leistungstexte.
#
# Note what has no key at all: **SALUTATION**. GLiNER2 has no label for "Herrn"
# or "Sehr geehrte", so `backend.rules.SALUT` stays load-bearing whichever
# classifier runs — including as the name memory's evidence for the person named
# beside it.
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
