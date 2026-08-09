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
    cfg = load_config(_write(tmp_path, '[engine]\nname = "onnx"\n'))
    assert cfg.engine.name == "onnx"
    assert cfg.redaction.padding == 2  # default preserved


@pytest.mark.parametrize(
    "name,expected",
    [
        ("native", ("paddle", "presidio")),
        ("onnx", ("onnxruntime", "presidio")),
    ],
)
def test_preset_resolution(name, expected):
    from backend.config import EngineConfig

    assert EngineConfig(name=name).resolve() == expected


def test_det_box_thresh_default_is_below_paddles_own(tmp_path):
    # Pinned: PaddleOCR defaults to 0.6, at which a full-width imprint footer in
    # small type is not detected at all. Raising this back silently loses it.
    assert Config().engine.det_box_thresh == 0.5
    cfg = load_config(_write(tmp_path, "[engine]\ndet_box_thresh = 0.35\n"))
    assert cfg.engine.det_box_thresh == 0.35


def test_explicit_override_beats_the_preset():
    from backend.config import EngineConfig

    assert EngineConfig(name="onnx", ocr_backend="paddle").resolve() == (
        "paddle",
        "presidio",
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


@pytest.mark.parametrize("env", ["PII_UNWARP", "PII_REDACT_REGIONS", "PII_REDACT_CODES"])
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


@pytest.mark.parametrize("env", ["PII_UNWARP", "PII_REDACT_REGIONS", "PII_REDACT_CODES"])
def test_env_rejects_a_non_boolean(tmp_path, monkeypatch, env):
    monkeypatch.setenv(env, "maybe")
    with pytest.raises(ValueError) as e:
        load_config(tmp_path / "does_not_exist.toml")
    # the message names the config field, not the variable — same as a bad TOML value
    assert env.removeprefix("PII_").lower() in str(e.value)


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
        ("[engine]\ndet_box_thresh = 1.5\n", "det_box_thresh"),  # not a probability
        ("[api]\nworkers = 0\n", "workers"),
        ("[redaction.regions]\nheader_frac = 0.9\n", "header_frac"),  # over the 0.5 cap
        ("[redaction.regions]\ngap_factor = 0\n", "gap_factor"),
        ("[redaction.regions]\ncolumn_frac = 0.5\n", "column_frac"),  # typo
    ],
)
def test_bad_config_is_rejected_at_load(tmp_path, body, culprit):
    with pytest.raises(ValueError) as e:
        load_config(_write(tmp_path, body))
    assert culprit in str(e.value)


def test_fill_must_be_three_channels(tmp_path):
    with pytest.raises(ValueError):
        load_config(_write(tmp_path, "[redaction]\nfill = [0, 0]\n"))


def test_regions_section_is_independent_of_the_toggle(tmp_path):
    # Geometry stays parseable with the pass switched off, so flipping the toggle
    # back on does not need the fractions retyped.
    body = "[redaction]\nredact_regions = false\n\n[redaction.regions]\nfooter_frac = 0.2\n"
    cfg = load_config(_write(tmp_path, body))
    assert cfg.redaction.redact_regions is False
    assert cfg.redaction.regions.footer_frac == 0.2
    assert cfg.redaction.regions.header_frac == 0.12  # default preserved


def test_recipient_window_defaults_and_override(tmp_path):
    cfg = load_config(_write(tmp_path, ""))
    assert cfg.redaction.regions.recipient_y_min_frac == 0.05
    assert cfg.redaction.regions.recipient_y_max_frac == 0.45
    # An empty window (max <= min) is the documented off switch and must parse.
    body = "[redaction.regions]\nrecipient_y_max_frac = 0.0\n"
    assert load_config(_write(tmp_path, body)).redaction.regions.recipient_y_max_frac == 0.0


def test_code_margin_is_independent_of_the_toggle(tmp_path):
    # Same bargain as the regions geometry above: the margin survives the pass
    # being switched off.
    body = "[redaction]\nredact_codes = false\ncode_margin_frac = 0.2\n"
    cfg = load_config(_write(tmp_path, body))
    assert cfg.redaction.redact_codes is False
    assert cfg.redaction.code_margin_frac == 0.2


def test_code_margin_frac_is_bounded(tmp_path):
    with pytest.raises(ValueError) as e:
        load_config(_write(tmp_path, "[redaction]\ncode_margin_frac = 0.9\n"))
    assert "code_margin_frac" in str(e.value)


def test_committed_config_toml_loads():
    """The config shipped in the repo must satisfy its own schema."""
    root = Path(__file__).resolve().parent.parent
    assert load_config(root / "config.toml").engine.resolve()
