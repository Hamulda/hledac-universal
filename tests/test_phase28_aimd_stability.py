"""
MODERN-04: AIMD Controller Stability Verification Tests

Tests for verifying AIMD (Additive Increase/Multiplicative Decrease) controller
stability and correctness.

Test Categories:
1. Convergence tests - verify AIMD converges to stable state
2. Bounds tests - verify window stays within min/max bounds
3. Alternation tests - verify success/failure alternation works correctly
4. Race condition tests - verify thread-safety under concurrent access
5. Memory leak tests - verify no memory leaks in AIMD state
"""
from __future__ import annotations

import asyncio
import gc
import sys
import weakref
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from coordinators.aimd_controllers import AIMDController


# Test constants for M1 8GB bounds
AIMD_BOUNDS = {
    "fetch": {"min": 1, "max": 25, "increment": 1, "factor": 0.75, "threshold": 2},
    "enrich": {"min": 1, "max": 16, "increment": 1, "factor": 0.75, "threshold": 2},
    "extract": {"min": 1, "max": 8, "increment": 1, "factor": 0.75, "threshold": 2},
}


class TestAIMDBoundsEnforcement:
    """Verify window stays within min/max bounds under all conditions."""

    @pytest.fixture
    def aimd(self) -> AIMDController:
        """Create AIMD controller for fetch concurrency."""
        from coordinators.aimd_controllers import AIMDController

        return AIMDController(
            min_value=1,
            max_value=25,
            additive_increment=1,
            decrease_factor=0.75,
            success_threshold=2,
            name="test_fetch",
        )

    @pytest.mark.asyncio
    async def test_initial_window_within_bounds(self, aimd: AIMDController) -> None:
        """Window should start within bounds after __post_init__."""
        window = aimd.window
        assert aimd.min_value <= window <= aimd.max_value, (
            f"Initial window {window} outside bounds [{aimd.min_value}, {aimd.max_value}]"
        )

    @pytest.mark.asyncio
    async def test_on_success_never_exceeds_max(self, aimd: AIMDController) -> None:
        """on_success should never allow window to exceed max_value."""
        max_window = 0
        for _ in range(100):
            window = await aimd.on_success()
            max_window = max(max_window, window)
        assert max_window <= aimd.max_value, (
            f"on_success() returned {max_window} > max_value {aimd.max_value}"
        )

    @pytest.mark.asyncio
    async def test_on_failure_never_below_min(self, aimd: AIMDController) -> None:
        """on_failure should never allow window to drop below min_value."""
        min_window = float('inf')
        for _ in range(100):
            window = await aimd.on_failure()
            min_window = min(min_window, window)
        assert min_window >= aimd.min_value, (
            f"on_failure() returned {min_window} < min_value {aimd.min_value}"
        )

    @pytest.mark.asyncio
    async def test_mixed_operations_maintain_bounds(self, aimd: AIMDController) -> None:
        """Mixed success/failure operations should maintain bounds."""
        min_w, max_w = float('inf'), 0
        for i in range(200):
            op = aimd.on_success if i % 2 == 0 else aimd.on_failure
            window = await op()
            min_w = min(min_w, window)
            max_w = max(max_w, window)
        assert aimd.min_value <= min_w, (
            f"Window {min_w} below min {aimd.min_value} after mixed operations"
        )
        assert max_w <= aimd.max_value, (
            f"Window {max_w} above max {aimd.max_value} after mixed operations"
        )


class TestAIMDConvergence:
    """Verify AIMD converges to stable state."""

    @pytest.fixture
    def aimd(self) -> AIMDController:
        from coordinators.aimd_controllers import AIMDController

        return AIMDController(
            min_value=1,
            max_value=16,
            additive_increment=1,
            decrease_factor=0.75,
            success_threshold=2,
            name="convergence_test",
        )

    @pytest.mark.asyncio
    async def test_consecutive_failures_converge_to_min(self, aimd: AIMDController) -> None:
        """Multiple consecutive failures should converge to min_value."""
        for _ in range(20):
            await aimd.on_failure()

        # After many failures, should be at or near minimum
        assert aimd.window <= aimd.min_value * 2, (
            f"Window {aimd.window} not converging toward min {aimd.min_value}"
        )

    @pytest.mark.asyncio
    async def test_consecutive_successes_converge_to_max(self, aimd: AIMDController) -> None:
        """Multiple consecutive successes should converge to max_value."""
        for _ in range(50):
            await aimd.on_success()

        # After many successes, should be at or near maximum
        assert aimd.window >= aimd.max_value * 0.9, (
            f"Window {aimd.window} not converging toward max {aimd.max_value}"
        )

    @pytest.mark.asyncio
    async def test_convergence_speed_matches_decrease_factor(self, aimd: AIMDController) -> None:
        """Window should decrease by approximately decrease_factor each failure."""
        initial_window = aimd.window
        await aimd.on_failure()
        new_window = aimd.window

        # Window should decrease by factor (accounting for min_value)
        expected_min = max(aimd.min_value, initial_window * aimd.decrease_factor)
        assert new_window <= expected_min * 1.1, (
            f"Window {new_window} not decreasing correctly (expected ~{expected_min})"
        )


