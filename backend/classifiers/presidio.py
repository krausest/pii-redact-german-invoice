"""Presidio classifier: spaCy German NER + custom regex recognizers.

Each OCR line is analyzed on its own (not one page-wide blob): PaddleOCR's reading
order interleaves the page's columns, which pollutes the text around an entity and
makes the NER model miss names it recognizes fine in isolation. A line counts as
PII whenever analyzing it turns up any redactable entity.
"""

from __future__ import annotations

import logging
import re

from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_analyzer.predefined_recognizers import EmailRecognizer, IbanRecognizer, CreditCardRecognizer, PhoneRecognizer

logger = logging.getLogger(__name__)

# Entities we treat as PII to redact. LOCATION is intentionally excluded: the NLP
# model fires it on the letterhead ("e-mops", jurisdiction city, ...) and the
# recipient's street/city is already covered precisely by DE_ADDRESS.
PII_ENTITIES = [
    "PERSON",
    "IBAN_CODE",
    "BIC_CODE",
    "DE_ADDRESS",
    "EMAIL_ADDRESS",
    "KONTO",
    "PHONE_NUMBER",
    "CREDIT_CARD"
]

# PERSON tokens that are OCR/NER noise, not names.
_PERSON_WHITELIST = {"Bgr", "Faktor", "Anz", "Anzahl"}

# A German dotted date ("09.07.2026") parses as a valid DE phone number
# (0907 is a real area code), so PHONE_NUMBER matches of this shape are ignored.
# Dates are not phone PII; the one date that *is* PII (the birthdate) is handled
# spatially by backend.rules.
_DOTTED_DATE = re.compile(r"\d{1,2}\.\d{1,2}\.\d{2,4}")


def build_analyzer() -> AnalyzerEngine:
    nlp_engine = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "de", "model_name": "de_core_news_lg"}],
            # de_core_news_lg labels PER/LOC/ORG/MISC; MISC has no Presidio
            # counterpart, so presidio logs "Entity MISC is not mapped..." for every
            # one it sees — once per entity per OCR line. Dropping it costs nothing:
            # analyze() only ever asks for PII_ENTITIES. Presidio's own default for
            # labels_to_ignore is empty, so this list overrides nothing.
            "ner_model_configuration": {"labels_to_ignore": ["MISC"]},
        }
    ).create_engine()

    analyzer = AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=["de"])

    # Predefined IBAN + email + credit card recognizers, registered for German.
    analyzer.registry.add_recognizer(IbanRecognizer(supported_language="de"))
    analyzer.registry.add_recognizer(EmailRecognizer(supported_language="de"))
    analyzer.registry.add_recognizer(CreditCardRecognizer(supported_language="de"))
    # Phone numbers via python-phonenumbers. DE only: the default region list
    # runs 8 regional matchers per line and lets e.g. US formats match random
    # digit columns. Note PhoneRecognizer.SCORE is 0.4 — exactly at the
    # classifier threshold, so matches pass with no context boost to spare.
    analyzer.registry.add_recognizer(
        PhoneRecognizer(supported_language="de", supported_regions=("DE",))
    )

    # BIC / SWIFT: 8 or 11 chars, AAAABBCC[DDD]. Case-sensitive (real BICs are
    # uppercase) so we don't match lowercase words, and given a low base score so
    # only matches near a "BIC"/"SWIFT" context word clear the threshold — this
    # keeps all-caps German words like "RECHNUNG" from being redacted.
    analyzer.registry.add_recognizer(
        PatternRecognizer(
            supported_entity="BIC_CODE",
            supported_language="de",
            patterns=[Pattern("bic", r"\b[A-Z]{6}[A-Z0-9]{2}(?:[A-Z0-9]{3})?\b", 0.15)],
            context=["bic", "swift"],
            global_regex_flags=re.DOTALL | re.MULTILINE,  # note: no IGNORECASE
        )
    )

    # German account number / bank code, matched *with* its label ("Kto. 520 22
    # 111", "BLZ 200 30000", "Konto-Nr.: 4 399 267 00"). Lines are classified one
    # at a time, so the label is always in the same text as the number — matching
    # it inline scores high enough on its own, instead of a low base score that
    # would need the context enhancer (which never fired here: the old context
    # word "bank" does not appear on these lines).
    analyzer.registry.add_recognizer(
        PatternRecognizer(
            supported_entity="KONTO",
            supported_language="de",
            patterns=[
                Pattern(
                    "konto_labeled",
                    r"\b(?:Kto|Konto(?:nummer|[ \-]?Nr)?|BLZ|Bankleitzahl)\.?\s*:?\s*\d(?:[ .]?\d){3,}\b",
                    0.7,
                )
            ],
            global_regex_flags=re.DOTALL | re.MULTILINE,
        )
    )

    # German address bits: "<Street>strasse 23" or "<Name> Straße 23" (suffix
    # attached lowercase, or capitalized as its own word), and "12345 Musterstadt"
    # postal + city.
    # The city token must contain lowercase letters so we don't match spec noise
    # like "15118 MID".
    analyzer.registry.add_recognizer(
        PatternRecognizer(
            supported_entity="DE_ADDRESS",
            supported_language="de",
            patterns=[
                Pattern(
                    "de_street",
                    r"\b[A-ZÄÖÜ][a-zäöüß.\-]+(?:(?:stra(?:ße|sse)|str|weg|platz|gasse|allee|ring|damm)|\s+(?:Stra(?:ße|sse)|Str|Weg|Platz|Gasse|Allee|Ring|Damm))\.?\s*\d+[a-zA-Z]?\b",
                    0.7,
                ),
                Pattern(
                    "de_plz_city",
                    r"\b\d{5}\s+[A-ZÄÖÜ][a-zäöüß]{2,}(?:[ \-][A-ZÄÖÜ][a-zäöüß]+)?\b",
                    0.6,
                ),
            ],
            # Case-sensitive so the lowercase city class really means lowercase:
            # keeps "15118 MID" out and stops city names from eating " www".
            global_regex_flags=re.DOTALL | re.MULTILINE,
        )
    )
    return analyzer


