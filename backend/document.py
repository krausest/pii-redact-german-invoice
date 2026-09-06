"""One page's OCR lines as a single text, and entity spans mapped back onto them.

The pipeline classifies a *document*, not a line. This module is the bridge in
both directions: :func:`build_document` joins the lines (already in reading
order — see :func:`backend.layout.document_order`) into the text a detector is
asked about, and :func:`spans_to_lines` takes the findings back to the lines
whose pixels have to be blackened.

Ported from the ``wholetext`` experiment on the ``guard_omni`` branch, minus its
sliding-window machinery: windows exist there because guard-omni's encoder caps
at 512 tokens, and presidio has no such limit. They come back when guard-omni
does.

Pure functions over ``Line``/``Span``: nothing here imports a model.
"""

from __future__ import annotations

from collections.abc import Sequence

from backend.models import Line
from backend.pii import PiiLabel, Span


def build_document(lines: Sequence[Line]) -> tuple[str, list[tuple[int, int]]]:
    """Join every line's text with ``"\\n"`` and return the joined text
    alongside each line's ``(start, end)`` char-offset span in it.

    ``"\\n"`` rather than a space: two words on separate OCR lines must not run
    together into what looks like one token — a phone number ending a line and a
    house number starting the next would otherwise fuse into a single number.

    ``bounds`` is index-aligned with ``lines``, and that alignment is the
    contract every caller relies on: a detector shifts its per-line offsets by
    ``bounds[i][0]``, and :func:`spans_to_lines` maps them back the same way.
    """
    parts: list[str] = []
    bounds: list[tuple[int, int]] = []
    pos = 0
    for line in lines:
        parts.append(line.text)
        bounds.append((pos, pos + len(line.text)))
        pos += len(line.text) + 1  # + the "\n"
    return "\n".join(parts), bounds


# The one label whose entity may legitimately run across a line break. Everything
# else in `PiiLabel` reaches us from a *pattern*, and a pattern describes a
# typographic unit: a German ZIP+city, a street, a phone number and an IBAN are
# each printed on one line by definition. A person's name is not — "Max" on one
# line and "Mustermann" on the next is one person, and seeing that is the whole
# reason the page is classified as one text.
_MAY_WRAP = frozenset({PiiLabel.PERSON})


def drop_wrapped(spans: Sequence[Span]) -> list[Span]:
    """Discard pattern matches that only exist because two lines were joined.

    Joining the page puts a ``"\\n"`` between lines, and ``\\s`` in a regex
    matches it — so a recognizer glues the tail of one line to the head of the
    next and reports something that is nowhere on the page. Measured on the
    corpus this is not theoretical: ``DE_PLZ_CITY`` turned a bare invoice number
    at a line end plus the first word of the line below it into an address three
    times over ("12345" + "Seite", "12345" + "Geschälshührer"), each blackening
    a line that has to stay readable.

    The cost of the rule is an address genuinely broken across a wrap, which
    then falls to the per-line street/ZIP rules that ran before and still run.
    """
    return [s for s in spans if s.label in _MAY_WRAP or "\n" not in s.text]


def spans_to_lines(
    spans: Sequence[Span], bounds: Sequence[tuple[int, int]]
) -> list[list[Span]]:
    """Per-line list of spans clipped to that line's character range,
    index-aligned with ``bounds`` (and so with the lines it was built from).

    A span that only *partially* overlaps a line still touches it. That is the
    whole point of classifying a document rather than a line: a name broken
    across a wrap ("Max\\nMustermann") is one span, and both lines it crosses
    have to be blackened, not just the one holding its start.
    """
    per_line: list[list[Span]] = [[] for _ in bounds]
    for s in spans:
        for i, (lo, hi) in enumerate(bounds):
            start, end = max(s.start, lo), min(s.end, hi)
            if start < end:
                # Slice relative to the *span's* own start, not the line's — a
                # span reaching a second line needs its tail, not its head.
                per_line[i].append(
                    Span(
                        label=s.label,
                        start=start - lo,
                        end=end - lo,
                        text=s.text[start - s.start : end - s.start],
                        source=s.source,
                        score=s.score,
                    )
                )
    return per_line
