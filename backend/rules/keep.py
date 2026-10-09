"""Lines that must stay readable for the reimbursement.

A reviewer has to see when, by what kind of doctor, through which clearing
house and for what. Such a line is *kept*; which findings may still redact it
is up to each detector (:mod:`backend.detectors`).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from backend.models import Line
from backend.rules.geometry import below, column_gap, left_aligned, left_neighbour
from backend.rules.labeled import LabeledValue, labeled_values
from backend.rules.patterns import (
    DATE_KEEP_CELL,
    DATE_KEEP_LABEL,
    DATE_KEEP_VALUE,
    DIAG_LABEL,
    DIAG_LABEL_CELL,
    PVS_NAME,
    SPECIALTY,
    SPECIALTY_GLUE,
)


class KeepReason(StrEnum):
    """Why a line is kept, as the trace prints it: ``-> keep (DIAG)``."""

    ITEM_TABLE = "item table"
    DATE = "DATE"  # invoice or treatment date
    DIAG = "DIAG"  # diagnosis
    PVS = "PVS"  # the clearing house's name
    FACH = "FACH"  # the medical specialty


# "Rechnungsdatum:" | "12.03.2026"
KEEP_VALUES: list[LabeledValue] = [
    LabeledValue(KeepReason.DATE, label=DATE_KEEP_LABEL, value=DATE_KEEP_VALUE, header=DATE_KEEP_CELL),
]

def kept_lines(lines: list[Line], table: set[Line] | None = None) -> dict[Line, KeepReason]:
    """Lines that must stay readable, mapped to why. The first reason wins: the
    item table, then a labeled date, then :data:`KEEP_RULES` in order."""
    table = table or set()
    keep: dict[Line, KeepReason] = {ln: KeepReason.ITEM_TABLE for ln in lines if ln in table}
    for ln, reason in labeled_values(lines, table, KEEP_VALUES).items():
        keep.setdefault(ln, reason)
    own = {ln: rule for ln in lines if (rule := _first_rule(ln.text)) is not None}
    gap = column_gap(lines)
    for rule in KEEP_RULES:
        for ln in lines:
            if own.get(ln) is not rule:
                continue
            keep.setdefault(ln, rule.reason)
            if rule.block is not None:
                for j in rule.block(lines, ln, table, gap):
                    keep.setdefault(lines[j], rule.reason)
    return keep


def is_specialty_line(text: str) -> bool:
    """Whether ``text`` names a specialty and nothing besides it."""
    if not SPECIALTY.search(text):
        return False
    return not SPECIALTY_GLUE.sub("", SPECIALTY.sub("", text))


def _diagnosis_block(lines: list[Line], label: Line, table: set[Line], gap: float) -> set[int]:
    """The diagnoses of ``label``: for a label alone in its cell, the cell beside
    it and that cell's wrapped lines; otherwise the block under the label."""
    block: set[int] = set()
    if DIAG_LABEL_CELL.search(label.text):
        for j, value in enumerate(lines):
            if value not in table and left_neighbour(lines, value) is label:
                block |= {j} | _block_below(lines, value, table, gap, wrapped=True)
    return block or _block_below(lines, label, table, gap)


def _block_below(
    lines: list[Line], start: Line, table: set[Line], gap: float, wrapped: bool = False
) -> set[int]:
    """The lines running on below ``start`` with its left edge, each within
    ``gap`` of the one above. ``wrapped`` stops at a line with a cell left of it.
    Stricter than :func:`~backend.rules.geometry.below`: a line overlapping the
    one above is skipped, or the block swallows the line under a diagnosis."""
    found: set[int] = set()
    prev = start
    for i, ln in sorted(enumerate(lines), key=lambda pair: pair[1].top):
        if ln.top < prev.bottom or not left_aligned(start, ln, start.height) or not ln.text.strip():
            continue
        if ln in table or not below(prev, ln, gap) or wrapped and left_neighbour(lines, ln):
            break
        found.add(i)
        prev = ln
    return found


@dataclass(frozen=True)
class KeepRule:
    """A line ``matches`` and is kept for ``reason``; ``block`` adds the lines
    that belong to it (the diagnoses under their label)."""

    reason: KeepReason
    matches: Callable[[str], bool]
    block: Callable[[list[Line], Line, set[Line], float], set[int]] | None = None


# In order of priority; a line is judged by the first rule it matches.
KEEP_RULES: list[KeepRule] = [
    KeepRule(KeepReason.DATE, lambda text: bool(DATE_KEEP_CELL.search(text))),
    KeepRule(KeepReason.DIAG, lambda text: bool(DIAG_LABEL.search(text)), block=_diagnosis_block),
    KeepRule(KeepReason.PVS, lambda text: bool(PVS_NAME.search(text))),
    KeepRule(KeepReason.FACH, is_specialty_line),
]


def _first_rule(text: str) -> KeepRule | None:
    return next((rule for rule in KEEP_RULES if rule.matches(text)), None)
