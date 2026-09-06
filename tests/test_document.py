"""Joining a page into one text, and finding the way back to the lines.

The whole-document switch lives or dies on these two mappings being exact: an
off-by-one in ``bounds`` puts a box on the wrong line, which is worse than no
box at all because the page looks redacted.
"""

from __future__ import annotations

from backend.document import build_document, spans_to_lines
from backend.models import Line
from backend.pii import PiiLabel, Span


def _line(text):
    return Line(text=text, left=0, top=0, width=10, height=10)


def _span(label, start, end, text):
    return Span(label=label, start=start, end=end, text=text, source="test")


def test_lines_are_joined_by_a_newline_never_a_space():
    """Two words on separate OCR lines must not fuse into one token — a phone
    number ending a line and a house number starting the next would read as a
    single number."""
    text, bounds = build_document([_line("0911 12"), _line("34 56")])
    assert text == "0911 12\n34 56"
    assert bounds == [(0, 7), (8, 13)]
    assert text[bounds[1][0] : bounds[1][1]] == "34 56"


def test_an_empty_line_still_takes_a_position():
    """`bounds` is index-aligned with the lines it was built from; a detector
    shifting its offsets by `bounds[i][0]` depends on that alignment holding for
    every line, including one OCR returned empty."""
    text, bounds = build_document([_line("a"), _line(""), _line("b")])
    assert text == "a\n\nb"
    assert len(bounds) == 3


def test_a_span_across_a_wrap_touches_both_lines():
    """The capability the switch exists for: "Max" and "Mustermann" on separate
    lines are one entity, and both lines have to be blackened."""
    lines = [_line("Max"), _line("Mustermann")]
    text, bounds = build_document(lines)
    per_line = spans_to_lines([_span(PiiLabel.PERSON, 0, len(text), text)], bounds)
    assert [s.text for s in per_line[0]] == ["Max"]
    assert [s.text for s in per_line[1]] == ["Mustermann"]


def test_a_span_reaching_a_later_line_keeps_its_tail_not_its_head():
    lines = [_line("aaa"), _line("bbbb")]
    _, bounds = build_document(lines)
    per_line = spans_to_lines([_span(PiiLabel.PERSON, 1, 6, "aa\nbb")], bounds)
    assert [s.text for s in per_line[0]] == ["aa"]
    assert [s.text for s in per_line[1]] == ["bb"]
