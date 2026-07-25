"""Document unwarping (page flattening), shared by every engine.

Photos of paper curl at the edges, which tilts the lines near the margins so the
OCR text detector misses them. We unwarp *first* and then OCR + redact the
flattened image, so the boxes line up by construction — there is no way to map
boxes from the flat space back onto the curled photo.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

# Skip Paddle's remote model-source check and keep its cache inside the project
# (instead of ~/.paddlex) — set before importing paddle.
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
os.environ.setdefault(
    "PADDLE_PDX_CACHE_HOME", str(Path(__file__).resolve().parent.parent / ".paddle_cache")
)

import numpy as np
from PIL import Image

# Silence paddlex's INFO "Creating model / cached files" chatter (not downloads).
logging.getLogger("paddlex").setLevel(logging.WARNING)


class DocUnwarper:
    """Flattens a photographed / curled document with PaddleOCR's UVDoc model."""

    def __init__(self) -> None:
        from paddlex import create_pipeline

        self._pipe = create_pipeline("doc_preprocessor")

    def unwarp(self, image: Image.Image) -> Image.Image:
        arr = np.array(image.convert("RGB"))
        result = next(
            iter(
                self._pipe.predict(
                    arr,
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=True,
                )
            )
        )
        return Image.fromarray(np.asarray(result["output_img"]))  # already RGB
