"""Barcodes and QR codes: they encode the invoice number or, as a GiroCode, the
payee and IBAN. OCR reads none of it, so each code is blackened whole."""

from __future__ import annotations

from typing import NamedTuple

import cv2
import numpy as np
from PIL import Image

from backend.models import Box

# A bar row has at least this share of the transitions of the rows found.
_BAR_ROW_SHARE = 0.6
# How far the bars may reach beyond the rows found, in their height.
_MAX_GROWTH = 3


class CodeBox(NamedTuple):
    """A box over one code; ``kind`` is ``barcode`` or ``qr``."""

    kind: str
    box: Box


def code_boxes(image: Image.Image, padding: int) -> list[CodeBox]:
    """A padded box over every barcode and QR code on ``image``."""
    gray = np.asarray(image.convert("L"))
    out = [CodeBox("barcode", _grow_bars(gray, b).padded(padding)) for b in _found(cv2.barcode.BarcodeDetector(), gray)]
    out += [CodeBox("qr", b.padded(padding)) for b in _found(cv2.QRCodeDetector(), gray, multi=True)]
    return out


def _found(detector, gray: np.ndarray, multi: bool = False) -> list[Box]:
    ok, points = (detector.detectMulti if multi else detector.detect)(gray)[:2]
    if not ok or points is None:
        return []
    h, w = gray.shape
    boxes = []
    for quad in np.asarray(points).reshape(-1, 4, 2):
        x0, y0 = np.clip(quad.min(axis=0), 0, (w, h)).astype(int)
        x1, y1 = np.clip(quad.max(axis=0), 0, (w, h)).astype(int)
        if x1 > x0 and y1 > y0:
            boxes.append(Box(int(x0), int(y0), int(x1), int(y1)))
    return boxes


def _grow_bars(gray: np.ndarray, box: Box) -> Box:
    """``box`` stretched up and down over every row still crossing the bars:
    the detector reports only the band it scanned."""
    strip = gray[:, box.x0 : box.x1]
    dark = strip <= _threshold(strip[box.y0 : box.y1])
    transitions = np.count_nonzero(dark[:, 1:] != dark[:, :-1], axis=1)
    floor = _BAR_ROW_SHARE * float(np.median(transitions[box.y0 : box.y1]))
    if floor < 1:
        return box
    reach = _MAX_GROWTH * (box.y1 - box.y0)
    top, bottom = box.y0, box.y1
    while top > max(0, box.y0 - reach) and transitions[top - 1] >= floor:
        top -= 1
    while bottom < min(len(transitions), box.y1 + reach) and transitions[bottom] >= floor:
        bottom += 1
    return Box(box.x0, top, box.x1, bottom)


def _threshold(pixels: np.ndarray) -> float:
    """Otsu's threshold between bars and gaps."""
    value, _ = cv2.threshold(pixels, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return float(value)
