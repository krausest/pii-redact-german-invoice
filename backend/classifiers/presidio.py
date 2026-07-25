"""Presidio classifier: spaCy German NER + custom regex recognizers.

Each OCR line is analyzed on its own (not one page-wide blob): PaddleOCR's reading
order interleaves the page's columns, which pollutes the text around an entity and
makes the NER model miss names it recognizes fine in isolation. A line counts as
PII whenever analyzing it turns up any redactable entity.
"""

from __future__ import annotations

import re

from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_analyzer.predefined_recognizers import EmailRecognizer, IbanRecognizer

# Entities we treat as PII to redact. LOCATION is intentionally excluded: the NLP
# model fires it on the letterhead ("e-mops", jurisdiction city, ...) and the
# recipient's street/city is already covered precisely by DE_ADDRESS.
PII_ENTITIES = [
    "PERSON",
    "IBAN_CODE",
    "BIC_CODE",
    "DE_ADDRESS",
    "EMAIL_ADDRESS",
]

# PERSON tokens that are OCR/NER noise, not names.
_PERSON_WHITELIST = {"Bgr", "Fatktor", "Anz", "Anzahl"}


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

    # Predefined IBAN + email recognizers, registered for German.
    analyzer.registry.add_recognizer(IbanRecognizer(supported_language="de"))
    analyzer.registry.add_recognizer(EmailRecognizer(supported_language="de"))

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

    # German address bits: "<Street>strasse 23", and "12345 Musterstadt" postal + city.
    # The city token must contain lowercase letters so we don't match spec noise
    # like "15118 MID".
    analyzer.registry.add_recognizer(
        PatternRecognizer(
            supported_entity="DE_ADDRESS",
            supported_language="de",
            patterns=[
                Pattern(
                    "de_street",
                    r"\b[A-ZÄÖÜ][a-zäöüß.\-]+(?:stra(?:ße|sse)|str\.?|weg|platz|gasse|allee|ring|damm)\s*\d+[a-zA-Z]?\b",
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
        results = self._analyzer.analyze(
            text=text,
            language="de",
            entities=PII_ENTITIES,
            score_threshold=self._score_threshold,
        )
        return _redactable(results, text)
