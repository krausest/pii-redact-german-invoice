"""QR / DataMatrix redaction: the only box source that reads pixels, not OCR lines.

Fixtures are generated rather than committed — zxing-cpp encodes as well as it
decodes, so a QR here is a real QR and the round trip is the test.
"""

from __future__ import annotations

import io
from dataclasses import replace

import numpy as np
import pytest
import zxingcpp
from PIL import Image

from backend.codes import CodeParams, code_boxes

PAGE_W, PAGE_H = 850, 1200
PARAMS = CodeParams(margin_frac=0.08)

# A real Girocode payload: this is the PII the pass exists to cover.
EPC = "BCD\n002\n1\nSCT\nGIBAATWWXXX\nMax Mustermann\nAT611904300234573201\nEUR123.45"
# A full EPC069-12 payload, as an invoice's "Zahlen mit Code" square actually carries
# it. Longer than EPC above, so it needs a 57-module symbol rather than a 41-module
# one — which is what makes it degrade first, and why the bug showed up here.
# The IBAN/BIC are placeholders, the IBAN with a deliberately invalid checksum as
# in tests/ground_truth/. Replacing them is not free: the payload has to stay the
# same length (57 modules, not 41) *and* the degradation test below reads the
# symbol at ~1 px per module, where whether it decodes at all depends on the
# actual bits. An all-zero IBAN, for one, stops being found at scale 0.2. Re-run
# tests/test_codes.py in full after touching this.
SEPA = "\n".join(
    [
        "BCD", "002", "1", "SCT",
        "MUSTDEFFXXX",
        "Musterpraxis Dr. Muster & Kollegen GmbH",
        "DE99123412341234123412",
        "EUR247.85",
        "",
        "RG-2024-0815-00123",
        "Rechnung 2024-0815 Patient 00000",
        "Vielen Dank fuer Ihren Besuch",
    ]
)


def _matrix(payload: str, fmt: str = "QRCode") -> np.ndarray:
    """The module matrix of a code, as a small uint8 array. Note that zxing-cpp
    renders a quiet zone into it, so the matrix is wider than the symbol."""
    barcode = zxingcpp.create_barcode(payload, getattr(zxingcpp.BarcodeFormat, fmt))
    return np.array(zxingcpp.write_barcode_to_image(barcode))


def _page(*codes, size=(PAGE_W, PAGE_H)) -> Image.Image:
    """A white page with each ``(matrix, (x, y), px)`` pasted at that pixel square."""
    page = Image.new("L", size, 255)
    for matrix, (x, y), px in codes:
        code = Image.fromarray(matrix).resize((px, px), Image.NEAREST)
        page.paste(code, (x, y))
    return page.convert("RGB")


def _symbol_rect(matrix: np.ndarray, at, px):
    """Where the *symbol* lands on the page, excluding the rendered quiet zone.

    This is what a box has to cover: the quiet zone is blank paper and carries no
    data, so asserting against the pasted square would demand coverage the format
    does not require.
    """
    quiet = 0
    while quiet < len(matrix) and matrix[quiet].min() == 255:
        quiet += 1
    inset = quiet * px / len(matrix)
    x, y = at
    return (x + inset, y + inset, x + px - inset, y + px - inset)


def _contains(box, rect, slack=0.0) -> bool:
    x0, y0, x1, y1 = rect
    return (
        box.x0 <= x0 + slack
        and box.y0 <= y0 + slack
        and box.x1 >= x1 - slack
        and box.y1 >= y1 - slack
    )


# -- the basics -------------------------------------------------------------- #
def test_qr_code_yields_one_box_covering_the_whole_symbol():
    # Not just "a box near the code": every module has to be under it, or error
    # correction can still reconstruct the payload from what is left showing.
    matrix = _matrix(EPC)
    page = _page((matrix, (560, 820), 240))
    boxes = code_boxes(page, PARAMS)
    assert len(boxes) == 1
    assert _contains(boxes[0], _symbol_rect(matrix, (560, 820), 240))


def test_data_matrix_yields_one_box():
    # OpenCV has no DataMatrix reader at all; this is what zxing-cpp is here for.
    matrix = _matrix("PZN-12345678", "DataMatrix")
    page = _page((matrix, (100, 200), 160))
    boxes = code_boxes(page, PARAMS)
    assert len(boxes) == 1
    assert _contains(boxes[0], _symbol_rect(matrix, (100, 200), 160))


def test_qr_and_data_matrix_on_one_page_are_two_boxes():
    page = _page(
        (_matrix(EPC), (560, 820), 240),
        (_matrix("PZN-12345678", "DataMatrix"), (100, 200), 160),
    )
    assert len(code_boxes(page, PARAMS)) == 2


def test_two_qr_codes_are_two_boxes():
    page = _page(
        (_matrix(EPC), (60, 820), 200),
        (_matrix("BCD\n002\n1\nSCT\nOther Payee"), (560, 820), 200),
    )
    assert len(code_boxes(page, PARAMS)) == 2


