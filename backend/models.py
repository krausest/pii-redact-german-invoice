"""Small shared data types passed between the pipeline stages."""

from __future__ import annotations

from dataclasses import dataclass


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
