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


def test_a_birthdate_misread_with_commas_is_learned_and_recalled():
    lines = [cell("Andrea Muster, geb, 01,02.1980", 10, 10, width=300), cell("01.02.1980", 10, 60)]
    doc = Document(lines)
    recalled = doc.per_line(_learn(doc).recall(doc))
    assert [(s.label, s.text) for s in recalled[lines[1]]] == [(PiiLabel.DATE_OF_BIRTH, "01.02.1980")]


def test_an_invoice_number_is_recalled_bare_elsewhere():
    lines = [cell("Rechnung 123456-654321", 10, 10, width=300), cell("123456-654321 1", 10, 60)]
    doc = Document(lines)
    recalled = doc.per_line(_learn(doc).recall(doc))
    assert [(s.label, s.text) for s in recalled[lines[1]]] == [(PiiLabel.ID, "123456-654321")]


def test_a_short_number_is_not_remembered():
    lines = [cell("Rechnung 4711", 10, 10, width=300), cell("Ziffer 4711", 10, 60)]
    doc = Document(lines)
    assert doc.per_line(_learn(doc).recall(doc))[lines[1]] == []


def test_a_label_after_a_name_is_not_learned():
    lines = [cell("Herrn Andrea Muster Telefon 0123 456789", 10, 10, width=400)]
    names = _learn(Document(lines)).names
    assert "Muster" in names and "Telefon" not in names


def test_a_name_is_found_in_any_casing_that_starts_with_a_capital():
    assert mentions_name("MUSTER, ANDREA", {"Muster"}) == "MUSTER"
    assert mentions_name("klein gedruckt", {"Klein"}) is None


def test_a_name_is_found_only_as_a_whole_word():
    assert mentions_name("Allgemeine Beratung", {"Allgemein"}) is None


def test_each_half_of_a_double_name_is_learned():
    """Either half recurs alone ("Frau Beispiel" after "Muster-Beispiel")."""
    lines = [cell("Patientin: Andrea Muster-Beispiel", 10, 10, width=300)]
    assert {"Muster", "Beispiel"} <= _learn(Document(lines)).names


def _names(*texts: str) -> set[str]:
    lines = [cell(t, 10, 10 + 50 * i, width=600) for i, t in enumerate(texts)]
    return _learn(Document(lines)).names


def test_a_title_teaches_the_name_after_it_not_its_line():
    assert _names("Dr. Andrea Muster GmbH, Erikastraße 1, Musterstadt") == {"Andrea", "Muster"}


def test_a_run_continues_across_an_ampersand_and_stops_at_colleagues():
    assert _names("Dr. Dr. Muster & Beispiel MSc & Kollegen") == {"Muster", "Beispiel"}


def test_a_street_named_after_a_person_is_not_a_name():
    assert _names("Probe & Kollegen GmbH, Erika-Muster-Str 1, Musterstadt") == set()


def test_a_surname_after_title_and_forename_is_learned():
    assert _names("Prof. Dr. Dr. Erik Musterfeld") == {"Erik", "Musterfeld"}


def test_both_halves_after_an_initial_are_learned():
    assert _names("Dr. med. A.Probe-Muster") == {"Probe", "Muster"}


def test_the_sentence_around_a_birth_mark_is_not_learned():
    assert _names("Behandelt wurde Andrea Muster, *01.02.2010") == {"Andrea", "Muster"}


def test_a_heading_before_a_title_is_not_learned():
    assert _names("Ärztliche Leitung: Dr. Erika Probe-Muster") == {"Erika", "Probe", "Muster"}
    assert _names("Mikrobiologie & Pathologie der Dr. Muster & Kollegen GmbH") == {"Muster"}


def test_a_name_before_a_birth_label_is_learned():
    assert _names("Andrea Muster, geboren am 01.02.1980") == {"Andrea", "Muster"}


def test_model_spans_are_shape_checked_too():
    """A classifier calls streets, titles and OCR junk a person now and then."""
    lines = [cell("Musterstraße Prof AbCde Andrea", 10, 10, width=300)]
    doc = Document(lines)
    text = lines[0].text
    hits = {lines[0]: [Span(PiiLabel.PERSON, 0, len(text), text, "presidio")]}
    assert _learn(doc, hits).names == {"Andrea"}
