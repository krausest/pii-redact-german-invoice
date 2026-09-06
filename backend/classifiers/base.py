"""The PII classifier interface."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from backend.pii import Span
from backend.trace import Trace


@runtime_checkable
class Classifier(Protocol):
    """Finds model-detected PII in one page's text, as labeled spans.

    ``text`` is the whole page joined in reading order (see
    :func:`backend.document.build_document`), not a single OCR line. That is the
    point: an entity is read in the context it actually stands in, and a name
    broken across a line wrap is one span rather than two invisible halves. The
    layout hierarchy is what makes that context coherent — without it the
    interleaved column order of raw OCR would put unrelated blocks side by side
    and cost more than it buys.

    Offsets are character offsets into ``text``. Labels come from
    :class:`backend.pii.PiiLabel`, so a caller never has to know whether it is
    talking to presidio or a zero-shot model.

    The deterministic rules (salutation, German address, the spatial birthdate
    and identifier pass) are applied by the pipeline, not here — a classifier
    only owns the model-based half.

    ``trace`` is where the classifier says *why*, and it is required rather than
    defaulted: there is one caller, and a default would quietly grow a second
    code path where the interesting half of the commentary goes missing."""

    def spans(self, text: str, trace: Trace) -> list[Span]: ...
