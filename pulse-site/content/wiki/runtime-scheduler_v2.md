+++
title = "runtime/scheduler_v2/"
weight = 21
description = "<think> Let me analyze the structural context to understand this module and write a proper module overview.  ## Module: runtime/scheduler_v2  Key observations: 1. **Purpose**: This is a greenfield rew..."

[extra]
tier = 2
file_count = 6
total_lines = 3357
languages = "Python"
parent_path = "runtime"
+++

<think>
Let me analyze the structural context to understand this module and write a proper module overview.

## Module: runtime/scheduler_v2

Key observations:
1. **Purpose**: This is a greenfield rewrite of the Sprint Scheduler (v2) that orchestrates the sprint execution pipeline. It replaces a 33,449 LOC monolithic scheduler with a thin orchestrator that delegates to typed Phase implementations.

2. **Core Architecture**:
 - Uses a Protocol-based phase composition pattern
 - All state is passed explicitly via `SprintContext` rather than stored in instance attributes
 - Three main phases: Prelude → Acquisition → Winddown
 - Fail-soft initialization pattern with `InitResult`

3. **Key Components**:
 - `SprintSchedulerV2` - main orchestrator class
 - `SprintContext` - shared immutable context passed to all phases
 - `AcquisitionOrchestrator` - manages the cycle loop
 - `WinddownOrchestrator` - manages export, synthesis, teardown
 - Multiple prelude lane runners (PUBLIC, CT, WAYBACK, PDNS, DOH)

4. **Dependencies**:
 - No outgoing dependencies detected (Tier 2)
 - No incoming dependencies detected

5. **Scale**:
 - 6 Python files
 - 3357 total lines
 - Average 559 lines per file
 - This is a fairly substantial module

6. **Key Patterns**:
 - 138 functions, 21 classes, 118 methods
 - Very high symbol density
 - Explicit state passing (not hidden state)
 - Type-safe patterns (Protocol, msgspec. Struct for performance)
 - Immutable context with mutable per-cycle state in `_CycleState`

7. **Fan-in/Fan-out**:
 - Zero incoming dependencies - this is unusual and worth noting
 - Zero outgoing dependencies - it's isolated and self-contained

This module is essentially self-contained with no external connections, functioning as an isolated hub within the codebase. Its complexity is notable—over 3,300 lines across six files with nearly 300 symbols total. I should highlight its role as a greenfield rewrite of the original scheduler, the Protocol-based architecture for phase composition, the explicit state management approach, and the fail-soft initialization strategy, while also pointing out the atypical isolation pattern and the shift toward performance-optimized types like msgspec. Struct.
</think>

This module serves as the v2 orchestrator for sprint execution, implementing a complete rewrite of the original monolithic scheduler using a Protocol-based phase composition pattern. The core design replaces 33,449 lines of coupled instance state with a thin `SprintSchedulerV2` class that delegates all work to typed Phase implementations (`PreludePhase`, `AcquisitionPhase`, `WinddownPhase`), while threading an immutable `SprintContext` through every phase so that state remains explicit and testable in isolation.

The module coordinates three distinct execution stages: a parallel prelude phase that fires multiple data-source lanes (public feeds, Certificate Transparency, Wayback, passive DNS, DNS-over-HTTPS) with bounded timeouts, an iterative acquisition loop that drives feed, public-discovery, and crawl branches until terminal conditions or hard deadlines are reached, and a winddown phase responsible for flushing deduplication state, running exporters, awaiting synthesis tasks, and unloading lazy models.

Context management uses a frozen `SprintContext` with a nested mutable `_CycleState` to isolate per-cycle mutations from cross-cycle leakage, and all service initialization follows a fail-soft pattern where `InitResult[T]` captures success, failure, elapsed time, and error details rather than returning `None` silently. A Rust Q-table integration enables temporal predictor-guided pre-warming of connection pools, with explicit reward signals recorded after each pre-warm attempt to break cold-start deadlock in early cycles.

At 3,357 lines across six files with 138 functions and 21 classes, this module is substantially smaller than its predecessor yet carries high symbolic density, and it currently sits with zero incoming or outgoing dependencies—effectively operating as a leaf within its tier.

## Structure

| Language | Files |
|---|---|
| Python | 6 |

### Largest Files

- `acquisition.py` (1246 lines)
- `scheduler.py` (1084 lines)
- `winddown.py` (409 lines)
- `protocol.py` (296 lines)
- `prelude.py` (240 lines)
- `__init__.py` (82 lines)


## Dependencies

No outgoing dependencies detected.

## Dependents

No incoming dependencies detected.

## Key Symbols

<p><strong>Key definitions:</strong></p>
<ul>
<li>
<p><code>__post_init__</code> (Function) in scheduler.py — referenced in 54 files</p>
<ul><li class="ref-list">Referenced by: __init__.py, _models.py, _stage_protocol.py, academic_search.py, acquisition_strategy.py +48 more</li></ul>
</li>
<li>
<p><code>SprintContext</code> (Class) in protocol.py — referenced in 11 files</p>
<details><summary>Shared immutable context passed to all phase orchestrators.</summary>
<div class="doc-comment">
<p>Shared immutable context passed to all phase orchestrators.</p>
<p></p>
<p>Unlike v1's `self._*` slots, v2 passes all state explicitly via this</p>
<p>context. This makes phases testable in isolation and enables the</p>
<p>greenfield rewrite without 156-slot coupling.</p>
<p></p>
<p>All mutable fields (result, bg_tasks, cancel_event) are passed as</p>
<p>explicit references, not hidden state.</p>
<p></p>
<p>Per-cycle mutable state is stored in `_cycle` field. Type checker</p>
<p>prevents accidental field addition to this class — new per-cycle fields</p>
<p>MUST be added to _CycleState and accessed via ctx._cycle.FIELD.</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py, _mlx_dispatcher.py, acquisition.py, acquisition_loop.py, prelude.py +4 more</li></ul>
</li>
<li>
<p><code>SprintSchedulerV2</code> (Class) in scheduler.py — referenced in 10 files</p>
<details><summary>SprintScheduler v2 — greenfield rewrite with Protocol-based phase composition.</summary>
<div class="doc-comment">
<p>SprintScheduler v2 — greenfield rewrite with Protocol-based phase composition.</p>
<p></p>
<p>Replaces the 33 449 LOC `SprintScheduler` with a thin orchestrator that</p>
<p>delegates to typed Phase implementations. All state is passed explicitly</p>
<p>via SprintContext rather than stored in instance attributes.</p>
<p></p>
<p>Issue #047 fix: @dataclass(slots=True) — __slots__ auto-generated from fields,</p>
<p>__init__ auto-generated, no duplication between __slots__ tuple and __init__ body.</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py, protocol.py, sprint_entrypoint.py, sprint_entrypoint_injections.py, sprint_scheduler.py +3 more</li></ul>
</li>
<li>
<p><code>_initialize_sprint_run</code> (Function) in scheduler.py — referenced in 4 files</p>
<details><summary>Initialize DuckDB, lifecycle, governor, Hermes prewarm.</summary>
<div class="doc-comment">
<p>Initialize DuckDB, lifecycle, governor, Hermes prewarm.</p>
<p></p>
<p>Corresponds to v1's _initialize_sprint_run (lines ~6600-7168).</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: _lazy_imports.py, sprint_scheduler_v1_archived.py, test_sprint_scheduler.py</li></ul>
</li>
<li>
<p><code>_run_one_cycle_stable</code> (Function) in acquisition.py — referenced in 4 files</p>
<ul><li class="ref-list">Referenced by: sprint_scheduler_v1_archived.py, test_sprint_f271.py, test_sprint_p11_early_exit.py</li></ul>
</li>
</ul>

