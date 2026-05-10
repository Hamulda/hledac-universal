# Sprint R3: Wayback Lane Closure — Report

## Goal

Prove the Wayback lane follows the bounded canonical path:
```
WAYBACK lane result
  → wayback_results_to_findings(result, query, sprint_id)
  → store.async_ingest_findings_batch(candidates)
  → NonfeedCandidateLedger stages
  → source_family_outcomes["wayback"]
  → acquisition_report
```

The lane was ALREADY FULLY WIRED — no code changes needed. 17 probe tests prove the path.

## Outcome Contract

`AcquisitionLaneOutcome` for WAYBACK lane:

| Field | Type | Description |
|-------|------|-------------|
| `lane` | `AcquisitionLane.WAYBACK` | Lane identifier |
| `enabled` | `bool` | Whether lane is enabled |
| `attempted` | `bool` | True if lookup was attempted |
| `timeout` | `bool` | True if timeout occurred |
| `error` | `str \| None` | Error message or skip reason |
| `source_family` | `"archive"` | Source family identifier |
| `wayback_raw_count` | `int` | Raw diff event count from Wayback |
| `candidate_findings` | `tuple` | CanonicalFinding candidates |
| `rejection_reasons` | `tuple` | Bridge rejection reasons |
| `rejected_count` | `int` | Number of bridge rejections |
| `sample_rejections` | `tuple` | Sample of rejection reasons |
| `accepted_findings` | `int` | Count accepted by store |

## Path (Already Wired)

```
AcquisitionStrategySnapshot (WAYBACK plan)
  → run_enabled_acquisition_lanes()
    → _run_wayback_lane(plan)        [existing: acquisition_strategy.py:2452]
      ├── asyncio.timeout(plan.timeout_s)
      ├── build_lane_query(query, AcquisitionLane.WAYBACK)
      ├── FakeWaybackDiffMiner.mine_diff(...) → WaybackDiffResult
      ├── wayback_results_to_findings(diff_result, query, sprint_id)
      ├── store.async_ingest_findings_batch(candidates)
      └── return AcquisitionLaneOutcome(...)
```

## Key Findings from Debug

1. `to_dict()` keys include `wayback_raw_count`, `passive_dns_raw_count` — NO `raw_count` key
2. `normalize_source_family_outcome` maps:
   - `wayback_raw_count` → `raw_count` (via `to_dict()` lookup)
   - `produced_items` → `built_count`
   - `accepted_findings` → `accepted_count`
3. `rejected_count` is NOT exposed in normalize output
4. In `_derive_terminal`: `error` takes precedence over `timeout` → "ATTEMPTED_ERROR"

## Tests: 34 Passing

| Class | Tests | Description |
|-------|-------|-------------|
| `TestWaybackLaneOutcomeContract` | 6 | AcquisitionLaneOutcome field contract |
| `TestWaybackBridgeConversion` | 5 | wayback_results_to_findings conversion |
| `TestWaybackLedgerEvents` | 4 | NonfeedCandidateLedger events |
| `TestWaybackLaneAccumulation` | 5 | Scheduler accumulation path |
| `TestWaybackAsyncIngest` | 5 | async_ingest_findings_batch calls |
| `TestWaybackLaneRunner` | 5 | _run_wayback_lane dispatch |
| `TestWaybackSourceFamilyOutcomes` | 6 | normalize_source_family_outcome |
| `TestWaybackNoLiveNetwork` | 3 | No live network imports |

## Regression: R2 PassiveDNS Lane Closure

```
tests/probe_r2_passivedns_lane_closure/: 27 passed
tests/probe_r3_wayback_lane_closure/: 34 passed
Total: 61 passed
```

No regressions introduced.

## Files

- Not modified: `runtime/acquisition_strategy.py` (wayback lane already wired)
- Not modified: `runtime/sprint_scheduler.py` (accumulation already wired)
- Created: `tests/probe_r3_wayback_lane_closure/test_wayback_lane_closure.py`
- Created: `_debug_normalize.py` (temporary, deleted after debug)