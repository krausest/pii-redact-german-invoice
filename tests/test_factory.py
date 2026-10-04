"""Engine resolution (the engine is fixed per process, not per request)."""

from __future__ import annotations

from backend import factory
from backend.config import Config, EngineConfig


def test_resolve_engine_reports_the_pair():
    """The dict is the wire shape: /health and the report's `engine` block both
    publish it verbatim, so this pins their contract too."""
    assert factory.resolve_engine(Config()) == {"ocr": "onnxruntime", "classifier": "presidio"}
    config = Config(engine=EngineConfig(ocr_backend="paddle", classifier="guard-omni"))
    assert factory.resolve_engine(config) == {"ocr": "paddle", "classifier": "guard-omni"}


def test_the_layout_detector_runs_on_the_engine_the_ocr_does(monkeypatch):
    """One machine-level choice, one answer.

    `ocr_backend` names the OCR's runtime, but what it really selects is the runtime
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

    factory.build_pipeline(Config(engine=EngineConfig(ocr_backend="onnxruntime")))
    assert seen == {"ocr": "onnxruntime", "layout": "onnxruntime"}

    seen.clear()
    factory.build_pipeline(Config(engine=EngineConfig(ocr_backend="paddle")))
    assert seen == {"ocr": "paddle", "layout": "paddle"}


def test_no_classifier_is_built_until_one_is_asked_for(monkeypatch):
    """The choice is per request, so a worker holds a factory per classifier and
    a model only for the ones it was actually asked for. guard-omni is ~1 GB of
    torch weights; a process that only ever serves the default must not pay for
    the option to exist."""
    built: list[str] = []
    monkeypatch.setattr(factory, "_build_ocr", lambda *_a: None)
    monkeypatch.setattr(factory, "_build_classifier", lambda name, _t: built.append(name) or name)
    monkeypatch.setattr(factory, "_build_layout_detector", lambda *_a: None)

    pipeline = factory.build_pipeline(Config())
    assert built == []  # constructing the pipeline builds no classifier at all

    assert pipeline.classifier() == "presidio"  # the process default
    assert pipeline.classifier("guard-omni") == "guard-omni"
    assert built == ["presidio", "guard-omni"]

    pipeline.classifier("guard-omni")
    assert built == ["presidio", "guard-omni"]  # built once, then kept