<details><summary><strong>Function</strong> (138)</summary>
<ul>
<li><code>run</code> (acquisition.py)</li>
<li><code>_maybe_dispatch_nonfeed_probe_lanes</code> (acquisition.py)</li>
<li><code>_run_synthesis_sidecar</code> (acquisition.py)</li>
<li><code>_run_synthesis_sidecar</code> (scheduler.py)
<details><summary>Sprint F259: Run SynthesisRunner in WINDUP phase.</summary>
<div class="doc-comment">
<p>Sprint F259: Run SynthesisRunner in WINDUP phase.</p>
<p></p>
<p>Delegates to AcquisitionOrchestrator._run_synthesis_sidecar if available,</p>
<p>otherwise runs inline.</p>
</div>
</details>
</li>
<li><code>_prewarm_temporal_predictor</code> (scheduler.py)
<details><summary>ISSUE #009 + ISSUE B/D fix: Temporal predictor + Q-table guided pre-warm.</summary>
<div class="doc-comment">
<p>ISSUE #009 + ISSUE B/D fix: Temporal predictor + Q-table guided pre-warm.</p>
<p></p>
<p>Runs in background (fire-and-forget) after prelude lanes complete.</p>
<p>1. TemporalIOCPredictor.predict_next_iocs() → predicted IOCs</p>
<p>2. PrefetchOracleIntegration.get_best_prefetch_actions() → Q-table ranked targets</p>
<p>3. prewarm_pool.acquire_session() → parallel TLS handshakes for predicted hosts</p>
<p>4. record_prefetch_outcome() → ISSUE B fix: reward signal to Rust Q-table</p>
<p></p>
<p>ISSUE B fix:</p>
<p>- record_prefetch_outcome() called after each pre-warm attempt so Rust</p>
<p>Q-table learns from pre-warm success/failure and updates future ranking.</p>
<p>- Uses next_state_key='first_cycle' so subsequent Q-table lookups in</p>
<p>the first real cycle read from the correct learned state.</p>
<p></p>
<p>ISSUE D fix:</p>
<p>- State transitions from 'prelude' → 'first_cycle' after pre-warm</p>
<p>completes. This means first-cycle prefetch decisions use a different</p>
<p>(learned) Q-state than pre-warm, breaking the cold-start deadlock.</p>
<p></p>
<p>Fail-soft: any error is caught and logged; pre-warm is best-effort.</p>
</div>
</details>
</li>
<li><code>_run_one_cycle_aggressive</code> (acquisition.py)</li>
<li><code>_run_one_cycle_stable</code> (acquisition.py)</li>
<li><code>_initialize_sprint_run</code> (scheduler.py)
<details><summary>Initialize DuckDB, lifecycle, governor, Hermes prewarm.</summary>
<div class="doc-comment">
<p>Initialize DuckDB, lifecycle, governor, Hermes prewarm.</p>
<p></p>
<p>Corresponds to v1's _initialize_sprint_run (lines ~6600-7168).</p>
</div>
</details>
</li>
<li><code>_prewarm_bg</code> (scheduler.py)</li>
<li><code>_run_prelude_and_first_cycle</code> (scheduler.py)
<details><summary>Run prelude lanes and first cycle in parallel.</summary>
<div class="doc-comment">
<p>Run prelude lanes and first cycle in parallel.</p>
<p></p>
<p>Corresponds to v1's gather at lines ~7755-7858.</p>
</div>
</details>
</li>
<li><code>run</code> (winddown.py)</li>
<li><code>run_doh_prelude_lane</code> (prelude.py) — <span class="doc-comment-inline">Run DOH prelude lane.</span></li>
<li><code>__post_init__</code> (scheduler.py)</li>
<li><code>record_hypothesis_feedback</code> (scheduler.py)</li>
<li><code>_run_prelude</code> (scheduler.py) — <span class="doc-comment-inline">Run the prelude phase via PreludeOrchestrator.</span></li>
<li><code>__getattr__</code> (__init__.py)</li>
<li><code>aclose</code> (scheduler.py)
<details><summary>Graceful shutdown — F285 canonical async cleanup path.</summary>
<div class="doc-comment">
<p>Graceful shutdown — F285 canonical async cleanup path.</p>
<p></p>
<p>Cancels the cancel event, cancels sidecar tasks, closes DuckDB store</p>
<p>and evidence log. Idempotent — safe to call multiple times.</p>
</div>
</details>
</li>
<li><code>_run_acquisition_loop</code> (scheduler.py)
<details><summary>Run acquisition cycles until terminal via AcquisitionOrchestrator.</summary>
<div class="doc-comment">
<p>Run acquisition cycles until terminal via AcquisitionOrchestrator.</p>
<p></p>
<p>Corresponds to v1's while-not-terminal loop (lines ~7894-8300+).</p>
</div>
</details>
</li>
<li><code>run_ct_prelude_lane</code> (prelude.py)
<details><summary>Run CT prelude lane.</summary>
<div class="doc-comment">
<p>Run CT prelude lane.</p>
<p></p>
<p>Returns LaneResult with telemetry written to result (ctx.result).</p>
<p>Bounded: 15s asyncio.timeout, max 5 results.</p>
</div>
</details>
</li>
<li><code>_run_public_branch_aggressive</code> (acquisition.py)</li>
<li><code>run_wayback_prelude_lane</code> (prelude.py)
<details><summary>Run WAYBACK prelude lane.</summary>
<div class="doc-comment">
<p>Run WAYBACK prelude lane.</p>
<p></p>
<p>Bounded: no hard timeout, writes to duckdb_store via bg_tasks.</p>
</div>
</details>
</li>
<li><code>_run_ct_branch_aggressive</code> (acquisition.py)</li>
<li><code>_build_acquisition_plan</code> (scheduler.py) — <span class="doc-comment-inline">Build acquisition plan from query + governor state.</span></li>
<li><code>_run_winddown</code> (scheduler.py)
<details><summary>Run winddown phase via WinddownOrchestrator.</summary>
<div class="doc-comment">
<p>Run winddown phase via WinddownOrchestrator.</p>
<p></p>
<p>Corresponds to v1's _run_winddown + teardown (lines ~8976-9200+).</p>
</div>
</details>
</li>
<li><code>_run_public_branch</code> (acquisition.py)</li>
<li><code>run</code> (scheduler.py) — <span class="doc-comment-inline">Run the sprint — orchestrate prelude → acquisition → winddown phases.</span></li>
<li><code>_run_one_cycle</code> (acquisition.py)</li>
<li><code>run_pdns_prelude_lane</code> (prelude.py) — <span class="doc-comment-inline">Run PASSIVE_DNS prelude lane.</span></li>
<li><code>_build_work_items</code> (acquisition.py)
<details><summary>Build tiered work items from ordered sources.</summary>
<div class="doc-comment">
<p>Build tiered work items from ordered sources.</p>
<p></p>
<p>Each work item has .url, .timeout_s, .max_results — compatible with</p>
<p>_async_run_live_feed and the live_feed_pipeline signature.</p>
<p></p>
<p>Bounded parallelism: feed sources within a cycle run concurrently via</p>
<p>Semaphore(max_parallel_sources) in fetch_one() — not sequential drain.</p>
</div>
</details>
</li>
<li><code>_run_ct_branch</code> (acquisition.py)</li>
<li><code>_init_sidecar_orchestrator</code> (scheduler.py) — <span class="doc-comment-inline">Initialize SidecarOrchestrator (fail-soft).</span></li>
<li><code>_run_export</code> (winddown.py) — <span class="doc-comment-inline">Run all four exporters + CTI + hypothesis. Returns {paths, errors}.</span></li>
<li><code>_async_run_live_feed</code> (acquisition.py)</li>
<li><code>_run_feed_branch_aggressive</code> (acquisition.py)</li>
<li><code>_init_duckdb_store</code> (scheduler.py) — <span class="doc-comment-inline">Initialize DuckDBShadowStore (fail-soft).</span></li>
<li><code>dataclass_replace</code> (protocol.py)
<details><summary>Type-safe replacement for frozen dataclass replace().</summary>
<div class="doc-comment">
<p>Type-safe replacement for frozen dataclass replace().</p>
<p></p>
<p>Uses object's __dataclass_fields__ to validate at runtime.</p>
<p>Works with frozen=True, slots=True dataclasses.</p>
</div>
</details>
</li>
<li><code>run_public_prelude_lane</code> (prelude.py)
<details><summary>Run PUBLIC prelude lane.</summary>
<div class="doc-comment">
<p>Run PUBLIC prelude lane.</p>
<p></p>
<p>Returns LaneResult, never raises.</p>
<p>Bounded: 10s asyncio.timeout, max 3 results, concurrency 2.</p>
</div>
</details>
</li>
<li><code>_init_governor</code> (scheduler.py) — <span class="doc-comment-inline">Initialize M1ResourceGovernor (fail-soft).</span></li>
<li><code>_init_hermes_engine</code> (scheduler.py) — <span class="doc-comment-inline">Initialize Hermes3Engine (fail-soft).</span></li>
<li><code>_init_evidence_log</code> (scheduler.py) — <span class="doc-comment-inline">Initialize EvidenceLog (fail-soft).</span></li>
<li><code>_graceful_sidecar_shutdown</code> (winddown.py) — <span class="doc-comment-inline">Graceful sidecar task shutdown with 15s bounded timeout.</span></li>
<li><code>_run_one_registered_sidecar</code> (acquisition.py)</li>
<li><code>_hypothesis_export_impl</code> (winddown.py) — <span class="doc-comment-inline">Hypothesis export implementation (lazy import inner).</span></li>
<li><code>run_public_branch</code> (acquisition.py) — <span class="doc-comment-inline">Run public discovery with remaining-time timeout.</span></li>
<li><code>_build_seed_context</code> (acquisition.py) — <span class="doc-comment-inline">Build seed context from query and acquisition plan.</span></li>
<li><code>_run_first_cycle</code> (scheduler.py) — <span class="doc-comment-inline">Run the first acquisition cycle (feed only, stable mode).</span></li>
<li><code>_maybe_call_pressure_relief</code> (winddown.py)
<details><summary>F273G: Call malloc_zone_pressure_relief if governor recommends.</summary>
<div class="doc-comment">
<p>F273G: Call malloc_zone_pressure_relief if governor recommends.</p>
<p></p>
<p>Standalone function (originally SprintScheduler method) for winddown use.</p>
<p>Safe to call at any point - no-op if unavailable.</p>
</div>
</details>
</li>
<li><code>_cancel_bg_tasks</code> (winddown.py) — <span class="doc-comment-inline">Cancel all background speculative tasks.</span></li>
<li><code>_ensure_pre_windup_lane_terminal_states</code> (acquisition.py)</li>
<li><code>_check_zero_findings_alert</code> (acquisition.py) — <span class="doc-comment-inline">Check zero-findings alert after each cycle.</span></li>
<li><code>__init__</code> (scheduler.py)</li>
<li><code>_close_privacy_layer</code> (winddown.py) — <span class="doc-comment-inline">Close privacy context at TEARDOWN.</span></li>
<li><code>run_feed_branch</code> (acquisition.py) — <span class="doc-comment-inline">Run feed sources and return (results, ok, count).</span></li>
<li><code>_get_effective_max_cycles</code> (acquisition.py) — <span class="doc-comment-inline">Adaptive max_cycles based on cycle_time EMA.</span></li>
<li><code>_finalize_result_truth</code> (acquisition.py)</li>
<li><code>_await_synthesis</code> (winddown.py) — <span class="doc-comment-inline">Await synthesis task launched during windup entry.</span></li>
<li><code>_sync_latent_relationships_to_graph</code> (winddown.py) — <span class="doc-comment-inline">Sync latent NetworkX relationships → DuckPGQ with low confidence.</span></li>
<li><code>_run_sidecars</code> (winddown.py) — <span class="doc-comment-inline">Run all advisory steps via SidecarOrchestrator.</span></li>
<li><code>_close_graph</code> (winddown.py) — <span class="doc-comment-inline">Save RelationshipDiscoveryEngine graph and sync latent relationships.</span></li>
<li><code>with_cycle</code> (protocol.py)
<details><summary>Return a new SprintContext with updated per-cycle state.</summary>
<div class="doc-comment">
<p>Return a new SprintContext with updated per-cycle state.</p>
<p></p>
<p>Usage::</p>
<p></p>
<p>ctx = ctx.with_cycle(wall_clock_start=ts, lifecycle=lifecycle_mgr)</p>
<p>ctx = ctx.with_cycle(stop_requested=True, barrier_retry_count=2)</p>
</div>
</details>
</li>
<li><code>_ensure_nonfeed_predispatch_before_finalization</code> (acquisition.py)</li>
<li><code>_ensure_mandatory_nonfeed_before_return</code> (acquisition.py)</li>
<li><code>_teardown_browser_pool</code> (winddown.py) — <span class="doc-comment-inline">Teardown nodriver/camoufox lazy state at sprint winddown.</span></li>
<li><code>_run_ane_semantic_dedup_advisory</code> (winddown.py) — <span class="doc-comment-inline">Run ANE semantic dedup advisory (near-duplicate detection).</span></li>
<li><code>build</code> (protocol.py)
<details><summary>Build a new SprintContext with required fields and defaults.</summary>
<div class="doc-comment">
<p>Build a new SprintContext with required fields and defaults.</p>
<p></p>
<p>Usage::</p>
<p></p>
<p>ctx = SprintContext.build(config, query, result)</p>
<p>ctx = ctx.with_services(duckdb_store=store, governor=gov)</p>
</div>
</details>
</li>
<li><code>fetch_one</code> (acquisition.py)</li>
<li><code>fetch_one</code> (acquisition.py)</li>
<li><code>_ensure_dedup_loaded</code> (acquisition.py) — <span class="doc-comment-inline">Ensure lazy dedup is loaded before first cycle.</span></li>
<li><code>_check_hard_deadline</code> (acquisition.py) — <span class="doc-comment-inline">Returns False if hard deadline exceeded.</span></li>
<li><code>_check_prewindup_barrier_sync</code> (acquisition.py)</li>
<li><code>_flush_dedup</code> (acquisition.py) — <span class="doc-comment-inline">Flush dedup at WINDUP entry.</span></li>
<li><code>_maybe_export_partial</code> (acquisition.py)</li>
<li><code>_feed_dominance_should_fetch</code> (acquisition.py)</li>
<li><code>_prewarm_hermes</code> (scheduler.py) — <span class="doc-comment-inline">Prewarm Hermes model in background.</span></li>
<li><code>_run_export_as_task</code> (winddown.py)
<details><summary>Run export and store results in ctx._export_result for parallel retrieval.</summary>
<div class="doc-comment">
<p>Run export and store results in ctx._export_result for parallel retrieval.</p>
<p></p>
<p>This wraps _run_export for use in TaskGroup - stores results in ctx</p>
<p>so the calling code can retrieve them after the TaskGroup completes.</p>
</div>
</details>
</li>
<li><code>_cti_export_impl</code> (winddown.py) — <span class="doc-comment-inline">CTI STIX export implementation (lazy import inner).</span></li>
<li><code>_run_hypothesis_export</code> (winddown.py) — <span class="doc-comment-inline">Sprint F259: Run causal hypothesis generation and export.</span></li>
<li><code>_shutdown_entity_signal_extractor</code> (winddown.py) — <span class="doc-comment-inline">Shutdown entity_signal_extractor ThreadPoolExecutor.</span></li>
<li><code>_run_vacuum</code> (winddown.py) — <span class="doc-comment-inline">Post-export DuckDB vacuum — reclaim space if DB &gt; 2GB.</span></li>
<li><code>_close_dedup</code> (winddown.py) — <span class="doc-comment-inline">Close persistent dedup at TEARDOWN.</span></li>
<li><code>_close_duckdb</code> (winddown.py) — <span class="doc-comment-inline">Close DuckDB store at teardown.</span></li>
<li><code>_drain_pending_pattern_extractions</code> (acquisition.py)</li>
<li><code>_run_ioc_cooccurrence_sidecar</code> (acquisition.py)</li>
<li><code>_run_epistemic_gap_advisory</code> (acquisition.py)</li>
<li><code>_close_enrichment</code> (winddown.py) — <span class="doc-comment-inline">Close forensics enricher and LMDB at TEARDOWN.</span></li>
<li><code>_maybe_launch_enhanced_research</code> (winddown.py) — <span class="doc-comment-inline">Launch deep research advisory fire-and-forget after teardown/export.</span></li>
<li><code>_unload_hermes_at_teardown</code> (winddown.py) — <span class="doc-comment-inline">Hermes engine teardown via ModelManager (bounded M1 8GB lifecycle).</span></li>
<li><code>_unload_lazy_models</code> (winddown.py) — <span class="doc-comment-inline">Release all lazy models (NER, GNN, ANE, MoE) via brain._lazy.</span></li>
<li><code>with_services</code> (protocol.py)
<details><summary>Return a new SprintContext with services initialized (type-safe).</summary>
<div class="doc-comment">
<p>Return a new SprintContext with services initialized (type-safe).</p>
<p></p>
<p>Each service field accepts an InitResult[T] (from fail-soft init) or None.</p>
<p>Access the live object via result.value when result.ok is True.</p>
</div>
</details>
</li>
<li><code>__new__</code> (scheduler.py)</li>
<li><code>inject_evidence_log</code> (scheduler.py) — <span class="doc-comment-inline">Inject a pre-initialized EvidenceLog (wraps in InitResult.success).</span></li>
<li><code>inject_duckdb_store</code> (scheduler.py) — <span class="doc-comment-inline">Inject a pre-initialized DuckDBShadowStore (wraps in InitResult.success).</span></li>
<li><code>_run_cti_export</code> (winddown.py) — <span class="doc-comment-inline">Run CTI STIX export.</span></li>
<li><code>_wb_ingest_bg</code> (prelude.py)</li>
<li><code>_pdns_ingest_bg</code> (prelude.py)</li>
<li><code>_doh_ingest_bg</code> (prelude.py)</li>
<li><code>__repr__</code> (scheduler.py)</li>
<li><code>gather_taskgroup</code> (prelude.py) — <span class="doc-comment-inline">Wrapper around utils.async_helpers.gather_taskgroup for prelude lanes.</span></li>
<li><code>inject_prefetch_oracle</code> (scheduler.py)</li>
<li><code>inject_prefetch_pipeline</code> (scheduler.py)</li>
<li><code>inject_temporal_predictor</code> (scheduler.py)</li>
<li><code>inject_pivot_planner</code> (scheduler.py)</li>
<li><code>inject_analyst_workbench</code> (scheduler.py)</li>
<li><code>inject_forensics_enricher</code> (scheduler.py)</li>
<li><code>inject_enrichment_services</code> (scheduler.py)</li>
<li><code>inject_privacy_layer</code> (scheduler.py)</li>
<li><code>inject_ioc_graph</code> (scheduler.py)</li>
<li><code>_import_exporters</code> (winddown.py) — <span class="doc-comment-inline">Lazy import all four exporters + CTI collector.</span></li>
<li><code>_maybe_call_pressure_relief</code> (acquisition.py) — <span class="doc-comment-inline">Call malloc_zone_pressure_relief if governor recommends.</span></li>
<li><code>_prioritize_sources</code> (acquisition.py) — <span class="doc-comment-inline">Re-prioritize sources using latest graph stats.</span></li>
<li><code>sprint_id</code> (scheduler.py) — <span class="doc-comment-inline">Return _sprint_id (setter stores there, not in result).</span></li>
<li><code>sprint_id</code> (scheduler.py) — <span class="doc-comment-inline">Set sprint_id (backward compat for tests).</span></li>
<li><code>inject_policy_manager</code> (scheduler.py)</li>
<li><code>inject_communication_layer</code> (scheduler.py)</li>
<li><code>inject_stealth_layer</code> (scheduler.py)</li>
<li><code>inject_ghost_layer</code> (scheduler.py)</li>
<li><code>inject_security_coordinator</code> (scheduler.py)</li>
<li><code>__init__</code> (scheduler.py)</li>
<li><code>health_check</code> (scheduler.py) — <span class="doc-comment-inline">Stub health check — returns None (pass).</span></li>
<li><code>_build_diagnostic_report</code> (winddown.py) — <span class="doc-comment-inline">Build minimal diagnostic report from result + config.</span></li>
<li><code>is_terminal</code> (protocol.py) — <span class="doc-comment-inline">True if runner has reached a terminal phase.</span></li>
<li><code>should_enter_windup</code> (protocol.py) — <span class="doc-comment-inline">True if windup should begin.</span></li>
<li><code>windup_guard</code> (protocol.py) — <span class="doc-comment-inline">True if windup guard allows continuation.</span></li>
<li><code>run</code> (protocol.py) — <span class="doc-comment-inline">Run the phase. Returns phase-specific result.</span></li>
<li><code>run</code> (protocol.py) — <span class="doc-comment-inline">Run prelude lanes (PUBLIC, CT, WAYBACK, PDNS, DOH).</span></li>
<li><code>run</code> (protocol.py) — <span class="doc-comment-inline">Run one acquisition cycle (feed/public/CT branches).</span></li>
<li><code>run</code> (protocol.py) — <span class="doc-comment-inline">Run winddown (flush, export, synthesis, teardown).</span></li>
<li><code>ok</code> (protocol.py) — <span class="doc-comment-inline">True if init succeeded (value is not None).</span></li>
<li><code>success</code> (protocol.py) — <span class="doc-comment-inline">Construct a success result.</span></li>
<li><code>failure</code> (protocol.py) — <span class="doc-comment-inline">Construct a failure result.</span></li>
<li><code>wall_clock_start</code> (protocol.py) — <span class="doc-comment-inline">Wall clock start — monotonic timestamp when sprint started.</span></li>
<li><code>is_terminal</code> (protocol.py) — <span class="doc-comment-inline">True if runner has reached a terminal phase.</span></li>
<li><code>_build_lane_query</code> (prelude.py)</li>
<li><code>__init__</code> (acquisition.py)</li>
<li><code>inject_multimodal_enricher</code> (scheduler.py)</li>
<li><code>inject_source_economics</code> (scheduler.py)</li>
<li><code>result</code> (scheduler.py)</li>
<li><code>__init__</code> (winddown.py)</li>
</ul>
</details>

