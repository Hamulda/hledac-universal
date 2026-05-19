# SprintScheduler State Heatmap Audit

**File:** `runtime/sprint_scheduler.py`
**Lines:** 12,370 | **Chars:** 608,068
**Async def:** 76 | **Await calls:** 181 | **Asyncio usages:** 125
**Generated:** 2026-05-18

---

## 1. Overview

SprintScheduler je 12k+ řádkový monolithic orchestrátor. Obsahuje 76 async def metod, 181 await bodů, a 125 asyncio užití. Dvě varování: `asyncio.run` a `ThreadPoolExecutor` present — oba potenciální M1 crash vektory.

---

## 2. Field Fan-In/Fan-Out Ranking (Top 30)

| Rank | Field | Reads | Writes | Init | Total | Ownership Group |
|------|-------|-------|--------|------|-------|-----------------|
| 1 | `_result` | 807 | 2 | 2 | **811** | export/report | ⚠️ CRITICAL |
| 2 | `_acquisition_plan` | 79 | 3 | 3 | **85** | lane/acquisition | ⚠️ HIGH |
| 3 | `_config` | 56 | 1 | 1 | **58** | init/setup | medium |
| 4 | `_public_outcome` | 25 | 13 | 13 | **38** | export/report | ⚠️ HIGH |
| 5 | `_governor` | 30 | 4 | 4 | **34** | memory/governor | medium |
| 6 | `_hermes_engine` | 12 | 8 | 8 | **20** | init/setup | medium |
| 7 | `_runner` | 18 | 1 | 1 | **20** | lifecycle/run | medium |
| 8 | `_public_pipeline_result` | 12 | 6 | 6 | **18** | public pipeline | medium |
| 9 | `_bg_tasks` | 17 | 0 | 0 | **17** | lifecycle/run | low |
| 10 | `_lane_outcomes` | 11 | 4 | 4 | **15** | export/report | medium |
| 11 | `_nonfeed_ledger` | 14 | 0 | 0 | **14** | lane/acquisition | medium |
| 12 | `_dedup_env` | 11 | 3 | 3 | **14** | storage/write | medium |
| 13 | `_lc` | 12 | 1 | 1 | **13** | lifecycle/run | medium |
| 14 | `_dedup_seen` | 12 | 1 | 1 | **13** | storage/write | medium |
| 15 | `_nonfeed_predispatch_done` | 8 | 5 | 5 | **13** | lane/acquisition | medium |
| 16 | `_arrow_batch` | 12 | 1 | 1 | **13** | storage/write | medium |
| 17 | `_duckdb_read_con` | 10 | 3 | 3 | **13** | storage/write | medium |
| 18 | `_memory_manager` | 8 | 5 | 5 | **13** | memory/governor | medium |
| 19 | `_metrics_registry` | 10 | 3 | 3 | **13** | metrics | medium |
| 20 | `_multimodal_enricher` | 8 | 4 | 4 | **12** | enrichment | medium |
| 21 | `_forensics_enricher` | 8 | 4 | 4 | **12** | enrichment | medium |
| 22 | `_enrichment_services` | 11 | 1 | 1 | **12** | enrichment | medium |
| 23 | `_lane_rejections` | 9 | 3 | 3 | **12** | lane/acquisition | medium |
| 24 | `_source_economics` | 11 | 1 | 1 | **12** | lane/acquisition | medium |
| 25 | `_advisory_gate_snapshot` | 7 | 4 | 4 | **11** | sidecars/advisories | medium |
| 26 | `_multimodal_lmdb_env` | 7 | 4 | 4 | **11** | enrichment | medium |
| 27 | `_forensics_lmdb_env` | 7 | 4 | 4 | **11** | enrichment | medium |
| 28 | `_pivot_queue` | 10 | 0 | 0 | **10** | graph/pivot | low |
| 29 | `_ct_log_client` | 9 | 1 | 1 | **10** | init/setup | low |
| 30 | `_stop_requested` | 5 | 4 | 4 | **9** | lifecycle/run | low |

---

## 3. Ownership Groups — Field Counts and Risk

