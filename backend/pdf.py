"""PDF rasterization and assembly (PyMuPDF).

Used by :mod:`backend.service` on both sides of a redaction: PDF in -> page images,
page images -> combined PDF. Kept separate from the ML pipeline: this module only
moves pixels between PDF and PIL.
"""

from __future__ import annotations

import io

import pymupdf
from PIL import Image


def rasterize_pdf(
    data: bytes,
    dpi: int = 200,
    max_pages: int = 30,
    max_pixels: int = 40_000_000,
) -> list[Image.Image]:
    """Render each PDF page to an RGB PIL image at ``dpi``.

    Guards against abusive inputs: rejects documents with more than ``max_pages``
    pages, and any page whose rasterized pixel count would exceed ``max_pixels``
    (a decompression-bomb guard for oversized media boxes).

    Every rejection — including a file that is not readable as a PDF at all — is a
    ``ValueError``, so callers have one thing to catch: the API turns it into a 400
    and the CLI skips the file."""
    zoom = dpi / 72.0
    matrix = pymupdf.Matrix(zoom, zoom)
    images: list[Image.Image] = []
    try:
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            if doc.page_count == 0:
                raise ValueError("PDF has no pages")
            if doc.page_count > max_pages:
                raise ValueError(f"PDF has too many pages ({doc.page_count} > {max_pages})")
            for page in doc:
                rect = page.rect
                est_pixels = int(rect.width * zoom) * int(rect.height * zoom)
                if est_pixels > max_pixels:
                    raise ValueError("PDF page exceeds the maximum allowed pixel count")
                pix = page.get_pixmap(matrix=matrix, alpha=False)
                images.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
    except (pymupdf.FileDataError, pymupdf.mupdf.FzErrorBase) as e:
        # A corrupt or truncated document: mupdf's own errors are RuntimeErrors,
        # which would otherwise surface as a 500.
        raise ValueError(f"PDF could not be read: {e}") from e
    return images


def encode_jpeg(image: Image.Image, quality: int = 90) -> bytes:
    """Encode a PIL image as JPEG bytes (visually lossless at the default quality)."""
    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def assemble_pdf(pages: list[Image.Image], jpeg_quality: int = 90, dpi: int = 72) -> bytes:
    """Combine page images into a single PDF, each page embedding the image as
    JPEG (compact, visually lossless at the default quality).

    ``dpi`` is the resolution the images represent, and sizes each PDF page at its
    true physical size (``pixels * 72 / dpi`` points). Pass the DPI the pages were
    rasterized at, or an A4 page scanned at 200 dpi comes back as a 23x32 inch
    monster. The 72 default maps pixels 1:1 to points."""
    if not pages:
        raise ValueError("no pages to assemble")
    scale = 72.0 / dpi
    doc = pymupdf.open()
    try:
        for image in pages:
            page = doc.new_page(width=image.width * scale, height=image.height * scale)
            page.insert_image(page.rect, stream=encode_jpeg(image, jpeg_quality))
        return doc.tobytes(garbage=3, deflate=True)
    finally:
        doc.close()
