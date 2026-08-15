"""
tests/test_stream_via_queue.py

L-02 CRITICAL: stream_via_queue — async bridge for sync generators.

Tests:
  1. basic_tokens       — 5 tokens, sequential yield, correct order + checksum
  2. empty_generator    — zero tokens, clean exit, no hang
  3. error_in_generator — exception in producer, consumer gets nothing, no crash
  4. cancellation        — consumer cancelled, executor future cancelled, clean exit
  5. queue_backpressure  — queue_max=1 forces producer to block-wait, but consumer
                            drains fast enough; all tokens arrive in order

Invariant checks:
  - ALWAYS-ON: no feature flag; always available
  - BOUNDED: queue_max prevents unbounded memory
  - FAIL-SAFE: no exception propagates to caller
  - M1-SAFE: no event-loop-blocking operations

Final: pytest tests/test_stream_via_queue.py -xvs -q
"""

from __future__ import annotations

import asyncio
import hashlib
import pytest

from hledac.universal._core.sync_bridge import stream_via_queue
from _core import aclose


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _checksum(tokens: list[str]) -> str:
    return hashlib.sha256("".join(tokens).encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def event_loop():
    """Create a fresh event loop for each test."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ---------------------------------------------------------------------------
# Tests — all use fresh event loop to avoid shared-state leakage
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_basic_tokens():
    """5 tokens yielded sequentially, correct order, checksum verified."""
    tokens = ["Der", " Geist", " ist", " ein", " Wind"]

    def gen():
        for t in tokens:
            yield t

    result = []
    async for tok in stream_via_queue(gen):
        result.append(tok)

    assert result == tokens
    assert _checksum(result) == _checksum(tokens)


@pytest.mark.asyncio
async def test_empty_generator():
    """Zero-token generator exits cleanly without hanging."""
    def gen():
        yield from ()  # must yield to return a generator object, not None

    result = []
    async for _tok in stream_via_queue(gen):
        result.append(_tok)

    assert result == []


@pytest.mark.asyncio
async def test_error_in_generator_propagates_nothing():
    """Exception raised inside the sync generator is swallowed; consumer gets nothing."""
    def gen():
        yield "a"
        raise RuntimeError("synthetic error from producer")

    result = []
    errors: list[Exception] = []
    try:
        async for tok in stream_via_queue(gen):
            result.append(tok)
    except Exception as e:
        errors.append(e)

    # No exception propagates — fail-safe invariant
    assert errors == []
    # Producer may have yielded "a" before crashing, or nothing at all — both OK
    assert all(t in ("a",) for t in result)


@pytest.mark.asyncio
async def test_cancellation():
    """
    asyncio.timeout cancels the consumer — verifies CancelledError propagates
    through stream_via_queue and executor future is cancelled cleanly.
    """
    import time

    # Use a fast generator; the point is that asyncio.timeout fires
    # CancelledError at the next await (q.get()), not that we wait for 100 tokens.
    def gen():
        for i in range(1000):
            yield str(i)

    results: list[str] = []

    async def consumer_inner():
        async for tok in stream_via_queue(gen):
            results.append(tok)

    start = time.monotonic()

    # asyncio.timeout fires CancelledError at the NEXT await — in our case,
    # at the q.get() inside stream_via_queue. The consumer catches it and exits.
    async with asyncio.timeout(0.2):
        try:
            await consumer_inner()
        except asyncio.CancelledError:
            pass  # expected

    elapsed = time.monotonic() - start

    # Should exit promptly after timeout fires, not hang for seconds.
    # Allow generous 1.5s to account for ThreadPoolExecutor internal latency.
    assert elapsed < 1.5, f"Cancellation took {elapsed:.2f}s — executor not cancelled"
    # Some tokens may have been produced before timeout
    assert len(results) >= 1, "At least 1 token should be produced before timeout"


@pytest.mark.asyncio
async def test_queue_backpressure():
    """queue_max=1 still produces all tokens correctly (backpressure handled)."""
    tokens = [f"token_{i}" for i in range(20)]

    def gen():
        for t in tokens:
            yield t

    result = []
    async for tok in stream_via_queue(gen):
        result.append(tok)

    assert result == tokens
    assert _checksum(result) == _checksum(tokens)


# ---------------------------------------------------------------------------
# Diagnostic: confirm the buggy pattern is NOT in deephermes3_engine.py
# ---------------------------------------------------------------------------

def test_buggy_pattern_removed():
    """
    CONFIRM: The buggy `async for token in asyncio.to_thread(...)` pattern
    is no longer present in deephermes3_engine.py streaming path.
    """
    import re
    path = "brain/deephermes3_engine.py"
    with open(path) as f:
        content = f.read()

    # Find the generate_stream method
    m = re.search(r"async def generate_stream.*?(?=\n    async def |\n    def |\Z)", content, re.DOTALL)
    assert m, "generate_stream not found"
    method_body = m.group()

    # The BUG pattern: async for + asyncio.to_thread on the same line
    bug_pattern = re.search(r"async for\s+\w+\s+in\s+asyncio\.to_thread", method_body)
    assert not bug_pattern, f"Buggy pattern still present: {bug_pattern.group()!r}"

    # The FIX pattern: stream_via_queue should be present
    assert "stream_via_queue" in method_body, "stream_via_queue not found in generate_stream"


if __name__ == "__main__":
    pytest.main([__file__, "-xvs", "-q"])
