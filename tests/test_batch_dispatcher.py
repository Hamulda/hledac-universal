"""
tests/test_batch_dispatcher.py — Validate ISSUE #16 scheduler pieces.

Runs WITHOUT the 8GB MLX model: the engine is faked, MLX import is avoided at
runtime (the canonical lock's semaphore is acquired/released without a model).
"""

from __future__ import annotations

import asyncio
import json

import pytest

from hledac.universal.brain._batch.dispatcher import GenerateJob, GenerateJobDispatcher
from hledac.universal.brain.hermes import capability_gate as cg
from hledac.universal.brain.hermes.lock import get_metal_lock


class FakeEngine:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self._cache_calls = 0

    async def generate(self, prompt: str, **kwargs: object) -> str:
        self.calls.append((prompt, kwargs.get("system_msg")))
        return f"OUT:{prompt}"

    def cache_stats(self) -> dict[str, int]:
        self._cache_calls += 1
        return {"pool_hits": 3}


async def test_dispatch_basic_roundtrip(monkeypatch) -> None:
    # Force the LLM path (environment-independent): MLX may be absent in CI.
    monkeypatch.setattr(cg, "capability_available", lambda *a, **k: True)
    eng = FakeEngine()
    d = GenerateJobDispatcher(eng)
    await d.start()
    try:
        futs = await d.batch_submit(["a", "b", "c"], system_msg="S1")
        results = await asyncio.gather(*futs, return_exceptions=True)
        assert results == ["OUT:a", "OUT:b", "OUT:c"], results
        assert len(eng.calls) == 3
        st = d.get_stats()
        assert st["completed"] == 3
        assert st["llm_used"] == 3
        assert st["engine_cache"] == {"pool_hits": 3}
    finally:
        await d.stop()


async def test_dispatch_lane_clustering_preserves_prefix(monkeypatch) -> None:
    """Same system_msg => same lane => engine sees the same prefix each time."""
    monkeypatch.setattr(cg, "capability_available", lambda *a, **k: True)
    eng = FakeEngine()
    d = GenerateJobDispatcher(eng, idle_poll_s=0.001)
    await d.start()
    try:
        futs = [await d.submit("p", system_msg="REPORT") for _ in range(5)]
        await asyncio.gather(*futs, return_exceptions=True)
        # All calls must carry the identical system prefix (KV cache reusable).
        assert len(eng.calls) == 5
        assert all(s == "REPORT" for _, s in eng.calls), eng.calls
    finally:
        await d.stop()


async def test_dispatch_capability_fallback(monkeypatch) -> None:
    eng = FakeEngine()
    monkeypatch.setattr(cg, "capability_available", lambda *a, **k: False)
    monkeypatch.setattr(cg, "regex_fallback", lambda text: [f"IOC:{text}"])
    d = GenerateJobDispatcher(eng)
    await d.start()
    try:
        fut = await d.submit("1.2.3.4", system_msg="S2")
        res = await fut
        payload = json.loads(res)
        assert payload["fallback"] is True
        assert payload["iocs"] == ["IOC:1.2.3.4"]
        assert eng.calls == []  # LLM must NOT run when gated off
        assert d.get_stats()["fallback_used"] == 1
    finally:
        await d.stop()


async def test_dispatch_queue_full_backpressure() -> None:
    d = GenerateJobDispatcher(FakeEngine(), max_queue_size=2)
    await d.start()
    try:
        await d.submit("x")
        await d.submit("y")
        with pytest.raises(RuntimeError):
            await d.submit("z")  # bounded queue full
    finally:
        await d.stop()


async def test_dispatch_requires_start() -> None:
    d = GenerateJobDispatcher(FakeEngine())
    with pytest.raises(RuntimeError):
        await d.submit("x")


async def test_dispatch_stop_resolves_pending(monkeypatch) -> None:
    monkeypatch.setattr(cg, "capability_available", lambda *a, **k: True)
    evt = asyncio.Event()

    class SlowEngine:
        async def generate(self, prompt: str, **kw: object) -> str:
            await evt.wait()  # never set -> job stays in flight
            return "OUT"

    d = GenerateJobDispatcher(SlowEngine())
    await d.start()
    fut = await d.submit("x")
    await asyncio.sleep(0.05)  # let worker enter generate()
    await d.stop(timeout=2.0)
    assert fut.done()
    with pytest.raises(asyncio.CancelledError):
        fut.result()


# ── capability_gate unit ──────────────────────────────────────────────────────


def test_capability_gate_fail_open(monkeypatch) -> None:
    # Degraded backend score => gate closed.
    monkeypatch.setattr(cg, "rust_capability_score", lambda: 0.1)
    assert cg.capability_available() is False
    # Healthy score => gate open.
    monkeypatch.setattr(cg, "rust_capability_score", lambda: 1.0)
    assert cg.capability_available() is True


def test_capability_regex_fallback_safe() -> None:
    # Must never raise; returns a list.
    assert isinstance(cg.regex_fallback("8.8.8.8 www.evil.test"), list)


# ── metal lock diagnostics ────────────────────────────────────────────────────


async def test_metal_lock_diagnostics() -> None:
    lock = get_metal_lock()
    before = lock.get_diagnostics()["acquires"]
    async with lock.acquire():
        pass
    after = lock.get_diagnostics()
    assert after["acquires"] == before + 1
    assert "capability_score" in after
    assert "backend_stats" in after
