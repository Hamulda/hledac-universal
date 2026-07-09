# F232E Probe Report — KPI Null-Safe Public Pipeline

**Date:** 2026-05-11
**Sprint:** F232E
**Focus:** Null-safe public pipeline KPI derivation with acquisition_report fallback

## Problem

F232 crash scenario: `public_pipeline` is `None` but `acquisition_report.public_stage_counters` provides the canonical fallback.

## Changes Made

### `benchmarks/live_measurement_kpi.py`

1. **Added `_as_mapping()` helper** (line 89-93)
   - Returns `{}` for `None` or non-dict values
   - Ensures safe `.get()` calls on acquisition_report

2. **Null-safe public pipeline handling** (lines 621-651)
   - `_pp` local bound to `inp.public_pipeline` (may be None)
   - `_ar_psc` extracted from `acquisition_report.get("public_stage_counters")` via `_as_mapping`
   - `_ar_pts` pulled from `_ar_psc.get("terminal_stage", "")`
   - `_public_terminal_stage`: pipeline dict wins, else AR fallback
   - `_public_candidate_ledger_summary`: pipeline dict wins, else AR counters
   - `_public_surface_present`: True when either pipeline or AR provides counters

3. **Added `return _result_dict`** at function exit (line 848)

### `benchmarks/live_measurement_schema.py`

No changes — `LiveKpiInput` already had correct null-safe field types.

## Test Results

```
16 passed in 0.72s
```

### Test Coverage

| Test Class | Tests | Status |
|---|---|---|
| `TestAsMapping` | 3 | PASS |
| `TestPublicPipelineNoneAcquisitionReportPresent` | 4 | PASS |
| `TestPublicPipelineNoneNoAcquisitionReport` | 2 | PASS |
| `TestPublicPipelineDictWins` | 2 | PASS |
| `TestDiscoveryErrorPreserved` | 2 | PASS |
| `TestResearchQualityScoreCallable` | 1 | PASS |
| `TestF232StyleCompletedSprint` | 2 | PASS |

### Key Scenarios Verified

- **F232 crash scenario**: `public_pipeline=None` + AR with counters — no crash, counters used
- **Both None**: `public_pipeline=None` + `acquisition_report=None` — zeros, no crash
- **Pipeline wins**: dict `public_pipeline` takes priority over AR counters
- **DISCOVERY_ERROR**: terminal stage preserved, NO stage counters set (discovery_empty=0)
- **Research quality callable**: `public_candidates_seen`, `ct_clues_seen`, `claims_extracted_count`, `claims_polarity_mix` all present
- **F232-style completed sprint**: stamps KPI correctly with all downstream fields

## Regression

- `probe_f221d_quality_surface_consistency`: 12 passed
- `probe_f224c_nonfeed_evidence_contract`: 15 passed

## Not Changed

- Quality formula (no changes to `score_research_quality`)
- Scheduler (no changes to `SprintScheduler`)
- Live sprint (no live sprint run)
- Exception suppression (no global suppressions)
- Nonfeed evidence fabrication (none added)