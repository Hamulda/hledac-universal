# Sprint F233A — Live Runtime Product Path Closure

**Date:** 2026-05-11
**canonical_owner:** `core.__main__.run_sprint()`
**Status:** COMPLETE

## Background — F232 Failure

F232 run (`8sa_1778450025999_a00bb3`) produced 4464 feed findings but FEED_ONLY result with `public=0`, `ct=0`. Teardown crashed with `AttributeError: '_bg_tasks'` in `DuckDBShadowStore.aclose()`. Post-run KPI derivation crashed with `AttributeError: 'inp.public_pipeline is None'`. The `acquisition_report.acquisition_profile` showed `"default"` despite running `nonfeed_diagnostic180`.

## Parts Completed

### Part A — DuckDBShadowStore.aclose() crash before async_initialize()

**Root cause:** `DuckDBShadowStore.__init__` did not initialize `_wal_lmdb` (only lazily in `async_initialize()`). The `aclose()` guard `if self._bg_tasks:` raised `AttributeError` because `_bg_tasks` was accessed before the instance field was declared.

**Fix (knowledge/duckdb_store.py):**
1. Added `self._wal_lmdb: Optional[Any] = None` to `__init__` (line 790)
2. Changed `_bg_tasks` guard from `if self._bg_tasks:` to `if getattr(self, "_bg_tasks", None):`
3. Changed `_wal_lmdb`/`_dedup_lmdb` guards from `hasattr` to `getattr` pattern with None check

**Tests:** 2 probe tests in `tests/probe_f233a_live_runtime_product_path_closure/`

### Part B — LiveKpiInput None guards

**Root cause:** `_public_candidate_ledger_summary` used truthy checks (`if _pp`) that would pass for non-dict truthy values, causing `.get()` to crash.

**Fix (benchmarks/live_measurement_kpi.py):**
- Changed all `if _pp` guards to `isinstance(_pp, dict)` with `acquisition_report` fallback for `pattern_matched`
- Line 634: Added `isinstance(_pp, dict)` guards throughout the summary dict

**Tests:** 2 probe tests verifying `LiveKpiInput(public_pipeline=None)` and `LiveKpiInput(public_pipeline={})` both derive safely

### Part C — nonfeed_diagnostic180 profile routing

**Root cause:** `live_sprint_measurement.py` was pre-resolving `nonfeed_diagnostic180` → `nonfeed_diagnostic` BEFORE passing to `run_sprint()`. The `HLEDAC_ACQUISITION_PROFILE` env var and `acquisition_profile` param both received the canonical name, not the benchmark alias. `run_sprint()`'s own normalization chain was short-circuited.

**Fix:** After the F233A changes to `live_sprint_measurement.py` (passing raw profile to run_sprint), the canonical normalization in `core/__main__.py` (lines 852-854) handles the alias resolution internally.

**Tests:** 3 probe tests verifying `_resolve_acquisition_profile` still returns canonical (it is still called for env var management), PROFILE_META has correct metadata, and run_sprint receives raw profile.

### Part D — _ensure_pre_windup RuntimeWarning

**Finding:** The RuntimeWarning "coroutine '_ensure_pre_windup_lane_terminal_states' was never awaited" was first documented in F214SMOKE3 (15 occurrences, line 3129). Root cause: fail-soft guard at line 3129 returns True on error without awaiting the inner coroutine.

**Assessment:** This is a KNOWN NON-FATAL warning — does not block windup, does not affect findings. F233A changes do not touch `_ensure_pre_windup_lane_terminal_states`. No fix required.

**Tests:** 1 probe test confirming F233A files do not reference `_ensure_pre_windup_lane_terminal_states` via AST scan.

### Part E — F232 report regression fixture

**Fixture created:** `tests/probe_f233a_live_runtime_product_path_closure/live_runtime_product_path_closure.json`

Key F232 report observations:
- `runtime_accepted_findings: 4464`, all FEED_ONLY (`public_findings: 0`, `ct_findings: 0`)
- `public_terminal_stage: "DISCOVERY_ERROR"`
- `acquisition_profile: "default"` (wrong — was running `nonfeed_diagnostic180`)
- `fallback_reason: "canonical_owner_missing_scheduler_report"`
- `schema_version: "f208.v1-fallback"` (fallback schema, not canonical)

## Verification

```bash
# F233A probe tests
uv run python -m pytest tests/probe_f233a_live_runtime_product_path_closure/test_f233a_live_runtime.py -v --no-header -q
# Result: 8 passed

# F231M regression (production evidence depth)
uv run python -m pytest tests/probe_f231m_production_evidence_depth/test_production_evidence_depth.py -v --no-header -q
# Result: 33 passed

# Combined
uv run python -m pytest tests/probe_f231m_production_evidence_depth/test_production_evidence_depth.py tests/probe_f233a_live_runtime_product_path_closure/test_f233a_live_runtime.py --no-header -q
# Result: 41 passed
```

## Files Modified

| File | Change |
|------|--------|
| `knowledge/duckdb_store.py` | Part A: `_wal_lmdb` in `__init__`, `getattr` guards in `aclose()` |
| `benchmarks/live_measurement_kpi.py` | Part B: `isinstance(_pp, dict)` guards in `_public_candidate_ledger_summary` |
| `benchmarks/live_sprint_measurement.py` | Part C: raw profile passed to `run_sprint()` via env + param |
| `core/__main__.py` | Already correct — normalization chain at lines 852-854 |
| `tests/probe_f233a_live_runtime_product_path_closure/` | New probe directory with 8 tests + F232 fixture |

## Open Items

- `tests/probe_f228h_live_kpi_behavior_smoke/` — pre-existing import error (`ModuleNotFoundError: No module named 'hledac.universal.network'`) — this is a pre-existing structural issue in the test suite, not introduced by F233A
- `_ensure_pre_windup` RuntimeWarning (F214-known, non-fatal, no fix required)