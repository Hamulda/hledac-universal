+++
title = "runtime/"
weight = 14
description = "<think> The user wants me to create a module overview for the 'runtime' module for a codebase wiki. I need to follow strict rules:  1. NEVER start with 'The X module consists of...', 'This module cont..."

[extra]
tier = 1
file_count = 136
total_lines = 64461
languages = "Python"
has_mermaid = true
+++

<think>
The user wants me to create a module overview for the "runtime" module for a codebase wiki. I need to follow strict rules:

1. NEVER start with 'The X module consists of...', 'This module contains...', or any variant
2. First sentence MUST state what the module DOES or what PURPOSE it serves
3. Focus on PURPOSE, RESPONSIBILITIES, and ARCHITECTURAL ROLE — not on listing individual files
4. Describe the module's architectural role: hub, leaf, or bridge
5. Explain how it fits into the larger system — what it provides and consumes
6. Note if high fan-in (many dependents) — changes have wide blast radius
7. Note complexity: file count, line count, symbol density
8. Do NOT enumerate specific file names, class names, or function names unless they represent a truly central abstraction
9. Vary sentence structure
10. Write 4-8 sentences
11. Be specific about what the module does and its scale
12. Do NOT speculate about design intent
13. NEVER leave missing spaces between words

Looking at the structural context:

**Module: runtime**
- 136 Python files, 64,461 total lines (473 avg lines/file)
- Outgoing deps: 4 (knowledge, tool_registry.py, utils, project_types.py)
- Incoming deps: 0 (no dependents!)
- Tier: 1 (top-level)

**Key information:**
- This is the core sprint execution engine
- Contains Sprint Scheduler (referenced in 87 files), Sprint SchedulerResult, run_sprint function
- Contains acquisition lanes (CT, Wayback, PassiveDNS, etc.), sidecars, protocols, adapters
- Largest files include sprint_scheduler_v1_archived.py (18,237 lines), sprint_entrypoint.py (3,678 lines), source_finding_bridge.py (2,774 lines)


- The module manages the end-to-end sprint lifecycle and coordinates data ingestion across multiple sources and enrichment pipelines. Despite having zero incoming dependencies, it anchors the entire system's execution flow as the primary orchestrator. Its sheer size—64K+ lines across 136 files—makes it a high-risk component for unintended side effects when modifications are made.
</think>

This module serves as the central execution engine for OSINT investigation sprints, orchestrating time-boxed acquisition cycles that fetch threat intelligence from multiple sources, run pattern matching, evaluate findings against quality gates, and persist results to DuckDB with graph enrichment. It implements a phased lifecycle (BOOT → WARMUP → ACTIVE → WINDUP → EXPORT → TEARDOWN) with adaptive timing that scales windup duration based on sprint length and observed cycle performance, ensuring bounded resource consumption on constrained hardware like M1 8GB systems. The module operates as a hub at the system center, consuming feed sources, DuckDB storage, and MLX model inference while producing structured findings, telemetry, and exported artifacts; it has zero incoming dependencies, making it a foundational Tier 1 component where changes cascade directly into all downstream consumers. With 136 Python files totaling over 64,000 lines, the codebase reflects substantial organic growth, including a legacy 18,000-line archived scheduler alongside newer protocol-based and lane-based abstractions that suggest an active refactoring trajectory toward smaller, composable phases. Domain-centric sidecar plugins (discovery, enrichment, forensics) and multi-lane acquisition (CT, Wayback, PassiveDNS, blockchain, academic) extend the core scheduler without modifying it, while a resource governor enforces M1 memory safety as an always-on advisory layer that never blocks execution.

## Dependency Diagram

{% mermaid() %}
graph LR
    m_runtime["<b>runtime/</b>"]
    style m_runtime fill:#a78bfa,color:#0d0d0d,stroke:#a78bfa
    m_tool_registry_py["tool_registry.py/"]
    m_runtime -->|2| m_tool_registry_py
    m_project_types_py["project_types.py/"]
    m_runtime -->|1| m_project_types_py
    m_knowledge["knowledge/"]
    m_runtime -->|1| m_knowledge
    m_utils["utils/"]
    m_runtime -->|1| m_utils
    classDef default fill:#1a1a2e,stroke:#a78bfa,color:#e0e0e0
    click m_runtime "/wiki/runtime/"
    click m_tool_registry_py "/wiki/tool_registry.py/"
    click m_project_types_py "/wiki/project_types.py/"
    click m_knowledge "/wiki/knowledge/"
    click m_utils "/wiki/utils/"
{% end %}

## Structure

### Sub-modules

- [**acquisition/**](/wiki/runtime-acquisition/) — 14 files, 3321 lines (Python)
- [**acquisition_lanes/**](/wiki/runtime-acquisition_lanes/) — 4 files, 164 lines (Python)
- [**adapters/**](/wiki/runtime-adapters/) — 4 files, 843 lines (Python)
- [**protocols/**](/wiki/runtime-protocols/) — 16 files, 1027 lines (Python)
- [**scheduler/**](/wiki/runtime-scheduler/) — 5 files, 2698 lines (Python)
- [**scheduler/core/**](/wiki/runtime-scheduler-core/) — 3 files, 315 lines (Python)
- [**scheduler_v2/**](/wiki/runtime-scheduler_v2/) — 6 files, 3357 lines (Python)
- [**sidecars/**](/wiki/runtime-sidecars/) — 15 files, 486 lines (Python)
- [**sidecars/discovery/**](/wiki/runtime-sidecars-discovery/) — 6 files, 145 lines (Python)
- [**sidecars/enrichment/**](/wiki/runtime-sidecars-enrichment/) — 4 files, 91 lines (Python)
- [**sidecars/forensics/**](/wiki/runtime-sidecars-forensics/) — 3 files, 67 lines (Python)

| Language | Files |
|---|---|
| Python | 136 |

### Directories

| Directory | Files | Lines |
|---|---|---|
| scheduler_v2/ | 6 | 3357 |
| acquisition/ | 14 | 3321 |
| scheduler/ | 5 | 2698 |
| protocols/ | 16 | 1027 |
| adapters/ | 4 | 843 |
| cti/ | 1 | 584 |
| sidecars/ | 15 | 486 |
| state/ | 1 | 421 |
| context/ | 2 | 228 |
| acquisition_lanes/ | 4 | 164 |
| patterns/ | 2 | 149 |
| scheduler_phases/ | 2 | 98 |

### Largest Files

- `sprint_scheduler_v1_archived.py` (18237 lines)
- `sprint_entrypoint.py` (3678 lines)
- `source_finding_bridge.py` (2774 lines)
- `acquisition_strategy.py` (2634 lines)
- `scheduler/lanes/__init__.py` (2341 lines)
- `shadow_pre_decision.py` (2090 lines)
- `scheduler_v2/acquisition.py` (1246 lines)
- `sidecar_protocol_adapters.py` (1238 lines)
- `sidecar_orchestrator.py` (1087 lines)
- `scheduler_v2/scheduler.py` (1084 lines)

<details><summary><strong>Show 126 more files</strong></summary>

- `pivot_planner.py` (951 lines)
- `role_based_pools.py` (791 lines)
- `nonfeed_candidate_ledger.py` (699 lines)
- `sidecar_bus.py` (697 lines)
- `resource_governor.py` (687 lines)
- `sprint_lifecycle.py` (679 lines)
- `adapters/graph_adapter.py` (653 lines)
- `scheduler_result.py` (623 lines)
- `sprint_advisory_runner.py` (621 lines)
- `nonfeed_seed_runtime.py` (609 lines)
- `acquisition_telemetry_reconcile.py` (601 lines)
- `acquisition/plan_builder.py` (587 lines)
- `cti/db/duckdb_domain_mv.py` (584 lines)
- `finding_pipeline.py` (509 lines)
- `worker_pool.py` (507 lines)
- `shadow_inputs.py` (499 lines)
- `acquisition/mission.py` (498 lines)
- `next_seeds_consumption.py` (480 lines)
- `observability.py` (460 lines)
- `investigation_planner.py` (457 lines)
- `sprint_entrypoint_injections.py` (439 lines)
- `unified_resource_manager.py` (431 lines)
- `int_counter_layout.py` (430 lines)
- `state/__init__.py` (421 lines)
- `scheduler_v2/winddown.py` (409 lines)
- `evidence_corroboration.py` (403 lines)
- `unified_executor.py` (395 lines)
- `error_policy.py` (393 lines)
- `sidecar_protocol.py` (389 lines)
- `sprint_lifecycle_runner.py` (381 lines)
- `sidecar_runner_decorator.py` (350 lines)
- `acquisition/report_builder.py` (349 lines)
- `corroboration_score.py` (322 lines)
- `acquisition/nonfeed_eligibility.py` (322 lines)
- `enrichment_services.py` (310 lines)
- `sprint_timer.py` (307 lines)
- `sprint_types.py` (307 lines)
- `windup_engine.py` (296 lines)
- `scheduler_v2/protocol.py` (296 lines)
- `nonfeed_seed_extractor.py` (290 lines)
- `_telemetry_setup.py` (282 lines)
- `source_finding_config.py` (274 lines)
- `prewarm_daemon.py` (274 lines)
- `telemetry.py` (272 lines)
- `shadow_parity.py` (264 lines)
- `acquisition/budget.py` (254 lines)
- `scheduler_v2/prelude.py` (240 lines)
- `pivot_executor.py` (237 lines)
- `acquisition/domain_expansion.py` (233 lines)
- `osint_query_expander.py` (222 lines)
- `hypothesis_feedback.py` (221 lines)
- `acquisition/nonfeed_outcomes.py` (207 lines)
- `protocols/cleanup_protocol.py` (206 lines)
- `acquisition/__init__.py` (200 lines)
- `scheduler/core/lifecycle.py` (188 lines)
- `context/bounded_dicts.py` (185 lines)
- `opsec_policy.py` (180 lines)
- `memory_authority.py` (176 lines)
- `acquisition/threat_dictionary.py` (175 lines)
- `sidecar_dispatcher.py` (173 lines)
- `observability_async_handler.py` (169 lines)
- `scheduler_config.py` (162 lines)
- `graph_accumulator.py` (161 lines)
- `privacy_budget.py` (146 lines)
- `wakefd_integration.py` (141 lines)
- `protocols/graph_protocol.py` (137 lines)
- `acquisition/profile.py` (135 lines)
- `patterns/discovery.py` (131 lines)
- `acquisition/lane_plan.py` (112 lines)
- `sidecars/_base.py` (108 lines)
- `health.py` (106 lines)
- `sprint_scheduler.py` (100 lines)
- `scheduler/core/types.py` (99 lines)
- `acquisition/lane_constants.py` (96 lines)
- `protocols/__init__.py` (90 lines)
- `acquisition/acquisition_lanes.py` (87 lines)
- `adapters/duckdb_adapter.py` (86 lines)
- `scheduler_phases/prelude.py` (84 lines)
- `scheduler_v2/__init__.py` (82 lines)
- `cli_session.py` (82 lines)
- `sidecars/__init__.py` (75 lines)
- `lifecycle_registry.py` (66 lines)
- `acquisition/cid_detection.py` (66 lines)
- `hermes_pivot_contract.py` (66 lines)
- `adapters/fetch_adapter.py` (66 lines)
- `__init__.py` (64 lines)
- `scheduler_phases.py` (61 lines)
- `memory_watchdog.py` (59 lines)
- `protocols/storage_protocol.py` (57 lines)
- `sidecar_legacy_adapters.py` (52 lines)
- `protocols/pivot_protocol.py` (52 lines)
- `protocols/brain_protocol.py` (49 lines)
- `protocols/transport_protocol.py` (48 lines)
- `protocols/lane_protocol.py` (48 lines)
- `protocols/score_protocol.py` (47 lines)
- `acquisition_lanes/_core.py` (46 lines)
- `sidecars/discovery/__init__.py` (45 lines)
- `acquisition_lanes/__init__.py` (44 lines)
- `protocols/intel_protocol.py` (44 lines)
- `context/__init__.py` (43 lines)
- `acquisition_lanes/_planning.py` (43 lines)
- `scheduler/__init__.py` (42 lines)
- `protocols/fetch_protocol.py` (42 lines)
- `protocols/metrics_protocol.py` (42 lines)
- `protocols/prefetch_protocol.py` (42 lines)
- `protocols/enrichment_protocol.py` (41 lines)
- `protocols/lifecycle_protocol.py` (41 lines)
- `protocols/layers_protocol.py` (41 lines)
- `adapters/__init__.py` (38 lines)
- `sidecars/enrichment/__init__.py` (38 lines)
- `sidecars/forensics/__init__.py` (35 lines)
- `acquisition_lanes/_nonfeed.py` (31 lines)
- `scheduler/core/config.py` (28 lines)
- `logging_setup.py` (24 lines)
- `sidecars/discovery/_dht.py` (23 lines)
- `sidecars/discovery/_commoncrawl.py` (23 lines)
- `sidecars/discovery/_ipfs.py` (22 lines)
- `sidecars/enrichment/_ti_feed.py` (21 lines)
- `patterns/__init__.py` (18 lines)
- `sidecars/discovery/_onion.py` (16 lines)
- `sidecars/discovery/_i2p.py` (16 lines)
- `sidecars/forensics/_digital_ghost.py` (16 lines)
- `sidecars/forensics/_steganography.py` (16 lines)
- `sidecars/enrichment/_banner.py` (16 lines)
- `sidecars/enrichment/_bgp.py` (16 lines)
- `scheduler_phases/__init__.py` (14 lines)

</details>


## Dependencies

Depends on **4 files** across **4 modules**.

**[knowledge/](@/wiki/knowledge.md)** (1 files):
- `duckdb_store.py`

**[tool_registry.py/](@/wiki/tool_registry.py.md)** (1 files):
- `tool_registry.py`

**[utils/](@/wiki/utils.md)** (1 files):
- `ioc_extract.py`

**[project_types.py/](@/wiki/project_types.py.md)** (1 files):
- `project_types.py`



## Dependents

No incoming dependencies detected.

## Key Symbols

<p><strong>Key definitions:</strong></p>
<ul>
<li>
<p><code>SprintScheduler</code> (Class) in sprint_scheduler_v1_archived.py — referenced in 87 files</p>
<ul><li class="ref-list">Referenced by: __init__.py, __main__.py, _base.py, _ti_feed.py, acquisition.py +74 more</li></ul>
</li>
<li>
<p><code>SprintSchedulerResult</code> (Class) in sprint_scheduler_v1_archived.py — referenced in 35 files</p>
<details><summary>Outcome of one sprint run.</summary>
<div class="doc-comment">
<p>Outcome of one sprint run.</p>
<p></p>
<p></p>
<p></p>
<p>Attributes:</p>
<p></p>
<p>cycles_started: Number of fetch cycles initiated.</p>
<p></p>
<p>cycles_completed: Number of fetch cycles that completed all phases.</p>
<p></p>
<p>unique_entry_hashes_seen: Count of deduplicated entries processed.</p>
<p></p>
<p>duplicate_entry_hashes_skipped: Count of duplicate entries filtered.</p>
<p></p>
<p>total_pattern_hits: Sum of pattern matches across all sources.</p>
<p></p>
<p>accepted_findings: Findings that passed quality gate.</p>
<p></p>
<p>entries_per_source: Breakdown of entries by source (source_name -&gt; count).</p>
<p></p>
<p>hits_per_source: Pattern hits per source (source_name -&gt; count).</p>
<p></p>
<p>final_phase: Last phase reached (BOOT, GATHER, JUDGMENT, EXPORT, TEARDOWN).</p>
<p></p>
<p>export_paths: List of paths where sprint results were exported.</p>
<p></p>
<p>aborted: True if sprint was aborted early.</p>
<p></p>
<p>abort_reason: Human-readable reason for abortion.</p>
<p></p>
<p>stop_requested: True when stop_on_first_accepted triggered acceptance.</p>
<p></p>
<p>public_discovered: Public pipeline discoveries (F8XE).</p>
<p></p>
<p>public_fetched: Public pipeline successful fetches.</p>
<p></p>
<p>public_matched_patterns: Public pipeline pattern matches.</p>
<p></p>
<p>public_accepted_findings: Public pipeline accepted findings.</p>
<p></p>
<p>public_stored_findings: Public pipeline stored findings.</p>
<p></p>
<p>public_error: Public pipeline error message.</p>
<p></p>
<p>ct_log_discovered: CT log discoveries (F193A).</p>
<p></p>
<p>ct_log_stored: CT log stored findings.</p>
<p></p>
<p>ct_log_accepted_findings: CT log accepted findings (F194A).</p>
<p></p>
<p>ct_log_error: CT log error message.</p>
<p></p>
<p>entered_active_at_monotonic: Timestamp when ACTIVE phase first entered.</p>
<p></p>
<p>pre_loop_elapsed_s: Wall-clock seconds from run() to loop guard entry.</p>
<p></p>
<p>first_cycle_started_at_monotonic: Timestamp of first cycles_started increment.</p>
<p></p>
<p>pre_active_starved: True when gap between entered_active and first_cycle_started &gt; 30s.</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py, _lazy_imports.py, acquisition_strategy.py, bgp_advisor_adapter.py, bounded_dicts.py +26 more</li></ul>
</li>
<li>
<p><code>run_sprint</code> (Function) in sprint_entrypoint.py — referenced in 27 files</p>
<ul><li class="ref-list">Referenced by: __init__.py, __main__.py, benchmark_pipeline.py, cleanup_protocol.py, composition_root.py +21 more</li></ul>
</li>
<li>
<p><code>M1ResourceGovernor</code> (Class) in resource_governor.py — referenced in 26 files</p>
<details><summary>Advisory safety layer for M1 8GB sprint execution.</summary>
<div class="doc-comment">
<p>Advisory safety layer for M1 8GB sprint execution.</p>
<p></p>
<p>Governs: branch concurrency, model lease, renderer lease.</p>
<p>Always-on, fail-soft. Never blocks the sprint — only advises.</p>
<p></p>
<p>Read-only surfaces:</p>
<p>brain.model_lifecycle.get_model_lifecycle_status()</p>
<p>core.resource_governor.sample_uma_status()</p>
<p>utils.concurrency.FETCH_SEMAPHORE.limit()</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: _lazy_imports.py, adaptive_context_policy.py, analyst_workbench.py, concurrency.py, concurrency_registry.py +19 more</li></ul>
</li>
<li>
<p><code>SprintLifecycleManager</code> (Class) in sprint_lifecycle.py — referenced in 19 files</p>
<details><summary>Lightweight sprint lifecycle state machine.</summary>
<div class="doc-comment">
<p>Lightweight sprint lifecycle state machine.</p>
<p></p>
<p>All methods accept an optional ``now_monotonic`` parameter to allow</p>
<p>deterministic testing with a fake clock. When omitted the call uses</p>
<p>``time.monotonic()`` at runtime.</p>
<p></p>
<p>Issue 1.2 — Phase TaskGroup Integration:</p>
<p>``_on_phase_exit_callbacks`` is a list of callables invoked synchronously</p>
<p>by ``_transition_to_unlocked()`` AFTER the phase field is updated.</p>
<p>Each callback receives ``(from_phase: SprintPhase, to_phase: SprintPhase)``.</p>
<p>This replaces the "cancel_event flag" pattern — the callback closes the</p>
<p>old phase TaskGroup, which cancels all lane subtasks cleanly.</p>
<p></p>
<p>Always-on, bounded, fail-safe: callbacks are called inside a</p>
<p>``try/except Exception`` loop; a failing callback never blocks the</p>
<p>transition. Callbacks are invoked in registration order.</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __main__.py, _lazy_imports.py, composition_root.py, conftest.py, htn_planner.py +12 more</li></ul>
</li>
</ul>

<details><summary><strong>Function</strong> (1126)</summary>
<ul>
<li><code>run_sprint</code> (sprint_entrypoint.py)</li>
<li><code>_run_internal</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_run_mandatory_acquisition_prelude</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_run_one_cycle_aggressive</code> (sprint_scheduler_v1_archived.py)
<details><summary>Aggressive mode: feed, public discovery, and CT branches fire concurrently.</summary>
<div class="doc-comment">
<p>Aggressive mode: feed, public discovery, and CT branches fire concurrently.</p>
<p></p>
<p>Each branch has its own timeout budget; slow branches are cancelled without</p>
<p></p>
<p>affecting other branches.</p>
<p></p>
<p></p>
<p></p>
<p>F212-B: All branch timeouts are remaining-time-aware and capped at</p>
<p></p>
<p>min(config_timeout, remaining * 0.5, MAX_CAP). Branches are skipped with</p>
<p></p>
<p>terminal outcome when remaining time is below the safety floor.</p>
</div>
</details>
</li>
<li><code>run_enabled_acquisition_lanes</code> (__init__.py)
<details><summary>Run all enabled optional acquisition lanes (CT, WAYBACK, PASSIVE_DNS, BLOCKCHAIN)</summary>
<div class="doc-comment">
<p>Run all enabled optional acquisition lanes (CT, WAYBACK, PASSIVE_DNS, BLOCKCHAIN)</p>
<p>bounded by their per-lane plans from the acquisition strategy snapshot.</p>
<p></p>
<p>FEED and PUBLIC lanes are NOT run here — they are run by SprintScheduler</p>
<p>via its own pipeline calls.</p>
<p></p>
<p>STEALTH lane is NOT run here — caller must explicitly enable it.</p>
<p></p>
<p>Args:</p>
<p>snapshot:   AcquisitionStrategySnapshot from build_acquisition_plan().</p>
<p>query:      Sprint query string.</p>
<p>store:      DuckDBShadowStore for canonical storage (async_ingest_findings_batch).</p>
<p>uma_state:  Current UMA state ("ok" | "warn" | "critical" | "emergency").</p>
<p>seed_context: NonfeedSeedContext for domain/IP seeding.</p>
<p>graph_accumulator: SprintGraphAccumulator instance for graph wiring.</p>
<p>If None, graph accumulation is skipped (fail-soft, F265C).</p>
<p></p>
<p>Returns:</p>
<p>Tuple of AcquisitionLaneOutcome, one per optional lane.</p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p>- gather(return_exceptions=True) so one lane crash never fails others</p>
<p>- per-lane timeout enforced via asyncio.timeout</p>
<p>- per-lane max_items enforced by each lane adapter</p>
<p>- STEALTH never auto-enabled</p>
<p>- No MLX/model load</p>
</div>
</details>
</li>
<li><code>compute_sprint_intelligence</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint 8VN: Lazy fail-soft computation of correlation + hypothesis seams.</summary>
<div class="doc-comment">
<p>Sprint 8VN: Lazy fail-soft computation of correlation + hypothesis seams.</p>
<p></p>
<p></p>
<p></p>
<p>Returns a dict with:</p>
<p></p>
<p>- correlation: from correlate_findings() -- full second-order condensation</p>
<p></p>
<p>- hypothesis_pack: from build_hypothesis_pack() -- operator shortlist + actionability</p>
<p></p>
<p>- branch_value: feed vs public branch value comparison</p>
<p></p>
<p>- signal_path: dominant signal path, next pivot, corroboration health</p>
<p></p>
<p>- feed_verdict: aggregated feed economics verdict across cycles</p>
<p></p>
<p>- public_verdict: aggregated public branch verdict across cycles</p>
<p></p>
<p></p>
<p></p>
<p>All computation is bounded and M1 8GB safe:</p>
<p></p>
<p>- correlation: max 500 findings</p>
<p></p>
<p>- hypothesis: max 200 finding texts</p>
<p></p>
<p>- feed/public verdict accumulation: max 10 entries each</p>
<p></p>
<p>- no model dependency</p>
<p></p>
<p>- fail-soft throughout</p>
</div>
</details>
</li>
<li><code>_build_diagnostic_report</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Build a diagnostic report dict for exporters.</span></li>
<li><code>run_enabled_acquisition_lanes</code> (acquisition_strategy.py)
<details><summary>Run all enabled optional acquisition lanes (CT, WAYBACK, PASSIVE_DNS, BLOCKCHAIN)</summary>
<div class="doc-comment">
<p>Run all enabled optional acquisition lanes (CT, WAYBACK, PASSIVE_DNS, BLOCKCHAIN)</p>
<p>bounded by their per-lane plans from the acquisition strategy snapshot.</p>
<p></p>
<p>FEED and PUBLIC lanes are NOT run here — they are run by SprintScheduler</p>
<p>via its own pipeline calls.</p>
<p></p>
<p>STEALTH lane is NOT run here — caller must explicitly enable it.</p>
<p></p>
<p>Args:</p>
<p>snapshot:   AcquisitionStrategySnapshot from build_acquisition_plan().</p>
<p>query:      Sprint query string.</p>
<p>store:      DuckDBShadowStore for canonical storage (async_ingest_findings_batch).</p>
<p>uma_state:  Current UMA state ("ok" | "warn" | "critical" | "emergency").</p>
<p>seed_context: NonfeedSeedContext for domain/IP seeding.</p>
<p>graph_accumulator: SprintGraphAccumulator instance for graph wiring.</p>
<p>If None, graph accumulation is skipped (fail-soft, F265C).</p>
<p></p>
<p>Returns:</p>
<p>Tuple of AcquisitionLaneOutcome, one per optional lane.</p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p>- gather(return_exceptions=True) so one lane crash never fails others</p>
<p>- per-lane timeout enforced via asyncio.timeout</p>
<p>- per-lane max_items enforced by each lane adapter</p>
<p>- STEALTH never auto-enabled</p>
<p>- No MLX/model load</p>
</div>
</details>
</li>
<li><code>_run_one_cycle_stable</code> (sprint_scheduler_v1_archived.py)
<details><summary>Stable mode: feed sources run first, then public discovery runs after.</summary>
<div class="doc-comment">
<p>Stable mode: feed sources run first, then public discovery runs after.</p>
<p></p>
<p>CT discovery runs once after the main cycle loop (in __main__.py).</p>
<p></p>
<p></p>
<p></p>
<p>F212-B: Public discovery runs under remaining-time-aware asyncio.timeout.</p>
<p></p>
<p>Branch is skipped if remaining time is at or below the safety floor.</p>
<p></p>
<p></p>
<p></p>
<p># P1.5-fix 2026-06-07: initialize _seed_ctx at function-top so it</p>
<p># is defined for the ENTIRE body of _run_one_cycle_stable, including</p>
<p># the public-outcome assembly at line ~15535 ("seed_context_available"</p>
<p># telemetry). The previous try-block-scoped initialization (14443)</p>
<p># was insufficient because the public-outcome code is OUTSIDE the</p>
<p># try block. When the nonfeed prelude never assigns _seed_ctx</p>
<p># (e.g. no pivot seeds and no next_seeds_ioc), NameError was raised</p>
<p># after the public branch completed.</p>
</div>
</details>
</li>
<li><code>rdap_result_to_findings</code> (source_finding_bridge.py)</li>
<li><code>_maybe_dispatch_nonfeed_probe_lanes</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F207M-A: Bounded nonfeed pre-dispatch checkpoint.</summary>
<div class="doc-comment">
<p>Sprint F207M-A: Bounded nonfeed pre-dispatch checkpoint.</p>
<p></p>
<p></p>
<p></p>
<p>Fires before the first active cycle's aggressive branch fan-out can trigger</p>
<p></p>
<p>early windup, ensuring CT (and optionally WAYBACK/PASSIVE_DNS) are attempted</p>
<p></p>
<p>at least once for domain queries before the sprint winds down.</p>
<p></p>
<p></p>
<p></p>
<p>Invariants (strict):</p>
<p></p>
<p>- No stealth, no graph writes, no unbounded network</p>
<p></p>
<p>- max_items &lt;= 5, timeout_s &lt;= 15</p>
<p></p>
<p>- Fail-soft: errors/skips are telemetry only, never crash sprint</p>
<p></p>
<p>- CT only by default for domain queries</p>
<p></p>
<p>- WAYBACK/PASSIVE_DNS only when memory is ok/warn</p>
<p></p>
<p></p>
<p></p>
<p>Windup blocking:</p>
<p></p>
<p>If domain query + CT enabled but not yet attempted, set</p>
<p></p>
<p>windup_blocked_until_nonfeed_attempted = True so the windup gate</p>
<p></p>
<p>delays entry until pre-dispatch completes.</p>
</div>
</details>
</li>
<li><code>ct_results_to_findings</code> (source_finding_bridge.py)</li>
<li><code>network_recon_result_to_findings</code> (source_finding_bridge.py)</li>
<li><code>acq_payload_to_dict</code> (sprint_entrypoint.py)
<details><summary>[Issue #9] Schema-driven acquisition payload.</summary>
<div class="doc-comment">
<p>[Issue #9] Schema-driven acquisition payload.</p>
<p></p>
<p>Replaces ~659-line _scheduler_result_acquisition_payload() triple-nested</p>
<p>try/except chain with:</p>
<p>1. msgspec.convert(result, AcqReportPayload) — C-level validation,</p>
<p>~50× faster than 31 getattr calls + defensive defaults.</p>
<p>2. Single canonical try/except around build_acquisition_report().</p>
<p>3. Direct .attribute access on AcqReportPayload — zero getattr.</p>
<p></p>
<p>All defensive defaults are encoded in AcqReportPayload field definitions.</p>
<p></p>
<p>Args:</p>
<p>result: SprintSchedulerResult instance</p>
<p>scheduler: SprintScheduler instance</p>
<p>query: sprint query string</p>
<p>duration_s: actual sprint duration</p>
<p></p>
<p>Returns:</p>
<p>dict with all acquisition report fields (see AcqReportPayload docstring)</p>
</div>
</details>
</li>
<li><code>run</code> (acquisition.py)</li>
<li><code>_run_public_discovery_in_cycle</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>run_runtime_pivot_prelude</code> (nonfeed_seed_runtime.py)</li>
<li><code>_ensure_mandatory_nonfeed_before_return</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F207T-A: Ensure mandatory nonfeed lanes have terminal state before</summary>
<div class="doc-comment">
<p>Sprint F207T-A: Ensure mandatory nonfeed lanes have terminal state before</p>
<p></p>
<p>the scheduler can return a meaningful result for a domain query.</p>
<p></p>
<p></p>
<p></p>
<p>This is the return-path analog of the pre-windup barrier -- it prevents</p>
<p></p>
<p>the scheduler from returning ACTIVE-phase results when PUBLIC/CT have</p>
<p></p>
<p>not yet been attempted (even if the windup guard was never reached).</p>
<p></p>
<p></p>
<p></p>
<p>Rules:</p>
<p></p>
<p>- domain query + ok/warn memory: both PUBLIC and CT must have terminal state</p>
<p></p>
<p>- domain query + critical/emergency: may skip with explicit reason recorded</p>
<p></p>
<p>- non-domain: only PUBLIC required (CT skips with no_domain)</p>
<p></p>
<p>- Feed-only result: may return if domain query but PUBLIC+CT already terminal</p>
<p></p>
<p></p>
<p></p>
<p>Semantics:</p>
<p></p>
<p>- Returns True if the scheduler MAY return (all required lanes terminal)</p>
<p></p>
<p>- Returns False if return must be DELAYED (required lanes not terminal)</p>
<p></p>
<p>- On False: sets return_guard telemetry and continues loop if possible</p>
<p></p>
<p></p>
<p></p>
<p>Args:</p>
<p></p>
<p>query: Sprint query</p>
<p></p>
<p>duckdb_store: DuckDB store (may be None)</p>
<p></p>
<p>reason: Human-readable reason for the return check (e.g. "stop_requested",</p>
<p></p>
<p>"max_cycles", "stop_on_first_accepted", "post_sleep_windup")</p>
<p></p>
<p></p>
<p></p>
<p>Returns:</p>
<p></p>
<p>True if return is allowed, False if blocked</p>
</div>
</details>
</li>
<li><code>_initialize_sprint_run</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_compose_provider_readiness_preview</code> (shadow_pre_decision.py)</li>
<li><code>_run_ct_predispatch</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">CT pre-dispatch with max_items=5, timeout=15s.</span></li>
<li><code>consume_shadow_pre_decision</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint 8VM: Read-only shadow pre-decision consumer.</summary>
<div class="doc-comment">
<p>Sprint 8VM: Read-only shadow pre-decision consumer.</p>
<p></p>
<p></p>
<p></p>
<p>Collects shadow inputs from current scheduler state,</p>
<p></p>
<p>runs parity check and pre-decision composition,</p>
<p></p>
<p>and returns PreDecisionSummary.</p>
<p></p>
<p></p>
<p></p>
<p>Caching: stores result in _shadow_pd_summary to avoid recomputation.</p>
<p></p>
<p>Cache is cleared in _reset_result().</p>
<p></p>
<p></p>
<p></p>
<p>THIS IS DIAGNOSTIC ONLY -- all hard boundaries enforced:</p>
<p></p>
<p>- Does NOT execute any tools (no execute_with_limits calls)</p>
<p></p>
<p>- Does NOT activate any providers</p>
<p></p>
<p>- Does NOT write to any ledgers as runtime truth</p>
<p></p>
<p>- Does NOT modify scheduler mutable state</p>
<p></p>
<p>- Does NOT create new scheduler framework</p>
<p></p>
<p>- Does NOT dispatch or enqueue work</p>
<p></p>
<p>- Returns PreDecisionSummary artifact, NOT a truth store</p>
<p></p>
<p></p>
<p></p>
<p>Injection point: called from _build_diagnostic_report() at export time.</p>
<p></p>
<p>The method is also available for ad-hoc calls during sprint for</p>
<p></p>
<p>diagnostic purposes only.</p>
<p></p>
<p></p>
<p></p>
<p>Returns None if shadow mode is not active.</p>
</div>
</details>
</li>
<li><code>_main_dispatch</code> (sprint_entrypoint.py)</li>
<li><code>_finalize_result_truth</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F208I-B: Finalize SprintSchedulerResult before run() returns.</summary>
<div class="doc-comment">
<p>Sprint F208I-B: Finalize SprintSchedulerResult before run() returns.</p>
<p></p>
<p></p>
<p></p>
<p>Computes terminality from acquisition strategy and records scheduler exit</p>
<p></p>
<p>path. Called once before every return from run() -- both normal completion</p>
<p></p>
<p>and all early exit paths (stop_requested, abort, windup_barrier, etc.).</p>
<p></p>
<p></p>
<p></p>
<p>Invariants (GHOST_INVARIANTS):</p>
<p></p>
<p>- No network I/O</p>
<p></p>
<p>- No model/MLX load</p>
<p></p>
<p>- No browser launch</p>
<p></p>
<p>- No blocking ops</p>
<p></p>
<p>- Fail-safe: terminality errors don't prevent return</p>
</div>
</details>
</li>
<li><code>_run_ct_log_discovery_in_cycle</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F193A: Run CT log canonical discovery in the current cycle.</summary>
<div class="doc-comment">
<p>Sprint F193A: Run CT log canonical discovery in the current cycle.</p>
<p></p>
<p></p>
<p></p>
<p>Extracts domain from query, pivots via CTLogClient, converts results</p>
<p></p>
<p>to CanonicalFinding and ingests into DuckDB store.</p>
<p></p>
<p></p>
<p></p>
<p>Fail-soft: errors are accumulated but never raise or abort the sprint.</p>
</div>
</details>
</li>
<li><code>run_enabled_acquisition_lanes_streaming</code> (__init__.py)
<details><summary>P2-1: Streaming variant -- lanes run concurrently, yields per-lane as they complete.</summary>
<div class="doc-comment">
<p>P2-1: Streaming variant -- lanes run concurrently, yields per-lane as they complete.</p>
<p></p>
<p>Yields cumulative (outcome,) tuples so callers can accumulate incrementally.</p>
<p>Early-exit when min_finished lanes done (min_finished=0 means wait for all).</p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p>- safe_gather_ok(return_exceptions=True) preserves fail-soft</p>
<p>- per-lane asyncio.timeout enforced</p>
<p>- STEALTH never auto-enabled</p>
<p>- M1 8GB safe: Semaphore(clearnet_max), bounded [1, 4] for M1 8GB safety</p>
</div>
</details>
</li>
<li><code>aclose</code> (sprint_scheduler_v1_archived.py)
<details><summary>F285: Canonical async cleanup — call on SIGINT / windup / completion.</summary>
<div class="doc-comment">
<p>F285: Canonical async cleanup — call on SIGINT / windup / completion.</p>
<p></p>
<p>Args:</p>
<p>timeout_s: max seconds for each cleanup phase (default 10.0).</p>
<p>Individual phases (DuckDB writer, LMDB, Hermes, transports)</p>
<p>have their own bounded timeouts (5s / 5s / 5s).</p>
<p></p>
<p>Addresses M1 8GB resource pressure: Metal cache, LMDB envs, DuckDB</p>
<p>writer, Hermes engine, transport adapters, and metrics registry are all</p>
<p>explicitly released here rather than relying on GC.</p>
<p></p>
<p>Call sites (priority order):</p>
<p>1. core/__main__.py finally: await scheduler.aclose()</p>
<p>2. Soft-fail path: await scheduler.aclose()</p>
<p>3. Any caller that creates SprintScheduler and needs deterministic cleanup.</p>
<p></p>
<p>Ordering rationale:</p>
<p>- DuckDB writer FIRST (drains pending writes)</p>
<p>- LMDB envs SECOND (flushes write buffers)</p>
<p>- Hermes / Metal THIRD (releases GPU memory on M1)</p>
<p>- Transport adapters LAST (Tor, I2P, Nym, DHT, Gopher)</p>
<p>- Metrics registry FINAL (flushes telemetry)</p>
<p></p>
<p>Fail-safe: every step is wrapped in try/except so one failure never</p>
<p>prevents subsequent steps from running.</p>
</div>
</details>
</li>
<li><code>dry_run_sprint</code> (sprint_entrypoint.py)
<details><summary>Dry-run mode: validate config, check Hermes/UMA/sources, show timing plan.</summary>
<div class="doc-comment">
<p>Dry-run mode: validate config, check Hermes/UMA/sources, show timing plan.</p>
<p>Read-only — no DuckDB writes, no real discovery, no data downloads.</p>
<p></p>
<p>Invariant: --dry-run is read-only. Minimal side effects (writes DRY_RUN_REPORT.json only).</p>
</div>
</details>
</li>
<li><code>_run_ct_to_passivedns_active_pivot</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>run_advisory_runner</code> (sidecar_orchestrator.py)
<details><summary>F206D + ISSUE #3: Run all teardown advisory steps via SprintAdvisoryRunner.</summary>
<div class="doc-comment">
<p>F206D + ISSUE #3: Run all teardown advisory steps via SprintAdvisoryRunner.</p>
<p></p>
<p>ISSUE #3 FIX: All 4 branches now run in PARALLEL via outer TaskGroup:</p>
<p>- Branch A: SprintAdvisoryRunner (4 core advisories)</p>
<p>- Branch B: CT → PassiveDNS pivot advisory</p>
<p>- Branch C: BGP/Wayback/CommonCrawl sidecars (TaskGroup)</p>
<p>- Branch D: IPFS/Onion/I2P/banner/DHT/Gopher/stego/TI sidecars (TaskGroup)</p>
<p>- Branch E: Plugin sidecars (TaskGroup)</p>
<p></p>
<p>Each branch's inner _run_bounded_sidecar calls share ONE global semaphore</p>
<p>(_ADVISORY_SIDECAR_SEMAPHORE_LIMIT=8). This replaces the prior sequential</p>
<p>execution that ran Steps 1→2→(3-4)→(5-7)→(plugin) in wall-time.</p>
<p></p>
<p>Expected speedup: 5-7× faster teardown (30-90s → 5-15s at full flag-on load).</p>
<p></p>
<p>Canonical teardown entry point. Each step is fail-soft;</p>
<p>CancelledError propagates to caller.</p>
</div>
</details>
</li>
<li><code>_ingest_feed_public_candidates_to_ledger</code> (sprint_scheduler_v1_archived.py)
<details><summary>F214: Bridge feed and PUBLIC findings into nonfeed candidate ledger.</summary>
<div class="doc-comment">
<p>F214: Bridge feed and PUBLIC findings into nonfeed candidate ledger.</p>
<p></p>
<p></p>
<p></p>
<p>Extracts domain candidates from feed/public lane outcomes and records them</p>
<p></p>
<p>in the ledger for downstream nonfeed lane planning (DOH, CT, Wayback, passiveDNS).</p>
<p></p>
<p></p>
<p></p>
<p>Flow:</p>
<p></p>
<p>1. Extract domain candidates from _lane_outcomes (FEED/PUBLIC lanes)</p>
<p></p>
<p>2. Apply source_host filtering (deprioritize domains that appear only</p>
<p></p>
<p>in source URL hostname, not in content body)</p>
<p></p>
<p>3. Rank candidates by confidence and seen_count</p>
<p></p>
<p>4. Record via add_feed_candidate() for FEED family</p>
<p></p>
<p>5. Compute lane eligibility from candidates</p>
<p></p>
<p></p>
<p></p>
<p>Bounding:</p>
<p></p>
<p>- MAX_DOMAIN_CANDIDATES_FOR_LANES (10) max candidates processed</p>
<p></p>
<p>- MAX_FEED_CANDIDATES (10) per source URL</p>
<p></p>
<p>- fail-soft throughout -- ledger errors never crash sprint</p>
<p></p>
<p></p>
<p></p>
<p>Lane eligibility telemetry:</p>
<p></p>
<p>- Stored in result.nonfeed_lane_eligibility after computation</p>
</div>
</details>
</li>
<li><code>run_target_memory_update</code> (sidecar_orchestrator.py)</li>
<li><code>preview_dispatch_parity</code> (shadow_pre_decision.py)</li>
<li><code>_accumulate_lane_findings</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F207J-A: Accumulate accepted lane findings into scheduler truth.</summary>
<div class="doc-comment">
<p>Sprint F207J-A: Accumulate accepted lane findings into scheduler truth.</p>
<p></p>
<p>[F207K-A] Extended with bridge rejection tracking.</p>
<p></p>
<p></p>
<p></p>
<p>Populates:</p>
<p></p>
<p>- _result.lane_*_accepted_findings counters</p>
<p></p>
<p>- _lane_verdicts accumulator (for feed_verdict analog per lane)</p>
<p></p>
<p>- _all_findings (bounded at 500, same cap as feed findings)</p>
<p></p>
<p>- _lane_rejections (source_family, rejection_reason, rejected_count, samples)</p>
<p></p>
<p></p>
<p></p>
<p>Also updates source_family_outcomes in the diagnostic report.</p>
<p></p>
<p></p>
<p></p>
<p>Args:</p>
<p></p>
<p>outcomes: Tuple of AcquisitionLaneOutcome from run_enabled_acquisition_lanes.</p>
<p></p>
<p>query: Sprint query string (used for _all_findings entry).</p>
</div>
</details>
</li>
<li><code>_run_ct_to_passivedns_pivot_advisory</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint R5: CT accepted domains -&gt; PassiveDNS one-hop pivot.</summary>
<div class="doc-comment">
<p>Sprint R5: CT accepted domains -&gt; PassiveDNS one-hop pivot.</p>
<p></p>
<p></p>
<p></p>
<p>One-hop pivot from CT lane accepted findings to PassiveDNS lookup.</p>
<p></p>
<p>No recursive pivoting (pivot depth = 1).</p>
<p></p>
<p>No new queue framework.</p>
<p></p>
<p>No stealth/browser.</p>
<p></p>
<p></p>
<p></p>
<p>Flow:</p>
<p></p>
<p>1. Extract CT accepted domains from acquisition lane outcomes</p>
<p></p>
<p>2. Deduplicate (max 10 via dict.fromkeys)</p>
<p></p>
<p>3. Guard: skip if UMA critical/emergency</p>
<p></p>
<p>4. For each domain: call PassiveDNS (monkeypatched in tests)</p>
<p></p>
<p>5. Record FAMILY_PIVOT in NonfeedCandidateLedger</p>
<p></p>
<p>6. Record source_family_outcomes pivot_source=ct</p>
<p></p>
<p></p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p></p>
<p>- gather(return_exceptions=True)</p>
<p></p>
<p>- Manual CancelledError filter + error collection after gather</p>
<p></p>
<p>- CancelledError re-raised</p>
<p></p>
<p>- No MLX model load</p>
<p></p>
<p>- No asyncio.run() in async context</p>
<p></p>
<p>- Bounded: max 10 pivot domains</p>
<p></p>
<p>- Fail-soft: pivot error never crashes sprint</p>
</div>
</details>
</li>
<li><code>run_enabled_acquisition_lanes_streaming</code> (acquisition_strategy.py)
<details><summary>P2-1: Streaming variant -- lanes run concurrently, yields per-lane as they complete.</summary>
<div class="doc-comment">
<p>P2-1: Streaming variant -- lanes run concurrently, yields per-lane as they complete.</p>
<p></p>
<p>Yields cumulative (outcome,) tuples so callers can accumulate incrementally.</p>
<p>Early-exit when min_finished lanes done (min_finished=0 means wait for all).</p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p>- safe_gather_ok(return_exceptions=True) preserves fail-soft</p>
<p>- per-lane asyncio.timeout enforced</p>
<p>- STEALTH never auto-enabled</p>
<p>- M1 8GB safe: Semaphore(clearnet_max), bounded [1, 4] for M1 8GB safety</p>
</div>
</details>
</li>
<li><code>_build_shadow_readiness_preview</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint 8VM + 8VQ: Build a machine-readable shadow readiness preview dict.</summary>
<div class="doc-comment">
<p>Sprint 8VM + 8VQ: Build a machine-readable shadow readiness preview dict.</p>
<p></p>
<p></p>
<p></p>
<p>Called from _build_diagnostic_report() when shadow mode is active.</p>
<p></p>
<p>This is a READ-ONLY summary extracted from PreDecisionSummary</p>
<p></p>
<p>for diagnostic/logging purposes -- NOT a truth store.</p>
</div>
</details>
</li>
<li><code>doh_results_to_findings</code> (source_finding_bridge.py)</li>
<li><code>_run_i2p_discovery_sidecar</code> (sprint_scheduler_v1_archived.py)
<details><summary>I2P discovery: crawl .i2p addresses found in sprint IOCs.</summary>
<div class="doc-comment">
<p>I2P discovery: crawl .i2p addresses found in sprint IOCs.</p>
<p></p>
<p></p>
<p></p>
<p>Gate: HLEDAC_ENABLE_I2P=1 AND I2PTransport.is_running().</p>
<p></p>
<p>Memory pressure &lt; 0.70. Fail-soft throughout -- never crashes sprint.</p>
<p></p>
<p></p>
<p></p>
<p>Sidecar chain position: AFTER _run_onion_discovery_sidecar() if it</p>
<p></p>
<p>exists, otherwise after CT log discovery.</p>
<p></p>
<p></p>
<p></p>
<p>M1 8GB constraints:</p>
<p></p>
<p>- max 5 .i2p addresses per sprint</p>
<p></p>
<p>- 45s per fetch timeout</p>
<p></p>
<p>- 120s total sidecar budget</p>
</div>
</details>
</li>
<li><code>passive_dns_results_to_findings</code> (source_finding_bridge.py)</li>
<li><code>_run_bgp_advisory_sidecar</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F234: BGP IP-to-Org attribution advisory.</summary>
<div class="doc-comment">
<p>Sprint F234: BGP IP-to-Org attribution advisory.</p>
<p></p>
<p></p>
<p></p>
<p>Advisory-only sidecar -- runs after main sprint to enrich accepted</p>
<p></p>
<p>findings with BGP/ASN intelligence. Fail-soft throughout: errors</p>
<p></p>
<p>never crash the sprint.</p>
<p></p>
<p></p>
<p></p>
<p>Flow:</p>
<p></p>
<p>1. Extract domain/IP candidates from acquisition lane outcomes</p>
<p></p>
<p>2. Query BGPView.io for ASN, org, prefix data</p>
<p></p>
<p>3. Convert results to CanonicalFinding via BGPAdapter</p>
<p></p>
<p>4. Record as source_family="bgp_advisory" in source_family_outcomes</p>
</div>
</details>
</li>
<li><code>summarize_ct_conversion</code> (source_finding_bridge.py)</li>
<li><code>_collect_ct_terminal_outcome</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F208L-A: Collect canonical CT terminal outcome from all CT surfaces.</summary>
<div class="doc-comment">
<p>Sprint F208L-A: Collect canonical CT terminal outcome from all CT surfaces.</p>
<p></p>
<p></p>
<p></p>
<p>This is the ONE source of truth for CT terminality in _finalize_result_truth.</p>
<p></p>
<p>It inspects all canonical CT surfaces and returns a complete outcome dict</p>
<p></p>
<p>with lane, family, attempted, terminal_state, raw_count, accepted_count,</p>
<p></p>
<p>error, timeout, skipped fields.</p>
<p></p>
<p></p>
<p></p>
<p>Returns None when CT was never attempted (not even attempted=True with zero</p>
<p></p>
<p>raw results) -- allowing terminality_report to mark CT as missing.</p>
<p></p>
<p></p>
<p></p>
<p>Terminal state rules:</p>
<p></p>
<p>- error not None  -&gt; terminal_state="error"</p>
<p></p>
<p>- timeout=True    -&gt; terminal_state="timeout"</p>
<p></p>
<p>- skipped=True    -&gt; terminal_state="skipped"</p>
<p></p>
<p>- raw_count &gt; 0 and accepted_count == 0 -&gt; terminal_state="success_empty"</p>
<p></p>
<p>- raw_count == 0 and attempted=True and no error -&gt; terminal_state="empty"</p>
<p></p>
<p>- attempted=True (default terminal) -&gt; terminal_state="success"</p>
</div>
</details>
</li>
<li><code>_ensure_pre_windup_lane_terminal_states</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_run_nonfeed_prelude_gather</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_get_windup_scorecard</code> (sprint_scheduler_v1_archived.py)
<details><summary>F206E: Extract read-only windup scorecard fields from active pipeline data.</summary>
<div class="doc-comment">
<p>F206E: Extract read-only windup scorecard fields from active pipeline data.</p>
<p></p>
<p></p>
<p></p>
<p>Reads bounded diagnostic fields from windup_engine.py scorecard WITHOUT</p>
<p></p>
<p>activating the dormant run_windup() path. No model load, no GNN import.</p>
<p></p>
<p></p>
<p></p>
<p>Safe read-only sources:</p>
<p></p>
<p>- Circuit breaker states (transport.circuit_breaker)</p>
<p></p>
<p>- Phase durations (from result timing fields)</p>
<p></p>
<p>- Graph stats (from graph_service, already via _get_graph_signal)</p>
<p></p>
<p>- Peak RSS (from result.peak_rss_gib or psutil)</p>
<p></p>
<p></p>
<p></p>
<p>Fail-soft: returns empty dict on any error.</p>
<p></p>
<p></p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p></p>
<p>- No asyncio.run() or loop.run_until_complete()</p>
<p></p>
<p>- No model/MLX imports on hot path</p>
<p></p>
<p>- No GNN inference</p>
<p></p>
<p>- Bounded: MAX_WINDUP_SCORECARD_KEYS=32</p>
</div>
</details>
</li>
<li><code>wayback_results_to_findings</code> (source_finding_bridge.py)</li>
<li><code>academic_results_to_findings</code> (source_finding_bridge.py)</li>
<li><code>_reset_result</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_run_doh_prelude_lane</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>health_check</code> (sprint_scheduler_v1_archived.py)
<details><summary>F228F: Pre-run health check for critical dependencies.</summary>
<div class="doc-comment">
<p>F228F: Pre-run health check for critical dependencies.</p>
<p>F270-4.3: Cached -- returns same report within same active sprint cycle.</p>
<p>Always returns HealthReport -- NEVER raises.</p>
<p>Timeout handled externally by caller (asyncio.timeout in __main__).</p>
</div>
</details>
</li>
<li><code>build_acquisition_report</code> (acquisition_strategy.py)
<details><summary>[F208C] Build a stable canonical acquisition report dict.</summary>
<div class="doc-comment">
<p>[F208C] Build a stable canonical acquisition report dict.</p>
<p>[F219A] Canonical Surface Contract Seal — extends F208C with full F216/F217 telemetry.</p>
<p></p>
<p>This is the ONE canonical schema for acquisition telemetry. The benchmark</p>
<p>parser checks report["acquisition_report"] FIRST before falling back to</p>
<p>legacy sibling fields. This stops the parser whack-a-mole.</p>
<p></p>
<p>Output shape::</p>
<p></p>
<p>{</p>
<p>"schema_version": "f208.v1",</p>
<p>"plan": ...          # AcquisitionStrategySnapshot plans as dicts</p>
<p>"terminality": ...   # terminality report from terminality_report()</p>
<p>"nonfeed_plan_debug": ...  # NonfeedPlanDebug as dict</p>
<p>"source_family_outcomes": ...  # list of SourceFamilyOutcome.to_dict()</p>
<p>"return_guard": ...  # return guard observation dict</p>
<p>"prewindup_barrier": ...  # prewindup barrier dict</p>
<p>"scheduler_exit": ...  # scheduler exit telemetry dict</p>
<p>"windup_guard_observation": ...  # windup guard observation dict</p>
<p># F216B: Nonfeed diagnostic profile telemetry</p>
<p>"acquisition_profile": "default",</p>
<p>"feed_cap_reason": None,</p>
<p>"nonfeed_priority_enabled": False,</p>
<p>"nonfeed_profile_expected_lanes": [],</p>
<p># F217C: PUBLIC bootstrap telemetry</p>
<p>"public_terminal_stage": "",</p>
<p>"public_stage_counters": {},</p>
<p># F217D: CT provider resilience telemetry</p>
<p>"ct_provider_status": "",</p>
<p>"ct_cache_used": False,</p>
<p>"ct_cache_stale": False,</p>
<p>"ct_cache_age_s": 0.0,</p>
<p>"ct_quarantine_count": 0,</p>
<p>"ct_quarantine_samples": [],</p>
<p># F216G: Quality rejection ledger</p>
<p>"quality_rejection_summary_by_family": {},</p>
<p># F216G: Duplicate rejection ledger</p>
<p>"duplicate_rejection_summary_by_family": {},</p>
<p># F216G: Low information rejection</p>
<p>"low_information_by_family": {},</p>
<p># F217E: Nonfeed candidate ledger summary</p>
<p>"nonfeed_candidate_ledger_summary": {},</p>
<p># F216E: Feed dominance budget telemetry</p>
<p>"feed_dominance_budget": {},</p>
<p>}</p>
<p></p>
<p>Args:</p>
<p>query:                      F214: Sprint query string (used for lane eligibility matrix).</p>
<p>plan:                          AcquisitionStrategySnapshot from build_acquisition_plan().</p>
<p>terminality:                    Result of terminality_report().</p>
<p>nonfeed_plan_debug:             NonfeedPlanDebug snapshot.</p>
<p>source_family_outcomes:         List of SourceFamilyOutcome.to_dict() dicts.</p>
<p>return_guard:                  Return guard observation dict.</p>
<p>prewindup_barrier:             Pre-windup barrier dict.</p>
<p>scheduler_exit:                Scheduler exit telemetry dict.</p>
<p>windup_guard_observation:      Windup guard observation dict.</p>
<p>acquisition_profile:            F216B: Nonfeed diagnostic profile name.</p>
<p>feed_cap_reason:                F216B: Reason FEED was capped (if any).</p>
<p>nonfeed_priority_enabled:       F216B: Whether nonfeed priority was active.</p>
<p>nonfeed_profile_expected_lanes: F216B: Expected nonfeed lanes for profile.</p>
<p>public_terminal_stage:          F217C: PUBLIC bootstrap terminal stage.</p>
<p>public_stage_counters:          F217C: PUBLIC stage counters dict.</p>
<p>ct_provider_status:             F217D: CT provider status string.</p>
<p>ct_cache_used:                 F217D: Whether CT cache was used.</p>
<p>ct_cache_stale:                F217D: Whether CT cache was stale.</p>
<p>ct_cache_age_s:                F217D: CT cache age in seconds.</p>
<p>ct_quarantine_count:           F217D: CT quarantine entry count.</p>
<p>ct_quarantine_samples:         F217D: CT quarantine sample strings.</p>
<p>quality_rejection_summary_by_family: F216G: Quality rejection counts by family.</p>
<p>duplicate_rejection_summary_by_family: F216G: Duplicate rejection counts.</p>
<p>low_information_by_family:     F216G: Low-information rejection counts.</p>
<p>nonfeed_candidate_ledger_summary: F217E: Nonfeed candidate ledger summary.</p>
<p>feed_dominance_budget:         F216E: Feed dominance budget telemetry.</p>
<p># F228C: Nonfeed surface completeness telemetry</p>
<p>nonfeed_expected_lanes:         F228C: Expected nonfeed lanes from profile.</p>
<p>nonfeed_missing_expected_lanes: F228C: Expected lanes not surfaced.</p>
<p>wayback_terminal_state:         F228C: WAYBACK family terminal state.</p>
<p>passive_dns_terminal_state:     F228C: PASSIVE_DNS family terminal state.</p>
<p>nonfeed_surface_complete:       F228C: True when all expected lanes surfaced.</p>
<p></p>
<p>Returns:</p>
<p>Canonical acquisition report dict with schema_version="f208.v1".</p>
</div>
</details>
</li>
<li><code>_run_onion_discovery_sidecar</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F251: Dark web .onion discovery via Tor.</summary>
<div class="doc-comment">
<p>Sprint F251: Dark web .onion discovery via Tor.</p>
<p></p>
<p></p>
<p></p>
<p>Gate: HLEDAC_ENABLE_TOR=1 AND TorTransport circuit established AND</p>
<p></p>
<p>memory_pressure &lt; 0.70. Fail-soft throughout -- never crashes sprint.</p>
<p></p>
<p></p>
<p></p>
<p>Sidecar chain position: AFTER _run_ct_log_discovery_in_cycle() (CT logs</p>
<p></p>
<p>reveal .onion domains from certificate transparency).</p>
<p></p>
<p></p>
<p></p>
<p>M1 8GB constraints:</p>
<p></p>
<p>- Semaphore(3): max 3 concurrent Tor crawls</p>
<p></p>
<p>- 45s per crawl timeout</p>
<p></p>
<p>- 120s total sidecar budget</p>
<p></p>
<p>- 20 seeds max per sprint</p>
</div>
</details>
</li>
<li><code>_run_epistemic_gap_advisory</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F260: Run EpistemicGapProgram and ContradictionResolverProgram.</summary>
<div class="doc-comment">
<p>Sprint F260: Run EpistemicGapProgram and ContradictionResolverProgram.</p>
<p></p>
<p>Wire point: called after _run_synthesis_sidecar in WINDUP phase.</p>
<p></p>
<p>Gates:</p>
<p>- HLEDAC_ENABLE_LLM=1 (same as synthesis)</p>
<p>- RAM &lt; 5.0GB (tighter than synthesis's 5.5GB)</p>
<p></p>
<p>Part A: EpistemicGapProgram</p>
<p>- Inputs: findings from sprint + known gaps from ResearchSessionMemory</p>
<p>- Output: gaps written to ResearchSessionMemory via record_sprint_outcome()</p>
<p></p>
<p>Part B: ContradictionResolverProgram</p>
<p>- Triggered when DS conflict_mass &gt; 0.3</p>
<p>- Max 5 contradictions per call (M1 constraint)</p>
</div>
</details>
</li>
<li><code>to_dict</code> (shadow_pre_decision.py)</li>
<li><code>_compute_public_stage</code> (sprint_scheduler_v1_archived.py)
<details><summary>Compute public_terminal_stage and public_stage_counters from _public_outcome.</summary>
<div class="doc-comment">
<p>Compute public_terminal_stage and public_stage_counters from _public_outcome.</p>
<p></p>
<p></p>
<p></p>
<p>The stage machine traces the full discovery-&gt;fetch-&gt;parse-&gt;quality-&gt;storage</p>
<p></p>
<p>pipeline to explain why PUBLIC=0.</p>
<p></p>
<p></p>
<p></p>
<p>Returns (terminal_stage: str, stage_counters: dict).</p>
</div>
</details>
</li>
<li><code>_run_target_memory_update</code> (sprint_scheduler_v1_archived.py)
<details><summary>F204D: Update cross-sprint target memory after findings are accepted.</summary>
<div class="doc-comment">
<p>F204D: Update cross-sprint target memory after findings are accepted.</p>
<p></p>
<p></p>
<p></p>
<p>Sidecar runs after findings are accepted and sidecar bus completes.</p>
<p></p>
<p>Extracts entity/exposure/pivot facets from findings and merges into</p>
<p></p>
<p>target memory via duckdb_store.</p>
<p></p>
<p></p>
<p></p>
<p>RAM guard: skip if RSS &gt; high_water (85% threshold).</p>
<p></p>
<p>Fail-soft: errors never crash the sprint.</p>
<p></p>
<p></p>
<p></p>
<p>Args:</p>
<p></p>
<p>findings: List of CanonicalFinding that were accepted and stored</p>
<p></p>
<p>store: DuckDBShadowStore instance for async_upsert_target_memory</p>
<p></p>
<p>query: Original sprint query (used as target context)</p>
</div>
</details>
</li>
<li><code>_run_dht_sidecar</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F214Q: DHT torrent discovery via BitTorrent DHT network.</summary>
<div class="doc-comment">
<p>Sprint F214Q: DHT torrent discovery via BitTorrent DHT network.</p>
<p></p>
<p></p>
<p></p>
<p>INVARIANT: DHT queries NEVER go over Tor -- clearnet UDP only.</p>
<p></p>
<p>Gate: HLEDAC_ENABLE_DHT=1, max_results=5, timeout=60s.</p>
<p></p>
<p>Fail-soft: DHT errors logged, never crash sprint.</p>
</div>
</details>
</li>
<li><code>_compute_early_exit_class</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F215D: Compute canonical early exit classification.</summary>
<div class="doc-comment">
<p>Sprint F215D: Compute canonical early exit classification.</p>
<p></p>
<p></p>
<p></p>
<p>Called in _finalize_result_truth after timing fields are populated.</p>
<p></p>
<p>Returns (early_exit_class, early_exit_reason).</p>
<p></p>
<p></p>
<p></p>
<p>Invariants (GHOST_INVARIANTS):</p>
<p></p>
<p>- No network I/O, no model load, no browser launch</p>
<p></p>
<p>- Fail-safe: returns (COMPLETED_FULL_DURATION, "") on any error</p>
</div>
</details>
</li>
<li><code>_run_dark_surface_pivot_advisory</code> (sprint_scheduler_v1_archived.py)
<details><summary>F214K: Generate and enqueue dark surface pivot queries (onion/IPFS/DHT/I2P)</summary>
<div class="doc-comment">
<p>F214K: Generate and enqueue dark surface pivot queries (onion/IPFS/DHT/I2P)</p>
<p></p>
<p>post-sprint if accepted_findings &gt;= 5 and HLEDAC_ENABLE_DARK_PIVOTS=1.</p>
<p></p>
<p></p>
<p></p>
<p>GHOST_INVARIANTS: fail-soft, named except, transport availability verified</p>
<p></p>
<p>before any query is enqueued.</p>
</div>
</details>
</li>
<li><code>_gate_then_ingest_and_accumulate</code> (sprint_scheduler_v1_archived.py)
<details><summary>F266: PII-gated canonical write + graph accumulation.</summary>
<div class="doc-comment">
<p>F266: PII-gated canonical write + graph accumulation.</p>
<p></p>
<p>Combines _gate_then_ingest (DuckDB write) with _accumulate_findings_to_graph</p>
<p>(graph upsert) in a single await chain. Fail-soft: graph errors never</p>
<p>prevent the DuckDB write from completing.</p>
<p></p>
<p></p>
<p>This is the canonical call for ALL nonfeed lanes (wayback/pdns/doh)</p>
<p>and sidecars that need graph wiring.</p>
<p></p>
<p></p>
<p>P0-5: Evidence log events for every finding state transition:</p>
<p>- CREATED: when findings list is received</p>
<p>- CANDIDATE: before DuckDB ingest</p>
<p>- ACCEPTED: ingest result shows accepted findings</p>
<p>- REJECTED: ingest result shows rejected findings</p>
<p></p>
<p>Args:</p>
<p>store: duckdb_store (or any object with async_ingest_findings_batch).</p>
<p>findings: list of CanonicalFinding.</p>
<p>sprint_id: Sprint identifier for graph source field.</p>
<p></p>
<p>Returns:</p>
<p>Whatever async_ingest_findings_batch returns.</p>
</div>
</details>
</li>
<li><code>_run_enhanced_research</code> (sprint_scheduler_v1_archived.py)
<details><summary>F11: Run enhanced/deep research advisory post-sprint.</summary>
<div class="doc-comment">
<p>F11: Run enhanced/deep research advisory post-sprint.</p>
<p></p>
<p></p>
<p></p>
<p>GHOST_INVARIANTS: fail-soft, named except, CancelledError propagated.</p>
</div>
</details>
</li>
<li><code>_run_feed_dominance_nonfeed_rescue_window</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F220D: Feed Dominance Nonfeed Rescue Window.</summary>
<div class="doc-comment">
<p>Sprint F220D: Feed Dominance Nonfeed Rescue Window.</p>
<p></p>
<p></p>
<p></p>
<p>When feed has been dominant (&gt;=1000 accepted) and nonfeed lanes are all</p>
<p></p>
<p>at zero, this rescue window attempts a final bounded nonfeed rescue before</p>
<p></p>
<p>declaring feed-only early exit.</p>
<p></p>
<p></p>
<p></p>
<p>Bounded:</p>
<p></p>
<p>- Max 60s wall-clock duration</p>
<p></p>
<p>- Fail-soft: returns None on any error, 0.0 if no candidates found</p>
<p></p>
<p>- No new network providers -- uses existing seams (_attempt_public_prewindup_barrier)</p>
<p></p>
<p>- No MLX / browser / stealth</p>
<p></p>
<p></p>
<p></p>
<p>Returns:</p>
<p></p>
<p>Elapsed seconds if rescue ran (even with 0 findings), None if skipped.</p>
</div>
</details>
</li>
<li><code>_run_public_branch</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Public discovery branch with remaining-time-aware asyncio.timeout.</span></li>
<li><code>run_all_sidecars</code> (sidecar_bus.py)
<details><summary>Fan out to all registered sidecar runners for the given batch, in stage order.</summary>
<div class="doc-comment">
<p>Fan out to all registered sidecar runners for the given batch, in stage order.</p>
<p></p>
<p>Stages run sequentially (stage 1 → stage 2 → stage 3). Within each stage,</p>
<p>runners execute concurrently via asyncio.gather(return_exceptions=True).</p>
<p></p>
<p>Returns list of SidecarRunResult (one per runner that was attempted).</p>
<p></p>
<p>Bounds:</p>
<p>- findings capped at MAX_SIDECAR_FINDINGS</p>
<p>- results capped at MAX_SIDECAR_RESULT_RECORDS</p>
<p>- per-runner timeout: SIDECAR_TIMEOUT_S</p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p>- gather(return_exceptions=True) within each stage</p>
<p>- _check_gathered() after each stage's gather</p>
<p>- asyncio.CancelledError re-raised</p>
<p>- fail-soft: stage N failure does not stop stage N+1</p>
</div>
</details>
</li>
<li><code>_ingest_ct_lane_candidates</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint R1B: Ingest CT lane CanonicalFinding candidates via DuckDBShadowStore.</summary>
<div class="doc-comment">
<p>Sprint R1B: Ingest CT lane CanonicalFinding candidates via DuckDBShadowStore.</p>
<p></p>
<p></p>
<p></p>
<p>Bridges the gap between the acquisition lane's ct_results_to_findings() output</p>
<p></p>
<p>(which produces CanonicalFinding dicts in candidate_findings) and the canonical</p>
<p></p>
<p>storage path (async_ingest_findings_batch).</p>
<p></p>
<p></p>
<p></p>
<p>Flow per CT outcome with candidates:</p>
<p></p>
<p>1. Extract candidate_findings from CT AcquisitionLaneOutcome</p>
<p></p>
<p>2. Call duckdb_store.async_ingest_findings_batch(candidates)</p>
<p></p>
<p>3. Record storage results in NonfeedCandidateLedger (stored / quarantine / provider_failed)</p>
<p></p>
<p>4. Update _result.lane_ct_accepted_findings with accepted count</p>
<p></p>
<p></p>
<p></p>
<p>Fail-soft: storage errors never crash the sprint.</p>
<p></p>
<p>CancelledError: re-raised to caller (GHOST_INVARIANTS I6).</p>
<p></p>
<p>M1/UMA: no MLX model load in this path.</p>
<p></p>
<p></p>
<p></p>
<p>Args:</p>
<p></p>
<p>outcomes:   Tuple of AcquisitionLaneOutcome from run_enabled_acquisition_lanes.</p>
<p></p>
<p>duckdb_store: DuckDBShadowStore instance for canonical storage.</p>
</div>
</details>
</li>
<li><code>_build_sfo_list</code> (sprint_entrypoint.py)
<details><summary>Build source_family_outcomes list from AcqReportPayload.</summary>
<div class="doc-comment">
<p>Build source_family_outcomes list from AcqReportPayload.</p>
<p>Direct attribute access — zero getattr, zero defensive defaults.</p>
</div>
</details>
</li>
<li><code>_maybe_dispatch_nonfeed_probe_lanes</code> (acquisition.py)</li>
<li><code>_run_synthesis_sidecar</code> (acquisition.py)</li>
<li><code>_branch_timeout_s</code> (sprint_scheduler_v1_archived.py)
<details><summary>F212-B: Compute remaining-time-aware timeout for a named branch.</summary>
<div class="doc-comment">
<p>F212-B: Compute remaining-time-aware timeout for a named branch.</p>
<p></p>
<p></p>
<p></p>
<p>Formula: min(config_timeout, remaining_s * 0.5, MAX_BRANCH_TIMEOUT_CAP)</p>
<p></p>
<p></p>
<p></p>
<p>- Prevents a branch from consuming more than 50% of remaining cycle time</p>
<p></p>
<p>- Capped at MAX_BRANCH_TIMEOUT_CAP to bound absolute worst case</p>
<p></p>
<p>- Returns 0 when remaining_s &lt;= MIN_BRANCH_REMAINING_S (safety floor)</p>
<p></p>
<p>F273B: Floor is remaining-time-aware via self._min_branch_remaining_s(remaining_s).</p>
</div>
</details>
</li>
<li><code>_load_hermes_for_sprint</code> (sprint_scheduler_v1_archived.py)
<details><summary>P12: Load Hermes engine at sprint start via ModelManager.</summary>
<div class="doc-comment">
<p>P12: Load Hermes engine at sprint start via ModelManager.</p>
<p>Bounded lifecycle: loaded at BOOT/WARMUP, released at TEARDOWN.</p>
<p>Fail-soft: memory pressure on load skips ToT, does not abort sprint.</p>
<p></p>
<p>M1 8GB invariant: ModelManager enforces bounded admission and RSS guards.</p>
<p></p>
<p>F267: MLX prewarm -- if prewarm active and inter-sprint gap &lt; 60s,</p>
<p>model is still in Metal cache. Skip reload and verify.</p>
<p></p>
<p>ISSUE-121: Serial model loading replaced with parallel prewarm via</p>
<p>asyncio.to_thread() + asyncio.TaskGroup. Hermes load (~5-10s I/O-bound)</p>
<p>now runs in background thread while ModernBERT + URL prefetch also run</p>
<p>in parallel. Expected 4-7s → 1-2s (3-5× speedup).</p>
</div>
</details>
</li>
<li><code>_run_ct_prelude_lane</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Run CT prelude lane. Returns (AcquisitionLaneOutcome, ct_result, ct_telemetry).</span></li>
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
<li><code>_run_advisory_branch</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Advisory lanes branch — runs concurrently with FEED and PUBLIC.</span></li>
<li><code>_run_synthesis_sidecar</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Sprint F259: Run SynthesisRunner in WINDUP phase.</span></li>
<li><code>_run_feed_branch</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Feed branch: fetches all sources concurrently.</span></li>
<li><code>_run_ooda_cycle</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Jeden OODA cyklus -- 60s interval.</span></li>
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
<li><code>_run_feed_branch</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Feed branch: fetches all sources concurrently.</span></li>
<li><code>run_plugin_sidecars</code> (sidecar_orchestrator.py)
<details><summary>F350M-FED: Iterate over SidecarRegistry.get_available() and dispatch</summary>
<div class="doc-comment">
<p>F350M-FED: Iterate over SidecarRegistry.get_available() and dispatch</p>
<p>each registered plugin sidecar in a non-blocking asyncio task.</p>
<p></p>
<p>Args:</p>
<p>ctx: A SidecarContext (or duck-typed equivalent) with</p>
<p>.query, .sprint_id, .findings, .sprint_mode, .memory_pressure.</p>
<p></p>
<p>Behavior:</p>
<p>- Reads the canonical M1 budget from the governor if available</p>
<p>(defaults to 100MB).</p>
<p>- Iterates in priority order (highest first).</p>
<p>- Each sidecar runs in its own task with the supplied ctx.</p>
<p>- Fail-soft: any exception is caught and logged, never raised.</p>
</div>
</details>
</li>
<li><code>_run_steganography_sidecar</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F3FORENSICS: Steganography detection on image findings.</summary>
<div class="doc-comment">
<p>Sprint F3FORENSICS: Steganography detection on image findings.</p>
<p>Gate: HLEDAC_ENABLE_STEGANOGRAPHY=1, max_images=10, max_image_size=50MB.</p>
<p>Only emit findings if overall_suspicious &gt; 0.3.</p>
<p>Fail-soft: errors logged, never crash sprint.</p>
</div>
</details>
</li>
<li><code>run_async</code> (sidecar_protocol_adapters.py) — <span class="doc-comment-inline">Fetch threat intel IoCs matching the query.</span></li>
<li><code>build_execution_context_readiness</code> (shadow_pre_decision.py)</li>
<li><code>compose_pre_decision</code> (shadow_pre_decision.py)</li>
<li><code>generate_pivot_candidates_from_query</code> (pivot_planner.py)
<details><summary>[F216F] Generate bounded pivot candidates from a query string.</summary>
<div class="doc-comment">
<p>[F216F] Generate bounded pivot candidates from a query string.</p>
<p></p>
<p>This is the FIRST-CLASS pivot executor entry point: given only a query</p>
<p>(no findings needed), generate diagnostic pivot candidates that can be</p>
<p>used even when no lane accepts the query.</p>
<p></p>
<p>F225D: Added mission_intent parameter for mission-aware scoring.</p>
<p>When provided, applies mission_boost and score_reason to each pivot.</p>
<p></p>
<p>Generation rules (NO network, NO brute-force):</p>
<p>- domain: root domain, www prefix variant, archive pivot</p>
<p>- IP: reverse DNS domain pivot, graph pivot</p>
<p>- URL: extract domain and generate domain/archive pivots</p>
<p>- Hash: graph pivot</p>
<p>- Email: leak pivot, identity pivot</p>
<p>- unknown: no pivots generated</p>
<p></p>
<p>Args:</p>
<p>query: The input query string</p>
<p>max_candidates: Maximum number of candidates (default MAX_PIVOT_CANDIDATES=25)</p>
<p>mission_intent: Optional mission intent string (e.g. "domain_recon", "wallet_recon")</p>
<p>for mission-aware scoring. None = no boost.</p>
<p></p>
<p>Returns:</p>
<p>List of Pivot objects, sorted by priority (highest first).</p>
<p>Empty list if query type is not pivotable or is None.</p>
</div>
</details>
</li>
<li><code>_run_wayback_cdx_deep_sidecar</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F234: WaybackCDX deep search advisory.</summary>
<div class="doc-comment">
<p>Sprint F234: WaybackCDX deep search advisory.</p>
<p></p>
<p></p>
<p></p>
<p>Advisory-only sidecar -- runs after main sprint to discover archived</p>
<p></p>
<p>URLs for accepted domains. Fail-soft throughout.</p>
<p></p>
<p></p>
<p></p>
<p>Flow:</p>
<p></p>
<p>1. Extract domains from acquisition lane outcomes</p>
<p></p>
<p>2. Query Wayback CDX for archived URLs (deep domain discovery)</p>
<p></p>
<p>3. Convert results to CanonicalFinding via WaybackCDXDeepSearch</p>
<p></p>
<p>4. Record as source_family="wayback_cdx_advisory" in source_family_outcomes</p>
</div>
</details>
</li>
<li><code>__getattr__</code> (sprint_scheduler_v1_archived.py)
<details><summary>Delegate attribute access to _lc with lazy normalization.</summary>
<div class="doc-comment">
<p>Delegate attribute access to _lc with lazy normalization.</p>
<p></p>
<p>Raises AttributeError if _lc lacks the resolved attribute.</p>
</div>
</details>
</li>
<li><code>_feed_dominance_should_fetch</code> (sprint_scheduler_v1_archived.py)
<details><summary>F216E+F227D: Determine if a feed source should be fetched given current budget state.</summary>
<div class="doc-comment">
<p>F216E+F227D: Determine if a feed source should be fetched given current budget state.</p>
<p></p>
<p></p>
<p></p>
<p>F227D: Added mission_intent and nonfeed_unresolved to support mission-aware cap.</p>
<p></p>
<p>F230D: Added acquisition_profile for nonfeed_diagnostic profile cap.</p>
<p></p>
<p></p>
<p></p>
<p>Returns (should_fetch, reason):</p>
<p></p>
<p>- (True, "")       -- source should run normally</p>
<p></p>
<p>- (False, reason)  -- source should be skipped due to budget cap</p>
</div>
</details>
</li>
<li><code>_run_one_cycle_aggressive</code> (acquisition.py)</li>
<li><code>_run_federated_research_advisory</code> (sprint_advisory_runner.py)
<details><summary>F350M-FED-P3-FOLLOWUP: Federated research advisory at teardown.</summary>
<div class="doc-comment">
<p>F350M-FED-P3-FOLLOWUP: Federated research advisory at teardown.</p>
<p></p>
<p>The canonical seam for the federated research capability at sprint</p>
<p>teardown. Performs four bounded, fail-soft actions:</p>
<p></p>
<p>1. **Lazy bridge creation** — `scheduler._ensure_federated_bridge()`</p>
<p>returns a long-lived `FederatedBridge` (singleton on scheduler).</p>
<p>Off by default (gated on HLEDAC_ENABLE_FEDERATED=1).</p>
<p>2. **M1 safety** — skip entirely if memory_pressure &gt; 0.85.</p>
<p>3. **Bridge updates** — for each accepted finding in</p>
<p>`scheduler._all_findings`, emit `bridge.update(lane, state, action, reward, next_state)`.</p>
<p>Reward = clamp01(confidence). State = (lane, len(findings)).</p>
<p>Bounded by len(findings) — typically &lt; 100.</p>
<p>4. **LMDB persistence** — call `bridge.persist_if_due()` (debounced,</p>
<p>`asyncio.to_thread`, fail-soft). Honors env-var</p>
<p>`HLEDAC_FEDERATED_LMDB_PATH` for cross-sprint state.</p>
<p></p>
<p>Complements (does NOT replace) the Phase 2 plugin sidecar:</p>
<p>- Plugin sidecar: fire-and-forget, runs FederatedResearchCoordinator,</p>
<p>produces CanonicalFinding objects → SidecarDispatcher.</p>
<p>- This advisory: bounded bridge updates + LMDB persistence +</p>
<p>telemetry → analytics/export.</p>
<p></p>
<p>Side effects (all fail-soft):</p>
<p>- Sets `scheduler._federated_bridge` to the long-lived instance.</p>
<p>- Updates `SprintSchedulerResult.federated_*` telemetry fields</p>
<p>(populated by `sprint_scheduler._apply_federated_outcome`).</p>
<p></p>
<p>CancelledError: re-raised to caller.</p>
<p>All other exceptions: caught, logged at debug, outcome returned.</p>
</div>
</details>
</li>
<li><code>_run_lane</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_run_ipfs_discovery_sidecar</code> (sprint_scheduler_v1_archived.py)
<details><summary>F218Z: IPFS CID resolution and content fetch via Tor transport.</summary>
<div class="doc-comment">
<p>F218Z: IPFS CID resolution and content fetch via Tor transport.</p>
<p></p>
<p></p>
<p></p>
<p>Gate: HLEDAC_ENABLE_IPFS=1</p>
<p></p>
<p>Transport: Tor required (self._tor_transport), NEVER clearnet</p>
<p></p>
<p>Bounds: max 20 CIDs, 120s timeout per CID, 10MB max file size</p>
<p></p>
<p>Fail-soft: returns empty list on any error.</p>
<p></p>
<p></p>
<p></p>
<p>Args:</p>
<p></p>
<p>cids: List of IPFS CIDs to fetch. If None, extracts from</p>
<p></p>
<p>pivot findings or DHT results in the current sprint.</p>
<p></p>
<p>query_context: Query string for ipfs_search_as_findings fallback.</p>
</div>
</details>
</li>
<li><code>_run_one_cycle_stable</code> (acquisition.py)</li>
<li><code>_run_graph_rag_context_sidecar</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F224: Graph RAG pre-cycle enrichment.</summary>
<div class="doc-comment">
<p>Sprint F224: Graph RAG pre-cycle enrichment.</p>
<p></p>
<p>Runs BEFORE first cycle to inject previously discovered graph context</p>
<p>into the sprint. Uses multi-hop search over DuckPGQGraph to find</p>
<p>relevant entities/relationships from previous sprints.</p>
<p></p>
<p>Gate: HLEDAC_ENABLE_GRAPH_RAG=1 + RAM check &lt; 5.0GB</p>
<p></p>
<p>Args:</p>
<p>query: Current sprint query</p>
<p>duckdb_store: DuckDB store for persistent state</p>
<p></p>
<p>Returns:</p>
<p>List of CanonicalFinding with "context_seed" source_type</p>
</div>
</details>
</li>
<li><code>_build_plan_impl</code> (acquisition_strategy.py) — <span class="doc-comment-inline">Internal implementation — raises on error (caller catches).</span></li>
<li><code>_build_plan_impl</code> (__init__.py) — <span class="doc-comment-inline">Internal implementation — raises on error (caller catches).</span></li>
<li><code>run_async</code> (sidecar_protocol_adapters.py) — <span class="doc-comment-inline">Perform WHOIS lookups for domain findings.</span></li>
<li><code>_final_source_family_outcomes_for_terminality</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F210A: Canonical source family outcomes for terminality SSOT.</summary>
<div class="doc-comment">
<p>Sprint F210A: Canonical source family outcomes for terminality SSOT.</p>
<p></p>
<p></p>
<p></p>
<p>This mirrors the EXACT same logic used in _build_diagnostic_report to build</p>
<p></p>
<p>source_family_outcomes (lines ~6219-6244), ensuring terminality_report is</p>
<p></p>
<p>ALWAYS computed from the same canonical outcomes that go into the report.</p>
<p></p>
<p></p>
<p></p>
<p>This fixes the stale terminality bug where:</p>
<p></p>
<p>- _finalize_result_truth() is called before all nonfeed lanes complete</p>
<p></p>
<p>- terminality was computed from a snapshot with CT/PUBLIC not yet attempted</p>
<p></p>
<p>- source_family_outcomes reflected final state but terminality was stale</p>
<p></p>
<p></p>
<p></p>
<p>Returns:</p>
<p></p>
<p>Tuple of outcome dicts for terminality computation -- same format as</p>
<p></p>
<p>observed_outcomes passed to terminality_report().</p>
</div>
</details>
</li>
<li><code>_run_digital_ghost_sidecar</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F3FORENSICS: Digital ghost detection on file findings.</summary>
<div class="doc-comment">
<p>Sprint F3FORENSICS: Digital ghost detection on file findings.</p>
<p>Gate: HLEDAC_ENABLE_DIGITAL_GHOST=1, max_files=10, max_file_size=50MB.</p>
<p>Fail-soft: errors logged, never crash sprint.</p>
</div>
</details>
</li>
<li><code>_runtime_truth</code> (sprint_entrypoint.py)</li>
<li><code>_get_pivot_graph_stats_for_planning</code> (sprint_scheduler_v1_archived.py)
<details><summary>F238D: Build structured graph_stats dict for PivotPlanner scoring.</summary>
<div class="doc-comment">
<p>F238D: Build structured graph_stats dict for PivotPlanner scoring.</p>
<p></p>
<p></p>
<p></p>
<p>Called during nonfeed prelude (before advisory runner) to populate</p>
<p></p>
<p>graph_stats with {nodes, edges, domains, connected_iocs, node_degrees}</p>
<p></p>
<p>so that _score_pivot_domain and _score_pivot_graph can apply degree penalties</p>
<p></p>
<p>and novelty checks.</p>
<p></p>
<p></p>
<p></p>
<p>Returns empty dict (fail-soft) if graph unavailable or query fails.</p>
<p></p>
<p>No network, no model, no DuckDB heavy scans -- only bounded in-memory</p>
<p></p>
<p>aggregation over already-persisted graph data.</p>
<p></p>
<p></p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p></p>
<p>- No asyncio.run() or loop.run_until_complete()</p>
<p></p>
<p>- No model/MLX imports</p>
<p></p>
<p>- No network calls</p>
<p></p>
<p>- Bounded: MAX_PIVOT_GRAPH_STATS_NODES=500</p>
</div>
</details>
</li>
<li><code>build_lane_query</code> (acquisition_strategy.py)
<details><summary>Shape a source-specific query for an acquisition lane.</summary>
<div class="doc-comment">
<p>Shape a source-specific query for an acquisition lane.</p>
<p></p>
<p>F222I: When seed_context is provided (pivot/DuckDB domain extraction),</p>
<p>lanes receive the extracted domain/IP seed instead of the generic text query.</p>
<p>This enables CT/DOH/PassiveDNS/Wayback for "LockBit ransomware" style queries</p>
<p>that have no explicit domain/IP in the raw query text.</p>
<p></p>
<p>Rules per lane:</p>
<p>CT:          seed.domains[0] if available, else extract domains from base_query</p>
<p>WAYBACK:     seed.domains[0] or seed.urls[0] if available, else base_query</p>
<p>PASSIVE_DNS: seed.domains[0] or seed.ips[0] if available, else base_query</p>
<p>BLOCKCHAIN:  wallet/hash only; returns {"_disabled": True} if no crypto indicator</p>
<p>PUBLIC:      original query plus 1-2 bounded variants (seed ignored)</p>
<p>FEED:        original query unchanged (seed ignored)</p>
<p></p>
<p>No LLM, no network I/O. Deterministic.</p>
<p></p>
<p>Args:</p>
<p>base_query:  The sprint query string.</p>
<p>lane:        One of AcquisitionLane values.</p>
<p>seed_context: Optional NonfeedSeedContext from pivot/DuckDB extraction.</p>
<p></p>
<p>Returns:</p>
<p>Shaped query string, or a dict with lane guidance (e.g. {"_disabled": True}).</p>
<p>Returns {"_disabled": True} for BLOCKCHAIN when no crypto indicator present.</p>
</div>
</details>
</li>
<li><code>score_with_hermes_output</code> (pivot_planner.py)
<details><summary>Sprint F256 + Issue #17: Single-pass Hermes+heuristic pivot scoring.</summary>
<div class="doc-comment">
<p>Sprint F256 + Issue #17: Single-pass Hermes+heuristic pivot scoring.</p>
<p></p>
<p>OPTIMIZATION: Previously iterated findings TWICE (Hermes path + heuristic</p>
<p>path). Now builds a Hermes pivot map first, then iterates findings ONCE,</p>
<p>boosting heuristic pivots with Hermes scores during the single pass.</p>
<p></p>
<p>When hermes_outputs is non-empty:</p>
<p>- Primary: extract IOCs/entities from HermesInferenceOutput.key_iocs</p>
<p>and key_entities to generate pivots with boosted expected_value</p>
<p>- Secondary: use HermesInferenceOutput.pivot_suggestions directly</p>
<p>- Fallback: if hermes_outputs empty, fall back to existing heuristic path</p>
<p></p>
<p>When hermes_outputs is empty:</p>
<p>- Fall back to plan_pivots() heuristic path</p>
<p></p>
<p>Bounds:</p>
<p>- MAX_PIVOTS=20 (unchanged)</p>
<p>- hermes_outputs capped at MAX_INFERENCE_ITEMS=50</p>
<p>- Each HermesInferenceOutput key_iocs/key_entities capped at 20 items each</p>
<p>- Each HermesInferenceOutput pivot_suggestions capped at 10 items each</p>
<p></p>
<p>Args:</p>
<p>findings: list of CanonicalFinding objects</p>
<p>hermes_outputs: list of HermesInferenceOutput from Hermes3Engine</p>
<p>max_pivots: maximum number of pivots to return (default MAX_PIVOTS=20)</p>
<p>graph_stats: optional graph statistics for scoring</p>
<p>mission_intent: optional mission intent string for scoring</p>
<p></p>
<p>Returns:</p>
<p>list[Pivot] sorted by priority (highest first)</p>
<p>Always returns at least [] (fail-safe)</p>
</div>
</details>
</li>
<li><code>_compose_decision_gate_readiness</code> (shadow_pre_decision.py)</li>
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
<li><code>_process_result</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Accumulate result stats and dedup.</span></li>
<li><code>_compose_diff_taxonomy</code> (shadow_pre_decision.py)</li>
<li><code>_evaluate_locked</code> (resource_governor.py)
<details><summary>Build GovernorDecision while caller holds self._lock.</summary>
<div class="doc-comment">
<p>Build GovernorDecision while caller holds self._lock.</p>
<p></p>
<p>Called by evaluate() (holds lock) and evaluate_adaptive() (holds lock).</p>
<p>Updates self._uma_state, self._model_loaded, counters on self.</p>
</div>
</details>
</li>
<li><code>_run_pivot_planner_advisory</code> (sprint_advisory_runner.py)
<details><summary>F202G: Run pivot planner on accepted findings for advisory ordering.</summary>
<div class="doc-comment">
<p>F202G: Run pivot planner on accepted findings for advisory ordering.</p>
<p></p>
<p>Planner generates pivot suggestions; scheduler may use them as</p>
<p>ordering input for future sprints. Advisory only.</p>
<p></p>
<p>Fail-soft: errors never crash the runner.</p>
</div>
</details>
</li>
<li><code>build_lane_query</code> (__init__.py)
<details><summary>Shape a source-specific query for an acquisition lane.</summary>
<div class="doc-comment">
<p>Shape a source-specific query for an acquisition lane.</p>
<p></p>
<p>F222I: When seed_context is provided (pivot/DuckDB domain extraction),</p>
<p>lanes receive the extracted domain/IP seed instead of the generic text query.</p>
<p>This enables CT/DOH/PassiveDNS/Wayback for "LockBit ransomware" style queries</p>
<p>that have no explicit domain/IP in the raw query text.</p>
<p></p>
<p>Rules per lane:</p>
<p>CT:          seed.domains[0] if available, else extract domains from base_query</p>
<p>WAYBACK:     seed.domains[0] or seed.urls[0] if available, else base_query</p>
<p>PASSIVE_DNS: seed.domains[0] or seed.ips[0] if available, else base_query</p>
<p>BLOCKCHAIN:  wallet/hash only; returns {"_disabled": True} if no crypto indicator</p>
<p>PUBLIC:      original query plus 1-2 bounded variants (seed ignored)</p>
<p>FEED:        original query unchanged (seed ignored)</p>
<p></p>
<p>No LLM, no network I/O. Deterministic.</p>
<p></p>
<p>Args:</p>
<p>base_query:  The sprint query string.</p>
<p>lane:        One of AcquisitionLane values.</p>
<p>seed_context: Optional NonfeedSeedContext from pivot/DuckDB extraction.</p>
<p></p>
<p>Returns:</p>
<p>Shaped query string, or a dict with lane guidance (e.g. {"_disabled": True}).</p>
<p>Returns {"_disabled": True} for BLOCKCHAIN when no crypto indicator present.</p>
</div>
</details>
</li>
<li><code>_build_nonfeed_lane_eligibility</code> (acquisition_strategy.py)
<details><summary>F214: Build the nonfeed lane eligibility matrix for acquisition reporting.</summary>
<div class="doc-comment">
<p>F214: Build the nonfeed lane eligibility matrix for acquisition reporting.</p>
<p></p>
<p>Computed from query indicators (not plan.enabled) so the matrix explains WHY</p>
<p>each lane was or was not planned — using the same indicator logic as the</p>
<p>planner, independent of runtime state (hardware, transport, etc.).</p>
<p></p>
<p>Schema::</p>
<p></p>
<p>{</p>
<p>"public":  {"eligible": true, "reason": "...", "required_inputs": [], "available_inputs": {...}},</p>
<p>"ct":      {"eligible": true|false, "reason": "...", "required_inputs": [...], "available_inputs": {...}},</p>
<p>"doh":     {"eligible": true|false, "reason": "...", "required_inputs": [...], "available_inputs": {...}},</p>
<p>"wayback": {"eligible": true|false, "reason": "...", "required_inputs": [...], "available_inputs": {...}},</p>
<p>"passive_dns": {"eligible": true|false, "reason": "...", "required_inputs": [...], "available_inputs": {...}},  # noqa: E501</p>
<p>}</p>
<p></p>
<p>Profile rules (active300/default):</p>
<p>- public:   always eligible if provider available (advisory, not gated by candidates)</p>
<p>- CT:       eligible if domain (not IP-only) candidates present</p>
<p>- DOH:      eligible if domain (not IP-only) candidates present</p>
<p>- WAYBACK:  eligible if URL or domain candidates present</p>
<p>- passive_dns: eligible if domain or IP candidates present</p>
<p></p>
<p>Profile rules (nonfeed_diagnostic):</p>
<p>- public:   expected if provider available</p>
<p>- DOH:      expected if domains exist</p>
<p>- CT:       expected if domains exist</p>
<p>- WAYBACK:  expected if URLs/domains exist</p>
<p>- passive_dns: expected if domains/IPs exist</p>
</div>
</details>
</li>
<li><code>_build_nonfeed_lane_eligibility</code> (__init__.py)
<details><summary>F214: Build the nonfeed lane eligibility matrix for acquisition reporting.</summary>
<div class="doc-comment">
<p>F214: Build the nonfeed lane eligibility matrix for acquisition reporting.</p>
<p></p>
<p>Computed from query indicators (not plan.enabled) so the matrix explains WHY</p>
<p>each lane was or was not planned — using the same indicator logic as the</p>
<p>planner, independent of runtime state (hardware, transport, etc.).</p>
<p></p>
<p>Schema::</p>
<p></p>
<p>{</p>
<p>"public":  {"eligible": true, "reason": "...", "required_inputs": [], "available_inputs": {...}},</p>
<p>"ct":      {"eligible": true|false, "reason": "...", "required_inputs": [...], "available_inputs": {...}},</p>
<p>"doh":     {"eligible": true|false, "reason": "...", "required_inputs": [...], "available_inputs": {...}},</p>
<p>"wayback": {"eligible": true|false, "reason": "...", "required_inputs": [...], "available_inputs": {...}},</p>
<p>"passive_dns": {"eligible": true|false, "reason": "...", "required_inputs": [...], "available_inputs": {...}},  # noqa: E501</p>
<p>}</p>
<p></p>
<p>Profile rules (active300/default):</p>
<p>- public:   always eligible if provider available (advisory, not gated by candidates)</p>
<p>- CT:       eligible if domain (not IP-only) candidates present</p>
<p>- DOH:      eligible if domain (not IP-only) candidates present</p>
<p>- WAYBACK:  eligible if URL or domain candidates present</p>
<p>- passive_dns: eligible if domain or IP candidates present</p>
<p></p>
<p>Profile rules (nonfeed_diagnostic):</p>
<p>- public:   expected if provider available</p>
<p>- DOH:      expected if domains exist</p>
<p>- CT:       expected if domains exist</p>
<p>- WAYBACK:  expected if URLs/domains exist</p>
<p>- passive_dns: expected if domains/IPs exist</p>
</div>
</details>
</li>
<li><code>_compose_diagnostic_metadata</code> (shadow_pre_decision.py)</li>
<li><code>_run_public_branch</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Public discovery branch — runs concurrently with FEED branch.</span></li>
<li><code>_run_banner_grab_sidecar</code> (sprint_scheduler_v1_archived.py)
<details><summary>F214Q: Banner grab -- service fingerprinting via TCP probe.</summary>
<div class="doc-comment">
<p>F214Q: Banner grab -- service fingerprinting via TCP probe.</p>
<p></p>
<p></p>
<p></p>
<p>Gate: HLEDAC_ENABLE_BANNER_GRAB=1 + memory guard.</p>
<p></p>
<p>Seeds: IP/domain z findings.</p>
<p></p>
<p>INVARIANT: Banner grab = aktivní TCP probe = CLEARNET ONLY (ne přes Tor).</p>
<p></p>
<p>Timeout: 10s per port, max 5 portů.</p>
</div>
</details>
</li>
<li><code>normalize_source_family_outcome</code> (acquisition_strategy.py)
<details><summary>Normalize a raw lane or adapter outcome dict into SourceFamilyOutcome fields.</summary>
<div class="doc-comment">
<p>Normalize a raw lane or adapter outcome dict into SourceFamilyOutcome fields.</p>
<p></p>
<p>Handles three F207F shapes:</p>
<p>- AcquisitionLaneOutcome  (ct, wayback, passive_dns, blockchain lanes)</p>
<p>- dict with ct_results_raw / produced_items / accepted_findings keys</p>
<p>- Feed balance tuple (verdict_tag, signal, fallback_use, fallback_waste, quality)</p>
<p>which maps to family=FEED, attempted=True, raw_count=signal</p>
<p></p>
<p>Also handles the "missing family" case where no outcome was produced at all,</p>
<p>returning a skipped/attempted=False outcome for documentation purposes.</p>
</div>
</details>
</li>
<li><code>_run_ipfs_lane</code> (__init__.py)
<details><summary>R10: CID-only IPFS evidence fetch — bounded gateway fetch, no search/DHT/recursive.</summary>
<div class="doc-comment">
<p>R10: CID-only IPFS evidence fetch — bounded gateway fetch, no search/DHT/recursive.</p>
<p></p>
<p>Enabled only when query is an explicit CID. No model load, no browser,</p>
<p>no stealth. Hard cap: 5 CIDs per sprint regardless of max_items.</p>
</div>
</details>
</li>
<li><code>_run_bgp_enrichment_sidecar</code> (sprint_scheduler_v1_archived.py)
<details><summary>F214Q: BGP enrichment -- AS path analysis for IP/ASN seeds.</summary>
<div class="doc-comment">
<p>F214Q: BGP enrichment -- AS path analysis for IP/ASN seeds.</p>
<p></p>
<p></p>
<p></p>
<p>Gate: HLEDAC_ENABLE_BGP=1 + M1 memory guard (skip if critical/emergency).</p>
<p></p>
<p>Seeds: IP/ASN z aktuálních findings (IOC_TYPES: "ip").</p>
<p></p>
<p>Max 3 IP/ASN per sprint, 30s timeout.</p>
<p></p>
<p>Semaphore(1) -- BGP queries jsou heavyweight.</p>
</div>
</details>
</li>
<li><code>fetch_one</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>compose_advisory_gate</code> (shadow_pre_decision.py)</li>
<li><code>_run_prelude_and_first_cycle</code> (scheduler.py)
<details><summary>Run prelude lanes and first cycle in parallel.</summary>
<div class="doc-comment">
<p>Run prelude lanes and first cycle in parallel.</p>
<p></p>
<p>Corresponds to v1's gather at lines ~7755-7858.</p>
</div>
</details>
</li>
<li><code>_run_privacy_gate_async</code> (sprint_scheduler_v1_archived.py)
<details><summary>Pre-storage PII anonymization gate.</summary>
<div class="doc-comment">
<p>Pre-storage PII anonymization gate.</p>
<p></p>
<p>Runs BEFORE async_ingest_findings_batch() for ALL storage paths.</p>
<p>Returns (anonymized_findings, pii_count).</p>
<p></p>
<p>Scopes: content, raw_content, payload_text, title, summary.</p>
<p>Fail-soft: never raises -- findings pass through unmodified on any error.</p>
<p></p>
<p>INVARIANT: Never raises. Always returns input findings on error.</p>
</div>
</details>
</li>
<li><code>_adapt_source_weights_from_feedback</code> (sprint_scheduler_v1_archived.py)
<details><summary>F199A: Adapt _source_weights from per-source quality feedback collected during the sprint.</summary>
<div class="doc-comment">
<p>F199A: Adapt _source_weights from per-source quality feedback collected during the sprint.</p>
<p></p>
<p></p>
<p></p>
<p>Called at teardown (in run() after cycles complete). Updates each feed_url's weight</p>
<p></p>
<p>based on accepted/total ratio signal collected via _process_result().</p>
<p></p>
<p></p>
<p></p>
<p>Adaptation rule (B.6 bounds ±20% per sprint -&gt; clamp to [0.3, 2.5]):</p>
<p></p>
<p>- accepted/total &gt;= 0.7 -&gt; reward: +10%</p>
<p></p>
<p>- accepted/total &gt;= 0.4 -&gt; reward: +5%</p>
<p></p>
<p>- accepted/total &gt;= 0.15 -&gt; reward: 0 (neutral)</p>
<p></p>
<p>- accepted/total &lt; 0.15 -&gt; penalty: -5%</p>
<p></p>
<p>- no signal (total=0) -&gt; no change</p>
<p></p>
<p></p>
<p></p>
<p>Signal is per-feed_url (feed_url as key), not per-source_type.</p>
<p></p>
<p>For scoring, feed_url maps to source_type via _config.tier_of(feed_url).name.</p>
</div>
</details>
</li>
<li><code>run_async</code> (sidecar_protocol_adapters.py) — <span class="doc-comment-inline">Embed query + findings into LanceDB for cross-sprint persistence.</span></li>
<li><code>deduplicate_and_rank_findings</code> (sprint_scheduler_v1_archived.py)
<details><summary>DuckDB-powered dedup + ranking over Parquet files (F5.4).</summary>
<div class="doc-comment">
<p>DuckDB-powered dedup + ranking over Parquet files (F5.4).</p>
<p></p>
<p>Strategy:</p>
<p>1. DuckDB SQL aggregation via read_parquet(glob) — zero-copy Arrow,</p>
<p>M1 RAM-safe streaming, no polars dependency for I/O.</p>
<p>2. COPY TO Parquet — DuckDB writes directly, no intermediate DataFrame.</p>
<p>3. Polars only for in-memory ranking when DuckDB COPY is unavailable.</p>
<p></p>
<p>Fallback chain: DuckDB COPY → polars LazyFrame streaming collect →</p>
<p>pyarrow fallback. All paths return a valid parquet path.</p>
</div>
</details>
</li>
<li><code>_get_circuit_breaker_summary</code> (sprint_scheduler_v1_archived.py)
<details><summary>F206I: Build a bounded circuit breaker state summary for the diagnostic report.</summary>
<div class="doc-comment">
<p>F206I: Build a bounded circuit breaker state summary for the diagnostic report.</p>
<p></p>
<p></p>
<p></p>
<p>Reads the shared domain circuit breaker registry (get_all_breaker_snapshots)</p>
<p></p>
<p>and returns a compact summary. Non-persisting, in-memory only.</p>
<p></p>
<p></p>
<p></p>
<p>Bounds:</p>
<p></p>
<p>- MAX_TRACKED_DOMAINS=500 (from circuit_breaker module)</p>
<p></p>
<p>- MAX_BREAKER_DOMAINS=500 (local alias)</p>
<p></p>
<p>- Each snapshot is a small dict: domain, state, failure_count, retry_after_s</p>
<p></p>
<p></p>
<p></p>
<p>Fail-soft: returns empty dict on any error.</p>
<p></p>
<p></p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p></p>
<p>- No asyncio.gather / _check_gathered (sync method)</p>
<p></p>
<p>- No asyncio.run() or loop.run_until_complete()</p>
<p></p>
<p>- No canonical write path (read-only)</p>
<p></p>
<p>- Circuit breaker itself does not persist</p>
</div>
</details>
</li>
<li><code>_prewarm_hermes_for_sprint</code> (sprint_scheduler_v1_archived.py)
<details><summary>P12: Mode-aware Hermes prewarm policy.</summary>
<div class="doc-comment">
<p>P12: Mode-aware Hermes prewarm policy.</p>
<p></p>
<p></p>
<p></p>
<p>Aggressive mode: prewarm blocks until Hermes is loaded, unless RSS &gt; 4GB</p>
<p></p>
<p>(hard headroom rule -- skip fail-soft, ToT is skipped for that run).</p>
<p></p>
<p></p>
<p></p>
<p>Stable mode: current safe behavior via ModelManager memory guards</p>
<p></p>
<p>(soft pressure clear + hard admission gate -- no RSS 4GB pre-check).</p>
<p></p>
<p></p>
<p></p>
<p>Bounded lifecycle: loaded once at BOOT/WARMUP, released at TEARDOWN.</p>
<p></p>
<p>Fail-soft: memory pressure on load skips ToT, does not abort sprint.</p>
<p></p>
<p></p>
<p></p>
<p>F203J: Quantization budget respected via QuantizationSelector advisory</p>
<p></p>
<p>in ModelManager._load_model_async. Budget is logged here for visibility.</p>
</div>
</details>
</li>
<li><code>_maybe_flush_to_parquet</code> (sprint_scheduler_v1_archived.py)
<details><summary>Flush Arrow batch to Parquet when N or S threshold is hit.</summary>
<div class="doc-comment">
<p>Flush Arrow batch to Parquet when N or S threshold is hit.</p>
<p></p>
<p></p>
<p></p>
<p>F214OPT-D: On flush failure, batch is truncated to HARD_CAP to prevent</p>
<p></p>
<p>unbounded growth. Failed entries are dropped (oldest first) and counted.</p>
</div>
</details>
</li>
<li><code>summarize_doh_conversion</code> (source_finding_bridge.py)</li>
<li><code>_run_public_prelude_lane</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Run PUBLIC prelude lane. Returns result dict, never raises.</span></li>
<li><code>fetch_one</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>fetch_one</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_record_quality_rejections_from_store</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F216G: Read quality rejection ledger from duckdb_store and</summary>
<div class="doc-comment">
<p>Sprint F216G: Read quality rejection ledger from duckdb_store and</p>
<p></p>
<p>compute summary dictionaries.</p>
<p></p>
<p></p>
<p></p>
<p>Called after run_enabled_acquisition_lanes() completes (both advisory</p>
<p></p>
<p>and aggressive cycles) so that all lane ingest quality gate rejections</p>
<p></p>
<p>are captured in SprintSchedulerResult.</p>
<p></p>
<p></p>
<p></p>
<p>Also called from _maybe_dispatch_nonfeed_probe_lanes after its</p>
<p></p>
<p>direct async_ingest_findings_batch call.</p>
<p></p>
<p></p>
<p></p>
<p>Invariants (strict):</p>
<p></p>
<p>- No threshold changes</p>
<p></p>
<p>- No dedup behavior changes</p>
<p></p>
<p>- No destructive DB schema migration</p>
<p></p>
<p>- No benchmark-owned scoring change</p>
</div>
</details>
</li>
<li><code>summarize_passive_dns_conversion</code> (source_finding_bridge.py)</li>
<li><code>_run_sprint_loop</code> (sprint_entrypoint.py)
<details><summary>Extracted CLI sprint wiring (Issue #7).</summary>
<div class="doc-comment">
<p>Extracted CLI sprint wiring (Issue #7).</p>
<p>Owns: loop + signals + shutdown_event + task lifecycle.</p>
<p>Canonical state lives in run_sprint(), not here.</p>
</div>
</details>
</li>
<li><code>_run_wayback_prelude_lane</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_duckdb_background_writer</code> (sprint_scheduler_v1_archived.py)
<details><summary>F285: Background writer that drains _duckdb_write_queue sequentially.</summary>
<div class="doc-comment">
<p>F285: Background writer that drains _duckdb_write_queue sequentially.</p>
<p></p>
<p>Enables overlapping DuckDB writes with the next cycle acquisition.</p>
<p>Sequential draining preserves WAL ordering guarantees.</p>
<p>Fail-soft: exceptions are logged but do not propagate.</p>
<p></p>
<p>Event-driven wakeup: uses asyncio.Event instead of 5s timeout polling.</p>
<p>Notifies writer immediately when items are enqueued. Falls back to</p>
<p>30s heartbeat to prevent starvation if notify is ever missed.</p>
<p></p>
<p>BUG-7 FIX: Drain-first shutdown. On shutdown signal, drain all queued</p>
<p>items BEFORE exiting. This closes the race where findings arriving</p>
<p>between shutdown.set() and the next queue.get() were silently dropped.</p>
</div>
</details>
</li>
<li><code>_run_ipfs_lane</code> (acquisition_strategy.py)
<details><summary>R10: CID-only IPFS evidence fetch — bounded gateway fetch, no search/DHT/recursive.</summary>
<div class="doc-comment">
<p>R10: CID-only IPFS evidence fetch — bounded gateway fetch, no search/DHT/recursive.</p>
<p></p>
<p>Enabled only when query is an explicit CID. No model load, no browser,</p>
<p>no stealth. Hard cap: 5 CIDs per sprint regardless of max_items.</p>
</div>
</details>
</li>
<li><code>run_async</code> (sidecar_protocol_adapters.py) — <span class="doc-comment-inline">Fetch content via alternative protocols based on query.</span></li>
<li><code>summarize_wayback_conversion</code> (source_finding_bridge.py)</li>
<li><code>_run_dark_pivot_sidecars</code> (sidecar_orchestrator.py)</li>
<li><code>run_embed</code> (role_based_pools.py)</li>
<li><code>_check_prewindup_barrier_sync</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F207R-A: Synchronous pre-windup barrier check (read-only).</summary>
<div class="doc-comment">
<p>Sprint F207R-A: Synchronous pre-windup barrier check (read-only).</p>
<p></p>
<p>P1-B FIX: This function is called from windup_guard() callback AFTER</p>
<p></p>
<p>_ensure_pre_windup_lane_terminal_states() has already run at line 7397.</p>
<p></p>
<p>Previously this function re-ran _ensure_pre_windup_lane_terminal_states()</p>
<p></p>
<p>via run_coroutine_threadsafe, causing a RACE CONDITION where both calls</p>
<p></p>
<p>wrote to self._result fields simultaneously, resulting in:</p>
<p></p>
<p>- attempted_lanes=[] (second call overwrote first)</p>
<p></p>
<p>- satisfied=False (skipped lanes not counted correctly)</p>
<p></p>
<p>- windup_guard_last_allowed=False (callback saw unsatisfied barrier)</p>
<p></p>
<p>FIX: Read prewindup barrier state directly from self._result instead</p>
<p></p>
<p>of re-running the async barrier check. This is the correct design because:</p>
<p></p>
<p>1. _ensure_pre_windup_lane_terminal_states() already ran at line 7397</p>
<p></p>
<p>2. It set prewindup_barrier_checked=True and populated all barrier fields</p>
<p></p>
<p>3. windup_guard() is called AFTER step 1, so telemetry is already available</p>
<p></p>
<p>Returns True if windup is allowed (barrier satisfied or not required).</p>
<p></p>
<p>Returns False if windup must be blocked (required lanes not terminal).</p>
<p></p>
<p>Fail-closed: on error, blocks windup with explicit telemetry.</p>
</div>
</details>
</li>
<li><code>_generate_conceptual_domains_mlx</code> (nonfeed_candidate_ledger.py)
<details><summary>F289: Generate plausible domain candidates from conceptual OSINT query using MLX.</summary>
<div class="doc-comment">
<p>F289: Generate plausible domain candidates from conceptual OSINT query using MLX.</p>
<p></p>
<p>Called when extract_domain_candidates_from_text() returns zero candidates.</p>
<p>Uses DeepHermes3Engine.generate() to produce domain candidates based on</p>
<p>the query's OSINT topic (e.g. "ransomware leak dark web" → plausible leak sites).</p>
<p></p>
<p>Bounded: max MAX_CONCEPTUAL_DOMAINS candidates, 30s timeout.</p>
<p>Fail-safe: returns empty list on any error (MLX unavailable, timeout, parse failure).</p>
<p></p>
<p>Args:</p>
<p>query: The sprint query string (conceptual, no domain/IP)</p>
<p></p>
<p>Returns:</p>
<p>List of DomainCandidate with confidence=0.5 (lower than extracted).</p>
</div>
</details>
</li>
<li><code>_update_source_economics</code> (sprint_scheduler_v1_archived.py)
<details><summary>Update per-source economics from pipeline result signals.</summary>
<div class="doc-comment">
<p>Update per-source economics from pipeline result signals.</p>
<p></p>
<p></p>
<p></p>
<p>Uses only existing surfaces from FeedPipelineRunResult:</p>
<p></p>
<p>- signal_stage: cold/hot diagnosis</p>
<p></p>
<p>- feed_confidence_score: 0-100 adapter-informed confidence</p>
<p></p>
<p>- winning_source_breakdown: signal origin analysis</p>
<p></p>
<p></p>
<p></p>
<p>Economics state is in-memory only for the current sprint.</p>
<p></p>
<p>Reset happens in _reset_result().</p>
</div>
</details>
</li>
<li><code>_get_source_health_summary</code> (sprint_scheduler_v1_archived.py)
<details><summary>F206I: Build a bounded source health summary from per-source economics.</summary>
<div class="doc-comment">
<p>F206I: Build a bounded source health summary from per-source economics.</p>
<p></p>
<p></p>
<p></p>
<p>Reads _source_economics (in-memory, per-sprint) and returns a</p>
<p></p>
<p>compact summary dict for the diagnostic report. Non-persisting.</p>
<p></p>
<p></p>
<p></p>
<p>Bounds:</p>
<p></p>
<p>- MAX_SOURCE_HEALTH_ENTRIES=100 (most-healthy first)</p>
<p></p>
<p>- Each entry is a small dict with posture and cooldown info</p>
<p></p>
<p></p>
<p></p>
<p>Fail-soft: returns empty dict on any error.</p>
<p></p>
<p></p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p></p>
<p>- No asyncio.gather / _check_gathered (sync method)</p>
<p></p>
<p>- No asyncio.run() or loop.run_until_complete()</p>
<p></p>
<p>- No model/MLX imports</p>
<p></p>
<p>- No canonical write path (read-only)</p>
</div>
</details>
</li>
<li><code>normalize_source_family_outcome</code> (__init__.py)
<details><summary>Normalize a raw lane or adapter outcome dict into SourceFamilyOutcome fields.</summary>
<div class="doc-comment">
<p>Normalize a raw lane or adapter outcome dict into SourceFamilyOutcome fields.</p>
<p></p>
<p>Handles three F207F shapes:</p>
<p>- AcquisitionLaneOutcome  (ct, wayback, passive_dns, blockchain lanes)</p>
<p>- dict with ct_results_raw / produced_items / accepted_findings keys</p>
<p>- Feed balance tuple (verdict_tag, signal, fallback_use, fallback_waste, quality)</p>
<p>which maps to family=FEED, attempted=True, raw_count=signal</p>
<p></p>
<p>Also handles the "missing family" case where no outcome was produced at all,</p>
<p>returning a skipped/attempted=False outcome for documentation purposes.</p>
</div>
</details>
</li>
<li><code>run_db</code> (role_based_pools.py)</li>
<li><code>extract_domain_candidates_from_text</code> (nonfeed_candidate_ledger.py)
<details><summary>F214: Extract domain and URL hostname candidates from arbitrary text.</summary>
<div class="doc-comment">
<p>F214: Extract domain and URL hostname candidates from arbitrary text.</p>
<p></p>
<p>No external dependencies — uses only stdlib urllib.parse and regex.</p>
<p></p>
<p>Normalization pipeline:</p>
<p>1. Normalize defanged markers ([.], (.), hxxp://, etc.) on whole text</p>
<p>2. Run domain regex on normalized text</p>
<p>3. Validate each candidate with _is_valid_domain_candidate</p>
<p>4. Deduplicate by normalized domain + source_field</p>
<p></p>
<p>Args:</p>
<p>text:           Text to scan (body content, title, etc.)</p>
<p>source_url:     Optional source URL for hostname extraction</p>
<p>source_family:  "PUBLIC" or "FEED" for ledger attribution</p>
<p>min_confidence: Minimum confidence threshold (0.0–1.0)</p>
<p></p>
<p>Returns:</p>
<p>List of DomainCandidate (may be empty).</p>
<p>Deduplicated by normalized domain (first-seen per field wins).</p>
</div>
</details>
</li>
<li><code>_classify_and_filter_seeds</code> (nonfeed_seed_runtime.py)</li>
<li><code>_decompose_query_keywords_to_seeds</code> (nonfeed_seed_runtime.py)
<details><summary>P3-1: Rule-based query decomposition for complex OSINT threat queries.</summary>
<div class="doc-comment">
<p>P3-1: Rule-based query decomposition for complex OSINT threat queries.</p>
<p></p>
<p>Maps keyword patterns to surface-level domain seeds when:</p>
<p>1. Regex found no domains (no direct IOC in query)</p>
<p>2. MLX is unavailable or produced nothing</p>
<p></p>
<p>Returns up to 5 domain seeds from _DEAD_SURFACE_DOMAINS matched by query</p>
<p>keywords. These are intentionally broad/generic surface seeds — the actual</p>
<p>pivoting happens via DOH/WAYBACK/CT lanes that query these seeds.</p>
<p></p>
<p>This is NOT MLX-dependent — pure keyword matching with bounded output.</p>
<p></p>
<p>Args:</p>
<p>query: Sprint query string (e.g. "APT nation-state ransomware leak")</p>
<p></p>
<p>Returns:</p>
<p>List of domain strings (max 5), sorted by specificity match.</p>
<p>Empty list if no rules matched.</p>
</div>
</details>
</li>
<li><code>_ensure_nonfeed_predispatch_before_finalization</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F208M-A: Ensure nonfeed predispatch has run before final terminality.</summary>
<div class="doc-comment">
<p>Sprint F208M-A: Ensure nonfeed predispatch has run before final terminality.</p>
<p></p>
<p></p>
<p></p>
<p>This helper is called before every _finalize_result_truth() to guarantee</p>
<p></p>
<p>that bounded CT/PUBLIC predispatch has had a chance to populate</p>
<p></p>
<p>acquisition_lane_outcomes / _lane_outcomes BEFORE terminality is computed.</p>
<p></p>
<p></p>
<p></p>
<p>Without this, terminality computed in _finalize_result_truth sees</p>
<p></p>
<p>acquisition_lane_outcomes empty (no CT attempted yet), marking CT as</p>
<p></p>
<p>missing even though _maybe_dispatch_nonfeed_probe_lanes() was called.</p>
<p></p>
<p></p>
<p></p>
<p>Runs only once per sprint -- subsequent calls are no-ops.</p>
<p></p>
<p>Records explicit telemetry so failure is never silent.</p>
<p></p>
<p></p>
<p></p>
<p>Args:</p>
<p></p>
<p>query: Sprint query for lane shaping.</p>
<p></p>
<p>reason: Human-readable reason for this finalization call.</p>
<p></p>
<p></p>
<p></p>
<p>Raises:</p>
<p></p>
<p>CancelledError: propagated if predispatch is cancelled.</p>
</div>
</details>
</li>
<li><code>_drain_pending_pattern_extractions</code> (sprint_scheduler_v1_archived.py)
<details><summary>F273C + F273H: Pre-windup drain of in-flight pattern-extraction Futures.</summary>
<div class="doc-comment">
<p>F273C + F273H: Pre-windup drain of in-flight pattern-extraction Futures.</p>
<p></p>
<p>Calls into `public_fetcher.drain_pending_extractions(deadline_s)` to</p>
<p>await any HTML the public fetcher has already submitted to</p>
<p>CPU_EXECUTOR. This is the direct fix for the "16/16 fetched → 0</p>
<p>matched patterns → 0 stored" failure mode where the windup transition</p>
<p>cancelled the awaiting branch before its extraction Future resolved.</p>
<p></p>
<p>F273H: Adaptive drain deadline. Before this fix the drain deadline was</p>
<p>a fixed 30s that could exceed the remaining sprint time, causing the</p>
<p>drain itself to consume nearly the entire active window on short</p>
<p>sprints (windup = 304.57s of 305s observed). Now bounded to</p>
<p>min(30s, remaining_s * 0.3) so the drain never consumes more than 30%</p>
<p>of whatever time remains. Also early-exits when the drain registry</p>
<p>is already empty, avoiding a pointless wait.</p>
<p></p>
<p>Always-on, bounded (adaptive deadline), fail-soft: any error</p>
<p>in the drain path returns silently and the windup decision proceeds.</p>
<p></p>
<p>Telemetry recorded on self._result:</p>
<p>- pattern_extraction_drain_completed  (cumulative count)</p>
<p>- pattern_extraction_drain_timed_out  (cumulative count)</p>
<p>- pattern_extraction_drain_elapsed_s  (last drain wall-clock)</p>
<p>- effective_windup_lead_used_s  (actual windup lead applied)</p>
</div>
</details>
</li>
<li><code>_enrich_findings_multimodal</code> (sprint_scheduler_v1_archived.py)
<details><summary>Enrich PDF/image findings with multimodal analysis before storage.</summary>
<div class="doc-comment">
<p>Enrich PDF/image findings with multimodal analysis before storage.</p>
<p></p>
<p>Fail-safe: enrichment errors are silent -- never crash or abort the sprint.</p>
<p>Enrichment is best-effort: absence of multimodal data is not an error.</p>
</div>
</details>
</li>
<li><code>_enrich_ct_findings_forensics</code> (sprint_scheduler_v1_archived.py)
<details><summary>Enrich CT findings with forensics analysis before storage.</summary>
<div class="doc-comment">
<p>Enrich CT findings with forensics analysis before storage.</p>
<p></p>
<p>Fail-safe: enrichment errors are silent -- never crash or abort the sprint.</p>
<p>Enrichment is best-effort: absence of forensics data is not an error.</p>
</div>
</details>
</li>
<li><code>_load_in_thread</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">I/O-bound Hermes model load -- runs in thread pool, not event loop.</span></li>
<li><code>run_pre_sprint_checks</code> (sprint_entrypoint.py)
<details><summary>Run mandatory pre-sprint checks.</summary>
<div class="doc-comment">
<p>Run mandatory pre-sprint checks.</p>
<p></p>
<p>Returns True if safe to proceed, False to abort.</p>
</div>
</details>
</li>
<li><code>build_acquisition_plan</code> (acquisition_strategy.py)
<details><summary>Build an acquisition strategy snapshot for the given sprint context.</summary>
<div class="doc-comment">
<p>Build an acquisition strategy snapshot for the given sprint context.</p>
<p></p>
<p>Args:</p>
<p>query:              The sprint query string.</p>
<p>duration_s:         Sprint duration in seconds.</p>
<p>aggressive_mode:    True if running in aggressive (parallel) mode.</p>
<p>uma_state:          Current UMA state string ("ok", "warn", "critical", "emergency").</p>
<p>swap_detected:      True if system swap has been detected.</p>
<p>accepted_findings_so_far: Number of accepted findings collected so far.</p>
<p>branch_timeout_count:    Number of branch timeouts in current sprint.</p>
<p>transport_authority_status: Optional dict with transport authority signals.</p>
<p>Supported keys:</p>
<p>- "degraded": bool — True if transport is degraded</p>
<p>- "stealth_phase": int — current stealth phase (1-4)</p>
<p>stealth_phase:      Optional dict with stealth phase info.</p>
<p>Supported keys:</p>
<p>- "phase": int — current stealth phase</p>
<p>- "breaker_seam_ready": bool — True when phase &gt;= 3</p>
<p>acquisition_profile: F216B: Runtime profile controlling lane caps.</p>
<p>"default" = standard behavior.</p>
<p>"nonfeed_diagnostic" = caps FEED at 25, enables nonfeed lanes for domain queries.</p>
<p>Falls back to HLEDAC_ACQUISITION_PROFILE env var if not explicitly passed.</p>
<p>rl_lane_combo: F265LANE: Optional frozenset of lane names (e.g. {"CT","WAYBACK"})</p>
<p>from RL policy action. When set, overrides lane enabled/disabled decisions</p>
<p>to match the RL-chosen combination.</p>
<p>feed_domain_seeds: P0-8: Domain seeds extracted from accepted feed findings.</p>
<p>When query has no domain indicator but feed findings contain domains,</p>
<p>these seeds enable CT/DOH/WAYBACK lanes mid-sprint.</p>
<p></p>
<p>Returns:</p>
<p>AcquisitionStrategySnapshot with per-lane plans.</p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p>- No network I/O</p>
<p>- No model/MLX load</p>
<p>- No asyncio.run() / loop.run_until_complete()</p>
<p>- Bounded: max 12 lane plans (all canonical acquisition lanes)</p>
<p>- Fail-soft: on any error returns minimal snapshot with all lanes disabled</p>
</div>
</details>
</li>
<li><code>run_all_advisories</code> (sprint_advisory_runner.py)
<details><summary>Run all 6 advisory steps with parallelization where safe.</summary>
<div class="doc-comment">
<p>Run all 6 advisory steps with parallelization where safe.</p>
<p></p>
<p>Order (mandatory sequential):</p>
<p>1. pivot_planner  → planned_pivots</p>
<p>2. pivot_executor → executed_pivots  [depends on 1]</p>
<p></p>
<p>Steps 3-6 run in PARALLEL (bounded semaphore, M1 8GB safe):</p>
<p>3. resource_governor → governor_recorded</p>
<p>4. analyst_brief → brief_generated</p>
<p>5. local_search → local_search_*</p>
<p>6. federated_research → federated_* (F350M-FED-P3-FOLLOWUP)</p>
<p></p>
<p>Parallel execution via safe_gather_ok with _ADVISORY_PARALLEL_SEMAPHORE_LIMIT=4.</p>
<p>Each step is fail-soft; exceptions are collected and merged into outcome.error.</p>
<p></p>
<p>CancelledError: re-raised to caller.</p>
<p>Fail-soft: any step failure returns partial outcome with error message.</p>
<p></p>
<p>Returns:</p>
<p>AdvisoryRunOutcome with counts/flags for each step.</p>
</div>
</details>
</li>
<li><code>_sort_work_items_by_economics</code> (sprint_scheduler_v1_archived.py)
<details><summary>Re-sort work items by source economics.</summary>
<div class="doc-comment">
<p>Re-sort work items by source economics.</p>
<p></p>
<p></p>
<p></p>
<p>Order:</p>
<p></p>
<p>1. Sources NOT in cooldown first (natural priority)</p>
<p></p>
<p>2. Sources with hot/warm posture boosted</p>
<p></p>
<p>3. Cold/in-cooldown sources at the end</p>
<p></p>
<p>4. Tier ordering still applies as secondary sort key</p>
<p></p>
<p>5. F200A: Advisory prefetch oracle score multiplies the sort key</p>
<p></p>
<p></p>
<p></p>
<p>F200A: oracle is ADVISORY ONLY -- scheduler retains authority.</p>
<p></p>
<p>If oracle is None or suggest_scores fails -&gt; falls back to default ordering.</p>
</div>
</details>
</li>
<li><code>_build_plugin_sidecar_context</code> (sidecar_orchestrator.py)
<details><summary>Construct a SidecarContext (or duck-typed equivalent) from the</summary>
<div class="doc-comment">
<p>Construct a SidecarContext (or duck-typed equivalent) from the</p>
<p>current scheduler state. Returns None if no scheduler is bound.</p>
</div>
</details>
</li>
<li><code>__post_init__</code> (scheduler.py)</li>
<li><code>build_acquisition_plan</code> (__init__.py)
<details><summary>Build an acquisition strategy snapshot for the given sprint context.</summary>
<div class="doc-comment">
<p>Build an acquisition strategy snapshot for the given sprint context.</p>
<p></p>
<p>Args:</p>
<p>query:              The sprint query string.</p>
<p>duration_s:         Sprint duration in seconds.</p>
<p>aggressive_mode:    True if running in aggressive (parallel) mode.</p>
<p>uma_state:          Current UMA state string ("ok", "warn", "critical", "emergency").</p>
<p>swap_detected:      True if system swap has been detected.</p>
<p>accepted_findings_so_far: Number of accepted findings collected so far.</p>
<p>branch_timeout_count:    Number of branch timeouts in current sprint.</p>
<p>transport_authority_status: Optional dict with transport authority signals.</p>
<p>Supported keys:</p>
<p>- "degraded": bool — True if transport is degraded</p>
<p>- "stealth_phase": int — current stealth phase (1-4)</p>
<p>stealth_phase:      Optional dict with stealth phase info.</p>
<p>Supported keys:</p>
<p>- "phase": int — current stealth phase</p>
<p>- "breaker_seam_ready": bool — True when phase &gt;= 3</p>
<p>acquisition_profile: F216B: Runtime profile controlling lane caps.</p>
<p>"default" = standard behavior.</p>
<p>"nonfeed_diagnostic" = caps FEED at 25, enables nonfeed lanes for domain queries.</p>
<p>Falls back to HLEDAC_ACQUISITION_PROFILE env var if not explicitly passed.</p>
<p>rl_lane_combo: F265LANE: Optional frozenset of lane names (e.g. {"CT","WAYBACK"})</p>
<p>from RL policy action. When set, overrides lane enabled/disabled decisions</p>
<p>to match the RL-chosen combination.</p>
<p>feed_domain_seeds: P0-8: Domain seeds extracted from accepted feed findings.</p>
<p>When query has no domain indicator but feed findings contain domains,</p>
<p>these seeds enable CT/DOH/WAYBACK lanes mid-sprint.</p>
<p></p>
<p>Returns:</p>
<p>AcquisitionStrategySnapshot with per-lane plans.</p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p>- No network I/O</p>
<p>- No model/MLX load</p>
<p>- No asyncio.run() / loop.run_until_complete()</p>
<p>- Bounded: max 12 lane plans (all canonical acquisition lanes)</p>
<p>- Fail-soft: on any error returns minimal snapshot with all lanes disabled</p>
</div>
</details>
</li>
<li><code>_compose_provider_activation_note</code> (shadow_pre_decision.py)</li>
<li><code>record_hypothesis_feedback</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_run_cti_export</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_is_meaningful_run</code> (sprint_entrypoint.py)</li>
<li><code>normalize_terminal_state</code> (acquisition_strategy.py)
<details><summary>[F208L] Map an outcome dict to a canonical terminal state string.</summary>
<div class="doc-comment">
<p>[F208L] Map an outcome dict to a canonical terminal state string.</p>
<p></p>
<p>Supported terminal states:</p>
<p>- success       : attempted=True, accepted_count &gt; 0</p>
<p>- success_empty : attempted=True, raw_count &gt; 0, accepted_count = 0</p>
<p>- empty         : attempted=True, raw_count = 0, accepted_count = 0</p>
<p>- attempted     : attempted=True, no other qualifier</p>
<p>- skipped       : skipped=True</p>
<p>- error         : error is not None and not empty string</p>
<p>- timeout       : timeout=True</p>
<p></p>
<p>Non-terminal states (return as-is for identity check):</p>
<p>- pending</p>
<p>- running</p>
<p>- not_attempted</p>
<p>- missing</p>
<p>- ""  (empty string)</p>
<p>- None</p>
<p></p>
<p>accepted_count=0 alone does NOT make a lane non-terminal.</p>
<p>raw_count &gt; 0 with accepted_count = 0 normalizes to success_empty.</p>
<p>raw_count = 0 with attempted = True normalizes to empty.</p>
</div>
</details>
</li>
<li><code>required_terminal_lanes</code> (__init__.py)
<details><summary>[F208A] Determine which lanes are mandatory for terminality.</summary>
<div class="doc-comment">
<p>[F208A] Determine which lanes are mandatory for terminality.</p>
<p></p>
<p>Rules:</p>
<p>- domain query + ok/warn memory: PUBLIC required, CT required</p>
<p>- domain query + critical: CT required (as attempted or explicit skip),</p>
<p>PUBLIC explicit skip allowed with memory_critical</p>
<p>- emergency: all non-feed lanes explicit skip with memory_emergency</p>
<p>- non-domain: CT not required (skip reason no_domain)</p>
<p>- STEALTH: never required by default</p>
<p>- FEED: not part of terminality guard</p>
<p></p>
<p>Args:</p>
<p>snapshot:    Current acquisition strategy snapshot.</p>
<p>query:       Sprint query string.</p>
<p>uma_state:   Current UMA state (ok, warn, critical, emergency).</p>
<p>swap_detected: True if swap has been detected.</p>
<p></p>
<p>Returns:</p>
<p>Tuple of MandatoryLaneTerminality, one per lane that has terminality requirements.</p>
</div>
</details>
</li>
<li><code>normalize_terminal_state</code> (__init__.py)
<details><summary>[F208L] Map an outcome dict to a canonical terminal state string.</summary>
<div class="doc-comment">
<p>[F208L] Map an outcome dict to a canonical terminal state string.</p>
<p></p>
<p>Supported terminal states:</p>
<p>- success       : attempted=True, accepted_count &gt; 0</p>
<p>- success_empty : attempted=True, raw_count &gt; 0, accepted_count = 0</p>
<p>- empty         : attempted=True, raw_count = 0, accepted_count = 0</p>
<p>- attempted     : attempted=True, no other qualifier</p>
<p>- skipped       : skipped=True</p>
<p>- error         : error is not None and not empty string</p>
<p>- timeout       : timeout=True</p>
<p></p>
<p>Non-terminal states (return as-is for identity check):</p>
<p>- pending</p>
<p>- running</p>
<p>- not_attempted</p>
<p>- missing</p>
<p>- ""  (empty string)</p>
<p>- None</p>
<p></p>
<p>accepted_count=0 alone does NOT make a lane non-terminal.</p>
<p>raw_count &gt; 0 with accepted_count = 0 normalizes to success_empty.</p>
<p>raw_count = 0 with attempted = True normalizes to empty.</p>
</div>
</details>
</li>
<li><code>required_terminal_lanes</code> (acquisition_strategy.py)
<details><summary>[F208A] Determine which lanes are mandatory for terminality.</summary>
<div class="doc-comment">
<p>[F208A] Determine which lanes are mandatory for terminality.</p>
<p></p>
<p>Rules:</p>
<p>- domain query + ok/warn memory: PUBLIC required, CT required</p>
<p>- domain query + critical: CT required (as attempted or explicit skip),</p>
<p>PUBLIC explicit skip allowed with memory_critical</p>
<p>- emergency: all non-feed lanes explicit skip with memory_emergency</p>
<p>- non-domain: CT not required (skip reason no_domain)</p>
<p>- STEALTH: never required by default</p>
<p></p>
<p>Args:</p>
<p>snapshot:    Current acquisition strategy snapshot.</p>
<p>query:       Sprint query string.</p>
<p>uma_state:   Current UMA state (ok, warn, critical, emergency).</p>
<p>swap_detected: True if swap has been detected.</p>
<p></p>
<p>Returns:</p>
<p>Tuple of MandatoryLaneTerminality, one per lane that has terminality requirements.</p>
</div>
</details>
</li>
<li><code>dispatch_findings</code> (sidecar_orchestrator.py)</li>
<li><code>record_hypothesis_feedback</code> (scheduler.py)</li>
<li><code>_run_resource_governor_advisory</code> (sprint_advisory_runner.py)
<details><summary>F202J: Apply resource governor decision at TEARDOWN.</summary>
<div class="doc-comment">
<p>F202J: Apply resource governor decision at TEARDOWN.</p>
<p></p>
<p>Advisory only: governor evaluates and applies concurrency hints.</p>
<p>Sprint retains all authority.</p>
<p></p>
<p>F204J: Also tracks peak RSS and sidecars skipped for budget scorecard.</p>
<p></p>
<p>Fail-soft: errors never crash the runner.</p>
</div>
</details>
</li>
<li><code>compute</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_run_social_identity_surface_sidecar</code> (sprint_scheduler_v1_archived.py)
<details><summary>F204I: Social Identity Surface Miner — extract usernames/profiles from findings.</summary>
<div class="doc-comment">
<p>F204I: Social Identity Surface Miner — extract usernames/profiles from findings.</p>
<p></p>
<p>Wire point: called in WINDUP phase after all acquisition lanes complete.</p>
<p>Canonical execution path via SprintScheduler (not SidecarRegistry) to avoid</p>
<p>double-execution — the SidecarRegistry adapter is wiring-only (returns []).</p>
<p></p>
<p>Gates:</p>
<p>- HLEDAC_ENABLE_SOCIAL_IDENTITY_SURFACE=1 (default: 0, opt-in)</p>
<p>- duckdb_store is not None</p>
<p>- self._result.accepted_findings is not empty</p>
<p></p>
<p>Args:</p>
<p>query: Original sprint query string</p>
<p>duckdb_store: DuckDBShadowStore instance for canonical write</p>
</div>
</details>
</li>
<li><code>_build_public_stage_counters</code> (sprint_scheduler_v1_archived.py)
<details><summary>F208G-A: Build public_stage_counters dict from _public_pipeline_result.</summary>
<div class="doc-comment">
<p>F208G-A: Build public_stage_counters dict from _public_pipeline_result.</p>
<p></p>
<p></p>
<p></p>
<p>This aggregates all F208G-A public_* telemetry fields from the stored</p>
<p></p>
<p>PipelineRunResult into a single dict for propagation to acquisition_report</p>
<p></p>
<p>and source_family_outcomes.</p>
<p></p>
<p></p>
<p></p>
<p>Returns an empty dict if _public_pipeline_result is None (PUBLIC skipped).</p>
</div>
</details>
</li>
<li><code>summarize_rdap_conversion</code> (source_finding_bridge.py)</li>
<li><code>build_snapshot</code> (acquisition_strategy.py)
<details><summary>Build a NonfeedMissionSnapshot from current scheduler state.</summary>
<div class="doc-comment">
<p>Build a NonfeedMissionSnapshot from current scheduler state.</p>
<p></p>
<p>Args:</p>
<p>acquisition_profile: Current acquisition profile name</p>
<p>acquisition_lane_outcomes: Tuple of AcquisitionLaneOutcome from run_enabled_acquisition_lanes</p>
<p>public_outcome: _public_outcome dict from SprintScheduler (None if PUBLIC never ran)</p>
<p>ct_quarantine_count: ct_quarantine_count from SprintSchedulerResult</p>
<p>quality_rejection_ledger: quality_rejection_ledger from SprintSchedulerResult</p>
<p>memory_skipped_families: Families skipped due to memory pressure</p>
</div>
</details>
</li>
<li><code>build_snapshot</code> (__init__.py)
<details><summary>Build a NonfeedMissionSnapshot from current scheduler state.</summary>
<div class="doc-comment">
<p>Build a NonfeedMissionSnapshot from current scheduler state.</p>
<p></p>
<p>Args:</p>
<p>acquisition_profile: Current acquisition profile name</p>
<p>acquisition_lane_outcomes: Tuple of AcquisitionLaneOutcome from run_enabled_acquisition_lanes</p>
<p>public_outcome: _public_outcome dict from SprintScheduler (None if PUBLIC never ran)</p>
<p>ct_quarantine_count: ct_quarantine_count from SprintSchedulerResult</p>
<p>quality_rejection_ledger: quality_rejection_ledger from SprintSchedulerResult</p>
<p>memory_skipped_families: Families skipped due to memory pressure</p>
</div>
</details>
</li>
<li><code>_compose_lifecycle_interpretation</code> (shadow_pre_decision.py)</li>
<li><code>_run_prelude</code> (scheduler.py) — <span class="doc-comment-inline">Run the prelude phase via PreludeOrchestrator.</span></li>
<li><code>_run_wayback_lane</code> (__init__.py)
<details><summary>Run Wayback diff mining lane — runtime safety check before network call.</summary>
<div class="doc-comment">
<p>Run Wayback diff mining lane — runtime safety check before network call.</p>
<p></p>
<p>[F207K-A] Uses bridge helpers to produce CanonicalFinding candidates</p>
<p>with rejection tracking.</p>
</div>
</details>
</li>
<li><code>_run_pdns_lane</code> (__init__.py)
<details><summary>Run passive DNS lookup lane — wired to call_lookup_passive_dns with domain/IP shaping.</summary>
<div class="doc-comment">
<p>Run passive DNS lookup lane — wired to call_lookup_passive_dns with domain/IP shaping.</p>
<p></p>
<p>[F207K-A] Uses bridge helpers to produce CanonicalFinding candidates</p>
<p>with rejection tracking.</p>
</div>
</details>
</li>
<li><code>should_enter_windup</code> (sprint_lifecycle.py)
<details><summary>True when remaining time is at or below the windup lead threshold.</summary>
<div class="doc-comment">
<p>True when remaining time is at or below the windup lead threshold.</p>
<p></p>
<p>F288: When pre_loop_cost_s &gt; windup_lead_s (measured at runtime),</p>
<p>the effective trigger is raised to windup_lead_s + pre_loop_cost_s.</p>
<p>This ensures at least one full acquisition cycle completes before</p>
<p>windup fires — even when init cost exceeded the static windup_lead_s.</p>
<p></p>
<p>F289: HARD MINIMUM — never return True if remaining time would leave</p>
<p>less than 30s of active work. This prevents "instant windup" where</p>
<p>windup_lead_s is set too close to sprint_duration (e.g. 450s windup</p>
<p>lead on a 460s sprint leaves only 10s of actual work).</p>
</div>
</details>
</li>
<li><code>_feed_dominance_record_result</code> (sprint_scheduler_v1_archived.py)
<details><summary>F216E: Record feed result into budget telemetry.</summary>
<div class="doc-comment">
<p>F216E: Record feed result into budget telemetry.</p>
<p></p>
<p></p>
<p></p>
<p>F230D: Also records nonfeed_budget telemetry when nonfeed_diagnostic profile active.</p>
</div>
</details>
</li>
<li><code>_maybe_export_partial</code> (sprint_scheduler_v1_archived.py)
<details><summary>Write a partial JSON artifact if the findings interval has been reached.</summary>
<div class="doc-comment">
<p>Write a partial JSON artifact if the findings interval has been reached.</p>
<p></p>
<p></p>
<p></p>
<p>Called every cycle in aggressive mode.  Also callable on early windup</p>
<p></p>
<p>or abort to ensure the latest partial survives.</p>
</div>
</details>
</li>
<li><code>_generate_pivots_for_ioc</code> (pivot_planner.py) — <span class="doc-comment-inline">Generate pivots for a single IOC.</span></li>
<li><code>lane_admission</code> (resource_governor.py)
<details><summary>F214R: Canonical lane admission check.</summary>
<div class="doc-comment">
<p>F214R: Canonical lane admission check.</p>
<p></p>
<p>Returns LaneAdmission with:</p>
<p>- allowed: True if lane can be admitted</p>
<p>- reason: human-readable denial reason</p>
<p>- uma_state: current UMA state</p>
<p>- risk_level: the risk level that was evaluated</p>
<p></p>
<p>risk_level: "low" | "medium" | "high" | "critical"</p>
<p>Heavy lanes (high/critical risk) are blocked under critical/emergency UMA.</p>
<p>Fail-soft: returns allowed=True on errors.</p>
</div>
</details>
</li>
<li><code>_run_analyst_brief_advisory</code> (sprint_advisory_runner.py)
<details><summary>F204E/F205J: Generate analyst brief at TEARDOWN.</summary>
<div class="doc-comment">
<p>F204E/F205J: Generate analyst brief at TEARDOWN.</p>
<p></p>
<p>Uses canonical target_id (query or duckdb_store lookup) instead of</p>
<p>sprint_id, enabling cross-sprint target memory reads.</p>
<p></p>
<p>Advisory only: brief summarizes sprint results but does not affect</p>
<p>sprint execution or outcomes. Sprint retains all authority.</p>
<p></p>
<p>Fail-soft: errors never crash the runner.</p>
<p></p>
<p>Stores brief in scheduler._analyst_brief for export hookup.</p>
</div>
</details>
</li>
<li><code>_configure_gc_for_sprint</code> (sprint_entrypoint.py)
<details><summary>Configure Python GC for sprint workload.</summary>
<div class="doc-comment">
<p>Configure Python GC for sprint workload.</p>
<p></p>
<p>Called once at sprint boot. Freezes GC to reduce pause variance on M1.</p>
<p>Sets threshold to (1000, 50, 50) to reduce collection frequency.</p>
<p>Opt-out via HLEDAC_DISABLE_GC_FREEZE=1.</p>
<p></p>
<p>Returns a dict with telemetry fields.</p>
</div>
</details>
</li>
<li><code>run_async</code> (sidecar_protocol_adapters.py)
<details><summary>Run ShadowWalker path prediction for the sprint query.</summary>
<div class="doc-comment">
<p>Run ShadowWalker path prediction for the sprint query.</p>
<p></p>
<p>1. Extract base URL from query</p>
<p>2. Run ShadowWalkerAlgorithm to predict hidden paths</p>
<p>3. Convert predictions to findings</p>
</div>
</details>
</li>
<li><code>_sprint_diff_runner</code> (sidecar_bus.py) — <span class="doc-comment-inline">F203A cross-sprint diff — heavy, RAM-guarded by bus.</span></li>
<li><code>_run_pdns_prelude_lane</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_resolve_attr</code> (sprint_scheduler_v1_archived.py)
<details><summary>Resolve the normalized attr name for `name` on _lc, cached in instance __dict__.</summary>
<div class="doc-comment">
<p>Resolve the normalized attr name for `name` on _lc, cached in instance __dict__.</p>
<p></p>
<p>Falls back through multiple candidate names to handle API differences</p>
<p>between runtime/ and utils/ lifecycle implementations.</p>
</div>
</details>
</li>
<li><code>_run_gopher_sidecar</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F214R: Gopher/Veronica-2 discovery via Floodgap proxy.</summary>
<div class="doc-comment">
<p>Sprint F214R: Gopher/Veronica-2 discovery via Floodgap proxy.</p>
<p>Gate: HLEDAC_ENABLE_GOPHER=1, max_items=50, timeout=30s.</p>
<p>Fail-soft: Gopher errors logged, never crash sprint.</p>
</div>
</details>
</li>
<li><code>canonicalize_source_family_outcomes</code> (acquisition_strategy.py)
<details><summary>Deduplicate and merge source family outcomes that normalize to the same family.</summary>
<div class="doc-comment">
<p>Deduplicate and merge source family outcomes that normalize to the same family.</p>
<p></p>
<p>When multiple outcomes normalize to the same family name (e.g., "CT" and "ct"),</p>
<p>they are merged into a single outcome using the merge rules:</p>
<p>- attempted = any(attempted=True)</p>
<p>- skipped   = all(skipped) only if no outcome was attempted; otherwise False</p>
<p>- timeout   = any(timeout=True)</p>
<p>- error     = prefer real provider/runtime error over synthetic "no_candidates"</p>
<p>- terminal_state = highest-priority from TERMINAL_PRIORITY table</p>
<p>- raw_count / built_count / accepted_count = max of all</p>
<p>- duration_s = max non-null duration</p>
</div>
</details>
</li>
<li><code>canonicalize_source_family_outcomes</code> (__init__.py)
<details><summary>Deduplicate and merge source family outcomes that normalize to the same family.</summary>
<div class="doc-comment">
<p>Deduplicate and merge source family outcomes that normalize to the same family.</p>
<p></p>
<p>When multiple outcomes normalize to the same family name (e.g., "CT" and "ct"),</p>
<p>they are merged into a single outcome using the merge rules:</p>
<p>- attempted = any(attempted=True)</p>
<p>- skipped   = all(skipped) only if no outcome was attempted; otherwise False</p>
<p>- timeout   = any(timeout=True)</p>
<p>- error     = prefer real provider/runtime error over synthetic "no_candidates"</p>
<p>- terminal_state = highest-priority from TERMINAL_PRIORITY table</p>
<p>- raw_count / built_count / accepted_count = max of all</p>
<p>- duration_s = max non-null duration</p>
</div>
</details>
</li>
<li><code>_run_ct_lane</code> (__init__.py)
<details><summary>Run CT/crt.sh lane — wired to call_crtsh() for measurable outcome.</summary>
<div class="doc-comment">
<p>Run CT/crt.sh lane — wired to call_crtsh() for measurable outcome.</p>
<p></p>
<p>[F207K-A] Uses bridge helpers to produce CanonicalFinding candidates</p>
<p>with rejection tracking. DB write is the lane runner's job (not adapter).</p>
</div>
</details>
</li>
<li><code>run_async</code> (sidecar_protocol_adapters.py) — <span class="doc-comment-inline">Search GitHub Gists for OSINT signals based on query and findings.</span></li>
<li><code>rank_candidates</code> (nonfeed_candidate_ledger.py)
<details><summary>F214: Rank and bound domain candidates for lane planner input.</summary>
<div class="doc-comment">
<p>F214: Rank and bound domain candidates for lane planner input.</p>
<p></p>
<p>Ranking priority (highest first):</p>
<p>1. body-extracted domains (confidence 0.7, likely target IOCs)</p>
<p>2. title-extracted domains</p>
<p>3. url-extracted domains (may include source infrastructure)</p>
<p>4. source_host_only candidates (deprioritized unless only option)</p>
<p></p>
<p>Source-host filtering:</p>
<p>- Domains that appear ONLY in source_url hostname (not in body/text)</p>
<p>are flagged as source_host_only and ranked last.</p>
<p>- This prevents krebsonsecurity.com from becoming a target candidate</p>
<p>when it appears only as a source URL.</p>
<p></p>
<p>Args:</p>
<p>candidates:       List of DomainCandidate to rank.</p>
<p>max_total:        Maximum candidates to return.</p>
<p>source_host_domains: Optional frozenset of domains that appear ONLY as</p>
<p>source URL hostnames (will be ranked last).</p>
<p></p>
<p>Returns:</p>
<p>Bounded, ranked list of candidates (top max_total).</p>
</div>
</details>
</li>
<li><code>run</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_prewarm_all_models</code> (sprint_scheduler_v1_archived.py)
<details><summary>Load all MLX models concurrently in shared event loop.</summary>
<div class="doc-comment">
<p>Load all MLX models concurrently in shared event loop.</p>
<p></p>
<p>Uses asyncio.gather for true parallelism - loop stays in one thread,</p>
<p>but all three model loads run concurrently via run_in_executor.</p>
<p></p>
<p>F320: Skip entirely if prewarm_daemon already loaded models at startup.</p>
</div>
</details>
</li>
<li><code>_attempt_ct_prewindup_barrier</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F207Q-A: Attempt CT lane as part of pre-windup barrier.</summary>
<div class="doc-comment">
<p>Sprint F207Q-A: Attempt CT lane as part of pre-windup barrier.</p>
<p></p>
<p></p>
<p></p>
<p>Args:</p>
<p></p>
<p>query: Sprint query for lane query shaping.</p>
<p></p>
<p></p>
<p></p>
<p>Returns dict with keys: attempted, error, timeout, or None on exception.</p>
<p></p>
<p>Uses tiny bounds (max 5 results, 15s timeout).</p>
</div>
</details>
</li>
<li><code>write_sprint_delta</code> (sprint_entrypoint.py)</li>
<li><code>_install_signal_handler_for_loop</code> (sprint_entrypoint.py)</li>
<li><code>_run_doh_lane</code> (__init__.py)
<details><summary>Run DOH lane — DNS-over-HTTPS passive DNS recon via DOHAdapter.</summary>
<div class="doc-comment">
<p>Run DOH lane — DNS-over-HTTPS passive DNS recon via DOHAdapter.</p>
<p></p>
<p>F222B: First-class nonfeed lane. No model load, no browser, no stealth.</p>
<p>Bounds: max_items=20, timeout_s=30, concurrency=2.</p>
<p>Fail-soft: provider errors never break other lanes.</p>
</div>
</details>
</li>
<li><code>_compose_tool_readiness_preview</code> (shadow_pre_decision.py)</li>
<li><code>_embedding_runner</code> (sidecar_bus.py) — <span class="doc-comment-inline">F203I streaming embedding — heavy, RAM-guarded by bus. Stores to ANN index.</span></li>
<li><code>_teardown_sprint</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Phase D: Teardown - cleanup resources at sprint end (tracemalloc, GC, privacy context).</span></li>
<li><code>_disabled_reason</code> (acquisition_strategy.py) — <span class="doc-comment-inline">Return the disabled-reason string for a lane, matching original inline logic.</span></li>
<li><code>terminality_report</code> (acquisition_strategy.py)
<details><summary>[F208A] Produce a terminality report comparing required vs observed lane states.</summary>
<div class="doc-comment">
<p>[F208A] Produce a terminality report comparing required vs observed lane states.</p>
<p></p>
<p>Args:</p>
<p>required_lanes:    Tuple of MandatoryLaneTerminality from required_terminal_lanes().</p>
<p>observed_outcomes: Tuple of outcome dicts (from AcquisitionLaneOutcome.to_dict()).</p>
<p></p>
<p>Returns:</p>
<p>Dict with:</p>
<p>checked: list of lane names checked</p>
<p>satisfied: list of lane names with terminal outcomes</p>
<p>required_lanes: list of mandatory lane specs</p>
<p>terminal_lanes: list of lanes at terminal state</p>
<p>missing_lanes: list of mandatory lanes NOT at terminal state</p>
<p>skipped_lanes: list of lanes that were skipped</p>
<p>errors: list of lanes with errors</p>
<p>reasons: dict mapping lane → terminality reason string</p>
</div>
</details>
</li>
<li><code>_disabled_reason</code> (__init__.py) — <span class="doc-comment-inline">Return the disabled-reason string for a lane, matching original inline logic.</span></li>
<li><code>terminality_report</code> (__init__.py)
<details><summary>[F208A] Produce a terminality report comparing required vs observed lane states.</summary>
<div class="doc-comment">
<p>[F208A] Produce a terminality report comparing required vs observed lane states.</p>
<p></p>
<p>Args:</p>
<p>required_lanes:    Tuple of MandatoryLaneTerminality from required_terminal_lanes().</p>
<p>observed_outcomes: Tuple of outcome dicts (from AcquisitionLaneOutcome.to_dict()).</p>
<p></p>
<p>Returns:</p>
<p>Dict with:</p>
<p>checked: list of lane names checked</p>
<p>satisfied: list of lane names with terminal outcomes</p>
<p>required_lanes: list of mandatory lane specs</p>
<p>terminal_lanes: list of lanes at terminal state</p>
<p>missing_lanes: list of mandatory lanes NOT at terminal state</p>
<p>skipped_lanes: list of lanes that were skipped</p>
<p>errors: list of lanes with errors</p>
<p>reasons: dict mapping lane → terminality reason string</p>
</div>
</details>
</li>
<li><code>_compose_windup_readiness_preview</code> (shadow_pre_decision.py)</li>
<li><code>_dispatch_plugin_sidecar</code> (sidecar_orchestrator.py)</li>
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
<li><code>extract_domain_candidates_from_finding</code> (nonfeed_candidate_ledger.py)
<details><summary>F214: Extract domain candidates from a CanonicalFinding-like object.</summary>
<div class="doc-comment">
<p>F214: Extract domain candidates from a CanonicalFinding-like object.</p>
<p></p>
<p>Scans: finding.payload_text, finding.query (as URL), source_url from provenance.</p>
<p></p>
<p>Args:</p>
<p>finding:  CanonicalFinding or dict with payload_text / query fields</p>
<p>source_family: "PUBLIC" or "FEED"</p>
<p></p>
<p>Returns:</p>
<p>List of DomainCandidate, deduplicated.</p>
</div>
</details>
</li>
<li><code>_run_ct_branch</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">CT log discovery branch with remaining-time-aware asyncio.timeout.</span></li>
<li><code>fetch_i2p_address</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Fetch single .i2p address and convert to CanonicalFinding list.</span></li>
<li><code>_get_lane_outcome</code> (acquisition_strategy.py)
<details><summary>Get the outcome dict for a lane family.</summary>
<div class="doc-comment">
<p>Get the outcome dict for a lane family.</p>
<p></p>
<p>Returns a dict with keys: accepted_findings, terminal_state, error, skipped</p>
<p>suitable for mission evaluation.</p>
<p></p>
<p>Args:</p>
<p>family: Lane family string (PUBLIC, CT, etc.)</p>
<p>acquisition_lane_outcomes: Tuple of AcquisitionLaneOutcome from run_enabled_acquisition_lanes</p>
<p>public_outcome: _public_outcome dict from SprintScheduler (for PUBLIC lane)</p>
<p>ct_quarantine_count: ct_quarantine_count from SprintSchedulerResult</p>
<p>quality_rejection_ledger: quality_rejection_ledger from SprintSchedulerResult</p>
</div>
</details>
</li>
<li><code>_get_lane_outcome</code> (__init__.py)
<details><summary>Get the outcome dict for a lane family.</summary>
<div class="doc-comment">
<p>Get the outcome dict for a lane family.</p>
<p></p>
<p>Returns a dict with keys: accepted_findings, terminal_state, error, skipped</p>
<p>suitable for mission evaluation.</p>
<p></p>
<p>Args:</p>
<p>family: Lane family string (PUBLIC, CT, etc.)</p>
<p>acquisition_lane_outcomes: Tuple of AcquisitionLaneOutcome from run_enabled_acquisition_lanes</p>
<p>public_outcome: _public_outcome dict from SprintScheduler (for PUBLIC lane)</p>
<p>ct_quarantine_count: ct_quarantine_count from SprintSchedulerResult</p>
<p>quality_rejection_ledger: quality_rejection_ledger from SprintSchedulerResult</p>
</div>
</details>
</li>
<li><code>run_async</code> (sidecar_protocol_adapters.py) — <span class="doc-comment-inline">Search academic sources for research papers matching query.</span></li>
<li><code>branch_admission</code> (resource_governor.py)
<details><summary>F214R: Canonical branch admission check.</summary>
<div class="doc-comment">
<p>F214R: Canonical branch admission check.</p>
<p>@pending_integration: no confirmed production call sites as of F214R audit.</p>
<p>See tests/test_resource_governor_authority_seal.py::TestPendingIntegrationMarkers.</p>
<p></p>
<p>Returns BranchAdmission with:</p>
<p>- allowed: True if branch can run</p>
<p>- reason: human-readable denial reason</p>
<p>- uma_state: current UMA state</p>
<p>- branch_concurrency: recommended concurrency for this branch</p>
<p>- estimated_mb: the estimate that was evaluated</p>
<p></p>
<p>Fail-soft: returns allowed=True with normal concurrency on errors.</p>
</div>
</details>
</li>
<li><code>_check_hard_deadline</code> (sprint_scheduler_v1_archived.py)
<details><summary>Check if the hard monotonic deadline has been exceeded.</summary>
<div class="doc-comment">
<p>Check if the hard monotonic deadline has been exceeded.</p>
<p></p>
<p></p>
<p></p>
<p>Returns:</p>
<p></p>
<p>True if deadline is NOT exceeded (new work may proceed).</p>
<p></p>
<p>False if deadline IS exceeded (no new branch dispatch).</p>
<p></p>
<p></p>
<p></p>
<p>This method is idempotent -- it can be called multiple times per cycle</p>
<p></p>
<p>without changing state. Deadline-exceeded state is tracked once in</p>
<p></p>
<p>the result and never reset.</p>
</div>
</details>
</li>
<li><code>_run_one_cycle</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_memory_pressure_loop</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Background task -- adjusts concurrency based on memory pressure.</span></li>
<li><code>_extract_ioc_from_finding</code> (pivot_planner.py)
<details><summary>Extract IOC value and type from a finding.</summary>
<div class="doc-comment">
<p>Extract IOC value and type from a finding.</p>
<p></p>
<p>Returns (ioc_value, ioc_type) or (None, None).</p>
<p></p>
<p>Extraction order (most specific first):</p>
<p>1. URL (has :// prefix)</p>
<p>2. Email (has @)</p>
<p>3. IP (specific pattern)</p>
<p>4. Hash (specific length)</p>
<p>5. Domain (generic fallback)</p>
</div>
</details>
</li>
<li><code>_ensure_initialized</code> (role_based_pools.py) — <span class="doc-comment-inline">Lazy initialization of all executors (double-checked locking).</span></li>
<li><code>_run_local_search_advisory</code> (sprint_advisory_runner.py)
<details><summary>F228C: Local search advisory at teardown.</summary>
<div class="doc-comment">
<p>F228C: Local search advisory at teardown.</p>
<p></p>
<p>Indexes accepted findings into LocalSearchSeam (advisory-only, no</p>
<p>canonical writes, no persistent DB). Then searches them with the</p>
<p>sprint query to surface relevant evidence for research context.</p>
<p></p>
<p>Bounded, fail-soft, no network, no model load.</p>
<p></p>
<p>Telemetry fields in AdvisoryRunOutcome:</p>
<p>local_search_attempted: True if seam was queried</p>
<p>local_search_hits: Number of top results returned</p>
<p>local_search_indexed: Number of findings indexed</p>
<p>local_search_source: "search_index" or "none"</p>
<p>local_search_elapsed_ms: Wall time of index+search</p>
<p>local_search_top_results: list[dict] with url/title/score/source_type/finding_id</p>
<p>local_search_error: Error string if failed, else None</p>
</div>
</details>
</li>
<li><code>_attempt_public_prewindup_barrier</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F207Q-A: Attempt PUBLIC lane as part of pre-windup barrier.</summary>
<div class="doc-comment">
<p>Sprint F207Q-A: Attempt PUBLIC lane as part of pre-windup barrier.</p>
<p></p>
<p></p>
<p></p>
<p>Args:</p>
<p></p>
<p>query: Sprint query for lane query shaping.</p>
<p></p>
<p></p>
<p></p>
<p>Returns dict with keys: attempted, error, timeout, or None on exception.</p>
<p></p>
<p>Uses tiny bounds (max 3 results, 10s timeout).</p>
</div>
</details>
</li>
<li><code>_run_ioc_cooccurrence_sidecar</code> (sprint_scheduler_v1_archived.py)
<details><summary>Issue 4.1: Run IOC co-occurrence analysis on accumulated findings.</summary>
<div class="doc-comment">
<p>Issue 4.1: Run IOC co-occurrence analysis on accumulated findings.</p>
<p></p>
<p>Wired in WINDUP phase — runs after all acquisition lanes complete so the</p>
<p>full finding set is available. Uses:</p>
<p>- Rust engine (compute_cooccurrence_edges_py) via asyncio.to_thread()</p>
<p>- msgspec.to_builtins() for cheap serialization</p>
<p></p>
<p>Architecture:</p>
<p>finding_pipeline (async enrich+store) ∥ live_public_pipeline ∥ IOCooccurrenceMiner</p>
<p></p>
<p>M1 8GB: asyncio.to_thread() runs Rust engine without blocking event loop.</p>
<p>No ProcessPoolExecutor — rayon CPU pool handles multi-core parallelism.</p>
</div>
</details>
</li>
<li><code>record_ct_storage_results</code> (source_finding_bridge.py)</li>
<li><code>_derive_terminal_stage</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_init_fetch_coordinator</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Phase K: FetchCoordinator instantiation with provider lambdas + DNS prewarm.</span></li>
<li><code>_parallel_ingest</code> (sprint_scheduler_v1_archived.py)
<details><summary>Bounded parallel ingest: chunk → TaskGroup → single mx.eval barrier.</summary>
<div class="doc-comment">
<p>Bounded parallel ingest: chunk → TaskGroup → single mx.eval barrier.</p>
<p></p>
<p>F320M-R FIX: Sequential for-loop replaced with asyncio.TaskGroup for TRUE parallelism.</p>
<p>Previously chunks ran sequentially even though a Semaphore existed — the await inside</p>
<p>the for-loop blocked until each chunk completed before starting the next.</p>
<p></p>
<p>M1 8GB: max _MAX_CHUNK_CONCURRENCY concurrent chunks, single Metal memory barrier after all.</p>
<p>Returns canonical results (same as async_ingest_findings_batch).</p>
</div>
</details>
</li>
<li><code>_speculative_prefetch</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Spustit top-n pivot tasků spekulativně jako background tasks.</span></li>
<li><code>_run_wayback_lane</code> (acquisition_strategy.py)
<details><summary>Run Wayback diff mining lane — runtime safety check before network call.</summary>
<div class="doc-comment">
<p>Run Wayback diff mining lane — runtime safety check before network call.</p>
<p></p>
<p>[F207K-A] Uses bridge helpers to produce CanonicalFinding candidates</p>
<p>with rejection tracking.</p>
</div>
</details>
</li>
<li><code>_run_pdns_lane</code> (acquisition_strategy.py)
<details><summary>Run passive DNS lookup lane — wired to call_lookup_passive_dns with domain/IP shaping.</summary>
<div class="doc-comment">
<p>Run passive DNS lookup lane — wired to call_lookup_passive_dns with domain/IP shaping.</p>
<p></p>
<p>[F207K-A] Uses bridge helpers to produce CanonicalFinding candidates</p>
<p>with rejection tracking.</p>
</div>
</details>
</li>
<li><code>select_ct_domains_for_passivedns_pivot</code> (acquisition_strategy.py)
<details><summary>Sprint R5: Extract deduplicated domains from CT-accepted CanonicalFinding</summary>
<div class="doc-comment">
<p>Sprint R5: Extract deduplicated domains from CT-accepted CanonicalFinding</p>
<p>candidates for PassiveDNS one-hop pivot.</p>
<p></p>
<p>Pure function: deterministic output from deterministic input.</p>
<p>No network I/O, no side effects.</p>
<p></p>
<p>Args:</p>
<p>ct_candidate_findings: List of CanonicalFinding (or dict-like) objects</p>
<p>with source_type="ct" and payload_text containing domain lines.</p>
<p>max_pivots: Default cap on pivot domains (default=5, hard_max=10).</p>
<p></p>
<p>Returns:</p>
<p>Deduplicated list of domain strings (max 10), in first-seen order.</p>
<p></p>
<p>Invariants:</p>
<p>- pivot depth = 1 (caller enforces)</p>
<p>- no recursive pivoting</p>
<p>- no network I/O</p>
<p>- no new queue framework</p>
<p>- deterministic: same input always yields same output</p>
<p></p>
<p>Domain extraction:</p>
<p>- Parse "domain: &lt;value&gt;" lines from payload_text</p>
<p>- Fallback: query field if no domain line found</p>
<p>- Skip: empty/whitespace-only domains</p>
<p>- Order: first-seen (dict.fromkeys preserves insertion order)</p>
</div>
</details>
</li>
<li><code>select_ct_domains_for_passivedns_pivot</code> (__init__.py)
<details><summary>Sprint R5: Extract deduplicated domains from CT-accepted CanonicalFinding</summary>
<div class="doc-comment">
<p>Sprint R5: Extract deduplicated domains from CT-accepted CanonicalFinding</p>
<p>candidates for PassiveDNS one-hop pivot.</p>
<p></p>
<p>Pure function: deterministic output from deterministic input.</p>
<p>No network I/O, no side effects.</p>
<p></p>
<p>Args:</p>
<p>ct_candidate_findings: List of CanonicalFinding (or dict-like) objects</p>
<p>with source_type="ct" and payload_text containing domain lines.</p>
<p>max_pivots: Default cap on pivot domains (default=5, hard_max=10).</p>
<p></p>
<p>Returns:</p>
<p>Deduplicated list of domain strings (max 10), in first-seen order.</p>
<p></p>
<p>Invariants:</p>
<p>- pivot depth = 1 (caller enforces)</p>
<p>- no recursive pivoting</p>
<p>- no network I/O</p>
<p>- no new queue framework</p>
<p>- deterministic: same input always yields same output</p>
<p></p>
<p>Domain extraction:</p>
<p>- Parse "domain: &lt;value&gt;" lines from payload_text</p>
<p>- Fallback: query field if no domain line found</p>
<p>- Skip: empty/whitespace-only domains</p>
<p>- Order: first-seen (dict.fromkeys preserves insertion order)</p>
</div>
</details>
</li>
<li><code>run_async</code> (sidecar_protocol_adapters.py) — <span class="doc-comment-inline">Scan for leaked credentials/data related to query.</span></li>
<li><code>_gate_then_ingest</code> (sprint_scheduler_v1_archived.py)
<details><summary>F285: PII gate + canonical write for feed lanes.</summary>
<div class="doc-comment">
<p>F285: PII gate + canonical write for feed lanes.</p>
<p></p>
<p>When HLEDAC_ENABLE_PRIVACY_LAYER=1, anonymizes PII in</p>
<p>content/raw_content/payload_text/title/summary BEFORE the</p>
<p>findings hit async_ingest_findings_batch.</p>
<p></p>
<p>Fail-soft: never raises. On any error, findings pass through</p>
<p>to the canonical write path unmodified.</p>
<p></p>
<p>Args:</p>
<p>store: duckdb_store (or any object with</p>
<p>async_ingest_findings_batch). None -&gt; no-op.</p>
<p>findings: list of CanonicalFinding (or duckdb-compatible</p>
<p>dicts). Empty -&gt; no-op.</p>
<p></p>
<p>Returns:</p>
<p>Whatever async_ingest_findings_batch returns, or None on</p>
<p>skip / error.</p>
</div>
</details>
</li>
<li><code>_speculative_dns_prefetch</code> (sprint_scheduler_v1_archived.py)
<details><summary>Fire-and-forget DNS resolution for top-k domain candidates.</summary>
<div class="doc-comment">
<p>Fire-and-forget DNS resolution for top-k domain candidates.</p>
<p></p>
<p>Runs as background task while fetch loop is active -- overlaps</p>
<p>DNS latency (~5-50ms) with ongoing network I/O.</p>
<p></p>
<p>Results stored in _speculative_dns_cache for later pivot planning.</p>
<p>Fail-soft: any error silently ignored, cache miss treated as "unresolved".</p>
<p></p>
<p>Args:</p>
<p>domains: List of domain strings to prefetch</p>
</div>
</details>
</li>
<li><code>_print_dry_run_summary</code> (sprint_entrypoint.py) — <span class="doc-comment-inline">Print human-readable dry-run summary to console.</span></li>
<li><code>_run_academic_lane</code> (__init__.py) — <span class="doc-comment-inline">Run academic search lane — R9: bounded, research-profile-only, no query expansion.</span></li>
<li><code>_run_acquisition_loop</code> (scheduler.py)
<details><summary>Run acquisition cycles until terminal via AcquisitionOrchestrator.</summary>
<div class="doc-comment">
<p>Run acquisition cycles until terminal via AcquisitionOrchestrator.</p>
<p></p>
<p>Corresponds to v1's while-not-terminal loop (lines ~7894-8300+).</p>
</div>
</details>
</li>
<li><code>_generate_pivots_from_findings</code> (pivot_planner.py)
<details><summary>Issue #17: Single-pass pivot generation from findings.</summary>
<div class="doc-comment">
<p>Issue #17: Single-pass pivot generation from findings.</p>
<p></p>
<p>Optional hermes_boost_map allows boosting heuristic pivots with Hermes scores</p>
<p>in a single pass, instead of iterating findings twice.</p>
<p></p>
<p>Args:</p>
<p>findings: List of findings to process</p>
<p>graph_stats: Optional graph statistics for scoring</p>
<p>feedback_summary: Optional feedback penalties</p>
<p>hermes_boost_map: Optional Hermes boost map (pivot_key → boost_score)</p>
<p>hermes_pivot_info: Optional Hermes pivot metadata (pivot_key → info_dict)</p>
<p></p>
<p>Note: caller handles max_pivots cap via slice after sort.</p>
</div>
</details>
</li>
<li><code>evaluate_adaptive</code> (resource_governor.py)
<details><summary>F2-2: EMA-adaptive governor evaluation.</summary>
<div class="doc-comment">
<p>F2-2: EMA-adaptive governor evaluation.</p>
<p></p>
<p>Runs the base evaluate() logic, then applies EMA timeout pressure override</p>
<p>on top of branch_concurrency only. The EMA tracks sustained timeout</p>
<p>pressure (0.0 = no pressure, 1.0 = continuous timeouts) and degrades</p>
<p>branch concurrency accordingly before the base UMA state would.</p>
<p></p>
<p>This is additive — it does NOT replace evaluate(). The EMA override is</p>
<p>applied as a post-processing step to the base decision's branch_concurrency.</p>
<p></p>
<p>EMA thresholds:</p>
<p>ema &gt; 0.7  → sustained high pressure  → branch_concurrency = 1</p>
<p>ema &gt; 0.4  → medium pressure          → branch_concurrency = min(base, 2)</p>
<p>ema ≤ 0.4   → no/low pressure         → branch_concurrency unchanged</p>
<p></p>
<p>Fails soft: falls back to safe defaults on any error.</p>
</div>
</details>
</li>
<li><code>_extract_keywords_for_search</code> (nonfeed_seed_runtime.py)
<details><summary>P1-2: Extract OSINT-relevant keywords from a broad threat query for</summary>
<div class="doc-comment">
<p>P1-2: Extract OSINT-relevant keywords from a broad threat query for</p>
<p>keyword-based cross-sprint DuckDB search.</p>
<p></p>
<p>Filters out stopwords and short tokens, returns up to 8 meaningful</p>
<p>keywords that improve recall in cross-sprint seed extraction.</p>
<p></p>
<p>Args:</p>
<p>query: Broad threat query string.</p>
<p></p>
<p>Returns:</p>
<p>List of up to 8 OSINT-relevant keywords.</p>
</div>
</details>
</li>
<li><code>_build_deep_security_config</code> (sprint_scheduler_v1_archived.py)
<details><summary>DS4: Mode-aware DeepSecurityConfig factory. research=conservative, aggressive=stricter.</summary>
<div class="doc-comment">
<p>DS4: Mode-aware DeepSecurityConfig factory. research=conservative, aggressive=stricter.</p>
<p></p>
<p>DS1+DS2+DS3 audit fix: use privacy_level to drive the cascade.</p>
<p>- "medium" activates obfuscation + chaff, but does NOT force heavy crypto.</p>
<p>- "low" activates audit only, no heavy ops at all.</p>
<p>- privacy_level="maximum" SILENTLY forces enable_quantum_safe=True +</p>
<p>enable_steganography=True via _apply_privacy_level() — M1 8GB killer.</p>
<p>Dead flags (enable_anti_fingerprinting, enable_request_signing,</p>
<p>enable_zero_knowledge) do not exist in DeepSecurityConfig — Python</p>
<p>silently ignores them, so they are removed from this factory entirely.</p>
</div>
</details>
</li>
<li><code>_capture_timing_fields</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F215D: Capture wall-clock timing fields for early-exit classification.</summary>
<div class="doc-comment">
<p>Sprint F215D: Capture wall-clock timing fields for early-exit classification.</p>
<p></p>
<p></p>
<p></p>
<p>Called before _finalize_result_truth in early-exit break paths so that</p>
<p></p>
<p>_compute_early_exit_class has correct elapsed_pct (not 0.0).</p>
<p></p>
<p>Timing is also captured at the normal-completion path (lines 1843-1859).</p>
</div>
</details>
</li>
<li><code>_adapt_source_weights_from_feedback_python</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_sensitive_query_transport</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F250: Return preferred transport for sensitive queries.</summary>
<div class="doc-comment">
<p>Sprint F250: Return preferred transport for sensitive queries.</p>
<p></p>
<p>Priority: Nym &gt; Tor &gt; I2P &gt; clearnet.</p>
<p></p>
<p>Returns transport name string or "clearnet" fallback.</p>
</div>
</details>
</li>
<li><code>summarize_bridge_conversion</code> (source_finding_bridge.py)</li>
<li><code>normalize_passive_dns_query</code> (acquisition_strategy.py)
<details><summary>Shape a PassiveDNS query with fallback domain extraction from raw query.</summary>
<div class="doc-comment">
<p>Shape a PassiveDNS query with fallback domain extraction from raw query.</p>
<p></p>
<p>F265: When seed_context.domains is empty (PUBLIC lane NameError caused</p>
<p>domain seeds to never populate), fall back to extracting a domain directly</p>
<p>from the raw query using the same regex used elsewhere in build_lane_query.</p>
<p></p>
<p>P2-4 Tier 2: If no domain/IP indicators found anywhere, return the full</p>
<p>base_query as a free-text PDNS search rather than empty string.</p>
<p>Many PDNS providers accept free-text queries (brand, actor, campaign names)</p>
<p>and return associated IPs/domains.</p>
<p></p>
<p>Returns:</p>
<p>First domain/IP indicator found, or full base_query as fallback, or "".</p>
</div>
</details>
</li>
<li><code>_run_public_branch_aggressive</code> (acquisition.py)</li>
<li><code>run_async</code> (sidecar_protocol_adapters.py) — <span class="doc-comment-inline">Search TV News Archive for broadcast content matching query.</span></li>
<li><code>_kill_chain_tagging_runner</code> (sidecar_bus.py) — <span class="doc-comment-inline">F203C MITRE ATT&amp;CK kill chain tagging.</span></li>
<li><code>_network_intel_runner</code> (sidecar_bus.py) — <span class="doc-comment-inline">F247B: Active network reconnaissance via NetworkReconnaissance + bridge.</span></li>
<li><code>build_search_documents_from_findings</code> (sprint_advisory_runner.py)
<details><summary>F228C: Convert CanonicalFinding objects to SearchDocument records.</summary>
<div class="doc-comment">
<p>F228C: Convert CanonicalFinding objects to SearchDocument records.</p>
<p></p>
<p>Advisory-only, no canonical writes. Skips findings without payload_text.</p>
<p>Deduplicates by url to avoid metadata explosion.</p>
<p>Bounds result to MAX_INDEXED_FINDINGS.</p>
<p></p>
<p>Args:</p>
<p>findings: List of CanonicalFinding objects (or dict-like with</p>
<p>finding_id, source_type, payload_text attrs).</p>
<p></p>
<p>Returns:</p>
<p>list[SearchDocument] suitable for LocalSearchSeam.index().</p>
</div>
</details>
</li>
<li><code>_min_branch_remaining_s</code> (sprint_scheduler_v1_archived.py)
<details><summary>F273B: Dynamic branch-remaining safety floor based on remaining time.</summary>
<div class="doc-comment">
<p>F273B: Dynamic branch-remaining safety floor based on remaining time.</p>
<p></p>
<p>Returns the safety floor (in seconds) below which a branch is skipped</p>
<p>with `terminal:remaining_too_low`. Replaces the cycle-ema-based formula</p>
<p>(0.2 * cycle_ema) that was too low for 300s+ sprints where 25 cycles</p>
<p>over-commit the timeline.</p>
<p></p>
<p>Formula (always-on, bounded [2.0, 5.0]):</p>
<p>remaining_s = time left in sprint (passed as argument)</p>
<p>base = max(2.0, 0.15 * remaining_s)</p>
<p>return min(5.0, base)</p>
<p></p>
<p>Examples (300s sprint):</p>
<p>- remaining_s=150s (50% left) -&gt; base = max(2.0, 22.5) = 22.5 -&gt; return 5.0s (capped)</p>
<p>- remaining_s=90s  (30% left) -&gt; base = max(2.0, 13.5) = 13.5 -&gt; return 5.0s (capped)</p>
<p>- remaining_s=60s  (20% left) -&gt; base = max(2.0, 9.0) = 9.0  -&gt; return 5.0s (capped)</p>
<p>- remaining_s=33.3s(11% left) -&gt; base = max(2.0, 5.0) = 5.0  -&gt; return 5.0s (at breakpoint)</p>
<p>- remaining_s=30s  (10% left) -&gt; base = max(2.0, 4.5) = 4.5  -&gt; return 4.5s</p>
<p>- remaining_s=15s  (5% left)  -&gt; base = max(2.0, 2.25) = 2.25 -&gt; return 2.25s</p>
<p></p>
<p>Why 0.15 * remaining_s: floor scales with remaining time so branches</p>
<p>get adequate time in long sprints while staying low in short sprints.</p>
<p>The 5.0s cap is active when 0.15*remaining_s &gt; 5.0, i.e. remaining_s &gt; 33.3s.</p>
<p>This prevents 300s sprints from losing all branches to terminal:remaining_too_low.</p>
<p></p>
<p>Fail-safe: if remaining_s is None or &lt;= 0, falls back to cycle-ema-based</p>
<p>formula (0.1 * cycle_ema, bounded [2.0, 5.0]) for backward compatibility.</p>
</div>
</details>
</li>
<li><code>_init_metrics_registry</code> (sprint_scheduler_v1_archived.py)
<details><summary>Initialize MetricsRegistry fail-soft using config export_dir or default path.</summary>
<div class="doc-comment">
<p>Initialize MetricsRegistry fail-soft using config export_dir or default path.</p>
<p></p>
<p></p>
<p></p>
<p>No absolute paths outside paths.py. Run dir is derived from export_dir</p>
<p></p>
<p>(if set) or ~/.hledac/runs (default fallback). Metrics file lives under</p>
<p></p>
<p>run_dir/logs/metrics.jsonl.</p>
</div>
</details>
</li>
<li><code>_make_ct_quarantine_entry</code> (source_finding_bridge.py)</li>
<li><code>_expand_keyword_query</code> (acquisition_strategy.py)
<details><summary>P1-2: Expand generic query to extract actionable indicators.</summary>
<div class="doc-comment">
<p>P1-2: Expand generic query to extract actionable indicators.</p>
<p></p>
<p>Returns up to 10 keywords spanning threat actors, TTPs, and IOCs.</p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p>- No network I/O, no model/MLX load</p>
<p>- Bounded: max 10 keywords returned</p>
<p>- Fail-safe: returns [query] on any error</p>
</div>
</details>
</li>
<li><code>_run_doh_lane</code> (acquisition_strategy.py)
<details><summary>Run DOH lane — DNS-over-HTTPS passive DNS recon via DOHAdapter.</summary>
<div class="doc-comment">
<p>Run DOH lane — DNS-over-HTTPS passive DNS recon via DOHAdapter.</p>
<p></p>
<p>F222B: First-class nonfeed lane. No model load, no browser, no stealth.</p>
<p>Bounds: max_items=20, timeout_s=30, concurrency=2.</p>
<p>Fail-soft: provider errors never break other lanes.</p>
</div>
</details>
</li>
<li><code>_run_wayback_lane</code> (__init__.py)</li>
<li><code>_run_pdns_lane</code> (__init__.py)</li>
<li><code>_run_ct_branch_aggressive</code> (acquisition.py)</li>
<li><code>run_hash</code> (role_based_pools.py)</li>
<li><code>run_regex</code> (role_based_pools.py)</li>
<li><code>_run_ane_semantic_dedup_advisory</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F265B-III: ANE-backed semantic deduplication of findings.</summary>
<div class="doc-comment">
<p>Sprint F265B-III: ANE-backed semantic deduplication of findings.</p>
<p></p>
<p>Runs after all advisory steps have completed, on the full findings list.</p>
<p>Uses ANE CoreML MiniLM embeddings to detect near-duplicate findings that</p>
<p>the RotatingBloomFilter URL dedup misses (similar title+snippet, not exact URL).</p>
<p></p>
<p>Bounded:</p>
<p>- threshold = 0.92 cosine similarity</p>
<p>- Only runs when ANE embedder is loaded (fail-soft if unavailable)</p>
<p>- No changes to canonical write path (DuckDB/LMDB untouched)</p>
<p></p>
<p>Returns:</p>
<p>None. Findings list is updated in-place via self._result.all_findings.</p>
</div>
</details>
</li>
<li><code>cap_feeding</code> (acquisition_strategy.py)
<details><summary>Check if feeding should be capped.</summary>
<div class="doc-comment">
<p>Check if feeding should be capped.</p>
<p></p>
<p>F227D: Added mission_intent and nonfeed_unresolved parameters.</p>
<p>When mission_runtime is active and nonfeed lanes are unresolved,</p>
<p>mission-aware thresholds override the base budget thresholds.</p>
<p></p>
<p>F230D: Added acquisition_profile parameter for nonfeed_diagnostic profile</p>
<p>per-intent feed cap thresholds.</p>
<p></p>
<p>Returns (should_cap, reason) where reason is empty when cap not active.</p>
</div>
</details>
</li>
<li><code>_run_ct_lane</code> (acquisition_strategy.py)
<details><summary>Run CT/crt.sh lane — wired to call_crtsh() for measurable outcome.</summary>
<div class="doc-comment">
<p>Run CT/crt.sh lane — wired to call_crtsh() for measurable outcome.</p>
<p></p>
<p>[F207K-A] Uses bridge helpers to produce CanonicalFinding candidates</p>
<p>with rejection tracking. DB write is the lane runner's job (not adapter).</p>
</div>
</details>
</li>
<li><code>_run_blockchain_lane</code> (__init__.py) — <span class="doc-comment-inline">Run blockchain forensics lane.</span></li>
<li><code>run_async</code> (sidecar_protocol_adapters.py) — <span class="doc-comment-inline">Search Fediverse for OSINT signals based on query and findings.</span></li>
<li><code>_score_pivot_domain</code> (pivot_planner.py)
<details><summary>Score a domain pivot based on multiple signals.</summary>
<div class="doc-comment">
<p>Score a domain pivot based on multiple signals.</p>
<p></p>
<p>F238A: Uses normalize_source_quality to interpret heterogeneous</p>
<p>source_quality_score values (0-90 int, 0-1 float, or None).</p>
<p>Applies degree-weighted noise penalty to high-degree generic domains.</p>
<p></p>
<p>F238F: Graph bonuses/penalties only apply when graph_stats is explicitly</p>
<p>available. None and {} both mean "graph unavailable" → no novelty bonus,</p>
<p>no seen-before penalty, no degree penalty.</p>
</div>
</details>
</li>
<li><code>_run_one</code> (sidecar_bus.py)</li>
<li><code>_sync_latent_relationships_to_graph</code> (sprint_scheduler_v1_archived.py)
<details><summary>Wave 2: Export NetworkX latent relationships and upsert unseen ones to DuckPGQ.</summary>
<div class="doc-comment">
<p>Wave 2: Export NetworkX latent relationships and upsert unseen ones to DuckPGQ.</p>
<p></p>
<p></p>
<p></p>
<p>NetworkX discovers relationships (co-occurrence, shared attributes) that are</p>
<p></p>
<p>NOT yet in DuckPGQ. These are upserted with confidence=0.3 (low-confidence</p>
<p></p>
<p>inferred relationships) so the knowledge graph learns across sprints.</p>
</div>
</details>
</li>
<li><code>evaluate_advisory_gate</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint 8VQ: Evaluate advisory gate at WINDUP entry -- DIAGNOSTIC ONLY.</summary>
<div class="doc-comment">
<p>Sprint 8VQ: Evaluate advisory gate at WINDUP entry -- DIAGNOSTIC ONLY.</p>
<p></p>
<p></p>
<p></p>
<p>Reads from cached PreDecisionSummary (computed by consume_shadow_pre_decision)</p>
<p></p>
<p>and composes AdvisoryGateSnapshot. Does NOT:</p>
<p></p>
<p>- Influence dispatch or source ordering</p>
<p></p>
<p>- Activate providers or tools</p>
<p></p>
<p>- Write to any ledgers as runtime truth</p>
<p></p>
<p>- Create new scheduler framework</p>
<p></p>
<p></p>
<p></p>
<p>Stores ephemeral result in _advisory_gate_snapshot (cleared in _reset_result).</p>
<p></p>
<p>Output goes into diagnostic report via _build_shadow_readiness_preview().</p>
</div>
</details>
</li>
<li><code>_canonical_finding</code> (source_finding_bridge.py)</li>
<li><code>_run_ct_lane</code> (__init__.py)</li>
<li><code>_compose_graph_capability_summary</code> (shadow_pre_decision.py)</li>
<li><code>_run_bounded_plugin_sidecar</code> (sidecar_orchestrator.py)
<details><summary>P0: Run a plugin sidecar coroutine through the plugin semaphore.</summary>
<div class="doc-comment">
<p>P0: Run a plugin sidecar coroutine through the plugin semaphore.</p>
<p></p>
<p>Bounds concurrent plugin sidecar executions to</p>
<p>_PLUGIN_SIDECAR_SEMAPHORE_LIMIT regardless of how many</p>
<p>@SidecarRegistry.register adapters are available.</p>
<p></p>
<p>Fail-soft: any exception is caught and logged, never raised.</p>
<p>F039: OTel span for plugin sidecar telemetry.</p>
</div>
</details>
</li>
<li><code>shutdown</code> (role_based_pools.py)
<details><summary>Shutdown all role-based pools.</summary>
<div class="doc-comment">
<p>Shutdown all role-based pools.</p>
<p></p>
<p>Args:</p>
<p>wait: If True, wait for pending tasks to complete</p>
</div>
</details>
</li>
<li><code>_run_quantum_path_analysis</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F214Q: Post-sprint quantum-inspired graph walk.</summary>
<div class="doc-comment">
<p>Sprint F214Q: Post-sprint quantum-inspired graph walk.</p>
<p>Find undiscovered connected IOCs via DuckPGQGraph.find_connected().</p>
<p></p>
<p>M1 RAM budget: bounded to 20 IOCs per sprint, max_hops=2, max 1000 total nodes.</p>
<p></p>
<p>Sprint P1-3: Routes through GraphService.find_entity_history() which</p>
<p>layers the hot-edges LMDB cache on top of DuckPGQ recursive CTE, giving</p>
<p>O(1) hot-path lookups for high-degree nodes and falling back to the CTE</p>
<p>only on cache miss.</p>
</div>
</details>
</li>
<li><code>_run_ti_feed_sidecar</code> (sprint_scheduler_v1_archived.py)
<details><summary>F252: TI feed advisory sidecar (NVD + CISA KEV).</summary>
<div class="doc-comment">
<p>F252: TI feed advisory sidecar (NVD + CISA KEV).</p>
<p></p>
<p>Fetches structured threat-intel feeds in parallel using safe_gather_ok.</p>
<p>Adapters are registered via source_registry; dispatches NvdApiAdapter</p>
<p>and CisaKevAdapter in parallel with bounded concurrency.</p>
<p>Fail-soft throughout: errors never crash the sprint.</p>
</div>
</details>
</li>
<li><code>_make_network_recon_finding</code> (source_finding_bridge.py)</li>
<li><code>_extract_domain_from_ct_finding</code> (acquisition_strategy.py)
<details><summary>Extract domain from a CT CanonicalFinding (or dict-like) object.</summary>
<div class="doc-comment">
<p>Extract domain from a CT CanonicalFinding (or dict-like) object.</p>
<p></p>
<p>Strategy:</p>
<p>1. Try payload_text: parse "domain: &lt;value&gt;" lines</p>
<p>2. Fallback: query field</p>
<p></p>
<p>Returns:</p>
<p>Normalized lowercase domain string, or None if not extractable.</p>
</div>
</details>
</li>
<li><code>_extract_domain_from_ct_finding</code> (__init__.py)
<details><summary>Extract domain from a CT CanonicalFinding (or dict-like) object.</summary>
<div class="doc-comment">
<p>Extract domain from a CT CanonicalFinding (or dict-like) object.</p>
<p></p>
<p>Strategy:</p>
<p>1. Try payload_text: parse "domain: &lt;value&gt;" lines</p>
<p>2. Fallback: query field</p>
<p></p>
<p>Returns:</p>
<p>Normalized lowercase domain string, or None if not extractable.</p>
</div>
</details>
</li>
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
<li><code>run_async_io</code> (role_based_pools.py)</li>
<li><code>_banner_grab_runner</code> (sidecar_bus.py) — <span class="doc-comment-inline">F214 banner grabber — TCP banner extraction, RAM-isolated.</span></li>
<li><code>_ipv6_recon_runner</code> (sidecar_bus.py) — <span class="doc-comment-inline">F214 IPv6 reconnaissance — RDAP, WHOIS, DoH AAAA, BGP peer.</span></li>
<li><code>resolve_nonfeed_expected_lanes</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_maybe_call_pressure_relief</code> (sprint_scheduler_v1_archived.py)
<details><summary>F273G: Per-sprint macOS malloc pressure relief.</summary>
<div class="doc-comment">
<p>F273G: Per-sprint macOS malloc pressure relief.</p>
<p></p>
<p>Calls the existing ``core.memory_cycle.malloc_zone_pressure_relief()``</p>
<p>helper to ask the Darwin allocator to release fragmented pages. Cheap</p>
<p>(single ctypes syscall), thread-safe in libmalloc, and fail-soft on</p>
<p>non-Darwin / on ctypes errors.</p>
<p></p>
<p>Wired into the pre-windup barrier so the windup phase starts with a</p>
<p>clean allocator state — better DuckDB ingest + LMDB mmap behavior +</p>
<p>reduced RSS fragmentation for the Hermes load that may follow.</p>
<p></p>
<p>Telemetry recorded on self._result:</p>
<p>- malloc_pressure_relief_count      (cumulative calls)</p>
<p>- malloc_pressure_relief_last_rc    (last return value, 0 = no-op)</p>
<p>- malloc_pressure_relief_last_at_s  (wall-clock of last call)</p>
<p></p>
<p>Bounded: 1 call per windup decision. No new feature flags.</p>
</div>
</details>
</li>
<li><code>_execute_pivot</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Dispatch pivot task to appropriate intelligence client.</span></li>
<li><code>_make_ct_conversion_summary</code> (source_finding_bridge.py)</li>
<li><code>_dispatch_accepted_findings_sidecars</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_run_public_branch</code> (acquisition.py)</li>
<li><code>run_async</code> (sidecar_protocol_adapters.py) — <span class="doc-comment-inline">Query DHT network for content hashes matching query.</span></li>
<li><code>run</code> (scheduler.py) — <span class="doc-comment-inline">Run the sprint — orchestrate prelude → acquisition → winddown phases.</span></li>
<li><code>_wayback_diff_runner</code> (sidecar_bus.py)
<details><summary>F203F Wayback CDX diff mining. Compatibility runner — canonical owner</summary>
<div class="doc-comment">
<p>F203F Wayback CDX diff mining. Compatibility runner — canonical owner</p>
<p>is intelligence/wayback_diff_miner.py::WaybackDiffMiner (wired as direct lane).</p>
</div>
</details>
</li>
<li><code>renderer_admission</code> (resource_governor.py)
<details><summary>F214R: Canonical renderer admission check.</summary>
<div class="doc-comment">
<p>F214R: Canonical renderer admission check.</p>
<p>@pending_integration: no confirmed production call sites as of F214R audit.</p>
<p>See tests/test_resource_governor_authority_seal.py::TestPendingIntegrationMarkers.</p>
<p></p>
<p>Returns RendererAdmission with:</p>
<p>- allowed: True if JS renderer may be used</p>
<p>- reason: human-readable denial reason</p>
<p>- uma_state: current UMA state</p>
<p>- model_loaded: whether model is currently loaded</p>
<p></p>
<p>Combines model lifecycle + UMA state in one authoritative call.</p>
<p>Fail-soft: returns allowed=False with "unknown" reason on errors.</p>
</div>
</details>
</li>
<li><code>windup_for_cycle</code> (sprint_scheduler_v1_archived.py)
<details><summary>F273B + F278A: Cycle-time-adaptive windup lead.</summary>
<div class="doc-comment">
<p>F273B + F278A: Cycle-time-adaptive windup lead.</p>
<p></p>
<p>The base `effective_windup_lead_s` (30% of duration, clamped [30, 180])</p>
<p>is the static floor. This method returns a longer windup when observed</p>
<p>cycles are slow -- so the windup phase has at least 2 cycles of headroom</p>
<p>for pattern extraction, synthesis, and DuckDB ingest.</p>
<p></p>
<p>Formula (F290):</p>
<p>base = effective_windup_lead_s  (adaptive 20/25/30% ratio)</p>
<p>adapt = max(0, (cycle_time_ema - 8) * 0.5)  # +0.5s per s over 8s cycle</p>
<p>adapt = min(30.0, adapt)         # cap the bonus at 30s</p>
<p>return clamp(base + adapt, 30, 180)</p>
<p></p>
<p>Examples (300s sprint, base=75s, F290 25%):</p>
<p>- cycle_time_ema=5s  -&gt; 75s (no bonus, quick cycles)</p>
<p>- cycle_time_ema=20s -&gt; 81s (+6s bonus)</p>
<p>- cycle_time_ema=60s -&gt; 105s (+30s bonus)</p>
<p></p>
<p>Examples (100s sprint, base=20s, F290 20%):</p>
<p>- cycle_time_ema=5s  -&gt; 30s (floor active since base+bonus &lt; 30)</p>
<p>- cycle_time_ema=30s -&gt; 41s (+11s bonus)</p>
<p>- cycle_time_ema=60s -&gt; 60s (bonus saturates below ceiling)</p>
<p></p>
<p>Always-on, bounded [30, 180], fail-soft (negative cycle_time_ema -&gt; base).</p>
</div>
</details>
</li>
<li><code>_process_chunk_parallel</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_run_hypothesis_export</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F259: Run causal hypothesis generation and export.</summary>
<div class="doc-comment">
<p>Sprint F259: Run causal hypothesis generation and export.</p>
<p></p>
<p>Gate: HLEDAC_ENABLE_HYPOTHESIS=1 and RAM &lt; 70%</p>
<p>Runs after CTI STIX export in the post-export phase.</p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p>- fail-soft: export error must not prevent teardown</p>
<p>- Lazy imports for causal_engine and hypothesis_graph</p>
<p>- RAM check before execution</p>
</div>
</details>
</li>
<li><code>_run_blockchain_lane</code> (acquisition_strategy.py) — <span class="doc-comment-inline">Run blockchain forensics lane.</span></li>
<li><code>_compose_model_control_summary</code> (shadow_pre_decision.py)</li>
<li><code>_compose_precursor_summary</code> (shadow_pre_decision.py)</li>
<li><code>_run_one_cycle</code> (acquisition.py)</li>
<li><code>_make_finding</code> (sidecar_protocol_adapters.py)
<details><summary>Construct a CanonicalFinding-compatible dict from a Fediverse post.</summary>
<div class="doc-comment">
<p>Construct a CanonicalFinding-compatible dict from a Fediverse post.</p>
<p></p>
<p>Accepts a `FediversePost` dataclass (the new contract from</p>
<p>`discovery/fediverse_adapter.search_multiple_instances`) or a raw</p>
<p>dict (legacy path) — both shapes are normalized via</p>
<p>`FediversePost.to_dict()` for downstream `post.get(...)` access.</p>
<p>Fail-soft: any conversion error returns `None` and the sidecar</p>
<p>logs nothing for the dropped post.</p>
</div>
</details>
</li>
<li><code>_run_bounded_sidecar</code> (sidecar_orchestrator.py)
<details><summary>P0: Run a sidecar coroutine through the advisory semaphore.</summary>
<div class="doc-comment">
<p>P0: Run a sidecar coroutine through the advisory semaphore.</p>
<p></p>
<p>Bounds concurrent advisory sidecar executions to</p>
<p>_ADVISORY_SIDECAR_SEMAPHORE_LIMIT regardless of how many</p>
<p>HLEDAC_ENABLE_* flags are active.</p>
<p></p>
<p>Fail-soft: any exception is caught and logged, never raised.</p>
<p>F039: OTel span for sidecar telemetry.</p>
</div>
</details>
</li>
<li><code>run_hash_batch</code> (role_based_pools.py)</li>
<li><code>run_regex_batch</code> (role_based_pools.py)</li>
<li><code>_is_valid_domain_candidate</code> (nonfeed_candidate_ledger.py)
<details><summary>F214: Validate domain candidate has a proper FQDN structure.</summary>
<div class="doc-comment">
<p>F214: Validate domain candidate has a proper FQDN structure.</p>
<p></p>
<p>Rejects:</p>
<p>- Empty strings or too short (&lt; 3 chars)</p>
<p>- Single labels without dots</p>
<p>- Two-label fragments where first label is short/no-digit AND second is a word</p>
<p>(e.g. "c2.bad" from "c2.bad actor[.]com" — "bad" is a word, not a TLD)</p>
<p>- Three-label fragments where last label is a word-like fragment</p>
<p>(e.g. "leak.lockbit-example" from broken "leak.lockbit-example[.]test")</p>
</div>
</details>
</li>
<li><code>_extract_hostname</code> (nonfeed_candidate_ledger.py)
<details><summary>Extract hostname from URL. Handles defanged hxxp:// variants.</summary>
<div class="doc-comment">
<p>Extract hostname from URL. Handles defanged hxxp:// variants.</p>
<p></p>
<p>F271: Uses Rust url_ops.extract_host() as the fast path for normal URLs.</p>
<p>When Rust returns empty (malformed or defanged), falls back to the</p>
<p>full defanged-URL parsing logic for security/defense OSINT use cases.</p>
</div>
</details>
</li>
<li><code>sidecar_admission</code> (resource_governor.py)
<details><summary>F204J: Check if a sidecar can be admitted given current memory state.</summary>
<div class="doc-comment">
<p>F204J: Check if a sidecar can be admitted given current memory state.</p>
<p></p>
<p>Returns SidecarAdmission with:</p>
<p>- allowed: True if sidecar should run</p>
<p>- reason: human-readable denial reason</p>
<p>- rss_gib: current RSS in GiB</p>
<p>- uma_state: current UMA state</p>
<p>- estimated_mb: the estimate that was evaluated</p>
<p></p>
<p>Fails soft: returns allowed=True if any check fails.</p>
</div>
</details>
</li>
<li><code>_log_advisory_dedup</code> (sprint_scheduler_v1_archived.py)
<details><summary>Emit a warning at most once per unique msg_key within a 16-slot FIFO window.</summary>
<div class="doc-comment">
<p>Emit a warning at most once per unique msg_key within a 16-slot FIFO window.</p>
<p></p>
<p>Returns True if the message was emitted, False if it was suppressed</p>
<p>(caller can use this to short-circuit expensive arg construction).</p>
<p></p>
<p>Bounded:</p>
<p>- _ADVISORY_LOG_LRU_MAX = 16 unique keys</p>
<p>- FIFO eviction when full (oldest key dropped, NOT promoted on hit)</p>
<p></p>
<p>ISSUE-041 fix: plain dict + deque replaces deprecated OrderedDict.</p>
<p>HIT:  O(1) membership test + counter increment only — deque order unchanged.</p>
<p>MISS: O(1) dict setitem + deque.append + optional deque.popleft for FIFO.</p>
<p></p>
<p>Usage:</p>
<p>_log_advisory_dedup(log, f"dht_sidecar_fail:{type(e).__name__}",</p>
<p>"[F214Q] DHT sidecar failed: %s", e)</p>
</div>
</details>
</li>
<li><code>_required_pre_windup_lanes</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F208B: Determine required lanes before windup.</summary>
<div class="doc-comment">
<p>Sprint F208B: Determine required lanes before windup.</p>
<p></p>
<p></p>
<p></p>
<p>Delegates to required_terminal_lanes() from acquisition_strategy,</p>
<p></p>
<p>which owns the canonical terminality policy (not the scheduler).</p>
<p></p>
<p></p>
<p></p>
<p>Returns tuple of required lane names (lowercase).</p>
</div>
</details>
</li>
<li><code>_load_dedup</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Load existing hashes from LMDB at BOOT. Idempotent. Non-blocking via to_thread.</span></li>
<li><code>run_ct_pivot</code> (sprint_entrypoint.py) — <span class="doc-comment-inline">Run CT log pivot for a single domain.</span></li>
<li><code>_derive_terminal</code> (acquisition_strategy.py)</li>
<li><code>_run_open_source_lane</code> (__init__.py) — <span class="doc-comment-inline">Run OpenSourceCollectors lane — pastebin, usenet, matrix, academic, sec_edgar, court records.</span></li>
<li><code>model_admission</code> (resource_governor.py)
<details><summary>F214R: Canonical model load admission check.</summary>
<div class="doc-comment">
<p>F214R: Canonical model load admission check.</p>
<p>@pending_integration: no confirmed production call sites as of F214R audit.</p>
<p>See tests/test_resource_governor_authority_seal.py::TestPendingIntegrationMarkers.</p>
<p></p>
<p>Returns ModelAdmission with:</p>
<p>- allowed: True if model load is permitted</p>
<p>- reason: human-readable denial reason</p>
<p>- uma_state: current UMA state</p>
<p>- free_uma_gib: available UMA GiB</p>
<p></p>
<p>Note: actual model lifecycle is managed by brain/model_lifecycle.py.</p>
<p>This only checks UMA state suitability for a new load.</p>
<p>Fail-soft: returns allowed=False with "unknown" reason on errors.</p>
</div>
</details>
</li>
<li><code>buffer_ioc</code> (sprint_scheduler_v1_archived.py)
<details><summary>Buffer an IOC into the Arrow batch.</summary>
<div class="doc-comment">
<p>Buffer an IOC into the Arrow batch.</p>
<p></p>
<p></p>
<p></p>
<p>Sprint 8VI §D: IOCScorer final_score zapojeno.</p>
<p></p>
<p>Sprint 8VI §C: Recent IOC ring buffer pro hypothesis feedback.</p>
</div>
</details>
</li>
<li><code>run_semantic_pivot</code> (sprint_entrypoint.py)
<details><summary>Sprint 8SB: Semantic pivot — ANN search for similar findings.</summary>
<div class="doc-comment">
<p>Sprint 8SB: Semantic pivot — ANN search for similar findings.</p>
<p></p>
<p>Loads SemanticStore, runs semantic_pivot, prints results.</p>
</div>
</details>
</li>
<li><code>_extract_domains_from_ct_name_value</code> (source_finding_bridge.py)
<details><summary>Extract all concrete (non-wildcard) domains from a multiline CT name_value.</summary>
<div class="doc-comment">
<p>Extract all concrete (non-wildcard) domains from a multiline CT name_value.</p>
<p></p>
<p>Returns list of (normalized_domain, was_wildcard) tuples for each line.</p>
<p>Wildcard-only lines are returned with was_wildcard=True; concrete domains</p>
<p>are returned with was_wildcard=False.</p>
<p></p>
<p>F213A: enables per-line wildcard rejection while preserving concrete siblings.</p>
<p>F226C: normalize concrete domains (strip *, lower, strip trailing dot) so</p>
<p>they can be compared against URL-derived candidates. Wildcard lines keep</p>
<p>their original (un-normalized) form so _quarantined_wildcards keys match</p>
<p>pre-normalization URL candidates.</p>
</div>
</details>
</li>
<li><code>_get_keyword_domain_expansion</code> (acquisition_strategy.py)
<details><summary>F1-3: Extract domain expansion seeds from keywords in query.</summary>
<div class="doc-comment">
<p>F1-3: Extract domain expansion seeds from keywords in query.</p>
<p></p>
<p>Maps threat-category keywords → expansion domains for lanes that need</p>
<p>a domain/IP seed (CT, WAYBACK, PASSIVE_DNS).</p>
<p></p>
<p>E.g. "ransomware C2" → ["ransomware_tracker.abuse.ch"]</p>
<p>"botnet"         → ["abuse.ch", "feodotracker.nl", "urlhaus.abuse.ch"]</p>
<p></p>
<p>Returns:</p>
<p>List of domain expansion strings (bounded, deduped, first-seen order).</p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p>- No network I/O, no model/MLX load</p>
<p>- Bounded: max 10 domains returned</p>
<p>- Fail-safe: returns [] on any error</p>
</div>
</details>
</li>
<li><code>_run_academic_lane</code> (acquisition_strategy.py) — <span class="doc-comment-inline">Run academic search lane — R9: bounded, research-profile-only, no query expansion.</span></li>
<li><code>_compose_export_readiness_summary</code> (shadow_pre_decision.py)</li>
<li><code>summary</code> (nonfeed_candidate_ledger.py)
<details><summary>Sprint F217E: Compute bounded summary for reporting.</summary>
<div class="doc-comment">
<p>Sprint F217E: Compute bounded summary for reporting.</p>
<p></p>
<p>Returns dict with counts per family, per stage, and key booleans.</p>
<p>Does NOT include full records (prevents payload leakage in reports).</p>
</div>
</details>
</li>
<li><code>_run_export</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Run all four exporters; failure is fail-soft.</span></li>
<li><code>to_dict</code> (shadow_pre_decision.py)</li>
<li><code>apply_scoring_metadata</code> (pivot_planner.py)
<details><summary>F225D: Apply full scoring metadata to a pivot.</summary>
<div class="doc-comment">
<p>F225D: Apply full scoring metadata to a pivot.</p>
<p></p>
<p>Mutates score_reason, estimated_cost, mission_boost via replacement</p>
<p>(frozen dataclass — returns new instance with updated fields).</p>
<p></p>
<p>Caps final expected_value to [0.0, 1.0].</p>
</div>
</details>
</li>
<li><code>filter_source_host_only</code> (nonfeed_candidate_ledger.py)
<details><summary>F214: Filter candidates that appear ONLY in source URL hostname.</summary>
<div class="doc-comment">
<p>F214: Filter candidates that appear ONLY in source URL hostname.</p>
<p></p>
<p>Args:</p>
<p>candidates:  Candidates extracted from text body + url.</p>
<p>source_url:  The source URL whose hostname to check.</p>
<p></p>
<p>Returns:</p>
<p>(filtered_candidates, source_host_domains):</p>
<p>- filtered_candidates: candidates with source_host_only removed</p>
<p>- source_host_domains: frozenset of domains that appeared ONLY in source URL</p>
</div>
</details>
</li>
<li><code>_run_pivot_executor_advisory</code> (sprint_advisory_runner.py)
<details><summary>F204C: Execute top pivots from PivotPlanner via AutonomousPivotExecutor.</summary>
<div class="doc-comment">
<p>F204C: Execute top pivots from PivotPlanner via AutonomousPivotExecutor.</p>
<p></p>
<p>Bounded advisory: executor stores derived findings via canonical ingest</p>
<p>and records HypothesisFeedback. Scheduler retains all authority.</p>
<p></p>
<p>Fail-soft: errors never crash the runner.</p>
</div>
</details>
</li>
<li><code>effective_windup_lead_s</code> (sprint_scheduler_v1_archived.py)
<details><summary>F250 + F272A + F273B + F278A + F285 + F290: Adaptive windup that scales</summary>
<div class="doc-comment">
<p>F250 + F272A + F273B + F278A + F285 + F290: Adaptive windup that scales</p>
<p>with sprint duration. Matches the F221-ABORT pre-flight guard formula exactly.</p>
<p></p>
<p>F290: Short sprints get smaller windup overhead to avoid consuming 50-100%</p>
<p>of the sprint budget in windup (F221/F289 abort).</p>
<p>sprint &lt;= 120s -&gt; 20% ratio (e.g. 60s -&gt; 12s windup, 48s active)</p>
<p>sprint &lt;= 300s -&gt; 25% ratio (e.g. 300s -&gt; 75s windup, 225s active)</p>
<p>sprint &gt; 300s  -&gt; 30% ratio (e.g. 600s -&gt; 180s cap, 420s active)</p>
<p>Clamped [15, 180] to allow short sprints to run without F289 abort.</p>
<p></p>
<p>F285: Explicit windup_lead_s (non-default 180.0) passes through directly.</p>
<p>F273B + F288: Aggressive mode → 15% ratio (parallel branches faster).</p>
</div>
</details>
</li>
<li><code>_init_i2p_background</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Background init -- I2PTransport singleton (F250). Fire-and-forget.</span></li>
<li><code>_build_ct_payload</code> (source_finding_bridge.py)</li>
<li><code>_run_wayback_lane</code> (acquisition_strategy.py)</li>
<li><code>_run_pdns_lane</code> (acquisition_strategy.py)</li>
<li><code>_run_shodan_lane</code> (__init__.py) — <span class="doc-comment-inline">Run Shodan intelligence lane — device/IP fingerprints.</span></li>
<li><code>phase_durations_so_far</code> (sprint_lifecycle.py)</li>
<li><code>_record_scheduler_exit</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F207V-A: Record the exact exit path taken by the scheduler.</summary>
<div class="doc-comment">
<p>Sprint F207V-A: Record the exact exit path taken by the scheduler.</p>
<p></p>
<p></p>
<p></p>
<p>Side-effect light -- only updates in-memory telemetry fields.</p>
<p></p>
<p>No network, no DB write, no graph write.</p>
</div>
</details>
</li>
<li><code>crawl_seed</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Crawl single .onion seed, convert to CanonicalFinding list.</span></li>
<li><code>_accumulate_findings_to_graph</code> (sprint_scheduler_v1_archived.py)
<details><summary>F198A: Extract IOCs from accepted findings and upsert to graph_service.</summary>
<div class="doc-comment">
<p>F198A: Extract IOCs from accepted findings and upsert to graph_service.</p>
<p></p>
<p></p>
<p></p>
<p>Delegates to SprintGraphAccumulator. Fail-soft: graph errors</p>
<p></p>
<p>must NOT prevent sprint continuation.</p>
<p></p>
<p></p>
<p></p>
<p>Returns:</p>
<p></p>
<p>Number of findings successfully upserted to graph.</p>
</div>
</details>
</li>
<li><code>_get_graph_signal</code> (sprint_scheduler_v1_archived.py)
<details><summary>F198A: Read graph signal at teardown without blocking sprint.</summary>
<div class="doc-comment">
<p>F198A: Read graph signal at teardown without blocking sprint.</p>
<p></p>
<p></p>
<p></p>
<p>Returns graph node/edge stats as a dict, or empty dict on error.</p>
<p></p>
<p>Non-blocking: called inside _build_diagnostic_report which is already</p>
<p></p>
<p>in the export teardown path (not on the critical sprint path).</p>
</div>
</details>
</li>
<li><code>_get_metrics_summary</code> (sprint_scheduler_v1_archived.py)
<details><summary>Get metrics summary for sprint report embedding.</summary>
<div class="doc-comment">
<p>Get metrics summary for sprint report embedding.</p>
<p></p>
<p></p>
<p></p>
<p>Returns lightweight state snapshot: counters/gauges count,</p>
<p></p>
<p>last_rss_mb, persist_available. Fail-soft: returns None if registry</p>
<p></p>
<p>not initialized.</p>
</div>
</details>
</li>
<li><code>_unload_hermes_at_teardown</code> (sprint_scheduler_v1_archived.py)
<details><summary>P12: Unload Hermes engine at sprint teardown via ModelManager.</summary>
<div class="doc-comment">
<p>P12: Unload Hermes engine at sprint teardown via ModelManager.</p>
<p></p>
<p>Bounded lifecycle: loaded at BOOT/WARMUP, released at TEARDOWN.</p>
<p>Uses ModelManager as canonical unload authority.</p>
<p></p>
<p>F273H: Idle-based lazy unload — skip unload if Hermes was recently</p>
<p>used (within _idle_unload_timeout_s window). Keeps model warm for</p>
<p>next sprint when inter-sprint gap &lt; 30 min.</p>
</div>
</details>
</li>
<li><code>async_run_tiered_feed_sprint_once</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>lane_is_terminal</code> (acquisition_strategy.py)
<details><summary>[F208A] Return True if the lane outcome is in a terminal state.</summary>
<div class="doc-comment">
<p>[F208A] Return True if the lane outcome is in a terminal state.</p>
<p></p>
<p>Terminal states:</p>
<p>- attempted=True (lane ran at least once)</p>
<p>- skipped=True (lane was intentionally skipped)</p>
<p>- error is not None (lane encountered an error)</p>
<p>- timeout=True (lane exceeded its time limit)</p>
</div>
</details>
</li>
<li><code>lane_is_terminal</code> (__init__.py)
<details><summary>[F208A] Return True if the lane outcome is in a terminal state.</summary>
<div class="doc-comment">
<p>[F208A] Return True if the lane outcome is in a terminal state.</p>
<p></p>
<p>Terminal states:</p>
<p>- attempted=True (lane ran at least once)</p>
<p>- skipped=True (lane was intentionally skipped)</p>
<p>- error is not None (lane encountered an error)</p>
<p>- timeout=True (lane exceeded its time limit)</p>
</div>
</details>
</li>
<li><code>plan_pivots</code> (pivot_planner.py)
<details><summary>Generate bounded pivots from accepted findings.</summary>
<div class="doc-comment">
<p>Generate bounded pivots from accepted findings.</p>
<p></p>
<p>Args:</p>
<p>findings: List of CanonicalFinding (or dict-like) objects</p>
<p>graph_stats: Optional graph statistics for scoring</p>
<p>max_pivots: Maximum number of pivots to generate (default MAX_PIVOTS=20)</p>
<p>feedback_summary: Optional dict mapping (pivot_type, ioc_type) to</p>
<p>HypothesisFeedbackSummary for scoring penalties (F203G).</p>
<p>If None or empty, no penalty is applied.</p>
<p></p>
<p>Returns:</p>
<p>List of Pivot objects, sorted by priority (highest first).</p>
<p>Empty list on any error (fail-soft).</p>
</div>
</details>
</li>
<li><code>mark_warmup_done</code> (sprint_lifecycle.py)
<details><summary>COMPAT ALIAS — transitions WARMUP→ACTIVE.</summary>
<div class="doc-comment">
<p>COMPAT ALIAS — transitions WARMUP→ACTIVE.</p>
<p></p>
<p>Canonical: use transition_to(SprintPhase.ACTIVE) directly.</p>
<p>NOTE: start() goes BOOT→WARMUP only. WARMUP→ACTIVE requires this alias</p>
<p>or explicit transition_to(ACTIVE). __main__.py uses this alias directly.</p>
<p></p>
<p>F4 metadata:</p>
<p>future_owner: __main__.py</p>
<p>removal_condition: __main__.py uses transition_to(ACTIVE) directly; or start() gains WARMUP→ACTIVE</p>
<p></p>
<p>Side effect: resets warmup failure counters on all domain circuit breakers.</p>
<p>This ensures warmup/probe failures do not affect production threshold.</p>
</div>
</details>
</li>
<li><code>_drain_pivot_queue</code> (sprint_scheduler_v1_archived.py)
<details><summary>Drain up to max_tasks from pivot queue. Max 8s total deadline.</summary>
<div class="doc-comment">
<p>Drain up to max_tasks from pivot queue. Max 8s total deadline.</p>
<p></p>
<p>Called at end of each ACTIVE cycle.</p>
</div>
</details>
</li>
<li><code>_emit_source_family_event</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_evaluate_family_status</code> (acquisition_strategy.py)
<details><summary>Evaluate the mission status of a single family.</summary>
<div class="doc-comment">
<p>Evaluate the mission status of a single family.</p>
<p></p>
<p>Returns one of: accepted, terminal, provider_failure, memory_skip, pending, missing</p>
</div>
</details>
</li>
<li><code>infer_mission_intent</code> (acquisition_strategy.py)
<details><summary>F225A: Infer mission intent from query string.</summary>
<div class="doc-comment">
<p>F225A: Infer mission intent from query string.</p>
<p></p>
<p>Rules:</p>
<p>- CVE-* pattern          → cve_recon</p>
<p>- crypto wallet/hash     → wallet_recon</p>
<p>- email-like indicator   → person_recon</p>
<p>- domain/IP/URL         → domain_recon / infra_recon</p>
<p>- otherwise             → unknown (safe lanes only)</p>
<p></p>
<p>Returns a string constant from MissionIntent.</p>
<p>No network I/O, no model load. Deterministic.</p>
</div>
</details>
</li>
<li><code>_run_ct_lane</code> (acquisition_strategy.py)</li>
<li><code>_evaluate_family_status</code> (__init__.py)
<details><summary>Evaluate the mission status of a single family.</summary>
<div class="doc-comment">
<p>Evaluate the mission status of a single family.</p>
<p></p>
<p>Returns one of: accepted, terminal, provider_failure, memory_skip, pending, missing</p>
</div>
</details>
</li>
<li><code>infer_mission_intent</code> (__init__.py)
<details><summary>F225A: Infer mission intent from query string.</summary>
<div class="doc-comment">
<p>F225A: Infer mission intent from query string.</p>
<p></p>
<p>Rules:</p>
<p>- CVE-* pattern          → cve_recon</p>
<p>- crypto wallet/hash     → wallet_recon</p>
<p>- email-like indicator   → person_recon</p>
<p>- domain/IP/URL         → domain_recon / infra_recon</p>
<p>- otherwise             → unknown (safe lanes only)</p>
<p></p>
<p>Returns a string constant from MissionIntent.</p>
<p>No network I/O, no model load. Deterministic.</p>
</div>
</details>
</li>
<li><code>_run_censys_lane</code> (__init__.py) — <span class="doc-comment-inline">Run Censys intelligence lane — certificate transparency.</span></li>
<li><code>_run_greynoise_lane</code> (__init__.py) — <span class="doc-comment-inline">Run GreyNoise intelligence lane — mass scanner classification.</span></li>
<li><code>_query_domain_score</code> (pivot_planner.py)
<details><summary>F238A: Apply degree-weighted penalty to a query-level domain pivot score.</summary>
<div class="doc-comment">
<p>F238A: Apply degree-weighted penalty to a query-level domain pivot score.</p>
<p></p>
<p>High-degree generic domains (CDN, registrar, parking, dynamic DNS) are noisy.</p>
<p>Ransomwar/malware-looking keywords are NOT penalized (suspicious = interesting).</p>
<p></p>
<p>F238F: All penalties/bonuses only apply when graph_stats is explicitly</p>
<p>available. None and {} both mean "graph unavailable" → return base_score.</p>
</div>
</details>
</li>
<li><code>_score_with_model</code> (pivot_planner.py)
<details><summary>Optional model-backed scoring via tot_integration.</summary>
<div class="doc-comment">
<p>Optional model-backed scoring via tot_integration.</p>
<p></p>
<p>This is an async function that uses the ToT integration layer</p>
<p>for deeper analysis. Only called when use_model_scoring=True</p>
<p>and tot_adapter is available.</p>
<p></p>
<p>Args:</p>
<p>pivot: The pivot to score</p>
<p>context: Context dict with query, findings, etc.</p>
<p>tot_adapter: TotIntegrationLayer instance</p>
<p></p>
<p>Returns:</p>
<p>Enhanced score [0.0, 1.0]</p>
</div>
</details>
</li>
<li><code>record_candidates</code> (nonfeed_candidate_ledger.py)
<details><summary>F214: Filter, rank, and record candidates in one call.</summary>
<div class="doc-comment">
<p>F214: Filter, rank, and record candidates in one call.</p>
<p></p>
<p>Combines filter_source_host_only + rank_candidates + add_feed_candidate.</p>
<p>Use this for the final ranking/recording step after deduplication.</p>
<p></p>
<p>Args:</p>
<p>candidates:  Deduplicated list of DomainCandidate</p>
<p>source_url:   Optional source URL for hostname filtering</p>
<p>max_total:    Maximum candidates to return/record</p>
<p></p>
<p>Returns:</p>
<p>Ranked, bounded list of DomainCandidate.</p>
</div>
</details>
</li>
<li><code>evaluate</code> (resource_governor.py)
<details><summary>Evaluate governor decisions for the current cycle.</summary>
<div class="doc-comment">
<p>Evaluate governor decisions for the current cycle.</p>
<p></p>
<p>Returns GovernorDecision with:</p>
<p>- fetch_limit: new FETCH_SEMAPHORE limit</p>
<p>- allow_renderer: True if JS renderer may be used</p>
<p>- allow_model_load: True if model load is permitted</p>
<p>- branch_concurrency: recommended branch parallelism</p>
<p>- reason: human-readable decision rationale</p>
<p>- free_uma_gib: available UMA GiB for QuantizationSelector</p>
<p>- system_used_gib: system memory used in GiB (F265H)</p>
<p>- swap_detected: True if swap &gt; 3.5 GiB (F265H)</p>
<p></p>
<p>Self-applying: calls apply_decision() before returning so all</p>
<p>decision fields (fetch_limit, counters) are propagated to runtime</p>
<p>surfaces. This eliminates the 90% drift problem where evaluate() was</p>
<p>called everywhere but apply_decision() was called only 2×.</p>
<p></p>
<p>Fails soft: returns safe defaults on any error.</p>
</div>
</details>
</li>
<li><code>final_windup_lead_s</code> (sprint_scheduler_v1_archived.py)
<details><summary>F290: Adaptive windup for sprint-end synthesis and graceful shutdown.</summary>
<div class="doc-comment">
<p>F290: Adaptive windup for sprint-end synthesis and graceful shutdown.</p>
<p>Matches effective_windup_lead_s ratio tiers but with [30, 180] floor</p>
<p>(vs [15, 180] for effective — final needs at least 30s for synthesis).</p>
<p></p>
<p>F285: Explicit windup_lead_s (non-default 180.0) passes through directly.</p>
</div>
</details>
</li>
<li><code>effective_cycle_sleep_s</code> (sprint_scheduler_v1_archived.py)
<details><summary>F228G: Adaptive cycle sleep that scales with sprint duration.</summary>
<div class="doc-comment">
<p>F228G: Adaptive cycle sleep that scales with sprint duration.</p>
<p></p>
<p>Short sprints (60-90s) need a much shorter inter-cycle sleep than</p>
<p>long ones (1800s). For very short sprints the 5.0s default sleep</p>
<p>consumes up to 50% of the active window -- making it impossible to</p>
<p>run more than a handful of cycles before windup.</p>
<p></p>
<p>Returns:</p>
<p>- 60s quick (active=30s) -&gt; 1.0s (fits ~25 cycles)</p>
<p>- 300s deep  (active=210s) -&gt; 2.0s (fits ~50 cycles)</p>
<p>- 600s thoro (active=420s) -&gt; 3.0s</p>
<p>- 1800s default (active=1620s) -&gt; 5.0s (preserves pre-F228G behavior)</p>
<p></p>
<p>Bounded: clamp [0.5, 5.0]s to prevent both over-sleep on quick</p>
<p>sprints and ultra-tight loops on long ones.</p>
<p></p>
<p>Fail-safe: if active &lt;= 0, returns 0.5s (minimum).</p>
</div>
</details>
</li>
<li><code>_init_rel_discovery</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>load_source_weights</code> (sprint_scheduler_v1_archived.py)
<details><summary>Load hit-rate history from DuckDB and set source weights.</summary>
<div class="doc-comment">
<p>Load hit-rate history from DuckDB and set source weights.</p>
<p></p>
<p></p>
<p></p>
<p>Bounds: 0.3 - 2.5 (30% floor, 250% ceiling, B.6).</p>
<p></p>
<p>Falls back to defaults on any error.</p>
</div>
</details>
</li>
<li><code>snapshot</code> (sprint_lifecycle.py)
<details><summary>Return a JSON-serializable dict representing the current state.</summary>
<div class="doc-comment">
<p>Return a JSON-serializable dict representing the current state.</p>
<p></p>
<p>DIAGNOSTIC ONLY — this is a read-only snapshot for monitoring,</p>
<p>not a second authority. The authoritative state is the live</p>
<p>_current_phase field and current_phase property.</p>
<p></p>
<p>No Path objects, no open handles — recovery-safe.</p>
</div>
</details>
</li>
<li><code>_init_graph_and_ioc_state</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Phase M: Graph accumulator, IOC graph, lane outcomes, verdict accumulators (21 attrs).</span></li>
<li><code>_prewarm_mlx_sync</code> (sprint_scheduler_v1_archived.py)
<details><summary>Single unified prewarm — loads MLXEmbeddingManager singleton (shared by ModernBertEngine).</summary>
<div class="doc-comment">
<p>Single unified prewarm — loads MLXEmbeddingManager singleton (shared by ModernBertEngine).</p>
<p></p>
<p>F320: Skip if prewarm_daemon already loaded models at startup.</p>
<p>is_prewarm_done() is checked first to avoid redundant ~10-15s load.</p>
</div>
</details>
</li>
<li><code>_maybe_launch_enhanced_research</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Fire-and-forget deep research advisory. Called at TEARDOWN.</span></li>
<li><code>enqueue_hypothesis_pivot</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>upsert_ioc</code> (graph_adapter.py)</li>
<li><code>_init_background_transports</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Phase 3: Initialize background transports - memory pressure, DHT, I2P, Nym, Tor.</span></li>
<li><code>_run_advisory_runner</code> (sprint_scheduler_v1_archived.py)
<details><summary>F206D: Delegate all advisory orchestration to SidecarOrchestrator.</summary>
<div class="doc-comment">
<p>F206D: Delegate all advisory orchestration to SidecarOrchestrator.</p>
<p></p>
<p></p>
<p></p>
<p>SidecarOrchestrator.run_advisory_runner() owns:</p>
<p></p>
<p>1. run_all_advisories (pivot_planner, pivot_executor, resource_governor, analyst_brief)</p>
<p></p>
<p>2. run_ct_to_passivedns_pivot_advisory</p>
<p></p>
<p>3. run_bgp_advisory_sidecar (non-blocking)</p>
<p></p>
<p>4. run_wayback_cdx_deep_sidecar (non-blocking)</p>
<p></p>
<p></p>
<p></p>
<p>This method remains for backward compatibility with any direct callers.</p>
</div>
</details>
</li>
<li><code>_get_prewindup_barrier_report</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F207Q-A: Read prewindup barrier telemetry for diagnostic report.</summary>
<div class="doc-comment">
<p>Sprint F207Q-A: Read prewindup barrier telemetry for diagnostic report.</p>
<p></p>
<p></p>
<p></p>
<p>Returns dict under acquisition_strategy.prewindup_barrier key.</p>
<p></p>
<p>Fails soft: returns None if barrier was never checked.</p>
</div>
</details>
</li>
<li><code>summarize_network_recon_conversion</code> (source_finding_bridge.py)</li>
<li><code>_should_enable_bootstrap</code> (acquisition_strategy.py)
<details><summary>P0-3: Enable bootstrap for threat queries even without domain.</summary>
<div class="doc-comment">
<p>P0-3: Enable bootstrap for threat queries even without domain.</p>
<p></p>
<p>Enables rescue URLs (CISA KEV, NVD, Shodan, Exploit-DB) for:</p>
<p>- Threat indicator queries (ransomware, malware, C2, botnet, APT...)</p>
<p>- CVE patterns (CVE-YYYY-NNNNN)</p>
<p>- Bare IP addresses</p>
<p>- nonfeed_diagnostic profile</p>
<p></p>
<p>This mirrors the F221A threat-query logic in required_terminal_lanes()</p>
<p>but surfaces the decision as a boolean flag stored in AcquisitionStrategySnapshot</p>
<p>so the scheduler can propagate it to LivePublicPipeline.run(public_bootstrap_enabled).</p>
<p></p>
<p>Returns:</p>
<p>True when bootstrap should be enabled for the query.</p>
<p>False when domain bootstrap handles it or profile opts out.</p>
</div>
</details>
</li>
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
<li><code>_run_sprint_advisory_branch</code> (sidecar_orchestrator.py) — <span class="doc-comment-inline">Branch A: SprintAdvisoryRunner for 4 core advisories.</span></li>
<li><code>estimate_pivot_cost</code> (pivot_planner.py)
<details><summary>F225D: Estimate relative cost/effort to execute a pivot.</summary>
<div class="doc-comment">
<p>F225D: Estimate relative cost/effort to execute a pivot.</p>
<p></p>
<p>Returns cost tier:</p>
<p>0.3 = trivial (archive, passive graph)</p>
<p>0.5 = moderate (domain WHOIS, passive DNS)</p>
<p>0.7 = expensive (live crawl, active scan)</p>
<p>1.0 = very expensive (model-backed inference)</p>
</div>
</details>
</li>
<li><code>ingest_text_for_candidates</code> (nonfeed_candidate_ledger.py)
<details><summary>F214: Extract domain candidates from text and record as FEED candidates.</summary>
<div class="doc-comment">
<p>F214: Extract domain candidates from text and record as FEED candidates.</p>
<p></p>
<p>Convenience facade that combines extraction + ledger recording.</p>
<p>Returns extracted candidates (for immediate use by caller).</p>
<p></p>
<p>Args:</p>
<p>text:           Text to scan</p>
<p>source_url:     Optional source URL for hostname extraction</p>
<p>source_family:  "PUBLIC" or "FEED"</p>
<p>max_candidates: Max candidates to record per source</p>
<p></p>
<p>Returns:</p>
<p>List of DomainCandidate extracted (may be empty).</p>
</div>
</details>
</li>
<li><code>_should_deprioritize_source</code> (sprint_scheduler_v1_archived.py)
<details><summary>Return True if source should be deprioritized this cycle.</summary>
<div class="doc-comment">
<p>Return True if source should be deprioritized this cycle.</p>
<p></p>
<p></p>
<p></p>
<p>Deprioritization conditions (all bounded, all in-memory):</p>
<p></p>
<p>1. Source is in cooldown -- pushed to end of work list</p>
<p></p>
<p>2. Silent streak &gt;= 4 cycles -- deprioritized but NOT excluded</p>
</div>
</details>
</li>
<li><code>_load_sync</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Synchronous LMDB load — runs in thread pool to avoid event-loop blocking.</span></li>
<li><code>_close_dedup</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Close LMDB at TEARDOWN. Calls flush first.</span></li>
<li><code>_init_nym_background</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Background init -- NymTransport singleton (F250). Fire-and-forget.</span></li>
<li><code>normalize_passive_dns_query</code> (__init__.py)
<details><summary>Shape a PassiveDNS query with fallback domain extraction from raw query.</summary>
<div class="doc-comment">
<p>Shape a PassiveDNS query with fallback domain extraction from raw query.</p>
<p></p>
<p>F265: When seed_context.domains is empty (PUBLIC lane NameError caused</p>
<p>domain seeds to never populate), fall back to extracting a domain directly</p>
<p>from the raw query using the same regex used elsewhere in build_lane_query.</p>
<p></p>
<p>Returns:</p>
<p>First domain/IP indicator found, or "" if nothing extractable.</p>
</div>
</details>
</li>
<li><code>_extract_domains</code> (sidecar_protocol_adapters.py) — <span class="doc-comment-inline">Extract domain IOCs from findings.</span></li>
<li><code>_init_sidecar_orchestrator</code> (scheduler.py) — <span class="doc-comment-inline">Initialize SidecarOrchestrator (fail-soft).</span></li>
<li><code>bump_counter</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint P0-1: increment a hot-path counter by `n` (default 1) on</summary>
<div class="doc-comment">
<p>Sprint P0-1: increment a hot-path counter by `n` (default 1) on</p>
<p>the SoA layout. Returns the new value, or 0 on layout miss.</p>
<p></p>
<p>Usage:</p>
<p>result.bump_counter("cycles_started")         # +1</p>
<p>result.bump_counter("cycles_completed", n=2)  # +2</p>
<p></p>
<p>This is a slightly faster path than `result.cycles_started += 1`</p>
<p>(skips the property setter) and is the recommended migration</p>
<p>target for hot-path counter bumps in a follow-up sprint.</p>
<p></p>
<p>Fail-soft: layout unavailable → returns 0.</p>
</div>
</details>
</li>
<li><code>__init__</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>buffer_finding</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Buffer a finding into the Arrow batch.</span></li>
<li><code>_init_tor_background</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Background init -- TorTransport singleton (F214Q). Fire-and-forget.</span></li>
<li><code>_run_open_source_lane</code> (acquisition_strategy.py) — <span class="doc-comment-inline">Run OpenSourceCollectors lane — pastebin, usenet, matrix, academic, sec_edgar, court records.</span></li>
<li><code>_async_run_live_feed</code> (acquisition.py)</li>
<li><code>__init__</code> (sidecar_orchestrator.py)</li>
<li><code>_cheap_score_finding</code> (pivot_planner.py)
<details><summary>Cheap heuristic scoring without model inference.</summary>
<div class="doc-comment">
<p>Cheap heuristic scoring without model inference.</p>
<p></p>
<p>Score based on:</p>
<p>- confidence: finding confidence [0.0, 1.0]</p>
<p>- signal_facets: if available, average of facet values</p>
<p>- source_type: some source types are higher quality</p>
</div>
</details>
</li>
<li><code>_score_pivot_archive</code> (pivot_planner.py)
<details><summary>Score an archive pivot.</summary>
<div class="doc-comment">
<p>Score an archive pivot.</p>
<p></p>
<p>F238A: Applies degree-weighted noise penalty — high-degree generic domains</p>
<p>(CDN, registrar, parking) get reduced archive value since their historical</p>
<p>records are noisy. Suspicious/ransomware-looking domains are NOT penalized.</p>
<p></p>
<p>F238F: Degree penalty only applies when graph_stats is explicitly available.</p>
<p>None and {} both mean "graph unavailable" → no degree penalty.</p>
</div>
</details>
</li>
<li><code>score_pivot_for_mission</code> (pivot_planner.py)
<details><summary>F225D: Apply mission-aware boost to a pivot.</summary>
<div class="doc-comment">
<p>F225D: Apply mission-aware boost to a pivot.</p>
<p></p>
<p>domain_recon  → boosts domain/archive/graph pivots</p>
<p>wallet_recon  → boosts graph (hash) pivots</p>
<p>cve_recon     → boosts public/feed/archive pivots</p>
<p>infra_recon   → boosts IP/domain/graph pivots</p>
<p>person_recon  → boosts leak/identity pivots</p>
<p>unknown       → no boost</p>
<p></p>
<p>Returns multiplier in [0.5, 1.5].</p>
</div>
</details>
</li>
<li><code>_identity_stitching_runner</code> (sidecar_bus.py) — <span class="doc-comment-inline">F202B identity stitching — heavy, RAM-guarded by bus.</span></li>
<li><code>snapshot</code> (resource_governor.py)
<details><summary>Current state snapshot for dashboard rendering.</summary>
<div class="doc-comment">
<p>Current state snapshot for dashboard rendering.</p>
<p></p>
<p>Issue #22: protected by _snapshot_lock (threading.RLock) to prevent</p>
<p>torn reads when executor threads mutate _ema_branch_timeouts via</p>
<p>record_branch_timeout()/record_branch_success().</p>
</div>
</details>
</li>
<li><code>recommended_tool_mode</code> (sprint_lifecycle.py)</li>
<li><code>__post_init__</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint P0-1: lazily allocate the SoA counter layout.</summary>
<div class="doc-comment">
<p>Sprint P0-1: lazily allocate the SoA counter layout.</p>
<p></p>
<p>Invariants:</p>
<p>L.1  Layout is allocated exactly once per instance.</p>
<p>L.2  Allocation failure (IntCounterLayout unavailable or</p>
<p>MemoryError) is fail-soft: layout remains None and</p>
<p>property getters/setters return 0 (counter-only).</p>
<p>L.3  Idempotent — safe to call multiple times.</p>
</div>
</details>
</li>
<li><code>_init_arrow_and_synthesis</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Phase I: Arrow columnar buffer, synthesis, enrichment, evidence, chain (15 attrs).</span></li>
<li><code>_close_metrics_registry</code> (sprint_scheduler_v1_archived.py)
<details><summary>Close metrics registry at TEARDOWN -- force flush prevents tail-loss.</summary>
<div class="doc-comment">
<p>Close metrics registry at TEARDOWN -- force flush prevents tail-loss.</p>
<p></p>
<p></p>
<p></p>
<p>CancelledError is re-raised per GHOST_INVARIANTS.</p>
</div>
</details>
</li>
<li><code>_extract_domain_from_ct_hit</code> (source_finding_bridge.py)
<details><summary>Extract the domain/subdomain from a CT DiscoveryHit URL or title.</summary>
<div class="doc-comment">
<p>Extract the domain/subdomain from a CT DiscoveryHit URL or title.</p>
<p></p>
<p>URL format: "https://subdomain.example.com/"</p>
<p>Title format: "CT: subdomain.example.com"</p>
<p></p>
<p>Returns None if no domain-like string is found.</p>
<p>Normalization: strips wildcard prefix, trailing dot, lowercases.</p>
</div>
</details>
</li>
<li><code>_build_ct_provenance</code> (source_finding_bridge.py)</li>
<li><code>_dataclass_to_dict</code> (source_finding_bridge.py) — <span class="doc-comment-inline">Convert a dataclass instance to dict, handling nested dataclasses.</span></li>
<li><code>_derive_terminal</code> (__init__.py)</li>
<li><code>explain_pivot_score</code> (pivot_planner.py)
<details><summary>F225D: Human-readable score explanation for debugging/audit.</summary>
<div class="doc-comment">
<p>F225D: Human-readable score explanation for debugging/audit.</p>
<p></p>
<p>Returns a one-line string describing the score components.</p>
</div>
</details>
</li>
<li><code>__init__</code> (role_based_pools.py)</li>
<li><code>_gopher_crawl_runner</code> (sidecar_bus.py) — <span class="doc-comment-inline">F216: Gopher archive crawler — crawls seed servers, extracts text, stores findings.</span></li>
<li><code>_compute_lanes_unlocked</code> (nonfeed_seed_runtime.py)</li>
<li><code>_build_work_items</code> (sprint_scheduler_v1_archived.py)
<details><summary>Build and tier-sort work items from source list.</summary>
<div class="doc-comment">
<p>Build and tier-sort work items from source list.</p>
<p></p>
<p>Sprint F228G: tier resolution falls back to _DEFAULT_SOURCE_TIER_MAP</p>
<p>before defaulting to SourceTier.OTHER. The five canonical structured</p>
<p>TI feeds (cisa_kev, threatfox_ioc, urlhaus_recent, feodo_ip,</p>
<p>openphish_feed) are mapped to STRUCTURED_TI so they survive prune</p>
<p>mode and produce real work each cycle.</p>
</div>
</details>
</li>
<li><code>_init_forensics</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Initialize forensics enricher and LMDB. Fail-safe -- does not raise.</span></li>
<li><code>_init_multimodal</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Initialize multimodal enricher and LMDB. Fail-safe -- does not raise.</span></li>
<li><code>inject_analyst_workbench</code> (sprint_scheduler_v1_archived.py)
<details><summary>F204E: Inject AnalystWorkbench reference for sprint brief generation.</summary>
<div class="doc-comment">
<p>F204E: Inject AnalystWorkbench reference for sprint brief generation.</p>
<p></p>
<p></p>
<p></p>
<p>Workbench is used at TEARDOWN to generate a model-free analyst brief</p>
<p></p>
<p>summarizing sprint results: what changed, strongest evidence,</p>
<p></p>
<p>next best pivots, and open questions.</p>
<p></p>
<p></p>
<p></p>
<p>All workbench calls are fail-soft -- exception or None workbench -&gt; no-op brief.</p>
</div>
</details>
</li>
<li><code>_resolve_arrow_batch_hard_cap</code> (sprint_scheduler_v1_archived.py)
<details><summary>Resolve Arrow batch hard cap from env or return M1-safe default.</summary>
<div class="doc-comment">
<p>Resolve Arrow batch hard cap from env or return M1-safe default.</p>
<p></p>
<p></p>
<p></p>
<p>F214OPT-D: Prevents unbounded Arrow batch growth after flush failure.</p>
<p></p>
<p>Default is max(2 * _ARROW_FLUSH_N, 2000) = 2000 entries (~10MB range).</p>
<p></p>
<p>Env override: HLEDAC_ARROW_BATCH_HARD_CAP (min 100, max 50000).</p>
</div>
</details>
</li>
<li><code>_run_feed_branch_aggressive</code> (acquisition.py)</li>
<li><code>run_async</code> (sidecar_protocol_adapters.py)</li>
<li><code>run_async</code> (sidecar_protocol_adapters.py)</li>
<li><code>_init_duckdb_store</code> (scheduler.py) — <span class="doc-comment-inline">Initialize DuckDBShadowStore (fail-soft).</span></li>
<li><code>_check_embed_ram_budget</code> (role_based_pools.py)
<details><summary>Check if embedding budget allows new work.</summary>
<div class="doc-comment">
<p>Check if embedding budget allows new work.</p>
<p></p>
<p>M1 8GB: MLX embeddings use Metal VRAM. We cap at 2 concurrent</p>
<p>workers because each embedding batch can use up to 2GB VRAM.</p>
<p></p>
<p>Returns:</p>
<p>True if budget allows, False otherwise.</p>
</div>
</details>
</li>
<li><code>_worker_adjust_consumer</code> (resource_governor.py)
<details><summary>Background consumer that applies worker count changes while holding self._lock.</summary>
<div class="doc-comment">
<p>Background consumer that applies worker count changes while holding self._lock.</p>
<p></p>
<p>This is the ONLY place where self._current_workers is written.</p>
<p>The lock is held only during the actual semaphore update — never blocks</p>
<p>the producer path (evaluate/evaluate_adaptive/apply_decision).</p>
</div>
</details>
</li>
<li><code>_try_enqueue_adjust</code> (resource_governor.py)
<details><summary>Enqueue fetch_limit adjustment with back-pressure on overflow.</summary>
<div class="doc-comment">
<p>Enqueue fetch_limit adjustment with back-pressure on overflow.</p>
<p></p>
<p>P1-2 fix: asyncio.Queue(maxsize=64) replaces unbounded Queue().</p>
<p>On overflow put_nowait drops the message and logs a warning — the</p>
<p>governor's AIMD loop will eventually converge via the next evaluate()</p>
<p>call. This prevents unbounded queue growth during degraded/emergency</p>
<p>mode where evaluate() is called every 5s but _worker_adjust_consumer</p>
<p>may fall behind.</p>
</div>
</details>
</li>
<li><code>add_phase_exit_callback</code> (sprint_lifecycle.py)</li>
<li><code>request_windup</code> (sprint_lifecycle.py)
<details><summary>COMPAT ALIAS — forwards to transition_to(WINDUP).</summary>
<div class="doc-comment">
<p>COMPAT ALIAS — forwards to transition_to(WINDUP).</p>
<p></p>
<p>Canonical: use transition_to(SprintPhase.WINDUP).</p>
<p>Idempotent: skips if already in WINDUP or beyond (matching utils behavior).</p>
<p></p>
<p>F4 metadata:</p>
<p>future_owner: __main__.py</p>
<p>removal_condition: All call-sites use transition_to(WINDUP)</p>
</div>
</details>
</li>
<li><code>upsert_relation</code> (graph_adapter.py)</li>
<li><code>__post_init__</code> (scheduler_result.py)
<details><summary>Sprint P0-1: lazily allocate the SoA counter layout.</summary>
<div class="doc-comment">
<p>Sprint P0-1: lazily allocate the SoA counter layout.</p>
<p></p>
<p>L.1  Allocated exactly once per instance.</p>
<p>L.2  Fail-soft: leaves layout as None on any error.</p>
<p>L.3  Idempotent — safe to call multiple times.</p>
</div>
</details>
</li>
<li><code>_safe_getattr</code> (sprint_scheduler_v1_archived.py)
<details><summary>Get attribute without lambda allocation. Returns default if missing.</summary>
<div class="doc-comment">
<p>Get attribute without lambda allocation. Returns default if missing.</p>
<p></p>
<p>For bool attrs (is_closed, is_frozen): coerces to bool.</p>
<p>For callable attrs (is_closed()): calls if present, returns default if missing.</p>
<p>M1 8GB: no heap allocation per call.</p>
</div>
</details>
</li>
<li><code>record_pivot_outcome</code> (sprint_scheduler_v1_archived.py)
<details><summary>Zaznamenej výsledek pivot tasku jako reward signal pro RL.</summary>
<div class="doc-comment">
<p>Zaznamenej výsledek pivot tasku jako reward signal pro RL.</p>
<p></p>
<p>reward = findings per second (FPS) -- normalizovaný na [0, 1].</p>
</div>
</details>
</li>
<li><code>_tick_metrics_on_cycle_end</code> (sprint_scheduler_v1_archived.py)
<details><summary>Tick metrics at cycle completion -- captures RSS, open FDs.</summary>
<div class="doc-comment">
<p>Tick metrics at cycle completion -- captures RSS, open FDs.</p>
<p></p>
<p></p>
<p></p>
<p>Called once per cycle (not in tight loop). Fail-soft: noop if registry</p>
<p></p>
<p>not initialized. No model load, no model inference.</p>
</div>
</details>
</li>
<li><code>score_source</code> (sprint_scheduler_v1_archived.py)
<details><summary>Compute priority score per B.1 formula.</summary>
<div class="doc-comment">
<p>Compute priority score per B.1 formula.</p>
<p></p>
<p></p>
<p></p>
<p>score(source) = base_tier_weight(source)</p>
<p></p>
<p>* hit_rate_multiplier(source)</p>
<p></p>
<p>* novelty_bonus(source)</p>
</div>
</details>
</li>
<li><code>inject_forensics_enricher</code> (sprint_scheduler_v1_archived.py)
<details><summary>F195C: Inject ForensicsEnricher + LMDB env (external wiring).</summary>
<div class="doc-comment">
<p>F195C: Inject ForensicsEnricher + LMDB env (external wiring).</p>
<p></p>
<p></p>
<p></p>
<p>OWNERSHIP: caller owns enricher lifecycle. Scheduler invokes</p>
<p></p>
<p>enricher.enrich() during finding sidecar processing. LMDB env</p>
<p></p>
<p>is owned by caller and passed here for reference only.</p>
<p></p>
<p>All calls are fail-soft -- exception or None -&gt; no-op.</p>
</div>
</details>
</li>
<li><code>inject_multimodal_enricher</code> (sprint_scheduler_v1_archived.py)
<details><summary>F195C: Inject MultimodalEnricher + LMDB env (external wiring).</summary>
<div class="doc-comment">
<p>F195C: Inject MultimodalEnricher + LMDB env (external wiring).</p>
<p></p>
<p></p>
<p></p>
<p>OWNERSHIP: caller owns enricher lifecycle. Scheduler invokes</p>
<p></p>
<p>enricher.enrich() during finding sidecar processing. LMDB env</p>
<p></p>
<p>is owned by caller and passed here for reference only.</p>
<p></p>
<p>All calls are fail-soft -- exception or None -&gt; no-op.</p>
</div>
</details>
</li>
<li><code>_init_dht_node_background</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Background init -- DHT node singleton (F214). Fire-and-forget.</span></li>
<li><code>main</code> (sprint_entrypoint.py)
<details><summary>Synchronous entry point with structured exit-code handling.</summary>
<div class="doc-comment">
<p>Synchronous entry point with structured exit-code handling.</p>
<p></p>
<p>Thin wrapper around _main_dispatch(). All sprint execution paths flow</p>
<p>through _main_dispatch(); main() owns the catch-all envelope and exit codes.</p>
</div>
</details>
</li>
<li><code>_extract_cids_from_text</code> (acquisition_strategy.py) — <span class="doc-comment-inline">Extract unique explicit CIDs from arbitrary text. Bounded dedup.</span></li>
<li><code>_load_feed_budget_from_env</code> (acquisition_strategy.py) — <span class="doc-comment-inline">Load FeedDominanceBudget from environment variables with safe fallback.</span></li>
<li><code>_run_shodan_lane</code> (acquisition_strategy.py) — <span class="doc-comment-inline">Run Shodan intelligence lane — device/IP fingerprints.</span></li>
<li><code>_run_censys_lane</code> (acquisition_strategy.py) — <span class="doc-comment-inline">Run Censys intelligence lane — certificate transparency.</span></li>
<li><code>_run_greynoise_lane</code> (acquisition_strategy.py) — <span class="doc-comment-inline">Run GreyNoise intelligence lane — mass scanner classification.</span></li>
<li><code>_extract_cids_from_text</code> (__init__.py) — <span class="doc-comment-inline">Extract unique explicit CIDs from arbitrary text. Bounded dedup.</span></li>
<li><code>prewarm_async</code> (sidecar_orchestrator.py)
<details><summary>ISSUE #22: Parallel pre-warm of SidecarRegistry adapters.</summary>
<div class="doc-comment">
<p>ISSUE #22: Parallel pre-warm of SidecarRegistry adapters.</p>
<p></p>
<p>Runs BEFORE first run_advisory_runner() call to overlap</p>
<p>import costs (academic GLiNER=200ms, dht cryptography=150ms).</p>
<p></p>
<p>Idempotent: only runs once.</p>
</div>
</details>
</li>
<li><code>_init_governor</code> (scheduler.py) — <span class="doc-comment-inline">Initialize M1ResourceGovernor (fail-soft).</span></li>
<li><code>_init_hermes_engine</code> (scheduler.py) — <span class="doc-comment-inline">Initialize Hermes3Engine (fail-soft).</span></li>
<li><code>_init_evidence_log</code> (scheduler.py) — <span class="doc-comment-inline">Initialize EvidenceLog (fail-soft).</span></li>
<li><code>run_in_db_pool</code> (role_based_pools.py)</li>
<li><code>generate_conceptual_domain_candidates</code> (nonfeed_candidate_ledger.py)
<details><summary>F289: Public wrapper — generate domain candidates from conceptual query.</summary>
<div class="doc-comment">
<p>F289: Public wrapper — generate domain candidates from conceptual query.</p>
<p></p>
<p>Returns empty list if regex extraction already found domains,</p>
<p>otherwise calls MLX generator. Use this instead of raw MLX call when</p>
<p>you want to check first whether regex found anything.</p>
<p></p>
<p>Args:</p>
<p>query: Sprint query string</p>
<p></p>
<p>Returns:</p>
<p>List of DomainCandidate (may be empty)</p>
</div>
</details>
</li>
<li><code>is_winding_down</code> (sprint_lifecycle.py)
<details><summary>COMPAT PROPERTY — True when in WINDUP, EXPORT, or TEARDOWN.</summary>
<div class="doc-comment">
<p>COMPAT PROPERTY — True when in WINDUP, EXPORT, or TEARDOWN.</p>
<p></p>
<p>Canonical: use in_phase(SprintPhase.WINDUP) or current_phase in (WINDUP, EXPORT, TEARDOWN).</p>
<p></p>
<p>DIAGNOSTIC ONLY — this property is intended for read-only shadow paths.</p>
<p>Do NOT use for runtime dispatch or path decisions.</p>
<p></p>
<p>F4 metadata:</p>
<p>future_owner: callers (shadow_* modules)</p>
<p>removal_condition: Callers use in_phase() checks</p>
</div>
</details>
</li>
<li><code>_init_core_state</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Phase A: Core config and basic state (13 attrs).</span></li>
<li><code>analyze_one</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>is_new_entry</code> (sprint_scheduler_v1_archived.py)
<details><summary>Return True if entry_hash has not been seen in this sprint.</summary>
<div class="doc-comment">
<p>Return True if entry_hash has not been seen in this sprint.</p>
<p></p>
<p>Uses LRU promotion: on hit, entry is moved to most-recently-used</p>
<p>position so it survives longer under eviction pressure.</p>
</div>
</details>
</li>
<li><code>inject_pivot_planner</code> (sprint_scheduler_v1_archived.py)
<details><summary>Inject PivotPlanner reference (F202G advisory pivot ordering).</summary>
<div class="doc-comment">
<p>Inject PivotPlanner reference (F202G advisory pivot ordering).</p>
<p></p>
<p></p>
<p></p>
<p>F202G: planner is ADVISORY ONLY -- scheduler retains all authority.</p>
<p></p>
<p>Planner generates pivot suggestions from findings; scheduler uses them</p>
<p></p>
<p>as advisory ordering input, NOT as new sprint owner.</p>
<p></p>
<p>All planner calls are fail-soft -- exception or None planner -&gt; no-op.</p>
</div>
</details>
</li>
<li><code>inject_privacy_layer</code> (sprint_scheduler_v1_archived.py)
<details><summary>F26X: Inject PrivacyLayer reference for PII gate.</summary>
<div class="doc-comment">
<p>F26X: Inject PrivacyLayer reference for PII gate.</p>
<p></p>
<p>Preferred over self._layer_manager.privacy -- removes the 7-site</p>
<p>lazy init scattering and makes the dependency explicit.</p>
<p></p>
<p>Fallback: if not injected, the helper still consults</p>
<p>self._layer_manager.privacy (legacy path). Never raises --</p>
<p>exception or None -&gt; no-op (same as other inject_* methods).</p>
<p></p>
<p>OWNERSHIP: caller owns the layer. Scheduler uses it for</p>
<p>_run_privacy_gate() before every async_ingest_findings_batch()</p>
<p>call when HLEDAC_ENABLE_PRIVACY_LAYER=1.</p>
</div>
</details>
</li>
<li><code>enqueue_pivot</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_acq_payload_without_sfo</code> (sprint_entrypoint.py)</li>
<li><code>_feed_budget_to_dict</code> (acquisition_strategy.py)
<details><summary>Convert FeedDominanceBudget (msgspec.Struct, dataclass, or dict) to a JSON-serializable dict.</summary>
<div class="doc-comment">
<p>Convert FeedDominanceBudget (msgspec.Struct, dataclass, or dict) to a JSON-serializable dict.</p>
<p></p>
<p>F216E-FIX: orjson cannot serialize msgspec.Struct directly.</p>
<p>Handles FeedDominanceBudget (msgspec.Struct), dataclass instances, and plain dicts.</p>
<p>Detection order: msgspec.Struct (__struct_fields__) → dataclass (__dataclass_fields__).</p>
</div>
</details>
</li>
<li><code>_wallet_to_findings</code> (acquisition_strategy.py) — <span class="doc-comment-inline">Convert blockchain WalletAnalysis to CanonicalFinding list.</span></li>
<li><code>_looks_like_domain</code> (acquisition_strategy.py) — <span class="doc-comment-inline">Return True if value looks like a domain name (no IP, has TLD).</span></li>
<li><code>_mission_lanes</code> (__init__.py)
<details><summary>F225A: Derive required and optional lanes from mission intent.</summary>
<div class="doc-comment">
<p>F225A: Derive required and optional lanes from mission intent.</p>
<p></p>
<p>Returns (required_lanes, optional_lanes).</p>
<p>Lane priority/reason adjustments only — all safety gates preserved.</p>
</div>
</details>
</li>
<li><code>_wallet_to_findings</code> (__init__.py) — <span class="doc-comment-inline">Convert blockchain WalletAnalysis to CanonicalFinding list.</span></li>
<li><code>_looks_like_domain</code> (__init__.py) — <span class="doc-comment-inline">Return True if value looks like a domain name (no IP, has TLD).</span></li>
<li><code>_run_archive_sidecars</code> (sidecar_orchestrator.py)</li>
<li><code>_graph_stats_available</code> (pivot_planner.py)
<details><summary>F238F: Check if graph_stats represents an explicitly available graph.</summary>
<div class="doc-comment">
<p>F238F: Check if graph_stats represents an explicitly available graph.</p>
<p></p>
<p>graph_stats is None  → graph unavailable (fail-soft fallback)</p>
<p>graph_stats == {}    → graph unavailable (fail-soft fallback)</p>
<p>graph_stats has keys → graph explicitly available (even if values are empty)</p>
<p></p>
<p>Examples:</p>
<p>None                    → False (graph unavailable)</p>
<p>{}                      → False (graph unavailable)</p>
<p>{"domains": set()}      → True  (explicitly empty graph)</p>
<p>{"domains": {"x.com"}}  → True  (graph with data)</p>
</div>
</details>
</li>
<li><code>_ioc_type_from_value</code> (pivot_planner.py) — <span class="doc-comment-inline">Infer IOC type from value string.</span></li>
<li><code>_pattern_mining_runner</code> (sidecar_bus.py) — <span class="doc-comment-inline">F250 pattern mining — detects temporal/behavioral patterns in findings.</span></li>
<li><code>record_observation</code> (graph_adapter.py)</li>
<li><code>_notify_phase_transition</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">F320: Call phase_transition_callback if phase actually changed.</span></li>
<li><code>hermes_budget_s</code> (sprint_scheduler_v1_archived.py)
<details><summary>F253: Adaptive Hermes synthesis budget = 35% of the active window,</summary>
<div class="doc-comment">
<p>F253: Adaptive Hermes synthesis budget = 35% of the active window,</p>
<p>floored at 30s. Prevents short sprints from starving the synthesis</p>
<p>lane while ensuring long sprints reserve enough budget.</p>
<p></p>
<p>Uses final_windup_lead_s (which reflects MLX vs non-MLX adaptive logic).</p>
<p></p>
<p>Examples:</p>
<p>- 60s quick (active=30s) -&gt; 30 (floor)</p>
<p>- 300s deep non-MLX (active=270s) -&gt; 94 (35%)</p>
<p>- 300s deep MLX     (active=210s) -&gt; 73 (35%)</p>
<p>- 600s thoro  (active=420s) -&gt; 147 (35%)</p>
</div>
</details>
</li>
<li><code>_import_exporters</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_init_background_tasks</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Phase G: Background tasks, speculative results, OODA loop (13 attrs).</span></li>
<li><code>_prefetch_urls</code> (sprint_scheduler_v1_archived.py)
<details><summary>ISSUE-121: DNS prefetch during Hermes load — uses unified_transport.prefetch_dns.</summary>
<div class="doc-comment">
<p>ISSUE-121: DNS prefetch during Hermes load — uses unified_transport.prefetch_dns.</p>
<p></p>
<p>Replaces blocking socket.getaddrinfo (M1 thread pool contention)</p>
<p>with async_getaddrinfo via prefetch_dns (LRU-bounded, fire-and-forget).</p>
</div>
</details>
</li>
<li><code>is_duplicate</code> (sprint_scheduler_v1_archived.py)
<details><summary>Check if (source_type, url, title) was already seen in any sprint.</summary>
<div class="doc-comment">
<p>Check if (source_type, url, title) was already seen in any sprint.</p>
<p></p>
<p>F1.1: Uses Rust BloomFilter for O(1) negative pre-check. On positive</p>
<p>(might-be-seen), falls back to LMDB-backed set check.</p>
</div>
</details>
</li>
<li><code>mark_seen</code> (sprint_scheduler_v1_archived.py)
<details><summary>Mark a finding as seen. Flush happens at WINDUP.</summary>
<div class="doc-comment">
<p>Mark a finding as seen. Flush happens at WINDUP.</p>
<p></p>
<p>F1.1: Inserts into Rust BloomFilter (fast negative pre-check) and</p>
<p>Python set (exact LMDB-backed check).</p>
</div>
</details>
</li>
<li><code>inject_source_economics</code> (sprint_scheduler_v1_archived.py)
<details><summary>F160C: Inject pre-built source economics map (external wiring).</summary>
<div class="doc-comment">
<p>F160C: Inject pre-built source economics map (external wiring).</p>
<p></p>
<p></p>
<p></p>
<p>OWNERSHIP: caller owns the economics map. Scheduler updates it</p>
<p></p>
<p>via _update_source_economics() during sprint execution.</p>
<p></p>
<p>Pass None or empty dict to use scheduler's internal dict (default).</p>
</div>
</details>
</li>
<li><code>inject_duckdb_store</code> (sprint_scheduler_v1_archived.py)
<details><summary>F195: Inject DuckDB store reference (canonical write seam).</summary>
<div class="doc-comment">
<p>F195: Inject DuckDB store reference (canonical write seam).</p>
<p></p>
<p></p>
<p></p>
<p>OWNERSHIP: caller owns the store. Scheduler uses it for</p>
<p></p>
<p>async_ingest_findings_batch() on accepted findings.</p>
<p></p>
<p>All calls are fail-soft -- exception or None -&gt; no-op.</p>
</div>
</details>
</li>
<li><code>_resolve_one</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>lookup_threat_entity</code> (acquisition_strategy.py)
<details><summary>Look up threat actor or malware family. Returns (type, primary_name) or None.</summary>
<div class="doc-comment">
<p>Look up threat actor or malware family. Returns (type, primary_name) or None.</p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p>- Bounded: O(1) dict lookup, no iteration over full dict</p>
<p>- Fail-safe: returns None on any error</p>
</div>
</details>
</li>
<li><code>to_dict</code> (shadow_pre_decision.py)</li>
<li><code>_run_one_registered_sidecar</code> (acquisition.py)</li>
<li><code>_score_pivot_graph</code> (pivot_planner.py)
<details><summary>Score a graph traversal pivot.</summary>
<div class="doc-comment">
<p>Score a graph traversal pivot.</p>
<p></p>
<p>F238F: Graph bonuses only apply when graph_stats is explicitly available.</p>
<p>None and {} both mean "graph unavailable" → no novelty bonus, no degree bonus.</p>
</div>
</details>
</li>
<li><code>compute_lane_eligibility</code> (nonfeed_candidate_ledger.py)
<details><summary>F214: Compute lane eligibility from domain candidates.</summary>
<div class="doc-comment">
<p>F214: Compute lane eligibility from domain candidates.</p>
<p></p>
<p>Returns dict:</p>
<p>ct:           CT lane eligible if any domain candidate exists</p>
<p>(.onion excluded — TOR cannot be queried via CT)</p>
<p>doh:          DOH lane eligible if any domain candidate exists</p>
<p>(.onion excluded — DOH does not resolve .onion)</p>
<p>wayback:      WAYBACK lane eligible if any candidates exist</p>
<p>passive_dns:  PASSIVE_DNS lane eligible if any domain candidates exist</p>
</div>
</details>
</li>
<li><code>tick</code> (sprint_lifecycle.py)
<details><summary>Advance the state machine.</summary>
<div class="doc-comment">
<p>Advance the state machine.</p>
<p></p>
<p>Automatically enters WINDUP when remaining_time &lt;= windup_lead_s.</p>
<p>Returns the current phase after ticking.</p>
</div>
</details>
</li>
<li><code>is_windup_phase</code> (sprint_lifecycle.py)
<details><summary>COMPAT ALIAS — forwards to should_enter_windup().</summary>
<div class="doc-comment">
<p>COMPAT ALIAS — forwards to should_enter_windup().</p>
<p></p>
<p>Canonical: use should_enter_windup() directly.</p>
<p></p>
<p>NOTE: This is a time-based heuristic (remaining &lt;= windup_lead_s),</p>
<p>NOT a phase-state check. Use in_phase(SprintPhase.WINDUP) for phase-state.</p>
<p></p>
<p>DIAGNOSTIC ONLY — for read-only shadow paths only.</p>
<p></p>
<p>F4 metadata:</p>
<p>future_owner: synthesis_runner.py</p>
<p>removal_condition: synthesis_runner uses should_enter_windup() from runtime path</p>
</div>
</details>
</li>
<li><code>upsert_ioc</code> (graph_adapter.py)</li>
<li><code>stats</code> (graph_adapter.py) — <span class="doc-comment-inline">Delegate to IOCGraph.graph_stats().</span></li>
<li><code>graph_stats</code> (graph_adapter.py) — <span class="doc-comment-inline">Delegate to IOCGraph.graph_stats().</span></li>
<li><code>pivot</code> (graph_adapter.py)</li>
<li><code>graph_stats</code> (graph_adapter.py) — <span class="doc-comment-inline">STIX graph stats — STIX path.</span></li>
<li><code>export_stix_bundle</code> (graph_adapter.py) — <span class="doc-comment-inline">Export STIX bundle — STIX path.</span></li>
<li><code>_derive_federated_lane</code> (sprint_advisory_runner.py)
<details><summary>Map an accepted finding to a federated lane (surface/dark/archive).</summary>
<div class="doc-comment">
<p>Map an accepted finding to a federated lane (surface/dark/archive).</p>
<p></p>
<p>Uses finding.source_lane attr if available, otherwise classifies by</p>
<p>source_type heuristic. Returns "surface" as the safe default.</p>
</div>
</details>
</li>
<li><code>_init_dedup_and_lifecycle</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Phase B: Persistent dedup, lifecycle adapter, IOC-aware scoring (7 attrs).</span></li>
<li><code>_get_adaptive_priority</code> (sprint_scheduler_v1_archived.py)
<details><summary>Vrátí EMA reward jako priority modifikátor.</summary>
<div class="doc-comment">
<p>Vrátí EMA reward jako priority modifikátor.</p>
<p></p>
<p>Task types s vyšší historickou yield dostávají vyšší prioritu.</p>
</div>
</details>
</li>
<li><code>_prewarm_hermes_sync</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sync wrapper: runs async _prewarm_hermes_for_sprint in dedicated thread.</summary>
<div class="doc-comment">
<p>Sync wrapper: runs async _prewarm_hermes_for_sprint in dedicated thread.</p>
<p></p>
<p>F320: Skip if prewarm_daemon already loaded Hermes at startup.</p>
</div>
</details>
</li>
<li><code>_get_governor_uma</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>analyze_one</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>inject_prefetch_oracle</code> (sprint_scheduler_v1_archived.py)
<details><summary>Inject PrefetchOracleIntegration reference (advisory prefetch ordering).</summary>
<div class="doc-comment">
<p>Inject PrefetchOracleIntegration reference (advisory prefetch ordering).</p>
<p></p>
<p></p>
<p></p>
<p>F200A: oracle is ADVISORY ONLY -- scheduler retains all authority.</p>
<p></p>
<p>Oracle suggests sort scores; scheduler multiplies them into economics sort key.</p>
<p></p>
<p>All oracle calls are fail-soft -- exception or None oracle -&gt; no-op.</p>
</div>
</details>
</li>
<li><code>_arrow_flush_n</code> (sprint_scheduler_v1_archived.py)
<details><summary>Dynamically resolve Arrow flush N based on UMA state.</summary>
<div class="doc-comment">
<p>Dynamically resolve Arrow flush N based on UMA state.</p>
<p></p>
<p>F26X-I: critical/emergency = 2500, warn = 1500, ok = 1000.</p>
<p>Read from _governor at call time (not init), so late binding is safe.</p>
</div>
</details>
</li>
<li><code>query_sprint_results</code> (sprint_scheduler_v1_archived.py)
<details><summary>DuckDB zero-copy query over Parquet files via Arrow.</summary>
<div class="doc-comment">
<p>DuckDB zero-copy query over Parquet files via Arrow.</p>
<p></p>
<p>DuckDB + pyarrow (no polars): DuckDB's read_parquet() + fetch_arrow_table()</p>
<p>gives zero-copy Arrow record batch → pyarrow table → list[dict].</p>
<p>Polars is NOT needed here — only for in-memory feature engineering (F5.4).</p>
</div>
</details>
</li>
<li><code>_handler</code> (sprint_entrypoint.py)</li>
<li><code>_make_rdap_finding</code> (source_finding_bridge.py)</li>
<li><code>_mission_lanes</code> (acquisition_strategy.py)
<details><summary>F225A: Derive required and optional lanes from mission intent.</summary>
<div class="doc-comment">
<p>F225A: Derive required and optional lanes from mission intent.</p>
<p></p>
<p>Returns (required_lanes, optional_lanes).</p>
<p>Lane priority/reason adjustments only — all safety gates preserved.</p>
</div>
</details>
</li>
<li><code>to_dict</code> (shadow_pre_decision.py)</li>
<li><code>run_public_branch</code> (acquisition.py) — <span class="doc-comment-inline">Run public discovery with remaining-time timeout.</span></li>
<li><code>_build_seed_context</code> (acquisition.py) — <span class="doc-comment-inline">Build seed context from query and acquisition plan.</span></li>
<li><code>_run_first_cycle</code> (scheduler.py) — <span class="doc-comment-inline">Run the first acquisition cycle (feed only, stable mode).</span></li>
<li><code>_get_feedback_penalty</code> (pivot_planner.py)
<details><summary>F203G: Get penalty multiplier for a pivot type + ioc type combination.</summary>
<div class="doc-comment">
<p>F203G: Get penalty multiplier for a pivot type + ioc type combination.</p>
<p></p>
<p>Returns 1.0 (no penalty) if no feedback exists or feedback module unavailable.</p>
</div>
</details>
</li>
<li><code>get_role_pools</code> (role_based_pools.py)
<details><summary>Get the global RoleBasedPools singleton.</summary>
<div class="doc-comment">
<p>Get the global RoleBasedPools singleton.</p>
<p></p>
<p>Returns:</p>
<p>RoleBasedPools instance (shared across all callers)</p>
</div>
</details>
</li>
<li><code>_evidence_triage_runner</code> (sidecar_bus.py) — <span class="doc-comment-inline">F202I evidence triage — counts document findings with triage facets. Stats only.</span></li>
<li><code>_sync_adaptive_threshold</code> (resource_governor.py) — <span class="doc-comment-inline">Push memory pressure to Rust adaptive_scheduler for thread pool adaptation.</span></li>
<li><code>transition_to</code> (sprint_lifecycle.py) — <span class="doc-comment-inline">Transition to the given phase if it respects monotonic ordering.</span></li>
<li><code>_transition_to_unlocked</code> (sprint_lifecycle.py)</li>
<li><code>buffer_ioc</code> (graph_adapter.py)</li>
<li><code>_seed_ctx_has_any_items</code> (sprint_scheduler_v1_archived.py)
<details><summary>F271D: Single source of truth for "does this seed context have any</summary>
<div class="doc-comment">
<p>F271D: Single source of truth for "does this seed context have any</p>
<p>shappable items?" -- duck-typed for NonfeedSeedContext and any other</p>
<p>object exposing `domains` / `urls` iterables.</p>
<p></p>
<p>Returns False for None and for contexts with no domains AND no URLs.</p>
<p>Used by public discovery telemetry to gate `seed_context_available`</p>
<p>and `bootstrap_eligible` flags. M1 8GB friendly: pure C-speed</p>
<p>attribute lookup, no Python-level construction.</p>
</div>
</details>
</li>
<li><code>update</code> (sprint_scheduler_v1_archived.py)
<details><summary>Batch update multiple fields at once.</summary>
<div class="doc-comment">
<p>Batch update multiple fields at once.</p>
<p></p>
<p>Example:</p>
<p>builder.update(</p>
<p>cycles_started=5,</p>
<p>aborted=True,</p>
<p>abort_reason="timeout"</p>
<p>)</p>
</div>
</details>
</li>
<li><code>_init_pivot_state</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Phase F: Agentic pivot loop — queue, stats, hypothesis tracking (12 attrs).</span></li>
<li><code>_sync_enrich_and_serialize</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Sync wrapper: enrich + serialize in thread pool. Returns (fid, payload) or None.</span></li>
<li><code>_sync_enrich_and_serialize</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Sync wrapper: enrich + serialize in thread pool. Returns (fid, payload) or None.</span></li>
<li><code>_close_forensics</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Close forensics enricher and LMDB at TEARDOWN.</span></li>
<li><code>_close_multimodal</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Close multimodal enricher and LMDB at TEARDOWN.</span></li>
<li><code>_final_phase_fallback</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Fallback for direct calls to _final_phase (e.g. tests).</span></li>
<li><code>inject_stealth_layer</code> (sprint_scheduler_v1_archived.py)
<details><summary>Inject StealthLayer reference (F260, advisory, default-OFF).</summary>
<div class="doc-comment">
<p>Inject StealthLayer reference (F260, advisory, default-OFF).</p>
<p></p>
<p>Caller (core/__main__.py) wires a StealthLayer produced by</p>
<p>layers.get_stealth_layer() unless --no-stealth is set. None injection</p>
<p>is allowed (caller may pass None as a no-op or to clear a previously</p>
<p>injected layer). All advisory call sites are guarded by</p>
<p>`if self._stealth_layer is not None:` and wrapped in try/except</p>
<p>(fail-soft, M1 invariant).</p>
</div>
</details>
</li>
<li><code>_fatal</code> (sprint_entrypoint.py)
<details><summary>Structured fatal-error handler. Logs _MAIN_FATAL with full traceback,</summary>
<div class="doc-comment">
<p>Structured fatal-error handler. Logs _MAIN_FATAL with full traceback,</p>
<p>then exits with a structured exit code.</p>
<p></p>
<p>Exit code convention (Sprint F350M-R Exit Codes):</p>
<p>0   = clean success</p>
<p>1   = runtime error (unexpected)</p>
<p>2   = config/validation error (e.g. windup_lead guard)</p>
<p>3   = programmer error / regression (NameError, ImportError, AttributeError)</p>
<p>130 = SIGINT (KeyboardInterrupt)</p>
</div>
</details>
</li>
<li><code>_normalize_domain</code> (source_finding_bridge.py)
<details><summary>Normalize a domain extracted from CT data.</summary>
<div class="doc-comment">
<p>Normalize a domain extracted from CT data.</p>
<p></p>
<p>Applies in order:</p>
<p>1. Strip wildcard prefix *.</p>
<p>2. Strip trailing dot</p>
<p>3. Lowercase</p>
</div>
</details>
</li>
<li><code>_normalize_domain</code> (source_finding_bridge.py)
<details><summary>Normalize a domain extracted from CT data.</summary>
<div class="doc-comment">
<p>Normalize a domain extracted from CT data.</p>
<p></p>
<p>Applies in order:</p>
<p>1. Strip wildcard prefix *.</p>
<p>2. Strip trailing dot</p>
<p>3. Lowercase</p>
</div>
</details>
</li>
<li><code>normalize_source_family_name</code> (acquisition_strategy.py)
<details><summary>Normalize a source family name to its canonical lowercase form.</summary>
<div class="doc-comment">
<p>Normalize a source family name to its canonical lowercase form.</p>
<p></p>
<p>Maps mixed-case variants to their canonical lowercase representation so that</p>
<p>"CT", "ct", "Ct" all resolve to "ct", preventing duplicate outcomes for the same</p>
<p>logical family in a single acquisition report.</p>
<p></p>
<p>Canonical families: feed, public, ct, wayback, passive_dns, academic, ipfs, pivot.</p>
</div>
</details>
</li>
<li><code>_derive_exit_reason</code> (acquisition_strategy.py) — <span class="doc-comment-inline">Derive the canonical mission exit reason.</span></li>
<li><code>_hits_to_ct_findings</code> (acquisition_strategy.py) — <span class="doc-comment-inline">Convert crt.sh DiscoveryHit tuple to CanonicalFinding list.</span></li>
<li><code>_ips_to_pdns_findings</code> (acquisition_strategy.py) — <span class="doc-comment-inline">Convert passive DNS IP list to CanonicalFinding list.</span></li>
<li><code>normalize_source_family_name</code> (__init__.py)
<details><summary>Normalize a source family name to its canonical lowercase form.</summary>
<div class="doc-comment">
<p>Normalize a source family name to its canonical lowercase form.</p>
<p></p>
<p>Maps mixed-case variants to their canonical lowercase representation so that</p>
<p>"CT", "ct", "Ct" all resolve to "ct", preventing duplicate outcomes for the same</p>
<p>logical family in a single acquisition report.</p>
<p></p>
<p>Canonical families: feed, public, ct, wayback, passive_dns, academic, ipfs, pivot.</p>
</div>
</details>
</li>
<li><code>_derive_exit_reason</code> (__init__.py) — <span class="doc-comment-inline">Derive the canonical mission exit reason.</span></li>
<li><code>_hits_to_ct_findings</code> (__init__.py) — <span class="doc-comment-inline">Convert crt.sh DiscoveryHit tuple to CanonicalFinding list.</span></li>
<li><code>_ips_to_pdns_findings</code> (__init__.py) — <span class="doc-comment-inline">Convert passive DNS IP list to CanonicalFinding list.</span></li>
<li><code>run</code> (sidecar_protocol_adapters.py) — <span class="doc-comment-inline">Fail-soft wrapper that delegates to the federated sidecar adapter.</span></li>
<li><code>_run_ct_to_passivedns_pivot_advisory</code> (sidecar_orchestrator.py)
<details><summary>R5: CT -&gt; PassiveDNS one-hop pivot advisory.</summary>
<div class="doc-comment">
<p>R5: CT -&gt; PassiveDNS one-hop pivot advisory.</p>
<p></p>
<p>Delegates to SprintScheduler._run_ct_to_passivedns_pivot_advisory().</p>
<p>Fail-soft: errors never crash the sprint.</p>
</div>
</details>
</li>
<li><code>__init__</code> (pivot_planner.py)
<details><summary>Initialize pivot planner.</summary>
<div class="doc-comment">
<p>Initialize pivot planner.</p>
<p></p>
<p>Args:</p>
<p>use_model_scoring: If True, use model-backed scoring via tot_integration.</p>
<p>Requires model_lifecycle_manager for model load/unload.</p>
<p>model_lifecycle_manager: Optional model lifecycle manager for model-backed scoring.</p>
<p>Must be provided if use_model_scoring=True.</p>
</div>
</details>
</li>
<li><code>run_hash_sync</code> (role_based_pools.py)</li>
<li><code>run_regex_sync</code> (role_based_pools.py)</li>
<li><code>_social_identity_surface_runner</code> (sidecar_bus.py) — <span class="doc-comment-inline">F204I: Social identity surface miner.</span></li>
<li><code>apply_decision</code> (resource_governor.py)
<details><summary>Apply governor decision to runtime surfaces (advisory only, fail-soft).</summary>
<div class="doc-comment">
<p>Apply governor decision to runtime surfaces (advisory only, fail-soft).</p>
<p></p>
<p>- Updates FETCH_SEMAPHORE limit via queue (Issue #6: lock-free)</p>
<p>- Tracks denied counts for telemetry</p>
</div>
</details>
</li>
<li><code>begin_sprint</code> (sprint_lifecycle.py)
<details><summary>COMPAT ALIAS — forwards to start().</summary>
<div class="doc-comment">
<p>COMPAT ALIAS — forwards to start().</p>
<p></p>
<p>Canonical: use start() directly.</p>
<p>NOTE: start() transitions BOOT→WARMUP only (not to ACTIVE).</p>
<p>Full activation requires: start() then mark_warmup_done() or transition_to(ACTIVE).</p>
<p>This alias exists to support __main__.py cutover without rewriting call-sites.</p>
<p></p>
<p>F4 metadata:</p>
<p>future_owner: __main__.py</p>
<p>removal_condition: All call-sites migrated to .start()</p>
</div>
</details>
</li>
<li><code>request_export</code> (sprint_lifecycle.py)
<details><summary>COMPAT ALIAS — forwards to mark_export_started().</summary>
<div class="doc-comment">
<p>COMPAT ALIAS — forwards to mark_export_started().</p>
<p></p>
<p>Canonical: use mark_export_started() directly.</p>
<p>Idempotent: skips if already in EXPORT or TEARDOWN (matching utils behavior).</p>
<p></p>
<p>F4 metadata:</p>
<p>future_owner: __main__.py</p>
<p>removal_condition: All call-sites use mark_export_started()</p>
</div>
</details>
</li>
<li><code>request_teardown</code> (sprint_lifecycle.py)
<details><summary>COMPAT ALIAS — forwards to mark_teardown_started().</summary>
<div class="doc-comment">
<p>COMPAT ALIAS — forwards to mark_teardown_started().</p>
<p></p>
<p>Canonical: use mark_teardown_started() directly.</p>
<p>Idempotent: skips if already in TEARDOWN (matching request_export/request_windup).</p>
<p></p>
<p>F4 metadata:</p>
<p>future_owner: __main__.py</p>
<p>removal_condition: All call-sites use mark_teardown_started()</p>
</div>
</details>
</li>
<li><code>is_active</code> (sprint_lifecycle.py)
<details><summary>COMPAT PROPERTY — True when in ACTIVE phase.</summary>
<div class="doc-comment">
<p>COMPAT PROPERTY — True when in ACTIVE phase.</p>
<p></p>
<p>Canonical: use in_phase(SprintPhase.ACTIVE) or current_phase == SprintPhase.ACTIVE.</p>
<p></p>
<p>DIAGNOSTIC ONLY — this property is intended for read-only shadow paths.</p>
<p>Do NOT use for runtime dispatch or path decisions.</p>
<p></p>
<p>F4 metadata:</p>
<p>future_owner: callers (shadow_* modules)</p>
<p>removal_condition: Callers use in_phase(SprintPhase.ACTIVE)</p>
</div>
</details>
</li>
<li><code>upsert_relation</code> (graph_adapter.py)</li>
<li><code>__init__</code> (graph_adapter.py)
<details><summary>Initialize facade with DuckDBShadowStore (or GraphAttachmentStore).</summary>
<div class="doc-comment">
<p>Initialize facade with DuckDBShadowStore (or GraphAttachmentStore).</p>
<p></p>
<p>Args:</p>
<p>store: DuckDBShadowStore instance (has _graph_store()) or</p>
<p>GraphAttachmentStore instance directly.</p>
</div>
</details>
</li>
<li><code>update</code> (scheduler_result.py)
<details><summary>Batch update multiple fields at once.</summary>
<div class="doc-comment">
<p>Batch update multiple fields at once.</p>
<p></p>
<p>Example:</p>
<p>builder.update(</p>
<p>cycles_started=5,</p>
<p>aborted=True,</p>
<p>abort_reason="timeout"</p>
<p>)</p>
</div>
</details>
</li>
<li><code>_init_findings_and_prefetch</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Phase L: All findings, prefetch oracle, temporal predictor, correlation cache (10 attrs).</span></li>
<li><code>economics_sort_key</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_ensure_dedup_loaded</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Block until lazy dedup load completes. Call at first cycle entry.</span></li>
<li><code>_scheduler_result_acquisition_payload</code> (sprint_entrypoint.py)</li>
<li><code>_classify_domain_shape</code> (source_finding_bridge.py) — <span class="doc-comment-inline">Classify the candidate shape for quarantine entry classification.</span></li>
<li><code>_ensure_pre_windup_lane_terminal_states</code> (acquisition.py)</li>
<li><code>_check_zero_findings_alert</code> (acquisition.py) — <span class="doc-comment-inline">Check zero-findings alert after each cycle.</span></li>
<li><code>__init__</code> (sidecar_orchestrator.py)</li>
<li><code>run_in_hash_pool</code> (role_based_pools.py)
<details><summary>Backward-compat shim for run_in_cpu_pool (hash role).</summary>
<div class="doc-comment">
<p>Backward-compat shim for run_in_cpu_pool (hash role).</p>
<p></p>
<p>DEPRECATED: Use RoleBasedPools.run_hash() instead.</p>
</div>
</details>
</li>
<li><code>run_in_regex_pool</code> (role_based_pools.py)
<details><summary>Backward-compat shim for run_in_cpu_pool (regex role).</summary>
<div class="doc-comment">
<p>Backward-compat shim for run_in_cpu_pool (regex role).</p>
<p></p>
<p>DEPRECATED: Use RoleBasedPools.run_regex() instead.</p>
</div>
</details>
</li>
<li><code>run_in_embed_pool</code> (role_based_pools.py)
<details><summary>Backward-compat shim for embedding role.</summary>
<div class="doc-comment">
<p>Backward-compat shim for embedding role.</p>
<p></p>
<p>DEPRECATED: Use RoleBasedPools.run_embed() instead.</p>
</div>
</details>
</li>
<li><code>compute_eligibility_from_candidates</code> (nonfeed_candidate_ledger.py)
<details><summary>F214: Compute lane eligibility from domain candidates.</summary>
<div class="doc-comment">
<p>F214: Compute lane eligibility from domain candidates.</p>
<p></p>
<p>Facade for compute_lane_eligibility — returns the same dict.</p>
<p></p>
<p>Args:</p>
<p>candidates:  List of DomainCandidate</p>
<p></p>
<p>Returns:</p>
<p>Dict with ct, doh, wayback, passive_dns bools.</p>
</div>
</details>
</li>
<li><code>_safe_payload_json</code> (sidecar_bus.py) — <span class="doc-comment-inline">Serialize obj to canonical JSON string, fail-soft.</span></li>
<li><code>__init__</code> (resource_governor.py)</li>
<li><code>record_branch_timeout</code> (resource_governor.py)
<details><summary>Record a branch timeout for EMA tracking.</summary>
<div class="doc-comment">
<p>Record a branch timeout for EMA tracking.</p>
<p></p>
<p>Call this wherever branch_timeout_count is incremented.</p>
<p>EMA formula: ema = alpha * 1.0 + (1 - alpha) * ema</p>
<p>with alpha = 0.3 (responsive without hyperreactivity).</p>
<p></p>
<p>Issue #22: _snapshot_lock prevents torn reads/writes when snapshot()</p>
<p>reads _ema_branch_timeouts concurrently.</p>
</div>
</details>
</li>
<li><code>set_deadline_expired_pre_cycle</code> (sprint_lifecycle.py)
<details><summary>F290-Deadline: Signal that hard deadline expired before first cycle.</summary>
<div class="doc-comment">
<p>F290-Deadline: Signal that hard deadline expired before first cycle.</p>
<p></p>
<p>Called by scheduler when _check_hard_deadline() detects deadline expiry</p>
<p>with cycles_started == 0. This allows windup to fire for cleanup even</p>
<p>though first_cycle_ran=False (F290 guarantee is locally overridden for</p>
<p>the specific case of deadline expiry before any cycle ran).</p>
<p></p>
<p>Invariant: first_cycle_ran remains False (cycle never ran).</p>
<p>Invariant: cycles_started remains 0 (tracked by scheduler result).</p>
</div>
</details>
</li>
<li><code>upsert_ioc</code> (graph_adapter.py)</li>
<li><code>buffer_observation</code> (graph_adapter.py)</li>
<li><code>record_observation</code> (graph_adapter.py)</li>
<li><code>record_observation</code> (graph_adapter.py)</li>
<li><code>with_</code> (sprint_scheduler_v1_archived.py)
<details><summary>Generic setter for any field by name.</summary>
<div class="doc-comment">
<p>Generic setter for any field by name.</p>
<p>Use for fields without dedicated with_ methods.</p>
<p></p>
<p>Example:</p>
<p>builder.with_('quantum_path_seeds', ['seed1', 'seed2'])</p>
</div>
</details>
</li>
<li><code>enrich_one</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>enrich_one</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_flush_dedup</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Flush in-memory hashes to LMDB. Called at WINDUP.</span></li>
<li><code>prioritize_sources</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sort candidates by score -- highest first.</summary>
<div class="doc-comment">
<p>Sort candidates by score -- highest first.</p>
<p></p>
<p>Returns list of source_type strings ordered by priority.</p>
</div>
</details>
</li>
<li><code>inject_security_coordinator</code> (sprint_scheduler_v1_archived.py)
<details><summary>F26X+: Inject UniversalSecurityCoordinator for multi-layer security.</summary>
<div class="doc-comment">
<p>F26X+: Inject UniversalSecurityCoordinator for multi-layer security.</p>
<p></p>
<p></p>
<p>Coordinates: StealthEngine, ThreatIntelligence, QuantumCrypto, ZKP.</p>
<p>Security levels: MINIMAL(1) → STANDARD(2) → HIGH(3) → MAXIMUM(4).</p>
<p></p>
<p>OWNERSHIP: caller owns the coordinator. Scheduler uses it for</p>
<p>_run_security_session() in research/aggressive sprint modes.</p>
</div>
</details>
</li>
<li><code>_get_apt_onion_seeder</code> (sprint_scheduler_v1_archived.py)
<details><summary>Lazily instantiate AptOnionSeeder backed by apt_onion_mapping.yaml.</summary>
<div class="doc-comment">
<p>Lazily instantiate AptOnionSeeder backed by apt_onion_mapping.yaml.</p>
<p></p>
<p>ISSUE-5 FIX: Replaces hardcoded _KNOWN_APT_ONION_DOMAINS substring match with</p>
<p>YAML-backed, confidence-scored mapping. Zero-code-update lifecycle: edit</p>
<p>config/apt_onion_mapping.yaml to add/remove/retire actor→domain mappings.</p>
</div>
</details>
</li>
<li><code>_ooda_apt_domain_mapping</code> (sprint_scheduler_v1_archived.py)
<details><summary>Map threat actor names to .onion infrastructure candidates for OODA bootstrap.</summary>
<div class="doc-comment">
<p>Map threat actor names to .onion infrastructure candidates for OODA bootstrap.</p>
<p></p>
<p>ISSUE-5 FIX: Uses AptOnionSeeder (YAML backend) instead of hardcoded dict.</p>
<p>Only returns confirmed + plausible domains (confidence &gt;= 0.7).</p>
<p>No substring match — requires full token match.</p>
</div>
</details>
</li>
<li><code>_get_live_feed_urls</code> (sprint_entrypoint.py)
<details><summary>Return canonical runtime feed URLs for live sprint path.</summary>
<div class="doc-comment">
<p>Return canonical runtime feed URLs for live sprint path.</p>
<p></p>
<p>Uses get_runtime_feed_seeds() from rss_atom_adapter — the single source</p>
<p>of truth for the runtime RSS/Atom feed surface. Returns only ``curated_seed``</p>
<p>entries sorted by priority descending. This is the accessor the canonical</p>
<p>sprint owner path should use; topology_candidates are excluded by design.</p>
</div>
</details>
</li>
<li><code>_make_blake2b_hex</code> (source_finding_bridge.py)
<details><summary>Deterministic BLAKE2b hex digest — avoids hash() builtin.</summary>
<div class="doc-comment">
<p>Deterministic BLAKE2b hex digest — avoids hash() builtin.</p>
<p></p>
<p>Uses a fixed salt (per-call-site salt param) to separate domains</p>
<p>of the same input value across different finding types.</p>
</div>
</details>
</li>
<li><code>is_lane_enabled</code> (acquisition_strategy.py)
<details><summary>Return True if the given lane is enabled in the acquisition plan.</summary>
<div class="doc-comment">
<p>Return True if the given lane is enabled in the acquisition plan.</p>
<p></p>
<p>Fail-soft: returns False if snapshot is None or lane is not found.</p>
</div>
</details>
</li>
<li><code>get_lane_plan</code> (acquisition_strategy.py)
<details><summary>Return the AcquisitionLanePlan for the given lane, or None if not found.</summary>
<div class="doc-comment">
<p>Return the AcquisitionLanePlan for the given lane, or None if not found.</p>
<p></p>
<p>Fail-soft: returns None if snapshot is None or lane is not found.</p>
</div>
</details>
</li>
<li><code>lane_skip_reason</code> (acquisition_strategy.py)
<details><summary>Return the skip reason for the given lane, or None if lane is enabled or not found.</summary>
<div class="doc-comment">
<p>Return the skip reason for the given lane, or None if lane is enabled or not found.</p>
<p></p>
<p>Fail-soft: returns None if snapshot is None or lane is not found.</p>
</div>
</details>
</li>
<li><code>is_lane_enabled</code> (__init__.py)
<details><summary>Return True if the given lane is enabled in the acquisition plan.</summary>
<div class="doc-comment">
<p>Return True if the given lane is enabled in the acquisition plan.</p>
<p></p>
<p>Fail-soft: returns False if snapshot is None or lane is not found.</p>
</div>
</details>
</li>
<li><code>get_lane_plan</code> (__init__.py)
<details><summary>Return the AcquisitionLanePlan for the given lane, or None if not found.</summary>
<div class="doc-comment">
<p>Return the AcquisitionLanePlan for the given lane, or None if not found.</p>
<p></p>
<p>Fail-soft: returns None if snapshot is None or lane is not found.</p>
</div>
</details>
</li>
<li><code>lane_skip_reason</code> (__init__.py)
<details><summary>Return the skip reason for the given lane, or None if lane is enabled or not found.</summary>
<div class="doc-comment">
<p>Return the skip reason for the given lane, or None if lane is enabled or not found.</p>
<p></p>
<p>Fail-soft: returns None if snapshot is None or lane is not found.</p>
</div>
</details>
</li>
<li><code>__init__</code> (scheduler.py)</li>
<li><code>decayed_score</code> (pivot_planner.py)
<details><summary>Apply exponential decay to base_score based on usage history.</summary>
<div class="doc-comment">
<p>Apply exponential decay to base_score based on usage history.</p>
<p>Older pivots and failed pivots lose priority.</p>
</div>
</details>
</li>
<li><code>_deserialize_envelope</code> (pivot_planner.py) — <span class="doc-comment-inline">Deserialize evidence envelope from finding payload_text.</span></li>
<li><code>sidecar_results_to_source_family_outcomes</code> (sidecar_bus.py) — <span class="doc-comment-inline">F245B: Convert SidecarRunResult list to source_family_outcomes tuple.</span></li>
<li><code>_adjust_workers_locked</code> (resource_governor.py)
<details><summary>Apply worker count change to concurrency primitives.</summary>
<div class="doc-comment">
<p>Apply worker count change to concurrency primitives.</p>
<p></p>
<p>Called while holding self._lock from _worker_adjust_consumer().</p>
</div>
</details>
</li>
<li><code>with_</code> (scheduler_result.py)
<details><summary>Generic setter for any field by name.</summary>
<div class="doc-comment">
<p>Generic setter for any field by name.</p>
<p>Use for fields without dedicated with_ methods.</p>
<p></p>
<p>Example:</p>
<p>builder.with_('quantum_path_seeds', ['seed1', 'seed2'])</p>
</div>
</details>
</li>
<li><code>_is_text_query_without_direct_seeds</code> (nonfeed_seed_runtime.py)
<details><summary>Returns True if the query is a text/threat query without direct IOC seeds.</summary>
<div class="doc-comment">
<p>Returns True if the query is a text/threat query without direct IOC seeds.</p>
<p></p>
<p>A domain/IP/URL query has direct seeds (already extractable from the query).</p>
<p>A text threat query (e.g. 'ransomware group APT29') does not — we need</p>
<p>DuckDB findings to extract seeds.</p>
</div>
</details>
</li>
<li><code>acquire</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Acquire a resource lease. Auto-evicts oldest if at capacity.</span></li>
<li><code>_gc_sprint_sentinel</code> (sprint_scheduler_v1_archived.py)
<details><summary>E4: Re-registers GC telemetry callback when cleanup() is called at sprint end.</summary>
<div class="doc-comment">
<p>E4: Re-registers GC telemetry callback when cleanup() is called at sprint end.</p>
<p></p>
<p>Called by _SprintCleanupHandle.cleanup() (not by GC itself) to ensure</p>
<p>_gc_sprint_callback remains registered in gc.callbacks across sprints.</p>
<p>Telemetry only — does not perform actual cleanup.</p>
</div>
</details>
</li>
<li><code>set_first_cycle_ran</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">F290: Signal that first acquisition cycle has completed.</span></li>
<li><code>_load_next_seeds</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_prefetch_modernbert</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">ISSUE-121: Parallel ModernBERT warmup during Hermes load.</span></li>
<li><code>log_source_hit</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>inject_communication_layer</code> (sprint_scheduler_v1_archived.py)
<details><summary>Inject CommunicationLayer reference (F26X-3, advisory, default-OFF).</summary>
<div class="doc-comment">
<p>Inject CommunicationLayer reference (F26X-3, advisory, default-OFF).</p>
<p></p>
<p>Caller (core/__main__.py) wires a CommunicationLayer produced by</p>
<p>layers.get_communication_layer() unless --no-communication is set.</p>
<p>None injection is allowed (caller may pass None as a no-op or to</p>
<p>clear a previously injected layer).</p>
<p>All advisory call sites are guarded by `if self._communication_layer</p>
<p>is not None:` and wrapped in try/except (fail-soft, M1 invariant).</p>
</div>
</details>
</li>
<li><code>inject_ghost_layer</code> (sprint_scheduler_v1_archived.py)
<details><summary>Inject GhostLayer reference (F260, advisory, default-OFF).</summary>
<div class="doc-comment">
<p>Inject GhostLayer reference (F260, advisory, default-OFF).</p>
<p></p>
<p>Caller (core/__main__.py) wires a GhostLayer produced by</p>
<p>layers.get_ghost_layer() unless --no-ghost is set. None injection is</p>
<p>allowed (caller may pass None as a no-op or to clear a previously</p>
<p>injected layer). All advisory call sites are guarded by</p>
<p>`if self._ghost_layer is not None:` and wrapped in try/except</p>
<p>(fail-soft, M1 invariant).</p>
</div>
</details>
</li>
<li><code>inject_prefetch_pipeline</code> (sprint_scheduler_v1_archived.py)
<details><summary>P3-3: Inject ContinuousPrefetchPipeline reference.</summary>
<div class="doc-comment">
<p>P3-3: Inject ContinuousPrefetchPipeline reference.</p>
<p></p>
<p></p>
<p></p>
<p>Pipeline runs producer-consumer pattern for speculative IOC prefetching.</p>
<p>Starts automatically with sprint if injected.</p>
</div>
</details>
</li>
<li><code>get_analyst_brief</code> (sprint_scheduler_v1_archived.py)
<details><summary>F204E: Return the last generated analyst brief.</summary>
<div class="doc-comment">
<p>F204E: Return the last generated analyst brief.</p>
<p></p>
<p></p>
<p></p>
<p>Returns None if no brief was generated or brief generation failed.</p>
</div>
</details>
</li>
<li><code>get_planned_pivots</code> (sprint_scheduler_v1_archived.py)
<details><summary>F202G: Return last planned pivots for diagnostics.</summary>
<div class="doc-comment">
<p>F202G: Return last planned pivots for diagnostics.</p>
<p></p>
<p></p>
<p></p>
<p>Returns empty list if no pivots were planned or planner failed.</p>
</div>
</details>
</li>
<li><code>_buffer_ioc_pivot</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Wrapper: buffer IOC to graph and enqueue for further pivoting.</span></li>
<li><code>detect_sprint_tier</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Detect sprint tier from duration in seconds.</span></li>
<li><code>_pick_best_terminal</code> (acquisition_strategy.py) — <span class="doc-comment-inline">Pick the highest-priority terminal_state from a list of same-family outcomes.</span></li>
<li><code>_pick_best_terminal</code> (__init__.py) — <span class="doc-comment-inline">Pick the highest-priority terminal_state from a list of same-family outcomes.</span></li>
<li><code>run_feed_branch</code> (acquisition.py) — <span class="doc-comment-inline">Run feed sources and return (results, ok, count).</span></li>
<li><code>_get_effective_max_cycles</code> (acquisition.py) — <span class="doc-comment-inline">Adaptive max_cycles based on cycle_time EMA.</span></li>
<li><code>_finalize_result_truth</code> (acquisition.py)</li>
<li><code>run_async</code> (sidecar_protocol_adapters.py)</li>
<li><code>_extract_base_url</code> (sidecar_protocol_adapters.py) — <span class="doc-comment-inline">Extract base URL from query string.</span></li>
<li><code>_extract_domain_from_finding</code> (pivot_planner.py) — <span class="doc-comment-inline">Extract domain IOC from a finding. DEPRECATED: Use _extract_ioc_from_finding.</span></li>
<li><code>_looks_like_domain</code> (pivot_planner.py) — <span class="doc-comment-inline">Check if value looks like a domain name.</span></li>
<li><code>_looks_like_ip</code> (pivot_planner.py) — <span class="doc-comment-inline">Check if string looks like an IP address.</span></li>
<li><code>_looks_like_domain</code> (pivot_planner.py) — <span class="doc-comment-inline">Check if string looks like a domain name (module-level, no self).</span></li>
<li><code>_normalize_defanged_text</code> (nonfeed_candidate_ledger.py)
<details><summary>F214: Normalize defanged text before domain extraction.</summary>
<div class="doc-comment">
<p>F214: Normalize defanged text before domain extraction.</p>
<p></p>
<p>Strips obfuscation markers so regex can match the full domain.</p>
<p>Operates on the whole text to handle mixed content.</p>
</div>
</details>
</li>
<li><code>_is_heavy_blocked</code> (sidecar_bus.py) — <span class="doc-comment-inline">Return (blocked, reason) if a heavy sidecar should be skipped due to RAM pressure.</span></li>
<li><code>record_branch_success</code> (resource_governor.py)
<details><summary>Record a successful branch completion for EMA decay.</summary>
<div class="doc-comment">
<p>Record a successful branch completion for EMA decay.</p>
<p></p>
<p>Decays the EMA toward 0: ema = (1 - alpha) * ema</p>
<p></p>
<p>Issue #22: _snapshot_lock prevents torn reads/writes when snapshot()</p>
<p>reads _ema_branch_timeouts concurrently.</p>
</div>
</details>
</li>
<li><code>buffer_ioc</code> (graph_adapter.py)</li>
<li><code>pivot</code> (graph_adapter.py)</li>
<li><code>buffer_ioc</code> (graph_adapter.py)</li>
<li><code>pivot</code> (graph_adapter.py)</li>
<li><code>find_connected</code> (graph_adapter.py) — <span class="doc-comment-inline">Graph traversal — analytics path (_ioc_graph).</span></li>
<li><code>upsert_ioc_batch</code> (graph_adapter.py) — <span class="doc-comment-inline">Batch upsert IOCs — analytics path.</span></li>
<li><code>find_connected_batch</code> (graph_adapter.py) — <span class="doc-comment-inline">Batch graph traversal — analytics path.</span></li>
<li><code>get_top_nodes_by_degree</code> (graph_adapter.py) — <span class="doc-comment-inline">Top nodes by degree — analytics path.</span></li>
<li><code>export_edge_list</code> (graph_adapter.py) — <span class="doc-comment-inline">Export edge list — analytics path.</span></li>
<li><code>stats</code> (graph_adapter.py) — <span class="doc-comment-inline">Graph stats — analytics path.</span></li>
<li><code>flush_buffers</code> (graph_adapter.py) — <span class="doc-comment-inline">Flush buffered IOCs — truth-write path.</span></li>
<li><code>_should_allow_low_quality_seed_for_profile</code> (nonfeed_seed_runtime.py)
<details><summary>F241B: Return True if low-quality / DROP seeds should still be used</summary>
<div class="doc-comment">
<p>F241B: Return True if low-quality / DROP seeds should still be used</p>
<p>for lane unlock. Only true for nonfeed_diagnostic profile.</p>
<p></p>
<p>deep_osint_m1: False — DROP seeds must not unlock CT/DOH/WAYBACK/PASSIVE_DNS.</p>
<p>nonfeed_diagnostic: True — all seeds kept for diagnostic purposes, but</p>
<p>quality telemetry is surfaced so operators can see what was filtered.</p>
<p>default: False — conservative default.</p>
</div>
</details>
</li>
<li><code>_release</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Internal release — called by ResourceLease.release() or evict.</span></li>
<li><code>cleanup</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Explicit cleanup — deterministic, no GC dependency. Idempotent.</span></li>
<li><code>get_lane_stats</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_broadcast_start</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_prewarm_mlx_embed</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">F275-5: Persistent prewarm with marker-based cache detection.</span></li>
<li><code>dht_lookup</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Lookup single info_hash via DHT singleton.</span></li>
<li><code>_query_one</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_query_one</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_get_otel_tracer</code> (sidecar_orchestrator.py) — <span class="doc-comment-inline">Lazily get OTel tracer for sidecar spans.</span></li>
<li><code>_run_bgp_advisory_sidecar</code> (sidecar_orchestrator.py) — <span class="doc-comment-inline">F234: BGP advisory sidecar for ASN/path analysis. Fail-soft.</span></li>
<li><code>_run_wayback_cdx_deep_sidecar</code> (sidecar_orchestrator.py) — <span class="doc-comment-inline">F234: Deep Wayback CDX analysis for URL history. Fail-soft.</span></li>
<li><code>_get_available_memory_gib</code> (role_based_pools.py) — <span class="doc-comment-inline">Get available system memory in GiB (M1 8GB UMA-aware).</span></li>
<li><code>to_source_family_outcomes</code> (sidecar_bus.py)</li>
<li><code>upsert_relation</code> (graph_adapter.py)</li>
<li><code>checkpoint</code> (graph_adapter.py) — <span class="doc-comment-inline">Flush WAL — analytics path.</span></li>
<li><code>bounded_step</code> (sprint_advisory_runner.py) — <span class="doc-comment-inline">Run a step with semaphore-bounded concurrency.</span></li>
<li><code>release</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_init_duckdb_pipeline</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Phase C: DuckDB write pipeline — producer-consumer queue (5 attrs).</span></li>
<li><code>_grab_one</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_enqueue_duckdb_write</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">F285: Enqueue a DuckDB write batch. Returns True if enqueued, False if queue full.</span></li>
<li><code>_run_enhanced_research_async</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Async wrapper -- runs deep research advisory with 180s timeout.</span></li>
<li><code>get_prefetch_pipeline_stats</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">P3-3: Return pipeline statistics if pipeline is injected.</span></li>
<li><code>_speculative_run</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_reset_circuit_breakers</code> (sprint_entrypoint.py) — <span class="doc-comment-inline">Reset warmup counters on all domain circuit breakers — O(n) where n&lt;100.</span></li>
<li><code>_has_explicit_cid</code> (acquisition_strategy.py) — <span class="doc-comment-inline">Return True if value is an explicit IPFS CID (CIDv0 or CIDv1 base32).</span></li>
<li><code>_lc</code> (acquisition_strategy.py) — <span class="doc-comment-inline">Apply lane-specific concurrency adjustments on top of base.</span></li>
<li><code>_base_concurrency</code> (acquisition_strategy.py) — <span class="doc-comment-inline">Return base concurrency based on hardware state.</span></li>
<li><code>_lane_concurrency</code> (acquisition_strategy.py) — <span class="doc-comment-inline">Apply lane-specific adjustments on top of base concurrency.</span></li>
<li><code>_extract_crypto_from_query</code> (acquisition_strategy.py) — <span class="doc-comment-inline">Extract crypto wallet addresses and hashes from query string.</span></li>
<li><code>_has_explicit_cid</code> (__init__.py) — <span class="doc-comment-inline">Return True if value is an explicit IPFS CID (CIDv0 or CIDv1 base32).</span></li>
<li><code>_lc</code> (__init__.py) — <span class="doc-comment-inline">Apply lane-specific concurrency adjustments on top of base.</span></li>
<li><code>_base_concurrency</code> (__init__.py) — <span class="doc-comment-inline">Return base concurrency based on hardware state.</span></li>
<li><code>_lane_concurrency</code> (__init__.py) — <span class="doc-comment-inline">Apply lane-specific adjustments on top of base concurrency.</span></li>
<li><code>_extract_crypto_from_query</code> (__init__.py) — <span class="doc-comment-inline">Extract crypto wallet addresses and hashes from query string.</span></li>
<li><code>is_diagnostic_only</code> (shadow_pre_decision.py)
<details><summary>PreDecisionSummary is DIAGNOSTIC ONLY — not a truth store.</summary>
<div class="doc-comment">
<p>PreDecisionSummary is DIAGNOSTIC ONLY — not a truth store.</p>
<p></p>
<p>This class method confirms the artifact must NOT be written</p>
<p>to production ledgers or used as runtime truth.</p>
<p>Must NOT participate in control flow decisions.</p>
</div>
</details>
</li>
<li><code>_ensure_nonfeed_predispatch_before_finalization</code> (acquisition.py)</li>
<li><code>_ensure_mandatory_nonfeed_before_return</code> (acquisition.py)</li>
<li><code>_extract_cids</code> (sidecar_protocol_adapters.py) — <span class="doc-comment-inline">Extract IPFS CIDs from findings.</span></li>
<li><code>_extract_targets</code> (sidecar_protocol_adapters.py) — <span class="doc-comment-inline">Extract domains/emails from findings for leak search.</span></li>
<li><code>_score_pivot_identity</code> (pivot_planner.py) — <span class="doc-comment-inline">Score an identity pivot based on IOC type and confidence.</span></li>
<li><code>_looks_like_ip</code> (pivot_planner.py) — <span class="doc-comment-inline">Check if value looks like an IP address.</span></li>
<li><code>_get_mlx_active_memory_gib</code> (role_based_pools.py) — <span class="doc-comment-inline">Get active MLX Metal memory in GiB (from MLX runtime).</span></li>
<li><code>_check_db_ram_budget</code> (role_based_pools.py)
<details><summary>Check if DuckDB budget allows new work.</summary>
<div class="doc-comment">
<p>Check if DuckDB budget allows new work.</p>
<p></p>
<p>M1 8GB: DuckDB in-process uses ~100MB per connection.</p>
<p>We cap at 2 concurrent writers.</p>
</div>
</details>
</li>
<li><code>_is_ip_literal</code> (nonfeed_candidate_ledger.py) — <span class="doc-comment-inline">F214: Return True if domain is an IP address literal (IPv4 or IPv6).</span></li>
<li><code>_ensure_consumer_running</code> (resource_governor.py)
<details><summary>Start the worker-adjust consumer task if not already running.</summary>
<div class="doc-comment">
<p>Start the worker-adjust consumer task if not already running.</p>
<p></p>
<p>Called by evaluate() / evaluate_adaptive() / apply_decision() before</p>
<p>enqueuing a request. Idempotent — safe to call multiple times.</p>
</div>
</details>
</li>
<li><code>remove_phase_exit_callback</code> (sprint_lifecycle.py)</li>
<li><code>request_abort</code> (sprint_lifecycle.py)
<details><summary>Signal that the sprint should abort.</summary>
<div class="doc-comment">
<p>Signal that the sprint should abort.</p>
<p></p>
<p>Does NOT add a new phase — abort flags are tracked separately.</p>
<p>The manager can transition directly to TEARDOWN via transition_to.</p>
</div>
</details>
</li>
<li><code>mark_teardown_started</code> (sprint_lifecycle.py)</li>
<li><code>entered_phase_at</code> (sprint_lifecycle.py)
<details><summary>Monotonic timestamp when the given phase was first entered.</summary>
<div class="doc-comment">
<p>Monotonic timestamp when the given phase was first entered.</p>
<p></p>
<p>Returns None if the phase has never been reached.</p>
<p></p>
<p>DIAGNOSTIC ONLY — read-only seam for observability.</p>
</div>
</details>
</li>
<li><code>_merge_outcomes</code> (sprint_advisory_runner.py)
<details><summary>Merge two AdvisoryRunOutcome objects, taking the last-seen value for each field.</summary>
<div class="doc-comment">
<p>Merge two AdvisoryRunOutcome objects, taking the last-seen value for each field.</p>
<p></p>
<p>Used when parallel advisory steps return partial outcomes that need to be</p>
<p>combined into a single coherent result. Non-count fields (bool/str) use</p>
<p>the value from `other` if it differs from the base default.</p>
</div>
</details>
</li>
<li><code>_advisory_log_stats</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Snapshot of advisory dedup state for diagnostics/tests.</span></li>
<li><code>remaining_time</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">runtime: remaining_time(). Fallback: 0.0.</span></li>
<li><code>is_terminal</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">runtime: is_terminal(). Fallback: _current_phase == TEARDOWN.</span></li>
<li><code>recommended_tool_mode</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">runtime: recommended_tool_mode(). Fallback: 'normal'.</span></li>
<li><code>_init_planner_and_advisory</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Phase O: Pivot planner and advisory state (5 attrs).</span></li>
<li><code>_is_source_in_cooldown</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">True if source is in bounded cooldown and cycle hasn't exceeded it.</span></li>
<li><code>_prewarm_modernbert</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_notify_governor_branch_timeout</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">F2-2: Notify governor of branch timeout for EMA tracking.</span></li>
<li><code>_notify_governor_branch_success</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">F2-2: Notify governor of successful branch completion for EMA decay.</span></li>
<li><code>_run_pdns_for_domain</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>inject_temporal_predictor</code> (sprint_scheduler_v1_archived.py)
<details><summary>P3-2: Inject TemporalIOCPredictor reference.</summary>
<div class="doc-comment">
<p>P3-2: Inject TemporalIOCPredictor reference.</p>
<p></p>
<p>Predictor observes findings for time-of-day pattern learning</p>
<p>and provides predict_next_iocs() for ContinuousPrefetchPipeline.</p>
</div>
</details>
</li>
<li><code>get_speculative_dns</code> (sprint_scheduler_v1_archived.py)
<details><summary>Retrieve prefetched DNS results for a domain.</summary>
<div class="doc-comment">
<p>Retrieve prefetched DNS results for a domain.</p>
<p></p>
<p>Returns IP list if prefetch hit, None if miss/unresolved.</p>
<p>Used by pivot planner to skip redundant DNS lookups.</p>
</div>
</details>
</li>
<li><code>_restore</code> (sprint_entrypoint.py)</li>
<li><code>_ts_from_wayback_timestamp</code> (source_finding_bridge.py) — <span class="doc-comment-inline">Convert CDX timestamp (YYYYMMDDHHMMSS) to Unix float.</span></li>
<li><code>_has_threat_indicator</code> (acquisition_strategy.py)
<details><summary>Return True if query contains threat/crime indicators suggesting active investigation.</summary>
<div class="doc-comment">
<p>Return True if query contains threat/crime indicators suggesting active investigation.</p>
<p></p>
<p>Used by required_terminal_lanes() to ensure PUBLIC lane is mandatory for threat</p>
<p>queries even when no domain/IP is present — PUBLIC can discover infrastructure</p>
<p>from text search results alone (no seeds required).</p>
</div>
</details>
</li>
<li><code>_has_threat_indicator</code> (__init__.py)
<details><summary>Return True if query contains threat/crime indicators suggesting active investigation.</summary>
<div class="doc-comment">
<p>Return True if query contains threat/crime indicators suggesting active investigation.</p>
<p></p>
<p>Used by required_terminal_lanes() to ensure PUBLIC lane is mandatory for threat</p>
<p>queries even when no domain/IP is present — PUBLIC can discover infrastructure</p>
<p>from text search results alone (no seeds required).</p>
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
<li><code>_extract_search_terms</code> (sidecar_protocol_adapters.py) — <span class="doc-comment-inline">Extract domain/IOC terms from findings for Fediverse search.</span></li>
<li><code>run_async</code> (sidecar_protocol_adapters.py)</li>
<li><code>_extract_search_terms</code> (sidecar_protocol_adapters.py) — <span class="doc-comment-inline">Extract domain/IOC terms from findings for Gist search.</span></li>
<li><code>_get_sprint_advisory_runner</code> (sidecar_orchestrator.py)</li>
<li><code>_run_ipfs_discovery_sidecar</code> (sidecar_orchestrator.py) — <span class="doc-comment-inline">F229: IPFS discovery — fetch unindexed content from IPFS network. Fail-soft.</span></li>
<li><code>_run_onion_discovery_sidecar</code> (sidecar_orchestrator.py) — <span class="doc-comment-inline">F251: Dark web .onion discovery via Tor. Fail-soft.</span></li>
<li><code>_run_i2p_discovery_sidecar</code> (sidecar_orchestrator.py) — <span class="doc-comment-inline">F2P: I2P .i2p discovery via I2P transport. Fail-soft.</span></li>
<li><code>_run_bgp_enrichment_sidecar</code> (sidecar_orchestrator.py) — <span class="doc-comment-inline">F229: BGP enrichment — AS path analysis for IP/ASN in query. Fail-soft.</span></li>
<li><code>_run_commoncrawl_sidecar</code> (sidecar_orchestrator.py) — <span class="doc-comment-inline">F250F: CommonCrawl CDX domain discovery. Fail-soft.</span></li>
<li><code>_run_banner_grab_sidecar</code> (sidecar_orchestrator.py) — <span class="doc-comment-inline">F229: Banner grab — active TCP probing for service fingerprinting. Fail-soft.</span></li>
<li><code>_run_dht_sidecar</code> (sidecar_orchestrator.py) — <span class="doc-comment-inline">F214Q: DHT torrent discovery via BitTorrent DHT network. Fail-soft.</span></li>
<li><code>_run_digital_ghost_sidecar</code> (sidecar_orchestrator.py) — <span class="doc-comment-inline">F3FORENSICS: Digital ghost detection on file artifacts. Fail-soft.</span></li>
<li><code>_run_steganography_sidecar</code> (sidecar_orchestrator.py) — <span class="doc-comment-inline">F3FORENSICS: Steganography detection on image artifacts. Fail-soft.</span></li>
<li><code>_run_ti_feed_sidecar</code> (sidecar_orchestrator.py) — <span class="doc-comment-inline">F252: TI feed advisory sidecar (NVD + CISA KEV). Fail-soft.</span></li>
<li><code>_prewarm_hermes</code> (scheduler.py) — <span class="doc-comment-inline">Prewarm Hermes model in background.</span></li>
<li><code>_looks_like_domain</code> (pivot_planner.py) — <span class="doc-comment-inline">Check if value looks like a domain name.</span></li>
<li><code>_deduplicate_pivots</code> (pivot_planner.py) — <span class="doc-comment-inline">Deduplicate pivots by (pivot_type, ioc_type, ioc_value), keeping highest score per type.</span></li>
<li><code>_check_gathered</code> (sidecar_bus.py)
<details><summary>Verify no unexpected exceptions leaked through gather(return_exceptions=True).</summary>
<div class="doc-comment">
<p>Verify no unexpected exceptions leaked through gather(return_exceptions=True).</p>
<p>GHOST_INVARIANT: called after every asyncio.gather with return_exceptions=True.</p>
</div>
</details>
</li>
<li><code>_get_model_status</code> (resource_governor.py) — <span class="doc-comment-inline">Read-only model status from canonical lifecycle API.</span></li>
<li><code>mark_export_started</code> (sprint_lifecycle.py)</li>
<li><code>has_reached_phase</code> (sprint_lifecycle.py)
<details><summary>True when the given phase has ever been entered (including current).</summary>
<div class="doc-comment">
<p>True when the given phase has ever been entered (including current).</p>
<p></p>
<p>DIAGNOSTIC ONLY — read-only seam for observability.</p>
<p>Does NOT mutate state. Does not check ordering.</p>
</div>
</details>
</li>
<li><code>__init__</code> (graph_adapter.py)
<details><summary>Initialize adapter with existing DuckPGQGraph.</summary>
<div class="doc-comment">
<p>Initialize adapter with existing DuckPGQGraph.</p>
<p></p>
<p>Args:</p>
<p>graph: DuckPGQGraph instance to wrap</p>
</div>
</details>
</li>
<li><code>__init__</code> (graph_adapter.py)
<details><summary>Initialize adapter with existing IOCGraph.</summary>
<div class="doc-comment">
<p>Initialize adapter with existing IOCGraph.</p>
<p></p>
<p>Args:</p>
<p>graph: IOCGraph instance to wrap</p>
</div>
</details>
</li>
<li><code>_with_federated_outcome</code> (sprint_advisory_runner.py)
<details><summary>Build a new AdvisoryRunOutcome with federated fields populated.</summary>
<div class="doc-comment">
<p>Build a new AdvisoryRunOutcome with federated fields populated.</p>
<p></p>
<p>Frozen dataclass requires rebuilding the whole object. This helper</p>
<p>keeps the call sites DRY.</p>
</div>
</details>
</li>
<li><code>tick</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">runtime: tick() returns SprintPhase. Fallback: 'UNKNOWN' phase string.</span></li>
<li><code>set_pre_loop_cost_s</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">F288: Set pre_loop_cost_s on the underlying lifecycle if supported.</span></li>
<li><code>set_windup_lead_s</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">O4-FIX: Set windup_lead_s on the underlying lifecycle if supported.</span></li>
<li><code>set_deadline_expired_pre_cycle</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">F290-Deadline: Signal that hard deadline expired before first cycle.</span></li>
<li><code>_abort_requested</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_abort_reason</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_init_pending_extractions</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Phase E: In-flight pattern-extraction tracker — F273C bounded ring (3 attrs).</span></li>
<li><code>_init_hermes_engine</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Phase J: Hermes engine, memory manager, M1 governor, fetch semaphore (5 attrs).</span></li>
<li><code>_run_prewarm</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_prewarm_patterns_sync</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_cb</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_init_evidence_chain</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_prewarm_hermes</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_try_public</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_try_ct</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_run_pdns_for_domain</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Run PassiveDNS for one domain, return (domain, ips, outcome).</span></li>
<li><code>request_immediate_abort</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Sprint 8RA: Request immediate abort (called from UMA EMERGENCY callback).</span></li>
<li><code>_update_latency_ema</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Update EMA for domain fetch latency. Bounded to _MAX_FETCH_LATENCY_EMA entries.</span></li>
<li><code>inject_ioc_graph</code> (sprint_scheduler_v1_archived.py)
<details><summary>Inject IOCGraph reference for pivot operations.</summary>
<div class="doc-comment">
<p>Inject IOCGraph reference for pivot operations.</p>
<p></p>
<p>F300-GRAPH: DuckPGQGraph is the sole canonical graph backend.</p>
<p>KuzuGraphBridge wiring removed — it is no longer used.</p>
</div>
</details>
</li>
<li><code>get_graph</code> (sprint_scheduler_v1_archived.py)
<details><summary>Get IOC graph for read operations (stats, export, injection).</summary>
<div class="doc-comment">
<p>Get IOC graph for read operations (stats, export, injection).</p>
<p></p>
<p>Returns the DuckPGQGraph instance used for analytics.</p>
<p>Used by windup_engine and other consumers that need graph access.</p>
</div>
</details>
</li>
<li><code>_get_duckdb_con</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Singleton DuckDB connection -- initialized once.</span></li>
<li><code>__post_init__</code> (acquisition_strategy.py)</li>
<li><code>_get_ct_adapter</code> (acquisition_strategy.py) — <span class="doc-comment-inline">Return the CT adapter: real call_crtsh or the patched fake.</span></li>
<li><code>__post_init__</code> (__init__.py)</li>
<li><code>_get_ct_adapter</code> (__init__.py) — <span class="doc-comment-inline">Return the CT adapter: real call_crtsh or the patched fake.</span></li>
<li><code>_assess_export_quality</code> (shadow_pre_decision.py) — <span class="doc-comment-inline">Assess export data quality for windup synthesis.</span></li>
<li><code>_drain_pending_pattern_extractions</code> (acquisition.py)</li>
<li><code>_run_ioc_cooccurrence_sidecar</code> (acquisition.py)</li>
<li><code>_run_epistemic_gap_advisory</code> (acquisition.py)</li>
<li><code>is_available</code> (sidecar_protocol_adapters.py) — <span class="doc-comment-inline">Env-gated check delegating to the federated module's gate.</span></li>
<li><code>_pivot_type_for_ioc</code> (pivot_planner.py) — <span class="doc-comment-inline">Map IOC type to primary pivot type.</span></li>
<li><code>_looks_like_hash</code> (pivot_planner.py) — <span class="doc-comment-inline">Check if string looks like a hash.</span></li>
<li><code>__post_init__</code> (nonfeed_candidate_ledger.py)</li>
<li><code>add_feed_candidate</code> (nonfeed_candidate_ledger.py)
<details><summary>F214: Record a FEED-sourced domain candidate for non-domain queries.</summary>
<div class="doc-comment">
<p>F214: Record a FEED-sourced domain candidate for non-domain queries.</p>
<p></p>
<p>Adds to FEED family with stage=discovered.</p>
</div>
</details>
</li>
<li><code>_get_cached_mlx_engine</code> (nonfeed_candidate_ledger.py) — <span class="doc-comment-inline">Get or create cached DeepHermes3Engine instance (P1-4 optimization).</span></li>
<li><code>_sidecar_profile_allows</code> (sidecar_bus.py) — <span class="doc-comment-inline">Return (allowed, reason). F240A.</span></li>
<li><code>start</code> (sprint_lifecycle.py) — <span class="doc-comment-inline">Transition from BOOT → WARMUP and record start time.</span></li>
<li><code>is_terminal</code> (sprint_lifecycle.py) — <span class="doc-comment-inline">True when the manager has reached TEARDOWN or has aborted and completed.</span></li>
<li><code>current_phase</code> (sprint_lifecycle.py)
<details><summary>Public read-only access to current phase.</summary>
<div class="doc-comment">
<p>Public read-only access to current phase.</p>
<p></p>
<p>Canonical alternative to direct _current_phase field access.</p>
</div>
</details>
</li>
<li><code>in_phase</code> (sprint_lifecycle.py)
<details><summary>True when manager is in the given phase.</summary>
<div class="doc-comment">
<p>True when manager is in the given phase.</p>
<p></p>
<p>Convenience helper — equivalent to current_phase == phase.</p>
</div>
</details>
</li>
<li><code>_is_valid_transition</code> (sprint_lifecycle.py) — <span class="doc-comment-inline">Allow TEARDOWN from any phase (abort path).</span></li>
<li><code>stats</code> (graph_adapter.py) — <span class="doc-comment-inline">Delegate to DuckPGQGraph.stats().</span></li>
<li><code>graph_stats</code> (graph_adapter.py) — <span class="doc-comment-inline">DuckPGQGraph: graph_stats (F271).</span></li>
<li><code>find_connected</code> (graph_adapter.py)</li>
<li><code>upsert_ioc_batch</code> (graph_adapter.py) — <span class="doc-comment-inline">Delegate batch upsert to IOCGraph.upsert_ioc_batch().</span></li>
<li><code>find_connected_batch</code> (graph_adapter.py)</li>
<li><code>_get_graph_service_registry</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Lazy initialization — avoids import-order issues.</span></li>
<li><code>get_sprint_ctx</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Get current sprint context. Raise if not established.</span></li>
<li><code>_init_source_tracking</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Phase D: Source quality feedback and feed dominance tracking (4 attrs).</span></li>
<li><code>_fetch_cid</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>request_early_windup</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Sprint 8RA: Request early wind-down (called from UMA CRITICAL callback).</span></li>
<li><code>_final_phase</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Mark teardown on lifecycle.</span></li>
<li><code>_render_sync</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_sync_resolve</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_on_cycle</code> (sprint_entrypoint.py)</li>
<li><code>filter</code> (sprint_entrypoint.py)</li>
<li><code>_safe_list</code> (source_finding_bridge.py)</li>
<li><code>_mission_cap_active</code> (acquisition_strategy.py) — <span class="doc-comment-inline">F227D: Return True when mission-aware cap should be evaluated.</span></li>
<li><code>_int</code> (acquisition_strategy.py)</li>
<li><code>_float</code> (acquisition_strategy.py)</li>
<li><code>reset</code> (sidecar_orchestrator.py) — <span class="doc-comment-inline">Clear in-memory state. Called on sprint teardown.</span></li>
<li><code>__new__</code> (scheduler.py)</li>
<li><code>inject_evidence_log</code> (scheduler.py) — <span class="doc-comment-inline">Inject a pre-initialized EvidenceLog (wraps in InitResult.success).</span></li>
<li><code>inject_duckdb_store</code> (scheduler.py) — <span class="doc-comment-inline">Inject a pre-initialized DuckDBShadowStore (wraps in InitResult.success).</span></li>
<li><code>_score_pivot_leak</code> (pivot_planner.py) — <span class="doc-comment-inline">Score a leak pivot.</span></li>
<li><code>_extract_domain_from_url</code> (pivot_planner.py) — <span class="doc-comment-inline">Extract domain from URL.</span></li>
<li><code>_extract_root_domain</code> (pivot_planner.py) — <span class="doc-comment-inline">Extract root domain from subdomain.</span></li>
<li><code>_is_cancelled_tree</code> (sidecar_bus.py)</li>
<li><code>_is_cancelled_tree</code> (sidecar_bus.py)</li>
<li><code>create_sidecar_bus</code> (sidecar_bus.py) — <span class="doc-comment-inline">Factory: create a pre-registered FindingSidecarBus.</span></li>
<li><code>get_governor</code> (resource_governor.py) — <span class="doc-comment-inline">Get or create the singleton M1ResourceGovernor.</span></li>
<li><code>find_connected</code> (graph_adapter.py) — <span class="doc-comment-inline">Delegate graph traversal to DuckPGQGraph.find_connected().</span></li>
<li><code>upsert_ioc_batch</code> (graph_adapter.py) — <span class="doc-comment-inline">Delegate batch upsert to DuckPGQGraph.upsert_ioc_batch().</span></li>
<li><code>find_connected_batch</code> (graph_adapter.py) — <span class="doc-comment-inline">Delegate batch traversal to DuckPGQGraph.find_connected_batch().</span></li>
<li><code>get_top_nodes_by_degree</code> (graph_adapter.py) — <span class="doc-comment-inline">Delegate to DuckPGQGraph.get_top_nodes_by_degree().</span></li>
<li><code>export_edge_list</code> (graph_adapter.py) — <span class="doc-comment-inline">Delegate to DuckPGQGraph.export_edge_list().</span></li>
<li><code>checkpoint</code> (graph_adapter.py) — <span class="doc-comment-inline">Delegate to DuckPGQGraph.checkpoint().</span></li>
<li><code>flush_buffers</code> (graph_adapter.py) — <span class="doc-comment-inline">DuckPGQGraph: flush via flush_buffers (F272).</span></li>
<li><code>export_stix_bundle</code> (graph_adapter.py) — <span class="doc-comment-inline">DuckPGQGraph: STIX export via DuckDB (F271).</span></li>
<li><code>flush_buffers</code> (graph_adapter.py) — <span class="doc-comment-inline">Delegate buffer flush to IOCGraph.flush_buffers().</span></li>
<li><code>export_stix_bundle</code> (graph_adapter.py) — <span class="doc-comment-inline">Delegate STIX export to IOCGraph.export_stix_bundle().</span></li>
<li><code>__init__</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>release</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Release resource — idempotent, safe to call multiple times.</span></li>
<li><code>__init__</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_sanitize_debug_text</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Strip raw HTML/script from debug strings -- do not expose page content.</span></li>
<li><code>allocate</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>get_utilization</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>__setattr__</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_init_fetch_latency_ema</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Phase H: Adaptive timeout EMA — per-domain latency learning (3 attrs).</span></li>
<li><code>_init_layers</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Phase N: Privacy, stealth, ghost layers + DOH adapter + circuit breakers (3 attrs).</span></li>
<li><code>_init_target_and_metrics</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Phase P: Target memory service, analyst workbench, metrics registry (2 attrs).</span></li>
<li><code>oracle_sort_key</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_on_phase_transition</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>get_adaptive_timeout</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Get adaptive timeout based on EMA latency. Clamped to [5, 30]s.</span></li>
<li><code>inject_policy_manager</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Inject SprintPolicyManager reference (opt-in RL layer).</span></li>
<li><code>_make_sprint_id</code> (sprint_entrypoint.py) — <span class="doc-comment-inline">Generate collision-resistant sprint ID using ns timestamp + short uuid suffix.</span></li>
<li><code>_derive_top_source</code> (sprint_entrypoint.py) — <span class="doc-comment-inline">Return source with most hits, or empty string if no data.</span></li>
<li><code>_safe_str</code> (source_finding_bridge.py)</li>
<li><code>is_mission_profile</code> (acquisition_strategy.py) — <span class="doc-comment-inline">Return True when the profile is any nonfeed_diagnostic variant.</span></li>
<li><code>is_mission_profile</code> (__init__.py) — <span class="doc-comment-inline">Return True when the profile is any nonfeed_diagnostic variant.</span></li>
<li><code>_get_advisory_semaphore</code> (sidecar_orchestrator.py)</li>
<li><code>_get_plugin_semaphore</code> (sidecar_orchestrator.py)</li>
<li><code>__repr__</code> (scheduler.py)</li>
<li><code>_recon_one</code> (sidecar_bus.py)</li>
<li><code>__init__</code> (sidecar_bus.py)</li>
<li><code>bump_counter</code> (scheduler_result.py)</li>
<li><code>__setattr__</code> (scheduler_result.py)</li>
<li><code>__init__</code> (sprint_advisory_runner.py)</li>
<li><code>obj</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>__init__</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>canonical_lane_name</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Normalize lane to UPPERCASE string -- handles Enum values and plain strings.</span></li>
<li><code>_reset_advisory_log_dedup</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Clear the LRU dedup state. Call between test runs or sprint cycles.</span></li>
<li><code>__init__</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>cycles_started</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>cycles_completed</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>consecutive_empty_cycles</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>unique_entry_hashes_seen</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>duplicate_entry_hashes_skipped</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>hard_deadline_checked_count</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>windup_guard_call_count</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>windup_guard_callback_supplied_count</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>windup_guard_callback_executed_count</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>policy_quality_feedback_calls</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>policy_quality_feedback_errors</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>ipfs_cids_attempted</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>multimodal_enriched_findings</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>feed_suppression_count</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>forensics_enriched_ct_findings</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>acquisition_lanes_skipped</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_set</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Internal setter — bypasses __setattr__ for speed.</span></li>
<li><code>__getattr__</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_import_live_feed_pipeline</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_import_live_public_pipeline</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_import_correlate_findings</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_import_hypothesis_engine</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_get_dedup_lmdb_path</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_get_forensics_lmdb_path</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_get_multimodal_lmdb_path</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_session_provider</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_session_provider</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_safe_dict_str</code> (source_finding_bridge.py)</li>
<li><code>_extract_ips_from_query</code> (acquisition_strategy.py) — <span class="doc-comment-inline">Extract IP address strings from query.</span></li>
<li><code>_extract_ips_from_query</code> (__init__.py) — <span class="doc-comment-inline">Extract IP address strings from query.</span></li>
<li><code>is_available</code> (sidecar_protocol_adapters.py) — <span class="doc-comment-inline">Available only when feature flag is enabled.</span></li>
<li><code>_run_gopher_sidecar</code> (sidecar_orchestrator.py) — <span class="doc-comment-inline">F214R: Gopher URL discovery. No-op until GopherLane is implemented.</span></li>
<li><code>inject_prefetch_oracle</code> (scheduler.py)</li>
<li><code>inject_prefetch_pipeline</code> (scheduler.py)</li>
<li><code>inject_temporal_predictor</code> (scheduler.py)</li>
<li><code>inject_pivot_planner</code> (scheduler.py)</li>
<li><code>inject_analyst_workbench</code> (scheduler.py)</li>
<li><code>inject_forensics_enricher</code> (scheduler.py)</li>
<li><code>inject_enrichment_services</code> (scheduler.py)</li>
<li><code>inject_privacy_layer</code> (scheduler.py)</li>
<li><code>inject_ioc_graph</code> (scheduler.py)</li>
<li><code>record_success</code> (pivot_planner.py) — <span class="doc-comment-inline">Record a successful pivot use.</span></li>
<li><code>record_failure</code> (pivot_planner.py) — <span class="doc-comment-inline">Record a failed pivot use.</span></li>
<li><code>_pivot_type_for_ioc</code> (pivot_planner.py) — <span class="doc-comment-inline">Map IOC type to pivot type.</span></li>
<li><code>_get_embed_lock</code> (role_based_pools.py)</li>
<li><code>_get_db_lock</code> (role_based_pools.py)</li>
<li><code>_get_hash_lock</code> (role_based_pools.py)</li>
<li><code>_get_regex_lock</code> (role_based_pools.py)</li>
<li><code>_get_async_io_lock</code> (role_based_pools.py)</li>
<li><code>add</code> (nonfeed_candidate_ledger.py)</li>
<li><code>records</code> (nonfeed_candidate_ledger.py) — <span class="doc-comment-inline">Return immutable snapshot of all records (oldest first).</span></li>
<li><code>count_by_stage</code> (nonfeed_candidate_ledger.py) — <span class="doc-comment-inline">Count records with given stage.</span></li>
<li><code>count_by_family</code> (nonfeed_candidate_ledger.py) — <span class="doc-comment-inline">Count records with given family.</span></li>
<li><code>count_accepted</code> (nonfeed_candidate_ledger.py) — <span class="doc-comment-inline">Count accepted=True records.</span></li>
<li><code>count_quarantine</code> (nonfeed_candidate_ledger.py) — <span class="doc-comment-inline">Count quarantine=True records.</span></li>
<li><code>_sort_key</code> (nonfeed_candidate_ledger.py)</li>
<li><code>register</code> (sidecar_bus.py)</li>
<li><code>_is_active_network_blocked</code> (sidecar_bus.py) — <span class="doc-comment-inline">Return (blocked, reason) if an active-network sidecar should be skipped.</span></li>
<li><code>remaining_time</code> (sprint_lifecycle.py) — <span class="doc-comment-inline">Seconds remaining in the sprint (0 if elapsed).</span></li>
<li><code>_remaining_time_unlocked</code> (sprint_lifecycle.py)</li>
<li><code>_now</code> (sprint_lifecycle.py)</li>
<li><code>_set</code> (scheduler_result.py) — <span class="doc-comment-inline">Internal setter — bypasses __setattr__ for speed.</span></li>
<li><code>__getattr__</code> (scheduler_result.py)</li>
<li><code>acquire</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Acquire GraphService instance for this sprint.</span></li>
<li><code>release</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Release GraphService and cleanup resources after sprint.</span></li>
<li><code>release</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Public release API — idempotent.</span></li>
<li><code>reset_sprint_ctx</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Reset context (call between sprints / for testing).</span></li>
<li><code>_gc_sprint_callback</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">E4: GC per-collection callback -- records generation and collection counts.</span></li>
<li><code>consume</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>cycles_started</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>cycles_completed</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>consecutive_empty_cycles</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>unique_entry_hashes_seen</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>duplicate_entry_hashes_skipped</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>hard_deadline_checked_count</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>windup_guard_call_count</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>windup_guard_callback_supplied_count</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>windup_guard_callback_executed_count</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>policy_quality_feedback_calls</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>policy_quality_feedback_errors</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>ipfs_cids_attempted</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>multimodal_enriched_findings</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>feed_suppression_count</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>forensics_enriched_ct_findings</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>acquisition_lanes_skipped</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>build</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Return the constructed SprintSchedulerResult.</span></li>
<li><code>_field_names</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Reflect field names from SprintSchedulerResult at runtime.</span></li>
<li><code>_get_source_economics</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Return economics state for a source, or None if not yet seen.</span></li>
<li><code>_prune_work_items</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Drop ARCHIVE and OTHER tier items when in prune mode.</span></li>
<li><code>_flush_forensics</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Flush forensics LMDB. Called at WINDUP. No-op if not initialized.</span></li>
<li><code>_flush_multimodal</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Flush multimodal LMDB. Called at WINDUP. No-op if not initialized.</span></li>
<li><code>set_novelty_bonus</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Set novelty bonus: 1.5 if source added new IOC types this sprint.</span></li>
<li><code>inject_enrichment_services</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">F350M: Inject EnrichmentServices (forensics + multimodal unified lifecycle).</span></li>
<li><code>inject_evidence_log</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">F11C: Inject EvidenceLog reference (fail-safe, M1 8GB safe).</span></li>
<li><code>_invalidate_health_cache</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">F270-4.3: Invalidate health_check cache (call on sprint start/end).</span></li>
<li><code>check_source</code> (sprint_entrypoint.py)</li>
<li><code>_strip_url_scheme</code> (source_finding_bridge.py) — <span class="doc-comment-inline">Strip https:// or http:// prefix and trailing slashes.</span></li>
<li><code>_is_wildcard_domain</code> (source_finding_bridge.py) — <span class="doc-comment-inline">Check if domain is a wildcard pattern like *.example.com.</span></li>
<li><code>_make_finding_id</code> (source_finding_bridge.py) — <span class="doc-comment-inline">Generate a stable finding ID from a key string using blake2b.</span></li>
<li><code>is_sentinel</code> (acquisition_strategy.py) — <span class="doc-comment-inline">Return True when all caps are at sentinel (None) — feature fully disabled.</span></li>
<li><code>is_active</code> (acquisition_strategy.py) — <span class="doc-comment-inline">Return True when any cap is configured (non-sentinel).</span></li>
<li><code>_nonfeed_profile_cap_active</code> (acquisition_strategy.py) — <span class="doc-comment-inline">F230D: Return True when nonfeed_diagnostic profile cap should be evaluated.</span></li>
<li><code>kind_counts</code> (acquisition_strategy.py) — <span class="doc-comment-inline">Return counts by non-empty seed kind.</span></li>
<li><code>get_required_families</code> (acquisition_strategy.py) — <span class="doc-comment-inline">Required lane families for nonfeed_diagnostic mission.</span></li>
<li><code>get_optional_families</code> (acquisition_strategy.py) — <span class="doc-comment-inline">Optional lane families for nonfeed_diagnostic mission.</span></li>
<li><code>_family_to_lane</code> (acquisition_strategy.py) — <span class="doc-comment-inline">Map lane family string to AcquisitionLane constant.</span></li>
<li><code>_mission_target_kind</code> (acquisition_strategy.py) — <span class="doc-comment-inline">F225A: Derive target kind from mission intent.</span></li>
<li><code>_has_crypto_wallet</code> (acquisition_strategy.py)</li>
<li><code>_stealth_never_run</code> (acquisition_strategy.py) — <span class="doc-comment-inline">STEALTH is never auto-run — always record the skip.</span></li>
<li><code>_looks_like_ip</code> (acquisition_strategy.py) — <span class="doc-comment-inline">Return True if string looks like an IP address.</span></li>
<li><code>kind_counts</code> (__init__.py) — <span class="doc-comment-inline">Return counts by non-empty seed kind.</span></li>
<li><code>get_required_families</code> (__init__.py) — <span class="doc-comment-inline">Required lane families for nonfeed_diagnostic mission.</span></li>
<li><code>get_optional_families</code> (__init__.py) — <span class="doc-comment-inline">Optional lane families for nonfeed_diagnostic mission.</span></li>
<li><code>_family_to_lane</code> (__init__.py) — <span class="doc-comment-inline">Map lane family string to AcquisitionLane constant.</span></li>
<li><code>_mission_target_kind</code> (__init__.py) — <span class="doc-comment-inline">F225A: Derive target kind from mission intent.</span></li>
<li><code>_has_crypto_wallet</code> (__init__.py)</li>
<li><code>_stealth_never_run</code> (__init__.py) — <span class="doc-comment-inline">STEALTH is never auto-run — always record the skip.</span></li>
<li><code>_looks_like_ip</code> (__init__.py) — <span class="doc-comment-inline">Return True if string looks like an IP address.</span></li>
<li><code>_maybe_call_pressure_relief</code> (acquisition.py) — <span class="doc-comment-inline">Call malloc_zone_pressure_relief if governor recommends.</span></li>
<li><code>_prioritize_sources</code> (acquisition.py) — <span class="doc-comment-inline">Re-prioritize sources using latest graph stats.</span></li>
<li><code>run_async</code> (sidecar_protocol_adapters.py)</li>
<li><code>sprint_id</code> (scheduler.py) — <span class="doc-comment-inline">Return _sprint_id (setter stores there, not in result).</span></li>
<li><code>sprint_id</code> (scheduler.py) — <span class="doc-comment-inline">Set sprint_id (backward compat for tests).</span></li>
<li><code>inject_policy_manager</code> (scheduler.py)</li>
<li><code>inject_communication_layer</code> (scheduler.py)</li>
<li><code>inject_stealth_layer</code> (scheduler.py)</li>
<li><code>inject_ghost_layer</code> (scheduler.py)</li>
<li><code>inject_security_coordinator</code> (scheduler.py)</li>
<li><code>__init__</code> (scheduler.py)</li>
<li><code>health_check</code> (scheduler.py) — <span class="doc-comment-inline">Stub health check — returns None (pass).</span></li>
<li><code>get_last_error</code> (pivot_planner.py) — <span class="doc-comment-inline">Return last error message, or None if no error.</span></li>
<li><code>_looks_like_url</code> (pivot_planner.py) — <span class="doc-comment-inline">Check if string looks like a URL.</span></li>
<li><code>_looks_like_email</code> (pivot_planner.py) — <span class="doc-comment-inline">Check if string looks like an email address.</span></li>
<li><code>add_ct_quarantine</code> (nonfeed_candidate_ledger.py) — <span class="doc-comment-inline">Add CT quarantine event. quarantine=True, accepted=False, family=CT.</span></li>
<li><code>add_public_event</code> (nonfeed_candidate_ledger.py) — <span class="doc-comment-inline">Add PUBLIC stage machine event.</span></li>
<li><code>add_pivot_discovered</code> (nonfeed_candidate_ledger.py) — <span class="doc-comment-inline">Add PIVOT family discovered event.</span></li>
<li><code>add_quality_rejection</code> (nonfeed_candidate_ledger.py) — <span class="doc-comment-inline">Add quality rejection event (mirrored from quality_rejection_ledger).</span></li>
<li><code>add_provider_failed</code> (nonfeed_candidate_ledger.py) — <span class="doc-comment-inline">Add provider_failed event (e.g., CT/WAYBACK timeout or error).</span></li>
<li><code>_hash_candidate</code> (nonfeed_candidate_ledger.py) — <span class="doc-comment-inline">Generate short stable candidate_id from IOC value (first 16 hex chars).</span></li>
<li><code>_source_for_family</code> (nonfeed_candidate_ledger.py) — <span class="doc-comment-inline">Map family to default source tag.</span></li>
<li><code>_encode_orjson</code> (sidecar_bus.py)</li>
<li><code>classify_sidecar_network</code> (sidecar_bus.py) — <span class="doc-comment-inline">Return network class for a sidecar: 'active_network' | 'core' | 'duplicate_compat'.</span></li>
<li><code>classify_sidecar_risk</code> (sidecar_bus.py) — <span class="doc-comment-inline">Return risk class for a sidecar: 'active_target' | 'third_party_provider' | 'core'.</span></li>
<li><code>__init__</code> (sidecar_bus.py)</li>
<li><code>__init__</code> (sidecar_bus.py)</li>
<li><code>ema_branch_pressure</code> (resource_governor.py) — <span class="doc-comment-inline">Return current EMA branch timeout pressure for telemetry.</span></li>
<li><code>get_pressure</code> (resource_governor.py) — <span class="doc-comment-inline">Get canonical pressure state (UMAGovernor protocol).</span></li>
<li><code>set_first_cycle_ran</code> (sprint_lifecycle.py) — <span class="doc-comment-inline">F290: Signal that first acquisition cycle has completed.</span></li>
<li><code>get_top_nodes_by_degree</code> (graph_adapter.py) — <span class="doc-comment-inline">IOCGraph does not support this — returns []. Use graph_stats().</span></li>
<li><code>export_edge_list</code> (graph_adapter.py) — <span class="doc-comment-inline">IOCGraph does not export edge lists — returns []. Use export_stix_bundle().</span></li>
<li><code>checkpoint</code> (graph_adapter.py) — <span class="doc-comment-inline">IOCGraph does not have a checkpoint — no-op.</span></li>
<li><code>build</code> (scheduler_result.py) — <span class="doc-comment-inline">Return the constructed SprintSchedulerResult.</span></li>
<li><code>_field_names</code> (scheduler_result.py) — <span class="doc-comment-inline">Reflect field names from SprintSchedulerResult at runtime.</span></li>
<li><code>__enter__</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>__exit__</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>__repr__</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_make_seen_hashes</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_make_entries_per_source</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_make_hits_per_source</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_make_source_weights</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_make_novelty_bonuses</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_make_feed_accepted</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_make_fetch_latency_ema</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>tier_of</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>sorted_tiers</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>summary</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>__init__</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_cycles_started</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_cycles_completed</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_aborted</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_abort_reason</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_final_phase</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_accepted_findings</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_total_pattern_hits</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_unique_entry_hashes_seen</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_duplicate_entry_hashes_skipped</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_consecutive_empty_cycles</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_max_consecutive_empty_cycles</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_entries_per_source</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_hits_per_source</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_export_paths</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_stop_requested</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_synthesis_success</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_synthesis_engine</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_synthesis_findings_count</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_synthesis_text</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_hypotheses_generated</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_public_discovered</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_public_fetched</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_public_matched_patterns</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_public_accepted_findings</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_public_stored_findings</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_public_error</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_ct_log_discovered</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_ct_log_stored</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_ct_log_accepted_findings</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_ct_log_error</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_entered_active_at_monotonic</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_pre_loop_elapsed_s</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_first_cycle_started_at_monotonic</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_pre_active_starved</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_serialize</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_serialize</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_fmt</code> (sprint_entrypoint.py)</li>
<li><code>_lane_rule</code> (acquisition_strategy.py)</li>
<li><code>has_domain</code> (acquisition_strategy.py)</li>
<li><code>has_ip</code> (acquisition_strategy.py)</li>
<li><code>has_url</code> (acquisition_strategy.py)</li>
<li><code>to_dict</code> (acquisition_strategy.py)</li>
<li><code>to_dict</code> (acquisition_strategy.py)</li>
<li><code>to_dict</code> (acquisition_strategy.py)</li>
<li><code>_has_domain_or_ip</code> (acquisition_strategy.py)</li>
<li><code>_has_url</code> (acquisition_strategy.py)</li>
<li><code>_has_crypto_hash</code> (acquisition_strategy.py)</li>
<li><code>_has_crypto_indicator</code> (acquisition_strategy.py)</li>
<li><code>_stealth_never_run</code> (acquisition_strategy.py)</li>
<li><code>_lane_rule</code> (__init__.py)</li>
<li><code>has_domain</code> (__init__.py)</li>
<li><code>has_ip</code> (__init__.py)</li>
<li><code>has_url</code> (__init__.py)</li>
<li><code>to_dict</code> (__init__.py)</li>
<li><code>to_dict</code> (__init__.py)</li>
<li><code>to_dict</code> (__init__.py)</li>
<li><code>_has_domain_or_ip</code> (__init__.py)</li>
<li><code>_has_url</code> (__init__.py)</li>
<li><code>_has_crypto_hash</code> (__init__.py)</li>
<li><code>_has_crypto_indicator</code> (__init__.py)</li>
<li><code>_stealth_never_run</code> (__init__.py)</li>
<li><code>__init__</code> (acquisition.py)</li>
<li><code>inject_multimodal_enricher</code> (scheduler.py)</li>
<li><code>inject_source_economics</code> (scheduler.py)</li>
<li><code>result</code> (scheduler.py)</li>
<li><code>wrap</code> (role_based_pools.py)</li>
<li><code>wrap</code> (role_based_pools.py)</li>
<li><code>_encode_fallback</code> (sidecar_bus.py)</li>
<li><code>_query_one</code> (sidecar_bus.py)</li>
<li><code>_query_one</code> (sidecar_bus.py)</li>
<li><code>cycles_started_</code> (scheduler_result.py)</li>
<li><code>cycles_completed_</code> (scheduler_result.py)</li>
<li><code>__init__</code> (scheduler_result.py)</li>
<li><code>with_cycles_started</code> (scheduler_result.py)</li>
<li><code>with_cycles_completed</code> (scheduler_result.py)</li>
<li><code>with_aborted</code> (scheduler_result.py)</li>
<li><code>with_abort_reason</code> (scheduler_result.py)</li>
<li><code>with_final_phase</code> (scheduler_result.py)</li>
<li><code>with_accepted_findings</code> (scheduler_result.py)</li>
<li><code>with_total_pattern_hits</code> (scheduler_result.py)</li>
<li><code>with_unique_entry_hashes_seen</code> (scheduler_result.py)</li>
<li><code>with_duplicate_entry_hashes_skipped</code> (scheduler_result.py)</li>
<li><code>with_consecutive_empty_cycles</code> (scheduler_result.py)</li>
<li><code>with_max_consecutive_empty_cycles</code> (scheduler_result.py)</li>
<li><code>with_entries_per_source</code> (scheduler_result.py)</li>
<li><code>with_hits_per_source</code> (scheduler_result.py)</li>
<li><code>with_export_paths</code> (scheduler_result.py)</li>
<li><code>with_stop_requested</code> (scheduler_result.py)</li>
<li><code>with_synthesis_success</code> (scheduler_result.py)</li>
<li><code>with_synthesis_engine</code> (scheduler_result.py)</li>
<li><code>with_synthesis_findings_count</code> (scheduler_result.py)</li>
<li><code>with_synthesis_text</code> (scheduler_result.py)</li>
<li><code>with_hypotheses_generated</code> (scheduler_result.py)</li>
<li><code>with_public_discovered</code> (scheduler_result.py)</li>
<li><code>with_public_fetched</code> (scheduler_result.py)</li>
<li><code>with_public_matched_patterns</code> (scheduler_result.py)</li>
<li><code>with_public_accepted_findings</code> (scheduler_result.py)</li>
<li><code>with_public_stored_findings</code> (scheduler_result.py)</li>
<li><code>with_public_error</code> (scheduler_result.py)</li>
<li><code>with_ct_log_discovered</code> (scheduler_result.py)</li>
<li><code>with_ct_log_stored</code> (scheduler_result.py)</li>
<li><code>with_ct_log_accepted_findings</code> (scheduler_result.py)</li>
<li><code>with_ct_log_error</code> (scheduler_result.py)</li>
<li><code>with_entered_active_at_monotonic</code> (scheduler_result.py)</li>
<li><code>with_pre_loop_elapsed_s</code> (scheduler_result.py)</li>
<li><code>with_first_cycle_started_at_monotonic</code> (scheduler_result.py)</li>
<li><code>with_pre_active_starved</code> (scheduler_result.py)</li>
<li><code>get</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>set</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>bump</code> (sprint_scheduler_v1_archived.py)</li>
</ul>
</details>

<details><summary><strong>Class</strong> (147)</summary>
<ul>
<li><code>SprintScheduler</code> (sprint_scheduler_v1_archived.py)</li>
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
<li><code>SidecarOrchestrator</code> (sidecar_orchestrator.py)
<details><summary>Thin facade wiring three canonical layers for sprint sidecar execution.</summary>
<div class="doc-comment">
<p>Thin facade wiring three canonical layers for sprint sidecar execution.</p>
<p></p>
<p>result_sink:     SprintSchedulerResult — telemetry fields are updated here.</p>
<p>governor:        M1 resource governor or None — RAM guard checks.</p>
<p>scheduler:       SprintScheduler reference for deferred advisory access.</p>
</div>
</details>
</li>
<li><code>SprintSchedulerResult</code> (sprint_scheduler_v1_archived.py)
<details><summary>Outcome of one sprint run.</summary>
<div class="doc-comment">
<p>Outcome of one sprint run.</p>
<p></p>
<p></p>
<p></p>
<p>Attributes:</p>
<p></p>
<p>cycles_started: Number of fetch cycles initiated.</p>
<p></p>
<p>cycles_completed: Number of fetch cycles that completed all phases.</p>
<p></p>
<p>unique_entry_hashes_seen: Count of deduplicated entries processed.</p>
<p></p>
<p>duplicate_entry_hashes_skipped: Count of duplicate entries filtered.</p>
<p></p>
<p>total_pattern_hits: Sum of pattern matches across all sources.</p>
<p></p>
<p>accepted_findings: Findings that passed quality gate.</p>
<p></p>
<p>entries_per_source: Breakdown of entries by source (source_name -&gt; count).</p>
<p></p>
<p>hits_per_source: Pattern hits per source (source_name -&gt; count).</p>
<p></p>
<p>final_phase: Last phase reached (BOOT, GATHER, JUDGMENT, EXPORT, TEARDOWN).</p>
<p></p>
<p>export_paths: List of paths where sprint results were exported.</p>
<p></p>
<p>aborted: True if sprint was aborted early.</p>
<p></p>
<p>abort_reason: Human-readable reason for abortion.</p>
<p></p>
<p>stop_requested: True when stop_on_first_accepted triggered acceptance.</p>
<p></p>
<p>public_discovered: Public pipeline discoveries (F8XE).</p>
<p></p>
<p>public_fetched: Public pipeline successful fetches.</p>
<p></p>
<p>public_matched_patterns: Public pipeline pattern matches.</p>
<p></p>
<p>public_accepted_findings: Public pipeline accepted findings.</p>
<p></p>
<p>public_stored_findings: Public pipeline stored findings.</p>
<p></p>
<p>public_error: Public pipeline error message.</p>
<p></p>
<p>ct_log_discovered: CT log discoveries (F193A).</p>
<p></p>
<p>ct_log_stored: CT log stored findings.</p>
<p></p>
<p>ct_log_accepted_findings: CT log accepted findings (F194A).</p>
<p></p>
<p>ct_log_error: CT log error message.</p>
<p></p>
<p>entered_active_at_monotonic: Timestamp when ACTIVE phase first entered.</p>
<p></p>
<p>pre_loop_elapsed_s: Wall-clock seconds from run() to loop guard entry.</p>
<p></p>
<p>first_cycle_started_at_monotonic: Timestamp of first cycles_started increment.</p>
<p></p>
<p>pre_active_starved: True when gap between entered_active and first_cycle_started &gt; 30s.</p>
</div>
</details>
</li>
<li><code>SprintLifecycleManager</code> (sprint_lifecycle.py)
<details><summary>Lightweight sprint lifecycle state machine.</summary>
<div class="doc-comment">
<p>Lightweight sprint lifecycle state machine.</p>
<p></p>
<p>All methods accept an optional ``now_monotonic`` parameter to allow</p>
<p>deterministic testing with a fake clock. When omitted the call uses</p>
<p>``time.monotonic()`` at runtime.</p>
<p></p>
<p>Issue 1.2 — Phase TaskGroup Integration:</p>
<p>``_on_phase_exit_callbacks`` is a list of callables invoked synchronously</p>
<p>by ``_transition_to_unlocked()`` AFTER the phase field is updated.</p>
<p>Each callback receives ``(from_phase: SprintPhase, to_phase: SprintPhase)``.</p>
<p>This replaces the "cancel_event flag" pattern — the callback closes the</p>
<p>old phase TaskGroup, which cancels all lane subtasks cleanly.</p>
<p></p>
<p>Always-on, bounded, fail-safe: callbacks are called inside a</p>
<p>``try/except Exception`` loop; a failing callback never blocks the</p>
<p>transition. Callbacks are invoked in registration order.</p>
</div>
</details>
</li>
<li><code>RoleBasedPools</code> (role_based_pools.py)
<details><summary>Unified facade for role-based executor pools on M1 8GB.</summary>
<div class="doc-comment">
<p>Unified facade for role-based executor pools on M1 8GB.</p>
<p></p>
<p>Provides specialized pools for different workload roles:</p>
<p>- HASH: CPU-bound hashing (xxhash, blake3)</p>
<p>- EMBED: Memory-heavy MLX embedding generation</p>
<p>- DB: I/O-bound DuckDB operations</p>
<p>- REGEX: CPU-bound regex/pattern matching</p>
<p>- ASYNC_IO: asyncio.to_thread wrapper for generic blocking I/O</p>
<p></p>
<p>Invariants:</p>
<p>1. Always-on: no feature flags</p>
<p>2. Bounded: RAM monitoring prevents OOM on M1 8GB</p>
<p>3. Fail-safe: returns None/[] on error, never raises</p>
<p>4. Lazy: pools initialized on first use</p>
<p></p>
<p>Thread safety: all methods are thread-safe via asyncio.Lock.</p>
</div>
</details>
</li>
<li><code>M1ResourceGovernor</code> (resource_governor.py)
<details><summary>Advisory safety layer for M1 8GB sprint execution.</summary>
<div class="doc-comment">
<p>Advisory safety layer for M1 8GB sprint execution.</p>
<p></p>
<p>Governs: branch concurrency, model lease, renderer lease.</p>
<p>Always-on, fail-soft. Never blocks the sprint — only advises.</p>
<p></p>
<p>Read-only surfaces:</p>
<p>brain.model_lifecycle.get_model_lifecycle_status()</p>
<p>core.resource_governor.sample_uma_status()</p>
<p>utils.concurrency.FETCH_SEMAPHORE.limit()</p>
</div>
</details>
</li>
<li><code>AcqReportPayload</code> (sprint_entrypoint.py)
<details><summary>[ISSUE-007] Schema-driven acquisition report — mirrors SprintSchedulerResult fields.</summary>
<div class="doc-comment">
<p>[ISSUE-007] Schema-driven acquisition report — mirrors SprintSchedulerResult fields.</p>
<p></p>
<p>M1 8GB: msgspec.Struct uses __slots__ — ~40 bytes/instance vs ~80 for dataclass,</p>
<p>no GC header, direct C-level field access. frozen=True enables faster comparison.</p>
<p>eq=False because we never compare payloads.</p>
<p></p>
<p>PERFORMANCE NOTE (ISSUE-007): The 3ms msgspec.convert cost is paid ONCE per sprint</p>
<p>at TEARDOWN — acceptable given the TEARDOWN budget. Splitting into sub-payloads</p>
<p>would INCREASE allocation work (nested struct construction). The real optimization</p>
<p>is avoiding unnecessary list()/dict() wrapping in acq_payload_to_dict.</p>
</div>
</details>
</li>
<li><code>SprintAdvisoryRunner</code> (sprint_advisory_runner.py)
<details><summary>F206D: Extracted advisory orchestration for sprint teardown.</summary>
<div class="doc-comment">
<p>F206D: Extracted advisory orchestration for sprint teardown.</p>
<p></p>
<p>Runs the 4 advisory steps in explicit order:</p>
<p>1. pivot_planner  → planned_pivots</p>
<p>2. pivot_executor → executed_pivots (consumes planner output)</p>
<p>3. resource_governor → governor_recorded</p>
<p>4. analyst_brief → brief_generated</p>
<p></p>
<p>Each step is fail-soft. CancelledError propagates to caller.</p>
<p>Scheduler retains all authority; runner is purely orchestration.</p>
<p></p>
<p>Args:</p>
<p>scheduler: SprintScheduler instance providing access to:</p>
<p>- _pivot_planner</p>
<p>- _duckdb_store</p>
<p>- _governor</p>
<p>- _analyst_workbench</p>
<p>- _all_findings</p>
<p>- sprint_id</p>
<p>- query</p>
<p>- _sidecars_skipped</p>
<p>- _peak_rss_gib</p>
<p>- _result</p>
<p>duckdb_store: DuckDBShadowStore (passed explicitly for clarity)</p>
<p>governor: M1ResourceGovernor instance</p>
<p>analyst_workbench: AnalystWorkbench instance (may be None)</p>
</div>
</details>
</li>
<li><code>SprintSchedulerResult</code> (scheduler_result.py)
<details><summary>Outcome of one sprint run.</summary>
<div class="doc-comment">
<p>Outcome of one sprint run.</p>
<p></p>
<p>STEP 1 extracted from sprint_scheduler.py (33 449 LOC → modular package).</p>
<p>F350M-R / Issue #P2.</p>
</div>
</details>
</li>
<li><code>PivotPlanner</code> (pivot_planner.py)
<details><summary>F202G: Hypothesis-driven pivot planner.</summary>
<div class="doc-comment">
<p>F202G: Hypothesis-driven pivot planner.</p>
<p></p>
<p>Generates bounded next pivots from accepted findings and envelope facets.</p>
<p>Advisory only: scheduler uses pivots as ordering input, NOT as sprint owner.</p>
<p></p>
<p>Bounds:</p>
<p>- MAX_PIVOTS=20 per sprint</p>
<p>- Planner failure never blocks export or sprint</p>
<p>- Model load/unload only via brain.model_lifecycle</p>
<p></p>
<p>Usage:</p>
<p>planner = PivotPlanner()</p>
<p>pivots = planner.plan_pivots(findings, graph_stats=graph_stats)</p>
<p>for pivot in pivots:</p>
<p>print(pivot.ioc_value, pivot.pivot_type, pivot.reason)</p>
</div>
</details>
</li>
<li><code>_LifecycleAdapter</code> (sprint_scheduler_v1_archived.py)
<details><summary>Adapts any lifecycle object to the runtime/sprint_lifecycle API.</summary>
<div class="doc-comment">
<p>Adapts any lifecycle object to the runtime/sprint_lifecycle API.</p>
<p></p>
<p>Normalizes API differences between runtime/ and utils/ versions:</p>
<p>runtime/sprint_lifecycle: start(), tick(), remaining_time(),</p>
<p>is_terminal(), should_enter_windup(), _current_phase,</p>
<p>recommended_tool_mode(), request_abort(), _abort_requested</p>
<p></p>
<p>Python 3.14 __slots__ optimization:</p>
<p>- 22 cached-attr slots → 3 slots (_lc, _phase_transition_callback, _prev_phase)</p>
<p>- __getattr__ for per-instance lazy attr name resolution (&lt;100ns vs ~660ns)</p>
<p>- No foot-gun: if the underlying object is replaced, delegation breaks</p>
<p>immediately (no stale cached values), matching the issue description.</p>
<p>- Phase transitions are preserved (prev_phase tracking + callback).</p>
<p></p>
<p>Invariants (F320 legacy):</p>
<p>- __slots__ for M1 8GB RAM savings</p>
<p>- Fail-safe: AttributeError from _lc propagates as-is</p>
</div>
</details>
</li>
<li><code>SprintResult</code> (sprint_scheduler_v1_archived.py)
<details><summary>Universal fields -- always populated regardless of sprint mode.</summary>
<div class="doc-comment">
<p>Universal fields -- always populated regardless of sprint mode.</p>
<p></p>
<p></p>
<p></p>
<p>This is the base class for all result variants. Subclasses add</p>
<p></p>
<p>mode-specific fields that are guaranteed to be populated when</p>
<p></p>
<p>that mode's pipeline ran.</p>
<p></p>
<p></p>
<p></p>
<p>Use factory methods on SprintScheduler to construct variants from</p>
<p></p>
<p>internal _result state when needed. The types are a foundation for</p>
<p></p>
<p>gradual migration away from the monolithic SprintSchedulerResult.</p>
</div>
</details>
</li>
<li><code>GraphFacade</code> (graph_adapter.py)
<details><summary>F270 Phase 2: Unified graph access facade over GraphAttachmentStore.</summary>
<div class="doc-comment">
<p>F270 Phase 2: Unified graph access facade over GraphAttachmentStore.</p>
<p></p>
<p>CONSOLIDATES 3 SLOTS into 1 capability-based interface:</p>
<p>- _ioc_graph (analytics)     → TIER_A methods</p>
<p>- _stix_graph (STIX export)  → TIER_S methods</p>
<p>- _truth_write_graph (buffered writes) → TIER_S buffered methods</p>
<p></p>
<p>Consumers no longer need to know which slot holds which graph.</p>
<p>Check capability via hasattr() then call.</p>
<p></p>
<p>Usage:</p>
<p>facade = GraphFacade(store)  # DuckDBShadowStore with GraphAttachmentStore</p>
<p>if hasattr(facade, 'export_stix_bundle'):</p>
<p>bundle = await facade.export_stix_bundle()</p>
<p></p>
<p>if hasattr(facade, 'find_connected'):</p>
<p>connected = facade.find_connected("1.2.3.4")</p>
<p></p>
<p>M1 8GB: GraphAttachmentStore is already fail-open throughout.</p>
</div>
</details>
</li>
<li><code>PreDecisionSummary</code> (shadow_pre_decision.py)
<details><summary>Pre-decision summary artifact — composed from ParityArtifact.</summary>
<div class="doc-comment">
<p>Pre-decision summary artifact — composed from ParityArtifact.</p>
<p></p>
<p>Toto je DIAGNOSTICKÝ artifact. Nesmí být zapsán do produkčních ledgerů</p>
<p>jako runtime facts. Nesmí participovat v control flow rozhodnutích.</p>
<p></p>
<p>Struktura:</p>
<p>- lifecycle: LifecycleInterpretation (composed from ParityArtifact)</p>
<p>- graph: GraphCapabilitySummary (composed from ParityArtifact)</p>
<p>- export: ExportReadinessSummary (composed from ParityArtifact)</p>
<p>- model_control: ModelControlSummary (composed from ParityArtifact)</p>
<p>- precursors: PrecursorSummary (composed from ParityArtifact)</p>
<p>- diff_taxonomy: list[DiffTaxonomy] (composed from ParityArtifact.mismatch_categories)</p>
<p>- blockers: list[str] — co brání pre-decision confidence</p>
<p>- unknowns: list[str] — co je neznámé</p>
<p>- mismatch_reasons: dict[str, str] — pro každý mismatch category důvod</p>
<p></p>
<p>Phase separation: VŠECHNY phase fields jsou ODDĚLENÉ v LifecycleInterpretation.</p>
<p>Žádné slité phase pole neexistuje.</p>
</div>
</details>
</li>
<li><code>NonfeedMissionController</code> (acquisition_strategy.py)
<details><summary>F217B: Canonical nonfeed mission contract for nonfeed_diagnostic profile.</summary>
<div class="doc-comment">
<p>F217B: Canonical nonfeed mission contract for nonfeed_diagnostic profile.</p>
<p></p>
<p>Coordinates lane family expectations without benchmark-owned logic.</p>
<p>For acquisition_profile=nonfeed_diagnostic:</p>
<p>- Required lane families: PUBLIC, CT, PIVOT_EXECUTOR</p>
<p>- Optional lane families: WAYBACK, PASSIVE_DNS</p>
<p>- FEED is capped until required nonfeed lanes are terminal</p>
<p>- Mission finishes only when each required family has:</p>
<p>accepted evidence</p>
<p>OR explicit terminal state</p>
<p>OR explicit provider failure</p>
<p>OR explicit memory skip</p>
<p></p>
<p>IMPORTANT — what does NOT count as accepted evidence:</p>
<p>- CT quarantine is NOT accepted evidence (raw hits rejected by bridge criteria)</p>
<p>- Quality rejection ledger is NOT accepted evidence (quality gate rejection)</p>
<p>- PUBLIC explicit failure (FETCH_ZERO_SUCCESS, QUALITY_REJECTED, etc.) counts</p>
<p>as terminal but NOT accepted</p>
<p>- Feed findings do NOT satisfy nonfeed mission</p>
</div>
</details>
</li>
<li><code>NonfeedMissionController</code> (__init__.py)
<details><summary>F217B: Canonical nonfeed mission contract for nonfeed_diagnostic profile.</summary>
<div class="doc-comment">
<p>F217B: Canonical nonfeed mission contract for nonfeed_diagnostic profile.</p>
<p></p>
<p>Coordinates lane family expectations without benchmark-owned logic.</p>
<p>For acquisition_profile=nonfeed_diagnostic:</p>
<p>- Required lane families: PUBLIC, CT, PIVOT_EXECUTOR</p>
<p>- Optional lane families: WAYBACK, PASSIVE_DNS</p>
<p>- FEED is capped until required nonfeed lanes are terminal</p>
<p>- Mission finishes only when each required family has:</p>
<p>accepted evidence</p>
<p>OR explicit terminal state</p>
<p>OR explicit provider failure</p>
<p>OR explicit memory skip</p>
<p></p>
<p>IMPORTANT — what does NOT count as accepted evidence:</p>
<p>- CT quarantine is NOT accepted evidence (raw hits rejected by bridge criteria)</p>
<p>- Quality rejection ledger is NOT accepted evidence (quality gate rejection)</p>
<p>- PUBLIC explicit failure (FETCH_ZERO_SUCCESS, QUALITY_REJECTED, etc.) counts</p>
<p>as terminal but NOT accepted</p>
<p>- Feed findings do NOT satisfy nonfeed mission</p>
</div>
</details>
</li>
<li><code>DuckPGQGraphAdapter</code> (graph_adapter.py)
<details><summary>Adapter wrapping DuckPGQGraph to implement GraphProtocol.</summary>
<div class="doc-comment">
<p>Adapter wrapping DuckPGQGraph to implement GraphProtocol.</p>
<p></p>
<p>Non-breaking: wraps existing DuckPGQGraph and delegates</p>
<p>to it without changing behavior.</p>
<p></p>
<p>Usage:</p>
<p>graph = DuckPGQGraph(...)</p>
<p>adapter = DuckPGQGraphAdapter(graph)</p>
<p># Use as GraphProtocol</p>
<p>await adapter.upsert_ioc("1.2.3.4", "ipv4", sprint_id="sprint_1")</p>
<p>adapter.find_connected("1.2.3.4")</p>
</div>
</details>
</li>
<li><code>IOCGraphAdapter</code> (graph_adapter.py)
<details><summary>Adapter wrapping IOCGraph (Kuzu) to implement GraphProtocol.</summary>
<div class="doc-comment">
<p>Adapter wrapping IOCGraph (Kuzu) to implement GraphProtocol.</p>
<p></p>
<p>IOCGraph is the STIX-compliant truth-write backend.</p>
<p>Wraps Kuzu operations without changing behavior.</p>
<p></p>
<p>Usage:</p>
<p>ioc_graph = IOCGraph(...)</p>
<p>await ioc_graph.initialize()</p>
<p>adapter = IOCGraphAdapter(ioc_graph)</p>
<p># Use as GraphProtocol</p>
<p>await adapter.upsert_ioc("1.2.3.4", "ipv4", sprint_id="sprint_1")</p>
<p>await adapter.buffer_ioc("ipv4", "1.2.3.4")</p>
<p>await adapter.flush_buffers()</p>
</div>
</details>
</li>
<li><code>SprintResultBuilder</code> (scheduler_result.py)
<details><summary>Fluent builder for SprintSchedulerResult (Issue #6).</summary>
<div class="doc-comment">
<p>Fluent builder for SprintSchedulerResult (Issue #6).</p>
<p></p>
<p>Uses __dataclass_fields__ reflection — no code generation step needed.</p>
<p>All 100+ fields are supported automatically.</p>
<p></p>
<p>Usage:</p>
<p>result = (SprintResultBuilder()</p>
<p>.with_cycles_started(5)</p>
<p>.with_cycles_completed(3)</p>
<p>.with_aborted(True)</p>
<p>.with_abort_reason("timeout")</p>
<p>.build())</p>
</div>
</details>
</li>
<li><code>SprintResultBuilder</code> (sprint_scheduler_v1_archived.py)
<details><summary>Fluent builder for SprintSchedulerResult (Issue #6).</summary>
<div class="doc-comment">
<p>Fluent builder for SprintSchedulerResult (Issue #6).</p>
<p></p>
<p>Uses __dataclass_fields__ reflection — no code generation step needed.</p>
<p>All 100+ fields are supported automatically.</p>
<p></p>
<p>Usage:</p>
<p>result = (SprintResultBuilder()</p>
<p>.with_cycles_started(5)</p>
<p>.with_cycles_completed(3)</p>
<p>.with_aborted(True)</p>
<p>.with_abort_reason("timeout")</p>
<p>.build())</p>
</div>
</details>
</li>
<li><code>NonfeedCandidateLedger</code> (nonfeed_candidate_ledger.py)
<details><summary>Sprint F217E: Bounded in-memory nonfeed candidate evidence ledger.</summary>
<div class="doc-comment">
<p>Sprint F217E: Bounded in-memory nonfeed candidate evidence ledger.</p>
<p></p>
<p>FIFO eviction at MAX_LEDGER_SIZE records. Thread-safe for async use via</p>
<p>a lock. All mutating operations acquire the lock; reads do not.</p>
<p></p>
<p>Producers wire:</p>
<p>- PUBLIC stage machine → discovered / fetched / rejected / accepted</p>
<p>- CT quarantine         → quarantined / rejected / provider_failed</p>
<p>- Pivot planner        → discovered (PIVOT family)</p>
<p>- Quality rejection    → rejected (mirrored from quality_rejection_ledger)</p>
<p></p>
<p>ABORT CONDITIONS (enforced by tests):</p>
<p>- NEVER count quarantine as accepted</p>
<p>- NEVER store full payload text</p>
<p>- NEVER generate ledger in benchmark context</p>
</div>
</details>
</li>
<li><code>FindingSidecarBus</code> (sidecar_bus.py)
<details><summary>Unified bounded orchestrator for all accepted-finding sidecars.</summary>
<div class="doc-comment">
<p>Unified bounded orchestrator for all accepted-finding sidecars.</p>
<p></p>
<p>All three source branches (feed, public, ct) route their accepted findings</p>
<p>through this bus. The bus fans out to registered sidecar runners in stage order,</p>
<p>collects per-runner SidecarRunResult records, and returns them.</p>
<p></p>
<p>Stages execute sequentially (stage 1 → stage 2 → stage 3). Within each stage,</p>
<p>runners execute concurrently via asyncio.gather(return_exceptions=True).</p>
<p></p>
<p>RAM guard: heavy sidecars (identity_stitching, embedding, sprint_diff) are</p>
<p>skipped when M1 governor reports critical or emergency memory pressure.</p>
<p></p>
<p>Fail-soft: individual sidecar errors are captured in SidecarRunResult and do</p>
<p>not propagate or crash the sprint. Stage N failure does not stop stage N+1.</p>
</div>
</details>
</li>
<li><code>SprintSchedulerConfig</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Configuration for one sprint run.</span></li>
<li><code>WhoisSidecarAdapter</code> (sidecar_protocol_adapters.py)
<details><summary>Historical WHOIS/RDAP Intelligence Sidecar.</summary>
<div class="doc-comment">
<p>Historical WHOIS/RDAP Intelligence Sidecar.</p>
<p></p>
<p>Consolidated async WHOIS/RDAP client providing domain registration</p>
<p>intelligence with historical data support. Replaces fragmented</p>
<p>network_reconnaissance.WHOISLookup, rir_correlator._whois_lookup_domain,</p>
<p>and ipv6_recon WHOIS fallback.</p>
<p></p>
<p>Features:</p>
<p>- RDAP (RFC 9224) primary — structured JSON, RIR bootstrap</p>
<p>- WHOIS port 43 fallback for legacy TLDs</p>
<p>- ipwhois RDAP fallback (blocking, last resort)</p>
<p>- Historical WHOIS API opt-in (whoisxmlapi, domainiq, whoisology)</p>
<p>- Bounded TTL cache (500 entries, 1h)</p>
<p>- Circuit breakers on all external calls</p>
<p></p>
<p>Env: HLEDAC_ENABLE_WHOIS=1</p>
<p>Env (historical): HLEDAC_WHOIS_API + HLEDAC_WHOIS_API_KEY</p>
<p>RAM: 30MB budget</p>
<p>Priority: 5 (medium, runs alongside passive DNS and CT lanes)</p>
</div>
</details>
</li>
<li><code>ThreatIntelSidecarAdapter</code> (sidecar_protocol_adapters.py)
<details><summary>Threat Intelligence Feed Sidecar — F266-U5.</summary>
<div class="doc-comment">
<p>Threat Intelligence Feed Sidecar — F266-U5.</p>
<p></p>
<p>Wires up orphaned TI feed functions from ti_feed_adapter.py:</p>
<p>- fetch_threatfox()    — ThreatFox IOC feed (API, no key)</p>
<p>- fetch_feodo_c2()     — Feodo Tracker C2 feed (API, no key)</p>
<p>- fetch_urlhaus()      — URLhaus malware URL feed (RSS already wired,</p>
<p>but sidecar adds query-filtered variant)</p>
<p></p>
<p>These functions were defined but NEVER called from anywhere in the codebase.</p>
<p>This sidecar activates for threat_intel profile and provides IoCs matching</p>
<p>the sprint query (ransomware, malware names, C2 IPs, etc.).</p>
<p></p>
<p>Env: HLEDAC_ENABLE_THREAT_INTEL=1</p>
<p>RAM: 40MB budget</p>
<p>Priority: 7 (high — threat intel is primary signal for threat_intel profile)</p>
</div>
</details>
</li>
<li><code>FediverseSidecarAdapter</code> (sidecar_protocol_adapters.py)
<details><summary>Fediverse/Mastodon Intelligence Sidecar.</summary>
<div class="doc-comment">
<p>Fediverse/Mastodon Intelligence Sidecar.</p>
<p></p>
<p>Searches public Mastodon/Fediverse instances for OSINT signals.</p>
<p>M1-safe: max 2 concurrent instances, 10s timeout per request.</p>
<p></p>
<p>Env: HLEDAC_ENABLE_FEDIVERSE=1</p>
<p>RAM: 50MB budget</p>
<p>Priority: 6 (higher than core sidecars)</p>
</div>
</details>
</li>
<li><code>AltProtocolSidecarAdapter</code> (sidecar_protocol_adapters.py)
<details><summary>Alternative Protocols Sidecar.</summary>
<div class="doc-comment">
<p>Alternative Protocols Sidecar.</p>
<p></p>
<p>Accesses content via IPFS, Gopher, Gemini, I2P protocols.</p>
<p>Enables discovery of content invisible to standard web crawlers.</p>
<p></p>
<p>Env: HLEDAC_ENABLE_ALT_PROTOCOLS=1</p>
<p>RAM: 60MB budget</p>
<p>Priority: 4 (lower priority, experimental)</p>
</div>
</details>
</li>
<li><code>ShadowWalkerSidecarAdapter</code> (sidecar_protocol_adapters.py)
<details><summary>ShadowWalker URL path prediction sidecar.</summary>
<div class="doc-comment">
<p>ShadowWalker URL path prediction sidecar.</p>
<p></p>
<p>Uses ShadowWalkerAlgorithm to predict hidden/unlisted URL paths on a target</p>
<p>domain, based on observed path patterns. One-shot per sprint — no persistent</p>
<p>state. Results returned as CanonicalFinding with source_type="shadow_walker".</p>
<p></p>
<p>Env gate: HLEDAC_ENABLE_SHADOW_WALKER=1 (default: 0, dormant)</p>
<p>RAM budget: ~20MB</p>
<p>Priority: 4 (runs early in advisory phase)</p>
</div>
</details>
</li>
<li><code>FeedDominanceGuard</code> (sprint_scheduler_v1_archived.py)
<details><summary>F214: Canonical feed dominance guard policy.</summary>
<div class="doc-comment">
<p>F214: Canonical feed dominance guard policy.</p>
<p></p>
<p></p>
<p></p>
<p>Computed at early exit classification time. Does NOT change scheduler</p>
<p></p>
<p>behavior in default (strict=False) mode -- only adds reporting fields</p>
<p></p>
<p>to SprintSchedulerResult and enriches early_exit_reason.</p>
<p></p>
<p></p>
<p></p>
<p>With strict=True (default False):</p>
<p></p>
<p>- Blocks feed-only early exit if nonfeed candidates exist but are unresolved</p>
<p></p>
<p>- Allows early exit if nonfeed accepted &gt;= min_nonfeed_findings</p>
<p></p>
<p>- Allows early exit if all eligible nonfeed lanes reached terminal state</p>
<p></p>
<p>- Allows early exit if nonfeed diagnostic timed out</p>
</div>
</details>
</li>
<li><code>LanceDBRAGSidecarAdapter</code> (sidecar_protocol_adapters.py)
<details><summary>Sprint P2-3 Layer A: Cross-sprint corpus mining sidecar.</summary>
<div class="doc-comment">
<p>Sprint P2-3 Layer A: Cross-sprint corpus mining sidecar.</p>
<p></p>
<p>Embeds current sprint query + top findings into LanceDB "documents" table.</p>
<p>Next sprint will retrieve similar queries as advisory seeds.</p>
<p></p>
<p>Env: HLEDAC_ENABLE_GRAPH_RAG=1 (shares gate with GraphRAGOrchestrator)</p>
<p>RAM: 60MB budget (M1 8GB safe)</p>
<p>Priority: 7 (runs early — results available to next sprint)</p>
</div>
</details>
</li>
<li><code>FeedDominanceBudget</code> (acquisition_strategy.py)
<details><summary>F216E / Sprint C: Canonical feed dominance budget policy.</summary>
<div class="doc-comment">
<p>F216E / Sprint C: Canonical feed dominance budget policy.</p>
<p></p>
<p>Limits how many feed findings can be accepted before nonfeed lanes</p>
<p>are given priority. Activated for non-default profiles when mandatory</p>
<p>nonfeed lanes are unresolved.</p>
<p></p>
<p>F227D: Added mission_intent context to adjust cap thresholds.</p>
<p>Missions like domain_recon/person_recon/infra_recon cap FEED earlier</p>
<p>once feed evidence accumulates and nonfeed is unresolved, while</p>
<p>cve_recon preserves feed lanes because feeds are high-value for CVE ops.</p>
<p></p>
<p>Sprint C migration: @dataclass(frozen=True) → msgspec.Struct().</p>
<p>Benefits: C-level __init__ (~2-3× faster), no GC tracking (~40B saved),</p>
<p>zero-cost property access on hot paths.</p>
<p></p>
<p>Invariants:</p>
<p>- max_feed_accepted_before_nonfeed_terminal &gt;= max_feed_per_source</p>
<p>- All limits are bounded (min 1, max 10000)</p>
<p>- Safe to use as frozen Struct field</p>
</div>
</details>
</li>
<li><code>DispatchReadinessPreview</code> (shadow_pre_decision.py)
<details><summary>Dispatch readiness preview — DIAGNOSTIC ONLY.</summary>
<div class="doc-comment">
<p>Dispatch readiness preview — DIAGNOSTIC ONLY.</p>
<p></p>
<p>Previewuje dispatch readiness pro sadu task/tool kandidátů</p>
<p>bez volání execute_with_limits() nebo provider activation.</p>
<p></p>
<p>Rozlišuje:</p>
<p>- dispatch_ready: kandidát má čistý canonical path, capabilities satisfied</p>
<p>- dispatch_blocked: kandidát má path ale capability gap</p>
<p>- dispatch_pruned: kandidát je pruned control modem</p>
<p>- dispatch_unknown: nelze určit</p>
<p>- runtime_only_compat_dispatch: kandidát nemá ToolRegistry mapping,</p>
<p>používá inline get_task_handler()</p>
<p></p>
<p>Nikdy nevola:</p>
<p>- execute_with_limits()</p>
<p>- acquire() na provider pool</p>
<p>- load_model()</p>
<p>- Žádný dispatch</p>
</div>
</details>
</li>
<li><code>GitHubGistSidecarAdapter</code> (sidecar_protocol_adapters.py)
<details><summary>GitHub Gist Archive Discovery Sidecar.</summary>
<div class="doc-comment">
<p>GitHub Gist Archive Discovery Sidecar.</p>
<p></p>
<p>Searches public GitHub Gists for OSINT signals matching the query</p>
<p>or related IoCs from sprint findings. Uses the existing</p>
<p>search_github_gists() function from ti_feed_adapter.</p>
<p></p>
<p>Env: HLEDAC_ENABLE_GITHUB_GIST=1</p>
<p>RAM: 30MB budget</p>
<p>Priority: 5 (medium)</p>
</div>
</details>
</li>
<li><code>DiffTaxonomy</code> (shadow_pre_decision.py)
<details><summary>Diff taxonomy pro pre-decision mismatch reasons.</summary>
<div class="doc-comment">
<p>Diff taxonomy pro pre-decision mismatch reasons.</p>
<p></p>
<p>Každá kategorie reprezentuje distinct failure mode</p>
<p>v pre-decision layer — NENÍ to scheduler decision samo.</p>
<p></p>
<p>unlike ParityArtifact.mismatch_categories which are RAW mismatch flags,</p>
<p>DiffTaxonomy je COMPOSED interpretation — bere raw mismatches</p>
<p>a skládá z nich higher-level diagnosis.</p>
<p></p>
<p>Každá kategorie má také `_stability` tag:</p>
<p>- STABLE mismatch: problém v stable/typed path, vyžaduje pozornost</p>
<p>- COMPAT mismatch: problém v compat/legacy path, může být expected</p>
<p>- UNKNOWN mismatch: nedostatek informací, obvykle není blocker</p>
</div>
</details>
</li>
<li><code>LeakSentinelSidecarAdapter</code> (sidecar_protocol_adapters.py)
<details><summary>Leak Sentinel Sidecar.</summary>
<div class="doc-comment">
<p>Leak Sentinel Sidecar.</p>
<p></p>
<p>Monitors paste sites, GitHub secret scanner, breach databases.</p>
<p>Redacts PII before storing findings.</p>
<p></p>
<p>Env: HLEDAC_ENABLE_LEAKSENTINEL=1</p>
<p>RAM: 30MB budget</p>
<p>Priority: 3 (lower priority, optional enrichment)</p>
</div>
</details>
</li>
<li><code>AcademicSidecarAdapter</code> (sidecar_protocol_adapters.py)
<details><summary>Academic Research Intelligence Sidecar.</summary>
<div class="doc-comment">
<p>Academic Research Intelligence Sidecar.</p>
<p></p>
<p>Searches academic sources: arXiv, Semantic Scholar, OpenAlex, CORE, Unpaywall.</p>
<p>Supports DOI resolution, PDF discovery, citation analysis.</p>
<p></p>
<p>Env: HLEDAC_ENABLE_ACADEMIC=1</p>
<p>RAM: 80MB budget</p>
<p>Priority: 5 (medium priority, research-focused profiles)</p>
</div>
</details>
</li>
<li><code>CtSprintResult</code> (sprint_scheduler_v1_archived.py)
<details><summary>CT mode result -- certificate transparency log pipeline fields guaranteed populated.</summary>
<div class="doc-comment">
<p>CT mode result -- certificate transparency log pipeline fields guaranteed populated.</p>
<p></p>
<p></p>
<p></p>
<p>Populated when CT acquisition lane runs (CT log discovery + bridge).</p>
</div>
</details>
</li>
<li><code>TVNewsSidecarAdapter</code> (sidecar_protocol_adapters.py)
<details><summary>Internet Archive TV News Sidecar.</summary>
<div class="doc-comment">
<p>Internet Archive TV News Sidecar.</p>
<p></p>
<p>Searches TV News Archive for broadcast content matching OSINT queries.</p>
<p>Uses Archive.org Advanced Search API with collection:tv filter.</p>
<p></p>
<p>Env: HLEDAC_ENABLE_TV_NEWS=1</p>
<p>RAM: 40MB budget</p>
<p>Priority: 5 (medium priority, research/academic profiles)</p>
</div>
</details>
</li>
<li><code>NonfeedSeedContext</code> (acquisition_strategy.py)
<details><summary>F222I: Bounded seed context for nonfeed lane query shaping.</summary>
<div class="doc-comment">
<p>F222I: Bounded seed context for nonfeed lane query shaping.</p>
<p></p>
<p>Produced by pivot planner / DuckDB seed extraction from text query.</p>
<p>Threaded into build_lane_query so lanes receive deterministic domain/IP</p>
<p>seeds instead of the generic text query.</p>
<p></p>
<p>Bounds:</p>
<p>- max_domains=10, max_ips=10, max_urls=10 — hard caps</p>
<p>- All fields are tuples (immutable, hashable)</p>
<p>- Publisher domains (source URL hostnames) are excluded from seeds</p>
<p></p>
<p>Lane shaping rules:</p>
<p>CT:          domains[0] if available, else empty</p>
<p>DOH:         domains[0] if available, else _disabled</p>
<p>WAYBACK:     domains[0] or URLs[0] if available</p>
<p>PASSIVE_DNS: domains[0] or IPs[0] if available</p>
<p>PUBLIC:      unchanged (original text query)</p>
<p>FEED:        unchanged</p>
</div>
</details>
</li>
<li><code>NonfeedSeedContext</code> (__init__.py)
<details><summary>F222I: Bounded seed context for nonfeed lane query shaping.</summary>
<div class="doc-comment">
<p>F222I: Bounded seed context for nonfeed lane query shaping.</p>
<p></p>
<p>Produced by pivot planner / DuckDB seed extraction from text query.</p>
<p>Threaded into build_lane_query so lanes receive deterministic domain/IP</p>
<p>seeds instead of the generic text query.</p>
<p></p>
<p>Bounds:</p>
<p>- max_domains=10, max_ips=10, max_urls=10 — hard caps</p>
<p>- All fields are tuples (immutable, hashable)</p>
<p>- Publisher domains (source URL hostnames) are excluded from seeds</p>
<p></p>
<p>Lane shaping rules:</p>
<p>CT:          domains[0] if available, else empty</p>
<p>DOH:         domains[0] if available, else _disabled</p>
<p>WAYBACK:     domains[0] or URLs[0] if available</p>
<p>PASSIVE_DNS: domains[0] or IPs[0] if available</p>
<p>PUBLIC:      unchanged (original text query)</p>
<p>FEED:        unchanged</p>
</div>
</details>
</li>
<li><code>ExecutionContextReadiness</code> (shadow_pre_decision.py)
<details><summary>Execution context readiness — DIAGNOSTIC ONLY.</summary>
<div class="doc-comment">
<p>Execution context readiness — DIAGNOSTIC ONLY.</p>
<p></p>
<p>Separované readiness pro tři dimenze:</p>
<p>1. Capability readiness — zda jsou available_capabilities dostatečné</p>
<p>2. Correlation readiness — zda scheduler má korelační klíče</p>
<p>3. Audit readiness — zda je exec_logger dostupný</p>
<p></p>
<p>Všechny tři dimenze musí být "ready" pro canonical execute_with_limits call.</p>
</div>
</details>
</li>
<li><code>DHTSidecarAdapter</code> (sidecar_protocol_adapters.py)
<details><summary>DHT (BitTorrent Kademlia) Discovery Sidecar.</summary>
<div class="doc-comment">
<p>DHT (BitTorrent Kademlia) Discovery Sidecar.</p>
<p></p>
<p>Queries DHT network for torrent metadata matching keywords.</p>
<p>BEP-05 based discovery for content invisible to web crawlers.</p>
<p></p>
<p>Env: HLEDAC_ENABLE_DHT=1</p>
<p>RAM: 100MB budget</p>
<p>Priority: 4 (lower priority, experimental)</p>
</div>
</details>
</li>
<li><code>ProviderReadinessPreview</code> (shadow_pre_decision.py)
<details><summary>Provider readiness preview — explicitní klasifikace provider readiness.</summary>
<div class="doc-comment">
<p>Provider readiness preview — explicitní klasifikace provider readiness.</p>
<p></p>
<p>DIAGNOSTIC ONLY — preview readiness bez activation.</p>
<p>NESMÍ volat load_model(), acquire(), unload(), execute_with_limits().</p>
<p>NESMÍ domýšlet chybějící facts jako ready.</p>
<p></p>
<p>Rozlišuje TŘI různé věci (NESMÍ splývat):</p>
<p>1. recommendation fact — co capabilities.py doporučuje</p>
<p>2. readiness preview — diagnostická klasifikace readiness z facts</p>
<p>3. actual activation — skutečné volání provider pool</p>
<p></p>
<p>Readiness klasifikace (pouze pokud facts podporují):</p>
<p>- ready: lifecycle ACTIVE/WINDUP + has_recommendation + normal/prune control</p>
<p>- deferred: lifecycle not ready OR recommendation deferred to future phase</p>
<p>- blocked: hard constraint (terminal phase, phase_conflict, panic mode)</p>
<p>- unknown: facts insufficient to determine readiness</p>
<p>- compat: lifecycle in COMPAT path (WARMUP), readiness indeterminate</p>
</div>
</details>
</li>
<li><code>FederatedResearchSidecarAdapter</code> (sidecar_protocol_adapters.py)
<details><summary>Federated Multi-Node Research Sidecar.</summary>
<div class="doc-comment">
<p>Federated Multi-Node Research Sidecar.</p>
<p></p>
<p>Wraps FederatedResearchCoordinator to expose the federated pattern</p>
<p>(multi-virtual-node, parallel, dedup) through the canonical</p>
<p>SidecarAdapterProtocol pipeline. Output is converted to</p>
<p>CanonicalFinding (or dict fallback) with source_type="federated_research".</p>
<p></p>
<p>This adapter does NOT inherit from BaseSidecarAdapter to keep the</p>
<p>federated/ package zero-coupled to runtime.sidecar_protocol. The</p>
<p>duck-typed subset of SidecarAdapterProtocol is sufficient:</p>
<p>- sidecar_id, env_gate, ram_budget_mb, priority  (class attrs)</p>
<p>- is_available()                                     (method)</p>
<p>- async run(ctx) -&gt; list                            (method, fail-soft)</p>
<p></p>
<p>Env: HLEDAC_ENABLE_FEDERATED=1</p>
<p>RAM: 30MB budget</p>
<p>Priority: 5 (medium, runs alongside other research sidecars)</p>
</div>
</details>
</li>
<li><code>ResourceRegistry</code> (sprint_scheduler_v1_archived.py)
<details><summary>Bounded resource registry with explicit lifecycle. No weakref.</summary>
<div class="doc-comment">
<p>Bounded resource registry with explicit lifecycle. No weakref.</p>
<p></p>
<p>Replaces ResourceLifecycleRegistry (WeakValueDictionary + dict + deque).</p>
<p>Single dict[str, Any] — simpler, faster, M1 8GB friendly.</p>
<p></p>
<p>Invariants:</p>
<p>- Always-on: no feature flags</p>
<p>- Bounded: max_size prevents unbounded growth</p>
<p>- Fail-safe: cleanup errors are suppressed, logged via try_op</p>
</div>
</details>
</li>
<li><code>AdvisoryGateSnapshot</code> (shadow_pre_decision.py)
<details><summary>Advisory gate snapshot — computed at scheduler decision points (WINDUP entry).</summary>
<div class="doc-comment">
<p>Advisory gate snapshot — computed at scheduler decision points (WINDUP entry).</p>
<p></p>
<p>DIAGNOSTIC ONLY — this artifact NESMÍ ovlivnit dispatch ani source ordering.</p>
<p>Pouze ukládá výsledek advisory gate evaluation pro diagnostiku/telemetry.</p>
<p></p>
<p>Na rozdíl od PreDecisionSummary (celkový stav), AdvisoryGateSnapshot</p>
<p>je scoped na konkrétní rozhodovací bod v scheduler loopu.</p>
<p></p>
<p>Rozlišuje:</p>
<p>- gate_outcome: "proceed" | "blocked" | "insufficient" | "unknown"</p>
<p>- blocker_reasons: konkrétní důvody blocking</p>
<p>- compat_seam_reasons: fyziologické compat seam důvody</p>
<p>- unknown_reasons: co je neznámé</p>
<p>- defer_to_provider: zda je provider activation deferred</p>
</div>
</details>
</li>
<li><code>AdvisoryRunOutcome</code> (sprint_advisory_runner.py)
<details><summary>Result of a full advisory run (all 6 advisory steps).</summary>
<div class="doc-comment">
<p>Result of a full advisory run (all 6 advisory steps).</p>
<p></p>
<p>Fields:</p>
<p>planned_pivots: Number of pivots planned (0 if planner skipped/failed)</p>
<p>executed_pivots: Number of pivots executed (0 if executor skipped/failed)</p>
<p>governor_recorded: True if governor evaluate+apply succeeded</p>
<p>brief_generated: True if analyst brief was generated</p>
<p>local_search_attempted: True if local search seam was queried</p>
<p>local_search_hits: Number of top results returned</p>
<p>local_search_source: "search_index" or "none"</p>
<p>local_search_indexed: Number of findings indexed</p>
<p>local_search_elapsed_ms: Wall time of index+search</p>
<p>local_search_top_results: list[dict] with url/title/score/source_type/finding_id</p>
<p>local_search_error: Error string if failed, else None</p>
<p>federated_attempted: True if federated bridge was queried</p>
<p>federated_nodes: Virtual nodes used in distributed run (0 if skipped)</p>
<p>federated_findings: Findings emitted from federated distribute_research</p>
<p>federated_bridge_updates: Bridge.update() calls during this advisory</p>
<p>federated_bridge_persists: Bridge.persist_if_due() writes during this advisory</p>
<p>federated_mode: Bridge mode (lightweight_only/lazy_hybrid/cross_sprint_persist)</p>
<p>federated_elapsed_ms: Wall time of the federated advisory</p>
<p>federated_error: Error string if failed, else None</p>
<p>error: Error message if any step failed, else None</p>
</div>
</details>
</li>
<li><code>EarlyExitClass</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F215D: Canonical early exit classification for sprint runs.</summary>
<div class="doc-comment">
<p>Sprint F215D: Canonical early exit classification for sprint runs.</p>
<p></p>
<p></p>
<p></p>
<p>Enforces that active300/600 runs that complete in &lt; 90% of planned</p>
<p></p>
<p>duration are NOT reported ambiguously as completed -- they must have an</p>
<p></p>
<p>explicit early exit class.</p>
<p></p>
<p></p>
<p></p>
<p>Values:</p>
<p></p>
<p>completed_full_duration              -- ran to or past planned duration</p>
<p></p>
<p>early_complete_no_work_remaining    -- work loop exited because no feed work remained</p>
<p></p>
<p>early_complete_return_guard_satisfied -- return_guard passed, windup entered legitimately early</p>
<p></p>
<p>early_complete_feed_only            -- feed-only run with zero nonfeed accepted findings</p>
<p></p>
<p>feed_dominant_nonfeed_rescue_attempted -- feed dominant, nonfeed rescue window was attempted (F220D)</p>
<p></p>
<p>aborted_by_memory                   -- aborted due to memory pressure / governor emergency</p>
<p></p>
<p>aborted_by_deadline                 -- hard deadline exceeded before completion</p>
<p></p>
<p>aborted_by_error                    -- exception in run() loop caused abort</p>
</div>
</details>
</li>
<li><code>NonfeedPlanDebug</code> (acquisition_strategy.py)
<details><summary>[F207L] Diagnostic snapshot of nonfeed lane planning for live KPI debugging.</summary>
<div class="doc-comment">
<p>[F207L] Diagnostic snapshot of nonfeed lane planning for live KPI debugging.</p>
<p></p>
<p>Records what the acquisition planner decided and why,</p>
<p>so live KPI can diagnose nonfeed_attempted=0 root cause.</p>
<p>F227D: Mutable so scheduler can annotate cap reason during sprint execution.</p>
</div>
</details>
</li>
<li><code>NonfeedPlanDebug</code> (__init__.py)
<details><summary>[F207L] Diagnostic snapshot of nonfeed lane planning for live KPI debugging.</summary>
<div class="doc-comment">
<p>[F207L] Diagnostic snapshot of nonfeed lane planning for live KPI debugging.</p>
<p></p>
<p>Records what the acquisition planner decided and why,</p>
<p>so live KPI can diagnose nonfeed_attempted=0 root cause.</p>
<p>F227D: Mutable so scheduler can annotate cap reason during sprint execution.</p>
</div>
</details>
</li>
<li><code>LaneBudgetPool</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>PublicSprintResult</code> (sprint_scheduler_v1_archived.py)
<details><summary>PUBLIC mode result -- public discovery pipeline fields guaranteed populated.</summary>
<div class="doc-comment">
<p>PUBLIC mode result -- public discovery pipeline fields guaranteed populated.</p>
<p></p>
<p></p>
<p></p>
<p>Populated when PUBLIC acquisition lane runs (discovery-&gt;fetch-&gt;parse-&gt;quality-&gt;storage).</p>
</div>
</details>
</li>
<li><code>ResourceLease</code> (sprint_scheduler_v1_archived.py)
<details><summary>Explicit, deterministic resource lease. No weakref magic.</summary>
<div class="doc-comment">
<p>Explicit, deterministic resource lease. No weakref magic.</p>
<p></p>
<p>Replaces _SprintCleanupHandle + weakref.finalize pattern.</p>
<p></p>
<p>Invariants:</p>
<p>- No weakref — lifecycle is fully explicit and deterministic</p>
<p>- Context manager protocol for deterministic cleanup</p>
<p>- Thread-safe: cleanup runs on the calling thread, not GC thread</p>
<p></p>
<p>M1 8GB: No GC pressure from weakref scanning.</p>
</div>
</details>
</li>
<li><code>PassiveTechStackSidecarAdapter</code> (sidecar_protocol_adapters.py)
<details><summary>R11: Passive tech-stack extraction — deterministic, no active scan.</summary>
<div class="doc-comment">
<p>R11: Passive tech-stack extraction — deterministic, no active scan.</p>
<p></p>
<p>Wraps `intelligence.passive_fingerprint.create_passive_tech_stack_adapter`</p>
<p>factory; calls `adapter.correlate(findings, query)`. Derived signal is</p>
<p>identical to `passive_fingerprint` for tech-stack component, but exposed</p>
<p>under its own registry ID for env-gated opt-in.</p>
<p></p>
<p>Env: HLEDAC_ENABLE_PASSIVE_TECH_STACK=1</p>
<p>RAM: 30MB budget</p>
<p>Priority: 4 (research-tier)</p>
</div>
</details>
</li>
<li><code>_PluginSidecarContext</code> (sidecar_orchestrator.py)
<details><summary>F350M-FED: Lightweight duck-typed SidecarContext for plugin sidecars.</summary>
<div class="doc-comment">
<p>F350M-FED: Lightweight duck-typed SidecarContext for plugin sidecars.</p>
<p></p>
<p>Constructed by SidecarOrchestrator._build_plugin_sidecar_context() from</p>
<p>the bound scheduler state. Avoids a hard import of SidecarContext</p>
<p>(which lives in runtime.sidecar_protocol) at module load time and</p>
<p>matches the attribute-based access pattern that registered adapters</p>
<p>use (getattr(ctx, "query") etc.).</p>
<p></p>
<p>SidecarRegistry.get_available() returns adapter instances; their</p>
<p>run(ctx) implementations read ctx attributes via getattr, so any</p>
<p>object with the 5 fields is accepted. We use this typed shim for</p>
<p>IDE/typing clarity but it is structurally compatible with</p>
<p>SidecarContext.</p>
</div>
</details>
</li>
<li><code>SprintRunContext</code> (sprint_scheduler_v1_archived.py)
<details><summary>Per-sprint mutable state — replaces instance dicts in SprintScheduler.</summary>
<div class="doc-comment">
<p>Per-sprint mutable state — replaces instance dicts in SprintScheduler.</p>
<p></p>
<p>Accessed via get_sprint_ctx() for copy-on-write isolation between</p>
<p>concurrent sprints or async tasks.</p>
<p></p>
<p>ISSUE-3 FIX: Memory bounds</p>
<p>- recent_iocs: bounded to 200 entries via collections.deque(maxlen=200)</p>
<p>(replaces unbounded list[dict] which leaked on 18h sprints)</p>
<p>- seen_hashes: BoundedLRUDict(maxsize=100_000) — LRU eviction with drop counter</p>
<p>- entries_per_source / hits_per_source: BoundedLRUDict(maxsize=500) each</p>
<p>- source_weights: BoundedLRUDict(maxsize=500)</p>
<p>- novelty_bonuses: BoundedLRUDict(maxsize=10_000)</p>
<p>- feed_accepted_per_source: BoundedLRUDict(maxsize=500)</p>
<p>- fetch_latency_ema: BoundedLRUDict(maxsize=200)</p>
<p>- arrow_batch: bounded HARD_CAP=50 000 with oldest eviction on flush failure</p>
<p>- pivot_rewards: bounded at usage site via history[-20:] slice</p>
<p>- pivot_stats: replaced per reset, not a BoundedLRUDict (tiny dict)</p>
<p></p>
<p>All BoundedLRUDict fields use LRU eviction — least-recently-used entry</p>
<p>is silently evicted when capacity is reached. Drop counters in</p>
<p>SprintSchedulerResult track evictions for telemetry.</p>
</div>
</details>
</li>
<li><code>PassiveFingerprintSidecarAdapter</code> (sidecar_protocol_adapters.py)
<details><summary>F204G: Passive service fingerprinting — deterministic, no active scan.</summary>
<div class="doc-comment">
<p>F204G: Passive service fingerprinting — deterministic, no active scan.</p>
<p></p>
<p>Lazy-imports `intelligence.passive_fingerprint.create_passive_fingerprint_adapter`</p>
<p>factory; invokes `adapter.correlate(findings, query)` and returns the</p>
<p>derived CanonicalFindings.</p>
<p></p>
<p>Env: HLEDAC_ENABLE_PASSIVE_FINGERPRINT=1</p>
<p>RAM: 50MB budget</p>
<p>Priority: 4 (research-tier)</p>
</div>
</details>
</li>
<li><code>SprintFlags</code> (sprint_entrypoint.py)
<details><summary>F221-ABORT + F26X-3 + F260: Bounded, immutable view of the CLI flags</summary>
<div class="doc-comment">
<p>F221-ABORT + F26X-3 + F260: Bounded, immutable view of the CLI flags</p>
<p>that gate pre-flight guards and layer-injection opt-outs. Mirrors the</p>
<p>args Namespace fields required by run_sprint() and gives downstream</p>
<p>seams (e.g. future advisory hooks) a typed contract instead of</p>
<p>getattr-style probing.</p>
<p></p>
<p>M1 memory friendly: frozen +  removes GC tracking + boxing</p>
<p>(smaller per-instance footprint, less GC pressure during sprint cycles).</p>
<p></p>
<p>Sprint F26X-3/F260 fix: this dataclass is now the SOLE carrier of</p>
<p>layer-injection flags (no_communication/no_stealth/no_ghost) into</p>
<p>run_sprint(). Replaces the previous getattr(args, "no_*", False)</p>
<p>pattern that leaked the `args` namespace from main() — `args` is a</p>
<p>local of argparse, never passed to run_sprint(), causing NameError.</p>
<p></p>
<p>Keep this Struct minimal: only flags that affect pre-flight</p>
<p>decisions or that callers consume as a coherent bundle belong here.</p>
<p>Per-flag args stay in argparse.</p>
<p></p>
<p>Sprint S2 (msgspec.Struct migration): attribute access, frozen, and</p>
<p>default-arg construction all work identically to the prior @dataclass</p>
<p>form. The only change is implementation: ~2-3× faster __init__ and</p>
<p>~40B/instance smaller footprint.</p>
</div>
</details>
</li>
<li><code>PivotStats</code> (pivot_planner.py)
<details><summary>Tracks pivot usage history for exponential decay scoring.</summary>
<div class="doc-comment">
<p>Tracks pivot usage history for exponential decay scoring.</p>
<p>Tracks successes/failures so underperforming or stale pivots lose priority.</p>
</div>
</details>
</li>
<li><code>SocialIdentityMinerSidecarAdapter</code> (sidecar_protocol_adapters.py)
<details><summary>F204I: Social identity surface miner — extract usernames/profiles from findings.</summary>
<div class="doc-comment">
<p>F204I: Social identity surface miner — extract usernames/profiles from findings.</p>
<p></p>
<p>Wraps `intelligence.social_identity_miner.create_social_identity_miner_adapter`</p>
<p>factory. `mine()` requires a `DuckDBShadowStore` instance which is not in</p>
<p>SidecarContext, so the adapter is **wiring-only**: registers the sidecar</p>
<p>for availability + env-gate, but returns `[]` from `run_async` so the</p>
<p>canonical execution path (SprintScheduler with store handle) remains</p>
<p>authoritative. This avoids double-execution of the social identity scan.</p>
<p></p>
<p>Env: HLEDAC_ENABLE_SOCIAL_IDENTITY_SURFACE=1</p>
<p>RAM: 60MB budget</p>
<p>Priority: 5</p>
</div>
</details>
</li>
<li><code>_SprintCleanupHandle</code> (sprint_scheduler_v1_archived.py)
<details><summary>Explicit cleanup handle — no weakref.finalize.</summary>
<div class="doc-comment">
<p>Explicit cleanup handle — no weakref.finalize.</p>
<p></p>
<p>Replaces weakref.finalize pattern with explicit cleanup() call.</p>
<p>Deterministic: cleanup runs on the calling thread, not GC thread.</p>
<p></p>
<p>Usage:</p>
<p>handle = _SprintCleanupHandle(sprint_scheduler_instance)</p>
<p>try:</p>
<p># sprint work</p>
<p>finally:</p>
<p>handle.cleanup()  # explicit, deterministic, no GC dependency</p>
</div>
</details>
</li>
<li><code>TemporalArchaeologySidecarAdapter</code> (sidecar_protocol_adapters.py)
<details><summary>F202E: Temporal archaeology timeline synthesis.</summary>
<div class="doc-comment">
<p>F202E: Temporal archaeology timeline synthesis.</p>
<p></p>
<p>Wraps `intelligence.temporal_archaeologist.create_temporal_archaeologist`</p>
<p>factory. The archaeologist exposes a context-managed async API</p>
<p>(`__aenter__`/`recover_deleted_content`/`reconstruct_version_history`)</p>
<p>that does not match the unified `correlate(findings, query)` contract,</p>
<p>so the adapter is **wiring-only**: registers the sidecar for</p>
<p>availability + env-gate. Actual execution is routed through</p>
<p>`intelligence.temporal_archaeologist_adapter.create_temporal_archaeologist_adapter`</p>
<p>invoked by SprintScheduler with a CT-findings slice.</p>
<p></p>
<p>Env: HLEDAC_ENABLE_TEMPORAL_ARCHAEOLOGY=1</p>
<p>RAM: 80MB budget</p>
<p>Priority: 4</p>
</div>
</details>
</li>
<li><code>DispatchTaxonomy</code> (shadow_pre_decision.py)
<details><summary>Dispatch taxonomy pro scheduler-shadow dispatch parity preview.</summary>
<div class="doc-comment">
<p>Dispatch taxonomy pro scheduler-shadow dispatch parity preview.</p>
<p></p>
<p>Rozlišuje mezi:</p>
<p>- CANONICAL_TOOL_DISPATCH: task/tool má čistý ToolRegistry mapping</p>
<p>- RUNTIME_ONLY_COMPAT_DISPATCH: task/type používá inline get_task_handler(),</p>
<p>nemá canonical ToolRegistry mapping</p>
<p>- DISPATCH_READY: všechny podmínky pro dispatch jsou splněny</p>
<p>- DISPATCH_BLOCKED: capability missing nebo hard constraint</p>
<p>- DISPATCH_PRUNED: control mode prune/panic</p>
<p>- DISPATCH_UNKNOWN: nelze určit readiness</p>
</div>
</details>
</li>
<li><code>SourceEconomics</code> (sprint_scheduler_v1_archived.py)
<details><summary>Per-source economics state for one sprint.</summary>
<div class="doc-comment">
<p>Per-source economics state for one sprint.</p>
<p></p>
<p></p>
<p></p>
<p>All fields are in-memory only. Reset happens in _reset_result().</p>
<p></p>
<p>No cross-sprint persistence. No background tasks.</p>
<p></p>
<p></p>
<p></p>
<p>Bounded:</p>
<p></p>
<p>- silent_streak: int (unbounded within sprint, capped by sprint length)</p>
<p></p>
<p>- cooldown_until_cycle: int | None (None = not in cooldown)</p>
<p></p>
<p>- recent_health_posture: str (one of hot/warm/lukewarm/marginal/cold)</p>
</div>
</details>
</li>
<li><code>AcquisitionLaneOutcome</code> (acquisition_strategy.py) — <span class="doc-comment-inline">Acquisition lane outcome DTO. Migrated from @dataclass(frozen=True) → msgspec.Struct.</span></li>
<li><code>Pivot</code> (pivot_planner.py)
<details><summary>A single investigation pivot derived from findings.</summary>
<div class="doc-comment">
<p>A single investigation pivot derived from findings.</p>
<p></p>
<p></p>
<p>Fields:</p>
<p>priority: Order key (negative = higher priority first)</p>
<p>pivot_id: Stable unique identifier for this pivot.</p>
<p>pivot_type: One of domain/identity/leak/archive/graph</p>
<p>ioc_value: The IOC value to pivot on</p>
<p>ioc_type: Type of IOC (ip, domain, hash, email, url, etc.)</p>
<p>reason: Human-readable justification for this pivot</p>
<p>expected_value: Confidence score [0.0, 1.0]</p>
<p>source_hint: Which finding/envelope triggered this pivot</p>
<p>evidence_pointers: List of source finding_ids</p>
</div>
</details>
</li>
<li><code>CTLossStage</code> (sprint_scheduler_v1_archived.py)
<details><summary>Enum describing where CT raw evidence is lost in the live bridge path.</summary>
<div class="doc-comment">
<p>Enum describing where CT raw evidence is lost in the live bridge path.</p>
<p></p>
<p></p>
<p></p>
<p>Canonical live path: crtsh_adapter -&gt; ct_results_to_findings -&gt; candidates -&gt;</p>
<p></p>
<p>duckdb async_ingest -&gt; lane_ct_accepted_findings -&gt; benchmark report.</p>
<p></p>
<p></p>
<p></p>
<p>Any deviation from this path constitutes a loss stage.</p>
</div>
</details>
</li>
<li><code>LedgerRecord</code> (nonfeed_candidate_ledger.py)
<details><summary>Sprint F217E: Bounded nonfeed candidate lifecycle record.</summary>
<div class="doc-comment">
<p>Sprint F217E: Bounded nonfeed candidate lifecycle record.</p>
<p></p>
<p>No full payload. No sensitive blobs. All fields are primitives.</p>
<p>candidate_id is truncated BLAKE2b hash of the actual value — stable</p>
<p>identifier without leaking the raw IOC.</p>
</div>
</details>
</li>
<li><code>AcquisitionLaneOutcome</code> (__init__.py)</li>
<li><code>IdentityStitchingSidecarAdapter</code> (sidecar_protocol_adapters.py)
<details><summary>F202B: Identity stitching engine — heavy, RAM-guarded by bus.</summary>
<div class="doc-comment">
<p>F202B: Identity stitching engine — heavy, RAM-guarded by bus.</p>
<p></p>
<p>Wraps `intelligence.identity_stitching.create_identity_stitching_engine`</p>
<p>factory. The engine exposes a builder API (`add_profile`, `find_matches`,</p>
<p>`find_all_matches`) that does not match the unified `correlate(findings,</p>
<p>query)` contract used by other F350M-R adapters, so the adapter is</p>
<p>**wiring-only**: registers the sidecar for availability + env-gate, with</p>
<p>actual execution routed through the canonical</p>
<p>`intelligence.identity_stitching_canonical.create_identity_stitching_adapter`</p>
<p>path which the SprintScheduler invokes directly.</p>
<p></p>
<p>Env: HLEDAC_ENABLE_IDENTITY_STITCHING=1</p>
<p>RAM: 100MB budget</p>
<p>Priority: 5</p>
</div>
</details>
</li>
<li><code>LifecycleInterpretation</code> (shadow_pre_decision.py)
<details><summary>Lifecycle interpretation summary — composed from ParityArtifact.</summary>
<div class="doc-comment">
<p>Lifecycle interpretation summary — composed from ParityArtifact.</p>
<p></p>
<p>Interpretuje workflow_phase, control_phase a windup_local_phase</p>
<p>z hlediska scheduler pre-decision, aniž by zasahovalo do lifecycle.</p>
</div>
</details>
</li>
<li><code>ToolReadinessPreview</code> (shadow_pre_decision.py)
<details><summary>Tool readiness preview — DIAGNOSTIC ONLY, no dispatch, no execute_with_limits.</summary>
<div class="doc-comment">
<p>Tool readiness preview — DIAGNOSTIC ONLY, no dispatch, no execute_with_limits.</p>
<p></p>
<p>Čte POUZE z existujícího ToolRegistry surface (list_tools, get_tool_cards).</p>
<p>NESMÍ volat acquire(), load_model(), nebo jakékoli provider activation.</p>
<p></p>
<p>Tento preview rozlišuje:</p>
<p>- TOOL_READINESS_READY: tools available, can execute</p>
<p>- TOOL_READINESS_DEGRADED: some tools unavailable due to resource pressure</p>
<p>- TOOL_READINESS_PRUNED: tools heavily pruned (panic mode)</p>
<p>- TOOL_READINESS_UNKNOWN: cannot determine tool readiness</p>
</div>
</details>
</li>
<li><code>HealthReport</code> (sprint_scheduler_v1_archived.py)
<details><summary>F228F: Pre-run health check result for critical dependencies.</summary>
<div class="doc-comment">
<p>F228F: Pre-run health check result for critical dependencies.</p>
<p>F265.1: Extended with EvidenceLog, memory pressure checks.</p>
<p></p>
<p>Returned by SprintScheduler.health_check() -- NEVER raises.</p>
</div>
</details>
</li>
<li><code>_PublicStage</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>AcquisitionContext</code> (acquisition_strategy.py) — <span class="doc-comment-inline">Derived flags bundle for lane planning — constructed once per _build_plan_impl call.</span></li>
<li><code>SourceFamilyOutcome</code> (acquisition_strategy.py)
<details><summary>Normalized outcome for one source family (lane) in the scheduler report.</summary>
<div class="doc-comment">
<p>Normalized outcome for one source family (lane) in the scheduler report.</p>
<p>Migrated from @dataclass(frozen=True) → msgspec.Struct.</p>
<p></p>
<p>F207G: Unifies CTOutcome, PassiveDNSOutcome, WaybackDiffResult, and feed</p>
<p>balance telemetry into one canonical shape so diagnostics have a single</p>
<p>place to explain per-family zero-yield.</p>
</div>
</details>
</li>
<li><code>AcquisitionContext</code> (__init__.py) — <span class="doc-comment-inline">Derived flags bundle for lane planning — constructed once per _build_plan_impl call.</span></li>
<li><code>DecisionGateReadiness</code> (shadow_pre_decision.py)
<details><summary>Decision gate readiness — explicit rozlišení pro scheduler decision gate.</summary>
<div class="doc-comment">
<p>Decision gate readiness — explicit rozlišení pro scheduler decision gate.</p>
<p></p>
<p>DIAGNOSTIC ONLY — tento artifact NESMÍ být použit pro skutečná</p>
<p>scheduler rozhodnutí. Pouze pro diagnostický výstup.</p>
<p></p>
<p>Rozlišuje:</p>
<p>- DECISION_GATE_READY: všechny facts dostatečné, žádné blockers</p>
<p>- DECISION_GATE_BLOCKED: hard blockers present — cannot proceed</p>
<p>- DECISION_GATE_INSUFFICIENT: facts insufficient for decision</p>
<p>- DECISION_GATE_UNKNOWN: cannot determine readiness</p>
</div>
</details>
</li>
<li><code>SourceFamilyOutcome</code> (__init__.py)
<details><summary>Normalized outcome for one source family (lane) in the scheduler report.</summary>
<div class="doc-comment">
<p>Normalized outcome for one source family (lane) in the scheduler report.</p>
<p></p>
<p>F207G: Unifies CTOutcome, PassiveDNSOutcome, WaybackDiffResult, and feed</p>
<p>balance telemetry into one canonical shape so diagnostics have a single</p>
<p>place to explain per-family zero-yield.</p>
</div>
</details>
</li>
<li><code>ProviderActivationNote</code> (shadow_pre_decision.py)
<details><summary>Provider activation note — deferred/unknown only, NO simulation.</summary>
<div class="doc-comment">
<p>Provider activation note — deferred/unknown only, NO simulation.</p>
<p></p>
<p>DIAGNOSTIC ONLY. Tento note NESMÍ:</p>
<p>- Simulovat load order providerů</p>
<p>- Simulovat provider state machine</p>
<p>- Vzniknout pseudo-authorita provider plane</p>
<p></p>
<p>Rozlišuje:</p>
<p>- PROVIDER_DEFERRED: activation deferred to future phase</p>
<p>- PROVIDER_UNKNOWN: cannot determine provider readiness</p>
<p>- PROVIDER_NOT_READY: provider not ready</p>
<p>- PROVIDER_BLOCKED: blocked by hard constraint</p>
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
<li><code>WindupReadinessPreview</code> (shadow_pre_decision.py)
<details><summary>Windup readiness preview — from existing fact bundles, DIAGNOSTIC ONLY.</summary>
<div class="doc-comment">
<p>Windup readiness preview — from existing fact bundles, DIAGNOSTIC ONLY.</p>
<p></p>
<p>Čte z LifecycleSnapshotBundle a ExportReadinessSummary.</p>
<p>NESMÍ měnit ownership, NESMÍ aktivovat windup engine.</p>
<p></p>
<p>Rozlišuje:</p>
<p>- WINDUP_READY: windup facts sufficient</p>
<p>- WINDUP_PARTIAL: some windup facts missing</p>
<p>- WINDUP_INSUFFICIENT: windup facts insufficient</p>
<p>- WINDUP_NOT_ACTIVE: not in WINDUP phase</p>
</div>
</details>
</li>
<li><code>DomainCandidate</code> (nonfeed_candidate_ledger.py)
<details><summary>F214: Domain candidate extracted from feed/public findings text.</summary>
<div class="doc-comment">
<p>F214: Domain candidate extracted from feed/public findings text.</p>
<p></p>
<p>Fields:</p>
<p>domain:        Normalized lower-case domain (or IP address)</p>
<p>source_family: "PUBLIC" | "FEED"</p>
<p>source_field:  "body" | "title" | "url"</p>
<p>confidence:    Extraction confidence [0.0, 1.0]</p>
<p>reason:        Why this was extracted</p>
<p>seen_count:    How many findings mentioned this domain</p>
<p>sample_context: Bounded text snippet where domain appeared (max 200 chars)</p>
</div>
</details>
</li>
<li><code>PreWindupBarrierResult</code> (sprint_scheduler_v1_archived.py)
<details><summary>Result of a pre-windup barrier check.</summary>
<div class="doc-comment">
<p>Result of a pre-windup barrier check.</p>
<p></p>
<p></p>
<p></p>
<p>Returned by _ensure_pre_windup_lane_terminal_states() to inform</p>
<p></p>
<p>the windup decision whether required lanes are satisfied.</p>
</div>
</details>
</li>
<li><code>NonfeedMissionSnapshot</code> (acquisition_strategy.py)
<details><summary>F217B: Snapshot of nonfeed mission controller state at a point in time.</summary>
<div class="doc-comment">
<p>F217B: Snapshot of nonfeed mission controller state at a point in time.</p>
<p></p>
<p>This is a plain msgspec.Struct (mutable) so that the scheduler can</p>
<p>accumulate state over the sprint lifetime.</p>
</div>
</details>
</li>
<li><code>NonfeedMissionSnapshot</code> (__init__.py)
<details><summary>F217B: Snapshot of nonfeed mission controller state at a point in time.</summary>
<div class="doc-comment">
<p>F217B: Snapshot of nonfeed mission controller state at a point in time.</p>
<p></p>
<p>This is a plain dataclass (not frozen) so that the scheduler can</p>
<p>accumulate state over the sprint lifetime.</p>
</div>
</details>
</li>
<li><code>PrecursorSummary</code> (shadow_pre_decision.py)
<details><summary>Provider/Branch precursor summary — composed from ParityArtifact.</summary>
<div class="doc-comment">
<p>Provider/Branch precursor summary — composed from ParityArtifact.</p>
<p></p>
<p>Interpretuje provider a branch decision precursors z hlediska pre-decision.</p>
</div>
</details>
</li>
<li><code>SidecarBatch</code> (sidecar_bus.py) — <span class="doc-comment-inline">Batch of accepted findings submitted to the sidecar bus.</span></li>
<li><code>AcquisitionStrategySnapshot</code> (acquisition_strategy.py) — <span class="doc-comment-inline">Full acquisition strategy snapshot for one sprint/cycle.</span></li>
<li><code>GraphCapabilitySummary</code> (shadow_pre_decision.py)
<details><summary>Graph capability summary — composed from ParityArtifact.</summary>
<div class="doc-comment">
<p>Graph capability summary — composed from ParityArtifact.</p>
<p></p>
<p>Interpretuje graph facts z hlediska pre-decision.</p>
</div>
</details>
</li>
<li><code>ModelControlSummary</code> (shadow_pre_decision.py)
<details><summary>Model/control fact summary — composed from ParityArtifact.</summary>
<div class="doc-comment">
<p>Model/control fact summary — composed from ParityArtifact.</p>
<p></p>
<p>Interpretuje model/control facts z hlediska pre-decision.</p>
</div>
</details>
</li>
<li><code>AcquisitionLane</code> (acquisition_strategy.py)</li>
<li><code>AcquisitionLane</code> (__init__.py)</li>
<li><code>AcquisitionStrategySnapshot</code> (__init__.py) — <span class="doc-comment-inline">Full acquisition strategy snapshot for one sprint/cycle.</span></li>
<li><code>ExportReadinessSummary</code> (shadow_pre_decision.py)
<details><summary>Export readiness summary — composed from ParityArtifact.</summary>
<div class="doc-comment">
<p>Export readiness summary — composed from ParityArtifact.</p>
<p></p>
<p>Interpretuje export handoff facts z hlediska pre-decision.</p>
</div>
</details>
</li>
<li><code>NonfeedSprintResult</code> (sprint_scheduler_v1_archived.py)
<details><summary>Nonfeed mode result -- nonfeed lane fields (CT, WAYBACK, PASSIVE_DNS, BLOCKCHAIN, PIVOT).</summary>
<div class="doc-comment">
<p>Nonfeed mode result -- nonfeed lane fields (CT, WAYBACK, PASSIVE_DNS, BLOCKCHAIN, PIVOT).</p>
<p></p>
<p></p>
<p></p>
<p>Populated when any nonfeed acquisition lane runs. Contains lane-specific</p>
<p></p>
<p>telemetry for all nonfeed lanes combined.</p>
</div>
</details>
</li>
<li><code>MissionIntent</code> (acquisition_strategy.py)
<details><summary>F225A: Lightweight mission intent classification.</summary>
<div class="doc-comment">
<p>F225A: Lightweight mission intent classification.</p>
<p></p>
<p>Additive telemetry — does NOT change lane enable/disable logic.</p>
<p>Does NOT bypass UMA/hardware safety, enable stealth/browser,</p>
<p>or increase network aggressiveness.</p>
</div>
</details>
</li>
<li><code>MissionIntent</code> (__init__.py)
<details><summary>F225A: Lightweight mission intent classification.</summary>
<div class="doc-comment">
<p>F225A: Lightweight mission intent classification.</p>
<p></p>
<p>Additive telemetry — does NOT change lane enable/disable logic.</p>
<p>Does NOT bypass UMA/hardware safety, enable stealth/browser,</p>
<p>or increase network aggressiveness.</p>
</div>
</details>
</li>
<li><code>GovernorDecision</code> (resource_governor.py) — <span class="doc-comment-inline">Output of M1ResourceGovernor.evaluate().</span></li>
<li><code>MandatoryLaneTerminality</code> (acquisition_strategy.py)
<details><summary>[F208A] Sprint F300 migration: @dataclass(slots=True) → msgspec.Struct.</summary>
<div class="doc-comment">
<p>[F208A] Sprint F300 migration: @dataclass(slots=True) → msgspec.Struct.</p>
<p></p>
<p>A mandatory lane must reach a terminal state (attempted, skipped, error, timeout)</p>
<p>before a sprint is considered complete. This dataclass defines the contract.</p>
<p>C-level __init__ (~2-3× faster), no GC tracking (~40B saved).</p>
</div>
</details>
</li>
<li><code>GovernorSnapshot</code> (resource_governor.py) — <span class="doc-comment-inline">Snapshot of governor internal state for dashboard rendering.</span></li>
<li><code>FeedSprintResult</code> (sprint_scheduler_v1_archived.py)
<details><summary>FEED mode result -- feed-specific telemetry fields guaranteed populated.</summary>
<div class="doc-comment">
<p>FEED mode result -- feed-specific telemetry fields guaranteed populated.</p>
<p></p>
<p></p>
<p></p>
<p>Populated when FEED acquisition lane runs (structured TI feeds).</p>
</div>
</details>
</li>
<li><code>RiskLevel</code> (acquisition_strategy.py)
<details><summary>Risk levels for acquisition lane planning.</summary>
<div class="doc-comment">
<p>Risk levels for acquisition lane planning.</p>
<p></p>
<p>Inherits from `str` so the enum members are also `str` instances —</p>
<p>preserves the existing `risk_level: str = RiskLevel.MEDIUM` field</p>
<p>type without forcing all callers to migrate. Values match canonical</p>
<p>`project_types.RiskLevel` (lowercase).</p>
</div>
</details>
</li>
<li><code>LaneRule</code> (acquisition_strategy.py)
<details><summary>Table-driven lane planning rule.</summary>
<div class="doc-comment">
<p>Table-driven lane planning rule.</p>
<p></p>
<p>One rule per AcquisitionLane.  The enabled/reason/concurrency logic</p>
<p>is expressed as pure functions of AcquisitionContext so the full</p>
<p>decision table is visible and auditable in one place.</p>
</div>
</details>
</li>
<li><code>RiskLevel</code> (__init__.py)
<details><summary>Risk levels for acquisition lane planning.</summary>
<div class="doc-comment">
<p>Risk levels for acquisition lane planning.</p>
<p></p>
<p>Inherits from `str` so the enum members are also `str` instances —</p>
<p>preserves the existing `risk_level: str = RiskLevel.MEDIUM` field</p>
<p>type without forcing all callers to migrate. Values match canonical</p>
<p>`project_types.RiskLevel` (lowercase).</p>
</div>
</details>
</li>
<li><code>LaneRule</code> (__init__.py)
<details><summary>Table-driven lane planning rule.</summary>
<div class="doc-comment">
<p>Table-driven lane planning rule.</p>
<p></p>
<p>One rule per AcquisitionLane.  The enabled/reason/concurrency logic</p>
<p>is expressed as pure functions of AcquisitionContext so the full</p>
<p>decision table is visible and auditable in one place.</p>
</div>
</details>
</li>
<li><code>MandatoryLaneTerminality</code> (__init__.py)
<details><summary>[F208A] Canonical terminality contract for mandatory lanes.</summary>
<div class="doc-comment">
<p>[F208A] Canonical terminality contract for mandatory lanes.</p>
<p></p>
<p>A mandatory lane must reach a terminal state (attempted, skipped, error, timeout)</p>
<p>before a sprint is considered complete. This dataclass defines the contract.</p>
</div>
</details>
</li>
<li><code>ToolCapabilityGap</code> (shadow_pre_decision.py) — <span class="doc-comment-inline">Capability gap pro jeden tool.</span></li>
<li><code>BranchAdmission</code> (resource_governor.py)
<details><summary>F214R: Result of branch admission check.</summary>
<div class="doc-comment">
<p>F214R: Result of branch admission check.</p>
<p></p>
<p>Answers: can a named branch run given current memory state?</p>
<p>estimated_mb is the expected RAM cost of the branch.</p>
</div>
</details>
</li>
<li><code>LaneAdmission</code> (resource_governor.py)
<details><summary>F214R: Result of lane admission check.</summary>
<div class="doc-comment">
<p>F214R: Result of lane admission check.</p>
<p></p>
<p>Answers: can a named lane be admitted given current memory state?</p>
<p>risk_level: "low" | "medium" | "high" | "critical"</p>
<p>estimated_mb: expected RAM cost of the lane.</p>
</div>
</details>
</li>
<li><code>GraphServiceLifecycle</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Explicit lifecycle Protocol for GraphService — replaces weakref to global.</span></li>
<li><code>FeedDominanceGuardResult</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">F214: Result of FeedDominanceGuard.compute().</span></li>
<li><code>MissionTargetKind</code> (acquisition_strategy.py) — <span class="doc-comment-inline">F225A: Target kind derived from query analysis.</span></li>
<li><code>MissionTargetKind</code> (__init__.py) — <span class="doc-comment-inline">F225A: Target kind derived from query analysis.</span></li>
<li><code>RendererAdmission</code> (resource_governor.py)
<details><summary>F214R: Result of renderer admission check.</summary>
<div class="doc-comment">
<p>F214R: Result of renderer admission check.</p>
<p></p>
<p>One unified answer to: can JS renderer be used right now?</p>
<p>Combines model lifecycle + UMA state in one call.</p>
</div>
</details>
</li>
<li><code>ModelAdmission</code> (resource_governor.py)
<details><summary>F214R: Result of model load admission check.</summary>
<div class="doc-comment">
<p>F214R: Result of model load admission check.</p>
<p></p>
<p>One unified answer to: can a new model load be initiated?</p>
<p>Uses current UMA state (not model lifecycle — that's caller-provided).</p>
</div>
</details>
</li>
<li><code>AcquisitionLanePlan</code> (acquisition_strategy.py) — <span class="doc-comment-inline">Plan for one acquisition lane.</span></li>
<li><code>AcquisitionLanePlan</code> (__init__.py) — <span class="doc-comment-inline">Plan for one acquisition lane.</span></li>
<li><code>_FeedWork</code> (acquisition.py)
<details><summary>Work item for one feed source. Compatible with _async_run_live_feed signature.</summary>
<div class="doc-comment">
<p>Work item for one feed source. Compatible with _async_run_live_feed signature.</p>
<p></p>
<p>Migrated from @dataclass(slots=True) to msgspec.Struct (frozen=True).</p>
</div>
</details>
</li>
<li><code>MissionBudgetSnapshot</code> (resource_governor.py) — <span class="doc-comment-inline">F204J: Budget snapshot for scorecard export.</span></li>
<li><code>_AdvisoryLogLRU</code> (sprint_scheduler_v1_archived.py)
<details><summary>FIFO advisory dedup: dict for O(1) counts, deque for O(1) insertion order.</summary>
<div class="doc-comment">
<p>FIFO advisory dedup: dict for O(1) counts, deque for O(1) insertion order.</p>
<p></p>
<p>No-promote on hit: deque order is never modified on cache hit — only on insert/evict.</p>
</div>
</details>
</li>
<li><code>IntCounterLayoutProto</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Minimal duck-typed interface for IntCounterLayout (used in hot-path properties).</span></li>
<li><code>SourceTier</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Feed source priority tier.</span></li>
<li><code>_MinimalCtx</code> (scheduler.py)</li>
<li><code>SidecarAdmission</code> (resource_governor.py) — <span class="doc-comment-inline">F204J: Result of sidecar admission check.</span></li>
<li><code>SourceWork</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">A single source fetch unit.</span></li>
<li><code>PivotTask</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Pivot task pro agentic pivot loop -- prioritizován podle confidence * degree.</span></li>
<li><code>_CoremlNativeLibFilter</code> (sprint_entrypoint.py)</li>
<li><code>NonfeedMissionExitReason</code> (acquisition_strategy.py) — <span class="doc-comment-inline">F217B: Canonical mission exit reason values.</span></li>
<li><code>NonfeedMissionExitReason</code> (__init__.py) — <span class="doc-comment-inline">F217B: Canonical mission exit reason values.</span></li>
<li><code>PivotType</code> (pivot_planner.py) — <span class="doc-comment-inline">Pivot type constants.</span></li>
<li><code>SidecarRunResult</code> (sidecar_bus.py)</li>
<li><code>SprintPhase</code> (sprint_lifecycle.py)</li>
<li><code>LaneBudgetAllocation</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_DiffFinding</code> (sidecar_bus.py)</li>
<li><code>_KCTFinding</code> (sidecar_bus.py)</li>
<li><code>_Sentinel</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>LaneSpec</code> (acquisition_strategy.py) — <span class="doc-comment-inline">Static per-lane execution constants.</span></li>
<li><code>LaneSpec</code> (__init__.py) — <span class="doc-comment-inline">Static per-lane execution constants.</span></li>
<li><code>_SeedCtx</code> (acquisition.py)</li>
<li><code>SprintTooShortError</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Raised when sprint duration is below minimum.</span></li>
<li><code>RAMBudgetExceeded</code> (role_based_pools.py) — <span class="doc-comment-inline">Raised when a role's RAM budget would be exceeded.</span></li>
<li><code>BarrierResult</code> (acquisition.py)</li>
<li><code>SprintLifecycleError</code> (sprint_lifecycle.py) — <span class="doc-comment-inline">Base exception for sprint lifecycle errors.</span></li>
<li><code>InvalidPhaseTransitionError</code> (sprint_lifecycle.py) — <span class="doc-comment-inline">Raised when a non-monotonic phase transition is attempted.</span></li>
</ul>
</details>

<details><summary><strong>Method</strong> (721)</summary>
<ul>
<li><code>_run_internal</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_run_mandatory_acquisition_prelude</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_run_one_cycle_aggressive</code> (sprint_scheduler_v1_archived.py)
<details><summary>Aggressive mode: feed, public discovery, and CT branches fire concurrently.</summary>
<div class="doc-comment">
<p>Aggressive mode: feed, public discovery, and CT branches fire concurrently.</p>
<p></p>
<p>Each branch has its own timeout budget; slow branches are cancelled without</p>
<p></p>
<p>affecting other branches.</p>
<p></p>
<p></p>
<p></p>
<p>F212-B: All branch timeouts are remaining-time-aware and capped at</p>
<p></p>
<p>min(config_timeout, remaining * 0.5, MAX_CAP). Branches are skipped with</p>
<p></p>
<p>terminal outcome when remaining time is below the safety floor.</p>
</div>
</details>
</li>
<li><code>compute_sprint_intelligence</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint 8VN: Lazy fail-soft computation of correlation + hypothesis seams.</summary>
<div class="doc-comment">
<p>Sprint 8VN: Lazy fail-soft computation of correlation + hypothesis seams.</p>
<p></p>
<p></p>
<p></p>
<p>Returns a dict with:</p>
<p></p>
<p>- correlation: from correlate_findings() -- full second-order condensation</p>
<p></p>
<p>- hypothesis_pack: from build_hypothesis_pack() -- operator shortlist + actionability</p>
<p></p>
<p>- branch_value: feed vs public branch value comparison</p>
<p></p>
<p>- signal_path: dominant signal path, next pivot, corroboration health</p>
<p></p>
<p>- feed_verdict: aggregated feed economics verdict across cycles</p>
<p></p>
<p>- public_verdict: aggregated public branch verdict across cycles</p>
<p></p>
<p></p>
<p></p>
<p>All computation is bounded and M1 8GB safe:</p>
<p></p>
<p>- correlation: max 500 findings</p>
<p></p>
<p>- hypothesis: max 200 finding texts</p>
<p></p>
<p>- feed/public verdict accumulation: max 10 entries each</p>
<p></p>
<p>- no model dependency</p>
<p></p>
<p>- fail-soft throughout</p>
</div>
</details>
</li>
<li><code>_build_diagnostic_report</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Build a diagnostic report dict for exporters.</span></li>
<li><code>_run_one_cycle_stable</code> (sprint_scheduler_v1_archived.py)
<details><summary>Stable mode: feed sources run first, then public discovery runs after.</summary>
<div class="doc-comment">
<p>Stable mode: feed sources run first, then public discovery runs after.</p>
<p></p>
<p>CT discovery runs once after the main cycle loop (in __main__.py).</p>
<p></p>
<p></p>
<p></p>
<p>F212-B: Public discovery runs under remaining-time-aware asyncio.timeout.</p>
<p></p>
<p>Branch is skipped if remaining time is at or below the safety floor.</p>
<p></p>
<p></p>
<p></p>
<p># P1.5-fix 2026-06-07: initialize _seed_ctx at function-top so it</p>
<p># is defined for the ENTIRE body of _run_one_cycle_stable, including</p>
<p># the public-outcome assembly at line ~15535 ("seed_context_available"</p>
<p># telemetry). The previous try-block-scoped initialization (14443)</p>
<p># was insufficient because the public-outcome code is OUTSIDE the</p>
<p># try block. When the nonfeed prelude never assigns _seed_ctx</p>
<p># (e.g. no pivot seeds and no next_seeds_ioc), NameError was raised</p>
<p># after the public branch completed.</p>
</div>
</details>
</li>
<li><code>_maybe_dispatch_nonfeed_probe_lanes</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F207M-A: Bounded nonfeed pre-dispatch checkpoint.</summary>
<div class="doc-comment">
<p>Sprint F207M-A: Bounded nonfeed pre-dispatch checkpoint.</p>
<p></p>
<p></p>
<p></p>
<p>Fires before the first active cycle's aggressive branch fan-out can trigger</p>
<p></p>
<p>early windup, ensuring CT (and optionally WAYBACK/PASSIVE_DNS) are attempted</p>
<p></p>
<p>at least once for domain queries before the sprint winds down.</p>
<p></p>
<p></p>
<p></p>
<p>Invariants (strict):</p>
<p></p>
<p>- No stealth, no graph writes, no unbounded network</p>
<p></p>
<p>- max_items &lt;= 5, timeout_s &lt;= 15</p>
<p></p>
<p>- Fail-soft: errors/skips are telemetry only, never crash sprint</p>
<p></p>
<p>- CT only by default for domain queries</p>
<p></p>
<p>- WAYBACK/PASSIVE_DNS only when memory is ok/warn</p>
<p></p>
<p></p>
<p></p>
<p>Windup blocking:</p>
<p></p>
<p>If domain query + CT enabled but not yet attempted, set</p>
<p></p>
<p>windup_blocked_until_nonfeed_attempted = True so the windup gate</p>
<p></p>
<p>delays entry until pre-dispatch completes.</p>
</div>
</details>
</li>
<li><code>run</code> (acquisition.py)</li>
<li><code>_run_public_discovery_in_cycle</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_ensure_mandatory_nonfeed_before_return</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F207T-A: Ensure mandatory nonfeed lanes have terminal state before</summary>
<div class="doc-comment">
<p>Sprint F207T-A: Ensure mandatory nonfeed lanes have terminal state before</p>
<p></p>
<p>the scheduler can return a meaningful result for a domain query.</p>
<p></p>
<p></p>
<p></p>
<p>This is the return-path analog of the pre-windup barrier -- it prevents</p>
<p></p>
<p>the scheduler from returning ACTIVE-phase results when PUBLIC/CT have</p>
<p></p>
<p>not yet been attempted (even if the windup guard was never reached).</p>
<p></p>
<p></p>
<p></p>
<p>Rules:</p>
<p></p>
<p>- domain query + ok/warn memory: both PUBLIC and CT must have terminal state</p>
<p></p>
<p>- domain query + critical/emergency: may skip with explicit reason recorded</p>
<p></p>
<p>- non-domain: only PUBLIC required (CT skips with no_domain)</p>
<p></p>
<p>- Feed-only result: may return if domain query but PUBLIC+CT already terminal</p>
<p></p>
<p></p>
<p></p>
<p>Semantics:</p>
<p></p>
<p>- Returns True if the scheduler MAY return (all required lanes terminal)</p>
<p></p>
<p>- Returns False if return must be DELAYED (required lanes not terminal)</p>
<p></p>
<p>- On False: sets return_guard telemetry and continues loop if possible</p>
<p></p>
<p></p>
<p></p>
<p>Args:</p>
<p></p>
<p>query: Sprint query</p>
<p></p>
<p>duckdb_store: DuckDB store (may be None)</p>
<p></p>
<p>reason: Human-readable reason for the return check (e.g. "stop_requested",</p>
<p></p>
<p>"max_cycles", "stop_on_first_accepted", "post_sleep_windup")</p>
<p></p>
<p></p>
<p></p>
<p>Returns:</p>
<p></p>
<p>True if return is allowed, False if blocked</p>
</div>
</details>
</li>
<li><code>_initialize_sprint_run</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>consume_shadow_pre_decision</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint 8VM: Read-only shadow pre-decision consumer.</summary>
<div class="doc-comment">
<p>Sprint 8VM: Read-only shadow pre-decision consumer.</p>
<p></p>
<p></p>
<p></p>
<p>Collects shadow inputs from current scheduler state,</p>
<p></p>
<p>runs parity check and pre-decision composition,</p>
<p></p>
<p>and returns PreDecisionSummary.</p>
<p></p>
<p></p>
<p></p>
<p>Caching: stores result in _shadow_pd_summary to avoid recomputation.</p>
<p></p>
<p>Cache is cleared in _reset_result().</p>
<p></p>
<p></p>
<p></p>
<p>THIS IS DIAGNOSTIC ONLY -- all hard boundaries enforced:</p>
<p></p>
<p>- Does NOT execute any tools (no execute_with_limits calls)</p>
<p></p>
<p>- Does NOT activate any providers</p>
<p></p>
<p>- Does NOT write to any ledgers as runtime truth</p>
<p></p>
<p>- Does NOT modify scheduler mutable state</p>
<p></p>
<p>- Does NOT create new scheduler framework</p>
<p></p>
<p>- Does NOT dispatch or enqueue work</p>
<p></p>
<p>- Returns PreDecisionSummary artifact, NOT a truth store</p>
<p></p>
<p></p>
<p></p>
<p>Injection point: called from _build_diagnostic_report() at export time.</p>
<p></p>
<p>The method is also available for ad-hoc calls during sprint for</p>
<p></p>
<p>diagnostic purposes only.</p>
<p></p>
<p></p>
<p></p>
<p>Returns None if shadow mode is not active.</p>
</div>
</details>
</li>
<li><code>_finalize_result_truth</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F208I-B: Finalize SprintSchedulerResult before run() returns.</summary>
<div class="doc-comment">
<p>Sprint F208I-B: Finalize SprintSchedulerResult before run() returns.</p>
<p></p>
<p></p>
<p></p>
<p>Computes terminality from acquisition strategy and records scheduler exit</p>
<p></p>
<p>path. Called once before every return from run() -- both normal completion</p>
<p></p>
<p>and all early exit paths (stop_requested, abort, windup_barrier, etc.).</p>
<p></p>
<p></p>
<p></p>
<p>Invariants (GHOST_INVARIANTS):</p>
<p></p>
<p>- No network I/O</p>
<p></p>
<p>- No model/MLX load</p>
<p></p>
<p>- No browser launch</p>
<p></p>
<p>- No blocking ops</p>
<p></p>
<p>- Fail-safe: terminality errors don't prevent return</p>
</div>
</details>
</li>
<li><code>_run_ct_log_discovery_in_cycle</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F193A: Run CT log canonical discovery in the current cycle.</summary>
<div class="doc-comment">
<p>Sprint F193A: Run CT log canonical discovery in the current cycle.</p>
<p></p>
<p></p>
<p></p>
<p>Extracts domain from query, pivots via CTLogClient, converts results</p>
<p></p>
<p>to CanonicalFinding and ingests into DuckDB store.</p>
<p></p>
<p></p>
<p></p>
<p>Fail-soft: errors are accumulated but never raise or abort the sprint.</p>
</div>
</details>
</li>
<li><code>aclose</code> (sprint_scheduler_v1_archived.py)
<details><summary>F285: Canonical async cleanup — call on SIGINT / windup / completion.</summary>
<div class="doc-comment">
<p>F285: Canonical async cleanup — call on SIGINT / windup / completion.</p>
<p></p>
<p>Args:</p>
<p>timeout_s: max seconds for each cleanup phase (default 10.0).</p>
<p>Individual phases (DuckDB writer, LMDB, Hermes, transports)</p>
<p>have their own bounded timeouts (5s / 5s / 5s).</p>
<p></p>
<p>Addresses M1 8GB resource pressure: Metal cache, LMDB envs, DuckDB</p>
<p>writer, Hermes engine, transport adapters, and metrics registry are all</p>
<p>explicitly released here rather than relying on GC.</p>
<p></p>
<p>Call sites (priority order):</p>
<p>1. core/__main__.py finally: await scheduler.aclose()</p>
<p>2. Soft-fail path: await scheduler.aclose()</p>
<p>3. Any caller that creates SprintScheduler and needs deterministic cleanup.</p>
<p></p>
<p>Ordering rationale:</p>
<p>- DuckDB writer FIRST (drains pending writes)</p>
<p>- LMDB envs SECOND (flushes write buffers)</p>
<p>- Hermes / Metal THIRD (releases GPU memory on M1)</p>
<p>- Transport adapters LAST (Tor, I2P, Nym, DHT, Gopher)</p>
<p>- Metrics registry FINAL (flushes telemetry)</p>
<p></p>
<p>Fail-safe: every step is wrapped in try/except so one failure never</p>
<p>prevents subsequent steps from running.</p>
</div>
</details>
</li>
<li><code>_run_ct_to_passivedns_active_pivot</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>run_advisory_runner</code> (sidecar_orchestrator.py)
<details><summary>F206D + ISSUE #3: Run all teardown advisory steps via SprintAdvisoryRunner.</summary>
<div class="doc-comment">
<p>F206D + ISSUE #3: Run all teardown advisory steps via SprintAdvisoryRunner.</p>
<p></p>
<p>ISSUE #3 FIX: All 4 branches now run in PARALLEL via outer TaskGroup:</p>
<p>- Branch A: SprintAdvisoryRunner (4 core advisories)</p>
<p>- Branch B: CT → PassiveDNS pivot advisory</p>
<p>- Branch C: BGP/Wayback/CommonCrawl sidecars (TaskGroup)</p>
<p>- Branch D: IPFS/Onion/I2P/banner/DHT/Gopher/stego/TI sidecars (TaskGroup)</p>
<p>- Branch E: Plugin sidecars (TaskGroup)</p>
<p></p>
<p>Each branch's inner _run_bounded_sidecar calls share ONE global semaphore</p>
<p>(_ADVISORY_SIDECAR_SEMAPHORE_LIMIT=8). This replaces the prior sequential</p>
<p>execution that ran Steps 1→2→(3-4)→(5-7)→(plugin) in wall-time.</p>
<p></p>
<p>Expected speedup: 5-7× faster teardown (30-90s → 5-15s at full flag-on load).</p>
<p></p>
<p>Canonical teardown entry point. Each step is fail-soft;</p>
<p>CancelledError propagates to caller.</p>
</div>
</details>
</li>
<li><code>_ingest_feed_public_candidates_to_ledger</code> (sprint_scheduler_v1_archived.py)
<details><summary>F214: Bridge feed and PUBLIC findings into nonfeed candidate ledger.</summary>
<div class="doc-comment">
<p>F214: Bridge feed and PUBLIC findings into nonfeed candidate ledger.</p>
<p></p>
<p></p>
<p></p>
<p>Extracts domain candidates from feed/public lane outcomes and records them</p>
<p></p>
<p>in the ledger for downstream nonfeed lane planning (DOH, CT, Wayback, passiveDNS).</p>
<p></p>
<p></p>
<p></p>
<p>Flow:</p>
<p></p>
<p>1. Extract domain candidates from _lane_outcomes (FEED/PUBLIC lanes)</p>
<p></p>
<p>2. Apply source_host filtering (deprioritize domains that appear only</p>
<p></p>
<p>in source URL hostname, not in content body)</p>
<p></p>
<p>3. Rank candidates by confidence and seen_count</p>
<p></p>
<p>4. Record via add_feed_candidate() for FEED family</p>
<p></p>
<p>5. Compute lane eligibility from candidates</p>
<p></p>
<p></p>
<p></p>
<p>Bounding:</p>
<p></p>
<p>- MAX_DOMAIN_CANDIDATES_FOR_LANES (10) max candidates processed</p>
<p></p>
<p>- MAX_FEED_CANDIDATES (10) per source URL</p>
<p></p>
<p>- fail-soft throughout -- ledger errors never crash sprint</p>
<p></p>
<p></p>
<p></p>
<p>Lane eligibility telemetry:</p>
<p></p>
<p>- Stored in result.nonfeed_lane_eligibility after computation</p>
</div>
</details>
</li>
<li><code>run_target_memory_update</code> (sidecar_orchestrator.py)</li>
<li><code>_accumulate_lane_findings</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F207J-A: Accumulate accepted lane findings into scheduler truth.</summary>
<div class="doc-comment">
<p>Sprint F207J-A: Accumulate accepted lane findings into scheduler truth.</p>
<p></p>
<p>[F207K-A] Extended with bridge rejection tracking.</p>
<p></p>
<p></p>
<p></p>
<p>Populates:</p>
<p></p>
<p>- _result.lane_*_accepted_findings counters</p>
<p></p>
<p>- _lane_verdicts accumulator (for feed_verdict analog per lane)</p>
<p></p>
<p>- _all_findings (bounded at 500, same cap as feed findings)</p>
<p></p>
<p>- _lane_rejections (source_family, rejection_reason, rejected_count, samples)</p>
<p></p>
<p></p>
<p></p>
<p>Also updates source_family_outcomes in the diagnostic report.</p>
<p></p>
<p></p>
<p></p>
<p>Args:</p>
<p></p>
<p>outcomes: Tuple of AcquisitionLaneOutcome from run_enabled_acquisition_lanes.</p>
<p></p>
<p>query: Sprint query string (used for _all_findings entry).</p>
</div>
</details>
</li>
<li><code>_run_ct_to_passivedns_pivot_advisory</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint R5: CT accepted domains -&gt; PassiveDNS one-hop pivot.</summary>
<div class="doc-comment">
<p>Sprint R5: CT accepted domains -&gt; PassiveDNS one-hop pivot.</p>
<p></p>
<p></p>
<p></p>
<p>One-hop pivot from CT lane accepted findings to PassiveDNS lookup.</p>
<p></p>
<p>No recursive pivoting (pivot depth = 1).</p>
<p></p>
<p>No new queue framework.</p>
<p></p>
<p>No stealth/browser.</p>
<p></p>
<p></p>
<p></p>
<p>Flow:</p>
<p></p>
<p>1. Extract CT accepted domains from acquisition lane outcomes</p>
<p></p>
<p>2. Deduplicate (max 10 via dict.fromkeys)</p>
<p></p>
<p>3. Guard: skip if UMA critical/emergency</p>
<p></p>
<p>4. For each domain: call PassiveDNS (monkeypatched in tests)</p>
<p></p>
<p>5. Record FAMILY_PIVOT in NonfeedCandidateLedger</p>
<p></p>
<p>6. Record source_family_outcomes pivot_source=ct</p>
<p></p>
<p></p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p></p>
<p>- gather(return_exceptions=True)</p>
<p></p>
<p>- Manual CancelledError filter + error collection after gather</p>
<p></p>
<p>- CancelledError re-raised</p>
<p></p>
<p>- No MLX model load</p>
<p></p>
<p>- No asyncio.run() in async context</p>
<p></p>
<p>- Bounded: max 10 pivot domains</p>
<p></p>
<p>- Fail-soft: pivot error never crashes sprint</p>
</div>
</details>
</li>
<li><code>_build_shadow_readiness_preview</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint 8VM + 8VQ: Build a machine-readable shadow readiness preview dict.</summary>
<div class="doc-comment">
<p>Sprint 8VM + 8VQ: Build a machine-readable shadow readiness preview dict.</p>
<p></p>
<p></p>
<p></p>
<p>Called from _build_diagnostic_report() when shadow mode is active.</p>
<p></p>
<p>This is a READ-ONLY summary extracted from PreDecisionSummary</p>
<p></p>
<p>for diagnostic/logging purposes -- NOT a truth store.</p>
</div>
</details>
</li>
<li><code>_run_i2p_discovery_sidecar</code> (sprint_scheduler_v1_archived.py)
<details><summary>I2P discovery: crawl .i2p addresses found in sprint IOCs.</summary>
<div class="doc-comment">
<p>I2P discovery: crawl .i2p addresses found in sprint IOCs.</p>
<p></p>
<p></p>
<p></p>
<p>Gate: HLEDAC_ENABLE_I2P=1 AND I2PTransport.is_running().</p>
<p></p>
<p>Memory pressure &lt; 0.70. Fail-soft throughout -- never crashes sprint.</p>
<p></p>
<p></p>
<p></p>
<p>Sidecar chain position: AFTER _run_onion_discovery_sidecar() if it</p>
<p></p>
<p>exists, otherwise after CT log discovery.</p>
<p></p>
<p></p>
<p></p>
<p>M1 8GB constraints:</p>
<p></p>
<p>- max 5 .i2p addresses per sprint</p>
<p></p>
<p>- 45s per fetch timeout</p>
<p></p>
<p>- 120s total sidecar budget</p>
</div>
</details>
</li>
<li><code>_run_bgp_advisory_sidecar</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F234: BGP IP-to-Org attribution advisory.</summary>
<div class="doc-comment">
<p>Sprint F234: BGP IP-to-Org attribution advisory.</p>
<p></p>
<p></p>
<p></p>
<p>Advisory-only sidecar -- runs after main sprint to enrich accepted</p>
<p></p>
<p>findings with BGP/ASN intelligence. Fail-soft throughout: errors</p>
<p></p>
<p>never crash the sprint.</p>
<p></p>
<p></p>
<p></p>
<p>Flow:</p>
<p></p>
<p>1. Extract domain/IP candidates from acquisition lane outcomes</p>
<p></p>
<p>2. Query BGPView.io for ASN, org, prefix data</p>
<p></p>
<p>3. Convert results to CanonicalFinding via BGPAdapter</p>
<p></p>
<p>4. Record as source_family="bgp_advisory" in source_family_outcomes</p>
</div>
</details>
</li>
<li><code>_collect_ct_terminal_outcome</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F208L-A: Collect canonical CT terminal outcome from all CT surfaces.</summary>
<div class="doc-comment">
<p>Sprint F208L-A: Collect canonical CT terminal outcome from all CT surfaces.</p>
<p></p>
<p></p>
<p></p>
<p>This is the ONE source of truth for CT terminality in _finalize_result_truth.</p>
<p></p>
<p>It inspects all canonical CT surfaces and returns a complete outcome dict</p>
<p></p>
<p>with lane, family, attempted, terminal_state, raw_count, accepted_count,</p>
<p></p>
<p>error, timeout, skipped fields.</p>
<p></p>
<p></p>
<p></p>
<p>Returns None when CT was never attempted (not even attempted=True with zero</p>
<p></p>
<p>raw results) -- allowing terminality_report to mark CT as missing.</p>
<p></p>
<p></p>
<p></p>
<p>Terminal state rules:</p>
<p></p>
<p>- error not None  -&gt; terminal_state="error"</p>
<p></p>
<p>- timeout=True    -&gt; terminal_state="timeout"</p>
<p></p>
<p>- skipped=True    -&gt; terminal_state="skipped"</p>
<p></p>
<p>- raw_count &gt; 0 and accepted_count == 0 -&gt; terminal_state="success_empty"</p>
<p></p>
<p>- raw_count == 0 and attempted=True and no error -&gt; terminal_state="empty"</p>
<p></p>
<p>- attempted=True (default terminal) -&gt; terminal_state="success"</p>
</div>
</details>
</li>
<li><code>_ensure_pre_windup_lane_terminal_states</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_run_nonfeed_prelude_gather</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_get_windup_scorecard</code> (sprint_scheduler_v1_archived.py)
<details><summary>F206E: Extract read-only windup scorecard fields from active pipeline data.</summary>
<div class="doc-comment">
<p>F206E: Extract read-only windup scorecard fields from active pipeline data.</p>
<p></p>
<p></p>
<p></p>
<p>Reads bounded diagnostic fields from windup_engine.py scorecard WITHOUT</p>
<p></p>
<p>activating the dormant run_windup() path. No model load, no GNN import.</p>
<p></p>
<p></p>
<p></p>
<p>Safe read-only sources:</p>
<p></p>
<p>- Circuit breaker states (transport.circuit_breaker)</p>
<p></p>
<p>- Phase durations (from result timing fields)</p>
<p></p>
<p>- Graph stats (from graph_service, already via _get_graph_signal)</p>
<p></p>
<p>- Peak RSS (from result.peak_rss_gib or psutil)</p>
<p></p>
<p></p>
<p></p>
<p>Fail-soft: returns empty dict on any error.</p>
<p></p>
<p></p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p></p>
<p>- No asyncio.run() or loop.run_until_complete()</p>
<p></p>
<p>- No model/MLX imports on hot path</p>
<p></p>
<p>- No GNN inference</p>
<p></p>
<p>- Bounded: MAX_WINDUP_SCORECARD_KEYS=32</p>
</div>
</details>
</li>
<li><code>_reset_result</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_run_doh_prelude_lane</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>health_check</code> (sprint_scheduler_v1_archived.py)
<details><summary>F228F: Pre-run health check for critical dependencies.</summary>
<div class="doc-comment">
<p>F228F: Pre-run health check for critical dependencies.</p>
<p>F270-4.3: Cached -- returns same report within same active sprint cycle.</p>
<p>Always returns HealthReport -- NEVER raises.</p>
<p>Timeout handled externally by caller (asyncio.timeout in __main__).</p>
</div>
</details>
</li>
<li><code>_run_onion_discovery_sidecar</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F251: Dark web .onion discovery via Tor.</summary>
<div class="doc-comment">
<p>Sprint F251: Dark web .onion discovery via Tor.</p>
<p></p>
<p></p>
<p></p>
<p>Gate: HLEDAC_ENABLE_TOR=1 AND TorTransport circuit established AND</p>
<p></p>
<p>memory_pressure &lt; 0.70. Fail-soft throughout -- never crashes sprint.</p>
<p></p>
<p></p>
<p></p>
<p>Sidecar chain position: AFTER _run_ct_log_discovery_in_cycle() (CT logs</p>
<p></p>
<p>reveal .onion domains from certificate transparency).</p>
<p></p>
<p></p>
<p></p>
<p>M1 8GB constraints:</p>
<p></p>
<p>- Semaphore(3): max 3 concurrent Tor crawls</p>
<p></p>
<p>- 45s per crawl timeout</p>
<p></p>
<p>- 120s total sidecar budget</p>
<p></p>
<p>- 20 seeds max per sprint</p>
</div>
</details>
</li>
<li><code>_run_epistemic_gap_advisory</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F260: Run EpistemicGapProgram and ContradictionResolverProgram.</summary>
<div class="doc-comment">
<p>Sprint F260: Run EpistemicGapProgram and ContradictionResolverProgram.</p>
<p></p>
<p>Wire point: called after _run_synthesis_sidecar in WINDUP phase.</p>
<p></p>
<p>Gates:</p>
<p>- HLEDAC_ENABLE_LLM=1 (same as synthesis)</p>
<p>- RAM &lt; 5.0GB (tighter than synthesis's 5.5GB)</p>
<p></p>
<p>Part A: EpistemicGapProgram</p>
<p>- Inputs: findings from sprint + known gaps from ResearchSessionMemory</p>
<p>- Output: gaps written to ResearchSessionMemory via record_sprint_outcome()</p>
<p></p>
<p>Part B: ContradictionResolverProgram</p>
<p>- Triggered when DS conflict_mass &gt; 0.3</p>
<p>- Max 5 contradictions per call (M1 constraint)</p>
</div>
</details>
</li>
<li><code>to_dict</code> (shadow_pre_decision.py)</li>
<li><code>_run_target_memory_update</code> (sprint_scheduler_v1_archived.py)
<details><summary>F204D: Update cross-sprint target memory after findings are accepted.</summary>
<div class="doc-comment">
<p>F204D: Update cross-sprint target memory after findings are accepted.</p>
<p></p>
<p></p>
<p></p>
<p>Sidecar runs after findings are accepted and sidecar bus completes.</p>
<p></p>
<p>Extracts entity/exposure/pivot facets from findings and merges into</p>
<p></p>
<p>target memory via duckdb_store.</p>
<p></p>
<p></p>
<p></p>
<p>RAM guard: skip if RSS &gt; high_water (85% threshold).</p>
<p></p>
<p>Fail-soft: errors never crash the sprint.</p>
<p></p>
<p></p>
<p></p>
<p>Args:</p>
<p></p>
<p>findings: List of CanonicalFinding that were accepted and stored</p>
<p></p>
<p>store: DuckDBShadowStore instance for async_upsert_target_memory</p>
<p></p>
<p>query: Original sprint query (used as target context)</p>
</div>
</details>
</li>
<li><code>_run_dht_sidecar</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F214Q: DHT torrent discovery via BitTorrent DHT network.</summary>
<div class="doc-comment">
<p>Sprint F214Q: DHT torrent discovery via BitTorrent DHT network.</p>
<p></p>
<p></p>
<p></p>
<p>INVARIANT: DHT queries NEVER go over Tor -- clearnet UDP only.</p>
<p></p>
<p>Gate: HLEDAC_ENABLE_DHT=1, max_results=5, timeout=60s.</p>
<p></p>
<p>Fail-soft: DHT errors logged, never crash sprint.</p>
</div>
</details>
</li>
<li><code>_compute_early_exit_class</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F215D: Compute canonical early exit classification.</summary>
<div class="doc-comment">
<p>Sprint F215D: Compute canonical early exit classification.</p>
<p></p>
<p></p>
<p></p>
<p>Called in _finalize_result_truth after timing fields are populated.</p>
<p></p>
<p>Returns (early_exit_class, early_exit_reason).</p>
<p></p>
<p></p>
<p></p>
<p>Invariants (GHOST_INVARIANTS):</p>
<p></p>
<p>- No network I/O, no model load, no browser launch</p>
<p></p>
<p>- Fail-safe: returns (COMPLETED_FULL_DURATION, "") on any error</p>
</div>
</details>
</li>
<li><code>_run_dark_surface_pivot_advisory</code> (sprint_scheduler_v1_archived.py)
<details><summary>F214K: Generate and enqueue dark surface pivot queries (onion/IPFS/DHT/I2P)</summary>
<div class="doc-comment">
<p>F214K: Generate and enqueue dark surface pivot queries (onion/IPFS/DHT/I2P)</p>
<p></p>
<p>post-sprint if accepted_findings &gt;= 5 and HLEDAC_ENABLE_DARK_PIVOTS=1.</p>
<p></p>
<p></p>
<p></p>
<p>GHOST_INVARIANTS: fail-soft, named except, transport availability verified</p>
<p></p>
<p>before any query is enqueued.</p>
</div>
</details>
</li>
<li><code>_gate_then_ingest_and_accumulate</code> (sprint_scheduler_v1_archived.py)
<details><summary>F266: PII-gated canonical write + graph accumulation.</summary>
<div class="doc-comment">
<p>F266: PII-gated canonical write + graph accumulation.</p>
<p></p>
<p>Combines _gate_then_ingest (DuckDB write) with _accumulate_findings_to_graph</p>
<p>(graph upsert) in a single await chain. Fail-soft: graph errors never</p>
<p>prevent the DuckDB write from completing.</p>
<p></p>
<p></p>
<p>This is the canonical call for ALL nonfeed lanes (wayback/pdns/doh)</p>
<p>and sidecars that need graph wiring.</p>
<p></p>
<p></p>
<p>P0-5: Evidence log events for every finding state transition:</p>
<p>- CREATED: when findings list is received</p>
<p>- CANDIDATE: before DuckDB ingest</p>
<p>- ACCEPTED: ingest result shows accepted findings</p>
<p>- REJECTED: ingest result shows rejected findings</p>
<p></p>
<p>Args:</p>
<p>store: duckdb_store (or any object with async_ingest_findings_batch).</p>
<p>findings: list of CanonicalFinding.</p>
<p>sprint_id: Sprint identifier for graph source field.</p>
<p></p>
<p>Returns:</p>
<p>Whatever async_ingest_findings_batch returns.</p>
</div>
</details>
</li>
<li><code>_run_enhanced_research</code> (sprint_scheduler_v1_archived.py)
<details><summary>F11: Run enhanced/deep research advisory post-sprint.</summary>
<div class="doc-comment">
<p>F11: Run enhanced/deep research advisory post-sprint.</p>
<p></p>
<p></p>
<p></p>
<p>GHOST_INVARIANTS: fail-soft, named except, CancelledError propagated.</p>
</div>
</details>
</li>
<li><code>_run_feed_dominance_nonfeed_rescue_window</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F220D: Feed Dominance Nonfeed Rescue Window.</summary>
<div class="doc-comment">
<p>Sprint F220D: Feed Dominance Nonfeed Rescue Window.</p>
<p></p>
<p></p>
<p></p>
<p>When feed has been dominant (&gt;=1000 accepted) and nonfeed lanes are all</p>
<p></p>
<p>at zero, this rescue window attempts a final bounded nonfeed rescue before</p>
<p></p>
<p>declaring feed-only early exit.</p>
<p></p>
<p></p>
<p></p>
<p>Bounded:</p>
<p></p>
<p>- Max 60s wall-clock duration</p>
<p></p>
<p>- Fail-soft: returns None on any error, 0.0 if no candidates found</p>
<p></p>
<p>- No new network providers -- uses existing seams (_attempt_public_prewindup_barrier)</p>
<p></p>
<p>- No MLX / browser / stealth</p>
<p></p>
<p></p>
<p></p>
<p>Returns:</p>
<p></p>
<p>Elapsed seconds if rescue ran (even with 0 findings), None if skipped.</p>
</div>
</details>
</li>
<li><code>run_all_sidecars</code> (sidecar_bus.py)
<details><summary>Fan out to all registered sidecar runners for the given batch, in stage order.</summary>
<div class="doc-comment">
<p>Fan out to all registered sidecar runners for the given batch, in stage order.</p>
<p></p>
<p>Stages run sequentially (stage 1 → stage 2 → stage 3). Within each stage,</p>
<p>runners execute concurrently via asyncio.gather(return_exceptions=True).</p>
<p></p>
<p>Returns list of SidecarRunResult (one per runner that was attempted).</p>
<p></p>
<p>Bounds:</p>
<p>- findings capped at MAX_SIDECAR_FINDINGS</p>
<p>- results capped at MAX_SIDECAR_RESULT_RECORDS</p>
<p>- per-runner timeout: SIDECAR_TIMEOUT_S</p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p>- gather(return_exceptions=True) within each stage</p>
<p>- _check_gathered() after each stage's gather</p>
<p>- asyncio.CancelledError re-raised</p>
<p>- fail-soft: stage N failure does not stop stage N+1</p>
</div>
</details>
</li>
<li><code>_ingest_ct_lane_candidates</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint R1B: Ingest CT lane CanonicalFinding candidates via DuckDBShadowStore.</summary>
<div class="doc-comment">
<p>Sprint R1B: Ingest CT lane CanonicalFinding candidates via DuckDBShadowStore.</p>
<p></p>
<p></p>
<p></p>
<p>Bridges the gap between the acquisition lane's ct_results_to_findings() output</p>
<p></p>
<p>(which produces CanonicalFinding dicts in candidate_findings) and the canonical</p>
<p></p>
<p>storage path (async_ingest_findings_batch).</p>
<p></p>
<p></p>
<p></p>
<p>Flow per CT outcome with candidates:</p>
<p></p>
<p>1. Extract candidate_findings from CT AcquisitionLaneOutcome</p>
<p></p>
<p>2. Call duckdb_store.async_ingest_findings_batch(candidates)</p>
<p></p>
<p>3. Record storage results in NonfeedCandidateLedger (stored / quarantine / provider_failed)</p>
<p></p>
<p>4. Update _result.lane_ct_accepted_findings with accepted count</p>
<p></p>
<p></p>
<p></p>
<p>Fail-soft: storage errors never crash the sprint.</p>
<p></p>
<p>CancelledError: re-raised to caller (GHOST_INVARIANTS I6).</p>
<p></p>
<p>M1/UMA: no MLX model load in this path.</p>
<p></p>
<p></p>
<p></p>
<p>Args:</p>
<p></p>
<p>outcomes:   Tuple of AcquisitionLaneOutcome from run_enabled_acquisition_lanes.</p>
<p></p>
<p>duckdb_store: DuckDBShadowStore instance for canonical storage.</p>
</div>
</details>
</li>
<li><code>_maybe_dispatch_nonfeed_probe_lanes</code> (acquisition.py)</li>
<li><code>_run_synthesis_sidecar</code> (acquisition.py)</li>
<li><code>_branch_timeout_s</code> (sprint_scheduler_v1_archived.py)
<details><summary>F212-B: Compute remaining-time-aware timeout for a named branch.</summary>
<div class="doc-comment">
<p>F212-B: Compute remaining-time-aware timeout for a named branch.</p>
<p></p>
<p></p>
<p></p>
<p>Formula: min(config_timeout, remaining_s * 0.5, MAX_BRANCH_TIMEOUT_CAP)</p>
<p></p>
<p></p>
<p></p>
<p>- Prevents a branch from consuming more than 50% of remaining cycle time</p>
<p></p>
<p>- Capped at MAX_BRANCH_TIMEOUT_CAP to bound absolute worst case</p>
<p></p>
<p>- Returns 0 when remaining_s &lt;= MIN_BRANCH_REMAINING_S (safety floor)</p>
<p></p>
<p>F273B: Floor is remaining-time-aware via self._min_branch_remaining_s(remaining_s).</p>
</div>
</details>
</li>
<li><code>_load_hermes_for_sprint</code> (sprint_scheduler_v1_archived.py)
<details><summary>P12: Load Hermes engine at sprint start via ModelManager.</summary>
<div class="doc-comment">
<p>P12: Load Hermes engine at sprint start via ModelManager.</p>
<p>Bounded lifecycle: loaded at BOOT/WARMUP, released at TEARDOWN.</p>
<p>Fail-soft: memory pressure on load skips ToT, does not abort sprint.</p>
<p></p>
<p>M1 8GB invariant: ModelManager enforces bounded admission and RSS guards.</p>
<p></p>
<p>F267: MLX prewarm -- if prewarm active and inter-sprint gap &lt; 60s,</p>
<p>model is still in Metal cache. Skip reload and verify.</p>
<p></p>
<p>ISSUE-121: Serial model loading replaced with parallel prewarm via</p>
<p>asyncio.to_thread() + asyncio.TaskGroup. Hermes load (~5-10s I/O-bound)</p>
<p>now runs in background thread while ModernBERT + URL prefetch also run</p>
<p>in parallel. Expected 4-7s → 1-2s (3-5× speedup).</p>
</div>
</details>
</li>
<li><code>_run_ct_prelude_lane</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Run CT prelude lane. Returns (AcquisitionLaneOutcome, ct_result, ct_telemetry).</span></li>
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
<li><code>_run_synthesis_sidecar</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Sprint F259: Run SynthesisRunner in WINDUP phase.</span></li>
<li><code>_run_ooda_cycle</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Jeden OODA cyklus -- 60s interval.</span></li>
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
<li><code>run_plugin_sidecars</code> (sidecar_orchestrator.py)
<details><summary>F350M-FED: Iterate over SidecarRegistry.get_available() and dispatch</summary>
<div class="doc-comment">
<p>F350M-FED: Iterate over SidecarRegistry.get_available() and dispatch</p>
<p>each registered plugin sidecar in a non-blocking asyncio task.</p>
<p></p>
<p>Args:</p>
<p>ctx: A SidecarContext (or duck-typed equivalent) with</p>
<p>.query, .sprint_id, .findings, .sprint_mode, .memory_pressure.</p>
<p></p>
<p>Behavior:</p>
<p>- Reads the canonical M1 budget from the governor if available</p>
<p>(defaults to 100MB).</p>
<p>- Iterates in priority order (highest first).</p>
<p>- Each sidecar runs in its own task with the supplied ctx.</p>
<p>- Fail-soft: any exception is caught and logged, never raised.</p>
</div>
</details>
</li>
<li><code>_run_steganography_sidecar</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F3FORENSICS: Steganography detection on image findings.</summary>
<div class="doc-comment">
<p>Sprint F3FORENSICS: Steganography detection on image findings.</p>
<p>Gate: HLEDAC_ENABLE_STEGANOGRAPHY=1, max_images=10, max_image_size=50MB.</p>
<p>Only emit findings if overall_suspicious &gt; 0.3.</p>
<p>Fail-soft: errors logged, never crash sprint.</p>
</div>
</details>
</li>
<li><code>run_async</code> (sidecar_protocol_adapters.py) — <span class="doc-comment-inline">Fetch threat intel IoCs matching the query.</span></li>
<li><code>_run_wayback_cdx_deep_sidecar</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F234: WaybackCDX deep search advisory.</summary>
<div class="doc-comment">
<p>Sprint F234: WaybackCDX deep search advisory.</p>
<p></p>
<p></p>
<p></p>
<p>Advisory-only sidecar -- runs after main sprint to discover archived</p>
<p></p>
<p>URLs for accepted domains. Fail-soft throughout.</p>
<p></p>
<p></p>
<p></p>
<p>Flow:</p>
<p></p>
<p>1. Extract domains from acquisition lane outcomes</p>
<p></p>
<p>2. Query Wayback CDX for archived URLs (deep domain discovery)</p>
<p></p>
<p>3. Convert results to CanonicalFinding via WaybackCDXDeepSearch</p>
<p></p>
<p>4. Record as source_family="wayback_cdx_advisory" in source_family_outcomes</p>
</div>
</details>
</li>
<li><code>__getattr__</code> (sprint_scheduler_v1_archived.py)
<details><summary>Delegate attribute access to _lc with lazy normalization.</summary>
<div class="doc-comment">
<p>Delegate attribute access to _lc with lazy normalization.</p>
<p></p>
<p>Raises AttributeError if _lc lacks the resolved attribute.</p>
</div>
</details>
</li>
<li><code>_feed_dominance_should_fetch</code> (sprint_scheduler_v1_archived.py)
<details><summary>F216E+F227D: Determine if a feed source should be fetched given current budget state.</summary>
<div class="doc-comment">
<p>F216E+F227D: Determine if a feed source should be fetched given current budget state.</p>
<p></p>
<p></p>
<p></p>
<p>F227D: Added mission_intent and nonfeed_unresolved to support mission-aware cap.</p>
<p></p>
<p>F230D: Added acquisition_profile for nonfeed_diagnostic profile cap.</p>
<p></p>
<p></p>
<p></p>
<p>Returns (should_fetch, reason):</p>
<p></p>
<p>- (True, "")       -- source should run normally</p>
<p></p>
<p>- (False, reason)  -- source should be skipped due to budget cap</p>
</div>
</details>
</li>
<li><code>_run_one_cycle_aggressive</code> (acquisition.py)</li>
<li><code>_run_federated_research_advisory</code> (sprint_advisory_runner.py)
<details><summary>F350M-FED-P3-FOLLOWUP: Federated research advisory at teardown.</summary>
<div class="doc-comment">
<p>F350M-FED-P3-FOLLOWUP: Federated research advisory at teardown.</p>
<p></p>
<p>The canonical seam for the federated research capability at sprint</p>
<p>teardown. Performs four bounded, fail-soft actions:</p>
<p></p>
<p>1. **Lazy bridge creation** — `scheduler._ensure_federated_bridge()`</p>
<p>returns a long-lived `FederatedBridge` (singleton on scheduler).</p>
<p>Off by default (gated on HLEDAC_ENABLE_FEDERATED=1).</p>
<p>2. **M1 safety** — skip entirely if memory_pressure &gt; 0.85.</p>
<p>3. **Bridge updates** — for each accepted finding in</p>
<p>`scheduler._all_findings`, emit `bridge.update(lane, state, action, reward, next_state)`.</p>
<p>Reward = clamp01(confidence). State = (lane, len(findings)).</p>
<p>Bounded by len(findings) — typically &lt; 100.</p>
<p>4. **LMDB persistence** — call `bridge.persist_if_due()` (debounced,</p>
<p>`asyncio.to_thread`, fail-soft). Honors env-var</p>
<p>`HLEDAC_FEDERATED_LMDB_PATH` for cross-sprint state.</p>
<p></p>
<p>Complements (does NOT replace) the Phase 2 plugin sidecar:</p>
<p>- Plugin sidecar: fire-and-forget, runs FederatedResearchCoordinator,</p>
<p>produces CanonicalFinding objects → SidecarDispatcher.</p>
<p>- This advisory: bounded bridge updates + LMDB persistence +</p>
<p>telemetry → analytics/export.</p>
<p></p>
<p>Side effects (all fail-soft):</p>
<p>- Sets `scheduler._federated_bridge` to the long-lived instance.</p>
<p>- Updates `SprintSchedulerResult.federated_*` telemetry fields</p>
<p>(populated by `sprint_scheduler._apply_federated_outcome`).</p>
<p></p>
<p>CancelledError: re-raised to caller.</p>
<p>All other exceptions: caught, logged at debug, outcome returned.</p>
</div>
</details>
</li>
<li><code>_run_ipfs_discovery_sidecar</code> (sprint_scheduler_v1_archived.py)
<details><summary>F218Z: IPFS CID resolution and content fetch via Tor transport.</summary>
<div class="doc-comment">
<p>F218Z: IPFS CID resolution and content fetch via Tor transport.</p>
<p></p>
<p></p>
<p></p>
<p>Gate: HLEDAC_ENABLE_IPFS=1</p>
<p></p>
<p>Transport: Tor required (self._tor_transport), NEVER clearnet</p>
<p></p>
<p>Bounds: max 20 CIDs, 120s timeout per CID, 10MB max file size</p>
<p></p>
<p>Fail-soft: returns empty list on any error.</p>
<p></p>
<p></p>
<p></p>
<p>Args:</p>
<p></p>
<p>cids: List of IPFS CIDs to fetch. If None, extracts from</p>
<p></p>
<p>pivot findings or DHT results in the current sprint.</p>
<p></p>
<p>query_context: Query string for ipfs_search_as_findings fallback.</p>
</div>
</details>
</li>
<li><code>_run_one_cycle_stable</code> (acquisition.py)</li>
<li><code>_run_graph_rag_context_sidecar</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F224: Graph RAG pre-cycle enrichment.</summary>
<div class="doc-comment">
<p>Sprint F224: Graph RAG pre-cycle enrichment.</p>
<p></p>
<p>Runs BEFORE first cycle to inject previously discovered graph context</p>
<p>into the sprint. Uses multi-hop search over DuckPGQGraph to find</p>
<p>relevant entities/relationships from previous sprints.</p>
<p></p>
<p>Gate: HLEDAC_ENABLE_GRAPH_RAG=1 + RAM check &lt; 5.0GB</p>
<p></p>
<p>Args:</p>
<p>query: Current sprint query</p>
<p>duckdb_store: DuckDB store for persistent state</p>
<p></p>
<p>Returns:</p>
<p>List of CanonicalFinding with "context_seed" source_type</p>
</div>
</details>
</li>
<li><code>run_async</code> (sidecar_protocol_adapters.py) — <span class="doc-comment-inline">Perform WHOIS lookups for domain findings.</span></li>
<li><code>_final_source_family_outcomes_for_terminality</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F210A: Canonical source family outcomes for terminality SSOT.</summary>
<div class="doc-comment">
<p>Sprint F210A: Canonical source family outcomes for terminality SSOT.</p>
<p></p>
<p></p>
<p></p>
<p>This mirrors the EXACT same logic used in _build_diagnostic_report to build</p>
<p></p>
<p>source_family_outcomes (lines ~6219-6244), ensuring terminality_report is</p>
<p></p>
<p>ALWAYS computed from the same canonical outcomes that go into the report.</p>
<p></p>
<p></p>
<p></p>
<p>This fixes the stale terminality bug where:</p>
<p></p>
<p>- _finalize_result_truth() is called before all nonfeed lanes complete</p>
<p></p>
<p>- terminality was computed from a snapshot with CT/PUBLIC not yet attempted</p>
<p></p>
<p>- source_family_outcomes reflected final state but terminality was stale</p>
<p></p>
<p></p>
<p></p>
<p>Returns:</p>
<p></p>
<p>Tuple of outcome dicts for terminality computation -- same format as</p>
<p></p>
<p>observed_outcomes passed to terminality_report().</p>
</div>
</details>
</li>
<li><code>_run_digital_ghost_sidecar</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F3FORENSICS: Digital ghost detection on file findings.</summary>
<div class="doc-comment">
<p>Sprint F3FORENSICS: Digital ghost detection on file findings.</p>
<p>Gate: HLEDAC_ENABLE_DIGITAL_GHOST=1, max_files=10, max_file_size=50MB.</p>
<p>Fail-soft: errors logged, never crash sprint.</p>
</div>
</details>
</li>
<li><code>_get_pivot_graph_stats_for_planning</code> (sprint_scheduler_v1_archived.py)
<details><summary>F238D: Build structured graph_stats dict for PivotPlanner scoring.</summary>
<div class="doc-comment">
<p>F238D: Build structured graph_stats dict for PivotPlanner scoring.</p>
<p></p>
<p></p>
<p></p>
<p>Called during nonfeed prelude (before advisory runner) to populate</p>
<p></p>
<p>graph_stats with {nodes, edges, domains, connected_iocs, node_degrees}</p>
<p></p>
<p>so that _score_pivot_domain and _score_pivot_graph can apply degree penalties</p>
<p></p>
<p>and novelty checks.</p>
<p></p>
<p></p>
<p></p>
<p>Returns empty dict (fail-soft) if graph unavailable or query fails.</p>
<p></p>
<p>No network, no model, no DuckDB heavy scans -- only bounded in-memory</p>
<p></p>
<p>aggregation over already-persisted graph data.</p>
<p></p>
<p></p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p></p>
<p>- No asyncio.run() or loop.run_until_complete()</p>
<p></p>
<p>- No model/MLX imports</p>
<p></p>
<p>- No network calls</p>
<p></p>
<p>- Bounded: MAX_PIVOT_GRAPH_STATS_NODES=500</p>
</div>
</details>
</li>
<li><code>score_with_hermes_output</code> (pivot_planner.py)
<details><summary>Sprint F256 + Issue #17: Single-pass Hermes+heuristic pivot scoring.</summary>
<div class="doc-comment">
<p>Sprint F256 + Issue #17: Single-pass Hermes+heuristic pivot scoring.</p>
<p></p>
<p>OPTIMIZATION: Previously iterated findings TWICE (Hermes path + heuristic</p>
<p>path). Now builds a Hermes pivot map first, then iterates findings ONCE,</p>
<p>boosting heuristic pivots with Hermes scores during the single pass.</p>
<p></p>
<p>When hermes_outputs is non-empty:</p>
<p>- Primary: extract IOCs/entities from HermesInferenceOutput.key_iocs</p>
<p>and key_entities to generate pivots with boosted expected_value</p>
<p>- Secondary: use HermesInferenceOutput.pivot_suggestions directly</p>
<p>- Fallback: if hermes_outputs empty, fall back to existing heuristic path</p>
<p></p>
<p>When hermes_outputs is empty:</p>
<p>- Fall back to plan_pivots() heuristic path</p>
<p></p>
<p>Bounds:</p>
<p>- MAX_PIVOTS=20 (unchanged)</p>
<p>- hermes_outputs capped at MAX_INFERENCE_ITEMS=50</p>
<p>- Each HermesInferenceOutput key_iocs/key_entities capped at 20 items each</p>
<p>- Each HermesInferenceOutput pivot_suggestions capped at 10 items each</p>
<p></p>
<p>Args:</p>
<p>findings: list of CanonicalFinding objects</p>
<p>hermes_outputs: list of HermesInferenceOutput from Hermes3Engine</p>
<p>max_pivots: maximum number of pivots to return (default MAX_PIVOTS=20)</p>
<p>graph_stats: optional graph statistics for scoring</p>
<p>mission_intent: optional mission intent string for scoring</p>
<p></p>
<p>Returns:</p>
<p>list[Pivot] sorted by priority (highest first)</p>
<p>Always returns at least [] (fail-safe)</p>
</div>
</details>
</li>
<li><code>_initialize_sprint_run</code> (scheduler.py)
<details><summary>Initialize DuckDB, lifecycle, governor, Hermes prewarm.</summary>
<div class="doc-comment">
<p>Initialize DuckDB, lifecycle, governor, Hermes prewarm.</p>
<p></p>
<p>Corresponds to v1's _initialize_sprint_run (lines ~6600-7168).</p>
</div>
</details>
</li>
<li><code>_process_result</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Accumulate result stats and dedup.</span></li>
<li><code>_evaluate_locked</code> (resource_governor.py)
<details><summary>Build GovernorDecision while caller holds self._lock.</summary>
<div class="doc-comment">
<p>Build GovernorDecision while caller holds self._lock.</p>
<p></p>
<p>Called by evaluate() (holds lock) and evaluate_adaptive() (holds lock).</p>
<p>Updates self._uma_state, self._model_loaded, counters on self.</p>
</div>
</details>
</li>
<li><code>_run_pivot_planner_advisory</code> (sprint_advisory_runner.py)
<details><summary>F202G: Run pivot planner on accepted findings for advisory ordering.</summary>
<div class="doc-comment">
<p>F202G: Run pivot planner on accepted findings for advisory ordering.</p>
<p></p>
<p>Planner generates pivot suggestions; scheduler may use them as</p>
<p>ordering input for future sprints. Advisory only.</p>
<p></p>
<p>Fail-soft: errors never crash the runner.</p>
</div>
</details>
</li>
<li><code>_run_banner_grab_sidecar</code> (sprint_scheduler_v1_archived.py)
<details><summary>F214Q: Banner grab -- service fingerprinting via TCP probe.</summary>
<div class="doc-comment">
<p>F214Q: Banner grab -- service fingerprinting via TCP probe.</p>
<p></p>
<p></p>
<p></p>
<p>Gate: HLEDAC_ENABLE_BANNER_GRAB=1 + memory guard.</p>
<p></p>
<p>Seeds: IP/domain z findings.</p>
<p></p>
<p>INVARIANT: Banner grab = aktivní TCP probe = CLEARNET ONLY (ne přes Tor).</p>
<p></p>
<p>Timeout: 10s per port, max 5 portů.</p>
</div>
</details>
</li>
<li><code>_run_bgp_enrichment_sidecar</code> (sprint_scheduler_v1_archived.py)
<details><summary>F214Q: BGP enrichment -- AS path analysis for IP/ASN seeds.</summary>
<div class="doc-comment">
<p>F214Q: BGP enrichment -- AS path analysis for IP/ASN seeds.</p>
<p></p>
<p></p>
<p></p>
<p>Gate: HLEDAC_ENABLE_BGP=1 + M1 memory guard (skip if critical/emergency).</p>
<p></p>
<p>Seeds: IP/ASN z aktuálních findings (IOC_TYPES: "ip").</p>
<p></p>
<p>Max 3 IP/ASN per sprint, 30s timeout.</p>
<p></p>
<p>Semaphore(1) -- BGP queries jsou heavyweight.</p>
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
<li><code>_run_privacy_gate_async</code> (sprint_scheduler_v1_archived.py)
<details><summary>Pre-storage PII anonymization gate.</summary>
<div class="doc-comment">
<p>Pre-storage PII anonymization gate.</p>
<p></p>
<p>Runs BEFORE async_ingest_findings_batch() for ALL storage paths.</p>
<p>Returns (anonymized_findings, pii_count).</p>
<p></p>
<p>Scopes: content, raw_content, payload_text, title, summary.</p>
<p>Fail-soft: never raises -- findings pass through unmodified on any error.</p>
<p></p>
<p>INVARIANT: Never raises. Always returns input findings on error.</p>
</div>
</details>
</li>
<li><code>_adapt_source_weights_from_feedback</code> (sprint_scheduler_v1_archived.py)
<details><summary>F199A: Adapt _source_weights from per-source quality feedback collected during the sprint.</summary>
<div class="doc-comment">
<p>F199A: Adapt _source_weights from per-source quality feedback collected during the sprint.</p>
<p></p>
<p></p>
<p></p>
<p>Called at teardown (in run() after cycles complete). Updates each feed_url's weight</p>
<p></p>
<p>based on accepted/total ratio signal collected via _process_result().</p>
<p></p>
<p></p>
<p></p>
<p>Adaptation rule (B.6 bounds ±20% per sprint -&gt; clamp to [0.3, 2.5]):</p>
<p></p>
<p>- accepted/total &gt;= 0.7 -&gt; reward: +10%</p>
<p></p>
<p>- accepted/total &gt;= 0.4 -&gt; reward: +5%</p>
<p></p>
<p>- accepted/total &gt;= 0.15 -&gt; reward: 0 (neutral)</p>
<p></p>
<p>- accepted/total &lt; 0.15 -&gt; penalty: -5%</p>
<p></p>
<p>- no signal (total=0) -&gt; no change</p>
<p></p>
<p></p>
<p></p>
<p>Signal is per-feed_url (feed_url as key), not per-source_type.</p>
<p></p>
<p>For scoring, feed_url maps to source_type via _config.tier_of(feed_url).name.</p>
</div>
</details>
</li>
<li><code>run_async</code> (sidecar_protocol_adapters.py) — <span class="doc-comment-inline">Embed query + findings into LanceDB for cross-sprint persistence.</span></li>
<li><code>deduplicate_and_rank_findings</code> (sprint_scheduler_v1_archived.py)
<details><summary>DuckDB-powered dedup + ranking over Parquet files (F5.4).</summary>
<div class="doc-comment">
<p>DuckDB-powered dedup + ranking over Parquet files (F5.4).</p>
<p></p>
<p>Strategy:</p>
<p>1. DuckDB SQL aggregation via read_parquet(glob) — zero-copy Arrow,</p>
<p>M1 RAM-safe streaming, no polars dependency for I/O.</p>
<p>2. COPY TO Parquet — DuckDB writes directly, no intermediate DataFrame.</p>
<p>3. Polars only for in-memory ranking when DuckDB COPY is unavailable.</p>
<p></p>
<p>Fallback chain: DuckDB COPY → polars LazyFrame streaming collect →</p>
<p>pyarrow fallback. All paths return a valid parquet path.</p>
</div>
</details>
</li>
<li><code>_get_circuit_breaker_summary</code> (sprint_scheduler_v1_archived.py)
<details><summary>F206I: Build a bounded circuit breaker state summary for the diagnostic report.</summary>
<div class="doc-comment">
<p>F206I: Build a bounded circuit breaker state summary for the diagnostic report.</p>
<p></p>
<p></p>
<p></p>
<p>Reads the shared domain circuit breaker registry (get_all_breaker_snapshots)</p>
<p></p>
<p>and returns a compact summary. Non-persisting, in-memory only.</p>
<p></p>
<p></p>
<p></p>
<p>Bounds:</p>
<p></p>
<p>- MAX_TRACKED_DOMAINS=500 (from circuit_breaker module)</p>
<p></p>
<p>- MAX_BREAKER_DOMAINS=500 (local alias)</p>
<p></p>
<p>- Each snapshot is a small dict: domain, state, failure_count, retry_after_s</p>
<p></p>
<p></p>
<p></p>
<p>Fail-soft: returns empty dict on any error.</p>
<p></p>
<p></p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p></p>
<p>- No asyncio.gather / _check_gathered (sync method)</p>
<p></p>
<p>- No asyncio.run() or loop.run_until_complete()</p>
<p></p>
<p>- No canonical write path (read-only)</p>
<p></p>
<p>- Circuit breaker itself does not persist</p>
</div>
</details>
</li>
<li><code>_prewarm_hermes_for_sprint</code> (sprint_scheduler_v1_archived.py)
<details><summary>P12: Mode-aware Hermes prewarm policy.</summary>
<div class="doc-comment">
<p>P12: Mode-aware Hermes prewarm policy.</p>
<p></p>
<p></p>
<p></p>
<p>Aggressive mode: prewarm blocks until Hermes is loaded, unless RSS &gt; 4GB</p>
<p></p>
<p>(hard headroom rule -- skip fail-soft, ToT is skipped for that run).</p>
<p></p>
<p></p>
<p></p>
<p>Stable mode: current safe behavior via ModelManager memory guards</p>
<p></p>
<p>(soft pressure clear + hard admission gate -- no RSS 4GB pre-check).</p>
<p></p>
<p></p>
<p></p>
<p>Bounded lifecycle: loaded once at BOOT/WARMUP, released at TEARDOWN.</p>
<p></p>
<p>Fail-soft: memory pressure on load skips ToT, does not abort sprint.</p>
<p></p>
<p></p>
<p></p>
<p>F203J: Quantization budget respected via QuantizationSelector advisory</p>
<p></p>
<p>in ModelManager._load_model_async. Budget is logged here for visibility.</p>
</div>
</details>
</li>
<li><code>_maybe_flush_to_parquet</code> (sprint_scheduler_v1_archived.py)
<details><summary>Flush Arrow batch to Parquet when N or S threshold is hit.</summary>
<div class="doc-comment">
<p>Flush Arrow batch to Parquet when N or S threshold is hit.</p>
<p></p>
<p></p>
<p></p>
<p>F214OPT-D: On flush failure, batch is truncated to HARD_CAP to prevent</p>
<p></p>
<p>unbounded growth. Failed entries are dropped (oldest first) and counted.</p>
</div>
</details>
</li>
<li><code>_run_public_prelude_lane</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Run PUBLIC prelude lane. Returns result dict, never raises.</span></li>
<li><code>_record_quality_rejections_from_store</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F216G: Read quality rejection ledger from duckdb_store and</summary>
<div class="doc-comment">
<p>Sprint F216G: Read quality rejection ledger from duckdb_store and</p>
<p></p>
<p>compute summary dictionaries.</p>
<p></p>
<p></p>
<p></p>
<p>Called after run_enabled_acquisition_lanes() completes (both advisory</p>
<p></p>
<p>and aggressive cycles) so that all lane ingest quality gate rejections</p>
<p></p>
<p>are captured in SprintSchedulerResult.</p>
<p></p>
<p></p>
<p></p>
<p>Also called from _maybe_dispatch_nonfeed_probe_lanes after its</p>
<p></p>
<p>direct async_ingest_findings_batch call.</p>
<p></p>
<p></p>
<p></p>
<p>Invariants (strict):</p>
<p></p>
<p>- No threshold changes</p>
<p></p>
<p>- No dedup behavior changes</p>
<p></p>
<p>- No destructive DB schema migration</p>
<p></p>
<p>- No benchmark-owned scoring change</p>
</div>
</details>
</li>
<li><code>_run_wayback_prelude_lane</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_duckdb_background_writer</code> (sprint_scheduler_v1_archived.py)
<details><summary>F285: Background writer that drains _duckdb_write_queue sequentially.</summary>
<div class="doc-comment">
<p>F285: Background writer that drains _duckdb_write_queue sequentially.</p>
<p></p>
<p>Enables overlapping DuckDB writes with the next cycle acquisition.</p>
<p>Sequential draining preserves WAL ordering guarantees.</p>
<p>Fail-soft: exceptions are logged but do not propagate.</p>
<p></p>
<p>Event-driven wakeup: uses asyncio.Event instead of 5s timeout polling.</p>
<p>Notifies writer immediately when items are enqueued. Falls back to</p>
<p>30s heartbeat to prevent starvation if notify is ever missed.</p>
<p></p>
<p>BUG-7 FIX: Drain-first shutdown. On shutdown signal, drain all queued</p>
<p>items BEFORE exiting. This closes the race where findings arriving</p>
<p>between shutdown.set() and the next queue.get() were silently dropped.</p>
</div>
</details>
</li>
<li><code>run_async</code> (sidecar_protocol_adapters.py) — <span class="doc-comment-inline">Fetch content via alternative protocols based on query.</span></li>
<li><code>run_embed</code> (role_based_pools.py)</li>
<li><code>_check_prewindup_barrier_sync</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F207R-A: Synchronous pre-windup barrier check (read-only).</summary>
<div class="doc-comment">
<p>Sprint F207R-A: Synchronous pre-windup barrier check (read-only).</p>
<p></p>
<p>P1-B FIX: This function is called from windup_guard() callback AFTER</p>
<p></p>
<p>_ensure_pre_windup_lane_terminal_states() has already run at line 7397.</p>
<p></p>
<p>Previously this function re-ran _ensure_pre_windup_lane_terminal_states()</p>
<p></p>
<p>via run_coroutine_threadsafe, causing a RACE CONDITION where both calls</p>
<p></p>
<p>wrote to self._result fields simultaneously, resulting in:</p>
<p></p>
<p>- attempted_lanes=[] (second call overwrote first)</p>
<p></p>
<p>- satisfied=False (skipped lanes not counted correctly)</p>
<p></p>
<p>- windup_guard_last_allowed=False (callback saw unsatisfied barrier)</p>
<p></p>
<p>FIX: Read prewindup barrier state directly from self._result instead</p>
<p></p>
<p>of re-running the async barrier check. This is the correct design because:</p>
<p></p>
<p>1. _ensure_pre_windup_lane_terminal_states() already ran at line 7397</p>
<p></p>
<p>2. It set prewindup_barrier_checked=True and populated all barrier fields</p>
<p></p>
<p>3. windup_guard() is called AFTER step 1, so telemetry is already available</p>
<p></p>
<p>Returns True if windup is allowed (barrier satisfied or not required).</p>
<p></p>
<p>Returns False if windup must be blocked (required lanes not terminal).</p>
<p></p>
<p>Fail-closed: on error, blocks windup with explicit telemetry.</p>
</div>
</details>
</li>
<li><code>_update_source_economics</code> (sprint_scheduler_v1_archived.py)
<details><summary>Update per-source economics from pipeline result signals.</summary>
<div class="doc-comment">
<p>Update per-source economics from pipeline result signals.</p>
<p></p>
<p></p>
<p></p>
<p>Uses only existing surfaces from FeedPipelineRunResult:</p>
<p></p>
<p>- signal_stage: cold/hot diagnosis</p>
<p></p>
<p>- feed_confidence_score: 0-100 adapter-informed confidence</p>
<p></p>
<p>- winning_source_breakdown: signal origin analysis</p>
<p></p>
<p></p>
<p></p>
<p>Economics state is in-memory only for the current sprint.</p>
<p></p>
<p>Reset happens in _reset_result().</p>
</div>
</details>
</li>
<li><code>_get_source_health_summary</code> (sprint_scheduler_v1_archived.py)
<details><summary>F206I: Build a bounded source health summary from per-source economics.</summary>
<div class="doc-comment">
<p>F206I: Build a bounded source health summary from per-source economics.</p>
<p></p>
<p></p>
<p></p>
<p>Reads _source_economics (in-memory, per-sprint) and returns a</p>
<p></p>
<p>compact summary dict for the diagnostic report. Non-persisting.</p>
<p></p>
<p></p>
<p></p>
<p>Bounds:</p>
<p></p>
<p>- MAX_SOURCE_HEALTH_ENTRIES=100 (most-healthy first)</p>
<p></p>
<p>- Each entry is a small dict with posture and cooldown info</p>
<p></p>
<p></p>
<p></p>
<p>Fail-soft: returns empty dict on any error.</p>
<p></p>
<p></p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p></p>
<p>- No asyncio.gather / _check_gathered (sync method)</p>
<p></p>
<p>- No asyncio.run() or loop.run_until_complete()</p>
<p></p>
<p>- No model/MLX imports</p>
<p></p>
<p>- No canonical write path (read-only)</p>
</div>
</details>
</li>
<li><code>run_db</code> (role_based_pools.py)</li>
<li><code>_ensure_nonfeed_predispatch_before_finalization</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F208M-A: Ensure nonfeed predispatch has run before final terminality.</summary>
<div class="doc-comment">
<p>Sprint F208M-A: Ensure nonfeed predispatch has run before final terminality.</p>
<p></p>
<p></p>
<p></p>
<p>This helper is called before every _finalize_result_truth() to guarantee</p>
<p></p>
<p>that bounded CT/PUBLIC predispatch has had a chance to populate</p>
<p></p>
<p>acquisition_lane_outcomes / _lane_outcomes BEFORE terminality is computed.</p>
<p></p>
<p></p>
<p></p>
<p>Without this, terminality computed in _finalize_result_truth sees</p>
<p></p>
<p>acquisition_lane_outcomes empty (no CT attempted yet), marking CT as</p>
<p></p>
<p>missing even though _maybe_dispatch_nonfeed_probe_lanes() was called.</p>
<p></p>
<p></p>
<p></p>
<p>Runs only once per sprint -- subsequent calls are no-ops.</p>
<p></p>
<p>Records explicit telemetry so failure is never silent.</p>
<p></p>
<p></p>
<p></p>
<p>Args:</p>
<p></p>
<p>query: Sprint query for lane shaping.</p>
<p></p>
<p>reason: Human-readable reason for this finalization call.</p>
<p></p>
<p></p>
<p></p>
<p>Raises:</p>
<p></p>
<p>CancelledError: propagated if predispatch is cancelled.</p>
</div>
</details>
</li>
<li><code>_drain_pending_pattern_extractions</code> (sprint_scheduler_v1_archived.py)
<details><summary>F273C + F273H: Pre-windup drain of in-flight pattern-extraction Futures.</summary>
<div class="doc-comment">
<p>F273C + F273H: Pre-windup drain of in-flight pattern-extraction Futures.</p>
<p></p>
<p>Calls into `public_fetcher.drain_pending_extractions(deadline_s)` to</p>
<p>await any HTML the public fetcher has already submitted to</p>
<p>CPU_EXECUTOR. This is the direct fix for the "16/16 fetched → 0</p>
<p>matched patterns → 0 stored" failure mode where the windup transition</p>
<p>cancelled the awaiting branch before its extraction Future resolved.</p>
<p></p>
<p>F273H: Adaptive drain deadline. Before this fix the drain deadline was</p>
<p>a fixed 30s that could exceed the remaining sprint time, causing the</p>
<p>drain itself to consume nearly the entire active window on short</p>
<p>sprints (windup = 304.57s of 305s observed). Now bounded to</p>
<p>min(30s, remaining_s * 0.3) so the drain never consumes more than 30%</p>
<p>of whatever time remains. Also early-exits when the drain registry</p>
<p>is already empty, avoiding a pointless wait.</p>
<p></p>
<p>Always-on, bounded (adaptive deadline), fail-soft: any error</p>
<p>in the drain path returns silently and the windup decision proceeds.</p>
<p></p>
<p>Telemetry recorded on self._result:</p>
<p>- pattern_extraction_drain_completed  (cumulative count)</p>
<p>- pattern_extraction_drain_timed_out  (cumulative count)</p>
<p>- pattern_extraction_drain_elapsed_s  (last drain wall-clock)</p>
<p>- effective_windup_lead_used_s  (actual windup lead applied)</p>
</div>
</details>
</li>
<li><code>_enrich_findings_multimodal</code> (sprint_scheduler_v1_archived.py)
<details><summary>Enrich PDF/image findings with multimodal analysis before storage.</summary>
<div class="doc-comment">
<p>Enrich PDF/image findings with multimodal analysis before storage.</p>
<p></p>
<p>Fail-safe: enrichment errors are silent -- never crash or abort the sprint.</p>
<p>Enrichment is best-effort: absence of multimodal data is not an error.</p>
</div>
</details>
</li>
<li><code>_enrich_ct_findings_forensics</code> (sprint_scheduler_v1_archived.py)
<details><summary>Enrich CT findings with forensics analysis before storage.</summary>
<div class="doc-comment">
<p>Enrich CT findings with forensics analysis before storage.</p>
<p></p>
<p>Fail-safe: enrichment errors are silent -- never crash or abort the sprint.</p>
<p>Enrichment is best-effort: absence of forensics data is not an error.</p>
</div>
</details>
</li>
<li><code>run_all_advisories</code> (sprint_advisory_runner.py)
<details><summary>Run all 6 advisory steps with parallelization where safe.</summary>
<div class="doc-comment">
<p>Run all 6 advisory steps with parallelization where safe.</p>
<p></p>
<p>Order (mandatory sequential):</p>
<p>1. pivot_planner  → planned_pivots</p>
<p>2. pivot_executor → executed_pivots  [depends on 1]</p>
<p></p>
<p>Steps 3-6 run in PARALLEL (bounded semaphore, M1 8GB safe):</p>
<p>3. resource_governor → governor_recorded</p>
<p>4. analyst_brief → brief_generated</p>
<p>5. local_search → local_search_*</p>
<p>6. federated_research → federated_* (F350M-FED-P3-FOLLOWUP)</p>
<p></p>
<p>Parallel execution via safe_gather_ok with _ADVISORY_PARALLEL_SEMAPHORE_LIMIT=4.</p>
<p>Each step is fail-soft; exceptions are collected and merged into outcome.error.</p>
<p></p>
<p>CancelledError: re-raised to caller.</p>
<p>Fail-soft: any step failure returns partial outcome with error message.</p>
<p></p>
<p>Returns:</p>
<p>AdvisoryRunOutcome with counts/flags for each step.</p>
</div>
</details>
</li>
<li><code>_sort_work_items_by_economics</code> (sprint_scheduler_v1_archived.py)
<details><summary>Re-sort work items by source economics.</summary>
<div class="doc-comment">
<p>Re-sort work items by source economics.</p>
<p></p>
<p></p>
<p></p>
<p>Order:</p>
<p></p>
<p>1. Sources NOT in cooldown first (natural priority)</p>
<p></p>
<p>2. Sources with hot/warm posture boosted</p>
<p></p>
<p>3. Cold/in-cooldown sources at the end</p>
<p></p>
<p>4. Tier ordering still applies as secondary sort key</p>
<p></p>
<p>5. F200A: Advisory prefetch oracle score multiplies the sort key</p>
<p></p>
<p></p>
<p></p>
<p>F200A: oracle is ADVISORY ONLY -- scheduler retains authority.</p>
<p></p>
<p>If oracle is None or suggest_scores fails -&gt; falls back to default ordering.</p>
</div>
</details>
</li>
<li><code>_build_plugin_sidecar_context</code> (sidecar_orchestrator.py)
<details><summary>Construct a SidecarContext (or duck-typed equivalent) from the</summary>
<div class="doc-comment">
<p>Construct a SidecarContext (or duck-typed equivalent) from the</p>
<p>current scheduler state. Returns None if no scheduler is bound.</p>
</div>
</details>
</li>
<li><code>__post_init__</code> (scheduler.py)</li>
<li><code>record_hypothesis_feedback</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_run_cti_export</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>dispatch_findings</code> (sidecar_orchestrator.py)</li>
<li><code>record_hypothesis_feedback</code> (scheduler.py)</li>
<li><code>_run_resource_governor_advisory</code> (sprint_advisory_runner.py)
<details><summary>F202J: Apply resource governor decision at TEARDOWN.</summary>
<div class="doc-comment">
<p>F202J: Apply resource governor decision at TEARDOWN.</p>
<p></p>
<p>Advisory only: governor evaluates and applies concurrency hints.</p>
<p>Sprint retains all authority.</p>
<p></p>
<p>F204J: Also tracks peak RSS and sidecars skipped for budget scorecard.</p>
<p></p>
<p>Fail-soft: errors never crash the runner.</p>
</div>
</details>
</li>
<li><code>compute</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_run_social_identity_surface_sidecar</code> (sprint_scheduler_v1_archived.py)
<details><summary>F204I: Social Identity Surface Miner — extract usernames/profiles from findings.</summary>
<div class="doc-comment">
<p>F204I: Social Identity Surface Miner — extract usernames/profiles from findings.</p>
<p></p>
<p>Wire point: called in WINDUP phase after all acquisition lanes complete.</p>
<p>Canonical execution path via SprintScheduler (not SidecarRegistry) to avoid</p>
<p>double-execution — the SidecarRegistry adapter is wiring-only (returns []).</p>
<p></p>
<p>Gates:</p>
<p>- HLEDAC_ENABLE_SOCIAL_IDENTITY_SURFACE=1 (default: 0, opt-in)</p>
<p>- duckdb_store is not None</p>
<p>- self._result.accepted_findings is not empty</p>
<p></p>
<p>Args:</p>
<p>query: Original sprint query string</p>
<p>duckdb_store: DuckDBShadowStore instance for canonical write</p>
</div>
</details>
</li>
<li><code>_build_public_stage_counters</code> (sprint_scheduler_v1_archived.py)
<details><summary>F208G-A: Build public_stage_counters dict from _public_pipeline_result.</summary>
<div class="doc-comment">
<p>F208G-A: Build public_stage_counters dict from _public_pipeline_result.</p>
<p></p>
<p></p>
<p></p>
<p>This aggregates all F208G-A public_* telemetry fields from the stored</p>
<p></p>
<p>PipelineRunResult into a single dict for propagation to acquisition_report</p>
<p></p>
<p>and source_family_outcomes.</p>
<p></p>
<p></p>
<p></p>
<p>Returns an empty dict if _public_pipeline_result is None (PUBLIC skipped).</p>
</div>
</details>
</li>
<li><code>build_snapshot</code> (acquisition_strategy.py)
<details><summary>Build a NonfeedMissionSnapshot from current scheduler state.</summary>
<div class="doc-comment">
<p>Build a NonfeedMissionSnapshot from current scheduler state.</p>
<p></p>
<p>Args:</p>
<p>acquisition_profile: Current acquisition profile name</p>
<p>acquisition_lane_outcomes: Tuple of AcquisitionLaneOutcome from run_enabled_acquisition_lanes</p>
<p>public_outcome: _public_outcome dict from SprintScheduler (None if PUBLIC never ran)</p>
<p>ct_quarantine_count: ct_quarantine_count from SprintSchedulerResult</p>
<p>quality_rejection_ledger: quality_rejection_ledger from SprintSchedulerResult</p>
<p>memory_skipped_families: Families skipped due to memory pressure</p>
</div>
</details>
</li>
<li><code>build_snapshot</code> (__init__.py)
<details><summary>Build a NonfeedMissionSnapshot from current scheduler state.</summary>
<div class="doc-comment">
<p>Build a NonfeedMissionSnapshot from current scheduler state.</p>
<p></p>
<p>Args:</p>
<p>acquisition_profile: Current acquisition profile name</p>
<p>acquisition_lane_outcomes: Tuple of AcquisitionLaneOutcome from run_enabled_acquisition_lanes</p>
<p>public_outcome: _public_outcome dict from SprintScheduler (None if PUBLIC never ran)</p>
<p>ct_quarantine_count: ct_quarantine_count from SprintSchedulerResult</p>
<p>quality_rejection_ledger: quality_rejection_ledger from SprintSchedulerResult</p>
<p>memory_skipped_families: Families skipped due to memory pressure</p>
</div>
</details>
</li>
<li><code>_run_prelude</code> (scheduler.py) — <span class="doc-comment-inline">Run the prelude phase via PreludeOrchestrator.</span></li>
<li><code>should_enter_windup</code> (sprint_lifecycle.py)
<details><summary>True when remaining time is at or below the windup lead threshold.</summary>
<div class="doc-comment">
<p>True when remaining time is at or below the windup lead threshold.</p>
<p></p>
<p>F288: When pre_loop_cost_s &gt; windup_lead_s (measured at runtime),</p>
<p>the effective trigger is raised to windup_lead_s + pre_loop_cost_s.</p>
<p>This ensures at least one full acquisition cycle completes before</p>
<p>windup fires — even when init cost exceeded the static windup_lead_s.</p>
<p></p>
<p>F289: HARD MINIMUM — never return True if remaining time would leave</p>
<p>less than 30s of active work. This prevents "instant windup" where</p>
<p>windup_lead_s is set too close to sprint_duration (e.g. 450s windup</p>
<p>lead on a 460s sprint leaves only 10s of actual work).</p>
</div>
</details>
</li>
<li><code>_feed_dominance_record_result</code> (sprint_scheduler_v1_archived.py)
<details><summary>F216E: Record feed result into budget telemetry.</summary>
<div class="doc-comment">
<p>F216E: Record feed result into budget telemetry.</p>
<p></p>
<p></p>
<p></p>
<p>F230D: Also records nonfeed_budget telemetry when nonfeed_diagnostic profile active.</p>
</div>
</details>
</li>
<li><code>_maybe_export_partial</code> (sprint_scheduler_v1_archived.py)
<details><summary>Write a partial JSON artifact if the findings interval has been reached.</summary>
<div class="doc-comment">
<p>Write a partial JSON artifact if the findings interval has been reached.</p>
<p></p>
<p></p>
<p></p>
<p>Called every cycle in aggressive mode.  Also callable on early windup</p>
<p></p>
<p>or abort to ensure the latest partial survives.</p>
</div>
</details>
</li>
<li><code>_generate_pivots_for_ioc</code> (pivot_planner.py) — <span class="doc-comment-inline">Generate pivots for a single IOC.</span></li>
<li><code>lane_admission</code> (resource_governor.py)
<details><summary>F214R: Canonical lane admission check.</summary>
<div class="doc-comment">
<p>F214R: Canonical lane admission check.</p>
<p></p>
<p>Returns LaneAdmission with:</p>
<p>- allowed: True if lane can be admitted</p>
<p>- reason: human-readable denial reason</p>
<p>- uma_state: current UMA state</p>
<p>- risk_level: the risk level that was evaluated</p>
<p></p>
<p>risk_level: "low" | "medium" | "high" | "critical"</p>
<p>Heavy lanes (high/critical risk) are blocked under critical/emergency UMA.</p>
<p>Fail-soft: returns allowed=True on errors.</p>
</div>
</details>
</li>
<li><code>_run_analyst_brief_advisory</code> (sprint_advisory_runner.py)
<details><summary>F204E/F205J: Generate analyst brief at TEARDOWN.</summary>
<div class="doc-comment">
<p>F204E/F205J: Generate analyst brief at TEARDOWN.</p>
<p></p>
<p>Uses canonical target_id (query or duckdb_store lookup) instead of</p>
<p>sprint_id, enabling cross-sprint target memory reads.</p>
<p></p>
<p>Advisory only: brief summarizes sprint results but does not affect</p>
<p>sprint execution or outcomes. Sprint retains all authority.</p>
<p></p>
<p>Fail-soft: errors never crash the runner.</p>
<p></p>
<p>Stores brief in scheduler._analyst_brief for export hookup.</p>
</div>
</details>
</li>
<li><code>run_async</code> (sidecar_protocol_adapters.py)
<details><summary>Run ShadowWalker path prediction for the sprint query.</summary>
<div class="doc-comment">
<p>Run ShadowWalker path prediction for the sprint query.</p>
<p></p>
<p>1. Extract base URL from query</p>
<p>2. Run ShadowWalkerAlgorithm to predict hidden paths</p>
<p>3. Convert predictions to findings</p>
</div>
</details>
</li>
<li><code>_run_pdns_prelude_lane</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_resolve_attr</code> (sprint_scheduler_v1_archived.py)
<details><summary>Resolve the normalized attr name for `name` on _lc, cached in instance __dict__.</summary>
<div class="doc-comment">
<p>Resolve the normalized attr name for `name` on _lc, cached in instance __dict__.</p>
<p></p>
<p>Falls back through multiple candidate names to handle API differences</p>
<p>between runtime/ and utils/ lifecycle implementations.</p>
</div>
</details>
</li>
<li><code>_run_gopher_sidecar</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F214R: Gopher/Veronica-2 discovery via Floodgap proxy.</summary>
<div class="doc-comment">
<p>Sprint F214R: Gopher/Veronica-2 discovery via Floodgap proxy.</p>
<p>Gate: HLEDAC_ENABLE_GOPHER=1, max_items=50, timeout=30s.</p>
<p>Fail-soft: Gopher errors logged, never crash sprint.</p>
</div>
</details>
</li>
<li><code>run_async</code> (sidecar_protocol_adapters.py) — <span class="doc-comment-inline">Search GitHub Gists for OSINT signals based on query and findings.</span></li>
<li><code>run</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_attempt_ct_prewindup_barrier</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F207Q-A: Attempt CT lane as part of pre-windup barrier.</summary>
<div class="doc-comment">
<p>Sprint F207Q-A: Attempt CT lane as part of pre-windup barrier.</p>
<p></p>
<p></p>
<p></p>
<p>Args:</p>
<p></p>
<p>query: Sprint query for lane query shaping.</p>
<p></p>
<p></p>
<p></p>
<p>Returns dict with keys: attempted, error, timeout, or None on exception.</p>
<p></p>
<p>Uses tiny bounds (max 5 results, 15s timeout).</p>
</div>
</details>
</li>
<li><code>_teardown_sprint</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Phase D: Teardown - cleanup resources at sprint end (tracemalloc, GC, privacy context).</span></li>
<li><code>_dispatch_plugin_sidecar</code> (sidecar_orchestrator.py)</li>
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
<li><code>_get_lane_outcome</code> (acquisition_strategy.py)
<details><summary>Get the outcome dict for a lane family.</summary>
<div class="doc-comment">
<p>Get the outcome dict for a lane family.</p>
<p></p>
<p>Returns a dict with keys: accepted_findings, terminal_state, error, skipped</p>
<p>suitable for mission evaluation.</p>
<p></p>
<p>Args:</p>
<p>family: Lane family string (PUBLIC, CT, etc.)</p>
<p>acquisition_lane_outcomes: Tuple of AcquisitionLaneOutcome from run_enabled_acquisition_lanes</p>
<p>public_outcome: _public_outcome dict from SprintScheduler (for PUBLIC lane)</p>
<p>ct_quarantine_count: ct_quarantine_count from SprintSchedulerResult</p>
<p>quality_rejection_ledger: quality_rejection_ledger from SprintSchedulerResult</p>
</div>
</details>
</li>
<li><code>_get_lane_outcome</code> (__init__.py)
<details><summary>Get the outcome dict for a lane family.</summary>
<div class="doc-comment">
<p>Get the outcome dict for a lane family.</p>
<p></p>
<p>Returns a dict with keys: accepted_findings, terminal_state, error, skipped</p>
<p>suitable for mission evaluation.</p>
<p></p>
<p>Args:</p>
<p>family: Lane family string (PUBLIC, CT, etc.)</p>
<p>acquisition_lane_outcomes: Tuple of AcquisitionLaneOutcome from run_enabled_acquisition_lanes</p>
<p>public_outcome: _public_outcome dict from SprintScheduler (for PUBLIC lane)</p>
<p>ct_quarantine_count: ct_quarantine_count from SprintSchedulerResult</p>
<p>quality_rejection_ledger: quality_rejection_ledger from SprintSchedulerResult</p>
</div>
</details>
</li>
<li><code>run_async</code> (sidecar_protocol_adapters.py) — <span class="doc-comment-inline">Search academic sources for research papers matching query.</span></li>
<li><code>branch_admission</code> (resource_governor.py)
<details><summary>F214R: Canonical branch admission check.</summary>
<div class="doc-comment">
<p>F214R: Canonical branch admission check.</p>
<p>@pending_integration: no confirmed production call sites as of F214R audit.</p>
<p>See tests/test_resource_governor_authority_seal.py::TestPendingIntegrationMarkers.</p>
<p></p>
<p>Returns BranchAdmission with:</p>
<p>- allowed: True if branch can run</p>
<p>- reason: human-readable denial reason</p>
<p>- uma_state: current UMA state</p>
<p>- branch_concurrency: recommended concurrency for this branch</p>
<p>- estimated_mb: the estimate that was evaluated</p>
<p></p>
<p>Fail-soft: returns allowed=True with normal concurrency on errors.</p>
</div>
</details>
</li>
<li><code>_check_hard_deadline</code> (sprint_scheduler_v1_archived.py)
<details><summary>Check if the hard monotonic deadline has been exceeded.</summary>
<div class="doc-comment">
<p>Check if the hard monotonic deadline has been exceeded.</p>
<p></p>
<p></p>
<p></p>
<p>Returns:</p>
<p></p>
<p>True if deadline is NOT exceeded (new work may proceed).</p>
<p></p>
<p>False if deadline IS exceeded (no new branch dispatch).</p>
<p></p>
<p></p>
<p></p>
<p>This method is idempotent -- it can be called multiple times per cycle</p>
<p></p>
<p>without changing state. Deadline-exceeded state is tracked once in</p>
<p></p>
<p>the result and never reset.</p>
</div>
</details>
</li>
<li><code>_run_one_cycle</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_memory_pressure_loop</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Background task -- adjusts concurrency based on memory pressure.</span></li>
<li><code>_ensure_initialized</code> (role_based_pools.py) — <span class="doc-comment-inline">Lazy initialization of all executors (double-checked locking).</span></li>
<li><code>_run_local_search_advisory</code> (sprint_advisory_runner.py)
<details><summary>F228C: Local search advisory at teardown.</summary>
<div class="doc-comment">
<p>F228C: Local search advisory at teardown.</p>
<p></p>
<p>Indexes accepted findings into LocalSearchSeam (advisory-only, no</p>
<p>canonical writes, no persistent DB). Then searches them with the</p>
<p>sprint query to surface relevant evidence for research context.</p>
<p></p>
<p>Bounded, fail-soft, no network, no model load.</p>
<p></p>
<p>Telemetry fields in AdvisoryRunOutcome:</p>
<p>local_search_attempted: True if seam was queried</p>
<p>local_search_hits: Number of top results returned</p>
<p>local_search_indexed: Number of findings indexed</p>
<p>local_search_source: "search_index" or "none"</p>
<p>local_search_elapsed_ms: Wall time of index+search</p>
<p>local_search_top_results: list[dict] with url/title/score/source_type/finding_id</p>
<p>local_search_error: Error string if failed, else None</p>
</div>
</details>
</li>
<li><code>_attempt_public_prewindup_barrier</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F207Q-A: Attempt PUBLIC lane as part of pre-windup barrier.</summary>
<div class="doc-comment">
<p>Sprint F207Q-A: Attempt PUBLIC lane as part of pre-windup barrier.</p>
<p></p>
<p></p>
<p></p>
<p>Args:</p>
<p></p>
<p>query: Sprint query for lane query shaping.</p>
<p></p>
<p></p>
<p></p>
<p>Returns dict with keys: attempted, error, timeout, or None on exception.</p>
<p></p>
<p>Uses tiny bounds (max 3 results, 10s timeout).</p>
</div>
</details>
</li>
<li><code>_run_ioc_cooccurrence_sidecar</code> (sprint_scheduler_v1_archived.py)
<details><summary>Issue 4.1: Run IOC co-occurrence analysis on accumulated findings.</summary>
<div class="doc-comment">
<p>Issue 4.1: Run IOC co-occurrence analysis on accumulated findings.</p>
<p></p>
<p>Wired in WINDUP phase — runs after all acquisition lanes complete so the</p>
<p>full finding set is available. Uses:</p>
<p>- Rust engine (compute_cooccurrence_edges_py) via asyncio.to_thread()</p>
<p>- msgspec.to_builtins() for cheap serialization</p>
<p></p>
<p>Architecture:</p>
<p>finding_pipeline (async enrich+store) ∥ live_public_pipeline ∥ IOCooccurrenceMiner</p>
<p></p>
<p>M1 8GB: asyncio.to_thread() runs Rust engine without blocking event loop.</p>
<p>No ProcessPoolExecutor — rayon CPU pool handles multi-core parallelism.</p>
</div>
</details>
</li>
<li><code>_init_fetch_coordinator</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Phase K: FetchCoordinator instantiation with provider lambdas + DNS prewarm.</span></li>
<li><code>_parallel_ingest</code> (sprint_scheduler_v1_archived.py)
<details><summary>Bounded parallel ingest: chunk → TaskGroup → single mx.eval barrier.</summary>
<div class="doc-comment">
<p>Bounded parallel ingest: chunk → TaskGroup → single mx.eval barrier.</p>
<p></p>
<p>F320M-R FIX: Sequential for-loop replaced with asyncio.TaskGroup for TRUE parallelism.</p>
<p>Previously chunks ran sequentially even though a Semaphore existed — the await inside</p>
<p>the for-loop blocked until each chunk completed before starting the next.</p>
<p></p>
<p>M1 8GB: max _MAX_CHUNK_CONCURRENCY concurrent chunks, single Metal memory barrier after all.</p>
<p>Returns canonical results (same as async_ingest_findings_batch).</p>
</div>
</details>
</li>
<li><code>_speculative_prefetch</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Spustit top-n pivot tasků spekulativně jako background tasks.</span></li>
<li><code>run_async</code> (sidecar_protocol_adapters.py) — <span class="doc-comment-inline">Scan for leaked credentials/data related to query.</span></li>
<li><code>_gate_then_ingest</code> (sprint_scheduler_v1_archived.py)
<details><summary>F285: PII gate + canonical write for feed lanes.</summary>
<div class="doc-comment">
<p>F285: PII gate + canonical write for feed lanes.</p>
<p></p>
<p>When HLEDAC_ENABLE_PRIVACY_LAYER=1, anonymizes PII in</p>
<p>content/raw_content/payload_text/title/summary BEFORE the</p>
<p>findings hit async_ingest_findings_batch.</p>
<p></p>
<p>Fail-soft: never raises. On any error, findings pass through</p>
<p>to the canonical write path unmodified.</p>
<p></p>
<p>Args:</p>
<p>store: duckdb_store (or any object with</p>
<p>async_ingest_findings_batch). None -&gt; no-op.</p>
<p>findings: list of CanonicalFinding (or duckdb-compatible</p>
<p>dicts). Empty -&gt; no-op.</p>
<p></p>
<p>Returns:</p>
<p>Whatever async_ingest_findings_batch returns, or None on</p>
<p>skip / error.</p>
</div>
</details>
</li>
<li><code>_speculative_dns_prefetch</code> (sprint_scheduler_v1_archived.py)
<details><summary>Fire-and-forget DNS resolution for top-k domain candidates.</summary>
<div class="doc-comment">
<p>Fire-and-forget DNS resolution for top-k domain candidates.</p>
<p></p>
<p>Runs as background task while fetch loop is active -- overlaps</p>
<p>DNS latency (~5-50ms) with ongoing network I/O.</p>
<p></p>
<p>Results stored in _speculative_dns_cache for later pivot planning.</p>
<p>Fail-soft: any error silently ignored, cache miss treated as "unresolved".</p>
<p></p>
<p>Args:</p>
<p>domains: List of domain strings to prefetch</p>
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
<li><code>_generate_pivots_from_findings</code> (pivot_planner.py)
<details><summary>Issue #17: Single-pass pivot generation from findings.</summary>
<div class="doc-comment">
<p>Issue #17: Single-pass pivot generation from findings.</p>
<p></p>
<p>Optional hermes_boost_map allows boosting heuristic pivots with Hermes scores</p>
<p>in a single pass, instead of iterating findings twice.</p>
<p></p>
<p>Args:</p>
<p>findings: List of findings to process</p>
<p>graph_stats: Optional graph statistics for scoring</p>
<p>feedback_summary: Optional feedback penalties</p>
<p>hermes_boost_map: Optional Hermes boost map (pivot_key → boost_score)</p>
<p>hermes_pivot_info: Optional Hermes pivot metadata (pivot_key → info_dict)</p>
<p></p>
<p>Note: caller handles max_pivots cap via slice after sort.</p>
</div>
</details>
</li>
<li><code>evaluate_adaptive</code> (resource_governor.py)
<details><summary>F2-2: EMA-adaptive governor evaluation.</summary>
<div class="doc-comment">
<p>F2-2: EMA-adaptive governor evaluation.</p>
<p></p>
<p>Runs the base evaluate() logic, then applies EMA timeout pressure override</p>
<p>on top of branch_concurrency only. The EMA tracks sustained timeout</p>
<p>pressure (0.0 = no pressure, 1.0 = continuous timeouts) and degrades</p>
<p>branch concurrency accordingly before the base UMA state would.</p>
<p></p>
<p>This is additive — it does NOT replace evaluate(). The EMA override is</p>
<p>applied as a post-processing step to the base decision's branch_concurrency.</p>
<p></p>
<p>EMA thresholds:</p>
<p>ema &gt; 0.7  → sustained high pressure  → branch_concurrency = 1</p>
<p>ema &gt; 0.4  → medium pressure          → branch_concurrency = min(base, 2)</p>
<p>ema ≤ 0.4   → no/low pressure         → branch_concurrency unchanged</p>
<p></p>
<p>Fails soft: falls back to safe defaults on any error.</p>
</div>
</details>
</li>
<li><code>_capture_timing_fields</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F215D: Capture wall-clock timing fields for early-exit classification.</summary>
<div class="doc-comment">
<p>Sprint F215D: Capture wall-clock timing fields for early-exit classification.</p>
<p></p>
<p></p>
<p></p>
<p>Called before _finalize_result_truth in early-exit break paths so that</p>
<p></p>
<p>_compute_early_exit_class has correct elapsed_pct (not 0.0).</p>
<p></p>
<p>Timing is also captured at the normal-completion path (lines 1843-1859).</p>
</div>
</details>
</li>
<li><code>_adapt_source_weights_from_feedback_python</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_sensitive_query_transport</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F250: Return preferred transport for sensitive queries.</summary>
<div class="doc-comment">
<p>Sprint F250: Return preferred transport for sensitive queries.</p>
<p></p>
<p>Priority: Nym &gt; Tor &gt; I2P &gt; clearnet.</p>
<p></p>
<p>Returns transport name string or "clearnet" fallback.</p>
</div>
</details>
</li>
<li><code>_run_public_branch_aggressive</code> (acquisition.py)</li>
<li><code>run_async</code> (sidecar_protocol_adapters.py) — <span class="doc-comment-inline">Search TV News Archive for broadcast content matching query.</span></li>
<li><code>_min_branch_remaining_s</code> (sprint_scheduler_v1_archived.py)
<details><summary>F273B: Dynamic branch-remaining safety floor based on remaining time.</summary>
<div class="doc-comment">
<p>F273B: Dynamic branch-remaining safety floor based on remaining time.</p>
<p></p>
<p>Returns the safety floor (in seconds) below which a branch is skipped</p>
<p>with `terminal:remaining_too_low`. Replaces the cycle-ema-based formula</p>
<p>(0.2 * cycle_ema) that was too low for 300s+ sprints where 25 cycles</p>
<p>over-commit the timeline.</p>
<p></p>
<p>Formula (always-on, bounded [2.0, 5.0]):</p>
<p>remaining_s = time left in sprint (passed as argument)</p>
<p>base = max(2.0, 0.15 * remaining_s)</p>
<p>return min(5.0, base)</p>
<p></p>
<p>Examples (300s sprint):</p>
<p>- remaining_s=150s (50% left) -&gt; base = max(2.0, 22.5) = 22.5 -&gt; return 5.0s (capped)</p>
<p>- remaining_s=90s  (30% left) -&gt; base = max(2.0, 13.5) = 13.5 -&gt; return 5.0s (capped)</p>
<p>- remaining_s=60s  (20% left) -&gt; base = max(2.0, 9.0) = 9.0  -&gt; return 5.0s (capped)</p>
<p>- remaining_s=33.3s(11% left) -&gt; base = max(2.0, 5.0) = 5.0  -&gt; return 5.0s (at breakpoint)</p>
<p>- remaining_s=30s  (10% left) -&gt; base = max(2.0, 4.5) = 4.5  -&gt; return 4.5s</p>
<p>- remaining_s=15s  (5% left)  -&gt; base = max(2.0, 2.25) = 2.25 -&gt; return 2.25s</p>
<p></p>
<p>Why 0.15 * remaining_s: floor scales with remaining time so branches</p>
<p>get adequate time in long sprints while staying low in short sprints.</p>
<p>The 5.0s cap is active when 0.15*remaining_s &gt; 5.0, i.e. remaining_s &gt; 33.3s.</p>
<p>This prevents 300s sprints from losing all branches to terminal:remaining_too_low.</p>
<p></p>
<p>Fail-safe: if remaining_s is None or &lt;= 0, falls back to cycle-ema-based</p>
<p>formula (0.1 * cycle_ema, bounded [2.0, 5.0]) for backward compatibility.</p>
</div>
</details>
</li>
<li><code>_init_metrics_registry</code> (sprint_scheduler_v1_archived.py)
<details><summary>Initialize MetricsRegistry fail-soft using config export_dir or default path.</summary>
<div class="doc-comment">
<p>Initialize MetricsRegistry fail-soft using config export_dir or default path.</p>
<p></p>
<p></p>
<p></p>
<p>No absolute paths outside paths.py. Run dir is derived from export_dir</p>
<p></p>
<p>(if set) or ~/.hledac/runs (default fallback). Metrics file lives under</p>
<p></p>
<p>run_dir/logs/metrics.jsonl.</p>
</div>
</details>
</li>
<li><code>_run_ct_branch_aggressive</code> (acquisition.py)</li>
<li><code>run_hash</code> (role_based_pools.py)</li>
<li><code>run_regex</code> (role_based_pools.py)</li>
<li><code>_run_ane_semantic_dedup_advisory</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F265B-III: ANE-backed semantic deduplication of findings.</summary>
<div class="doc-comment">
<p>Sprint F265B-III: ANE-backed semantic deduplication of findings.</p>
<p></p>
<p>Runs after all advisory steps have completed, on the full findings list.</p>
<p>Uses ANE CoreML MiniLM embeddings to detect near-duplicate findings that</p>
<p>the RotatingBloomFilter URL dedup misses (similar title+snippet, not exact URL).</p>
<p></p>
<p>Bounded:</p>
<p>- threshold = 0.92 cosine similarity</p>
<p>- Only runs when ANE embedder is loaded (fail-soft if unavailable)</p>
<p>- No changes to canonical write path (DuckDB/LMDB untouched)</p>
<p></p>
<p>Returns:</p>
<p>None. Findings list is updated in-place via self._result.all_findings.</p>
</div>
</details>
</li>
<li><code>cap_feeding</code> (acquisition_strategy.py)
<details><summary>Check if feeding should be capped.</summary>
<div class="doc-comment">
<p>Check if feeding should be capped.</p>
<p></p>
<p>F227D: Added mission_intent and nonfeed_unresolved parameters.</p>
<p>When mission_runtime is active and nonfeed lanes are unresolved,</p>
<p>mission-aware thresholds override the base budget thresholds.</p>
<p></p>
<p>F230D: Added acquisition_profile parameter for nonfeed_diagnostic profile</p>
<p>per-intent feed cap thresholds.</p>
<p></p>
<p>Returns (should_cap, reason) where reason is empty when cap not active.</p>
</div>
</details>
</li>
<li><code>run_async</code> (sidecar_protocol_adapters.py) — <span class="doc-comment-inline">Search Fediverse for OSINT signals based on query and findings.</span></li>
<li><code>_sync_latent_relationships_to_graph</code> (sprint_scheduler_v1_archived.py)
<details><summary>Wave 2: Export NetworkX latent relationships and upsert unseen ones to DuckPGQ.</summary>
<div class="doc-comment">
<p>Wave 2: Export NetworkX latent relationships and upsert unseen ones to DuckPGQ.</p>
<p></p>
<p></p>
<p></p>
<p>NetworkX discovers relationships (co-occurrence, shared attributes) that are</p>
<p></p>
<p>NOT yet in DuckPGQ. These are upserted with confidence=0.3 (low-confidence</p>
<p></p>
<p>inferred relationships) so the knowledge graph learns across sprints.</p>
</div>
</details>
</li>
<li><code>evaluate_advisory_gate</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint 8VQ: Evaluate advisory gate at WINDUP entry -- DIAGNOSTIC ONLY.</summary>
<div class="doc-comment">
<p>Sprint 8VQ: Evaluate advisory gate at WINDUP entry -- DIAGNOSTIC ONLY.</p>
<p></p>
<p></p>
<p></p>
<p>Reads from cached PreDecisionSummary (computed by consume_shadow_pre_decision)</p>
<p></p>
<p>and composes AdvisoryGateSnapshot. Does NOT:</p>
<p></p>
<p>- Influence dispatch or source ordering</p>
<p></p>
<p>- Activate providers or tools</p>
<p></p>
<p>- Write to any ledgers as runtime truth</p>
<p></p>
<p>- Create new scheduler framework</p>
<p></p>
<p></p>
<p></p>
<p>Stores ephemeral result in _advisory_gate_snapshot (cleared in _reset_result).</p>
<p></p>
<p>Output goes into diagnostic report via _build_shadow_readiness_preview().</p>
</div>
</details>
</li>
<li><code>shutdown</code> (role_based_pools.py)
<details><summary>Shutdown all role-based pools.</summary>
<div class="doc-comment">
<p>Shutdown all role-based pools.</p>
<p></p>
<p>Args:</p>
<p>wait: If True, wait for pending tasks to complete</p>
</div>
</details>
</li>
<li><code>_run_quantum_path_analysis</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F214Q: Post-sprint quantum-inspired graph walk.</summary>
<div class="doc-comment">
<p>Sprint F214Q: Post-sprint quantum-inspired graph walk.</p>
<p>Find undiscovered connected IOCs via DuckPGQGraph.find_connected().</p>
<p></p>
<p>M1 RAM budget: bounded to 20 IOCs per sprint, max_hops=2, max 1000 total nodes.</p>
<p></p>
<p>Sprint P1-3: Routes through GraphService.find_entity_history() which</p>
<p>layers the hot-edges LMDB cache on top of DuckPGQ recursive CTE, giving</p>
<p>O(1) hot-path lookups for high-degree nodes and falling back to the CTE</p>
<p>only on cache miss.</p>
</div>
</details>
</li>
<li><code>_run_ti_feed_sidecar</code> (sprint_scheduler_v1_archived.py)
<details><summary>F252: TI feed advisory sidecar (NVD + CISA KEV).</summary>
<div class="doc-comment">
<p>F252: TI feed advisory sidecar (NVD + CISA KEV).</p>
<p></p>
<p>Fetches structured threat-intel feeds in parallel using safe_gather_ok.</p>
<p>Adapters are registered via source_registry; dispatches NvdApiAdapter</p>
<p>and CisaKevAdapter in parallel with bounded concurrency.</p>
<p>Fail-soft throughout: errors never crash the sprint.</p>
</div>
</details>
</li>
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
<li><code>run_async_io</code> (role_based_pools.py)</li>
<li><code>_maybe_call_pressure_relief</code> (sprint_scheduler_v1_archived.py)
<details><summary>F273G: Per-sprint macOS malloc pressure relief.</summary>
<div class="doc-comment">
<p>F273G: Per-sprint macOS malloc pressure relief.</p>
<p></p>
<p>Calls the existing ``core.memory_cycle.malloc_zone_pressure_relief()``</p>
<p>helper to ask the Darwin allocator to release fragmented pages. Cheap</p>
<p>(single ctypes syscall), thread-safe in libmalloc, and fail-soft on</p>
<p>non-Darwin / on ctypes errors.</p>
<p></p>
<p>Wired into the pre-windup barrier so the windup phase starts with a</p>
<p>clean allocator state — better DuckDB ingest + LMDB mmap behavior +</p>
<p>reduced RSS fragmentation for the Hermes load that may follow.</p>
<p></p>
<p>Telemetry recorded on self._result:</p>
<p>- malloc_pressure_relief_count      (cumulative calls)</p>
<p>- malloc_pressure_relief_last_rc    (last return value, 0 = no-op)</p>
<p>- malloc_pressure_relief_last_at_s  (wall-clock of last call)</p>
<p></p>
<p>Bounded: 1 call per windup decision. No new feature flags.</p>
</div>
</details>
</li>
<li><code>_execute_pivot</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Dispatch pivot task to appropriate intelligence client.</span></li>
<li><code>_dispatch_accepted_findings_sidecars</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_run_public_branch</code> (acquisition.py)</li>
<li><code>run_async</code> (sidecar_protocol_adapters.py) — <span class="doc-comment-inline">Query DHT network for content hashes matching query.</span></li>
<li><code>run</code> (scheduler.py) — <span class="doc-comment-inline">Run the sprint — orchestrate prelude → acquisition → winddown phases.</span></li>
<li><code>renderer_admission</code> (resource_governor.py)
<details><summary>F214R: Canonical renderer admission check.</summary>
<div class="doc-comment">
<p>F214R: Canonical renderer admission check.</p>
<p>@pending_integration: no confirmed production call sites as of F214R audit.</p>
<p>See tests/test_resource_governor_authority_seal.py::TestPendingIntegrationMarkers.</p>
<p></p>
<p>Returns RendererAdmission with:</p>
<p>- allowed: True if JS renderer may be used</p>
<p>- reason: human-readable denial reason</p>
<p>- uma_state: current UMA state</p>
<p>- model_loaded: whether model is currently loaded</p>
<p></p>
<p>Combines model lifecycle + UMA state in one authoritative call.</p>
<p>Fail-soft: returns allowed=False with "unknown" reason on errors.</p>
</div>
</details>
</li>
<li><code>windup_for_cycle</code> (sprint_scheduler_v1_archived.py)
<details><summary>F273B + F278A: Cycle-time-adaptive windup lead.</summary>
<div class="doc-comment">
<p>F273B + F278A: Cycle-time-adaptive windup lead.</p>
<p></p>
<p>The base `effective_windup_lead_s` (30% of duration, clamped [30, 180])</p>
<p>is the static floor. This method returns a longer windup when observed</p>
<p>cycles are slow -- so the windup phase has at least 2 cycles of headroom</p>
<p>for pattern extraction, synthesis, and DuckDB ingest.</p>
<p></p>
<p>Formula (F290):</p>
<p>base = effective_windup_lead_s  (adaptive 20/25/30% ratio)</p>
<p>adapt = max(0, (cycle_time_ema - 8) * 0.5)  # +0.5s per s over 8s cycle</p>
<p>adapt = min(30.0, adapt)         # cap the bonus at 30s</p>
<p>return clamp(base + adapt, 30, 180)</p>
<p></p>
<p>Examples (300s sprint, base=75s, F290 25%):</p>
<p>- cycle_time_ema=5s  -&gt; 75s (no bonus, quick cycles)</p>
<p>- cycle_time_ema=20s -&gt; 81s (+6s bonus)</p>
<p>- cycle_time_ema=60s -&gt; 105s (+30s bonus)</p>
<p></p>
<p>Examples (100s sprint, base=20s, F290 20%):</p>
<p>- cycle_time_ema=5s  -&gt; 30s (floor active since base+bonus &lt; 30)</p>
<p>- cycle_time_ema=30s -&gt; 41s (+11s bonus)</p>
<p>- cycle_time_ema=60s -&gt; 60s (bonus saturates below ceiling)</p>
<p></p>
<p>Always-on, bounded [30, 180], fail-soft (negative cycle_time_ema -&gt; base).</p>
</div>
</details>
</li>
<li><code>_process_chunk_parallel</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_run_hypothesis_export</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F259: Run causal hypothesis generation and export.</summary>
<div class="doc-comment">
<p>Sprint F259: Run causal hypothesis generation and export.</p>
<p></p>
<p>Gate: HLEDAC_ENABLE_HYPOTHESIS=1 and RAM &lt; 70%</p>
<p>Runs after CTI STIX export in the post-export phase.</p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p>- fail-soft: export error must not prevent teardown</p>
<p>- Lazy imports for causal_engine and hypothesis_graph</p>
<p>- RAM check before execution</p>
</div>
</details>
</li>
<li><code>_run_one_cycle</code> (acquisition.py)</li>
<li><code>_make_finding</code> (sidecar_protocol_adapters.py)
<details><summary>Construct a CanonicalFinding-compatible dict from a Fediverse post.</summary>
<div class="doc-comment">
<p>Construct a CanonicalFinding-compatible dict from a Fediverse post.</p>
<p></p>
<p>Accepts a `FediversePost` dataclass (the new contract from</p>
<p>`discovery/fediverse_adapter.search_multiple_instances`) or a raw</p>
<p>dict (legacy path) — both shapes are normalized via</p>
<p>`FediversePost.to_dict()` for downstream `post.get(...)` access.</p>
<p>Fail-soft: any conversion error returns `None` and the sidecar</p>
<p>logs nothing for the dropped post.</p>
</div>
</details>
</li>
<li><code>run_hash_batch</code> (role_based_pools.py)</li>
<li><code>run_regex_batch</code> (role_based_pools.py)</li>
<li><code>sidecar_admission</code> (resource_governor.py)
<details><summary>F204J: Check if a sidecar can be admitted given current memory state.</summary>
<div class="doc-comment">
<p>F204J: Check if a sidecar can be admitted given current memory state.</p>
<p></p>
<p>Returns SidecarAdmission with:</p>
<p>- allowed: True if sidecar should run</p>
<p>- reason: human-readable denial reason</p>
<p>- rss_gib: current RSS in GiB</p>
<p>- uma_state: current UMA state</p>
<p>- estimated_mb: the estimate that was evaluated</p>
<p></p>
<p>Fails soft: returns allowed=True if any check fails.</p>
</div>
</details>
</li>
<li><code>_required_pre_windup_lanes</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F208B: Determine required lanes before windup.</summary>
<div class="doc-comment">
<p>Sprint F208B: Determine required lanes before windup.</p>
<p></p>
<p></p>
<p></p>
<p>Delegates to required_terminal_lanes() from acquisition_strategy,</p>
<p></p>
<p>which owns the canonical terminality policy (not the scheduler).</p>
<p></p>
<p></p>
<p></p>
<p>Returns tuple of required lane names (lowercase).</p>
</div>
</details>
</li>
<li><code>_load_dedup</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Load existing hashes from LMDB at BOOT. Idempotent. Non-blocking via to_thread.</span></li>
<li><code>model_admission</code> (resource_governor.py)
<details><summary>F214R: Canonical model load admission check.</summary>
<div class="doc-comment">
<p>F214R: Canonical model load admission check.</p>
<p>@pending_integration: no confirmed production call sites as of F214R audit.</p>
<p>See tests/test_resource_governor_authority_seal.py::TestPendingIntegrationMarkers.</p>
<p></p>
<p>Returns ModelAdmission with:</p>
<p>- allowed: True if model load is permitted</p>
<p>- reason: human-readable denial reason</p>
<p>- uma_state: current UMA state</p>
<p>- free_uma_gib: available UMA GiB</p>
<p></p>
<p>Note: actual model lifecycle is managed by brain/model_lifecycle.py.</p>
<p>This only checks UMA state suitability for a new load.</p>
<p>Fail-soft: returns allowed=False with "unknown" reason on errors.</p>
</div>
</details>
</li>
<li><code>buffer_ioc</code> (sprint_scheduler_v1_archived.py)
<details><summary>Buffer an IOC into the Arrow batch.</summary>
<div class="doc-comment">
<p>Buffer an IOC into the Arrow batch.</p>
<p></p>
<p></p>
<p></p>
<p>Sprint 8VI §D: IOCScorer final_score zapojeno.</p>
<p></p>
<p>Sprint 8VI §C: Recent IOC ring buffer pro hypothesis feedback.</p>
</div>
</details>
</li>
<li><code>summary</code> (nonfeed_candidate_ledger.py)
<details><summary>Sprint F217E: Compute bounded summary for reporting.</summary>
<div class="doc-comment">
<p>Sprint F217E: Compute bounded summary for reporting.</p>
<p></p>
<p>Returns dict with counts per family, per stage, and key booleans.</p>
<p>Does NOT include full records (prevents payload leakage in reports).</p>
</div>
</details>
</li>
<li><code>_run_export</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Run all four exporters; failure is fail-soft.</span></li>
<li><code>to_dict</code> (shadow_pre_decision.py)</li>
<li><code>_run_pivot_executor_advisory</code> (sprint_advisory_runner.py)
<details><summary>F204C: Execute top pivots from PivotPlanner via AutonomousPivotExecutor.</summary>
<div class="doc-comment">
<p>F204C: Execute top pivots from PivotPlanner via AutonomousPivotExecutor.</p>
<p></p>
<p>Bounded advisory: executor stores derived findings via canonical ingest</p>
<p>and records HypothesisFeedback. Scheduler retains all authority.</p>
<p></p>
<p>Fail-soft: errors never crash the runner.</p>
</div>
</details>
</li>
<li><code>effective_windup_lead_s</code> (sprint_scheduler_v1_archived.py)
<details><summary>F250 + F272A + F273B + F278A + F285 + F290: Adaptive windup that scales</summary>
<div class="doc-comment">
<p>F250 + F272A + F273B + F278A + F285 + F290: Adaptive windup that scales</p>
<p>with sprint duration. Matches the F221-ABORT pre-flight guard formula exactly.</p>
<p></p>
<p>F290: Short sprints get smaller windup overhead to avoid consuming 50-100%</p>
<p>of the sprint budget in windup (F221/F289 abort).</p>
<p>sprint &lt;= 120s -&gt; 20% ratio (e.g. 60s -&gt; 12s windup, 48s active)</p>
<p>sprint &lt;= 300s -&gt; 25% ratio (e.g. 300s -&gt; 75s windup, 225s active)</p>
<p>sprint &gt; 300s  -&gt; 30% ratio (e.g. 600s -&gt; 180s cap, 420s active)</p>
<p>Clamped [15, 180] to allow short sprints to run without F289 abort.</p>
<p></p>
<p>F285: Explicit windup_lead_s (non-default 180.0) passes through directly.</p>
<p>F273B + F288: Aggressive mode → 15% ratio (parallel branches faster).</p>
</div>
</details>
</li>
<li><code>_init_i2p_background</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Background init -- I2PTransport singleton (F250). Fire-and-forget.</span></li>
<li><code>phase_durations_so_far</code> (sprint_lifecycle.py)</li>
<li><code>_record_scheduler_exit</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F207V-A: Record the exact exit path taken by the scheduler.</summary>
<div class="doc-comment">
<p>Sprint F207V-A: Record the exact exit path taken by the scheduler.</p>
<p></p>
<p></p>
<p></p>
<p>Side-effect light -- only updates in-memory telemetry fields.</p>
<p></p>
<p>No network, no DB write, no graph write.</p>
</div>
</details>
</li>
<li><code>_accumulate_findings_to_graph</code> (sprint_scheduler_v1_archived.py)
<details><summary>F198A: Extract IOCs from accepted findings and upsert to graph_service.</summary>
<div class="doc-comment">
<p>F198A: Extract IOCs from accepted findings and upsert to graph_service.</p>
<p></p>
<p></p>
<p></p>
<p>Delegates to SprintGraphAccumulator. Fail-soft: graph errors</p>
<p></p>
<p>must NOT prevent sprint continuation.</p>
<p></p>
<p></p>
<p></p>
<p>Returns:</p>
<p></p>
<p>Number of findings successfully upserted to graph.</p>
</div>
</details>
</li>
<li><code>_get_graph_signal</code> (sprint_scheduler_v1_archived.py)
<details><summary>F198A: Read graph signal at teardown without blocking sprint.</summary>
<div class="doc-comment">
<p>F198A: Read graph signal at teardown without blocking sprint.</p>
<p></p>
<p></p>
<p></p>
<p>Returns graph node/edge stats as a dict, or empty dict on error.</p>
<p></p>
<p>Non-blocking: called inside _build_diagnostic_report which is already</p>
<p></p>
<p>in the export teardown path (not on the critical sprint path).</p>
</div>
</details>
</li>
<li><code>_get_metrics_summary</code> (sprint_scheduler_v1_archived.py)
<details><summary>Get metrics summary for sprint report embedding.</summary>
<div class="doc-comment">
<p>Get metrics summary for sprint report embedding.</p>
<p></p>
<p></p>
<p></p>
<p>Returns lightweight state snapshot: counters/gauges count,</p>
<p></p>
<p>last_rss_mb, persist_available. Fail-soft: returns None if registry</p>
<p></p>
<p>not initialized.</p>
</div>
</details>
</li>
<li><code>_unload_hermes_at_teardown</code> (sprint_scheduler_v1_archived.py)
<details><summary>P12: Unload Hermes engine at sprint teardown via ModelManager.</summary>
<div class="doc-comment">
<p>P12: Unload Hermes engine at sprint teardown via ModelManager.</p>
<p></p>
<p>Bounded lifecycle: loaded at BOOT/WARMUP, released at TEARDOWN.</p>
<p>Uses ModelManager as canonical unload authority.</p>
<p></p>
<p>F273H: Idle-based lazy unload — skip unload if Hermes was recently</p>
<p>used (within _idle_unload_timeout_s window). Keeps model warm for</p>
<p>next sprint when inter-sprint gap &lt; 30 min.</p>
</div>
</details>
</li>
<li><code>plan_pivots</code> (pivot_planner.py)
<details><summary>Generate bounded pivots from accepted findings.</summary>
<div class="doc-comment">
<p>Generate bounded pivots from accepted findings.</p>
<p></p>
<p>Args:</p>
<p>findings: List of CanonicalFinding (or dict-like) objects</p>
<p>graph_stats: Optional graph statistics for scoring</p>
<p>max_pivots: Maximum number of pivots to generate (default MAX_PIVOTS=20)</p>
<p>feedback_summary: Optional dict mapping (pivot_type, ioc_type) to</p>
<p>HypothesisFeedbackSummary for scoring penalties (F203G).</p>
<p>If None or empty, no penalty is applied.</p>
<p></p>
<p>Returns:</p>
<p>List of Pivot objects, sorted by priority (highest first).</p>
<p>Empty list on any error (fail-soft).</p>
</div>
</details>
</li>
<li><code>mark_warmup_done</code> (sprint_lifecycle.py)
<details><summary>COMPAT ALIAS — transitions WARMUP→ACTIVE.</summary>
<div class="doc-comment">
<p>COMPAT ALIAS — transitions WARMUP→ACTIVE.</p>
<p></p>
<p>Canonical: use transition_to(SprintPhase.ACTIVE) directly.</p>
<p>NOTE: start() goes BOOT→WARMUP only. WARMUP→ACTIVE requires this alias</p>
<p>or explicit transition_to(ACTIVE). __main__.py uses this alias directly.</p>
<p></p>
<p>F4 metadata:</p>
<p>future_owner: __main__.py</p>
<p>removal_condition: __main__.py uses transition_to(ACTIVE) directly; or start() gains WARMUP→ACTIVE</p>
<p></p>
<p>Side effect: resets warmup failure counters on all domain circuit breakers.</p>
<p>This ensures warmup/probe failures do not affect production threshold.</p>
</div>
</details>
</li>
<li><code>_drain_pivot_queue</code> (sprint_scheduler_v1_archived.py)
<details><summary>Drain up to max_tasks from pivot queue. Max 8s total deadline.</summary>
<div class="doc-comment">
<p>Drain up to max_tasks from pivot queue. Max 8s total deadline.</p>
<p></p>
<p>Called at end of each ACTIVE cycle.</p>
</div>
</details>
</li>
<li><code>_emit_source_family_event</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_evaluate_family_status</code> (acquisition_strategy.py)
<details><summary>Evaluate the mission status of a single family.</summary>
<div class="doc-comment">
<p>Evaluate the mission status of a single family.</p>
<p></p>
<p>Returns one of: accepted, terminal, provider_failure, memory_skip, pending, missing</p>
</div>
</details>
</li>
<li><code>_evaluate_family_status</code> (__init__.py)
<details><summary>Evaluate the mission status of a single family.</summary>
<div class="doc-comment">
<p>Evaluate the mission status of a single family.</p>
<p></p>
<p>Returns one of: accepted, terminal, provider_failure, memory_skip, pending, missing</p>
</div>
</details>
</li>
<li><code>record_candidates</code> (nonfeed_candidate_ledger.py)
<details><summary>F214: Filter, rank, and record candidates in one call.</summary>
<div class="doc-comment">
<p>F214: Filter, rank, and record candidates in one call.</p>
<p></p>
<p>Combines filter_source_host_only + rank_candidates + add_feed_candidate.</p>
<p>Use this for the final ranking/recording step after deduplication.</p>
<p></p>
<p>Args:</p>
<p>candidates:  Deduplicated list of DomainCandidate</p>
<p>source_url:   Optional source URL for hostname filtering</p>
<p>max_total:    Maximum candidates to return/record</p>
<p></p>
<p>Returns:</p>
<p>Ranked, bounded list of DomainCandidate.</p>
</div>
</details>
</li>
<li><code>evaluate</code> (resource_governor.py)
<details><summary>Evaluate governor decisions for the current cycle.</summary>
<div class="doc-comment">
<p>Evaluate governor decisions for the current cycle.</p>
<p></p>
<p>Returns GovernorDecision with:</p>
<p>- fetch_limit: new FETCH_SEMAPHORE limit</p>
<p>- allow_renderer: True if JS renderer may be used</p>
<p>- allow_model_load: True if model load is permitted</p>
<p>- branch_concurrency: recommended branch parallelism</p>
<p>- reason: human-readable decision rationale</p>
<p>- free_uma_gib: available UMA GiB for QuantizationSelector</p>
<p>- system_used_gib: system memory used in GiB (F265H)</p>
<p>- swap_detected: True if swap &gt; 3.5 GiB (F265H)</p>
<p></p>
<p>Self-applying: calls apply_decision() before returning so all</p>
<p>decision fields (fetch_limit, counters) are propagated to runtime</p>
<p>surfaces. This eliminates the 90% drift problem where evaluate() was</p>
<p>called everywhere but apply_decision() was called only 2×.</p>
<p></p>
<p>Fails soft: returns safe defaults on any error.</p>
</div>
</details>
</li>
<li><code>final_windup_lead_s</code> (sprint_scheduler_v1_archived.py)
<details><summary>F290: Adaptive windup for sprint-end synthesis and graceful shutdown.</summary>
<div class="doc-comment">
<p>F290: Adaptive windup for sprint-end synthesis and graceful shutdown.</p>
<p>Matches effective_windup_lead_s ratio tiers but with [30, 180] floor</p>
<p>(vs [15, 180] for effective — final needs at least 30s for synthesis).</p>
<p></p>
<p>F285: Explicit windup_lead_s (non-default 180.0) passes through directly.</p>
</div>
</details>
</li>
<li><code>effective_cycle_sleep_s</code> (sprint_scheduler_v1_archived.py)
<details><summary>F228G: Adaptive cycle sleep that scales with sprint duration.</summary>
<div class="doc-comment">
<p>F228G: Adaptive cycle sleep that scales with sprint duration.</p>
<p></p>
<p>Short sprints (60-90s) need a much shorter inter-cycle sleep than</p>
<p>long ones (1800s). For very short sprints the 5.0s default sleep</p>
<p>consumes up to 50% of the active window -- making it impossible to</p>
<p>run more than a handful of cycles before windup.</p>
<p></p>
<p>Returns:</p>
<p>- 60s quick (active=30s) -&gt; 1.0s (fits ~25 cycles)</p>
<p>- 300s deep  (active=210s) -&gt; 2.0s (fits ~50 cycles)</p>
<p>- 600s thoro (active=420s) -&gt; 3.0s</p>
<p>- 1800s default (active=1620s) -&gt; 5.0s (preserves pre-F228G behavior)</p>
<p></p>
<p>Bounded: clamp [0.5, 5.0]s to prevent both over-sleep on quick</p>
<p>sprints and ultra-tight loops on long ones.</p>
<p></p>
<p>Fail-safe: if active &lt;= 0, returns 0.5s (minimum).</p>
</div>
</details>
</li>
<li><code>load_source_weights</code> (sprint_scheduler_v1_archived.py)
<details><summary>Load hit-rate history from DuckDB and set source weights.</summary>
<div class="doc-comment">
<p>Load hit-rate history from DuckDB and set source weights.</p>
<p></p>
<p></p>
<p></p>
<p>Bounds: 0.3 - 2.5 (30% floor, 250% ceiling, B.6).</p>
<p></p>
<p>Falls back to defaults on any error.</p>
</div>
</details>
</li>
<li><code>snapshot</code> (sprint_lifecycle.py)
<details><summary>Return a JSON-serializable dict representing the current state.</summary>
<div class="doc-comment">
<p>Return a JSON-serializable dict representing the current state.</p>
<p></p>
<p>DIAGNOSTIC ONLY — this is a read-only snapshot for monitoring,</p>
<p>not a second authority. The authoritative state is the live</p>
<p>_current_phase field and current_phase property.</p>
<p></p>
<p>No Path objects, no open handles — recovery-safe.</p>
</div>
</details>
</li>
<li><code>_init_graph_and_ioc_state</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Phase M: Graph accumulator, IOC graph, lane outcomes, verdict accumulators (21 attrs).</span></li>
<li><code>_maybe_launch_enhanced_research</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Fire-and-forget deep research advisory. Called at TEARDOWN.</span></li>
<li><code>enqueue_hypothesis_pivot</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>upsert_ioc</code> (graph_adapter.py)</li>
<li><code>_init_background_transports</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Phase 3: Initialize background transports - memory pressure, DHT, I2P, Nym, Tor.</span></li>
<li><code>_run_advisory_runner</code> (sprint_scheduler_v1_archived.py)
<details><summary>F206D: Delegate all advisory orchestration to SidecarOrchestrator.</summary>
<div class="doc-comment">
<p>F206D: Delegate all advisory orchestration to SidecarOrchestrator.</p>
<p></p>
<p></p>
<p></p>
<p>SidecarOrchestrator.run_advisory_runner() owns:</p>
<p></p>
<p>1. run_all_advisories (pivot_planner, pivot_executor, resource_governor, analyst_brief)</p>
<p></p>
<p>2. run_ct_to_passivedns_pivot_advisory</p>
<p></p>
<p>3. run_bgp_advisory_sidecar (non-blocking)</p>
<p></p>
<p>4. run_wayback_cdx_deep_sidecar (non-blocking)</p>
<p></p>
<p></p>
<p></p>
<p>This method remains for backward compatibility with any direct callers.</p>
</div>
</details>
</li>
<li><code>_get_prewindup_barrier_report</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint F207Q-A: Read prewindup barrier telemetry for diagnostic report.</summary>
<div class="doc-comment">
<p>Sprint F207Q-A: Read prewindup barrier telemetry for diagnostic report.</p>
<p></p>
<p></p>
<p></p>
<p>Returns dict under acquisition_strategy.prewindup_barrier key.</p>
<p></p>
<p>Fails soft: returns None if barrier was never checked.</p>
</div>
</details>
</li>
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
<li><code>ingest_text_for_candidates</code> (nonfeed_candidate_ledger.py)
<details><summary>F214: Extract domain candidates from text and record as FEED candidates.</summary>
<div class="doc-comment">
<p>F214: Extract domain candidates from text and record as FEED candidates.</p>
<p></p>
<p>Convenience facade that combines extraction + ledger recording.</p>
<p>Returns extracted candidates (for immediate use by caller).</p>
<p></p>
<p>Args:</p>
<p>text:           Text to scan</p>
<p>source_url:     Optional source URL for hostname extraction</p>
<p>source_family:  "PUBLIC" or "FEED"</p>
<p>max_candidates: Max candidates to record per source</p>
<p></p>
<p>Returns:</p>
<p>List of DomainCandidate extracted (may be empty).</p>
</div>
</details>
</li>
<li><code>_should_deprioritize_source</code> (sprint_scheduler_v1_archived.py)
<details><summary>Return True if source should be deprioritized this cycle.</summary>
<div class="doc-comment">
<p>Return True if source should be deprioritized this cycle.</p>
<p></p>
<p></p>
<p></p>
<p>Deprioritization conditions (all bounded, all in-memory):</p>
<p></p>
<p>1. Source is in cooldown -- pushed to end of work list</p>
<p></p>
<p>2. Silent streak &gt;= 4 cycles -- deprioritized but NOT excluded</p>
</div>
</details>
</li>
<li><code>_close_dedup</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Close LMDB at TEARDOWN. Calls flush first.</span></li>
<li><code>_init_nym_background</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Background init -- NymTransport singleton (F250). Fire-and-forget.</span></li>
<li><code>_extract_domains</code> (sidecar_protocol_adapters.py) — <span class="doc-comment-inline">Extract domain IOCs from findings.</span></li>
<li><code>_init_sidecar_orchestrator</code> (scheduler.py) — <span class="doc-comment-inline">Initialize SidecarOrchestrator (fail-soft).</span></li>
<li><code>bump_counter</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint P0-1: increment a hot-path counter by `n` (default 1) on</summary>
<div class="doc-comment">
<p>Sprint P0-1: increment a hot-path counter by `n` (default 1) on</p>
<p>the SoA layout. Returns the new value, or 0 on layout miss.</p>
<p></p>
<p>Usage:</p>
<p>result.bump_counter("cycles_started")         # +1</p>
<p>result.bump_counter("cycles_completed", n=2)  # +2</p>
<p></p>
<p>This is a slightly faster path than `result.cycles_started += 1`</p>
<p>(skips the property setter) and is the recommended migration</p>
<p>target for hot-path counter bumps in a follow-up sprint.</p>
<p></p>
<p>Fail-soft: layout unavailable → returns 0.</p>
</div>
</details>
</li>
<li><code>__init__</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>buffer_finding</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Buffer a finding into the Arrow batch.</span></li>
<li><code>_init_tor_background</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Background init -- TorTransport singleton (F214Q). Fire-and-forget.</span></li>
<li><code>_async_run_live_feed</code> (acquisition.py)</li>
<li><code>__init__</code> (sidecar_orchestrator.py)</li>
<li><code>snapshot</code> (resource_governor.py)
<details><summary>Current state snapshot for dashboard rendering.</summary>
<div class="doc-comment">
<p>Current state snapshot for dashboard rendering.</p>
<p></p>
<p>Issue #22: protected by _snapshot_lock (threading.RLock) to prevent</p>
<p>torn reads when executor threads mutate _ema_branch_timeouts via</p>
<p>record_branch_timeout()/record_branch_success().</p>
</div>
</details>
</li>
<li><code>recommended_tool_mode</code> (sprint_lifecycle.py)</li>
<li><code>__post_init__</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sprint P0-1: lazily allocate the SoA counter layout.</summary>
<div class="doc-comment">
<p>Sprint P0-1: lazily allocate the SoA counter layout.</p>
<p></p>
<p>Invariants:</p>
<p>L.1  Layout is allocated exactly once per instance.</p>
<p>L.2  Allocation failure (IntCounterLayout unavailable or</p>
<p>MemoryError) is fail-soft: layout remains None and</p>
<p>property getters/setters return 0 (counter-only).</p>
<p>L.3  Idempotent — safe to call multiple times.</p>
</div>
</details>
</li>
<li><code>_init_arrow_and_synthesis</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Phase I: Arrow columnar buffer, synthesis, enrichment, evidence, chain (15 attrs).</span></li>
<li><code>_close_metrics_registry</code> (sprint_scheduler_v1_archived.py)
<details><summary>Close metrics registry at TEARDOWN -- force flush prevents tail-loss.</summary>
<div class="doc-comment">
<p>Close metrics registry at TEARDOWN -- force flush prevents tail-loss.</p>
<p></p>
<p></p>
<p></p>
<p>CancelledError is re-raised per GHOST_INVARIANTS.</p>
</div>
</details>
</li>
<li><code>__init__</code> (role_based_pools.py)</li>
<li><code>_build_work_items</code> (sprint_scheduler_v1_archived.py)
<details><summary>Build and tier-sort work items from source list.</summary>
<div class="doc-comment">
<p>Build and tier-sort work items from source list.</p>
<p></p>
<p>Sprint F228G: tier resolution falls back to _DEFAULT_SOURCE_TIER_MAP</p>
<p>before defaulting to SourceTier.OTHER. The five canonical structured</p>
<p>TI feeds (cisa_kev, threatfox_ioc, urlhaus_recent, feodo_ip,</p>
<p>openphish_feed) are mapped to STRUCTURED_TI so they survive prune</p>
<p>mode and produce real work each cycle.</p>
</div>
</details>
</li>
<li><code>_init_forensics</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Initialize forensics enricher and LMDB. Fail-safe -- does not raise.</span></li>
<li><code>_init_multimodal</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Initialize multimodal enricher and LMDB. Fail-safe -- does not raise.</span></li>
<li><code>inject_analyst_workbench</code> (sprint_scheduler_v1_archived.py)
<details><summary>F204E: Inject AnalystWorkbench reference for sprint brief generation.</summary>
<div class="doc-comment">
<p>F204E: Inject AnalystWorkbench reference for sprint brief generation.</p>
<p></p>
<p></p>
<p></p>
<p>Workbench is used at TEARDOWN to generate a model-free analyst brief</p>
<p></p>
<p>summarizing sprint results: what changed, strongest evidence,</p>
<p></p>
<p>next best pivots, and open questions.</p>
<p></p>
<p></p>
<p></p>
<p>All workbench calls are fail-soft -- exception or None workbench -&gt; no-op brief.</p>
</div>
</details>
</li>
<li><code>_resolve_arrow_batch_hard_cap</code> (sprint_scheduler_v1_archived.py)
<details><summary>Resolve Arrow batch hard cap from env or return M1-safe default.</summary>
<div class="doc-comment">
<p>Resolve Arrow batch hard cap from env or return M1-safe default.</p>
<p></p>
<p></p>
<p></p>
<p>F214OPT-D: Prevents unbounded Arrow batch growth after flush failure.</p>
<p></p>
<p>Default is max(2 * _ARROW_FLUSH_N, 2000) = 2000 entries (~10MB range).</p>
<p></p>
<p>Env override: HLEDAC_ARROW_BATCH_HARD_CAP (min 100, max 50000).</p>
</div>
</details>
</li>
<li><code>_run_feed_branch_aggressive</code> (acquisition.py)</li>
<li><code>run_async</code> (sidecar_protocol_adapters.py)</li>
<li><code>run_async</code> (sidecar_protocol_adapters.py)</li>
<li><code>_init_duckdb_store</code> (scheduler.py) — <span class="doc-comment-inline">Initialize DuckDBShadowStore (fail-soft).</span></li>
<li><code>_check_embed_ram_budget</code> (role_based_pools.py)
<details><summary>Check if embedding budget allows new work.</summary>
<div class="doc-comment">
<p>Check if embedding budget allows new work.</p>
<p></p>
<p>M1 8GB: MLX embeddings use Metal VRAM. We cap at 2 concurrent</p>
<p>workers because each embedding batch can use up to 2GB VRAM.</p>
<p></p>
<p>Returns:</p>
<p>True if budget allows, False otherwise.</p>
</div>
</details>
</li>
<li><code>_worker_adjust_consumer</code> (resource_governor.py)
<details><summary>Background consumer that applies worker count changes while holding self._lock.</summary>
<div class="doc-comment">
<p>Background consumer that applies worker count changes while holding self._lock.</p>
<p></p>
<p>This is the ONLY place where self._current_workers is written.</p>
<p>The lock is held only during the actual semaphore update — never blocks</p>
<p>the producer path (evaluate/evaluate_adaptive/apply_decision).</p>
</div>
</details>
</li>
<li><code>_try_enqueue_adjust</code> (resource_governor.py)
<details><summary>Enqueue fetch_limit adjustment with back-pressure on overflow.</summary>
<div class="doc-comment">
<p>Enqueue fetch_limit adjustment with back-pressure on overflow.</p>
<p></p>
<p>P1-2 fix: asyncio.Queue(maxsize=64) replaces unbounded Queue().</p>
<p>On overflow put_nowait drops the message and logs a warning — the</p>
<p>governor's AIMD loop will eventually converge via the next evaluate()</p>
<p>call. This prevents unbounded queue growth during degraded/emergency</p>
<p>mode where evaluate() is called every 5s but _worker_adjust_consumer</p>
<p>may fall behind.</p>
</div>
</details>
</li>
<li><code>add_phase_exit_callback</code> (sprint_lifecycle.py)</li>
<li><code>request_windup</code> (sprint_lifecycle.py)
<details><summary>COMPAT ALIAS — forwards to transition_to(WINDUP).</summary>
<div class="doc-comment">
<p>COMPAT ALIAS — forwards to transition_to(WINDUP).</p>
<p></p>
<p>Canonical: use transition_to(SprintPhase.WINDUP).</p>
<p>Idempotent: skips if already in WINDUP or beyond (matching utils behavior).</p>
<p></p>
<p>F4 metadata:</p>
<p>future_owner: __main__.py</p>
<p>removal_condition: All call-sites use transition_to(WINDUP)</p>
</div>
</details>
</li>
<li><code>upsert_relation</code> (graph_adapter.py)</li>
<li><code>__post_init__</code> (scheduler_result.py)
<details><summary>Sprint P0-1: lazily allocate the SoA counter layout.</summary>
<div class="doc-comment">
<p>Sprint P0-1: lazily allocate the SoA counter layout.</p>
<p></p>
<p>L.1  Allocated exactly once per instance.</p>
<p>L.2  Fail-soft: leaves layout as None on any error.</p>
<p>L.3  Idempotent — safe to call multiple times.</p>
</div>
</details>
</li>
<li><code>record_pivot_outcome</code> (sprint_scheduler_v1_archived.py)
<details><summary>Zaznamenej výsledek pivot tasku jako reward signal pro RL.</summary>
<div class="doc-comment">
<p>Zaznamenej výsledek pivot tasku jako reward signal pro RL.</p>
<p></p>
<p>reward = findings per second (FPS) -- normalizovaný na [0, 1].</p>
</div>
</details>
</li>
<li><code>_tick_metrics_on_cycle_end</code> (sprint_scheduler_v1_archived.py)
<details><summary>Tick metrics at cycle completion -- captures RSS, open FDs.</summary>
<div class="doc-comment">
<p>Tick metrics at cycle completion -- captures RSS, open FDs.</p>
<p></p>
<p></p>
<p></p>
<p>Called once per cycle (not in tight loop). Fail-soft: noop if registry</p>
<p></p>
<p>not initialized. No model load, no model inference.</p>
</div>
</details>
</li>
<li><code>score_source</code> (sprint_scheduler_v1_archived.py)
<details><summary>Compute priority score per B.1 formula.</summary>
<div class="doc-comment">
<p>Compute priority score per B.1 formula.</p>
<p></p>
<p></p>
<p></p>
<p>score(source) = base_tier_weight(source)</p>
<p></p>
<p>* hit_rate_multiplier(source)</p>
<p></p>
<p>* novelty_bonus(source)</p>
</div>
</details>
</li>
<li><code>inject_forensics_enricher</code> (sprint_scheduler_v1_archived.py)
<details><summary>F195C: Inject ForensicsEnricher + LMDB env (external wiring).</summary>
<div class="doc-comment">
<p>F195C: Inject ForensicsEnricher + LMDB env (external wiring).</p>
<p></p>
<p></p>
<p></p>
<p>OWNERSHIP: caller owns enricher lifecycle. Scheduler invokes</p>
<p></p>
<p>enricher.enrich() during finding sidecar processing. LMDB env</p>
<p></p>
<p>is owned by caller and passed here for reference only.</p>
<p></p>
<p>All calls are fail-soft -- exception or None -&gt; no-op.</p>
</div>
</details>
</li>
<li><code>inject_multimodal_enricher</code> (sprint_scheduler_v1_archived.py)
<details><summary>F195C: Inject MultimodalEnricher + LMDB env (external wiring).</summary>
<div class="doc-comment">
<p>F195C: Inject MultimodalEnricher + LMDB env (external wiring).</p>
<p></p>
<p></p>
<p></p>
<p>OWNERSHIP: caller owns enricher lifecycle. Scheduler invokes</p>
<p></p>
<p>enricher.enrich() during finding sidecar processing. LMDB env</p>
<p></p>
<p>is owned by caller and passed here for reference only.</p>
<p></p>
<p>All calls are fail-soft -- exception or None -&gt; no-op.</p>
</div>
</details>
</li>
<li><code>_init_dht_node_background</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Background init -- DHT node singleton (F214). Fire-and-forget.</span></li>
<li><code>prewarm_async</code> (sidecar_orchestrator.py)
<details><summary>ISSUE #22: Parallel pre-warm of SidecarRegistry adapters.</summary>
<div class="doc-comment">
<p>ISSUE #22: Parallel pre-warm of SidecarRegistry adapters.</p>
<p></p>
<p>Runs BEFORE first run_advisory_runner() call to overlap</p>
<p>import costs (academic GLiNER=200ms, dht cryptography=150ms).</p>
<p></p>
<p>Idempotent: only runs once.</p>
</div>
</details>
</li>
<li><code>_init_governor</code> (scheduler.py) — <span class="doc-comment-inline">Initialize M1ResourceGovernor (fail-soft).</span></li>
<li><code>_init_hermes_engine</code> (scheduler.py) — <span class="doc-comment-inline">Initialize Hermes3Engine (fail-soft).</span></li>
<li><code>_init_evidence_log</code> (scheduler.py) — <span class="doc-comment-inline">Initialize EvidenceLog (fail-soft).</span></li>
<li><code>is_winding_down</code> (sprint_lifecycle.py)
<details><summary>COMPAT PROPERTY — True when in WINDUP, EXPORT, or TEARDOWN.</summary>
<div class="doc-comment">
<p>COMPAT PROPERTY — True when in WINDUP, EXPORT, or TEARDOWN.</p>
<p></p>
<p>Canonical: use in_phase(SprintPhase.WINDUP) or current_phase in (WINDUP, EXPORT, TEARDOWN).</p>
<p></p>
<p>DIAGNOSTIC ONLY — this property is intended for read-only shadow paths.</p>
<p>Do NOT use for runtime dispatch or path decisions.</p>
<p></p>
<p>F4 metadata:</p>
<p>future_owner: callers (shadow_* modules)</p>
<p>removal_condition: Callers use in_phase() checks</p>
</div>
</details>
</li>
<li><code>_init_core_state</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Phase A: Core config and basic state (13 attrs).</span></li>
<li><code>is_new_entry</code> (sprint_scheduler_v1_archived.py)
<details><summary>Return True if entry_hash has not been seen in this sprint.</summary>
<div class="doc-comment">
<p>Return True if entry_hash has not been seen in this sprint.</p>
<p></p>
<p>Uses LRU promotion: on hit, entry is moved to most-recently-used</p>
<p>position so it survives longer under eviction pressure.</p>
</div>
</details>
</li>
<li><code>inject_pivot_planner</code> (sprint_scheduler_v1_archived.py)
<details><summary>Inject PivotPlanner reference (F202G advisory pivot ordering).</summary>
<div class="doc-comment">
<p>Inject PivotPlanner reference (F202G advisory pivot ordering).</p>
<p></p>
<p></p>
<p></p>
<p>F202G: planner is ADVISORY ONLY -- scheduler retains all authority.</p>
<p></p>
<p>Planner generates pivot suggestions from findings; scheduler uses them</p>
<p></p>
<p>as advisory ordering input, NOT as new sprint owner.</p>
<p></p>
<p>All planner calls are fail-soft -- exception or None planner -&gt; no-op.</p>
</div>
</details>
</li>
<li><code>inject_privacy_layer</code> (sprint_scheduler_v1_archived.py)
<details><summary>F26X: Inject PrivacyLayer reference for PII gate.</summary>
<div class="doc-comment">
<p>F26X: Inject PrivacyLayer reference for PII gate.</p>
<p></p>
<p>Preferred over self._layer_manager.privacy -- removes the 7-site</p>
<p>lazy init scattering and makes the dependency explicit.</p>
<p></p>
<p>Fallback: if not injected, the helper still consults</p>
<p>self._layer_manager.privacy (legacy path). Never raises --</p>
<p>exception or None -&gt; no-op (same as other inject_* methods).</p>
<p></p>
<p>OWNERSHIP: caller owns the layer. Scheduler uses it for</p>
<p>_run_privacy_gate() before every async_ingest_findings_batch()</p>
<p>call when HLEDAC_ENABLE_PRIVACY_LAYER=1.</p>
</div>
</details>
</li>
<li><code>enqueue_pivot</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_ioc_type_from_value</code> (pivot_planner.py) — <span class="doc-comment-inline">Infer IOC type from value string.</span></li>
<li><code>record_observation</code> (graph_adapter.py)</li>
<li><code>_notify_phase_transition</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">F320: Call phase_transition_callback if phase actually changed.</span></li>
<li><code>hermes_budget_s</code> (sprint_scheduler_v1_archived.py)
<details><summary>F253: Adaptive Hermes synthesis budget = 35% of the active window,</summary>
<div class="doc-comment">
<p>F253: Adaptive Hermes synthesis budget = 35% of the active window,</p>
<p>floored at 30s. Prevents short sprints from starving the synthesis</p>
<p>lane while ensuring long sprints reserve enough budget.</p>
<p></p>
<p>Uses final_windup_lead_s (which reflects MLX vs non-MLX adaptive logic).</p>
<p></p>
<p>Examples:</p>
<p>- 60s quick (active=30s) -&gt; 30 (floor)</p>
<p>- 300s deep non-MLX (active=270s) -&gt; 94 (35%)</p>
<p>- 300s deep MLX     (active=210s) -&gt; 73 (35%)</p>
<p>- 600s thoro  (active=420s) -&gt; 147 (35%)</p>
</div>
</details>
</li>
<li><code>_init_background_tasks</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Phase G: Background tasks, speculative results, OODA loop (13 attrs).</span></li>
<li><code>is_duplicate</code> (sprint_scheduler_v1_archived.py)
<details><summary>Check if (source_type, url, title) was already seen in any sprint.</summary>
<div class="doc-comment">
<p>Check if (source_type, url, title) was already seen in any sprint.</p>
<p></p>
<p>F1.1: Uses Rust BloomFilter for O(1) negative pre-check. On positive</p>
<p>(might-be-seen), falls back to LMDB-backed set check.</p>
</div>
</details>
</li>
<li><code>mark_seen</code> (sprint_scheduler_v1_archived.py)
<details><summary>Mark a finding as seen. Flush happens at WINDUP.</summary>
<div class="doc-comment">
<p>Mark a finding as seen. Flush happens at WINDUP.</p>
<p></p>
<p>F1.1: Inserts into Rust BloomFilter (fast negative pre-check) and</p>
<p>Python set (exact LMDB-backed check).</p>
</div>
</details>
</li>
<li><code>inject_source_economics</code> (sprint_scheduler_v1_archived.py)
<details><summary>F160C: Inject pre-built source economics map (external wiring).</summary>
<div class="doc-comment">
<p>F160C: Inject pre-built source economics map (external wiring).</p>
<p></p>
<p></p>
<p></p>
<p>OWNERSHIP: caller owns the economics map. Scheduler updates it</p>
<p></p>
<p>via _update_source_economics() during sprint execution.</p>
<p></p>
<p>Pass None or empty dict to use scheduler's internal dict (default).</p>
</div>
</details>
</li>
<li><code>inject_duckdb_store</code> (sprint_scheduler_v1_archived.py)
<details><summary>F195: Inject DuckDB store reference (canonical write seam).</summary>
<div class="doc-comment">
<p>F195: Inject DuckDB store reference (canonical write seam).</p>
<p></p>
<p></p>
<p></p>
<p>OWNERSHIP: caller owns the store. Scheduler uses it for</p>
<p></p>
<p>async_ingest_findings_batch() on accepted findings.</p>
<p></p>
<p>All calls are fail-soft -- exception or None -&gt; no-op.</p>
</div>
</details>
</li>
<li><code>to_dict</code> (shadow_pre_decision.py)</li>
<li><code>tick</code> (sprint_lifecycle.py)
<details><summary>Advance the state machine.</summary>
<div class="doc-comment">
<p>Advance the state machine.</p>
<p></p>
<p>Automatically enters WINDUP when remaining_time &lt;= windup_lead_s.</p>
<p>Returns the current phase after ticking.</p>
</div>
</details>
</li>
<li><code>is_windup_phase</code> (sprint_lifecycle.py)
<details><summary>COMPAT ALIAS — forwards to should_enter_windup().</summary>
<div class="doc-comment">
<p>COMPAT ALIAS — forwards to should_enter_windup().</p>
<p></p>
<p>Canonical: use should_enter_windup() directly.</p>
<p></p>
<p>NOTE: This is a time-based heuristic (remaining &lt;= windup_lead_s),</p>
<p>NOT a phase-state check. Use in_phase(SprintPhase.WINDUP) for phase-state.</p>
<p></p>
<p>DIAGNOSTIC ONLY — for read-only shadow paths only.</p>
<p></p>
<p>F4 metadata:</p>
<p>future_owner: synthesis_runner.py</p>
<p>removal_condition: synthesis_runner uses should_enter_windup() from runtime path</p>
</div>
</details>
</li>
<li><code>upsert_ioc</code> (graph_adapter.py)</li>
<li><code>stats</code> (graph_adapter.py) — <span class="doc-comment-inline">Delegate to IOCGraph.graph_stats().</span></li>
<li><code>graph_stats</code> (graph_adapter.py) — <span class="doc-comment-inline">Delegate to IOCGraph.graph_stats().</span></li>
<li><code>pivot</code> (graph_adapter.py)</li>
<li><code>graph_stats</code> (graph_adapter.py) — <span class="doc-comment-inline">STIX graph stats — STIX path.</span></li>
<li><code>export_stix_bundle</code> (graph_adapter.py) — <span class="doc-comment-inline">Export STIX bundle — STIX path.</span></li>
<li><code>_init_dedup_and_lifecycle</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Phase B: Persistent dedup, lifecycle adapter, IOC-aware scoring (7 attrs).</span></li>
<li><code>_get_adaptive_priority</code> (sprint_scheduler_v1_archived.py)
<details><summary>Vrátí EMA reward jako priority modifikátor.</summary>
<div class="doc-comment">
<p>Vrátí EMA reward jako priority modifikátor.</p>
<p></p>
<p>Task types s vyšší historickou yield dostávají vyšší prioritu.</p>
</div>
</details>
</li>
<li><code>inject_prefetch_oracle</code> (sprint_scheduler_v1_archived.py)
<details><summary>Inject PrefetchOracleIntegration reference (advisory prefetch ordering).</summary>
<div class="doc-comment">
<p>Inject PrefetchOracleIntegration reference (advisory prefetch ordering).</p>
<p></p>
<p></p>
<p></p>
<p>F200A: oracle is ADVISORY ONLY -- scheduler retains all authority.</p>
<p></p>
<p>Oracle suggests sort scores; scheduler multiplies them into economics sort key.</p>
<p></p>
<p>All oracle calls are fail-soft -- exception or None oracle -&gt; no-op.</p>
</div>
</details>
</li>
<li><code>_arrow_flush_n</code> (sprint_scheduler_v1_archived.py)
<details><summary>Dynamically resolve Arrow flush N based on UMA state.</summary>
<div class="doc-comment">
<p>Dynamically resolve Arrow flush N based on UMA state.</p>
<p></p>
<p>F26X-I: critical/emergency = 2500, warn = 1500, ok = 1000.</p>
<p>Read from _governor at call time (not init), so late binding is safe.</p>
</div>
</details>
</li>
<li><code>query_sprint_results</code> (sprint_scheduler_v1_archived.py)
<details><summary>DuckDB zero-copy query over Parquet files via Arrow.</summary>
<div class="doc-comment">
<p>DuckDB zero-copy query over Parquet files via Arrow.</p>
<p></p>
<p>DuckDB + pyarrow (no polars): DuckDB's read_parquet() + fetch_arrow_table()</p>
<p>gives zero-copy Arrow record batch → pyarrow table → list[dict].</p>
<p>Polars is NOT needed here — only for in-memory feature engineering (F5.4).</p>
</div>
</details>
</li>
<li><code>to_dict</code> (shadow_pre_decision.py)</li>
<li><code>_build_seed_context</code> (acquisition.py) — <span class="doc-comment-inline">Build seed context from query and acquisition plan.</span></li>
<li><code>_run_first_cycle</code> (scheduler.py) — <span class="doc-comment-inline">Run the first acquisition cycle (feed only, stable mode).</span></li>
<li><code>_get_feedback_penalty</code> (pivot_planner.py)
<details><summary>F203G: Get penalty multiplier for a pivot type + ioc type combination.</summary>
<div class="doc-comment">
<p>F203G: Get penalty multiplier for a pivot type + ioc type combination.</p>
<p></p>
<p>Returns 1.0 (no penalty) if no feedback exists or feedback module unavailable.</p>
</div>
</details>
</li>
<li><code>_sync_adaptive_threshold</code> (resource_governor.py) — <span class="doc-comment-inline">Push memory pressure to Rust adaptive_scheduler for thread pool adaptation.</span></li>
<li><code>transition_to</code> (sprint_lifecycle.py) — <span class="doc-comment-inline">Transition to the given phase if it respects monotonic ordering.</span></li>
<li><code>_transition_to_unlocked</code> (sprint_lifecycle.py)</li>
<li><code>buffer_ioc</code> (graph_adapter.py)</li>
<li><code>update</code> (sprint_scheduler_v1_archived.py)
<details><summary>Batch update multiple fields at once.</summary>
<div class="doc-comment">
<p>Batch update multiple fields at once.</p>
<p></p>
<p>Example:</p>
<p>builder.update(</p>
<p>cycles_started=5,</p>
<p>aborted=True,</p>
<p>abort_reason="timeout"</p>
<p>)</p>
</div>
</details>
</li>
<li><code>_init_pivot_state</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Phase F: Agentic pivot loop — queue, stats, hypothesis tracking (12 attrs).</span></li>
<li><code>_close_forensics</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Close forensics enricher and LMDB at TEARDOWN.</span></li>
<li><code>_close_multimodal</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Close multimodal enricher and LMDB at TEARDOWN.</span></li>
<li><code>_final_phase_fallback</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Fallback for direct calls to _final_phase (e.g. tests).</span></li>
<li><code>inject_stealth_layer</code> (sprint_scheduler_v1_archived.py)
<details><summary>Inject StealthLayer reference (F260, advisory, default-OFF).</summary>
<div class="doc-comment">
<p>Inject StealthLayer reference (F260, advisory, default-OFF).</p>
<p></p>
<p>Caller (core/__main__.py) wires a StealthLayer produced by</p>
<p>layers.get_stealth_layer() unless --no-stealth is set. None injection</p>
<p>is allowed (caller may pass None as a no-op or to clear a previously</p>
<p>injected layer). All advisory call sites are guarded by</p>
<p>`if self._stealth_layer is not None:` and wrapped in try/except</p>
<p>(fail-soft, M1 invariant).</p>
</div>
</details>
</li>
<li><code>_derive_exit_reason</code> (acquisition_strategy.py) — <span class="doc-comment-inline">Derive the canonical mission exit reason.</span></li>
<li><code>_derive_exit_reason</code> (__init__.py) — <span class="doc-comment-inline">Derive the canonical mission exit reason.</span></li>
<li><code>run</code> (sidecar_protocol_adapters.py) — <span class="doc-comment-inline">Fail-soft wrapper that delegates to the federated sidecar adapter.</span></li>
<li><code>_run_ct_to_passivedns_pivot_advisory</code> (sidecar_orchestrator.py)
<details><summary>R5: CT -&gt; PassiveDNS one-hop pivot advisory.</summary>
<div class="doc-comment">
<p>R5: CT -&gt; PassiveDNS one-hop pivot advisory.</p>
<p></p>
<p>Delegates to SprintScheduler._run_ct_to_passivedns_pivot_advisory().</p>
<p>Fail-soft: errors never crash the sprint.</p>
</div>
</details>
</li>
<li><code>__init__</code> (pivot_planner.py)
<details><summary>Initialize pivot planner.</summary>
<div class="doc-comment">
<p>Initialize pivot planner.</p>
<p></p>
<p>Args:</p>
<p>use_model_scoring: If True, use model-backed scoring via tot_integration.</p>
<p>Requires model_lifecycle_manager for model load/unload.</p>
<p>model_lifecycle_manager: Optional model lifecycle manager for model-backed scoring.</p>
<p>Must be provided if use_model_scoring=True.</p>
</div>
</details>
</li>
<li><code>run_hash_sync</code> (role_based_pools.py)</li>
<li><code>run_regex_sync</code> (role_based_pools.py)</li>
<li><code>apply_decision</code> (resource_governor.py)
<details><summary>Apply governor decision to runtime surfaces (advisory only, fail-soft).</summary>
<div class="doc-comment">
<p>Apply governor decision to runtime surfaces (advisory only, fail-soft).</p>
<p></p>
<p>- Updates FETCH_SEMAPHORE limit via queue (Issue #6: lock-free)</p>
<p>- Tracks denied counts for telemetry</p>
</div>
</details>
</li>
<li><code>begin_sprint</code> (sprint_lifecycle.py)
<details><summary>COMPAT ALIAS — forwards to start().</summary>
<div class="doc-comment">
<p>COMPAT ALIAS — forwards to start().</p>
<p></p>
<p>Canonical: use start() directly.</p>
<p>NOTE: start() transitions BOOT→WARMUP only (not to ACTIVE).</p>
<p>Full activation requires: start() then mark_warmup_done() or transition_to(ACTIVE).</p>
<p>This alias exists to support __main__.py cutover without rewriting call-sites.</p>
<p></p>
<p>F4 metadata:</p>
<p>future_owner: __main__.py</p>
<p>removal_condition: All call-sites migrated to .start()</p>
</div>
</details>
</li>
<li><code>request_export</code> (sprint_lifecycle.py)
<details><summary>COMPAT ALIAS — forwards to mark_export_started().</summary>
<div class="doc-comment">
<p>COMPAT ALIAS — forwards to mark_export_started().</p>
<p></p>
<p>Canonical: use mark_export_started() directly.</p>
<p>Idempotent: skips if already in EXPORT or TEARDOWN (matching utils behavior).</p>
<p></p>
<p>F4 metadata:</p>
<p>future_owner: __main__.py</p>
<p>removal_condition: All call-sites use mark_export_started()</p>
</div>
</details>
</li>
<li><code>request_teardown</code> (sprint_lifecycle.py)
<details><summary>COMPAT ALIAS — forwards to mark_teardown_started().</summary>
<div class="doc-comment">
<p>COMPAT ALIAS — forwards to mark_teardown_started().</p>
<p></p>
<p>Canonical: use mark_teardown_started() directly.</p>
<p>Idempotent: skips if already in TEARDOWN (matching request_export/request_windup).</p>
<p></p>
<p>F4 metadata:</p>
<p>future_owner: __main__.py</p>
<p>removal_condition: All call-sites use mark_teardown_started()</p>
</div>
</details>
</li>
<li><code>is_active</code> (sprint_lifecycle.py)
<details><summary>COMPAT PROPERTY — True when in ACTIVE phase.</summary>
<div class="doc-comment">
<p>COMPAT PROPERTY — True when in ACTIVE phase.</p>
<p></p>
<p>Canonical: use in_phase(SprintPhase.ACTIVE) or current_phase == SprintPhase.ACTIVE.</p>
<p></p>
<p>DIAGNOSTIC ONLY — this property is intended for read-only shadow paths.</p>
<p>Do NOT use for runtime dispatch or path decisions.</p>
<p></p>
<p>F4 metadata:</p>
<p>future_owner: callers (shadow_* modules)</p>
<p>removal_condition: Callers use in_phase(SprintPhase.ACTIVE)</p>
</div>
</details>
</li>
<li><code>upsert_relation</code> (graph_adapter.py)</li>
<li><code>__init__</code> (graph_adapter.py)
<details><summary>Initialize facade with DuckDBShadowStore (or GraphAttachmentStore).</summary>
<div class="doc-comment">
<p>Initialize facade with DuckDBShadowStore (or GraphAttachmentStore).</p>
<p></p>
<p>Args:</p>
<p>store: DuckDBShadowStore instance (has _graph_store()) or</p>
<p>GraphAttachmentStore instance directly.</p>
</div>
</details>
</li>
<li><code>update</code> (scheduler_result.py)
<details><summary>Batch update multiple fields at once.</summary>
<div class="doc-comment">
<p>Batch update multiple fields at once.</p>
<p></p>
<p>Example:</p>
<p>builder.update(</p>
<p>cycles_started=5,</p>
<p>aborted=True,</p>
<p>abort_reason="timeout"</p>
<p>)</p>
</div>
</details>
</li>
<li><code>_init_findings_and_prefetch</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Phase L: All findings, prefetch oracle, temporal predictor, correlation cache (10 attrs).</span></li>
<li><code>_ensure_dedup_loaded</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Block until lazy dedup load completes. Call at first cycle entry.</span></li>
<li><code>_ensure_pre_windup_lane_terminal_states</code> (acquisition.py)</li>
<li><code>_check_zero_findings_alert</code> (acquisition.py) — <span class="doc-comment-inline">Check zero-findings alert after each cycle.</span></li>
<li><code>__init__</code> (sidecar_orchestrator.py)</li>
<li><code>compute_eligibility_from_candidates</code> (nonfeed_candidate_ledger.py)
<details><summary>F214: Compute lane eligibility from domain candidates.</summary>
<div class="doc-comment">
<p>F214: Compute lane eligibility from domain candidates.</p>
<p></p>
<p>Facade for compute_lane_eligibility — returns the same dict.</p>
<p></p>
<p>Args:</p>
<p>candidates:  List of DomainCandidate</p>
<p></p>
<p>Returns:</p>
<p>Dict with ct, doh, wayback, passive_dns bools.</p>
</div>
</details>
</li>
<li><code>__init__</code> (resource_governor.py)</li>
<li><code>record_branch_timeout</code> (resource_governor.py)
<details><summary>Record a branch timeout for EMA tracking.</summary>
<div class="doc-comment">
<p>Record a branch timeout for EMA tracking.</p>
<p></p>
<p>Call this wherever branch_timeout_count is incremented.</p>
<p>EMA formula: ema = alpha * 1.0 + (1 - alpha) * ema</p>
<p>with alpha = 0.3 (responsive without hyperreactivity).</p>
<p></p>
<p>Issue #22: _snapshot_lock prevents torn reads/writes when snapshot()</p>
<p>reads _ema_branch_timeouts concurrently.</p>
</div>
</details>
</li>
<li><code>set_deadline_expired_pre_cycle</code> (sprint_lifecycle.py)
<details><summary>F290-Deadline: Signal that hard deadline expired before first cycle.</summary>
<div class="doc-comment">
<p>F290-Deadline: Signal that hard deadline expired before first cycle.</p>
<p></p>
<p>Called by scheduler when _check_hard_deadline() detects deadline expiry</p>
<p>with cycles_started == 0. This allows windup to fire for cleanup even</p>
<p>though first_cycle_ran=False (F290 guarantee is locally overridden for</p>
<p>the specific case of deadline expiry before any cycle ran).</p>
<p></p>
<p>Invariant: first_cycle_ran remains False (cycle never ran).</p>
<p>Invariant: cycles_started remains 0 (tracked by scheduler result).</p>
</div>
</details>
</li>
<li><code>upsert_ioc</code> (graph_adapter.py)</li>
<li><code>buffer_observation</code> (graph_adapter.py)</li>
<li><code>record_observation</code> (graph_adapter.py)</li>
<li><code>record_observation</code> (graph_adapter.py)</li>
<li><code>with_</code> (sprint_scheduler_v1_archived.py)
<details><summary>Generic setter for any field by name.</summary>
<div class="doc-comment">
<p>Generic setter for any field by name.</p>
<p>Use for fields without dedicated with_ methods.</p>
<p></p>
<p>Example:</p>
<p>builder.with_('quantum_path_seeds', ['seed1', 'seed2'])</p>
</div>
</details>
</li>
<li><code>_flush_dedup</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Flush in-memory hashes to LMDB. Called at WINDUP.</span></li>
<li><code>prioritize_sources</code> (sprint_scheduler_v1_archived.py)
<details><summary>Sort candidates by score -- highest first.</summary>
<div class="doc-comment">
<p>Sort candidates by score -- highest first.</p>
<p></p>
<p>Returns list of source_type strings ordered by priority.</p>
</div>
</details>
</li>
<li><code>inject_security_coordinator</code> (sprint_scheduler_v1_archived.py)
<details><summary>F26X+: Inject UniversalSecurityCoordinator for multi-layer security.</summary>
<div class="doc-comment">
<p>F26X+: Inject UniversalSecurityCoordinator for multi-layer security.</p>
<p></p>
<p></p>
<p>Coordinates: StealthEngine, ThreatIntelligence, QuantumCrypto, ZKP.</p>
<p>Security levels: MINIMAL(1) → STANDARD(2) → HIGH(3) → MAXIMUM(4).</p>
<p></p>
<p>OWNERSHIP: caller owns the coordinator. Scheduler uses it for</p>
<p>_run_security_session() in research/aggressive sprint modes.</p>
</div>
</details>
</li>
<li><code>_get_apt_onion_seeder</code> (sprint_scheduler_v1_archived.py)
<details><summary>Lazily instantiate AptOnionSeeder backed by apt_onion_mapping.yaml.</summary>
<div class="doc-comment">
<p>Lazily instantiate AptOnionSeeder backed by apt_onion_mapping.yaml.</p>
<p></p>
<p>ISSUE-5 FIX: Replaces hardcoded _KNOWN_APT_ONION_DOMAINS substring match with</p>
<p>YAML-backed, confidence-scored mapping. Zero-code-update lifecycle: edit</p>
<p>config/apt_onion_mapping.yaml to add/remove/retire actor→domain mappings.</p>
</div>
</details>
</li>
<li><code>_ooda_apt_domain_mapping</code> (sprint_scheduler_v1_archived.py)
<details><summary>Map threat actor names to .onion infrastructure candidates for OODA bootstrap.</summary>
<div class="doc-comment">
<p>Map threat actor names to .onion infrastructure candidates for OODA bootstrap.</p>
<p></p>
<p>ISSUE-5 FIX: Uses AptOnionSeeder (YAML backend) instead of hardcoded dict.</p>
<p>Only returns confirmed + plausible domains (confidence &gt;= 0.7).</p>
<p>No substring match — requires full token match.</p>
</div>
</details>
</li>
<li><code>__init__</code> (scheduler.py)</li>
<li><code>decayed_score</code> (pivot_planner.py)
<details><summary>Apply exponential decay to base_score based on usage history.</summary>
<div class="doc-comment">
<p>Apply exponential decay to base_score based on usage history.</p>
<p>Older pivots and failed pivots lose priority.</p>
</div>
</details>
</li>
<li><code>_adjust_workers_locked</code> (resource_governor.py)
<details><summary>Apply worker count change to concurrency primitives.</summary>
<div class="doc-comment">
<p>Apply worker count change to concurrency primitives.</p>
<p></p>
<p>Called while holding self._lock from _worker_adjust_consumer().</p>
</div>
</details>
</li>
<li><code>with_</code> (scheduler_result.py)
<details><summary>Generic setter for any field by name.</summary>
<div class="doc-comment">
<p>Generic setter for any field by name.</p>
<p>Use for fields without dedicated with_ methods.</p>
<p></p>
<p>Example:</p>
<p>builder.with_('quantum_path_seeds', ['seed1', 'seed2'])</p>
</div>
</details>
</li>
<li><code>acquire</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Acquire a resource lease. Auto-evicts oldest if at capacity.</span></li>
<li><code>set_first_cycle_ran</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">F290: Signal that first acquisition cycle has completed.</span></li>
<li><code>log_source_hit</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>inject_communication_layer</code> (sprint_scheduler_v1_archived.py)
<details><summary>Inject CommunicationLayer reference (F26X-3, advisory, default-OFF).</summary>
<div class="doc-comment">
<p>Inject CommunicationLayer reference (F26X-3, advisory, default-OFF).</p>
<p></p>
<p>Caller (core/__main__.py) wires a CommunicationLayer produced by</p>
<p>layers.get_communication_layer() unless --no-communication is set.</p>
<p>None injection is allowed (caller may pass None as a no-op or to</p>
<p>clear a previously injected layer).</p>
<p>All advisory call sites are guarded by `if self._communication_layer</p>
<p>is not None:` and wrapped in try/except (fail-soft, M1 invariant).</p>
</div>
</details>
</li>
<li><code>inject_ghost_layer</code> (sprint_scheduler_v1_archived.py)
<details><summary>Inject GhostLayer reference (F260, advisory, default-OFF).</summary>
<div class="doc-comment">
<p>Inject GhostLayer reference (F260, advisory, default-OFF).</p>
<p></p>
<p>Caller (core/__main__.py) wires a GhostLayer produced by</p>
<p>layers.get_ghost_layer() unless --no-ghost is set. None injection is</p>
<p>allowed (caller may pass None as a no-op or to clear a previously</p>
<p>injected layer). All advisory call sites are guarded by</p>
<p>`if self._ghost_layer is not None:` and wrapped in try/except</p>
<p>(fail-soft, M1 invariant).</p>
</div>
</details>
</li>
<li><code>inject_prefetch_pipeline</code> (sprint_scheduler_v1_archived.py)
<details><summary>P3-3: Inject ContinuousPrefetchPipeline reference.</summary>
<div class="doc-comment">
<p>P3-3: Inject ContinuousPrefetchPipeline reference.</p>
<p></p>
<p></p>
<p></p>
<p>Pipeline runs producer-consumer pattern for speculative IOC prefetching.</p>
<p>Starts automatically with sprint if injected.</p>
</div>
</details>
</li>
<li><code>get_analyst_brief</code> (sprint_scheduler_v1_archived.py)
<details><summary>F204E: Return the last generated analyst brief.</summary>
<div class="doc-comment">
<p>F204E: Return the last generated analyst brief.</p>
<p></p>
<p></p>
<p></p>
<p>Returns None if no brief was generated or brief generation failed.</p>
</div>
</details>
</li>
<li><code>get_planned_pivots</code> (sprint_scheduler_v1_archived.py)
<details><summary>F202G: Return last planned pivots for diagnostics.</summary>
<div class="doc-comment">
<p>F202G: Return last planned pivots for diagnostics.</p>
<p></p>
<p></p>
<p></p>
<p>Returns empty list if no pivots were planned or planner failed.</p>
</div>
</details>
</li>
<li><code>_buffer_ioc_pivot</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Wrapper: buffer IOC to graph and enqueue for further pivoting.</span></li>
<li><code>_get_effective_max_cycles</code> (acquisition.py) — <span class="doc-comment-inline">Adaptive max_cycles based on cycle_time EMA.</span></li>
<li><code>_finalize_result_truth</code> (acquisition.py)</li>
<li><code>run_async</code> (sidecar_protocol_adapters.py)</li>
<li><code>_extract_base_url</code> (sidecar_protocol_adapters.py) — <span class="doc-comment-inline">Extract base URL from query string.</span></li>
<li><code>_looks_like_domain</code> (pivot_planner.py) — <span class="doc-comment-inline">Check if value looks like a domain name.</span></li>
<li><code>_is_heavy_blocked</code> (sidecar_bus.py) — <span class="doc-comment-inline">Return (blocked, reason) if a heavy sidecar should be skipped due to RAM pressure.</span></li>
<li><code>record_branch_success</code> (resource_governor.py)
<details><summary>Record a successful branch completion for EMA decay.</summary>
<div class="doc-comment">
<p>Record a successful branch completion for EMA decay.</p>
<p></p>
<p>Decays the EMA toward 0: ema = (1 - alpha) * ema</p>
<p></p>
<p>Issue #22: _snapshot_lock prevents torn reads/writes when snapshot()</p>
<p>reads _ema_branch_timeouts concurrently.</p>
</div>
</details>
</li>
<li><code>buffer_ioc</code> (graph_adapter.py)</li>
<li><code>pivot</code> (graph_adapter.py)</li>
<li><code>buffer_ioc</code> (graph_adapter.py)</li>
<li><code>pivot</code> (graph_adapter.py)</li>
<li><code>find_connected</code> (graph_adapter.py) — <span class="doc-comment-inline">Graph traversal — analytics path (_ioc_graph).</span></li>
<li><code>upsert_ioc_batch</code> (graph_adapter.py) — <span class="doc-comment-inline">Batch upsert IOCs — analytics path.</span></li>
<li><code>find_connected_batch</code> (graph_adapter.py) — <span class="doc-comment-inline">Batch graph traversal — analytics path.</span></li>
<li><code>get_top_nodes_by_degree</code> (graph_adapter.py) — <span class="doc-comment-inline">Top nodes by degree — analytics path.</span></li>
<li><code>export_edge_list</code> (graph_adapter.py) — <span class="doc-comment-inline">Export edge list — analytics path.</span></li>
<li><code>stats</code> (graph_adapter.py) — <span class="doc-comment-inline">Graph stats — analytics path.</span></li>
<li><code>flush_buffers</code> (graph_adapter.py) — <span class="doc-comment-inline">Flush buffered IOCs — truth-write path.</span></li>
<li><code>_release</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Internal release — called by ResourceLease.release() or evict.</span></li>
<li><code>cleanup</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Explicit cleanup — deterministic, no GC dependency. Idempotent.</span></li>
<li><code>get_lane_stats</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_run_bgp_advisory_sidecar</code> (sidecar_orchestrator.py) — <span class="doc-comment-inline">F234: BGP advisory sidecar for ASN/path analysis. Fail-soft.</span></li>
<li><code>_run_wayback_cdx_deep_sidecar</code> (sidecar_orchestrator.py) — <span class="doc-comment-inline">F234: Deep Wayback CDX analysis for URL history. Fail-soft.</span></li>
<li><code>to_source_family_outcomes</code> (sidecar_bus.py)</li>
<li><code>upsert_relation</code> (graph_adapter.py)</li>
<li><code>checkpoint</code> (graph_adapter.py) — <span class="doc-comment-inline">Flush WAL — analytics path.</span></li>
<li><code>release</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_init_duckdb_pipeline</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Phase C: DuckDB write pipeline — producer-consumer queue (5 attrs).</span></li>
<li><code>_enqueue_duckdb_write</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">F285: Enqueue a DuckDB write batch. Returns True if enqueued, False if queue full.</span></li>
<li><code>_run_enhanced_research_async</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Async wrapper -- runs deep research advisory with 180s timeout.</span></li>
<li><code>get_prefetch_pipeline_stats</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">P3-3: Return pipeline statistics if pipeline is injected.</span></li>
<li><code>is_diagnostic_only</code> (shadow_pre_decision.py)
<details><summary>PreDecisionSummary is DIAGNOSTIC ONLY — not a truth store.</summary>
<div class="doc-comment">
<p>PreDecisionSummary is DIAGNOSTIC ONLY — not a truth store.</p>
<p></p>
<p>This class method confirms the artifact must NOT be written</p>
<p>to production ledgers or used as runtime truth.</p>
<p>Must NOT participate in control flow decisions.</p>
</div>
</details>
</li>
<li><code>_ensure_nonfeed_predispatch_before_finalization</code> (acquisition.py)</li>
<li><code>_ensure_mandatory_nonfeed_before_return</code> (acquisition.py)</li>
<li><code>_extract_cids</code> (sidecar_protocol_adapters.py) — <span class="doc-comment-inline">Extract IPFS CIDs from findings.</span></li>
<li><code>_extract_targets</code> (sidecar_protocol_adapters.py) — <span class="doc-comment-inline">Extract domains/emails from findings for leak search.</span></li>
<li><code>_looks_like_ip</code> (pivot_planner.py) — <span class="doc-comment-inline">Check if value looks like an IP address.</span></li>
<li><code>_check_db_ram_budget</code> (role_based_pools.py)
<details><summary>Check if DuckDB budget allows new work.</summary>
<div class="doc-comment">
<p>Check if DuckDB budget allows new work.</p>
<p></p>
<p>M1 8GB: DuckDB in-process uses ~100MB per connection.</p>
<p>We cap at 2 concurrent writers.</p>
</div>
</details>
</li>
<li><code>_ensure_consumer_running</code> (resource_governor.py)
<details><summary>Start the worker-adjust consumer task if not already running.</summary>
<div class="doc-comment">
<p>Start the worker-adjust consumer task if not already running.</p>
<p></p>
<p>Called by evaluate() / evaluate_adaptive() / apply_decision() before</p>
<p>enqueuing a request. Idempotent — safe to call multiple times.</p>
</div>
</details>
</li>
<li><code>remove_phase_exit_callback</code> (sprint_lifecycle.py)</li>
<li><code>request_abort</code> (sprint_lifecycle.py)
<details><summary>Signal that the sprint should abort.</summary>
<div class="doc-comment">
<p>Signal that the sprint should abort.</p>
<p></p>
<p>Does NOT add a new phase — abort flags are tracked separately.</p>
<p>The manager can transition directly to TEARDOWN via transition_to.</p>
</div>
</details>
</li>
<li><code>mark_teardown_started</code> (sprint_lifecycle.py)</li>
<li><code>entered_phase_at</code> (sprint_lifecycle.py)
<details><summary>Monotonic timestamp when the given phase was first entered.</summary>
<div class="doc-comment">
<p>Monotonic timestamp when the given phase was first entered.</p>
<p></p>
<p>Returns None if the phase has never been reached.</p>
<p></p>
<p>DIAGNOSTIC ONLY — read-only seam for observability.</p>
</div>
</details>
</li>
<li><code>remaining_time</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">runtime: remaining_time(). Fallback: 0.0.</span></li>
<li><code>is_terminal</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">runtime: is_terminal(). Fallback: _current_phase == TEARDOWN.</span></li>
<li><code>recommended_tool_mode</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">runtime: recommended_tool_mode(). Fallback: 'normal'.</span></li>
<li><code>_init_planner_and_advisory</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Phase O: Pivot planner and advisory state (5 attrs).</span></li>
<li><code>_is_source_in_cooldown</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">True if source is in bounded cooldown and cycle hasn't exceeded it.</span></li>
<li><code>_notify_governor_branch_timeout</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">F2-2: Notify governor of branch timeout for EMA tracking.</span></li>
<li><code>_notify_governor_branch_success</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">F2-2: Notify governor of successful branch completion for EMA decay.</span></li>
<li><code>inject_temporal_predictor</code> (sprint_scheduler_v1_archived.py)
<details><summary>P3-2: Inject TemporalIOCPredictor reference.</summary>
<div class="doc-comment">
<p>P3-2: Inject TemporalIOCPredictor reference.</p>
<p></p>
<p>Predictor observes findings for time-of-day pattern learning</p>
<p>and provides predict_next_iocs() for ContinuousPrefetchPipeline.</p>
</div>
</details>
</li>
<li><code>get_speculative_dns</code> (sprint_scheduler_v1_archived.py)
<details><summary>Retrieve prefetched DNS results for a domain.</summary>
<div class="doc-comment">
<p>Retrieve prefetched DNS results for a domain.</p>
<p></p>
<p>Returns IP list if prefetch hit, None if miss/unresolved.</p>
<p>Used by pivot planner to skip redundant DNS lookups.</p>
</div>
</details>
</li>
<li><code>_ensure_dedup_loaded</code> (acquisition.py) — <span class="doc-comment-inline">Ensure lazy dedup is loaded before first cycle.</span></li>
<li><code>_check_hard_deadline</code> (acquisition.py) — <span class="doc-comment-inline">Returns False if hard deadline exceeded.</span></li>
<li><code>_check_prewindup_barrier_sync</code> (acquisition.py)</li>
<li><code>_flush_dedup</code> (acquisition.py) — <span class="doc-comment-inline">Flush dedup at WINDUP entry.</span></li>
<li><code>_maybe_export_partial</code> (acquisition.py)</li>
<li><code>_feed_dominance_should_fetch</code> (acquisition.py)</li>
<li><code>_extract_search_terms</code> (sidecar_protocol_adapters.py) — <span class="doc-comment-inline">Extract domain/IOC terms from findings for Fediverse search.</span></li>
<li><code>run_async</code> (sidecar_protocol_adapters.py)</li>
<li><code>_extract_search_terms</code> (sidecar_protocol_adapters.py) — <span class="doc-comment-inline">Extract domain/IOC terms from findings for Gist search.</span></li>
<li><code>_run_ipfs_discovery_sidecar</code> (sidecar_orchestrator.py) — <span class="doc-comment-inline">F229: IPFS discovery — fetch unindexed content from IPFS network. Fail-soft.</span></li>
<li><code>_run_onion_discovery_sidecar</code> (sidecar_orchestrator.py) — <span class="doc-comment-inline">F251: Dark web .onion discovery via Tor. Fail-soft.</span></li>
<li><code>_run_i2p_discovery_sidecar</code> (sidecar_orchestrator.py) — <span class="doc-comment-inline">F2P: I2P .i2p discovery via I2P transport. Fail-soft.</span></li>
<li><code>_run_bgp_enrichment_sidecar</code> (sidecar_orchestrator.py) — <span class="doc-comment-inline">F229: BGP enrichment — AS path analysis for IP/ASN in query. Fail-soft.</span></li>
<li><code>_run_commoncrawl_sidecar</code> (sidecar_orchestrator.py) — <span class="doc-comment-inline">F250F: CommonCrawl CDX domain discovery. Fail-soft.</span></li>
<li><code>_run_banner_grab_sidecar</code> (sidecar_orchestrator.py) — <span class="doc-comment-inline">F229: Banner grab — active TCP probing for service fingerprinting. Fail-soft.</span></li>
<li><code>_run_dht_sidecar</code> (sidecar_orchestrator.py) — <span class="doc-comment-inline">F214Q: DHT torrent discovery via BitTorrent DHT network. Fail-soft.</span></li>
<li><code>_run_digital_ghost_sidecar</code> (sidecar_orchestrator.py) — <span class="doc-comment-inline">F3FORENSICS: Digital ghost detection on file artifacts. Fail-soft.</span></li>
<li><code>_run_steganography_sidecar</code> (sidecar_orchestrator.py) — <span class="doc-comment-inline">F3FORENSICS: Steganography detection on image artifacts. Fail-soft.</span></li>
<li><code>_run_ti_feed_sidecar</code> (sidecar_orchestrator.py) — <span class="doc-comment-inline">F252: TI feed advisory sidecar (NVD + CISA KEV). Fail-soft.</span></li>
<li><code>_prewarm_hermes</code> (scheduler.py) — <span class="doc-comment-inline">Prewarm Hermes model in background.</span></li>
<li><code>_looks_like_domain</code> (pivot_planner.py) — <span class="doc-comment-inline">Check if value looks like a domain name.</span></li>
<li><code>_deduplicate_pivots</code> (pivot_planner.py) — <span class="doc-comment-inline">Deduplicate pivots by (pivot_type, ioc_type, ioc_value), keeping highest score per type.</span></li>
<li><code>_check_gathered</code> (sidecar_bus.py)
<details><summary>Verify no unexpected exceptions leaked through gather(return_exceptions=True).</summary>
<div class="doc-comment">
<p>Verify no unexpected exceptions leaked through gather(return_exceptions=True).</p>
<p>GHOST_INVARIANT: called after every asyncio.gather with return_exceptions=True.</p>
</div>
</details>
</li>
<li><code>_get_model_status</code> (resource_governor.py) — <span class="doc-comment-inline">Read-only model status from canonical lifecycle API.</span></li>
<li><code>mark_export_started</code> (sprint_lifecycle.py)</li>
<li><code>has_reached_phase</code> (sprint_lifecycle.py)
<details><summary>True when the given phase has ever been entered (including current).</summary>
<div class="doc-comment">
<p>True when the given phase has ever been entered (including current).</p>
<p></p>
<p>DIAGNOSTIC ONLY — read-only seam for observability.</p>
<p>Does NOT mutate state. Does not check ordering.</p>
</div>
</details>
</li>
<li><code>__init__</code> (graph_adapter.py)
<details><summary>Initialize adapter with existing DuckPGQGraph.</summary>
<div class="doc-comment">
<p>Initialize adapter with existing DuckPGQGraph.</p>
<p></p>
<p>Args:</p>
<p>graph: DuckPGQGraph instance to wrap</p>
</div>
</details>
</li>
<li><code>__init__</code> (graph_adapter.py)
<details><summary>Initialize adapter with existing IOCGraph.</summary>
<div class="doc-comment">
<p>Initialize adapter with existing IOCGraph.</p>
<p></p>
<p>Args:</p>
<p>graph: IOCGraph instance to wrap</p>
</div>
</details>
</li>
<li><code>tick</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">runtime: tick() returns SprintPhase. Fallback: 'UNKNOWN' phase string.</span></li>
<li><code>set_pre_loop_cost_s</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">F288: Set pre_loop_cost_s on the underlying lifecycle if supported.</span></li>
<li><code>set_windup_lead_s</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">O4-FIX: Set windup_lead_s on the underlying lifecycle if supported.</span></li>
<li><code>set_deadline_expired_pre_cycle</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">F290-Deadline: Signal that hard deadline expired before first cycle.</span></li>
<li><code>_abort_requested</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_abort_reason</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_init_pending_extractions</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Phase E: In-flight pattern-extraction tracker — F273C bounded ring (3 attrs).</span></li>
<li><code>_init_hermes_engine</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Phase J: Hermes engine, memory manager, M1 governor, fetch semaphore (5 attrs).</span></li>
<li><code>request_immediate_abort</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Sprint 8RA: Request immediate abort (called from UMA EMERGENCY callback).</span></li>
<li><code>_update_latency_ema</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Update EMA for domain fetch latency. Bounded to _MAX_FETCH_LATENCY_EMA entries.</span></li>
<li><code>inject_ioc_graph</code> (sprint_scheduler_v1_archived.py)
<details><summary>Inject IOCGraph reference for pivot operations.</summary>
<div class="doc-comment">
<p>Inject IOCGraph reference for pivot operations.</p>
<p></p>
<p>F300-GRAPH: DuckPGQGraph is the sole canonical graph backend.</p>
<p>KuzuGraphBridge wiring removed — it is no longer used.</p>
</div>
</details>
</li>
<li><code>get_graph</code> (sprint_scheduler_v1_archived.py)
<details><summary>Get IOC graph for read operations (stats, export, injection).</summary>
<div class="doc-comment">
<p>Get IOC graph for read operations (stats, export, injection).</p>
<p></p>
<p>Returns the DuckPGQGraph instance used for analytics.</p>
<p>Used by windup_engine and other consumers that need graph access.</p>
</div>
</details>
</li>
<li><code>_get_duckdb_con</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Singleton DuckDB connection -- initialized once.</span></li>
<li><code>__post_init__</code> (acquisition_strategy.py)</li>
<li><code>__post_init__</code> (__init__.py)</li>
<li><code>_drain_pending_pattern_extractions</code> (acquisition.py)</li>
<li><code>_run_ioc_cooccurrence_sidecar</code> (acquisition.py)</li>
<li><code>_run_epistemic_gap_advisory</code> (acquisition.py)</li>
<li><code>is_available</code> (sidecar_protocol_adapters.py) — <span class="doc-comment-inline">Env-gated check delegating to the federated module's gate.</span></li>
<li><code>__post_init__</code> (nonfeed_candidate_ledger.py)</li>
<li><code>add_feed_candidate</code> (nonfeed_candidate_ledger.py)
<details><summary>F214: Record a FEED-sourced domain candidate for non-domain queries.</summary>
<div class="doc-comment">
<p>F214: Record a FEED-sourced domain candidate for non-domain queries.</p>
<p></p>
<p>Adds to FEED family with stage=discovered.</p>
</div>
</details>
</li>
<li><code>start</code> (sprint_lifecycle.py) — <span class="doc-comment-inline">Transition from BOOT → WARMUP and record start time.</span></li>
<li><code>is_terminal</code> (sprint_lifecycle.py) — <span class="doc-comment-inline">True when the manager has reached TEARDOWN or has aborted and completed.</span></li>
<li><code>current_phase</code> (sprint_lifecycle.py)
<details><summary>Public read-only access to current phase.</summary>
<div class="doc-comment">
<p>Public read-only access to current phase.</p>
<p></p>
<p>Canonical alternative to direct _current_phase field access.</p>
</div>
</details>
</li>
<li><code>in_phase</code> (sprint_lifecycle.py)
<details><summary>True when manager is in the given phase.</summary>
<div class="doc-comment">
<p>True when manager is in the given phase.</p>
<p></p>
<p>Convenience helper — equivalent to current_phase == phase.</p>
</div>
</details>
</li>
<li><code>_is_valid_transition</code> (sprint_lifecycle.py) — <span class="doc-comment-inline">Allow TEARDOWN from any phase (abort path).</span></li>
<li><code>stats</code> (graph_adapter.py) — <span class="doc-comment-inline">Delegate to DuckPGQGraph.stats().</span></li>
<li><code>graph_stats</code> (graph_adapter.py) — <span class="doc-comment-inline">DuckPGQGraph: graph_stats (F271).</span></li>
<li><code>find_connected</code> (graph_adapter.py)</li>
<li><code>upsert_ioc_batch</code> (graph_adapter.py) — <span class="doc-comment-inline">Delegate batch upsert to IOCGraph.upsert_ioc_batch().</span></li>
<li><code>find_connected_batch</code> (graph_adapter.py)</li>
<li><code>_init_source_tracking</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Phase D: Source quality feedback and feed dominance tracking (4 attrs).</span></li>
<li><code>request_early_windup</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Sprint 8RA: Request early wind-down (called from UMA CRITICAL callback).</span></li>
<li><code>_final_phase</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Mark teardown on lifecycle.</span></li>
<li><code>filter</code> (sprint_entrypoint.py)</li>
<li><code>_mission_cap_active</code> (acquisition_strategy.py) — <span class="doc-comment-inline">F227D: Return True when mission-aware cap should be evaluated.</span></li>
<li><code>reset</code> (sidecar_orchestrator.py) — <span class="doc-comment-inline">Clear in-memory state. Called on sprint teardown.</span></li>
<li><code>__new__</code> (scheduler.py)</li>
<li><code>inject_evidence_log</code> (scheduler.py) — <span class="doc-comment-inline">Inject a pre-initialized EvidenceLog (wraps in InitResult.success).</span></li>
<li><code>inject_duckdb_store</code> (scheduler.py) — <span class="doc-comment-inline">Inject a pre-initialized DuckDBShadowStore (wraps in InitResult.success).</span></li>
<li><code>_extract_domain_from_url</code> (pivot_planner.py) — <span class="doc-comment-inline">Extract domain from URL.</span></li>
<li><code>find_connected</code> (graph_adapter.py) — <span class="doc-comment-inline">Delegate graph traversal to DuckPGQGraph.find_connected().</span></li>
<li><code>upsert_ioc_batch</code> (graph_adapter.py) — <span class="doc-comment-inline">Delegate batch upsert to DuckPGQGraph.upsert_ioc_batch().</span></li>
<li><code>find_connected_batch</code> (graph_adapter.py) — <span class="doc-comment-inline">Delegate batch traversal to DuckPGQGraph.find_connected_batch().</span></li>
<li><code>get_top_nodes_by_degree</code> (graph_adapter.py) — <span class="doc-comment-inline">Delegate to DuckPGQGraph.get_top_nodes_by_degree().</span></li>
<li><code>export_edge_list</code> (graph_adapter.py) — <span class="doc-comment-inline">Delegate to DuckPGQGraph.export_edge_list().</span></li>
<li><code>checkpoint</code> (graph_adapter.py) — <span class="doc-comment-inline">Delegate to DuckPGQGraph.checkpoint().</span></li>
<li><code>flush_buffers</code> (graph_adapter.py) — <span class="doc-comment-inline">DuckPGQGraph: flush via flush_buffers (F272).</span></li>
<li><code>export_stix_bundle</code> (graph_adapter.py) — <span class="doc-comment-inline">DuckPGQGraph: STIX export via DuckDB (F271).</span></li>
<li><code>flush_buffers</code> (graph_adapter.py) — <span class="doc-comment-inline">Delegate buffer flush to IOCGraph.flush_buffers().</span></li>
<li><code>export_stix_bundle</code> (graph_adapter.py) — <span class="doc-comment-inline">Delegate STIX export to IOCGraph.export_stix_bundle().</span></li>
<li><code>__init__</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>release</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Release resource — idempotent, safe to call multiple times.</span></li>
<li><code>__init__</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>allocate</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>get_utilization</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>__setattr__</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_init_fetch_latency_ema</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Phase H: Adaptive timeout EMA — per-domain latency learning (3 attrs).</span></li>
<li><code>_init_layers</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Phase N: Privacy, stealth, ghost layers + DOH adapter + circuit breakers (3 attrs).</span></li>
<li><code>_init_target_and_metrics</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Phase P: Target memory service, analyst workbench, metrics registry (2 attrs).</span></li>
<li><code>get_adaptive_timeout</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Get adaptive timeout based on EMA latency. Clamped to [5, 30]s.</span></li>
<li><code>inject_policy_manager</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Inject SprintPolicyManager reference (opt-in RL layer).</span></li>
<li><code>is_mission_profile</code> (acquisition_strategy.py) — <span class="doc-comment-inline">Return True when the profile is any nonfeed_diagnostic variant.</span></li>
<li><code>is_mission_profile</code> (__init__.py) — <span class="doc-comment-inline">Return True when the profile is any nonfeed_diagnostic variant.</span></li>
<li><code>__repr__</code> (scheduler.py)</li>
<li><code>__init__</code> (sidecar_bus.py)</li>
<li><code>bump_counter</code> (scheduler_result.py)</li>
<li><code>__setattr__</code> (scheduler_result.py)</li>
<li><code>__init__</code> (sprint_advisory_runner.py)</li>
<li><code>obj</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>__init__</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>__init__</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>cycles_started</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>cycles_completed</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>consecutive_empty_cycles</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>unique_entry_hashes_seen</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>duplicate_entry_hashes_skipped</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>hard_deadline_checked_count</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>windup_guard_call_count</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>windup_guard_callback_supplied_count</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>windup_guard_callback_executed_count</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>policy_quality_feedback_calls</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>policy_quality_feedback_errors</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>ipfs_cids_attempted</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>multimodal_enriched_findings</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>feed_suppression_count</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>forensics_enriched_ct_findings</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>acquisition_lanes_skipped</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_set</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Internal setter — bypasses __setattr__ for speed.</span></li>
<li><code>__getattr__</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>is_available</code> (sidecar_protocol_adapters.py) — <span class="doc-comment-inline">Available only when feature flag is enabled.</span></li>
<li><code>_run_gopher_sidecar</code> (sidecar_orchestrator.py) — <span class="doc-comment-inline">F214R: Gopher URL discovery. No-op until GopherLane is implemented.</span></li>
<li><code>inject_prefetch_oracle</code> (scheduler.py)</li>
<li><code>inject_prefetch_pipeline</code> (scheduler.py)</li>
<li><code>inject_temporal_predictor</code> (scheduler.py)</li>
<li><code>inject_pivot_planner</code> (scheduler.py)</li>
<li><code>inject_analyst_workbench</code> (scheduler.py)</li>
<li><code>inject_forensics_enricher</code> (scheduler.py)</li>
<li><code>inject_enrichment_services</code> (scheduler.py)</li>
<li><code>inject_privacy_layer</code> (scheduler.py)</li>
<li><code>inject_ioc_graph</code> (scheduler.py)</li>
<li><code>record_success</code> (pivot_planner.py) — <span class="doc-comment-inline">Record a successful pivot use.</span></li>
<li><code>record_failure</code> (pivot_planner.py) — <span class="doc-comment-inline">Record a failed pivot use.</span></li>
<li><code>_pivot_type_for_ioc</code> (pivot_planner.py) — <span class="doc-comment-inline">Map IOC type to pivot type.</span></li>
<li><code>_get_embed_lock</code> (role_based_pools.py)</li>
<li><code>_get_db_lock</code> (role_based_pools.py)</li>
<li><code>_get_hash_lock</code> (role_based_pools.py)</li>
<li><code>_get_regex_lock</code> (role_based_pools.py)</li>
<li><code>_get_async_io_lock</code> (role_based_pools.py)</li>
<li><code>add</code> (nonfeed_candidate_ledger.py)</li>
<li><code>records</code> (nonfeed_candidate_ledger.py) — <span class="doc-comment-inline">Return immutable snapshot of all records (oldest first).</span></li>
<li><code>count_by_stage</code> (nonfeed_candidate_ledger.py) — <span class="doc-comment-inline">Count records with given stage.</span></li>
<li><code>count_by_family</code> (nonfeed_candidate_ledger.py) — <span class="doc-comment-inline">Count records with given family.</span></li>
<li><code>count_accepted</code> (nonfeed_candidate_ledger.py) — <span class="doc-comment-inline">Count accepted=True records.</span></li>
<li><code>count_quarantine</code> (nonfeed_candidate_ledger.py) — <span class="doc-comment-inline">Count quarantine=True records.</span></li>
<li><code>register</code> (sidecar_bus.py)</li>
<li><code>_is_active_network_blocked</code> (sidecar_bus.py) — <span class="doc-comment-inline">Return (blocked, reason) if an active-network sidecar should be skipped.</span></li>
<li><code>remaining_time</code> (sprint_lifecycle.py) — <span class="doc-comment-inline">Seconds remaining in the sprint (0 if elapsed).</span></li>
<li><code>_remaining_time_unlocked</code> (sprint_lifecycle.py)</li>
<li><code>_set</code> (scheduler_result.py) — <span class="doc-comment-inline">Internal setter — bypasses __setattr__ for speed.</span></li>
<li><code>__getattr__</code> (scheduler_result.py)</li>
<li><code>acquire</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Acquire GraphService instance for this sprint.</span></li>
<li><code>release</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Release GraphService and cleanup resources after sprint.</span></li>
<li><code>release</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Public release API — idempotent.</span></li>
<li><code>consume</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>cycles_started</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>cycles_completed</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>consecutive_empty_cycles</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>unique_entry_hashes_seen</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>duplicate_entry_hashes_skipped</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>hard_deadline_checked_count</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>windup_guard_call_count</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>windup_guard_callback_supplied_count</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>windup_guard_callback_executed_count</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>policy_quality_feedback_calls</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>policy_quality_feedback_errors</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>ipfs_cids_attempted</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>multimodal_enriched_findings</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>feed_suppression_count</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>forensics_enriched_ct_findings</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>acquisition_lanes_skipped</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>build</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Return the constructed SprintSchedulerResult.</span></li>
<li><code>_field_names</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Reflect field names from SprintSchedulerResult at runtime.</span></li>
<li><code>_get_source_economics</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Return economics state for a source, or None if not yet seen.</span></li>
<li><code>_prune_work_items</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Drop ARCHIVE and OTHER tier items when in prune mode.</span></li>
<li><code>_flush_forensics</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Flush forensics LMDB. Called at WINDUP. No-op if not initialized.</span></li>
<li><code>_flush_multimodal</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Flush multimodal LMDB. Called at WINDUP. No-op if not initialized.</span></li>
<li><code>set_novelty_bonus</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">Set novelty bonus: 1.5 if source added new IOC types this sprint.</span></li>
<li><code>inject_enrichment_services</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">F350M: Inject EnrichmentServices (forensics + multimodal unified lifecycle).</span></li>
<li><code>inject_evidence_log</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">F11C: Inject EvidenceLog reference (fail-safe, M1 8GB safe).</span></li>
<li><code>_invalidate_health_cache</code> (sprint_scheduler_v1_archived.py) — <span class="doc-comment-inline">F270-4.3: Invalidate health_check cache (call on sprint start/end).</span></li>
<li><code>is_sentinel</code> (acquisition_strategy.py) — <span class="doc-comment-inline">Return True when all caps are at sentinel (None) — feature fully disabled.</span></li>
<li><code>is_active</code> (acquisition_strategy.py) — <span class="doc-comment-inline">Return True when any cap is configured (non-sentinel).</span></li>
<li><code>_nonfeed_profile_cap_active</code> (acquisition_strategy.py) — <span class="doc-comment-inline">F230D: Return True when nonfeed_diagnostic profile cap should be evaluated.</span></li>
<li><code>kind_counts</code> (acquisition_strategy.py) — <span class="doc-comment-inline">Return counts by non-empty seed kind.</span></li>
<li><code>get_required_families</code> (acquisition_strategy.py) — <span class="doc-comment-inline">Required lane families for nonfeed_diagnostic mission.</span></li>
<li><code>get_optional_families</code> (acquisition_strategy.py) — <span class="doc-comment-inline">Optional lane families for nonfeed_diagnostic mission.</span></li>
<li><code>_family_to_lane</code> (acquisition_strategy.py) — <span class="doc-comment-inline">Map lane family string to AcquisitionLane constant.</span></li>
<li><code>kind_counts</code> (__init__.py) — <span class="doc-comment-inline">Return counts by non-empty seed kind.</span></li>
<li><code>get_required_families</code> (__init__.py) — <span class="doc-comment-inline">Required lane families for nonfeed_diagnostic mission.</span></li>
<li><code>get_optional_families</code> (__init__.py) — <span class="doc-comment-inline">Optional lane families for nonfeed_diagnostic mission.</span></li>
<li><code>_family_to_lane</code> (__init__.py) — <span class="doc-comment-inline">Map lane family string to AcquisitionLane constant.</span></li>
<li><code>_maybe_call_pressure_relief</code> (acquisition.py) — <span class="doc-comment-inline">Call malloc_zone_pressure_relief if governor recommends.</span></li>
<li><code>_prioritize_sources</code> (acquisition.py) — <span class="doc-comment-inline">Re-prioritize sources using latest graph stats.</span></li>
<li><code>run_async</code> (sidecar_protocol_adapters.py)</li>
<li><code>sprint_id</code> (scheduler.py) — <span class="doc-comment-inline">Return _sprint_id (setter stores there, not in result).</span></li>
<li><code>sprint_id</code> (scheduler.py) — <span class="doc-comment-inline">Set sprint_id (backward compat for tests).</span></li>
<li><code>inject_policy_manager</code> (scheduler.py)</li>
<li><code>inject_communication_layer</code> (scheduler.py)</li>
<li><code>inject_stealth_layer</code> (scheduler.py)</li>
<li><code>inject_ghost_layer</code> (scheduler.py)</li>
<li><code>inject_security_coordinator</code> (scheduler.py)</li>
<li><code>__init__</code> (scheduler.py)</li>
<li><code>health_check</code> (scheduler.py) — <span class="doc-comment-inline">Stub health check — returns None (pass).</span></li>
<li><code>get_last_error</code> (pivot_planner.py) — <span class="doc-comment-inline">Return last error message, or None if no error.</span></li>
<li><code>add_ct_quarantine</code> (nonfeed_candidate_ledger.py) — <span class="doc-comment-inline">Add CT quarantine event. quarantine=True, accepted=False, family=CT.</span></li>
<li><code>add_public_event</code> (nonfeed_candidate_ledger.py) — <span class="doc-comment-inline">Add PUBLIC stage machine event.</span></li>
<li><code>add_pivot_discovered</code> (nonfeed_candidate_ledger.py) — <span class="doc-comment-inline">Add PIVOT family discovered event.</span></li>
<li><code>add_quality_rejection</code> (nonfeed_candidate_ledger.py) — <span class="doc-comment-inline">Add quality rejection event (mirrored from quality_rejection_ledger).</span></li>
<li><code>add_provider_failed</code> (nonfeed_candidate_ledger.py) — <span class="doc-comment-inline">Add provider_failed event (e.g., CT/WAYBACK timeout or error).</span></li>
<li><code>__init__</code> (sidecar_bus.py)</li>
<li><code>__init__</code> (sidecar_bus.py)</li>
<li><code>ema_branch_pressure</code> (resource_governor.py) — <span class="doc-comment-inline">Return current EMA branch timeout pressure for telemetry.</span></li>
<li><code>get_pressure</code> (resource_governor.py) — <span class="doc-comment-inline">Get canonical pressure state (UMAGovernor protocol).</span></li>
<li><code>set_first_cycle_ran</code> (sprint_lifecycle.py) — <span class="doc-comment-inline">F290: Signal that first acquisition cycle has completed.</span></li>
<li><code>get_top_nodes_by_degree</code> (graph_adapter.py) — <span class="doc-comment-inline">IOCGraph does not support this — returns []. Use graph_stats().</span></li>
<li><code>export_edge_list</code> (graph_adapter.py) — <span class="doc-comment-inline">IOCGraph does not export edge lists — returns []. Use export_stix_bundle().</span></li>
<li><code>checkpoint</code> (graph_adapter.py) — <span class="doc-comment-inline">IOCGraph does not have a checkpoint — no-op.</span></li>
<li><code>build</code> (scheduler_result.py) — <span class="doc-comment-inline">Return the constructed SprintSchedulerResult.</span></li>
<li><code>_field_names</code> (scheduler_result.py) — <span class="doc-comment-inline">Reflect field names from SprintSchedulerResult at runtime.</span></li>
<li><code>__enter__</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>__exit__</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>__repr__</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>tier_of</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>sorted_tiers</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>summary</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>__init__</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_cycles_started</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_cycles_completed</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_aborted</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_abort_reason</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_final_phase</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_accepted_findings</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_total_pattern_hits</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_unique_entry_hashes_seen</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_duplicate_entry_hashes_skipped</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_consecutive_empty_cycles</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_max_consecutive_empty_cycles</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_entries_per_source</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_hits_per_source</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_export_paths</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_stop_requested</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_synthesis_success</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_synthesis_engine</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_synthesis_findings_count</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_synthesis_text</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_hypotheses_generated</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_public_discovered</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_public_fetched</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_public_matched_patterns</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_public_accepted_findings</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_public_stored_findings</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_public_error</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_ct_log_discovered</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_ct_log_stored</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_ct_log_accepted_findings</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_ct_log_error</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_entered_active_at_monotonic</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_pre_loop_elapsed_s</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_first_cycle_started_at_monotonic</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>with_pre_active_starved</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>has_domain</code> (acquisition_strategy.py)</li>
<li><code>has_ip</code> (acquisition_strategy.py)</li>
<li><code>has_url</code> (acquisition_strategy.py)</li>
<li><code>to_dict</code> (acquisition_strategy.py)</li>
<li><code>to_dict</code> (acquisition_strategy.py)</li>
<li><code>to_dict</code> (acquisition_strategy.py)</li>
<li><code>has_domain</code> (__init__.py)</li>
<li><code>has_ip</code> (__init__.py)</li>
<li><code>has_url</code> (__init__.py)</li>
<li><code>to_dict</code> (__init__.py)</li>
<li><code>to_dict</code> (__init__.py)</li>
<li><code>to_dict</code> (__init__.py)</li>
<li><code>__init__</code> (acquisition.py)</li>
<li><code>inject_multimodal_enricher</code> (scheduler.py)</li>
<li><code>inject_source_economics</code> (scheduler.py)</li>
<li><code>result</code> (scheduler.py)</li>
<li><code>cycles_started_</code> (scheduler_result.py)</li>
<li><code>cycles_completed_</code> (scheduler_result.py)</li>
<li><code>__init__</code> (scheduler_result.py)</li>
<li><code>with_cycles_started</code> (scheduler_result.py)</li>
<li><code>with_cycles_completed</code> (scheduler_result.py)</li>
<li><code>with_aborted</code> (scheduler_result.py)</li>
<li><code>with_abort_reason</code> (scheduler_result.py)</li>
<li><code>with_final_phase</code> (scheduler_result.py)</li>
<li><code>with_accepted_findings</code> (scheduler_result.py)</li>
<li><code>with_total_pattern_hits</code> (scheduler_result.py)</li>
<li><code>with_unique_entry_hashes_seen</code> (scheduler_result.py)</li>
<li><code>with_duplicate_entry_hashes_skipped</code> (scheduler_result.py)</li>
<li><code>with_consecutive_empty_cycles</code> (scheduler_result.py)</li>
<li><code>with_max_consecutive_empty_cycles</code> (scheduler_result.py)</li>
<li><code>with_entries_per_source</code> (scheduler_result.py)</li>
<li><code>with_hits_per_source</code> (scheduler_result.py)</li>
<li><code>with_export_paths</code> (scheduler_result.py)</li>
<li><code>with_stop_requested</code> (scheduler_result.py)</li>
<li><code>with_synthesis_success</code> (scheduler_result.py)</li>
<li><code>with_synthesis_engine</code> (scheduler_result.py)</li>
<li><code>with_synthesis_findings_count</code> (scheduler_result.py)</li>
<li><code>with_synthesis_text</code> (scheduler_result.py)</li>
<li><code>with_hypotheses_generated</code> (scheduler_result.py)</li>
<li><code>with_public_discovered</code> (scheduler_result.py)</li>
<li><code>with_public_fetched</code> (scheduler_result.py)</li>
<li><code>with_public_matched_patterns</code> (scheduler_result.py)</li>
<li><code>with_public_accepted_findings</code> (scheduler_result.py)</li>
<li><code>with_public_stored_findings</code> (scheduler_result.py)</li>
<li><code>with_public_error</code> (scheduler_result.py)</li>
<li><code>with_ct_log_discovered</code> (scheduler_result.py)</li>
<li><code>with_ct_log_stored</code> (scheduler_result.py)</li>
<li><code>with_ct_log_accepted_findings</code> (scheduler_result.py)</li>
<li><code>with_ct_log_error</code> (scheduler_result.py)</li>
<li><code>with_entered_active_at_monotonic</code> (scheduler_result.py)</li>
<li><code>with_pre_loop_elapsed_s</code> (scheduler_result.py)</li>
<li><code>with_first_cycle_started_at_monotonic</code> (scheduler_result.py)</li>
<li><code>with_pre_active_starved</code> (scheduler_result.py)</li>
<li><code>get</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>set</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>bump</code> (sprint_scheduler_v1_archived.py)</li>
</ul>
</details>

<details><summary><strong>Constant</strong> (160)</summary>
<ul>
<li><code>_DECOMPOSE_RULES</code> (nonfeed_seed_runtime.py)</li>
<li><code>_DEAD_SURFACE_DOMAINS</code> (nonfeed_seed_runtime.py)</li>
<li><code>INT_COUNTER_LAYOUT_NAMES</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_DARK_WEB_CLEARNET_SEEDS</code> (nonfeed_seed_runtime.py)</li>
<li><code>_OSINT_KEYWORDS</code> (nonfeed_seed_runtime.py)</li>
<li><code>_PHASE_ORDER</code> (sprint_lifecycle.py)</li>
<li><code>_DEFAULT_SOURCE_TIER_MAP</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_LANES_BY_SEED</code> (nonfeed_seed_runtime.py)</li>
<li><code>SPRINT_TIERS</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>T</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_NONFEED_DIAGNOSTIC_FALLBACK_LANES</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_UNSET</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_ADVISORY_LOG_LRU_MAX</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_MAX_CHUNK_SIZE</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_MAX_CHUNK_CONCURRENCY</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>MAX_LANE_REJECTIONS</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>MAX_GC_STATS</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_TIER_ORDER</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_DEDUP_LMDB_NAME</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_FORENSICS_LMDB_NAME</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>_MULTIMODAL_LMDB_NAME</code> (sprint_scheduler_v1_archived.py)</li>
<li><code>MIN_ACTIVE_WINDOW_S</code> (sprint_entrypoint.py)</li>
<li><code>_CT_CONFIDENCE</code> (source_finding_bridge.py)</li>
<li><code>_CT_SOURCE_TYPE</code> (source_finding_bridge.py)</li>
<li><code>_CT_SALT</code> (source_finding_bridge.py)</li>
<li><code>_WAYBACK_CONFIDENCE</code> (source_finding_bridge.py)</li>
<li><code>_WAYBACK_SOURCE_TYPE</code> (source_finding_bridge.py)</li>
<li><code>_WAYBACK_SALT</code> (source_finding_bridge.py)</li>
<li><code>_PDNS_CONFIDENCE</code> (source_finding_bridge.py)</li>
<li><code>_PDNS_SOURCE_TYPE</code> (source_finding_bridge.py)</li>
<li><code>_PDNS_SALT</code> (source_finding_bridge.py)</li>
<li><code>_DOH_SOURCE_TYPE</code> (source_finding_bridge.py)</li>
<li><code>_DOH_CONFIDENCE</code> (source_finding_bridge.py)</li>
<li><code>_RDAP_SOURCE_TYPE</code> (source_finding_bridge.py)</li>
<li><code>_RDAP_BASE_CONFIDENCE</code> (source_finding_bridge.py)</li>
<li><code>DOMAIN_EXPANSIONS</code> (acquisition_strategy.py)</li>
<li><code>_THREAT_DICTIONARY</code> (acquisition_strategy.py)</li>
<li><code>ACQUISITION_REPORT_SCHEMA_VERSION</code> (acquisition_strategy.py)</li>
<li><code>_CIDV0_RE</code> (acquisition_strategy.py)</li>
<li><code>_CIDV1_BASE32_RE</code> (acquisition_strategy.py)</li>
<li><code>_MISSION_FEED_CAP_THRESHOLDS</code> (acquisition_strategy.py)</li>
<li><code>_NONFEED_PROFILE_FEED_CAP_THRESHOLDS</code> (acquisition_strategy.py)</li>
<li><code>LANE_RULES</code> (acquisition_strategy.py)</li>
<li><code>TERMINAL_STATES</code> (acquisition_strategy.py)</li>
<li><code>NON_TERMINAL_STATES</code> (acquisition_strategy.py)</li>
<li><code>_TERMINAL_PRIORITY</code> (acquisition_strategy.py)</li>
<li><code>_NONFEED_LANE_FAMILY_MAP</code> (acquisition_strategy.py)</li>
<li><code>_ACCEPTED_TERMINAL_STATES</code> (acquisition_strategy.py)</li>
<li><code>_DOMAIN_OR_IP_RE</code> (acquisition_strategy.py)</li>
<li><code>_URL_RE</code> (acquisition_strategy.py)</li>
<li><code>_WALLET_RE</code> (acquisition_strategy.py)</li>
<li><code>_CRYPTO_HASH_RE</code> (acquisition_strategy.py)</li>
<li><code>_CVE_RE</code> (acquisition_strategy.py)</li>
<li><code>_SAFE_LANES</code> (acquisition_strategy.py)</li>
<li><code>_SAFE_OPTIONAL</code> (acquisition_strategy.py)</li>
<li><code>_MISSION_TARGET_KIND</code> (acquisition_strategy.py)</li>
<li><code>_THREAT_INDICATOR_RE</code> (acquisition_strategy.py)</li>
<li><code>_LANE_TO_FAMILY</code> (acquisition_strategy.py)</li>
<li><code>_NONFEED_SEED_EMPTY</code> (acquisition_strategy.py)</li>
<li><code>ACQUISITION_REPORT_SCHEMA_VERSION</code> (__init__.py)</li>
<li><code>_CIDV0_RE</code> (__init__.py)</li>
<li><code>_CIDV1_BASE32_RE</code> (__init__.py)</li>
<li><code>_MISSION_FEED_CAP_THRESHOLDS</code> (__init__.py)</li>
<li><code>_NONFEED_PROFILE_FEED_CAP_THRESHOLDS</code> (__init__.py)</li>
<li><code>_MISSION_FEED_CAP_THRESHOLDS</code> (__init__.py)</li>
<li><code>_NONFEED_PROFILE_FEED_CAP_THRESHOLDS</code> (__init__.py)</li>
<li><code>LANE_RULES</code> (__init__.py)</li>
<li><code>TERMINAL_STATES</code> (__init__.py)</li>
<li><code>NON_TERMINAL_STATES</code> (__init__.py)</li>
<li><code>_TERMINAL_PRIORITY</code> (__init__.py)</li>
<li><code>_NONFEED_LANE_FAMILY_MAP</code> (__init__.py)</li>
<li><code>_ACCEPTED_TERMINAL_STATES</code> (__init__.py)</li>
<li><code>_DOMAIN_OR_IP_RE</code> (__init__.py)</li>
<li><code>_URL_RE</code> (__init__.py)</li>
<li><code>_WALLET_RE</code> (__init__.py)</li>
<li><code>_CRYPTO_HASH_RE</code> (__init__.py)</li>
<li><code>_CVE_RE</code> (__init__.py)</li>
<li><code>_SAFE_LANES</code> (__init__.py)</li>
<li><code>_SAFE_OPTIONAL</code> (__init__.py)</li>
<li><code>_MISSION_TARGET_KIND</code> (__init__.py)</li>
<li><code>_THREAT_INDICATOR_RE</code> (__init__.py)</li>
<li><code>_LANE_TO_FAMILY</code> (__init__.py)</li>
<li><code>_NONFEED_SEED_EMPTY</code> (__init__.py)</li>
<li><code>_SPRINT_ADVISORY_RUNNER</code> (sidecar_orchestrator.py)</li>
<li><code>_OTEL_TRACER</code> (sidecar_orchestrator.py)</li>
<li><code>_ADVISORY_SIDECAR_SEMAPHORE_LIMIT</code> (sidecar_orchestrator.py)</li>
<li><code>_PLUGIN_SIDECAR_SEMAPHORE_LIMIT</code> (sidecar_orchestrator.py)</li>
<li><code>MAX_PIVOTS</code> (pivot_planner.py)</li>
<li><code>MAX_PIVOT_CANDIDATES</code> (pivot_planner.py)</li>
<li><code>_MISSION_BOOST_RULES</code> (pivot_planner.py)</li>
<li><code>T</code> (role_based_pools.py)</li>
<li><code>_EMBED_WORKERS</code> (role_based_pools.py)</li>
<li><code>_DB_WORKERS</code> (role_based_pools.py)</li>
<li><code>_HASH_WORKERS</code> (role_based_pools.py)</li>
<li><code>_REGEX_WORKERS</code> (role_based_pools.py)</li>
<li><code>_ASYNC_IO_WORKERS</code> (role_based_pools.py)</li>
<li><code>LEDGER_FAMILY</code> (nonfeed_candidate_ledger.py)</li>
<li><code>LEDGER_STAGE</code> (nonfeed_candidate_ledger.py)</li>
<li><code>MAX_LEDGER_SIZE</code> (nonfeed_candidate_ledger.py)</li>
<li><code>MAX_SAMPLE_CHARS</code> (nonfeed_candidate_ledger.py)</li>
<li><code>CANDIDATE_ID_TRUNC</code> (nonfeed_candidate_ledger.py)</li>
<li><code>FAMILY_PUBLIC</code> (nonfeed_candidate_ledger.py)</li>
<li><code>FAMILY_CT</code> (nonfeed_candidate_ledger.py)</li>
<li><code>FAMILY_WAYBACK</code> (nonfeed_candidate_ledger.py)</li>
<li><code>FAMILY_PASSIVE_DNS</code> (nonfeed_candidate_ledger.py)</li>
<li><code>FAMILY_PIVOT</code> (nonfeed_candidate_ledger.py)</li>
<li><code>FAMILY_FEED</code> (nonfeed_candidate_ledger.py)</li>
<li><code>MAX_DOMAIN_CANDIDATES_FOR_LANES</code> (nonfeed_candidate_ledger.py)</li>
<li><code>MAX_FEED_CANDIDATES</code> (nonfeed_candidate_ledger.py)</li>
<li><code>MAX_DOH_DOMAINS</code> (nonfeed_candidate_ledger.py)</li>
<li><code>MAX_CT_DOMAINS</code> (nonfeed_candidate_ledger.py)</li>
<li><code>MAX_CONCEPTUAL_DOMAINS</code> (nonfeed_candidate_ledger.py)</li>
<li><code>MAX_WAYBACK_CANDIDATES</code> (nonfeed_candidate_ledger.py)</li>
<li><code>MAX_PASSIVE_DNS_CANDIDATES</code> (nonfeed_candidate_ledger.py)</li>
<li><code>STAGE_DISCOVERED</code> (nonfeed_candidate_ledger.py)</li>
<li><code>STAGE_FETCHED</code> (nonfeed_candidate_ledger.py)</li>
<li><code>STAGE_PARSED</code> (nonfeed_candidate_ledger.py)</li>
<li><code>STAGE_QUARANTINED</code> (nonfeed_candidate_ledger.py)</li>
<li><code>STAGE_REJECTED</code> (nonfeed_candidate_ledger.py)</li>
<li><code>STAGE_STORED</code> (nonfeed_candidate_ledger.py)</li>
<li><code>STAGE_ACCEPTED</code> (nonfeed_candidate_ledger.py)</li>
<li><code>STAGE_PROVIDER_FAILED</code> (nonfeed_candidate_ledger.py)</li>
<li><code>_DEFANG_PATTERNS</code> (nonfeed_candidate_ledger.py)</li>
<li><code>_DEDUP_DOMAIN_RE</code> (nonfeed_candidate_ledger.py)</li>
<li><code>_URL_PREFIX_RE</code> (nonfeed_candidate_ledger.py)</li>
<li><code>MAX_SIDECAR_FINDINGS</code> (sidecar_bus.py)</li>
<li><code>MAX_SIDECAR_RESULT_RECORDS</code> (sidecar_bus.py)</li>
<li><code>SIDECAR_TIMEOUT_S</code> (sidecar_bus.py)</li>
<li><code>SIDECAR_DEFAULT_ESTIMATE_MB</code> (sidecar_bus.py)</li>
<li><code>_HEAVY_SIDECARS</code> (sidecar_bus.py)</li>
<li><code>_ACTIVE_NETWORK_SIDECARS</code> (sidecar_bus.py)</li>
<li><code>SIDECAR_NETWORK_CLASS</code> (sidecar_bus.py)</li>
<li><code>SIDECAR_RISK_CLASS</code> (sidecar_bus.py)</li>
<li><code>SIDECAR_STAGES</code> (sidecar_bus.py)</li>
<li><code>DEFAULT_SIDECAR_RUNNERS</code> (sidecar_bus.py)</li>
<li><code>DEFAULT_FETCH_LIMIT</code> (resource_governor.py)</li>
<li><code>MODEL_LOADED_FETCH_LIMIT</code> (resource_governor.py)</li>
<li><code>CRITICAL_FETCH_LIMIT</code> (resource_governor.py)</li>
<li><code>CRITICAL_BRANCH_CONCURRENCY</code> (resource_governor.py)</li>
<li><code>CRITICAL_NEAR_EMERGENCY_BRANCH_CONCURRENCY</code> (resource_governor.py)</li>
<li><code>CRITICAL_MILD_BRANCH_CONCURRENCY</code> (resource_governor.py)</li>
<li><code>MODEL_LOADED_BRANCH_CONCURRENCY</code> (resource_governor.py)</li>
<li><code>CRITICAL_ALLOW_RENDERER</code> (resource_governor.py)</li>
<li><code>CRITICAL_ALLOW_MODEL_LOAD</code> (resource_governor.py)</li>
<li><code>_EMA_ALPHA</code> (resource_governor.py)</li>
<li><code>MISSION_PEAK_RSS_GIB</code> (resource_governor.py)</li>
<li><code>SIDECAR_DEFAULT_ESTIMATE_MB</code> (resource_governor.py)</li>
<li><code>HEAVY_SIDECARS</code> (resource_governor.py)</li>
<li><code>MAX_BUDGET_EVENTS</code> (resource_governor.py)</li>
<li><code>_UNSET</code> (scheduler_result.py)</li>
<li><code>MAX_PIVOTS</code> (sprint_advisory_runner.py)</li>
<li><code>_ADVISORY_PARALLEL_SEMAPHORE_LIMIT</code> (sprint_advisory_runner.py)</li>
<li><code>FEDERATED_ADVISORY_MAX_NODES</code> (sprint_advisory_runner.py)</li>
<li><code>FEDERATED_ADVISORY_MEMORY_SKIP_THRESHOLD</code> (sprint_advisory_runner.py)</li>
<li><code>FEDERATED_ADVISORY_MEMORY_REDUCED_THRESHOLD</code> (sprint_advisory_runner.py)</li>
<li><code>FEDERATED_ADVISORY_MAX_UPDATES</code> (sprint_advisory_runner.py)</li>
<li><code>_MAX_SEEDS_FROM_QUERY</code> (nonfeed_seed_runtime.py)</li>
<li><code>_MAX_SEEDS_FROM_FINDINGS</code> (nonfeed_seed_runtime.py)</li>
<li><code>_MAX_ROWS_FROM_DUCKDB</code> (nonfeed_seed_runtime.py)</li>
<li><code>_ACQUISITION_PROFILE</code> (nonfeed_seed_runtime.py)</li>
</ul>
</details>



## Metrics

| Metric | Value |
|---|---|
| Files | 136 |
| Total lines | 64461 |
| Avg lines/file | 473 |
| Languages | Python |
| Outgoing deps | 4 |
| Incoming deps | 0 |
| Tier | 1 |

