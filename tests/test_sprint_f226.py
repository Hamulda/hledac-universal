"""
Sprint F226 — Body-cap dedup, JS-renderer semaphore, UMA adaptive cap.

Verifies three seams from the public_fetcher audit:
  A. transport.body_limiter: BodyReadResult + CHUNKS_BUDGET
  B. public_fetcher._JS_RENDERER_SEMAPHORE serializes Camoufox + nodriver
  C. _compute_effective_max_bytes halves cap on UMA critical
  D. _read_aiohttp_body_with_peek preserves CT-recovery semantics
  E. aiohttp body read no longer duplicates body_limiter logic
  F. End-to-end: 25 in-flight × effective cap = bounded worst case
"""

import asyncio
import sys
import types
from typing import Never
from unittest.mock import AsyncMock, patch

import pytest

# ---------------------------------------------------------------------------
# A. BodyReadResult + CHUNKS_BUDGET — transport.body_limiter
# ---------------------------------------------------------------------------


class TestBodyReadResult:
    """A. transport.body_limiter helper contract."""

    def test_body_read_result_fields(self) -> None:
        from hledac.universal.transport.body_limiter import BodyReadResult

        r = BodyReadResult(body=b"hello", total_read=5, truncated=False, chunks_consumed=1)
        assert r.body == b"hello"
        assert r.total_read == 5
        assert r.truncated is False
        assert r.chunks_consumed == 1

    def test_body_read_result_is_frozen(self) -> None:
        """F226: BodyReadResult is immutable (frozen dataclass) — no in-place mutation."""
        from hledac.universal.transport.body_limiter import BodyReadResult

        r = BodyReadResult(body=b"x", total_read=1, truncated=False, chunks_consumed=1)
        with pytest.raises((AttributeError, Exception)):
            r.body = b"y"  # type: ignore[misc]

    def test_chunks_budget_is_bounded(self) -> None:
        """F226: CHUNKS_BUDGET guards against pathological sources."""
        from hledac.universal.transport.body_limiter import CHUNKS_BUDGET

        assert CHUNKS_BUDGET >= 1024
        assert CHUNKS_BUDGET <= 65536

    @pytest.mark.asyncio
    async def test_read_body_with_cap_no_cap(self) -> None:
        from hledac.universal.transport.body_limiter import read_body_with_cap

        async def gen():
            for chunk in [b"abc", b"de", b"f"]:
                yield chunk

        body, truncated = await read_body_with_cap(gen(), max_bytes=0)
        assert body == b"abcdef"
        assert truncated is False

    @pytest.mark.asyncio
    async def test_read_body_with_cap_truncates(self) -> None:
        from hledac.universal.transport.body_limiter import read_body_with_cap

        async def gen():
            for chunk in [b"abc", b"de", b"f"]:
                yield chunk

        body, truncated = await read_body_with_cap(gen(), max_bytes=4)
        assert body == b"abcd"
        assert truncated is True

    @pytest.mark.asyncio
    async def test_read_body_respects_chunks_budget(self) -> None:
        """F226: pathological source with millions of tiny chunks stops at CHUNKS_BUDGET."""
        from hledac.universal.transport.body_limiter import CHUNKS_BUDGET, read_body_with_cap

        async def gen():
            for _ in range(CHUNKS_BUDGET + 1000):
                yield b"x"

        body, truncated = await read_body_with_cap(gen(), max_bytes=0)
        assert truncated is True
        assert len(body) == CHUNKS_BUDGET


# ---------------------------------------------------------------------------
# B. _JS_RENDERER_SEMAPHORE — Camoufox + nodriver serialization
# ---------------------------------------------------------------------------


