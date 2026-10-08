"""One page's OCR lines as a single text, and entity spans mapped back onto them.

The pipeline classifies a *document*, not a line. This module is the bridge in
both directions: a :class:`Document` joins the lines (already in reading order —
see :func:`backend.layout.document_order`) into the text a detector is asked
about, and :meth:`Document.per_line` takes the findings back to the lines whose
pixels have to be blackened.

Ported from the ``wholetext`` experiment on the ``guard_omni`` branch, minus its
sliding-window machinery: windows exist there because guard-omni's encoder caps
at 512 tokens, and presidio has no such limit. They come back when guard-omni
does.

Pure code over ``Line``/``Span``: nothing here imports a model.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
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
            zip(self.lines, _line_ranges([ln.text for ln in self.lines]))
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


def _line_ranges(texts: Sequence[str]) -> list[TextRange]:
    """Each text's range once they are joined with ``"\n"``."""
    ranges: list[TextRange] = []
    pos = 0
    for text in texts:
        ranges.append(TextRange(pos, pos + len(text)))
        pos += len(text) + 1  # + the "\n"
    return ranges


# One window's word budget, and how many lines two windows share. The overlap is
# in *lines*, not words, because windows are cut on line boundaries: two lines
# is enough that an entity straddling a cut is still whole in one of them.
WINDOW_WORDS = 160
WINDOW_OVERLAP_LINES = 2


def make_windows(
    texts: Sequence[str],
    budget: int = WINDOW_WORDS,
    overlap: int = WINDOW_OVERLAP_LINES,
) -> list[tuple[int, int]]:
    """Line-index windows ``[start, end)`` over ``texts``, greedily filled to
    ``budget`` words, always taking at least one line even when it alone busts
    the budget. Consecutive windows overlap by ``overlap`` lines."""
    if not texts:
        return []
    windows: list[tuple[int, int]] = []
    start = 0
    while start < len(texts):
        words, end = 0, start
        while end < len(texts):
            n = len(texts[end].split())
            if end > start and words + n > budget:
                break
            words += n
            end += 1
        windows.append((start, end))
        if end >= len(texts):
            break
        start = max(end - overlap, start + 1)
    return windows


def windowed(
    text: str,
    predict: Callable[[str], list[Span]],
    budget: int = WINDOW_WORDS,
    overlap: int = WINDOW_OVERLAP_LINES,
) -> list[Span]:
    """Run ``predict`` over ``text`` in line-aligned windows, in *document*
    coordinates, deduped by ``(label, start, end)`` with the higher score kept
    where two overlapping windows both saw a span.

    **This is about cost, not truncation.** The wholetext experiment introduced
    windowing believing mdeberta-v3-base caps at 512 positions; measured here it
    does not — that encoder uses relative position embeddings, and a planted
    entity at 99 % of a 1600-word document is still found in one shot. What is
    real is the quadratic attention cost: 204 words took 0.2 s, 804 took 0.9 s,
    1604 took 3.4 s, and 3200 did not finish inside ten minutes. Windowing turns
    that curve linear.

    On this corpus it is a no-op — the longest page is 345 words, one window —
    which is exactly the shape wanted: it costs nothing on every real page and
    bounds the one dense page nobody has sent yet. A single-window document is
    returned in its own coordinates untouched, so there is no second code path.

    The lines come back out of ``text`` rather than being passed in:
    :class:`Document` joined them with ``"\\n"`` and nothing else, so the
    split is exact and the classifier interface stays one string wide.
    """
    texts = text.split("\n")
    bounds = _line_ranges(texts)

    seen: dict[tuple[str, int, int], Span] = {}
    for first, last in make_windows(texts, budget=budget, overlap=overlap):
        offset = bounds[first].start
        for s in predict(text[offset : bounds[last - 1].end]):
            moved = Span(
                label=s.label,
                start=s.start + offset,
                end=s.end + offset,
                text=s.text,
                source=s.source,
                score=s.score,
            )
            key = (str(moved.label), moved.start, moved.end)
            if key not in seen or moved.score > seen[key].score:
                seen[key] = moved
    return sorted(seen.values(), key=lambda s: (s.start, s.end))
