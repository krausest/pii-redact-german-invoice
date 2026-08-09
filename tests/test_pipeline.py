"""Pipeline composition with stub OCR/classifier (no models)."""

from __future__ import annotations

from PIL import Image

from backend.codes import CodeParams
from backend.models import Box, Line
from backend.pipeline import RedactionPipeline
from backend.regions import RegionParams
from backend.trace import Trace
from tests.conftest import RecordingUnwarper, StubClassifier, StubOCR


def _pipeline(lines, pii_subs, **kwargs):
    return RedactionPipeline(
        ocr=StubOCR(lines),
        classifier=StubClassifier(pii_subs),
        **kwargs,
    )


def _qr_page(size=(500, 500), at=(200, 200), px=150):
    """A page with a real QR drawn on it. StubOCR ignores the image, so this is
    only ever seen by the code pass."""
    import numpy as np
    import zxingcpp

    barcode = zxingcpp.create_barcode("Max Mustermann", zxingcpp.BarcodeFormat.QRCode)
    matrix = np.array(zxingcpp.write_barcode_to_image(barcode))
    page = Image.new("RGB", size, "white")
    page.paste(Image.fromarray(matrix).convert("RGB").resize((px, px), Image.NEAREST), at)
    return page


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


def test_compute_boxes_appends_region_boxes():
    # StubOCR ignores the image, so the page size the bands are measured against
    # comes from the image argument alone.
    lines = [Line("Muster GmbH", left=0, top=5, width=80, height=10)]
    p = _pipeline(
        lines,
        [],
        padding=0,
        regions=RegionParams(
            header_frac=0.2,
            footer_frac=0.0,
            column_x_frac=1.0,
            column_y_frac=0.0,
            vgap_factor=0.5,
            align_factor=0.4,
        ),
    )
    boxes = p.compute_boxes(Image.new("RGB", (100, 100)))
    # the line itself (ORG_LEGAL) plus the full-width header band over it — same
    # height as the line, but spanning the page so a logo beside it is covered too.
    assert boxes == [Box(0, 5, 80, 15), Box(0, 0, 100, 15)]


def test_compute_boxes_skips_regions_by_default():
    lines = [Line("Muster GmbH", left=0, top=5, width=80, height=10)]
    p = _pipeline(lines, [], padding=0)
    assert p.compute_boxes(Image.new("RGB", (100, 100))) == [Box(0, 5, 80, 15)]


def test_compute_boxes_appends_code_boxes_last():
    # The code pass is the one source that reads the image rather than `lines`, so
    # unlike the others it needs a page with something actually drawn on it.
    page = _qr_page()
    p = _pipeline(
        [Line("Muster GmbH", left=0, top=5, width=80, height=10)],
        [],
        padding=0,
        codes=CodeParams(margin_frac=0.08),
    )
    boxes = p.compute_boxes(page)
    assert boxes[0] == Box(0, 5, 80, 15)  # the OCR line, first
    assert len(boxes) == 2  # then the QR, appended
    assert boxes[1].x0 >= 150 and boxes[1].x1 <= 400  # where it was drawn


def test_compute_boxes_skips_codes_by_default():
    # A page carrying a real QR yields nothing without `codes` — the toggle and the
    # geometry are one argument, so there is no way to half-enable the pass.
    p = _pipeline([], [], padding=0)
    assert p.compute_boxes(_qr_page()) == []


def test_compute_boxes_does_not_unwarp():
    unwarper = RecordingUnwarper()
    p = _pipeline([], [], unwarper=unwarper)
    p.compute_boxes(Image.new("RGB", (30, 30)))
    assert unwarper.calls == 0


def test_static_rule_redacts_without_classifier_hit():
    lines = [Line("Musterstrasse 23", left=0, top=0, width=80, height=10)]
    p = _pipeline(lines, [])  # classifier flags nothing
    assert len(p.compute_boxes(Image.new("RGB", (100, 100)))) == 1


def test_item_table_gates_the_classifier_only():
    # Two money rows 30px apart make one table band (220..260). The Leistungstext
    # inside it is flagged by the classifier and must NOT be redacted; the same
    # text below the table still is, and a deterministic rule keeps working
    # *inside* the table.
    lines = [
        Line("Orientierende Testuntersuchg.", left=0, top=220, width=100, height=10),
        Line("4,66 €", left=200, top=220, width=50, height=10),
        Line("Patient Mustermann, Max", left=0, top=240, width=100, height=10),
        Line("10,72 €", left=200, top=250, width=50, height=10),
        Line("Orientierende Testuntersuchg.", left=0, top=600, width=100, height=10),
    ]
    p = _pipeline(lines, ["Orientierende"], padding=0)
    assert p.compute_boxes(Image.new("RGB", (400, 800))) == [
        Box(0, 240, 100, 250),  # static rule, inside the table
        Box(0, 600, 100, 610),  # classifier, below the table
    ]


def test_name_memory_redacts_bare_recurrence():
    # "Diagnose Mustermann" matches no static rule and the classifier flags
    # nothing — only the surname harvested from the labeled patient line above
    # can catch it.
    lines = [
        Line("Patient Mustermann, Max", left=0, top=0, width=80, height=10),
        Line("Diagnose Mustermann", left=0, top=30, width=80, height=10),
    ]
    p = _pipeline(lines, [], padding=0)
    assert p.compute_boxes(Image.new("RGB", (100, 100))) == [
        Box(0, 0, 80, 10),
        Box(0, 30, 80, 40),
    ]


def test_name_memory_carries_across_pages_via_accumulator():
    page1 = [Line("Patient Mustermann, Max", left=0, top=0, width=80, height=10)]
    page2 = [Line("Diagnose Mustermann", left=0, top=0, width=80, height=10)]
    known: set[str] = set()
    img = Image.new("RGB", (100, 100))

    p1 = _pipeline(page1, [], padding=0)
    assert len(p1.compute_boxes(img, known_names=known)) == 1
    assert "Mustermann" in known  # harvested into the caller's set

    p2 = _pipeline(page2, [], padding=0)
    assert len(p2.compute_boxes(img, known_names=known)) == 1  # bare recurrence

    # Without the accumulator, page 2 on its own finds nothing.
    p3 = _pipeline(page2, [], padding=0)
    assert p3.compute_boxes(img) == []


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


def test_compute_boxes_traces_each_line_and_its_verdict():
    lines = [
        Line("Rechnung Nr 5", left=0, top=0, width=40, height=10, conf=98.0),
        Line("Sehr geehrter Herr Mustermann,", left=5, top=20, width=60, height=12, conf=99.0),
    ]
    trace = Trace(collect=True)
    p = _pipeline(lines, [], padding=0)
    p.compute_boxes(Image.new("RGB", (100, 100)), trace=trace)
    text = trace.collected
    # Every line is reported with the pixel box that would be blackened...
    assert "line @(0,0 40x10 conf=98.0): 'Rechnung Nr 5'" in text
    assert "line @(5,20 60x12 conf=99.0): 'Sehr geehrter Herr Mustermann,'" in text
    # ...and the verdict names *which* arm fired, which is what a wrong box is
    # diagnosed from. The salutation is a static rule, so the classifier never ran.
    assert "    -> REDACT (static-rule)" in text
