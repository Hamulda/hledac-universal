"""Sprint 8BI — SprintLifecycleManager + SprintContext tests.

Covers all 22 D-n test cases from the sprint brief.
"""


import json
import time
from typing import Generator

import pytest

from hledac.universal.runtime.sprint_lifecycle import (
    InvalidPhaseTransitionError,
    SprintLifecycleManager,
    SprintPhase,
)
from hledac.universal.utils.sprint_context import (
    SprintContext,
    clear_sprint_context,
    get_current_context,
    set_sprint_context,
    sprint_scope,
    update_phase,
)


# =============================================================================
# SprintLifecycleManager — D.1–D.18
# =============================================================================

class TestDefaultPhaseIsBootAfterInit:
    def test_default_phase_is_boot_after_init(self, manager: SprintLifecycleManager) -> None:
        assert manager._current_phase == SprintPhase.BOOT
        assert manager._started_at is None


class TestStartInitializesStartedAtAndPhase:
    def test_start_initializes_started_at_and_phase(
        self, manager: SprintLifecycleManager, t0: float
    ) -> None:
        manager.start(now_monotonic=t0)
        assert manager._started_at == t0
        assert manager._current_phase == SprintPhase.WARMUP


class TestTransitionOrderIsMonotonic:
    def test_transition_order_is_monotonic(
        self, manager_started: SprintLifecycleManager, t0: float
    ) -> None:
        mgr = manager_started
        # WARMUP → ACTIVE
        mgr.transition_to(SprintPhase.ACTIVE, now_monotonic=t0 + 10)
        assert mgr._current_phase == SprintPhase.ACTIVE


class TestInvalidBackwardsTransitionRaises:
    def test_invalid_backwards_transition_raises(
        self, manager_started: SprintLifecycleManager, t0: float
    ) -> None:
        mgr = manager_started
        with pytest.raises(InvalidPhaseTransitionError):
            mgr.transition_to(SprintPhase.BOOT, now_monotonic=t0 + 10)


class TestRemainingTimeUsesMonotonicMath:
    def test_remaining_time_uses_monotonic_math(
        self, manager_started: SprintLifecycleManager, t0: float
    ) -> None:
        # sprint_duration_s=1800, started at t0
        assert manager_started.remaining_time(now_monotonic=t0) == 1800.0
        # After 600s elapsed
        assert manager_started.remaining_time(now_monotonic=t0 + 600) == 1200.0
        # At deadline
        assert manager_started.remaining_time(now_monotonic=t0 + 1800) == 0.0
        # Past deadline
        assert manager_started.remaining_time(now_monotonic=t0 + 2000) == 0.0


class TestShouldEnterWindupAtTMinus180:
    def test_should_enter_windup_at_t_minus_180(
        self, manager_started: SprintLifecycleManager, t0: float
    ) -> None:
        mgr = manager_started
        mgr.transition_to(SprintPhase.ACTIVE, now_monotonic=t0 + 10)
        # Remaining = 1800 - 10 = 1790, windup_lead_s=180 → not yet
        assert mgr.should_enter_windup(now_monotonic=t0 + 10) is False
        # Remaining = 1800 - 1620 = 180 → exactly at threshold
        assert mgr.should_enter_windup(now_monotonic=t0 + 1620) is True
        # Remaining = 1800 - 1700 = 100 ≤ 180 → yes
        assert mgr.should_enter_windup(now_monotonic=t0 + 1700) is True


class TestTickAutoEntersWindup:
    def test_tick_auto_enters_windup(
        self, manager_started: SprintLifecycleManager, t0: float
    ) -> None:
        mgr = manager_started
        mgr.transition_to(SprintPhase.ACTIVE, now_monotonic=t0 + 10)
        # Time advances to T-120s remaining → tick should trigger WINDUP
        result = mgr.tick(now_monotonic=t0 + 1680)  # 1800 - 1680 = 120 remaining
        assert mgr._current_phase == SprintPhase.WINDUP
        assert result == SprintPhase.WINDUP


class TestExportOnlyAfterWindup:
    def test_export_only_after_windup(
        self, manager_started: SprintLifecycleManager, t0: float
    ) -> None:
        mgr = manager_started
        mgr.transition_to(SprintPhase.ACTIVE, now_monotonic=t0 + 10)
        # Try EXPORT from ACTIVE → must fail
        with pytest.raises(InvalidPhaseTransitionError):
            mgr.mark_export_started(now_monotonic=t0 + 20)
        # Correct path: WARMUP → ACTIVE → WINDUP → EXPORT
        mgr.transition_to(SprintPhase.WINDUP, now_monotonic=t0 + 100)
        mgr.mark_export_started(now_monotonic=t0 + 110)
        assert mgr._current_phase == SprintPhase.EXPORT


