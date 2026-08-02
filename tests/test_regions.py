"""Whole-region redaction: the two bands and the anchored sender column."""

from __future__ import annotations

from dataclasses import replace

from backend.models import Box, Line
from backend.regions import RegionParams, region_boxes  # noqa: F401

PAGE_W, PAGE_H = 1000, 1000

# band lines are looked for in the top and bottom 10% of the page; the band is then
# as tall as the lines found there.
BANDS = RegionParams(
    header_frac=0.1,
    footer_frac=0.1,
    column_x_frac=1.0,  # sender column off
    column_y_frac=0.5,
    vgap_factor=0.5,
    align_factor=0.4,
)
# ...and vice versa: only the sender column, right half, upper half.
COLUMN = RegionParams(
    header_frac=0.0,
    footer_frac=0.0,
    column_x_frac=0.5,
    column_y_frac=0.5,
    vgap_factor=0.5,
    align_factor=0.4,
)


def _line(text, left=0, top=0, width=100, height=20):
    return Line(text=text, left=left, top=top, width=width, height=height)


def _boxes(lines, params=BANDS, width=PAGE_W, height=PAGE_H):
    return region_boxes(lines, width, height, params)


# -- bands ------------------------------------------------------------------- #
def test_band_spans_the_page_width_but_only_its_content_height():
    # Full width, because a logo sits beside the text and OCR never reports it.
    # Only down to y=40 though, not to the 100px window edge: padding the strip
    # out to the fraction would blacken whitespace and nothing else.
    assert _boxes([_line("Muster GmbH", top=20)]) == [Box(0, 0, PAGE_W, 40)]


def test_bands_skipped_on_a_page_with_no_header_or_footer_text():
    # A continuation page: text only in the middle. A blanket stripe here would
    # be pure damage, so nothing is emitted.
    assert _boxes([_line("Begruendung zu 605", top=500)]) == []


def test_band_needs_a_sender_not_merely_text():
    # A continuation page whose item table starts at the top of the sheet and
    # whose totals sit in the bottom tenth. Both bands hold text, neither names a
    # sender, so blackening either would destroy the invoice and remove nothing.
    lines = [
        _line("Seite 2 zu Rechnung Nr. NEC-11-1111", top=73),
        _line("Beh.-Dat. Geb.Nr. Beschreibung Faktor EUR", top=101),
        _line("ENDBETRAG", top=910),
        _line("Aerztliche Leistung: 580,67 / Technische Leistung: 0,00", top=947),
    ]
    assert _boxes(lines) == []


def test_blank_lines_do_not_trigger_a_band():
    assert _boxes([_line("   ", top=20)]) == []


def test_footer_band_reaches_the_bottom_edge():
    # Down to the paper edge always; up only as far as the imprint reaches.
    assert _boxes([_line("HRB 1234", top=950)]) == [Box(0, 950, PAGE_W, PAGE_H)]


def test_band_stretches_to_swallow_a_straddling_line():
    # Starts inside the 0-100 band and runs to 120, so its *centre* is outside it
    # — the case that made a real letterhead's last line come out sliced in half.
    assert _boxes([_line("Muster GmbH", top=95, height=25)]) == [Box(0, 0, PAGE_W, 120)]


def test_band_height_is_capped():
    # Nothing else bounds the height: one line inside the window sets it. A merged
    # OCR box reaching y=180 must not drag the band that far down, so it is cut at
    # 1.5x the window even though that leaves the box half-covered.
    assert _boxes([_line("Muster GmbH", top=10, height=170)]) == [Box(0, 0, PAGE_W, 150)]


def test_footer_band_height_is_capped():
    assert _boxes([_line("HRB 1234", top=700, height=250)]) == [Box(0, 850, PAGE_W, PAGE_H)]


def test_zero_fraction_disables_a_band():
    p = replace(BANDS, header_frac=0.0)
    lines = [_line("Muster GmbH", top=20), _line("HRB 1234", top=950)]
    assert _boxes(lines, p) == [Box(0, 950, PAGE_W, PAGE_H)]


# -- sender column ----------------------------------------------------------- #
# Default line height is 20, so with the shipped factors the merge thresholds are
# a 10px vertical gap and an 8px edge offset.
def test_sender_block_is_one_block():
    lines = [
        _line("Behandlung durch:", left=600, top=200),
        _line("Hautarzt-Allergologie", left=600, top=222, width=200),
        _line("www.dr-muster.de", left=600, top=244),
    ]
    assert _boxes(lines, COLUMN) == [Box(600, 200, 800, 264)]


# The two cases below are why the merge test is an AND: each is cut by exactly one
# of its halves, and both patterns occur in the sample scans.
def test_aligned_but_too_far_does_not_join():
    # Perfectly aligned (dx 0) but 15px apart. Only the gap separates these.
    lines = [
        _line("Praxis Dr. Muster", left=600, top=200),
        _line("Rg.-Nr.: 000111", left=600, top=235),
    ]
    assert _boxes(lines, COLUMN) == [Box(600, 200, 700, 220)]


