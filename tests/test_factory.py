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


def test_the_layout_detector_runs_on_the_engine_the_ocr_does(monkeypatch):
    """One machine-level choice, one answer.

    The preset names an OCR backend, but what it really selects is the runtime
    that executes paddle models here — and the layout detector is a paddle model
    too. A page whose text models run on ONNX Runtime while its layout model
    stays native is a configuration disagreeing with itself; it is also slower
    for nothing, since that checkpoint has an ONNX build returning byte-identical
    regions (measured over 12 sample pages, all 194 of them)."""
    seen: dict[str, str] = {}
    monkeypatch.setattr(factory, "_build_ocr", lambda backend, _t: seen.setdefault("ocr", backend))
    monkeypatch.setattr(factory, "_build_classifier", lambda _n, _t: None)
    monkeypatch.setattr(
        factory, "_build_layout_detector", lambda _m, _t, _n, engine: seen.setdefault("layout", engine)
    )

    factory.build_pipeline(Config(engine=EngineConfig(name="onnx")))
    assert seen == {"ocr": "onnxruntime", "layout": "onnxruntime"}

    seen.clear()
    factory.build_pipeline(Config(engine=EngineConfig(name="native")))
    assert seen == {"ocr": "paddle", "layout": "paddle"}
