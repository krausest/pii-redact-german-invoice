"""The user's word lists: entries that are always redacted, and entries cut
out of every other finding.

An entry matches as a whole word or phrase, in any casing; whitespace inside a
phrase matches any whitespace, line breaks included, so a phrase wrapped across
two lines still matches both.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from backend.document import Document
from backend.ocr_variants import VariantIndex
from backend.pii import USER_LIST, PiiLabel, Span
from backend.textnorm import fold

# A remainder needs a letter or digit to be worth a box.
_PIECE = re.compile(r"\w(?:.*\w)?", re.DOTALL)

# Shorter entries match exactly only: one confusion would make them another word.
_MIN_VARIANT_LENGTH = 4


def _variant_ok(word: str) -> bool:
    return len(word) >= _MIN_VARIANT_LENGTH


def _pattern(entries: tuple[str, ...]) -> re.Pattern[str] | None:
    """One alternation over ``entries``, longest first; ``None`` when empty."""
    words = sorted({e.strip() for e in entries if e.strip()}, key=len, reverse=True)
    if not words:
        return None
    alts = (r"\s+".join(map(re.escape, w.split())) for w in words)
    return re.compile(rf"(?<!\w)(?:{'|'.join(alts)})(?!\w)", re.IGNORECASE)


@dataclass(frozen=True)
class WordLists:
    """``redact``: always blacken the entry. ``keep``: cut the entry out of every
    other finding; the rest of the finding still redacts."""

    redact: tuple[str, ...] = ()
    keep: tuple[str, ...] = ()
    _redact_re: re.Pattern[str] | None = field(init=False, repr=False, compare=False)
    _keep_re: re.Pattern[str] | None = field(init=False, repr=False, compare=False)
    _redact_variants: VariantIndex = field(init=False, repr=False, compare=False)
    _keep_variants: VariantIndex = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        # Folded like the OCR text, so "René" on a list meets "Rene" on the page.
        redact, keep = tuple(map(fold, self.redact)), tuple(map(fold, self.keep))
        object.__setattr__(self, "_redact_re", _pattern(redact))
        object.__setattr__(self, "_keep_re", _pattern(keep))
        object.__setattr__(self, "_redact_variants", VariantIndex(redact))
        object.__setattr__(self, "_keep_variants", VariantIndex(keep))

    def redact_spans(self, doc: Document) -> list[Span]:
        """A span for every occurrence of a ``redact`` entry in ``doc``, and of
        its OCR variants."""
        if self._redact_re is None:
            return []
        spans = [
            Span(PiiLabel.LISTED, m.start(), m.end(), m.group(), USER_LIST)
            for m in self._redact_re.finditer(doc.text)
        ]
        for start, end, _ in self._redact_variants.find(doc.text, _variant_ok):
            spans.append(Span(PiiLabel.LISTED, start, end, doc.text[start:end], USER_LIST))
        return spans

    def carve(self, span: Span) -> list[Span]:
        """``span`` without its ``keep`` entries and their OCR variants: the
        remaining pieces, or ``[span]`` when none matches."""
        if self._keep_re is None:
            return [span]
        cuts = [m.span() for m in self._keep_re.finditer(span.text)]
        cuts += [(a, b) for a, b, _ in self._keep_variants.find(span.text, _variant_ok)]
        if not cuts:
            return [span]
        pieces, pos = [], 0
        for a, b in [*sorted(cuts), (len(span.text), len(span.text))]:
            m = _PIECE.search(span.text, pos, a) if a > pos else None
            if m:
                start, end = span.start + m.start(), span.start + m.end()
                pieces.append(replace(span, start=start, end=end, text=m.group()))
            pos = max(pos, b)
        return pieces

EMPTY = WordLists()
