"""Joining a page into one text, and finding the way back to the lines.

The whole-document switch lives or dies on these two mappings being exact: an
off-by-one in a line's range puts a box on the wrong line, which is worse than no
box at all because the page looks redacted.
"""

from __future__ import annotations

from backend.document import Document
from backend.models import Line
from backend.pii import PiiLabel, Span


def _line(text):
    return Line(text=text, left=0, top=0, width=10, height=10)


def _span(label, start, end, text):
    return Span(label=label, start=start, end=end, text=text, source="test")

#ok
def test_lines_are_joined_by_a_newline_never_a_space():
    """Two words on separate OCR lines must not fuse into one token — a phone
    number ending a line and a house number starting the next would read as a
    single number."""
    doc = Document([_line("0911 12"), _line("34 56")])
    assert doc.text == "0911 12\n34 56"
    assert [doc.range_of(ln) for ln in doc.lines] == [(0, 7), (8, 13)]
    second = doc.range_of(doc.lines[1])
    assert doc.text[second.start : second.end] == "34 56"

#ok
def test_an_empty_line_still_takes_a_position():
    """A detector shifting its offsets by a line's range depends on every line
    having one, including one OCR returned empty."""
    doc = Document([_line("a"), _line(""), _line("b")])
    assert doc.text == "a\n\nb"
    assert [doc.range_of(ln) for ln in doc.lines] == [(0, 1), (2, 2), (3, 4)]


def test_a_span_across_a_wrap_touches_both_lines():
    """The capability the switch exists for: "Max" and "Mustermann" on separate
    lines are one entity, and both lines have to be blackened."""
    lines = [_line("Max"), _line("Mustermann")]
    doc = Document(lines)
    per_line = doc.per_line([_span(PiiLabel.PERSON, 0, len(doc.text), doc.text)])
    assert [s.text for s in per_line[lines[0]]] == ["Max"]
    assert [s.text for s in per_line[lines[1]]] == ["Mustermann"]


def test_a_span_reaching_a_later_line_keeps_its_tail_not_its_head():
    lines = [_line("aaa"), _line("bbbb")]
    per_line = Document(lines).per_line([_span(PiiLabel.PERSON, 1, 6, "aa\nbb")])
    assert [s.text for s in per_line[lines[0]]] == ["aa"]
    assert [s.text for s in per_line[lines[1]]] == ["bb"]


def test_a_whole_line_span_covers_the_line_and_quotes_its_text():
    lines = [_line("Herrn"), _line("Andrea Muster")]
    doc = Document(lines)
    span = doc.whole_line(lines[1], PiiLabel.PERSON, "Muster", "name-memory")
    assert (span.start, span.end, span.text) == (6, 19, "Muster")


def test_identical_lines_are_one_line():
    """Same text, same box: the same pixels, so one key for every per-line result."""
    doc = Document([_line("Muster"), _line("Muster")])
    assert list(doc.per_line([])) == [_line("Muster")]
