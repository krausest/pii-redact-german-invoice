"""Presidio classifier: spaCy German NER + custom regex recognizers.

Each OCR line is analyzed on its own (not one page-wide blob): PaddleOCR's reading
order interleaves the page's columns, which pollutes the text around an entity and
makes the NER model miss names it recognizes fine in isolation. A line counts as
PII whenever analyzing it turns up any redactable entity.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Protocol

from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_analyzer.predefined_recognizers import EmailRecognizer, IbanRecognizer, CreditCardRecognizer, PhoneRecognizer

from backend.rules import DE_PLZ_CITY
from backend.trace import Trace

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
# spatially by backend.rules. The trailing class covers a date the recognizer
# swallowed together with the code column beside it ("12.12.15 51-61") — without
# it the span is not a bare date any more and the guard misses.
_DOTTED_DATE = re.compile(r"\d{1,2}\.\d{1,2}\.\d{2,4}[\s\d.\-/]*")

# An undelimited run of digits is an identifier (a lab or order number), not a
# phone number: on these documents a real number is written with a separator or
# carries a Tel/Fax label, and a labeled one is already caught deterministically
# by backend.rules.CONTACT before the classifier ever runs. python-phonenumbers
# scores such a run at exactly the 0.4 threshold, with no context to spare.
_BARE_DIGITS = re.compile(r"\d+")


class _Token(Protocol):
    """What :func:`_redactable` needs of a token — the structural subset of a
    spaCy ``Token``, so tests can pass a namedtuple and stay model-free. A
    ``Sequence`` of these, not an iterator: a line with two PERSON spans walks
    them twice."""

    idx: int
    text: str
    pos_: str


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
    # postal + city. Both accept the all-caps form a letterhead prints, and the
    # city pattern is imported from backend.rules rather than restated — the two
    # are supposed to agree, and while they were written out twice they did not.
    analyzer.registry.add_recognizer(
        PatternRecognizer(
            supported_entity="DE_ADDRESS",
            supported_language="de",
            patterns=[
                Pattern(
                    "de_street",
                    # Kept in step with backend.rules.DE_STREET: the name part
                    # allows upper case throughout and the suffix is matched
                    # case-insensitively, so an all-caps "MUSTERSTR.23" is
                    # found as well as "Musterstrasse 23".
                    r"\b[A-ZÄÖÜ][A-ZÄÖÜa-zäöüß.\-]+(?:(?i:stra(?:ße|sse)|str|weg|platz|gasse|allee|ring|damm)|\s+(?i:stra(?:ße|sse)|str|weg|platz|gasse|allee|ring|damm))\.?\s*\d+[a-zA-Z]?\b",
                    0.7,
                ),
                Pattern(
                    "de_plz_city",
                    # backend.rules.DE_PLZ_CITY itself, so the two cannot drift:
                    # this pattern used to allow a two-letter all-caps tail and so
                    # matched the "15118 MID" its own comment promised to keep out.
                    DE_PLZ_CITY.pattern,
                    0.6,
                ),
            ],
            # Case-sensitive so the lowercase city class really means lowercase:
            # keeps "15118 MID" out and stops city names from eating " www".
            global_regex_flags=re.DOTALL | re.MULTILINE,
        )
    )
    return analyzer


def _name_token_count(tokens: Sequence[_Token], start: int, end: int) -> int:
    """How many name tokens the PERSON span ``[start, end)`` is worth.

    Inside the span a token must be a proper noun. *Across a comma* the bar is
    only that it be capitalized: NER routinely returns just one half of
    "Muster,Andrea" — surname-comma-forename is how a patient row is written —
    and the surnames it leaves out are exactly the ones the tagger reads as
    common nouns ("Bauer", "Jäger", "Wolf" are all ordinary German words). The
    comma carries that weight safely because *something* on the line still has to
    have been recognized as a PERSON: the Leistungstexte this guard exists to
    reject ("Mikroskopie,Kultur", "Summe,Betrag", "Ferritin,CRP") produce no
    PERSON entity at all, so there is no span here to extend.
    """
    inside = [
        i
        for i, tok in enumerate(tokens)
        if tok.idx < end and tok.idx + len(tok.text) > start
    ]
    if not inside:
        return 0

    def counts(tok: _Token, *, proper: bool) -> bool:
        return (
            tok.text[:1].isupper()
            and tok.text not in _PERSON_WHITELIST
            and (tok.pos_ == "PROPN" or not proper)
        )

    count = sum(1 for i in inside if counts(tokens[i], proper=True))
    for edge, step in ((inside[0], -1), (inside[-1], 1)):
        comma, neighbour = edge + step, edge + 2 * step
        if 0 <= comma < len(tokens) and 0 <= neighbour < len(tokens):
            if tokens[comma].text == "," and counts(tokens[neighbour], proper=False):
                count += 1
    return count


def _redactable(results, line: str, tokens: Sequence[_Token], trace: Trace) -> bool:
    """True if any result warrants redacting the line.

    A PERSON must contain at least two *proper-noun* tokens ("First Last").
    Capitalization alone is not evidence in German — every noun is capitalized,
    so a two-word Leistungstext reads exactly like a forename/surname pair to the
    NER model. The tagger disagrees with it there, and the tagger is right:
    "Orientierende" (of "Orientierende Testuntersuchg.") is NOUN and "Cleed" (of
    the culture medium "Cleed Agar") is ADV, while both tokens of a real name are
    PROPN. It is the *coarse* tag that separates them — "Cleed" is tag_=NE.

    Requiring two also still drops the model's single-token noise ("5.0016") and
    its hits on German medical terms. See :func:`_name_token_count` for the one
    place the proper-noun bar is relaxed — a name across a comma.
    """
    for r in results:
        if r.entity_type == "PHONE_NUMBER":
            span = line[r.start : r.end].strip()
            if _DOTTED_DATE.fullmatch(span) or _BARE_DIGITS.fullmatch(span):
                trace.add("      Ignoring PHONE_NUMBER %r: a date or an identifier", span)
                continue
        if r.entity_type == "PERSON":
            names = _name_token_count(tokens, r.start, r.end)
            if names < 2:
                trace.add("      Ignoring PERSON with only %d name token(s)", names)
                continue
        return True
    return False


class PresidioClassifier:
    def __init__(self, score_threshold: float = 0.4) -> None:
        self._analyzer = build_analyzer()
        self._score_threshold = score_threshold

    def is_pii(self, text: str, trace: Trace) -> bool:
        # The decision process (per-match explanations) is extra work presidio
        # only has to do when someone will actually read it — which is exactly
        # what `trace.wanted` answers, for the log and for a `?debug=true`
        # collector alike.
        explain = trace.wanted
        # One parse, used twice. `analyze` runs the spaCy pipeline itself unless
        # it is handed the artifacts, so passing them costs nothing and gives
        # `_redactable` the very tokens the recognizers saw — parsing a second
        # time could only disagree with them.
        artifacts = self._analyzer.nlp_engine.process_text(text, "de")
        results = self._analyzer.analyze(
            text=text,
            language="de",
            entities=PII_ENTITIES,
            score_threshold=self._score_threshold,
            return_decision_process=explain,
            nlp_artifacts=artifacts,
        )
        if explain:
            for r in results:
                e = r.analysis_explanation
                trace.add(
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
        return _redactable(results, text, artifacts.tokens, trace)
