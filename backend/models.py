"""Small shared data types passed between the pipeline stages."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import NamedTuple

_TOKEN = re.compile(r"\S+")


class WordBox(NamedTuple):
    """Where ``text[start:end]`` of a line sits horizontally: ``x0`` to ``x1``."""

    start: int
    end: int
    x0: int
    x1: int


@dataclass(frozen=True, eq=False)
class Line:
    """One OCR text line with its axis-aligned pixel box.

    One shape for every OCR backend and classifier, rather than each speaking the
    dict-of-parallel-lists or list-of-dicts its own library returns. ``conf`` is
    the recognition confidence on a 0-100 scale (``None`` when a backend does not
    expose it). A line is itself: two OCR lines with the same text and box are
    still two lines, so it is hashed and compared by identity."""

    text: str
    left: int
    top: int
    width: int
    height: int
    conf: float | None = None
    # The OCR's own word geometry, in text order; empty: estimate by character share.
    words: tuple[WordBox, ...] = ()

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    @property
    def mid_x(self) -> float:
        return self.left + self.width / 2

    @property
    def mid_y(self) -> float:
        return self.top + self.height / 2

    @property
    def box(self) -> Box:
        return Box(self.left, self.top, self.right, self.bottom)

    def x_range(self, start: int, end: int) -> tuple[int, int]:
        """Pixel columns of ``text[start:end]``: from the OCR's word boxes when
        they cover both ends, else by the characters' share of the line width."""
        first = next((w for w in self.words if w.start <= start < w.end), None)
        last = next((w for w in self.words if w.start < end <= w.end), None)
        if first is None or last is None:
            n = max(len(self.text), 1)
            return self.left + self.width * start // n, self.left + self.width * end // n
        x0 = first.x0 + (first.x1 - first.x0) * (start - first.start) // (first.end - first.start)
        x1 = last.x0 + (last.x1 - last.x0) * (end - last.start) // (last.end - last.start)
        return x0, x1

    def tokens(self, start: int, end: int) -> tuple[int, int]:
        """``[start, end)`` widened to the whitespace-delimited tokens it touches."""
        for m in _TOKEN.finditer(self.text):
            if m.end() > start and m.start() < end:
                start = min(start, m.start())
                end = max(end, m.end())
        return start, end

    def span_box(self, start: int, end: int) -> Box:
        """The box over the tokens ``text[start:end]`` touches, full line height,
        widened to the middle of the gap to each neighbouring word, or to the
        line's edge: OCR word boxes clip the outer strokes."""
        start, end = self.tokens(start, end)
        x0, x1 = self.x_range(start, end)
        before = self.text[:start].rstrip()
        after = len(self.text) - len(self.text[end:].lstrip())
        x0 = (x0 + self.x_range(len(before) - 1, len(before))[1]) // 2 if before else min(x0, self.left)
        x1 = (x1 + self.x_range(after, after + 1)[0]) // 2 if after < len(self.text) else max(x1, self.right)
        return Box(x0, self.top, max(x1, x0 + 1), self.bottom)


@dataclass(frozen=True)
class Box:
    """An axis-aligned rectangle to blacken, in image pixel coordinates.

    ``text`` makes it a *note*: the rectangle is blackened and the text printed
    on it in white — the birth year over a redacted birthdate."""

    x0: int
    y0: int
    x1: int
    y1: int
    text: str | None = None

    def padded(self, n: int) -> Box:
        """This rectangle grown by ``n`` pixels on every side."""
        return Box(self.x0 - n, self.y0 - n, self.x1 + n, self.y1 + n)

    def contains(self, other: Box) -> bool:
        return self.x0 <= other.x0 and self.y0 <= other.y0 and other.x1 <= self.x1 and other.y1 <= self.y1

    def as_list(self) -> list[int]:
        """``[x0, y0, x1, y1]`` — the JSON shape the API returns and the shape
        ``PIL.ImageDraw.rectangle`` accepts."""
        return [self.x0, self.y0, self.x1, self.y1]
