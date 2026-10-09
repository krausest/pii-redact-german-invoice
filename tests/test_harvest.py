"""The name and birthdate memory (``backend.harvest.Memory``)."""

from __future__ import annotations

from backend.document import Document
from backend.harvest import Memory, mentions_name
from backend.pii import PiiLabel, Span
from backend.rules import rule_spans
from backend.trace import Trace
from tests.conftest import cell


def _learn(doc: Document, hits=None) -> Memory:
    memory = Memory()
    memory.learn(doc, hits if hits is not None else doc.per_line(rule_spans(doc)), Trace())
    return memory


def test_a_labeled_name_is_recalled_bare_elsewhere():
    lines = [
        cell("Patient: Muster, Andrea", 10, 10, width=300),
        cell("Sehr geehrte Damen und Herren, Muster", 10, 60, width=300),
    ]
    doc = Document(lines)
    memory = _learn(doc)
    assert {"Muster", "Andrea"} <= memory.names
    recalled = doc.per_line(memory.recall(doc))
    assert [s.label for s in recalled[lines[1]]] == [PiiLabel.PERSON]


def test_a_model_span_teaches_only_itself_not_its_line():
    """A letterhead line holds a person and a company: only the span is a name."""
    lines = [cell("Andrea Muster Beispielwerke GmbH", 10, 10, width=300)]
    doc = Document(lines)
    hits = {lines[0]: [Span(PiiLabel.PERSON, 0, 13, "Andrea Muster", "presidio")]}
    assert _learn(doc, hits).names == {"Andrea", "Muster"}


def test_stopwords_are_compared_case_folded():
    """An all-caps letterhead must not teach its profession as a name."""
    lines = [cell("DR. MED. ANDREA MUSTER, FACHARZT", 10, 10, width=300)]
    assert _learn(Document(lines)).names == {"ANDREA", "MUSTER"}


def test_a_birthdate_is_recalled_in_another_spelling():
    lines = [cell("geb. 01.02.1980", 10, 10, width=200), cell("1.2.80", 10, 60)]
    doc = Document(lines)
    recalled = doc.per_line(_learn(doc).recall(doc))
    assert [(s.label, s.text) for s in recalled[lines[1]]] == [(PiiLabel.DATE_OF_BIRTH, "1.2.80")]


def test_a_name_is_found_in_any_casing_that_starts_with_a_capital():
    assert mentions_name("MUSTER, ANDREA", {"Muster"}) == "MUSTER"
    assert mentions_name("klein gedruckt", {"Klein"}) is None


def test_a_name_is_found_only_as_a_whole_word():
    assert mentions_name("Allgemeine Beratung", {"Allgemein"}) is None


def test_each_half_of_a_double_name_is_learned():
    """Either half recurs alone ("Frau Beispiel" after "Muster-Beispiel")."""
    lines = [cell("Patientin: Andrea Muster-Beispiel", 10, 10, width=300)]
    assert {"Muster", "Beispiel"} <= _learn(Document(lines)).names
