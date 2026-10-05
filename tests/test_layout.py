"""backend.layout: assigning OCR lines to detected layout regions and turning
those regions into whole-region boxes. Pure functions only — the
PaddleLayoutDetector wrapper itself is covered by tests/test_slow.py
against the real model."""

from __future__ import annotations

from PIL import Image

from backend.models import Box, Line
from backend.layout import (
    _DEBUG_LINE_WIDTH,
    _DEBUG_PALETTE,
    LayoutRegion,
    assign_lines,
    document_order,
    draw_layout_debug,
    region_boxes,
    region_parents,
    signature_boxes,
    subtract,
)


def _line(text, left, top, width=100, height=20):
    return Line(text=text, left=left, top=top, width=width, height=height)


def _region(x0, y0, x1, y1, label="text", score=0.9):
    return LayoutRegion(label=label, score=score, box=Box(x0, y0, x1, y1))


def test_assign_lines_groups_by_containing_region():
    lines = [
        _line("in A", left=10, top=10),
        _line("in B", left=10, top=210),
        _line("also in A", left=10, top=40),
    ]
    regions = [_region(0, 0, 300, 100), _region(0, 200, 300, 300)]
    assert assign_lines(lines, regions) == [[0, 2], [1]]


def test_assign_lines_uses_the_line_center_not_its_corner():
    # Line box starts outside the region but its center (60, 110) is inside.
    lines = [_line("straddler", left=10, top=100, width=100, height=20)]
    regions = [_region(50, 0, 300, 300)]
    assert assign_lines(lines, regions) == [[0]]


def test_assign_lines_smallest_region_wins_on_nesting():
    # A tight table region inside a page-wide text region: the tighter box is
    # the more specific claim.
    lines = [_line("cell", left=100, top=100)]
    regions = [
        _region(0, 0, 1000, 1000, label="text"),
        _region(50, 50, 300, 200, label="table"),
    ]
    assert assign_lines(lines, regions) == [[0]]
    # ...and the group belongs to the table region: with another line only in
    # the big region, two groups come back in region order.
    lines.append(_line("outside table", left=500, top=500))
    assert assign_lines(lines, regions) == [[1], [0]]


def test_assign_lines_uncontained_line_becomes_a_singleton():
    lines = [
        _line("claimed", left=10, top=10),
        _line("orphan", left=10, top=500),
    ]
    regions = [_region(0, 0, 300, 100)]
    assert assign_lines(lines, regions) == [[0], [1]]


def test_assign_lines_is_a_full_partition():
    lines = [_line(f"l{i}", left=10, top=10 + 60 * i) for i in range(6)]
    regions = [_region(0, 0, 300, 100), _region(0, 150, 300, 250)]
    groups = assign_lines(lines, regions)
    seen = sorted(i for g in groups for i in g)
    assert seen == list(range(len(lines)))


def test_assign_lines_with_no_regions_yields_only_singletons():
    lines = [_line("a", left=10, top=10), _line("b", left=10, top=50)]
    assert assign_lines(lines, []) == [[0], [1]]


def test_draw_layout_debug_returns_annotated_copy_without_mutating_source():
    lines = [_line("a", left=10, top=10)]
    regions = [_region(5, 5, 200, 50)]
    img = Image.new("RGB", (300, 300), color=(255, 255, 255))
    out = draw_layout_debug(img, lines, assign_lines(lines, regions), regions)
    assert out.size == img.size
    assert img.getpixel((5, 5)) == (255, 255, 255)  # source untouched
    assert out.getpixel((5, 5)) == _DEBUG_PALETTE[0]  # region outline drawn


def test_draw_layout_debug_tints_only_the_always_blackened_labels():
    regions = [
        _region(10, 10, 180, 90, label="footer"),
        _region(10, 110, 180, 190, label="text"),
        _region(10, 210, 180, 290, label="table"),
    ]
    img = Image.new("RGB", (200, 300), color=(255, 255, 255))
    out = draw_layout_debug(img, [], [], regions)
    # Sampled bottom-right in each region: clear of both the outline and the
    # label chip, which sit at the top-left.
    r, g, b = out.getpixel((150, 75))  # footer: blackened on sight -> reddish
    assert (r, g, b) != (255, 255, 255)
    assert r > g == b
    # `text` and `table` are decided by the majority rule, which this view does
    # not compute, so neither is tinted.
    assert out.getpixel((150, 175)) == (255, 255, 255)
    assert out.getpixel((150, 275)) == (255, 255, 255)


