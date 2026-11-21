"""Words OCR may have misread: "llja" for "Ilja", "Loneburg" for "Lüneburg".

Two words are *close* when their look-alike skeletons are equal, or — from six
letters on — when one glyph-shape confusion separates them. An arbitrary vowel
is no confusion: "Master" is not "Muster".
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Sequence

from backend.textnorm import fold

# A word as OCR may print it: letters, digits and the glyphs read for an "I".
WORD = re.compile(r"[\w|!]+")
# Long enough for one edit to be a confusion rather than another word.
_MIN_EDIT_LENGTH = 6

_SEQUENCES = (("rn", "m"), ("vv", "w"), ("cl", "d"))
_CLASSES = str.maketrans({
    "i": "l", "1": "l", "|": "l", "!": "l",
    "0": "o", "5": "s",
    "ä": "a", "ö": "o", "ü": "u",
})
# One substitution OCR makes between glyphs of similar shape (skeleton letters).
_CHEAP_SUBSTITUTIONS = frozenset(
    frozenset(p) for p in ("uo", "nu", "ec", "hb", "ft", "mn")
)
# A glyph OCR drops or invents.
_CHEAP_INDELS = frozenset("l.")


def skeleton(word: str) -> str:
    """``word`` with diacritics and umlauts folded, case-folded, look-alike
    glyphs collapsed."""
    s = fold(word).casefold()
    for seq, repl in _SEQUENCES:
        s = s.replace(seq, repl)
    return s.translate(_CLASSES)


def _one_cheap_edit(a: str, b: str) -> bool:
    if len(a) == len(b):
        diff = [(x, y) for x, y in zip(a, b) if x != y]
        return len(diff) == 1 and frozenset(diff[0]) in _CHEAP_SUBSTITUTIONS
    if abs(len(a) - len(b)) != 1:
        return False
    short, long_ = sorted((a, b), key=len)
    for i, ch in enumerate(long_):
        if long_[:i] + long_[i + 1 :] == short:
            return ch in _CHEAP_INDELS
    return False


def close(a: str, b: str) -> bool:
    """Whether OCR may have printed ``b`` for ``a`` (or the other way round)."""
    sa, sb = skeleton(a), skeleton(b)
    if sa == sb:
        return True
    return min(len(a), len(b)) >= _MIN_EDIT_LENGTH and _one_cheap_edit(sa, sb)


class VariantIndex:
    """Finds OCR variants of ``targets`` (words or phrases) in a text."""

    def __init__(self, targets: Sequence[str]) -> None:
        self._targets = [(t, WORD.findall(t)) for t in targets if WORD.findall(t)]

    def find(
        self, text: str, accept: Callable[[str], bool] = lambda _w: True
    ) -> Iterator[tuple[int, int, str]]:
        """``(start, end, target)`` for every run of words in ``text`` close to a
        target, word by word, that is not the target itself. ``accept`` vets
        each word of the run."""
        words = list(WORD.finditer(text))
        for target, parts in self._targets:
            n = len(parts)
            for i in range(len(words) - n + 1):
                run = words[i : i + n]
                got = [m.group() for m in run]
                if [g.casefold() for g in got] == [p.casefold() for p in parts]:
                    continue  # an exact match is the exact matcher's job
                if all(close(p, g) and accept(g) for p, g in zip(parts, got)):
                    yield run[0].start(), run[-1].end(), target
