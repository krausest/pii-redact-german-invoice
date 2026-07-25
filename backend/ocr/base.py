"""The OCR backend interface."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from PIL import Image

from backend.models import Line


@runtime_checkable
class OCRBackend(Protocol):
    """Reads an image and returns its text lines with pixel-precise boxes."""

    def lines(self, image: Image.Image) -> list[Line]: ...
