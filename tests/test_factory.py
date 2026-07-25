"""Engine resolution (the engine is fixed per process, not per request)."""

from __future__ import annotations

from backend import factory
from backend.config import Config, EngineConfig


def test_resolve_engine_reports_the_pair():
    """The dict is the wire shape: /health and the report's `engine` block both
    publish it verbatim, so this pins their contract too."""
    assert factory.resolve_engine(Config()) == {
        "name": "native",
        "ocr": "paddle",
        "classifier": "presidio",
    }
    assert factory.resolve_engine(Config(engine=EngineConfig(name="onnx"))) == {
        "name": "onnx",
        "ocr": "onnxruntime",
        "classifier": "presidio",
    }


def test_resolve_engine_applies_per_axis_overrides():
    config = Config(engine=EngineConfig(name="native", ocr_backend="onnxruntime"))
    assert factory.resolve_engine(config) == {
        "name": "native",
        "ocr": "onnxruntime",
        "classifier": "presidio",
    }
