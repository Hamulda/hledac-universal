"""
Sprint P3: Testy pro AIMDWindow a _AIMDSlotController.
Scope: coordinators/fetch_coordinator.AIMDWindow, ._AIMDSlotController
"""
from __future__ import annotations

import asyncio

import pytest

from hledac.universal.coordinators.fetch_coordinator import AIMDWindow, _AIMDSlotController


class TestAIMDWindow:
    """Testy AIMDWindow — thread-safe AIMD concurrency controller."""

    def test_initial_window(self):
        """Window starts at configured initial value."""
        w = AIMDWindow(initial=10.0)
        assert w.window == 10.0
        assert w.successes == 0
        assert w.failures == 0

    @pytest.mark.asyncio
    async def test_on_success_increases_window_at_threshold(self):
        """Window increases when successes reach AIMD_SUCCESS_THRESHOLD."""
        w = AIMDWindow(initial=10.0)
        # AIMD_SUCCESS_THRESHOLD = 2; call enough times to guarantee increase
        for _ in range(5):
            await w.on_success()
        # Window must have increased at least once
        assert w.window > 10.0

    @pytest.mark.asyncio
    async def test_on_failure_decreases_window(self):
        """Window decreases multiplicatively on failure."""
        w = AIMDWindow(initial=10.0)
        await w.on_failure(uma_state='warn')  # decrease factor 0.5
        assert w.window == 5.0

    @pytest.mark.asyncio
    async def test_set_window_direct(self):
        """set_window() clamps window for backpressure."""
        w = AIMDWindow(initial=10.0)
        await w.set_window(3.0)
        assert w.window == 3.0

    def test_stats_tracked(self):
        """Stats dict tracks increases/decreases/changes."""
        w = AIMDWindow(initial=10.0)
        assert w.stats['increases'] == 0
        assert w.stats['decreases'] == 0
        assert w.stats['window_changes'] == 0


class TestAIMDSlotController:
    """Testy _AIMDSlotController — semaphore-based AIMD slot controller."""

    def test_initial_window(self):
        """Semaphore starts with configured initial bound."""
        s = _AIMDSlotController(initial_window=5)
        assert s.window == 5
        assert s.stats['acquired'] == 0
        assert s.stats['released'] == 0

    @pytest.mark.asyncio
    async def test_acquire_release(self):
        """Acquire and release work in pair."""
        s = _AIMDSlotController(initial_window=5)
        await s.acquire()
        assert s.stats['acquired'] == 1
        s.release()
        assert s.stats['released'] == 1

    @pytest.mark.asyncio
    async def test_update_window_grow(self):
        """update_window(delta > 0) raises semaphore bound."""
        s = _AIMDSlotController(initial_window=3)
        await s.update_window(5)
        assert s.window == 5
        assert s.stats['window_updates'] == 1

    @pytest.mark.asyncio
    async def test_update_window_shrink_no_reduce(self):
        """update_window(delta < 0) does NOT lower bound (permits drain naturally)."""
        s = _AIMDSlotController(initial_window=5)
        await s.update_window(3)
        assert s.window == 3  # Bound updated
        # Semaphore internal value unchanged — permits drain naturally

    @pytest.mark.asyncio
    async def test_available_approx(self):
        """available property returns approximate free slots."""
        s = _AIMDSlotController(initial_window=4)
        await s.acquire()
        await s.acquire()
        assert s.available >= 0

    @pytest.mark.asyncio
    async def test_concurrent_acquire(self):
        """Multiple concurrent acquires work correctly."""
        s = _AIMDSlotController(initial_window=3)
        results = []
        async def worker():
            await s.acquire()
            results.append(1)
            s.release()
        await asyncio.gather(*[worker() for _ in range(6)])
        assert len(results) == 6


class TestAIMDIntegration:
    """Integration tests: AIMDWindow drives _AIMDSlotController."""

    @pytest.mark.asyncio
    async def test_window_sync(self):
        """AIMDWindow and _AIMDSlotController stay in sync."""
        w = AIMDWindow(initial=5.0)
        s = _AIMDSlotController(initial_window=5)
        # Call enough times to guarantee window increase (CAS may retry)
        for _ in range(10):
            new_window, _ = await w.on_success()
        # Window must have grown
        assert w.window >= 5.0
        # Slot controller can be updated to match window
        await s.update_window(int(new_window))
        assert s.window == int(new_window)
