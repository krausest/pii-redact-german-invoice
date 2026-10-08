"""One page's OCR lines as a single text, and entity spans mapped back onto them.

The pipeline classifies a *document*, not a line: a :class:`Document` joins the
lines (in reading order, see :func:`backend.layout.document_order`) into the text
a detector reads, and :meth:`Document.per_line` takes its findings back to the
lines whose pixels have to be blackened.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import NamedTuple

from backend.models import Line
from backend.pii import PiiLabel, Span

class TextRange(NamedTuple):
    """A ``[start, end)`` character range in the page text."""

    start: int
    end: int


class Document:
    """One page's lines in reading order, joined into the text a detector reads.

    The lines are joined with ``"\n"``, never a space: a phone number ending one
    line and a house number starting the next must not fuse into one number.
    A line is addressed by itself; two identical lines (same text, same box) are
    one line. ``item_table`` holds the lines of the fee table, where the
    classifier's findings do not count (empty: no table).
    """

    def __init__(self, lines: Sequence[Line], item_table: set[Line] | None = None) -> None:
        self.lines: list[Line] = list(lines)
        self.item_table: set[Line] = item_table or set()
        self.text: str = "\n".join(ln.text for ln in self.lines)
        self._ranges: dict[Line, TextRange] = dict(
            zip(self.lines, line_ranges([ln.text for ln in self.lines]))
        )

    def range_of(self, line: Line) -> TextRange:
        """Where ``line``'s text sits in :attr:`text`."""
        return self._ranges[line]

    def whole_line(self, line: Line, label: PiiLabel, text: str, source: str) -> Span:
        """A span over all of ``line``, quoting ``text``."""
        r = self._ranges[line]
        return Span(label, r.start, r.end, text, source)

    def per_line(self, spans: Sequence[Span]) -> dict[Line, list[Span]]:
        """Each line's spans, clipped to the line and in its own coordinates.

        A span crossing a wrap ("Max\\nMustermann") touches every line it crosses:
        both have to be blackened, not just the one holding its start."""
        found: dict[Line, list[Span]] = {ln: [] for ln in self._ranges}
        for s in spans:
            for line, r in self._ranges.items():
                start, end = max(s.start, r.start), min(s.end, r.end)
                if start < end:
                    found[line].append(
                        Span(
                            label=s.label,
                            start=start - r.start,
                            end=end - r.start,
                            text=s.text[start - s.start : end - s.start],
                            source=s.source,
                            score=s.score,
                        )
                    )
        return found


def line_ranges(texts: Sequence[str]) -> list[TextRange]:
    """Each text's range once they are joined with ``"\n"``."""
    ranges: list[TextRange] = []
    pos = 0
    for text in texts:
        ranges.append(TextRange(pos, pos + len(text)))
        pos += len(text) + 1  # + the "\n"
    return ranges