def test_blank_page_yields_nothing():
    assert code_boxes(Image.new("RGB", (PAGE_W, PAGE_H), "white"), PARAMS) == []


# -- recall: the reason `return_errors=True` is on --------------------------- #
def test_qr_survives_downscale_and_jpeg():
    # A phone photo of an invoice is exactly this: the code still has to be found.
    page = _page((_matrix(EPC), (560, 820), 240)).resize((510, 720))
    buf = io.BytesIO()
    page.save(buf, "JPEG", quality=60)
    assert len(code_boxes(Image.open(buf), PARAMS)) == 1


def test_undecodable_qr_is_still_redacted():
    # Blurred past the point where the checksum verifies. Plain decoding reports
    # nothing here; detection is what redaction needs, so the box must appear.
    from PIL import ImageFilter

    page = _page((_matrix(EPC), (560, 820), 240)).filter(ImageFilter.GaussianBlur(3.5))
    assert zxingcpp.read_barcodes(page) == []
    assert len(code_boxes(page, PARAMS)) == 1


def test_one_symbol_read_several_ways_is_merged_into_one_box():
    # A damaged code is routinely reported several times over (a corner that also
    # parses as a Micro QR, partial reads); overlapping detections must not become
    # several boxes, or the report lies about how many codes are on the page.
    from PIL import ImageFilter

    page = _page((_matrix(EPC), (300, 400), 220)).filter(ImageFilter.GaussianBlur(3.0))
    assert len(zxingcpp.read_barcodes(page, return_errors=True)) > 1
    assert len(code_boxes(page, PARAMS)) == 1


def test_coarse_sepa_qr_is_covered_whole_not_just_its_top_left_corner():
    # The reported bug. A real SEPA/Girocode payload makes a 57-module QR; render it
    # at ~2 px per module and the decoder gives up, at which point zxing-cpp reports
    # one piece per *finder pattern* — a QR's three concentric corner squares each
    # read as a Micro QR. They do not touch, so merging on overlap alone left two
    # partial boxes pinned to the top-left and the rest of the code showing.
    matrix = _matrix(SEPA)
    page = _page((matrix, (200, 200), 300), size=(1000, 1000)).resize((350, 350))
    symbol = tuple(v * 0.35 for v in _symbol_rect(matrix, (200, 200), 300))

    boxes = code_boxes(page, PARAMS)
    assert len(boxes) == 1
    assert _contains(boxes[0], symbol)


def test_a_coarse_code_is_covered_across_the_scales_it_degrades_through():
    # The failure was scale-dependent — 0.32 and 0.37 decoded cleanly while 0.35 in
    # between did not — so pinning one scale would not have held the fix down.
    matrix = _matrix(SEPA)
    for scale in (0.2, 0.25, 0.3, 0.35, 0.4, 0.5):
        page = _page((matrix, (200, 200), 300), size=(1000, 1000)).resize(
            (int(1000 * scale),) * 2
        )
        symbol = tuple(v * scale for v in _symbol_rect(matrix, (200, 200), 300))
        boxes = code_boxes(page, PARAMS)
        assert boxes, f"no box at scale {scale}"
        assert any(_contains(b, symbol) for b in boxes), f"symbol uncovered at scale {scale}"


@pytest.mark.parametrize("mm", [12, 15, 20, 25, 30])
def test_a_small_printed_code_is_covered_on_a_full_resolution_page(mm):
    # Code coarseness is not page coarseness: a 15 mm Girocode rasterized at the
    # default 200 dpi is 118 px — 2.1 px per module — and fragments exactly like a
    # thumbnail does, on a 3.9 MP page. So the retry must not be gated on page size.
    # 12 mm decodes and 15 mm does not, which is aliasing between the module grid
    # and the pixel grid, not a threshold: both have to come out covered.
    matrix = _matrix(SEPA)
    px = round(mm / 25.4 * 200)
    page = Image.new("L", (1654, 2339), 255)
    page.paste(Image.fromarray(matrix).resize((px, px), Image.NEAREST), (1100, 1800))

    boxes = code_boxes(page.convert("RGB"), PARAMS)
    assert len(boxes) == 1
    assert _contains(boxes[0], _symbol_rect(matrix, (1100, 1800), px))
    # ...and without blackening half the invoice to do it.
    assert (boxes[0].x1 - boxes[0].x0) < 3 * px


def test_fragments_are_squared_away_from_their_anchor_not_about_their_centre():
    # A cluster holding only the top-left corner's pieces must grow right and down
    # into the code. Growing about the centre walked the box up and left instead —
    # blackening the line above the QR while leaving its bottom-right showing.
    from backend.codes import _fuse_fragments

    top_left_corner = [(100.0, 100.0, 130.0, 130.0), (100.0, 100.0, 130.0, 190.0)]
    (x0, y0, x1, y1) = _fuse_fragments(top_left_corner)[0]

    assert (x0, y0) == (100.0, 100.0)  # the anchor is kept, not straddled
    assert x1 > 130.0 and y1 >= 190.0  # and the growth goes toward the symbol
    assert x1 - x0 == y1 - y0  # square, because a matrix code is