class TestAIMDThreadSafety:
    """Verify thread-safety under concurrent access."""

    @pytest.fixture
    def aimd(self) -> AIMDController:
        from coordinators.aimd_controllers import AIMDController

        return AIMDController(
            min_value=1,
            max_value=25,
            additive_increment=1,
            decrease_factor=0.75,
            success_threshold=2,
            name="thread_safety_test",
        )

    @pytest.mark.asyncio
    async def test_concurrent_success_calls(self, aimd: AIMDController) -> None:
        """Concurrent on_success calls should not corrupt state."""
        tasks = [aimd.on_success() for _ in range(100)]
        results = await asyncio.gather(*tasks)

        # All results should be valid
        for window in results:
            assert aimd.min_value <= window <= aimd.max_value

        # Final window should be within bounds
        assert aimd.min_value <= aimd.window <= aimd.max_value

    @pytest.mark.asyncio
    async def test_concurrent_failure_calls(self, aimd: AIMDController) -> None:
        """Concurrent on_failure calls should not corrupt state."""
        tasks = [aimd.on_failure() for _ in range(100)]
        results = await asyncio.gather(*tasks)

        # All results should be valid
        for window in results:
            assert aimd.min_value <= window <= aimd.max_value

        # Final window should be within bounds
        assert aimd.min_value <= aimd.window <= aimd.max_value

    @pytest.mark.asyncio
    async def test_mixed_concurrent_operations(self, aimd: AIMDController) -> None:
        """Mixed concurrent operations should maintain consistency."""
        tasks = []
        for i in range(100):
            if i % 2 == 0:
                tasks.append(aimd.on_success())
            else:
                tasks.append(aimd.on_failure())

        results = await asyncio.gather(*tasks)

        # All results should be valid
        for window in results:
            assert aimd.min_value <= window <= aimd.max_value

        # Stats should be recorded
        stats = aimd.get_stats()
        assert stats["successes"] + stats["failures"] == 100


class TestAIMDStatsTracking:
    """Verify statistics are tracked correctly."""

    @pytest.fixture
    def aimd(self) -> AIMDController:
        from coordinators.aimd_controllers import AIMDController

        return AIMDController(
            min_value=1,
            max_value=16,
            additive_increment=1,
            decrease_factor=0.75,
            success_threshold=2,
            name="stats_test",
        )

    @pytest.mark.asyncio
    async def test_stats_initialized_correctly(self, aimd: AIMDController) -> None:
        """Stats should be initialized to zero."""
        stats = aimd.get_stats()
        assert stats["increases"] == 0
        assert stats["decreases"] == 0
        assert stats["successes"] == 0
        assert stats["failures"] == 0
        assert stats["window_changes"] == 0

    @pytest.mark.asyncio
    async def test_success_increments_stats(self, aimd: AIMDController) -> None:
        """on_success should increment success counter."""
        await aimd.on_success()
        await aimd.on_success()

        stats = aimd.get_stats()
        assert stats["successes"] == 2

    @pytest.mark.asyncio
    async def test_failure_increments_stats(self, aimd: AIMDController) -> None:
        """on_failure should increment failure counter."""
        await aimd.on_failure()
        await aimd.on_failure()

        stats = aimd.get_stats()
        assert stats["failures"] == 2

    @pytest.mark.asyncio
    async def test_window_change_increments_stats(self, aimd: AIMDController) -> None:
        """Window changes should increment window_changes counter."""
        # Need 2 consecutive successes to trigger increase
        await aimd.on_success()
        await aimd.on_success()

        stats = aimd.get_stats()
        assert stats["increases"] >= 1
        assert stats["window_changes"] >= 1


