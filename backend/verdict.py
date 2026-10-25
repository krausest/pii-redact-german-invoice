"""What one page decided, as one object: the input of the trace, the source of the boxes."""

from __future__ import annotations

from dataclasses import dataclass

from backend.layout import LayoutRegion, RegionBox, SignatureBox
from backend.models import Box, Line
from backend.pii import Span


@dataclass(frozen=True)
class PageVerdict:
    """Which lines of a page go black and why, which stay readable and why, and
    the boxes beyond single lines.

    ``found`` holds the spans that redact a line, ``dropped`` the ones a kept
    line rejected, ``keep`` why a line stays readable. ``reading`` is the order
    the page was read in."""

    lines: list[Line]
    regions: list[LayoutRegion]
    reading: list[Line]
    found: dict[Line, list[Span]]
    keep: dict[Line, str]
    dropped: dict[Line, list[Span]]
    line_boxes: list[Box]
    notes: dict[Line, Box]
    region_boxes: list[RegionBox]
    signatures: list[SignatureBox]

    @property
    def boxes(self) -> list[Box]:
        """Every box to fill; the notes last, so no box covers their text."""
        return [
            *self.line_boxes,
            *(hit.box for hit in self.region_boxes),
            *(sig.box for sig in self.signatures),
            *self.notes.values(),
        ]
