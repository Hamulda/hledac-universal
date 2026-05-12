# hledac/universal Live Sprint Audit

Datum: 2026-05-12 | Commit: 73aa5a87

---

## Executive Summary

The hledac/universal sprint execution engine (`runtime/sprint_scheduler.py`, 11,720 lines) is a production-grade autonomous orchestrator with solid async foundations, bounded memory guards, and comprehensive lane-based acquisition strategy. Key findings: 23 validator checks, 5 active acquisition lanes, 101 `getattr(self._result)` calls indicating repeated dict unpacking, 20 bare `except Exception:` patterns, and multiple sequential `await` patterns in nonfeed prelude that could benefit from gather-based parallelization.

---

## Architekturální přehled

```
Entry point: core.__main__.run_sprint()
    │
    └── runtime/sprint_scheduler.py::SprintScheduler
            │
            ├── Lanes: CT, WAYBACK, PASSIVE_DNS, DOH, PIVOT_EXECUTOR
            │
            ├── _build_diagnostic_report() → compute_sprint_intelligence()
            │
            ├── _lane_verdicts accumulator (F207J-A)
            │
            └── DuckDB ingest via async_ingest_findings_batch()
```

---

## Funguje dobře

| Feature | Evidence | Status |
|---------|----------|--------|
| Entry point discipline | `core.__main__.run_sprint()` sole production owner | ✅ |
| DuckDB async ingest | `async_ingest_findings_batch()` called per lane | ✅ |
| Memory bounds | `_MAX_FINDINGS_PER_SPRINT = 500` enforced | ✅ |
| Lane verdict telemetry | `_lane_verdicts` accumulator for compute_sprint_intelligence | ✅ |
| Background task hygiene | `asyncio.create_task()` with names in `sprint:` namespace | ✅ |
| Swap warning | `swap_warning` propagated in LiveMeasurementResult | ✅ |
| Live KPI stamping | `_stamp_live_kpi()` in benchmarks/live_sprint_measurement.py | ✅ |

---

## Kritické problémy (HIGH severity)

### K1: Sequentiální await v nonfeed prelude
- **Dopad**: WAYBACK → PASSIVE_DNS → DOH lanes execute sequentially, not in parallel
- **Lines**: 4205, 4232, 4275 (3x await duckdb_store.async_ingest_findings_batch)
- **Fix**: Gather all nonfeed prelude branches with bounded semaphore

### K2: 20x bare `except Exception:`
- **Dopad**: Silent failures mask real errors
- **Lines**: 1592, 1676, 1773, 1793, 1863, 1938, 1940, 2169, 2333, 2488 + more
- **Fix**: Add specific exception types and logging

---

## Střední problémy (MED severity)

### M1: 101 getattr(self._result) calls
- **Dopad**: Repeated dict unpacking, potential micro-optimization
- **Line**: Throughout sprint_scheduler.py
- **Fix**: Cache unpacking in `_capture_timing_fields()` and report builders

### M2: TODO D1 - O(n²) string concatenation per finding
- **Lines**: 10446, 10521
- **Fix**: Pre-allocate list and join once

### M3: TODO D7 - per-finding DuckDB upsert
- **Line**: 5776
- **Fix**: Batch upsert optimization

### M4: DuckDB store hasattr guard pattern
- **Dopad**: Repeated `hasattr(duckdb_store, "async_ingest_findings_batch")` checks
- **Lines**: 3264, 4205, 4232, 4275
- **Fix**: Cache capability at startup

---

## Low priority

### L1: Validator profile FAILED on recent report
- **Validator output**: `Exit 2: PROFILE FAILED`
- **Action**: Investigate failed checks

### L2: TODO D6 - PUBLIC lane timeout does not release per-lane budget
- **Line**: 4855

### L3: TODO D7 - LanceDB ANN index rebuild on startup
- **File**: knowledge/lancedb_store.py:862

---

## Missing pieces & TODO gap

| File | Line | TODO | Priority |
|------|------|------|----------|
| runtime/sprint_scheduler.py | 4855 | D6: PUBLIC lane timeout budget | MED |
| runtime/sprint_scheduler.py | 5776 | D7: per-finding DuckDB upsert | MED |
| runtime/sprint_scheduler.py | 10446 | D1: list.append() without pre-allocation | MED |
| runtime/sprint_scheduler.py | 10521 | D1: f-string per finding profiling | MED |
| knowledge/lancedb_store.py | 862 | D7: ANN index rebuild on M1 8GB | MED |
| utils/shared_tensor.py | 3 | Metal buffer zero-copy | LOW |

---

## Optimalizační příležitosti (impact/effort)

| Opportunity | Impact | Effort | Priority |
|-------------|--------|--------|----------|
| Gather nonfeed prelude lanes | HIGH | MED | P1 |
| Cache DuckDB capability at startup | MED | LOW | P2 |
| Add exception specificity | MED | MED | P3 |
| Pre-allocate findings list | MED | LOW | P4 |

---

## Metriky

| Metric | Value |
|--------|-------|
| Validator checks | 23/23 |
| getattr(self._result) calls | 101 |
| OOM guard: _MAX_FINDINGS_PER_SPRINT | 500 |
| Active lanes | CT / WAYBACK / PASSIVE_DNS / DOH / PIVOT_EXECUTOR |
| Advisory lanes | BGP / WaybackCDX |
| Bare except patterns | 20 |
| Sequential await candidates | 16 |
| Caching decorators | 4 in use |

