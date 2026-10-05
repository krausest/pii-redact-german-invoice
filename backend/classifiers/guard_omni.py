"""guard-omni: a zero-shot GLiNER2 model as the classifier half of the pipeline.

``hivetrace/gliner-guard-omni`` on Hugging Face — GLiNER2 (package ``gliner2``,
vendor ``fastino``), encoder ``microsoft/mdeberta-v3-base``. **Not** the GLiNER
that was tried and dropped here: different library, different vendor, different
architecture. The dependency-weight objection from that removal still applies
though, which is why ``pyproject.toml`` pins torch to CPU wheels.

It is an *alternative* to :mod:`backend.classifiers.presidio`, not an addition:
one classifier per run, chosen with ``?classifier=``. Since :mod:`backend.pii`
gave every detector the same span vocabulary, that is all it takes — the rules,
the item-table gate, the name memory and the region pass are untouched by which
model is behind this seam.

What this model does *not* replace, whichever way that comparison goes: the
deterministic rules decide questions that are not language questions. Which
number is personal — an insurance or invoice number, not a fee number or an
amount — is decided by the label printed beside it, and all of them are "a
number" to any zero-shot extractor. Measured live while porting this:
guard-omni tags an invoice number as ``document_id`` at 0.97.

``gliner2`` and ``torch`` are imported **inside the constructor**, never at
module scope, so importing this module for :data:`GLINER2_LABELS` costs nothing
and the fast test suite never loads torch.
"""

from __future__ import annotations

from backend.document import windowed
from backend.pii import GUARD_OMNI_LABELS, Span
from backend.trace import Trace

GUARD_OMNI_MODEL = "hivetrace/gliner-guard-omni"

# GUARD_OMNI.md's measured sweet spot on a corpus of this kind: 0.4 buys recall
# at a precision the page cannot afford, 0.6 starts dropping short names that
# carry little context.
GUARD_OMNI_THRESHOLD = 0.5

# The vocabulary handed to the model: a subset of GLiNER2's own fixed ~42-label
# space, so the wording is not free text. Every entry maps in
# `backend.pii.GUARD_OMNI_LABELS`; a label the model returns that is not in that
# table is dropped, the same safety valve that keeps presidio's LOCATION out.
GLINER2_LABELS = sorted(GUARD_OMNI_LABELS)


class GuardOmniClassifier:
    """Duck-types :class:`backend.classifiers.base.Classifier`."""

    def __init__(
        self,
        threshold: float = GUARD_OMNI_THRESHOLD,
        model_id: str = GUARD_OMNI_MODEL,
    ) -> None:
        # Constructing loads ~1 GB of weights, so this happens once per process
        # — and, unlike every other model here, only in a process that was
        # actually asked for this classifier (see RedactionPipeline's registry).
        from gliner2 import GLiNER2

        self._model = GLiNER2.from_pretrained(model_id)
        self._threshold = threshold

    def spans(self, text: str, trace: Trace) -> list[Span]:
        # Windowed for cost, not for truncation — see `document.windowed`. Every
        # page in this corpus is a single window, so this is the same one call
        # it looks like; a dense page nobody has sent yet becomes several short
        # ones instead of one quadratically expensive one.
        return windowed(text, lambda chunk: self._extract(chunk, trace))

    def _extract(self, text: str, trace: Trace) -> list[Span]:
        # format_results=False is not a preference: `True` drops start/end and
        # returns text+confidence only, which cannot be mapped back to a line.
        result = self._model.extract_entities(
            text,
            GLINER2_LABELS,
            threshold=self._threshold,
            format_results=False,
            include_confidence=True,
            include_spans=True,
        )
        spans: list[Span] = []
        for label, raw, start, end, score in _entities(result):
            mapped = GUARD_OMNI_LABELS.get(label)
            if mapped is None:
                # Not an error: the vocabulary above is what we asked for, and
                # this is where a label we decided not to act on is dropped.
                trace.add("      Ignoring %s %r: label not mapped", label, raw)
                continue
            spans.append(
                Span(
                    label=mapped,
                    start=start,
                    end=end,
                    text=text[start:end],
                    source="guard-omni",
                    score=score,
                )
            )
            if trace.wanted:
                trace.add("      match %s %.2f %r [guard-omni]", label, score, raw)
        return spans


def _entities(result: object):
    """Flatten one ``extract_entities`` response into
    ``(label, text, start, end, score)`` tuples.

    Defensive because the shape genuinely varies: ``entities`` comes back as a
    dict *or* as a one-element list holding one, and each label's value is a
    single dict *or* a list of them. An entry without offsets is skipped rather
    than guessed at — a span with no position cannot draw a box.
    """
    entities = result.get("entities") if isinstance(result, dict) else None
    if isinstance(entities, list):
        entities = entities[0] if entities else {}
    if not isinstance(entities, dict):
        return
    for label, found in entities.items():
        if isinstance(found, dict):
            found = [found]
        for item in found or []:
            if not isinstance(item, dict):
                continue
            start, end = item.get("start"), item.get("end")
            if start is None or end is None:
                continue
            yield (
                label,
                item.get("text", ""),
                int(start),
                int(end),
                float(item.get("confidence", 1.0)),
            )
