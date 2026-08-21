"""
Sprint F265G: Recursion guard for SprintScheduler.

The guard prevents infinite recursion via self-calls inside run():
  - _run_doh_prelude_lane (DoH pre-dispatch)
  - _check_prewindup_barrier_sync (pre-windup barrier callback)
  - async_run_tiered_feed_sprint_once (tiered feed sub-sprint)

MAX depth = 3 — consistent with 3 internal self-calls. Exceeding depth
raises RecursionError so a sub-sprint bug surfaces immediately instead of
silently spinning the M1 into a thermal throttling death spiral.

Tests are hermetic: they bypass the full sprint pipeline by stubbing
_run_internal() so the guard logic is exercised in isolation.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# ── Helpers ──────────────────────────────────────────────────────────────────


def _import_scheduler() -> Any:
    """Lazy import — sprint_scheduler has heavy module-level cost."""
    from hledac.universal.runtime.sprint_scheduler import (
        SprintScheduler,
        SprintSchedulerConfig,
    )

    return SprintScheduler, SprintSchedulerConfig


def _make_minimal_scheduler() -> Any:
    """SprintScheduler with __init__ skipped to avoid 200+ dep init."""
    SprintScheduler, SprintSchedulerConfig = _import_scheduler()
    config = SprintSchedulerConfig(
        sprint_duration_s=60.0,
        cycle_sleep_s=10.0,
    )
    # Bypass __init__ — only depth-related attrs matter for guard tests
    sched = SprintScheduler.__new__(SprintScheduler)
    sched._config = config
    sched._sprint_depth = 0
    sched._result = MagicMock()
    return sched


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sprint_depth_increments_on_run() -> None:
    """Mocked run() must observe depth=1 on first invocation."""
    sched = _make_minimal_scheduler()
    assert sched._sprint_depth == 0

    # Stub _run_internal to a no-op coroutine
    sched._run_internal = AsyncMock(return_value=sched._result)  # type: ignore[method-assign]

    result = await sched.run(
        lifecycle=MagicMock(),
        sources=[],
        query="test",
    )
    assert result is sched._result
    # Depth must have been incremented to 1 during the call, then decremented back
    assert sched._sprint_depth == 0
    # And _run_internal must have been awaited exactly once
    sched._run_internal.assert_awaited_once()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_sprint_depth_raises_at_limit() -> None:
    """depth=4 must raise RecursionError BEFORE calling _run_internal."""
    sched = _make_minimal_scheduler()
    sched._sprint_depth = 4  # Already at limit when run() is entered
    sched._run_internal = AsyncMock(return_value=sched._result)  # type: ignore[method-assign]

    with pytest.raises(RecursionError, match="recursion depth exceeded"):
        await sched.run(
            lifecycle=MagicMock(),
            sources=[],
            query="test",
        )

    # _run_internal must NOT have been called — guard fires first
    sched._run_internal.assert_not_awaited()  # type: ignore[attr-defined]
    # And depth must be restored to its pre-call value (no leak on raise)
    assert sched._sprint_depth == 4


@pytest.mark.asyncio
async def test_sprint_depth_decrements_in_finally() -> None:
    """Exception in _run_internal must still decrement depth back to 0."""
    sched = _make_minimal_scheduler()
    assert sched._sprint_depth == 0

    # Stub _run_internal to raise — finally must restore depth
    sched._run_internal = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="boom"):
        await sched.run(
            lifecycle=MagicMock(),
            sources=[],
            query="test",
        )

    # Critical: depth must NOT leak across the exception
    assert sched._sprint_depth == 0


@pytest.mark.asyncio
async def test_sprint_depth_decrements_on_recursion_error() -> None:
    """RecursionError path must also restore depth (no double-decrement)."""
    sched = _make_minimal_scheduler()
    sched._sprint_depth = 5  # Would trigger recursion guard
    sched._run_internal = AsyncMock(return_value=sched._result)  # type: ignore[method-assign]

    with pytest.raises(RecursionError):
        await sched.run(
            lifecycle=MagicMock(),
            sources=[],
            query="test",
        )

    # depth was 5 going in, guard decrements once before raising → still 5
    assert sched._sprint_depth == 5


@pytest.mark.asyncio
async def test_sprint_depth_allows_nested_calls_up_to_max() -> None:
    """Depth=2 is the last SAFE value (becomes 3 after +=1, 3 > 3 = False, OK)."""
    sched = _make_minimal_scheduler()
    sched._sprint_depth = 2  # 2 + 1 = 3, 3 > 3 = False → must proceed

    sched._run_internal = AsyncMock(return_value=sched._result)  # type: ignore[method-assign]

    result = await sched.run(
        lifecycle=MagicMock(),
        sources=[],
        query="test",
    )

    assert result is sched._result
    sched._run_internal.assert_awaited_once()  # type: ignore[attr-defined]
    # depth 2 → +1 in guard = 3, then finally -1 = 2 (restored)
    assert sched._sprint_depth == 2
