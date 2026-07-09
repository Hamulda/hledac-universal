# Sprint F209B-C: Export and Core Prelude Pass-Through

## Problem

Sprint F209A added `acquisition_prelude_*` fields to `SprintSchedulerResult`, but they were not extracted in `_scheduler_result_acquisition_payload()` and therefore never flowed through to:
- `ExportHandoff.scorecard`
- `ExportHandoff.canonical_run_summary`
- `canonical_run_summary` top-level key in JSON report
- Final `~/.hledac/reports/*_report.json`

## Solution

### Core: `core/__main__.py`

Extended `_scheduler_result_acquisition_payload()` to extract all 9 `acquisition_prelude_*` fields from `SprintSchedulerResult` and return them as top-level dict keys:

| Field | Type | Source |
|-------|------|--------|
| `acquisition_prelude_checked` | bool | `result.acquisition_prelude_checked` |
| `acquisition_prelude_ran` | bool | `result.acquisition_prelude_ran` |
| `acquisition_prelude_required_lanes` | list | `result.acquisition_prelude_required_lanes` |
| `acquisition_prelude_terminal_lanes` | list | `result.acquisition_prelude_terminal_lanes` |
| `acquisition_prelude_missing_lanes` | list | `result.acquisition_prelude_missing_lanes` |
| `acquisition_prelude_skipped_lanes` | dict | `result.acquisition_prelude_skipped_lanes` |
| `acquisition_prelude_errors` | dict | `result.acquisition_prelude_errors` |
| `acquisition_prelude_duration_s` | float | `result.acquisition_prelude_duration_s` |
| `acquisition_prelude_reason` | str | `result.acquisition_prelude_reason` |

The spread `**_scheduler_result_acquisition_payload(result, ...)` in `report_dict` carries these fields to:
- Top-level of `report_dict` (goes to `canonical_run_summary` nested dict AND as top-level key)
- `ExportHandoff.scorecard` via `**_acq_payload` in scorecard construction

### Export: `export/sprint_exporter.py`

Extended `_get_acquisition_truth()` to pass through all 9 `acquisition_prelude_*` fields using the same 3-source priority order:

1. `eh.scorecard` (highest priority)
2. `eh.canonical_run_summary`
3. `eh.runtime_truth`

Applied via `_make_serializable()` for dict/list values, direct assignment for primitives.

## Invariants

- Fails soft — missing fields produce `None`/empty defaults, never crash
- Scorecard priority over canonical_run_summary over runtime_truth
- Do not overwrite existing non-empty values
- No scheduler execution, no store read, no network, no MLX load
- Fields reach final JSON via `sanitized_obj` pass-through in `export_sprint()`

## Files Modified

| File | Change |
|------|--------|
| `core/__main__.py` | Added 9 acquisition_prelude_* fields to `_scheduler_result_acquisition_payload()` return + docstring |
| `export/sprint_exporter.py` | Added `_prelude_fields` loop to `_get_acquisition_truth()` + updated docstring |

## Files Created

- `probe_f209b_export_prelude_pass_through/test_f209b_export_prelude_pass_through.py` — 22 probe tests
- `probe_f209b_export_prelude_pass_through/export_prelude_pass_through.json` — test manifest
- `probe_f209b_export_prelude_pass_through/REPORT_EXPORT_PRELUDE_PASS_THROUGH.md` — this report

## Verification

```bash
pytest tests/probe_f209b_export_prelude_pass_through/ -v
pytest tests/probe_f208j_export_pass_through/ tests/probe_f208j_core_handoff_scorecard/ -q
```
