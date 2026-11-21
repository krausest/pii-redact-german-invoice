"""The regression suite: every frozen sample against its snapshot.

Opt in with ``uv run pytest --regression``. It is not in the fast loop because it
loads the classifier, and not in ``-m slow`` because it is nothing like as slow —
OCR and unwarp are frozen (see :mod:`backend.replay`), so a whole corpus replays
in seconds. The flag is orthogonal to ``-m``, which is why it is a flag: both
documented selectors would otherwise pull the suite into one of the two runs.

Samples are ``<stem>_ocr.txt`` files. The committed one is the synthetic example;
the private corpus keeps its own beside it in a git-ignored directory, because a
dump is the document's full text and so is PII.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.config import load_config
from backend.factory import build_classifier
from backend.replay import (
    OCR_SUFFIX,
    build_replay_pipeline,
    compare,
    expected_path,
    parse_expected,
    parse_ocr,
    replay,
)

pytestmark = pytest.mark.regression

ROOT = Path(__file__).resolve().parent.parent
COMMITTED = ROOT / "tests" / "regression"
CORPORA = (
    COMMITTED,
    # Real invoices whose text has been scrubbed to placeholders, so they may be
    # committed (`check --ignore-text` is what verifies a scrub changed no
    # verdict). Layout — the thing the geometry passes are tuned on — is real.
    ROOT / "tests" / "Arztrechnungen"
)


def _samples() -> list[Path]:
    return [p for d in CORPORA if d.is_dir() for p in sorted(d.glob(f"*{OCR_SUFFIX}"))]


@pytest.fixture(scope="module")
def pipeline():
    """One classifier for the whole module — the only model load in the suite."""
    config = load_config()
    return build_replay_pipeline(config, build_classifier(config))


@pytest.mark.parametrize("sample", _samples(), ids=lambda p: p.name.removesuffix(OCR_SUFFIX))
def test_sample_matches_its_snapshot(pipeline, sample):
    snapshot = expected_path(sample)
    if not snapshot.is_file():
        pytest.fail(
            f"no snapshot at {snapshot.name} — write one with:\n"
            f"  uv run python -m backend.replay check {sample} --update"
        )
    outcomes = replay(pipeline, parse_ocr(sample.read_text(encoding="utf-8")))
    failures = compare(outcomes, parse_expected(snapshot.read_text(encoding="utf-8")))
    assert not failures, "\n".join(["", *failures])


def test_the_committed_sample_is_collected():
    """Guards against a vacuous pass: a wrong glob or a moved directory would
    otherwise leave the suite green with nothing in it."""
    assert any(p.parent == COMMITTED for p in _samples())
