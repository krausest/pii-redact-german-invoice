"""What stays readable for the reimbursement (``backend.rules.keep``)."""

from __future__ import annotations

import pytest

from backend.rules.keep import is_specialty_line, kept_lines
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


# -- which reason wins --------------------------------------------------------- #
def _diagnoses_over(line_text):
    """A diagnosis label with a block under it whose second line is ``line_text``."""
    return [
        cell("Diagnosen:", 10, 120),
        cell("1. Akute Bronchitis", 10, 142, width=250),
        cell(line_text, 10, 164, width=250),
    ]


def test_a_line_keeps_its_own_reason_before_a_block_claims_it():
    lines = _diagnoses_over("Datum")
    assert kept_lines(lines)[lines[2]] == "DATE"


def test_a_diagnosis_block_outranks_a_later_rule():
    lines = _diagnoses_over("Muster Verrechnungsstelle")
    assert kept_lines(lines)[lines[2]] == "DIAG"


def test_the_item_table_outranks_every_rule():
    lines = _diagnoses_over("2. Lumbago")
    assert kept_lines(lines, table={lines[0]})[lines[0]] == "item table"