| Group | Fields | High-Risk Fields | Avg Fan-In |
|-------|--------|------------------|------------|
| **export/report** | _result, _public_outcome, _lane_outcomes, finalize_result_truth | _result (811), _public_outcome (38) | 287 |
| **lane/acquisition** | _acquisition_plan, _nonfeed_ledger, _nonfeed_predispatch_done, _lane_rejections, _source_economics | _acquisition_plan (85) | 30 |
| **init/setup** | _config, _hermes_engine, _runner, _lc, _ct_log_client, _wall_clock_start | _config (58), _hermes_engine (20) | 23 |
| **storage/write** | _dedup_env, _dedup_seen, _arrow_batch, _duckdb_read_con, _duckdb_store | _arrow_batch (13) | 12 |
| **memory/governor** | _governor, _memory_manager, _prefetch_oracle | _governor (34), _memory_manager (13) | 19 |
| **public pipeline** | _public_pipeline_result, _public_outcome | _public_pipeline_result (18) | 18 |
| **enrichment** | _multimodal_enricher, _forensics_enricher, _enrichment_services, _multimodal_lmdb_env, _forensics_lmdb_env | _multimodal_enricher (12), _forensics_enricher (12) | 12 |
| **sidecars/advisories** | _advisory_gate_snapshot, _sidecar_orchestrator, _policy_manager | _advisory_gate_snapshot (11) | 10 |
| **graph/pivot** | _pivot_queue, _pivot_stats, _graph_accumulator | _pivot_queue (10) | 9 |
| **metrics** | _metrics_registry, _fetch_latency_ema | _metrics_registry (13) | 11 |
| **lifecycle/run** | _bg_tasks, _stop_requested, _hard_deadline_monotonic, _finding_count, _all_findings | _bg_tasks (17), _stop_requested (9) | 13 |

---

## 4. Danger Zones

### DZ-1: `_result` (811 total access — CRITICAL)
- **Reads:** 807 | **Writes:** 2 | **Init:** 2
- **Problem:** Monolithic SprintSchedulerResult dataclass with 270+ fields. Every cycle reads _result ~50+ times for statistics, timing, early-exit computation, finalization.
- **Danger:** Any refactor touching result assembly is a blast radius event. Cross-phase reads across init → lifecycle → enrichment → export.
- **Recommendation:** Extract result building into separate `ResultBuilder` class with staged append methods.

### DZ-2: `_acquisition_plan` (85 total access — HIGH)
- **Reads:** 79 | **Writes:** 3 | **Init:** 3
- **Problem:** Acquired once but read 79 times across all phases — public branch, CT branch, nonfeed prelude, sidecars, finalization.
- **Danger:** Adding new lanes requires searching 79 read sites. Architecture unclear about which lanes are mandatory vs optional.
- **Recommendation:** Extract lane metadata into immutable `LaneMetadata` frozen class, acquired once, read-only.

### DZ-3: `_public_outcome` (38 total access — HIGH, multiple writers)
- **Reads:** 25 | **Writes:** 13 (multiple write sites across phases)
- **Problem:** Written by 13 different code paths across public_branch, nonfeed_prelude, sidecars. No transactional boundary.
- **Danger:** Race conditions possible if future parallelization introduced. Public outcome branching logic is scattered across 13 sites.
- **Recommendation:** Enforce single writer pattern — funnel all writes through one `_set_public_outcome()` method.

### DZ-4: `_public_outcome` (38 total access — HIGH, multiple writers)
- **Reads:** 25 | **Writes:** 13 (multiple write sites across phases)
- **Problem:** Written by 13 different code paths across public_branch, nonfeed_prelude, sidecars. No transactional boundary.
- **Danger:** Race conditions possible if future parallelization introduced. Public outcome branching logic is scattered across 13 sites.
- **Recommendation:** Enforce single writer pattern — funnel all writes through one `_set_public_outcome()` method.

---

## 5. Low-Risk Extraction Candidates (Micro-Extraction Targets)

These fields have high fan-out but are **structurally isolated** — cleanly separable without breaking call chains.

### Candidate A: `_pivot_queue` + Pivot Execution
- **Fan-out:** 10 reads, 0 writes in body
- **Ownership:** graph/pivot
- **Method cluster:** `enqueue_pivot`, `enqueue_hypothesis_pivot`, `_drain_pivot_queue`, `_execute_pivot`, `_buffer_ioc_pivot`
- **Extraction path:** Extract `PivotPlanner` class — receives ioc_graph, produces pivot tasks via queue interface
- **Why safe:** Pivot queue is append-only in body (reads only), enqueue methods are clearly bounded, pivot execution is isolated from lane execution
- **Stub complexity:** LOW — minimal cross-field deps

### Candidate B: `_source_economics` + Economics Sorting
- **Fan-out:** 11 reads, 1 write
- **Ownership:** lane/acquisition
- **Method cluster:** `_get_source_economics`, `_update_source_economics`, `_is_source_in_cooldown`, `_should_deprioritize_source`, `_sort_work_items_by_economics`, `economics_sort_key`, `oracle_sort_key`
- **Extraction path:** Extract `SourceEconomicsEngine` class — receives feed URL → returns economics scores
- **Why safe:** Economics logic is pure functions over SourceWork items, no cross-phase mutations, only one write site (init)
- **Stub complexity:** LOW — source_economics injected via `inject_source_economics()`

### Candidate C: `_fetch_latency_ema` + Adaptive Timeout
- **Fan-out:** 7 reads, 0 writes in body
- **Ownership:** memory/governor
- **Method cluster:** `_update_latency_ema`, `get_adaptive_timeout`
- **Extraction path:** Extract `LatencyEMATracker` class — pure async-friendly, no lifecycle deps
- **Why safe:** Latency EMA is simple rolling average, no cross-field deps, reads only in body
- **Stub complexity:** LOW — already follows clean interface pattern

