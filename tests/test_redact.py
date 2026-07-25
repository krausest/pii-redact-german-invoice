"""POST /api/redact — validation, options, and the two response shapes.

A FakePipeline is injected, so no ML models load here.
"""

from __future__ import annotations

import base64
import io

import pymupdf
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.api import create_app
from backend.config import ApiConfig, Config
from tests.conftest import FakePipeline, make_image_bytes, make_pdf_bytes as pdf_bytes

PNG = {"content-type": "image/png"}
JPEG = {"content-type": "image/jpeg"}
PDF = {"content-type": "application/pdf"}

URL = "/api/redact"


def build_client(config: Config | None = None, pipeline=None):
    cfg = config or Config()
    fake = pipeline if pipeline is not None else FakePipeline()
    app = create_app(cfg)
    app.state.pipeline = fake  # set before lifespan so no real pipeline is built
    return TestClient(app), fake


def gif_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), (1, 2, 3)).save(buf, format="GIF")
    return buf.getvalue()


# -- media type (415) ------------------------------------------------------- #
def test_rejects_text_plain():
    client, _ = build_client()
    with client:
        r = client.post(URL, content=b"hello", headers={"content-type": "text/plain"})
    assert r.status_code == 415


def test_rejects_gif_media_type():
    client, _ = build_client()
    with client:
        r = client.post(URL, content=gif_bytes(), headers={"content-type": "image/gif"})
    assert r.status_code == 415


def test_rejects_gif_disguised_as_png_by_format():
    client, _ = build_client()
    with client:
        r = client.post(URL, content=gif_bytes(), headers=PNG)
    assert r.status_code == 415  # media type ok, but decoded format is GIF


def test_rejects_pdf_body_that_is_not_a_pdf(png_bytes):
    client, _ = build_client()
    with client:
        r = client.post(URL, content=png_bytes, headers=PDF)
    assert r.status_code == 400


def test_rejects_a_corrupt_pdf_with_a_valid_header():
    """The %PDF prefix check only sees the first bytes; a truncated document has
    to fail in rasterization — as a 400, not a 500."""
    client, _ = build_client()
    with client:
        r = client.post(URL, content=pdf_bytes()[:400], headers=PDF)
    assert r.status_code == 400


# -- size cap (413) --------------------------------------------------------- #
def test_rejects_oversized_content_length():
    client, _ = build_client(Config(api=ApiConfig(max_upload_bytes=100)))
    with client:
        r = client.post(URL, content=b"x" * 200, headers=PNG)
    assert r.status_code == 413


def test_streaming_cap_when_content_length_absent():
    # A chunked body (generator) carries no Content-Length, so the first layer is
    # skipped and the streaming cap must catch it.
    client, _ = build_client(Config(api=ApiConfig(max_upload_bytes=100)))

    def body():
        yield b"x" * 200

    with client:
        r = client.post(URL, content=body(), headers=PNG)
    assert r.status_code == 413


# -- decode / bomb (400) ---------------------------------------------------- #
def test_rejects_garbage_bytes():
    client, _ = build_client()
    with client:
        r = client.post(URL, content=b"definitely not an image", headers=PNG)
    assert r.status_code == 400


def test_rejects_empty_body():
    client, _ = build_client()
    with client:
        r = client.post(URL, content=b"", headers=PNG)
    assert r.status_code == 400


def test_rejects_decompression_bomb():
    # 64x48 = 3072 px; with max_image_pixels=1000, Pillow raises past 2x the cap.
    client, _ = build_client(Config(api=ApiConfig(max_image_pixels=1000)))
    with client:
        r = client.post(URL, content=make_image_bytes("PNG"), headers=PNG)
    assert r.status_code == 400


def test_rejects_pdf_with_too_many_pages():
    client, _ = build_client(Config())
    config = Config()
    with client:
        r = client.post(URL, content=pdf_bytes(config.redaction.max_pages + 1), headers=PDF)
    assert r.status_code == 400


# -- file responses --------------------------------------------------------- #
def test_png_in_jpeg_out(png_bytes):
    """Images always come back as JPEG, whatever went in."""
    client, fake = build_client()
    with client:
        r = client.post(URL, content=png_bytes, headers=PNG)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/jpeg")
    assert Image.open(io.BytesIO(r.content)).format == "JPEG"
    assert fake.calls == ["unwarp", "compute_boxes", "apply_boxes"]


def test_jpeg_in_jpeg_out(jpeg_bytes):
    client, _ = build_client()
    with client:
        r = client.post(URL, content=jpeg_bytes, headers=JPEG)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/jpeg")


def test_pdf_in_pdf_out():
    client, _ = build_client()
    with client:
        r = client.post(URL, content=pdf_bytes(2), headers=PDF)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    with pymupdf.open(stream=r.content, filetype="pdf") as doc:
        assert doc.page_count == 2


def test_single_page_pdf_still_comes_back_as_a_pdf():
    """The output kind follows the input kind, not the page count."""
    client, _ = build_client()
    with client:
        r = client.post(URL, content=pdf_bytes(1), headers=PDF)
    assert r.headers["content-type"] == "application/pdf"


