+++
title = "tests/"
weight = 31
description = "<think> The user wants me to write a module overview for the `tests` module in a codebase wiki. I need to follow these critical rules:  1. NEVER start with 'The X module consists of...', 'This module ..."

[extra]
tier = 1
file_count = 187
total_lines = 53775
languages = "Python"
+++

<think>
The user wants me to write a module overview for the `tests` module in a codebase wiki. I need to follow these critical rules:

1. NEVER start with 'The X module consists of...', 'This module contains...', or any variant
2. First sentence MUST state what the module DOES or what PURPOSE it serves — infer from file names, symbol names, and dependency position
3. Focus on PURPOSE, RESPONSIBILITIES, and ARCHITECTURAL ROLE — not listing files/classes
4. Describe the module's architectural role: Is it a hub? A leaf? A bridge between subsystems?
5. Explain how this module fits into the larger system — what it provides and consumes
6. If high fan-in, note blast radius
7. Note complexity: file count, line count, symbol density
8. Do NOT enumerate specific file names, class names, or function names unless truly central
9. Vary sentence structure
10. Write 4-8 sentences
11. Be specific about what the module does and its scale
12. Do NOT speculate about design intent
13. NEVER leave missing spaces between words
14. Proofread

Looking at the structural context:
- This is the `tests` module with 187 files, 53,775 lines total
- It's Python-based
- No outgoing dependencies detected
- No incoming dependencies detected (it's a leaf module)
- It has sub-modules: manual/ and utils/
- Largest files include test_e2e_first_finding.py, test_sprint_scheduler.py, conftest.py, etc.
- Key symbols include Tracemalloc Snapshot, various test classes and functions

This is clearly the test suite for the codebase. It:
- Validates all other modules in the system
- Has no dependencies on other modules (leaf role)
- Is not depended upon by other modules
- Contains extensive utilities for memory profiling, mock creation, async testing
- Covers sprint scheduling, hypothesis generation, Rust backend, differential fuzzing, etc.


- Drives the entire system's reliability by validating every major component

The tests module serves as the validation layer for the entire codebase, exercising functionality across sprint scheduling, hypothesis generation, Rust backend operations, and async patterns. Its isolation from other modules—no inbound or outbound dependencies—positions it as a leaf node that consumes production code while providing test infrastructure like memory profiling and mock generation utilities. This independence means test failures directly indicate production issues, creating a wide blast radius for any regressions.
</think>

This module provides comprehensive validation for the entire codebase by exercising sprint scheduling, hypothesis generation, Rust backend operations, async patterns, DuckDB storage, and telemetry pipelines. As the terminal leaf in the dependency graph with zero incoming and zero outgoing dependencies, it serves as the system's final quality gate—consuming production code from all other modules without being consumed by any. The suite spans 187 files and roughly 54,000 lines of Python, with substantial investment in memory profiling infrastructure (Tracemalloc Snapshot, Memory Tracker), sophisticated mock factories, and session-scoped fixtures that eliminate per-test initialization overhead. Notable architectural concerns include fail-soft patterns throughout, differential fuzzing that compares Rust implementations against Python fallbacks, and M1 8GB memory safety constraints enforced via bounded concurrency and leak detection. The manual/ subdirectory contains interactive debugging utilities while utils/ provides shared profiling and mock creation facilities—together they reduce test flakiness and improve CI reliability across the full system.

## Structure

### Sub-modules

- [**manual/**](/wiki/tests-manual/) — 6 files, 357 lines (Python)
- [**utils/**](/wiki/tests-utils/) — 3 files, 1358 lines (Python)

| Language | Files |
|---|---|
| Python | 187 |

### Directories

| Directory | Files | Lines |
|---|---|---|
| utils/ | 3 | 1358 |
| probe_p_e2_feed_pipeline/ | 1 | 458 |
| manual/ | 6 | 357 |
| rust/ | 1 | 320 |
| test_embedding_prefix_discipline/ | 1 | 253 |
| cli/ | 2 | 182 |
| test_inference_pipeliner/ | 2 | 115 |

### Largest Files

- `test_e2e_first_finding.py` (1363 lines)
- `test_sprint_scheduler.py` (1235 lines)
- `conftest.py` (1148 lines)
- `test_research_depth_metric.py` (947 lines)
- `test_differential_fuzzing.py` (840 lines)
- `test_sprint_p12_hypothesis.py` (783 lines)
- `test_hledac_core_rust.py` (766 lines)
- `test_t1_otel.py` (720 lines)
- `test_rust_backend.py` (707 lines)
- `utils/spec_mocks.py` (697 lines)

<details><summary><strong>Show 177 more files</strong></summary>

- `utils/memory_profiler.py` (659 lines)
- `test_sprint8l_live.py` (658 lines)
- `test_alt_protocols.py` (639 lines)
- `test_sprint_f273.py` (616 lines)
- `test_sprint_memory_profiling.py` (594 lines)
- `test_f227k_acquisition_lane_parity.py` (563 lines)
- `test_coroutine_cleanup.py` (545 lines)
- `test_correlation_propagation.py` (524 lines)
- `test_hypothesis_engine.py` (505 lines)
- `test_sprint8ax_duckdb_shadow.py` (502 lines)
- `test_circuit_breaker_metrics.py` (502 lines)
- `test_url_ops_rust.py` (501 lines)
- `test_storage_router.py` (499 lines)
- `test_foca_integration.py` (498 lines)
- `test_f_lmdb_bulk.py` (488 lines)
- `test_write_coalescer.py` (474 lines)
- `test_sprint60.py` (469 lines)
- `test_sprint_f226.py` (465 lines)
- `test_r0_nonfeed_reality_lock.py` (463 lines)
- `test_deep_probe_dht.py` (460 lines)
- `probe_p_e2_feed_pipeline/test_feed_pipeline.py` (458 lines)
- `test_race_first_success.py` (452 lines)
- `test_f221f_acquisition_plan_semantics.py` (451 lines)
- `test_sprint_f271.py` (451 lines)
- `test_hypothesis_builder.py` (450 lines)
- `test_sprint58b.py` (444 lines)
- `test_sprint_dashboard.py` (442 lines)
- `test_async_generators.py` (428 lines)
- `test_sprint61.py` (419 lines)
- `test_issue9_error_policy.py` (399 lines)
- `test_f_a4_batch_dns.py` (393 lines)
- `test_f_a2_lazy_intelligence.py` (391 lines)
- `test_regex_python314_compat.py` (390 lines)
- `test_brain_lazy.py` (375 lines)
- `test_sprint_f193b_hypothesis_feedback.py` (373 lines)
- `test_issue24_finding_pipeline.py` (370 lines)
- `test_f_asyncio_run_audit.py` (360 lines)
- `test_f_u1_mmap_bloom.py` (356 lines)
- `test_acquisition_fallback.py` (352 lines)
- `test_f_drift_fixes.py` (350 lines)
- `test_pivot_executor_f314.py` (350 lines)
- `test_sprint_policy_manager.py` (349 lines)
- `test_sprint8ao_duckdb_sidecar.py` (346 lines)
- `test_sprint55.py` (340 lines)
- `test_html_parser_characterization.py` (339 lines)
- `test_hermes_model_cache.py` (339 lines)
- `test_sprint_f272.py` (339 lines)
- `test_semantic_deduplicator.py` (337 lines)
- `test_evidence_network.py` (335 lines)
- `test_issue_027_async_generators.py` (324 lines)
- `tool_schema_validation.py` (322 lines)
- `rust/test_ffi_parity.py` (320 lines)
- `test_rayon_pool.py` (318 lines)
- `test_p3b_public_pipeline_split.py` (318 lines)
- `test_alert_manager.py` (313 lines)
- `test_deep_probe_runner.py` (307 lines)
- `test_adaptive_cache.py` (301 lines)
- `test_sprint8ay_mlx_memory.py` (300 lines)
- `test_f261_arrow_fetch_batch.py` (297 lines)
- `test_resource_governor_authority_seal.py` (293 lines)
- `test_global_scheduler_spawn_registry.py` (283 lines)
- `test_harness.py` (280 lines)
- `test_semantic_store_buffer.py` (272 lines)
- `test_f_a3_cycle_deadline.py` (269 lines)
- `test_sprint59.py` (268 lines)
- `test_f265a_transport_audit.py` (267 lines)
- `test_f_graph_serde.py` (266 lines)
- `test_lazy_singleton.py` (265 lines)
- `test_pep734_isolated_executors.py` (262 lines)
- `test_domain_rate_limiter.py` (260 lines)
- `test_sprint41.py` (257 lines)
- `test_content_hasher.py` (254 lines)
- `test_py314_fallback.py` (253 lines)
- `test_embedding_prefix_discipline/test_embedding_task.py` (253 lines)
- `test_flag_validation.py` (253 lines)
- `test_sprint47.py` (251 lines)
- `test_circuit_breaker_ttl_override.py` (251 lines)
- `test_sprint42.py` (244 lines)
- `test_f_a4_wire_in.py` (243 lines)
- `test_exit_codes.py` (242 lines)
- `test_f_a5_url_dedup.py` (242 lines)
- `test_sprint45.py` (241 lines)
- `test_sprint_f26x.py` (238 lines)
- `test_vault_manager.py` (237 lines)
- `test_discovery_base.py` (233 lines)
- `test_federated_qtable_persistence.py` (230 lines)
- `test_two_pass_pipeline.py` (226 lines)
- `test_ioc_batch_pipeline.py` (226 lines)
- `test_ipfs_canonical.py` (223 lines)
- `test_e2e_dry_run.py` (220 lines)
- `test_embedding_dimensions.py` (220 lines)
- `test_sprint46.py` (218 lines)
- `test_sprint8au_aho_shadow.py` (217 lines)
- `manual/_test_aimd_window.py` (217 lines)
- `test_coordinator_routing_authority_seal.py` (216 lines)
- `test_bounded_per_host_gate.py` (215 lines)
- `test_dspy_evidence_seam.py` (214 lines)
- `test_f_git_stash_guard.py` (214 lines)
- `test_sprint58a.py` (213 lines)
- `test_q_learning.py` (211 lines)
- `test_sprint8m_import_diet.py` (210 lines)
- `test_flag_registry.py` (206 lines)
- `test_memory_budget_gate.py` (203 lines)
- `test_benchmark_hotpaths.py` (200 lines)
- `test_f_pipeline_asyncio_shadowing.py` (196 lines)
- `test_ds_integration.py` (195 lines)
- `test_f26x_dataclass_migration.py` (195 lines)
- `test_sprint_f260.py` (195 lines)
- `test_role_based_pools.py` (193 lines)
- `test_bounded_dicts.py` (193 lines)
- `test_atomic_storage_arch_seal.py` (191 lines)
- `test_rust_extensions.py` (190 lines)
- `test_transport_policy.py` (188 lines)
- `test_i2p_transport.py` (188 lines)
- `test_sprint7g.py` (186 lines)
- `test_graph_service_f226.py` (181 lines)
- `cli/test_parser.py` (181 lines)
- `test_f253_sprint_tiers.py` (179 lines)
- `test_issue34_async_helpers.py` (177 lines)
- `test_exception_policy.py` (177 lines)
- `test_tstring_utils.py` (176 lines)
- `test_rust_pyi_consistency.py` (175 lines)
- `test_issue023_gpu_arbiter.py` (175 lines)
- `test_hypothesis_generator_bounds.py` (173 lines)
- `test_bgp_ripe_live.py` (171 lines)
- `test_mlx_model_pool.py` (169 lines)
- `test_sprint65e_no_available_flags_in_orchestrator.py` (159 lines)
- `test_public_provider_exception_isolation.py` (159 lines)
- `test_p34_msgspec_builtins_benchmark.py` (156 lines)
- `test_sprint8aw_aho_integration.py` (155 lines)
- `test_f_u3_pressure_relief.py` (155 lines)
- `test_hypothesis_dspy_fallback.py` (154 lines)
- `test_f14_duckdb_ingest_breaker.py` (153 lines)
- `test_recursion_guard.py` (149 lines)
- `PHASE_GATES.py` (142 lines)
- `test_sprint11_execution_coordinator.py` (139 lines)
- `test_sprint64_transport_resolver.py` (138 lines)
- `test_f265c_transport_policy_wire.py` (137 lines)
- `test_http_cache.py` (135 lines)
- `test_issue14_adaptive_worker_pool.py` (132 lines)
- `test_issue17_bfs_crawl.py` (132 lines)
- `test_8ba_phase0.py` (129 lines)
- `test_f_bloom_regression.py` (128 lines)
- `test_aimd_controller.py` (127 lines)
- `test_f_u2_gc_cycle.py` (126 lines)
- `test_no_aiohttp_socks.py` (126 lines)
- `issue23_wiring_test.py` (122 lines)
- `test_sprint62a.py` (117 lines)
- `test_inference_pipeliner/test_pipeliner.py` (114 lines)
- `test_f250_dynamic_windup.py` (113 lines)
- `test_sprint_p11_early_exit.py` (105 lines)
- `test_http3_lane_dark_web_guard.py` (104 lines)
- `test_graph_service_smoke.py` (104 lines)
- `test_e2e_pipeline_smoke.py` (102 lines)
- `test_ipfs_cid_extraction.py` (100 lines)
- `test_intelligence_http_helpers.py` (97 lines)
- `test_f43_flag_smoke.py` (91 lines)
- `test_f500i_import_benchmark.py` (88 lines)
- `test_mlx_worker_thread_spsc.py` (79 lines)
- `test_retry_jitter.py` (76 lines)
- `test_bgp_env_gate.py` (69 lines)
- `test_banner_grab_env_gate.py` (69 lines)
- `test_f33_benchmark_deprecation.py` (60 lines)
- `test_sprint62b.py` (55 lines)
- `test_issue24_lancedb_write_queue.py` (53 lines)
- `test_f52_mobileclip_gate.py` (51 lines)
- `test_temporal_signal_smoke.py` (50 lines)
- `manual/_verify_stats.py` (40 lines)
- `manual/_debug_test.py` (33 lines)
- `test_scheduler_v2_imports.py` (33 lines)
- `manual/_test_graph_debug.py` (28 lines)
- `manual/_debug_test3.py` (25 lines)
- `manual/_debug_test2.py` (14 lines)
- `utils/__init__.py` (2 lines)
- `test_inference_pipeliner/__init__.py` (1 lines)
- `cli/__init__.py` (1 lines)
- `__init__.py` (0 lines)

</details>


## Dependencies

No outgoing dependencies detected.

## Dependents

No incoming dependencies detected.

## Key Symbols

<p><strong>Key definitions:</strong></p>
<ul>
<li>
<p><code>TracemallocSnapshot</code> (Class) in memory_profiler.py — referenced in 3 files</p>
<details><summary>tracemalloc-based snapshot for Python object allocation tracking.</summary>
<div class="doc-comment">
<p>tracemalloc-based snapshot for Python object allocation tracking.</p>
<p></p>
<p>More precise than RSS for detecting Python object leaks (e.g., lists</p>
<p>accumulating in module globals, forgotten callbacks, etc.)</p>
<p></p>
<p>Example:</p>
<p>snap = TracemallocSnapshot()</p>
<p># ... test code ...</p>
<p>top_deltas = snap.compare_top_n(5)</p>
<p>for stat in top_deltas:</p>
<p>print(f"  {stat}: {stat.size_diff/1024:.1f} KB")</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: conftest.py, test_sprint_memory_profiling.py</li></ul>
</li>
<li>
<p><code>TestDifferentialUrlDomain</code> (Class) in test_differential_fuzzing.py — referenced in 1 file</p>
<details><summary>Differential fuzzing pro URL domain — Rust vs Python.</summary></details>
</li>
<li>
<p><code>TestEvidenceLogCorrelation</code> (Class) in test_correlation_propagation.py — referenced in 1 file</p>
<details><summary>Test EvidenceLog.create_event correlation support.</summary></details>
</li>
<li>
<p><code>TestF11WindupFirstCycle</code> (Class) in test_sprint_scheduler.py — referenced in 1 file</p>
<details><summary>F1-1: Windup guard first_cycle_ran identity bug.</summary>
<div class="doc-comment">
<p>F1-1: Windup guard first_cycle_ran identity bug.</p>
<p></p>
<p>Hypotéza A: set_first_cycle_ran() a should_enter_windup() operují nad</p>
<p>různými instancemi SprintLifecycleManager (přes _LifecycleAdapter wrapper).</p>
<p></p>
<p>Test ověřuje, že _LifecycleAdapter.set_first_cycle_ran() správně propaguje</p>
<p>first_cycle_ran=True do underlying lifecycle na STEJNÉ instanci.</p>
</div>
</details>
</li>
<li>
<p><code>TestP12DILoadWire</code> (Class) in test_sprint_p12_hypothesis.py — referenced in 1 file</p>
<details><summary>P12 DI wire tests: gate opens only when store+hermes_engine+stored_findings&gt;0.</summary></details>
</li>
</ul>

<details><summary><strong>Function</strong> (818)</summary>
<ul>
<li><code>test_canonical_run_sprint_persists_and_exports_findings</code> (test_e2e_first_finding.py)</li>
<li><code>_build_expected</code> (test_f227k_acquisition_lane_parity.py)</li>
<li><code>canned_feed_adapter</code> (test_e2e_first_finding.py)
<details><summary>Patch rss_atom_adapter to return a single high-quality canned entry.</summary>
<div class="doc-comment">
<p>Patch rss_atom_adapter to return a single high-quality canned entry.</p>
<p>Uses FeedEntryHit msgspec.Struct to match what the pipeline expects.</p>
<p>Also patch live_feed_pipeline.async_run_live_feed_pipeline to pass store.</p>
</div>
</details>
</li>
<li><code>run_live_benchmark</code> (test_sprint8l_live.py)</li>
<li><code>test_final_export_still_replaces_partial_as_terminal_artifact</code> (test_e2e_first_finding.py)</li>
<li><code>test_e2e_first_persisted_finding</code> (test_e2e_first_finding.py)</li>
<li><code>print_live_results</code> (test_sprint8l_live.py)</li>
<li><code>_ctx</code> (test_f227k_acquisition_lane_parity.py)</li>
<li><code>test_slow_branch_timeout_does_not_block_other_branches</code> (test_e2e_first_finding.py)</li>
<li><code>test_partial_branch_success_still_updates_runtime_truth</code> (test_e2e_first_finding.py)</li>
<li><code>test_partial_export_survives_early_windup</code> (test_e2e_first_finding.py)</li>
<li><code>test_partial_export_written_every_ten_findings</code> (test_e2e_first_finding.py)</li>
<li><code>make_sprint_scheduler_mock</code> (spec_mocks.py)</li>
<li><code>_canned_live_feed_pipeline</code> (test_e2e_first_finding.py)</li>
<li><code>make_governor_mock</code> (spec_mocks.py)</li>
<li><code>test_aggressive_cycle_fans_out_feed_public_ct_concurrently</code> (test_e2e_first_finding.py)</li>
<li><code>session_duckdb_store</code> (conftest.py)
<details><summary>Session-scoped DuckDB store — one instance for all tests.</summary>
<div class="doc-comment">
<p>Session-scoped DuckDB store — one instance for all tests.</p>
<p>Temp directory, isolated dedup LMDB, cleaned up at session end.</p>
<p>M1 8GB: avoids ~132× DuckDB init overhead.</p>
<p></p>
<p>Fail-soft: yields None if DuckDB or Rust backend unavailable (pre-existing</p>
<p>bugs like DelegatingDomain NameError won't block test collection).</p>
</div>
</details>
</li>
<li><code>test_shadow_flag_on_records_batch</code> (test_sprint8ax_duckdb_shadow.py)
<details><summary>With GHOST_DUCKDB_SHADOW=1, evidence_packet events are shadow-recorded.</summary>
<div class="doc-comment">
<p>With GHOST_DUCKDB_SHADOW=1, evidence_packet events are shadow-recorded.</p>
<p>Uses :memory: mode (DB_ROOT unavailable in test env).</p>
</div>
</details>
</li>
<li><code>test_e2e_export_handoff_sees_non_zero_findings</code> (test_e2e_first_finding.py)</li>
<li><code>test_export_sprint_includes_research_depth_metric</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">FIX F350M-R: Use session_event_loop fixture instead of asyncio.run().</span></li>
<li><code>canned_public_adapter</code> (test_e2e_first_finding.py) — <span class="doc-comment-inline">Patch live_public_pipeline to return a single high-quality canned public entry.</span></li>
<li><code>test_evidence_log_append_propagates_correlation_to_shadow</code> (test_correlation_propagation.py)
<details><summary>EvidenceLog.append() extracts _correlation from payload and passes to shadow_record_finding.</summary>
<div class="doc-comment">
<p>EvidenceLog.append() extracts _correlation from payload and passes to shadow_record_finding.</p>
<p></p>
<p>Verifies cross-ledger propagation: EvidenceLog → analytics_hook (DuckDB shadow).</p>
</div>
</details>
</li>
<li><code>_gc_and_close_loops</code> (conftest.py)
<details><summary>Hermetic cleanup — close leaked loops + gc.collect() after each test.</summary>
<div class="doc-comment">
<p>Hermetic cleanup — close leaked loops + gc.collect() after each test.</p>
<p></p>
<p>Tracks loops created before the test via asyncio._all_loops (monkey-patched</p>
<p>for Python 3.14+). After the test, any loop not in the "before" set AND</p>
<p>not closed is closed and removed from _loop_registry.  Then gc.collect()</p>
<p>runs once (two-pass if heavy markers present).</p>
<p></p>
<p>Compatible: Python 3.12 (has asyncio._all_loops natively) and</p>
<p>Python 3.14+ (monkey-patched via _loop_registry above).</p>
</div>
</details>
</li>
<li><code>_force_load</code> (conftest.py)
<details><summary>Force-load `modname` from &lt;HUB_DIR&gt; by absolute path, replacing any</summary>
<div class="doc-comment">
<p>Force-load `modname` from &lt;HUB_DIR&gt; by absolute path, replacing any</p>
<p>stub already in sys.modules.  Idempotent and fail-safe.</p>
</div>
</details>
</li>
<li><code>test_sprint_scheduler_config_with_keyword_query</code> (test_sprint_scheduler.py)
<details><summary>Integration test 5.2: SprintSchedulerConfig accepts keyword query.</summary>
<div class="doc-comment">
<p>Integration test 5.2: SprintSchedulerConfig accepts keyword query.</p>
<p></p>
<p>Verifies that SprintSchedulerConfig can be created with a non-domain</p>
<p>keyword query string (not just domain names), and that the resulting</p>
<p>config has appropriate settings for a short sprint run.</p>
<p></p>
<p>Accepts 0 findings as valid outcome for keyword queries.</p>
</div>
</details>
</li>
<li><code>test_memory_mode_persists_across_multiple_async_calls</code> (test_sprint8ax_duckdb_shadow.py)
<details><summary>In :memory: mode, repeated async writes should all go to the same</summary>
<div class="doc-comment">
<p>In :memory: mode, repeated async writes should all go to the same</p>
<p>persistent connection and be queryable across calls.</p>
</div>
</details>
</li>
<li><code>test_lifecycle_adapter_set_first_cycle_ran_propagates_to_same_instance</code> (test_sprint_scheduler.py)
<details><summary>F1-1: Ověřuje, že set_first_cycle_ran() na _LifecycleAdapter</summary>
<div class="doc-comment">
<p>F1-1: Ověřuje, že set_first_cycle_ran() na _LifecycleAdapter</p>
<p>skutečně nastaví first_cycle_ran na STEJNÉ instanci SprintLifecycleManager,</p>
<p>kterou should_enter_windup() čte.</p>
</div>
</details>
</li>
<li><code>test_lane_parity_matrix</code> (test_f227k_acquisition_lane_parity.py)
<details><summary>Parity matrix: verify all 12 lanes across 13 scenarios.</summary>
<div class="doc-comment">
<p>Parity matrix: verify all 12 lanes across 13 scenarios.</p>
<p></p>
<p>Each assertion: (enabled, reason, max_items, timeout_s, concurrency, risk_level).</p>
<p>Source of truth is the pre-refactor inline logic at git commit ff3f444b.</p>
</div>
</details>
</li>
<li><code>_cleanup</code> (conftest.py)
<details><summary>Centralized test cleanup — Issue #9 fix.</summary>
<div class="doc-comment">
<p>Centralized test cleanup — Issue #9 fix.</p>
<p></p>
<p>ONE autouse fixture replaces scattered gc.collect() calls across 7+ test files.</p>
<p>Runs gc.collect() after EVERY test via request.addfinalizer(gc.collect).</p>
<p></p>
<p>- 2-pass GC (gc.collect(); gc.collect()) for mlx/duckdb/lmdb/heavy markers</p>
<p>- 1-pass GC for all other tests</p>
<p>- gc.unfreeze() if GC was frozen from MemoryTracker</p>
<p></p>
<p>OLD scattered pattern (now replaced):</p>
<p>tests/test_coroutine_cleanup.py: 7× gc.collect()</p>
<p>tests/test_sprint_memory_profiling.py: 14×</p>
<p>tests/test_f_u2_gc_cycle.py: 1×</p>
<p>tests/test_brain_lazy.py: 2×</p>
<p>tests/test_f14_duckdb_ingest_breaker.py: 2×</p>
<p>tests/test_pep734_isolated_executors.py: 3×</p>
<p>tests/test_sprint8ay_mlx_memory.py: 5×</p>
</div>
</details>
</li>
<li><code>test_otlp_json_shape</code> (test_t1_otel.py) — <span class="doc-comment-inline">A real span (from ring) serializes to valid OTLP/JSON.</span></li>
<li><code>_make_scheduler_base</code> (conftest.py)</li>
<li><code>make_storage_mock</code> (spec_mocks.py)</li>
<li><code>_asyncio_task_leak_guard</code> (conftest.py)
<details><summary>Detect and warn about asyncio task leaks within each test.</summary>
<div class="doc-comment">
<p>Detect and warn about asyncio task leaks within each test.</p>
<p></p>
<p>CRITICAL FIX F350M-R: Orphaned tasks indicate forgotten cleanup.</p>
<p>Uses return_exceptions=False to expose real failures, not mask them.</p>
</div>
</details>
</li>
<li><code>compare_top_n</code> (memory_profiler.py)</li>
<li><code>test_synthesis_sidecar_runs_when_accepted_findings_present</code> (test_sprint_scheduler.py)
<details><summary>F259B: When accepted_findings &gt; 0, synthesis must proceed normally</summary>
<div class="doc-comment">
<p>F259B: When accepted_findings &gt; 0, synthesis must proceed normally</p>
<p>(regression guard for the early-exit — must not block the happy path).</p>
</div>
</details>
</li>
<li><code>save_results</code> (test_sprint8l_live.py)</li>
<li><code>_leak_in_subprocess</code> (test_sprint_memory_profiling.py)
<details><summary>Fork subprocess to measure RSS delta of an intentional leak.</summary>
<div class="doc-comment">
<p>Fork subprocess to measure RSS delta of an intentional leak.</p>
<p></p>
<p>This isolates the measurement from Python's GC and memory allocator</p>
<p>noise, giving a clean RSS delta without false positives/negatives.</p>
<p></p>
<p>Returns True if leak was detected (delta &gt; threshold_mb), False otherwise.</p>
</div>
</details>
</li>
<li><code>test_memory_mode_uses_same_worker_thread_name</code> (test_sprint8ax_duckdb_shadow.py)
<details><summary>In :memory: mode, the duckdb_worker thread name should be stable</summary>
<div class="doc-comment">
<p>In :memory: mode, the duckdb_worker thread name should be stable</p>
<p>across multiple async batch calls.</p>
</div>
</details>
</li>
<li><code>test_safe_skip_on_memory_pressure</code> (test_sprint_p12_hypothesis.py)
<details><summary>When ModelManager raises RuntimeError (memory pressure), Hermes load is skipped.</summary>
<div class="doc-comment">
<p>When ModelManager raises RuntimeError (memory pressure), Hermes load is skipped.</p>
<p>Verifies fail-soft skip — sprint continues, ToT is skipped.</p>
</div>
</details>
</li>
<li><code>test_successful_load_sets_hermes_engine</code> (test_sprint_p12_hypothesis.py)
<details><summary>When ModelManager.load_model succeeds, hermes_engine is set.</summary>
<div class="doc-comment">
<p>When ModelManager.load_model succeeds, hermes_engine is set.</p>
<p>Verifies DI wire to public pipeline.</p>
</div>
</details>
</li>
<li><code>canned_pattern_matcher</code> (test_e2e_first_finding.py)
<details><summary>Ensure the "cve-" bootstrap pattern is active and patch match_text to</summary>
<div class="doc-comment">
<p>Ensure the "cve-" bootstrap pattern is active and patch match_text to</p>
<p>return a canned CVE PatternHit when the canned entry text is scanned.</p>
</div>
</details>
</li>
<li><code>test_unload_releases_via_model_manager</code> (test_sprint_p12_hypothesis.py)
<details><summary>_unload_hermes_at_teardown calls ModelManager.release_model.</summary>
<div class="doc-comment">
<p>_unload_hermes_at_teardown calls ModelManager.release_model.</p>
<p>Verifies canonical unload authority.</p>
</div>
</details>
</li>
<li><code>make_duckdb_store_mock_full</code> (spec_mocks.py)</li>
<li><code>_deep_cleanup_mock</code> (spec_mocks.py)
<details><summary>Recursively clean a MagicMock and its _mock_children.</summary>
<div class="doc-comment">
<p>Recursively clean a MagicMock and its _mock_children.</p>
<p></p>
<p>Args:</p>
<p>mock: MagicMock/AsyncMock to clean</p>
<p></p>
<p>Clears:</p>
<p>• _mock_children dict</p>
<p>• _mock_sealed state</p>
<p>• call_args_list</p>
</div>
</details>
</li>
<li><code>test_synthesis_sidecar_skipped_when_zero_accepted_findings</code> (test_sprint_scheduler.py)
<details><summary>F259B CRITICAL #3: Synthesis sidecar MUST early-exit when this sprint</summary>
<div class="doc-comment">
<p>F259B CRITICAL #3: Synthesis sidecar MUST early-exit when this sprint</p>
<p>produced 0 accepted findings, BEFORE touching duckdb_store I/O.</p>
<p></p>
<p>Regression for 1780830658: 120s windup wasted on 0-finding sprints.</p>
<p>Verifies the guard fires at the in-memory `self._result.accepted_findings`</p>
<p>check, not at the post-query `if not findings` check.</p>
</div>
</details>
</li>
<li><code>test_f1_1_fallback_when_lc_adapter_is_none</code> (test_sprint_scheduler.py)
<details><summary>F1-1: Fallback logika — když _lc_adapter je None, kód správně</summary>
<div class="doc-comment">
<p>F1-1: Fallback logika — když _lc_adapter je None, kód správně</p>
<p>přistoupí přímo k lifecycle.first_cycle_ran místo volání adapteru.</p>
<p></p>
<p>SprintSchedulerV2 má __slots__ — nelze testovat přes object.__new__().</p>
<p>Testujeme přímo, že lifecycle podporuje first_cycle_ran a správně reaguje.</p>
</div>
</details>
</li>
<li><code>test_skip_hermes_prewarm_when_rss_above_4gb</code> (test_sprint_p12_hypothesis.py)
<details><summary>Aggressive mode: when RSS &gt; 4GB before prewarm, Hermes is skipped fail-soft.</summary>
<div class="doc-comment">
<p>Aggressive mode: when RSS &gt; 4GB before prewarm, Hermes is skipped fail-soft.</p>
<p>Hard headroom rule: RSS &gt; 4GB means insufficient headroom for safe prewarm.</p>
</div>
</details>
</li>
<li><code>test_flush_includes_correlation</code> (test_correlation_propagation.py) — <span class="doc-comment-inline">flush() serializes correlation into metrics JSONL.</span></li>
<li><code>test_async_generator_context_manager_pattern</code> (test_coroutine_cleanup.py)
<details><summary>RECOMMENDED: async generator as async context manager.</summary>
<div class="doc-comment">
<p>RECOMMENDED: async generator as async context manager.</p>
<p></p>
<p>Python 3.11+: async generators support `async with`:</p>
<p>```python</p>
<p>async def async_generator():</p>
<p>try:</p>
<p>yield ...</p>
<p>finally:</p>
<p>cleanup()</p>
<p></p>
<p>async def consumer():</p>
<p>async for item in async_generator():  # auto-aclose on exit</p>
<p>...</p>
<p>```</p>
<p></p>
<p>For older Python, use acli Util helper.</p>
</div>
</details>
</li>
<li><code>test_evidence_event_queryable</code> (test_correlation_propagation.py) — <span class="doc-comment-inline">Correlation in payload is queryable via payload access.</span></li>
<li><code>_is_numeric_hostname</code> (test_differential_fuzzing.py)
<details><summary>Returns True if URL has a numeric or problematic hostname.</summary>
<div class="doc-comment">
<p>Returns True if URL has a numeric or problematic hostname.</p>
<p></p>
<p>Rust URL parser resolves numeric hostnames to their expanded form</p>
<p>while Python urllib.parse returns the raw hostname as-is.</p>
<p>Also filters malformed URLs and URLs with non-ASCII control chars.</p>
</div>
</details>
</li>
<li><code>make_lmdb_mock</code> (spec_mocks.py)</li>
<li><code>make_duckdb_store_mock</code> (spec_mocks.py)</li>
<li><code>test_task_with_reference_can_be_cancelled</code> (test_coroutine_cleanup.py)
<details><summary>FIXED: Task saved to list for later cleanup.</summary>
<div class="doc-comment">
<p>FIXED: Task saved to list for later cleanup.</p>
<p></p>
<p>Correct pattern:</p>
<p>```python</p>
<p>tasks: list[asyncio.Task] = []</p>
<p>tasks.append(asyncio.create_task(coro()))</p>
<p># ... later ...</p>
<p>for t in tasks:</p>
<p>t.cancel()</p>
<p>await asyncio.gather(*tasks, return_exceptions=True)</p>
<p>```</p>
</div>
</details>
</li>
<li><code>test_lifecycle_adapter_should_enter_windup_uses_same_instance_as_setter</code> (test_sprint_scheduler.py)
<details><summary>F1-1: should_enter_windup() volaný přes adapter musí vidět stejný</summary>
<div class="doc-comment">
<p>F1-1: should_enter_windup() volaný přes adapter musí vidět stejný</p>
<p>first_cycle_ran stav jako set_first_cycle_ran() nastavil.</p>
</div>
</details>
</li>
<li><code>_read_current_state</code> (test_sprint8l_live.py) — <span class="doc-comment-inline">Read current phase and promotion score from orchestrator.</span></li>
<li><code>test_batch_chunking_1001_records_produces_3_batches</code> (test_sprint8ax_duckdb_shadow.py)
<details><summary>Inserting 1001 records with max_batch_size=500 must produce</summary>
<div class="doc-comment">
<p>Inserting 1001 records with max_batch_size=500 must produce</p>
<p>exactly 3 batch executions: 500 + 500 + 1.</p>
</div>
</details>
</li>
<li><code>test_evidence_log_append_still_works</code> (test_sprint8ax_duckdb_shadow.py) — <span class="doc-comment-inline">Adding the shadow hook must not break EvidenceLog.append().</span></li>
<li><code>test_privacy_gate_setattr_failsoft_appends_finding</code> (test_sprint_scheduler.py)
<details><summary>L4343 &amp; L4351 &amp; L4356: privacy_gate exception handlers.</summary>
<div class="doc-comment">
<p>L4343 &amp; L4351 &amp; L4356: privacy_gate exception handlers.</p>
<p>verify: anonymize_text/setattr failure → finding still appended (not lost).</p>
</div>
</details>
</li>
<li><code>memory_tracker</code> (conftest.py)
<details><summary>Per-test memory tracker context manager — RSS + tracemalloc bookend.</summary>
<div class="doc-comment">
<p>Per-test memory tracker context manager — RSS + tracemalloc bookend.</p>
<p></p>
<p>Usage:</p>
<p>async def test_sprint_cycle(memory_tracker):</p>
<p>tracker = memory_tracker</p>
<p>with tracker:</p>
<p>await run_one_cycle()</p>
<p>tracker.assert_leak_threshold(50)</p>
<p></p>
<p>Always-on, fail-safe: returns None if psutil unavailable.</p>
</div>
</details>
</li>
<li><code>make_async_mock</code> (spec_mocks.py)</li>
<li><code>make_sync_mock</code> (spec_mocks.py)</li>
<li><code>test_async_generator_with_explicit_cleanup</code> (test_coroutine_cleanup.py)
<details><summary>FIXED: async generator with aclose() on early exit.</summary>
<div class="doc-comment">
<p>FIXED: async generator with aclose() on early exit.</p>
<p></p>
<p>Correct pattern:</p>
<p>```python</p>
<p>async def consume():</p>
<p>gen = async_range_slow(1000)</p>
<p>try:</p>
<p>async for item in gen:</p>
<p>if stop_condition:</p>
<p>break</p>
<p>finally:</p>
<p>await gen.aclose()  # CRITICAL!</p>
<p>```</p>
</div>
</details>
</li>
<li><code>joinable_threads</code> (spec_mocks.py)
<details><summary>Context manager: start daemon threads, join on exit with timeout.</summary>
<div class="doc-comment">
<p>Context manager: start daemon threads, join on exit with timeout.</p>
<p></p>
<p>Prevents thread leaks between tests:</p>
<p>• daemon=True — threads don't block pytest cleanup</p>
<p>• join(timeout) — catches threads that crash before completion</p>
<p>• join on exit — ensures cleanup even if test body raises</p>
<p></p>
<p>Usage:</p>
<p>with joinable_threads([worker_factory(i) for i in range(8)]) as threads:</p>
<p># threads are running</p>
<p>pass</p>
<p># all threads joined (or killed after timeout)</p>
<p></p>
<p>Args:</p>
<p>targets: Sequence of callables to run in separate daemon threads.</p>
<p></p>
<p>Yields:</p>
<p>List of started threading.Thread instances (daemon=True).</p>
</div>
</details>
</li>
<li><code>test_ds_contradiction_detection</code> (test_hypothesis_engine.py)
<details><summary>Dempster-Shafer contradiction detection via plausibility comparison.</summary>
<div class="doc-comment">
<p>Dempster-Shafer contradiction detection via plausibility comparison.</p>
<p></p>
<p>When two hypotheses have similar belief/plausibility values after evidence,</p>
<p>it indicates the evidence supports multiple paths — a form of contradiction.</p>
<p></p>
<p>Note: The conflict_mass() in this implementation accumulates K during add_evidence.</p>
<p>Testing detect_contradiction with a threshold that matches actual behavior.</p>
</div>
</details>
</li>
<li><code>test_aclose_timeout_does_not_block_forever</code> (test_sprint8ax_duckdb_shadow.py) — <span class="doc-comment-inline">aclose() with a stuck store should not block longer than its timeout.</span></li>
<li><code>test_aclose_has_log_output</code> (test_sprint_scheduler.py) — <span class="doc-comment-inline">aclean() must log completion with sprint_id and elapsed time.</span></li>
<li><code>assert_no_leak</code> (memory_profiler.py)</li>
<li><code>test_unprotected_coroutine_can_hang</code> (test_coroutine_cleanup.py)
<details><summary>COROUTINE LEAK: Coroutine without timeout can hang indefinitely.</summary>
<div class="doc-comment">
<p>COROUTINE LEAK: Coroutine without timeout can hang indefinitely.</p>
<p></p>
<p>This test demonstrates the BUGGY pattern - without timeout protection,</p>
<p>a coroutine that takes too long will block indefinitely.</p>
</div>
</details>
</li>
<li><code>test_tool_exec_event_serialization</code> (test_correlation_propagation.py) — <span class="doc-comment-inline">ToolExecEvent.to_dict() includes correlation when present.</span></li>
<li><code>session_event_loop</code> (conftest.py)
<details><summary>Session-scoped event loop for pytest-asyncio.</summary>
<div class="doc-comment">
<p>Session-scoped event loop for pytest-asyncio.</p>
<p>Reuses one loop across all tests instead of creating per-test.</p>
<p>Required for asyncio_default_fixture_loop_scope = "session".</p>
<p></p>
<p>Task leak guard: at teardown, any unresolved tasks are cancelled and</p>
<p>logged so CI can detect forgotten task cleanup without false positives.</p>
</div>
</details>
</li>
<li><code>count_mock_methods</code> (spec_mocks.py)
<details><summary>Count configured vs auto-created mock methods.</summary>
<div class="doc-comment">
<p>Count configured vs auto-created mock methods.</p>
<p></p>
<p>Useful for identifying overly-permissive mocks:</p>
<p>info = count_mock_methods(test_mock)</p>
<p>assert info['configured'] &gt; info['auto_created'], "Mock too permissive"</p>
<p></p>
<p>Returns:</p>
<p>dict with 'configured' (explicitly set) and 'auto_created' counts</p>
</div>
</details>
</li>
<li><code>test_hermes_prewarm_failsoft_continues_without_ToT</code> (test_sprint_scheduler.py)</li>
<li><code>test_v2_slots_initialized_on_construction</code> (test_sprint_scheduler.py)
<details><summary>V2: construction initializes all slots via __post_init__.</summary>
<div class="doc-comment">
<p>V2: construction initializes all slots via __post_init__.</p>
<p></p>
<p>SprintSchedulerV2 uses @dataclass(slots=True) — all fields must be</p>
<p>set to None/initial values in __post_init__. This test verifies</p>
<p>the core orchestrator slots are accessible after construction.</p>
</div>
</details>
</li>
<li><code>mock_cleanup</code> (spec_mocks.py)
<details><summary>Context manager: collect and reset MagicMock instances on exit.</summary>
<div class="doc-comment">
<p>Context manager: collect and reset MagicMock instances on exit.</p>
<p></p>
<p>Fixes mock memory leaks in pytest:</p>
<p>• Clears _mock_children dicts (each holds 50-100 KB unreferenced)</p>
<p>• Resets call counts and side effects</p>
<p>• Triggers gc.collect() to free mock object memory</p>
<p></p>
<p>Usage:</p>
<p>def test_something():</p>
<p>with mock_cleanup():</p>
<p>scheduler = _make_scheduler_base()</p>
<p># ... test code ...</p>
<p># All mocks cleaned up after</p>
<p></p>
<p>Args:</p>
<p>*mocks: MagicMock/AsyncMock instances to clean up</p>
<p></p>
<p>M1 8GB impact:</p>
<p>• 30+ mock instances per test → 1.5-3 MB freed on exit</p>
<p>• gc.collect() clears ~5-10 MB of accumulated _mock_children</p>
</div>
</details>
</li>
<li><code>test_async_generator_early_exit_leak</code> (test_coroutine_cleanup.py)
<details><summary>COROUTINE LEAK: async generator without aclose() on break.</summary>
<div class="doc-comment">
<p>COROUTINE LEAK: async generator without aclose() on break.</p>
<p></p>
<p>Without aclose(), the generator's __anext__ coroutine holds:</p>
<p>- Parent function's local variables</p>
<p>- Pending items list</p>
<p>- Any captured context</p>
<p></p>
<p>Memory impact: 5-20 KB per leaked generator.</p>
</div>
</details>
</li>
<li><code>test_full_pipeline_cleanup</code> (test_coroutine_cleanup.py) — <span class="doc-comment-inline">Full pipeline with proper cleanup.</span></li>
<li><code>test_many_leaked_generators_memory_impact</code> (test_coroutine_cleanup.py)
<details><summary>Verify: 1000 leaked generators ≈ 15-20 MB memory.</summary>
<div class="doc-comment">
<p>Verify: 1000 leaked generators ≈ 15-20 MB memory.</p>
<p></p>
<p>This test documents the memory cost of coroutine leaks.</p>
<p>Run with memory profiler to verify:</p>
<p>```</p>
<p>pip install memory_profiler</p>
<p>mprof run pytest tests/test_coroutine_cleanup.py::TestMemoryImpact::test_many_leaked_generators_memory_impact</p>
<p>mprof plot</p>
<p>```</p>
</div>
</details>
</li>
<li><code>test_evidence_event_serialization_stable</code> (test_correlation_propagation.py) — <span class="doc-comment-inline">EvidenceEvent.to_dict() serialization includes correlation when present.</span></li>
<li><code>test_log_with_correlation</code> (test_correlation_propagation.py) — <span class="doc-comment-inline">log() accepts correlation and stores in ToolExecEvent.correlation.</span></li>
<li><code>test_tool_exec_event_from_dict_with_correlation</code> (test_correlation_propagation.py) — <span class="doc-comment-inline">ToolExecEvent.from_dict() correctly deserializes correlation.</span></li>
<li><code>test_shadow_fail_open_queue_drop_when_full</code> (test_sprint8ax_duckdb_shadow.py) — <span class="doc-comment-inline">When the queue is full, records are dropped and _SHADOW_INGEST_FAILURES is incremented.</span></li>
<li><code>_get_python_domain</code> (test_differential_fuzzing.py)
<details><summary>Get pure Python domain instance for differential testing.</summary>
<div class="doc-comment">
<p>Get pure Python domain instance for differential testing.</p>
<p></p>
<p>Creates Python domain directly without triggering RustBackend initialization.</p>
<p>This avoids importing the module-level `rust = RustBackend()` singleton.</p>
</div>
</details>
</li>
<li><code>assert_leak_threshold</code> (memory_profiler.py)
<details><summary>Assert RSS delta is below threshold, with detailed failure message.</summary>
<div class="doc-comment">
<p>Assert RSS delta is below threshold, with detailed failure message.</p>
<p></p>
<p>Args:</p>
<p>threshold_mb: Override instance threshold. Uses instance value if None.</p>
<p></p>
<p>Raises:</p>
<p>AssertionError: If RSS grows beyond threshold, with detailed</p>
<p>breakdown showing tracemalloc allocation growth.</p>
</div>
</details>
</li>
<li><code>test_pipeline_with_timeout</code> (test_coroutine_cleanup.py)
<details><summary>FIXED: Pipeline operation with timeout protection.</summary>
<div class="doc-comment">
<p>FIXED: Pipeline operation with timeout protection.</p>
<p></p>
<p>Pattern for test_e2e_pipeline_smoke.py fix:</p>
<p>```python</p>
<p>async def run_pipeline():</p>
<p>try:</p>
<p>async with asyncio.timeout(120.0):</p>
<p>result = await pipeline.run()</p>
<p>return result</p>
<p>except asyncio.TimeoutError:</p>
<p>return None  # or handle gracefully</p>
<p>```</p>
</div>
</details>
</li>
<li><code>test_create_event_with_correlation_flat</code> (test_correlation_propagation.py) — <span class="doc-comment-inline">create_event accepts correlation dict and stores in payload._correlation.</span></li>
<li><code>test_shadow_flag_off_is_noop</code> (test_sprint8ax_duckdb_shadow.py) — <span class="doc-comment-inline">With GHOST_DUCKDB_SHADOW=0, no shadow records are written.</span></li>
<li><code>test_record_hypothesis_feedback_failsoft_does_not_crash</code> (test_sprint_scheduler.py)
<details><summary>L4954: record_hypothesis_feedback() exception handler.</summary>
<div class="doc-comment">
<p>L4954: record_hypothesis_feedback() exception handler.</p>
<p>verify: exception in store does NOT propagate (fail-safe pattern).</p>
</div>
</details>
</li>
<li><code>test_synthesis_sidecar_skipped_when_uma_emergency</code> (test_sprint_scheduler.py)
<details><summary>F259: UMA emergency → synthesis skipped.</summary>
<div class="doc-comment">
<p>F259: UMA emergency → synthesis skipped.</p>
<p>verify: _result.synthesis_engine = "uma_guard".</p>
</div>
</details>
</li>
<li><code>test_gate_requires_store_and_hermes_and_stored</code> (test_sprint_p12_hypothesis.py)
<details><summary>P12 gate requires ALL THREE: store is not None AND hermes_engine is not None AND total_stored &gt; 0.</summary>
<div class="doc-comment">
<p>P12 gate requires ALL THREE: store is not None AND hermes_engine is not None AND total_stored &gt; 0.</p>
<p>Canonical sprint DI wire: hermes_engine travels with duckdb_store into pipeline.</p>
</div>
</details>
</li>
<li><code>test_noop_lane_rule_called_once</code> (test_f227k_acquisition_lane_parity.py) — <span class="doc-comment-inline">Each LaneRule.enabled/reason/concurrency is called exactly once per plan build.</span></li>
<li><code>test_safe_wait_for_from_async_helpers</code> (test_coroutine_cleanup.py)
<details><summary>F320: safe_wait_for() wrapper from utils/async_helpers.</summary>
<div class="doc-comment">
<p>F320: safe_wait_for() wrapper from utils/async_helpers.</p>
<p></p>
<p>Preferred pattern for Python 3.14+ compatibility:</p>
<p>```python</p>
<p>from utils.async_helpers import safe_wait_for</p>
<p>result = await safe_wait_for(coro, timeout=30.0)</p>
<p>```</p>
</div>
</details>
</li>
<li><code>test_analytics_hook_fail_open_without_shadow</code> (test_correlation_propagation.py) — <span class="doc-comment-inline">shadow_record_finding is fail-open when shadow disabled.</span></li>
<li><code>test_shadow_failure_increments_warning_counter</code> (test_sprint8ax_duckdb_shadow.py) — <span class="doc-comment-inline">Shadow failures are fail-open: they increment the counter but never raise.</span></li>
<li><code>mock_lifecycle</code> (test_sprint_scheduler.py) — <span class="doc-comment-inline">Minimal lifecycle mock for run() entry point.</span></li>
<li><code>mock_adapter</code> (test_sprint_scheduler.py) — <span class="doc-comment-inline">Lifecycle adapter mock — converts runtime.lifecycle to adapter interface.</span></li>
<li><code>test_synthesis_sidecar_graceful_on_error</code> (test_sprint_scheduler.py)
<details><summary>F259: Exception in synthesis → graceful degradation.</summary>
<div class="doc-comment">
<p>F259: Exception in synthesis → graceful degradation.</p>
<p>verify: _result fields updated but no crash.</p>
</div>
</details>
</li>
<li><code>test_extract_iocs</code> (test_rust_backend.py) — <span class="doc-comment-inline">extract_iocs returns dict of IOC type -&gt; list of values (grouped format).</span></li>
<li><code>init_session_tracer</code> (memory_profiler.py)
<details><summary>Start the session-scoped tracemalloc tracer (idempotent).</summary>
<div class="doc-comment">
<p>Start the session-scoped tracemalloc tracer (idempotent).</p>
<p></p>
<p>Call once at pytest session start (e.g. in conftest.py session fixture).</p>
<p>Does NOT stop the tracer — use stop_session_tracer() at teardown.</p>
<p></p>
<p>Args:</p>
<p>nframes: Number of stack frames to trace (default: _TM_NFRAMES).</p>
<p>Higher = more detail, more memory (~8 KB per frame).</p>
<p></p>
<p>Returns:</p>
<p>True if tracer is now active.</p>
</div>
</details>
</li>
<li><code>test_extract_intel_from_torrent</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test OSINT extraction from torrent metadata.</span></li>
<li><code>test_create_event_correlation_partial</code> (test_correlation_propagation.py) — <span class="doc-comment-inline">Correlation can be partial - only some keys present.</span></li>
<li><code>test_production_db_path_is_analytics_duckdb</code> (test_sprint8ax_duckdb_shadow.py)
<details><summary>When RAMDISK is inactive and DB_ROOT is available,</summary>
<div class="doc-comment">
<p>When RAMDISK is inactive and DB_ROOT is available,</p>
<p>the DuckDB store should use DB_ROOT / "analytics.duckdb" as the path.</p>
</div>
</details>
</li>
<li><code>test_scheduler_healthy_after_multiple_failsoft_paths</code> (test_sprint_scheduler.py)
<details><summary>Verify: after multiple fail-soft handlers, scheduler is still usable.</summary>
<div class="doc-comment">
<p>Verify: after multiple fail-soft handlers, scheduler is still usable.</p>
<p>This is the PRIMARY behavioral assertion — scheduler must not crash.</p>
</div>
</details>
</li>
<li><code>test_pvs_commoncrawl_field_present</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">product_value_summary includes commoncrawl_archive_augmented when CC is active.</span></li>
<li><code>temp_duckdb_store</code> (test_e2e_first_finding.py)
<details><summary>Create a DuckDB store backed by a temp directory.</summary>
<div class="doc-comment">
<p>Create a DuckDB store backed by a temp directory.</p>
<p>Isolated: persistent dedup LMDB is bypassed so test findings aren't</p>
<p>rejected as duplicates from previous runs.</p>
<p>Cleaned up after test. Hermetic: no shared dedup state.</p>
</div>
</details>
</li>
<li><code>test_prefetch_oracle_suggest_scores_failsoft_returns_empty</code> (test_sprint_scheduler.py)
<details><summary>L4786: prefetch_oracle.suggest_scores exception handler.</summary>
<div class="doc-comment">
<p>L4786: prefetch_oracle.suggest_scores exception handler.</p>
<p>verify: exception causes fallback to empty dict (default ordering preserved).</p>
</div>
</details>
</li>
<li><code>test_prefetch_oracle_suggest_scores_fallback_preserves_ordering</code> (test_sprint_scheduler.py)
<details><summary>L4786: verify fallback produces empty oracle_scores dict.</summary>
<div class="doc-comment">
<p>L4786: verify fallback produces empty oracle_scores dict.</p>
<p>When suggest_scores fails, oracle_scores = {} and oracle_mult = 1.0 for all items.</p>
</div>
</details>
</li>
<li><code>test_privacy_context_init_failsoft_does_not_crash</code> (test_sprint_scheduler.py)
<details><summary>L5144 &amp; L5199: privacy_context init exception handlers.</summary>
<div class="doc-comment">
<p>L5144 &amp; L5199: privacy_context init exception handlers.</p>
<p>verify: exception in create_privacy_context does NOT crash __init__.</p>
</div>
</details>
</li>
<li><code>_ensure_r0_artifacts</code> (conftest.py) — <span class="doc-comment-inline">Run R0 probe runner if artifacts are stale (env-gated).</span></li>
<li><code>test_batch_cosine_similarity</code> (test_differential_fuzzing.py)
<details><summary>batch_cosine_similarity musí vracet stejné výsledky.</summary>
<div class="doc-comment">
<p>batch_cosine_similarity musí vracet stejné výsledky.</p>
<p></p>
<p>F5.3: Zero-vector inputs ([0.0]) dávají různé výsledky mezi Python a Rust.</p>
<p>Filtrujeme zero-vector query a zero-length vectors.</p>
</div>
</details>
</li>
<li><code>test_stores_spans</code> (test_t1_otel.py)</li>
<li><code>_track</code> (test_sprint8l_live.py)</li>
<li><code>test_loop_close_without_cancel_leaves_tasks</code> (test_coroutine_cleanup.py)
<details><summary>COROUTINE LEAK: loop.close() without cancelling pending tasks.</summary>
<div class="doc-comment">
<p>COROUTINE LEAK: loop.close() without cancelling pending tasks.</p>
<p></p>
<p>This test verifies that proper cleanup patterns work.</p>
<p>In production, always cancel tasks before closing the loop.</p>
</div>
</details>
</li>
<li><code>test_governor_evaluate_failsoft_continues</code> (test_sprint_scheduler.py)
<details><summary>L5529: governor.evaluate() exception handler.</summary>
<div class="doc-comment">
<p>L5529: governor.evaluate() exception handler.</p>
<p>verify: evaluate failure → no concurrency change (advisory only).</p>
</div>
</details>
</li>
<li><code>_make_runner_mock</code> (conftest.py)
<details><summary>Runner mock s konzistentními default return hodnotami.</summary>
<div class="doc-comment">
<p>Runner mock s konzistentními default return hodnotami.</p>
<p></p>
<p>Uses spec=SprintLifecycleRunner to restrict mock to real attributes only,</p>
<p>preventing unbounded _mock_children growth (Issue 5.6).</p>
<p>Extra runtime state (current_phase, abort_requested, last_guard_observation)</p>
<p>is attached as plain attributes — allowed because they don't conflict with</p>
<p>spec-class members.</p>
</div>
</details>
</li>
<li><code>_wrapped_execute</code> (test_sprint8l_live.py)</li>
<li><code>test_drain_stats_monotonic_counters</code> (test_sprint_f273.py) — <span class="doc-comment-inline">FIX F350M-R: Use session_event_loop fixture instead of asyncio.run().</span></li>
<li><code>test_all_lanes_have_rules</code> (test_f227k_acquisition_lane_parity.py) — <span class="doc-comment-inline">Every AcquisitionLane value has a corresponding LaneRule.</span></li>
<li><code>test_shadow_hook_location_is_not_ao</code> (test_sprint8ax_duckdb_shadow.py)
<details><summary>The shadow hook must NOT be added to autonomous_orchestrator.py.</summary>
<div class="doc-comment">
<p>The shadow hook must NOT be added to autonomous_orchestrator.py.</p>
<p>autonomous_orchestrator.py was merged into core/ in F314.</p>
<p>The hook must live in evidence_log.py or analytics_hook.py.</p>
</div>
</details>
</li>
<li><code>test_real_async_feedback_recording_does_not_crash</code> (test_sprint_scheduler.py)
<details><summary>L4954: Real async test — verify record_hypothesis_feedback pattern</summary>
<div class="doc-comment">
<p>L4954: Real async test — verify record_hypothesis_feedback pattern</p>
<p>(exception in store does not propagate).</p>
</div>
</details>
</li>
<li><code>test_single_branch_gives_5_points</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">1 active branch → 5 points.</span></li>
<li><code>test_pvs_academic_field_present</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">product_value_summary includes academic_discovery_contribution when academic is active.</span></li>
<li><code>_get_rust_domain</code> (test_differential_fuzzing.py) — <span class="doc-comment-inline">Get Rust domain instance for differential testing.</span></li>
<li><code>test_parse_ip_fast</code> (test_differential_fuzzing.py)
<details><summary>parse_ip_fast musí vracet konzistentní výsledky (buď str nebo tuple).</summary>
<div class="doc-comment">
<p>parse_ip_fast musí vracet konzistentní výsledky (buď str nebo tuple).</p>
<p></p>
<p>F5.3: API MISMATCH — Python vrací tuple (int, version), Rust vrací str.</p>
<p>Toto je fundamentální API rozdíl, skipáme bit-identical test.</p>
</div>
</details>
</li>
<li><code>test_aggressive_mode_blocks_until_hermes_prewarm</code> (test_sprint_p12_hypothesis.py)
<details><summary>Aggressive mode: _prewarm_hermes_for_sprint blocks until Hermes is loaded.</summary>
<div class="doc-comment">
<p>Aggressive mode: _prewarm_hermes_for_sprint blocks until Hermes is loaded.</p>
<p>The prewarm call is synchronous from run() — no async fan-out until prewarm completes.</p>
</div>
</details>
</li>
<li><code>test_concurrent_spans_thread_safe</code> (test_t1_otel.py)</li>
<li><code>test_feed_dominance_guard_strict_blocks_early_exit</code> (test_rust_backend.py) — <span class="doc-comment-inline">FeedDominanceGuard strict=True blocks early exit when guard triggered.</span></li>
<li><code>_domain_in_traceback</code> (memory_profiler.py)
<details><summary>Return True if traceback contains any of the given domain prefixes.</summary>
<div class="doc-comment">
<p>Return True if traceback contains any of the given domain prefixes.</p>
<p></p>
<p>Matches three formats tracemalloc can produce:</p>
<p>1. Absolute paths:  /Users/.../hledac/brain/engine.py:123</p>
<p>2. Relative paths:  core/resource_governor.py:200, tests/utils/memory_profiler.py:160</p>
<p>3. Dot-notation:    hledac.universal.core.resource_governor:42</p>
<p></p>
<p>Filters noisy third-party allocations (psutil, tracemalloc internals, etc.)</p>
<p>while keeping only allocations from the project's modules.</p>
</div>
</details>
</li>
<li><code>test_min_branch_remaining_s_fallback_cycle_ema_formula</code> (test_sprint_f273.py)
<details><summary>Fallback (no remaining_s arg) uses 0.1 * cycle_ema, clamped [2, 5].</summary>
<div class="doc-comment">
<p>Fallback (no remaining_s arg) uses 0.1 * cycle_ema, clamped [2, 5].</p>
<p>This tests backward compatibility when remaining_s is None.</p>
</div>
</details>
</li>
<li><code>test_drain_completes_pending_futures</code> (test_sprint_f273.py) — <span class="doc-comment-inline">FIX F350M-R: Use session_event_loop fixture instead of asyncio.run().</span></li>
<li><code>test_finalizers_invoked_on_exit</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">weakref finalizers are invoked when MemoryTracker exits.</span></li>
<li><code>test_tracemalloc_snapshot_uses_session_mode</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">TracemallocSnapshot detects session tracer and sets _session_mode=True.</span></li>
<li><code>test_discovery_coroutine_has_timeout</code> (test_coroutine_cleanup.py)
<details><summary>F271B: _ASYNC_DISCOVERY_SEARCH must use asyncio.wait_for(timeout=35.0).</summary>
<div class="doc-comment">
<p>F271B: _ASYNC_DISCOVERY_SEARCH must use asyncio.wait_for(timeout=35.0).</p>
<p></p>
<p>This test verifies the pattern exists in the codebase.</p>
<p>Actual implementation should be:</p>
<p>```python</p>
<p>result = await asyncio.wait_for(</p>
<p>_async_discovery_search(...),</p>
<p>timeout=35.0</p>
<p>)</p>
<p>```</p>
</div>
</details>
</li>
<li><code>test_extract_hashes</code> (test_hypothesis_engine.py) — <span class="doc-comment-inline">Finding with MD5/SHA256 → lateral hypothesis with hash: pivot seed.</span></li>
<li><code>test_tracemalloc_start_failsoft_disables_tracing</code> (test_sprint_scheduler.py)
<details><summary>L5379: tracemalloc.start exception handler.</summary>
<div class="doc-comment">
<p>L5379: tracemalloc.start exception handler.</p>
<p>verify: failure sets _trace_enabled = False (prevents finally crash).</p>
</div>
</details>
</li>
<li><code>test_evidence_chain_builder_failsoft_continues</code> (test_sprint_scheduler.py)
<details><summary>L5423: EvidenceChainBuilder init exception handler.</summary>
<div class="doc-comment">
<p>L5423: EvidenceChainBuilder init exception handler.</p>
<p>verify: set_global_builder fails → chain tracking skipped (advisory only).</p>
</div>
</details>
</li>
<li><code>test_full_run_returns_all_required_depth_signals_keys</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">depth_signals always has all 8 signal keys.</span></li>
<li><code>test_moderate_level_with_deep_sources</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">Deep sources + no corroboration → moderate.</span></li>
<li><code>test_extract_iocs_returns_valid_types</code> (test_differential_fuzzing.py)
<details><summary>extract_iocs musí vracet konzistentní sadu IOC typů.</summary>
<div class="doc-comment">
<p>extract_iocs musí vracet konzistentní sadu IOC typů.</p>
<p></p>
<p>F5.3: API MISMATCH — Python dict vs Rust list. Skipáme bit-identical test.</p>
<p>Testujeme pouze že obě implementace vrací nějaké výsledky.</p>
</div>
</details>
</li>
<li><code>test_tot_not_in_hot_path</code> (test_sprint_p12_hypothesis.py) — <span class="doc-comment-inline">ToT runs after fetch+storage, not in the hot discovery-to-fetch path.</span></li>
<li><code>test_load_via_model_manager</code> (test_sprint_p12_hypothesis.py)
<details><summary>_load_hermes_for_sprint uses ModelManager.load_model("hermes").</summary>
<div class="doc-comment">
<p>_load_hermes_for_sprint uses ModelManager.load_model("hermes").</p>
<p>Verifies canonical Hermes lifecycle owner is ModelManager.</p>
</div>
</details>
</li>
<li><code>test_teardown_still_releases_hermes_after_prewarm</code> (test_sprint_p12_hypothesis.py)
<details><summary>After successful prewarm+load, teardown still calls _unload_hermes_at_teardown.</summary>
<div class="doc-comment">
<p>After successful prewarm+load, teardown still calls _unload_hermes_at_teardown.</p>
<p>Verifies bounded lifecycle: load at BOOT, release at TEARDOWN.</p>
</div>
</details>
</li>
<li><code>test_max_attrs_truncation</code> (test_t1_otel.py)</li>
<li><code>stop</code> (memory_profiler.py)
<details><summary>Stop tracemalloc — ONLY in legacy (non-session) mode.</summary>
<div class="doc-comment">
<p>Stop tracemalloc — ONLY in legacy (non-session) mode.</p>
<p></p>
<p>In session-scoped tracer mode, this is a no-op: the session tracer</p>
<p>is owned by init_session_tracer() / stop_session_tracer() and must</p>
<p>not be stopped by individual snapshot instances.</p>
<p></p>
<p>Safe to call multiple times (idempotent in both modes).</p>
</div>
</details>
</li>
<li><code>detect_ner_fallback</code> (test_sprint8l_live.py)</li>
<li><code>test_sprint_flags_hermes_force_constructible</code> (test_sprint_f273.py) — <span class="doc-comment-inline">SprintFlags(hermes_force=True) must work without breaking other fields.</span></li>
<li><code>test_wait_for_prevents_infinite_hang</code> (test_coroutine_cleanup.py)
<details><summary>FIXED: asyncio.wait_for prevents infinite hangs.</summary>
<div class="doc-comment">
<p>FIXED: asyncio.wait_for prevents infinite hangs.</p>
<p></p>
<p>Correct pattern (F271B reference):</p>
<p>```python</p>
<p>result = await asyncio.wait_for(</p>
<p>some_coroutine(),</p>
<p>timeout=35.0  # Match F271B spec</p>
<p>)</p>
<p>```</p>
</div>
</details>
</li>
<li><code>test_run_correlation_to_dict</code> (test_correlation_propagation.py) — <span class="doc-comment-inline">RunCorrelation.to_dict() returns serializable dict.</span></li>
<li><code>memory_snapshot</code> (conftest.py)
<details><summary>Per-test RSS memory snapshot — takes RSS on enter, provides delta on exit.</summary>
<div class="doc-comment">
<p>Per-test RSS memory snapshot — takes RSS on enter, provides delta on exit.</p>
<p></p>
<p>Usage:</p>
<p>def test_something(memory_snapshot):</p>
<p>before = memory_snapshot.rss_mb</p>
<p># ... test code ...</p>
<p>delta = memory_snapshot.delta_mb()</p>
<p>assert delta &lt; 50</p>
<p></p>
<p>Always-on, fail-safe: returns None if psutil unavailable.</p>
</div>
</details>
</li>
<li><code>test_all_new_sources_diverse_contributes_high_diversity</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">5 diverse sources including new types → high source_diversity score.</span></li>
<li><code>test_gate_uses_store_and_engine</code> (test_sprint_p12_hypothesis.py) — <span class="doc-comment-inline">P12 gate condition: store is not None AND hermes_engine is not None.</span></li>
<li><code>test_context_not_from_rag_alone</code> (test_sprint_p12_hypothesis.py) — <span class="doc-comment-inline">P12 context uses stored findings count, not rag_context alone.</span></li>
<li><code>_python_extract_iocs</code> (test_hledac_core_rust.py)</li>
<li><code>test_thread_safety</code> (test_t1_otel.py)</li>
<li><code>test_int_overflow_safe</code> (test_t1_otel.py) — <span class="doc-comment-inline">int64 overflow -&gt; 0 (fail-soft, never crash).</span></li>
<li><code>test_feed_dominance_guard_compute_balanced</code> (test_rust_backend.py) — <span class="doc-comment-inline">FeedDominanceGuard.compute returns balanced result.</span></li>
<li><code>test_feed_dominance_guard_compute_feed_dominant</code> (test_rust_backend.py) — <span class="doc-comment-inline">FeedDominanceGuard.compute detects feed dominance.</span></li>
<li><code>test_min_branch_remaining_s_bounded_2_to_5</code> (test_sprint_f273.py) — <span class="doc-comment-inline">Floor is always in [2.0, 5.0] for any remaining_s or cycle_ema.</span></li>
<li><code>test_hypothesis_type_temporal_requires_depth</code> (test_hypothesis_engine.py)
<details><summary>sprint_depth=1 → limited hypotheses (no temporal).</summary>
<div class="doc-comment">
<p>sprint_depth=1 → limited hypotheses (no temporal).</p>
<p>sprint_depth=2 → temporal hypotheses may appear for registered domains.</p>
</div>
</details>
</li>
<li><code>test_sprint_id_getattr_failsoft_defaults_to_empty</code> (test_sprint_scheduler.py)
<details><summary>L5233: sprint_id getattr exception handler.</summary>
<div class="doc-comment">
<p>L5233: sprint_id getattr exception handler.</p>
<p>verify: getattr(lifecycle, "sprint_id", "") raises → sprint_id = "".</p>
</div>
</details>
</li>
<li><code>test_synthesis_sidecar_skipped_when_env_disabled</code> (test_sprint_scheduler.py)
<details><summary>F259: HLEDAC_ENABLE_HERMES_SYNTHESIS=0 (default) → synthesis skipped.</summary>
<div class="doc-comment">
<p>F259: HLEDAC_ENABLE_HERMES_SYNTHESIS=0 (default) → synthesis skipped.</p>
<p>verify: _result fields remain at defaults.</p>
</div>
</details>
</li>
<li><code>test_sprint_scheduler_result_synthesis_fields_exist</code> (test_sprint_scheduler.py)
<details><summary>F259: SprintSchedulerResult has all required synthesis fields.</summary>
<div class="doc-comment">
<p>F259: SprintSchedulerResult has all required synthesis fields.</p>
<p>verify: fields exist with correct default values.</p>
</div>
</details>
</li>
<li><code>test_deep_level_with_corrob_and_branches</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">Corroborated + branches active → deep level.</span></li>
<li><code>test_comprehensive_level_at_maximum</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">All signals active + 3 branches → comprehensive level.</span></li>
<li><code>test_pvs_zero_when_missing_canonical_run_summary</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">Both fields default to 0 when canonical_run_summary is absent.</span></li>
<li><code>test_fingerprint_stability</code> (test_differential_fuzzing.py)
<details><summary>Fingerprint URL musí být stabilní a konzistentní.</summary>
<div class="doc-comment">
<p>Fingerprint URL musí být stabilní a konzistentní.</p>
<p></p>
<p>F5.3: API MISMATCH — Python vrací str (hex), Rust vrací int.</p>
<p>Testujeme semantic equivalence: obě representace jsou validní fingerprinty.</p>
<p>Skipáme http://0 a podobné edge cases kde hostname parsing diverguje.</p>
</div>
</details>
</li>
<li><code>test_strip_tracking</code> (test_differential_fuzzing.py)
<details><summary>Strip tracking musí odstranit UTM a podobné parametry.</summary>
<div class="doc-comment">
<p>Strip tracking musí odstranit UTM a podobné parametry.</p>
<p></p>
<p>F5.3: Rust _RustUrlDomain nemá strip_tracking() metodu.</p>
<p>Test pouze srovnává Python fallback vs Python fallback (no-op pro Rust path).</p>
</div>
</details>
</li>
<li><code>test_bloom_filter_consistency</code> (test_differential_fuzzing.py) — <span class="doc-comment-inline">BloomFilter add/contains musí být konzistentní.</span></li>
<li><code>test_no_tot_block_before_fetch_batch</code> (test_sprint_p12_hypothesis.py) — <span class="doc-comment-inline">ToT/hypothesis block is NOT placed before the fetch batch.</span></li>
<li><code>test_string_value_bounded</code> (test_t1_otel.py)</li>
<li><code>_get_psutil_process</code> (memory_profiler.py)
<details><summary>Get or create a cached psutil.Process for the current PID.</summary>
<div class="doc-comment">
<p>Get or create a cached psutil.Process for the current PID.</p>
<p></p>
<p>Avoids 50 KB allocation per get_rss_mb() call by caching the Process object.</p>
<p>Refreshes the cached object every 10s or when PID changes.</p>
</div>
</details>
</li>
<li><code>register_allocation</code> (memory_profiler.py)
<details><summary>Register a large object for weakref-based finalization safety net.</summary>
<div class="doc-comment">
<p>Register a large object for weakref-based finalization safety net.</p>
<p></p>
<p>Issue #12 fix: pytest fixtures can crash before __exit__ cleanup, leaving</p>
<p>large objects pinned in memory. weakref.finalize() guarantees __del__ runs</p>
<p>even if the fixture crashes mid-test.</p>
<p></p>
<p>Args:</p>
<p>obj: Large object to track.</p>
<p>name: Optional name for debugging.</p>
<p></p>
<p>Returns:</p>
<p>weakref.finalize object — call .detach() to cancel tracking.</p>
</div>
</details>
</li>
<li><code>__exit__</code> (memory_profiler.py)</li>
<li><code>test_tracemalloc_snapshot_legacy_mode</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">TracemallocSnapshot falls back to legacy mode when no session tracer.</span></li>
<li><code>test_task_without_reference_is_orphaned</code> (test_coroutine_cleanup.py)
<details><summary>COROUTINE LEAK: create_task without saving reference.</summary>
<div class="doc-comment">
<p>COROUTINE LEAK: create_task without saving reference.</p>
<p></p>
<p>When a task is created but not saved:</p>
<p>- Task runs to completion independently</p>
<p>- Cannot be cancelled if needed</p>
<p>- Reference held only by GC until collection</p>
</div>
</details>
</li>
<li><code>test_init_with_correlation</code> (test_correlation_propagation.py) — <span class="doc-comment-inline">MetricsRegistry.__init__ accepts correlation and stores it.</span></li>
<li><code>test_generate_with_dspy_enabled_but_unavailable</code> (test_hypothesis_engine.py)
<details><summary>DSPy enabled but _load_dspy_program returns None → fallback to heuristic.</summary>
<div class="doc-comment">
<p>DSPy enabled but _load_dspy_program returns None → fallback to heuristic.</p>
<p>No exception propagated, output is valid ResearchHypothesis list.</p>
</div>
</details>
</li>
<li><code>test_generate_with_dspy_forward_exception</code> (test_hypothesis_engine.py)
<details><summary>DSPy forward() raises → _heuristic_generate fallback is triggered.</summary>
<div class="doc-comment">
<p>DSPy forward() raises → _heuristic_generate fallback is triggered.</p>
<p>Exception is caught, no propagation.</p>
</div>
</details>
</li>
<li><code>test_ds_round_trip_serialization</code> (test_hypothesis_engine.py) — <span class="doc-comment-inline">to_dict() → from_dict() → belief() == original.</span></li>
<li><code>test_extract_ips</code> (test_hypothesis_engine.py) — <span class="doc-comment-inline">Finding with known IP → IP extracted into hypothesis pivot_seeds.</span></li>
<li><code>test_extract_domains</code> (test_hypothesis_engine.py) — <span class="doc-comment-inline">Finding with known domain → domain extracted into hypothesis.</span></li>
<li><code>test_all_four_hypothesis_types_generated</code> (test_hypothesis_engine.py)
<details><summary>Input triggers entity_expansion, temporal, lateral, adversarial.</summary>
<div class="doc-comment">
<p>Input triggers entity_expansion, temporal, lateral, adversarial.</p>
<p>Assert all 4 types appear in output.</p>
</div>
</details>
</li>
<li><code>canned_ct_adapter</code> (test_e2e_first_finding.py) — <span class="doc-comment-inline">Patch CTLogClient.pivot_domain to return canned CT findings.</span></li>
<li><code>session_otel_tracer</code> (conftest.py)
<details><summary>Session-scoped OTel tracer — initialized once, shared across tests.</summary>
<div class="doc-comment">
<p>Session-scoped OTel tracer — initialized once, shared across tests.</p>
<p>Exports to console (JSON-Lines) to avoid file I/O overhead.</p>
</div>
</details>
</li>
<li><code>test_full_run_returns_all_required_breakdown_keys</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">breakdown always has all 5 component keys.</span></li>
<li><code>test_multiple_diverse_sources_high_diversity</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">3+ diverse sources with even distribution → high diversity score.</span></li>
<li><code>test_nan_inf_safe</code> (test_t1_otel.py)</li>
<li><code>weak_mock</code> (spec_mocks.py)
<details><summary>Wrap mock in weakref to detect when all references are gone.</summary>
<div class="doc-comment">
<p>Wrap mock in weakref to detect when all references are gone.</p>
<p></p>
<p>Usage:</p>
<p>ref = weak_mock(some_mock)</p>
<p>del some_mock</p>
<p>gc.collect()</p>
<p>assert ref() is None, "Mock still referenced!"</p>
<p></p>
<p>Args:</p>
<p>mock: MagicMock/AsyncMock to wrap</p>
<p></p>
<p>Returns:</p>
<p>Weak reference to the mock</p>
</div>
</details>
</li>
<li><code>assert_no_leak</code> (memory_profiler.py)
<details><summary>Assert that RSS delta from snapshot is below threshold.</summary>
<div class="doc-comment">
<p>Assert that RSS delta from snapshot is below threshold.</p>
<p></p>
<p>Args:</p>
<p>threshold_mb: Maximum acceptable RSS growth in MB.</p>
<p></p>
<p>Raises:</p>
<p>AssertionError: If delta exceeds threshold.</p>
</div>
</details>
</li>
<li><code>__post_init__</code> (memory_profiler.py)</li>
<li><code>test_apply_nocache_below_threshold_returns_false</code> (test_sprint_f273.py) — <span class="doc-comment-inline">Below NOCACHE_THRESHOLD_BYTES the call is a no-op (False).</span></li>
<li><code>test_concurrent_cleanup_with_gather</code> (test_coroutine_cleanup.py) — <span class="doc-comment-inline">Multiple tasks with proper gather cleanup.</span></li>
<li><code>test_create_event_without_correlation_backward_compat</code> (test_correlation_propagation.py) — <span class="doc-comment-inline">Old call sites without correlation still work.</span></li>
<li><code>test_log_without_correlation_backward_compat</code> (test_correlation_propagation.py) — <span class="doc-comment-inline">Old call sites without correlation still work.</span></li>
<li><code>test_generate_with_dspy_disabled</code> (test_hypothesis_engine.py)
<details><summary>With HLEDAC_ENABLE_DSPY=0, _heuristic_generate() is called directly.</summary>
<div class="doc-comment">
<p>With HLEDAC_ENABLE_DSPY=0, _heuristic_generate() is called directly.</p>
<p>Output is list[ResearchHypothesis] bounded by MAX_HYPOTHESES.</p>
</div>
</details>
</li>
<li><code>_run_in_subprocess</code> (test_sprint8ax_duckdb_shadow.py) — <span class="doc-comment-inline">Run code in a fresh Python subprocess, return stdout, stderr, returncode.</span></li>
<li><code>_canned_match_text</code> (test_e2e_first_finding.py) — <span class="doc-comment-inline">Return canned CVE hit when the canned entry text is scanned.</span></li>
<li><code>test_rel_discovery_init_failsoft_sets_none</code> (test_sprint_scheduler.py)
<details><summary>L5331: RelDiscovery init exception handler.</summary>
<div class="doc-comment">
<p>L5331: RelDiscovery init exception handler.</p>
<p>verify: init failure → _rel_discovery_engine = None (non-critical advisory).</p>
</div>
</details>
</li>
<li><code>test_synthesis_sidecar_skipped_when_no_findings</code> (test_sprint_scheduler.py)
<details><summary>F259: No findings → synthesis skipped.</summary>
<div class="doc-comment">
<p>F259: No findings → synthesis skipped.</p>
<p>verify: _result fields updated, no crash.</p>
</div>
</details>
</li>
<li><code>_memory_profiler_gc_sync</code> (conftest.py)
<details><summary>Ensure GC is unfrozen before each test.</summary>
<div class="doc-comment">
<p>Ensure GC is unfrozen before each test.</p>
<p></p>
<p>CRITICAL FIX (F350M-R): MemoryTracker uses gc.freeze() to pin objects</p>
<p>during measurement. If a previous test's MemoryTracker crashes or</p>
<p>skips __exit__, GC stays frozen and subsequent gc.collect() calls</p>
<p>become no-ops, silently breaking leak detection.</p>
<p></p>
<p>This fixture runs BEFORE every test to ensure GC is in a clean state.</p>
</div>
</details>
</li>
<li><code>_hermes_cache_cleanup</code> (conftest.py)
<details><summary>Auto-cleanup HermesModelCache singleton after each test.</summary>
<div class="doc-comment">
<p>Auto-cleanup HermesModelCache singleton after each test.</p>
<p></p>
<p>CRITICAL FIX F350M-R: hermes_cache() is a process singleton.</p>
<p>Models accumulate across tests unless explicitly cleared.</p>
</div>
</details>
</li>
<li><code>test_all_deep_gives_max_non_indexed_score</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">Only tier-1+tier-2 sources → non_indexed_ratio score = 20.</span></li>
<li><code>test_mixed_gives_partial_non_indexed_score</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">50% deep sources → partial non_indexed_ratio.</span></li>
<li><code>test_unique_source_types_reflected</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">unique_source_types in depth_signals matches number of source types.</span></li>
<li><code>test_deep_sources_found_accumulates_tier1_tier2</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">deep_sources_found sums hits from tier1 + tier2 sources.</span></li>
<li><code>test_batch_classify</code> (test_differential_fuzzing.py)
<details><summary>batch_classify musí vracet stejné výsledky.</summary>
<div class="doc-comment">
<p>batch_classify musí vracet stejné výsledky.</p>
<p></p>
<p>F5.3: Many edge cases cause divergence. Skip inline.</p>
</div>
</details>
</li>
<li><code>test_no_memory_manager_in_gate</code> (test_sprint_p12_hypothesis.py) — <span class="doc-comment-inline">P12 gate does NOT use memory_manager (that was the pre-storage gate).</span></li>
<li><code>test_failsoft_exception_handling</code> (test_sprint_p12_hypothesis.py) — <span class="doc-comment-inline">Exception in P12 block does not propagate — fail-soft.</span></li>
<li><code>test_unload_via_model_manager</code> (test_sprint_p12_hypothesis.py)
<details><summary>_unload_hermes_at_teardown uses ModelManager.release_model("hermes").</summary>
<div class="doc-comment">
<p>_unload_hermes_at_teardown uses ModelManager.release_model("hermes").</p>
<p>Verifies canonical Hermes unload authority is ModelManager.</p>
</div>
</details>
</li>
<li><code>test_parallel_hypothesis_burst_keeps_max_five_cap</code> (test_sprint_p12_hypothesis.py) — <span class="doc-comment-inline">Up to 5 hypotheses are evaluated concurrently — cap of 5 is preserved.</span></li>
<li><code>test_tot_burst_uses_per_hypothesis_timeout</code> (test_sprint_p12_hypothesis.py) — <span class="doc-comment-inline">Each ToT task has its own 15s timeout budget — no single task blocks the burst.</span></li>
<li><code>test_first_three_completed_results_enqueue_pivots_immediately</code> (test_sprint_p12_hypothesis.py) — <span class="doc-comment-inline">as_completed iterates in arrival order — first completed ToT results feed enqueue immediately.</span></li>
<li><code>test_failed_tot_tasks_do_not_block_other_hypotheses</code> (test_sprint_p12_hypothesis.py)
<details><summary>Fail-soft: one failed ToT task does not fail the others — asyncio.as_completed handles results independently."""  # noqa: E501</summary>
<div class="doc-comment">
<p>Fail-soft: one failed ToT task does not fail the others — asyncio.as_completed handles results independently."""  # noqa: E501</p>
<p>from hledac.universal.pipeline.live_public_pipeline import async_run_live_public_pipeline</p>
<p>source = inspect.getsource(async_run_live_public_pipeline)</p>
<p></p>
<p>p12_start = source.find("# P12: Hypothesis generation")</p>
<p>p12_block = source[p12_start:p12_start + 5000]</p>
<p></p>
<p># except asyncio.TimeoutError with return "" — fail-soft per task</p>
<p>assert "asyncio.TimeoutError" in p12_block and 'return ""' in p12_block, (</p>
<p>"P12 must catch TimeoutError per-task and return empty string — fail-soft"</p>
<p>)</p>
<p># except Exception with return "" — broad fail-soft</p>
<p>assert "except Exception as e:" in p12_block and 'return ""' in p12_block, (</p>
<p>"P12 must catch all exceptions per-task and return empty string — fail-soft"</p>
<p>)</p>
<p></p>
<p></p>
<p>class TestP12HermesPrewarmPolicy:</p>
</div>
</details>
</li>
<li><code>test_rust_path_when_available</code> (test_hledac_core_rust.py) — <span class="doc-comment-inline">Test Rust fast path when Rust extension is available.</span></li>
<li><code>test_async_decorator_preserves_signature</code> (test_t1_otel.py) — <span class="doc-comment-inline">FIX F350M-R: Use session_event_loop fixture instead of asyncio.run().</span></li>
<li><code>test_url_set_add_batch_parallel</code> (test_rust_backend.py) — <span class="doc-comment-inline">UrlSet add_batch uses rayon parallel FNV-1a hashing.</span></li>
<li><code>test_ioc_dedup_store_add_batch_parallel</code> (test_rust_backend.py) — <span class="doc-comment-inline">IocDedupStore add_batch uses rayon parallel hashing.</span></li>
<li><code>reset_mock_deep</code> (spec_mocks.py)
<details><summary>Deep reset: reset_mock() + clear _mock_children.</summary>
<div class="doc-comment">
<p>Deep reset: reset_mock() + clear _mock_children.</p>
<p></p>
<p>Equivalent to mock.reset_mock() but also clears the _mock_children</p>
<p>dict that accumulates on chained attribute access.</p>
<p></p>
<p>Usage:</p>
<p>mock_foo.bar.baz.qux()  # creates _mock_children entries</p>
<p>reset_mock_deep(mock_foo)  # clears everything</p>
<p></p>
<p>Args:</p>
<p>mock: MagicMock/AsyncMock to reset</p>
</div>
</details>
</li>
<li><code>format_top_deltas</code> (memory_profiler.py)
<details><summary>Format top N allocation deltas as a readable string.</summary>
<div class="doc-comment">
<p>Format top N allocation deltas as a readable string.</p>
<p></p>
<p>Returns:</p>
<p>Multi-line string suitable for assertion messages.</p>
</div>
</details>
</li>
<li><code>test_apply_nocache_to_path_returns_bool</code> (test_sprint_f273.py)</li>
<li><code>test_f278a_replaces_f273b_contract</code> (test_sprint_f273.py) — <span class="doc-comment-inline">P0-1: 0.30 ratio with [30, 180] ceiling -- F288 cap removed.</span></li>
<li><code>test_bounded_gather_prevents_task_accumulation</code> (test_coroutine_cleanup.py)
<details><summary>F320: parallel() limits concurrent tasks.</summary>
<div class="doc-comment">
<p>F320: parallel() limits concurrent tasks.</p>
<p></p>
<p>parallel() with semaphore caps concurrent tasks,</p>
<p>preventing resource exhaustion.</p>
</div>
</details>
</li>
<li><code>test_ds_belief_multiple_hypotheses</code> (test_hypothesis_engine.py) — <span class="doc-comment-inline">Multiple hypotheses → belief values sum correctly.</span></li>
<li><code>_canned_match_text</code> (test_e2e_first_finding.py)</li>
<li><code>test_v2_has_aclose_method</code> (test_sprint_scheduler.py)
<details><summary>V2: aclose() method exists and is callable.</summary>
<div class="doc-comment">
<p>V2: aclose() method exists and is callable.</p>
<p></p>
<p>F285 graceful shutdown protocol — aclose() must exist on the</p>
<p>SprintSchedulerV2 instance for backward compatibility.</p>
</div>
</details>
</li>
<li><code>assert_memory_leak</code> (conftest.py)
<details><summary>Standalone assertion helper for memory leak checks.</summary>
<div class="doc-comment">
<p>Standalone assertion helper for memory leak checks.</p>
<p></p>
<p>Usage:</p>
<p>def test_something(assert_memory_leak):</p>
<p>before = get_rss_mb()</p>
<p># ... test code ...</p>
<p>after = get_rss_mb()</p>
<p>assert_memory_leak(before, after, threshold_mb=50)</p>
<p></p>
<p>Falls back to no-op if psutil unavailable.</p>
</div>
</details>
</li>
<li><code>_mlx_model_pool_cleanup</code> (conftest.py)
<details><summary>Auto-cleanup MLXModelPool singleton after each test.</summary>
<div class="doc-comment">
<p>Auto-cleanup MLXModelPool singleton after each test.</p>
<p></p>
<p>CRITICAL FIX F350M-R: MLXModelPool is a process singleton.</p>
<p>Loaded models accumulate across tests without reset.</p>
</div>
</details>
</li>
<li><code>_do_cleanup</code> (conftest.py)</li>
<li><code>test_normalize_idempotent</code> (test_differential_fuzzing.py) — <span class="doc-comment-inline">Normalizace URL musí být konzistentní — Rust vs Python.</span></li>
<li><code>test_is_valid_url</code> (test_differential_fuzzing.py)
<details><summary>is_valid_url musí být konzistentní.</summary>
<div class="doc-comment">
<p>is_valid_url musí být konzistentní.</p>
<p></p>
<p>F5.3: Many edge cases (numeric hostnames, control chars, non-ASCII, etc.)</p>
<p>cause Python vs Rust divergence. Skip any mismatches inline.</p>
</div>
</details>
</li>
<li><code>test_hypothesis_layer_after_aggregate_section</code> (test_sprint_p12_hypothesis.py) — <span class="doc-comment-inline">Hypothesis layer appears after the aggregate/compute block.</span></li>
<li><code>test_scheduler_releases_hermes_at_teardown</code> (test_sprint_p12_hypothesis.py)
<details><summary>SprintScheduler releases Hermes at teardown (in _close_dedup region).</summary>
<div class="doc-comment">
<p>SprintScheduler releases Hermes at teardown (in _close_dedup region).</p>
<p>Verifies bounded M1 8GB lifecycle: load at BOOT, release at TEARDOWN.</p>
</div>
</details>
</li>
<li><code>test_gate_open_with_store_and_hermes</code> (test_sprint_p12_hypothesis.py)
<details><summary>P12 gate opens when store is not None AND hermes_engine is not None AND total_stored &gt; 0.</summary>
<div class="doc-comment">
<p>P12 gate opens when store is not None AND hermes_engine is not None AND total_stored &gt; 0.</p>
<p>Verifies canonical DI wire: store+hermes+stored findings = gate open.</p>
</div>
</details>
</li>
<li><code>_python_normalize</code> (test_hledac_core_rust.py)</li>
<li><code>test_all_domains_accessible</code> (test_rust_backend.py) — <span class="doc-comment-inline">All 18 domain properties are accessible.</span></li>
<li><code>delta_mb</code> (memory_profiler.py)
<details><summary>Return RSS delta from snapshot to now.</summary>
<div class="doc-comment">
<p>Return RSS delta from snapshot to now.</p>
<p></p>
<p>Args:</p>
<p>force_gc: If True (default), run gc.collect() before measuring</p>
<p>to exclude unreachable Python objects from the delta.</p>
<p>Pass False for raw measured delta.</p>
<p></p>
<p>Returns:</p>
<p>RSS delta in MB (positive = growth, negative = freed).</p>
</div>
</details>
</li>
<li><code>take</code> (memory_profiler.py) — <span class="doc-comment-inline">Take a lightweight snapshot of Python object allocations.</span></li>
<li><code>test_constants</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test BEP-9 constants are bounded.</span></li>
<li><code>test_torrent_info_dataclass</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test TorrentInfo dataclass.</span></li>
<li><code>test_f288_aggressive_mode_windup</code> (test_sprint_f273.py) — <span class="doc-comment-inline">P0-1: aggressive mode uses 0.15 ratio, [30, 180] ceiling (F288 cap removed).</span></li>
<li><code>test_resource_governor_init_failsoft_sets_none</code> (test_sprint_scheduler.py)
<details><summary>L5155: governor init exception handler.</summary>
<div class="doc-comment">
<p>L5155: governor init exception handler.</p>
<p>verify: exception results in self._governor = None (degraded but running).</p>
</div>
</details>
</li>
<li><code>test_layer_manager_init_failsoft_does_not_crash</code> (test_sprint_scheduler.py)
<details><summary>L5202: LayerManager init exception handler.</summary>
<div class="doc-comment">
<p>L5202: LayerManager init exception handler.</p>
<p>verify: HLEDAC_ENABLE_LAYERS=1 but LayerManager fails → scheduler continues.</p>
</div>
</details>
</li>
<li><code>_r0_artifacts_stale</code> (conftest.py) — <span class="doc-comment-inline">Return True if R0 probe artifacts are missing or older than the runner.</span></li>
<li><code>_session_tracer</code> (conftest.py)
<details><summary>Session-scoped tracemalloc tracer.</summary>
<div class="doc-comment">
<p>Session-scoped tracemalloc tracer.</p>
<p>Starts tracemalloc once at pytest session start and stops it once at</p>
<p>session teardown. Eliminates repeated start/stop cycles that fragment</p>
<p>Python's pymalloc arenas (~200 KB per cycle × 100 tests = ~20 MB retained).</p>
<p>Individual TracemallocSnapshot / MemoryTracker instances in per-test</p>
<p>fixtures only take snapshots — they never start or stop the tracer.</p>
</div>
</details>
</li>
<li><code>_make_lifecycle_mock</code> (conftest.py)
<details><summary>Lifecycle mock pro _run_one_cycle testy.</summary>
<div class="doc-comment">
<p>Lifecycle mock pro _run_one_cycle testy.</p>
<p></p>
<p>Uses spec=SprintLifecycleManager to restrict mock to real attributes only,</p>
<p>preventing unbounded _mock_children growth (Issue 5.6).</p>
</div>
</details>
</li>
<li><code>test_classify_url</code> (test_differential_fuzzing.py)
<details><summary>classify_url musí vracet stejný (kind, host) pár.</summary>
<div class="doc-comment">
<p>classify_url musí vracet stejný (kind, host) pár.</p>
<p></p>
<p>F5.3: Many edge cases cause divergence. Skip inline.</p>
</div>
</details>
</li>
<li><code>test_extract_domain</code> (test_differential_fuzzing.py)
<details><summary>extract_domain musí vracet stejný doménový host.</summary>
<div class="doc-comment">
<p>extract_domain musí vracet stejný doménový host.</p>
<p></p>
<p>F5.3: Many edge cases cause divergence. Skip inline.</p>
</div>
</details>
</li>
<li><code>test_batch_dedup_fingerprints</code> (test_differential_fuzzing.py)
<details><summary>batch_dedup_fingerprints musí vracet hex stringy.</summary>
<div class="doc-comment">
<p>batch_dedup_fingerprints musí vracet hex stringy.</p>
<p></p>
<p>F5.3: Short inputs produce variable-length hex. Skip inline on mismatch.</p>
</div>
</details>
</li>
<li><code>test_compute_simhash</code> (test_differential_fuzzing.py)
<details><summary>compute_simhash musí vracet stejné integer hodnoty.</summary>
<div class="doc-comment">
<p>compute_simhash musí vracet stejné integer hodnoty.</p>
<p></p>
<p>F5.3: Short digit strings cause Rust=0 vs Python=correct. Skip inline.</p>
</div>
</details>
</li>
<li><code>test_is_private_ip</code> (test_differential_fuzzing.py)
<details><summary>is_private_ip musí vracet konzistentní výsledky.</summary>
<div class="doc-comment">
<p>is_private_ip musí vracet konzistentní výsledky.</p>
<p></p>
<p>F5.3: 250+.x.x.x Rust=false positive. Skip inline.</p>
</div>
</details>
</li>
<li><code>test_is_public_ip</code> (test_differential_fuzzing.py)
<details><summary>is_public_ip musí vracet konzistentní výsledky.</summary>
<div class="doc-comment">
<p>is_public_ip musí vracet konzistentní výsledky.</p>
<p></p>
<p>F5.3: 250+.x.x.x Rust=false positive. Skip inline.</p>
</div>
</details>
</li>
<li><code>test_gate_conditional_on_total_stored</code> (test_sprint_p12_hypothesis.py) — <span class="doc-comment-inline">P12 runs only when total_stored &gt; 0 (real findings exist).</span></li>
<li><code>test_queries_store_for_findings</code> (test_sprint_p12_hypothesis.py) — <span class="doc-comment-inline">P12 calls store.async_get_recent_findings() to get real persisted findings.</span></li>
<li><code>test_scheduler_passes_hermes_to_pipeline</code> (test_sprint_p12_hypothesis.py)
<details><summary>SprintScheduler passes hermes_engine into async_run_live_public_pipeline.</summary>
<div class="doc-comment">
<p>SprintScheduler passes hermes_engine into async_run_live_public_pipeline.</p>
<p>Verifies DI wire: scheduler._hermes_engine → pipeline P12 gate.</p>
</div>
</details>
</li>
<li><code>test_scheduler_loads_hermes_at_sprint_start</code> (test_sprint_p12_hypothesis.py)
<details><summary>SprintScheduler prewarms Hermes at sprint start (_prewarm_hermes_for_sprint).</summary>
<div class="doc-comment">
<p>SprintScheduler prewarms Hermes at sprint start (_prewarm_hermes_for_sprint).</p>
<p>Verifies bounded M1 8GB lifecycle: load at BOOT, release at TEARDOWN.</p>
</div>
</details>
</li>
<li><code>test_parse_ip_fast</code> (test_rust_backend.py) — <span class="doc-comment-inline">parse_ip_fast returns normalized IP string or None (Rust) / tuple (Python fallback).</span></li>
<li><code>test_feed_dominance_guard_zero_findings</code> (test_rust_backend.py) — <span class="doc-comment-inline">FeedDominanceGuard.compute handles zero findings.</span></li>
<li><code>test_compute_dominance_convenience</code> (test_rust_backend.py) — <span class="doc-comment-inline">compute_dominance convenience method works.</span></li>
<li><code>_cleanup_mocks</code> (spec_mocks.py)
<details><summary>Clean up mock instances: clear _mock_children, reset call counts.</summary>
<div class="doc-comment">
<p>Clean up mock instances: clear _mock_children, reset call counts.</p>
<p></p>
<p>Args:</p>
<p>mocks: Tuple of mock instances to clean</p>
<p></p>
<p>Always-on, fail-safe: errors are swallowed to not break test teardown.</p>
</div>
</details>
</li>
<li><code>has_leak</code> (memory_profiler.py)
<details><summary>Check if any allocation grew by more than threshold_kb.</summary>
<div class="doc-comment">
<p>Check if any allocation grew by more than threshold_kb.</p>
<p></p>
<p>Args:</p>
<p>threshold_kb: Threshold in KB per allocation site.</p>
<p></p>
<p>Returns:</p>
<p>True if any single allocation site grew beyond threshold_kb.</p>
</div>
</details>
</li>
<li><code>test_extract_gemini_links</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test gemtext link extraction.</span></li>
<li><code>test_hardware_critical_derived</code> (test_f227k_acquisition_lane_parity.py) — <span class="doc-comment-inline">hardware_critical = uma_state in (critical, emergency) OR swap_detected.</span></li>
<li><code>test_run_correlation_exists</code> (test_correlation_propagation.py) — <span class="doc-comment-inline">RunCorrelation dataclass exists in types.py.</span></li>
<li><code>test_extract_emails</code> (test_hypothesis_engine.py) — <span class="doc-comment-inline">Finding with known email → adversarial hypothesis with leak: pivot seed.</span></li>
<li><code>test_max_hypotheses_bound_respected</code> (test_hypothesis_engine.py)
<details><summary>INVARIANT: MAX_HYPOTHESES is a config constant.</summary>
<div class="doc-comment">
<p>INVARIANT: MAX_HYPOTHESES is a config constant.</p>
<p>Output MUST NOT exceed MAX_HYPOTHESES regardless of input.</p>
</div>
</details>
</li>
<li><code>_make_canned_entry</code> (test_e2e_first_finding.py) — <span class="doc-comment-inline">Single high-quality feed entry that triggers CVE pattern.</span></li>
<li><code>_fake_async_run_public</code> (test_e2e_first_finding.py)</li>
<li><code>_slow_public</code> (test_e2e_first_finding.py)</li>
<li><code>_slow_public</code> (test_e2e_first_finding.py)</li>
<li><code>test_latency_ema_bounded</code> (test_sprint_scheduler.py) — <span class="doc-comment-inline">Property: EMA latency never exceeds clamp bounds [5, 30]s.</span></li>
<li><code>_graph_service_session_cleanup</code> (conftest.py)
<details><summary>Reset GraphService singleton state between tests.</summary>
<div class="doc-comment">
<p>Reset GraphService singleton state between tests.</p>
<p></p>
<p>F350M-R: _DEFAULT_GRAPH_SERVICE holds _seen_iocs / _seen_rels idempotency</p>
<p>sets that persist across tests. reset_session() clears both sets AND the</p>
<p>DuckPGQGraph singleton -- preventing cross-test IOC leakage.</p>
</div>
</details>
</li>
<li><code>test_surface_run_returns_all_required_keys</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">surface/smoke run still returns complete structure (no KeyError).</span></li>
<li><code>test_three_branches_active_gives_15_points</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">3 active branches → 15 points (cap).</span></li>
<li><code>test_normalize_quality_text</code> (test_differential_fuzzing.py)
<details><summary>normalize_quality_text musí vracet bit-identický výstup.</summary>
<div class="doc-comment">
<p>normalize_quality_text musí vracet bit-identický výstup.</p>
<p></p>
<p>F5.3: Rust normalize_quality_text() přijímá pouze str, ne bytes.</p>
<p>QUALITY_TEXT strategie nyní produkuje pouze text (ne binary) — TypeError fixed.</p>
</div>
</details>
</li>
<li><code>test_cosine_similarity</code> (test_differential_fuzzing.py) — <span class="doc-comment-inline">cosine_similarity musí vracet bit-identické výsledky.</span></li>
<li><code>test_max_five_hypotheses</code> (test_sprint_p12_hypothesis.py) — <span class="doc-comment-inline">ToT evaluation bounded to 5 hypotheses: hypotheses[:5].</span></li>
<li><code>test_gate_blocks_when_hermes_none</code> (test_sprint_p12_hypothesis.py) — <span class="doc-comment-inline">P12 gate does NOT open when hermes_engine is None (even with store and findings).</span></li>
<li><code>test_teardown_calls_unload_method</code> (test_sprint_p12_hypothesis.py)
<details><summary>Teardown path calls _unload_hermes_at_teardown.</summary>
<div class="doc-comment">
<p>Teardown path calls _unload_hermes_at_teardown.</p>
<p>Verifies bounded M1 8GB lifecycle: load at BOOT, release at TEARDOWN.</p>
</div>
</details>
</li>
<li><code>test_imports</code> (test_t1_otel.py)</li>
<li><code>test_sync_decorator</code> (test_t1_otel.py)</li>
<li><code>test_async_decorator</code> (test_t1_otel.py)</li>
<li><code>test_burst_does_not_exceed_ring_capacity</code> (test_t1_otel.py) — <span class="doc-comment-inline">M1 8GB bound: ring stays &lt;= capacity even under burst.</span></li>
<li><code>test_rolling_hash_engine</code> (test_rust_backend.py) — <span class="doc-comment-inline">RollingHashEngine hash and roll work.</span></li>
<li><code>test_cosine_similarity</code> (test_rust_backend.py) — <span class="doc-comment-inline">cosine_similarity returns float.</span></li>
<li><code>stop_session_tracer</code> (memory_profiler.py)
<details><summary>Stop the session-scoped tracemalloc tracer (idempotent).</summary>
<div class="doc-comment">
<p>Stop the session-scoped tracemalloc tracer (idempotent).</p>
<p></p>
<p>Call once at pytest session teardown. Safe to call even if not started.</p>
</div>
</details>
</li>
<li><code>get_rss_mb</code> (memory_profiler.py)
<details><summary>Get current process RSS in MB.</summary>
<div class="doc-comment">
<p>Get current process RSS in MB.</p>
<p></p>
<p>Fail-safe: returns 0.0 on any error (permission, process terminated, etc.)</p>
<p>This ensures CI never fails due to measurement error.</p>
<p></p>
<p>Uses a cached psutil.Process object (~50 KB saved per call).</p>
</div>
</details>
</li>
<li><code>take</code> (memory_profiler.py) — <span class="doc-comment-inline">Take a baseline snapshot (call before code under test).</span></li>
<li><code>add</code> (test_sprint8l_live.py)</li>
<li><code>test_fediverse_constants</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test Fediverse constants are bounded.</span></li>
<li><code>test_matrix_constants</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test Matrix constants are bounded.</span></li>
<li><code>test_maybe_call_pressure_relief_increments_counter</code> (test_sprint_f273.py)</li>
<li><code>test_assert_no_leak_fails_over_threshold</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">assert_no_leak() raises AssertionError when delta exceeds threshold.</span></li>
<li><code>test_tracker_custom_threshold</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">Custom threshold is respected by assert_leak_threshold.</span></li>
<li><code>test_tracker_assertion_message_contains_details</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">AssertionError message includes delta, threshold, and RSS values.</span></li>
<li><code>test_has_leak_true_when_growing</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">has_leak() returns True when any site grows beyond threshold.</span></li>
<li><code>test_register_allocation_returns_finalizer</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">register_allocation() returns a weakref.finalize object.</span></li>
<li><code>test_init_session_tracer_idempotent</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">init_session_tracer() is safe to call multiple times.</span></li>
<li><code>test_analytics_hook_signature_extended</code> (test_correlation_propagation.py) — <span class="doc-comment-inline">shadow_record_finding accepts branch_id, provider_id, action_id.</span></li>
<li><code>test_extract_none_returns_empty_lists</code> (test_hypothesis_engine.py) — <span class="doc-comment-inline">Empty payload → no extractions.</span></li>
<li><code>test_hypothesis_generator_respects_max</code> (test_hypothesis_engine.py) — <span class="doc-comment-inline">HypothesisGenerator.generate() respects MAX_HYPOTHESES bound.</span></li>
<li><code>test_confidence_range</code> (test_hypothesis_engine.py) — <span class="doc-comment-inline">All hypotheses have confidence in [0.0, 1.0].</span></li>
<li><code>test_windup_efficiency_computed_correctly</code> (test_sprint_scheduler.py) — <span class="doc-comment-inline">windup_efficiency = windup / (windup + active).</span></li>
<li><code>base_resource_governor_mock</code> (conftest.py)</li>
<li><code>test_ct_log_contributes_to_non_indexed_ratio</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">ct_log hits contribute to non_indexed_ratio component (tier 1).</span></li>
<li><code>test_onion_discovery_contributes_as_deep</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">onion_discovery hits count as tier 2 (deep) for non_indexed_ratio.</span></li>
<li><code>_derive_level_from_score</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">Pure helper: maps score to level name (mirrors internal logic).</span></li>
<li><code>test_no_tot_on_zero_findings</code> (test_sprint_p12_hypothesis.py) — <span class="doc-comment-inline">When total_stored == 0, P12 does not run ToT.</span></li>
<li><code>test_tot_solution_count_tracked</code> (test_sprint_p12_hypothesis.py) — <span class="doc-comment-inline">P12 tracks tot_solution_count for telemetry.</span></li>
<li><code>test_no_tot_when_store_none</code> (test_sprint_p12_hypothesis.py) — <span class="doc-comment-inline">ToT does not run when store is None (hermes_engine is irrelevant without store).</span></li>
<li><code>test_no_tot_when_no_stored_findings</code> (test_sprint_p12_hypothesis.py) — <span class="doc-comment-inline">ToT does not run when total_stored == 0 (no evidence to reason about).</span></li>
<li><code>test_batch_entropy_basic</code> (test_rust_backend.py) — <span class="doc-comment-inline">batch_entropy returns correct Shannon entropy values.</span></li>
<li><code>test_int_counter_layout</code> (test_rust_backend.py) — <span class="doc-comment-inline">IntCounterLayout get/set/bump work.</span></li>
<li><code>_rebuild_snapshot_domains</code> (memory_profiler.py)
<details><summary>Parse HLEDAC_TEST_TM_DOMAINS env var into a tuple of domain prefixes.</summary>
<div class="doc-comment">
<p>Parse HLEDAC_TEST_TM_DOMAINS env var into a tuple of domain prefixes.</p>
<p></p>
<p>Example: HLEDAC_TEST_TM_DOMAINS="hledac,brain,knowledge"</p>
<p>Results in domain prefixes used to filter comparison results.</p>
<p></p>
<p>Falls back to all domains (empty tuple) if env var is not set.</p>
</div>
</details>
</li>
<li><code>test_gopher_item_dataclass</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test GopherItem structure.</span></li>
<li><code>test_alt_protocol_result_namedtuple</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test AltProtocolResult structure.</span></li>
<li><code>test_branch_timeout_returns_zero_only_below_dynamic_floor</code> (test_sprint_f273.py) — <span class="doc-comment-inline">_branch_timeout_s returns 0 only when remaining_s &lt;= dynamic floor.</span></li>
<li><code>_run_drain</code> (test_sprint_f273.py)</li>
<li><code>_run_drain</code> (test_sprint_f273.py)</li>
<li><code>test_stop_noop_in_session_mode</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">stop() does NOT stop the session tracer.</span></li>
<li><code>test_disabled_reason_hardware_critical_ipfs</code> (test_f227k_acquisition_lane_parity.py) — <span class="doc-comment-inline">IPFS disabled reason: cid_present + hardware_critical → hardware_critical (not no_cid_in_query).</span></li>
<li><code>test_init_without_correlation_backward_compat</code> (test_correlation_propagation.py) — <span class="doc-comment-inline">Old call sites without correlation still work.</span></li>
<li><code>test_ds_source_weight_modulates_belief</code> (test_hypothesis_engine.py) — <span class="doc-comment-inline">Higher source_weight → higher belief contribution.</span></li>
<li><code>test_research_hypothesis_immutable</code> (test_hypothesis_engine.py) — <span class="doc-comment-inline">ResearchHypothesis is frozen → attributes cannot be modified after creation.</span></li>
<li><code>test_effective_windup_60s_15pct_no_floor</code> (test_sprint_scheduler.py)
<details><summary>Sprint 60s: 30% ratio = 18s, floored to 30s. Active = 30s.</summary>
<div class="doc-comment">
<p>Sprint 60s: 30% ratio = 18s, floored to 30s. Active = 30s.</p>
<p></p>
<p>F290: sprint&lt;=120 → ratio=0.20, raw=60*0.20=12s → floor max(15,12)=15.</p>
<p>F288: floor [15, 180] always applies (15s floor).</p>
</div>
</details>
</li>
<li><code>test_aclose_does_not_raise_on_clean_scheduler</code> (test_sprint_scheduler.py) — <span class="doc-comment-inline">aclean() must not raise even when all resources are None/empty.</span></li>
<li><code>test_aclose_is_idempotent</code> (test_sprint_scheduler.py) — <span class="doc-comment-inline">Calling aclose() twice must not raise.</span></li>
<li><code>test_all_signals_max_score_is_100</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">All signals active yields score &lt;= 100.</span></li>
<li><code>test_shallow_level_single_indexed_source</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">Single indexed source + no corroboration → shallow.</span></li>
<li><code>test_single_indexed_source_low_diversity</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">One indexed source → low diversity score.</span></li>
<li><code>test_campaign_hints_3_plus_gives_5_bonus</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">3+ campaign hints → +5 corroboration bonus (capped at 25).</span></li>
<li><code>test_pivot_depth_capped_at_15</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">Both hypothesis + pivot recommended → capped at 15.</span></li>
<li><code>test_source_tier_tier1_contributes_to_non_indexed_ratio</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">Tier-1 academic_discovery hits contribute to non_indexed_ratio component.</span></li>
<li><code>test_ipfs_contributes_to_non_indexed_ratio</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">ipfs hits contribute to non_indexed_ratio component (tier 1).</span></li>
<li><code>test_shodan_search_contributes_to_non_indexed_ratio</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">shodan_search hits contribute to non_indexed_ratio component (tier 1).</span></li>
<li><code>test_bgp_monitor_contributes_to_non_indexed_ratio</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">bgp_monitor hits contribute to non_indexed_ratio component (tier 1).</span></li>
<li><code>_mock_handoff_with_runtime_truth</code> (test_research_depth_metric.py)</li>
<li><code>test_batch_entropy</code> (test_differential_fuzzing.py) — <span class="doc-comment-inline">batch_entropy musí vracet bit-identické výsledky.</span></li>
<li><code>test_batch_compute_simhash</code> (test_differential_fuzzing.py) — <span class="doc-comment-inline">batch_compute_simhash musí vracet stejnou délku a hodnoty.</span></li>
<li><code>test_hypothesis_engine_initialized</code> (test_sprint_p12_hypothesis.py) — <span class="doc-comment-inline">HypothesisEngine is instantiated inside P12 block.</span></li>
<li><code>test_tot_integration_layer_initialized</code> (test_sprint_p12_hypothesis.py) — <span class="doc-comment-inline">TotIntegrationLayer is instantiated inside P12 block.</span></li>
<li><code>test_default_name_uses_qualname</code> (test_t1_otel.py)</li>
<li><code>test_concurrent_spans_async_safe</code> (test_t1_otel.py)</li>
<li><code>test_compute_entropy_single</code> (test_rust_backend.py) — <span class="doc-comment-inline">compute_entropy returns correct value.</span></li>
<li><code>test_hot_edge_counter</code> (test_rust_backend.py) — <span class="doc-comment-inline">HotEdgeCounter bump_edge and drain work.</span></li>
<li><code>test_chain_hash</code> (test_rust_backend.py) — <span class="doc-comment-inline">chain_hash returns tuple of strings.</span></li>
<li><code>test_lane_budget_pool_allocate_consume</code> (test_rust_backend.py) — <span class="doc-comment-inline">LaneBudgetPool allocate and consume work.</span></li>
<li><code>test_lane_budget_pool_release</code> (test_rust_backend.py) — <span class="doc-comment-inline">LaneBudgetPool release works.</span></li>
<li><code>test_lane_budget_pool_get_utilization</code> (test_rust_backend.py) — <span class="doc-comment-inline">LaneBudgetPool get_utilization returns float.</span></li>
<li><code>test_lane_budget_pool_timeout</code> (test_rust_backend.py) — <span class="doc-comment-inline">LaneBudgetPool release increments timeout_count.</span></li>
<li><code>delta_bytes</code> (memory_profiler.py) — <span class="doc-comment-inline">Return delta in bytes from baseline to now.</span></li>
<li><code>peak_delta_mb</code> (memory_profiler.py) — <span class="doc-comment-inline">Return peak memory growth in MB from baseline.</span></li>
<li><code>to_dict</code> (test_sprint8l_live.py)</li>
<li><code>compute_slope</code> (test_sprint8l_live.py)</li>
<li><code>test_gopher_finding_dataclass</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test GopherFinding structure.</span></li>
<li><code>test_gemini_response_namedtuple</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test GeminiResponse structure.</span></li>
<li><code>test_gemini_finding_namedtuple</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test GeminiFinding structure.</span></li>
<li><code>test_schedule_html_extraction_returns_future</code> (test_sprint_f273.py)</li>
<li><code>test_drain_helpers_importable</code> (test_sprint_f273.py) — <span class="doc-comment-inline">drain_pending_extractions + get_drain_stats are importable.</span></li>
<li><code>test_compare_top_n_returns_list</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">compare_top_n() returns a list of stat pairs.</span></li>
<li><code>test_take_returns_started_stats</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">TracemallocStats.take() returns a started instance with 2 numbers.</span></li>
<li><code>test_weakref_finalizers_cleared_on_exit</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">_weakref_finalizers list is cleared after __exit__.</span></li>
<li><code>test_init_session_tracer_starts_tracing</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">init_session_tracer() starts tracemalloc and returns True.</span></li>
<li><code>test_memory_tracker_fixture_bookend</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">memory_tracker fixture provides context manager that captures RSS delta.</span></li>
<li><code>test_run_correlation_partial</code> (test_correlation_propagation.py) — <span class="doc-comment-inline">RunCorrelation supports partial fields.</span></li>
<li><code>test_run_correlation_with_provider</code> (test_correlation_propagation.py) — <span class="doc-comment-inline">RunCorrelation.with_provider() returns new instance.</span></li>
<li><code>test_hypothesis_type_lateral</code> (test_hypothesis_engine.py) — <span class="doc-comment-inline">Hash finding → lateral type hypothesis.</span></li>
<li><code>test_hypothesis_type_adversarial</code> (test_hypothesis_engine.py) — <span class="doc-comment-inline">Email finding → adversarial type hypothesis.</span></li>
<li><code>_make_canned_public_entry</code> (test_e2e_first_finding.py) — <span class="doc-comment-inline">Single high-quality public-discovery entry that triggers CVE pattern.</span></li>
<li><code>_make_canned_ct_result</code> (test_e2e_first_finding.py) — <span class="doc-comment-inline">Canned CT log pivot result for a domain.</span></li>
<li><code>test_finding_count_never_negative</code> (test_sprint_scheduler.py)
<details><summary>Property: finding_count is non-negative.</summary>
<div class="doc-comment">
<p>Property: finding_count is non-negative.</p>
<p>Bounds: 0 &lt;= finding_count &lt;= 10000</p>
</div>
</details>
</li>
<li><code>scheduler_mocks</code> (conftest.py) — <span class="doc-comment-inline">Per-test fixture vracející (scheduler, result, runner) s auto-cleanup.</span></li>
<li><code>test_surface_smoke_run_score_not_negative</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">Zero sources yields score &gt;= 0.</span></li>
<li><code>test_score_is_float</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">Score type is always float (not int, not None).</span></li>
<li><code>test_surface_level_at_minimum</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">Empty inputs → surface level.</span></li>
<li><code>test_all_indexed_gives_zero_non_indexed_score</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">Only tier-0 sources → non_indexed_ratio = 0.</span></li>
<li><code>test_no_signals_gives_zero_corrob</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">No corroboration signals → corroboration = 0.</span></li>
<li><code>test_is_corroborated_true_gives_15</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">is_corroborated=True → 15 points.</span></li>
<li><code>test_is_noisy_false_gives_5</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">is_noisy=False → 5 points (distinct from is_corroborated).</span></li>
<li><code>test_corrob_capped_at_25</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">Corroboration score cannot exceed 25.</span></li>
<li><code>test_no_runtime_truth_zero_branch_score</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">No runtime_truth → branch_diversity = 0.</span></li>
<li><code>test_no_signals_gives_zero_pivot</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">No hypothesis_pack or pivot signal → pivot_depth = 0.</span></li>
<li><code>test_hypothesis_count_gives_5</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">hypothesis_count &gt; 0 → 5 points.</span></li>
<li><code>test_pivot_recommended_gives_10</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">next_pivot_recommendation != continue → 10 points.</span></li>
<li><code>test_continue_pivot_not_counted</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">next_pivot_recommendation='continue' → 0 pivot points.</span></li>
<li><code>test_campaign_hints_count_from_correlation</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">campaign_hints count matches correlation input.</span></li>
<li><code>_mock_handoff</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">Build a mock ExportHandoff with scorecard source counts.</span></li>
<li><code>test_html_extract</code> (test_differential_fuzzing.py) — <span class="doc-comment-inline">html_extract musí vracet konzistentní strukturu.</span></li>
<li><code>test_batch_nfc_normalize_nfc_composition</code> (test_hledac_core_rust.py)</li>
<li><code>test_stats</code> (test_t1_otel.py)</li>
<li><code>test_nested_spans</code> (test_t1_otel.py)</li>
<li><code>test_batch_dedup_fingerprints</code> (test_rust_backend.py) — <span class="doc-comment-inline">batch_dedup_fingerprints returns list of hex strings.</span></li>
<li><code>test_ioc_dedup_store</code> (test_rust_backend.py) — <span class="doc-comment-inline">IocDedupStore add/contains work.</span></li>
<li><code>test_ioc_dedup_store_batch_insert_alias</code> (test_rust_backend.py) — <span class="doc-comment-inline">batch_insert is an alias for add_batch.</span></li>
<li><code>test_html_extract</code> (test_rust_backend.py) — <span class="doc-comment-inline">html_extract returns dict with links, emails, title.</span></li>
<li><code>make_mock_backend</code> (spec_mocks.py) — <span class="doc-comment-inline">Deprecated: Use make_storage_mock() instead.</span></li>
<li><code>make_mock_governor</code> (spec_mocks.py) — <span class="doc-comment-inline">Deprecated: Use make_governor_mock() instead.</span></li>
<li><code>make_mock_store</code> (spec_mocks.py) — <span class="doc-comment-inline">Deprecated: Use make_duckdb_store_mock() instead.</span></li>
<li><code>__enter__</code> (memory_profiler.py)</li>
<li><code>test_gate_disabled_by_default</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test alt protocols disabled by default.</span></li>
<li><code>test_gate_enabled_with_env</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test alt protocols enabled with env var.</span></li>
<li><code>test_get_alt_protocols_status</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test status reporting.</span></li>
<li><code>test_bencode_encoder</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test bencode encoding.</span></li>
<li><code>test_min_branch_remaining_s_floor_when_no_cycles_seen</code> (test_sprint_f273.py) — <span class="doc-comment-inline">When _cycle_time_ema is 0 (pre-loop), returns the default 2.0s floor.</span></li>
<li><code>test_write_section_uses_aiofiles_when_available</code> (test_sprint_f273.py) — <span class="doc-comment-inline">If aiofiles is available, _write_section uses async with aiofiles.open.</span></li>
<li><code>test_tracker_enters_and_exits_cleanly</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">MemoryTracker __enter__ / __exit__ cycle completes without error.</span></li>
<li><code>test_tracker_measures_delta</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">MemoryTracker captures RSS delta between enter and assert.</span></li>
<li><code>test_peak_delta_mb_returns_float</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">peak_delta_mb() returns peak memory growth in MB.</span></li>
<li><code>test_sprint_lifecycle_no_leak</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">SprintLifecycleManager import + instantiation does not leak memory.</span></li>
<li><code>test_memory_tracker_fixture_reports_leak</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">memory_tracker fixture raises AssertionError with leak details.</span></li>
<li><code>test_pipeline_timeout_fires</code> (test_coroutine_cleanup.py) — <span class="doc-comment-inline">Verify timeout is respected.</span></li>
<li><code>test_run_correlation_with_action</code> (test_correlation_propagation.py) — <span class="doc-comment-inline">RunCorrelation.with_action() returns new instance.</span></li>
<li><code>test_ds_no_contradiction_below_threshold</code> (test_hypothesis_engine.py) — <span class="doc-comment-inline">Mild evidence → detect_contradiction(threshold=0.5) returns False.</span></li>
<li><code>test_hypothesis_type_entity_expansion</code> (test_hypothesis_engine.py) — <span class="doc-comment-inline">IP finding → entity_expansion type with high confidence.</span></li>
<li><code>test_empty_findings_returns_fallback</code> (test_hypothesis_engine.py) — <span class="doc-comment-inline">No findings, no seeds → single fallback hypothesis.</span></li>
<li><code>test_empty_findings_with_seeds</code> (test_hypothesis_engine.py) — <span class="doc-comment-inline">No findings but seeds present → valid hypotheses from seeds.</span></li>
<li><code>find_spec</code> (conftest.py)</li>
<li><code>lifecycle_mock</code> (conftest.py) — <span class="doc-comment-inline">Standard lifecycle mock pro OODA loop testy s auto-cleanup.</span></li>
<li><code>_full_source_counts</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">Return a diverse source mix spanning all 3 tiers.</span></li>
<li><code>test_compute_entropy</code> (test_differential_fuzzing.py) — <span class="doc-comment-inline">compute_entropy single musí být konzistentní.</span></li>
<li><code>test_nfc_normalize</code> (test_differential_fuzzing.py) — <span class="doc-comment-inline">NFC normalizace musí vracet bit-identické výsledky.</span></li>
<li><code>test_strip_diacritics</code> (test_differential_fuzzing.py) — <span class="doc-comment-inline">strip_diacritics musí vracet bit-identické výsledky.</span></li>
<li><code>test_batch_nfc_normalize</code> (test_differential_fuzzing.py) — <span class="doc-comment-inline">batch_nfc_normalize musí vracet stejné výsledky.</span></li>
<li><code>test_cidr_contains</code> (test_differential_fuzzing.py) — <span class="doc-comment-inline">cidr_contains musí vracet konzistentní výsledky.</span></li>
<li><code>test_nfc_normalize</code> (test_differential_fuzzing.py) — <span class="doc-comment-inline">NFC normalizace IOC textů musí být konzistentní.</span></li>
<li><code>test_content_hash_64</code> (test_differential_fuzzing.py) — <span class="doc-comment-inline">content_hash_64 musí vracet stejné integer hodnoty.</span></li>
<li><code>test_content_hash_hex</code> (test_differential_fuzzing.py) — <span class="doc-comment-inline">content_hash_hex musí vracet stejné hex stringy.</span></li>
<li><code>_python_strip_tracking_params</code> (test_hledac_core_rust.py)</li>
<li><code>test_batch_dedup_removes_duplicates</code> (test_hledac_core_rust.py)</li>
<li><code>test_python_fallback_content_hash</code> (test_hledac_core_rust.py) — <span class="doc-comment-inline">Python fallback uses hashlib.sha256 (not xxhash, just verifies import works).</span></li>
<li><code>test_find_near_duplicates_no_pairs</code> (test_hledac_core_rust.py)</li>
<li><code>test_lru_eviction</code> (test_t1_otel.py)</li>
<li><code>test_basic_export</code> (test_t1_otel.py)</li>
<li><code>test_attributes_recorded</code> (test_t1_otel.py)</li>
<li><code>test_trace_id_nonzero_in_span</code> (test_t1_otel.py)</li>
<li><code>test_otel_disabled_yields_noop</code> (test_t1_otel.py) — <span class="doc-comment-inline">When exporter_kind='none', no actual SDK is used; ring stays empty.</span></li>
<li><code>test_filter_valid_urls</code> (test_rust_backend.py) — <span class="doc-comment-inline">filter_valid_urls filters a list.</span></li>
<li><code>test_bloom_filter_add_contains</code> (test_rust_backend.py) — <span class="doc-comment-inline">BloomFilter add/contains work.</span></li>
<li><code>test_url_set</code> (test_rust_backend.py) — <span class="doc-comment-inline">UrlSet add/contains work.</span></li>
<li><code>test_batch_graph_traverse_returns_list</code> (test_rust_backend.py) — <span class="doc-comment-inline">batch_graph_traverse returns list of dicts (or None on invalid path).</span></li>
<li><code>test_feed_dominance_guard_ratio_class</code> (test_rust_backend.py) — <span class="doc-comment-inline">FeedDominanceGuard.ratio_class returns correct class.</span></li>
<li><code>test_bencode_decoder</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test bencode decoding.</span></li>
<li><code>test_size_formatter</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test human-readable size formatting.</span></li>
<li><code>test_clear_cache</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test cache clearing.</span></li>
<li><code>test_alt_protocols_status_includes_social</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test get_alt_protocols_status includes social protocols.</span></li>
<li><code>test_windup_for_cycle_no_bonus_when_quick</code> (test_sprint_f273.py) — <span class="doc-comment-inline">Cycle EMA &lt;= 8s gives no adaptive bonus.</span></li>
<li><code>test_windup_for_cycle_adaptive_bonus</code> (test_sprint_f273.py) — <span class="doc-comment-inline">Slow cycles get +0.5s per s over 8s, capped at +30s.</span></li>
<li><code>test_sprint_scheduler_accepts_flags_param</code> (test_sprint_f273.py) — <span class="doc-comment-inline">SprintScheduler.__init__ signature must include flags kwarg.</span></li>
<li><code>test_sprint_scheduler_result_has_hermes_diagnostic_fields</code> (test_sprint_f273.py) — <span class="doc-comment-inline">SprintSchedulerResult must have hermes_model_loaded etc. with sane defaults.</span></li>
<li><code>test_format_top_deltas_returns_string</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">format_top_deltas() returns a formatted string.</span></li>
<li><code>test_has_leak_false_when_clean</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">has_leak() returns False when no significant allocation growth.</span></li>
<li><code>test_delta_bytes_captures_allocation</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">delta_bytes() captures Python object allocation growth.</span></li>
<li><code>test_memory_snapshot_fixture_delta</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">memory_snapshot captures RSS on enter, provides delta on exit.</span></li>
<li><code>_has_domain</code> (test_f227k_acquisition_lane_parity.py)</li>
<li><code>_has_crypto</code> (test_f227k_acquisition_lane_parity.py)</li>
<li><code>_conc</code> (test_f227k_acquisition_lane_parity.py) — <span class="doc-comment-inline">Mirror _lane_concurrency() from the source module (same implementation as _lc).</span></li>
<li><code>test_discovery_timeout_fires</code> (test_coroutine_cleanup.py) — <span class="doc-comment-inline">F271B: Verify 35 second timeout fires on slow operation.</span></li>
<li><code>test_ds_belief_single_hypothesis</code> (test_hypothesis_engine.py) — <span class="doc-comment-inline">Add 1 hypothesis, add 1 supporting evidence → belief() &gt; 0.5.</span></li>
<li><code>test_research_hypothesis_default_type</code> (test_hypothesis_engine.py) — <span class="doc-comment-inline">Default hypothesis_type is entity_expansion.</span></li>
<li><code>minimal_config</code> (test_sprint_scheduler.py) — <span class="doc-comment-inline">Minimal SprintSchedulerConfig for testing.</span></li>
<li><code>_instantiate_scheduler</code> (test_sprint_scheduler.py) — <span class="doc-comment-inline">Create scheduler instance with minimal mocking.</span></li>
<li><code>test_budget_allocation_in_bounds</code> (test_sprint_scheduler.py)
<details><summary>Property: budget allocation respects MAX_SPRINT_BUDGET bounds.</summary>
<div class="doc-comment">
<p>Property: budget allocation respects MAX_SPRINT_BUDGET bounds.</p>
<p>Bounds: 0 &lt; budget &lt;= 10000.0</p>
</div>
</details>
</li>
<li><code>make_resource_governor_mock</code> (conftest.py)
<details><summary>Fixture: vytvoří spec-limited governor mock.</summary>
<div class="doc-comment">
<p>Fixture: vytvoří spec-limited governor mock.</p>
<p></p>
<p>Usage:</p>
<p>def test_something(make_resource_governor_mock):</p>
<p>governor = make_resource_governor_mock()</p>
</div>
</details>
</li>
<li><code>content_hash_64</code> (test_hledac_core_rust.py) — <span class="doc-comment-inline">xxHash3-64 with str/bytes convenience.</span></li>
<li><code>content_hash_hex</code> (test_hledac_core_rust.py) — <span class="doc-comment-inline">xxHash3-64 hex with str/bytes convenience (16-char hex).</span></li>
<li><code>test_python_fallback_available</code> (test_hledac_core_rust.py) — <span class="doc-comment-inline">Python fallback path is always available.</span></li>
<li><code>test_simhash_near_duplicate_detection</code> (test_hledac_core_rust.py)</li>
<li><code>test_compute_simhash_fingerprint_format</code> (test_hledac_core_rust.py)</li>
<li><code>test_find_near_duplicates_in_batch_no_pairs</code> (test_hledac_core_rust.py)</li>
<li><code>test_noop_tracer_yields_noop_span</code> (test_t1_otel.py)</li>
<li><code>test_decorator_with_uninitialized_telemetry</code> (test_t1_otel.py)</li>
<li><code>test_decorator_preserves_metadata</code> (test_t1_otel.py)</li>
<li><code>test_unsupported_value_coerced_to_string</code> (test_t1_otel.py)</li>
<li><code>test_bloom_filter_len</code> (test_rust_backend.py) — <span class="doc-comment-inline">BloomFilter __len__ works.</span></li>
<li><code>test_batch_content_hash</code> (test_rust_backend.py) — <span class="doc-comment-inline">batch_content_hash returns list of ints.</span></li>
<li><code>test_batch_compute_simhash</code> (test_rust_backend.py) — <span class="doc-comment-inline">batch_compute_simhash returns list of ints.</span></li>
<li><code>test_aho_matcher</code> (test_rust_backend.py) — <span class="doc-comment-inline">AhoCorasickMatcher.scan returns list of matches.</span></li>
<li><code>_get_snapshot_domains</code> (memory_profiler.py) — <span class="doc-comment-inline">Return cached domain prefixes, rebuilding only when TTL expires.</span></li>
<li><code>__init__</code> (test_sprint8l_live.py)</li>
<li><code>__init__</code> (test_sprint8l_live.py)</li>
<li><code>_monitor</code> (test_sprint8l_live.py)</li>
<li><code>__init__</code> (test_sprint8l_live.py)</li>
<li><code>compute_hhi</code> (test_sprint8l_live.py)</li>
<li><code>test_gopher_item_is_file</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test GopherItem is_file property.</span></li>
<li><code>test_fediverse_is_enabled</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test Fediverse is_enabled gate.</span></li>
<li><code>test_matrix_is_enabled</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test Matrix is_enabled gate.</span></li>
<li><code>test_async_fetch_dht_metadata_disabled</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test async_fetch_dht_metadata when DHT is disabled.</span></li>
<li><code>setUp</code> (test_sprint_f273.py)</li>
<li><code>test_drain_zero_deadline_returns_immediately</code> (test_sprint_f273.py) — <span class="doc-comment-inline">FIX F350M-R: Use session_event_loop fixture instead of asyncio.run().</span></li>
<li><code>test_hermes_diagnostic_fields</code> (test_sprint_f273.py)</li>
<li><code>test_snapshot_takes_rss_on_init</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">Snapshot captures RSS at construction time.</span></li>
<li><code>test_delta_mb_with_allocation</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">delta_mb() captures deliberate allocation growth.</span></li>
<li><code>test_take_captures_baseline</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">take() stores current tracemalloc snapshot.</span></li>
<li><code>test_delta_bytes_zero_on_noop</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">delta_bytes() returns ~0 for no-op.</span></li>
<li><code>test_delta_mb_returns_float</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">delta_mb() returns delta in MB as float.</span></li>
<li><code>_fake_fetch</code> (test_e2e_first_finding.py)</li>
<li><code>mock_store</code> (test_sprint_scheduler.py) — <span class="doc-comment-inline">DuckDB store mock — minimal methods needed by scheduler.</span></li>
<li><code>mock_public_fetcher</code> (test_sprint_scheduler.py) — <span class="doc-comment-inline">Public fetcher mock.</span></li>
<li><code>test_lane_count_within_bounds</code> (test_sprint_scheduler.py)
<details><summary>Property: lane count is between 1 and 25 (not hardcoded).</summary>
<div class="doc-comment">
<p>Property: lane count is between 1 and 25 (not hardcoded).</p>
<p>Bounds: 1 &lt;= len(lanes) &lt;= 25</p>
</div>
</details>
</li>
<li><code>test_source_economics_count_nonnegative</code> (test_sprint_scheduler.py)
<details><summary>Property: source economics entries are non-negative.</summary>
<div class="doc-comment">
<p>Property: source economics entries are non-negative.</p>
<p>Bounds: count &gt;= 0</p>
</div>
</details>
</li>
<li><code>test_effective_windup_300s_25pct</code> (test_sprint_scheduler.py) — <span class="doc-comment-inline">Sprint 300s: F290 ratio=0.25, raw=75s → floor [15,180]→75. Active = 225s.</span></li>
<li><code>test_effective_windup_600s_30pct</code> (test_sprint_scheduler.py) — <span class="doc-comment-inline">Sprint 600s: F290 ratio=0.30, raw=180s → floor [15,180]→180. Active = 420s.</span></li>
<li><code>test_windup_efficiency_field_present</code> (test_sprint_scheduler.py) — <span class="doc-comment-inline">SprintSchedulerResult has windup_efficiency field (F289).</span></li>
<li><code>_read_settings_json</code> (conftest.py)
<details><summary>Read settings.json at fixture invocation time (not load/collection time).</summary>
<div class="doc-comment">
<p>Read settings.json at fixture invocation time (not load/collection time).</p>
<p></p>
<p>Avoids global mutable cache — safe for parallel test execution where each</p>
<p>worker may see a different file state at the moment the fixture runs.</p>
</div>
</details>
</li>
<li><code>mock_settings_json</code> (conftest.py) — <span class="doc-comment-inline">Hermetický settings.json mock — fresh read per invocation, no global cache.</span></li>
<li><code>_make_resource_governor_mock</code> (conftest.py) — <span class="doc-comment-inline">Governor mock s evaluate() → uma_state.</span></li>
<li><code>test_roll_method</code> (test_hledac_core_rust.py)</li>
<li><code>test_rust_extension_loads</code> (test_hledac_core_rust.py) — <span class="doc-comment-inline">Sanity: Rust extension loads without error.</span></li>
<li><code>test_find_near_duplicates_all_same</code> (test_hledac_core_rust.py)</li>
<li><code>test_batch_nfc_normalize_unicode_sameness</code> (test_hledac_core_rust.py)</li>
<li><code>test_telemetry_config_sample_clamped</code> (test_t1_otel.py)</li>
<li><code>test_capacity_validation</code> (test_t1_otel.py)</li>
<li><code>test_basic_put_get</code> (test_t1_otel.py)</li>
<li><code>test_update_existing_no_evict</code> (test_t1_otel.py)</li>
<li><code>test_basic_open_close</code> (test_t1_otel.py)</li>
<li><code>test_decorator_handles_exception</code> (test_t1_otel.py)</li>
<li><code>test_basic_primitives_pass_through</code> (test_t1_otel.py)</li>
<li><code>worker</code> (test_t1_otel.py)</li>
<li><code>test_dedup_fingerprint_returns_hex</code> (test_rust_backend.py) — <span class="doc-comment-inline">dedup_fingerprint returns a hex string.</span></li>
<li><code>test_is_valid_url</code> (test_rust_backend.py) — <span class="doc-comment-inline">is_valid_url validates URLs correctly.</span></li>
<li><code>test_batch_classify</code> (test_rust_backend.py) — <span class="doc-comment-inline">batch_classify returns list of (kind, host) tuples.</span></li>
<li><code>test_content_hasher</code> (test_rust_backend.py) — <span class="doc-comment-inline">ContentHasher produces hex strings via static methods.</span></li>
<li><code>test_xxhash_64</code> (test_rust_backend.py) — <span class="doc-comment-inline">content_hash_64 returns integer.</span></li>
<li><code>test_nfc_normalize</code> (test_rust_backend.py) — <span class="doc-comment-inline">nfc_normalize normalizes Unicode.</span></li>
<li><code>test_available_memory</code> (test_rust_backend.py) — <span class="doc-comment-inline">available_memory returns int &gt;= 0.</span></li>
<li><code>test_total_memory</code> (test_rust_backend.py) — <span class="doc-comment-inline">total_memory returns int &gt; 0.</span></li>
<li><code>finalize</code> (test_sprint8l_live.py)</li>
<li><code>stop</code> (test_sprint8l_live.py)</li>
<li><code>stop</code> (test_sprint8l_live.py)</li>
<li><code>test_is_i2p_available_cached</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test I2P availability check uses caching.</span></li>
<li><code>test_fediverse_adapter_init</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test FediverseAdapter initialization.</span></li>
<li><code>test_fediverse_instances_defined</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test OSINT instances are defined.</span></li>
<li><code>test_matrix_adapter_init</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test MatrixPublicAdapter initialization.</span></li>
<li><code>test_metadata_fetcher_init</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test TorrentMetadataFetcher initialization.</span></li>
<li><code>test_async_fetch_dht_metadata_invalid_hash</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test async_fetch_dht_metadata with invalid hash.</span></li>
<li><code>test_drain_bounded_capacity</code> (test_sprint_f273.py) — <span class="doc-comment-inline">Registry maxlen=512 — overflow drops oldest (with cancel).</span></li>
<li><code>test_sprint_flags_has_hermes_force_field</code> (test_sprint_f273.py) — <span class="doc-comment-inline">SprintFlags must have a hermes_force:bool field, default False.</span></li>
<li><code>test_sprint_flags_is_frozen</code> (test_sprint_f273.py) — <span class="doc-comment-inline">SprintFlags is frozen msgspec.Struct — hermes_force must be immutable.</span></li>
<li><code>test_fnocache_constant_present</code> (test_sprint_f273.py)</li>
<li><code>test_sprint_scheduler_result_has_pressure_relief_fields</code> (test_sprint_f273.py)</li>
<li><code>test_pattern_extraction_drain_fields</code> (test_sprint_f273.py)</li>
<li><code>test_assert_no_leak_passes_when_clean</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">assert_no_leak() does not raise when delta is within threshold.</span></li>
<li><code>test_tracemalloc_starts_on_init</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">TracemallocSnapshot starts tracemalloc on __post_init__.</span></li>
<li><code>tearDown</code> (test_sprint_memory_profiling.py)</li>
<li><code>test_stop_session_tracer_stops_tracing</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">stop_session_tracer() stops tracemalloc.</span></li>
<li><code>test_stop_session_tracer_idempotent</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">stop_session_tracer() is safe to call when not started.</span></li>
<li><code>test_assert_memory_leak_fixture_noop_when_clean</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">assert_memory_leak fixture passes when delta is within threshold.</span></li>
<li><code>test_assert_memory_leak_fixture_fails_over_threshold</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">assert_memory_leak fixture raises AssertionError when delta &gt; threshold.</span></li>
<li><code>test_assert_no_leak_fails_over_threshold</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">assert_no_leak() raises AssertionError when delta &gt; threshold.</span></li>
<li><code>test_assert_no_leak_with_context</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">assert_no_leak() includes context string in error message.</span></li>
<li><code>test_stale_docstring_max_lanes</code> (test_f227k_acquisition_lane_parity.py) — <span class="doc-comment-inline">Docstring says 'max 8 lanes' but LANE_RULES has 15 — verify actual count.</span></li>
<li><code>test_disabled_reason_covers_all_lanes</code> (test_f227k_acquisition_lane_parity.py) — <span class="doc-comment-inline">_disabled_reason returns a string for every known lane.</span></li>
<li><code>tracked_generator</code> (test_coroutine_cleanup.py)</li>
<li><code>test_uma_threshold_state_valid</code> (test_sprint_scheduler.py) — <span class="doc-comment-inline">Property: UMA state is one of known values.</span></li>
<li><code>test_effective_windup_explicit_override_respected</code> (test_sprint_scheduler.py) — <span class="doc-comment-inline">Explicit --windup-lead 50s is respected (above 30s floor).</span></li>
<li><code>normalize</code> (test_hledac_core_rust.py)</li>
<li><code>test_cve</code> (test_hledac_core_rust.py)</li>
<li><code>test_fingerprint_stable</code> (test_hledac_core_rust.py)</li>
<li><code>test_fingerprint_different_for_different_urls</code> (test_hledac_core_rust.py)</li>
<li><code>test_update_and_digest</code> (test_hledac_core_rust.py)</li>
<li><code>test_hashes_method</code> (test_hledac_core_rust.py)</li>
<li><code>test_insert_and_check</code> (test_hledac_core_rust.py)</li>
<li><code>test_content_hash_hex_matches_manual</code> (test_hledac_core_rust.py)</li>
<li><code>test_simhash_different_texts_high_distance</code> (test_hledac_core_rust.py)</li>
<li><code>test_telemetry_config_from_env_default</code> (test_t1_otel.py)</li>
<li><code>worker</code> (test_t1_otel.py)</li>
<li><code>test_clear</code> (test_t1_otel.py)</li>
<li><code>test_record_exception_noop</code> (test_t1_otel.py)</li>
<li><code>test_truncation_marker</code> (test_t1_otel.py)</li>
<li><code>test_init_idempotent</code> (test_t1_otel.py)</li>
<li><code>test_init_returns_false_on_bad_kind</code> (test_t1_otel.py)</li>
<li><code>test_singleton_identity</code> (test_rust_backend.py) — <span class="doc-comment-inline">RustBackend() returns the same instance.</span></li>
<li><code>test_normalize_quality_text</code> (test_rust_backend.py) — <span class="doc-comment-inline">normalize_quality_text strips and lowercases.</span></li>
<li><code>test_classify_url_clearnet</code> (test_rust_backend.py) — <span class="doc-comment-inline">classify_url returns (kind, host) tuple for https URLs.</span></li>
<li><code>test_classify_url_onion</code> (test_rust_backend.py) — <span class="doc-comment-inline">classify_url returns (kind, host) tuple for .onion URLs.</span></li>
<li><code>test_compute_simhash</code> (test_rust_backend.py) — <span class="doc-comment-inline">compute_simhash returns integer.</span></li>
<li><code>test_madvise_returns_bool</code> (test_rust_backend.py) — <span class="doc-comment-inline">madvise_on_mmap_region returns bool (no-op in fallback).</span></li>
<li><code>test_is_private_ip</code> (test_rust_backend.py) — <span class="doc-comment-inline">is_private_ip returns bool.</span></li>
<li><code>test_cidr_contains</code> (test_rust_backend.py) — <span class="doc-comment-inline">cidr_contains returns bool.</span></li>
<li><code>test_sprint_policies_domain_accessible</code> (test_rust_backend.py) — <span class="doc-comment-inline">sprint_policies domain is accessible.</span></li>
<li><code>test_feed_dominance_guard_factory</code> (test_rust_backend.py) — <span class="doc-comment-inline">FeedDominanceGuard factory method works.</span></li>
<li><code>test_lane_budget_pool_factory</code> (test_rust_backend.py) — <span class="doc-comment-inline">LaneBudgetPool factory method works.</span></li>
<li><code>is_tracing</code> (memory_profiler.py) — <span class="doc-comment-inline">Return True if tracemalloc is currently active (session or otherwise).</span></li>
<li><code>start</code> (test_sprint8l_live.py)</li>
<li><code>test_cid_extraction</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test CID pattern extraction from text.</span></li>
<li><code>test_cid_extraction_v1</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test CIDv1 (bafy) extraction.</span></li>
<li><code>test_veronica_search_config</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test Veronica-2 search is configured.</span></li>
<li><code>test_parse_gemini_url</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test Gemini URL parsing.</span></li>
<li><code>test_parse_gemini_url_with_port</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test Gemini URL parsing with custom port.</span></li>
<li><code>test_parse_gemini_url_simple</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test Gemini URL parsing with just host.</span></li>
<li><code>test_known_eepsites_structure</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test known eepsites list structure.</span></li>
<li><code>test_ipfs_gateway_reachable</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test IPFS gateways are reachable.</span></li>
<li><code>test_gopher_floodgap_reachable</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test Gopher floodgap server is reachable.</span></li>
<li><code>_import_min_branch</code> (test_sprint_f273.py)
<details><summary>Import SprintScheduler so we can call _min_branch_remaining_s without</summary>
<div class="doc-comment">
<p>Import SprintScheduler so we can call _min_branch_remaining_s without</p>
<p>instantiating the full scheduler (which would touch LMDB/DuckDB).</p>
</div>
</details>
</li>
<li><code>test_default_floor_is_2_seconds</code> (test_sprint_f273.py) — <span class="doc-comment-inline">The class-level default must be 2.0s (was 5.0s in pre-F273A).</span></li>
<li><code>test_windup_for_cycle_floor_protects_short_sprints</code> (test_sprint_f273.py) — <span class="doc-comment-inline">Short sprint (60s, base=30) keeps a usable active window under adapt.</span></li>
<li><code>test_drain_registry_starts_empty</code> (test_sprint_f273.py)</li>
<li><code>test_streaming_exporter_imports_cleanly</code> (test_sprint_f273.py) — <span class="doc-comment-inline">The module must import without errors even on minimal installs.</span></li>
<li><code>test_apply_nocache_missing_file_returns_false</code> (test_sprint_f273.py) — <span class="doc-comment-inline">If file doesn't exist, returns False (fail-soft).</span></li>
<li><code>test_malloc_zone_pressure_relief_returns_int</code> (test_sprint_f273.py)</li>
<li><code>test_windup_lead_diagnostic_fields</code> (test_sprint_f273.py)</li>
<li><code>test_delta_mb_zero_on_noop</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">delta_mb() returns ~0 for no-op (within GC noise margin).</span></li>
<li><code>test_tracker_tracemalloc_included_by_default</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">MemoryTracker includes tracemalloc by default.</span></li>
<li><code>test_assert_no_leak_passes_within_threshold</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">assert_no_leak() does not raise when delta ≤ threshold.</span></li>
<li><code>test_lane_spec_feednfd_unused</code> (test_f227k_acquisition_lane_parity.py) — <span class="doc-comment-inline">LaneSpecFeedNFD is defined but unused in the loop — confirm it exists for API compat.</span></li>
<li><code>test_enabled_fn_returns_bool</code> (test_f227k_acquisition_lane_parity.py) — <span class="doc-comment-inline">Each rule's enabled_fn returns a bool for any ctx.</span></li>
<li><code>test_reason_fn_returns_str</code> (test_f227k_acquisition_lane_parity.py) — <span class="doc-comment-inline">Each rule's reason_fn returns a str for enabled ctx.</span></li>
<li><code>test_concurrency_fn_returns_int</code> (test_f227k_acquisition_lane_parity.py) — <span class="doc-comment-inline">Each rule's concurrency_fn returns an int.</span></li>
<li><code>collect</code> (test_coroutine_cleanup.py) — <span class="doc-comment-inline">Run gc and return leaked coroutines.</span></li>
<li><code>test_ds_empty_hypotheses</code> (test_hypothesis_engine.py) — <span class="doc-comment-inline">DempsterShafer with no hypotheses → belief() returns 0.</span></li>
<li><code>_import_scheduler</code> (test_sprint_scheduler.py) — <span class="doc-comment-inline">Lazy import to avoid heavy startup cost on test collection.</span></li>
<li><code>load_module</code> (conftest.py)</li>
<li><code>_make_governor_mock</code> (conftest.py) — <span class="doc-comment-inline">Governor mock bez spec (pro situace kde spec není dostupný).</span></li>
<li><code>_make_duckdb_batch_result_mock</code> (conftest.py) — <span class="doc-comment-inline">DuckDB batch result mock s configurable hits.</span></li>
<li><code>_make_duckdb_diff_mock</code> (conftest.py) — <span class="doc-comment-inline">DuckDB diff result mock s configurable change_events.</span></li>
<li><code>_make_session_mock</code> (conftest.py) — <span class="doc-comment-inline">aiohttp session mock s closed attribute.</span></li>
<li><code>_make_graph_mock</code> (conftest.py) — <span class="doc-comment-inline">IOCGraph mock s find_connected_batch.</span></li>
<li><code>_make_ct_batch_mock</code> (conftest.py) — <span class="doc-comment-inline">CT batch result mock.</span></li>
<li><code>_make_extractor_mock</code> (conftest.py) — <span class="doc-comment-inline">Extractor mock s nested to_dict MagicMock.</span></li>
<li><code>test_source_tier_classification_tier2_deep</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">Tier-2 sources (rl_research, tot_synthesis) are classified as deep.</span></li>
<li><code>test_academic_discovery_in_source_tier_tier1</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">academic_discovery is classified as tier-1 (structured TI).</span></li>
<li><code>_python_fingerprint</code> (test_hledac_core_rust.py)</li>
<li><code>batch_content_hash</code> (test_hledac_core_rust.py) — <span class="doc-comment-inline">Batch xxHash3-64 (Rust expects Vec&lt;String&gt;, so pass-through).</span></li>
<li><code>batch_content_hash_hex</code> (test_hledac_core_rust.py) — <span class="doc-comment-inline">Batch xxHash3-64 hex (Rust expects Vec&lt;String&gt;, so pass-through).</span></li>
<li><code>test_onion_v3</code> (test_hledac_core_rust.py)</li>
<li><code>test_domain</code> (test_hledac_core_rust.py)</li>
<li><code>test_email</code> (test_hledac_core_rust.py)</li>
<li><code>test_batch_content_hash_hex</code> (test_hledac_core_rust.py)</li>
<li><code>test_batch_compute_consistency</code> (test_hledac_core_rust.py)</li>
<li><code>test_is_near_duplicate_true</code> (test_hledac_core_rust.py)</li>
<li><code>test_is_near_duplicate_false_distant</code> (test_hledac_core_rust.py)</li>
<li><code>test_batch_nfc_normalize_preserves_ascii</code> (test_hledac_core_rust.py)</li>
<li><code>_reset_telemetry</code> (test_t1_otel.py) — <span class="doc-comment-inline">Ensure clean state: shutdown any prior init.</span></li>
<li><code>test_span_yields_noop_when_uninitialized</code> (test_t1_otel.py)</li>
<li><code>test_export_empty</code> (test_t1_otel.py)</li>
<li><code>test_uninitialized_returns_noop</code> (test_t1_otel.py)</li>
<li><code>test_init_none_succeeds</code> (test_t1_otel.py)</li>
<li><code>test_shutdown_idempotent</code> (test_t1_otel.py)</li>
<li><code>test_import_no_error</code> (test_rust_backend.py) — <span class="doc-comment-inline">RustBackend imports without ImportError.</span></li>
<li><code>test_classify_url_i2p</code> (test_rust_backend.py) — <span class="doc-comment-inline">classify_url returns (kind, host) tuple for .i2p URLs.</span></li>
<li><code>test_extract_domain</code> (test_rust_backend.py) — <span class="doc-comment-inline">extract_domain extracts the domain.</span></li>
<li><code>get_rss_mb</code> (test_sprint8l_live.py)</li>
<li><code>ipfs_client</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Lazy import IPFS client.</span></li>
<li><code>test_cid_extraction_none</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test CID extraction with no CIDs.</span></li>
<li><code>test_resolve_ipns_invalid_input</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test IPNS resolution with invalid input (raw CID).</span></li>
<li><code>gopher</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Import GopherTransport module directly.</span></li>
<li><code>test_constants</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test protocol constants.</span></li>
<li><code>gemini</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Lazy import Gemini transport.</span></li>
<li><code>test_extract_gemini_links_empty</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test gemtext link extraction with no links.</span></li>
<li><code>test_constants</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test protocol constants.</span></li>
<li><code>i2p</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Lazy import I2P client.</span></li>
<li><code>test_constants</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test I2P constants.</span></li>
<li><code>fetcher</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Lazy import fetcher.</span></li>
<li><code>test_gemini_circumlunar_reachable</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test Gemini circumlunar.space is configured.</span></li>
<li><code>test_matrix_homeserver</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test Matrix homeserver is configured.</span></li>
<li><code>test_async_fetch_dht_metadata_import</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test async_fetch_dht_metadata is importable.</span></li>
<li><code>test_fediverse_fetch_function_exists</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test fetch_fediverse_only function exists.</span></li>
<li><code>test_matrix_fetch_function_exists</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test fetch_matrix_only function exists.</span></li>
<li><code>test_fediverse_timeout_constant</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test FEDIVERSE_TIMEOUT constant.</span></li>
<li><code>test_matrix_timeout_constant</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test MATRIX_TIMEOUT constant.</span></li>
<li><code>_import_sprint_scheduler_config</code> (test_sprint_f273.py) — <span class="doc-comment-inline">Import SprintSchedulerConfig without triggering full module side effects.</span></li>
<li><code>test_windup_for_cycle_negative_ema_returns_base</code> (test_sprint_f273.py) — <span class="doc-comment-inline">Negative cycle EMA (defensive) returns base — fail-safe.</span></li>
<li><code>test_tools_init_exports_apply_nocache_to_path</code> (test_sprint_f273.py) — <span class="doc-comment-inline">tools/__init__.py must export apply_nocache_to_path for canonical import.</span></li>
<li><code>test_dynamic_branch_floor_field</code> (test_sprint_f273.py)</li>
<li><code>test_get_rss_mb_returns_positive</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">get_rss_mb() returns a positive float (or 0 on error).</span></li>
<li><code>test_disabled_reason_ipfs_no_cid</code> (test_f227k_acquisition_lane_parity.py) — <span class="doc-comment-inline">IPFS disabled reason: no cid_present → no_cid_in_query.</span></li>
<li><code>test_context_feed_max_items_nfd</code> (test_f227k_acquisition_lane_parity.py) — <span class="doc-comment-inline">nonfeed_diagnostic profile: _feed_max_items should be 25.</span></li>
<li><code>test_context_feed_max_items_default</code> (test_f227k_acquisition_lane_parity.py) — <span class="doc-comment-inline">default profile: _feed_max_items should be 50.</span></li>
<li><code>make_counter</code> (test_f227k_acquisition_lane_parity.py)</li>
<li><code>track</code> (test_coroutine_cleanup.py) — <span class="doc-comment-inline">Register a coroutine for tracking.</span></li>
<li><code>async_range_slow</code> (test_coroutine_cleanup.py) — <span class="doc-comment-inline">Async generator that yields with delay - mimics real IO operations.</span></li>
<li><code>_patched_new_event_loop</code> (conftest.py)</li>
<li><code>base_sprint_scheduler_mock</code> (conftest.py)</li>
<li><code>test_source_tier_classification_tier1_structured</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">Tier-1 sources (ct_log_pipeline, circl_pdns) are classified as structured.</span></li>
<li><code>test_source_tier_classification_tier0_indexed</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">Tier-0 sources (rss_atom_pipeline, live_public_pipeline) are indexed.</span></li>
<li><code>pytest_configure</code> (test_differential_fuzzing.py) — <span class="doc-comment-inline">Register custom markers.</span></li>
<li><code>extract_iocs</code> (test_hledac_core_rust.py)</li>
<li><code>strip_tracking_params</code> (test_hledac_core_rust.py)</li>
<li><code>fingerprint</code> (test_hledac_core_rust.py)</li>
<li><code>test_ipv4_basic</code> (test_hledac_core_rust.py)</li>
<li><code>test_ipv4_private_ranges</code> (test_hledac_core_rust.py)</li>
<li><code>test_ipv4_negative</code> (test_hledac_core_rust.py)</li>
<li><code>test_ipv6</code> (test_hledac_core_rust.py)</li>
<li><code>test_onion_negative_short</code> (test_hledac_core_rust.py)</li>
<li><code>test_md5</code> (test_hledac_core_rust.py)</li>
<li><code>test_sha1</code> (test_hledac_core_rust.py)</li>
<li><code>test_sha256</code> (test_hledac_core_rust.py)</li>
<li><code>test_strip_default_http_port</code> (test_hledac_core_rust.py)</li>
<li><code>test_strip_default_https_port</code> (test_hledac_core_rust.py)</li>
<li><code>test_strip_utm_params</code> (test_hledac_core_rust.py)</li>
<li><code>test_fragment_preserved</code> (test_hledac_core_rust.py)</li>
<li><code>test_strip_utm</code> (test_hledac_core_rust.py)</li>
<li><code>test_strip_fbclid</code> (test_hledac_core_rust.py)</li>
<li><code>test_preserve_other_params</code> (test_hledac_core_rust.py)</li>
<li><code>test_fingerprint_returns_u64</code> (test_hledac_core_rust.py)</li>
<li><code>test_hash_method</code> (test_hledac_core_rust.py)</li>
<li><code>test_content_hash_hex_idempotent</code> (test_hledac_core_rust.py)</li>
<li><code>test_batch_content_hash_deterministic</code> (test_hledac_core_rust.py)</li>
<li><code>test_find_near_duplicates_in_batch_all_same</code> (test_hledac_core_rust.py)</li>
<li><code>test_telemetry_config_frozen</code> (test_t1_otel.py)</li>
<li><code>test_telemetry_config_sample_ratio_full</code> (test_t1_otel.py)</li>
<li><code>test_telemetry_config_from_env_invalid_kind</code> (test_t1_otel.py)</li>
<li><code>test_current_trace_id_zeros</code> (test_t1_otel.py)</li>
<li><code>test_force_flush</code> (test_t1_otel.py)</li>
<li><code>test_exception_in_block_propagates</code> (test_t1_otel.py)</li>
<li><code>test_string_truncation</code> (test_t1_otel.py)</li>
<li><code>test_nested_list_truncated</code> (test_t1_otel.py)</li>
<li><code>test_is_available_is_bool</code> (test_rust_backend.py) — <span class="doc-comment-inline">is_available is a bool.</span></li>
<li><code>test_cid_extraction_empty</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test CID extraction with empty input (fail-safe).</span></li>
<li><code>test_find_via_ipfs_search_returns_list</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test IPFS search returns list of CIDs (may be empty if API unavailable).</span></li>
<li><code>test_bootstrap_servers_defined</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test gopher bootstrap servers are configured.</span></li>
<li><code>test_is_i2p_available_returns_bool</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test I2P availability check returns bool.</span></li>
<li><code>test_windup_ratio_is_30_percent</code> (test_sprint_f273.py)</li>
<li><code>test_windup_60s_uses_floor_30</code> (test_sprint_f273.py) — <span class="doc-comment-inline">60s sprint: 0.30*60=18, clamped up to 30.</span></li>
<li><code>test_windup_120s_scales</code> (test_sprint_f273.py) — <span class="doc-comment-inline">120s sprint: 0.30*120=36 (above floor, within cap).</span></li>
<li><code>test_windup_1800s_uses_ceiling_180</code> (test_sprint_f273.py) — <span class="doc-comment-inline">1800s sprint: 0.30*1800=540, clamped to 180 (max ceiling).</span></li>
<li><code>test_windup_600s_uses_ceiling_180</code> (test_sprint_f273.py) — <span class="doc-comment-inline">600s sprint: 0.30*600=180, clamped to 180 (max ceiling).</span></li>
<li><code>test_windup_300s_uses_90_no_cap</code> (test_sprint_f273.py) — <span class="doc-comment-inline">P0-1: 300s sprint: 0.30*300=90 (F288 cap removed).</span></li>
<li><code>test_windup_aggressive_300s_uses_45</code> (test_sprint_f273.py) — <span class="doc-comment-inline">Aggressive 300s: 0.15*300=45.</span></li>
<li><code>test_windup_aggressive_600s_uses_90</code> (test_sprint_f273.py) — <span class="doc-comment-inline">Aggressive 600s: 0.15*600=90 (within [30, 180] ceiling).</span></li>
<li><code>test_malloc_zone_pressure_relief_importable</code> (test_sprint_f273.py)</li>
<li><code>test_maybe_call_pressure_relief_method_exists</code> (test_sprint_f273.py)</li>
<li><code>test_stop_is_noop_when_not_started</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">stop() is safe to call on non-started instance.</span></li>
<li><code>test_snapshot_fixture_returns_snapshot</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">memory_snapshot fixture returns a Snapshot object.</span></li>
<li><code>test_memory_tracker_fixture_returns_tracker</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">memory_tracker fixture returns a MemoryTracker object.</span></li>
<li><code>_has_url</code> (test_f227k_acquisition_lane_parity.py)</li>
<li><code>slow_processor</code> (test_coroutine_cleanup.py) — <span class="doc-comment-inline">Simulate slow processing.</span></li>
<li><code>data_source</code> (test_coroutine_cleanup.py)</li>
<li><code>_get_all_loops</code> (conftest.py) — <span class="doc-comment-inline">Python 3.14+ compatible: return all non-closed event loops.</span></li>
<li><code>_read_settings_bak</code> (conftest.py) — <span class="doc-comment-inline">Read settings backup at fixture invocation time.</span></li>
<li><code>mock_settings_bak</code> (conftest.py) — <span class="doc-comment-inline">Hermetický backup settings.json mock.</span></li>
<li><code>make_governor_mock</code> (conftest.py) — <span class="doc-comment-inline">Fixture: vytvoří unbounded governor mock.</span></li>
<li><code>make_lancedb_table_mock</code> (conftest.py) — <span class="doc-comment-inline">Fixture: vytvoří LanceDB table mock.</span></li>
<li><code>make_duckdb_batch_result_mock</code> (conftest.py) — <span class="doc-comment-inline">Fixture: vytvoří DuckDB batch mock s configurable hits.</span></li>
<li><code>make_duckdb_diff_mock</code> (conftest.py) — <span class="doc-comment-inline">Fixture: vytvoří DuckDB diff mock s configurable change_events.</span></li>
<li><code>make_session_mock</code> (conftest.py) — <span class="doc-comment-inline">Fixture: vytvoří aiohttp session mock.</span></li>
<li><code>make_graph_mock</code> (conftest.py) — <span class="doc-comment-inline">Fixture: vytvoří IOCGraph mock s find_connected_batch.</span></li>
<li><code>make_ioc_graph_mock</code> (conftest.py) — <span class="doc-comment-inline">Fixture: vytvoří IOCGraph mock.</span></li>
<li><code>make_outcome_mock</code> (conftest.py) — <span class="doc-comment-inline">Fixture: vytvoří Sprint outcome mock.</span></li>
<li><code>make_ct_batch_mock</code> (conftest.py) — <span class="doc-comment-inline">Fixture: vytvoří CT batch mock.</span></li>
<li><code>make_extractor_mock</code> (conftest.py) — <span class="doc-comment-inline">Fixture: vytvoří extractor mock s nested to_dict.</span></li>
<li><code>_make_lancedb_table_mock</code> (conftest.py) — <span class="doc-comment-inline">LanceDB table mock s add() method.</span></li>
<li><code>_make_ioc_graph_mock</code> (conftest.py) — <span class="doc-comment-inline">IOCGraph mock bez spec (pro jednoduché graph operace).</span></li>
<li><code>_make_outcome_mock</code> (conftest.py) — <span class="doc-comment-inline">Sprint outcome mock.</span></li>
<li><code>_gc_after_heavy_tests</code> (conftest.py) — <span class="doc-comment-inline">Deprecated: replaced by _cleanup() autouse fixture. No-op.</span></li>
<li><code>test_ct_log_is_tier1</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">ct_log (ct_log_client.py:273) is tier 1 — structured TI.</span></li>
<li><code>test_ct_log_pipeline_also_tier1</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">ct_log_pipeline alias is also tier 1 — backward compat.</span></li>
<li><code>test_onion_discovery_is_tier2</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">onion_discovery (live_public_pipeline.py:1785) is tier 2 — deep/dark web.</span></li>
<li><code>test_ipfs_is_tier1</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">ipfs (ti_feed_adapter.py:1367) is tier 1 — structured TI.</span></li>
<li><code>test_shodan_search_is_tier1</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">shodan_search (shodan_wrapper.py:204) is tier 1 — structured TI.</span></li>
<li><code>test_bgp_monitor_is_tier1</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">bgp_monitor (ti_feed_adapter.py:1742) is tier 1 — structured TI.</span></li>
<li><code>test_live_public_pipeline_is_tier0</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">live_public_pipeline (live_public_pipeline.py) is tier 0 — indexed/surface.</span></li>
<li><code>test_academic_discovery_is_tier1</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">academic_discovery (live_public_pipeline.py:1995) is tier 1.</span></li>
<li><code>test_pastebin_monitor_is_tier1</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">pastebin_monitor (live_public_pipeline.py:2067) is tier 1.</span></li>
<li><code>test_github_secret_scanner_is_tier1</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">github_secret_scanner (live_public_pipeline.py:2107) is tier 1.</span></li>
<li><code>test_lowercase_scheme_host</code> (test_hledac_core_rust.py)</li>
<li><code>test_preserve_path</code> (test_hledac_core_rust.py)</li>
<li><code>test_preserve_valid_params</code> (test_hledac_core_rust.py)</li>
<li><code>test_ipv6_in_url</code> (test_hledac_core_rust.py)</li>
<li><code>test_empty_url</code> (test_hledac_core_rust.py)</li>
<li><code>test_creation</code> (test_hledac_core_rust.py)</li>
<li><code>test_creation_with_size</code> (test_hledac_core_rust.py)</li>
<li><code>test_batch_dedup_empty</code> (test_hledac_core_rust.py)</li>
<li><code>test_module_guarded</code> (test_hledac_core_rust.py) — <span class="doc-comment-inline">Ensure all imports are properly guarded.</span></li>
<li><code>test_content_hash_64_idempotent</code> (test_hledac_core_rust.py)</li>
<li><code>test_simhash_same_text_distance_zero</code> (test_hledac_core_rust.py)</li>
<li><code>test_find_near_duplicates_empty_list</code> (test_hledac_core_rust.py)</li>
<li><code>test_find_near_duplicates_in_batch_empty</code> (test_hledac_core_rust.py)</li>
<li><code>test_batch_nfc_normalize_empty_list</code> (test_hledac_core_rust.py)</li>
<li><code>fetch</code> (test_t1_otel.py)</li>
<li><code>documented_fn</code> (test_t1_otel.py) — <span class="doc-comment-inline">My docstring.</span></li>
<li><code>test_empty_returns_none</code> (test_t1_otel.py)</li>
<li><code>test_shutdown_without_init_noop</code> (test_t1_otel.py)</li>
<li><code>task</code> (test_t1_otel.py)</li>
<li><code>delta_mb</code> (memory_profiler.py) — <span class="doc-comment-inline">Return delta in MB.</span></li>
<li><code>stop</code> (memory_profiler.py) — <span class="doc-comment-inline">Stop tracemalloc if not in session mode (no-op in session mode).</span></li>
<li><code>finalize</code> (test_sprint8l_live.py)</li>
<li><code>start</code> (test_sprint8l_live.py)</li>
<li><code>test_assert_no_leak_default_threshold</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">Default LEAK_THRESHOLD_MB is 50.0.</span></li>
<li><code>test_lane_rules_count</code> (test_f227k_acquisition_lane_parity.py) — <span class="doc-comment-inline">Verify LANE_RULES has exactly 15 entries (one per AcquisitionLane).</span></li>
<li><code>counter</code> (test_f227k_acquisition_lane_parity.py)</li>
<li><code>never_completes</code> (test_coroutine_cleanup.py)</li>
<li><code>slow_operation</code> (test_coroutine_cleanup.py)</li>
<li><code>quick_op</code> (test_coroutine_cleanup.py)</li>
<li><code>slow_op</code> (test_coroutine_cleanup.py)</li>
<li><code>cancellable_work</code> (test_coroutine_cleanup.py)</li>
<li><code>work</code> (test_coroutine_cleanup.py)</li>
<li><code>mock_pipeline_run</code> (test_coroutine_cleanup.py)</li>
<li><code>slow_pipeline</code> (test_coroutine_cleanup.py)</li>
<li><code>mock_discovery</code> (test_coroutine_cleanup.py)</li>
<li><code>slow_discovery</code> (test_coroutine_cleanup.py)</li>
<li><code>list_async_wrapper</code> (test_coroutine_cleanup.py)</li>
<li><code>task_work</code> (test_coroutine_cleanup.py)</li>
<li><code>__init__</code> (test_hypothesis_engine.py)</li>
<li><code>_patched_live_feed_pipeline</code> (test_e2e_first_finding.py)</li>
<li><code>_patched_public_pipeline</code> (test_e2e_first_finding.py)</li>
<li><code>_fake_pivot</code> (test_e2e_first_finding.py)</li>
<li><code>broken_prewarm</code> (test_sprint_scheduler.py)</li>
<li><code>failing_store</code> (test_sprint_scheduler.py)</li>
<li><code>event_loop_policy</code> (conftest.py)</li>
<li><code>test_content_hash_64_different_inputs</code> (test_hledac_core_rust.py)</li>
<li><code>test_content_hash_hex_different_inputs</code> (test_hledac_core_rust.py)</li>
<li><code>test_simhash_identical_texts_equal_fingerprint</code> (test_hledac_core_rust.py)</li>
<li><code>get_span_context</code> (test_t1_otel.py)</li>
<li><code>get_span_context</code> (test_t1_otel.py)</li>
<li><code>get_span_context</code> (test_t1_otel.py)</li>
<li><code>get_span_context</code> (test_t1_otel.py)</li>
<li><code>get_span_context</code> (test_t1_otel.py)</li>
<li><code>get_span_context</code> (test_t1_otel.py)</li>
<li><code>add</code> (test_t1_otel.py)</li>
<li><code>my_func</code> (test_t1_otel.py)</li>
<li><code>f</code> (test_t1_otel.py)</li>
<li><code>boom</code> (test_t1_otel.py)</li>
<li><code>my_async_fn</code> (test_t1_otel.py)</li>
<li><code>__del__</code> (memory_profiler.py)</li>
<li><code>tracker_finalizer</code> (test_sprint_memory_profiling.py)</li>
<li><code>__init__</code> (test_coroutine_cleanup.py)</li>
<li><code>background_work</code> (test_coroutine_cleanup.py)</li>
<li><code>long_running</code> (test_coroutine_cleanup.py)</li>
</ul>
</details>

<details><summary><strong>Class</strong> (155)</summary>
<ul>
<li><code>TestP12HermesLifecycleUnderModelManager</code> (test_sprint_p12_hypothesis.py)
<details><summary>Test Hermes lifecycle under ModelManager (canonical owner).</summary>
<div class="doc-comment">
<p>Test Hermes lifecycle under ModelManager (canonical owner).</p>
<p></p>
<p>Invariant table:</p>
<p>| Test | Invariant |</p>
<p>|------|-----------|</p>
<p>| test_load_via_model_manager | Hermes loaded via ModelManager.load_model("hermes"), not direct Hermes3Engine |</p>
<p>| test_unload_via_model_manager | Hermes unloaded via ModelManager.release_model("hermes") |</p>
<p>| test_safe_skip_on_memory_pressure | Hermes load skipped when ModelManager raises MemoryPressureError |</p>
<p>| test_gate_open_with_store_and_hermes | P12 gate opens when store+hermes_engine+stored findings all present |</p>
<p>| test_teardown_calls_unload_method | Teardown path calls _unload_hermes_at_teardown |</p>
</div>
</details>
</li>
<li><code>TestRustBackendSprintPoliciesFallback</code> (test_rust_backend.py) — <span class="doc-comment-inline">Sprint policies domain — Python fallback tests (F5.2).</span></li>
<li><code>TestSimhash</code> (test_hledac_core_rust.py) — <span class="doc-comment-inline">Test SimHash near-duplicate detection functions.</span></li>
<li><code>TracemallocSnapshot</code> (memory_profiler.py)
<details><summary>tracemalloc-based snapshot for Python object allocation tracking.</summary>
<div class="doc-comment">
<p>tracemalloc-based snapshot for Python object allocation tracking.</p>
<p></p>
<p>More precise than RSS for detecting Python object leaks (e.g., lists</p>
<p>accumulating in module globals, forgotten callbacks, etc.)</p>
<p></p>
<p>Example:</p>
<p>snap = TracemallocSnapshot()</p>
<p># ... test code ...</p>
<p>top_deltas = snap.compare_top_n(5)</p>
<p>for stat in top_deltas:</p>
<p>print(f"  {stat}: {stat.size_diff/1024:.1f} KB")</p>
</div>
</details>
</li>
<li><code>TestSprintT1StdoutExporter</code> (test_t1_otel.py) — <span class="doc-comment-inline">OTLP/JSON-Lines format, bounded attrs, fail-soft.</span></li>
<li><code>TestEvidenceLogCorrelation</code> (test_correlation_propagation.py) — <span class="doc-comment-inline">Test EvidenceLog.create_event correlation support.</span></li>
<li><code>TestDifferentialUrlDomain</code> (test_differential_fuzzing.py) — <span class="doc-comment-inline">Differential fuzzing pro URL domain — Rust vs Python.</span></li>
<li><code>TestF11WindupFirstCycle</code> (test_sprint_scheduler.py)
<details><summary>F1-1: Windup guard first_cycle_ran identity bug.</summary>
<div class="doc-comment">
<p>F1-1: Windup guard first_cycle_ran identity bug.</p>
<p></p>
<p>Hypotéza A: set_first_cycle_ran() a should_enter_windup() operují nad</p>
<p>různými instancemi SprintLifecycleManager (přes _LifecycleAdapter wrapper).</p>
<p></p>
<p>Test ověřuje, že _LifecycleAdapter.set_first_cycle_ran() správně propaguje</p>
<p>first_cycle_ran=True do underlying lifecycle na STEJNÉ instanci.</p>
</div>
</details>
</li>
<li><code>TestP12DILoadWire</code> (test_sprint_p12_hypothesis.py) — <span class="doc-comment-inline">P12 DI wire tests: gate opens only when store+hermes_engine+stored_findings&gt;0.</span></li>
<li><code>TestTorrentMetadataFetcher</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Tests for dht/metadata_fetcher.py</span></li>
<li><code>TestToolExecLogCorrelation</code> (test_correlation_propagation.py) — <span class="doc-comment-inline">Test ToolExecLog.log() correlation support.</span></li>
<li><code>TestSprintMemoryProfilingG_SessionTracer</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">G. Session-scoped tracemalloc tracer — init/stop/is_tracing functions.</span></li>
<li><code>TestCoroutineCleanup</code> (test_coroutine_cleanup.py) — <span class="doc-comment-inline">Verify coroutine cleanup patterns prevent memory leaks.</span></li>
<li><code>MemoryTracker</code> (memory_profiler.py)
<details><summary>Combined RSS + tracemalloc context manager for sprint cycle testing.</summary>
<div class="doc-comment">
<p>Combined RSS + tracemalloc context manager for sprint cycle testing.</p>
<p></p>
<p>Use as a context manager around a single sprint cycle to capture</p>
<p>memory state before/after and produce a detailed leak report.</p>
<p></p>
<p>Example:</p>
<p>with MemoryTracker() as tracker:</p>
<p>await run_one_sprint_cycle()</p>
<p>tracker.assert_leak_threshold(50)</p>
<p></p>
<p>Or for CI automation:</p>
<p>tracker = MemoryTracker(threshold_mb=50)</p>
<p>tracker.__enter__()</p>
<p>try:</p>
<p>await run_one_sprint_cycle()</p>
<p>finally:</p>
<p>tracker.__exit__(None, None, None)</p>
<p># raises AssertionError on leak</p>
</div>
</details>
</li>
<li><code>TestP12HermesPrewarmPolicy</code> (test_sprint_p12_hypothesis.py)
<details><summary>Test Hermes prewarm policy for aggressive vs stable mode.</summary>
<div class="doc-comment">
<p>Test Hermes prewarm policy for aggressive vs stable mode.</p>
<p></p>
<p>Invariant table:</p>
<p>| Test | Invariant |</p>
<p>|------|-----------|</p>
<p>| test_aggressive_mode_blocks_until_hermes_prewarm | Aggressive mode calls _prewarm_hermes_for_sprint which blocks until loaded |  # noqa: E501</p>
<p>| test_skip_hermes_prewarm_when_rss_above_4gb | When RSS &gt; 4GB before prewarm, Hermes is skipped (aggressive mode) |</p>
<p>| test_teardown_still_releases_hermes_after_prewarm | After prewarm+load, teardown still calls _unload_hermes_at_teardown |  # noqa: E501</p>
</div>
</details>
</li>
<li><code>TestAnalyticsHookCorrelation</code> (test_correlation_propagation.py) — <span class="doc-comment-inline">Test analytics_hook.shadow_record_finding correlation support.</span></li>
<li><code>TestF273CPatternExtractionDrain</code> (test_sprint_f273.py) — <span class="doc-comment-inline">F273C: schedule_html_extraction + drain_pending_extractions in public_fetcher.</span></li>
<li><code>TestSprint8AXMemoryMode</code> (test_sprint8ax_duckdb_shadow.py)</li>
<li><code>TestArchiveAcademicContributionSurface</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">F193B: CommonCrawl and academic findings surface in canonical export.</span></li>
<li><code>TestResearchDepthLevelThresholds</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">Level assignment must follow threshold boundaries exactly.</span></li>
<li><code>TestSourceTaxonomyContribution</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">Verify each new source type contributes correctly to research depth components.</span></li>
<li><code>TestWaitForTimeouts</code> (test_coroutine_cleanup.py) — <span class="doc-comment-inline">Verify asyncio.wait_for is used for all blocking operations.</span></li>
<li><code>PhaseTracker</code> (test_sprint8l_live.py)</li>
<li><code>TestGeminiTransport</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Tests for network/gemini_transport.py</span></li>
<li><code>TestF273BWindupRatio</code> (test_sprint_f273.py)
<details><summary>F288: effective_windup_lead_s uses 0.30 ratio (standard), [30, 60/120] cap.</summary>
<div class="doc-comment">
<p>F288: effective_windup_lead_s uses 0.30 ratio (standard), [30, 60/120] cap.</p>
<p></p>
<p>Aggressive mode uses 0.15 ratio. F221-ABORT guard uses 30%/[30,180].</p>
</div>
</details>
</li>
<li><code>TestDifferentialIpDomain</code> (test_differential_fuzzing.py) — <span class="doc-comment-inline">Differential fuzzing pro IP domain.</span></li>
<li><code>TestF273ADynamicBranchFloor</code> (test_sprint_f273.py) — <span class="doc-comment-inline">F273A: _MIN_BRANCH_REMAINING_S is now dynamic via _min_branch_remaining_s().</span></li>
<li><code>TestSprintT1BoundedRing</code> (test_t1_otel.py) — <span class="doc-comment-inline">LRU ring buffer: bounded, thread-safe, O(1) ops.</span></li>
<li><code>TestMetricsRegistryCorrelation</code> (test_correlation_propagation.py) — <span class="doc-comment-inline">Test MetricsRegistry correlation support.</span></li>
<li><code>TestCorrelationSchema</code> (test_correlation_propagation.py) — <span class="doc-comment-inline">Test RunCorrelation canonical schema in types.py.</span></li>
<li><code>TracemallocStats</code> (memory_profiler.py)
<details><summary>Lightweight tracemalloc stats using get_traced_memory() — 2 numbers, no snapshot.</summary>
<div class="doc-comment">
<p>Lightweight tracemalloc stats using get_traced_memory() — 2 numbers, no snapshot.</p>
<p></p>
<p>F350M-R fix: tracemalloc.take_snapshot() allocates a full Python object graph</p>
<p>(~15-20 MB per snapshot on a 181-test suite). get_traced_memory() returns</p>
<p>only two integers: current bytes and peak bytes — zero allocation overhead.</p>
<p></p>
<p>For detailed per-site breakdown (when needed), call take_snapshot() separately</p>
<p>and use compare_top_n() on TracemallocSnapshot.</p>
<p></p>
<p>Example:</p>
<p>stats = TracemallocStats.take()</p>
<p># ... test code ...</p>
<p>delta = stats.delta_bytes()</p>
<p>assert delta &lt; 10 * 1024 * 1024, f"Python allocations grew {delta / 1024 / 1024:.1f} MB"</p>
</div>
</details>
</li>
<li><code>TestP12ParallelHypothesisBurst</code> (test_sprint_p12_hypothesis.py) — <span class="doc-comment-inline">Sprint P12: Parallel hypothesis burst — bounded concurrent ToT evaluation.</span></li>
<li><code>TestSprintT1Instrumented</code> (test_t1_otel.py) — <span class="doc-comment-inline">@instrumented(name, **attrs) — sync + async, fail-safe.</span></li>
<li><code>TestExtractIocs</code> (test_hledac_core_rust.py) — <span class="doc-comment-inline">Test IOC extraction for each type.</span></li>
<li><code>TestSprintMemoryProfilingC_TracemallocSnapshot</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">C. TracemallocSnapshot — Python object allocation tracking.</span></li>
<li><code>TestF285Acllose</code> (test_sprint_scheduler.py)
<details><summary>F285: SprintScheduler.aclean() graceful shutdown protocol.</summary>
<div class="doc-comment">
<p>F285: SprintScheduler.aclean() graceful shutdown protocol.</p>
<p></p>
<p>Verifies that aclose() runs all cleanup steps without raising,</p>
<p>handles missing attributes gracefully, and is idempotent.</p>
</div>
</details>
</li>
<li><code>TestDifferentialQualityDomain</code> (test_differential_fuzzing.py) — <span class="doc-comment-inline">Differential fuzzing pro Quality domain — Rust vs Python.</span></li>
<li><code>TestGopherTransport</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Tests for transport/gopher_transport.py</span></li>
<li><code>TestTaskReferenceManagement</code> (test_coroutine_cleanup.py) — <span class="doc-comment-inline">Verify create_task saves references for cleanup.</span></li>
<li><code>TestSprint8AXFlagOn</code> (test_sprint8ax_duckdb_shadow.py)</li>
<li><code>TestF273DForceHermes</code> (test_sprint_f273.py) — <span class="doc-comment-inline">F273D: hermes_force flag wires through SprintFlags -&gt; SprintScheduler.</span></li>
<li><code>TestSprintMemoryProfilingA_RssSnapshot</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">A. RSS snapshot and delta measurement.</span></li>
<li><code>TestSprintMemoryProfilingB_MemoryTracker</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">B. MemoryTracker context manager — bookend + assertion.</span></li>
<li><code>TestF289WindupBudget</code> (test_sprint_scheduler.py) — <span class="doc-comment-inline">F288-WINDUP: effective_windup_lead_s uses 30% ratio / [30, 180] ceiling.</span></li>
<li><code>TestCorroborationComponent</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">Corroboration score rewards cross-source signal validation.</span></li>
<li><code>TestPivotDepthComponent</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">Pivot depth rewards hypothesis generation and pivot recommendations.</span></li>
<li><code>TestF273FFnocacheRuntimeArtifacts</code> (test_sprint_f273.py) — <span class="doc-comment-inline">F273F: apply_nocache_to_path for LMDB / DuckDB / telemetry artifacts.</span></li>
<li><code>TestSprintMemoryProfilingH_TracemallocStats</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">H. TracemallocStats — lightweight 2-number tracemalloc without take_snapshot().</span></li>
<li><code>TestResearchDepthInExportReturn</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">export_sprint() must include research_depth_metric in its return dict.</span></li>
<li><code>TestResearchDepthOutputShape</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">Output dict keys and types must be stable across all input combinations.</span></li>
<li><code>TestAlternativeProtocolFetcher</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Tests for fetching/alternative_protocol_fetcher.py</span></li>
<li><code>TestDifferentialSimdDomain</code> (test_differential_fuzzing.py) — <span class="doc-comment-inline">Differential fuzzing pro SIMD domain (cosine similarity).</span></li>
<li><code>TestP12GateLogic</code> (test_sprint_p12_hypothesis.py) — <span class="doc-comment-inline">Verify P12 gate uses store+hermes_engine+total_stored, not memory_manager.</span></li>
<li><code>TestRustBackendUrlFallback</code> (test_rust_backend.py) — <span class="doc-comment-inline">URL engine domain — Python fallback tests.</span></li>
<li><code>TestLaneTableDrift</code> (test_f227k_acquisition_lane_parity.py) — <span class="doc-comment-inline">Table-driven implementation correctness checks.</span></li>
<li><code>TestF270InitOrder</code> (test_sprint_scheduler.py)
<details><summary>F270: SprintScheduler v2 __init__ invariants.</summary>
<div class="doc-comment">
<p>F270: SprintScheduler v2 __init__ invariants.</p>
<p></p>
<p>F350M-R migration: SprintScheduler now resolves to SprintSchedulerV2.</p>
<p>V2 uses @dataclass(slots=True) with __post_init__ for initialization.</p>
<p>The 17-phase v1 init pattern no longer applies — replaced by</p>
<p>Protocol-based phase composition in _initialize_sprint_run().</p>
</div>
</details>
</li>
<li><code>TestRustBackendQualityFallback</code> (test_rust_backend.py) — <span class="doc-comment-inline">Quality gate domain — Python fallback tests.</span></li>
<li><code>TestContentHashXxhash</code> (test_hledac_core_rust.py) — <span class="doc-comment-inline">Test xxHash3-64 content hashing for dedup keys and cache IDs.</span></li>
<li><code>RSSMonitor</code> (test_sprint8l_live.py)</li>
<li><code>TestSprintMemoryProfilingE_ConftestFixtures</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">E. Conftest fixtures integration — memory_snapshot, memory_tracker, assert_memory_leak.</span></li>
<li><code>TestIntegrationCleanup</code> (test_coroutine_cleanup.py) — <span class="doc-comment-inline">End-to-end cleanup scenarios.</span></li>
<li><code>TestSourceTaxonomyNormalization</code> (test_research_depth_metric.py)
<details><summary>Sprint F192H: Verify all active canonical source types are in _SOURCE_TIER.</summary>
<div class="doc-comment">
<p>Sprint F192H: Verify all active canonical source types are in _SOURCE_TIER.</p>
<p>Mismatches found:</p>
<p>- ct_log (ct_log_client.py:273) vs ct_log_pipeline in tier map</p>
<p>- onion_discovery (live_public_pipeline.py:1785) — missing from tier map</p>
<p>- ipfs (ti_feed_adapter.py:1367) — missing from tier map</p>
<p>- shodan_search (shodan_wrapper.py:204) — missing from tier map</p>
<p>- bgp_monitor (ti_feed_adapter.py:1742) — missing from tier map</p>
</div>
</details>
</li>
<li><code>TestF273IBackwardCompat</code> (test_sprint_f273.py)
<details><summary>F288: Windup formula updated to 0.30 ratio / [30, 60/120] cap.</summary>
<div class="doc-comment">
<p>F288: Windup formula updated to 0.30 ratio / [30, 60/120] cap.</p>
<p></p>
<p>Aggressive mode uses 0.15 ratio. Previous contracts superseded.</p>
</div>
</details>
</li>
<li><code>TestSprintMemoryProfilingI_MemoryTrackerWeakref</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">I. MemoryTracker weakref finalizer safety net (Issue #12).</span></li>
<li><code>TestBranchDiversityComponent</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">Branch diversity rewards parallel use of feed + public + CT branches.</span></li>
<li><code>Snapshot</code> (memory_profiler.py)
<details><summary>Bookend snapshot for RSS delta measurement.</summary>
<div class="doc-comment">
<p>Bookend snapshot for RSS delta measurement.</p>
<p></p>
<p>Takes a RSS snapshot on creation. Call `delta_mb()` after the code</p>
<p>under test runs to get the RSS change.</p>
<p></p>
<p>Example:</p>
<p>snap = Snapshot()</p>
<p># ... test code ...</p>
<p>delta = snap.delta_mb()</p>
<p>assert delta &lt; 50, f"Memory leak: {delta:.1f} MB"</p>
</div>
</details>
</li>
<li><code>TestSourceDiversityComponent</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">Source diversity score responds correctly to source type diversity.</span></li>
<li><code>TestRustBackendBloomFallback</code> (test_rust_backend.py) — <span class="doc-comment-inline">Bloom filter domain — Python fallback tests.</span></li>
<li><code>TestNonIndexedRatioComponent</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">Non-indexed ratio rewards use of deep/structured sources.</span></li>
<li><code>TestDepthSignalsReflectInputs</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">depth_signals dict must accurately reflect the computed inputs.</span></li>
<li><code>TestSprintT1M1Safety</code> (test_t1_otel.py) — <span class="doc-comment-inline">M1 8GB bounds: bounded RAM, no leaks, thread-safe, async-safe.</span></li>
<li><code>TestIPFSClient</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Tests for network/ipfs_client.py</span></li>
<li><code>TestSprintT1PublicAPI</code> (test_t1_otel.py) — <span class="doc-comment-inline">The public API surface exists and is importable.</span></li>
<li><code>LiveHandlerLatency</code> (test_sprint8l_live.py) — <span class="doc-comment-inline">Per-handler latency statistics.</span></li>
<li><code>TestLoopCleanup</code> (test_coroutine_cleanup.py) — <span class="doc-comment-inline">Verify event loop cleanup patterns.</span></li>
<li><code>TestPipelinePatterns</code> (test_coroutine_cleanup.py) — <span class="doc-comment-inline">Verify correct patterns for async pipeline cleanup.</span></li>
<li><code>TestNormalize</code> (test_hledac_core_rust.py) — <span class="doc-comment-inline">Test URL normalization via Rust url_engine.</span></li>
<li><code>TestSprintT1SpanContext</code> (test_t1_otel.py) — <span class="doc-comment-inline">span(name, **attrs) context manager — fail-safe.</span></li>
<li><code>TestDifferentialIocDomain</code> (test_differential_fuzzing.py)
<details><summary>Differential fuzzing pro IOC domain — CRITICKÝ TEST.</summary>
<div class="doc-comment">
<p>Differential fuzzing pro IOC domain — CRITICKÝ TEST.</p>
<p></p>
<p>F5.3: API MISMATCH — Python extract_iocs() vrací dict-of-lists,</p>
<p>Rust vrací flat list of tuples. Testujeme pouze NFC normalizaci.</p>
</div>
</details>
</li>
<li><code>TestRustBackendIocDedupFallback</code> (test_rust_backend.py) — <span class="doc-comment-inline">IOC dedup domain — Python fallback tests.</span></li>
<li><code>TestFediverseAdapter</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Tests for discovery/fediverse_adapter.py</span></li>
<li><code>TestF273GMallocPressureRelief</code> (test_sprint_f273.py) — <span class="doc-comment-inline">F273G: _maybe_call_pressure_relief wired into pre-windup barrier.</span></li>
<li><code>TestBatchNfcNormalize</code> (test_hledac_core_rust.py)
<details><summary>Test Rust batch_nfc_normalize for Unicode text normalization.</summary>
<div class="doc-comment">
<p>Test Rust batch_nfc_normalize for Unicode text normalization.</p>
<p></p>
<p>ISSUE #022 FIX: Previously streaming_embedder used pipeline_compose_two</p>
<p>with "nfc_normalize" stage name which was never registered — all items</p>
<p>were silently dropped. batch_nfc_normalize is the correct direct entry point.</p>
</div>
</details>
</li>
<li><code>TestSprintT1AttributeSanitize</code> (test_t1_otel.py) — <span class="doc-comment-inline">_filter_attrs — coerce arbitrary Python into OTel-safe values.</span></li>
<li><code>TestMatrixAdapter</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Tests for discovery/matrix_adapter.py</span></li>
<li><code>TestDifferentialTextDomain</code> (test_differential_fuzzing.py) — <span class="doc-comment-inline">Differential fuzzing pro Text domain (NFC, diacritics).</span></li>
<li><code>TestRollingHashEngine</code> (test_hledac_core_rust.py) — <span class="doc-comment-inline">Test Rust RollingHashEngine class.</span></li>
<li><code>LiveLatencyCollector</code> (test_sprint8l_live.py) — <span class="doc-comment-inline">Wraps orchestrator to capture per-handler latency.</span></li>
<li><code>TestP12PostStoragePlacement</code> (test_sprint_p12_hypothesis.py) — <span class="doc-comment-inline">Verify P12 hypothesis layer is placed AFTER storage, not before fetch.</span></li>
<li><code>TestP12UsesRealFindings</code> (test_sprint_p12_hypothesis.py) — <span class="doc-comment-inline">Verify P12 builds context from real stored findings, not placeholder RAG.</span></li>
<li><code>TestI2PClient</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Tests for network/i2p_client.py</span></li>
<li><code>TestResearchDepthScoreBounds</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">Score must never exceed 100.0 or go below 0.0.</span></li>
<li><code>TestRustBackendModule</code> (test_rust_backend.py) — <span class="doc-comment-inline">Module-level import and singleton tests.</span></li>
<li><code>TestRustBackendIocFallback</code> (test_rust_backend.py) — <span class="doc-comment-inline">IOC extraction domain — Python fallback tests.</span></li>
<li><code>LiveBenchmarkResults</code> (test_sprint8l_live.py) — <span class="doc-comment-inline">Complete LIVE benchmark results.</span></li>
<li><code>TestAltProtocolFetcherSocial</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Tests for alternative_protocol_fetcher.py social protocol wiring.</span></li>
<li><code>TestF271BCompliance</code> (test_coroutine_cleanup.py) — <span class="doc-comment-inline">Verify F271B asyncio.wait_for(..., timeout=35.0) compliance.</span></li>
<li><code>TestSprint8AXBatchChunking</code> (test_sprint8ax_duckdb_shadow.py)</li>
<li><code>TestSprint8AXRegression</code> (test_sprint8ax_duckdb_shadow.py)</li>
<li><code>TestDifferentialSimhashDomain</code> (test_differential_fuzzing.py) — <span class="doc-comment-inline">Differential fuzzing pro Simhash domain.</span></li>
<li><code>TestP12BoundedBehavior</code> (test_sprint_p12_hypothesis.py) — <span class="doc-comment-inline">Verify P12 respects M1 8GB constraints: bounded hypotheses, fail-soft.</span></li>
<li><code>TestSprintT1Integration</code> (test_t1_otel.py) — <span class="doc-comment-inline">End-to-end: instrumented decorator on real fetch/run functions.</span></li>
<li><code>TestSprintT1InitLifecycle</code> (test_t1_otel.py) — <span class="doc-comment-inline">init_telemetry() / shutdown_telemetry() — idempotent, safe.</span></li>
<li><code>TestF273HResultDiagnostics</code> (test_sprint_f273.py) — <span class="doc-comment-inline">F273H: All F273 result fields are present with correct defaults.</span></li>
<li><code>TestMemoryImpact</code> (test_coroutine_cleanup.py) — <span class="doc-comment-inline">Verify memory impact of coroutine leaks.</span></li>
<li><code>TestSprint8AXAclclose</code> (test_sprint8ax_duckdb_shadow.py)</li>
<li><code>TestRustBackendIpFallback</code> (test_rust_backend.py) — <span class="doc-comment-inline">IP parsing domain — Python fallback tests.</span></li>
<li><code>TestSprintT1NoOp</code> (test_t1_otel.py) — <span class="doc-comment-inline">When OTel is not available, every call must be a silent no-op.</span></li>
<li><code>TestSprint8AXQueueFull</code> (test_sprint8ax_duckdb_shadow.py)</li>
<li><code>TestP12HypothesisEngine</code> (test_sprint_p12_hypothesis.py) — <span class="doc-comment-inline">Verify P12 correctly uses HypothesisEngine for generation.</span></li>
<li><code>TestP12CanonicalBehavior</code> (test_sprint_p12_hypothesis.py) — <span class="doc-comment-inline">Canonical sprint behavior: no ToT on empty runs.</span></li>
<li><code>TestAltProtocolsIntegration</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Integration tests that require actual network access.</span></li>
<li><code>TestSprint8AXFlagOff</code> (test_sprint8ax_duckdb_shadow.py)</li>
<li><code>TestRustBackendHashFallback</code> (test_rust_backend.py) — <span class="doc-comment-inline">Hash domain — Python fallback tests.</span></li>
<li><code>TestRustBackendHotEdgesFallback</code> (test_rust_backend.py) — <span class="doc-comment-inline">Hot edges domain — Python fallback tests.</span></li>
<li><code>TestDHTAdapterBEP9</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Tests for dht_adapter.py BEP-9 integration.</span></li>
<li><code>TestSprint8AXFailOpen</code> (test_sprint8ax_duckdb_shadow.py)</li>
<li><code>TestDifferentialHashDomain</code> (test_differential_fuzzing.py) — <span class="doc-comment-inline">Differential fuzzing pro Hash domain.</span></li>
<li><code>TestSprintT1RingExporter</code> (test_t1_otel.py) — <span class="doc-comment-inline">RingBufferExporter stores bounded span summaries.</span></li>
<li><code>TestSprint8AXProductionPath</code> (test_sprint8ax_duckdb_shadow.py)</li>
<li><code>TestDifferentialHtmlDomain</code> (test_differential_fuzzing.py) — <span class="doc-comment-inline">Differential fuzzing pro HTML domain.</span></li>
<li><code>TestDifferentialBloomDomain</code> (test_differential_fuzzing.py) — <span class="doc-comment-inline">Differential fuzzing pro BloomFilter domain.</span></li>
<li><code>TestSprintMemoryProfilingF_StandaloneAssertNoLeak</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">F. Standalone assert_no_leak() helper function.</span></li>
<li><code>TestP12NoPreFetchLatency</code> (test_sprint_p12_hypothesis.py) — <span class="doc-comment-inline">Verify canonical sprint does not incur ToT latency before fetch batch.</span></li>
<li><code>TestSprintMemoryProfilingD_FixtureIntegration</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">D. Sprint cycle memory leak detection — integration with lifecycle.</span></li>
<li><code>TestSprint8AXHookLocation</code> (test_sprint8ax_duckdb_shadow.py)</li>
<li><code>TestFingerprint</code> (test_hledac_core_rust.py) — <span class="doc-comment-inline">Test URL fingerprinting.</span></li>
<li><code>TestF273EAiofilesStreamingExporter</code> (test_sprint_f273.py) — <span class="doc-comment-inline">F273E: streaming_exporter._write_section uses aiofiles (with sync fallback).</span></li>
<li><code>_LazyForceLoadFinder</code> (conftest.py) — <span class="doc-comment-inline">Meta-path finder: force-loads from HUB_DIR on first import, then steps aside.</span></li>
<li><code>TestBatchDedupUrls</code> (test_hledac_core_rust.py) — <span class="doc-comment-inline">Test batch URL deduplication.</span></li>
<li><code>TestRustBackendSimhashFallback</code> (test_rust_backend.py) — <span class="doc-comment-inline">SimHash domain — Python fallback tests.</span></li>
<li><code>TestRustBackendMemoryFallback</code> (test_rust_backend.py) — <span class="doc-comment-inline">Memory probe domain — Python fallback tests.</span></li>
<li><code>LeakTracker</code> (test_coroutine_cleanup.py) — <span class="doc-comment-inline">Track coroutine objects to detect leaks.</span></li>
<li><code>TestStripTrackingParams</code> (test_hledac_core_rust.py) — <span class="doc-comment-inline">Test tracking parameter stripping.</span></li>
<li><code>TestRustBackendRollingHashFallback</code> (test_rust_backend.py) — <span class="doc-comment-inline">Rolling hash domain — Python fallback tests.</span></li>
<li><code>TestRustBackendSimdFallback</code> (test_rust_backend.py) — <span class="doc-comment-inline">SIMD domain — Python fallback tests.</span></li>
<li><code>TestBloomFilter</code> (test_hledac_core_rust.py) — <span class="doc-comment-inline">Test Rust BloomFilter class.</span></li>
<li><code>TestRustBackendEvidenceFallback</code> (test_rust_backend.py) — <span class="doc-comment-inline">Evidence domain — Python fallback tests.</span></li>
<li><code>TestRustBackendHtmlFallback</code> (test_rust_backend.py) — <span class="doc-comment-inline">HTML parsing domain — Python fallback tests.</span></li>
<li><code>TestSprintT1Context</code> (test_t1_otel.py) — <span class="doc-comment-inline">trace_id/span_id surface — non-zero when in a span.</span></li>
<li><code>TestRustBackendGraphFallback</code> (test_rust_backend.py) — <span class="doc-comment-inline">Graph traversal domain — Python fallback tests.</span></li>
<li><code>_Sp</code> (test_t1_otel.py)</li>
<li><code>_Sp</code> (test_t1_otel.py)</li>
<li><code>_Sp</code> (test_t1_otel.py)</li>
<li><code>_Sp</code> (test_t1_otel.py)</li>
<li><code>_Sp</code> (test_t1_otel.py)</li>
<li><code>TestRustBackendAhoFallback</code> (test_rust_backend.py) — <span class="doc-comment-inline">Aho-Corasick domain — Python fallback tests.</span></li>
<li><code>_Sp</code> (test_t1_otel.py)</li>
<li><code>TestRustBackendMadviseFallback</code> (test_rust_backend.py) — <span class="doc-comment-inline">Madvise domain — Python fallback tests.</span></li>
<li><code>MockFinding</code> (test_hypothesis_engine.py) — <span class="doc-comment-inline">Minimal finding mock for hypothesis generation.</span></li>
<li><code>_FakeFeedBatch</code> (test_e2e_first_finding.py)</li>
<li><code>_Ev</code> (test_t1_otel.py)</li>
<li><code>_Ctx</code> (test_t1_otel.py)</li>
<li><code>_Ctx</code> (test_t1_otel.py)</li>
<li><code>Foo</code> (test_t1_otel.py)</li>
</ul>
</details>

<details><summary><strong>Method</strong> (561)</summary>
<ul>
<li><code>test_shadow_flag_on_records_batch</code> (test_sprint8ax_duckdb_shadow.py)
<details><summary>With GHOST_DUCKDB_SHADOW=1, evidence_packet events are shadow-recorded.</summary>
<div class="doc-comment">
<p>With GHOST_DUCKDB_SHADOW=1, evidence_packet events are shadow-recorded.</p>
<p>Uses :memory: mode (DB_ROOT unavailable in test env).</p>
</div>
</details>
</li>
<li><code>test_export_sprint_includes_research_depth_metric</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">FIX F350M-R: Use session_event_loop fixture instead of asyncio.run().</span></li>
<li><code>test_evidence_log_append_propagates_correlation_to_shadow</code> (test_correlation_propagation.py)
<details><summary>EvidenceLog.append() extracts _correlation from payload and passes to shadow_record_finding.</summary>
<div class="doc-comment">
<p>EvidenceLog.append() extracts _correlation from payload and passes to shadow_record_finding.</p>
<p></p>
<p>Verifies cross-ledger propagation: EvidenceLog → analytics_hook (DuckDB shadow).</p>
</div>
</details>
</li>
<li><code>test_memory_mode_persists_across_multiple_async_calls</code> (test_sprint8ax_duckdb_shadow.py)
<details><summary>In :memory: mode, repeated async writes should all go to the same</summary>
<div class="doc-comment">
<p>In :memory: mode, repeated async writes should all go to the same</p>
<p>persistent connection and be queryable across calls.</p>
</div>
</details>
</li>
<li><code>test_lifecycle_adapter_set_first_cycle_ran_propagates_to_same_instance</code> (test_sprint_scheduler.py)
<details><summary>F1-1: Ověřuje, že set_first_cycle_ran() na _LifecycleAdapter</summary>
<div class="doc-comment">
<p>F1-1: Ověřuje, že set_first_cycle_ran() na _LifecycleAdapter</p>
<p>skutečně nastaví first_cycle_ran na STEJNÉ instanci SprintLifecycleManager,</p>
<p>kterou should_enter_windup() čte.</p>
</div>
</details>
</li>
<li><code>test_otlp_json_shape</code> (test_t1_otel.py) — <span class="doc-comment-inline">A real span (from ring) serializes to valid OTLP/JSON.</span></li>
<li><code>compare_top_n</code> (memory_profiler.py)</li>
<li><code>test_memory_mode_uses_same_worker_thread_name</code> (test_sprint8ax_duckdb_shadow.py)
<details><summary>In :memory: mode, the duckdb_worker thread name should be stable</summary>
<div class="doc-comment">
<p>In :memory: mode, the duckdb_worker thread name should be stable</p>
<p>across multiple async batch calls.</p>
</div>
</details>
</li>
<li><code>test_safe_skip_on_memory_pressure</code> (test_sprint_p12_hypothesis.py)
<details><summary>When ModelManager raises RuntimeError (memory pressure), Hermes load is skipped.</summary>
<div class="doc-comment">
<p>When ModelManager raises RuntimeError (memory pressure), Hermes load is skipped.</p>
<p>Verifies fail-soft skip — sprint continues, ToT is skipped.</p>
</div>
</details>
</li>
<li><code>test_successful_load_sets_hermes_engine</code> (test_sprint_p12_hypothesis.py)
<details><summary>When ModelManager.load_model succeeds, hermes_engine is set.</summary>
<div class="doc-comment">
<p>When ModelManager.load_model succeeds, hermes_engine is set.</p>
<p>Verifies DI wire to public pipeline.</p>
</div>
</details>
</li>
<li><code>test_unload_releases_via_model_manager</code> (test_sprint_p12_hypothesis.py)
<details><summary>_unload_hermes_at_teardown calls ModelManager.release_model.</summary>
<div class="doc-comment">
<p>_unload_hermes_at_teardown calls ModelManager.release_model.</p>
<p>Verifies canonical unload authority.</p>
</div>
</details>
</li>
<li><code>test_f1_1_fallback_when_lc_adapter_is_none</code> (test_sprint_scheduler.py)
<details><summary>F1-1: Fallback logika — když _lc_adapter je None, kód správně</summary>
<div class="doc-comment">
<p>F1-1: Fallback logika — když _lc_adapter je None, kód správně</p>
<p>přistoupí přímo k lifecycle.first_cycle_ran místo volání adapteru.</p>
<p></p>
<p>SprintSchedulerV2 má __slots__ — nelze testovat přes object.__new__().</p>
<p>Testujeme přímo, že lifecycle podporuje first_cycle_ran a správně reaguje.</p>
</div>
</details>
</li>
<li><code>test_skip_hermes_prewarm_when_rss_above_4gb</code> (test_sprint_p12_hypothesis.py)
<details><summary>Aggressive mode: when RSS &gt; 4GB before prewarm, Hermes is skipped fail-soft.</summary>
<div class="doc-comment">
<p>Aggressive mode: when RSS &gt; 4GB before prewarm, Hermes is skipped fail-soft.</p>
<p>Hard headroom rule: RSS &gt; 4GB means insufficient headroom for safe prewarm.</p>
</div>
</details>
</li>
<li><code>test_flush_includes_correlation</code> (test_correlation_propagation.py) — <span class="doc-comment-inline">flush() serializes correlation into metrics JSONL.</span></li>
<li><code>test_async_generator_context_manager_pattern</code> (test_coroutine_cleanup.py)
<details><summary>RECOMMENDED: async generator as async context manager.</summary>
<div class="doc-comment">
<p>RECOMMENDED: async generator as async context manager.</p>
<p></p>
<p>Python 3.11+: async generators support `async with`:</p>
<p>```python</p>
<p>async def async_generator():</p>
<p>try:</p>
<p>yield ...</p>
<p>finally:</p>
<p>cleanup()</p>
<p></p>
<p>async def consumer():</p>
<p>async for item in async_generator():  # auto-aclose on exit</p>
<p>...</p>
<p>```</p>
<p></p>
<p>For older Python, use acli Util helper.</p>
</div>
</details>
</li>
<li><code>test_evidence_event_queryable</code> (test_correlation_propagation.py) — <span class="doc-comment-inline">Correlation in payload is queryable via payload access.</span></li>
<li><code>test_task_with_reference_can_be_cancelled</code> (test_coroutine_cleanup.py)
<details><summary>FIXED: Task saved to list for later cleanup.</summary>
<div class="doc-comment">
<p>FIXED: Task saved to list for later cleanup.</p>
<p></p>
<p>Correct pattern:</p>
<p>```python</p>
<p>tasks: list[asyncio.Task] = []</p>
<p>tasks.append(asyncio.create_task(coro()))</p>
<p># ... later ...</p>
<p>for t in tasks:</p>
<p>t.cancel()</p>
<p>await asyncio.gather(*tasks, return_exceptions=True)</p>
<p>```</p>
</div>
</details>
</li>
<li><code>test_lifecycle_adapter_should_enter_windup_uses_same_instance_as_setter</code> (test_sprint_scheduler.py)
<details><summary>F1-1: should_enter_windup() volaný přes adapter musí vidět stejný</summary>
<div class="doc-comment">
<p>F1-1: should_enter_windup() volaný přes adapter musí vidět stejný</p>
<p>first_cycle_ran stav jako set_first_cycle_ran() nastavil.</p>
</div>
</details>
</li>
<li><code>_read_current_state</code> (test_sprint8l_live.py) — <span class="doc-comment-inline">Read current phase and promotion score from orchestrator.</span></li>
<li><code>test_batch_chunking_1001_records_produces_3_batches</code> (test_sprint8ax_duckdb_shadow.py)
<details><summary>Inserting 1001 records with max_batch_size=500 must produce</summary>
<div class="doc-comment">
<p>Inserting 1001 records with max_batch_size=500 must produce</p>
<p>exactly 3 batch executions: 500 + 500 + 1.</p>
</div>
</details>
</li>
<li><code>test_evidence_log_append_still_works</code> (test_sprint8ax_duckdb_shadow.py) — <span class="doc-comment-inline">Adding the shadow hook must not break EvidenceLog.append().</span></li>
<li><code>test_async_generator_with_explicit_cleanup</code> (test_coroutine_cleanup.py)
<details><summary>FIXED: async generator with aclose() on early exit.</summary>
<div class="doc-comment">
<p>FIXED: async generator with aclose() on early exit.</p>
<p></p>
<p>Correct pattern:</p>
<p>```python</p>
<p>async def consume():</p>
<p>gen = async_range_slow(1000)</p>
<p>try:</p>
<p>async for item in gen:</p>
<p>if stop_condition:</p>
<p>break</p>
<p>finally:</p>
<p>await gen.aclose()  # CRITICAL!</p>
<p>```</p>
</div>
</details>
</li>
<li><code>test_aclose_timeout_does_not_block_forever</code> (test_sprint8ax_duckdb_shadow.py) — <span class="doc-comment-inline">aclose() with a stuck store should not block longer than its timeout.</span></li>
<li><code>test_aclose_has_log_output</code> (test_sprint_scheduler.py) — <span class="doc-comment-inline">aclean() must log completion with sprint_id and elapsed time.</span></li>
<li><code>test_unprotected_coroutine_can_hang</code> (test_coroutine_cleanup.py)
<details><summary>COROUTINE LEAK: Coroutine without timeout can hang indefinitely.</summary>
<div class="doc-comment">
<p>COROUTINE LEAK: Coroutine without timeout can hang indefinitely.</p>
<p></p>
<p>This test demonstrates the BUGGY pattern - without timeout protection,</p>
<p>a coroutine that takes too long will block indefinitely.</p>
</div>
</details>
</li>
<li><code>test_tool_exec_event_serialization</code> (test_correlation_propagation.py) — <span class="doc-comment-inline">ToolExecEvent.to_dict() includes correlation when present.</span></li>
<li><code>test_v2_slots_initialized_on_construction</code> (test_sprint_scheduler.py)
<details><summary>V2: construction initializes all slots via __post_init__.</summary>
<div class="doc-comment">
<p>V2: construction initializes all slots via __post_init__.</p>
<p></p>
<p>SprintSchedulerV2 uses @dataclass(slots=True) — all fields must be</p>
<p>set to None/initial values in __post_init__. This test verifies</p>
<p>the core orchestrator slots are accessible after construction.</p>
</div>
</details>
</li>
<li><code>test_async_generator_early_exit_leak</code> (test_coroutine_cleanup.py)
<details><summary>COROUTINE LEAK: async generator without aclose() on break.</summary>
<div class="doc-comment">
<p>COROUTINE LEAK: async generator without aclose() on break.</p>
<p></p>
<p>Without aclose(), the generator's __anext__ coroutine holds:</p>
<p>- Parent function's local variables</p>
<p>- Pending items list</p>
<p>- Any captured context</p>
<p></p>
<p>Memory impact: 5-20 KB per leaked generator.</p>
</div>
</details>
</li>
<li><code>test_full_pipeline_cleanup</code> (test_coroutine_cleanup.py) — <span class="doc-comment-inline">Full pipeline with proper cleanup.</span></li>
<li><code>test_many_leaked_generators_memory_impact</code> (test_coroutine_cleanup.py)
<details><summary>Verify: 1000 leaked generators ≈ 15-20 MB memory.</summary>
<div class="doc-comment">
<p>Verify: 1000 leaked generators ≈ 15-20 MB memory.</p>
<p></p>
<p>This test documents the memory cost of coroutine leaks.</p>
<p>Run with memory profiler to verify:</p>
<p>```</p>
<p>pip install memory_profiler</p>
<p>mprof run pytest tests/test_coroutine_cleanup.py::TestMemoryImpact::test_many_leaked_generators_memory_impact</p>
<p>mprof plot</p>
<p>```</p>
</div>
</details>
</li>
<li><code>test_evidence_event_serialization_stable</code> (test_correlation_propagation.py) — <span class="doc-comment-inline">EvidenceEvent.to_dict() serialization includes correlation when present.</span></li>
<li><code>test_log_with_correlation</code> (test_correlation_propagation.py) — <span class="doc-comment-inline">log() accepts correlation and stores in ToolExecEvent.correlation.</span></li>
<li><code>test_tool_exec_event_from_dict_with_correlation</code> (test_correlation_propagation.py) — <span class="doc-comment-inline">ToolExecEvent.from_dict() correctly deserializes correlation.</span></li>
<li><code>test_shadow_fail_open_queue_drop_when_full</code> (test_sprint8ax_duckdb_shadow.py) — <span class="doc-comment-inline">When the queue is full, records are dropped and _SHADOW_INGEST_FAILURES is incremented.</span></li>
<li><code>assert_leak_threshold</code> (memory_profiler.py)
<details><summary>Assert RSS delta is below threshold, with detailed failure message.</summary>
<div class="doc-comment">
<p>Assert RSS delta is below threshold, with detailed failure message.</p>
<p></p>
<p>Args:</p>
<p>threshold_mb: Override instance threshold. Uses instance value if None.</p>
<p></p>
<p>Raises:</p>
<p>AssertionError: If RSS grows beyond threshold, with detailed</p>
<p>breakdown showing tracemalloc allocation growth.</p>
</div>
</details>
</li>
<li><code>test_pipeline_with_timeout</code> (test_coroutine_cleanup.py)
<details><summary>FIXED: Pipeline operation with timeout protection.</summary>
<div class="doc-comment">
<p>FIXED: Pipeline operation with timeout protection.</p>
<p></p>
<p>Pattern for test_e2e_pipeline_smoke.py fix:</p>
<p>```python</p>
<p>async def run_pipeline():</p>
<p>try:</p>
<p>async with asyncio.timeout(120.0):</p>
<p>result = await pipeline.run()</p>
<p>return result</p>
<p>except asyncio.TimeoutError:</p>
<p>return None  # or handle gracefully</p>
<p>```</p>
</div>
</details>
</li>
<li><code>test_create_event_with_correlation_flat</code> (test_correlation_propagation.py) — <span class="doc-comment-inline">create_event accepts correlation dict and stores in payload._correlation.</span></li>
<li><code>test_shadow_flag_off_is_noop</code> (test_sprint8ax_duckdb_shadow.py) — <span class="doc-comment-inline">With GHOST_DUCKDB_SHADOW=0, no shadow records are written.</span></li>
<li><code>test_gate_requires_store_and_hermes_and_stored</code> (test_sprint_p12_hypothesis.py)
<details><summary>P12 gate requires ALL THREE: store is not None AND hermes_engine is not None AND total_stored &gt; 0.</summary>
<div class="doc-comment">
<p>P12 gate requires ALL THREE: store is not None AND hermes_engine is not None AND total_stored &gt; 0.</p>
<p>Canonical sprint DI wire: hermes_engine travels with duckdb_store into pipeline.</p>
</div>
</details>
</li>
<li><code>test_safe_wait_for_from_async_helpers</code> (test_coroutine_cleanup.py)
<details><summary>F320: safe_wait_for() wrapper from utils/async_helpers.</summary>
<div class="doc-comment">
<p>F320: safe_wait_for() wrapper from utils/async_helpers.</p>
<p></p>
<p>Preferred pattern for Python 3.14+ compatibility:</p>
<p>```python</p>
<p>from utils.async_helpers import safe_wait_for</p>
<p>result = await safe_wait_for(coro, timeout=30.0)</p>
<p>```</p>
</div>
</details>
</li>
<li><code>test_analytics_hook_fail_open_without_shadow</code> (test_correlation_propagation.py) — <span class="doc-comment-inline">shadow_record_finding is fail-open when shadow disabled.</span></li>
<li><code>test_shadow_failure_increments_warning_counter</code> (test_sprint8ax_duckdb_shadow.py) — <span class="doc-comment-inline">Shadow failures are fail-open: they increment the counter but never raise.</span></li>
<li><code>test_extract_iocs</code> (test_rust_backend.py) — <span class="doc-comment-inline">extract_iocs returns dict of IOC type -&gt; list of values (grouped format).</span></li>
<li><code>test_extract_intel_from_torrent</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test OSINT extraction from torrent metadata.</span></li>
<li><code>test_create_event_correlation_partial</code> (test_correlation_propagation.py) — <span class="doc-comment-inline">Correlation can be partial - only some keys present.</span></li>
<li><code>test_production_db_path_is_analytics_duckdb</code> (test_sprint8ax_duckdb_shadow.py)
<details><summary>When RAMDISK is inactive and DB_ROOT is available,</summary>
<div class="doc-comment">
<p>When RAMDISK is inactive and DB_ROOT is available,</p>
<p>the DuckDB store should use DB_ROOT / "analytics.duckdb" as the path.</p>
</div>
</details>
</li>
<li><code>test_pvs_commoncrawl_field_present</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">product_value_summary includes commoncrawl_archive_augmented when CC is active.</span></li>
<li><code>test_batch_cosine_similarity</code> (test_differential_fuzzing.py)
<details><summary>batch_cosine_similarity musí vracet stejné výsledky.</summary>
<div class="doc-comment">
<p>batch_cosine_similarity musí vracet stejné výsledky.</p>
<p></p>
<p>F5.3: Zero-vector inputs ([0.0]) dávají různé výsledky mezi Python a Rust.</p>
<p>Filtrujeme zero-vector query a zero-length vectors.</p>
</div>
</details>
</li>
<li><code>test_stores_spans</code> (test_t1_otel.py)</li>
<li><code>_track</code> (test_sprint8l_live.py)</li>
<li><code>test_loop_close_without_cancel_leaves_tasks</code> (test_coroutine_cleanup.py)
<details><summary>COROUTINE LEAK: loop.close() without cancelling pending tasks.</summary>
<div class="doc-comment">
<p>COROUTINE LEAK: loop.close() without cancelling pending tasks.</p>
<p></p>
<p>This test verifies that proper cleanup patterns work.</p>
<p>In production, always cancel tasks before closing the loop.</p>
</div>
</details>
</li>
<li><code>_wrapped_execute</code> (test_sprint8l_live.py)</li>
<li><code>test_drain_stats_monotonic_counters</code> (test_sprint_f273.py) — <span class="doc-comment-inline">FIX F350M-R: Use session_event_loop fixture instead of asyncio.run().</span></li>
<li><code>test_all_lanes_have_rules</code> (test_f227k_acquisition_lane_parity.py) — <span class="doc-comment-inline">Every AcquisitionLane value has a corresponding LaneRule.</span></li>
<li><code>test_shadow_hook_location_is_not_ao</code> (test_sprint8ax_duckdb_shadow.py)
<details><summary>The shadow hook must NOT be added to autonomous_orchestrator.py.</summary>
<div class="doc-comment">
<p>The shadow hook must NOT be added to autonomous_orchestrator.py.</p>
<p>autonomous_orchestrator.py was merged into core/ in F314.</p>
<p>The hook must live in evidence_log.py or analytics_hook.py.</p>
</div>
</details>
</li>
<li><code>test_single_branch_gives_5_points</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">1 active branch → 5 points.</span></li>
<li><code>test_pvs_academic_field_present</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">product_value_summary includes academic_discovery_contribution when academic is active.</span></li>
<li><code>test_parse_ip_fast</code> (test_differential_fuzzing.py)
<details><summary>parse_ip_fast musí vracet konzistentní výsledky (buď str nebo tuple).</summary>
<div class="doc-comment">
<p>parse_ip_fast musí vracet konzistentní výsledky (buď str nebo tuple).</p>
<p></p>
<p>F5.3: API MISMATCH — Python vrací tuple (int, version), Rust vrací str.</p>
<p>Toto je fundamentální API rozdíl, skipáme bit-identical test.</p>
</div>
</details>
</li>
<li><code>test_aggressive_mode_blocks_until_hermes_prewarm</code> (test_sprint_p12_hypothesis.py)
<details><summary>Aggressive mode: _prewarm_hermes_for_sprint blocks until Hermes is loaded.</summary>
<div class="doc-comment">
<p>Aggressive mode: _prewarm_hermes_for_sprint blocks until Hermes is loaded.</p>
<p>The prewarm call is synchronous from run() — no async fan-out until prewarm completes.</p>
</div>
</details>
</li>
<li><code>test_concurrent_spans_thread_safe</code> (test_t1_otel.py)</li>
<li><code>test_feed_dominance_guard_strict_blocks_early_exit</code> (test_rust_backend.py) — <span class="doc-comment-inline">FeedDominanceGuard strict=True blocks early exit when guard triggered.</span></li>
<li><code>test_min_branch_remaining_s_fallback_cycle_ema_formula</code> (test_sprint_f273.py)
<details><summary>Fallback (no remaining_s arg) uses 0.1 * cycle_ema, clamped [2, 5].</summary>
<div class="doc-comment">
<p>Fallback (no remaining_s arg) uses 0.1 * cycle_ema, clamped [2, 5].</p>
<p>This tests backward compatibility when remaining_s is None.</p>
</div>
</details>
</li>
<li><code>test_drain_completes_pending_futures</code> (test_sprint_f273.py) — <span class="doc-comment-inline">FIX F350M-R: Use session_event_loop fixture instead of asyncio.run().</span></li>
<li><code>test_finalizers_invoked_on_exit</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">weakref finalizers are invoked when MemoryTracker exits.</span></li>
<li><code>test_tracemalloc_snapshot_uses_session_mode</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">TracemallocSnapshot detects session tracer and sets _session_mode=True.</span></li>
<li><code>test_discovery_coroutine_has_timeout</code> (test_coroutine_cleanup.py)
<details><summary>F271B: _ASYNC_DISCOVERY_SEARCH must use asyncio.wait_for(timeout=35.0).</summary>
<div class="doc-comment">
<p>F271B: _ASYNC_DISCOVERY_SEARCH must use asyncio.wait_for(timeout=35.0).</p>
<p></p>
<p>This test verifies the pattern exists in the codebase.</p>
<p>Actual implementation should be:</p>
<p>```python</p>
<p>result = await asyncio.wait_for(</p>
<p>_async_discovery_search(...),</p>
<p>timeout=35.0</p>
<p>)</p>
<p>```</p>
</div>
</details>
</li>
<li><code>test_full_run_returns_all_required_depth_signals_keys</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">depth_signals always has all 8 signal keys.</span></li>
<li><code>test_moderate_level_with_deep_sources</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">Deep sources + no corroboration → moderate.</span></li>
<li><code>test_extract_iocs_returns_valid_types</code> (test_differential_fuzzing.py)
<details><summary>extract_iocs musí vracet konzistentní sadu IOC typů.</summary>
<div class="doc-comment">
<p>extract_iocs musí vracet konzistentní sadu IOC typů.</p>
<p></p>
<p>F5.3: API MISMATCH — Python dict vs Rust list. Skipáme bit-identical test.</p>
<p>Testujeme pouze že obě implementace vrací nějaké výsledky.</p>
</div>
</details>
</li>
<li><code>test_tot_not_in_hot_path</code> (test_sprint_p12_hypothesis.py) — <span class="doc-comment-inline">ToT runs after fetch+storage, not in the hot discovery-to-fetch path.</span></li>
<li><code>test_load_via_model_manager</code> (test_sprint_p12_hypothesis.py)
<details><summary>_load_hermes_for_sprint uses ModelManager.load_model("hermes").</summary>
<div class="doc-comment">
<p>_load_hermes_for_sprint uses ModelManager.load_model("hermes").</p>
<p>Verifies canonical Hermes lifecycle owner is ModelManager.</p>
</div>
</details>
</li>
<li><code>test_teardown_still_releases_hermes_after_prewarm</code> (test_sprint_p12_hypothesis.py)
<details><summary>After successful prewarm+load, teardown still calls _unload_hermes_at_teardown.</summary>
<div class="doc-comment">
<p>After successful prewarm+load, teardown still calls _unload_hermes_at_teardown.</p>
<p>Verifies bounded lifecycle: load at BOOT, release at TEARDOWN.</p>
</div>
</details>
</li>
<li><code>test_max_attrs_truncation</code> (test_t1_otel.py)</li>
<li><code>stop</code> (memory_profiler.py)
<details><summary>Stop tracemalloc — ONLY in legacy (non-session) mode.</summary>
<div class="doc-comment">
<p>Stop tracemalloc — ONLY in legacy (non-session) mode.</p>
<p></p>
<p>In session-scoped tracer mode, this is a no-op: the session tracer</p>
<p>is owned by init_session_tracer() / stop_session_tracer() and must</p>
<p>not be stopped by individual snapshot instances.</p>
<p></p>
<p>Safe to call multiple times (idempotent in both modes).</p>
</div>
</details>
</li>
<li><code>test_sprint_flags_hermes_force_constructible</code> (test_sprint_f273.py) — <span class="doc-comment-inline">SprintFlags(hermes_force=True) must work without breaking other fields.</span></li>
<li><code>test_wait_for_prevents_infinite_hang</code> (test_coroutine_cleanup.py)
<details><summary>FIXED: asyncio.wait_for prevents infinite hangs.</summary>
<div class="doc-comment">
<p>FIXED: asyncio.wait_for prevents infinite hangs.</p>
<p></p>
<p>Correct pattern (F271B reference):</p>
<p>```python</p>
<p>result = await asyncio.wait_for(</p>
<p>some_coroutine(),</p>
<p>timeout=35.0  # Match F271B spec</p>
<p>)</p>
<p>```</p>
</div>
</details>
</li>
<li><code>test_run_correlation_to_dict</code> (test_correlation_propagation.py) — <span class="doc-comment-inline">RunCorrelation.to_dict() returns serializable dict.</span></li>
<li><code>test_all_new_sources_diverse_contributes_high_diversity</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">5 diverse sources including new types → high source_diversity score.</span></li>
<li><code>test_gate_uses_store_and_engine</code> (test_sprint_p12_hypothesis.py) — <span class="doc-comment-inline">P12 gate condition: store is not None AND hermes_engine is not None.</span></li>
<li><code>test_context_not_from_rag_alone</code> (test_sprint_p12_hypothesis.py) — <span class="doc-comment-inline">P12 context uses stored findings count, not rag_context alone.</span></li>
<li><code>test_thread_safety</code> (test_t1_otel.py)</li>
<li><code>test_int_overflow_safe</code> (test_t1_otel.py) — <span class="doc-comment-inline">int64 overflow -&gt; 0 (fail-soft, never crash).</span></li>
<li><code>test_feed_dominance_guard_compute_balanced</code> (test_rust_backend.py) — <span class="doc-comment-inline">FeedDominanceGuard.compute returns balanced result.</span></li>
<li><code>test_feed_dominance_guard_compute_feed_dominant</code> (test_rust_backend.py) — <span class="doc-comment-inline">FeedDominanceGuard.compute detects feed dominance.</span></li>
<li><code>test_min_branch_remaining_s_bounded_2_to_5</code> (test_sprint_f273.py) — <span class="doc-comment-inline">Floor is always in [2.0, 5.0] for any remaining_s or cycle_ema.</span></li>
<li><code>test_deep_level_with_corrob_and_branches</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">Corroborated + branches active → deep level.</span></li>
<li><code>test_comprehensive_level_at_maximum</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">All signals active + 3 branches → comprehensive level.</span></li>
<li><code>test_pvs_zero_when_missing_canonical_run_summary</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">Both fields default to 0 when canonical_run_summary is absent.</span></li>
<li><code>test_fingerprint_stability</code> (test_differential_fuzzing.py)
<details><summary>Fingerprint URL musí být stabilní a konzistentní.</summary>
<div class="doc-comment">
<p>Fingerprint URL musí být stabilní a konzistentní.</p>
<p></p>
<p>F5.3: API MISMATCH — Python vrací str (hex), Rust vrací int.</p>
<p>Testujeme semantic equivalence: obě representace jsou validní fingerprinty.</p>
<p>Skipáme http://0 a podobné edge cases kde hostname parsing diverguje.</p>
</div>
</details>
</li>
<li><code>test_strip_tracking</code> (test_differential_fuzzing.py)
<details><summary>Strip tracking musí odstranit UTM a podobné parametry.</summary>
<div class="doc-comment">
<p>Strip tracking musí odstranit UTM a podobné parametry.</p>
<p></p>
<p>F5.3: Rust _RustUrlDomain nemá strip_tracking() metodu.</p>
<p>Test pouze srovnává Python fallback vs Python fallback (no-op pro Rust path).</p>
</div>
</details>
</li>
<li><code>test_bloom_filter_consistency</code> (test_differential_fuzzing.py) — <span class="doc-comment-inline">BloomFilter add/contains musí být konzistentní.</span></li>
<li><code>test_no_tot_block_before_fetch_batch</code> (test_sprint_p12_hypothesis.py) — <span class="doc-comment-inline">ToT/hypothesis block is NOT placed before the fetch batch.</span></li>
<li><code>test_string_value_bounded</code> (test_t1_otel.py)</li>
<li><code>register_allocation</code> (memory_profiler.py)
<details><summary>Register a large object for weakref-based finalization safety net.</summary>
<div class="doc-comment">
<p>Register a large object for weakref-based finalization safety net.</p>
<p></p>
<p>Issue #12 fix: pytest fixtures can crash before __exit__ cleanup, leaving</p>
<p>large objects pinned in memory. weakref.finalize() guarantees __del__ runs</p>
<p>even if the fixture crashes mid-test.</p>
<p></p>
<p>Args:</p>
<p>obj: Large object to track.</p>
<p>name: Optional name for debugging.</p>
<p></p>
<p>Returns:</p>
<p>weakref.finalize object — call .detach() to cancel tracking.</p>
</div>
</details>
</li>
<li><code>__exit__</code> (memory_profiler.py)</li>
<li><code>test_tracemalloc_snapshot_legacy_mode</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">TracemallocSnapshot falls back to legacy mode when no session tracer.</span></li>
<li><code>test_task_without_reference_is_orphaned</code> (test_coroutine_cleanup.py)
<details><summary>COROUTINE LEAK: create_task without saving reference.</summary>
<div class="doc-comment">
<p>COROUTINE LEAK: create_task without saving reference.</p>
<p></p>
<p>When a task is created but not saved:</p>
<p>- Task runs to completion independently</p>
<p>- Cannot be cancelled if needed</p>
<p>- Reference held only by GC until collection</p>
</div>
</details>
</li>
<li><code>test_init_with_correlation</code> (test_correlation_propagation.py) — <span class="doc-comment-inline">MetricsRegistry.__init__ accepts correlation and stores it.</span></li>
<li><code>test_full_run_returns_all_required_breakdown_keys</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">breakdown always has all 5 component keys.</span></li>
<li><code>test_multiple_diverse_sources_high_diversity</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">3+ diverse sources with even distribution → high diversity score.</span></li>
<li><code>test_nan_inf_safe</code> (test_t1_otel.py)</li>
<li><code>assert_no_leak</code> (memory_profiler.py)
<details><summary>Assert that RSS delta from snapshot is below threshold.</summary>
<div class="doc-comment">
<p>Assert that RSS delta from snapshot is below threshold.</p>
<p></p>
<p>Args:</p>
<p>threshold_mb: Maximum acceptable RSS growth in MB.</p>
<p></p>
<p>Raises:</p>
<p>AssertionError: If delta exceeds threshold.</p>
</div>
</details>
</li>
<li><code>__post_init__</code> (memory_profiler.py)</li>
<li><code>test_apply_nocache_below_threshold_returns_false</code> (test_sprint_f273.py) — <span class="doc-comment-inline">Below NOCACHE_THRESHOLD_BYTES the call is a no-op (False).</span></li>
<li><code>test_concurrent_cleanup_with_gather</code> (test_coroutine_cleanup.py) — <span class="doc-comment-inline">Multiple tasks with proper gather cleanup.</span></li>
<li><code>test_create_event_without_correlation_backward_compat</code> (test_correlation_propagation.py) — <span class="doc-comment-inline">Old call sites without correlation still work.</span></li>
<li><code>test_log_without_correlation_backward_compat</code> (test_correlation_propagation.py) — <span class="doc-comment-inline">Old call sites without correlation still work.</span></li>
<li><code>test_all_deep_gives_max_non_indexed_score</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">Only tier-1+tier-2 sources → non_indexed_ratio score = 20.</span></li>
<li><code>test_mixed_gives_partial_non_indexed_score</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">50% deep sources → partial non_indexed_ratio.</span></li>
<li><code>test_unique_source_types_reflected</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">unique_source_types in depth_signals matches number of source types.</span></li>
<li><code>test_deep_sources_found_accumulates_tier1_tier2</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">deep_sources_found sums hits from tier1 + tier2 sources.</span></li>
<li><code>test_batch_classify</code> (test_differential_fuzzing.py)
<details><summary>batch_classify musí vracet stejné výsledky.</summary>
<div class="doc-comment">
<p>batch_classify musí vracet stejné výsledky.</p>
<p></p>
<p>F5.3: Many edge cases cause divergence. Skip inline.</p>
</div>
</details>
</li>
<li><code>test_no_memory_manager_in_gate</code> (test_sprint_p12_hypothesis.py) — <span class="doc-comment-inline">P12 gate does NOT use memory_manager (that was the pre-storage gate).</span></li>
<li><code>test_failsoft_exception_handling</code> (test_sprint_p12_hypothesis.py) — <span class="doc-comment-inline">Exception in P12 block does not propagate — fail-soft.</span></li>
<li><code>test_unload_via_model_manager</code> (test_sprint_p12_hypothesis.py)
<details><summary>_unload_hermes_at_teardown uses ModelManager.release_model("hermes").</summary>
<div class="doc-comment">
<p>_unload_hermes_at_teardown uses ModelManager.release_model("hermes").</p>
<p>Verifies canonical Hermes unload authority is ModelManager.</p>
</div>
</details>
</li>
<li><code>test_parallel_hypothesis_burst_keeps_max_five_cap</code> (test_sprint_p12_hypothesis.py) — <span class="doc-comment-inline">Up to 5 hypotheses are evaluated concurrently — cap of 5 is preserved.</span></li>
<li><code>test_tot_burst_uses_per_hypothesis_timeout</code> (test_sprint_p12_hypothesis.py) — <span class="doc-comment-inline">Each ToT task has its own 15s timeout budget — no single task blocks the burst.</span></li>
<li><code>test_first_three_completed_results_enqueue_pivots_immediately</code> (test_sprint_p12_hypothesis.py) — <span class="doc-comment-inline">as_completed iterates in arrival order — first completed ToT results feed enqueue immediately.</span></li>
<li><code>test_failed_tot_tasks_do_not_block_other_hypotheses</code> (test_sprint_p12_hypothesis.py)
<details><summary>Fail-soft: one failed ToT task does not fail the others — asyncio.as_completed handles results independently."""  # noqa: E501</summary>
<div class="doc-comment">
<p>Fail-soft: one failed ToT task does not fail the others — asyncio.as_completed handles results independently."""  # noqa: E501</p>
<p>from hledac.universal.pipeline.live_public_pipeline import async_run_live_public_pipeline</p>
<p>source = inspect.getsource(async_run_live_public_pipeline)</p>
<p></p>
<p>p12_start = source.find("# P12: Hypothesis generation")</p>
<p>p12_block = source[p12_start:p12_start + 5000]</p>
<p></p>
<p># except asyncio.TimeoutError with return "" — fail-soft per task</p>
<p>assert "asyncio.TimeoutError" in p12_block and 'return ""' in p12_block, (</p>
<p>"P12 must catch TimeoutError per-task and return empty string — fail-soft"</p>
<p>)</p>
<p># except Exception with return "" — broad fail-soft</p>
<p>assert "except Exception as e:" in p12_block and 'return ""' in p12_block, (</p>
<p>"P12 must catch all exceptions per-task and return empty string — fail-soft"</p>
<p>)</p>
<p></p>
<p></p>
<p>class TestP12HermesPrewarmPolicy:</p>
</div>
</details>
</li>
<li><code>test_async_decorator_preserves_signature</code> (test_t1_otel.py) — <span class="doc-comment-inline">FIX F350M-R: Use session_event_loop fixture instead of asyncio.run().</span></li>
<li><code>test_url_set_add_batch_parallel</code> (test_rust_backend.py) — <span class="doc-comment-inline">UrlSet add_batch uses rayon parallel FNV-1a hashing.</span></li>
<li><code>test_ioc_dedup_store_add_batch_parallel</code> (test_rust_backend.py) — <span class="doc-comment-inline">IocDedupStore add_batch uses rayon parallel hashing.</span></li>
<li><code>format_top_deltas</code> (memory_profiler.py)
<details><summary>Format top N allocation deltas as a readable string.</summary>
<div class="doc-comment">
<p>Format top N allocation deltas as a readable string.</p>
<p></p>
<p>Returns:</p>
<p>Multi-line string suitable for assertion messages.</p>
</div>
</details>
</li>
<li><code>test_apply_nocache_to_path_returns_bool</code> (test_sprint_f273.py)</li>
<li><code>test_f278a_replaces_f273b_contract</code> (test_sprint_f273.py) — <span class="doc-comment-inline">P0-1: 0.30 ratio with [30, 180] ceiling -- F288 cap removed.</span></li>
<li><code>test_bounded_gather_prevents_task_accumulation</code> (test_coroutine_cleanup.py)
<details><summary>F320: parallel() limits concurrent tasks.</summary>
<div class="doc-comment">
<p>F320: parallel() limits concurrent tasks.</p>
<p></p>
<p>parallel() with semaphore caps concurrent tasks,</p>
<p>preventing resource exhaustion.</p>
</div>
</details>
</li>
<li><code>test_v2_has_aclose_method</code> (test_sprint_scheduler.py)
<details><summary>V2: aclose() method exists and is callable.</summary>
<div class="doc-comment">
<p>V2: aclose() method exists and is callable.</p>
<p></p>
<p>F285 graceful shutdown protocol — aclose() must exist on the</p>
<p>SprintSchedulerV2 instance for backward compatibility.</p>
</div>
</details>
</li>
<li><code>test_normalize_idempotent</code> (test_differential_fuzzing.py) — <span class="doc-comment-inline">Normalizace URL musí být konzistentní — Rust vs Python.</span></li>
<li><code>test_is_valid_url</code> (test_differential_fuzzing.py)
<details><summary>is_valid_url musí být konzistentní.</summary>
<div class="doc-comment">
<p>is_valid_url musí být konzistentní.</p>
<p></p>
<p>F5.3: Many edge cases (numeric hostnames, control chars, non-ASCII, etc.)</p>
<p>cause Python vs Rust divergence. Skip any mismatches inline.</p>
</div>
</details>
</li>
<li><code>test_hypothesis_layer_after_aggregate_section</code> (test_sprint_p12_hypothesis.py) — <span class="doc-comment-inline">Hypothesis layer appears after the aggregate/compute block.</span></li>
<li><code>test_scheduler_releases_hermes_at_teardown</code> (test_sprint_p12_hypothesis.py)
<details><summary>SprintScheduler releases Hermes at teardown (in _close_dedup region).</summary>
<div class="doc-comment">
<p>SprintScheduler releases Hermes at teardown (in _close_dedup region).</p>
<p>Verifies bounded M1 8GB lifecycle: load at BOOT, release at TEARDOWN.</p>
</div>
</details>
</li>
<li><code>test_gate_open_with_store_and_hermes</code> (test_sprint_p12_hypothesis.py)
<details><summary>P12 gate opens when store is not None AND hermes_engine is not None AND total_stored &gt; 0.</summary>
<div class="doc-comment">
<p>P12 gate opens when store is not None AND hermes_engine is not None AND total_stored &gt; 0.</p>
<p>Verifies canonical DI wire: store+hermes+stored findings = gate open.</p>
</div>
</details>
</li>
<li><code>test_all_domains_accessible</code> (test_rust_backend.py) — <span class="doc-comment-inline">All 18 domain properties are accessible.</span></li>
<li><code>delta_mb</code> (memory_profiler.py)
<details><summary>Return RSS delta from snapshot to now.</summary>
<div class="doc-comment">
<p>Return RSS delta from snapshot to now.</p>
<p></p>
<p>Args:</p>
<p>force_gc: If True (default), run gc.collect() before measuring</p>
<p>to exclude unreachable Python objects from the delta.</p>
<p>Pass False for raw measured delta.</p>
<p></p>
<p>Returns:</p>
<p>RSS delta in MB (positive = growth, negative = freed).</p>
</div>
</details>
</li>
<li><code>take</code> (memory_profiler.py) — <span class="doc-comment-inline">Take a lightweight snapshot of Python object allocations.</span></li>
<li><code>test_constants</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test BEP-9 constants are bounded.</span></li>
<li><code>test_torrent_info_dataclass</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test TorrentInfo dataclass.</span></li>
<li><code>test_f288_aggressive_mode_windup</code> (test_sprint_f273.py) — <span class="doc-comment-inline">P0-1: aggressive mode uses 0.15 ratio, [30, 180] ceiling (F288 cap removed).</span></li>
<li><code>test_classify_url</code> (test_differential_fuzzing.py)
<details><summary>classify_url musí vracet stejný (kind, host) pár.</summary>
<div class="doc-comment">
<p>classify_url musí vracet stejný (kind, host) pár.</p>
<p></p>
<p>F5.3: Many edge cases cause divergence. Skip inline.</p>
</div>
</details>
</li>
<li><code>test_extract_domain</code> (test_differential_fuzzing.py)
<details><summary>extract_domain musí vracet stejný doménový host.</summary>
<div class="doc-comment">
<p>extract_domain musí vracet stejný doménový host.</p>
<p></p>
<p>F5.3: Many edge cases cause divergence. Skip inline.</p>
</div>
</details>
</li>
<li><code>test_batch_dedup_fingerprints</code> (test_differential_fuzzing.py)
<details><summary>batch_dedup_fingerprints musí vracet hex stringy.</summary>
<div class="doc-comment">
<p>batch_dedup_fingerprints musí vracet hex stringy.</p>
<p></p>
<p>F5.3: Short inputs produce variable-length hex. Skip inline on mismatch.</p>
</div>
</details>
</li>
<li><code>test_compute_simhash</code> (test_differential_fuzzing.py)
<details><summary>compute_simhash musí vracet stejné integer hodnoty.</summary>
<div class="doc-comment">
<p>compute_simhash musí vracet stejné integer hodnoty.</p>
<p></p>
<p>F5.3: Short digit strings cause Rust=0 vs Python=correct. Skip inline.</p>
</div>
</details>
</li>
<li><code>test_is_private_ip</code> (test_differential_fuzzing.py)
<details><summary>is_private_ip musí vracet konzistentní výsledky.</summary>
<div class="doc-comment">
<p>is_private_ip musí vracet konzistentní výsledky.</p>
<p></p>
<p>F5.3: 250+.x.x.x Rust=false positive. Skip inline.</p>
</div>
</details>
</li>
<li><code>test_is_public_ip</code> (test_differential_fuzzing.py)
<details><summary>is_public_ip musí vracet konzistentní výsledky.</summary>
<div class="doc-comment">
<p>is_public_ip musí vracet konzistentní výsledky.</p>
<p></p>
<p>F5.3: 250+.x.x.x Rust=false positive. Skip inline.</p>
</div>
</details>
</li>
<li><code>test_gate_conditional_on_total_stored</code> (test_sprint_p12_hypothesis.py) — <span class="doc-comment-inline">P12 runs only when total_stored &gt; 0 (real findings exist).</span></li>
<li><code>test_queries_store_for_findings</code> (test_sprint_p12_hypothesis.py) — <span class="doc-comment-inline">P12 calls store.async_get_recent_findings() to get real persisted findings.</span></li>
<li><code>test_scheduler_passes_hermes_to_pipeline</code> (test_sprint_p12_hypothesis.py)
<details><summary>SprintScheduler passes hermes_engine into async_run_live_public_pipeline.</summary>
<div class="doc-comment">
<p>SprintScheduler passes hermes_engine into async_run_live_public_pipeline.</p>
<p>Verifies DI wire: scheduler._hermes_engine → pipeline P12 gate.</p>
</div>
</details>
</li>
<li><code>test_scheduler_loads_hermes_at_sprint_start</code> (test_sprint_p12_hypothesis.py)
<details><summary>SprintScheduler prewarms Hermes at sprint start (_prewarm_hermes_for_sprint).</summary>
<div class="doc-comment">
<p>SprintScheduler prewarms Hermes at sprint start (_prewarm_hermes_for_sprint).</p>
<p>Verifies bounded M1 8GB lifecycle: load at BOOT, release at TEARDOWN.</p>
</div>
</details>
</li>
<li><code>test_parse_ip_fast</code> (test_rust_backend.py) — <span class="doc-comment-inline">parse_ip_fast returns normalized IP string or None (Rust) / tuple (Python fallback).</span></li>
<li><code>test_feed_dominance_guard_zero_findings</code> (test_rust_backend.py) — <span class="doc-comment-inline">FeedDominanceGuard.compute handles zero findings.</span></li>
<li><code>test_compute_dominance_convenience</code> (test_rust_backend.py) — <span class="doc-comment-inline">compute_dominance convenience method works.</span></li>
<li><code>has_leak</code> (memory_profiler.py)
<details><summary>Check if any allocation grew by more than threshold_kb.</summary>
<div class="doc-comment">
<p>Check if any allocation grew by more than threshold_kb.</p>
<p></p>
<p>Args:</p>
<p>threshold_kb: Threshold in KB per allocation site.</p>
<p></p>
<p>Returns:</p>
<p>True if any single allocation site grew beyond threshold_kb.</p>
</div>
</details>
</li>
<li><code>test_extract_gemini_links</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test gemtext link extraction.</span></li>
<li><code>test_run_correlation_exists</code> (test_correlation_propagation.py) — <span class="doc-comment-inline">RunCorrelation dataclass exists in types.py.</span></li>
<li><code>test_surface_run_returns_all_required_keys</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">surface/smoke run still returns complete structure (no KeyError).</span></li>
<li><code>test_three_branches_active_gives_15_points</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">3 active branches → 15 points (cap).</span></li>
<li><code>test_normalize_quality_text</code> (test_differential_fuzzing.py)
<details><summary>normalize_quality_text musí vracet bit-identický výstup.</summary>
<div class="doc-comment">
<p>normalize_quality_text musí vracet bit-identický výstup.</p>
<p></p>
<p>F5.3: Rust normalize_quality_text() přijímá pouze str, ne bytes.</p>
<p>QUALITY_TEXT strategie nyní produkuje pouze text (ne binary) — TypeError fixed.</p>
</div>
</details>
</li>
<li><code>test_cosine_similarity</code> (test_differential_fuzzing.py) — <span class="doc-comment-inline">cosine_similarity musí vracet bit-identické výsledky.</span></li>
<li><code>test_max_five_hypotheses</code> (test_sprint_p12_hypothesis.py) — <span class="doc-comment-inline">ToT evaluation bounded to 5 hypotheses: hypotheses[:5].</span></li>
<li><code>test_gate_blocks_when_hermes_none</code> (test_sprint_p12_hypothesis.py) — <span class="doc-comment-inline">P12 gate does NOT open when hermes_engine is None (even with store and findings).</span></li>
<li><code>test_teardown_calls_unload_method</code> (test_sprint_p12_hypothesis.py)
<details><summary>Teardown path calls _unload_hermes_at_teardown.</summary>
<div class="doc-comment">
<p>Teardown path calls _unload_hermes_at_teardown.</p>
<p>Verifies bounded M1 8GB lifecycle: load at BOOT, release at TEARDOWN.</p>
</div>
</details>
</li>
<li><code>test_imports</code> (test_t1_otel.py)</li>
<li><code>test_sync_decorator</code> (test_t1_otel.py)</li>
<li><code>test_async_decorator</code> (test_t1_otel.py)</li>
<li><code>test_burst_does_not_exceed_ring_capacity</code> (test_t1_otel.py) — <span class="doc-comment-inline">M1 8GB bound: ring stays &lt;= capacity even under burst.</span></li>
<li><code>test_rolling_hash_engine</code> (test_rust_backend.py) — <span class="doc-comment-inline">RollingHashEngine hash and roll work.</span></li>
<li><code>test_cosine_similarity</code> (test_rust_backend.py) — <span class="doc-comment-inline">cosine_similarity returns float.</span></li>
<li><code>take</code> (memory_profiler.py) — <span class="doc-comment-inline">Take a baseline snapshot (call before code under test).</span></li>
<li><code>add</code> (test_sprint8l_live.py)</li>
<li><code>test_fediverse_constants</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test Fediverse constants are bounded.</span></li>
<li><code>test_matrix_constants</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test Matrix constants are bounded.</span></li>
<li><code>test_maybe_call_pressure_relief_increments_counter</code> (test_sprint_f273.py)</li>
<li><code>test_assert_no_leak_fails_over_threshold</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">assert_no_leak() raises AssertionError when delta exceeds threshold.</span></li>
<li><code>test_tracker_custom_threshold</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">Custom threshold is respected by assert_leak_threshold.</span></li>
<li><code>test_tracker_assertion_message_contains_details</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">AssertionError message includes delta, threshold, and RSS values.</span></li>
<li><code>test_has_leak_true_when_growing</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">has_leak() returns True when any site grows beyond threshold.</span></li>
<li><code>test_register_allocation_returns_finalizer</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">register_allocation() returns a weakref.finalize object.</span></li>
<li><code>test_init_session_tracer_idempotent</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">init_session_tracer() is safe to call multiple times.</span></li>
<li><code>test_analytics_hook_signature_extended</code> (test_correlation_propagation.py) — <span class="doc-comment-inline">shadow_record_finding accepts branch_id, provider_id, action_id.</span></li>
<li><code>test_windup_efficiency_computed_correctly</code> (test_sprint_scheduler.py) — <span class="doc-comment-inline">windup_efficiency = windup / (windup + active).</span></li>
<li><code>test_ct_log_contributes_to_non_indexed_ratio</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">ct_log hits contribute to non_indexed_ratio component (tier 1).</span></li>
<li><code>test_onion_discovery_contributes_as_deep</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">onion_discovery hits count as tier 2 (deep) for non_indexed_ratio.</span></li>
<li><code>test_no_tot_on_zero_findings</code> (test_sprint_p12_hypothesis.py) — <span class="doc-comment-inline">When total_stored == 0, P12 does not run ToT.</span></li>
<li><code>test_tot_solution_count_tracked</code> (test_sprint_p12_hypothesis.py) — <span class="doc-comment-inline">P12 tracks tot_solution_count for telemetry.</span></li>
<li><code>test_no_tot_when_store_none</code> (test_sprint_p12_hypothesis.py) — <span class="doc-comment-inline">ToT does not run when store is None (hermes_engine is irrelevant without store).</span></li>
<li><code>test_no_tot_when_no_stored_findings</code> (test_sprint_p12_hypothesis.py) — <span class="doc-comment-inline">ToT does not run when total_stored == 0 (no evidence to reason about).</span></li>
<li><code>test_batch_entropy_basic</code> (test_rust_backend.py) — <span class="doc-comment-inline">batch_entropy returns correct Shannon entropy values.</span></li>
<li><code>test_int_counter_layout</code> (test_rust_backend.py) — <span class="doc-comment-inline">IntCounterLayout get/set/bump work.</span></li>
<li><code>test_gopher_item_dataclass</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test GopherItem structure.</span></li>
<li><code>test_alt_protocol_result_namedtuple</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test AltProtocolResult structure.</span></li>
<li><code>test_branch_timeout_returns_zero_only_below_dynamic_floor</code> (test_sprint_f273.py) — <span class="doc-comment-inline">_branch_timeout_s returns 0 only when remaining_s &lt;= dynamic floor.</span></li>
<li><code>test_stop_noop_in_session_mode</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">stop() does NOT stop the session tracer.</span></li>
<li><code>test_init_without_correlation_backward_compat</code> (test_correlation_propagation.py) — <span class="doc-comment-inline">Old call sites without correlation still work.</span></li>
<li><code>test_effective_windup_60s_15pct_no_floor</code> (test_sprint_scheduler.py)
<details><summary>Sprint 60s: 30% ratio = 18s, floored to 30s. Active = 30s.</summary>
<div class="doc-comment">
<p>Sprint 60s: 30% ratio = 18s, floored to 30s. Active = 30s.</p>
<p></p>
<p>F290: sprint&lt;=120 → ratio=0.20, raw=60*0.20=12s → floor max(15,12)=15.</p>
<p>F288: floor [15, 180] always applies (15s floor).</p>
</div>
</details>
</li>
<li><code>test_aclose_does_not_raise_on_clean_scheduler</code> (test_sprint_scheduler.py) — <span class="doc-comment-inline">aclean() must not raise even when all resources are None/empty.</span></li>
<li><code>test_aclose_is_idempotent</code> (test_sprint_scheduler.py) — <span class="doc-comment-inline">Calling aclose() twice must not raise.</span></li>
<li><code>test_all_signals_max_score_is_100</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">All signals active yields score &lt;= 100.</span></li>
<li><code>test_shallow_level_single_indexed_source</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">Single indexed source + no corroboration → shallow.</span></li>
<li><code>test_single_indexed_source_low_diversity</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">One indexed source → low diversity score.</span></li>
<li><code>test_campaign_hints_3_plus_gives_5_bonus</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">3+ campaign hints → +5 corroboration bonus (capped at 25).</span></li>
<li><code>test_pivot_depth_capped_at_15</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">Both hypothesis + pivot recommended → capped at 15.</span></li>
<li><code>test_source_tier_tier1_contributes_to_non_indexed_ratio</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">Tier-1 academic_discovery hits contribute to non_indexed_ratio component.</span></li>
<li><code>test_ipfs_contributes_to_non_indexed_ratio</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">ipfs hits contribute to non_indexed_ratio component (tier 1).</span></li>
<li><code>test_shodan_search_contributes_to_non_indexed_ratio</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">shodan_search hits contribute to non_indexed_ratio component (tier 1).</span></li>
<li><code>test_bgp_monitor_contributes_to_non_indexed_ratio</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">bgp_monitor hits contribute to non_indexed_ratio component (tier 1).</span></li>
<li><code>test_batch_entropy</code> (test_differential_fuzzing.py) — <span class="doc-comment-inline">batch_entropy musí vracet bit-identické výsledky.</span></li>
<li><code>test_batch_compute_simhash</code> (test_differential_fuzzing.py) — <span class="doc-comment-inline">batch_compute_simhash musí vracet stejnou délku a hodnoty.</span></li>
<li><code>test_hypothesis_engine_initialized</code> (test_sprint_p12_hypothesis.py) — <span class="doc-comment-inline">HypothesisEngine is instantiated inside P12 block.</span></li>
<li><code>test_tot_integration_layer_initialized</code> (test_sprint_p12_hypothesis.py) — <span class="doc-comment-inline">TotIntegrationLayer is instantiated inside P12 block.</span></li>
<li><code>test_default_name_uses_qualname</code> (test_t1_otel.py)</li>
<li><code>test_concurrent_spans_async_safe</code> (test_t1_otel.py)</li>
<li><code>test_compute_entropy_single</code> (test_rust_backend.py) — <span class="doc-comment-inline">compute_entropy returns correct value.</span></li>
<li><code>test_hot_edge_counter</code> (test_rust_backend.py) — <span class="doc-comment-inline">HotEdgeCounter bump_edge and drain work.</span></li>
<li><code>test_chain_hash</code> (test_rust_backend.py) — <span class="doc-comment-inline">chain_hash returns tuple of strings.</span></li>
<li><code>test_lane_budget_pool_allocate_consume</code> (test_rust_backend.py) — <span class="doc-comment-inline">LaneBudgetPool allocate and consume work.</span></li>
<li><code>test_lane_budget_pool_release</code> (test_rust_backend.py) — <span class="doc-comment-inline">LaneBudgetPool release works.</span></li>
<li><code>test_lane_budget_pool_get_utilization</code> (test_rust_backend.py) — <span class="doc-comment-inline">LaneBudgetPool get_utilization returns float.</span></li>
<li><code>test_lane_budget_pool_timeout</code> (test_rust_backend.py) — <span class="doc-comment-inline">LaneBudgetPool release increments timeout_count.</span></li>
<li><code>delta_bytes</code> (memory_profiler.py) — <span class="doc-comment-inline">Return delta in bytes from baseline to now.</span></li>
<li><code>peak_delta_mb</code> (memory_profiler.py) — <span class="doc-comment-inline">Return peak memory growth in MB from baseline.</span></li>
<li><code>to_dict</code> (test_sprint8l_live.py)</li>
<li><code>compute_slope</code> (test_sprint8l_live.py)</li>
<li><code>test_gopher_finding_dataclass</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test GopherFinding structure.</span></li>
<li><code>test_gemini_response_namedtuple</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test GeminiResponse structure.</span></li>
<li><code>test_gemini_finding_namedtuple</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test GeminiFinding structure.</span></li>
<li><code>test_schedule_html_extraction_returns_future</code> (test_sprint_f273.py)</li>
<li><code>test_drain_helpers_importable</code> (test_sprint_f273.py) — <span class="doc-comment-inline">drain_pending_extractions + get_drain_stats are importable.</span></li>
<li><code>test_compare_top_n_returns_list</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">compare_top_n() returns a list of stat pairs.</span></li>
<li><code>test_take_returns_started_stats</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">TracemallocStats.take() returns a started instance with 2 numbers.</span></li>
<li><code>test_weakref_finalizers_cleared_on_exit</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">_weakref_finalizers list is cleared after __exit__.</span></li>
<li><code>test_init_session_tracer_starts_tracing</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">init_session_tracer() starts tracemalloc and returns True.</span></li>
<li><code>test_memory_tracker_fixture_bookend</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">memory_tracker fixture provides context manager that captures RSS delta.</span></li>
<li><code>test_run_correlation_partial</code> (test_correlation_propagation.py) — <span class="doc-comment-inline">RunCorrelation supports partial fields.</span></li>
<li><code>test_run_correlation_with_provider</code> (test_correlation_propagation.py) — <span class="doc-comment-inline">RunCorrelation.with_provider() returns new instance.</span></li>
<li><code>test_surface_smoke_run_score_not_negative</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">Zero sources yields score &gt;= 0.</span></li>
<li><code>test_score_is_float</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">Score type is always float (not int, not None).</span></li>
<li><code>test_surface_level_at_minimum</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">Empty inputs → surface level.</span></li>
<li><code>test_all_indexed_gives_zero_non_indexed_score</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">Only tier-0 sources → non_indexed_ratio = 0.</span></li>
<li><code>test_no_signals_gives_zero_corrob</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">No corroboration signals → corroboration = 0.</span></li>
<li><code>test_is_corroborated_true_gives_15</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">is_corroborated=True → 15 points.</span></li>
<li><code>test_is_noisy_false_gives_5</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">is_noisy=False → 5 points (distinct from is_corroborated).</span></li>
<li><code>test_corrob_capped_at_25</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">Corroboration score cannot exceed 25.</span></li>
<li><code>test_no_runtime_truth_zero_branch_score</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">No runtime_truth → branch_diversity = 0.</span></li>
<li><code>test_no_signals_gives_zero_pivot</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">No hypothesis_pack or pivot signal → pivot_depth = 0.</span></li>
<li><code>test_hypothesis_count_gives_5</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">hypothesis_count &gt; 0 → 5 points.</span></li>
<li><code>test_pivot_recommended_gives_10</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">next_pivot_recommendation != continue → 10 points.</span></li>
<li><code>test_continue_pivot_not_counted</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">next_pivot_recommendation='continue' → 0 pivot points.</span></li>
<li><code>test_campaign_hints_count_from_correlation</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">campaign_hints count matches correlation input.</span></li>
<li><code>test_html_extract</code> (test_differential_fuzzing.py) — <span class="doc-comment-inline">html_extract musí vracet konzistentní strukturu.</span></li>
<li><code>test_batch_nfc_normalize_nfc_composition</code> (test_hledac_core_rust.py)</li>
<li><code>test_stats</code> (test_t1_otel.py)</li>
<li><code>test_nested_spans</code> (test_t1_otel.py)</li>
<li><code>test_batch_dedup_fingerprints</code> (test_rust_backend.py) — <span class="doc-comment-inline">batch_dedup_fingerprints returns list of hex strings.</span></li>
<li><code>test_ioc_dedup_store</code> (test_rust_backend.py) — <span class="doc-comment-inline">IocDedupStore add/contains work.</span></li>
<li><code>test_ioc_dedup_store_batch_insert_alias</code> (test_rust_backend.py) — <span class="doc-comment-inline">batch_insert is an alias for add_batch.</span></li>
<li><code>test_html_extract</code> (test_rust_backend.py) — <span class="doc-comment-inline">html_extract returns dict with links, emails, title.</span></li>
<li><code>__enter__</code> (memory_profiler.py)</li>
<li><code>test_gate_disabled_by_default</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test alt protocols disabled by default.</span></li>
<li><code>test_gate_enabled_with_env</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test alt protocols enabled with env var.</span></li>
<li><code>test_get_alt_protocols_status</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test status reporting.</span></li>
<li><code>test_bencode_encoder</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test bencode encoding.</span></li>
<li><code>test_min_branch_remaining_s_floor_when_no_cycles_seen</code> (test_sprint_f273.py) — <span class="doc-comment-inline">When _cycle_time_ema is 0 (pre-loop), returns the default 2.0s floor.</span></li>
<li><code>test_write_section_uses_aiofiles_when_available</code> (test_sprint_f273.py) — <span class="doc-comment-inline">If aiofiles is available, _write_section uses async with aiofiles.open.</span></li>
<li><code>test_tracker_enters_and_exits_cleanly</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">MemoryTracker __enter__ / __exit__ cycle completes without error.</span></li>
<li><code>test_tracker_measures_delta</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">MemoryTracker captures RSS delta between enter and assert.</span></li>
<li><code>test_peak_delta_mb_returns_float</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">peak_delta_mb() returns peak memory growth in MB.</span></li>
<li><code>test_sprint_lifecycle_no_leak</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">SprintLifecycleManager import + instantiation does not leak memory.</span></li>
<li><code>test_memory_tracker_fixture_reports_leak</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">memory_tracker fixture raises AssertionError with leak details.</span></li>
<li><code>test_pipeline_timeout_fires</code> (test_coroutine_cleanup.py) — <span class="doc-comment-inline">Verify timeout is respected.</span></li>
<li><code>test_run_correlation_with_action</code> (test_correlation_propagation.py) — <span class="doc-comment-inline">RunCorrelation.with_action() returns new instance.</span></li>
<li><code>find_spec</code> (conftest.py)</li>
<li><code>test_compute_entropy</code> (test_differential_fuzzing.py) — <span class="doc-comment-inline">compute_entropy single musí být konzistentní.</span></li>
<li><code>test_nfc_normalize</code> (test_differential_fuzzing.py) — <span class="doc-comment-inline">NFC normalizace musí vracet bit-identické výsledky.</span></li>
<li><code>test_strip_diacritics</code> (test_differential_fuzzing.py) — <span class="doc-comment-inline">strip_diacritics musí vracet bit-identické výsledky.</span></li>
<li><code>test_batch_nfc_normalize</code> (test_differential_fuzzing.py) — <span class="doc-comment-inline">batch_nfc_normalize musí vracet stejné výsledky.</span></li>
<li><code>test_cidr_contains</code> (test_differential_fuzzing.py) — <span class="doc-comment-inline">cidr_contains musí vracet konzistentní výsledky.</span></li>
<li><code>test_nfc_normalize</code> (test_differential_fuzzing.py) — <span class="doc-comment-inline">NFC normalizace IOC textů musí být konzistentní.</span></li>
<li><code>test_content_hash_64</code> (test_differential_fuzzing.py) — <span class="doc-comment-inline">content_hash_64 musí vracet stejné integer hodnoty.</span></li>
<li><code>test_content_hash_hex</code> (test_differential_fuzzing.py) — <span class="doc-comment-inline">content_hash_hex musí vracet stejné hex stringy.</span></li>
<li><code>test_batch_dedup_removes_duplicates</code> (test_hledac_core_rust.py)</li>
<li><code>test_python_fallback_content_hash</code> (test_hledac_core_rust.py) — <span class="doc-comment-inline">Python fallback uses hashlib.sha256 (not xxhash, just verifies import works).</span></li>
<li><code>test_find_near_duplicates_no_pairs</code> (test_hledac_core_rust.py)</li>
<li><code>test_lru_eviction</code> (test_t1_otel.py)</li>
<li><code>test_basic_export</code> (test_t1_otel.py)</li>
<li><code>test_attributes_recorded</code> (test_t1_otel.py)</li>
<li><code>test_trace_id_nonzero_in_span</code> (test_t1_otel.py)</li>
<li><code>test_otel_disabled_yields_noop</code> (test_t1_otel.py) — <span class="doc-comment-inline">When exporter_kind='none', no actual SDK is used; ring stays empty.</span></li>
<li><code>test_filter_valid_urls</code> (test_rust_backend.py) — <span class="doc-comment-inline">filter_valid_urls filters a list.</span></li>
<li><code>test_bloom_filter_add_contains</code> (test_rust_backend.py) — <span class="doc-comment-inline">BloomFilter add/contains work.</span></li>
<li><code>test_url_set</code> (test_rust_backend.py) — <span class="doc-comment-inline">UrlSet add/contains work.</span></li>
<li><code>test_batch_graph_traverse_returns_list</code> (test_rust_backend.py) — <span class="doc-comment-inline">batch_graph_traverse returns list of dicts (or None on invalid path).</span></li>
<li><code>test_feed_dominance_guard_ratio_class</code> (test_rust_backend.py) — <span class="doc-comment-inline">FeedDominanceGuard.ratio_class returns correct class.</span></li>
<li><code>test_bencode_decoder</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test bencode decoding.</span></li>
<li><code>test_size_formatter</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test human-readable size formatting.</span></li>
<li><code>test_clear_cache</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test cache clearing.</span></li>
<li><code>test_alt_protocols_status_includes_social</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test get_alt_protocols_status includes social protocols.</span></li>
<li><code>test_windup_for_cycle_no_bonus_when_quick</code> (test_sprint_f273.py) — <span class="doc-comment-inline">Cycle EMA &lt;= 8s gives no adaptive bonus.</span></li>
<li><code>test_windup_for_cycle_adaptive_bonus</code> (test_sprint_f273.py) — <span class="doc-comment-inline">Slow cycles get +0.5s per s over 8s, capped at +30s.</span></li>
<li><code>test_sprint_scheduler_accepts_flags_param</code> (test_sprint_f273.py) — <span class="doc-comment-inline">SprintScheduler.__init__ signature must include flags kwarg.</span></li>
<li><code>test_sprint_scheduler_result_has_hermes_diagnostic_fields</code> (test_sprint_f273.py) — <span class="doc-comment-inline">SprintSchedulerResult must have hermes_model_loaded etc. with sane defaults.</span></li>
<li><code>test_format_top_deltas_returns_string</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">format_top_deltas() returns a formatted string.</span></li>
<li><code>test_has_leak_false_when_clean</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">has_leak() returns False when no significant allocation growth.</span></li>
<li><code>test_delta_bytes_captures_allocation</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">delta_bytes() captures Python object allocation growth.</span></li>
<li><code>test_memory_snapshot_fixture_delta</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">memory_snapshot captures RSS on enter, provides delta on exit.</span></li>
<li><code>test_discovery_timeout_fires</code> (test_coroutine_cleanup.py) — <span class="doc-comment-inline">F271B: Verify 35 second timeout fires on slow operation.</span></li>
<li><code>test_simhash_near_duplicate_detection</code> (test_hledac_core_rust.py)</li>
<li><code>test_compute_simhash_fingerprint_format</code> (test_hledac_core_rust.py)</li>
<li><code>test_find_near_duplicates_in_batch_no_pairs</code> (test_hledac_core_rust.py)</li>
<li><code>test_noop_tracer_yields_noop_span</code> (test_t1_otel.py)</li>
<li><code>test_decorator_with_uninitialized_telemetry</code> (test_t1_otel.py)</li>
<li><code>test_decorator_preserves_metadata</code> (test_t1_otel.py)</li>
<li><code>test_unsupported_value_coerced_to_string</code> (test_t1_otel.py)</li>
<li><code>test_bloom_filter_len</code> (test_rust_backend.py) — <span class="doc-comment-inline">BloomFilter __len__ works.</span></li>
<li><code>test_batch_content_hash</code> (test_rust_backend.py) — <span class="doc-comment-inline">batch_content_hash returns list of ints.</span></li>
<li><code>test_batch_compute_simhash</code> (test_rust_backend.py) — <span class="doc-comment-inline">batch_compute_simhash returns list of ints.</span></li>
<li><code>test_aho_matcher</code> (test_rust_backend.py) — <span class="doc-comment-inline">AhoCorasickMatcher.scan returns list of matches.</span></li>
<li><code>__init__</code> (test_sprint8l_live.py)</li>
<li><code>__init__</code> (test_sprint8l_live.py)</li>
<li><code>_monitor</code> (test_sprint8l_live.py)</li>
<li><code>__init__</code> (test_sprint8l_live.py)</li>
<li><code>test_gopher_item_is_file</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test GopherItem is_file property.</span></li>
<li><code>test_fediverse_is_enabled</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test Fediverse is_enabled gate.</span></li>
<li><code>test_matrix_is_enabled</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test Matrix is_enabled gate.</span></li>
<li><code>test_async_fetch_dht_metadata_disabled</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test async_fetch_dht_metadata when DHT is disabled.</span></li>
<li><code>setUp</code> (test_sprint_f273.py)</li>
<li><code>test_drain_zero_deadline_returns_immediately</code> (test_sprint_f273.py) — <span class="doc-comment-inline">FIX F350M-R: Use session_event_loop fixture instead of asyncio.run().</span></li>
<li><code>test_hermes_diagnostic_fields</code> (test_sprint_f273.py)</li>
<li><code>test_snapshot_takes_rss_on_init</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">Snapshot captures RSS at construction time.</span></li>
<li><code>test_delta_mb_with_allocation</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">delta_mb() captures deliberate allocation growth.</span></li>
<li><code>test_take_captures_baseline</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">take() stores current tracemalloc snapshot.</span></li>
<li><code>test_delta_bytes_zero_on_noop</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">delta_bytes() returns ~0 for no-op.</span></li>
<li><code>test_delta_mb_returns_float</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">delta_mb() returns delta in MB as float.</span></li>
<li><code>test_effective_windup_300s_25pct</code> (test_sprint_scheduler.py) — <span class="doc-comment-inline">Sprint 300s: F290 ratio=0.25, raw=75s → floor [15,180]→75. Active = 225s.</span></li>
<li><code>test_effective_windup_600s_30pct</code> (test_sprint_scheduler.py) — <span class="doc-comment-inline">Sprint 600s: F290 ratio=0.30, raw=180s → floor [15,180]→180. Active = 420s.</span></li>
<li><code>test_windup_efficiency_field_present</code> (test_sprint_scheduler.py) — <span class="doc-comment-inline">SprintSchedulerResult has windup_efficiency field (F289).</span></li>
<li><code>test_roll_method</code> (test_hledac_core_rust.py)</li>
<li><code>test_find_near_duplicates_all_same</code> (test_hledac_core_rust.py)</li>
<li><code>test_batch_nfc_normalize_unicode_sameness</code> (test_hledac_core_rust.py)</li>
<li><code>test_telemetry_config_sample_clamped</code> (test_t1_otel.py)</li>
<li><code>test_capacity_validation</code> (test_t1_otel.py)</li>
<li><code>test_basic_put_get</code> (test_t1_otel.py)</li>
<li><code>test_update_existing_no_evict</code> (test_t1_otel.py)</li>
<li><code>test_basic_open_close</code> (test_t1_otel.py)</li>
<li><code>test_decorator_handles_exception</code> (test_t1_otel.py)</li>
<li><code>test_basic_primitives_pass_through</code> (test_t1_otel.py)</li>
<li><code>test_dedup_fingerprint_returns_hex</code> (test_rust_backend.py) — <span class="doc-comment-inline">dedup_fingerprint returns a hex string.</span></li>
<li><code>test_is_valid_url</code> (test_rust_backend.py) — <span class="doc-comment-inline">is_valid_url validates URLs correctly.</span></li>
<li><code>test_batch_classify</code> (test_rust_backend.py) — <span class="doc-comment-inline">batch_classify returns list of (kind, host) tuples.</span></li>
<li><code>test_content_hasher</code> (test_rust_backend.py) — <span class="doc-comment-inline">ContentHasher produces hex strings via static methods.</span></li>
<li><code>test_xxhash_64</code> (test_rust_backend.py) — <span class="doc-comment-inline">content_hash_64 returns integer.</span></li>
<li><code>test_nfc_normalize</code> (test_rust_backend.py) — <span class="doc-comment-inline">nfc_normalize normalizes Unicode.</span></li>
<li><code>test_available_memory</code> (test_rust_backend.py) — <span class="doc-comment-inline">available_memory returns int &gt;= 0.</span></li>
<li><code>test_total_memory</code> (test_rust_backend.py) — <span class="doc-comment-inline">total_memory returns int &gt; 0.</span></li>
<li><code>finalize</code> (test_sprint8l_live.py)</li>
<li><code>stop</code> (test_sprint8l_live.py)</li>
<li><code>stop</code> (test_sprint8l_live.py)</li>
<li><code>test_is_i2p_available_cached</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test I2P availability check uses caching.</span></li>
<li><code>test_fediverse_adapter_init</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test FediverseAdapter initialization.</span></li>
<li><code>test_fediverse_instances_defined</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test OSINT instances are defined.</span></li>
<li><code>test_matrix_adapter_init</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test MatrixPublicAdapter initialization.</span></li>
<li><code>test_metadata_fetcher_init</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test TorrentMetadataFetcher initialization.</span></li>
<li><code>test_async_fetch_dht_metadata_invalid_hash</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test async_fetch_dht_metadata with invalid hash.</span></li>
<li><code>test_drain_bounded_capacity</code> (test_sprint_f273.py) — <span class="doc-comment-inline">Registry maxlen=512 — overflow drops oldest (with cancel).</span></li>
<li><code>test_sprint_flags_has_hermes_force_field</code> (test_sprint_f273.py) — <span class="doc-comment-inline">SprintFlags must have a hermes_force:bool field, default False.</span></li>
<li><code>test_sprint_flags_is_frozen</code> (test_sprint_f273.py) — <span class="doc-comment-inline">SprintFlags is frozen msgspec.Struct — hermes_force must be immutable.</span></li>
<li><code>test_fnocache_constant_present</code> (test_sprint_f273.py)</li>
<li><code>test_sprint_scheduler_result_has_pressure_relief_fields</code> (test_sprint_f273.py)</li>
<li><code>test_pattern_extraction_drain_fields</code> (test_sprint_f273.py)</li>
<li><code>test_assert_no_leak_passes_when_clean</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">assert_no_leak() does not raise when delta is within threshold.</span></li>
<li><code>test_tracemalloc_starts_on_init</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">TracemallocSnapshot starts tracemalloc on __post_init__.</span></li>
<li><code>tearDown</code> (test_sprint_memory_profiling.py)</li>
<li><code>test_stop_session_tracer_stops_tracing</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">stop_session_tracer() stops tracemalloc.</span></li>
<li><code>test_stop_session_tracer_idempotent</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">stop_session_tracer() is safe to call when not started.</span></li>
<li><code>test_assert_memory_leak_fixture_noop_when_clean</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">assert_memory_leak fixture passes when delta is within threshold.</span></li>
<li><code>test_assert_memory_leak_fixture_fails_over_threshold</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">assert_memory_leak fixture raises AssertionError when delta &gt; threshold.</span></li>
<li><code>test_assert_no_leak_fails_over_threshold</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">assert_no_leak() raises AssertionError when delta &gt; threshold.</span></li>
<li><code>test_assert_no_leak_with_context</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">assert_no_leak() includes context string in error message.</span></li>
<li><code>test_disabled_reason_covers_all_lanes</code> (test_f227k_acquisition_lane_parity.py) — <span class="doc-comment-inline">_disabled_reason returns a string for every known lane.</span></li>
<li><code>test_effective_windup_explicit_override_respected</code> (test_sprint_scheduler.py) — <span class="doc-comment-inline">Explicit --windup-lead 50s is respected (above 30s floor).</span></li>
<li><code>test_cve</code> (test_hledac_core_rust.py)</li>
<li><code>test_fingerprint_stable</code> (test_hledac_core_rust.py)</li>
<li><code>test_fingerprint_different_for_different_urls</code> (test_hledac_core_rust.py)</li>
<li><code>test_update_and_digest</code> (test_hledac_core_rust.py)</li>
<li><code>test_hashes_method</code> (test_hledac_core_rust.py)</li>
<li><code>test_insert_and_check</code> (test_hledac_core_rust.py)</li>
<li><code>test_content_hash_hex_matches_manual</code> (test_hledac_core_rust.py)</li>
<li><code>test_simhash_different_texts_high_distance</code> (test_hledac_core_rust.py)</li>
<li><code>test_telemetry_config_from_env_default</code> (test_t1_otel.py)</li>
<li><code>test_clear</code> (test_t1_otel.py)</li>
<li><code>test_record_exception_noop</code> (test_t1_otel.py)</li>
<li><code>test_truncation_marker</code> (test_t1_otel.py)</li>
<li><code>test_init_idempotent</code> (test_t1_otel.py)</li>
<li><code>test_init_returns_false_on_bad_kind</code> (test_t1_otel.py)</li>
<li><code>test_singleton_identity</code> (test_rust_backend.py) — <span class="doc-comment-inline">RustBackend() returns the same instance.</span></li>
<li><code>test_normalize_quality_text</code> (test_rust_backend.py) — <span class="doc-comment-inline">normalize_quality_text strips and lowercases.</span></li>
<li><code>test_classify_url_clearnet</code> (test_rust_backend.py) — <span class="doc-comment-inline">classify_url returns (kind, host) tuple for https URLs.</span></li>
<li><code>test_classify_url_onion</code> (test_rust_backend.py) — <span class="doc-comment-inline">classify_url returns (kind, host) tuple for .onion URLs.</span></li>
<li><code>test_compute_simhash</code> (test_rust_backend.py) — <span class="doc-comment-inline">compute_simhash returns integer.</span></li>
<li><code>test_madvise_returns_bool</code> (test_rust_backend.py) — <span class="doc-comment-inline">madvise_on_mmap_region returns bool (no-op in fallback).</span></li>
<li><code>test_is_private_ip</code> (test_rust_backend.py) — <span class="doc-comment-inline">is_private_ip returns bool.</span></li>
<li><code>test_cidr_contains</code> (test_rust_backend.py) — <span class="doc-comment-inline">cidr_contains returns bool.</span></li>
<li><code>test_sprint_policies_domain_accessible</code> (test_rust_backend.py) — <span class="doc-comment-inline">sprint_policies domain is accessible.</span></li>
<li><code>test_feed_dominance_guard_factory</code> (test_rust_backend.py) — <span class="doc-comment-inline">FeedDominanceGuard factory method works.</span></li>
<li><code>test_lane_budget_pool_factory</code> (test_rust_backend.py) — <span class="doc-comment-inline">LaneBudgetPool factory method works.</span></li>
<li><code>start</code> (test_sprint8l_live.py)</li>
<li><code>test_cid_extraction</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test CID pattern extraction from text.</span></li>
<li><code>test_cid_extraction_v1</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test CIDv1 (bafy) extraction.</span></li>
<li><code>test_veronica_search_config</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test Veronica-2 search is configured.</span></li>
<li><code>test_parse_gemini_url</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test Gemini URL parsing.</span></li>
<li><code>test_parse_gemini_url_with_port</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test Gemini URL parsing with custom port.</span></li>
<li><code>test_parse_gemini_url_simple</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test Gemini URL parsing with just host.</span></li>
<li><code>test_known_eepsites_structure</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test known eepsites list structure.</span></li>
<li><code>test_ipfs_gateway_reachable</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test IPFS gateways are reachable.</span></li>
<li><code>test_gopher_floodgap_reachable</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test Gopher floodgap server is reachable.</span></li>
<li><code>test_default_floor_is_2_seconds</code> (test_sprint_f273.py) — <span class="doc-comment-inline">The class-level default must be 2.0s (was 5.0s in pre-F273A).</span></li>
<li><code>test_windup_for_cycle_floor_protects_short_sprints</code> (test_sprint_f273.py) — <span class="doc-comment-inline">Short sprint (60s, base=30) keeps a usable active window under adapt.</span></li>
<li><code>test_drain_registry_starts_empty</code> (test_sprint_f273.py)</li>
<li><code>test_streaming_exporter_imports_cleanly</code> (test_sprint_f273.py) — <span class="doc-comment-inline">The module must import without errors even on minimal installs.</span></li>
<li><code>test_apply_nocache_missing_file_returns_false</code> (test_sprint_f273.py) — <span class="doc-comment-inline">If file doesn't exist, returns False (fail-soft).</span></li>
<li><code>test_malloc_zone_pressure_relief_returns_int</code> (test_sprint_f273.py)</li>
<li><code>test_windup_lead_diagnostic_fields</code> (test_sprint_f273.py)</li>
<li><code>test_delta_mb_zero_on_noop</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">delta_mb() returns ~0 for no-op (within GC noise margin).</span></li>
<li><code>test_tracker_tracemalloc_included_by_default</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">MemoryTracker includes tracemalloc by default.</span></li>
<li><code>test_assert_no_leak_passes_within_threshold</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">assert_no_leak() does not raise when delta ≤ threshold.</span></li>
<li><code>test_enabled_fn_returns_bool</code> (test_f227k_acquisition_lane_parity.py) — <span class="doc-comment-inline">Each rule's enabled_fn returns a bool for any ctx.</span></li>
<li><code>test_reason_fn_returns_str</code> (test_f227k_acquisition_lane_parity.py) — <span class="doc-comment-inline">Each rule's reason_fn returns a str for enabled ctx.</span></li>
<li><code>test_concurrency_fn_returns_int</code> (test_f227k_acquisition_lane_parity.py) — <span class="doc-comment-inline">Each rule's concurrency_fn returns an int.</span></li>
<li><code>collect</code> (test_coroutine_cleanup.py) — <span class="doc-comment-inline">Run gc and return leaked coroutines.</span></li>
<li><code>load_module</code> (conftest.py)</li>
<li><code>test_source_tier_classification_tier2_deep</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">Tier-2 sources (rl_research, tot_synthesis) are classified as deep.</span></li>
<li><code>test_academic_discovery_in_source_tier_tier1</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">academic_discovery is classified as tier-1 (structured TI).</span></li>
<li><code>test_onion_v3</code> (test_hledac_core_rust.py)</li>
<li><code>test_domain</code> (test_hledac_core_rust.py)</li>
<li><code>test_email</code> (test_hledac_core_rust.py)</li>
<li><code>test_batch_content_hash_hex</code> (test_hledac_core_rust.py)</li>
<li><code>test_batch_compute_consistency</code> (test_hledac_core_rust.py)</li>
<li><code>test_is_near_duplicate_true</code> (test_hledac_core_rust.py)</li>
<li><code>test_is_near_duplicate_false_distant</code> (test_hledac_core_rust.py)</li>
<li><code>test_batch_nfc_normalize_preserves_ascii</code> (test_hledac_core_rust.py)</li>
<li><code>test_span_yields_noop_when_uninitialized</code> (test_t1_otel.py)</li>
<li><code>test_export_empty</code> (test_t1_otel.py)</li>
<li><code>test_uninitialized_returns_noop</code> (test_t1_otel.py)</li>
<li><code>test_init_none_succeeds</code> (test_t1_otel.py)</li>
<li><code>test_shutdown_idempotent</code> (test_t1_otel.py)</li>
<li><code>test_import_no_error</code> (test_rust_backend.py) — <span class="doc-comment-inline">RustBackend imports without ImportError.</span></li>
<li><code>test_classify_url_i2p</code> (test_rust_backend.py) — <span class="doc-comment-inline">classify_url returns (kind, host) tuple for .i2p URLs.</span></li>
<li><code>test_extract_domain</code> (test_rust_backend.py) — <span class="doc-comment-inline">extract_domain extracts the domain.</span></li>
<li><code>get_rss_mb</code> (test_sprint8l_live.py)</li>
<li><code>ipfs_client</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Lazy import IPFS client.</span></li>
<li><code>test_cid_extraction_none</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test CID extraction with no CIDs.</span></li>
<li><code>test_resolve_ipns_invalid_input</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test IPNS resolution with invalid input (raw CID).</span></li>
<li><code>gopher</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Import GopherTransport module directly.</span></li>
<li><code>test_constants</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test protocol constants.</span></li>
<li><code>gemini</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Lazy import Gemini transport.</span></li>
<li><code>test_extract_gemini_links_empty</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test gemtext link extraction with no links.</span></li>
<li><code>test_constants</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test protocol constants.</span></li>
<li><code>i2p</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Lazy import I2P client.</span></li>
<li><code>test_constants</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test I2P constants.</span></li>
<li><code>fetcher</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Lazy import fetcher.</span></li>
<li><code>test_gemini_circumlunar_reachable</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test Gemini circumlunar.space is configured.</span></li>
<li><code>test_matrix_homeserver</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test Matrix homeserver is configured.</span></li>
<li><code>test_async_fetch_dht_metadata_import</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test async_fetch_dht_metadata is importable.</span></li>
<li><code>test_fediverse_fetch_function_exists</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test fetch_fediverse_only function exists.</span></li>
<li><code>test_matrix_fetch_function_exists</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test fetch_matrix_only function exists.</span></li>
<li><code>test_fediverse_timeout_constant</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test FEDIVERSE_TIMEOUT constant.</span></li>
<li><code>test_matrix_timeout_constant</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test MATRIX_TIMEOUT constant.</span></li>
<li><code>test_windup_for_cycle_negative_ema_returns_base</code> (test_sprint_f273.py) — <span class="doc-comment-inline">Negative cycle EMA (defensive) returns base — fail-safe.</span></li>
<li><code>test_tools_init_exports_apply_nocache_to_path</code> (test_sprint_f273.py) — <span class="doc-comment-inline">tools/__init__.py must export apply_nocache_to_path for canonical import.</span></li>
<li><code>test_dynamic_branch_floor_field</code> (test_sprint_f273.py)</li>
<li><code>test_get_rss_mb_returns_positive</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">get_rss_mb() returns a positive float (or 0 on error).</span></li>
<li><code>track</code> (test_coroutine_cleanup.py) — <span class="doc-comment-inline">Register a coroutine for tracking.</span></li>
<li><code>test_source_tier_classification_tier1_structured</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">Tier-1 sources (ct_log_pipeline, circl_pdns) are classified as structured.</span></li>
<li><code>test_source_tier_classification_tier0_indexed</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">Tier-0 sources (rss_atom_pipeline, live_public_pipeline) are indexed.</span></li>
<li><code>test_ipv4_basic</code> (test_hledac_core_rust.py)</li>
<li><code>test_ipv4_private_ranges</code> (test_hledac_core_rust.py)</li>
<li><code>test_ipv4_negative</code> (test_hledac_core_rust.py)</li>
<li><code>test_ipv6</code> (test_hledac_core_rust.py)</li>
<li><code>test_onion_negative_short</code> (test_hledac_core_rust.py)</li>
<li><code>test_md5</code> (test_hledac_core_rust.py)</li>
<li><code>test_sha1</code> (test_hledac_core_rust.py)</li>
<li><code>test_sha256</code> (test_hledac_core_rust.py)</li>
<li><code>test_strip_default_http_port</code> (test_hledac_core_rust.py)</li>
<li><code>test_strip_default_https_port</code> (test_hledac_core_rust.py)</li>
<li><code>test_strip_utm_params</code> (test_hledac_core_rust.py)</li>
<li><code>test_fragment_preserved</code> (test_hledac_core_rust.py)</li>
<li><code>test_strip_utm</code> (test_hledac_core_rust.py)</li>
<li><code>test_strip_fbclid</code> (test_hledac_core_rust.py)</li>
<li><code>test_preserve_other_params</code> (test_hledac_core_rust.py)</li>
<li><code>test_fingerprint_returns_u64</code> (test_hledac_core_rust.py)</li>
<li><code>test_hash_method</code> (test_hledac_core_rust.py)</li>
<li><code>test_content_hash_hex_idempotent</code> (test_hledac_core_rust.py)</li>
<li><code>test_batch_content_hash_deterministic</code> (test_hledac_core_rust.py)</li>
<li><code>test_find_near_duplicates_in_batch_all_same</code> (test_hledac_core_rust.py)</li>
<li><code>test_telemetry_config_frozen</code> (test_t1_otel.py)</li>
<li><code>test_telemetry_config_sample_ratio_full</code> (test_t1_otel.py)</li>
<li><code>test_telemetry_config_from_env_invalid_kind</code> (test_t1_otel.py)</li>
<li><code>test_current_trace_id_zeros</code> (test_t1_otel.py)</li>
<li><code>test_force_flush</code> (test_t1_otel.py)</li>
<li><code>test_exception_in_block_propagates</code> (test_t1_otel.py)</li>
<li><code>test_string_truncation</code> (test_t1_otel.py)</li>
<li><code>test_nested_list_truncated</code> (test_t1_otel.py)</li>
<li><code>test_is_available_is_bool</code> (test_rust_backend.py) — <span class="doc-comment-inline">is_available is a bool.</span></li>
<li><code>test_cid_extraction_empty</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test CID extraction with empty input (fail-safe).</span></li>
<li><code>test_find_via_ipfs_search_returns_list</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test IPFS search returns list of CIDs (may be empty if API unavailable).</span></li>
<li><code>test_bootstrap_servers_defined</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test gopher bootstrap servers are configured.</span></li>
<li><code>test_is_i2p_available_returns_bool</code> (test_alt_protocols.py) — <span class="doc-comment-inline">Test I2P availability check returns bool.</span></li>
<li><code>test_windup_ratio_is_30_percent</code> (test_sprint_f273.py)</li>
<li><code>test_windup_60s_uses_floor_30</code> (test_sprint_f273.py) — <span class="doc-comment-inline">60s sprint: 0.30*60=18, clamped up to 30.</span></li>
<li><code>test_windup_120s_scales</code> (test_sprint_f273.py) — <span class="doc-comment-inline">120s sprint: 0.30*120=36 (above floor, within cap).</span></li>
<li><code>test_windup_1800s_uses_ceiling_180</code> (test_sprint_f273.py) — <span class="doc-comment-inline">1800s sprint: 0.30*1800=540, clamped to 180 (max ceiling).</span></li>
<li><code>test_windup_600s_uses_ceiling_180</code> (test_sprint_f273.py) — <span class="doc-comment-inline">600s sprint: 0.30*600=180, clamped to 180 (max ceiling).</span></li>
<li><code>test_windup_300s_uses_90_no_cap</code> (test_sprint_f273.py) — <span class="doc-comment-inline">P0-1: 300s sprint: 0.30*300=90 (F288 cap removed).</span></li>
<li><code>test_windup_aggressive_300s_uses_45</code> (test_sprint_f273.py) — <span class="doc-comment-inline">Aggressive 300s: 0.15*300=45.</span></li>
<li><code>test_windup_aggressive_600s_uses_90</code> (test_sprint_f273.py) — <span class="doc-comment-inline">Aggressive 600s: 0.15*600=90 (within [30, 180] ceiling).</span></li>
<li><code>test_malloc_zone_pressure_relief_importable</code> (test_sprint_f273.py)</li>
<li><code>test_maybe_call_pressure_relief_method_exists</code> (test_sprint_f273.py)</li>
<li><code>test_stop_is_noop_when_not_started</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">stop() is safe to call on non-started instance.</span></li>
<li><code>test_snapshot_fixture_returns_snapshot</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">memory_snapshot fixture returns a Snapshot object.</span></li>
<li><code>test_memory_tracker_fixture_returns_tracker</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">memory_tracker fixture returns a MemoryTracker object.</span></li>
<li><code>test_ct_log_is_tier1</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">ct_log (ct_log_client.py:273) is tier 1 — structured TI.</span></li>
<li><code>test_ct_log_pipeline_also_tier1</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">ct_log_pipeline alias is also tier 1 — backward compat.</span></li>
<li><code>test_onion_discovery_is_tier2</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">onion_discovery (live_public_pipeline.py:1785) is tier 2 — deep/dark web.</span></li>
<li><code>test_ipfs_is_tier1</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">ipfs (ti_feed_adapter.py:1367) is tier 1 — structured TI.</span></li>
<li><code>test_shodan_search_is_tier1</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">shodan_search (shodan_wrapper.py:204) is tier 1 — structured TI.</span></li>
<li><code>test_bgp_monitor_is_tier1</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">bgp_monitor (ti_feed_adapter.py:1742) is tier 1 — structured TI.</span></li>
<li><code>test_live_public_pipeline_is_tier0</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">live_public_pipeline (live_public_pipeline.py) is tier 0 — indexed/surface.</span></li>
<li><code>test_academic_discovery_is_tier1</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">academic_discovery (live_public_pipeline.py:1995) is tier 1.</span></li>
<li><code>test_pastebin_monitor_is_tier1</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">pastebin_monitor (live_public_pipeline.py:2067) is tier 1.</span></li>
<li><code>test_github_secret_scanner_is_tier1</code> (test_research_depth_metric.py) — <span class="doc-comment-inline">github_secret_scanner (live_public_pipeline.py:2107) is tier 1.</span></li>
<li><code>test_lowercase_scheme_host</code> (test_hledac_core_rust.py)</li>
<li><code>test_preserve_path</code> (test_hledac_core_rust.py)</li>
<li><code>test_preserve_valid_params</code> (test_hledac_core_rust.py)</li>
<li><code>test_ipv6_in_url</code> (test_hledac_core_rust.py)</li>
<li><code>test_empty_url</code> (test_hledac_core_rust.py)</li>
<li><code>test_creation</code> (test_hledac_core_rust.py)</li>
<li><code>test_creation_with_size</code> (test_hledac_core_rust.py)</li>
<li><code>test_batch_dedup_empty</code> (test_hledac_core_rust.py)</li>
<li><code>test_content_hash_64_idempotent</code> (test_hledac_core_rust.py)</li>
<li><code>test_simhash_same_text_distance_zero</code> (test_hledac_core_rust.py)</li>
<li><code>test_find_near_duplicates_empty_list</code> (test_hledac_core_rust.py)</li>
<li><code>test_find_near_duplicates_in_batch_empty</code> (test_hledac_core_rust.py)</li>
<li><code>test_batch_nfc_normalize_empty_list</code> (test_hledac_core_rust.py)</li>
<li><code>test_empty_returns_none</code> (test_t1_otel.py)</li>
<li><code>test_shutdown_without_init_noop</code> (test_t1_otel.py)</li>
<li><code>delta_mb</code> (memory_profiler.py) — <span class="doc-comment-inline">Return delta in MB.</span></li>
<li><code>stop</code> (memory_profiler.py) — <span class="doc-comment-inline">Stop tracemalloc if not in session mode (no-op in session mode).</span></li>
<li><code>finalize</code> (test_sprint8l_live.py)</li>
<li><code>start</code> (test_sprint8l_live.py)</li>
<li><code>test_assert_no_leak_default_threshold</code> (test_sprint_memory_profiling.py) — <span class="doc-comment-inline">Default LEAK_THRESHOLD_MB is 50.0.</span></li>
<li><code>__init__</code> (test_hypothesis_engine.py)</li>
<li><code>test_content_hash_64_different_inputs</code> (test_hledac_core_rust.py)</li>
<li><code>test_content_hash_hex_different_inputs</code> (test_hledac_core_rust.py)</li>
<li><code>test_simhash_identical_texts_equal_fingerprint</code> (test_hledac_core_rust.py)</li>
<li><code>get_span_context</code> (test_t1_otel.py)</li>
<li><code>get_span_context</code> (test_t1_otel.py)</li>
<li><code>get_span_context</code> (test_t1_otel.py)</li>
<li><code>get_span_context</code> (test_t1_otel.py)</li>
<li><code>get_span_context</code> (test_t1_otel.py)</li>
<li><code>get_span_context</code> (test_t1_otel.py)</li>
<li><code>__del__</code> (memory_profiler.py)</li>
<li><code>__init__</code> (test_coroutine_cleanup.py)</li>
</ul>
</details>

<details><summary><strong>Constant</strong> (61)</summary>
<ul>
<li><code>INVARIANT_TABLES</code> (test_differential_fuzzing.py)</li>
<li><code>_TRACKED_PREFIXES</code> (conftest.py)</li>
<li><code>SCENARIOS</code> (test_f227k_acquisition_lane_parity.py)</li>
<li><code>TIMEOUT_BUDGETS</code> (test_sprint8l_live.py)</li>
<li><code>_UNICODE_CHARS</code> (test_differential_fuzzing.py)</li>
<li><code>RATE_LIMIT_STRATEGY</code> (test_sprint8l_live.py)</li>
<li><code>ASCII_TEXT</code> (test_differential_fuzzing.py)</li>
<li><code>IOC_TEXT_IPV4</code> (test_differential_fuzzing.py)</li>
<li><code>IOC_TEXT_EMAILS</code> (test_differential_fuzzing.py)</li>
<li><code>IOC_TEXT_DOMAINS</code> (test_differential_fuzzing.py)</li>
<li><code>IOC_TEXT_HASHES</code> (test_differential_fuzzing.py)</li>
<li><code>IOC_TEXT_CVES</code> (test_differential_fuzzing.py)</li>
<li><code>ENTROPY_TEXT</code> (test_differential_fuzzing.py)</li>
<li><code>QUALITY_TEXT</code> (test_differential_fuzzing.py)</li>
<li><code>TRACKING_URLS</code> (test_differential_fuzzing.py)</li>
<li><code>AUTH_URLS</code> (test_differential_fuzzing.py)</li>
<li><code>IP_URLS</code> (test_differential_fuzzing.py)</li>
<li><code>ONION_URLS</code> (test_differential_fuzzing.py)</li>
<li><code>I2P_URLS</code> (test_differential_fuzzing.py)</li>
<li><code>REPO_ROOT</code> (conftest.py)</li>
<li><code>TESTS_DIR</code> (conftest.py)</li>
<li><code>_HLEDAC_UNIVERSAL_INIT</code> (conftest.py)</li>
<li><code>_HUB_DIR</code> (conftest.py)</li>
<li><code>_LOADED</code> (conftest.py)</li>
<li><code>_OTEL_AVAILABLE</code> (conftest.py)</li>
<li><code>_REPO_ROOT_TEST</code> (conftest.py)</li>
<li><code>_SETTINGS_JSON_PRE_BAK</code> (conftest.py)</li>
<li><code>_MLX_AVAILABLE</code> (conftest.py)</li>
<li><code>URL_REGEX</code> (test_differential_fuzzing.py)</li>
<li><code>URL_STRATEGY</code> (test_differential_fuzzing.py)</li>
<li><code>URL_WITH_TRACKING</code> (test_differential_fuzzing.py)</li>
<li><code>URL_WITH_AUTH</code> (test_differential_fuzzing.py)</li>
<li><code>ONION_URL</code> (test_differential_fuzzing.py)</li>
<li><code>I2P_URL</code> (test_differential_fuzzing.py)</li>
<li><code>IP_URL</code> (test_differential_fuzzing.py)</li>
<li><code>UNICODE_TEXT</code> (test_differential_fuzzing.py)</li>
<li><code>MIXED_CONTENT</code> (test_differential_fuzzing.py)</li>
<li><code>_IOC_BASE_CHARS</code> (test_differential_fuzzing.py)</li>
<li><code>IPV4_STRATEGY</code> (test_differential_fuzzing.py)</li>
<li><code>IPV6_STRATEGY</code> (test_differential_fuzzing.py)</li>
<li><code>PRIVATE_IPS</code> (test_differential_fuzzing.py)</li>
<li><code>PUBLIC_IPS</code> (test_differential_fuzzing.py)</li>
<li><code>PRIVATE_IP</code> (test_differential_fuzzing.py)</li>
<li><code>PUBLIC_IP</code> (test_differential_fuzzing.py)</li>
<li><code>MD5_STRATEGY</code> (test_differential_fuzzing.py)</li>
<li><code>SHA1_STRATEGY</code> (test_differential_fuzzing.py)</li>
<li><code>SHA256_STRATEGY</code> (test_differential_fuzzing.py)</li>
<li><code>UNIFORM_TEXT</code> (test_differential_fuzzing.py)</li>
<li><code>RANDOM_TEXT</code> (test_differential_fuzzing.py)</li>
<li><code>BATCH_TEXTS</code> (test_differential_fuzzing.py)</li>
<li><code>BATCH_URLS</code> (test_differential_fuzzing.py)</li>
<li><code>BATCH_IPS</code> (test_differential_fuzzing.py)</li>
<li><code>GRAPH_IDS</code> (test_differential_fuzzing.py)</li>
<li><code>_JOIN_TIMEOUT_S</code> (spec_mocks.py)</li>
<li><code>_TM_NFRAMES</code> (memory_profiler.py)
<details><summary>Number of stack frames tracked by tracemalloc (default 10, ~80 KB ring buffer).</summary>
<div class="doc-comment">
<p>Number of stack frames tracked by tracemalloc (default 10, ~80 KB ring buffer).</p>
<p></p>
<p>F350M-R fix: reduced from 25 → 10 frames (~80 KB vs ~200 KB per test session).</p>
<p>25 frames × ~8 KB/frame × 100 tests = ~20 MB retained. 10 frames × 100 = ~8 MB.</p>
</div>
</details>
</li>
<li><code>_SESSION_TRACER_STARTED</code> (memory_profiler.py)</li>
<li><code>_SESSION_TRACER_N_FRAMES</code> (memory_profiler.py)</li>
<li><code>LEAK_THRESHOLD_MB</code> (memory_profiler.py) — <span class="doc-comment-inline">Default leak threshold in MB per sprint cycle.</span></li>
<li><code>SEED_DOMAINS</code> (test_sprint8l_live.py)</li>
<li><code>LIVE_QUERY</code> (test_sprint8l_live.py)</li>
<li><code>RSS_KILL_SWITCH_MB</code> (test_sprint8l_live.py)</li>
</ul>
</details>



## Metrics

| Metric | Value |
|---|---|
| Files | 187 |
| Total lines | 53775 |
| Avg lines/file | 287 |
| Languages | Python |
| Outgoing deps | 0 |
| Incoming deps | 0 |
| Tier | 1 |