class TestTeardownOnlyAfterExport:
    def test_teardown_only_after_export(
        self, manager_started: SprintLifecycleManager, t0: float
    ) -> None:
        mgr = manager_started
        mgr.transition_to(SprintPhase.ACTIVE, now_monotonic=t0 + 10)
        # Try TEARDOWN from ACTIVE → must fail
        with pytest.raises(InvalidPhaseTransitionError):
            mgr.mark_teardown_started(now_monotonic=t0 + 20)
        # Correct path
        mgr.transition_to(SprintPhase.WINDUP, now_monotonic=t0 + 100)
        mgr.mark_export_started(now_monotonic=t0 + 110)
        mgr.mark_teardown_started(now_monotonic=t0 + 120)
        assert mgr._current_phase == SprintPhase.TEARDOWN


class TestRequestAbortSetsAbortFlags:
    def test_request_abort_sets_abort_flags(
        self, manager_started: SprintLifecycleManager
    ) -> None:
        mgr = manager_started
        mgr.transition_to(SprintPhase.ACTIVE, now_monotonic=100.0)
        mgr.request_abort(reason="memory pressure")
        assert mgr._abort_requested is True
        assert mgr._abort_reason == "memory pressure"
        # Abort does not force a phase change immediately
        assert mgr._current_phase == SprintPhase.ACTIVE


class TestIsTerminalFalseBeforeTeardown:
    def test_is_terminal_false_before_teardown(
        self, manager_started: SprintLifecycleManager, t0: float
    ) -> None:
        mgr = manager_started
        assert mgr.is_terminal() is False
        mgr.transition_to(SprintPhase.ACTIVE, now_monotonic=t0 + 10)
        assert mgr.is_terminal() is False
        mgr.request_abort()
        assert mgr.is_terminal() is False  # still not reached TEARDOWN


class TestIsTerminalTrueAfterTeardown:
    def test_is_terminal_true_after_teardown(
        self, manager_started: SprintLifecycleManager, t0: float
    ) -> None:
        mgr = manager_started
        mgr.transition_to(SprintPhase.ACTIVE, now_monotonic=t0 + 10)
        mgr.transition_to(SprintPhase.WINDUP, now_monotonic=t0 + 100)
        mgr.mark_export_started(now_monotonic=t0 + 110)
        mgr.mark_teardown_started(now_monotonic=t0 + 120)
        assert mgr.is_terminal() is True


class TestSnapshotIsDeterministic:
    def test_snapshot_is_deterministic(
        self, manager_started: SprintLifecycleManager, t0: float
    ) -> None:
        mgr = manager_started
        mgr.transition_to(SprintPhase.ACTIVE, now_monotonic=t0 + 10)
        snap1 = mgr.snapshot()
        snap2 = mgr.snapshot()
        assert snap1 == snap2
        # Same state captured at two points in time should differ only
        # by fields that can change (entered_phase_at)
        snap1["entered_phase_at"] = 0.0
        snap2["entered_phase_at"] = 0.0
        assert snap1 == snap2


class TestSnapshotIsJsonSerializable:
    def test_snapshot_is_json_serializable(
        self, manager_started: SprintLifecycleManager, t0: float
    ) -> None:
        mgr = manager_started
        mgr.transition_to(SprintPhase.ACTIVE, now_monotonic=t0 + 10)
        snap = mgr.snapshot()
        # Must not raise
        encoded = json.dumps(snap)
        decoded = json.loads(encoded)
        assert decoded["current_phase"] == "ACTIVE"
        assert decoded["sprint_duration_s"] == 1800.0
        assert decoded["abort_requested"] is False


class TestRecommendedToolModeNormal:
    def test_recommended_tool_mode_normal(
        self, manager_started: SprintLifecycleManager, t0: float
    ) -> None:
        mgr = manager_started
        mgr.transition_to(SprintPhase.ACTIVE, now_monotonic=t0 + 10)
        # Ample time, thermal nominal → normal
        mode = mgr.recommended_tool_mode(now_monotonic=t0 + 10, thermal_state="nominal")
        assert mode == "normal"


class TestRecommendedToolModePruneTime:
    def test_recommended_tool_mode_prune_time(
        self, manager_started: SprintLifecycleManager, t0: float
    ) -> None:
        mgr = manager_started
        mgr.transition_to(SprintPhase.ACTIVE, now_monotonic=t0 + 10)
        # Remaining = 120s ≤ windup_lead_s=180 → prune
        mode = mgr.recommended_tool_mode(now_monotonic=t0 + 1680, thermal_state="nominal")
        assert mode == "prune"


class TestRecommendedToolModePruneThermal:
    def test_recommended_tool_mode_prune_thermal(
        self, manager_started: SprintLifecycleManager, t0: float
    ) -> None:
        mgr = manager_started
        mgr.transition_to(SprintPhase.ACTIVE, now_monotonic=t0 + 10)
        # Ample time but thermal throttled → prune
        mode = mgr.recommended_tool_mode(now_monotonic=t0 + 10, thermal_state="throttled")
        assert mode == "prune"


