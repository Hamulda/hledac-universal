"""
Sprint F206C: Lifecycle runner refactor tests.

Verifies that SprintLifecycleRunner correctly encapsulates lifecycle orchestration
and produces the same phase traces as direct scheduler lifecycle handling.

Phase trace comparison tests — verify runner vs direct adapter give same results.
"""

import sys
sys.path.insert(0, ".")


def test_runner_setup_transitions_boot_to_warmup(mock_lifecycle):
    """After setup(), lifecycle is in WARMUP."""
    from hledac.universal.runtime.sprint_scheduler import _LifecycleAdapter
    from hledac.universal.runtime.sprint_lifecycle_runner import SprintLifecycleRunner

    adapter = _LifecycleAdapter(mock_lifecycle)
    runner = SprintLifecycleRunner(mock_lifecycle, adapter)

    assert mock_lifecycle._started_at is None
    runner.setup()

    assert mock_lifecycle._started_at is not None
    assert mock_lifecycle._current_phase.name == "WARMUP"
    print("PASS: setup() → WARMUP")


def test_runner_tick_returns_current_phase(mock_lifecycle):
    """tick() returns current phase via adapter."""
    from hledac.universal.runtime.sprint_scheduler import _LifecycleAdapter
    from hledac.universal.runtime.sprint_lifecycle_runner import SprintLifecycleRunner

    adapter = _LifecycleAdapter(mock_lifecycle)
    runner = SprintLifecycleRunner(mock_lifecycle, adapter)
    runner.setup()

    phase = runner.tick()
    assert phase is not None
    print(f"PASS: tick() returned phase object (name={phase.name})")


def test_runner_ensure_active_transitions_warmup_to_active(mock_lifecycle):
    """ensure_active() transitions WARMUP → ACTIVE."""
    from hledac.universal.runtime.sprint_scheduler import _LifecycleAdapter
    from hledac.universal.runtime.sprint_lifecycle_runner import SprintLifecycleRunner

    adapter = _LifecycleAdapter(mock_lifecycle)
    runner = SprintLifecycleRunner(mock_lifecycle, adapter)
    runner.setup()

    assert mock_lifecycle._current_phase.name == "WARMUP"
    runner.ensure_active()
    assert mock_lifecycle._current_phase.name == "ACTIVE"
    print("PASS: ensure_active() → ACTIVE")


def test_runner_windup_guard_false_when_active(mock_lifecycle):
    """windup_guard() returns False when sprint is in ACTIVE."""
    from hledac.universal.runtime.sprint_scheduler import _LifecycleAdapter
    from hledac.universal.runtime.sprint_lifecycle_runner import SprintLifecycleRunner

    adapter = _LifecycleAdapter(mock_lifecycle)
    runner = SprintLifecycleRunner(mock_lifecycle, adapter)
    runner.setup()
    runner.ensure_active()

    assert runner.windup_guard() is False
    print("PASS: windup_guard() → False in ACTIVE")


def test_runner_windup_guard_true_when_time_near_end(mock_lifecycle):
    """windup_guard() returns True when remaining <= windup_lead_s."""
    from hledac.universal.runtime.sprint_scheduler import _LifecycleAdapter
    from hledac.universal.runtime.sprint_lifecycle_runner import SprintLifecycleRunner

    mock_lifecycle.sprint_duration_s = 100.0
    mock_lifecycle.windup_lead_s = 180.0  # windup_lead > sprint_duration

    adapter = _LifecycleAdapter(mock_lifecycle)
    runner = SprintLifecycleRunner(mock_lifecycle, adapter)
    # Simulate sprint start at now=0 via the mock's internal tick state
    runner.setup()
    # Manually advance mock to time 0 so remaining = sprint_duration_s
    mock_lifecycle._started_at = 0.0

    # remaining_time = 100 - 0 = 100, windup_lead_s = 180, so 100 <= 180 → True
    assert runner.windup_guard(now_monotonic=0.0) is True
    print("PASS: windup_guard() → True when remaining <= windup_lead")


def test_runner_abort_requested(mock_lifecycle):
    """abort_requested property reflects lifecycle abort state."""
    from hledac.universal.runtime.sprint_scheduler import _LifecycleAdapter
    from hledac.universal.runtime.sprint_lifecycle_runner import SprintLifecycleRunner

    adapter = _LifecycleAdapter(mock_lifecycle)
    runner = SprintLifecycleRunner(mock_lifecycle, adapter)

    assert runner.abort_requested is False
    mock_lifecycle.request_abort("test_abort")
    assert runner.abort_requested is True
    assert runner.abort_reason == "test_abort"
    print("PASS: abort_requested / abort_reason work correctly")


def test_runner_is_terminal_false_in_active(mock_lifecycle):
    """is_terminal() is False in ACTIVE."""
    from hledac.universal.runtime.sprint_scheduler import _LifecycleAdapter
    from hledac.universal.runtime.sprint_lifecycle_runner import SprintLifecycleRunner

    adapter = _LifecycleAdapter(mock_lifecycle)
    runner = SprintLifecycleRunner(mock_lifecycle, adapter)
    runner.setup()
    runner.ensure_active()

    assert runner.is_terminal() is False
    print("PASS: is_terminal() → False in ACTIVE")


def test_runner_is_terminal_true_in_teardown(mock_lifecycle):
    """is_terminal() is True after TEARDOWN."""
    from hledac.universal.runtime.sprint_scheduler import _LifecycleAdapter
    from hledac.universal.runtime.sprint_lifecycle_runner import SprintLifecycleRunner

    adapter = _LifecycleAdapter(mock_lifecycle)
    runner = SprintLifecycleRunner(mock_lifecycle, adapter)
    runner.setup()
    runner.ensure_active()
    runner.teardown()

    assert runner.is_terminal() is True
    print("PASS: is_terminal() → True after TEARDOWN")