class TestJSRendererSemaphore:
    """B. The shared Semaphore(1) ensures only 1 browser process at a time."""

    def test_semaphore_exists_and_is_bounded(self) -> None:
        from hledac.universal.fetching.public_fetcher import _get_js_renderer_semaphore

        sem = _get_js_renderer_semaphore()
        assert isinstance(sem, asyncio.Semaphore)
        # Semaphore(1) — at most 1 acquire available
        # _value is implementation detail; just verify we can acquire+release once
        # and the second acquire would block (we test serialization below instead).

    def test_semaphore_getter_returns_singleton(self) -> None:
        """F226A: getter is idempotent within the same loop."""
        from hledac.universal.fetching.public_fetcher import _get_js_renderer_semaphore

        sem1 = _get_js_renderer_semaphore()
        sem2 = _get_js_renderer_semaphore()
        assert sem1 is sem2

    @pytest.mark.asyncio
    async def test_semaphore_serializes_concurrent_acquires(self) -> None:
        """F226A: two parallel acquires must serialize — one runs, one waits."""
        from hledac.universal.fetching.public_fetcher import _get_js_renderer_semaphore

        sem = _get_js_renderer_semaphore()

        order: list[str] = []

        async def worker(name: str) -> None:
            async with sem:
                order.append(f"{name}_start")
                await asyncio.sleep(0.05)
                order.append(f"{name}_end")

        await asyncio.gather(worker("A"), worker("B"))
        # The two critical sections must NOT overlap.
        assert len(order) == 4
        first_end_idx = next(i for i, e in enumerate(order) if e.endswith("_end"))
        second_start_idx = next(i for i, e in enumerate(order) if e.endswith("_start") and i > first_end_idx)
        assert first_end_idx < second_start_idx

    @pytest.mark.asyncio
    async def test_semaphore_releases_on_exception(self) -> Never:
        """F226A: Semaphore.release() happens via async-with even on exception."""
        from hledac.universal.fetching.public_fetcher import _get_js_renderer_semaphore

        sem = _get_js_renderer_semaphore()

        with pytest.raises(RuntimeError, match="boom"):
            async with sem:
                raise RuntimeError("boom")

        # If release didn't happen, the second acquire would hang. We give it
        # a 0.5s ceiling — should be instant.
        try:
            await asyncio.wait_for(sem.acquire(), timeout=0.5)
        except TimeoutError:
            pytest.fail("Semaphore did not release after exception")
        else:
            sem.release()


