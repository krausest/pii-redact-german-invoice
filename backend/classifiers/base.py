"""The PII classifier interface."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Classifier(Protocol):
    """Classifies one OCR line's text: True if it contains PII to redact.

    The deterministic per-line rules (salutation, German address) and the spatial
    birthdate rule are applied by the pipeline, not here — a classifier only owns
    the model-based decision (NER / zero-shot)."""

    def is_pii(self, text: str) -> bool: ...
