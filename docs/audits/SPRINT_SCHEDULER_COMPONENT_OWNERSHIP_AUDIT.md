# SprintScheduler Component Ownership Audit
**Date:** 2026-05-18
**Scope:** `runtime/sprint_scheduler.py`
**Constraint:** No runtime changes. No new sub-scheduler classes.

---

## Ownership Matrix

| Field | Injected By | Read By | Written By | Phase | Owner Group | Fail-Soft |
|-------|-------------|---------|------------|-------|-------------|-----------|
| `_duckdb_store` | `inject_duckdb_store()` | `_ingest_findings`, `_flush_arrow_batch` | `inject_duckdb_store()` only | active | **StorageServices** | N/A (canonical) |
| `_duckdb_can_ingest` | derived | `run()` pre-loop guard | derived | warmup | StorageServices | N/A |
| `_duckdb_read_con` | lazy `run()` | `query_ct_log_status()` | created once | active | StorageServices | N/A |
| `_forensics_enricher` | `inject_forensics_enricher()` | `_enrich_ct_findings_forensics()` | `inject_forensics_enricher()` only | active | **EnrichmentServices** | ✅ |
| `_forensics_lmdb_env` | `inject_forensics_enricher()` | enricher init/close | `inject_forensics_enricher()` only | active | EnrichmentServices | ✅ |
| `_multimodal_enricher` | `inject_multimodal_enricher()` | `_enrich_findings_multimodal()` | `inject_multimodal_enricher()` only | active | EnrichmentServices | ✅ |
| `_multimodal_lmdb_env` | `inject_multimodal_enricher()` | enricher init/close | `inject_multimodal_enricher()` only | active | EnrichmentServices | ✅ |
| `_pivot_planner` | `inject_pivot_planner()` | `_run_advisory_runner()`, `_plan_pivots()` | `inject_pivot_planner()` only | active | **PivotServices** | ✅ |
| `_prefetch_oracle` | `inject_prefetch_oracle()` | `_sort_feed_items`, `_run_prefetch_feedback` | `inject_prefetch_oracle()` only | active | PivotServices | ✅ |
| `_pivot_ioc_graph` | `inject_ioc_graph()` | `_accumulate_findings_to_graph()` | `inject_ioc_graph()` only | active | PivotServices | ✅ |
| `_graph_accumulator` | `new SprintGraphAccumulator()` | `_accumulate_findings_to_graph()` | created in `__init__` | active | PivotServices | ✅ |
| `_source_economics` | `inject_source_economics()` | `get_economic_feed_url`, `_adapt_src_weights_from_feedback` | `inject_source_economics()`, `_update_src_economics()` | active | **AdvisoryServices** | ✅ |
| `_policy_manager` | `inject_policy_manager()` | `run()` RL update calls | `inject_policy_manager()` + bidirectional | active | AdvisoryServices | ✅ |
| `_analyst_workbench` | `inject_analyst_workbench()` | `get_analyst_brief()` | `inject_analyst_workbench()` only | teardown | AdvisoryServices | ✅ |
| `_acquisition_plan` | `build_acquisition_plan()` | lifecycle decisions | rebuilt per cycle | active | **LaneServices** | ✅ |
| `_lane_outcomes` | derived | `run()` result | `_run_nonfeed_lanes()` | active | LaneServices | ✅ |
| `_lane_rejections` | derived | `run()` diagnostics | `_run_nonfeed_lanes()` | active | LaneServices | ✅ |
| `_sidecar_orchestrator` | `new SidecarOrchestrator()` | `run_advisory_runner()`, `dispatch_findings()` | created in `__init__` | all | *(already extracted)* | ✅ |
| `_runner` | `new SprintLifecycleRunner()` | all phase methods | created in `__init__` | all | *(already extracted)* | ✅ |
| `_hermes_engine` | `set_hermes_engine()` | `_run_hermes_sprint_*` | `set_hermes_engine()` only | active | *(skip — MLX)* | N/A |
| `_memory_manager` | `set_memory_manager()` | `_run_memory_cleanup_*` | `set_memory_manager()` only | active | *(skip)* | N/A |
| `_ioc_scorer` | `set_ioc_scorer()` | `_score_iocs_*` | `set_ioc_scorer()` only | active | *(skip)* | N/A |
| `_dedup_env` | `open_lmdb()` | `_load_dedup_seen()`, `_save_dedup_seen()` | created once | warmup | *(skip — infra)* | N/A |

---

## Component Groups

### 1. EnrichmentServices
**Members:** `_forensics_enricher`, `_forensics_lmdb_env`, `_multimodal_enricher`, `_multimodal_lmdb_env`

| Property | Value |
|----------|-------|
| Injection methods | `inject_forensics_enricher()`, `inject_multimodal_enricher()` |
| Read methods | `_enrich_ct_findings_forensics()`, `_enrich_findings_multimodal()` |
| Lifecycle | Both initialized in warmup, both closed in teardown |
| Coupling | Shared `_ingest_findings` block, separate LMDB envs |
| Fail-soft | Both enrichers fail-soft internally |

**Coupling analysis:**
- `_forensics_enricher` → called only in `_enrich_ct_findings_forensics()` (1 site)
- `_multimodal_enricher` → called only in `_enrich_findings_multimodal()` (1 site)
- No cross-contamination with PivotServices or StorageServices
- Both receive separate LMDB envs (clean boundary)

