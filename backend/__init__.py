"""PII redaction for scanned German invoices.

A single pipeline (unwarp -> OCR -> per-line PII classification -> box fill) with
a pluggable OCR backend (Paddle native or ONNX Runtime) and a pluggable PII
classifier (Presidio). The concrete combination is chosen by config (see
:mod:`backend.config` and :mod:`backend.factory`).
"""

from backend.models import Box, Line

__all__ = ["Box", "Line"]
