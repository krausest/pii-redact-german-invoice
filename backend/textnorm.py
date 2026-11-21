"""Diacritic folding right after OCR: one spelling for every later stage.

OCR invents accents ("Símone" for "Simone") and real names carry them ("René").
Folding a Latin letter with a diacritic to its base letter, upper and lower case,
lets rules, classifier, name memory and word lists see one spelling. German
umlauts and ß are kept: the rules and the German model depend on them.

One character in, one out, so offsets into the text keep pointing at the same
glyphs on the page.
"""

from __future__ import annotations

import unicodedata

_KEEP = frozenset("äöüÄÖÜß")
# Letters without a decomposition.
_EXPLICIT = {"Ø": "O", "ø": "o", "Ł": "L", "ł": "l", "Đ": "D", "đ": "d", "ı": "i"}


def _base(ch: str) -> str:
    if ch in _KEEP or ch.isascii():
        return ch
    if ch in _EXPLICIT:
        return _EXPLICIT[ch]
    parts = unicodedata.normalize("NFD", ch)
    if len(parts) > 1 and parts[0].isascii() and parts[0].isalpha():
        if all(unicodedata.combining(c) for c in parts[1:]):
            return parts[0]
    return ch


def fold(text: str) -> str:
    """``text`` (NFC) with every diacritic but the German ones removed."""
    return "".join(_base(ch) for ch in unicodedata.normalize("NFC", text))
