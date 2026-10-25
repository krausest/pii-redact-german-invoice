"""Running a classifier over a long page in line-aligned windows.

For cost, not truncation: attention cost grows quadratically with the text, and
windows make it linear. A page of ordinary length is a single window and comes
back untouched (measurements: ``docs/Redaction.md``, Classifiers).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from backend.document import line_ranges
from backend.pii import Span

# One window's word budget, and how many lines two windows share — enough that
# an entity straddling a cut is whole in one of them.
WINDOW_WORDS = 160
WINDOW_OVERLAP_LINES = 2


def make_windows(
    texts: Sequence[str],
    budget: int = WINDOW_WORDS,
    overlap: int = WINDOW_OVERLAP_LINES,
) -> list[range]:
    """Windows of line indices over ``texts``, each filled up to ``budget`` words
    but holding at least one line, overlapping by ``overlap`` lines."""
    if not texts:
        return []
    windows: list[range] = []
    start = 0
    while start < len(texts):
        words, end = 0, start
        while end < len(texts):
            n = len(texts[end].split())
            if end > start and words + n > budget:
                break
            words += n
            end += 1
        windows.append(range(start, end))
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
    """``predict`` over ``text`` window by window, in the coordinates of ``text``.
    A span two windows both saw is kept once, with the higher score."""
    texts = text.split("\n")
    ranges = line_ranges(texts)
    seen: dict[tuple[str, int, int], Span] = {}
    for window in make_windows(texts, budget=budget, overlap=overlap):
        offset = ranges[window[0]].start
        for s in predict(text[offset : ranges[window[-1]].end]):
            moved = Span(s.label, s.start + offset, s.end + offset, s.text, s.source, s.score)
            key = (str(moved.label), moved.start, moved.end)
            if key not in seen or moved.score > seen[key].score:
                seen[key] = moved
    return sorted(seen.values(), key=lambda s: (s.start, s.end))
