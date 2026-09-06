"""Build a :class:`RedactionPipeline` from a :class:`Config`.

This is where the heavy model objects (OCR backend, classifier, unwarper) are
instantiated, so calling it pays the cold-start model-load cost. The engine is
fixed per process by config, so the API builds one pipeline per worker at startup
and the CLI builds one per run.
"""

from __future__ import annotations

import typing

from backend.classifiers.base import Classifier
from backend.config import ClassifierName, Config
from backend.pipeline import RedactionPipeline


def _build_ocr(ocr_backend: str, det_box_thresh: float):
    from backend.ocr.paddle import PaddleOCRBackend

    return PaddleOCRBackend(engine=ocr_backend, det_box_thresh=det_box_thresh)


def _build_classifier(classifier: str, score_threshold: float):
    """Construct one classifier by name. Both imports are local: each drags in a
    different several-hundred-megabyte runtime (spaCy here, torch there), and
    naming one must not load the other."""
    if classifier == "presidio":
        from backend.classifiers.presidio import PresidioClassifier

        return PresidioClassifier(score_threshold=score_threshold)
    if classifier == "guard-omni":
        from backend.classifiers.guard_omni import GuardOmniClassifier

        return GuardOmniClassifier()
    raise ValueError(f"unknown classifier {classifier!r}")


def classifier_names() -> tuple[str, ...]:
    """Every classifier a request may name. Published by ``/health`` and used by
    :mod:`backend.options` to validate ``?classifier=``, so the wire contract and
    what can actually be built are the same list."""
    return typing.get_args(ClassifierName)


def _build_layout_detector(model_name: str, threshold: float, layout_nms: bool, engine: str):
    from backend.layout import PaddleLayoutDetector

    return PaddleLayoutDetector(
        model_name=model_name, threshold=threshold, layout_nms=layout_nms, engine=engine
    )


def build_classifier(config: Config) -> Classifier:
    """The classifier half of the engine, without the OCR backend beside it.

    :mod:`backend.replay` needs exactly this: it feeds the pipeline frozen OCR
    lines, so building the OCR backend would only cost model loads nothing calls.
    Going through here rather than importing the class keeps one place resolving
    a classifier *name*."""
    _, classifier_name = config.engine.resolve()
    return _build_classifier(classifier_name, config.redaction.score_threshold)


def build_pipeline(config: Config) -> RedactionPipeline:
    ocr_backend, default_classifier = config.engine.resolve()
    ocr = _build_ocr(ocr_backend, config.engine.det_box_thresh)
    # A factory per selectable classifier, none of them called yet: the choice is
    # per request, and a worker only ever asked for one must not load the other.
    # The unwarper next door is lazy for the same reason and for longer.
    threshold = config.redaction.score_threshold
    factories = {
        name: (lambda n=name: _build_classifier(n, threshold)) for name in classifier_names()
    }

    def unwarper_factory():
        from backend.unwarp import DocUnwarper

        return DocUnwarper()

    return RedactionPipeline(
        ocr=ocr,
        classifier_factories=factories,
        default_classifier=default_classifier,
        # Built lazily: `unwarp` is a per-request option, so the capability must
        # always be available, but only processes that use it pay for it.
        unwarper_factory=unwarper_factory,
        fill=tuple(config.redaction.fill),
        padding=config.redaction.padding,
        unwarp_enabled=config.redaction.unwarp,
        # `None` is how the pipeline is told to skip the region pass, so the
        # toggle and its geometry collapse into one argument.
        # Built eagerly, like the OCR backend — docker/warmup.py relies on
        # pipeline construction alone baking every model into the image.
        layout=(
            _build_layout_detector(
                config.redaction.layout.model_name,
                config.redaction.layout.threshold,
                config.redaction.layout.layout_nms,
                # The same inference engine the OCR models run on: it is one
                # machine-level choice, so there is one place that answers it.
                ocr_backend,
            )
            if config.redaction.redact_regions
            else None
        ),
    )


def resolve_engine(config: Config) -> dict[str, str]:
    """What this process runs on, in the shape both ``/health`` and the report's
    ``engine`` block publish it: ``{"name", "ocr", "classifier"}``."""
    ocr, classifier = config.engine.resolve()
    return {"name": config.engine.name, "ocr": ocr, "classifier": classifier}
