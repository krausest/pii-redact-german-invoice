"""/api/redact with real models. Opt-in: `pytest -m slow`."""

from __future__ import annotations

import base64
import io
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageChops

from backend.api import create_app
from backend.config import Config
from backend.factory import build_pipeline

ROOT = Path(__file__).resolve().parent.parent
IMAGE = ROOT / "example" / "GOÄ_Rechnung1.png"
PDF = ROOT / "example" / "GOÄ_Rechnung1.pdf"

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def client():
    app = create_app(Config())
    app.state.pipeline = build_pipeline(Config())  # loads real models once
    return TestClient(app)


def _post(client, data, content_type, query="", **headers):
    return client.post(
        f"/api/redact{query}",
        content=data,
        headers={"content-type": content_type, **headers},
    )


def test_image_json_report(client):
    if not IMAGE.is_file():
        pytest.skip("sample image missing")
    with client:
        r = _post(client, IMAGE.read_bytes(), "image/png", "?json-output=true")
    assert r.status_code == 200
    body = r.json()
    assert body["redacted"]["content_type"] == "image/jpeg"  # image in, image out
    pages = body["pages"]
    assert len(pages) == 1
    page = pages[0]
    assert page["width"] > 0 and page["height"] > 0
    assert isinstance(page["boxes"], list) and len(page["boxes"]) >= 1
    image = Image.open(io.BytesIO(base64.b64decode(page["image"]["data"])))
    assert image.size == (page["width"], page["height"])
    # the coordinate rule: every box lies inside the image it came with
    for x0, y0, x1, y1 in page["boxes"]:
        assert 0 <= x0 < x1 <= page["width"] and 0 <= y0 < y1 <= page["height"]
    # and `redacted` really is the redacted page, not a second copy of the clean one
    redacted = Image.open(io.BytesIO(base64.b64decode(body["redacted"]["data"])))
    assert redacted.size == image.size
    assert ImageChops.difference(image.convert("RGB"), redacted.convert("RGB")).getbbox()


def test_image_in_jpeg_out(client):
    """The plain curl path: raw bytes in, a redacted file out."""
    if not IMAGE.is_file():
        pytest.skip("sample image missing")
    with client:
        r = _post(client, IMAGE.read_bytes(), "image/png")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/jpeg")
    redacted = Image.open(io.BytesIO(r.content))
    assert redacted.format == "JPEG"
    # the file carries no metadata now, so prove the pixels really changed
    with Image.open(IMAGE) as original:
        assert ImageChops.difference(original.convert("RGB"), redacted.convert("RGB")).getbbox()


def test_pdf_in_pdf_out_keeping_the_page_size(client):
    if not PDF.is_file():
        pytest.skip("sample PDF missing")
    with pymupdf.open(PDF) as doc:
        before, page_count = doc[0].rect, doc.page_count
    with client:
        r = _post(client, PDF.read_bytes(), "application/pdf")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    with pymupdf.open(stream=r.content, filetype="pdf") as doc:
        assert doc.page_count == page_count
        after = doc[0].rect
    # an A4 invoice must come back A4, not 2.8x oversized
    assert abs(after.width - before.width) <= 1 and abs(after.height - before.height) <= 1


def test_pdf_json_report_also_carries_the_document(client):
    if not PDF.is_file():
        pytest.skip("sample PDF missing")
    with client:
        r = _post(client, PDF.read_bytes(), "application/pdf", "?json-output=true")
    assert r.status_code == 200
    body = r.json()
    assert len(body["pages"]) >= 1
    assert all("image" in p and "boxes" in p for p in body["pages"])
    assert body["redacted"]["content_type"] == "application/pdf"
    with pymupdf.open(stream=base64.b64decode(body["redacted"]["data"]), filetype="pdf") as doc:
        assert doc.page_count == len(body["pages"])


def test_redact_then_assemble(client):
    """Report, edit the boxes, assemble — the flow the web UI drives."""
    if not IMAGE.is_file():
        pytest.skip("sample image missing")
    with client:
        report = _post(client, IMAGE.read_bytes(), "image/png", "?json-output=true")
        page = report.json()["pages"][0]
        assembled = client.post(
            "/api/assemble?format=jpeg",
            json={
                "pages": [
                    {
                        "content_type": "image/jpeg",
                        "data": page["image"]["data"],
                        "boxes": page["boxes"] + [[0, 0, 50, 50]],  # a user-added box
                    }
                ]
            },
        )
    assert assembled.status_code == 200
    assert assembled.headers["content-type"].startswith("image/jpeg")
    image = Image.open(io.BytesIO(assembled.content)).convert("RGB")
    assert image.getpixel((25, 25)) == (0, 0, 0)  # the added box was filled


def test_rejects_unsupported_type(client):
    with client:
        r = _post(client, b"hello", "text/plain")
    assert r.status_code == 415
