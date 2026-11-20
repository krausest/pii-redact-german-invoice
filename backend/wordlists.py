"""The user's word lists: entries that always redact their line, and entries no
finding may contain.

An entry matches as a whole word or phrase, in any casing; whitespace inside a
phrase matches any whitespace, line breaks included, so a phrase wrapped across
two lines still matches both.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from backend.document import Document
from backend.pii import USER_LIST, PiiLabel, Span


def _pattern(entries: tuple[str, ...]) -> re.Pattern[str] | None:
    """One alternation over ``entries``, longest first; ``None`` when empty."""
    words = sorted({e.strip() for e in entries if e.strip()}, key=len, reverse=True)
    if not words:
        return None
    alts = (r"\s+".join(map(re.escape, w.split())) for w in words)
    return re.compile(rf"(?<!\w)(?:{'|'.join(alts)})(?!\w)", re.IGNORECASE)


@dataclass(frozen=True)
class WordLists:
    """``redact``: always blacken the line. ``keep``: drop every other finding
    whose text contains the entry."""

    redact: tuple[str, ...] = ()
    keep: tuple[str, ...] = ()
    _redact_re: re.Pattern[str] | None = field(init=False, repr=False, compare=False)
    _keep_re: re.Pattern[str] | None = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_redact_re", _pattern(self.redact))
        object.__setattr__(self, "_keep_re", _pattern(self.keep))

    def redact_spans(self, doc: Document) -> list[Span]:
        """A span for every occurrence of a ``redact`` entry in ``doc``."""
        if self._redact_re is None:
            return []
        return [
            Span(PiiLabel.LISTED, m.start(), m.end(), m.group(), USER_LIST)
            for m in self._redact_re.finditer(doc.text)
        ]

    def vetoes(self, span: Span) -> bool:
        """Whether ``span``'s text contains a ``keep`` entry."""
        return self._keep_re is not None and self._keep_re.search(span.text) is not None


EMPTY = WordLists()
