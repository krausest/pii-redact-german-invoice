"""The sources of candidate spans, and what each may redact on a kept line.

Every detector proposes spans for a whole :class:`~backend.document.Document`;
:meth:`Detector.admits` says which of them a kept line lets through. Keeping
that decision on the detector, not on the span's source string, is what lets a
new detector join without the pipeline knowing it.
"""

from __future__ import annotations

from typing import Protocol

from backend.classifiers.base import Classifier
from backend.document import Document
from backend.harvest import Memory
from backend.pii import Span, rule_source
from backend.rules import rule_spans
from backend.rules.keep import KeepReason
from backend.trace import Trace


class Detector(Protocol):
    def spans(self, doc: Document, trace: Trace) -> list[Span]: ...

    def admits(self, span: Span, keep: str | None) -> bool:
        """Whether ``span`` may redact a line kept for ``keep`` (``None``: not kept)."""
        ...


class RuleDetector:
    """The deterministic rules. A rule wins over every keep reason, except a
    legal form on the clearing house's name — the name PVS is kept for."""

    def spans(self, doc: Document, trace: Trace) -> list[Span]:  # noqa: ARG002
        return rule_spans(doc)

    def admits(self, span: Span, keep: str | None) -> bool:
        return not (keep == KeepReason.PVS and span.source == rule_source("ORG_LEGAL"))


class ModelDetector:
    """A classifier. It yields on every kept line: inside the item table a
    two-word service text reads like a name to it."""

    def __init__(self, classifier: Classifier) -> None:
        self._classifier = classifier

    def spans(self, doc: Document, trace: Trace) -> list[Span]:
        return self._classifier.spans(doc.text, trace)

    def admits(self, span: Span, keep: str | None) -> bool:  # noqa: ARG002
        return keep is None


class MemoryDetector:
    """The names and birthdates remembered so far. It yields only on specialty
    and clearing-house lines, which hold no name."""

    def __init__(self, memory: Memory) -> None:
        self._memory = memory

    def spans(self, doc: Document, trace: Trace) -> list[Span]:  # noqa: ARG002
        return self._memory.recall(doc)

    def admits(self, span: Span, keep: str | None) -> bool:  # noqa: ARG002
        return keep not in (KeepReason.FACH, KeepReason.PVS)
