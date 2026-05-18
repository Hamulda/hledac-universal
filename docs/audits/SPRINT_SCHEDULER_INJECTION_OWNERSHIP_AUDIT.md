# SprintScheduler Injection Ownership Audit

**File:** `runtime/sprint_scheduler.py` (~12,300 LOC)
**Date:** 2026-05-18
**Scope:** All `inject_*`, `set_*`, and `self._*` state fields; existing seam classes
**Goal:** Map ownership, find smallest safe extraction slice — no runtime changes

---

## Existing Seams (already extracted)

| Seam | File | Owns |
|------|------|------|
| `SidecarOrchestrator` | `runtime/sidecar_orchestrator.py` | FindingSidecarBus + advisory dispatch |
| `SprintLifecycleRunner` | `runtime/sprint_lifecycle_runner.py` | Phase transitions, abort, windup guard |
| `SprintGraphAccumulator` | `runtime/graph_accumulator.py` | Graph upsert from accepted findings |
| `AcquisitionStrategy` | `runtime/acquisition_strategy.py` | Lane plan construction (imported, not owned) |
| `ResourceGovernor` | `utils/concurrency.py` → `get_governor()` | Advisory concurrency hints (external singleton) |
| `SprintAdvisoryRunner` | `runtime/sidecar_orchestrator.py` | Advisory orchestration (delegated from scheduler) |

---

## Dependency / State Field Ownership Table

| Field | Injected By | Read By | Written By | Phase | Owner Seam |
|-------|-------------|---------|------------|-------|------------|
| `_duckdb_store` | `inject_duckdb_store()` | `_ingest_findings`, `_flush_arrow_batch`, all canonical write paths | `inject_duckdb_store()` only | active | DuckDBWritePath (canonical) |
| `_duckdb_can_ingest` | derived from `_duckdb_store` | `run()` pre-loop guard | derived | warmup | DuckDBWritePath |
| `_forensics_enricher` | `inject_forensics_enricher()` | `_enrich_ct_findings_forensics()` | `inject_forensics_enricher()` only | active | EnrichmentServices |
| `_forensics_lmdb_env` | `inject_forensics_enricher()` | passed to enricher init/close | `inject_forensics_enricher()` only | active | EnrichmentServices |
| `_multimodal_enricher` | `inject_multimodal_enricher()` | `_enrich_findings_multimodal()` | `inject_multimodal_enricher()` only | active | EnrichmentServices |
| `_multimodal_lmdb_env` | `inject_multimodal_enricher()` | passed to enricher init/close | `inject_multimodal_enricher()` only | active | EnrichmentServices |
| `_source_economics` | `inject_source_economics()` | `get_economic_feed_url`, `_adapt_source_weights_from_feedback`, `update_source_economics`, advisory runner | `inject_source_economics()`, `_update_source_economics()` | active | SourceEconomicsManager |
| `_pivot_planner` | `inject_pivot_planner()` | `_run_advisory_runner()`, `_plan_pivots()` | `inject_pivot_planner()` only | active | PivotServices |
| `_prefetch_oracle` | `inject_prefetch_oracle()` | `_sort_feed_items`, `_run_prefetch_feedback` | `inject_prefetch_oracle()` only | active | PivotServices |
| `_policy_manager` | `inject_policy_manager()` | `run()` RL update calls | `inject_policy_manager()` + bidirectional inject_scheduler | active | (opt-in RL, skip) |
| `_pivot_ioc_graph` | `inject_ioc_graph()` | `_accumulate_findings_to_graph()` | `inject_ioc_graph()` only | active | PivotServices |
| `_graph_accumulator` | `new SprintGraphAccumulator()` | `_accumulate_findings_to_graph()` | created in `__init__`, never replaced | active | SprintGraphAccumulator (already extracted) |
| `_analyst_workbench` | `inject_analyst_workbench()` | `get_analyst_brief()` | `inject_analyst_workbench()` only | teardown | (late-feature, skip) |
| `_sidecar_orchestrator` | `new SidecarOrchestrator()` | `run_advisory_runner()`, `dispatch_findings()`, `run_target_memory_update()`, `reset()` | created in `__init__`, reset at teardown | all | SidecarOrchestrator (already extracted) |
| `_runner` | `new SprintLifecycleRunner()` | `setup()`, `tick()`, `ensure_active()`, `is_terminal()`, `abort_requested`, `windup_guard()`, `sleep_or_abort()`, `post_sleep_gate()`, `teardown()`, `current_phase` | created in `__init__` | all | SprintLifecycleRunner (already extracted) |
| `_hermes_engine` | `set_hermes_engine()` (no inject_* prefix) | `_run_hermes_sprint_*`, `close()` | `set_hermes_engine()` only | active | (MLX engine, skip) |
| `_memory_manager` | `set_memory_manager()` | `_run_memory_cleanup_*` | `set_memory_manager()` only | active | (memory mgmt, skip) |
| `_ioc_scorer` | `set_ioc_scorer()` | `_score_iocs_*` | `set_ioc_scorer()` only | active | (scorer, skip) |
| `_dedup_env` | `open_lmdb()` in `__init__` | `_load_dedup_seen()`, `_save_dedup_seen()` | created once | warmup | (dedup infra, skip) |
| `_duckdb_read_con` | lazy in `run()` | `query_ct_log_status()`, DuckDB read queries | created once | active | DuckDBWritePath |

