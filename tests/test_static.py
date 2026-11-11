"""Cache headers of the served web UI."""

from fastapi.testclient import TestClient

from backend.api import create_app
from backend.config import Config


def build(tmp_path, monkeypatch) -> TestClient:
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html></html>")
    (tmp_path / "assets" / "index-abc123.js").write_text("console.log(1)")
    monkeypatch.setenv("PII_STATIC_DIR", str(tmp_path))
    return TestClient(create_app(Config()))


def test_index_is_revalidated(tmp_path, monkeypatch):
    client = build(tmp_path, monkeypatch)
    for url in ("/", "/index.html"):
        r = client.get(url)
        assert r.status_code == 200
        assert r.headers["cache-control"] == "no-cache"


def test_hashed_assets_are_immutable(tmp_path, monkeypatch):
    r = build(tmp_path, monkeypatch).get("/assets/index-abc123.js")
    assert r.status_code == 200
    assert "immutable" in r.headers["cache-control"]


def test_revalidation_keeps_the_header(tmp_path, monkeypatch):
    client = build(tmp_path, monkeypatch)
    etag = client.get("/").headers["etag"]
    r = client.get("/", headers={"If-None-Match": etag})
    assert r.status_code == 304
    assert r.headers["cache-control"] == "no-cache"


def test_missing_asset_is_not_cached(tmp_path, monkeypatch):
    r = build(tmp_path, monkeypatch).get("/assets/missing.js")
    assert r.status_code == 404
    assert "immutable" not in r.headers.get("cache-control", "")
