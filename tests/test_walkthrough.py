"""A guided tour of the detection code, from one regex up to a whole page.

Each test is one step and prints what it sees — run with
``uv run pytest tests/test_walkthrough.py -s``. Loads spaCy, hence ``slow``.
"""

from __future__ import annotations

import re

import pytest

from backend.classifiers.presidio import PII_ENTITIES, PresidioClassifier
from backend.document import Document
from backend.models import Line
from backend.pii import PiiLabel
from backend.rules import rule_spans
from backend.rules.line_rules import STATIC_RULES
from backend.rules.patterns import DE_PLZ_CITY, DE_STREET
from backend.trace import Trace

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def presidio() -> PresidioClassifier:
    return PresidioClassifier()


def _header(title: str) -> None:
    print(f"\n=== {title} ===")


def _pattern(analyzer, name: str) -> str:
    return next(
        p.regex
        for r in analyzer.registry.recognizers
        for p in getattr(r, "patterns", [])
        if p.name == name
    )


# --- step 1: one line, both detectors ---------------------------------------

LINE = "Dr. Andrea Muster, Musterstraße 7, 12345 Musterhausen"


def test_01_rules_and_presidio_on_one_line(presidio):
    lines = [Line(LINE, 0, 0, 700, 27)]
    # line:   'Dr. Andrea Muster, Musterstraße 7, 12345 Musterhausen'
    doc = Document(lines)
    text = doc.text
    # text: 'Dr. Andrea Muster, Musterstraße 7, 12345 Musterhausen', range: (0, 53)
    print(f"\nline:   {LINE!r}\nrange:  {doc.range_of(lines[0])}")

    _header("A  backend.rules: every STATIC_RULES regex on its own")
    for _, name, pattern in STATIC_RULES:
        print(f"  {name:13} {[m.group() for m in pattern.finditer(LINE)]}")

    _header("B  rule_spans -> Span (per line, score 1.0)")
    rules = rule_spans(doc)
    for s in rules:
        print(f"  [{s.start:2}:{s.end:2}] {s.label:8} {s.text!r:22} {s.source}")

    _header("C  presidio registry (language 'de')")
    analyzer = presidio._analyzer
    for r in analyzer.registry.get_recognizers("de", all_fields=True):
        pats = [(p.name, p.score) for p in getattr(r, "patterns", [])]
        print(f"  {r.name:24} {r.supported_entities}{'  ' + str(pats) if pats else ''}")
    print(f"  PII_ENTITIES (what analyze is asked for): {PII_ENTITIES}")

    street, plz_city = _pattern(analyzer, "de_street"), _pattern(analyzer, "de_plz_city")
    print(f"\n  de_plz_city is DE_PLZ_CITY.pattern: {plz_city == DE_PLZ_CITY.pattern}")
    print(f"  de_street   is DE_STREET.pattern:   {street == DE_STREET.pattern}")
    for sample in ("Musterstraße 7", "Muster-StraBe 10", "Gehtraining Weg 200 m"):
        print(
            f"    {sample!r:24} rule={bool(DE_STREET.search(sample))!s:5}"
            f" presidio={bool(re.search(street, sample))}"
        )

    _header("D  presidio raw: all entities, no threshold, no filters")
    raw = analyzer.analyze(
        text=text, language="de", entities=None, score_threshold=0,
        return_decision_process=True,
    )
    for r in sorted(raw, key=lambda r: r.start):
        e = r.analysis_explanation
        print(
            f"  [{r.start:2}:{r.end:2}] {r.entity_type:12} {r.score:.2f} "
            f"{text[r.start:r.end]!r:22} {e.recognizer}"
            + (f"/{e.pattern_name}" if e.pattern_name else "")
            + ("" if r.entity_type in PII_ENTITIES else "   (not in PII_ENTITIES)")
        )

    _header("E  PresidioClassifier.spans: PII_ENTITIES, >= 0.4, _keep filters")
    trace = Trace(collect=True)
    model = presidio.spans(text, trace)
    print("  trace:")
    print(trace.collected)
    print("  spans:")
    for s in model:
        print(f"  [{s.start:2}:{s.end:2}] {s.label:8} {s.text!r:22} {s.source} {s.score:.2f}")

    _header("F  side by side (what the pipeline merges for this line)")
    for start, end in sorted({(s.start, s.end) for s in rules + model}):
        by = lambda spans: ", ".join(  # noqa: E731
            f"{s.label}/{s.source}" for s in spans if (s.start, s.end) == (start, end)
        )
        print(f"  {text[start:end]!r:22} rules: {by(rules) or '-':32} presidio: {by(model) or '-'}")

    assert {(s.text, s.source) for s in rules} >= {
        ("Dr. Andrea", "rule TITLE_NAME"),
        ("Musterstraße 7", "rule DE_STREET"),
        ("12345 Musterhausen", "rule DE_PLZ_CITY"),
    }
    assert {(s.text, s.label) for s in model} >= {
        ("Andrea Muster", PiiLabel.PERSON),
        ("Musterstraße 7", PiiLabel.ADDRESS),
        ("12345 Musterhausen", PiiLabel.ADDRESS),
    }
