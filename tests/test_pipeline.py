"""What the pipeline promises when the pieces are composed.

Acceptance-level: every test here states a behaviour a user of the redactor
would notice, using stub OCR/classifier/layout so no model loads. The
line-by-line correctness of the rules lives in ``test_rules.py``, the reading
order in ``test_layout.py``, and the real documents in the corpus replay
(``pytest --regression``).
"""

from __future__ import annotations

from PIL import Image

from backend.layout import LayoutRegion
from backend.models import Box, Line
from backend.pii import PiiLabel, Span
from backend.pipeline import RedactionPipeline
from backend.trace import Trace
from tests.conftest import (
    RecordingUnwarper,
    StubClassifier,
    StubLayoutDetector,
    StubOCR,
)


def _pipeline(lines, pii_subs, **kwargs):
    return RedactionPipeline(ocr=StubOCR(lines), classifier=StubClassifier(pii_subs), **kwargs)


def _line(text, top, left=10, width=80, height=10):
    return Line(text=text, left=left, top=top, width=width, height=height)


def _region(label, x0, y0, x1, y1):
    return LayoutRegion(label=label, score=0.9, box=Box(x0, y0, x1, y1))


def _page():
    return Image.new("RGB", (300, 300))


class _AddressStub:
    """Reports one ADDRESS span over the given substring — the shape a span
    model produces for an address printed on two lines."""

    def __init__(self, sub):
        self._sub = sub

    def spans(self, text, trace):  # noqa: ARG002
        start = text.find(self._sub)
        if start < 0:
            return []
        return [Span(PiiLabel.ADDRESS, start, start + len(self._sub), self._sub, "stub", 0.9)]


# -- what the whole-document switch bought ---------------------------------- #
def test_a_name_broken_across_two_lines_blackens_both():
    """The reason the page is classified as one text at all.

    Per line, "Max" and "Mustermann" are each a single token that no classifier
    would call a person; joined, they are one entity, and *both* lines have to go
    black or half the name stays on the page."""
    lines = [_line("Max", top=10), _line("Mustermann", top=30), _line("Summe", top=200)]
    p = _pipeline(lines, ["Max\nMustermann"], padding=0)
    assert p.compute_boxes(_page()) == [Box(10, 10, 90, 20), Box(10, 30, 90, 40)]


def test_the_classifier_sees_the_page_in_reading_order_not_ocr_order():
    """Two blocks side by side must not be interleaved into one another.

    This is what raw OCR order does and what the layout hierarchy undoes: the
    stub only matches if the recipient's three lines arrive consecutively."""
    lines = [
        _line("Absender GmbH", top=100, left=200),
        _line("Herrn", top=105, left=10),
        _line("Musterweg 1", top=130, left=200),
        _line("Max Muster", top=135, left=10),
    ]
    regions = [_region("text", 190, 90, 290, 150), _region("text", 5, 95, 100, 150)]
    p = _pipeline(lines, ["Herrn\nMax Muster"], padding=0, layout=StubLayoutDetector(regions))
    boxes = p.compute_boxes(_page())
    assert Box(10, 105, 90, 115) in boxes and Box(10, 135, 90, 145) in boxes


def test_a_wrapped_span_from_the_classifier_reaches_every_line_it_crosses():
    """Not only a name: any label a classifier reports across a wrap.

    A guard that dropped every wrapped non-PERSON span used to sit here, in the
    shared path — it is a patch for presidio's regex recognizers matching the
    joining newline, and it now lives with them. A span model reports a two-line
    address as one entity, and both lines have to go."""
    lines = [_line("Musterweg 1", top=10), _line("12345 Musterhausen", top=30)]
    p = RedactionPipeline(
        ocr=StubOCR(lines),
        classifier=_AddressStub("Musterweg 1\n12345 Musterhausen"),
        padding=0,
    )
    assert p.compute_boxes(_page()) == [Box(10, 10, 90, 20), Box(10, 30, 90, 40)]


# -- the two span sources are not equal ------------------------------------- #
def test_a_deterministic_rule_redacts_without_the_classifier():
    lines = [_line("Musterstrasse 7", top=10)]
    assert _pipeline(lines, [], padding=0).compute_boxes(_page()) == [Box(10, 10, 90, 20)]


