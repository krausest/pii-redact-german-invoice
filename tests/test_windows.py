"""Classifying a long page in line-aligned windows (``backend.classifiers.windows``)."""

from __future__ import annotations

from backend.classifiers.windows import make_windows, windowed
from backend.pii import PiiLabel, Span


def test_windows_fill_to_the_budget_and_overlap():
    texts = ["a b", "c", "d e", "f"]
    assert make_windows(texts, budget=3, overlap=1) == [range(0, 2), range(1, 3), range(2, 4)]


def test_a_line_over_the_budget_is_a_window_of_its_own():
    assert make_windows(["a b c d", "e"], budget=2, overlap=0) == [range(0, 1), range(1, 2)]


def test_no_text_no_windows():
    assert make_windows([]) == []


def _first_word(chunk: str) -> list[Span]:
    word = chunk.split()[0]
    return [Span(PiiLabel.PERSON, 0, len(word), word, "test", score=len(chunk) / 100)]


def test_spans_come_back_in_page_coordinates():
    text = "Andrea x\nMuster y\nBeispiel z"
    spans = windowed(text, _first_word, budget=2, overlap=0)
    assert [(text[s.start : s.end], s.start) for s in spans] == [
        ("Andrea", 0),
        ("Muster", 9),
        ("Beispiel", 18),
    ]


def _the_c(chunk: str) -> list[Span]:
    """Finds "c", more sure of it when the window starts there."""
    if "c" not in chunk:
        return []
    start = chunk.index("c")
    return [Span(PiiLabel.PERSON, start, start + 1, "c", "test", score=0.9 if start == 0 else 0.5)]


def test_a_span_two_windows_saw_is_kept_once_with_the_higher_score():
    text = "a b\nc d\ne f"
    spans = windowed(text, _the_c, budget=4, overlap=1)
    assert [(s.start, s.score) for s in spans] == [(text.index("c"), 0.9)]
