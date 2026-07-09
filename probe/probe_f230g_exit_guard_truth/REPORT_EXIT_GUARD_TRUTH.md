# EXIT GUARD AND MEASUREMENT TRUTH — SPRINT F230G REPORT

## Verdict: ALL PASS

**Date**: 2026-05-10
**Sprint**: F230G — EXIT GUARD AND MEASUREMENT TRUTH SEAL

---

## Probes

| Probe | Status | Key Finding |
|-------|--------|-------------|
| P2: acquisition_profile parser truth | **PASS** | `if _ap_from_report:` guard prevents parsed value from being overwritten by file re-read returning None |
| P3: prewindup barrier propagation | **PASS** | `result.prewindup_barrier_checked/satisfied = bool(_as.get(...))` reads from `parsed.acquisition_strategy` dict and stamps onto LiveMeasurementResult |
| P4: async _record_scheduler_exit call contract | **PASS** | `async def _record_scheduler_exit` at line 2391; `_finalize_result_truth` awaits it at line 2688; zero bare unawaited call sites; zero asyncio.run in either function |
| exit_guard: Exit guard semantics | **PASS** | `if not getattr(self._result, "return_guard_checked", False):` triggers `_ensure_mandatory_nonfeed_before_return` wrapped in try/except (fail-soft); `return_guard_checked=True` set on entry; `scheduler_exit_guard_checked` mirrors `return_guard_checked` post-attempt |
| telemetry: Telemetry cannot lie | **PASS** | `scheduler_exit_guard_checked = return_guard_checked` and `scheduler_exit_guard_satisfied = return_guard_satisfied`; prewindup fields propagate from `parsed.acquisition_strategy` via `_as.get()` in live_sprint_measurement.py (not sprint_scheduler.py) |
| regression: Other lanes | **PASS** | F223D (2 files), F230A (2 files), F230D (3 files) — all reference lanes present |

---

## Fixes Verified

### P2 — acquisition_profile parser truth (`live_sprint_measurement.py` lines ~1166–1169)
Before fix: `_ap_from_report=None` would overwrite `result.acquisition_profile` (the parsed canonical value).
After fix: `if _ap_from_report: result.acquisition_profile = _ap_from_report` — only overwrites when file re-read actually returns a value.

### P3 — prewindup_barrier propagation (`live_sprint_measurement.py` lines ~1137–1140)
```python
_as = result.acquisition_strategy or {}
result.prewindup_barrier_checked = bool(_as.get('prewindup_barrier_checked', False))
result.prewindup_barrier_satisfied = bool(_as.get('prewindup_barrier_satisfied', False))
```
`acquisition_strategy` comes from `parsed = _parse_sprint_report(...)` so the canonical report value is preserved. False/None cases remain truthful via `bool()` cast with False default.

### P4 — async _record_scheduler_exit call contract (`sprint_scheduler.py`)
- `_record_scheduler_exit` is declared `async def` (line 2391)
- `_finalize_result_truth` calls `await self._record_scheduler_exit(...)` at line 2688
- Zero bare `self._record_scheduler_exit(...)` calls without `await` anywhere in the file
- Zero `asyncio.run()` inside either `_record_scheduler_exit` or `_finalize_result_truth`

### Exit Guard Semantics (`_record_scheduler_exit` at lines ~2391–2423)
```python
if not getattr(self._result, "return_guard_checked", False):
    self._result.return_guard_checked = True   # set BEFORE attempt
    try:
        guard_satisfied = self._ensure_mandatory_nonfeed_before_return()
        self._result.return_guard_satisfied = guard_satisfied
    except BaseException:
        # fail-soft: caught, not recorded as satisfied
        pass
```
`scheduler_exit_guard_checked` mirrors `return_guard_checked` (True only after attempt or already true). No duplicate guard attempts when `return_guard_checked=True`.

### Telemetry Integrity
All three satellite assignments are present and correct:
- `self._result.scheduler_exit_guard_checked = self._result.return_guard_checked`
- `self._result.scheduler_exit_guard_satisfied = self._result.return_guard_satisfied`
- `result.prewindup_barrier_checked = bool(_as.get('prewindup_barrier_checked', False))` (in live_sprint_measurement.py, from parsed acquisition_strategy)

---

## JSON Output

`probe_f230g_exit_guard_truth/exit_guard_truth.json` — full structured output with all probe details.