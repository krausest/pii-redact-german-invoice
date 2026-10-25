"""The result of one page (``backend.verdict.PageVerdict``)."""

from __future__ import annotations

from backend.layout import RegionBox, SignatureBox
from backend.models import Box
from backend.verdict import PageVerdict
from tests.conftest import cell


def test_the_notes_come_last_so_no_box_covers_them():
    line = cell("geb. 01.02.1980", 10, 10)
    line_box, region_box, signature_box = Box(0, 0, 1, 1), Box(0, 0, 2, 2), Box(0, 0, 3, 3)
    note = Box(0, 0, 4, 4, text="1980")
    verdict = PageVerdict(
        lines=[line],
        regions=[],
        reading=[line],
        found={},
        keep={},
        dropped={},
        line_boxes=[line_box],
        notes={line: note},
        region_boxes=[RegionBox(0, region_box, "footer")],
        signatures=[SignatureBox(line, signature_box)],
    )
    assert verdict.boxes == [line_box, region_box, signature_box, note]
