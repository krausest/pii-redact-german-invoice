"""The guards that exist because of *this* classifier, not because of PII.

Presidio's recognizers are regexes over the joined page text, and spaCy's NER
has its own habits. Everything pinned here is a patch on one of those two — the
kind of thing that must stay inside `backend/classifiers/presidio.py` so a
second classifier never inherits a fix for a problem it does not have.

Only pure functions are exercised; no model loads.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import NamedTuple

from backend.classifiers.presidio import _drop_wrapped, _keep, _name_fragments, _name_token_count
from backend.pii import PiiLabel, Span
from backend.trace import Trace


class _Tok(NamedTuple):
    idx: int
    text: str
    pos_: str


def _tokens(text: str, tags: dict[str, str]) -> list[_Tok]:
    """Whitespace/comma tokens of ``text``, each tagged from ``tags`` (default NOUN)."""
    out, pos = [], 0
    for word in text.replace(",", " , ").split():
        idx = text.index(word, pos)
        out.append(_Tok(idx, word, tags.get(word, "PUNCT" if word == "," else "NOUN")))
        pos = idx + len(word)
    return out


def _span(label, text):
    return Span(label=label, start=0, end=len(text), text=text, source="presidio")


def test_a_pattern_match_that_only_exists_because_the_lines_were_joined_is_dropped():
    """A regex's whitespace class matches the joining newline, so a recognizer
    glues the tail of one line to the head of the next. Measured on the corpus:
    a bare invoice number plus the first word below it was reported as an
    address, blackening a line that has to stay readable."""
    assert _drop_wrapped([_span(PiiLabel.ADDRESS, "12345\nSeite")]) == []


def test_a_person_may_still_wrap():
    """The one label whose entity legitimately runs across a line break — and
    the reason the guard is by label rather than a blanket rule."""
    wrapped = _span(PiiLabel.PERSON, "Max\nMustermann")
    assert _drop_wrapped([wrapped]) == [wrapped]


def test_importing_a_classifier_module_does_not_load_its_runtime():
    """torch and spaCy are hundreds of megabytes and a second of import time
    each. Both classifier modules import their runtime inside the constructor, so
    a module can be read for its constants — and this guard is what keeps that
    true: adding gliner2 once made `import presidio_analyzer` pull torch in
    through presidio's own HuggingFace recognizers, and the fast suite went from
    1.6 s to 10 s before anyone noticed.

    Checked in a fresh interpreter, because the claim is about *importing*: a
    test session that has legitimately built a real classifier (the regression
    suite does) has torch in `sys.modules` for good reason, and asserting on this
    process would make that a failure."""
    import subprocess
    import sys

    probe = (
        "import backend.classifiers.guard_omni, backend.classifiers.presidio,"
        " backend.factory, backend.pipeline, sys;"
        " print(sorted(m for m in ('torch', 'gliner2', 'spacy') if m in sys.modules))"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "[]", out.stdout


# -- how many name tokens a PERSON span is worth ------------------------------ #
def _count(text: str, name: str, tags: dict[str, str]) -> int:
    start = text.index(name)
    return _name_token_count(text, _tokens(text, tags), start, start + len(name))


def test_two_proper_nouns_are_a_name_whatever_ocr_did_to_a_capital():
    assert _count("loanna Muster", "loanna Muster", {"loanna": "PROPN", "Muster": "PROPN"}) == 2


def test_a_capitalized_noun_across_a_comma_counts_as_the_other_half():
    """NER returns one half of "Muster,Andrea"; the tagger calls the surname a noun."""
    assert _count("Muster,Andrea", "Andrea", {"Andrea": "PROPN"}) == 2


def test_a_comma_ending_the_line_above_lends_nothing():
    assert _count("Summe,\nAndrea", "Andrea", {"Andrea": "PROPN"}) == 1


def test_known_noise_tokens_are_no_name():
    assert _count("Faktor Muster", "Faktor Muster", {"Faktor": "PROPN", "Muster": "PROPN"}) == 1


# -- a PERSON span across a line break ---------------------------------------- #
def test_a_name_split_by_a_wrap_keeps_both_halves():
    text = "Andrea\nMuster"
    tokens = _tokens(text, {"Andrea": "PROPN", "Muster": "PROPN"})
    assert _name_fragments(text, tokens, 0, len(text)) == [(0, 6), (7, 13)]


def test_a_name_running_on_into_the_next_row_is_cut_back():
    text = "Andrea Muster\nLeistungsdatum"
    tokens = _tokens(text, {"Andrea": "PROPN", "Muster": "PROPN"})
    assert _name_fragments(text, tokens, 0, len(text)) == [(0, 13)]


# -- results dropped before they become spans --------------------------------- #
def _result(entity_type: str, text: str, part: str) -> SimpleNamespace:
    start = text.index(part)
    return SimpleNamespace(entity_type=entity_type, start=start, end=start + len(part))


def test_a_date_or_a_bare_digit_run_is_no_phone_number():
    text = "09.07.2026 4711 0911 123456"
    results = [
        _result("PHONE_NUMBER", text, "09.07.2026"),
        _result("PHONE_NUMBER", text, "4711"),
        _result("PHONE_NUMBER", text, "0911 123456"),
    ]
    kept = _keep(results, text, _tokens(text, {}), Trace())
    assert [text[r.start : r.end] for r in kept] == ["0911 123456"]


def test_a_person_needs_two_name_tokens():
    text = "Orientierende Testuntersuchung"
    kept = _keep([_result("PERSON", text, text)], text, _tokens(text, {}), Trace())
    assert kept == []
