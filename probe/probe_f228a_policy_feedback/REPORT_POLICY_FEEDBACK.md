# F228A: Policy Quality Feedback Wiring — Report

## Objective

Wire existing `SprintPolicyManager.update_with_quality_decisions()` into the scheduler's
teardown path so it becomes operational without creating a new RL framework.

## Changes Made

### `runtime/sprint_scheduler.py`

1. **Telemetry fields added to `SprintSchedulerResult`** (after `feed_no_signal_sources`):
   - `policy_quality_feedback_calls: int = 0`
   - `policy_quality_feedback_decisions: int = 0`
   - `policy_quality_feedback_sources: int = 0`
   - `policy_quality_feedback_errors: int = 0`

2. **Teardown wiring added** (after `_adapt_source_weights_from_feedback()` call):
   - Reconstructs `FindingQualityDecision`-style dicts from `_source_quality_feedback`
   - Calls `self._policy_manager.update_with_quality_decisions(_decisions, feed_url)` when:
     - `self._policy_manager is not None`
     - `self._policy_manager.enabled is True`
     - `_decisions` is non-empty
   - Fail-soft: all exceptions caught and logged at DEBUG; `CancelledError` propagates
   - Updates telemetry fields on the result

### `rl/sprint_policy_manager.py`

3. **`inject_scheduler()` made disabled-safe**: returns early when `self._enabled` is False,
   preventing scheduler reference leakage into disabled policy managers.

## Invariants Verified

| Test | What it checks |
|------|----------------|
| `test_disabled_no_calls_to_policy` | disabled pm is no-op |
| `test_disabled_inject_scheduler_noop` | disabled pm does not set `_scheduler` |
| `test_disabled_update_with_quality_decisions_no_telemetry` | disabled pm handles malformed input without raising |
| `test_enabled_calls_update_with_quality_decisions` | enabled pm accepts decisions without raising |
| `test_enabled_delegates_to_scheduler_via_inject_scheduler` | `inject_scheduler` wires policy→scheduler |
| `test_enabled_empty_decisions_no_error` | empty decision list is handled |
| `test_enabled_dict_and_struct_decisions` | both dict and attribute-style decisions work |
| `test_inject_scheduler_sets_scheduler_ref` | `_scheduler` is set after injection |
| `test_inject_scheduler_idempotent` | double injection does not fail |
| `test_inject_scheduler_none_scheduler` | passing None does not raise |
| `test_missing_accepted_field` | missing field defaults to False |
| `test_missing_source_family_field` | missing field falls back to feed_url |
| `test_completely_invalid_decision` | invalid types (int, None, str) handled |
| `test_non_list_decisions` | non-list inputs handled without crash |
| `test_bounded_at_200_sources` | >200 sources are silently dropped |
| `test_merge_into_scheduler_feedback` | delegated decisions appear in scheduler's `_source_quality_feedback` |
| `test_accumulation_across_multiple_calls` | multiple calls accumulate correctly |
| `test_no_duckdb_store_instantiation` | no DuckDB schema interactions |
| `test_no_sql_calls` | no SQL executed |
| `test_no_network_calls` | no HTTP/network calls |
| `test_no_mlx_load` | no MLX model loading |

## ABORT Conditions — All Avoided

| Condition | How avoided |
|-----------|-------------|
| New RL framework | No new classes; only calls to existing `SprintPolicyManager` |
| Scheduler rewrite | New block inserted at existing teardown location; no structural changes |
| DB schema migration | No DuckDB schema touched |
| Live network/model | All calls are in-process, fail-soft, no I/O |
| Changing default enabled state | `enabled=False` default unchanged; only existing `inject_policy_manager()` path activates |

## Test Results

```
pytest tests/probe_f228a_policy_feedback -q        → 21 passed
pytest tests/test_sprint_policy_manager.py -q     → 28 passed
pytest tests/probe_f227d_mission_feed_throttle -q  → 29 passed
pytest tests/probe_f226f_capability_delta -q       → 30 passed
```

## File Summary

| File | Change |
|------|--------|
| `runtime/sprint_scheduler.py` | +4 telemetry fields, +teardown policy wiring block |
| `rl/sprint_policy_manager.py` | `inject_scheduler` guard for disabled state |
| `probe_f228a_policy_feedback/test_policy_feedback.py` | 21 tests |
| `probe_f228a_policy_feedback/REPORT_POLICY_FEEDBACK.md` | this file |
| `probe_f228a_policy_feedback/policy_feedback.json` | structured output |