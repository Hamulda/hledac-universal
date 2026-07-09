# Sprint F208N-A: Scheduler Prewindup Callback Wiring and Final Nonfeed Terminality

## Status: COMPLETE

## Root Cause

The `windup_guard()` method in `SprintLifecycleRunner` only called the prewindup barrier callback when `should_enter_windup()` returned `True`. If `should_enter_windup()` was `False` (the scheduler was still in active work), the method returned early at line 138 before ever setting `callback_supplied=True`.

In the F208M live active300 run:
- `windup_guard_call_count = 17` (windup guard was called 17 times)
- `windup_guard_callback_supplied_count = 0` (callback was never reached)
- `should_enter_windup()` was `False` for all 17 cycles

This meant PUBLIC and CT never got their bounded terminality predispatch opportunity before the sprint reached finalization.

## Fix Applied

**File**: `runtime/sprint_lifecycle_runner.py`

**Change**: Modified `windup_guard()` to always evaluate the prewindup barrier callback when provided, regardless of whether `should_enter_windup()` is `True` or `False`.

```python
# Before (broken): returned early when should_enter_windup was False
if not self._adapter.should_enter_windup(now_monotonic):
    self._guard_observation["reason"] = "not_windup_time"
    self._guard_observation["callback_not_executed_reason"] = "callback_not_executed_guard_not_reached"
    return False  # <-- callback_supplied never set to True

# After (fixed): always call callback if provided
if _callback is not None:
    self._guard_observation["callback_supplied"] = True
    try:
        barrier_ok = _callback()
        self._guard_observation["callback_executed"] = True
        # ...
    except Exception as exc:
        # fail-soft on callback error
        self._guard_observation["callback_executed"] = True
        self._guard_observation["callback_not_executed_reason"] = "callback_not_executed_exception"
        # ...
```

## Key Invariants

1. **Callback always reached**: When a callback is provided to `windup_guard()`, `callback_supplied` is set to `True` regardless of `should_enter_windup()` state.

2. **Callback always executed**: When a callback is provided, it is called (not skipped when windup not triggered).

3. **CT terminal with raw results**: CT attempted with `raw=N, accepted=0` is terminal (state=`success_empty`), not missing.

4. **PUBLIC discovery_empty**: PUBLIC with discovery returning empty is terminal, not missing.

5. **CancelledError propagation**: `_ensure_nonfeed_predispatch_before_finalization` re-raises `CancelledError` without catching it.

## Test Coverage

- `test_callback_supplied_true_when_callback_provided_and_not_windup_time`: Verifies callback_supplied=True even when should_enter_windup is False
- `test_callback_executed_and_barrier_ok_recorded`: Verifies callback execution and barrier_ok recording
- `test_callback_exception_allows_windup`: Verifies fail-soft on callback exception
- `test_no_callback_no_supplied`: Verifies callback_supplied=False when no callback
- `test_windup_time_and_callback_passed`: Verifies normal windup path still works
- `test_ct_raw9_accepted0_terminal_not_missing`: Verifies CT with raw results is terminal
- `test_callback_supplied_count_increments_when_callback_provided`: Regression test for the 17-cycle scenario
- `test_cancelled_error_propagates`: Verifies CancelledError re-raise

## No Live Network

All tests use mocks - no live network calls, no MLX load, no browser launch.

## Verification

```bash
pytest tests/probe_f208n_scheduler_callback_wiring -v
```