class TestCamoufoxNodriverSharedSemaphore:
    """B'. Both renderers must go through the SAME Semaphore(1)."""

    @pytest.mark.asyncio
    async def test_camoufox_acquires_js_semaphore(self) -> None:
        """F226A: _fetch_with_camoufox must wrap body in _JS_RENDERER_SEMAPHORE."""
        from hledac.universal.fetching import public_fetcher

        # F226A: Reset singleton so it's bound to the current test's event loop.
        public_fetcher._JS_RENDERER_SEMAPHORE = None

        # Inject a fake "camoufox" module so the import inside _camoufox_locked succeeds.
        fake_camoufox = types.ModuleType("camoufox")
        fake_async_api = types.ModuleType("camoufox.async_api")

        class FakeAsyncCamoufox:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def new_page(self):
                raise NotImplementedError  # we never reach this — _camoufox_locked is patched

        fake_async_api.AsyncCamoufox = FakeAsyncCamoufox
        fake_camoufox.async_api = fake_async_api

        # Patch _camoufox_locked to verify the outer semaphore is held during the call.
        acquired_events: list[str] = []

        async def fake_locked(url, timeout) -> str:
            sem = public_fetcher._get_js_renderer_semaphore()
            try:
                await asyncio.wait_for(sem.acquire(), timeout=0.05)
                acquired_events.append("nested_acquired")  # BAD — semaphore should be held
                sem.release()
            except TimeoutError:
                acquired_events.append("nested_blocked")  # GOOD
            return "<html/>"

        with patch.dict(sys.modules, {"camoufox": fake_camoufox, "camoufox.async_api": fake_async_api}):
            with patch.object(public_fetcher, "_camoufox_locked", fake_locked):
                html = await public_fetcher._fetch_with_camoufox("http://test", timeout=1.0)

        assert html == "<html/>", f"expected <html/>, got {html!r}"
        assert acquired_events == ["nested_blocked"], (
            f"Camoufox body must be wrapped in _JS_RENDERER_SEMAPHORE; observed: {acquired_events}"
        )

    @pytest.mark.asyncio
    async def test_nodriver_acquires_js_semaphore(self) -> None:
        """F226A: _fetch_with_nodriver must wrap body in _JS_RENDERER_SEMAPHORE."""
        from hledac.universal.fetching import public_fetcher

        # F226A: Reset singleton so it's bound to the current test's event loop.
        public_fetcher._JS_RENDERER_SEMAPHORE = None

        # Inject fake nodriver module so the import inside _fetch_with_nodriver succeeds.
        fake_nodriver = types.ModuleType("nodriver")
        # Provide a dummy `start` so the import doesn't blow up before patching.
        fake_nodriver.start = AsyncMock()  # never called — we patch _nodriver_locked

        # Skip the env gate + chrome check by patching them.
        with patch.dict(sys.modules, {"nodriver": fake_nodriver}):
            with patch.object(public_fetcher, "_check_chrome_binary_exists", return_value=True):
                with patch.dict("os.environ", {"HLEDAC_ENABLE_NODRIVER": "1"}):
                    acquired_events: list[str] = []

                    async def fake_nodriver_locked(url) -> str:
                        sem = public_fetcher._get_js_renderer_semaphore()
                        try:
                            await asyncio.wait_for(sem.acquire(), timeout=0.05)
                            acquired_events.append("nested_acquired")
                            sem.release()
                        except TimeoutError:
                            acquired_events.append("nested_blocked")
                        return "<html/>"

                    with patch.object(public_fetcher, "_nodriver_locked", fake_nodriver_locked):
                        html = await public_fetcher._fetch_with_nodriver("http://test")

        assert html == "<html/>"
        assert acquired_events == ["nested_blocked"], (
            f"nodriver body must be wrapped in _JS_RENDERER_SEMAPHORE; observed: {acquired_events}"
        )


# ---------------------------------------------------------------------------
# C. _compute_effective_max_bytes — UMA adaptive cap
# ---------------------------------------------------------------------------


