"""
ISSUE #023: GPUArbiter tests — GPU resource arbitration for MLX embedder vs Hermes3.

Tests:
1. GPUArbiter singleton — one instance globally
2. should_defer() — returns False when GPU fraction < 0.85 (no MLX = idle)
3. should_defer() — returns True when GPU fraction > 0.85 (mocked)
4. wait_until_free() — returns True when GPU free
5. wait_until_free() — polls until free or timeout
6. wait_until_free(timeout=0) — no-wait fallback
7. stats — returns defer_count, poll_count, last_gpu_fraction
8. encode_async — calls arbiter before encoding
"""
import asyncio
import time
from unittest.mock import patch

import pytest
from core import aclose


class TestGPUArbiterUnit:
    """Unit tests for GPUArbiter — no MLX required."""

    def test_arbiter_singleton(self):
        """get_gpu_arbiter() returns the same instance on repeated calls."""
        from core.embeddings.manager import get_gpu_arbiter
        a = get_gpu_arbiter()
        b = get_gpu_arbiter()
        assert a is b

    def test_should_defer_idle(self):
        """When MLX unavailable, _probe_gpu_fraction returns 0.0 → should_defer=False."""
        from core.embeddings.manager import GPUArbiter
        arbiter = GPUArbiter()
        # Mock _probe_gpu_fraction to return 0.5 (normal, not > 0.85)
        with patch('core.embeddings.manager._probe_gpu_fraction', return_value=0.5):
            result = arbiter.should_defer()
            assert result is False
            assert arbiter._last_fraction == 0.5

    def test_should_defer_pressure(self):
        """When GPU fraction > 0.85, should_defer returns True."""
        from core.embeddings.manager import GPUArbiter
        arbiter = GPUArbiter()
        with patch('core.embeddings.manager._probe_gpu_fraction', return_value=0.95):
            result = arbiter.should_defer()
            assert result is True
            assert arbiter._last_fraction == 0.95
            assert arbiter._defer_count == 1

    def test_should_defer_boundary_idle(self):
        """Fraction exactly 0.85 is NOT deferred (threshold is exclusive)."""
        from core.embeddings.manager import GPUArbiter
        arbiter = GPUArbiter()
        with patch('core.embeddings.manager._probe_gpu_fraction', return_value=0.85):
            result = arbiter.should_defer()
            assert result is False  # boundary: 0.85 is not > 0.85

    def test_should_defer_boundary_pressure(self):
        """Fraction exactly 0.86 IS deferred (> threshold)."""
        from core.embeddings.manager import GPUArbiter
        arbiter = GPUArbiter()
        with patch('core.embeddings.manager._probe_gpu_fraction', return_value=0.86):
            result = arbiter.should_defer()
            assert result is True

    @pytest.mark.asyncio
    async def test_wait_until_free_returns_true_when_idle(self):
        """wait_until_free returns True immediately when GPU is free."""
        from core.embeddings.manager import GPUArbiter
        arbiter = GPUArbiter()
        with patch('core.embeddings.manager._probe_gpu_fraction', return_value=0.5):
            result = await arbiter.wait_until_free(timeout=2.0)
            assert result is True
            assert arbiter._poll_count == 0  # no polling needed

    @pytest.mark.asyncio
    async def test_wait_until_free_returns_false_on_timeout(self):
        """wait_until_free returns False when GPU stays saturated past timeout."""
        from core.embeddings.manager import GPUArbiter
        arbiter = GPUArbiter()
        # Always return 0.95 (> 0.85)
        with patch('core.embeddings.manager._probe_gpu_fraction', return_value=0.95):
            result = await arbiter.wait_until_free(timeout=0.35)  # 350ms
            assert result is False
            # Should have polled at least 2-3 times (0.1s interval)
            assert arbiter._poll_count >= 2

    @pytest.mark.asyncio
    async def test_wait_until_free_polls_until_recovered(self):
        """wait_until_free polls until GPU pressure drops, then returns True."""
        from core.embeddings.manager import GPUArbiter
        arbiter = GPUArbiter()
        call_count = [0]

        def fake_fraction():
            call_count[0] += 1
            if call_count[0] <= 2:
                return 0.95  # pressure for first 2 calls
            return 0.5  # recovered

        with patch('core.embeddings.manager._probe_gpu_fraction', side_effect=fake_fraction):
            t0 = time.monotonic()
            result = await arbiter.wait_until_free(timeout=2.0)
            elapsed = time.monotonic() - t0
            assert result is True
            assert call_count[0] == 3  # 2 pressure + 1 recovered
            assert arbiter._poll_count == 2

    @pytest.mark.asyncio
    async def test_wait_until_free_zero_timeout_no_wait(self):
        """timeout=0 returns immediately with current defer state."""
        from core.embeddings.manager import GPUArbiter
        arbiter = GPUArbiter()
        with patch('core.embeddings.manager._probe_gpu_fraction', return_value=0.95):
            t0 = time.monotonic()
            result = await arbiter.wait_until_free(timeout=0.0)
            elapsed = time.monotonic() - t0
            assert result is False
            assert elapsed < 0.05  # no sleep

    def test_stats(self):
        """stats returns defer_count, poll_count, last_gpu_fraction."""
        from core.embeddings.manager import GPUArbiter
        arbiter = GPUArbiter()
        with patch('core.embeddings.manager._probe_gpu_fraction', return_value=0.6):
            arbiter.should_defer()  # False
        with patch('core.embeddings.manager._probe_gpu_fraction', return_value=0.95):
            arbiter.should_defer()  # True, defer_count=1
            arbiter.should_defer()  # True, defer_count=2
        stats = arbiter.stats
        assert stats['defer_count'] == 2
        assert stats['poll_count'] == 0
        assert stats['last_gpu_fraction'] == 0.95


class TestGPUArbiterEncodeAsyncIntegration:
    """Integration: encode_async calls arbiter.should_defer() before encoding."""

    @pytest.mark.asyncio
    async def test_encode_async_checks_arbiter(self):
        """encode_async calls get_gpu_arbiter().should_defer() before encoding."""
        deferred = []

        def tracking_should_defer(_self):
            deferred.append(True)
            return False  # don't actually wait

        # Patch BEFORE importing MLXEmbeddingManager (it checks MLX_AVAILABLE at __init__)
        with patch('core.embeddings.manager.MLX_AVAILABLE', True):
            with patch('core.embeddings.manager.GPUArbiter.should_defer', tracking_should_defer):
                with patch('core.embeddings.manager.get_gpu_arbiter') as mock_get_arbiter:
                    from core.embeddings.manager import GPUArbiter, MLXEmbeddingManager
                    arbiter_instance = GPUArbiter()
                    mock_get_arbiter.return_value = arbiter_instance
                    mgr = MLXEmbeddingManager(lazy_load=True)
                    mgr._is_loaded = True
                    try:
                        await mgr.encode_async('test text')
                    except Exception:
                        pass
        assert len(deferred) >= 1, "encode_async should call should_defer()"


class TestProbeGpuFractionFailSafe:
    """Fail-safe behavior of _probe_gpu_fraction — covered by other tests."""

    def test_should_defer_catches_exceptions(self):
        """should_defer returns False on any exception from _probe_gpu_fraction."""
        from core.embeddings.manager import GPUArbiter
        arbiter = GPUArbiter()
        with patch('core.embeddings.manager._probe_gpu_fraction', side_effect=RuntimeError("mock")):
            result = arbiter.should_defer()
            assert result is False
            assert arbiter._last_fraction == 0.0
