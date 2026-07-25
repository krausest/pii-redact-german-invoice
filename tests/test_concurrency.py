"""The one thing standing between a worker and 40 concurrent redactions.

One ``RedactionPipeline`` is shared by every request in a worker, the models are
not known to be thread-safe, and a 30-page PDF holds hundreds of MB of page
images — so ``api.max_concurrent_per_worker`` is a safety bound, not a tuning
knob. anyio's default thread pool would allow 40 at once; these tests pin that the
limiter in ``create_app`` really caps it.
"""

from __future__ import annotations

import threading
import time

import anyio
import httpx

from backend.api import create_app
from backend.config import ApiConfig, Config
from tests.conftest import FakePipeline, make_image_bytes

URL = "/api/redact"
PNG = {"content-type": "image/png"}


class ConcurrencyProbe(FakePipeline):
    """Records the high-water mark of threads inside the pipeline at once."""

    def __init__(self, hold: float = 0.1) -> None:
        super().__init__()
        self.peak = 0
        self._live = 0
        self._hold = hold
        self._lock = threading.Lock()

    def compute_boxes(self, image):
        with self._lock:
            self._live += 1
            self.peak = max(self.peak, self._live)
        time.sleep(self._hold)  # stand in for OCR + classify
        with self._lock:
            self._live -= 1
        return super().compute_boxes(image)


def _peak_concurrency(max_concurrent: int, requests: int) -> int:
    """Fire ``requests`` redactions at once; return how many ran simultaneously."""
    app = create_app(Config(api=ApiConfig(max_concurrent_per_worker=max_concurrent)))
    probe = ConcurrencyProbe()
    app.state.pipeline = probe  # set before the lifespan, so no real models load
    body = make_image_bytes("PNG")

    async def main():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:

            async def one():
                r = await client.post(URL, content=body, headers=PNG)
                assert r.status_code == 200

            async with anyio.create_task_group() as tg:
                for _ in range(requests):
                    tg.start_soon(one)

    anyio.run(main)
    return probe.peak


def test_default_config_serializes_redactions():
    """max_concurrent_per_worker = 1: eight callers, one at a time in the models."""
    assert _peak_concurrency(max_concurrent=1, requests=8) == 1


def test_the_bound_is_the_configured_one():
    """Raising the bound really does let more through — and never more than asked."""
    peak = _peak_concurrency(max_concurrent=4, requests=8)
    assert 1 < peak <= 4
