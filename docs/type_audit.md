# Type Audit — 2026-05-12

## Baseline Status

- **File**: `runtime/sprint_scheduler.py`
- **py_compile**: ✅ PASS — no syntax errors
- **Any annotation count**: 77 (zero in comments, all are real type hints)

---

## Any Annotations in sprint_scheduler.py (77 total)

### Category A — Manager/engine/service instance variables (initialized to None)

| Line | Annotation |
|------|-----------|
| 1338 | `self._hermes_engine: Any = None` |
| 1339 | `self._memory_manager: Any = None` |
| 1364 | `self._ioc_scorer: Any = None` |
| 1366 | `self._duckdb_store: Any = None` |
| 1368 | `self._forensics_enricher: Any = None` |
| 1369 | `self._forensics_lmdb_env: Any = None` |
| 1371 | `self._multimodal_enricher: Any = None` |
| 1372 | `self._multimodal_lmdb_env: Any = None` |
| 1374 | `self._ioc_graph: Any = None` |
| 1382 | `self._prefetch_oracle: Any = None` |
| 1384 | `self._shadow_pd_summary: Any = None` |
| 1386 | `self._advisory_gate_snapshot: Any = None` |
| 1393 | `self._acquisition_plan: Any = None` |
| 1418 | `self._policy_manager: Any = None` |
| 1420 | `self._identity_adapter: Any = None` |
| 1422 | `self._exposure_adapter: Any = None` |
| 1424 | `self._leak_sentinel_adapter: Any = None` |
| 1426 | `self._doh_adapter: Any = None` |
| 1428 | `self._evidence_triage_adapter: Any = None` |
| 1430 | `self._diff_engine: Any = None` |
| 1432 | `self._pivot_planner: Any = None` |
| 1435 | `self._sidecar_bus: Any = None` |
| 1439 | `self._analyst_brief: Any = None` |
| 1444 | `self._metrics_registry: Any = None` |

### Category B — Constructor/function parameters (lazy-loaded or cross-boundary)

| Line | Annotation |
|------|-----------|
| 191 | `public_result: Any \| None = None` (function arg) |
| 435 | `lifecycle: Any` (class `__init__`) |
| 1266 | `ct_log_client: Any = None` (`__init__`) |
| 1416 | `self._ct_log_client: Any = ct_log_client` |
| 1454 | `result: Any` (callback arg) |
| 1737 | `lifecycle: Any` (function arg) |
| 1741 | `duckdb_store: Any = None` |
| 1742 | `ct_log_client: Any = None` |
| 1743 | `policy_manager: Any = None` |
| 1834 | `_trace_snap_before: Any = None` |

### Category C — Internal method parameters (store/graph adapters)

| Line | Annotation |
|------|-----------|
| 3093 | `duckdb_store: Any` |
| 3534 | `acquisition_plan: Any` |
| 3571 | `acquisition_plan: Any` |
| 3775 | `duckdb_store: Any` |
| 3776 | `ct_log_client: Any` |
| 3995 | `_ct_outcome_prelude: Any = None` |
| 3998 | `_ct_result: Any = None` |
| 4361 | `duckdb_store: Any` |
| 4581 | `duckdb_store: Any` |
| 4684 | `duckdb_store: Any = None` |
| 4725 | `duckdb_store: Any` |
| 4979 | `duckdb_store: Any` |
| 5380 | `duckdb_store: Any = None` |
| 5381 | `hermes_engine: Any \| None = None` |
| 5382 | `memory_manager: Any \| None = None` |
| 5613 | `store: Any` |
| 5646 | `store: Any` |
| 5802 | `store: Any \| None` |
| 5860 | `duckdb_store: Any \| None` |
| 6197 | `duckdb_store: Any` |
| 6308 | `store: Any` |
| 6437 | `store: Any` |
| 6514 | `store: Any` |
| 6587 | `store: Any` |
| 6669 | `store: Any` |
| 6721 | `store: Any` |
| 6860 | `store: Any` |
| 7081 | `store: Any` |
| 7205 | `store: Any` |
| 7291 | `store: Any` |
| 7368 | `store: Any` |
| 7510 | `store: Any` |
| 9832 | `store: Any` |
| 9958 | `store: Any` |
| 9976 | `ioc_graph: Any` |
| 9980 | `policy_manager: Any` |
| 9988 | `oracle: Any` |
| 9998 | `planner: Any` |
| 10009 | `workbench: Any` |
| 11707 | `duckdb_store: Any = None` |

### Category D — Callback/closure parameters

| Line | Annotation |
|------|-----------|
| 7155 | `**kw: Any` (`__init__` inside partial) |
| 9226 | `render_cti_stix_to_path: Any` |
| 9227 | `collect_cti_inputs: Any` |

---

## Project-Wide (non-test, non-.venv)

Only 1 additional `type: Any` found outside tests and vendor code:

| File | Line | Annotation |
|------|------|-----------|
| `context_optimization/context_cache.py` | 80 | `def _list_to_ndarray(obj: Any, target_type: Any = None) -> Any` |

---

## Pre-existing pyright Diagnostics (not audited here — scope limited to documentation)

F1 scope: document only. Code changes deferred to future sprint.

---

**F1 complete — baseline zdokumentován**