def test_draw_layout_debug_names_each_region_in_a_readable_chip():
    # The label is drawn as glyphs knocked out of a filled chip, sized from the
    # text: a longer label makes a wider chip. Pinned because the label used to
    # be bare ~11px text that no page-sized render could resolve.
    img = Image.new("RGB", (600, 600), color=(255, 255, 255))

    fill = _DEBUG_PALETTE[0]

    def chip(label):
        out = draw_layout_debug(img, [], [], [_region(10, 10, 550, 300, label=label)])
        top = 10 + _DEBUG_LINE_WIDTH  # the chip starts just inside the outline
        # The chip's padding rows/columns are solid fill, so its extent is the
        # run of fill along the row under its top edge and down its right edge.
        width = next(i for i in range(540) if out.getpixel((10 + i, top + 1)) != fill)
        height = next(j for j in range(560) if out.getpixel((10 + width - 2, top + j)) != fill)
        pixels = out.crop((10, top, 10 + width, top + height)).getdata()
        # Glyphs are knocked out of the chip in white: something must be lighter
        # than the fill.
        return width, max(sum(px) for px in pixels) > sum(fill) + 60

    long_width, long_is_lit = chip("paragraph_title")
    short_width, short_is_lit = chip("text")
    assert long_is_lit and short_is_lit
    assert long_width > short_width


def test_draw_layout_debug_cycles_colors_past_the_palette_length():
    n = len(_DEBUG_PALETTE) + 1
    regions = [_region(10, 10 + i * 100, 200, 60 + i * 100) for i in range(n)]
    img = Image.new("RGB", (300, 10 + n * 100 + 50), color=(255, 255, 255))
    out = draw_layout_debug(img, [], [], regions)
    assert out.getpixel((10, 10)) == _DEBUG_PALETTE[0]
    assert out.getpixel((10, 10 + 100)) == _DEBUG_PALETTE[1]
    assert out.getpixel((10, 10 + (n - 1) * 100)) == _DEBUG_PALETTE[0]  # wrapped


# -- region_boxes: what actually gets blackened --------------------------- #
def test_region_boxes_always_blackens_the_graphic_and_furniture_types():
    # These four are covered by their type alone. For `image` and `seal` that is
    # the only evidence available: a logo and a stamp carry no OCR line, so the
    # majority rule could never reach them.
    regions = [
        _region(0, 30, 100, 50, label="image"),
        _region(0, 60, 100, 80, label="footer"),
        _region(0, 90, 100, 110, label="seal"),
    ]
    boxes = region_boxes([], regions, set(), padding=0, ratio=0.4)
    assert [b for _, b, _ in boxes] == [r.box for r in regions]
    assert [why for _, _, why in boxes] == ["image", "footer", "seal"]


def test_a_header_goes_by_the_ratio_rule():
    # The detector draws it around the whole letterhead, specialty included.
    lines = [_line("name", left=10, top=10), _line("practice", left=10, top=40)]
    regions = [_region(0, 0, 300, 100, label="header")]
    assert region_boxes(lines, regions, set(), padding=0, ratio=0.4) == []
    assert region_boxes(lines, regions, {0, 1}, padding=0, ratio=1.0) == []
    assert region_boxes(lines, regions, {0}, padding=0, ratio=0.4)


def test_region_boxes_pads_outwards():
    regions = [_region(10, 10, 90, 40, label="image")]
    (_, box, _), = region_boxes([], regions, set(), padding=3, ratio=0.4)
    assert box == Box(7, 7, 93, 43)


def test_region_boxes_needs_enough_of_the_lines():
    lines = [_line(c, left=10, top=10 + 30 * i) for i, c in enumerate("abc")]
    regions = [_region(0, 0, 300, 100, label="text")]
    assert region_boxes(lines, regions, {0}, padding=0, ratio=0.4) == []  # 1/3 is under the bar
    assert region_boxes(lines, regions, {0, 1}, padding=0, ratio=0.4)[0][1] == Box(0, 0, 300, 100)


