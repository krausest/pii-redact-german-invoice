"""The PII classifier interface."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from backend.trace import Trace


@runtime_checkable
class Classifier(Protocol):
    """Classifies one OCR line's text: True if it contains PII to redact.

    The deterministic per-line rules (salutation, German address) and the spatial
    birthdate rule are applied by the pipeline, not here — a classifier only owns
    the model-based decision (NER / zero-shot).

    ``trace`` is where the classifier says *why*, and it is required rather than
    defaulted: there is one caller, and a default would quietly grow a second
    code path where the interesting half of the commentary goes missing."""

    def is_pii(self, text: str, trace: Trace) -> bool: ...
