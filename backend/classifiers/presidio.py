"""Presidio classifier: spaCy German NER plus custom regex recognizers, run over
the whole page in reading order, so a name across a line wrap is one entity.

The guards here (``_drop_wrapped``, ``_keep``, ``_name_token_count``,
``_name_fragments``) fix this classifier's own false hits; ``tests/test_presidio.py``
pins each one.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Protocol

from backend.document import TextRange
from backend.rules.patterns import DE_PLZ_CITY, DE_STREET
from backend.pii import PRESIDIO_LABELS, PiiLabel, Span
from backend.trace import Trace

# LOCATION is excluded: it fires on letterhead words; DE_ADDRESS covers addresses.
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

# A dotted date ("09.07.2026", maybe with the code column after it) parses as a
# DE phone number; it is never one here.
_DOTTED_DATE = re.compile(r"\d{1,2}\.\d{1,2}\.\d{2,4}[\s\d.\-/]*")

# An undelimited digit run is an identifier, not a phone number (real ones are
# written with separators or a label, which the rules catch).
_BARE_DIGITS = re.compile(r"\d+")


# The one label that may run across a line break: a name split by a wrap. Every
# other label comes from a pattern, and a pattern is printed on one line.
_MAY_WRAP = frozenset({PiiLabel.PERSON})


def _drop_wrapped(spans: Sequence[Span]) -> list[Span]:
    """Drop pattern matches that only exist because two lines were joined: a
    regex's whitespace matches the joining newline. Presidio-only — a span model
    legitimately reports entities across a wrap."""
    return [s for s in spans if s.label in _MAY_WRAP or "\n" not in s.text]


class _Token(Protocol):
    """The part of a spaCy ``Token`` the guards read; tests pass a namedtuple."""

    idx: int
    text: str
    pos_: str


def build_analyzer():
    # Imported here: presidio_analyzer pulls in torch, which importing this
    # module for its guards must not.
    from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer
    from presidio_analyzer.nlp_engine import NlpEngineProvider
    from presidio_analyzer.predefined_recognizers import (
        CreditCardRecognizer,
        EmailRecognizer,
        IbanRecognizer,
        PhoneRecognizer,
    )

    nlp_engine = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "de", "model_name": "de_core_news_lg"}],
            # MISC has no Presidio entity and would only log a warning per hit.
            "ner_model_configuration": {"labels_to_ignore": ["MISC"]},
        }
    ).create_engine()

    analyzer = AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=["de"])

    # Predefined IBAN + email + credit card recognizers, registered for German.
    analyzer.registry.add_recognizer(IbanRecognizer(supported_language="de"))
    analyzer.registry.add_recognizer(EmailRecognizer(supported_language="de"))
    analyzer.registry.add_recognizer(CreditCardRecognizer(supported_language="de"))
    # DE only: other regions match random digit columns. Its score (0.4) sits
    # exactly at the threshold.
    analyzer.registry.add_recognizer(
        PhoneRecognizer(supported_language="de", supported_regions=("DE",))
    )

    # BIC: case-sensitive, and a low score so only a "BIC"/"SWIFT" context word
    # lifts it over the threshold — "RECHNUNG" has the same shape.
    analyzer.registry.add_recognizer(
        PatternRecognizer(
            supported_entity="BIC_CODE",
            supported_language="de",
            patterns=[Pattern("bic", r"\b[A-Z]{6}[A-Z0-9]{2}(?:[A-Z0-9]{3})?\b", 0.15)],
            context=["bic", "swift"],
            global_regex_flags=re.DOTALL | re.MULTILINE,  # note: no IGNORECASE
        )
    )

    # Account number or bank code with its label: "Kto. 520 22 111", "BLZ 200 30000".
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

    # Street and ZIP+city: the rules' own patterns, so the two cannot disagree.
    analyzer.registry.add_recognizer(
        PatternRecognizer(
            supported_entity="DE_ADDRESS",
            supported_language="de",
            patterns=[
                Pattern(
                    "de_street",
                    DE_STREET.pattern,
                    0.7,
                ),
                Pattern(
                    "de_plz_city",
                    DE_PLZ_CITY.pattern,
                    0.6,
                ),
            ],
            # Case-sensitive, so "15118 MID" stays out.
            global_regex_flags=re.DOTALL | re.MULTILINE,
        )
    )
    return analyzer


def _name_fragments(
    text: str, tokens: Sequence[_Token], start: int, end: int
) -> list[TextRange]:
    """A PERSON span cut into its lines, keeping each line's part only if it holds
    a proper noun: "Andrea\nMuster" keeps both, "Andrea Muster\nLeistungsdatum"
    only the name."""
    fragments: list[TextRange] = []
    pos = start
    for piece in text[start:end].split("\n"):
        lo, hi = pos, pos + len(piece)
        if piece.strip() and any(
            tok.pos_ == "PROPN" and tok.idx < hi and tok.idx + len(tok.text) > lo
            for tok in tokens
        ):
            fragments.append(TextRange(lo, hi))
        pos = hi + 1  # + the "\n"
    return fragments


def _name_token_count(text: str, tokens: Sequence[_Token], start: int, end: int) -> int:
    """How many name tokens the PERSON span ``[start, end)`` is worth.

    Inside the span a token counts if the tagger calls it a proper noun, whatever
    its case (OCR turns "Ioanna" into "loanna"). Across a comma on the same line,
    a capitalized neighbour counts too: NER returns one half of "Muster,Andrea"
    and the tagger reads many surnames as nouns.
    """
    inside = [
        i
        for i, tok in enumerate(tokens)
        if tok.idx < end and tok.idx + len(tok.text) > start
    ]
    if not inside:
        return 0

    def counts(tok: _Token, *, proper: bool) -> bool:
        if tok.text in _PERSON_WHITELIST:
            return False
        return tok.pos_ == "PROPN" if proper else tok.text[:1].isupper()

    count = sum(1 for i in inside if counts(tokens[i], proper=True))
    for edge, step in ((inside[0], -1), (inside[-1], 1)):
        comma, neighbour = edge + step, edge + 2 * step
        if 0 <= comma < len(tokens) and 0 <= neighbour < len(tokens):
            lo, hi = sorted((tokens[edge].idx, tokens[neighbour].idx))
            if "\n" in text[lo:hi]:
                continue
            if tokens[comma].text == "," and counts(tokens[neighbour], proper=False):
                count += 1
    return count


def _keep(results, line: str, tokens: Sequence[_Token], trace: Trace) -> list:
    """The results worth acting on: no date or bare digit run as a phone number,
    and no PERSON with fewer than two name tokens — German capitalizes every noun,
    so a two-word service text reads like a name to NER."""
    kept = []
    for r in results:
        if r.entity_type == "PHONE_NUMBER":
            span = line[r.start : r.end].strip()
            if _DOTTED_DATE.fullmatch(span) or _BARE_DIGITS.fullmatch(span):
                trace.add("      Ignoring PHONE_NUMBER %r: a date or an identifier", span)
                continue
        if r.entity_type == "PERSON":
            names = _name_token_count(line, tokens, r.start, r.end)
            if names < 2:
                trace.add("      Ignoring PERSON with only %d name token(s)", names)
                continue
        kept.append(r)
    return kept


class PresidioClassifier:
    def __init__(self, score_threshold: float = 0.4) -> None:
        self._analyzer = build_analyzer()
        self._score_threshold = score_threshold

    def spans(self, text: str, trace: Trace) -> list[Span]:
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
        spans: list[Span] = []
        for r in _keep(results, text, artifacts.tokens, trace):
            label = PRESIDIO_LABELS[r.entity_type]
            # A PERSON is the one entity allowed to wrap, so it is also the one
            # that has to be cut back when the model wrapped it too far.
            pieces = (
                _name_fragments(text, artifacts.tokens, r.start, r.end)
                if label is PiiLabel.PERSON and "\n" in text[r.start : r.end]
                else [TextRange(r.start, r.end)]
            )
            for piece in pieces:
                spans.append(
                    Span(
                        label=label,
                        start=piece.start,
                        end=piece.end,
                        text=text[piece.start : piece.end],
                        source="presidio",
                        score=r.score,
                    )
                )
        return _drop_wrapped(spans)
