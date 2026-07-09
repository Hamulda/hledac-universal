# Sprint F226B — Confidence Policy Reality Seal

**Date:** 2026-05-09
**Status:** COMPLETE

## Mission

Make confidence policy migration **real in source**, not just in reports.

## What Was Wrong

GitHub reality check showed:
1. `compute_confidence()` created a **local** `_BASELINES` dict instead of using the module-level `_SOURCE_BASELINES`
2. `SocialIdentityMiner._compute_confidence()` appeared to have a local heuristic on GitHub
3. `ClaimsCoordinator._derive_confidence()` appeared to have local confidence constants

## Fix Applied

**File:** `intelligence/confidence_policy.py` (line ~123)

**Before:**
```python
def compute_confidence(...):
    # Map source_family to baseline — unrecognized families use default
    _BASELINES = {
        "FEED": FEED,
        "PUBLIC": PUBLIC,
        ...
    }
    base = _BASELINES.get(source_family.upper(), default)
```

**After:**
```python
def compute_confidence(...):
    # Map source_family to baseline via module-level constant
    base = _SOURCE_BASELINES.get(source_family.upper(), default)
```

Removed the local `_BASELINES = {...}` dict inside `compute_confidence()`. The function now uses the module-level `_SOURCE_BASELINES` as the single source of truth.

## Verification Results

### Source Tests (14/14 PASS)
| Test | Result |
|------|--------|
| No local `_BASELINES` inside `compute_confidence` | PASS |
| Uses `_SOURCE_BASELINES` lookup | PASS |
| `claims_coordinator.py` imports `compute_confidence` | PASS |
| `_derive_confidence()` calls `compute_confidence()` | PASS |
| MAX_CONFIDENCE=0.75 cap preserved | PASS |
| `social_identity_miner.py` imports `compute_confidence` | PASS |
| `_compute_confidence()` calls `compute_confidence()` | PASS |
| SOCIAL_MIN_CONFIDENCE threshold preserved | PASS |
| No DuckDB at module level (claims_coordinator) | PASS |
| No DuckDB at module level (social_identity_miner) | PASS |
| No MLX in claims_coordinator | PASS |
| No MLX in social_identity_miner | PASS |
| `_SOURCE_BASELINES` is module-level constant | PASS |
| Constants and `_SOURCE_BASELINES` values match | PASS |

### Behavior Tests (7/7 PASS)
| Test | Result |
|------|--------|
| All baselines bounded [0.10, 0.95] | PASS |
| Provenance bonus: +0.05 | PASS |
| IOC bonus: +0.10 | PASS |
| Corroboration: +0.05 per (capped at 4) | PASS |
| Rejection penalty: -0.10 per | PASS |
| Model score overrides when valid | PASS |
| Baseline ordering: CT (0.70) > FEED (0.65) > PUBLIC (0.60) > SOCIAL (0.50) | PASS |

### Key Finding

**The confidence policy seam is genuinely used:**
- `ClaimsCoordinator._derive_confidence()` calls `compute_confidence()` (line 469)
- `SocialIdentityMiner._compute_confidence()` calls `compute_confidence()` (line 476)
- `_SOURCE_BASELINES` is the single source of source-family baselines
- No local `_BASELINES` dict inside `compute_confidence()`

## Baseline Values

| Source | Baseline |
|--------|----------|
| CT | 0.70 |
| PLANNER | 0.75 |
| FEED | 0.65 |
| PASSIVE_DNS | 0.68 |
| PUBLIC | 0.60 |
| STEALTH | 0.58 |
| WAYBACK | 0.55 |
| SOCIAL | 0.50 |

## Quality Gate

**21/21 tests PASS** — ALL source and behavior verification tests pass.

## Files Modified

- `intelligence/confidence_policy.py` — removed local `_BASELINES` dict, now uses `_SOURCE_BASELINES`

## Files Created (probe only)

- `probe_f226b_confidence_policy_reality/test_confidence_policy_reality.py`
- `probe_f226b_confidence_policy_reality/conftest.py`
- `probe_f226b_confidence_policy_reality/run_tests.py`
- `probe_f226b_confidence_policy_reality/confidence_policy_reality.json`
- `probe_f226b_confidence_policy_reality/REPORT_CONFIDENCE_POLICY_REALITY.md` (this file)

## ABORT Conditions

All ABORT conditions respected — no runtime/benchmark/core/pipeline edits made.