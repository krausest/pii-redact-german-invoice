"""Build a :class:`RedactionPipeline` from a :class:`Config`.

This is where the heavy model objects (OCR backend, classifier, unwarper) are
instantiated, so calling it pays the cold-start model-load cost. The engine is
fixed per process by config, so the API builds one pipeline per worker at startup
and the CLI builds one per run.
"""

from __future__ import annotations

from backend.codes import CodeParams
from backend.config import Config
from backend.pipeline import RedactionPipeline
from backend.regions import RegionParams


def _build_ocr(ocr_backend: str, det_box_thresh: float):
    from backend.ocr.paddle import PaddleOCRBackend

    return PaddleOCRBackend(engine=ocr_backend, det_box_thresh=det_box_thresh)


def _build_classifier(classifier: str, score_threshold: float):
    if classifier == "presidio":
        from backend.classifiers.presidio import PresidioClassifier

        return PresidioClassifier(score_threshold=score_threshold)
    if classifier == "gliner":
        try:
            from backend.classifiers.gliner import GlinerClassifier
        except ModuleNotFoundError as e:
            raise ModuleNotFoundError(
                "The 'gliner' engine requires the optional 'gliner' dependency "
                "(it pulls torch). Install it with:  uv sync --extra gliner"
            ) from e

        return GlinerClassifier()
    raise ValueError(f"unknown classifier {classifier!r}")


def build_pipeline(config: Config) -> RedactionPipeline:
    ocr_backend, classifier_name = config.engine.resolve()
    ocr = _build_ocr(ocr_backend, config.engine.det_box_thresh)
    classifier = _build_classifier(classifier_name, config.redaction.score_threshold)

    def unwarper_factory():
        from backend.unwarp import DocUnwarper

        return DocUnwarper()

    return RedactionPipeline(
        ocr=ocr,
        classifier=classifier,
        # Built lazily: `unwarp` is a per-request option, so the capability must
        # always be available, but only processes that use it pay for it.
        unwarper_factory=unwarper_factory,
        fill=tuple(config.redaction.fill),
        padding=config.redaction.padding,
        unwarp_enabled=config.redaction.unwarp,
        # `None` is how the pipeline is told to skip the region pass, so the
        # toggle and its geometry collapse into one argument.
        regions=(
            RegionParams(
                **config.redaction.regions.model_dump(),
                padding=config.redaction.padding,
            )
            if config.redaction.redact_regions
            else None
        ),
        # Same `None`-means-off convention as `regions`.
        codes=(
            CodeParams(
                margin_frac=config.redaction.code_margin_frac,
                padding=config.redaction.padding,
            )
            if config.redaction.redact_codes
            else None
        ),
    )


def resolve_engine(config: Config) -> dict[str, str]:
    """What this process runs on, in the shape both ``/health`` and the report's
    ``engine`` block publish it: ``{"name", "ocr", "classifier"}``."""
    ocr, classifier = config.engine.resolve()
    return {"name": config.engine.name, "ocr": ocr, "classifier": classifier}