class TestRecommendedToolModePanic:
    def test_recommended_tool_mode_panic_abort(
        self, manager_started: SprintLifecycleManager, t0: float
    ) -> None:
        mgr = manager_started
        mgr.transition_to(SprintPhase.ACTIVE, now_monotonic=t0 + 10)
        mgr.request_abort(reason="emergency")
        mode = mgr.recommended_tool_mode(now_monotonic=t0 + 10, thermal_state="nominal")
        assert mode == "panic"

    def test_recommended_tool_mode_panic_time(
        self, manager_started: SprintLifecycleManager, t0: float
    ) -> None:
        mgr = manager_started
        mgr.transition_to(SprintPhase.ACTIVE, now_monotonic=t0 + 10)
        # Remaining = 20s ≤ 30 → panic
        mode = mgr.recommended_tool_mode(now_monotonic=t0 + 1780, thermal_state="nominal")
        assert mode == "panic"

    def test_recommended_tool_mode_panic_thermal_critical(
        self, manager_started: SprintLifecycleManager, t0: float
    ) -> None:
        mgr = manager_started
        mgr.transition_to(SprintPhase.ACTIVE, now_monotonic=t0 + 10)
        mode = mgr.recommended_tool_mode(now_monotonic=t0 + 10, thermal_state="critical")
        assert mode == "panic"


# =============================================================================
# SprintContext — D.19–D.22
# =============================================================================

class TestSprintContextRoundtrip:
    def test_sprint_context_roundtrip(self) -> None:
        ctx = SprintContext(
            sprint_id="8bi-test",
            target="osint",
            phase="active",
            transport="curl_cffi",
        )
        set_sprint_context(ctx)
        result = get_current_context()
        assert result is not None
        assert result.sprint_id == "8bi-test"
        assert result.target == "osint"
        assert result.phase == "active"
        assert result.transport == "curl_cffi"
        clear_sprint_context()


class TestSprintContextReset:
    def test_sprint_context_reset(self) -> None:
        # set_sprint_context is fire-and-forget; use clear_sprint_context to reset
        ctx = SprintContext(sprint_id="reset-test", target="x", phase="boot")
        set_sprint_context(ctx)
        current = get_current_context()
        assert current is not None and current.sprint_id == "reset-test"
        clear_sprint_context()
        assert get_current_context() is None


class TestSprintContextIsolationBetweenResets:
    def test_sprint_context_isolation_between_resets(self) -> None:
        # Verify contexts are independent: setting one doesn't mutate another
        clear_sprint_context()
        ctx1 = SprintContext(sprint_id="first", target="a")
        ctx2 = SprintContext(sprint_id="second", target="b")
        set_sprint_context(ctx1)
        cur = get_current_context()
        assert cur is not None and cur.sprint_id == "first"
        set_sprint_context(ctx2)
        cur = get_current_context()
        assert cur is not None and cur.sprint_id == "second"
        # ctx1 is unchanged in storage
        set_sprint_context(ctx1)
        cur = get_current_context()
        assert cur is not None and cur.sprint_id == "first"
        clear_sprint_context()
        assert get_current_context() is None


class TestManagerHasNoAsyncOrThreadSideEffects:
    def test_manager_has_no_async_or_thread_side_effects(
        self, manager: SprintLifecycleManager
    ) -> None:
        """Verify manager methods are synchronous and don't spawn threads/tasks."""
        import asyncio
        import threading

        manager.start(now_monotonic=100.0)
        manager.transition_to(SprintPhase.ACTIVE, now_monotonic=110.0)

        # tick is pure sync
        phase = manager.tick(now_monotonic=120.0)
        assert phase == SprintPhase.ACTIVE

        # remaining_time is pure sync
        rt = manager.remaining_time(now_monotonic=120.0)
        assert rt >= 0

        # snapshot is pure sync
        snap = manager.snapshot()
        assert isinstance(snap, dict)

        # recommended_tool_mode is pure sync
        mode = manager.recommended_tool_mode(now_monotonic=120.0)
        assert mode in ("normal", "prune", "panic")

        # Verify no threads were spawned
        assert threading.active_count() == 1  # main thread only

        # Verify async context is not required
        async def dummy_async() -> dict:
            return manager.snapshot()
        result = asyncio.get_event_loop().run_until_complete(dummy_async())
        assert result["current_phase"] == "ACTIVE"


# =============================================================================
# update_phase helper
# =============================================================================

class TestUpdatePhase:
    def test_update_phase_returns_new_context(self) -> None:
        ctx = SprintContext(sprint_id="up-test", phase="active")
        new_ctx = update_phase(ctx, "windup")
        assert new_ctx.phase == "windup"
        assert ctx.phase == "active"  # original unchanged


# =============================================================================
# sprint_scope context manager
# =============================================================================

class TestSprintScope:
    def test_sprint_scope_sets_and_resets(self) -> None:
        ctx = SprintContext(sprint_id="scope-test", target="y")
        with sprint_scope(ctx):
            assert get_current_context() is ctx
        assert get_current_context() is None or not get_current_context().sprint_id

    def test_sprint_scope_resets_on_exception(self) -> None:
        ctx = SprintContext(sprint_id="exc-test")
        try:
            with sprint_scope(ctx):
                assert get_current_context() is ctx
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        assert get_current_context() is None
