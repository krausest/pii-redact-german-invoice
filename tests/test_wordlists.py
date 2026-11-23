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


@pytest.mark.parametrize(
    "entry,text,rest",
    [
        ("Andrea", "Andrea Muster", ["Muster"]),
        ("Muster", "Andrea Muster", ["Andrea"]),
        ("muster", "Frau Andrea MUSTER,", ["Frau Andrea"]),
        ("Andrea", "Muster, Andrea Erika", ["Muster", "Erika"]),
        ("Andrea Muster", "Dr. Andrea Muster", ["Dr"]),
        ("Andrea Muster", "Andrea Muster", []),
        ("Muster", "Mustermann", ["Mustermann"]),
    ],
)
def test_keep_carves_the_entry_out_of_a_span(entry, text, rest):
    span = Span(PiiLabel.PERSON, 5, 5 + len(text), text, "stub", 0.8)
    got = WordLists(keep=(entry,)).carve(span)
    assert [s.text for s in got] == rest
    for s in got:
        assert (s.label, s.source, s.score) == (span.label, span.source, span.score)
        assert text[s.start - 5 : s.end - 5] == s.text


def test_no_keep_list_leaves_the_span_whole():
    span = Span(PiiLabel.PERSON, 0, 6, "Muster", "stub")
    assert WordLists().carve(span) == [span]


@pytest.mark.parametrize(
    "text,hit",
    [
        ("Beratung.", True),
        ("Beratung)", True),
        ("Beratung-", True),
        ("(Beratung", True),
        ("Beratung, auch telefonisch", True),
        ("Beratungsfehler", False),
        ("Fehlberatung", False),
        ("Beratung2", False),
    ],
)
def test_a_symbol_ends_an_entry_like_whitespace_a_letter_does_not(text, hit):
    """Both lists match the same way: only a letter or digit beside the entry
    makes it part of a longer word."""
    lists = WordLists(redact=("Beratung",), keep=("Beratung",))
    assert bool(lists.redact_spans(_doc(text))) is hit
    span = Span(PiiLabel.PERSON, 0, len(text), text, "stub")
    assert (lists.carve(span) != [span]) is hit
