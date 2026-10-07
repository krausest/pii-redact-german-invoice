"""Lines that must stay readable for the reimbursement, and what they reject.

A reviewer has to see when, by what kind of doctor, through which clearing
house and for what. Such a line is *kept*: the classifier's findings on it are
rejected, while a deterministic rule still redacts it.
"""

from __future__ import annotations

from enum import StrEnum

from backend.models import Line
from backend.pii import Span
from backend.rules.geometry import column_gap, left_neighbour
from backend.rules.labeled import LabeledValue, labeled_value_indices
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
KEEP_VALUES: tuple[LabeledValue, ...] = (
    LabeledValue(KeepReason.DATE, label=DATE_KEEP_LABEL, value=DATE_KEEP_VALUE, header=DATE_KEEP_CELL),
)

_MEMORY_SOURCES = ("name-memory", "birthdate-memory")


def keep_indices(lines: list[Line], table: set[int] | None = None) -> dict[int, KeepReason]:
    """Lines that must stay readable, mapped to why. The first reason wins."""
    table = table or set()
    keep: dict[int, KeepReason] = {i: KeepReason.ITEM_TABLE for i in sorted(table)}
    for i, reason in labeled_value_indices(lines, table, KEEP_VALUES).items():
        keep.setdefault(i, reason)
    gap = column_gap(lines)
    for i, ln in enumerate(lines):
        reason = _line_keep_reason(ln.text)
        if reason is None:
            continue
        keep.setdefault(i, reason)
        if reason is KeepReason.DIAG:
            for j in _diagnosis_block(lines, ln, table, gap):
                keep.setdefault(j, KeepReason.DIAG)
    return keep


def rejected(span: Span, keep: str | None) -> bool:
    """Whether ``span`` is dropped on a line kept for ``keep`` (``None``: not kept).

    A rule wins over every reason, except a legal form on the clearing house's
    name. The name memory yields only on specialty and clearing-house lines,
    which hold no name. The classifier yields on every kept line."""
    if keep is None:
        return False
    if span.source.startswith("rule ") or span.source == "labeled-value":
        return keep == KeepReason.PVS and span.source == "rule ORG_LEGAL"
    if span.source in _MEMORY_SOURCES:
        return keep in (KeepReason.FACH, KeepReason.PVS)
    return True


def is_specialty_line(text: str) -> bool:
    """Whether ``text`` names a specialty and nothing besides it."""
    if not SPECIALTY.search(text):
        return False
    return not SPECIALTY_GLUE.sub("", SPECIALTY.sub("", text))


def _line_keep_reason(text: str) -> KeepReason | None:
    if DATE_KEEP_CELL.search(text):
        return KeepReason.DATE
    if DIAG_LABEL.search(text):
        return KeepReason.DIAG
    if PVS_NAME.search(text):
        return KeepReason.PVS
    if is_specialty_line(text):
        return KeepReason.FACH
    return None


def _diagnosis_block(lines: list[Line], label: Line, table: set[int], gap: float) -> set[int]:
    """The diagnoses of ``label``: for a label alone in its cell, the cell beside
    it and that cell's wrapped lines; otherwise the block under the label."""
    block: set[int] = set()
    if DIAG_LABEL_CELL.search(label.text):
        for j, value in enumerate(lines):
            if j not in table and left_neighbour(lines, value) is label:
                block |= {j} | _block_below(lines, value, table, gap, wrapped=True)
    return block or _block_below(lines, label, table, gap)


def _block_below(
    lines: list[Line], start: Line, table: set[int], gap: float, wrapped: bool = False
) -> set[int]:
    """The lines running on below ``start`` with its left edge, each within
    ``gap`` of the one above. ``wrapped`` stops at a line with a cell left of it."""
    found: set[int] = set()
    bottom = start.bottom
    for i, ln in sorted(enumerate(lines), key=lambda pair: pair[1].top):
        if ln.top < bottom or abs(ln.left - start.left) > start.height or not ln.text.strip():
            continue
        if i in table or ln.top - bottom > gap or wrapped and left_neighbour(lines, ln):
            break
        found.add(i)
        bottom = ln.bottom
    return found
