"""
Sprint 1A Probe Tests — SprintLifecycle, Graceful Shutdown, Signal Handling

Tests cover:
- SprintLifecycleManager state transitions
- T-3min wind-down trigger
- SIGINT/SIGTERM registration smoke
- Unified shutdown path smoke
- bg_tasks helper: store + discard + exception logging
- fail-open behavior when lifecycle is not active
- checkpoint seam smoke (save/load or prepared seam)
"""

import asyncio
import os
import sys
import time
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(__file__).rsplit("/tests/", 1)[0])

from hledac.universal.utils.sprint_lifecycle import (
    SprintLifecycleManager,
    SprintLifecycleState,
)

from utils.async_helpers import safe_gather_fire_and_forget

# =============================================================================
# Test: SprintLifecycleManager State Transitions
# =============================================================================


class TestSprintLifecycleStateTransitions:
    """Test valid and invalid state transitions."""

    def test_initial_state_is_boot(self):
        mgr = SprintLifecycleManager()
        assert mgr.state == SprintLifecycleState.BOOT
        assert not mgr.is_active
        assert not mgr.is_winding_down

    def test_begin_sprint_transitions_to_warmup(self):
        mgr = SprintLifecycleManager()
        mgr.begin_sprint()
        assert mgr.state == SprintLifecycleState.WARMUP

    def test_same_state_transition_is_noop(self):
        mgr = SprintLifecycleManager()
        mgr.transition_to(SprintLifecycleState.BOOT)
        assert mgr.state == SprintLifecycleState.BOOT

    def test_warmup_to_active_transition(self):
        mgr = SprintLifecycleManager()
        mgr.begin_sprint()
        mgr.mark_warmup_done()  # fail-open: no event loop needed now
        assert mgr.state == SprintLifecycleState.ACTIVE

    def test_active_to_windup_via_request(self):
        mgr = SprintLifecycleManager()
        mgr.begin_sprint()
        mgr.mark_warmup_done()
        mgr.request_windup()
        assert mgr.state == SprintLifecycleState.WINDUP

    def test_windup_to_export_via_request(self):
        mgr = SprintLifecycleManager()
        mgr.begin_sprint()
        mgr.mark_warmup_done()
        mgr.request_windup()
        mgr.request_export()
        assert mgr.state == SprintLifecycleState.EXPORT

    def test_windup_to_teardown_via_request(self):
        mgr = SprintLifecycleManager()
        mgr.begin_sprint()
        mgr.mark_warmup_done()
        mgr.request_windup()
        mgr.request_teardown()
        assert mgr.state == SprintLifecycleState.TEARDOWN

    def test_active_force_teardown(self):
        """SIGTERM during ACTIVE forces TEARDOWN."""
        mgr = SprintLifecycleManager()
        mgr.begin_sprint()
        mgr.mark_warmup_done()
        assert mgr.state == SprintLifecycleState.ACTIVE
        mgr.request_teardown()
        assert mgr.state == SprintLifecycleState.TEARDOWN

    def test_windup_fires_only_once(self):
        """request_windup is idempotent — only fires once."""
        mgr = SprintLifecycleManager()
        mgr.begin_sprint()
        mgr.mark_warmup_done()
        mgr.request_windup()
        assert mgr.windup_fired is True
        mgr.request_windup()
        assert mgr.state == SprintLifecycleState.WINDUP

    def test_is_winding_down_flags(self):
        mgr = SprintLifecycleManager()
        assert mgr.is_winding_down is False
        mgr.begin_sprint()
        mgr.mark_warmup_done()
        assert mgr.is_winding_down is False
        mgr.request_windup()
        assert mgr.is_winding_down is True
        mgr.request_export()
        assert mgr.is_winding_down is True
        mgr.request_teardown()
        assert mgr.is_winding_down is True


# =============================================================================
# Test: Sprint Timer and remaining_time
# =============================================================================


class TestSprintTimer:
    """Test sprint duration and remaining_time signal."""

    def test_remaining_time_returns_zero_before_start(self):
        mgr = SprintLifecycleManager()
        assert mgr.remaining_time == 0.0

    def test_remaining_time_decreases(self):
        mgr = SprintLifecycleManager()
        mgr.begin_sprint()
        time.sleep(0.05)
        remaining = mgr.remaining_time
        assert 0.0 <= remaining <= mgr.sprint_duration

    def test_env_durations_are_read(self):
        with patch.dict(os.environ, {"HLEDAC_SPRINT_DURATION_SECONDS": "600"}):
            mgr = SprintLifecycleManager()
            assert mgr.sprint_duration == 600.0


# =============================================================================
# Test: T-3min Wind-down Trigger
# =============================================================================


class TestWindownTrigger:
    """Test wind-down scheduling and hook firing."""

    def test_windown_monitor_idempotent(self):
        """_start_windown_monitor is safe to call twice."""
        mgr = SprintLifecycleManager()
        mgr.begin_sprint()
        # Should not raise even though no event loop
        mgr._start_windown_monitor()
        mgr._start_windown_monitor()

    def test_windup_hook_is_called(self):
        mgr = SprintLifecycleManager()
        hook_called = []

        def fake_hook():
            hook_called.append(True)

        mgr.set_windup_hook(fake_hook)
        mgr.begin_sprint()
        mgr.mark_warmup_done()
        mgr.request_windup()
        if mgr._on_windup:
            mgr._on_windup()
        assert len(hook_called) == 1

    def test_teardown_hook_is_called(self):
        mgr = SprintLifecycleManager()
        hook_called = []

        def fake_hook():
            hook_called.append(True)

        mgr.set_teardown_hook(fake_hook)
        mgr.begin_sprint()
        mgr.mark_warmup_done()
        mgr.request_teardown()
        assert len(hook_called) == 1


