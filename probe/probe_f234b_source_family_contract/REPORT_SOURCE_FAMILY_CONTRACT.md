# F234B: Source Family Outcome Contract Closure — REPORT

**Date:** 2026-05-11
**Sprint:** F234B
**Status:** COMPLETE — all 23 probe tests passing, 51 regression tests passing

---

## Problem

Canonical `source_family_outcomes` was dropping PUBLIC and CT lanes when `raw_count=0` and `accepted_count=0`, even when those lanes had terminal stages, errors, or were planned/scheduled.

**Root cause** — `core/__main__.py:_scheduler_result_acquisition_payload()` gated PUBLIC/CT emission on `public_discovered > 0 OR public_accepted_findings > 0` (PUBLIC) and `ct_log_discovered > 0 OR ct_log_accepted_findings > 0` (CT). Zero-count but error/timeout/planned lanes were silently omitted.

---

## Changes

### `core/__main__.py` — PUBLIC block (lines 204-226)

**Before:**
```python
if getattr(result, "public_discovered", 0) > 0 or getattr(result, "public_accepted_findings", 0) > 0:
    _pub_raw = {...}
    _sfo_list.append(normalize_source_family_outcome("PUBLIC", _pub_raw))
```

**After:**
```python
_pub_has_outcome = (
    getattr(result, "public_discovered", 0) > 0
    or getattr(result, "public_accepted_findings", 0) > 0
    or bool(getattr(result, "public_terminal_stage", ""))
    or bool(getattr(result, "public_error", ""))
    or getattr(result, "public_stage_counters", None) is not None
)
if _pub_has_outcome:
    _pub_raw = {
        "family": "PUBLIC",
        "attempted": True,
        ...
        "error": getattr(result, "public_error", None) or getattr(result, "public_terminal_stage", "") or None,
        "timeout": getattr(result, "public_terminal_stage", "") == "DISCOVERY_TIMEOUT",
        ...
    }
```

### `core/__main__.py` — CT block (lines 228-246)

**Before:**
```python
if getattr(result, "ct_log_discovered", 0) > 0 or getattr(result, "ct_log_accepted_findings", 0) > 0:
    _ct_raw = {...}
    _sfo_list.append(normalize_source_family_outcome("CT", _ct_raw))
```

**After:**
```python
_ct_has_outcome = (
    getattr(result, "ct_log_discovered", 0) > 0
    or getattr(result, "ct_log_accepted_findings", 0) > 0
    or bool(getattr(result, "ct_terminal_stage", ""))
    or bool(getattr(result, "ct_log_error", ""))
    or getattr(result, "ct_planned", False)
    or getattr(result, "ct_scheduled", False)
    or getattr(result, "ct_request_attempted", False)
)
if _ct_has_outcome:
    _ct_raw = {
        "family": "CT",
        "attempted": getattr(result, "ct_request_attempted", False) or getattr(result, "ct_scheduled", False) or getattr(result, "ct_planned", False),
        ...
        "error": getattr(result, "ct_log_error", None) or getattr(result, "ct_terminal_stage", "") or None,
        "timeout": getattr(result, "ct_terminal_stage", "") == "request_timeout",
        ...
    }
```

---

## PUBLIC Outcome Rule

| `public_terminal_stage` | `attempted` | `raw_count` | `accepted_count` | `error` | `timeout` | `terminal_state` |
|---|---|---|---|---|---|---|
| `DISCOVERY_TIMEOUT` | True | 0 | 0 | `DISCOVERY_TIMEOUT` | **True** | `ATTEMPTED_TIMEOUT` |
| `DISCOVERY_ERROR` | True | 0 | 0 | `DISCOVERY_ERROR` | False | `ATTEMPTED_ERROR` |
| `DISCOVERY_ZERO_RESULTS` | True | 0 | 0 | `DISCOVERY_ZERO_RESULTS` | False | `ATTEMPTED_ERROR` |
| (with findings) | True | N | M | (error msg) | False | `ATTEMPTED_ACCEPTED` |

## CT Outcome Rule

| Condition | `attempted` | `error` | `timeout` | `terminal_state` |
|---|---|---|---|---|
| `ct_planned` but provider unavailable | True | `provider_unavailable` | False | `ATTEMPTED_ERROR` |
| `ct_request_attempted` with HTTP 502 | True | `http_502` | False | `ATTEMPTED_ERROR` |
| `ct_terminal_stage=request_timeout` | True | (stage message) | **True** | `ATTEMPTED_TIMEOUT` |
| `ct_raw=0 accepted=0` | True | (stage message) | False | varies |
| `ct_scheduled` | True | None | False | varies |

---

## Case Normalization

`normalize_source_family_outcome()` emits `family` as provided (no forced lowercase in the current implementation). Downstream readers (e.g., `evidence_delta_memory._get_source_families`) use case-insensitive comparison (`x["family"].lower()`). Tests verify both uppercase and lowercase are handled.

---

## Test Coverage — 23 probe tests

