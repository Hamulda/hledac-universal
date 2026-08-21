"""
Sprint P3: Testy pro AIMDWindow.
Scope: coordinators/fetch_coordinator.AIMDWindow (Python fallback)
"""

from __future__ import annotations

import pytest

from hledac.universal.coordinators.fetch_coordinator import AIMDWindow


class TestAIMDWindow:
    """Testy AIMDWindow — thread-safe AIMD concurrency controller."""

    def test_initial_window(self) -> None:
        """Window starts at configured initial value."""
        w = AIMDWindow(initial=10.0)
        assert w.window == 10.0
        assert w.successes == 0
        assert w.failures == 0

    @pytest.mark.asyncio
    async def test_on_success_increases_window_at_threshold(self) -> None:
        """Window increases when successes reach AIMD_SUCCESS_THRESHOLD."""
        w = AIMDWindow(initial=10.0)
        # AIMD_SUCCESS_THRESHOLD = 2; call enough times to guarantee increase
        for _ in range(5):
            await w.on_success()
        # Window must have increased at least once
        assert w.window > 10.0

    @pytest.mark.asyncio
    async def test_on_failure_decreases_window(self) -> None:
        """Window decreases multiplicatively on failure."""
        w = AIMDWindow(initial=10.0)
        await w.on_failure(uma_state="warn")  # decrease factor 0.5
        assert w.window == 5.0

    @pytest.mark.asyncio
    async def test_set_window_direct(self) -> None:
        """set_window() clamps window for backpressure."""
        w = AIMDWindow(initial=10.0)
        await w.set_window(3.0)
        assert w.window == 3.0

    def test_stats_tracked(self) -> None:
        """Stats dict tracks increases/decreases/changes."""
        w = AIMDWindow(initial=10.0)
        assert w.stats["increases"] == 0
        assert w.stats["decreases"] == 0
        assert w.stats["window_changes"] == 0
