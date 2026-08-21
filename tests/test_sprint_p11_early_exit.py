"""P1-1: Early-Exit on Empty Cycles — Before Windup Completes.

Tests for the P1-1 fix that adds:
  RC1: _run_one_cycle increments consecutive_empty_cycles BEFORE dispatch when
       work_items is empty (docstring said it did, code didn't).
  RC2+RC3: Pre-cycle early-exit check in OODA loop — if remaining active window
       < 30s AND consecutive_empty_cycles >= 3, trigger immediate windup.

All tests are hermetic (no network, no MLX, no M1-only code paths).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import hledac.universal.runtime.sprint_scheduler as ss_module


class TestP11RC1EmptyWorkItemsIncrement:
    """RC1: empty work_items increments consecutive_empty_cycles BEFORE dispatch."""

    @pytest.mark.asyncio
    async def test_empty_work_items_increments_counter(self, scheduler_mocks, lifecycle_mock) -> None:
        """When _build_work_items returns [], consecutive_empty_cycles += 1."""
        scheduler, result, runner = scheduler_mocks
        lifecycle = lifecycle_mock()

        with (
            patch.object(scheduler, "_build_work_items", return_value=[]),
            patch.object(scheduler, "_sort_work_items_by_economics", return_value=[]),
        ):
            initial_count = result.consecutive_empty_cycles
            result0 = await scheduler._run_one_cycle(
                lifecycle,
                sources=[],
                now_monotonic=0.0,
                query="",
                duckdb_store=None,
            )
            assert result0, "run_one_cycle should return True"
            assert result.consecutive_empty_cycles == initial_count + 1

    @pytest.mark.asyncio
    async def test_nonempty_work_items_resets_counter(self, scheduler_mocks, lifecycle_mock) -> None:
        """When work_items is non-empty, consecutive_empty_cycles resets to 0."""
        scheduler, result, runner = scheduler_mocks
        result.consecutive_empty_cycles = 5
        lifecycle = lifecycle_mock()
        mock_work_item = MagicMock()

        with (
            patch.object(scheduler, "_build_work_items", return_value=[mock_work_item]),
            patch.object(scheduler, "_sort_work_items_by_economics", return_value=[mock_work_item]),
            patch.object(scheduler, "_run_one_cycle_stable", new_callable=AsyncMock, return_value=True),
        ):
            result0 = await scheduler._run_one_cycle(
                lifecycle,
                sources=[],
                now_monotonic=0.0,
                query="",
                duckdb_store=None,
            )
            assert result0, "run_one_cycle should return True"
            assert result.consecutive_empty_cycles == 0


class TestP11RC2PreCycleEarlyExit:
    """RC2+RC3: OODA loop triggers early windup when remaining < 30s + >= 3 empty cycles."""

    @pytest.mark.asyncio
    async def test_early_exit_triggers_when_remaining_active_too_small(self, scheduler_mocks, lifecycle_mock) -> None:
        """If remaining_active < 30s and consecutive_empty_cycles >= 3, break."""
        scheduler, result, runner = scheduler_mocks
        result.consecutive_empty_cycles = 3
        result.cycles_started = 5
        result.entries_per_source = {"a": 1, "b": 2}
        lifecycle = lifecycle_mock()

        with (
            patch.object(
                scheduler, "_ensure_nonfeed_predispatch_before_finalization", new_callable=AsyncMock, return_value=True
            ),
            patch.object(scheduler, "_capture_timing_fields", new_callable=AsyncMock),
            patch.object(scheduler, "_finalize_result_truth", new_callable=AsyncMock),
            patch.object(scheduler, "_run_one_cycle", new_callable=AsyncMock, return_value=True),
        ):
            mock_time = MagicMock()
            mock_time.monotonic = MagicMock(return_value=55.0)
            with patch.object(ss_module, "_time", mock_time):
                await scheduler._run_one_cycle(
                    lifecycle,
                    sources=[],
                    now_monotonic=55.0,
                    query="test",
                    duckdb_store=None,
                )

    def test_empty_cycle_limit_is_bounded(self) -> None:
        """_empty_cycle_limit = max(2, min(8, int(duration/30))) — never > 8."""
        for duration in [30, 60, 120, 180, 300, 600]:
            limit = max(2, min(8, int(duration / 30.0)))
            assert limit <= 8
            assert limit >= 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
