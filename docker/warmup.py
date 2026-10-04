"""Build-time model warmup: bake every engine's models into the image.

Run during `docker build`. Building each engine's pipeline *constructs* its models
(OCR, classifier, and the PP-DocLayout detector behind the whole-region pass),
which is what triggers the download into the image cache. Looping over both
OCR backends is what makes that complete: the backend selects the *inference runtime*
for every paddle model, so each pass fetches its own flavour of the OCR
detection/recognition models and of the layout detector (native and ONNX). The unwarp models are NOT covered by that: the engines only
receive an unwarper *factory* (see backend/factory.py), and the DocUnwarper is
built on the first ``unwarp()`` call — which never happens here — so it is
constructed explicitly below. Afterwards `/app/.paddle_cache` is populated, so
the runtime container needs no network (and the Dockerfile forces offline at
runtime, so a model missed here fails loudly instead of downloading).

We deliberately do NOT run inference here: it isn't needed to fetch the model files,
and Paddle's oneDNN kernels crash under x86 QEMU emulation (when building an amd64
image on an arm64 host) — on a real amd64 host inference runs fine at runtime.

The spaCy ``de_core_news_lg`` model is a pip package (installed by uv), not a
download, so it is already present.
"""

from __future__ import annotations

import typing

from backend.config import Config, EngineConfig, OCRBackend
from backend.factory import build_pipeline, classifier_names, _build_classifier
from backend.unwarp import DocUnwarper


def main() -> None:
    for name in typing.get_args(OCRBackend):
        print(f"[warmup] constructing engine (downloads models): {name}", flush=True)
        # No try/except: a model this step cannot fetch is a broken image, and the
        # runtime is offline — better to fail the build than to fail every request.
        build_pipeline(Config(engine=EngineConfig(ocr_backend=name)))
        print(f"[warmup] done: {name}", flush=True)
    # The classifiers are the one thing `build_pipeline` no longer constructs:
    # they are chosen per request and built on first use, so a pipeline holds
    # factories, not models. That laziness is right at runtime and wrong here —
    # the image has to carry every checkpoint a request may name. guard-omni's
    # comes from HuggingFace, which is why `HF_HUB_OFFLINE=1` at runtime is a
    # real network guard again rather than a no-op.
    for name in classifier_names():
        print(f"[warmup] constructing classifier (downloads models): {name}", flush=True)
        _build_classifier(name, Config().redaction.score_threshold)
        print(f"[warmup] done: {name}", flush=True)
    # Lazy in the pipeline (see module docstring); construction alone downloads
    # UVDoc + PP-LCNet_x1_0_doc_ori, no inference involved.
    print("[warmup] constructing DocUnwarper (downloads unwarp models)", flush=True)
    DocUnwarper()
    print("[warmup] engine models baked", flush=True)


if __name__ == "__main__":
    main()
