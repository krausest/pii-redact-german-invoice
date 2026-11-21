"""Query-parameter parsing — strict on names, on values, and on spelling."""

from __future__ import annotations

import pytest

from backend.config import Config, RedactionConfig
from backend.options import AssembleOptions, RedactOptions, WordListsIn


def redact(raw: dict, config: Config | None = None) -> RedactOptions:
    return RedactOptions.from_query(raw, config or Config())


def assemble(raw: dict, config: Config | None = None) -> AssembleOptions:
    return AssembleOptions.from_query(raw, config or Config())


def test_defaults_come_from_config():
    config = Config(redaction=RedactionConfig(unwarp=False, pdf_dpi=150, jpeg_quality=80))
    opts = redact({}, config)
    assert opts.unwarp is False
    assert opts.pdf_dpi == 150
    assert opts.jpeg_quality == 80
    assert opts.json_output is False
    assert opts.debug is False

    assert assemble({}, config).dpi == 150
    assert assemble({}, config).format == "pdf"


@pytest.mark.parametrize("value,expected", [("true", True), ("1", True), ("false", False), ("0", False)])
def test_booleans(value, expected):
    assert redact({"json-output": value}).json_output is expected


def test_hyphenated_wire_names():
    opts = redact({"json-output": "true", "pdf-dpi": "300", "jpeg-quality": "70"})
    assert (opts.json_output, opts.pdf_dpi, opts.jpeg_quality) == (True, 300, 70)


@pytest.mark.parametrize(
    "raw",
    [
        {"unwrap": "false"},  # typo in the name
        {"json_output": "true"},  # underscores are not the wire spelling
        {"engine": "onnx"},  # engine is fixed by config
        {"detect": "false"},  # detection always runs
        {"include": "boxes"},
        {"output": "pdf"},
        {"unwarp": "maybe"},
        {"jpeg-quality": "0"},
        {"jpeg-quality": "101"},
        {"pdf-dpi": "5"},
        {"pdf-dpi": "nope"},
        {"region-ratio": "0.5"},  # removed option
    ],
)
def test_redact_rejects(raw):
    with pytest.raises(ValueError):
        redact(raw)


def test_debug_needs_json_output():
    # There is nowhere to put a trace in a file response, so the combination is
    # an error rather than a silently ignored option.
    with pytest.raises(ValueError, match="debug=true requires json-output=true"):
        redact({"debug": "true"})
    assert redact({"debug": "true", "json-output": "true"}).debug is True
    assert redact({"debug": "false"}).debug is False  # false alone is fine


@pytest.mark.parametrize(
    "raw",
    [{"format": "png"}, {"format": "PDF"}, {"dpi": "0"}, {"unwarp": "false"}, {"pdf-dpi": "200"}],
)
def test_assemble_rejects(raw):
    with pytest.raises(ValueError):
        assemble(raw)


def test_word_lists_come_from_a_body():
    body = WordListsIn.from_json({"redact_words": ["Andrea Muster", " Muster "], "keep_words": ["Praxis"]})
    assert body.lists().redact == ("Andrea Muster", "Muster")
    assert body.lists().keep == ("Praxis",)
    assert WordListsIn.from_json({}).lists().redact == ()


@pytest.mark.parametrize(
    "data",
    [
        {"redact_words": ["  "]},
        {"keep_words": ["x" * 101]},
        {"redact_words": ["w"] * 201},
        {"redact_words": ["Muster"], "keep_words": ["MUSTER"]},
        {"redact-word": ["Muster"]},
    ],
)
def test_word_lists_reject(data):
    with pytest.raises(ValueError):
        WordListsIn.from_json(data)


@pytest.mark.parametrize("name", ["redact-word", "keep-word"])
def test_word_lists_are_not_query_parameters(name):
    with pytest.raises(ValueError, match="unknown parameter"):
        redact({name: "Muster"})


def test_granularity_comes_from_config_and_the_query_wins():
    assert redact({}).granularity == "word"
    line = Config(redaction=RedactionConfig(granularity="line"))
    assert redact({}, line).granularity == "line"
    assert redact({"granularity": "word"}, line).granularity == "word"
    with pytest.raises(ValueError):
        redact({"granularity": "letter"})