def test_the_item_table_gates_the_classifier_and_only_the_classifier():
    """An invoice body is a grid of two-word service texts, which in German read
    exactly like a forename/surname pair. So a model hit inside the table is
    ignored — while a rule that fires there is still evidence."""
    rows = [_line(f"Beratung {n}", top=100 + 20 * n) for n in range(4)]
    money = [_line("10,72", top=100 + 20 * n, left=200) for n in range(4)]
    lines = [_line("Cleed Agar", top=105), *rows, *money]
    boxes = _pipeline(lines, ["Cleed Agar"], padding=0).compute_boxes(_page())
    assert boxes == []  # the model's only hit sits in the table
    # ...but a street in the same rows is still redacted.
    lines[0] = _line("Musterstrasse 7", top=105)
    assert _pipeline(lines, [], padding=0).compute_boxes(_page()) == [Box(10, 105, 90, 115)]


# -- the name memory: pass one names a person, pass two finds them again ----- #
def test_the_name_memory_carries_a_surname_across_pages():
    """A name the rules labelled on page 1 is redacted bare on page 2, through
    the accumulator the caller threads between pages."""
    names: set[str] = set()
    p1 = _pipeline([_line("Patient Mustermann, Max", top=10)], [], padding=0)
    p1.compute_boxes(_page(), known_names=names)
    p2 = _pipeline([_line("Diagnose Mustermann", top=10)], [], padding=0)
    assert p2.compute_boxes(_page(), known_names=names) == [Box(10, 10, 90, 20)]


def test_a_name_only_the_model_found_still_redacts_its_bare_recurrence():
    """The reason the memory reads spans rather than patterns.

    No rule labels the first line — the model does. The second names the same
    person with nothing beside it: a lone token no classifier calls a person and
    no pattern describes. It is redacted because the page already said who that
    is."""
    lines = [_line("Anna Beispiel", top=10), _line("Betrifft: Beispiel", top=200)]
    boxes = _pipeline(lines, ["Anna Beispiel"], padding=0).compute_boxes(_page())
    assert boxes == [Box(10, 10, 90, 20), Box(10, 200, 90, 210)]


def test_the_memory_takes_the_model_span_not_the_line_around_it():
    """A letterhead line names the managing director *and* the practice. Taking
    the line whole made the company a remembered name, which then blackened body
    text and an invoice number elsewhere on the page — measured on the corpus.
    The span carries the name and stops."""
    lines = [
        _line("Musterlabor GmbH, Anna Beispiel", top=10),
        _line("Musterlabor rechnet quartalsweise ab", top=200),
    ]
    boxes = _pipeline(lines, ["Anna Beispiel"], padding=0).compute_boxes(_page())
    assert boxes == [Box(10, 10, 90, 20)]  # the second line stays readable


def test_a_model_hit_inside_the_item_table_never_feeds_the_memory():
    """The table gate would be worth little if the hit it drops came back as a
    memory: a two-word service text reads like a name, and remembering one would
    spread that single mistake over the whole document."""
    rows = [_line(f"Beratung {n}", top=100 + 20 * n) for n in range(4)]
    money = [_line("10,72", top=100 + 20 * n, left=200) for n in range(4)]
    lines = [*rows, *money, _line("Cleed Agar", top=100), _line("Cleed", top=250)]
    assert _pipeline(lines, ["Cleed Agar"], padding=0).compute_boxes(_page()) == []


# -- regions ---------------------------------------------------------------- #
def test_a_graphic_region_is_blackened_although_it_holds_no_text():
    """The only boxes not derived from an OCR line: a logo, a stamp, a payment
    QR code. Nothing text-based could ever reach them."""
    lines = [_line("Muster GmbH", top=200)]
    p = _pipeline(lines, [], padding=0, layout=StubLayoutDetector([_region("image", 0, 0, 100, 30)]))
    assert Box(0, 0, 100, 30) in p.compute_boxes(_page())


def test_regions_are_off_without_a_detector():
    lines = [_line("Muster GmbH", top=10)]
    assert _pipeline(lines, [], padding=0).compute_boxes(_page()) == [Box(10, 10, 90, 20)]


# -- what must stay readable ---------------------------------------------- #
def test_the_clearing_house_keeps_its_name_but_not_its_address():
    lines = [
        _line("Muster VerrechnungsSysteme GmbH", top=10),
        _line("Muster VerrechnungsSysteme GmbH - 12345 Musterhausen", top=30),
    ]
    assert _pipeline(lines, [], padding=0).compute_boxes(_page()) == [Box(10, 30, 90, 40)]


