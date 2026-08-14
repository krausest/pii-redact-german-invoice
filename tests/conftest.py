"""Shared test fixtures and lightweight stubs (no ML models load in fast tests)."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from backend.models import Box, Line
from backend.pdf import assemble_pdf


def make_image_bytes(fmt: str = "PNG", size=(64, 48), color=(200, 200, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format=fmt)
    return buf.getvalue()


def make_pdf_bytes(pages: int = 2, size=(40, 30)) -> bytes:
    """A blank N-page PDF. Assembled at ``assemble_pdf``'s default 72 dpi, so a
    page is ``size`` in points — tests that check rasterization dimensions compute
    against that."""
    return assemble_pdf([Image.new("RGB", size, (255, 255, 255)) for _ in range(pages)])


class StubOCR:
    """Returns a fixed set of lines regardless of the image."""

    def __init__(self, lines: list[Line]):
        self._lines = lines

    def lines(self, image):  # noqa: ARG002 - image ignored on purpose
        return list(self._lines)


class StubClassifier:
    """Flags a line as PII when its text contains any of the given substrings."""

    def __init__(self, pii_substrings: list[str] | None = None):
        self._subs = pii_substrings or []

    def is_pii(self, text: str, trace) -> bool:
        trace.add("    stub match" if (hit := any(s in text for s in self._subs)) else "    no match")
        return hit


class RecordingUnwarper:
    """Records calls and returns a distinctly recolored copy so unwarp is
    observable in the output."""

    def __init__(self):
        self.calls = 0

    def unwarp(self, image):
        self.calls += 1
        return Image.new("RGB", image.size, (10, 20, 30))


class FakePipeline:
    """A pipeline test double that records which primitive each endpoint calls,
    without loading any model."""

    def __init__(self, boxes: list[Box] | None = None):
        self.boxes = boxes if boxes is not None else [Box(1, 2, 3, 4)]
        self.calls: list[str] = []

    def unwarp(self, image):
        self.calls.append("unwarp")
        return Image.new("RGB", image.size, (10, 20, 30))

    def compute_boxes(self, image, lines=None, known_names=None, trace=None):  # noqa: ARG002
        self.calls.append("compute_boxes")
        if trace is not None:
            trace.add("fake pipeline: %d box(es)", len(self.boxes))
        return list(self.boxes)

    def apply_boxes(self, image, boxes, fill=None):  # noqa: ARG002
        self.calls.append("apply_boxes")
        return image

    def redact(self, image):
        self.calls.append("redact")
        return Image.new("RGB", image.size, (0, 0, 0))


def pytest_addoption(parser):
    parser.addoption(
        "--regression",
        action="store_true",
        default=False,
        help="also run the OCR-replay regression suite (loads the classifier)",
    )


def pytest_collection_modifyitems(config, items):
    """Make ``regression`` opt-in via a flag rather than a marker expression.

    A marker alone would not do it: the documented selectors are ``-m 'not slow'``
    and ``-m slow``, and a command-line ``-m`` replaces anything in ``addopts``,
    so one of the two would always drag the suite in. A flag is orthogonal to
    ``-m`` and holds whatever the caller selects."""
    if config.getoption("--regression"):
        return
    skip = pytest.mark.skip(reason="needs --regression")
    for item in items:
        if "regression" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def png_bytes() -> bytes:
    return make_image_bytes("PNG")


@pytest.fixture
def jpeg_bytes() -> bytes:
    return make_image_bytes("JPEG")