<details><summary><strong>Class</strong> (21)</summary>
<ul>
<li><code>AcquisitionOrchestrator</code> (acquisition.py)
<details><summary>Orchestrates the main acquisition cycle loop.</summary>
<div class="doc-comment">
<p>Orchestrates the main acquisition cycle loop.</p>
<p></p>
<p>Replaces the 33 449 LOC SprintScheduler's while-not-terminal loop</p>
<p>with a thin class that delegates to typed cycle functions.</p>
<p></p>
<p>Lifecycle:</p>
<p>run() → while not ctx.runner.is_terminal():</p>
<p>→ _check_hard_deadline()</p>
<p>→ _ensure_pre_windup_lane_terminal_states()</p>
<p>→ _drain_pending_pattern_extractions()</p>
<p>→ _maybe_call_pressure_relief()</p>
<p>→ _runner.windup_guard()</p>
<p>→ _run_one_cycle()  (stable or aggressive)</p>
</div>
</details>
</li>
<li><code>SprintSchedulerV2</code> (scheduler.py)
<details><summary>SprintScheduler v2 — greenfield rewrite with Protocol-based phase composition.</summary>
<div class="doc-comment">
<p>SprintScheduler v2 — greenfield rewrite with Protocol-based phase composition.</p>
<p></p>
<p>Replaces the 33 449 LOC `SprintScheduler` with a thin orchestrator that</p>
<p>delegates to typed Phase implementations. All state is passed explicitly</p>
<p>via SprintContext rather than stored in instance attributes.</p>
<p></p>
<p>Issue #047 fix: @dataclass(slots=True) — __slots__ auto-generated from fields,</p>
<p>__init__ auto-generated, no duplication between __slots__ tuple and __init__ body.</p>
</div>
</details>
</li>
<li><code>WinddownOrchestrator</code> (winddown.py)
<details><summary>Orchestrates the winddown phase: export, synthesis, teardown.</summary>
<div class="doc-comment">
<p>Orchestrates the winddown phase: export, synthesis, teardown.</p>
<p></p>
<p>Replaces the 33 449 LOC SprintScheduler's winddown section</p>
<p>with a thin class that delegates to typed sub-operations.</p>
<p></p>
<p>Lifecycle:</p>
<p>run() → _run_winddown_sequence()</p>
<p>→ _maybe_call_pressure_relief()</p>
<p>→ runner.teardown()</p>
<p>→ _run_export(lifecycle)</p>
<p>→ _await_synthesis()</p>
<p>→ _run_vacuum()</p>
<p>→ _close_dedup()</p>
<p>→ _close_graph()</p>
<p>→ _close_enrichment()</p>
<p>→ _run_sidecars()</p>
<p>→ _run_ane_semantic_dedup()</p>
<p>→ _maybe_launch_research()</p>
<p>→ _unload_hermes()</p>
<p>→ _unload_lazy_models()</p>
<p>→ _cancel_bg_tasks()</p>
<p>→ _graceful_sidecar_shutdown()</p>
<p>→ _close_duckdb()</p>
</div>
</details>
</li>
<li><code>SprintContext</code> (protocol.py)
<details><summary>Shared immutable context passed to all phase orchestrators.</summary>
<div class="doc-comment">
<p>Shared immutable context passed to all phase orchestrators.</p>
<p></p>
<p>Unlike v1's `self._*` slots, v2 passes all state explicitly via this</p>
<p>context. This makes phases testable in isolation and enables the</p>
<p>greenfield rewrite without 156-slot coupling.</p>
<p></p>
<p>All mutable fields (result, bg_tasks, cancel_event) are passed as</p>
<p>explicit references, not hidden state.</p>
<p></p>
<p>Per-cycle mutable state is stored in `_cycle` field. Type checker</p>
<p>prevents accidental field addition to this class — new per-cycle fields</p>
<p>MUST be added to _CycleState and accessed via ctx._cycle.FIELD.</p>
</div>
</details>
</li>
<li><code>_CycleState</code> (protocol.py)
<details><summary>Per-cycle mutable state — isolated to prevent cross-cycle leakage.</summary>
<div class="doc-comment">
<p>Per-cycle mutable state — isolated to prevent cross-cycle leakage.</p>
<p></p>
<p>Unlike SprintContext (which is immutable/frozen), _CycleState IS mutable</p>
<p>because per-cycle state changes frequently within a single cycle (e.g.,</p>
<p>barrier_retry_count increments, cycle_time_ema updates).</p>
<p></p>
<p>Lifecycle: a fresh _CycleState is created at the START of each cycle</p>
<p>(acquisition, winddown) and passed via SprintContext._cycle. This prevents</p>
<p>cross-cycle state leakage while keeping the phase orchestrator API clean.</p>
<p></p>
<p>Usage::</p>
<p></p>
<p>ctx = ctx.with_cycle(barrier_retry_count=2)</p>
<p>ctx._cycle.barrier_retry_count += 1  # mutate in-place within cycle</p>
</div>
</details>
</li>
<li><code>InitResult</code> (protocol.py)
<details><summary>Result of a fail-soft init — captures success/failure with reason.</summary>
<div class="doc-comment">
<p>Result of a fail-soft init — captures success/failure with reason.</p>
<p></p>
<p>Replaces ``try/except → return None`` antipattern across all SprintSchedulerV2</p>
<p>service inits. Every init now logs (warning + elapsed_ms) on failure rather</p>
<p>than silently returning None.</p>
<p></p>
<p>Usage::</p>
<p></p>
<p>result: InitResult[DuckDBShadowStore] = await _init_duckdb_store(query)</p>
<p>if result.ok:</p>
<p>store = result.value</p>
<p>else:</p>
<p>logger.warning("DuckDB init failed after %.1fms: %s",</p>
<p>result.elapsed_ms, result.error)</p>
</div>
</details>
</li>
<li><code>CycleResult</code> (acquisition.py)
<details><summary>Result from one acquisition cycle.</summary>
<div class="doc-comment">
<p>Result from one acquisition cycle.</p>
<p></p>
<p>Migrated from @dataclass to msgspec.Struct (frozen=True) for:</p>
<p>- 5-7× faster instantiation</p>
<p>- Built-in __eq__/__hash__ on slot fields</p>
<p>- ~50% smaller memory footprint</p>
<p>- JSON serialization via msgspec</p>
</div>
</details>
</li>
<li><code>PhaseRunner</code> (protocol.py) — <span class="doc-comment-inline">Protocol for the lifecycle runner (SprintLifecycleManager).</span></li>
<li><code>LaneResult</code> (prelude.py)</li>
<li><code>_FeedWork</code> (acquisition.py)
<details><summary>Work item for one feed source. Compatible with _async_run_live_feed signature.</summary>
<div class="doc-comment">
<p>Work item for one feed source. Compatible with _async_run_live_feed signature.</p>
<p></p>
<p>Migrated from @dataclass(slots=True) to msgspec.Struct (frozen=True).</p>
</div>
</details>
</li>
<li><code>AcquisitionPhaseResult</code> (protocol.py) — <span class="doc-comment-inline">Result from one acquisition cycle.</span></li>
<li><code>_MinimalCtx</code> (scheduler.py)</li>
<li><code>WinddownPhaseResult</code> (winddown.py) — <span class="doc-comment-inline">Result from the winddown phase.</span></li>
<li><code>PreludePhaseResult</code> (protocol.py) — <span class="doc-comment-inline">Result from the prelude phase.</span></li>
<li><code>Phase</code> (protocol.py) — <span class="doc-comment-inline">Base protocol for all SprintScheduler v2 phases.</span></li>
<li><code>PreludePhase</code> (protocol.py) — <span class="doc-comment-inline">Prelude phase: runs mandatory acquisition prelude lanes in parallel.</span></li>
<li><code>AcquisitionPhase</code> (protocol.py) — <span class="doc-comment-inline">Acquisition phase: runs the main cycle loop.</span></li>
<li><code>WinddownPhase</code> (protocol.py) — <span class="doc-comment-inline">Winddown phase: export, synthesis, teardown.</span></li>
<li><code>WinddownPhaseResult</code> (protocol.py) — <span class="doc-comment-inline">Result from the winddown phase.</span></li>
<li><code>_SeedCtx</code> (acquisition.py)</li>
<li><code>BarrierResult</code> (acquisition.py)</li>
</ul>
</details>

