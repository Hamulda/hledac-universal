# Sprint F232F: Canonical Acquisition Report Owner Seal — REPRO

## Problem

F232 actual sprint report contained:
```
acquisition_report.schema_version = "f208.v1-fallback"
fallback_reason = "canonical_owner_missing_scheduler_report"
acquisition_profile = "default"
nonfeed_priority_enabled = false
nonfeed_plan_debug = null
```
even though the live run was intended as `nonfeed_diagnostic180`.

Downstream tools (live_measurement_parser) read this fallback report and lose
mission/nonfeed truth.

## Root Cause Analysis

`SprintScheduler.run()` returns only `SprintSchedulerResult` (no `acquisition_report` field).
`acquisition_report` is built in `_scheduler_result_acquisition_payload()` which calls
`build_acquisition_report()` inside `try/except`.

When ANY exception escapes the `try` block (line 326), the `except` block (line 427)
creates a fallback with hard-coded values:
- `acquisition_profile = "default"` (ignores plan profile)
- `nonfeed_plan_debug = None` (ignores _nd dict)
- `nonfeed_profile_expected_lanes = []` (ignores plan lanes)
- `fallback_reason = "canonical_owner_missing_scheduler_report"` (misleading message)

## F232F Fix Applied

**File: `core/__main__.py:427-464`**

The `except` block was updated to:

1. **Capture actual exception** in fallback_reason (not misleading static message):
   ```python
   "fallback_reason": f"canonical_build_failed: {_exc}",
   ```

2. **Add fail-loud marker** for downstream detection:
   ```python
   "acquisition_report_fallback_used": True,
   ```

3. **Preserve _nd values** when available (not hard-coded defaults):
   ```python
   _fallback_profile = _nd.get("acquisition_profile", "default") if _nd else "default"
   "acquisition_profile": _fallback_profile,  # from _nd, not hard-coded "default"
   "nonfeed_plan_debug": _nd,  # preserve _nd when available
   "nonfeed_priority_enabled": _nd.get("nonfeed_priority_enabled", False) if _nd else False,
   "nonfeed_profile_expected_lanes": _nd.get("nonfeed_profile_expected_lanes", []) if _nd else [],
   ```

4. **Preserve plan when available**:
   ```python
   "plan": getattr(_plan, "plans", None) if _plan else None,
   ```

## Result

- Fallback report now has `acquisition_report_fallback_used=True` — gate/quality
  tools can detect degraded reports
- Fallback preserves nonfeed profile/lanes from _nd when build fails (not hard-coded)
- `fallback_reason` now contains actual exception message (e.g., "canonical_build_failed: AttributeError: 'NoneType' object has no attribute 'get'")
- Successful live sprints no longer produce `f208.v1-fallback` unless truly absent

## Files Modified

- `core/__main__.py:427-464` — fallback construction (F232F fix)

## Tests Created

- `tests/probe_f232f_canonical_acq_report/test_canonical_acq_report.py` — 8 tests, all passing

## Verification

```bash
.venv/bin/python -m pytest tests/probe_f232f_canonical_acq_report/ -v -o "addopts="
```

Expected: 8 passed