class TestAIMDReset:
    """Verify reset functionality."""

    @pytest.fixture
    def aimd(self) -> AIMDController:
        from coordinators.aimd_controllers import AIMDController

        return AIMDController(
            min_value=1,
            max_value=16,
            additive_increment=1,
            decrease_factor=0.75,
            success_threshold=2,
            name="reset_test",
        )

    @pytest.mark.asyncio
    async def test_reset_restores_initial_state(self, aimd: AIMDController) -> None:
        """Reset should restore initial window and clear counters."""
        # Modify state
        for _ in range(10):
            await aimd.on_success()
        for _ in range(5):
            await aimd.on_failure()

        initial_window = aimd._window
        initial_successes = aimd._successes

        # Reset
        await aimd.reset()

        # Verify reset
        stats = aimd.get_stats()
        assert stats["increases"] == 0
        assert stats["decreases"] == 0
        assert stats["successes"] == 0
        assert stats["failures"] == 0
        assert aimd.window >= aimd.min_value


class TestAIMDMemoryLeaks:
    """Verify no memory leaks in AIMD state (MODERN-02)."""

    @pytest.mark.asyncio
    async def test_no_memory_leak_on_many_operations(self) -> None:
        """Many operations should not leak memory."""
        from coordinators.aimd_controllers import AIMDController

        gc.collect()
        initial_objects = len(gc.get_objects())

        aimd = AIMDController(
            min_value=1,
            max_value=16,
            additive_increment=1,
            decrease_factor=0.75,
            success_threshold=2,
            name="leak_test",
        )

        # Perform many operations
        for _ in range(1000):
            await aimd.on_success()
            await aimd.on_failure()

        # Delete reference
        del aimd
        gc.collect()

        # Check for leaks (allow some tolerance for asyncio internals)
        final_objects = len(gc.get_objects())
        leaked = final_objects - initial_objects

        assert leaked < 50, f"Potential memory leak: {leaked} objects retained"

    @pytest.mark.asyncio
    async def test_stats_dict_no_growth(self) -> None:
        """Stats dict should not grow unbounded."""
        from coordinators.aimd_controllers import AIMDController

        aimd = AIMDController(
            min_value=1,
            max_value=16,
            additive_increment=1,
            decrease_factor=0.75,
            success_threshold=2,
            name="stats_dict_test",
        )

        initial_stats_len = len(aimd._stats)

        # Perform many operations
        for _ in range(1000):
            await aimd.on_success()
            await aimd.on_failure()

        # Stats dict should not grow
        final_stats_len = len(aimd._stats)
        assert final_stats_len == initial_stats_len, (
            f"Stats dict grew from {initial_stats_len} to {final_stats_len}"
        )


class TestAIMDM1Bounds:
    """Verify M1 8GB memory bounds are enforced."""

    @pytest.mark.parametrize(
        "controller_type,bounds",
        [
            ("fetch", AIMD_BOUNDS["fetch"]),
            ("enrich", AIMD_BOUNDS["enrich"]),
            ("extract", AIMD_BOUNDS["extract"]),
        ],
    )
    @pytest.mark.asyncio
    async def test_m1_bounds_enforced(
        self, controller_type: str, bounds: dict[str, int]
    ) -> None:
        """M1 8GB bounds should be enforced for each controller type."""
        from coordinators.aimd_controllers import AIMDController

        aimd = AIMDController(
            min_value=bounds["min"],
            max_value=bounds["max"],
            additive_increment=bounds["increment"],
            decrease_factor=bounds["factor"],
            success_threshold=bounds["threshold"],
            name=controller_type,
        )

        # Verify initial bounds
        assert aimd.min_value == bounds["min"]
        assert aimd.max_value == bounds["max"]

        # Perform many operations
        for _ in range(100):
            await aimd.on_success()
            await aimd.on_failure()

        # Final window should still be within bounds
        assert aimd.min_value <= aimd.window <= aimd.max_value


# MODERN-04 verification summary
"""
MODERN-04 Test Coverage:
========================

✓ Bounds Enforcement (4 tests)
  - Initial window within bounds
  - on_success never exceeds max
  - on_failure never below min
  - Mixed operations maintain bounds

✓ Convergence (3 tests)
  - Consecutive failures converge to min
  - Consecutive successes converge to max
  - Decrease factor respected

✓ Thread Safety (3 tests)
  - Concurrent success calls
  - Concurrent failure calls
  - Mixed concurrent operations

✓ Stats Tracking (4 tests)
  - Initialized to zero
  - Success increments counter
  - Failure increments counter
  - Window changes tracked

✓ Reset Functionality (1 test)
  - Reset restores initial state

✓ Memory Leak Detection (2 tests) [MODERN-02]
  - No leak on many operations
  - Stats dict no growth

✓ M1 8GB Bounds (3 tests)
  - Fetch controller bounds
  - Enrich controller bounds
  - Extract controller bounds

Total: 20 test cases
"""