---

## Dependency Groups

### Storage / Write Path
- `_duckdb_store`, `_duckdb_can_ingest`, `_duckdb_read_con`
- **Owner:** Canonical write seam — already well-structured, `inject_duckdb_store()` is the sole setter
- **Risk:** High coupling to sprint result fields

### Enrichment (F195C)
- `_forensics_enricher`, `_forensics_lmdb_env`, `_multimodal_enricher`, `_multimodal_lmdb_env`
- **Owner:** `EnrichmentServices` bundle — both share `initialize()`/`close()` lifecycle pattern
- **Reads:** `_enrich_ct_findings_forensics()`, `_enrich_findings_multimodal()`
- **Comment:** Both injected as pair, both used in same `_ingest_findings` block

### Pivot / Graph (F202G, F198A)
- `_pivot_planner`, `_prefetch_oracle`, `_pivot_ioc_graph`, `_graph_accumulator`
- **Owner:** `PivotServices` — `_pivot_planner` and `_prefetch_oracle` are both advisory; `_pivot_ioc_graph` feeds graph accumulation
- **Reads:** `_run_advisory_runner()`, `_accumulate_findings_to_graph()`, `_sort_feed_items()`
- `_graph_accumulator` already extracted as separate class

### Source Economics
- `_source_economics` dict + `set_novelty_bonus()`
- **Owner:** Could be `SourceEconomicsManager` — but economics map is updated by scheduler internally via `_update_source_economics()`
- **Boundary:** Would need to expose `_update_source_economics()` to the bundle

### Advisory / RL (F205F, F206D)
- `_policy_manager`
- **Owner:** Opt-in RL layer, bidirectional wiring makes extraction complex

### Telemetry / Result
- `_result` (SprintSchedulerResult), `_finding_count`, `_arrow_batch`, `_public_outcome`, etc.
- These are scheduler-owned output state — not injection targets

---

## Injection Method Signatures

```
inject_ioc_graph(ioc_graph)           → _pivot_ioc_graph
inject_policy_manager(pm)             → _policy_manager
inject_prefetch_oracle(oracle)        → _prefetch_oracle
inject_pivot_planner(planner)         → _pivot_planner
inject_analyst_workbench(wb)          → _analyst_workbench
inject_forensics_enricher(enricher, lmdb_env)
inject_multimodal_enricher(enricher, lmdb_env)
inject_source_economics(economics)    → _source_economics (merge-able)
inject_duckdb_store(store)            → _duckdb_store
set_hermes_engine(engine)             → _hermes_engine (non-inject prefix)
set_memory_manager(mm)               → _memory_manager
set_novelty_bonus(source, flag)       → _source_economics mutation
set_ioc_scorer(scorer)                → _ioc_scorer
```

---

## Phase Lifecycle Map

