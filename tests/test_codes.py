"""Barcodes and QR codes are blackened whole (``backend.codes``)."""

from __future__ import annotations

import random

import cv2
from PIL import Image, ImageDraw

from backend.codes import code_boxes
from backend.models import Line
from backend.pipeline import RedactionPipeline
from backend.trace import Trace
from tests.conftest import StubClassifier, StubOCR

# A GiroCode payload: payee and IBAN, all synthetic.
_GIROCODE = "BCD\n002\n1\nSCT\n\nAndrea Muster\nDE00123456780000000000"


def _page_with_qr() -> Image.Image:
    qr = cv2.QRCodeEncoder.create().encode(_GIROCODE)
    qr = cv2.resize(qr, None, fx=8, fy=8, interpolation=cv2.INTER_NEAREST)
    page = Image.new("L", (800, 800), 255)
    page.paste(Image.fromarray(qr), (200, 300))
    return page.convert("RGB")


def _page_with_bars(top: int, height: int) -> Image.Image:
    page = Image.new("RGB", (1200, 600), (255, 255, 255))
    draw = ImageDraw.Draw(page)
    rng = random.Random(7)
    x = 200
    while x < 900:
        width = rng.choice((3, 3, 6, 9))
        draw.rectangle((x, top, x + width - 1, top + height - 1), fill=(0, 0, 0))
        x += width + rng.choice((3, 6, 9))
    return page


def test_a_qr_code_is_found_whole():
    (code,) = code_boxes(_page_with_qr(), padding=0)
    assert code.kind == "qr"
    assert code.box.x0 <= 240 and code.box.y0 <= 340 and code.box.x1 >= 400 and code.box.y1 >= 500


def test_a_barcode_box_reaches_over_all_its_bars():
    """The detector alone reports a narrower band than the bars' height."""
    codes = code_boxes(_page_with_bars(top=200, height=120), padding=0)
    assert codes and {c.kind for c in codes} == {"barcode"}
    assert all((c.box.y0, c.box.y1) == (200, 320) for c in codes)


def test_a_page_of_text_has_no_code():
    page = Image.new("RGB", (800, 400), (255, 255, 255))
    draw = ImageDraw.Draw(page)
    for row in range(10):
        draw.text((20, 20 + 35 * row), "Beratung, auch mittels Fernsprecher 1 2,30 10,72 EUR", fill=(0, 0, 0))
    assert code_boxes(page, padding=0) == []


def test_the_pipeline_blackens_a_code_and_traces_it():
    trace = Trace(collect=True)
    lines = [Line(text="Rechnung", left=10, top=10, width=80, height=10)]
    p = RedactionPipeline(ocr=StubOCR(lines), classifier=StubClassifier([]), padding=0)
    boxes = p.compute_boxes(_page_with_qr(), trace=trace)
    assert any(b.x0 <= 240 and b.x1 >= 400 for b in boxes)
    assert "code qr" in trace.collected