def test_a_specialty_line_survives_the_imprint():
    """The footer goes black around it, and neither the model nor the name
    memory reaches it."""
    lines = [_line("Dr. med. Andrea Muster", top=10), _line("Fachärztin für Orthopädie", top=30)]
    regions = [_region("footer", 0, 0, 100, 50)]
    p = _pipeline(lines, ["Orthopädie"], padding=0, layout=StubLayoutDetector(regions))
    boxes = p.compute_boxes(_page())
    assert Box(10, 30, 90, 40) not in boxes
    assert Box(0, 0, 100, 30) in boxes and Box(0, 40, 100, 50) in boxes


def test_a_kept_line_clips_the_padding_of_the_line_below_it():
    """The invoice date sits on the tax number's row edge; the tax number's
    padding must not cut into the date, its own box still covers it whole."""
    lines = [
        _line("Rechnungsdatum: 19.03.2026", top=10, height=20),
        _line("Musterstrasse 7", top=28, height=20),
    ]
    boxes = _pipeline(lines, [], padding=4).compute_boxes(_page())
    own = Box(10, 28, 90, 48)
    assert own in boxes  # the redacted line itself, unpadded
    date = Box(10, 10, 90, 30)
    others = [b for b in boxes if b != own]
    assert not any(b.x0 < date.x1 and b.x1 > date.x0 and b.y0 < date.y1 and b.y1 > date.y0 for b in others)
    assert Box(6, 30, 94, 52) in others  # the padding away from the date stays


def test_the_birth_year_is_printed_over_the_redacted_birthdate():
    lines = [_line("Geburtsdatum: 26.06.1975", top=10, width=240, height=20)]
    p = _pipeline(lines, [], padding=0, fill=(0, 0, 0))
    boxes = p.compute_boxes(_page())
    assert boxes == [Box(10, 10, 250, 30), Box(150, 10, 250, 30, text="1975")]
    out = p.apply_boxes(Image.new("RGB", (300, 300), (255, 255, 255)), boxes)
    printed = [out.getpixel((x, y)) for x in range(150, 250) for y in range(10, 30)]
    assert (255, 255, 255) in printed  # the year, white on black
    assert all(out.getpixel((x, 20)) == (0, 0, 0) for x in range(10, 150))  # day and month


# -- composition ------------------------------------------------------------ #
def test_compute_boxes_never_unwarps():
    """The coordinate rule: boxes are in the space of the image passed in."""
    unwarper = RecordingUnwarper()
    p = _pipeline([], [], unwarper=unwarper)
    p.compute_boxes(_page())
    assert unwarper.calls == 0


def test_redact_unwarps_then_applies_the_boxes():
    unwarper = RecordingUnwarper()
    p = _pipeline([_line("Musterstrasse 7", top=10)], [], unwarper=unwarper, unwarp_enabled=True)
    out = p.redact(_page())
    assert unwarper.calls == 1
    assert out.getpixel((50, 15)) == (0, 0, 0)  # the box, over the recolored page
    assert out.getpixel((250, 250)) == (10, 20, 30)  # untouched unwarped ground


def test_apply_boxes_fills_the_exact_rectangle():
    img = Image.new("RGB", (50, 50), (255, 255, 255))
    _pipeline([], [], fill=(0, 0, 0)).apply_boxes(img, [Box(10, 10, 20, 20)])
    assert img.getpixel((15, 15)) == (0, 0, 0)
    assert img.getpixel((25, 25)) == (255, 255, 255)


# -- the trace -------------------------------------------------------------- #
def test_the_trace_narrates_region_then_content_then_verdict():
    """The debug format: a box first, then the text it contributed, then each
    line with the span that decided it. Every region appears, including one
    holding no text at all."""
    lines = [_line("Musterstrasse 7", top=110)]
    regions = [_region("text", 5, 100, 95, 130), _region("image", 0, 0, 90, 40)]
    p = _pipeline(lines, [], padding=0, layout=StubLayoutDetector(regions))
    trace = Trace(collect=True)
    p.compute_boxes(_page(), trace=trace)
    out = trace.collected
    assert "--- region 0  text  0.90  [5, 100, 95, 130] ---" in out
    assert "wholetext: 'Musterstrasse 7'" in out
    assert "ADDRESS 'Musterstrasse 7' [rule DE_STREET 1.00]" in out
    assert "-> REDACT" in out
    # the graphic region is narrated too, though it has no lines to show
    assert "--- region 1  image  0.90  [0, 0, 90, 40] ---" in out