| Phase | inject_* called | state mutated | side effects |
|-------|----------------|--------------|-------------|
| `__init__` | — | `_sidecar_orchestrator`, `_runner`, `_graph_accumulator` created | no I/O |
| warmup | `inject_duckdb_store` (via ctor), `inject_policy_manager`, `inject_ioc_graph`, `inject_forensics_enricher`, `inject_multimodal_enricher`, `inject_source_economics`, `inject_prefetch_oracle`, `inject_pivot_planner` | dedup preload, duckdb init, enricher init | network, LMDB |
| active | — (no new inject_* during run) | `_source_economics` updates, `_fetch_latency_ema`, `_lane_*` arrays, `_public_outcome` | all I/O |
| windup | `inject_analyst_workbench` (late) | `_result` finalization | optional |
| teardown | — | enricher close, sidecar reset, graph accumulator teardown | cleanup |

---

## Extraction Candidates

### 1. `EnrichmentServices` (RECOMMENDED — smallest safe slice)

**Bundle:**
- `_forensics_enricher` + `_forensics_lmdb_env`
- `_multimodal_enricher` + `_multimodal_lmdb_env`
- Methods: `_enrich_ct_findings_forensics()`, `_enrich_findings_multimodal()`
- Plus: `inject_forensics_enricher()`, `inject_multimodal_enricher()`

**Why safe:**
- Both injected as a pair in the same warmup block (from `core/__main__.py`)
- Both share identical lifecycle: `initialize()` in warmup, `close()` in teardown
- Both used exclusively in `_ingest_findings` → same discovery cycle
- No cross-contamination with other groups (pivot, storage, economics)
- Already has separate LMDB env per enricher (clear boundary)

**Wire to scheduler:** `SprintScheduler` receives `EnrichmentServices` instance with single `inject_enrichment_services(services)` — exposes `.enrich_forensics(findings)`, `.enrich_multimodal(findings)`, `.initialize()`, `.close()`.

**Not yet extracted because:**
- Both enrichers still owned by `core/__main__.py` wiring
- LMDB envs passed separately (could be bundled)

**Risk:** LOW — bundle is self-contained, only called from one method chain.

---

### 2. `SprintExportContext` (defer — depends on result shape)

**Bundle:** Result fields + metrics + lane outcomes → exported via `SprintExporter`

**Why defer:** `_result` fields bleed across every group (storage write updates it, enrichment updates it, pivot updates it). Extracting it requires defining a stable `SprintResult` protocol first.

---

### NOT Recommended

- **PivotServices bundle** — `_pivot_planner` and `_prefetch_oracle` are advisory-only with fail-soft semantics; extracting them saves little complexity while adding IPC overhead for the same advisory calls.
- **Storage bundle** — `_duckdb_store` is the canonical write seam and already well-encapsulated; extracting it would require defining a write protocol that matches `async_ingest_findings_batch`.

---

## Architecture Seal (minimal)

No new `inject_*` fields without an ownership group. A new injection must either:

1. Belong to an existing seam (`SidecarOrchestrator`, `SprintLifecycleRunner`, `EnrichmentServices`, `PivotServices`)
2. Form a new trivial bundle of ≤2 related fields with shared lifecycle

Rule applies to `runtime/sprint_scheduler.py` only. No enforcement mechanism needed — this is documentation.

---

## Test Coverage

Import smoke + basic instantiation:
```bash
cd /Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal
python -c "from runtime.sprint_scheduler import SprintScheduler; print('import OK')"
```

Scheduler probe tests (F195C forensics/multimodal, F202G pivot):
```bash
pytest tests/probe_f195c/ tests/probe_f202g/ -q --tb=no
```

E2E smoke:
```bash
pytest tests/test_forensics_enrichment.py tests/test_multimodal_analyzer.py -q --tb=short
```

---

## Summary

| Group | Status | Recommendation |
|-------|--------|----------------|
| Storage (DuckDB) | Well-encapsulated | No change |
| **Enrichment (Forensics + Multimodal)** | **Best extraction candidate** | **Bundle as `EnrichmentServices` in future sprint** |
| Pivot / Graph | Advisory-only, already split | No change |
| Source Economics | Internal dict + setter | No change |
| Advisory / RL | Opt-in, bidirectional | No change |
| Lifecycle / Sidecar | Already extracted | No change |