def test_region_boxes_takes_a_two_line_block_on_one_hit():
    # The common sender-block shape: one line names the company and matches a
    # static rule, the tagline beside it matches nothing. At a strict majority
    # this survived, which is why the bar sits below a half.
    lines = [_line("a", left=10, top=10), _line("b", left=10, top=40)]
    regions = [_region(0, 0, 300, 100, label="text")]
    assert region_boxes(lines, regions, {0}, padding=0, ratio=0.4)[0][1] == Box(0, 0, 300, 100)


def test_region_boxes_treats_the_ratio_as_strict():
    # More than the ratio is needed: 2 of 5 is exactly 0.4 and stays, 3 of 5 goes.
    lines = [_line(str(i), left=10, top=10 + 30 * i) for i in range(5)]
    regions = [_region(0, 0, 300, 200, label="text")]
    assert region_boxes(lines, regions, {0, 1}, padding=0, ratio=0.4) == []
    assert region_boxes(lines, regions, {0, 1, 2}, padding=0, ratio=0.4)


def test_region_ratio_one_switches_the_ratio_rule_off():
    # Even a fully redacted block is not blackened whole; furniture still is.
    lines = [_line("a", left=10, top=10), _line("b", left=10, top=40)]
    regions = [_region(0, 0, 300, 100, label="text"), _region(0, 200, 300, 220, label="footer")]
    boxes = region_boxes(lines, regions, {0, 1}, padding=0, ratio=1.0)
    assert [why for _, _, why in boxes] == ["footer"]


def test_region_boxes_ignores_blank_lines_in_the_ratio():
    # OCR emits empty lines and nothing can ever redact one, so counting them
    # would drag a fully-redacted block below the threshold.
    lines = [_line("a", left=10, top=10), _line("   ", left=10, top=40), _line("", left=10, top=70)]
    regions = [_region(0, 0, 300, 100, label="text")]
    assert region_boxes(lines, regions, {0}, padding=0, ratio=0.4)


def test_region_boxes_skips_a_region_holding_no_lines():
    regions = [_region(0, 0, 300, 100, label="text")]
    assert region_boxes([], regions, set(), padding=0, ratio=0.4) == []


def test_region_boxes_treats_a_table_like_any_other_region():
    # The ratio rule has no exceptions; what saves the item rows on such a page
    # is that the pipeline passes them as `keep` (see the next tests).
    lines = [_line("a", left=10, top=10), _line("b", left=10, top=40)]
    regions = [_region(0, 0, 300, 100, label="table")]
    assert region_boxes(lines, regions, {0, 1}, padding=0, ratio=0.4)[0][1] == Box(0, 0, 300, 100)


def test_a_kept_line_is_cut_out_of_a_region_box():
    # A letterhead: the header goes black, the specialty line under the name
    # stays readable, with the header blackened around it.
    lines = [_line("name", left=10, top=10, width=100), _line("specialty", left=10, top=40, width=100)]
    regions = [_region(0, 0, 300, 100, label="header")]
    boxes = [b for _, b, _ in region_boxes(lines, regions, {0}, padding=0, ratio=0.4, keep={1})]
    assert boxes == [
        Box(0, 0, 300, 40),
        Box(0, 40, 10, 60),
        Box(110, 40, 300, 60),
        Box(0, 60, 300, 100),
    ]


def test_a_kept_line_that_was_redacted_is_not_cut_out():
    lines = [_line("a", left=10, top=10)]
    regions = [_region(0, 0, 300, 100, label="header")]
    assert region_boxes(lines, regions, {0}, padding=0, ratio=0.4, keep={0})[0][1] == Box(0, 0, 300, 100)


def test_a_stamp_is_never_cut_open():
    # A stamp prints the doctor's name around the specialty; a hole would show it.
    lines = [_line("specialty", left=10, top=10)]
    regions = [_region(0, 0, 300, 100, label="seal")]
    assert [b for _, b, _ in region_boxes(lines, regions, set(), padding=0, ratio=0.4, keep={0})] == [
        Box(0, 0, 300, 100)
    ]