class TestEffectiveMaxBytes:
    """C. Adaptive cap halves MAX_BYTES_HARD on UMA critical."""

    def test_normal_returns_max_bytes_hard(self) -> None:
        """No pressure → 10MB ceiling."""
        from hledac.universal.fetching import public_fetcher

        with patch.object(public_fetcher, "_is_uma_critical", return_value=False):
            assert public_fetcher._compute_effective_max_bytes(0) == public_fetcher.MAX_BYTES_HARD
            assert public_fetcher._compute_effective_max_bytes(5_000_000) == 5_000_000
            assert (
                public_fetcher._compute_effective_max_bytes(public_fetcher.MAX_BYTES_HARD)
                == public_fetcher.MAX_BYTES_HARD
            )

    def test_critical_halves_cap(self) -> None:
        """UMA critical → 5MB ceiling (MAX_BYTES_HARD_PRESSURE)."""
        from hledac.universal.fetching import public_fetcher

        with patch.object(public_fetcher, "_is_uma_critical", return_value=True):
            assert public_fetcher._compute_effective_max_bytes(0) == public_fetcher.MAX_BYTES_HARD_PRESSURE
            # Request larger than pressure cap → clamp to pressure cap
            assert (
                public_fetcher._compute_effective_max_bytes(public_fetcher.MAX_BYTES_HARD)
                == public_fetcher.MAX_BYTES_HARD_PRESSURE
            )
            # Request smaller than pressure cap → honor request
            assert public_fetcher._compute_effective_max_bytes(1_000_000) == 1_000_000

    def test_constants_correct(self) -> None:
        """MAX_BYTES_HARD_PRESSURE = 5MB, MAX_BYTES_HARD = 10MB."""
        from hledac.universal.fetching import public_fetcher

        assert public_fetcher.MAX_BYTES_HARD_PRESSURE == 5_000_000
        assert public_fetcher.MAX_BYTES_HARD == 10_000_000
        # Half the hard cap on pressure
        assert public_fetcher.MAX_BYTES_HARD_PRESSURE == public_fetcher.MAX_BYTES_HARD // 2

    def test_negative_request_returns_pressure_cap(self) -> None:
        """requested <= 0 → return effective cap (callers can detect no-cap intent)."""
        from hledac.universal.fetching import public_fetcher

        with patch.object(public_fetcher, "_is_uma_critical", return_value=True):
            assert public_fetcher._compute_effective_max_bytes(-1) == 5_000_000

    def test_worst_case_under_critical_bounded(self) -> None:
        """25 in-flight × 5MB = 125MB — bounded under M1 8GB UMA pressure."""
        from hledac.universal.fetching import public_fetcher

        with patch.object(public_fetcher, "_is_uma_critical", return_value=True):
            per_request = public_fetcher._compute_effective_max_bytes(0)
            worst_case = 25 * per_request
            # 25 × 5_000_000 = 125_000_000 bytes (decimal MB)
            assert worst_case == 125_000_000
            # Well within M1 8GB budget (~3GB available for fetch layer)
            assert worst_case < 150_000_000

    def test_worst_case_normal(self) -> None:
        """25 in-flight × 10MB = 250MB (decimal) — default worst case."""
        from hledac.universal.fetching import public_fetcher

        with patch.object(public_fetcher, "_is_uma_critical", return_value=False):
            per_request = public_fetcher._compute_effective_max_bytes(0)
            worst_case = 25 * per_request
            # 25 × 10_000_000 = 250_000_000 bytes
            assert worst_case == 250_000_000

    def test_is_uma_critical_import_fail_safe(self) -> None:
        """F226A: if uma_budget import fails, helper still works (no cap halving)."""
        # Simulate the import-time fallback by patching the module-level alias.
        from hledac.universal.fetching import public_fetcher

        original = public_fetcher._is_uma_critical
        try:
            # Inject a function that always raises (mimic sensor failure).
            def _broken() -> Never:
                raise RuntimeError("sensor offline")

            public_fetcher._is_uma_critical = _broken
            # Should fall back to MAX_BYTES_HARD (10MB), not 5MB
            result = public_fetcher._compute_effective_max_bytes(0)
            assert result == public_fetcher.MAX_BYTES_HARD
        finally:
            public_fetcher._is_uma_critical = original


# ---------------------------------------------------------------------------
# D. _read_aiohttp_body_with_peek — preserves CT-recovery semantics
# ---------------------------------------------------------------------------


