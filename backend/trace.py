"""The detection commentary: every layout region, its text, the spans, the verdict.

This is the stream that diagnoses a wrong box — the one you read with
``PII_LOG_LEVEL=DEBUG`` or ask for with ``?debug=true``. It is organised the way
the pipeline now reasons: **region first, then what is inside it**, because a
line's fate depends on the block it was read in.

    --- region 2  text  0.93  [134,175,368,286] ---
      wholetext: 'Herrn | Max Mustermann | Musterstr. 7 | 12345 Musterhausen'
      line @(134,175 234x28 conf=99.40): 'Herrn'
          SALUTATION 'Herrn' [rule SALUT 1.00]
          -> REDACT
      -> region REDACT (text 4/4 lines)

:class:`Trace` is passed *down* through ``compute_boxes`` into the classifier
rather than scraped back off the logger by a temporary handler. Both would
capture the same text, but a handler has to answer two questions this does not:
which of several in-flight requests a record belongs to (it would need to filter
on the thread the CPU work runs in), and how to make presidio produce its
per-match explanations at all — those are gated on the logger's level, so
capturing them would mean flipping that level process-wide for the duration.
An argument has neither problem, and the seam is small: ``Classifier.spans`` has
one call site.

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
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # only for the annotations below — no import cost at runtime
    from backend.models import Line
    from backend.verdict import PageVerdict

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


def format_line(line: "Line") -> str:
    """The ``line @(...)`` header, in one place.

    It is the anchor of the whole format: the replay parser matches on it to
    pair a snapshot's expectation with the line it belongs to, so the header has
    exactly one producer and both sides stay in step by construction."""
    return (
        f"line @({line.left},{line.top} {line.width}x{line.height} "
        f"conf={line.conf}): {line.text!r}"
    )


def trace_page(trace: Trace, verdict: PageVerdict) -> None:
    """Narrate one page: every detected region, the text it contributed, the
    lines inside it with their spans and verdict, then the region's own verdict.

    Every region is named, including the ones holding no OCR line at all — a
    logo, a stamp, a payment QR code. Those are precisely the boxes a text-only
    trace could never explain, and they are blackened on type alone, so leaving
    them out would hide the reason for a black rectangle nobody can account for.

    Lines are narrated in reading order, the way the classifier read the page
    rather than the way the detector emitted its boxes.
    """
    from backend.layout import lines_by_region

    per_region = lines_by_region(verdict.lines, verdict.regions)
    rank = {ln: pos for pos, ln in enumerate(verdict.reading)}
    claimed = {ln for group in per_region for ln in group}
    why_by_region = {hit.region: hit.label for hit in verdict.region_boxes}
    signed = {sig.closing: sig.box for sig in verdict.signatures}

    def narrate(group: list[Line], header: str) -> None:
        trace.add("%s", header)
        shown = sorted(group, key=lambda ln: rank.get(ln, len(verdict.reading)))
        joined = " | ".join(ln.text for ln in shown if ln.text.strip())
        if joined:
            trace.add("  wholetext: %r", joined)
        for ln in shown:
            if not ln.text.strip():
                continue
            trace.add("  %s", format_line(ln))
            for span in verdict.found.get(ln, []):
                trace.add(
                    "      %s %r [%s %.2f]", span.label, span.text, span.source, span.score
                )
            for span in verdict.dropped.get(ln, []):
                trace.add(
                    "      rejected %s %r [%s %.2f]", span.label, span.text, span.source, span.score
                )
            if ln in verdict.found:
                trace.add("      -> REDACT")
                if ln in verdict.notes:
                    trace.add("      note %r", verdict.notes[ln].text)
            elif ln in verdict.keep:
                # Why no classifier verdict was reported for this line.
                trace.add("      -> keep (%s)", verdict.keep[ln])
            else:
                trace.add("      -> keep")
            if ln in signed:
                trace.add("      signature %s", signed[ln].as_list())

    for r, region in enumerate(verdict.regions):
        why = why_by_region.get(r)
        narrate(
            per_region[r],
            f"--- region {r}  {region.label}  {region.score:.2f}  {region.box.as_list()} ---",
        )
        trace.add("  -> region %s", f"REDACT ({why})" if why else "keep")

    unclaimed = [ln for ln in verdict.lines if ln.text.strip() and ln not in claimed]
    if unclaimed:
        narrate(unclaimed, "--- unclaimed lines ---")
