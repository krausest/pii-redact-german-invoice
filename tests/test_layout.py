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
    draw_layout_debug,
    region_boxes,
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
        _region(10, 10, 180, 90, label="header"),
        _region(10, 110, 180, 190, label="text"),
        _region(10, 210, 180, 290, label="table"),
    ]
    img = Image.new("RGB", (200, 300), color=(255, 255, 255))
    out = draw_layout_debug(img, [], [], regions)
    # Sampled bottom-right in each region: clear of both the outline and the
    # label chip, which sit at the top-left.
    r, g, b = out.getpixel((150, 75))  # header: blackened on sight -> reddish
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
        _region(0, 0, 100, 20, label="header"),
        _region(0, 30, 100, 50, label="image"),
        _region(0, 60, 100, 80, label="footer"),
        _region(0, 90, 100, 110, label="seal"),
    ]
    boxes = region_boxes([], regions, set(), padding=0)
    assert [b for b, _ in boxes] == [r.box for r in regions]
    assert [why for _, why in boxes] == ["header", "image", "footer", "seal"]


def test_region_boxes_pads_outwards():
    regions = [_region(10, 10, 90, 40, label="image")]
    (box, _), = region_boxes([], regions, set(), padding=3)
    assert box == Box(7, 7, 93, 43)


def test_region_boxes_needs_more_than_half_the_lines():
    lines = [_line("a", left=10, top=10), _line("b", left=10, top=40), _line("c", left=10, top=70)]
    regions = [_region(0, 0, 300, 100, label="text")]
    assert region_boxes(lines, regions, {0}, padding=0) == []
    assert region_boxes(lines, regions, {0, 1}, padding=0)[0][0] == Box(0, 0, 300, 100)


def test_region_boxes_is_a_strict_majority():
    lines = [_line("a", left=10, top=10), _line("b", left=10, top=40)]
    regions = [_region(0, 0, 300, 100, label="text")]
    assert region_boxes(lines, regions, {0}, padding=0) == []  # exactly half
    assert region_boxes(lines, regions, {0, 1}, padding=0)


def test_region_boxes_ignores_blank_lines_in_the_ratio():
    # OCR emits empty lines and nothing can ever redact one, so counting them
    # would drag a fully-redacted block below the threshold.
    lines = [_line("a", left=10, top=10), _line("   ", left=10, top=40), _line("", left=10, top=70)]
    regions = [_region(0, 0, 300, 100, label="text")]
    assert region_boxes(lines, regions, {0}, padding=0)


def test_region_boxes_skips_a_region_holding_no_lines():
    regions = [_region(0, 0, 300, 100, label="text")]
    assert region_boxes([], regions, set(), padding=0) == []


def test_region_boxes_treats_a_table_like_any_other_region():
    # A deliberate trade: the rule has no exceptions, and it costs the invoice
    # body on a page where most item rows carry a name.
    lines = [_line("a", left=10, top=10), _line("b", left=10, top=40)]
    regions = [_region(0, 0, 300, 100, label="table")]
    assert region_boxes(lines, regions, {0, 1}, padding=0)[0][0] == Box(0, 0, 300, 100)