def test_touching_but_misaligned_does_not_join():
    # Touching (gap 0) but in a different column. Only alignment separates these.
    lines = [
        _line("Praxis Dr. Muster", left=600, top=200),
        _line("Rg.-Nr.: 000111", left=700, top=220),
    ]
    assert _boxes(lines, COLUMN) == [Box(600, 200, 700, 220)]


def test_right_aligned_pair_does_not_merge():
    # Right edges identical, lefts 40px apart. Alignment is left-edge only: a
    # right-edge arm was measured across the sample scans, changed no box on any
    # page, and would have linked the right-aligned numeric columns of a table.
    lines = [
        _line("Praxis Dr. Muster", left=600, top=200, width=100),
        _line("Musterhausen", left=640, top=222, width=60),
    ]
    assert _boxes(lines, COLUMN) == [Box(600, 200, 700, 220)]


def test_overlapping_boxes_count_as_touching():
    # OCR line boxes routinely overlap; a negative gap is the strongest possible
    # evidence of one block, not a reason to split.
    lines = [
        _line("Praxis Dr. Muster", left=600, top=200),
        _line("Musterhausen", left=599, top=215),
    ]
    assert _boxes(lines, COLUMN) == [Box(599, 200, 700, 235)]


def test_line_joins_via_any_member_not_just_the_nearest():
    # The third line is 55px below the second — far too far to join it — but only
    # 5px below the tall first line, so it joins the block through that one. Growth
    # tests every member, which is what makes the block independent of any order.
    lines = [
        _line("Praxis Dr. Muster", left=600, top=200, height=60),
        _line("Musterhausen", left=600, top=210),
        _line("www.dr-muster.de", left=600, top=265),
    ]
    assert _boxes(lines, COLUMN) == [Box(600, 200, 700, 285)]


def test_seed_must_be_in_the_right_column():
    # Same anchor text, but in the recipient column — that is the patient's
    # address, which the per-line rules already handle.
    assert _boxes([_line("Musterstrasse 23", left=100, top=200)], COLUMN) == []


def test_seed_must_be_in_the_upper_page():
    assert _boxes([_line("Praxis Dr. Muster", left=600, top=800)], COLUMN) == []


def test_block_may_grow_past_the_seed_window():
    # column_y_frac bounds seeding only. A block seeded just above the line is
    # followed wherever it goes — deliberately, so a sender block running into the
    # item table is visible rather than silently clipped.
    lines = [
        _line("Praxis Dr. Muster", left=600, top=480),
        _line("Musterhausen", left=600, top=505),
    ]
    assert _boxes(lines, COLUMN) == [Box(600, 480, 700, 525)]


def test_block_without_an_anchor_is_left_alone():
    # A bare phone number matches no anchor rule; the per-line pass has already
    # blacked it, so the region pass adds nothing by covering it again.
    lines = [
        _line("04131 2637-510", left=600, top=200),
        _line("Musterhausen", left=600, top=222),
    ]
    assert _boxes(lines, COLUMN) == []


def test_page_number_line_without_an_anchor_is_left_alone():
    assert _boxes([_line("Seite 1 von 2", left=700, top=150)], COLUMN) == []


def test_interleaved_other_column_does_not_split_the_block():
    # The recipient address sorts *between* the two sender lines by `top`. Anything
    # that walks the page top-to-bottom breaks the block here and leaves a hole in
    # the middle of it; a component search never sees an order.
    lines = [
        _line("Praxis Dr. Muster", left=600, top=200),
        _line("Musterstrasse 23", left=100, top=210),  # recipient column
        _line("www.dr-muster.de", left=600, top=222),
    ]
    assert _boxes(lines, COLUMN) == [Box(600, 200, 700, 242)]


def test_block_grows_both_ways_from_its_anchor():
    lines = [
        _line("Musterhausen", left=600, top=178),
        _line("Praxis Dr. Muster", left=600, top=200),  # the only anchor
        _line("Musterstadt", left=600, top=222),
    ]
    assert _boxes(lines, COLUMN) == [Box(600, 178, 700, 242)]


def test_several_anchors_in_one_block_yield_one_box():
    # The real sender block holds half a dozen anchors. Growing greedily from each
    # would emit half a dozen identical boxes; partitioning once emits one.
    lines = [
        _line("Praxis Dr. Muster", left=600, top=200),
        _line("www.dr-muster.de", left=600, top=222),
        _line("Musterstrasse 23", left=600, top=244),
    ]
    assert _boxes(lines, COLUMN) == [Box(600, 200, 700, 264)]


def test_padding_expands_the_sender_box_only():
    p = replace(COLUMN, padding=3)
    lines = [_line("Praxis Dr. Muster", left=600, top=200)]
    assert _boxes(lines, p) == [Box(597, 197, 703, 223)]