def test_subtract_without_overlap_returns_the_box():
    assert subtract(Box(0, 0, 10, 10), [Box(20, 20, 30, 30)]) == [Box(0, 0, 10, 10)]


# -- the signature ------------------------------------------------------------ #
def test_the_signature_is_the_gap_between_the_greeting_and_the_typed_name():
    lines = [
        _line("Mit freundlichen Grüßen", left=100, top=500, width=200, height=20),
        _line("Dr. med. Andrea Muster", left=100, top=580, width=180, height=20),
    ]
    assert signature_boxes(lines, page_width=1000, padding=2) == [(0, Box(98, 520, 402, 580))]


def test_a_signature_without_a_typed_name_reaches_six_line_heights():
    lines = [_line("Hochachtungsvoll", left=100, top=500, width=200, height=20)]
    assert signature_boxes(lines, page_width=350, padding=0) == [(0, Box(100, 520, 350, 640))]


def test_no_signature_when_the_name_follows_directly():
    lines = [
        _line("Mit freundlichen Grüßen", left=100, top=500, width=200, height=20),
        _line("Ihre Praxis", left=100, top=522, width=100, height=20),
    ]
    assert signature_boxes(lines, page_width=1000, padding=0) == []


# -- reading order ---------------------------------------------------------- #
def test_a_table_row_reads_across_although_its_cells_are_offset():
    """The reason ordering is banded rather than a plain (top, left) sort: on a
    photographed page the cells of one row differ by a few pixels, and a plain
    sort would read the table column by column, pulling "Datum" away from the
    date beside it and gluing it to the row below."""
    lines = [
        _line("Datum", left=100, top=812),
        _line("Ziffer", left=300, top=814),
        _line("Betrag", left=500, top=811),
        _line("18.02.", left=100, top=846),
        _line("2", left=300, top=848),
        _line("3,15", left=500, top=845),
    ]
    regions = [_region(50, 800, 650, 880, label="table")]
    assert [lines[i].text for i in document_order(lines, regions)] == [
        "Datum", "Ziffer", "Betrag", "18.02.", "2", "3,15",
    ]


def test_two_columns_at_the_same_height_stay_whole():
    """The interleaving that made per-line classification necessary in the first
    place: a sender column and a recipient block at similar page height. Each
    must arrive as a run, not alternating line by line."""
    lines = [
        _line("Absender GmbH", left=600, top=100),
        _line("Musterweg 1", left=600, top=130),
        _line("Herrn", left=100, top=110),
        _line("Max Muster", left=100, top=140),
    ]
    regions = [_region(580, 90, 900, 190), _region(80, 100, 400, 200)]
    assert [lines[i].text for i in document_order(lines, regions)] == [
        "Herrn", "Max Muster", "Absender GmbH", "Musterweg 1",
    ]


def test_a_nested_block_stays_contiguous():
    """A table inside a text region enters as a whole at its own position,
    rather than interleaving its rows with the surrounding paragraph."""
    lines = [
        _line("Ueberschrift", left=100, top=10),
        _line("Datum", left=120, top=110),
        _line("Betrag", left=400, top=112),
        _line("Schluss", left=100, top=300),
    ]
    regions = [_region(50, 0, 600, 350, label="text"), _region(100, 100, 500, 140, label="table")]
    assert region_parents(regions) == [None, 0]
    assert [lines[i].text for i in document_order(lines, regions)] == [
        "Ueberschrift", "Datum", "Betrag", "Schluss",
    ]


def test_nesting_uses_mostly_contained_not_strict_containment():
    """Detector boxes overhang each other by a few pixels routinely; demanding
    true containment would make a nested table a sibling instead of a child."""
    outer = _region(0, 0, 1000, 1000, label="text")
    poking_out = _region(-20, 100, 400, 300, label="table")
    assert region_parents([outer, poking_out]) == [None, 0]


def test_every_line_survives_the_ordering():
    """A permutation, not a filter: a line no region claimed is ordered among
    the top-level items rather than dropped."""
    lines = [_line(f"l{n}", left=10 * n, top=37 * n) for n in range(12)]
    regions = [_region(0, 0, 100, 200), _region(0, 300, 200, 400)]
    assert sorted(document_order(lines, regions)) == list(range(12))
