"""The detection commentary: every OCR line, each classifier match, the verdict.

This is the stream that diagnoses a wrong box — the one you read with
``PII_LOG_LEVEL=DEBUG``:

    line @(244,889 525x47 conf=99.40): 'MUSTERSTR.23'
        match PERSON 0.85 'MUSTERSTR.23' [SpacyRecognizer]
          Ignoring PERSON with only 1 name token(s)
        -> keep (item table)

:class:`Trace` is passed *down* through ``compute_boxes`` into the classifier
rather than scraped back off the logger by a temporary handler. Both would
capture the same text, but a handler has to answer two questions this does not:
which of several in-flight requests a record belongs to (it would need to filter
on the thread the CPU work runs in), and how to make presidio produce its
per-match explanations at all — those are gated on the logger's level, so
capturing them would mean flipping that level process-wide for the duration.
An argument has neither problem, and the seam is small: ``is_pii`` has three
implementations and one call site.

The log is not replaced, it is *joined*: :meth:`Trace.add` always emits at DEBUG,
so ``PII_LOG_LEVEL=DEBUG`` behaves exactly as before whether or not anyone asked
for a copy. The one visible difference is the logger name — the whole narrative
now arrives under ``backend.trace`` instead of being split between
``backend.pipeline`` and ``backend.classifiers.presidio``. The CLI formats with
``"%(message)s"`` and never showed the name; the API format does, and one name
for one stream reads better than two.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class Trace:
    """Collects the detection commentary for one document when asked to.

    ``collect=False`` — the default, and what every caller that only wants the
    log passes — keeps nothing and costs nothing beyond the level check.
    """

    def __init__(self, collect: bool = False) -> None:
        self._lines: list[str] | None = [] if collect else None

    @property
    def wanted(self) -> bool:
        """Whether anyone will read the next line: a collector, or the log.

        The single gate for work that only exists to be described. Presidio's
        ``return_decision_process`` is the expensive one — without it there are
        no ``match ...`` lines to add, so it has to follow this and not the
        logger's level alone.
        """
        return self._lines is not None or logger.isEnabledFor(logging.DEBUG)

    def add(self, fmt: str, *args: object) -> None:
        """Add one line, ``%``-formatted like a logging call.

        The formatting is deferred exactly as ``logger.debug`` defers it: this
        runs per OCR line, several times each, and on a page nobody is watching
        the interpolation must not happen.
        """
        if not self.wanted:
            return
        message = fmt % args if args else fmt
        logger.debug(message)
        if self._lines is not None:
            self._lines.append(message)

    @property
    def collected(self) -> str | None:
        """The trace as text, or ``None`` when this one was not collecting — so
        a caller hands the result on without re-checking the flag it passed in."""
        return "\n".join(self._lines) if self._lines is not None else None
