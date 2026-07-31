"""Whole-region redaction: the two bands and the anchored sender column."""

from __future__ import annotations

from dataclasses import replace

from backend.models import Box, Line
from backend.regions import RegionParams, region_boxes

PAGE_W, PAGE_H = 1000, 1000

# header/footer bands are the top and bottom 10% of the page.
BANDS = RegionParams(
    header_frac=0.1,
    footer_frac=0.1,
    column_x_frac=1.0,  # sender column off
    column_y_frac=0.5,
    gap_factor=1.5,
)
# ...and vice versa: only the sender column, right half, upper half.
COLUMN = RegionParams(
    header_frac=0.0,
    footer_frac=0.0,
    column_x_frac=0.5,
    column_y_frac=0.5,
    gap_factor=1.5,
)


def _line(text, left=0, top=0, width=100, height=20):
    return Line(text=text, left=left, top=top, width=width, height=height)


def _boxes(lines, params=BANDS, width=PAGE_W, height=PAGE_H):
    return region_boxes(lines, width, height, params)


# -- bands ------------------------------------------------------------------- #
def test_band_covers_full_width_when_it_holds_text():
    assert _boxes([_line("Muster GmbH", top=20)]) == [Box(0, 0, PAGE_W, 100)]


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
    assert _boxes([_line("HRB 1234", top=950)]) == [Box(0, 900, PAGE_W, PAGE_H)]


def test_band_stretches_to_swallow_a_straddling_line():
    # Starts inside the 0-100 band and runs to 120, so its *centre* is outside it
    # — the case that made a real letterhead's last line come out sliced in half.
    assert _boxes([_line("Muster GmbH", top=95, height=25)]) == [Box(0, 0, PAGE_W, 120)]


def test_band_stretch_is_capped():
    # A line reaching y=180 must not drag the 100px band that far down the page:
    # the stretch stops at 1.5x the nominal band.
    assert _boxes([_line("Muster GmbH", top=10, height=170)]) == [Box(0, 0, PAGE_W, 150)]


def test_zero_fraction_disables_a_band():
    p = replace(BANDS, header_frac=0.0)
    lines = [_line("Muster GmbH", top=20), _line("HRB 1234", top=950)]
    assert region_boxes(lines, PAGE_W, PAGE_H, p) == [Box(0, 900, PAGE_W, PAGE_H)]


# -- sender column ----------------------------------------------------------- #
def test_sender_block_is_grown_around_its_anchor():
    lines = [
        _line("Behandlung durch:", left=600, top=200),
        _line("Hautarzt-Allergologie", left=600, top=225, width=200),
        _line("www.dr-muster.de", left=600, top=250),
    ]
    # bounding box of the three lines, none of which the per-line rules would
    # have covered in full.
    assert _boxes(lines, COLUMN) == [Box(600, 200, 800, 270)]


def test_unanchored_block_below_the_sender_survives():
    # The "Bitte bei Zahlung angeben" table sits in the same column but is
    # separated by a wide gap and holds no sender evidence.
    lines = [
        _line("Praxis Dr. Muster", left=600, top=200),
        _line("Rg.-Nr.: 000111/034567", left=600, top=400),
        _line("faelliger Betrag: 150,68 EUR", left=600, top=425),
    ]
    assert _boxes(lines, COLUMN) == [Box(600, 200, 700, 220)]


def test_page_number_line_without_an_anchor_is_left_alone():
    assert _boxes([_line("Seite 1 von 2", left=700, top=150)], COLUMN) == []


def test_left_column_is_never_a_sender_block():
    # Same anchor text, but in the recipient column — that is the patient's
    # address, which the per-line rules already handle.
    assert _boxes([_line("Musterstrasse 23", left=100, top=200)], COLUMN) == []


def test_sender_column_ignores_the_lower_page():
    assert _boxes([_line("Praxis Dr. Muster", left=600, top=800)], COLUMN) == []


def test_table_row_ends_the_sender_column():
    # The gap here (5px) is smaller than the block's own internal gaps, so only
    # the two-cell row structure distinguishes the table from the letterhead.
    lines = [
        _line("Praxis Dr. Muster", left=600, top=200, height=20),
        _line("www.dr-muster.de", left=600, top=235, height=20),
        _line("Rg.-Nr.:", left=600, top=260, width=60, height=20),
        _line("faelliger Betrag:", left=700, top=261, width=90, height=20),
    ]
    assert _boxes(lines, COLUMN) == [Box(600, 200, 700, 255)]


def test_stacked_lines_with_overlapping_boxes_are_not_a_table_row():
    # OCR line boxes routinely overlap by a pixel or two; that is a stack, not a
    # row, because the two also overlap horizontally.
    lines = [
        _line("Dr. med.", left=600, top=200, width=60, height=20),
        _line("Peter Muster", left=599, top=219, width=110, height=20),
        _line("www.dr-muster.de", left=600, top=241, width=110, height=20),
    ]
    assert _boxes(lines, COLUMN) == [Box(599, 200, 710, 261)]


def test_padding_expands_the_sender_box_only():
    p = replace(COLUMN, padding=3)
    lines = [_line("Praxis Dr. Muster", left=600, top=200)]
    assert region_boxes(lines, PAGE_W, PAGE_H, p) == [Box(597, 197, 703, 223)]
