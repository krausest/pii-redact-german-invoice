"""The one thing standing between a worker and 40 concurrent redactions.

One ``RedactionPipeline`` is shared by every request in a worker and its models
are not known to be thread-safe, so two users' documents must never be inside it
at once. anyio's default thread pool would allow 40; this pins that the fixed
limiter in ``create_app`` lets exactly one through.
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

    def compute_boxes(self, image, lines=None, memory=None, trace=None, regions=None, classifier=None, lists=None, granularity=None):
        with self._lock:
            self._live += 1
            self.peak = max(self.peak, self._live)
        time.sleep(self._hold)  # stand in for OCR + classify
        with self._lock:
            self._live -= 1
        return super().compute_boxes(image, trace=trace)


def _fire(requests: int, config: Config | None = None) -> tuple[int, list[httpx.Response]]:
    """Send ``requests`` redactions at once; return how many ran simultaneously,
    and every response."""
    app = create_app(config or Config())
    probe = ConcurrencyProbe()
    app.state.pipeline = probe  # set before the lifespan, so no real models load
    body = make_image_bytes("PNG")
    responses: list[httpx.Response] = []

    async def main():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:

            async def one():
                responses.append(await client.post(URL, content=body, headers=PNG))

            async with anyio.create_task_group() as tg:
                for _ in range(requests):
                    tg.start_soon(one)
            # Drained: the worker takes requests again.
            responses.append(await client.post(URL, content=body, headers=PNG))

    anyio.run(main)
    return probe.peak, responses


def test_a_worker_runs_one_redaction_at_a_time():
    """Nine callers at once — one running, eight queued — one at a time in the models."""
    peak, responses = _fire(requests=9)
    assert peak == 1
    assert [r.status_code for r in responses] == [200] * 10


def test_a_full_queue_turns_requests_away():
    """Past one running and eight waiting, a caller gets a 503 and when to retry."""
    _, responses = _fire(requests=12)
    *burst, after = responses
    statuses = sorted(r.status_code for r in burst)
    assert statuses == [200] * 9 + [503] * 3
    busy = next(r for r in burst if r.status_code == 503)
    assert busy.headers["retry-after"] == "5"
    assert busy.json() == {"detail": "server busy, retry later"}
    assert after.status_code == 200


def test_the_queue_length_is_configured():
    _, responses = _fire(requests=4, config=Config(api=ApiConfig(max_queued_per_worker=0)))
    assert sorted(r.status_code for r in responses[:-1]) == [200] + [503] * 3


def test_bodies_are_decoded_off_the_event_loop(monkeypatch):
    """A queued request holds raw bytes only: pixels are decoded on the worker thread."""
    import asyncio

    from PIL import Image

    on_loop: list[bool] = []
    real_open = Image.open

    def spying_open(*args, **kwargs):
        try:
            asyncio.get_running_loop()
            on_loop.append(True)
        except RuntimeError:
            on_loop.append(False)
        return real_open(*args, **kwargs)

    monkeypatch.setattr(Image, "open", spying_open)
    _, responses = _fire(requests=3)
    assert [r.status_code for r in responses] == [200] * 4
    assert on_loop and not any(on_loop)
