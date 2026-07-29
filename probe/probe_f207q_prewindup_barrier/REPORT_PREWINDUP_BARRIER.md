# Sprint F207Q-A: Pre-Windup Barrier

## Verdict: COMPLETE

## Root Cause
F207P-A clean live validation disproved F207M-A:
- Preflight READY_FOR_ACTIVE300
- Live PASS_VALID_CAPABILITY_RUN
- 3116 findings
- feed_dominance=1.0
- CT attempted = NO
- Public attempted = NO
- windup_lead_observed_s=2.27s

Scheduler was entering windup before PUBLIC/CT dispatch slots were reached.
Existing `_maybe_dispatch_nonfeed_probe_lanes` hook was insufficient because
it was not a hard pre-windup barrier.

## Changes Made

### 1. New Telemetry Fields (SprintSchedulerResult)
| Field | Type | Description |
|-------|------|-------------|
| `prewindup_barrier_checked` | bool | Barrier was invoked |
| `prewindup_barrier_required_lanes` | tuple[str] | Required lanes for this query |
| `prewindup_barrier_satisfied` | bool | All required lanes reached terminal state |
| `prewindup_barrier_attempted_lanes` | tuple[str] | Lanes actually attempted |
| `prewindup_barrier_skipped_lanes` | dict[str,str] | Skip reason per lane |
| `prewindup_barrier_errors` | dict[str,str] | Error per lane |
| `prewindup_barrier_duration_s` | float | Time spent in barrier |
| `windup_delayed_for_nonfeed` | bool | Windup blocked for nonfeed |

### 2. New Dataclass: PreWindupBarrierResult
```python
@dataclass(frozen=True)
class PreWindupBarrierResult:
    required_lanes: tuple[str, ...] = ()
    satisfied: bool = False
    attempted_lanes: tuple[str, ...] = ()
    skipped_lanes: tuple[str, ...] = ()
    error_lanes: tuple[str, ...] = ()
    duration_s: float = 0.0
```

### 3. New Helper: `_required_pre_windup_lanes()`
Rules:
- domain query + ok/warn memory: require ("public", "ct")
- domain query + critical/emergency: lanes required but may skip with explicit reason
- non-domain query: ct may skip with no_domain, public required if enabled
- stealth: never required

### 4. New Helper: `_ensure_pre_windup_lane_terminal_states()`
- Checks if required lanes already have terminal state
- If not, attempts them with tiny bounds (3 results, 10s for public; 5 results, 15s for ct)
- Fail-soft: errors become terminal error states, not crashes
- Uses `store=None` for PUBLIC (no DB write)
- Records all telemetry on `self._result`

### 5. New Helper: `_attempt_public_prewindup_barrier(query: str) -> dict | None`
- Attempts PUBLIC lane with tiny bounds
- Returns `{"attempted": True, "accepted": N}` or `{"timeout": True}` or `{"error": "..."}` or `None`

### 6. New Helper: `_attempt_ct_prewindup_barrier(query: str) -> dict | None`
- Attempts CT lane with tiny bounds (reuses existing `_get_ct_adapter()`)
- Returns `{"attempted": True, "raw_count": N}` or `{"timeout": True}` or `{"error": "..."}` or `None`

### 7. Modified Windup Decision (line ~1253-1278)
Before entering windup when `windup_guard()` returns True:
1. Get memory state from governor
2. Call `_ensure_pre_windup_lane_terminal_states()`
3. If required lanes not satisfied and required_lanes non-empty:
   - Set `windup_delayed_for_nonfeed = True`
   - Log debug message
   - Do another nonfeed pre-dispatch attempt
   - Re-check barrier

### 8. Diagnostic Report Integration
`acquisition_strategy.prewindup_barrier` section added to diagnostic report:
- Returns `None` if barrier never checked
- Contains: required_lanes, satisfied, attempted_lanes, skipped_lanes, errors, duration_s, windup_delayed

## Invariants Enforced
- [x] Never calls stealth lane
- [x] Never directly writes DB or graph
- [x] Uses bounded timeout per lane (10s public, 15s ct)
- [x] Fail-soft: adapter error becomes terminal error, not crash
- [x] Records all telemetry
- [x] No MLX/model load
- [x] PUBLIC barrier uses `store=None`

## Files Modified
- `runtime/sprint_scheduler.py` (backup: `.bak_F207Q_A_PREWINDUP_BARRIER`)
  - Lines ~469-487: New telemetry fields on SprintSchedulerResult
  - Lines ~1716-1993: New pre-windup barrier helpers
  - Lines ~1253-1278: Modified windup decision
  - Lines ~4862-4881: New `_get_prewindup_barrier_report()` method
  - Line ~5141: Added `prewindup_barrier` to diagnostic report

## Files Created
- `tests/probe_f207q_prewindup_barrier/test_f207q_prewindup_barrier.py` — 12 test cases
- `probe_f207q_prewindup_barrier/__init__.py`
- `probe_f207q_prewindup_barrier/REPORT_PREWINDUP_BARRIER.md`
- `probe_f207q_prewindup_barrier/prewindup_barrier.json`

## Verification
```bash
rtk proxy python -m pytest -q tests/probe_f207q_prewindup_barrier
rtk proxy python -m pytest -q tests/probe_f207m_nonfeed_predispatch tests/probe_f207l_nonfeed_attempt_fix tests/probe_f207j_nonfeed_execution
```

## Abort Conditions (all avoided)
- [x] No adapter production edit
- [x] No live network in tests (all mocked)
- [x] No stealth/dark-web enablement
- [x] No unbounded lane
- [x] No new storage authority
- [x] No graph direct write
- [x] No broad scheduler rewrite (targeted additions only)

## Success Definition
- [x] Early windup cannot bypass public/CT terminal state for domain query
- [x] Future clean live run should not have CT attempted=NO and public attempted=NO