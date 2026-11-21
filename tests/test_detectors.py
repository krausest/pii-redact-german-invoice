"""What each detector may still redact on a kept line (``backend.detectors``)."""

from __future__ import annotations

import pytest

from backend.detectors import ListDetector, MemoryDetector, ModelDetector, RuleDetector
from backend.harvest import Memory
from backend.pii import PiiLabel, Span
from backend.wordlists import WordLists

RULES, MODEL, MEMORY = RuleDetector(), ModelDetector(classifier=None), MemoryDetector(Memory())
LIST = ListDetector(WordLists())


@pytest.mark.parametrize(
    "detector,source,keep,admitted",
    [
        (RULES, "rule DE_STREET", None, True),
        (MODEL, "presidio", None, True),
        # a deterministic rule wins over every keep reason ...
        (RULES, "rule DE_STREET", "item table", True),
        (RULES, "labeled-value", "DATE", True),
        (RULES, "rule ORG_LEGAL", "DIAG", True),
        # ... except a legal form on the clearing house's name
        (RULES, "rule ORG_LEGAL", "PVS", False),
        # the model survives no kept line
        (MODEL, "presidio", "item table", False),
        (MODEL, "guard-omni", "DIAG", False),
        # the memory reaches the table, a date or a diagnosis, not FACH or PVS
        (MEMORY, "name-memory", "item table", True),
        (MEMORY, "birthdate-memory", "DIAG", True),
        (MEMORY, "name-memory", "FACH", False),
        (MEMORY, "birthdate-memory", "PVS", False),
        # the user's list wins over every keep reason
        (LIST, "user-list", "item table", True),
        (LIST, "user-list", "PVS", True),
    ],
)
def test_what_a_kept_line_admits(detector, source, keep, admitted):
    span = Span(PiiLabel.PERSON, 0, 13, "Andrea Muster", source)
    assert detector.admits(span, keep) is admitted


class _Fixed:
    """A classifier reporting fixed substrings of the document as PERSON."""

    def __init__(self, *subs: str) -> None:
        self._subs = subs

    def spans(self, text, trace):  # noqa: ARG002
        return [Span(PiiLabel.PERSON, text.index(s), text.index(s) + len(s), s, "presidio") for s in self._subs]


@pytest.mark.parametrize(
    "text,found,kept",
    [
        ("Patient Muster, Andrea geb. 01.02.1980", "Andrea geb.", ["Andrea"]),
        ("Behandelte Person: Andrea Muster", "Person: Andrea Muster", ["Andrea Muster"]),
        ("Telefon Muster", "Telefon Muster", ["Muster"]),
        ("Telefon: 0123", "Telefon:", []),
        ("IK 123456789", "IK 123456789", ["123456789"]),
        ("IK-Nr.: 123456789", "IK-Nr.: 123456789", ["123456789"]),
        ("IK-Zeichen 123456789", "IK-Zeichen 123456789", ["123456789"]),
    ],
)
def test_a_model_span_leaves_its_label_readable(text, found, kept):
    from backend.document import Document
    from backend.models import Line
    from backend.trace import Trace

    doc = Document([Line(text=text, left=0, top=0, width=100, height=10)])
    spans = ModelDetector(_Fixed(found)).spans(doc, Trace())
    assert [s.text for s in spans] == kept
