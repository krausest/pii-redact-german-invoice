"""The user's word lists (``backend.wordlists``)."""

from __future__ import annotations

import pytest

from backend.document import Document
from backend.models import Line
from backend.pii import PiiLabel, Span
from backend.wordlists import WordLists


def _doc(*texts):
    return Document([Line(t, 0, 20 * i, 100, 10) for i, t in enumerate(texts)])


@pytest.mark.parametrize(
    "entry,text,hit",
    [
        ("Muster", "Andrea Muster", "Muster"),
        ("muster", "ANDREA MUSTER,", "MUSTER"),
        ("Muster", "Mustermann", None),
        ("Andrea Muster", "Frau Andrea   Muster", "Andrea   Muster"),
        ("Dr. Muster", "Dr. Muster", "Dr. Muster"),
    ],
)
def test_an_entry_matches_a_whole_word_or_phrase_in_any_casing(entry, text, hit):
    spans = WordLists(redact=(entry,)).redact_spans(_doc(text))
    assert [s.text for s in spans] == ([hit] if hit else [])


def test_a_phrase_wrapped_across_lines_matches():
    doc = _doc("Andrea", "Muster")
    [span] = WordLists(redact=("Andrea Muster",)).redact_spans(doc)
    assert (span.label, span.source) == (PiiLabel.LISTED, "user-list")
    assert set(doc.per_line([span])) == set(doc.lines)


def test_keep_vetoes_a_span_containing_the_entry():
    lists = WordLists(keep=("Muster",))
    assert lists.vetoes(Span(PiiLabel.PERSON, 0, 13, "Andrea Muster", "stub"))
    assert not lists.vetoes(Span(PiiLabel.PERSON, 0, 10, "Mustermann", "stub"))
    assert not WordLists().vetoes(Span(PiiLabel.PERSON, 0, 6, "Muster", "stub"))