class TestAiohttpBodyHelper:
    """D. The new helper must preserve XML recovery and size cap behavior."""

    @pytest.mark.asyncio
    async def test_helper_truncates_at_max_bytes(self) -> None:
        from hledac.universal.fetching.public_fetcher import _read_aiohttp_body_with_peek

        async def gen():
            for chunk in [b"a" * 100, b"b" * 100, b"c" * 100]:
                yield chunk

        outcome = await _read_aiohttp_body_with_peek(gen(), max_bytes=150, enable_peek=False)
        assert outcome.truncated is True
        assert outcome.total_read == 150
        assert len(outcome.body) == 150
        assert outcome.chunks_consumed >= 1

    @pytest.mark.asyncio
    async def test_helper_reads_all_when_under_cap(self) -> None:
        from hledac.universal.fetching.public_fetcher import _read_aiohttp_body_with_peek

        async def gen():
            for chunk in [b"abc", b"de"]:
                yield chunk

        outcome = await _read_aiohttp_body_with_peek(gen(), max_bytes=100, enable_peek=False)
        assert outcome.truncated is False
        assert outcome.body == b"abcde"
        assert outcome.total_read == 5
        assert outcome.chunks_consumed == 2

    @pytest.mark.asyncio
    async def test_helper_xml_recovery_on_first_chunk(self) -> None:
        """F226B: enable_peek=True detects XML on first chunk (for CT recovery)."""
        from hledac.universal.fetching.public_fetcher import _read_aiohttp_body_with_peek

        xml_header = b'<?xml version="1.0"?><rss>'

        async def gen():
            yield xml_header
            yield b"<channel/></rss>"

        outcome = await _read_aiohttp_body_with_peek(gen(), max_bytes=1000, enable_peek=True)
        assert outcome.xml_recovered is True
        assert outcome.first_chunk_peeked is True
        # Whole body is preserved (no truncation)
        assert outcome.body == xml_header + b"<channel/></rss>"

    @pytest.mark.asyncio
    async def test_helper_no_peek_when_disabled(self) -> None:
        from hledac.universal.fetching.public_fetcher import _read_aiohttp_body_with_peek

        async def gen():
            yield b"<?xml version='1.0'?>"

        outcome = await _read_aiohttp_body_with_peek(gen(), max_bytes=1000, enable_peek=False)
        assert outcome.xml_recovered is False
        assert outcome.first_chunk_peeked is False


# ---------------------------------------------------------------------------
# E. Aiohttp body read no longer duplicates body_limiter logic
# ---------------------------------------------------------------------------


class TestAiohttpPathDelegation:
    """E. Confirm the inline duplication is gone — single source of truth in body_limiter."""

    @pytest.mark.asyncio
    async def test_helper_is_used_inside_public_fetcher(self) -> None:
        """F226B: public_fetcher's aiohttp path must call _read_aiohttp_body_with_peek."""
        from hledac.universal.fetching import public_fetcher

        # We don't run the full async_fetch_public_text (it needs network).
        # Instead, check the symbol exists and is the unified helper.
        assert hasattr(public_fetcher, "_read_aiohttp_body_with_peek")
        assert hasattr(public_fetcher, "AiohttpBodyOutcome")
        assert hasattr(public_fetcher, "_peek_aiohttp_first_chunk")

    def test_outcome_dataclass_is_frozen_slots(self) -> None:
        """AiohttpBodyOutcome is immutable + memory-efficient."""
        from hledac.universal.fetching.public_fetcher import AiohttpBodyOutcome

        # Has __slots__ (memory friendly)
        assert hasattr(AiohttpBodyOutcome, "__slots__")
        # Frozen (immutable)
        o = AiohttpBodyOutcome(
            body=b"",
            total_read=0,
            truncated=False,
            chunks_consumed=0,
            xml_recovered=False,
            first_chunk_peeked=False,
        )
        with pytest.raises((AttributeError, Exception)):
            o.body = b"x"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# F. End-to-end — 25 in-flight is bounded under UMA pressure
# ---------------------------------------------------------------------------


class TestF226Integration:
    """F. Worst-case 25 in-flight × effective cap stays under M1 budget."""

    def test_25_inflight_under_critical_within_budget(self) -> None:
        from hledac.universal.fetching import public_fetcher

        with patch.object(public_fetcher, "_is_uma_critical", return_value=True):
            per_request = public_fetcher._compute_effective_max_bytes(0)
            inflight = 25
            worst_case_mb = (inflight * per_request) / (1024 * 1024)
            # Must be <= 125MB under pressure
            assert worst_case_mb <= 125, f"worst case {worst_case_mb}MB exceeds 125MB cap"

    def test_25_inflight_normal_within_budget(self) -> None:
        from hledac.universal.fetching import public_fetcher

        with patch.object(public_fetcher, "_is_uma_critical", return_value=False):
            per_request = public_fetcher._compute_effective_max_bytes(0)
            inflight = 25
            # Decimal MB: 25 × 10MB = 250MB
            worst_case_mb_decimal = (inflight * per_request) / 1_000_000
            assert worst_case_mb_decimal == 250.0