def test_runner_teardown_windup_to_export_to_teardown(mock_lifecycle):
    """teardown() from WINDUP: EXPORT then TEARDOWN."""
    from hledac.universal.runtime.sprint_scheduler import _LifecycleAdapter
    from hledac.universal.runtime.sprint_lifecycle_runner import SprintLifecycleRunner
    from hledac.universal.runtime.sprint_lifecycle import SprintPhase

    adapter = _LifecycleAdapter(mock_lifecycle)
    runner = SprintLifecycleRunner(mock_lifecycle, adapter)
    runner.setup()
    runner.ensure_active()

    # Simulate entering WINDUP by replacing _current_phase with actual SprintPhase enum.
    # This ensures phase == SprintPhase.WINDUP is True (MagicMock != enum).
    # The current_phase property returns _current_phase, so it also returns the enum.
    mock_lifecycle._current_phase = SprintPhase.WINDUP
    runner.teardown()

    assert mock_lifecycle._export_started is True
    assert mock_lifecycle._teardown_started is True
    print("PASS: teardown() WINDUP → EXPORT → TEARDOWN")


def test_runner_teardown_active_to_teardown_abort(mock_lifecycle):
    """teardown() from ACTIVE: abort then TEARDOWN."""
    from hledac.universal.runtime.sprint_scheduler import _LifecycleAdapter
    from hledac.universal.runtime.sprint_lifecycle_runner import SprintLifecycleRunner

    adapter = _LifecycleAdapter(mock_lifecycle)
    runner = SprintLifecycleRunner(mock_lifecycle, adapter)
    runner.setup()
    runner.ensure_active()

    # Do NOT set WINDUP - simulate abort from ACTIVE
    runner.teardown()

    assert mock_lifecycle._abort_requested is True
    assert mock_lifecycle._teardown_started is True
    assert mock_lifecycle._current_phase.name == "TEARDOWN"
    print("PASS: teardown() ACTIVE → abort → TEARDOWN")


def test_runner_post_sleep_gate_false_in_active(mock_lifecycle):
    """post_sleep_gate() is False when still in ACTIVE."""
    from hledac.universal.runtime.sprint_scheduler import _LifecycleAdapter
    from hledac.universal.runtime.sprint_lifecycle_runner import SprintLifecycleRunner

    adapter = _LifecycleAdapter(mock_lifecycle)
    runner = SprintLifecycleRunner(mock_lifecycle, adapter)
    runner.setup()
    runner.ensure_active()

    assert runner.post_sleep_gate() is False
    print("PASS: post_sleep_gate() → False in ACTIVE")


def test_runner_current_phase_matches_adapter(mock_lifecycle):
    """current_phase property reflects adapter._current_phase."""
    from hledac.universal.runtime.sprint_scheduler import _LifecycleAdapter
    from hledac.universal.runtime.sprint_lifecycle_runner import SprintLifecycleRunner

    adapter = _LifecycleAdapter(mock_lifecycle)
    runner = SprintLifecycleRunner(mock_lifecycle, adapter)
    runner.setup()

    assert runner.current_phase == adapter._current_phase
    print(f"PASS: current_phase = '{runner.current_phase}'")


def test_runner_wall_clock_start_set_on_setup(mock_lifecycle):
    """wall_clock_start is set when setup() is called."""
    from hledac.universal.runtime.sprint_scheduler import _LifecycleAdapter
    from hledac.universal.runtime.sprint_lifecycle_runner import SprintLifecycleRunner

    adapter = _LifecycleAdapter(mock_lifecycle)
    runner = SprintLifecycleRunner(mock_lifecycle, adapter)

    assert runner.wall_clock_start is None
    runner.setup()
    assert runner.wall_clock_start is not None
    print("PASS: wall_clock_start set after setup()")


def test_phase_trace_matches_direct_adapter(mock_lifecycle):
    """
    Phase trace via runner matches direct adapter usage.
    This is the key refactor invariant: no behavior change.
    """
    from hledac.universal.runtime.sprint_scheduler import _LifecycleAdapter
    from hledac.universal.runtime.sprint_lifecycle_runner import SprintLifecycleRunner

    # Direct adapter trace
    adapter_direct = _LifecycleAdapter(mock_lifecycle)
    mock_lifecycle._started_at = None
    mock_lifecycle._current_phase.name = "BOOT"
    mock_lifecycle._abort_requested = False
    mock_lifecycle._teardown_started = False

    adapter_direct.start()
    phase1 = adapter_direct.tick()
    phase1_str = str(phase1)
    if phase1_str == "SprintPhase.WARMUP" or phase1_str.endswith(".WARMUP"):
        adapter_direct.mark_warmup_done()
    trace_direct = mock_lifecycle._current_phase.name

    # Reset
    mock_lifecycle._started_at = None
    mock_lifecycle._current_phase.name = "BOOT"
    mock_lifecycle._abort_requested = False
    mock_lifecycle._teardown_started = False

    # Runner trace
    adapter_runner = _LifecycleAdapter(mock_lifecycle)
    runner = SprintLifecycleRunner(mock_lifecycle, adapter_runner)
    runner.setup()
    runner.tick()
    runner.ensure_active()
    trace_runner = mock_lifecycle._current_phase.name

    assert trace_runner == trace_direct == "ACTIVE"
    print(f"PASS: phase trace matches — direct={trace_direct}, runner={trace_runner}")


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "-s"])