def test_file_response_carries_no_metadata(png_bytes):
    """The file is the whole response — ask for the report to learn more."""
    client, _ = build_client()
    with client:
        r = client.post(URL, content=png_bytes, headers=PNG)
    assert not [h for h in r.headers if h.lower().startswith("x-redact")]


def test_unwarp_false_skips_unwarping(png_bytes):
    client, fake = build_client()
    with client:
        r = client.post(f"{URL}?unwarp=false", content=png_bytes, headers=PNG)
    assert r.status_code == 200
    assert "unwarp" not in fake.calls
    assert "compute_boxes" in fake.calls  # detection always runs


# -- the JSON report --------------------------------------------------------- #
def test_json_report_shape(png_bytes):
    client, _ = build_client()
    with client:
        r = client.post(f"{URL}?json-output=true", content=png_bytes, headers=PNG)
    assert r.status_code == 200
    body = r.json()
    assert "engine" not in body  # the engine is fixed per process — read it from /health
    assert body["unwarped"] is True
    assert body["redacted"]["content_type"] == "image/jpeg"  # image in, image out
    page = body["pages"][0]
    assert page["index"] == 0
    assert page["boxes"] == [[1, 2, 3, 4]]
    assert page["image"]["content_type"] == "image/jpeg"
    image = Image.open(io.BytesIO(base64.b64decode(page["image"]["data"])))
    assert image.size == (page["width"], page["height"])


def test_boxes_are_in_the_returned_images_coordinate_space():
    """The one coordinate rule: every box lies within the page image it came with,
    whatever unwarping or rasterization did to the geometry."""
    client, _ = build_client()
    with client:
        r = client.post(f"{URL}?json-output=true", content=pdf_bytes(2, (400, 300)), headers=PDF)
    for page in r.json()["pages"]:
        assert (page["width"], page["height"]) != (400, 300)  # rasterized at pdf-dpi
        for x0, y0, x1, y1 in page["boxes"]:
            assert 0 <= x0 < x1 <= page["width"]
            assert 0 <= y0 < y1 <= page["height"]


def test_json_report_for_a_pdf_also_carries_the_full_pdf():
    client, _ = build_client()
    with client:
        r = client.post(f"{URL}?json-output=true", content=pdf_bytes(3), headers=PDF)
    body = r.json()
    assert [p["index"] for p in body["pages"]] == [0, 1, 2]
    assert body["redacted"]["content_type"] == "application/pdf"
    with pymupdf.open(stream=base64.b64decode(body["redacted"]["data"]), filetype="pdf") as doc:
        assert doc.page_count == 3


def test_report_redacted_is_the_same_file_the_endpoint_returns_without_json(png_bytes):
    """``redacted`` is not a second rendering: it is what ``json-output=false``
    would have returned, so a client never re-runs the models to fetch the file."""
    client, _ = build_client()
    with client:
        plain = client.post(URL, content=png_bytes, headers=PNG)
        report = client.post(f"{URL}?json-output=true", content=png_bytes, headers=PNG)
    body = report.json()
    assert body["redacted"]["content_type"] == plain.headers["content-type"]
    assert base64.b64decode(body["redacted"]["data"]) == plain.content


def test_page_images_in_the_report_are_not_redacted(png_bytes):
    """They are the pages under review — the boxes are not filled in yet."""
    client, fake = build_client()
    with client:
        r = client.post(f"{URL}?json-output=true", content=png_bytes, headers=PNG)
    page = r.json()["pages"][0]
    # FakePipeline.unwarp returns a flat (10,20,30) image; redaction would blacken it.
    image = Image.open(io.BytesIO(base64.b64decode(page["image"]["data"]))).convert("RGB")
    assert image.getpixel((1, 1)) != (0, 0, 0)
    assert "apply_boxes" in fake.calls  # the redacted copy was still produced


# -- option parsing ---------------------------------------------------------- #
def test_typo_in_a_parameter_name_is_rejected(png_bytes):
    client, _ = build_client()
    with client:
        r = client.post(f"{URL}?unwrap=false", content=png_bytes, headers=PNG)
    assert r.status_code == 400
    assert "unwrap" in r.json()["detail"]


@pytest.mark.parametrize(
    "query",
    [
        "unwarp=maybe",
        "json-output=perhaps",
        "jpeg-quality=0",
        "jpeg-quality=101",
        "jpeg-quality=high",
        "pdf-dpi=5",
        "json_output=true",  # underscores are not the wire spelling
        "engine=onnx",  # fixed by config, not per request
        "detect=false",  # detection always runs
        "include=boxes",
        "format=pdf",
    ],
)
def test_bad_parameters_are_rejected(png_bytes, query):
    client, _ = build_client()
    with client:
        r = client.post(f"{URL}?{query}", content=png_bytes, headers=PNG)
    assert r.status_code == 400


# -- health ------------------------------------------------------------------ #
def test_health_reports_the_resolved_engine():
    client, _ = build_client(Config())
    with client:
        r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {
        "status": "ok",
        "engine": {"name": "native", "ocr": "paddle", "classifier": "presidio"},
    }
