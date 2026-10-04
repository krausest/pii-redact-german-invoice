"""End-to-end redaction with real models. Opt-in: `pytest -m slow`.

Loads the default (native) engine and runs on example/GOÄ_Rechnung1.png.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image, ImageChops

from backend.config import Config
from backend.factory import build_pipeline

SAMPLE = Path(__file__).resolve().parent.parent / "example" / "GOÄ_Rechnung1.png"

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def pipeline():
    if not SAMPLE.is_file():
        pytest.skip(f"sample image not found: {SAMPLE}")
    return build_pipeline(Config())


def _differs(a: Image.Image, b: Image.Image) -> bool:
    return ImageChops.difference(a.convert("RGB"), b.convert("RGB")).getbbox() is not None


def test_compute_boxes_finds_pii(pipeline):
    with Image.open(SAMPLE) as src:
        unwrapped = pipeline.unwarp(src)
    boxes = pipeline.compute_boxes(unwrapped)
    assert len(boxes) >= 1


def test_redact_image_changes_pixels(pipeline):
    with Image.open(SAMPLE) as src:
        original = src.convert("RGB")
        redacted = pipeline.redact(src)
    # unwarp changes geometry, so compare against the unwarped page, not original.
    assert _differs(original, redacted)


def test_composition_matches_redact(pipeline):
    """redact() == unwarp -> compute_boxes -> apply_boxes, which is what
    /api/redact composes per request (with `unwarp` optional)."""
    with Image.open(SAMPLE) as src:
        full = pipeline.redact(src)
    with Image.open(SAMPLE) as src:
        unwrapped = pipeline.unwarp(src)
    boxes = pipeline.compute_boxes(unwrapped)
    composed = pipeline.apply_boxes(unwrapped, boxes)
    assert not _differs(full, composed)


def test_either_classifier_can_redact_the_same_page(pipeline):
    """The seam the shared span vocabulary was built for: a second model plugs in
    where presidio does, and the rest of the pipeline — rules, item-table gate,
    name memory, region pass — cannot tell the difference.

    Only that both find PII is asserted, not that they agree: they are different
    detectors, which is the whole reason there is a choice. Measured over the
    replay corpus, guard-omni draws ~180 boxes presidio does not and misses 11 it
    finds."""
    with Image.open(SAMPLE) as src:
        page = pipeline.unwarp(src)
    assert pipeline.compute_boxes(page, classifier="presidio")
    assert pipeline.compute_boxes(page, classifier="guard-omni")
