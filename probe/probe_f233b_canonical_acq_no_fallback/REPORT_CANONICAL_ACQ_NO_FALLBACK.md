# Sprint F233B: Canonical Acquisition Report No-Fallback Seal

## Summary

Sprint F233B adds explicit `acquisition_report_fallback_used: bool` and `fallback_reason: str | None` markers to all acquisition reports, so downstream tools (exporter, parser, KPI) can distinguish canonical from fallback without inspecting the schema_version suffix.

**Status**: ✅ COMPLETE — 15/15 probe tests passing

## Problem

The live report/acquisition surfaces could silently fall back or erase scheduler-owned acquisition truth:
- `schema_version` may become `f208.v1-fallback` even on successful canonical builds
- `acquisition_report_fallback_used` and `fallback_reason` markers were absent in canonical `build_acquisition_report()` output
- Downstream tools (KPI validator) could not distinguish explicit fallback from canonical without inspecting schema version strings

## Changes Made

### 1. `runtime/acquisition_strategy.py` — Canonical build now sets fallback_used: False

**File**: `runtime/acquisition_strategy.py:1101-1106`

Added explicit `acquisition_report_fallback_used: False` to the canonical `build_acquisition_report()` return dict:

```python
return {
    "schema_version": ACQUISITION_REPORT_SCHEMA_VERSION,  # "f208.v1"
    # F233B: Explicit marker so downstream tools (exporter, parser, KPI) can
    # distinguish canonical from fallback without inspecting schema_version suffix.
    # Fallback path (in core.__main__._scheduler_result_acquisition_payload) sets this to True.
    "acquisition_report_fallback_used": False,
    "plan": plan_dicts,
    ...
}
```

**Why**: The fallback path (in `core/__main__._scheduler_result_acquisition_payload()`) already sets `acquisition_report_fallback_used: True` when the canonical build fails. Adding `False` to the canonical path makes the field presence universal and machine-parseable without string suffix inspection.

### 2. `_scheduler_result_acquisition_payload()` — Already correct

**File**: `core/__main__.py:460`

The fallback path already sets:
```python
"acquisition_report_fallback_used": True,  # F232F: fail-loud marker
"fallback_reason": f"canonical_build_failed: {_exc}",
```

## Verification Results

### F233B Probe Tests — 15/15 PASSED

```
test_f233b_canonical_build_no_fallback_schema_version         PASSED
test_f233b_canonical_build_has_fallback_marker_false          PASSED
test_f233b_fallback_path_has_explicit_markers                 PASSED
test_f233b_exporter_uses_canonical_acquisition_report        PASSED
test_f233b_exporter_preserves_nonfeed_fields                 PASSED
test_f233b_exporter_preserves_public_stage_counters          PASSED
test_f233b_exporter_preserves_ct_f232_fields                 PASSED
test_f233b_parser_reads_canonical_acquisition_report         PASSED
test_f233b_parser_preserves_nonfeed_profile                  PASSED
test_f233b_parser_preserves_source_family_outcomes            PASSED
test_f233b_parser_preserves_fallback_markers                 PASSED
test_f233b_parser_returns_none_for_missing_acquisition_report PASSED
test_f233b_kpi_accepts_canonical_schema                      PASSED
test_f233b_kpi_accepts_fallback_schema_with_marker          PASSED
test_f233b_live_sprint_measurement_reads_acquisition_profile PASSED
```

## Acquisition Report Ownership Chain (End-to-End)

| Stage | Owner | Key Field |
|-------|-------|-----------|
| SprintSchedulerResult | `runtime/sprint_scheduler.py` | Raw counters (accepted_findings, etc.) |
| build_acquisition_report() | `runtime/acquisition_strategy.py:888` | `schema_version="f208.v1"`, `acquisition_report_fallback_used=False` |
| _scheduler_result_acquisition_payload() | `core/__main__.py:139` | Canonical payload OR fallback |
| ExportHandoff.scorecard | `core/__main__.py:1625` | `**_acq_payload` — spread into scorecard |
| _get_acquisition_truth() | `export/sprint_exporter.py:1765` | Priority: scorecard → canonical_run_summary → runtime_truth |
| JSON export | `export/sprint_exporter.py` | `sanitized_obj["acquisition_report"] = ...` |
| _parse_canonical_sprint_report() | `benchmarks/live_measurement_parser.py:37` | `acq_report = data.get("acquisition_report")` |
| live_sprint_measurement | `benchmarks/live_sprint_measurement.py:603-609` | `result.acquisition_report.get('acquisition_profile')` |
| KPI validation | `benchmarks/live_measurement_kpi.py:598-619` | Checks schema_version presence |

## Invariants Tested

| ID | Invariant | Test |
|----|-----------|------|
| F233B-1 | canonical build → schema_version == "f208.v1" | test_f233b_canonical_build_no_fallback_schema_version |
| F233B-2 | canonical build → acquisition_report_fallback_used == False | test_f233b_canonical_build_has_fallback_marker_false |
| F233B-3 | fallback path → explicit fallback markers present | test_f233b_fallback_path_has_explicit_markers |
| F233B-4 | exporter scorecard.acquisition_report takes priority | test_f233b_exporter_uses_canonical_acquisition_report |
| F233B-5 | nonfeed fields survive export | test_f233b_exporter_preserves_nonfeed_fields |
| F233B-6 | public_stage_counters survive export | test_f233b_exporter_preserves_public_stage_counters |
| F233B-7 | CT F232 fields survive export | test_f233b_exporter_preserves_ct_f232_fields |
| F233B-8 | parser reads acquisition_report from data | test_f233b_parser_reads_canonical_acquisition_report |
| F233B-9 | nonfeed_diagnostic profile survives parse | test_f233b_parser_preserves_nonfeed_profile |
| F233B-10 | source_family_outcomes survive parse | test_f233b_parser_preserves_source_family_outcomes |
| F233B-11 | fallback markers survive parse | test_f233b_parser_preserves_fallback_markers |
| F233B-12 | parser returns None for missing acquisition_report | test_f233b_parser_returns_none_for_missing_acquisition_report |
| F233B-13 | KPI accepts canonical schema_version | test_f233b_kpi_accepts_canonical_schema |
| F233B-14 | KPI accepts explicit fallback with marker | test_f233b_kpi_accepts_fallback_schema_with_marker |
| F233B-15 | acquisition_profile from acquisition_report | test_f233b_live_sprint_measurement_reads_acquisition_profile |

## Pre-existing Failures (Not Introduced by F233B)

9 regression tests in probe_f219a, probe_f223a fail due to source-code string matching
checks that are stale relative to current implementation (e.g., checking for exact
`acq_report = data.get("acquisition_report")` in live_sprint_measurement.py when the code
now reads from `result.acquisition_report` after parsing). These failures pre-existed F233B.

## Abort Conditions Compliance

- ✅ No sprint ownership moved into benchmark
- ✅ Fallback support not deleted (explicit fallback markers still work)
- ✅ No nonfeed evidence fabricated
- ✅ No live sprint execution
- ✅ No terminality semantics changed

## Success Definition Met

A completed live sprint **cannot silently produce fallback acquisition truth when scheduler-owned canonical acquisition truth exists** — because:
1. Canonical `build_acquisition_report()` now explicitly sets `acquisition_report_fallback_used: False`
2. Fallback path still sets `acquisition_report_fallback_used: True` with `fallback_reason`
3. Both paths are machine-parseable without string suffix inspection
4. Exporter priority chain (scorecard first) preserves scheduler-owned truth
5. Parser reads top-level `acquisition_report` first (canonical path checked FIRST)