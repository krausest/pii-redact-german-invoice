"""PDF rasterization + assembly (pymupdf only, no ML models)."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from backend.pdf import assemble_pdf, encode_jpeg, rasterize_pdf

SAMPLE = Path(__file__).resolve().parent.parent / "example" / "GOÄ_Rechnung1.pdf"


def _require_sample() -> bytes:
    if not SAMPLE.is_file():
        pytest.skip(f"sample PDF not found: {SAMPLE}")
    return SAMPLE.read_bytes()


def test_rasterize_returns_rgb_pages():
    pages = rasterize_pdf(_require_sample(), dpi=150, max_pages=30, max_pixels=40_000_000)
    assert len(pages) >= 1
    assert all(p.mode == "RGB" and p.width > 0 and p.height > 0 for p in pages)


def test_rasterize_rejects_too_many_pages():
    with pytest.raises(ValueError):
        rasterize_pdf(_require_sample(), dpi=72, max_pages=0, max_pixels=40_000_000)


def test_rasterize_rejects_oversized_page():
    with pytest.raises(ValueError):
        rasterize_pdf(_require_sample(), dpi=200, max_pages=30, max_pixels=1000)


def test_rasterize_rejects_garbage():
    # ValueError specifically: mupdf's own RuntimeErrors are translated, so callers
    # (400 in the API, skip-the-file in the CLI) have one exception to catch.
    with pytest.raises(ValueError):
        rasterize_pdf(b"not a pdf", dpi=72, max_pages=30, max_pixels=40_000_000)


def test_rasterize_rejects_a_truncated_pdf():
    with pytest.raises(ValueError):
        rasterize_pdf(_require_sample()[:400], dpi=72, max_pages=30, max_pixels=40_000_000)


def test_assemble_pdf_roundtrip():
    imgs = [Image.new("RGB", (100, 120), (255, 255, 255)), Image.new("RGB", (80, 100), (200, 200, 200))]
    pdf = assemble_pdf(imgs, jpeg_quality=90)
    assert pdf[:4] == b"%PDF"
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        assert doc.page_count == 2


def test_assemble_pdf_maps_pixels_to_points_by_default():
    pdf = assemble_pdf([Image.new("RGB", (100, 120), (255, 255, 255))])
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        assert (round(doc[0].rect.width), round(doc[0].rect.height)) == (100, 120)


def test_assemble_pdf_dpi_gives_pages_their_physical_size():
    """A4 rasterized at 200 dpi must assemble back into an A4 page."""
    pdf = assemble_pdf([Image.new("RGB", (1654, 2339), (255, 255, 255))], dpi=200)
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        assert (round(doc[0].rect.width), round(doc[0].rect.height)) == (595, 842)


def test_rasterize_assemble_roundtrip_preserves_page_size():
    original = _require_sample()
    with pymupdf.open(stream=original, filetype="pdf") as doc:
        before = doc[0].rect
    pages = rasterize_pdf(original, dpi=200, max_pages=30, max_pixels=40_000_000)
    with pymupdf.open(stream=assemble_pdf(pages, dpi=200), filetype="pdf") as doc:
        after = doc[0].rect
    assert abs(after.width - before.width) <= 1 and abs(after.height - before.height) <= 1


def test_encode_jpeg_quality_affects_size():
    import os

    noise = Image.frombytes("RGB", (200, 200), os.urandom(200 * 200 * 3))
    assert len(encode_jpeg(noise, 20)) < len(encode_jpeg(noise, 95))