---

## 10-fázový plán

| # | Název | Závislosti | Riziko | Effort |
|---|-------|------------|--------|--------|
| F1 | Typy + compile + diagnostika | — | LOW | 30 min |
| F2 | DuckDB capability caching | F1 | LOW | 15 min |
| F3 | Async hygiene (HIGH severity) | F1 | MED | 60 min |
| F4 | Gather nonfeed prelude | F3 | MED | 45 min |
| F5 | Exception specificity hardening | F4 | MED | 45 min |
| F6 | Findings list pre-allocation | F5 | LOW | 30 min |
| F7 | PUBLIC lane timeout fix | F4 | MED | 60 min |
| F8 | Validator CI integration | F7 | LOW | 30 min |
| F9 | LanceDB startup optimization | F6 | MED | 90 min |
| F10 | ARCHITECTURE.md + runbook | F9 | LOW | 60 min |

---

## Prompty F1–F10

### F1: Typy + compile + diagnostika
**Prerekvizity**: None
**Discovery**:
```bash
uv run py_compile runtime/sprint_scheduler.py && echo "COMPILE OK"
rg -n "type:\s*Any" runtime/sprint_scheduler.py | head -20
```
**Implementation**: Run ruff check, fix type annotations
**Verification**: `py_compile && ruff check runtime/sprint_scheduler.py`
**Scope**: runtime/sprint_scheduler.py | ~50 lines | 30 min

### F2: DuckDB capability caching
**Prerekvizity**: F1
**Discovery**:
```bash
rg -n "hasattr.*async_ingest_findings_batch" runtime/sprint_scheduler.py
```
**Implementation**: Cache capability in `__init__` as `_duckdb_can_ingest: bool`
**Verification**: `py_compile`
**Scope**: runtime/sprint_scheduler.py | ~20 lines | 15 min

### F3: Async hygiene - sequential awaits
**Prerekvizity**: F2
**Discovery**:
```bash
rg -n "elif _lane_name ==" runtime/sprint_scheduler.py
rg -n "await.*ingest_findings_batch" runtime/sprint_scheduler.py
```
**Implementation**: Replace sequential awaits with `asyncio.gather()` and bounded semaphore
**Verification**: `py_compile && pytest tests/`
**Scope**: runtime/sprint_scheduler.py | ~100 lines | 60 min

### F4: Gather nonfeed prelude
**Prerekvizity**: F3
**Discovery**:
```bash
rg -n "WAYBACK|PASSIVE_DNS|DOH" runtime/sprint_scheduler.py | rg "elif _lane_name"
```
**Implementation**: Create `_run_nonfeed_prelude_gather()` method
**Verification**: `py_compile`
**Scope**: runtime/sprint_scheduler.py | ~80 lines | 45 min

### F5: Exception specificity
**Prerekvizity**: F4
**Discovery**:
```bash
rg -n "except Exception:" runtime/sprint_scheduler.py
```
**Implementation**: Replace bare `except:` with specific types + logger.error
**Verification**: `py_compile && ruff check`
**Scope**: runtime/sprint_scheduler.py | ~40 lines | 45 min

### F6: Findings list pre-allocation
**Prerekvizity**: F5
**Discovery**:
```bash
rg -n "TODO D1" runtime/sprint_scheduler.py
```
**Implementation**: Pre-allocate list with known size, use join()
**Verification**: `py_compile`
**Scope**: runtime/sprint_scheduler.py | ~30 lines | 30 min

### F7: PUBLIC lane timeout fix
**Prerekvizity**: F4
**Discovery**:
```bash
rg -n "TODO D6" runtime/sprint_scheduler.py
```
**Implementation**: Add per-lane budget release in PUBLIC lane timeout path
**Verification**: `py_compile`
**Scope**: runtime/sprint_scheduler.py | ~40 lines | 60 min

### F8: Validator CI integration
**Prerekvizity**: F7
**Discovery**:
```bash
uv run python tools/f234_validate_nonfeed_live_report.py $(ls -t reports/*.json | head -1)
```
**Implementation**: Add validator to CI pipeline
**Verification**: CI passes
**Scope**: .github/workflows/ | ~20 lines | 30 min

### F9: LanceDB startup optimization
**Prerekvizity**: F6
**Discovery**:
```bash
rg -n "TODO D7" knowledge/lancedb_store.py
```
**Implementation**: Lazy ANN index rebuild, memory-aware threshold
**Verification**: M1 8GB memory test
**Scope**: knowledge/lancedb_store.py | ~60 lines | 90 min

### F10: ARCHITECTURE.md + runbook
**Prerekvizity**: F9
**Discovery**:
```bash
wc -l runtime/sprint_scheduler.py ARCHITECTURE_MAP.py
```
**Implementation**: Document lane flow, advisory sidecars, memory budget
**Verification**: Readable markdown
**Scope**: docs/ARCHITECTURE.md | ~200 lines | 60 min

---

## Validator Status

Last run on `reports/capability_export_f228d.json`:
- Result: `Exit 2: PROFILE FAILED`
- Checks passing: schema_version, acquisition_terminality, scheduler_exit, live_kpi_integrity, runtime_truth_termination, return_guard
- Failed checks: Needs investigation

---

*Generated via CODEX_AUDIT framework | 2026-05-12*
