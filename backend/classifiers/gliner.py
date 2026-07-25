"""GLiNER classifier: a single zero-shot NER model scored against PII labels.

Tests whether one modern NER model can replace the Presidio NER+regex stack. The
German street / ZIP+city and salutation gaps GLiNER leaves are closed by the
shared deterministic rules the pipeline applies (see :mod:`backend.rules`).
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path

# Run fully offline against the local cache — set before any HF import.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
# Let unsupported ops fall back to CPU when running the model on Apple MPS.
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
# Keep the HuggingFace cache (GLiNER + its mdeberta backbone) inside the project
# instead of ~/.cache/huggingface, so the model runs fully offline.
os.environ.setdefault(
    "HF_HUB_CACHE", str(Path(__file__).resolve().parent.parent.parent / ".gliner_cache")
)
warnings.filterwarnings("ignore", message=".*resume_download.*")

GLINER_MODEL = "urchade/gliner_multi_pii-v1"
# Zero-shot labels the model scores each line against. Kept to the same scope the
# Presidio path redacts (no "organization": company names are not redacted).
PII_LABELS = ["person", "address", "email", "iban", "bic"]


def _best_device() -> str:
    import torch

    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


class GlinerClassifier:
    def __init__(self, threshold: float = 0.6) -> None:
        from gliner import GLiNER

        self._model = GLiNER.from_pretrained(GLINER_MODEL).to(_best_device())
        self._threshold = threshold

    def is_pii(self, text: str) -> bool:
        for ent in self._model.predict_entities(text, PII_LABELS, threshold=self._threshold):
            # A person must be multi-word (mirrors the Presidio PERSON guard,
            # which drops single-token NER noise like "5.0016").
            if ent["label"] == "person" and " " not in ent["text"].strip():
                continue
            return True
        return False
