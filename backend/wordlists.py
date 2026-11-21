"""The user's word lists: entries that are always redacted, and entries no
finding may contain.

An entry matches as a whole word or phrase, in any casing; whitespace inside a
phrase matches any whitespace, line breaks included, so a phrase wrapped across
two lines still matches both.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from backend.document import Document
from backend.ocr_variants import VariantIndex
from backend.pii import USER_LIST, PiiLabel, Span
from backend.textnorm import fold

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
    """``redact``: always blacken the entry. ``keep``: drop every other finding
    whose text contains the entry."""

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

    def vetoes(self, span: Span) -> bool:
        """Whether ``span``'s text contains a ``keep`` entry or an OCR variant of one."""
        if self._keep_re is None:
            return False
        if self._keep_re.search(span.text) is not None:
            return True
        return next(self._keep_variants.find(span.text, _variant_ok), None) is not None


EMPTY = WordLists()