<details><summary><strong>Method</strong> (118)</summary>
<ul>
<li><code>run</code> (acquisition.py)</li>
<li><code>_maybe_dispatch_nonfeed_probe_lanes</code> (acquisition.py)</li>
<li><code>_run_synthesis_sidecar</code> (acquisition.py)</li>
<li><code>_run_synthesis_sidecar</code> (scheduler.py)
<details><summary>Sprint F259: Run SynthesisRunner in WINDUP phase.</summary>
<div class="doc-comment">
<p>Sprint F259: Run SynthesisRunner in WINDUP phase.</p>
<p></p>
<p>Delegates to AcquisitionOrchestrator._run_synthesis_sidecar if available,</p>
<p>otherwise runs inline.</p>
</div>
</details>
</li>
<li><code>_prewarm_temporal_predictor</code> (scheduler.py)
<details><summary>ISSUE #009 + ISSUE B/D fix: Temporal predictor + Q-table guided pre-warm.</summary>
<div class="doc-comment">
<p>ISSUE #009 + ISSUE B/D fix: Temporal predictor + Q-table guided pre-warm.</p>
<p></p>
<p>Runs in background (fire-and-forget) after prelude lanes complete.</p>
<p>1. TemporalIOCPredictor.predict_next_iocs() → predicted IOCs</p>
<p>2. PrefetchOracleIntegration.get_best_prefetch_actions() → Q-table ranked targets</p>
<p>3. prewarm_pool.acquire_session() → parallel TLS handshakes for predicted hosts</p>
<p>4. record_prefetch_outcome() → ISSUE B fix: reward signal to Rust Q-table</p>
<p></p>
<p>ISSUE B fix:</p>
<p>- record_prefetch_outcome() called after each pre-warm attempt so Rust</p>
<p>Q-table learns from pre-warm success/failure and updates future ranking.</p>
<p>- Uses next_state_key='first_cycle' so subsequent Q-table lookups in</p>
<p>the first real cycle read from the correct learned state.</p>
<p></p>
<p>ISSUE D fix:</p>
<p>- State transitions from 'prelude' → 'first_cycle' after pre-warm</p>
<p>completes. This means first-cycle prefetch decisions use a different</p>
<p>(learned) Q-state than pre-warm, breaking the cold-start deadlock.</p>
<p></p>
<p>Fail-soft: any error is caught and logged; pre-warm is best-effort.</p>
</div>
</details>
</li>
<li><code>_run_one_cycle_aggressive</code> (acquisition.py)</li>
<li><code>_run_one_cycle_stable</code> (acquisition.py)</li>
<li><code>_initialize_sprint_run</code> (scheduler.py)
<details><summary>Initialize DuckDB, lifecycle, governor, Hermes prewarm.</summary>
<div class="doc-comment">
<p>Initialize DuckDB, lifecycle, governor, Hermes prewarm.</p>
<p></p>
<p>Corresponds to v1's _initialize_sprint_run (lines ~6600-7168).</p>
</div>
</details>
</li>
<li><code>_run_prelude_and_first_cycle</code> (scheduler.py)
<details><summary>Run prelude lanes and first cycle in parallel.</summary>
<div class="doc-comment">
<p>Run prelude lanes and first cycle in parallel.</p>
<p></p>
<p>Corresponds to v1's gather at lines ~7755-7858.</p>
</div>
</details>
</li>
<li><code>run</code> (winddown.py)</li>
<li><code>__post_init__</code> (scheduler.py)</li>
<li><code>record_hypothesis_feedback</code> (scheduler.py)</li>
<li><code>_run_prelude</code> (scheduler.py) — <span class="doc-comment-inline">Run the prelude phase via PreludeOrchestrator.</span></li>
<li><code>aclose</code> (scheduler.py)
<details><summary>Graceful shutdown — F285 canonical async cleanup path.</summary>
<div class="doc-comment">
<p>Graceful shutdown — F285 canonical async cleanup path.</p>
<p></p>
<p>Cancels the cancel event, cancels sidecar tasks, closes DuckDB store</p>
<p>and evidence log. Idempotent — safe to call multiple times.</p>
</div>
</details>
</li>
<li><code>_run_acquisition_loop</code> (scheduler.py)
<details><summary>Run acquisition cycles until terminal via AcquisitionOrchestrator.</summary>
<div class="doc-comment">
<p>Run acquisition cycles until terminal via AcquisitionOrchestrator.</p>
<p></p>
<p>Corresponds to v1's while-not-terminal loop (lines ~7894-8300+).</p>
</div>
</details>
</li>
<li><code>_run_public_branch_aggressive</code> (acquisition.py)</li>
<li><code>_run_ct_branch_aggressive</code> (acquisition.py)</li>
<li><code>_build_acquisition_plan</code> (scheduler.py) — <span class="doc-comment-inline">Build acquisition plan from query + governor state.</span></li>
<li><code>_run_winddown</code> (scheduler.py)
<details><summary>Run winddown phase via WinddownOrchestrator.</summary>
<div class="doc-comment">
<p>Run winddown phase via WinddownOrchestrator.</p>
<p></p>
<p>Corresponds to v1's _run_winddown + teardown (lines ~8976-9200+).</p>
</div>
</details>
</li>
<li><code>_run_public_branch</code> (acquisition.py)</li>
<li><code>run</code> (scheduler.py) — <span class="doc-comment-inline">Run the sprint — orchestrate prelude → acquisition → winddown phases.</span></li>
<li><code>_run_one_cycle</code> (acquisition.py)</li>
<li><code>_build_work_items</code> (acquisition.py)
<details><summary>Build tiered work items from ordered sources.</summary>
<div class="doc-comment">
<p>Build tiered work items from ordered sources.</p>
<p></p>
<p>Each work item has .url, .timeout_s, .max_results — compatible with</p>
<p>_async_run_live_feed and the live_feed_pipeline signature.</p>
<p></p>
<p>Bounded parallelism: feed sources within a cycle run concurrently via</p>
<p>Semaphore(max_parallel_sources) in fetch_one() — not sequential drain.</p>
</div>
</details>
</li>
<li><code>_run_ct_branch</code> (acquisition.py)</li>
<li><code>_init_sidecar_orchestrator</code> (scheduler.py) — <span class="doc-comment-inline">Initialize SidecarOrchestrator (fail-soft).</span></li>
<li><code>_run_export</code> (winddown.py) — <span class="doc-comment-inline">Run all four exporters + CTI + hypothesis. Returns {paths, errors}.</span></li>
<li><code>_async_run_live_feed</code> (acquisition.py)</li>
<li><code>_run_feed_branch_aggressive</code> (acquisition.py)</li>
<li><code>_init_duckdb_store</code> (scheduler.py) — <span class="doc-comment-inline">Initialize DuckDBShadowStore (fail-soft).</span></li>
<li><code>_init_governor</code> (scheduler.py) — <span class="doc-comment-inline">Initialize M1ResourceGovernor (fail-soft).</span></li>
<li><code>_init_hermes_engine</code> (scheduler.py) — <span class="doc-comment-inline">Initialize Hermes3Engine (fail-soft).</span></li>
<li><code>_init_evidence_log</code> (scheduler.py) — <span class="doc-comment-inline">Initialize EvidenceLog (fail-soft).</span></li>
<li><code>_graceful_sidecar_shutdown</code> (winddown.py) — <span class="doc-comment-inline">Graceful sidecar task shutdown with 15s bounded timeout.</span></li>
<li><code>_hypothesis_export_impl</code> (winddown.py) — <span class="doc-comment-inline">Hypothesis export implementation (lazy import inner).</span></li>
<li><code>_build_seed_context</code> (acquisition.py) — <span class="doc-comment-inline">Build seed context from query and acquisition plan.</span></li>
<li><code>_run_first_cycle</code> (scheduler.py) — <span class="doc-comment-inline">Run the first acquisition cycle (feed only, stable mode).</span></li>
<li><code>_cancel_bg_tasks</code> (winddown.py) — <span class="doc-comment-inline">Cancel all background speculative tasks.</span></li>
<li><code>_ensure_pre_windup_lane_terminal_states</code> (acquisition.py)</li>
<li><code>_check_zero_findings_alert</code> (acquisition.py) — <span class="doc-comment-inline">Check zero-findings alert after each cycle.</span></li>
<li><code>__init__</code> (scheduler.py)</li>
<li><code>_close_privacy_layer</code> (winddown.py) — <span class="doc-comment-inline">Close privacy context at TEARDOWN.</span></li>
<li><code>_get_effective_max_cycles</code> (acquisition.py) — <span class="doc-comment-inline">Adaptive max_cycles based on cycle_time EMA.</span></li>
<li><code>_finalize_result_truth</code> (acquisition.py)</li>
<li><code>_await_synthesis</code> (winddown.py) — <span class="doc-comment-inline">Await synthesis task launched during windup entry.</span></li>
<li><code>_sync_latent_relationships_to_graph</code> (winddown.py) — <span class="doc-comment-inline">Sync latent NetworkX relationships → DuckPGQ with low confidence.</span></li>
<li><code>_run_sidecars</code> (winddown.py) — <span class="doc-comment-inline">Run all advisory steps via SidecarOrchestrator.</span></li>
<li><code>_close_graph</code> (winddown.py) — <span class="doc-comment-inline">Save RelationshipDiscoveryEngine graph and sync latent relationships.</span></li>
<li><code>with_cycle</code> (protocol.py)
<details><summary>Return a new SprintContext with updated per-cycle state.</summary>
<div class="doc-comment">
<p>Return a new SprintContext with updated per-cycle state.</p>
<p></p>
<p>Usage::</p>
<p></p>
<p>ctx = ctx.with_cycle(wall_clock_start=ts, lifecycle=lifecycle_mgr)</p>
<p>ctx = ctx.with_cycle(stop_requested=True, barrier_retry_count=2)</p>
</div>
</details>
</li>
<li><code>_ensure_nonfeed_predispatch_before_finalization</code> (acquisition.py)</li>
<li><code>_ensure_mandatory_nonfeed_before_return</code> (acquisition.py)</li>
<li><code>_teardown_browser_pool</code> (winddown.py) — <span class="doc-comment-inline">Teardown nodriver/camoufox lazy state at sprint winddown.</span></li>
<li><code>_run_ane_semantic_dedup_advisory</code> (winddown.py) — <span class="doc-comment-inline">Run ANE semantic dedup advisory (near-duplicate detection).</span></li>
<li><code>build</code> (protocol.py)
<details><summary>Build a new SprintContext with required fields and defaults.</summary>
<div class="doc-comment">
<p>Build a new SprintContext with required fields and defaults.</p>
<p></p>
<p>Usage::</p>
<p></p>
<p>ctx = SprintContext.build(config, query, result)</p>
<p>ctx = ctx.with_services(duckdb_store=store, governor=gov)</p>
</div>
</details>
</li>
<li><code>_ensure_dedup_loaded</code> (acquisition.py) — <span class="doc-comment-inline">Ensure lazy dedup is loaded before first cycle.</span></li>
<li><code>_check_hard_deadline</code> (acquisition.py) — <span class="doc-comment-inline">Returns False if hard deadline exceeded.</span></li>
<li><code>_check_prewindup_barrier_sync</code> (acquisition.py)</li>
<li><code>_flush_dedup</code> (acquisition.py) — <span class="doc-comment-inline">Flush dedup at WINDUP entry.</span></li>
<li><code>_maybe_export_partial</code> (acquisition.py)</li>
<li><code>_feed_dominance_should_fetch</code> (acquisition.py)</li>
<li><code>_prewarm_hermes</code> (scheduler.py) — <span class="doc-comment-inline">Prewarm Hermes model in background.</span></li>
<li><code>_run_export_as_task</code> (winddown.py)
<details><summary>Run export and store results in ctx._export_result for parallel retrieval.</summary>
<div class="doc-comment">
<p>Run export and store results in ctx._export_result for parallel retrieval.</p>
<p></p>
<p>This wraps _run_export for use in TaskGroup - stores results in ctx</p>
<p>so the calling code can retrieve them after the TaskGroup completes.</p>
</div>
</details>
</li>
<li><code>_cti_export_impl</code> (winddown.py) — <span class="doc-comment-inline">CTI STIX export implementation (lazy import inner).</span></li>
<li><code>_run_hypothesis_export</code> (winddown.py) — <span class="doc-comment-inline">Sprint F259: Run causal hypothesis generation and export.</span></li>
<li><code>_shutdown_entity_signal_extractor</code> (winddown.py) — <span class="doc-comment-inline">Shutdown entity_signal_extractor ThreadPoolExecutor.</span></li>
<li><code>_run_vacuum</code> (winddown.py) — <span class="doc-comment-inline">Post-export DuckDB vacuum — reclaim space if DB &gt; 2GB.</span></li>
<li><code>_close_dedup</code> (winddown.py) — <span class="doc-comment-inline">Close persistent dedup at TEARDOWN.</span></li>
<li><code>_close_duckdb</code> (winddown.py) — <span class="doc-comment-inline">Close DuckDB store at teardown.</span></li>
<li><code>_drain_pending_pattern_extractions</code> (acquisition.py)</li>
<li><code>_run_ioc_cooccurrence_sidecar</code> (acquisition.py)</li>
<li><code>_run_epistemic_gap_advisory</code> (acquisition.py)</li>
<li><code>_close_enrichment</code> (winddown.py) — <span class="doc-comment-inline">Close forensics enricher and LMDB at TEARDOWN.</span></li>
<li><code>_maybe_launch_enhanced_research</code> (winddown.py) — <span class="doc-comment-inline">Launch deep research advisory fire-and-forget after teardown/export.</span></li>
<li><code>_unload_hermes_at_teardown</code> (winddown.py) — <span class="doc-comment-inline">Hermes engine teardown via ModelManager (bounded M1 8GB lifecycle).</span></li>
<li><code>_unload_lazy_models</code> (winddown.py) — <span class="doc-comment-inline">Release all lazy models (NER, GNN, ANE, MoE) via brain._lazy.</span></li>
<li><code>with_services</code> (protocol.py)
<details><summary>Return a new SprintContext with services initialized (type-safe).</summary>
<div class="doc-comment">
<p>Return a new SprintContext with services initialized (type-safe).</p>
<p></p>
<p>Each service field accepts an InitResult[T] (from fail-soft init) or None.</p>
<p>Access the live object via result.value when result.ok is True.</p>
</div>
</details>
</li>
<li><code>__new__</code> (scheduler.py)</li>
<li><code>inject_evidence_log</code> (scheduler.py) — <span class="doc-comment-inline">Inject a pre-initialized EvidenceLog (wraps in InitResult.success).</span></li>
<li><code>inject_duckdb_store</code> (scheduler.py) — <span class="doc-comment-inline">Inject a pre-initialized DuckDBShadowStore (wraps in InitResult.success).</span></li>
<li><code>_run_cti_export</code> (winddown.py) — <span class="doc-comment-inline">Run CTI STIX export.</span></li>
<li><code>__repr__</code> (scheduler.py)</li>
<li><code>inject_prefetch_oracle</code> (scheduler.py)</li>
<li><code>inject_prefetch_pipeline</code> (scheduler.py)</li>
<li><code>inject_temporal_predictor</code> (scheduler.py)</li>
<li><code>inject_pivot_planner</code> (scheduler.py)</li>
<li><code>inject_analyst_workbench</code> (scheduler.py)</li>
<li><code>inject_forensics_enricher</code> (scheduler.py)</li>
<li><code>inject_enrichment_services</code> (scheduler.py)</li>
<li><code>inject_privacy_layer</code> (scheduler.py)</li>
<li><code>inject_ioc_graph</code> (scheduler.py)</li>
<li><code>_maybe_call_pressure_relief</code> (acquisition.py) — <span class="doc-comment-inline">Call malloc_zone_pressure_relief if governor recommends.</span></li>
<li><code>_prioritize_sources</code> (acquisition.py) — <span class="doc-comment-inline">Re-prioritize sources using latest graph stats.</span></li>
<li><code>sprint_id</code> (scheduler.py) — <span class="doc-comment-inline">Return _sprint_id (setter stores there, not in result).</span></li>
<li><code>sprint_id</code> (scheduler.py) — <span class="doc-comment-inline">Set sprint_id (backward compat for tests).</span></li>
<li><code>inject_policy_manager</code> (scheduler.py)</li>
<li><code>inject_communication_layer</code> (scheduler.py)</li>
<li><code>inject_stealth_layer</code> (scheduler.py)</li>
<li><code>inject_ghost_layer</code> (scheduler.py)</li>
<li><code>inject_security_coordinator</code> (scheduler.py)</li>
<li><code>__init__</code> (scheduler.py)</li>
<li><code>health_check</code> (scheduler.py) — <span class="doc-comment-inline">Stub health check — returns None (pass).</span></li>
<li><code>_build_diagnostic_report</code> (winddown.py) — <span class="doc-comment-inline">Build minimal diagnostic report from result + config.</span></li>
<li><code>is_terminal</code> (protocol.py) — <span class="doc-comment-inline">True if runner has reached a terminal phase.</span></li>
<li><code>should_enter_windup</code> (protocol.py) — <span class="doc-comment-inline">True if windup should begin.</span></li>
<li><code>windup_guard</code> (protocol.py) — <span class="doc-comment-inline">True if windup guard allows continuation.</span></li>
<li><code>run</code> (protocol.py) — <span class="doc-comment-inline">Run the phase. Returns phase-specific result.</span></li>
<li><code>run</code> (protocol.py) — <span class="doc-comment-inline">Run prelude lanes (PUBLIC, CT, WAYBACK, PDNS, DOH).</span></li>
<li><code>run</code> (protocol.py) — <span class="doc-comment-inline">Run one acquisition cycle (feed/public/CT branches).</span></li>
<li><code>run</code> (protocol.py) — <span class="doc-comment-inline">Run winddown (flush, export, synthesis, teardown).</span></li>
<li><code>ok</code> (protocol.py) — <span class="doc-comment-inline">True if init succeeded (value is not None).</span></li>
<li><code>success</code> (protocol.py) — <span class="doc-comment-inline">Construct a success result.</span></li>
<li><code>failure</code> (protocol.py) — <span class="doc-comment-inline">Construct a failure result.</span></li>
<li><code>wall_clock_start</code> (protocol.py) — <span class="doc-comment-inline">Wall clock start — monotonic timestamp when sprint started.</span></li>
<li><code>is_terminal</code> (protocol.py) — <span class="doc-comment-inline">True if runner has reached a terminal phase.</span></li>
<li><code>__init__</code> (acquisition.py)</li>
<li><code>inject_multimodal_enricher</code> (scheduler.py)</li>
<li><code>inject_source_economics</code> (scheduler.py)</li>
<li><code>result</code> (scheduler.py)</li>
<li><code>__init__</code> (winddown.py)</li>
</ul>
</details>

<details><summary><strong>Constant</strong> (1)</summary>
<ul>
<li><code>T</code> (protocol.py)</li>
</ul>
</details>



## Metrics

| Metric | Value |
|---|---|
| Files | 6 |
| Total lines | 3357 |
| Avg lines/file | 559 |
| Languages | Python |
| Outgoing deps | 0 |
| Incoming deps | 0 |
| Tier | 2 |

