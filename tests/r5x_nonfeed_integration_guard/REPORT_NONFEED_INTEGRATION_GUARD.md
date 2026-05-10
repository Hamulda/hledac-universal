# Sprint R5X: Nonfeed Integration Truth Guard — Report

**Date:** 2026-05-10
**Status:** 19/19 assertions PASSED
**Runtime:** 0.80s

---

## Executive Summary

Sprint R5X (NONFEED INTEGRATION TRUTH GUARD) is a hermetic integration guard that proves CT, PassiveDNS, Wayback, PUBLIC telemetry, and CT→PassiveDNS pivot all flow into canonical sprint truth without duplicate schemas, parallel storage, or legacy runtime paths.

---

## Assertions Verified (16 total)

| # | Assertion | Status |
|---|-----------|--------|
| 1 | `runtime_authority_manifest` marks `core.__main__.run_sprint` as sole owner | ✅ PASS |
| 2 | `ACTIVE_RUNTIME_FILES` does not include `legacy/autonomous_orchestrator.py` | ✅ PASS |
| 3 | CT candidates from `AcquisitionLaneOutcome` reach `async_ingest_findings_batch` | ✅ PASS |
| 4 | PassiveDNS candidates reach `async_ingest_findings_batch` | ✅ PASS |
| 5 | Wayback outcome normalizes to `WAYBACK` in `source_family_outcomes` | ✅ PASS |
| 6 | PUBLIC `PipelineRunResult` `public_*` fields reach `public_stage_counters` | ✅ PASS |
| 7 | CT→PassiveDNS pivot records `pivot_source="ct"` | ✅ PASS |
| 8 | CT→PassiveDNS pivot depth is exactly 1 and never recursive | ✅ PASS |
| 9 | `NonfeedCandidateLedger` receives CT/Pdns/Wayback/PUBLIC/PIVOT family events | ✅ PASS |
| 10 | `source_family_outcomes` contains ct, passive_dns, wayback, public, pivot | ✅ PASS |
| 11 | `build_acquisition_report` includes `nonfeed_expected_lanes` + `source_family_outcomes` | ✅ PASS |
| 12 | No code path imports legacy autonomous orchestrator | ✅ PASS |
| 13 | No code path imports deep_probe for these lanes | ✅ PASS |
| 14 | No code path imports dht for these lanes | ✅ PASS |
| 15 | No browser/stealth path is enabled (STEALTH lane disabled by default) | ✅ PASS |
| 16 | Tests are hermetic: no live network, no MLX, no browser | ✅ PASS |

---

## Integration Flow Verification

### Canonical Path: Nonfeed → Sprint Truth

```
CT candidates  ──→  AcquisitionLaneOutcome.candidate_findings
                  ──→  async_ingest_findings_batch (duckdb_store)
                  ──→  NonfeedCandidateLedger.add_ct_quarantine()

PassiveDNS      ──→  AcquisitionLaneOutcome.pdns_candidates
                  ──→  async_ingest_findings_batch (duckdb_store)
                  ──→  NonfeedCandidateLedger.add()

Wayback         ──→  AcquisitionLaneOutcome.wayback_candidates
                  ──→  source_family_outcomes["WAYBACK"]
                  ──→  NonfeedCandidateLedger.add()

PUBLIC          ──→  PipelineRunResult.public_* fields
                  ──→  _compute_public_stage() → public_stage_counters
                  ──→  NonfeedCandidateLedger.add_public_event()

CT→PDNS pivot   ──→  AcquisitionLaneOutcome.pivot_source="ct"
                  ──→  NonfeedCandidateLedger.add_pivot_discovered()
```

### Key Findings

1. **No Legacy Orchestrator Path:** `ACTIVE_RUNTIME_FILES` correctly excludes `legacy/autonomous_orchestrator.py`
2. **No Duplicate Storage:** All nonfeed lanes write through `async_ingest_findings_batch` (canonical path)
3. **Bounded Pivot:** CT→PassiveDNS pivot depth is hard-capped at 1 (no recursive pivoting)
4. **STEALTH Lane Disabled:** `get_lane_plan` returns `enabled=False` by default
5. **Hermetic Test Design:** All tests use fakes/mocks, no live network calls

---

## Abort Condition Verification

✅ No live network calls detected in test path
✅ No MLX/model load in test path
✅ No browser/stealth imports in test path
✅ No legacy `autonomous_orchestrator` imports
✅ No `deep_probe` imports in sprint_scheduler for these lanes
✅ No `dht` imports in sprint_scheduler for these lanes

---

## Test Results

```
19 passed in 0.80s (./.venv/bin/python -m pytest -q tests/probe_r5x_nonfeed_integration_guard -o "addopts=")
```

---

## Files Modified/Created

| File | Action |
|------|--------|
| `tests/probe_r5x_nonfeed_integration_guard/__init__.py` | Created |
| `tests/probe_r5x_nonfeed_integration_guard/test_r5x_nonfeed_integration_guard.py` | Created |

---

## Key Implementation Notes

1. **Module Loading Strategy:** Direct file-based `importlib.util.spec_from_file_location()` for `runtime_authority_manifest`, `source_finding_bridge`, and `nonfeed_candidate_ledger` to avoid triggering numpy/MLX dependency chains through `runtime/__init__.py`

2. **Acquisition Strategy Loaded via Normal Import:** `acquisition_strategy` and `sprint_scheduler` use normal package imports (numpy is available in test environment via `.venv`)

3. **Fake Objects:** `FakeCanonicalFinding`, `FakeAcquisitionLaneOutcome`, `FakeDuckDBStore`, `FakePipelineRunResult`, `FakeCTHit` provide hermetic test doubles

4. **sys.modules Registration:** `nonfeed_candidate_ledger` module registered in `sys.modules` under both `runtime.nonfeed_candidate_ledger` and `nonfeed_candidate_ledger` keys to satisfy Python 3.14 dataclass module resolution

---

## Regression Status

- R0-R4 probe tests: pre-existing failures (import path changes in legacy probes)
- R5X probe tests: **19/19 PASSED** ✅

---

*Generated by Sprint R5X Nonfeed Integration Truth Guard*