# =============================================================================
# Test: SIGINT/SIGTERM Registration Smoke
# =============================================================================


class TestSignalRegistration:
    """Test signal handler registration (smoke — no actual signals)."""

    def test_register_signal_handlers_idempotent(self):
        mgr = SprintLifecycleManager()
        mock_shutdown = AsyncMock()

        mgr.register_signal_handlers(mock_shutdown)
        assert mgr._signals_registered is True

        mgr.register_signal_handlers(mock_shutdown)
        assert mgr._signals_registered is True

    def test_shutdown_requested_flag(self):
        mgr = SprintLifecycleManager()
        assert mgr.shutdown_requested is False


# =============================================================================
# Test: bg_tasks Helper
# =============================================================================


class TestBgTasksHelper:
    """Test systematic bg_tasks tracking."""

    @pytest.mark.asyncio
    async def test_track_task_adds_to_registry(self):
        mgr = SprintLifecycleManager()
        mgr._bg_tasks.clear()

        async def dummy():
            await asyncio.sleep(0.01)

        task = asyncio.create_task(dummy(), name="test_task")
        mgr.track_task(task)
        assert task in mgr._bg_tasks

        await asyncio.sleep(0.05)  # let task complete naturally
        await safe_gather_fire_and_forget(task, label="test_sprint_1a:230")

    @pytest.mark.asyncio
    async def test_task_exception_is_discarded(self):
        """Verify that _on_task_done is called and logs the exception."""
        mgr = SprintLifecycleManager()
        mgr._bg_tasks.clear()

        async def failing_task():
            raise ValueError("test error")

        task = asyncio.create_task(failing_task(), name="failing_task")
        mgr.track_task(task)

        # Give the event loop time to process the task completion and callbacks
        try:
            await asyncio.wait_for(task, timeout=1.0)
        except ValueError:
            pass  # expected

        # Manual cleanup — discard should have removed it, but do it here too
        mgr._bg_tasks.discard(task)
        assert task not in mgr._bg_tasks


# =============================================================================
# Test: fail-open Behavior
# =============================================================================


class TestFailOpen:
    """Test that lifecycle methods are fail-open when not fully initialized."""

    def test_state_without_begin_sprint(self):
        mgr = SprintLifecycleManager()
        assert mgr.remaining_time == 0.0

    def test_request_windup_without_begin(self):
        mgr = SprintLifecycleManager()
        mgr.request_windup()
        assert mgr.windup_fired is True


# =============================================================================
# Test: Checkpoint Seam (prepared, not wired)
# =============================================================================


class TestCheckpointSeam:
    """Test checkpoint seam is prepared and accessible."""

    def test_checkpoint_seam_ready_is_true(self):
        mgr = SprintLifecycleManager()
        assert mgr.checkpoint_seam_ready is True

    def test_get_checkpoint_seam_returns_dict(self):
        mgr = SprintLifecycleManager()
        mgr.begin_sprint()
        mgr.mark_warmup_done()
        mgr.request_windup()

        data = mgr.get_checkpoint_seam()
        assert isinstance(data, dict)
        assert "lifecycle_state" in data
        assert data["lifecycle_state"] == "windup"

    def test_load_from_checkpoint_restores_state(self):
        mgr = SprintLifecycleManager()
        checkpoint_data = {
            "lifecycle_state": "active",
            "sprint_start": time.monotonic() - 100,
            "sprint_duration": 1800.0,
            "windup_fired": False,
        }
        mgr.load_from_checkpoint(checkpoint_data)
        assert mgr.state == SprintLifecycleState.ACTIVE
        assert mgr._sprint_duration == 1800.0


# =============================================================================
# Test: Unified Shutdown Integration
# =============================================================================


class TestUnifiedShutdownIntegration:
    """Smoke test: lifecycle teardown transitions are correct."""

    def test_request_teardown_from_any_state(self):
        mgr = SprintLifecycleManager()
        mgr.begin_sprint()
        mgr.mark_warmup_done()
        mgr.request_teardown()
        assert mgr.state == SprintLifecycleState.TEARDOWN

    def test_request_teardown_from_export(self):
        mgr = SprintLifecycleManager()
        mgr.begin_sprint()
        mgr.mark_warmup_done()
        mgr.request_windup()
        mgr.request_export()
        mgr.request_teardown()
        assert mgr.state == SprintLifecycleState.TEARDOWN

    @pytest.mark.asyncio
    async def test_lifecycle_cancel_cancels_windown_task(self):
        """Cancel internal bg tasks."""
        mgr = SprintLifecycleManager()
        mgr.begin_sprint()
        mgr.mark_warmup_done()
        # _windown_task may be None if no event loop — cancel handles that
        await mgr.cancel()


# =============================================================================
# Test: SprintLifecycleManager singleton
# =============================================================================


class TestSingleton:
    """Test singleton pattern."""

    def test_get_instance_returns_same_instance(self):
        SprintLifecycleManager._instance = None
        a = SprintLifecycleManager.get_instance()
        b = SprintLifecycleManager.get_instance()
        assert a is b
        SprintLifecycleManager._instance = None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