---

## 6. Phase Map — Method Counts per Lifecycle Phase

```
init/setup:              8 methods  (_load_dedup, _init_forensics, _init_multimodal, _init_metrics_registry, _load_hermes_for_sprint, _unload_hermes_at_teardown, __init__)
lifecycle/run:          13 methods  (_run_mandatory_acquisition_prelude, _run_nonfeed_prelude_gather, _run_lane, _run_one_cycle*, _prune_work_items, _speculative_run)
feed_branch:             2 methods  (_run_feed_dominance_nonfeed_rescue_window, _run_feed_branch)
public_branch:          2 methods  (_run_public_branch, _run_public_discovery_in_cycle)
ct_branch:              6 methods  (_run_ct_predispatch, _run_ct_branch, _run_ct_log_discovery_in_cycle, _run_ct_to_passivedns_active_pivot, _run_ct_to_passivedns_pivot_advisory, _run_cti_export)
sidecars/advisories:     7 methods  (_dispatch_accepted_findings_sidecars, _run_advisory_runner, _run_bgp_advisory_sidecar, _run_wayback_cdx_deep_sidecar, _drain_pivot_queue, _execute_pivot, _buffer_ioc_pivot)
graph/pivot:            3 methods  (_accumulate_findings_to_graph, _get_graph_signal, _run_ooda_cycle)
export/report:          6 methods  (_import_exporters, _finalize_result_truth, _get_prewindup_barrier_report, _maybe_export_partial, _run_export, _build_diagnostic_report)
enrichment:             2 methods  (_enrich_findings_multimodal, _enrich_ct_findings_forensics)
memory/governor:        2 methods  (_run_target_memory_update, _memory_pressure_loop)
dedup/flush:            9 methods  (_get_dedup_lmdb_path, _flush_dedup, _close_dedup, _flush_forensics, _close_forensics, _flush_multimodal, _close_multimodal, _close_metrics_registry, _maybe_flush_to_parquet)
metrics:                2 methods  (_tick_metrics_on_cycle_end, _get_metrics_summary)
```

---

## 7. Architectural Red Flags

| Flag | Location | Severity | Description |
|------|----------|----------|-------------|
| **Monolithic result** | `_result` field, 807 reads | CRITICAL | SprintSchedulerResult has 270+ fields. Any change to result shape requires diffing across 800+ read sites. |
| **Safe async bridge** | asyncio.run + ThreadPoolExecutor | ✅ SAFE | F223D safe bridge: checks running loop first, uses TPE only when loop is live |
| **Multi-writer shared state** | `_public_outcome` (13 writers) | HIGH | No transactional boundary. Future parallelization risk. |
| **Acquisition plan scatter** | `_acquisition_plan` (79 reads) | HIGH | Lane eligibility computation scattered. Adding new lanes requires navigating 79 read sites. |
| **Sidecar orchestrator hidden** | `_sidecar_orchestrator` (8 reads) | MEDIUM | Unclear what sidecars run when. Advisory gate snapshot written in 4 places but orchestrator is singleton. |
| **LMDB env proliferation** | 3 separate LMDB envs (forensics, multimodal, dedup) | MEDIUM | 3 LMDB environments opened/closed/flushed independently. Potential resource pressure on M1. |
| **DuckDB dual connection** | `_duckdb_store` + `_duckdb_read_con` | MEDIUM | Separate read connection maintained alongside write store. Unclear when to use which. |

---

## 8. Summary Tables

### Top 5 Extraction Candidates (Safest Path)

| Rank | Candidate | Fields Involved | Method Count | Stub Complexity | Rationale |
|------|-----------|----------------|--------------|-----------------|----------|
| 1 | PivotPlanner | _pivot_queue | 5 | LOW | Append-only queue, isolated execution, clean inject interface |
| 2 | SourceEconomicsEngine | _source_economics | 7 | LOW | Pure functions, single write site, clear inject interface |
| 3 | LatencyEMATracker | _fetch_latency_ema | 2 | LOW | Simple rolling average, no cross-field deps |

### Top 5 Danger Zones

| Rank | Zone | Severity | Root Cause |
|------|------|----------|------------|
| 1 | `_result` 811 accesses | CRITICAL | Monolithic 270+ field dataclass, no staged building |
| 2 | `_public_outcome` 13 writers | HIGH | No transactional boundary, scattered writes |
| 3 | `_acquisition_plan` 79 reads | HIGH | Lane eligibility scattered across all phases |
| 4 | DuckDB dual connection | MEDIUM | Separate read connection, unclear routing |
| 5 | LMDB env proliferation | MEDIUM | 3 separate envs (forensics, multimodal, dedup) |

---

*No code was moved. Recommendations only.*