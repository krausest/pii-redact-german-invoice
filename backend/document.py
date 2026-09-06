"""One page's OCR lines as a single text, and entity spans mapped back onto them.

The pipeline classifies a *document*, not a line. This module is the bridge in
both directions: :func:`build_document` joins the lines (already in reading
order — see :func:`backend.layout.document_order`) into the text a detector is
asked about, and :func:`spans_to_lines` takes the findings back to the lines
whose pixels have to be blackened.

Ported from the ``wholetext`` experiment on the ``guard_omni`` branch, minus its
sliding-window machinery: windows exist there because guard-omni's encoder caps
at 512 tokens, and presidio has no such limit. They come back when guard-omni
does.

Pure functions over ``Line``/``Span``: nothing here imports a model.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from backend.models import Line
from backend.pii import Span


def build_document(lines: Sequence[Line]) -> tuple[str, list[tuple[int, int]]]:
    """Join every line's text with ``"\\n"`` and return the joined text
    alongside each line's ``(start, end)`` char-offset span in it.

    ``"\\n"`` rather than a space: two words on separate OCR lines must not run
    together into what looks like one token — a phone number ending a line and a
    house number starting the next would otherwise fuse into a single number.

    ``bounds`` is index-aligned with ``lines``, and that alignment is the
    contract every caller relies on: a detector shifts its per-line offsets by
    ``bounds[i][0]``, and :func:`spans_to_lines` maps them back the same way.
    """
    parts: list[str] = []
    bounds: list[tuple[int, int]] = []
    pos = 0
    for line in lines:
        parts.append(line.text)
        bounds.append((pos, pos + len(line.text)))
        pos += len(line.text) + 1  # + the "\n"
    return "\n".join(parts), bounds


def spans_to_lines(
    spans: Sequence[Span], bounds: Sequence[tuple[int, int]]
) -> list[list[Span]]:
    """Per-line list of spans clipped to that line's character range,
    index-aligned with ``bounds`` (and so with the lines it was built from).

    A span that only *partially* overlaps a line still touches it. That is the
    whole point of classifying a document rather than a line: a name broken
    across a wrap ("Max\\nMustermann") is one span, and both lines it crosses
    have to be blackened, not just the one holding its start.
    """
    per_line: list[list[Span]] = [[] for _ in bounds]
    for s in spans:
        for i, (lo, hi) in enumerate(bounds):
            start, end = max(s.start, lo), min(s.end, hi)
            if start < end:
                # Slice relative to the *span's* own start, not the line's — a
                # span reaching a second line needs its tail, not its head.
                per_line[i].append(
                    Span(
                        label=s.label,
                        start=start - lo,
                        end=end - lo,
                        text=s.text[start - s.start : end - s.start],
                        source=s.source,
                        score=s.score,
                    )
                )
    return per_line


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
    :func:`build_document` joined them with ``"\\n"`` and nothing else, so the
    split is exact and the classifier interface stays one string wide.
    """
    texts = text.split("\n")
    bounds: list[tuple[int, int]] = []
    pos = 0
    for line in texts:
        bounds.append((pos, pos + len(line)))
        pos += len(line) + 1

    seen: dict[tuple[str, int, int], Span] = {}
    for first, last in make_windows(texts, budget=budget, overlap=overlap):
        offset = bounds[first][0]
        for s in predict(text[offset : bounds[last - 1][1]]):
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
