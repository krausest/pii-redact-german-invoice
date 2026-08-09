"""The batch CLI — file collection, and the flags that mirror the /api/redact
query parameters.

A FakePipeline is injected in place of ``build_pipeline``, so no ML model loads.
"""

from __future__ import annotations

import base64
import json

import pytest

from backend.cli import build_parser, collect_input_files, main, query_from_args
from backend.config import Config, RedactionConfig
from backend.options import RedactOptions
from tests.conftest import FakePipeline, make_image_bytes, make_pdf_bytes


@pytest.fixture
def run_cli(monkeypatch):
    """Runs ``main`` with a stub pipeline and a known config, returning the exit
    code. The injected FakePipeline is left on ``run.pipeline`` so a test can see
    which primitives ran."""

    def run(argv, config: Config | None = None):
        cfg = config or Config()
        run.pipeline = FakePipeline()
        monkeypatch.setattr("backend.cli.load_config", lambda: cfg)
        monkeypatch.setattr("backend.cli.build_pipeline", lambda _cfg: run.pipeline)
        return main(argv)

    run.pipeline = None
    return run


def write_png(tmp_path, name="page.png"):
    p = tmp_path / name
    p.write_bytes(make_image_bytes("PNG"))
    return p


def write_pdf(tmp_path, name="doc.pdf", pages=2):
    p = tmp_path / name
    p.write_bytes(make_pdf_bytes(pages))
    return p


# -- file collection --------------------------------------------------------- #
def test_directory_is_scanned_one_level_and_redacted_outputs_skipped(tmp_path):
    write_png(tmp_path, "a.png")
    write_png(tmp_path, "b_redacted.jpg")
    (tmp_path / "notes.txt").write_text("x")
    (tmp_path / "sub").mkdir()
    write_png(tmp_path / "sub", "deep.png")

    assert [f.name for f in collect_input_files([str(tmp_path)])] == ["a.png"]


def test_unsupported_and_missing_paths_are_skipped(tmp_path, capsys):
    (tmp_path / "notes.txt").write_text("x")
    assert collect_input_files([str(tmp_path / "notes.txt"), str(tmp_path / "gone.png")]) == []
    err = capsys.readouterr().err
    assert "unsupported file type" in err and "path not found" in err


# -- the flags are the query parameters -------------------------------------- #
def test_untouched_flags_stay_out_of_the_query():
    """Defaults must come from the config, so an unset flag is simply absent."""
    args = build_parser().parse_args(["x.png"])
    assert query_from_args(args) == {}


def test_flags_map_to_their_query_names():
    args = build_parser().parse_args(
        ["--no-unwarp", "--json-output", "--pdf-dpi", "300", "--jpeg-quality", "55", "x.png"]
    )
    assert query_from_args(args) == {
        "unwarp": False,
        "json-output": True,
        "pdf-dpi": "300",
        "jpeg-quality": "55",
    }


def test_every_redact_query_parameter_has_a_flag():
    """If an option is added to RedactOptions, the CLI must grow a flag for it —
    query_from_args reads the model, so a missing flag is an AttributeError."""
    parsed = {name.replace("_", "-") for name in vars(build_parser().parse_args(["x.png"]))}
    assert set(RedactOptions.wire_names()) <= parsed


def test_unset_flags_take_the_config_defaults(run_cli, tmp_path):
    """No flag given, `unwarp = false` in the config — so the page is not flattened."""
    cfg = Config(redaction=RedactionConfig(unwarp=False, pdf_dpi=150, jpeg_quality=40))
    assert run_cli([str(write_png(tmp_path))], cfg) == 0
    assert "unwarp" not in run_cli.pipeline.calls


def test_a_flag_overrides_the_config_default(run_cli, tmp_path):
    cfg = Config(redaction=RedactionConfig(unwarp=False, pdf_dpi=150, jpeg_quality=40))
    assert run_cli(["--unwarp", str(write_png(tmp_path))], cfg) == 0
    assert run_cli.pipeline.calls == ["unwarp", "compute_boxes", "apply_boxes"]


def test_pdf_dpi_reaches_rasterization(run_cli, tmp_path):
    """The page is rasterized at --pdf-dpi, and the boxes belong to *that* image."""
    write_pdf(tmp_path, pages=1)
    assert run_cli(["--pdf-dpi", "300", "--json-output", str(tmp_path)]) == 0
    page = json.loads((tmp_path / "doc_redacted.json").read_text())["pages"][0]
    # write_pdf assembles at assemble_pdf's default 72 dpi, so the page is 40 pt wide
    assert page["width"] == round(40 * 300 / 72)


def test_bad_value_is_a_usage_error_reported_like_the_api(run_cli, tmp_path, capsys):
    write_png(tmp_path)
    assert run_cli(["--pdf-dpi", "10", str(tmp_path)]) == 2
    assert "pdf-dpi: Input should be greater than or equal to 36" in capsys.readouterr().err
    assert not list(tmp_path.glob("*_redacted*"))  # nothing processed


def test_debug_without_json_output_is_a_usage_error(run_cli, tmp_path, capsys):
    # The same rule and the same wording as the endpoint's 400 — the flags *are*
    # the query parameters, including what they refuse.
    write_png(tmp_path)
    assert run_cli(["--debug", str(tmp_path)]) == 2
    assert "debug=true requires json-output=true" in capsys.readouterr().err
    assert not list(tmp_path.glob("*_redacted*"))


def test_debug_writes_the_trace_into_the_report(run_cli, tmp_path):
    write_png(tmp_path)
    assert run_cli(["--debug", "--json-output", str(tmp_path)]) == 0
    report = json.loads((tmp_path / "page_redacted.json").read_text())
    assert report["debug"] == "fake pipeline: 1 box(es)"


def test_unknown_flag_is_rejected_by_argparse(run_cli, tmp_path):
    write_png(tmp_path)
    with pytest.raises(SystemExit):
        run_cli(["--unwrap", "false", str(tmp_path)])


# -- outputs ----------------------------------------------------------------- #
def test_image_in_jpeg_out_pdf_in_pdf_out(run_cli, tmp_path):
    write_png(tmp_path)
    write_pdf(tmp_path)
    assert run_cli([str(tmp_path)]) == 0
    assert (tmp_path / "page_redacted.jpg").exists()
    assert (tmp_path / "doc_redacted.pdf").read_bytes().startswith(b"%PDF")


def test_json_output_writes_the_api_report(run_cli, tmp_path):
    """The report's own shape is pinned by test_redact.py — both go through
    service.build_report. What is CLI-specific is the filename and that the
    report *replaces* the document."""
    write_pdf(tmp_path, pages=2)
    assert run_cli(["--json-output", str(tmp_path)]) == 0
    assert not (tmp_path / "doc_redacted.pdf").exists()

    report = json.loads((tmp_path / "doc_redacted.json").read_text())
    assert [p["index"] for p in report["pages"]] == [0, 1]
    assert base64.b64decode(report["redacted"]["data"]).startswith(b"%PDF")


def test_a_bad_file_does_not_abort_the_batch(run_cli, tmp_path, capsys):
    (tmp_path / "broken.png").write_bytes(b"not an image")
    write_png(tmp_path, "good.png")
    assert run_cli([str(tmp_path)]) == 1
    assert (tmp_path / "good_redacted.jpg").exists()
    assert "broken.png" in capsys.readouterr().err


def test_no_input_files_is_an_error(run_cli, tmp_path, capsys):
    assert run_cli([str(tmp_path)]) == 1
    assert "no jpg/jpeg/png/pdf files found" in capsys.readouterr().err