def test_a_blank_high_resolution_page_is_not_re_read():
    # The blind retry costs ~10x this pass for nothing on the common case — a page
    # with no code at all — so it is skipped once a page is big enough that "found
    # nothing" means "there is nothing" rather than "too coarse to see".
    import backend.codes

    page = Image.new("RGB", (1654, 2339), "white")
    calls = []
    original = backend.codes._read
    backend.codes._read = lambda image: (calls.append(image.size), original(image))[1]
    try:
        assert code_boxes(page, PARAMS) == []
    finally:
        backend.codes._read = original
    assert calls == [(1654, 2339)]


def test_a_decoded_page_does_not_pay_for_the_retry():
    # The 2x re-read is gated on nothing having decoded, so the ordinary case still
    # reads the page exactly once.
    import backend.codes

    page = _page((_matrix(EPC), (560, 820), 240))
    calls = []
    original = backend.codes._read
    backend.codes._read = lambda image: (calls.append(image.size), original(image))[1]
    try:
        assert len(code_boxes(page, PARAMS)) == 1
    finally:
        backend.codes._read = original
    assert calls == [(PAGE_W, PAGE_H)]


# -- precision: the shape guards --------------------------------------------- #
def test_real_invoice_page_without_a_code_yields_nothing():
    # The false-positive regression. `return_errors=True` will happily report a
    # run of item-table rows as a DataMatrix (measured on Rechnung1.pdf: a 738x108
    # quad); the aspect guard is what rejects it. This page has no code at all.
    page = Image.open("example/GOÄ_Rechnung1.png").convert("RGB")
    assert code_boxes(page, PARAMS) == []


@pytest.mark.parametrize(
    "quad",
    [
        # the measured Rechnung1.pdf false positive: three text rows, 6.8:1, and
        # its corners are wound so that x0 > x1 if you trust the naming.
        [(1089, 1242), (351, 1233), (441, 1134), (1089, 1188)],
        [(0, 0), (8, 0), (8, 8), (0, 8)],  # too small to be a symbol
    ],
)
def test_misshapen_detections_are_rejected(quad):
    from backend.codes import _plausible, _quad_rect

    assert not _plausible(_quad_rect(_FakePosition(quad)))


def test_rotated_code_is_covered_by_its_enclosing_rect():
    # `Box` is axis-aligned, so a code on a skewed photo has to be reduced to the
    # rect around its corners — which must still contain every corner.
    page = _page((_matrix(EPC), (300, 400), 240)).rotate(
        20, resample=Image.BICUBIC, fillcolor=255
    )
    boxes = code_boxes(page, PARAMS)
    assert len(boxes) == 1
    assert boxes[0].x1 - boxes[0].x0 > 240  # wider than the code: it is the hull


# -- the knob ---------------------------------------------------------------- #
def test_margin_frac_grows_the_box():
    matrix = _matrix(EPC)
    page = _page((matrix, (560, 820), 240))
    tight = code_boxes(page, replace(PARAMS, margin_frac=0.0))[0]
    loose = code_boxes(page, PARAMS)[0]
    assert loose.x0 < tight.x0 and loose.y0 < tight.y0
    assert loose.x1 > tight.x1 and loose.y1 > tight.y1
    # The margin is headroom, not a correction: even with it off the box lands on
    # the symbol edge, to within the pixel the detection is rounded to.
    symbol = _symbol_rect(matrix, (560, 820), 240)
    assert _contains(tight, symbol, slack=1.0)
    assert not _contains(tight, symbol)  # ...but only to within that pixel
    # What the default buys is slack an order of magnitude past that rounding, for a
    # detection that reads short on a blurred or downscaled page. It spends it on the
    # quiet zone, which is blank paper — reaching all the way across that zone is not
    # the goal, so the box stays inside the pasted square.
    assert loose.x0 < symbol[0] and loose.x1 > symbol[2]
    assert _contains(loose, symbol, slack=-8.0)


def test_padding_is_added_on_top_of_the_margin():
    page = _page((_matrix(EPC), (560, 820), 240))
    plain = code_boxes(page, PARAMS)[0]
    padded = code_boxes(page, replace(PARAMS, padding=5))[0]
    assert padded.x0 == plain.x0 - 5
    assert padded.y1 == plain.y1 + 5


def test_boxes_are_clamped_to_the_page():
    # A code in the very corner must not produce negative coordinates.
    page = _page((_matrix(EPC), (0, 0), 200), size=(240, 240))
    box = code_boxes(page, PARAMS)[0]
    assert box.x0 >= 0 and box.y0 >= 0
    assert box.x1 <= 240 and box.y1 <= 240


class _FakePosition:
    """The four corners zxing-cpp reports, without needing a real detection."""

    def __init__(self, quad):
        self.top_left, self.top_right, self.bottom_right, self.bottom_left = (
            _Point(*p) for p in quad
        )


class _Point:
    def __init__(self, x, y):
        self.x, self.y = x, y
