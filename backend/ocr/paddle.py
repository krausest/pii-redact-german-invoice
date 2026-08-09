"""PaddleOCR backend, in either Paddle-native or ONNX Runtime flavor.

Both flavors run the same German PP-OCRv6 models and return the same text and box
geometry; they differ only in inference engine:

* ``engine="paddle"`` — Paddle's native predictor. On Apple Silicon / macOS it
  runs single-threaded and dominates the runtime (~12.7 s per invoice).
* ``engine="onnxruntime"`` — PaddleOCR's ONNX Runtime path, which spreads the
  same models across ~7 CPU cores, cutting a single page to ~3.8 s (~3.3x) with
  no change in accuracy. The ONNX models are auto-downloaded on first use into
  ``.paddle_cache`` next to the Paddle ones. ``cpu_threads`` is passed through for
  completeness, but ONNX Runtime auto-sizes its own intra-op pool.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

# Keep Paddle's cache inside the project and skip the remote model-source check —
# set before importing paddle.
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
os.environ.setdefault(
    "PADDLE_PDX_CACHE_HOME", str(Path(__file__).resolve().parent.parent.parent / ".paddle_cache")
)

import numpy as np
from PIL import Image

from backend.models import Line

logging.getLogger("paddlex").setLevel(logging.WARNING)


class PaddleOCRBackend:
    """Adapts PaddleOCR 3.x output to a list of :class:`Line`."""

    def __init__(
        self,
        lang: str = "german",
        engine: str = "paddle",
        cpu_threads: int = 10,
        det_box_thresh: float = 0.5,
    ) -> None:
        from paddleocr import PaddleOCR

        kwargs = dict(
            lang=lang,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            # Below PaddleOCR's own 0.6, and the reason is a shape, not a
            # contrast: the threshold applies to the *mean* detector score over
            # the box, so one line of 6pt type running the full width of the
            # page averages lower than the 11pt table above it even though its
            # ink is just as dark. The imprint footer of a photographed A4 page
            # measured 0.5..0.6 and so was not detected *at all* — no box, no
            # text, and therefore no footer band either, since the band needs a
            # sender anchor to exist. 0.5 finds those three lines and nothing
            # else: on the sample pages it changes no other box.
            text_det_box_thresh=det_box_thresh,
        )
        if engine == "onnxruntime":
            kwargs.update(engine="onnxruntime", cpu_threads=cpu_threads)
        self._ocr = PaddleOCR(**kwargs)

    def lines(self, image: Image.Image) -> list[Line]:
        results = self._ocr.predict(np.array(image.convert("RGB")))
        if not results:  # blank / OCR-failed page
            return []
        result = results[0]
        texts = result["rec_texts"]
        scores = result["rec_scores"]
        boxes = result["rec_boxes"]  # axis-aligned [x1, y1, x2, y2]

        out: list[Line] = []
        for text, score, box in zip(texts, scores, boxes):
            x1, y1, x2, y2 = (int(v) for v in np.asarray(box).tolist())
            out.append(
                Line(
                    text=text,
                    left=x1,
                    top=y1,
                    width=x2 - x1,
                    height=y2 - y1,
                    conf=float(score) * 100.0,  # Presidio uses a 0-100 scale
                )
            )
        return out
