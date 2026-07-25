"""Build-time model warmup: bake every engine's models into the image.

Run during `docker build` (with HF_HUB_OFFLINE=0 so GLiNER may download). Building
each engine's pipeline *constructs* its models, which is what triggers the download
into the image caches (Paddle native + onnxruntime OCR, and GLiNER + its backbone).
The unwarp models are NOT covered by that: the engines only receive an unwarper
*factory* (see backend/factory.py), and the DocUnwarper is built on the first
``unwarp()`` call — which never happens here — so it is constructed explicitly
below. Afterwards `/app/.paddle_cache` and `/app/.gliner_cache` are populated, so
the runtime container needs no network (and the Dockerfile sets HF_HUB_OFFLINE=1
at runtime, so a model missed here fails loudly instead of downloading).

We deliberately do NOT run inference here: it isn't needed to fetch the model files,
and Paddle's oneDNN kernels crash under x86 QEMU emulation (when building an amd64
image on an arm64 host) — on a real amd64 host inference runs fine at runtime.

The spaCy ``de_core_news_lg`` model is a pip package (installed by uv), not a
download, so it is already present.
"""

from __future__ import annotations

from backend.config import Config, EngineConfig
from backend.factory import build_pipeline
from backend.unwarp import DocUnwarper

ENGINES = ("native", "onnx", "gliner")


def main() -> None:
    for name in ENGINES:
        print(f"[warmup] constructing engine (downloads models): {name}", flush=True)
        try:
            build_pipeline(Config(engine=EngineConfig(name=name)))
        except ModuleNotFoundError as e:
            # e.g. the 'gliner' engine when the image is built without its extra.
            print(f"[warmup] skipping {name}: {e}", flush=True)
            continue
        print(f"[warmup] done: {name}", flush=True)
    # Lazy in the pipeline (see module docstring); construction alone downloads
    # UVDoc + PP-LCNet_x1_0_doc_ori, no inference involved.
    print("[warmup] constructing DocUnwarper (downloads unwarp models)", flush=True)
    DocUnwarper()
    print("[warmup] engine models baked", flush=True)


if __name__ == "__main__":
    main()
