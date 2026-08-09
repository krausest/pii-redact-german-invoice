"""Matrix-code redaction: QR codes and DataMatrix codes.

The third box source, and the only one that reads *pixels* rather than OCR lines.
A matrix code is a graphic, so ``OCRBackend.lines`` returns nothing for it and no
text rule can ever reach it — the same blind spot :mod:`backend.regions` exists
for, but here the graphic is not merely an identifier, it is machine-readable PII
in the clear:

* an **EPC QR / Girocode** carries IBAN, BIC and the account-holder *name*;
* a **Swiss QR-bill** adds the full debtor address;
* a **DataMatrix** carries the E-Rezept token, a securPharm pack identity or a
  Deutsche Post franking record.

Blackening the box is the whole point: a "redacted" page that still scans is not
redacted. Recognition is therefore tuned for recall — see ``return_errors``
below — and the resulting boxes are, like every other box here, in the pixel
space of the image passed in.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from backend.models import Box

if TYPE_CHECKING:
    from PIL import Image

logger = logging.getLogger(__name__)

# rMQR is deliberately absent. It is the one matrix format that is legitimately
# long and thin (up to 17x139 modules), which would force `_MAX_ASPECT` open wide
# enough to re-admit the text-row false positive it exists to reject — and no
# German invoice carries one. QR, Micro QR and DataMatrix are what actually shows
# up on this paper. Note this list is a *request*, not a guarantee: on the
# `return_errors` path zxing-cpp reports formats outside it (an rMQR turns up in
# the fragment case below), so nothing downstream may assume the format is one of
# these.
_FORMAT_NAMES = ("QRCode", "MicroQRCode", "DataMatrix")

# Linear symbologies, read by a separate pass — see `_read_linear` for why it cannot
# just be this tuple with three more names in it. These are what German medical
# paperwork actually prints: a lab order or sample number, rotated 90 degrees up the
# left edge. EAN/UPC/Codabar are deliberately absent — they price groceries, not
# invoices, and every format costs its own read of the page.
_LINEAR_FORMAT_NAMES = ("ITF", "Code128", "Code39")

# When no symbol on the page decoded, retry once on a 2x enlargement before
# trusting any of the located-but-undecoded geometry. A QR that is merely too
# coarse for the decoder — a SEPA/Girocode at a couple of pixels per module — comes
# back as one clean read with exact corners, which is worth far more than patching
# up the fragments it otherwise produces. Measured: pages where the code covered
# only its own top-left corner became exact at 2x.
#
# Note this is *not* gated on the page being low-resolution. A 15 mm Girocode on a
# 200 dpi A4 render is 118 px — 2.1 px per module — and fragments exactly like a
# thumbnail does, on a 3.9 MP page. Code coarseness is not page coarseness.
_RETRY_UPSCALE = 2

# ...but when the first pass found *nothing at all*, there is usually nothing to
# find, and re-reading every clean page at 4x the pixels costs ~10x this pass for
# no result. So that case is retried only for pages small enough that "nothing" is
# plausibly "too coarse to see" rather than "no code here". A page that located
# something is always retried, however large: that is the fragment case.
_RETRY_BLIND_MAX_PIXELS = 2_000_000

# Last resort, when even the retry fails to decode. What zxing-cpp returns then is
# not one box per symbol but one per *finder pattern* — a QR's three 7x7 concentric
# corner squares each read as a Micro QR, and they do not touch each other, so
# overlap-merging leaves several partial boxes with the symbol's middle and
# bottom-right showing. That is the bug this constant exists for. Fragments of one
# symbol are fused when the gap between them is under this multiple of the larger
# one's longer side.
#
# The value is measured, not derived from the 7-modules-of-57 geometry: what
# zxing-cpp actually reports on the fragment path is not a bare finder pattern but a
# chunk about a third of the symbol, so the widest real gap across the degradation
# corpus is 0.79 (two pieces 27 px apart, longer side 34). The 7-finder-widths
# reading gave 8.0, and that is wide enough to reach *past a symbol* to the next one:
# a page carrying a DataMatrix above a Girocode fused the two into one box spanning
# the item table between them. Nothing above ~2 is needed and everything above ~5
# can bridge two symbols on the same page.
_FRAGMENT_REACH = 1.5

# Two shape guards on every candidate, needed only because `return_errors=True`
# also reports symbols that were *located but not decoded* (see `code_boxes`), and
# an undecoded candidate has had no checksum to prove it real.
#
# Measured on Arztrechnung/Rechnung1.pdf, where the error-tolerant DataMatrix
# search latched onto three rows of the item table and returned a 738x108 quad
# (6.8:1) with self-crossing corners. Both real codes in the sample corpus are
# square to within a pixel or two, so a 3:1 allowance is loose enough for
# rectangular DMRE symbols and still an order of magnitude clear of that quad.
_MAX_ASPECT = 3.0
# Below this a "code" is a smudge; the smallest real symbol in the corpus is 36 px.
_MIN_SIDE_PX = 12


@dataclass(frozen=True)
class CodeParams:
    """Geometry for :func:`code_boxes`.

    ``margin_frac`` grows each box by that fraction of its own longer side. The
    detected quad already lands on the symbol's outer edge — measured across QR and
    DataMatrix at several sizes, clean, blurred, and downscaled-plus-JPEG, the
    reported side never fell more than 1.4% short of the symbol. So the margin is
    headroom, not a correction: it absorbs that error with an order of magnitude to
    spare. What it spends is blank paper — it grows into the code's quiet zone,
    which the format requires to be empty.

    ``padding`` is the flat pixel padding the line boxes use, applied on top so
    every box on the page shares the same minimum breathing room.
    """

    margin_frac: float
    padding: int = 0


def code_boxes(image: Image.Image, p: CodeParams) -> list[Box]:
    """Bounding boxes of the QR / DataMatrix codes in ``image``.

    One symbol yields one box, however many ways it was found — which takes real
    work when nothing decoded, because then a symbol arrives in pieces.
    """
    reads = _read(image)
    if not any(decoded for decoded, _ in reads) and _worth_retrying(image, reads):
        # Nothing verified. Give the decoder a bigger picture to work from — but take
        # what comes back for its *geometry*, not for a decode: a symbol that will not
        # checksum is still a symbol, and blackening it is the whole job.
        #
        # Unioned rather than substituted because neither pass is a superset of the
        # other. An earlier version replaced `reads` only when the retry *decoded*
        # something, which silently dropped the case this exists for: a dewarped phone
        # photo whose Girocode zxing-cpp cannot even locate at 1x is located at 2x and
        # still does not checksum, so the one pass that saw it was the one discarded.
        size = (image.width * _RETRY_UPSCALE, image.height * _RETRY_UPSCALE)
        retry = [(decoded, _scaled(rect, 1 / _RETRY_UPSCALE)) for decoded, rect in _read(image.resize(size))]
        if retry:
            logger.debug("no symbol decoded at 1x; adding the %dx pass", _RETRY_UPSCALE)
            reads += retry

    # Decoded corners are exact, so those boxes stand on their own. The rest are
    # fragments of something, and have to be reassembled before they mean anything.
    exact = [rect for decoded, rect in reads if decoded]
    fragments = [rect for decoded, rect in reads if not decoded]

    # Linear barcodes are exact by construction — the pass only reports decoded ones —
    # and are never fragments, so they join the set that vouches for itself. Added
    # here rather than into `reads` on purpose: they must not count towards the
    # "nothing decoded" test above, since a page can carry a readable barcode *and* an
    # unreadable QR, and the QR is the one that needs the enlarged retry.
    exact += _read_linear(image)

    if fragments:
        # Ask OpenCV for the outline before trying to rebuild one. It reads the three
        # finder patterns as a *set* and returns the symbol they bound, so it answers
        # the question the fragments only hint at — and it does so without decoding,
        # which is the whole difficulty here.
        outlines = [
            rect
            for rect in _qr_outlines(image)
            if not any(_overlaps_rect(rect, done) for done in exact)
        ]
        exact += outlines
        fragments = [f for f in fragments if not any(_overlaps_rect(f, r) for r in exact)]

    boxes = [box for rect in exact + _fuse_fragments(fragments) if (box := _box(rect, image, p))]
    for box in boxes:
        logger.debug("code -> REDACT %s", box.as_list())
    return _merge_overlapping(boxes)


def _qr_outlines(image: Image.Image) -> list[tuple[float, float, float, float]]:
    """Whole-symbol QR outlines from OpenCV, which never decodes anything.

    This is the piece zxing-cpp cannot supply. Its reader has to decode to report a
    symbol, and when it fails it falls back to reporting each finder pattern
    separately; OpenCV's detector instead solves for the quad the three finder
    patterns *imply*, so a code too coarse to read still yields its true outline.
    Measured on a 15 mm Girocode at 200 dpi: exact to a pixel, where reassembling
    the fragments was 2x oversized and offset onto the line above.
    """
    import cv2
    import numpy as np

    found, points = cv2.QRCodeDetectorAruco().detectMulti(np.asarray(image.convert("L")))
    if not found or points is None:
        return []
    outlines = []
    for quad in points:
        rect = (
            float(quad[:, 0].min()),
            float(quad[:, 1].min()),
            float(quad[:, 0].max()),
            float(quad[:, 1].max()),
        )
        if _plausible(rect):
            outlines.append(rect)
    return outlines


def _overlaps_rect(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _worth_retrying(image: Image.Image, reads: list) -> bool:
    """Whether a second, enlarged read is likely to pay for itself."""
    if reads:
        return True  # something is there and it came out in pieces: always worth it
    return image.width * image.height <= _RETRY_BLIND_MAX_PIXELS


def _read(image: Image.Image) -> list[tuple[bool, tuple[float, float, float, float]]]:
    """Every located symbol as ``(decoded, enclosing rect)``, shape guards applied.

    ``return_errors=True`` is the setting that makes this pass worth having. Plain
    decoding reports only symbols whose checksum verifies, and the ~60 px payment QR
    on a phone-photographed invoice in the sample corpus does not verify: unredacted
    at exactly the resolution a real upload has. With errors returned it is found.
    Detection, not decoding, is what redaction needs; ``_MAX_ASPECT`` /
    ``_MIN_SIDE_PX`` pay for the looser gate.

    Note this is measured on the image *as passed in*, and `compute_boxes` runs after
    unwarping. On that same page the dewarp resamples the symbol just past what the
    finder-pattern search will locate at 1x — nothing here reports it, at either
    error setting — which is why `code_boxes` cannot rely on a single read.
    (OpenCV's detect-without-decode QR detector was measured as an alternative and
    found nothing this misses, on 17 synthetic degradations or on the sample corpus,
    so it is not used.)
    """
    import zxingcpp

    formats = [getattr(zxingcpp.BarcodeFormat, name) for name in _FORMAT_NAMES]
    reads: list[tuple[bool, tuple[float, float, float, float]]] = []
    for barcode in zxingcpp.read_barcodes(image, formats=formats, return_errors=True):
        rect = _quad_rect(barcode.position)
        if _plausible(rect):
            reads.append((barcode.error is None, rect))
    return reads


def _read_linear(image: Image.Image) -> list[tuple[float, float, float, float]]:
    """Decoded linear barcodes, as one enclosing rect each.

    Two things here are the exact opposite of :func:`_read`, and both are deliberate.

    **One call per format**, which is not something a clean fixture will show you.
    On a generated page a merged format list finds everything, so folding these three
    names into `_FORMAT_NAMES` looks like an obvious simplification and passes the
    tests. On the real thing it silently stops working: the ITF up the left edge of a
    12 MP phone photo in the sample corpus is found by ``formats=ITF`` and by nothing
    else — paired with any other format, or left to the default all-formats scan, the
    result is an empty list. Verify against a photographed page, not a synthetic one.

    **Decoded only** — no ``return_errors``. The matrix pass can afford undecoded
    geometry because a matrix code is square and `_MAX_ASPECT` vouches for the shape.
    A linear barcode *is* the long thin shape that guard exists to reject, so shape
    proves nothing here and the checksum has to instead. Which is also why these
    rects skip `_plausible`: printed the ordinary way round a barcode is 8:1 and
    would fail it outright, and the one in the corpus clears 3.0 by 1% only because
    it happens to be rotated.
    """
    import zxingcpp

    rects = []
    for name in _LINEAR_FORMAT_NAMES:
        fmt = getattr(zxingcpp.BarcodeFormat, name)
        rects += [_quad_rect(barcode.position) for barcode in zxingcpp.read_barcodes(image, formats=fmt)]
    return rects


def _quad_rect(position: Any) -> tuple[float, float, float, float]:
    """A detection's four corners as one axis-aligned rect.

    The corners are taken as an unordered set — a `Box` is axis-aligned, and an
    error-tolerant detection can report them wound in any order, or self-crossing.
    """
    corners = (
        position.top_left,
        position.top_right,
        position.bottom_right,
        position.bottom_left,
    )
    xs = [c.x for c in corners]
    ys = [c.y for c in corners]
    return min(xs), min(ys), max(xs), max(ys)


def _plausible(rect: tuple[float, float, float, float]) -> bool:
    """Whether a rect is the right shape to be a matrix code at all."""
    side_x, side_y = rect[2] - rect[0], rect[3] - rect[1]
    if side_x < _MIN_SIDE_PX or side_y < _MIN_SIDE_PX:
        return False
    return max(side_x, side_y) <= _MAX_ASPECT * min(side_x, side_y)


def _scaled(rect, factor: float) -> tuple[float, float, float, float]:
    return (rect[0] * factor, rect[1] * factor, rect[2] * factor, rect[3] * factor)


def _fuse_fragments(rects: list) -> list[tuple[float, float, float, float]]:
    """Reassemble located-but-undecoded pieces into whole symbols.

    Each piece is typically one finder pattern, so the pieces of a symbol sit apart
    rather than overlapping and never individually reach its far side. They are
    grouped by proximity, and each group is then squared about its centre: a matrix
    code is square, and the group's own extent is the best estimate of the side.
    Square-and-fuse is what makes the box cover the whole code instead of the corner
    it was anchored on.
    """
    groups: list[list[tuple[float, float, float, float]]] = []
    for rect in rects:
        near = [g for g in groups if any(_within_reach(rect, other) for other in g)]
        merged = [rect] + [r for g in near for r in g]
        groups = [g for g in groups if g not in near] + [merged]

    fused = []
    for group in groups:
        x0 = min(r[0] for r in group)
        y0 = min(r[1] for r in group)
        x1 = max(r[2] for r in group)
        y1 = max(r[3] for r in group)
        # Square it by extending right and down, *not* about the centre. A QR's three
        # finder patterns are its top-left, top-right and bottom-left corners — there
        # is none at the bottom right — so a group is anchored at the symbol's
        # top-left and the side it falls short on is always the far one. Growing
        # about the centre instead walks the box up and off the code, covering the
        # line above it while leaving the code's own bottom-right showing.
        side = max(x1 - x0, y1 - y0)
        fused.append((x0, y0, x0 + side, y0 + side))
    return fused


def _within_reach(a, b) -> bool:
    """Whether two pieces are close enough to belong to the same symbol."""
    gap_x = max(0.0, max(a[0], b[0]) - min(a[2], b[2]))
    gap_y = max(0.0, max(a[1], b[1]) - min(a[3], b[3]))
    longest = max(a[2] - a[0], a[3] - a[1], b[2] - b[0], b[3] - b[1])
    return max(gap_x, gap_y) <= _FRAGMENT_REACH * longest


def _box(rect, image: Image.Image, p: CodeParams) -> Box | None:
    """A rect as the padded, clamped :class:`Box` that actually gets drawn."""
    x0, y0, x1, y1 = rect
    grow = round(p.margin_frac * max(x1 - x0, y1 - y0)) + p.padding
    box = Box(
        max(0, round(x0) - grow),
        max(0, round(y0) - grow),
        min(image.width, round(x1) + grow),
        min(image.height, round(y1) + grow),
    )
    return box if box.x1 > box.x0 and box.y1 > box.y0 else None


def _merge_overlapping(boxes: list[Box]) -> list[Box]:
    """Fuse boxes that overlap into their bounding box, until none do.

    One symbol is routinely reported more than once — a QR whose corner also
    parses as a Micro QR, or several partial reads of a damaged code — and those
    detections overlap. Merging keeps the report honest about how many codes are on
    the page. The naive O(n^2) sweep is the right one: a page has 0-3 codes.
    """
    merged: list[Box] = []
    for box in boxes:
        current = box
        rest: list[Box] = []
        for other in merged:
            if _overlaps(current, other):
                current = Box(
                    min(current.x0, other.x0),
                    min(current.y0, other.y0),
                    max(current.x1, other.x1),
                    max(current.y1, other.y1),
                )
            else:
                rest.append(other)
        rest.append(current)
        merged = rest
    return merged


def _overlaps(a: Box, b: Box) -> bool:
    return a.x0 < b.x1 and b.x0 < a.x1 and a.y0 < b.y1 and b.y0 < a.y1
