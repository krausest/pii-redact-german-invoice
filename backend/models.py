"""Small shared data types passed between the pipeline stages."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Line:
    """One OCR text line with its axis-aligned pixel box.

    Unifies the two shapes the old scripts used (Presidio's dict of parallel
    lists and gliner's list of dicts) so every OCR backend and classifier speaks
    the same language. ``conf`` is the recognition confidence on Presidio's
    0-100 scale (``None`` when a backend does not expose it)."""

    text: str
    left: int
    top: int
    width: int
    height: int
    conf: float | None = None


@dataclass(frozen=True)
class Box:
    """An axis-aligned rectangle to blacken, in image pixel coordinates."""

    x0: int
    y0: int
    x1: int
    y1: int

    def as_list(self) -> list[int]:
        """``[x0, y0, x1, y1]`` — the JSON shape the API returns and the shape
        ``PIL.ImageDraw.rectangle`` accepts."""
        return [self.x0, self.y0, self.x1, self.y1]
