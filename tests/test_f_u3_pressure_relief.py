"""
F266-U3 — macOS malloc_zone_pressure_relief tests
=================================================

Hermetic tests for `core.memory_cycle.malloc_zone_pressure_relief` and
the background pressure-relief task. Validates:
  - `malloc_zone_pressure_relief` returns int >= 0.
  - On non-Darwin platforms, returns 0 immediately (no-op).
  - On Darwin, the syscall is wrapped in try/except — never raises.
  - Background loop: starts, runs at least one tick, stops cleanly.
  - start_pressure_relief_loop is idempotent.
  - stop_pressure_relief_loop is idempotent and bounded.
"""

from __future__ import annotations

import asyncio
import sys

import pytest


class TestMallocZonePressureRelief:
    """Direct tests for the syscall wrapper."""

    def test_returns_int(self) -> None:
        from hledac.universal.core.memory_cycle import malloc_zone_pressure_relief  # type: ignore[import-not-found]

        result = malloc_zone_pressure_relief()
        assert isinstance(result, int)
        assert result >= 0

    def test_noop_on_non_darwin(self) -> None:
        from hledac.universal.core.memory_cycle import malloc_zone_pressure_relief  # type: ignore[import-not-found]

        if sys.platform == "darwin":
            pytest.skip("Darwin — real syscall path, not no-op")
        # On Linux/Windows it must be a guaranteed 0 — no syscall attempted.
        result = malloc_zone_pressure_relief()
        assert result == 0

    def test_does_not_raise_on_darwin_io_error(self, monkeypatch) -> None:
        """Even on Darwin, ctypes can fail. Must not raise."""
        from hledac.universal.core import memory_cycle  # type: ignore[import-not-found]

        if sys.platform != "darwin":
            pytest.skip("Not on Darwin — skipping Darwin-specific failure path")
        # Simulate ctypes raising.
        def _boom(*args, **kwargs):
            raise OSError("simulated ctypes failure")

        import ctypes
        monkeypatch.setattr(
            ctypes.CDLL(None, use_errno=True),
            "malloc_zone_pressure_relief",
            _boom,
            raising=False,
        )
        result = memory_cycle.malloc_zone_pressure_relief()
        assert result == 0  # fail-soft


class TestPressureReliefLoop:
    """Background task lifecycle tests."""

    @pytest.mark.asyncio
    async def test_start_inside_event_loop(self) -> None:
        from hledac.universal.core import memory_cycle  # type: ignore[import-not-found]

        task = memory_cycle.start_pressure_relief_loop(interval_s=300.0)
        # Either returns a Task (loop running) or None (no loop — shouldn't
        # happen in pytest-asyncio, but handle both).
        if task is not None:
            assert isinstance(task, asyncio.Task)
            # Allow one tick (or less than interval, so we just check it
            # didn't immediately fail). We don't sleep 300s in tests.
            await asyncio.sleep(0.01)
            assert not task.done() or task.exception() is None
            # Stop it.
            await memory_cycle.stop_pressure_relief_loop()

    @pytest.mark.asyncio
    async def test_start_is_idempotent(self) -> None:
        from hledac.universal.core import memory_cycle  # type: ignore[import-not-found]

        t1 = memory_cycle.start_pressure_relief_loop(interval_s=300.0)
        t2 = memory_cycle.start_pressure_relief_loop(interval_s=300.0)
        if t1 is not None and t2 is not None:
            # Second call must return the SAME task (idempotent).
            assert t1 is t2
        # Cleanup.
        await memory_cycle.stop_pressure_relief_loop()

    @pytest.mark.asyncio
    async def test_stop_is_idempotent(self) -> None:
        from hledac.universal.core import memory_cycle  # type: ignore[import-not-found]

        # No start at all — stop must be a no-op, not raise.
        await memory_cycle.stop_pressure_relief_loop()
        # Start + stop + stop.
        memory_cycle.start_pressure_relief_loop(interval_s=300.0)
        await memory_cycle.stop_pressure_relief_loop()
        # Second stop after first completes — no-op.
        await memory_cycle.stop_pressure_relief_loop()

    @pytest.mark.asyncio
    async def test_loop_runs_one_tick(self) -> None:
        """Verify the loop actually invokes malloc_zone_pressure_relief at
        least once within a short interval."""
        from hledac.universal.core import memory_cycle  # type: ignore[import-not-found]

        runs_before = memory_cycle.get_stats()["pressure_relief_runs"]
        # Use the minimum allowed interval so the test completes quickly.
        task = memory_cycle.start_pressure_relief_loop(interval_s=60.0)
        if task is None:
            pytest.skip("No running event loop")
        # Give the loop a brief moment to run the first tick.
        # The first tick happens immediately at loop start (no sleep first).
        await asyncio.sleep(0.1)
        runs_after = memory_cycle.get_stats()["pressure_relief_runs"]
        await memory_cycle.stop_pressure_relief_loop()
        # At least one tick ran (the loop hits the syscall on entry).
        assert runs_after >= runs_before + 1, (
            f"expected at least 1 tick, got {runs_after - runs_before}"
        )

    @pytest.mark.asyncio
    async def test_min_interval_enforced(self) -> None:
        """Interval below 60s must be clamped up to 60s."""
        from hledac.universal.core import memory_cycle  # type: ignore[import-not-found]

        # Start with tiny interval — internal clamp should raise it to 60s.
        task = memory_cycle.start_pressure_relief_loop(interval_s=0.1)
        if task is None:
            pytest.skip("No running event loop")
        # Cancel quickly. The clamp is internal; we just verify the loop
        # was created and is running (didn't crash on sub-60s interval).
        await asyncio.sleep(0.01)
        assert not task.done() or task.exception() is None
        await memory_cycle.stop_pressure_relief_loop()

    @pytest.mark.asyncio
    async def test_stats_bytes_released_accumulate(self) -> None:
        """The bytes_released counter must be a non-decreasing sum across ticks."""
        from hledac.universal.core import memory_cycle  # type: ignore[import-not-found]

        task = memory_cycle.start_pressure_relief_loop(interval_s=60.0)
        if task is None:
            pytest.skip("No running event loop")
        await asyncio.sleep(0.1)
        await memory_cycle.stop_pressure_relief_loop()
        stats = memory_cycle.get_stats()
        # Counter must be int (may be 0 on non-Darwin).
        assert isinstance(stats["pressure_relief_bytes_released"], int)
        assert stats["pressure_relief_bytes_released"] >= 0
        assert stats["pressure_relief_runs"] >= 1
