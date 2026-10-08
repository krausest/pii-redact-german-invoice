"""What stays readable for the reimbursement (``backend.rules.keep``)."""

from __future__ import annotations

import pytest

from backend.pii import PiiLabel, Span
from backend.rules.keep import is_specialty_line, kept_lines, rejected
from tests.conftest import cell


@pytest.mark.parametrize(
    "text,kept",
    [
        ("Facharzt für Orthopädie", True),
        ("Hautarzt-Allergologie-Lasermedizin", True),
        ("Privatärztliche Praxis für", True),
        ("Chirotherapie – Sportmedizin", True),
        # The unknown word is the practice's name.
        ("Kieferorthopädie Muster", False),
        ("Dr. med. Andrea Muster, Fachärztin für Innere Medizin", False),
        ("Beratung", False),
    ],
)
def test_a_specialty_is_kept_only_on_its_own(text, kept):
    assert is_specialty_line(text) is kept


def test_keep_names_dates_diagnoses_and_the_clearing_house():
    lines = [
        cell("Muster VerrechnungsSysteme GmbH", 10, 10, width=300),
        cell("Rechnungsdatum:", 10, 60),
        cell("19.03.2026", 130, 60),
        cell("Diagnosen:", 10, 120),
        cell("1. Akute Bronchitis", 10, 142, width=250),
        cell("2. Lumbago", 12, 164, width=120),
    ]
    reasons = ["PVS", "DATE", "DATE", "DIAG", "DIAG", "DIAG"]
    assert kept_lines(lines) == dict(zip(lines, reasons))


def test_a_diagnosis_beside_its_label_cell_is_kept():
    lines = [
        cell("Diagnose:", 10, 120, width=80),
        cell("V.a. Akute Bronchitis, Verruca vulgaris", 100, 120, width=400),
        cell("Lumbago, Zephalgie", 103, 142, width=200),
        cell("Andrea Muster", 10, 164, width=150),
        cell("Musterstraße 1", 103, 250, width=150),
    ]
    assert kept_lines(lines) == dict.fromkeys(lines[:3], "DIAG")


def test_a_diagnosis_beside_its_label_ends_at_the_next_row_of_cells():
    lines = [
        cell("Diagnose:", 10, 120, width=80),
        cell("Lumbago", 100, 120, width=200),
        cell("Nr.", 10, 142, width=40),
        cell("Muster", 102, 142, width=100),
    ]
    assert kept_lines(lines) == dict.fromkeys(lines[:2], "DIAG")


def test_a_merged_diagnosis_label_keeps_nothing_beside_it():
    lines = [
        cell("Diagnose: Lumbago", 10, 120, width=150),
        cell("Andrea Muster", 300, 120, width=150),
    ]
    assert kept_lines(lines) == {lines[0]: "DIAG"}


def test_a_date_beside_a_name_is_not_kept_for_its_label():
    lines = [cell("Datum:", 10, 10), cell("Muster, Andrea 12.03.2026", 130, 10, width=200)]
    assert lines[1] not in kept_lines(lines)


# -- what a kept line rejects -------------------------------------------------- #
@pytest.mark.parametrize(
    "source,keep,dropped",
    [
        ("rule DE_STREET", None, False),
        ("presidio", None, False),
        # a deterministic rule wins over every keep reason ...
        ("rule DE_STREET", "item table", False),
        ("labeled-value", "DATE", False),
        ("rule ORG_LEGAL", "DIAG", False),
        # ... except a legal form on the clearing house's name
        ("rule ORG_LEGAL", "PVS", True),
        # the model survives no kept line
        ("presidio", "item table", True),
        ("guard-omni", "DIAG", True),
        # the memory reaches the table, a date or a diagnosis, not FACH or PVS
        ("name-memory", "item table", False),
        ("birthdate-memory", "DIAG", False),
        ("name-memory", "FACH", True),
        ("birthdate-memory", "PVS", True),
    ],
)
def test_what_a_kept_line_rejects(source, keep, dropped):
    span = Span(PiiLabel.PERSON, 0, 13, "Andrea Muster", source)
    assert rejected(span, keep) is dropped
