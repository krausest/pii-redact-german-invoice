"""Config loading, defaults, engine-preset resolution and env override."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.config import Config, load_config


def _write(tmp_path, body: str):
    p = tmp_path / "config.toml"
    p.write_text(body)
    return p


def test_missing_file_yields_defaults(tmp_path):
    cfg = load_config(tmp_path / "does_not_exist.toml")
    assert cfg == Config()
    assert cfg.engine.name == "native"
    assert cfg.api.max_upload_bytes == 30 * 1024 * 1024


def test_partial_file_fills_defaults(tmp_path):
    cfg = load_config(_write(tmp_path, '[engine]\nname = "gliner"\n'))
    assert cfg.engine.name == "gliner"
    assert cfg.redaction.padding == 2  # default preserved


@pytest.mark.parametrize(
    "name,expected",
    [
        ("native", ("paddle", "presidio")),
        ("onnx", ("onnxruntime", "presidio")),
        ("gliner", ("paddle", "gliner")),
    ],
)
def test_preset_resolution(name, expected):
    from backend.config import EngineConfig

    assert EngineConfig(name=name).resolve() == expected


def test_explicit_override_unlocks_fourth_combo():
    from backend.config import EngineConfig

    assert EngineConfig(name="gliner", ocr_backend="onnxruntime").resolve() == (
        "onnxruntime",
        "gliner",
    )


def test_unknown_preset_raises():
    from backend.config import EngineConfig

    with pytest.raises(ValueError):
        EngineConfig(name="nope").resolve()


def test_env_engine_overrides_file(tmp_path, monkeypatch):
    path = _write(tmp_path, '[engine]\nname = "native"\n')
    monkeypatch.setenv("PII_ENGINE", "onnx")
    cfg = load_config(path)
    assert cfg.engine.name == "onnx"
    assert cfg.engine.resolve() == ("onnxruntime", "presidio")


def test_api_values_parsed(tmp_path):
    cfg = load_config(_write(tmp_path, "[api]\nmax_upload_bytes = 123\nworkers = 4\n"))
    assert cfg.api.max_upload_bytes == 123
    assert cfg.api.workers == 4


# -- a bad config fails at load, not on the first request -------------------- #
@pytest.mark.parametrize(
    "body,culprit",
    [
        ("[redaction]\npadd1ng = 4\n", "padd1ng"),  # typo: silently ignored would look like it worked
        ("[surprise]\nx = 1\n", "surprise"),  # unknown section
        ("[redaction]\njpeg_quality = 500\n", "jpeg_quality"),  # out of range
        ("[redaction]\npdf_dpi = 5\n", "pdf_dpi"),
        ('[engine]\nname = "nope"\n', "name"),  # not a preset
        ('[engine]\nclassifier = "nope"\n', "classifier"),  # not a classifier
        ("[api]\nworkers = 0\n", "workers"),
    ],
)
def test_bad_config_is_rejected_at_load(tmp_path, body, culprit):
    with pytest.raises(ValueError) as e:
        load_config(_write(tmp_path, body))
    assert culprit in str(e.value)


def test_fill_must_be_three_channels(tmp_path):
    with pytest.raises(ValueError):
        load_config(_write(tmp_path, "[redaction]\nfill = [0, 0]\n"))


def test_committed_config_toml_loads():
    """The config shipped in the repo must satisfy its own schema."""
    root = Path(__file__).resolve().parent.parent
    assert load_config(root / "config.toml").engine.resolve()