| # | Test | Description |
|---|---|---|
| 1 | `test_public_discovery_timeout_creates_outcome[DISCOVERY_TIMEOUT]` | PUBLIC DISCOVERY_TIMEOUT → `ATTEMPTED_TIMEOUT` |
| 2 | `test_public_discovery_timeout_creates_outcome[DISCOVERY_ERROR]` | PUBLIC DISCOVERY_ERROR → `ATTEMPTED_ERROR` |
| 3 | `test_public_discovery_timeout_creates_outcome[DISCOVERY_ZERO_RESULTS]` | PUBLIC DISCOVERY_ZERO_RESULTS → `ATTEMPTED_ERROR` |
| 4 | `test_public_zero_with_stage_counters_creates_outcome` | `public_stage_counters` present → outcome emitted |
| 5 | `test_public_with_accepted_findings` | With accepted findings → `ATTEMPTED_ACCEPTED` |
| 6 | `test_ct_planned_provider_unavailable` | CT planned + unavailable → `ATTEMPTED_ERROR` |
| 7 | `test_ct_request_attempted_http_502` | CT HTTP 502 → `ATTEMPTED_ERROR` |
| 8 | `test_ct_timeout` | CT timeout → `ATTEMPTED_TIMEOUT`, `timeout=True` |
| 9 | `test_ct_raw_zero_accepted_zero_not_disappeared` | CT raw=0 accepted=0 stays in outcomes |
| 10 | `test_lane_outcome_zero_accepted_still_appears` | AcquisitionLaneOutcome with accepted=0 survives |
| 11 | `test_normalize_lowercases_family` | Family normalization works |
| 12 | `test_case_insensitive_reader_sees_both_cases` | Uppercase/lowercase both visible to readers |
| 13 | `test_acquisition_report_includes_public_when_terminal_stage_exists` | `public_terminal_stage` triggers PUBLIC outcome |
| 14 | `test_acquisition_report_includes_ct_when_terminal_stage_exists` | `ct_terminal_stage` triggers CT outcome |
| 15 | `test_terminal_state_case_insensitive` | Schema handles mixed-case terminal states |
| 16 | `test_delta_memory_sees_ct_public_attempted_true` | `evidence_delta_memory._get_source_families` sees CT/PUBLIC |
| 17 | `test_delta_memory_sees_ct_public_from_uppercase` | Uppercase CT/PUBLIC visible to delta memory |
| 18 | `test_rqs_family_count_from_sfo` | `research_quality_score` counts all attempted families |
| 19 | `test_fallback_not_canonical` | Fallback reports stay explicit, not canonical |
| 20 | `test_no_live_network_imports` | No live-fetch imports in `__main__` |
| 21 | `test_no_mlx_in_scheduler_result_payload` | No MLX import in `__main__` |
| 22 | `test_no_legacy_autonomous_orchestrator_in_payload` | No legacy orchestrator import |
| 23 | `test_sprint_scheduler_no_forbidden_imports` | No browser/stealth/deep_probe/DHT imports |

---

## Regression

| Suite | Result |
|---|---|
| `tests/probe_f232e_kpi_nullsafe/` | PASS |
| `tests/probe_f232f_canonical_acq_report/` | PASS |
| `tests/probe_f232_profile_propagation/` | PASS |
| `tests/probe_r11_passive_tech_stack/` | PASS |
| **Total** | **51 passed, 1 warning (pre-existing)** |

**Note:** `tests/probe_f232h_gate_false_green/` has 3 failures unrelated to F234B — missing F224 probe files (`probe_f224a_advisory_classifier/`, `probe_f224b_skillmap_adapter/`) that were removed from the repo but the gate test still expects them. Pre-existing.

---

## Abort Condition Checklist

| Condition | Status |
|---|---|
| Live network | NOT triggered — hermetic tests only |
| Real live sprint | NOT triggered |
| MLX/model load | NOT triggered — no MLX imports in changed path |
| Browser/stealth | NOT triggered — no browser imports |
| Legacy/deep_probe/DHT import | NOT triggered — imports blocked by environment guards |
| New storage authority | NOT triggered — no storage changes |
| Fabricating nonfeed accepted findings | NOT triggered — no findings fabricated |
| Hiding fallback as canonical | NOT triggered — fallback report schema unchanged |

---

## Backup Files

All backed up before editing:
- `core/__main__.py.bak_F234B_SOURCE_FAMILY_CONTRACT`
- `runtime/acquisition_strategy.py.bak_F234B_SOURCE_FAMILY_CONTRACT`
- `runtime/sprint_scheduler.py.bak_F234B_SOURCE_FAMILY_CONTRACT`
- `benchmarks/live_measurement_kpi.py.bak_F234B_SOURCE_FAMILY_CONTRACT`
- `tools/evidence_delta_memory.py.bak_F234B_SOURCE_FAMILY_CONTRACT`
- `tools/research_quality_score.py.bak_F234B_SOURCE_FAMILY_CONTRACT`
- `tools/live_artifact_triage.py.bak_F234B_SOURCE_FAMILY_CONTRACT`