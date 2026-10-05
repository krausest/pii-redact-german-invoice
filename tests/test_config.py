"""Config loading, defaults and env override."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.config import Config, EngineConfig, load_config


def _write(tmp_path, body: str):
    p = tmp_path / "config.toml"
    p.write_text(body)
    return p


def test_missing_file_yields_defaults(tmp_path):
    cfg = load_config(tmp_path / "does_not_exist.toml")
    assert cfg == Config()
    assert (cfg.engine.ocr_backend, cfg.engine.classifier) == ("onnxruntime", "presidio")
    assert cfg.api.max_upload_bytes == 30 * 1024 * 1024


def test_partial_file_fills_defaults(tmp_path):
    cfg = load_config(_write(tmp_path, '[engine]\nocr_backend = "paddle"\n'))
    assert cfg.engine.ocr_backend == "paddle"
    assert cfg.engine.classifier == "presidio"  # default preserved
    assert cfg.redaction.padding == 2


def test_engine_axes_combine_freely(tmp_path):
    body = '[engine]\nocr_backend = "paddle"\nclassifier = "guard-omni"\nguard_omni = true\n'
    cfg = load_config(_write(tmp_path, body))
    assert (cfg.engine.ocr_backend, cfg.engine.classifier) == ("paddle", "guard-omni")


def test_guard_omni_is_opt_in():
    assert Config().engine.classifiers == ("presidio",)
    assert EngineConfig(guard_omni=True).classifiers == ("presidio", "guard-omni")


def test_a_disabled_classifier_cannot_be_the_default(tmp_path, monkeypatch):
    monkeypatch.setenv("PII_CLASSIFIER", "guard-omni")
    with pytest.raises(ValidationError, match="PII_GUARD_OMNI"):
        load_config(_write(tmp_path, ""))


def test_det_box_thresh_default_is_below_paddles_own(tmp_path):
    # Pinned: PaddleOCR defaults to 0.6, at which a full-width imprint footer in
    # small type is not detected at all. Raising this back silently loses it.
    assert Config().engine.det_box_thresh == 0.5
    cfg = load_config(_write(tmp_path, "[engine]\ndet_box_thresh = 0.35\n"))
    assert cfg.engine.det_box_thresh == 0.35


def test_env_engine_overrides_file(tmp_path, monkeypatch):
    body = '[engine]\nocr_backend = "onnxruntime"\nclassifier = "presidio"\n'
    path = _write(tmp_path, body)
    monkeypatch.setenv("PII_OCR_BACKEND", "paddle")
    monkeypatch.setenv("PII_CLASSIFIER", "guard-omni")
    monkeypatch.setenv("PII_GUARD_OMNI", "true")
    cfg = load_config(path)
    assert (cfg.engine.ocr_backend, cfg.engine.classifier) == ("paddle", "guard-omni")
    assert cfg.engine.det_box_thresh == 0.5  # rest of the section kept


@pytest.mark.parametrize("env", ["PII_UNWARP", "PII_REDACT_REGIONS"])
@pytest.mark.parametrize(
    "value,expected",
    [("false", False), ("0", False), ("off", False), ("true", True), ("1", True)],
)
def test_env_boolean_overrides_file(tmp_path, monkeypatch, env, value, expected):
    field = env.removeprefix("PII_").lower()
    path = _write(tmp_path, f"[redaction]\n{field} = {str(not expected).lower()}\n")
    monkeypatch.setenv(env, value)
    assert getattr(load_config(path).redaction, field) is expected


def test_env_override_keeps_the_rest_of_the_section(tmp_path, monkeypatch):
    path = _write(tmp_path, "[redaction]\npadding = 7\n")
    monkeypatch.setenv("PII_UNWARP", "false")
    monkeypatch.setenv("PII_REDACT_REGIONS", "false")
    cfg = load_config(path)
    assert cfg.redaction.unwarp is False
    assert cfg.redaction.redact_regions is False
    assert cfg.redaction.padding == 7


def test_unset_env_leaves_the_file_alone(tmp_path, monkeypatch):
    monkeypatch.delenv("PII_UNWARP", raising=False)
    path = _write(tmp_path, "[redaction]\nunwarp = false\n")
    assert load_config(path).redaction.unwarp is False


@pytest.mark.parametrize("env", ["PII_UNWARP", "PII_REDACT_REGIONS"])
def test_env_rejects_a_non_boolean(tmp_path, monkeypatch, env):
    monkeypatch.setenv(env, "maybe")
    with pytest.raises(ValueError) as e:
        load_config(tmp_path / "does_not_exist.toml")
    # the message names the config field, not the variable — same as a bad TOML value
    assert env.removeprefix("PII_").lower() in str(e.value)


def test_api_values_parsed(tmp_path):
    body = "[api]\nmax_upload_bytes = 123\nmax_concurrent_per_worker = 4\n"
    cfg = load_config(_write(tmp_path, body))
    assert cfg.api.max_upload_bytes == 123
    assert cfg.api.max_concurrent_per_worker == 4


# -- a bad config fails at load, not on the first request -------------------- #
@pytest.mark.parametrize(
    "body,culprit",
    [
        ("[redaction]\npadd1ng = 4\n", "padd1ng"),  # typo: silently ignored would look like it worked
        ("[surprise]\nx = 1\n", "surprise"),  # unknown section
        ("[redaction]\njpeg_quality = 500\n", "jpeg_quality"),  # out of range
        ("[redaction]\npdf_dpi = 5\n", "pdf_dpi"),
        ('[engine]\nname = "onnx"\n', "name"),  # the removed preset key
        ('[engine]\nocr_backend = "nope"\n', "ocr_backend"),  # not a backend
        ('[engine]\nclassifier = "nope"\n', "classifier"),  # not a classifier
        ("[engine]\ndet_box_thresh = 1.5\n", "det_box_thresh"),  # not a probability
        ("[api]\nmax_concurrent_per_worker = 0\n", "max_concurrent_per_worker"),
        ("[api]\nworkers = 2\n", "workers"),  # removed key: set -w on gunicorn
        ("[redaction.layout]\nthreshold = 0.0\n", "threshold"),  # not a probability
        ("[redaction.layout]\nthreshold = 1.5\n", "threshold"),
        ("[redaction.layout]\nmodel = \"x\"\n", "model"),  # typo
        ("[redaction.layout]\nregion_ratio = 0.4\n", "region_ratio"),  # removed key
    ],
)
def test_bad_config_is_rejected_at_load(tmp_path, body, culprit):
    with pytest.raises(ValueError) as e:
        load_config(_write(tmp_path, body))
    assert culprit in str(e.value)


def test_fill_must_be_three_channels(tmp_path):
    with pytest.raises(ValueError):
        load_config(_write(tmp_path, "[redaction]\nfill = [0, 0]\n"))


def test_layout_section_is_independent_of_the_toggle(tmp_path):
    # The model settings stay parseable with the pass switched off, so flipping
    # the toggle back on does not need them retyped.
    body = '[redaction]\nredact_regions = false\n\n[redaction.layout]\nthreshold = 0.5\n'
    cfg = load_config(_write(tmp_path, body))
    assert cfg.redaction.redact_regions is False
    assert cfg.redaction.layout.threshold == 0.5
    assert cfg.redaction.layout.model_name == "PP-DocLayout_plus-L"  # default preserved


def test_layout_defaults():
    cfg = Config()
    assert cfg.redaction.layout.model_name == "PP-DocLayout_plus-L"
    # Pinned: the model's own default is 0.5, at which a photographed page's fee
    # table (0.38-0.43 on the corpus) is not detected at all.
    assert cfg.redaction.layout.threshold == 0.35
    assert cfg.redaction.layout.layout_nms is True


def test_committed_config_toml_loads():
    """The config shipped in the repo must satisfy its own schema."""
    root = Path(__file__).resolve().parent.parent
    assert load_config(root / "config.toml").engine.ocr_backend
