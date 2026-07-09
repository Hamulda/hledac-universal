# Sprint F215C — PUBLIC Terminality Truth

## Problem

`public_terminal_state` was `None` in canonical report for domain-query active profiles because:

1. `terminal_state` field was **never added** to `SourceFamilyOutcome.to_dict()`
2. `normalize_source_family_outcome()` **did not compute** `terminal_state`
3. `_build_diagnostic_report` line 6886 did `_sfo.get("public", {}).get("terminal_state")` → returned `None`

## Fix

### 1. `runtime/acquisition_strategy.py` — `SourceFamilyOutcome` dataclass

Added `terminal_state` field with default `"UNKNOWN"`:

```python
terminal_state: str = "UNKNOWN"  # F215C

def to_dict(self) -> dict:
    return {
        # ... existing fields ...
        "terminal_state": self.terminal_state,
    }
```

### 2. `runtime/acquisition_strategy.py` — `normalize_source_family_outcome()`

Added `_derive_terminal()` helper that computes `terminal_state` from outcome fields:

```
NEVER_SCHEDULED   — raw=None or skip_reason in ("never_scheduled", "no_outcome_recorded")
SKIPPED_BY_POLICY — attempted=False, skip_reason contains "policy"/"disabled"/"not_enabled"
SKIPPED_BY_MEMORY — attempted=False, skip_reason contains "memory"/"hw_skip"/"hardware"
SKIPPED           — attempted=False, other skip_reason
ATTEMPTED_ERROR   — attempted=True, error is set
ATTEMPTED_TIMEOUT — attempted=True, timeout=True
ATTEMPTED_ACCEPTED — attempted=True, accepted_count > 0
ATTEMPTED_NO_RESULTS — attempted=True, no error/timeout, accepted_count == 0
```

### 3. `runtime/sprint_scheduler.py` — `_build_diagnostic_report()`

Changed fallback from `"NEVER_ATTEMPTED"` to `"NEVER_SCHEDULED"`:

```python
report["public_terminal_state"] = (
    _sfo.get("public", {}).get("terminal_state") or "NEVER_SCHEDULED"
)
```

## PUBLIC Terminal State Values

| State | Meaning |
|-------|---------|
| `NEVER_SCHEDULED` | PUBLIC lane was never in the acquisition plan |
| `SKIPPED_BY_POLICY` | PUBLIC disabled by acquisition policy |
| `SKIPPED_BY_MEMORY` | PUBLIC skipped due to memory pressure |
| `SKIPPED` | Scheduled but skipped (e.g., `terminal:remaining_too_low`) |
| `ATTEMPTED_NO_RESULTS` | Attempted but produced zero accepted findings |
| `ATTEMPTED_ALL_REJECTED` | All findings were rejected (bridge rejection) |
| `ATTEMPTED_TIMEOUT` | Timed out |
| `ATTEMPTED_ERROR` | Error during execution |
| `ATTEMPTED_ACCEPTED` | At least one finding was accepted |

## Test Coverage

- 20 probe tests in `tests/probe_f215c_public_terminality/`
- All states covered: `NEVER_SCHEDULED`, `SKIPPED_BY_POLICY`, `SKIPPED_BY_MEMORY`, `SKIPPED`, `ATTEMPTED_*`
- `SourceFamilyOutcome.to_dict()` round-trip verified
- Hermetic invariants: no network imports, no MLX load

## Success Criteria

✅ `public_terminal_state` is **never null** for domain-query active profiles
✅ All 8 terminal states are derivable from outcome fields
✅ No changes to public fetch behavior
✅ No changes to transport policy
✅ No live network in tests
