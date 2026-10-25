"""What each detector may still redact on a kept line (``backend.detectors``)."""

from __future__ import annotations

import pytest

from backend.detectors import MemoryDetector, ModelDetector, RuleDetector
from backend.harvest import Memory
from backend.pii import PiiLabel, Span

RULES, MODEL, MEMORY = RuleDetector(), ModelDetector(classifier=None), MemoryDetector(Memory())


@pytest.mark.parametrize(
    "detector,source,keep,admitted",
    [
        (RULES, "rule DE_STREET", None, True),
        (MODEL, "presidio", None, True),
        # a deterministic rule wins over every keep reason ...
        (RULES, "rule DE_STREET", "item table", True),
        (RULES, "labeled-value", "DATE", True),
        (RULES, "rule ORG_LEGAL", "DIAG", True),
        # ... except a legal form on the clearing house's name
        (RULES, "rule ORG_LEGAL", "PVS", False),
        # the model survives no kept line
        (MODEL, "presidio", "item table", False),
        (MODEL, "guard-omni", "DIAG", False),
        # the memory reaches the table, a date or a diagnosis, not FACH or PVS
        (MEMORY, "name-memory", "item table", True),
        (MEMORY, "birthdate-memory", "DIAG", True),
        (MEMORY, "name-memory", "FACH", False),
        (MEMORY, "birthdate-memory", "PVS", False),
    ],
)
def test_what_a_kept_line_admits(detector, source, keep, admitted):
    span = Span(PiiLabel.PERSON, 0, 13, "Andrea Muster", source)
    assert detector.admits(span, keep) is admitted