def _redactable(results, line: str) -> bool:
    """True if any result warrants redacting the line. A PERSON must have at least
    two capitalized tokens ("First Last") to be redacted — this drops the NLP
    model's single-token noise ("5.0016") and its false positives on German
    medical terms ("Infiltrationsanästhesie gro-", 2nd token lowercase)."""
    for r in results:
        if r.entity_type == "PHONE_NUMBER" and _DOTTED_DATE.fullmatch(
            line[r.start : r.end].strip()
        ):
            continue
        if r.entity_type == "PERSON":
            caps = sum(
                1
                for tok in line[r.start : r.end].split()
                if tok[:1].isupper() and tok not in _PERSON_WHITELIST
            )
            if caps < 2:
                continue
        return True
    return False


class PresidioClassifier:
    def __init__(self, score_threshold: float = 0.4) -> None:
        self._analyzer = build_analyzer()
        self._score_threshold = score_threshold

    def is_pii(self, text: str) -> bool:
        # The decision process (per-match explanations) is extra work presidio
        # only has to do when someone will actually read it.
        debug = logger.isEnabledFor(logging.DEBUG)
        results = self._analyzer.analyze(
            text=text,
            language="de",
            entities=PII_ENTITIES,
            score_threshold=self._score_threshold,
            return_decision_process=debug,
        )
        if debug:
            for r in results:
                e = r.analysis_explanation
                logger.debug(
                    f"    match {r.entity_type} {r.score:.2f} {text[r.start:r.end]!r}"
                    f" [{e.recognizer}"
                    + (f"/{e.pattern_name}" if e.pattern_name else "")
                    + (
                        f", ctx +{e.score_context_improvement:.2f} ({e.supportive_context_word})"
                        if e.score_context_improvement
                        else ""
                    )
                    + "]"
                )
        return _redactable(results, text)
