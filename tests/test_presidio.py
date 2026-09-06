"""The guards that exist because of *this* classifier, not because of PII.

Presidio's recognizers are regexes over the joined page text, and spaCy's NER
has its own habits. Everything pinned here is a patch on one of those two — the
kind of thing that must stay inside `backend/classifiers/presidio.py` so a
second classifier never inherits a fix for a problem it does not have.

Only pure functions are exercised; no model loads.
"""

from __future__ import annotations

from backend.classifiers.presidio import _drop_wrapped
from backend.pii import PiiLabel, Span


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
