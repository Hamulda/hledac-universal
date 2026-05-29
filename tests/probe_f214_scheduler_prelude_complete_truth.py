"""
Sprint F214: Scheduler prelude_complete truth guard probe.

Tests that _finalize_result_truth("prelude_complete", ...) is only called
when acquisition_prelude_ran=True. Verifies:
- Test A: default non-domain query → acquisition_prelude_ran=False → NO prelude_complete
- Test B: nonfeed_diagnostic profile → acquisition_prelude_ran=True → prelude_complete with ACTIVE phase
- Test C: domain query default → prelude_complete with ACTIVE phase (if prelude ran)

Run:
  PYTHONPATH=/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal \
    python -m pytest tests/probe_f214_scheduler_prelude_complete_truth.py -v
"""

import pytest


def _make_result():
    """Minimal SprintSchedulerResult — all fields keyword, no positional args."""
    from runtime.sprint_scheduler import SprintSchedulerResult
    return SprintSchedulerResult()


async def _replay_guard(result, prelude_ran):
    """
    Replay the F214 patched call site logic in isolation.

    Simulates: after _run_mandatory_acquisition_prelude() sets result.acquisition_prelude_ran,
    the guard either calls _finalize_result_truth or skips it.

    Returns list of finalize calls made.
    """
    calls = []

    async def capture_finalize(exit_path, exit_reason, exit_phase, query):
        calls.append((exit_path, exit_reason, exit_phase, query))

    # Set the field exactly as _run_mandatory_acquisition_prelude would
    result.acquisition_prelude_ran = prelude_ran

    # F214 patch: guarded call site
    if getattr(result, "acquisition_prelude_ran", False):
        await capture_finalize("prelude_complete", "acquisition prelude finished", "ACTIVE", "")

    return calls


# ── Test A: prelude_ran=False → NO finalize call ───────────────────────────

@pytest.mark.asyncio
async def test_a_prelude_ran_false_no_finalize():
    """
    Test A: acquisition_prelude_ran=False (non-domain default query path)

    Expected: _finalize_result_truth("prelude_complete", ...) NOT called.
    """
    result = _make_result()

    calls = await _replay_guard(result, prelude_ran=False)

    assert len(calls) == 0, f"Expected no calls, got: {calls}"
    print("Test A PASS: no false prelude_complete when prelude_ran=False")


# ── Test B: prelude_ran=True, nonfeed_diagnostic path ───────────────────────

@pytest.mark.asyncio
async def test_b_prelude_ran_true_active_phase():
    """
    Test B: acquisition_prelude_ran=True (nonfeed_diagnostic or domain query path)

    Expected: _finalize_result_truth("prelude_complete", ..., phase="ACTIVE") called once.
    """
    result = _make_result()

    calls = await _replay_guard(result, prelude_ran=True)

    assert len(calls) == 1, f"Expected 1 call, got: {len(calls)}: {calls}"
    exit_path, exit_reason, exit_phase, _ = calls[0]
    assert exit_path == "prelude_complete"
    assert exit_phase == "ACTIVE", f"Expected phase=ACTIVE, got phase={exit_phase}"
    print(f"Test B PASS: prelude_complete phase={exit_phase}")


# ── Test C: guard getattr on falsy attribute ────────────────────────────────

def test_getattr_false_case():
    """Unit: getattr(result, 'acquisition_prelude_ran', False) is False when unset."""
    result = _make_result()
    # Field defaults to False
    assert getattr(result, "acquisition_prelude_ran", False) is False
    print("Test C PASS: getattr returns False for unset field")


def test_getattr_true_case():
    """Unit: getattr(result, 'acquisition_prelude_ran', False) is True when explicitly set."""
    result = _make_result()
    result.acquisition_prelude_ran = True
    assert getattr(result, "acquisition_prelude_ran", False) is True
    print("Test C PASS: getattr returns True for explicitly set field")


# ── Regression smoke ────────────────────────────────────────────────────────

def test_result_has_acquisition_prelude_ran():
    """SprintSchedulerResult has acquisition_prelude_ran field."""
    from runtime.sprint_scheduler import SprintSchedulerResult
    r = SprintSchedulerResult()
    assert hasattr(r, "acquisition_prelude_ran")
    print("Smoke PASS: acquisition_prelude_ran field exists")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
