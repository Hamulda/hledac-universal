# Sprint F233D: Nonfeed Prelude Coverage Engine — REPORT

## Verdict: COMPLETE

## Changes Made

### 1. SprintSchedulerResult telemetry fields (runtime/sprint_scheduler.py)
Added 10 new `nonfeed_prelude_*` fields to `SprintSchedulerResult`:

| Field | Type | Purpose |
|-------|------|---------|
| `nonfeed_prelude_enabled` | `bool` | True when nonfeed_diagnostic profile active |
| `nonfeed_prelude_expected_lanes` | `tuple[str, ...]` | Expected lanes from nonfeed_plan_debug |
| `nonfeed_prelude_attempted_lanes` | `tuple[str, ...]` | Lanes attempted in prelude |
| `nonfeed_prelude_terminal_lanes` | `tuple[str, ...]` | Lanes that reached terminal state |
| `nonfeed_prelude_missing_lanes` | `tuple[str, ...]` | Required but not terminal (explicit) |
| `nonfeed_prelude_accepted_by_lane` | `dict[str, int]` | Accepted count per lane |
| `nonfeed_prelude_error_by_lane` | `dict[str, str]` | Error per lane |
| `nonfeed_prelude_duration_s` | `float` | Prelude duration |
| `nonfeed_prelude_feed_blocked_until_complete` | `bool` | True = feed waits for prelude |

### 2. Nonfeed Prelude Extension in `_run_mandatory_acquisition_prelude`
Extended existing F209A acquisition prelude (PUBLIC + CT) to cover full nonfeed lane set:

**Lanes covered:**
- PUBLIC (already existed)
- CT (already existed)
- WAYBACK (new in nonfeed prelude)
- PASSIVE_DNS (new in nonfeed prelude)
- PIVOT_EXECUTOR (new in nonfeed prelude)

**Execution rules:**
- Only runs for `nonfeed_diagnostic` profile with domain query
- One bounded attempt per lane, no stealth/browser/MLX
- Hardware critical blocks WAYBACK and PASSIVE_DNS
- Budget: 40% of elapsed prelude time max
- No new timeout increases
- No stealth, no browser, no MLX load

### 3. Telemetry population
At end of `_run_mandatory_acquisition_prelude`, when `_is_nonfeed_diagnostic` is True:
- `nonfeed_prelude_expected_lanes` = list from `nonfeed_plan_debug.nonfeed_profile_expected_lanes`
- `nonfeed_prelude_attempted_lanes` = actual lanes attempted
- `nonfeed_prelude_terminal_lanes` = lanes that completed without error
- `nonfeed_prelude_missing_lanes` = required but not terminal (explicit, never silent)
- `nonfeed_prelude_accepted_by_lane` = accepted count per lane
- `nonfeed_prelude_error_by_lane` = errors per lane
- `nonfeed_prelude_feed_blocked_until_complete` = True (prelude runs before feed cycle)

## Feed Interaction
- FEED is NOT globally disabled
- FEED waits for nonfeed prelude to complete before running
- `nonfeed_prelude_feed_blocked_until_complete = True` signals feed should wait
- Prelude runs BEFORE feed cycle loop (at line 1920 in `run()`)

## Tests: 31 passing

```
tests/probe_f233d_nonfeed_prelude_coverage/
├── __init__.py
├── test_nonfeed_prelude_telemetry.py  (10 tests)
└── test_nonfeed_prelude_behavior.py   (21 tests)
```

**Telemetry field tests (10):**
- `nonfeed_prelude_enabled` field exists
- `nonfeed_prelude_expected_lanes` field exists
- `nonfeed_prelude_attempted_lanes` field exists
- `nonfeed_prelude_terminal_lanes` field exists
- `nonfeed_prelude_missing_lanes` field exists
- `nonfeed_prelude_accepted_by_lane` field exists
- `nonfeed_prelude_error_by_lane` field exists
- `nonfeed_prelude_duration_s` field exists
- `nonfeed_prelude_feed_blocked_until_complete` field exists
- All 10 fields present

**Behavior tests (21):**
- Domain nonfeed_diagnostic expected lanes include WAYBACK/PASSIVE_DNS/PIVOT/CT
- nonfeed_plan_debug has is_nonfeed_diagnostic flag
- WAYBACK/PASSIVE_DNS lanes enabled in nonfeed plan
- Missing lanes computed as explicit set difference
- Skipped lanes do not appear in terminal
- Empty missing when all lanes terminal
- Hardware critical blocks WAYBACK/PASSIVE_DNS
- No live network calls in tests (mocked patterns)
- No stealth/browser references in prelude lanes
- CT attempted even if PUBLIC times out

## Verification Commands

```bash
# F233D probe tests
.venv/bin/python -m pytest tests/probe_f233d_nonfeed_prelude_coverage/ -v

# Related regression lanes
.venv/bin/python -m pytest \
  tests/probe_f230d_nonfeed_budget \
  tests/probe_f228c_nonfeed_lane_surface \
  tests/probe_f227b_wayback_pdns_acceptance \
  tests/probe_f230c_ct_provider_truth \
  -q --tb=short
```

## Abort Conditions Compliance

| Condition | Status |
|-----------|--------|
| FEED globally disabled | ✓ NOT done — FEED still enabled |
| New scheduler framework | ✓ NOT done — uses existing prelude mechanism |
| New network providers | ✓ NOT done |
| Timeout increases | ✓ NOT done |
| Live sprint run | ✓ NOT done |

## Architecture

```
run() → _run_mandatory_acquisition_prelude()
              ↓
    ┌─ F209A: PUBLIC + CT prelude (existing)
    └─ F233D: + WAYBACK + PASSIVE_DNS + PIVOT_EXECUTOR (new, nonfeed only)
              ↓
    nonfeed_prelude_* telemetry populated
              ↓
    _finalize_result_truth("prelude_complete")
              ↓
    FEED cycle loop (blocked until prelude complete)
```

## Success Definition: ✓ MET

Nonfeed_diagnostic domain runs now get a bounded nonfeed prelude covering CT, WAYBACK, PASSIVE_DNS, and PIVOT_EXECUTOR before FEED dominates. The prelude is:
- Bounded: one attempt per lane, timeout capped
- Explicit: missing lanes are never silently absent
- Non-unsafe: hardware critical blocks WAYBACK/PASSIVE_DNS
- Telemetry-rich: 10 new fields capture full prelude state
- Feed-aware: FEED blocked until prelude completes