### 2. PivotServices
**Members:** `_pivot_planner`, `_prefetch_oracle`, `_pivot_ioc_graph`, `_graph_accumulator`

| Property | Value |
|----------|-------|
| Injection methods | `inject_pivot_planner()`, `inject_prefetch_oracle()`, `inject_ioc_graph()` |
| Read methods | `_run_advisory_runner()`, `_accumulate_findings_to_graph()`, `_sort_feed_items()` |
| Advisory coupling | All advisory-only with fail-soft semantics |
| IOC graph | `inject_ioc_graph()` sets `_pivot_ioc_graph` (not extracted) |

**Note:** `_graph_accumulator` already extracted as `SprintGraphAccumulator` class. Remaining pivot fields are advisory-only.

### 3. StorageServices
**Members:** `_duckdb_store`, `_duckdb_can_ingest`, `_duckdb_read_con`

| Property | Value |
|----------|-------|
| Injection method | `inject_duckdb_store()` |
| Read methods | `_ingest_findings()`, `_flush_arrow_batch()`, `query_ct_log_status()` |
| Canonical write | Yes — `async_ingest_findings_batch()` seam |

### 4. AdvisoryServices
**Members:** `_source_economics`, `_policy_manager`, `_analyst_workbench`

| Property | Value |
|----------|-------|
| Injection methods | `inject_source_economics()`, `inject_policy_manager()`, `inject_analyst_workbench()` |
| Mutability | `_source_economics` updated internally via `_update_src_economics()` |
| Policy manager | Bidirectional wiring — extraction would require protocol redesign |

### 5. LaneServices
**Members:** `_acquisition_plan`, `_lane_outcomes`, `_lane_rejections`

| Property | Value |
|----------|-------|
| Source | Built by `build_acquisition_plan()` per cycle |
| Terminality | Acquisition terminality check in `_check_acquisition_terminality()` |
| Scope | **NOT extracted — acquisition terminality must remain in scheduler** |

---

## Injection Method Registry

| Method | Target Field | Owner Group | Lines |
|--------|-------------|-------------|-------|
| `inject_duckdb_store()` | `_duckdb_store` | StorageServices | 10531 |
| `inject_forensics_enricher()` | `_forensics_enricher`, `_forensics_lmdb_env` | EnrichmentServices | 10485 |
| `inject_multimodal_enricher()` | `_multimodal_enricher`, `_multimodal_lmdb_env` | EnrichmentServices | 10501 |
| `inject_pivot_planner()` | `_pivot_planner` | PivotServices | 10462 |
| `inject_prefetch_oracle()` | `_prefetch_oracle` | PivotServices | 10452 |
| `inject_ioc_graph()` | `_pivot_ioc_graph` | PivotServices | 10440 |
| `inject_source_economics()` | `_source_economics` | AdvisoryServices | 10517 |
| `inject_policy_manager()` | `_policy_manager` | AdvisoryServices | 10444 |
| `inject_analyst_workbench()` | `_analyst_workbench` | AdvisoryServices | 10473 |

---

## Recommended Future Micro-Extraction

### `EnrichmentServices` — Single Safe Slice

**Rationale:** Smallest coupling, clearest boundary.

| Criterion | EnrichmentServices | PivotServices |
|-----------|-------------------|---------------|
| Fields | 4 | 4 |
| Injection sites | 2 (pair) | 3 (separate) |
| Read sites | 2 | 3 |
| Cross-contamination | None | AdvisoryRunner shared |
| LMDB boundary | Separate envs | N/A |
| Lifecycle sync | Identical | Varies |

**Proposed shape (future sprint, not implemented):**
```python
class EnrichmentServices:
    def __init__(self, forensics_enricher, forensics_lmdb_env,
                 multimodal_enricher, multimodal_lmdb_env): ...
    def enrich(self, finding: CanonicalFinding) -> CanonicalFinding: ...
    async def close(self): ...
```

**Why not now:** Requires coordination with `core/__main__.py` wiring changes. Audit-only scope.

---

## Architecture Seal

**Rule:** No new `inject_*` method may be added to `runtime/sprint_scheduler.py` without a corresponding ownership group entry in this document.

New injection fields must declare:
1. Owner group (`EnrichmentServices`, `PivotServices`, `StorageServices`, `AdvisoryServices`, `LaneServices`)
2. Whether the group already exists or a new one is needed
3. If new group: why it cannot belong to an existing group

This is documentation only — no mechanical enforcement.

---

## Caller Map (Injection Sites)

| Caller File | Injects | Location |
|-------------|---------|----------|
| `core/__main__.py` | forensics, multimodal, duckdb, ioc_graph, prefetch, pivot_planner, source_economics | lines ~2400-2500 |
| `prefetch/prefetch_oracle_integration.py` | `inject_prefetch_oracle()` | line 22, 86 |

---

## Verification Commands

```bash
# Import smoke
python -c "from runtime.sprint_scheduler import SprintScheduler; print('import OK')"

# Probe tests by ownership group
pytest tests/probe_f195c/ tests/probe_f202g/ -q --tb=no

# Enrichment-specific smoke
pytest tests/test_forensics_enrichment.py tests/test_multimodal_analyzer.py -q --tb=short
```
