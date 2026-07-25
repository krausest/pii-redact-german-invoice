"""POST /api/assemble — fills boxes and packages pages. No models involved."""

from __future__ import annotations

import base64
import io
import os

import pymupdf
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.api import create_app
from backend.config import Config
from backend.pipeline import RedactionPipeline
from tests.conftest import StubClassifier, StubOCR

URL = "/api/assemble"


def build() -> TestClient:
    app = create_app(Config())
    # apply_boxes is pure PIL; the stubs stand in for the (unused here) OCR/classifier.
    app.state.pipeline = RedactionPipeline(ocr=StubOCR([]), classifier=StubClassifier([]))
    return TestClient(app)


def b64_png(size=(40, 30), color=(255, 255, 255)) -> str:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def b64_noise(size=(200, 200)) -> str:
    image = Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3))
    buf = io.BytesIO()
    image.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def test_fills_the_box():
    client = build()
    payload = {"pages": [{"data": b64_png(), "boxes": [[10, 5, 30, 25]]}]}
    with client:
        r = client.post(f"{URL}?format=jpeg", json=payload)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/jpeg")
    image = Image.open(io.BytesIO(r.content)).convert("RGB")
    assert image.getpixel((20, 15)) == (0, 0, 0)  # inside the box
    assert image.getpixel((2, 2)) == (255, 255, 255)  # outside


def test_combines_pages_into_one_pdf():
    client = build()
    payload = {"pages": [{"data": b64_png(), "boxes": []}, {"data": b64_png(), "boxes": []}]}
    with client:
        r = client.post(URL, json=payload)  # pdf is the default format
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content[:4] == b"%PDF"
    with pymupdf.open(stream=r.content, filetype="pdf") as doc:
        assert doc.page_count == 2


def test_dpi_sets_the_physical_page_size():
    """A4 at 200 dpi (1654x2339 px) must come back as an A4 page, not 23x32 inches."""
    client = build()
    payload = {"pages": [{"data": b64_png(size=(1654, 2339)), "boxes": []}]}
    with client:
        r = client.post(f"{URL}?dpi=200", json=payload)
    with pymupdf.open(stream=r.content, filetype="pdf") as doc:
        rect = doc[0].rect
    assert round(rect.width) == 595 and round(rect.height) == 842  # A4 in points


def test_boxes_are_optional():
    client = build()
    with client:
        r = client.post(URL, json={"pages": [{"data": b64_png()}]})
    assert r.status_code == 200


def test_jpeg_quality_affects_size():
    client = build()
    pages = [{"data": b64_noise(), "boxes": []}]
    with client:
        hi = client.post(f"{URL}?jpeg-quality=95", json={"pages": pages})
        lo = client.post(f"{URL}?jpeg-quality=20", json={"pages": pages})
    assert lo.status_code == hi.status_code == 200
    assert len(lo.content) < len(hi.content)


def test_jpeg_format_rejects_multiple_pages():
    client = build()
    payload = {"pages": [{"data": b64_png(), "boxes": []}, {"data": b64_png(), "boxes": []}]}
    with client:
        r = client.post(f"{URL}?format=jpeg", json=payload)
    assert r.status_code == 400


def test_rejects_too_many_pages():
    client = build()
    pages = [{"data": b64_png(), "boxes": []}] * (Config().redaction.max_pages + 1)
    with client:
        r = client.post(URL, json={"pages": pages})
    assert r.status_code == 400


@pytest.mark.parametrize(
    "payload",
    [
        {},  # no pages
        {"pages": []},
        {"pages": "nope"},
        {"pages": [{"data": "!!!notb64"}]},
        {"pages": [{"data": b64_png(), "boxes": [[1, 2, 3]]}]},
        {"pages": [{"data": b64_png(), "boxes": "nope"}]},
        {"pages": [{"data": b64_png(), "surprise": 1}]},
        {"pages": [{"boxes": []}]},  # no data
        {"pages": [b64_png()]},  # not an object
        {"pages": [{"data": b64_png()}], "options": {}},  # options are query params now
    ],
)
def test_malformed_bodies_are_rejected(payload):
    client = build()
    with client:
        r = client.post(URL, json=payload)
    assert r.status_code == 400


@pytest.mark.parametrize("query", ["format=png", "dpi=0", "jpeg-quality=0", "unwarp=false"])
def test_bad_parameters_are_rejected(query):
    client = build()
    with client:
        r = client.post(f"{URL}?{query}", json={"pages": [{"data": b64_png()}]})
    assert r.status_code == 400


def test_rejects_wrong_content_type():
    client = build()
    with client:
        r = client.post(URL, content=b"{}", headers={"content-type": "text/plain"})
    assert r.status_code == 415


def test_rejects_invalid_json():
    client = build()
    with client:
        r = client.post(URL, content=b"{not json", headers={"content-type": "application/json"})
    assert r.status_code == 400
