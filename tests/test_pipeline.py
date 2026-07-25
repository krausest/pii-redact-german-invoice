"""Pipeline composition with stub OCR/classifier (no models)."""

from __future__ import annotations

from PIL import Image

from backend.models import Box, Line
from backend.pipeline import RedactionPipeline
from tests.conftest import RecordingUnwarper, StubClassifier, StubOCR


def _pipeline(lines, pii_subs, **kwargs):
    return RedactionPipeline(
        ocr=StubOCR(lines),
        classifier=StubClassifier(pii_subs),
        **kwargs,
    )


def test_apply_boxes_fills_exact_rectangle():
    img = Image.new("RGB", (50, 50), (255, 255, 255))
    p = _pipeline([], [], fill=(0, 0, 0))
    out = p.apply_boxes(img, [Box(10, 10, 20, 20)])
    assert out.getpixel((15, 15)) == (0, 0, 0)  # inside the box
    assert out.getpixel((40, 40)) == (255, 255, 255)  # outside


def test_compute_boxes_flags_only_pii_lines():
    lines = [
        Line("Rechnung Nr 5", left=0, top=0, width=40, height=10),
        Line("Max Mustermann", left=5, top=20, width=60, height=12),
    ]
    p = _pipeline(lines, ["Mustermann"], padding=2)
    boxes = p.compute_boxes(Image.new("RGB", (100, 100)))
    assert boxes == [Box(3, 18, 67, 34)]  # only the second line, padded by 2


def test_compute_boxes_does_not_unwarp():
    unwarper = RecordingUnwarper()
    p = _pipeline([], [], unwarper=unwarper)
    p.compute_boxes(Image.new("RGB", (30, 30)))
    assert unwarper.calls == 0


def test_static_rule_redacts_without_classifier_hit():
    lines = [Line("Musterstrasse 23", left=0, top=0, width=80, height=10)]
    p = _pipeline(lines, [])  # classifier flags nothing
    assert len(p.compute_boxes(Image.new("RGB", (100, 100)))) == 1


def test_redact_unwarps_then_applies_boxes():
    lines = [Line("Max Mustermann", left=0, top=0, width=40, height=10)]
    unwarper = RecordingUnwarper()
    p = _pipeline(lines, ["Mustermann"], unwarper=unwarper, unwarp_enabled=True, fill=(0, 0, 0))
    out = p.redact(Image.new("RGB", (50, 50), (255, 255, 255)))
    assert unwarper.calls == 1
    # box drawn on the unwarped (recolored) canvas: inside black, elsewhere the
    # unwarper's fill (10, 20, 30).
    assert out.getpixel((5, 5)) == (0, 0, 0)
    assert out.getpixel((45, 45)) == (10, 20, 30)


def test_redact_without_unwarp_uses_original():
    lines = [Line("Max Mustermann", left=0, top=0, width=40, height=10)]
    p = _pipeline(lines, ["Mustermann"], unwarp_enabled=False, fill=(0, 0, 0))
    out = p.redact(Image.new("RGB", (50, 50), (255, 255, 255)))
    assert out.getpixel((45, 45)) == (255, 255, 255)  # original background kept
