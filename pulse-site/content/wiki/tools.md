+++
title = "tools/"
weight = 34
description = "<think> The user wants me to create a module overview for the `tools` module based on the structural context provided. Let me analyze what this module does based on the file names, symbol names, and d..."

[extra]
tier = 1
file_count = 119
total_lines = 36873
languages = "Python"
has_mermaid = true
+++

<think>
The user wants me to create a module overview for the `tools` module based on the structural context provided. Let me analyze what this module does based on the file names, symbol names, and dependency information.

Key observations:
1. This is a very large module with 119 Python files and ~37K lines of code
2. It has very few dependencies (1 outgoing, 2 incoming) - suggesting it's more of a leaf/utility module
3. The files include things like:
 - `url_dedup.py` - URL deduplication with Rust-backed Bloom filters
 - `content_miner.py` - Content extraction/mining with Rust libraries
 - `document_metadata_extractor.py` - Forensic document metadata extraction
 - Various gate/check scripts (prelive_decision_gate, prelive_one_button_gate, live_measurement_extraction_guard, etc.)
 - Benchmark scripts (bench_f214_*, bench_m1_*)
 - Migration scripts (migrate_*)
 - Research/quality scoring
 - Executor for tools

4. Key symbols show:
 - `MmapBloomFilterAdapter` - Thread-safe wrapper for Rust Bloom filter
 - `RustMiner` - Content miner using Rust-backed libraries
 - `ToolExecutor` - Canonical async tool executor
 - Various `run_gate`, `run_guard` functions for prelive/live validation
 - `run_one_button_gate`, `run_guard` etc.

5. The module seems to provide:
 - Infrastructure for URL deduplication and content mining
 - Gates and guards for prelive/live decision making
 - Benchmarking capabilities
 - Document metadata extraction (FOCA-style forensics)
 - Research quality scoring

6. Architectural role: This seems like a hub for operational utilities - providing infrastructure for the main system. 

The module functions as a utility hub that other parts of the system depend on, yet it maintains minimal external dependencies. This suggests the tools are self-contained and serve as operational infrastructure for the broader system, handling critical functions like validation, deduplication, and content extraction that support the main workflow.

With 119 files totaling around 37K lines of code, the module is notably large—well above the typical codebase average—indicating substantial implementation complexity. The "Mmap BloomFilter Adapter" stands out as a key abstraction, enabling thread-safe Bloom filtering operations for the deduplication system. The module's primary responsibilities center on providing operational utilities that other components rely on, including specialized artifacts like `RustMiner` for content extraction and validation gates for pre-release checks.
</think>

This module provides operational utilities and infrastructure services that support the core research pipeline—encompassing content extraction, URL deduplication, document forensics, pre-live and live validation gates, benchmarking, and research quality scoring. It operates as a leaf in the dependency graph with minimal upstream coupling (only one internal dependency on `paths.py`), yet serves as a foundation relied upon by coordinators and project-type systems, meaning modifications here carry significant blast radius for dependent workflows. The module is large by codebase standards, with 119 Python files totaling roughly 37,000 lines and an average density of 309 lines per file, indicating substantial complexity and breadth of tooling. Core abstractions include a thread-safe `MmapBloomFilterAdapter` wrapping Rust-backed Bloom filters for cross-process URL deduplication, a `RustMiner` class leveraging Rust libraries (trafilex, traflatura) for memory-efficient content extraction, and a `ToolExecutor` that serves as the canonical async execution entry point. A significant portion of the module consists of gating and guard scripts (`run_gate`, `run_guard`, `run_one_button_gate`) that enforce pre-live and live measurement integrity, alongside benchmarking harnesses and migration codemods that modernize async patterns and dataclass schemas. Document forensics capabilities extract FOCA-style metadata from PDFs, Office documents, and emails, including hidden content, macros, and embedded fonts. The high symbol count (526 functions, 77 classes) reflects a library of focused, composable tools rather than a monolithic subsystem.

## Dependency Diagram

{% mermaid() %}
graph LR
    m_tools["<b>tools/</b>"]
    style m_tools fill:#a78bfa,color:#0d0d0d,stroke:#a78bfa
    m_paths_py["paths.py/"]
    m_tools -->|2| m_paths_py
    m_coordinators["coordinators/"]
    m_coordinators -->|3| m_tools
    m_project_types_py["project_types.py/"]
    m_project_types_py -->|1| m_tools
    classDef default fill:#1a1a2e,stroke:#a78bfa,color:#e0e0e0
    click m_tools "/wiki/tools/"
    click m_paths_py "/wiki/paths.py/"
    click m_coordinators "/wiki/coordinators/"
    click m_project_types_py "/wiki/project_types.py/"
{% end %}

## Structure

### Sub-modules

- [**analyze/**](/wiki/tools-analyze/) — 3 files, 737 lines (Python)

| Language | Files |
|---|---|
| Python | 119 |

### Directories

| Directory | Files | Lines |
|---|---|---|
| analyze/ | 3 | 737 |
| probe_f214h_content_miner_backpressure/ | 1 | 309 |

### Largest Files

- `document_metadata_extractor.py` (1324 lines)
- `url_dedup.py` (1322 lines)
- `content_miner.py` (1064 lines)
- `f234_validate_nonfeed_live_report.py` (1016 lines)
- `bench_f214_python314_runtime.py` (978 lines)
- `prelive_one_button_gate.py` (918 lines)
- `live_measurement_extraction_guard.py` (845 lines)
- `prelive_decision_gate.py` (731 lines)
- `live_multisource_validator.py` (664 lines)
- `migrate_gather_to_safe_gather.py` (655 lines)

<details><summary><strong>Show 109 more files</strong></summary>

- `bench_m1_runtime_gates.py` (654 lines)
- `executor.py` (640 lines)
- `evidence_delta_memory.py` (610 lines)
- `prelive_artifact_cockpit.py` (604 lines)
- `live_result_sanity.py` (603 lines)
- `live_artifact_triage.py` (557 lines)
- `research_quality_score.py` (547 lines)
- `live_kpi_extraction_guard.py` (544 lines)
- `migrate_dataclass_to_msgspec.py` (523 lines)
- `qoder_reality_check.py` (517 lines)
- `api_doc_generator.py` (499 lines)
- `final_prelive_readiness.py` (481 lines)
- `codegen_ioc_patterns.py` (447 lines)
- `probe_r0_nonfeed_reality_lock.py` (430 lines)
- `profile_f214_runtime_workloads.py` (426 lines)
- `probe_f214t_tstring_safe_renderer.py` (417 lines)
- `analyze/autonomous_analyzer.py` (410 lines)
- `probe_f214int_interpreter_pool.py` (401 lines)
- `capability_kpi_dashboard.py` (393 lines)
- `migrate_waitfor_issue9.py` (368 lines)
- `bench_gc_314_runtime.py` (361 lines)
- `replay_research_loop.py` (360 lines)
- `migrate_waitfor_phase2.py` (359 lines)
- `probe_f214opt314_runtime_optimizations.py` (354 lines)
- `lmdb_kv.py` (334 lines)
- `regex_cache.py` (332 lines)
- `core_readiness_gate.py` (329 lines)
- `probe_f214m_execution_optimizer_backpressure.py` (320 lines)
- `discovery_replay.py` (310 lines)
- `probe_f214h_content_miner_backpressure/probe_f214h.py` (309 lines)
- `migrate_test_mocks.py` (307 lines)
- `probe_f214r_annotationlib_introspection.py` (306 lines)
- `bench_py314_jit.py` (305 lines)
- `registry.py` (304 lines)
- `runtime_authority_probe.py` (302 lines)
- `rl_health_report.py` (297 lines)
- `prelive_artifact_pack.py` (293 lines)
- `source_bandit.py` (291 lines)
- `codehealth_guard.py` (286 lines)
- `test.py` (281 lines)
- `report_truth_trace.py` (280 lines)
- `audit_eager_imports.py` (278 lines)
- `rolling_hash_engine.py` (275 lines)
- `audit_reality_index.py` (274 lines)
- `bounded_queue_audit.py` (269 lines)
- `flag_smoke_runner.py` (265 lines)
- `file_cache.py` (260 lines)
- `metadata_dedup.py` (252 lines)
- `session_manager.py` (251 lines)
- `probe_f214zstd2_transient_artifacts.py` (249 lines)
- `f234_nonfeed_diagnostic_preflight.py` (246 lines)
- `deep_web_hints.py` (241 lines)
- `reranker.py` (240 lines)
- `analyze/analyze_report.py` (232 lines)
- `windup_authority_audit.py` (225 lines)
- `content_extractor.py` (223 lines)
- `wasm_sandbox.py` (220 lines)
- `_py314_apply_slots.py` (218 lines)
- `codemod_add_slots.py` (214 lines)
- `ioc_dedup.py` (211 lines)
- `check_dependency_profiles.py` (204 lines)
- `ftp_explorer.py` (204 lines)
- `delta_compressor.py` (202 lines)
- `hledac_doctor.py` (197 lines)
- `rl_training_dryrun.py` (196 lines)
- `vlm_analyzer.py` (194 lines)
- `_py314_raise_from_e.py` (180 lines)
- `f231_artifact_inventory.py` (173 lines)
- `live_memory_preflight.py` (173 lines)
- `osint_frameworks.py` (169 lines)
- `cp314_wheel_gate.py` (168 lines)
- `serialization.py` (159 lines)
- `smart_deduplicator.py` (157 lines)
- `async_compat_audit.py` (154 lines)
- `revert_gather_migration.py` (154 lines)
- `dump_asyncio_tasks.py` (148 lines)
- `fix_broken_codemod.py` (146 lines)
- `darknet.py` (145 lines)
- `revert_gather_migration_text.py` (139 lines)
- `lightpanda_manager.py` (137 lines)
- `commoncrawl_adapter.py` (134 lines)
- `ddgs_client.py` (134 lines)
- `run_live_validation_pack.py` (133 lines)
- `vision_analyzer.py` (128 lines)
- `audit_try_except.py` (127 lines)
- `live_kpi_responsibility_index.py` (126 lines)
- `ocr_engine.py` (115 lines)
- `repair_nodriver_py314_encoding.py` (113 lines)
- `hnsw_builder.py` (111 lines)
- `audit_flags.py` (108 lines)
- `temporal.py` (106 lines)
- `searxng_client.py` (101 lines)
- `fix_project_types.py` (96 lines)
- `analyze/_analyze_deep.py` (95 lines)
- `live_measurement_responsibility_index.py` (94 lines)
- `ci_tst001_guard.py` (87 lines)
- `paywall.py` (80 lines)
- `zstd_compressor.py` (75 lines)
- `assert_py314_runtime.py` (73 lines)
- `reputation.py` (69 lines)
- `fix_sprint_scheduler.py` (69 lines)
- `deep_research_sources.py` (69 lines)
- `ci_root_scripts_guard.py` (67 lines)
- `scoring.py` (64 lines)
- `policies.py` (59 lines)
- `lightpanda_pool.py` (58 lines)
- `__init__.py` (50 lines)
- `search_fusion.py` (45 lines)
- `wayback_adapter.py` (37 lines)

</details>


## Dependencies

Depends on **1 files** across **1 modules**.

**[paths.py/](@/wiki/paths.py.md)** (1 files):
- `paths.py`



## Dependents

Used by **2 files** across **2 modules**.

**[coordinators/](@/wiki/coordinators.md)** (1 files):
- `fetch_coordinator.py`

**[project_types.py/](@/wiki/project_types.py.md)** (1 files):
- `project_types.py`



## Key Symbols

<p><strong>Key definitions:</strong></p>
<ul>
<li>
<p><code>run_gate</code> (Function) in prelive_decision_gate.py — referenced in 4 files</p>
<details><summary>Run the pre-live decision gate.</summary>
<div class="doc-comment">
<p>Run the pre-live decision gate.</p>
<p>No live sprint. No model load. No network. No SprintScheduler.</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: core_readiness_gate.py, final_no_live_readiness.py, final_prelive_readiness.py</li></ul>
</li>
<li>
<p><code>run_guard</code> (Function) in live_measurement_extraction_guard.py — referenced in 4 files</p>
<details><summary>Run all extraction guard checks.</summary>
<div class="doc-comment">
<p>Run all extraction guard checks.</p>
<p>Returns a dict with verdict, checks, and details.</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: codehealth_guard.py, live_kpi_extraction_guard.py, run_probe.py</li></ul>
</li>
<li>
<p><code>MmapBloomFilterAdapter</code> (Class) in url_dedup.py — referenced in 3 files</p>
<details><summary>Thread-safe adapter wrapping Rust MmapBloomFilter.</summary>
<div class="doc-comment">
<p>Thread-safe adapter wrapping Rust MmapBloomFilter.</p>
<p></p>
<p>The underlying Rust class is not Send+Sync at the bit level — concurrent</p>
<p>add/contains on the same filter would race on the bitmap. This adapter</p>
<p>adds a `threading.Lock` so multi-threaded dedup is safe.</p>
<p></p>
<p>Lifecycle:</p>
<p>- File is opened or created on first call to `create_mmap_bloom_filter`.</p>
<p>- State persists in `path` across process restarts (msync(MS_ASYNC) per</p>
<p>write + msync(MS_SYNC) on Drop).</p>
<p>- On `reset()` the file is truncated to empty state (in-place, no</p>
<p>re-alloc — the mmap region stays valid).</p>
<p></p>
<p>M1 8GB safety:</p>
<p>- Demand-paged: cold pages live on disk, not in RSS.</p>
<p>- Bounded: capacity is fixed at creation; FPR degrades past capacity.</p>
<p>- Fail-soft: every method is wrapped in try/except. On IO error the</p>
<p>dedup degrades to "definitely not present" so the caller can still</p>
<p>proceed without crashing the sprint.</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: bloom.rs, test_f_u1_mmap_bloom.py</li></ul>
</li>
<li>
<p><code>RustMiner</code> (Class) in content_miner.py — referenced in 3 files</p>
<details><summary>Lightweight content miner using Rust-backed libraries.</summary>
<div class="doc-comment">
<p>Lightweight content miner using Rust-backed libraries.</p>
<p></p>
<p>Strategy:</p>
<p>1. Try trafilex (Rust, fastest) - minimal DOM</p>
<p>2. Fallback to traflatura (minimal mode) - streaming</p>
<p>3. Ultimate fallback to regex (no dependencies)</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py, test_html_parser_characterization.py</li></ul>
</li>
<li>
<p><code>ToolExecutor</code> (Class) in executor.py — referenced in 2 files</p>
<details><summary>Canonical async tool executor.</summary>
<div class="doc-comment">
<p>Canonical async tool executor.</p>
<p></p>
<p>Separated from registry for testability — async patterns can be</p>
<p>tested in isolation without full registry initialization.</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: tool_registry.py</li></ul>
</li>
</ul>

<details><summary><strong>Function</strong> (526)</summary>
<ul>
<li><code>run_one_button_gate</code> (prelive_one_button_gate.py)
<details><summary>Run the one-button prelive gate.</summary>
<div class="doc-comment">
<p>Run the one-button prelive gate.</p>
<p></p>
<p>No live sprint. No model load. No network.</p>
</div>
</details>
</li>
<li><code>run_gate</code> (prelive_decision_gate.py)
<details><summary>Run the pre-live decision gate.</summary>
<div class="doc-comment">
<p>Run the pre-live decision gate.</p>
<p>No live sprint. No model load. No network. No SprintScheduler.</p>
</div>
</details>
</li>
<li><code>merge_cockpit</code> (prelive_artifact_cockpit.py) — <span class="doc-comment-inline">Merge decision gate + artifact pack + readiness into a single CockpitResult.</span></li>
<li><code>build_structure_map</code> (content_miner.py)
<details><summary>Build structure map: scan Python project, extract imports, build dependency graph.</summary>
<div class="doc-comment">
<p>Build structure map: scan Python project, extract imports, build dependency graph.</p>
<p></p>
<p>Args:</p>
<p>root_dir: Root directory to scan</p>
<p>limits: Resource limits (max_files, max_bytes_total, time_budget_ms, etc.)</p>
<p>state: Persistent state (file_cache LRU, prev_edges)</p>
<p></p>
<p>Returns:</p>
<p>Dict with fingerprint, files, edges, meta</p>
</div>
</details>
</li>
<li><code>run_guard</code> (live_measurement_extraction_guard.py)
<details><summary>Run all extraction guard checks.</summary>
<div class="doc-comment">
<p>Run all extraction guard checks.</p>
<p>Returns a dict with verdict, checks, and details.</p>
</div>
</details>
</li>
<li><code>triage_live_artifact</code> (live_artifact_triage.py)
<details><summary>Classify a live sprint measurement JSON and return triage result.</summary>
<div class="doc-comment">
<p>Classify a live sprint measurement JSON and return triage result.</p>
<p></p>
<p>Decision order matters — earlier rules take precedence.</p>
</div>
</details>
</li>
<li><code>classify_path</code> (qoder_reality_check.py) — <span class="doc-comment-inline">Classify a path based on known patterns and wiring evidence.</span></li>
<li><code>_python_execute_handler</code> (executor.py)</li>
<li><code>compare_capability_artifacts</code> (evidence_delta_memory.py)
<details><summary>Compare two live measurement JSON artifacts and determine if OSINT capability improved.</summary>
<div class="doc-comment">
<p>Compare two live measurement JSON artifacts and determine if OSINT capability improved.</p>
<p></p>
<p>F226F: Deterministic capability comparator that answers:</p>
<p>"Did OSINT capability improve?"</p>
<p></p>
<p>Does NOT run live measurement. Consumes existing JSON artifacts only.</p>
<p>No benchmark import. No scheduler import. No network/model call.</p>
<p></p>
<p>Args:</p>
<p>previous_json: Path to previous run JSON (None for first run)</p>
<p>current_json: Path to current run JSON</p>
<p></p>
<p>Returns:</p>
<p>CapabilityDelta with verdict and dimension breakdowns</p>
</div>
</details>
</li>
<li><code>compute_delta</code> (evidence_delta_memory.py) — <span class="doc-comment-inline">Compute evidence delta between two report JSON files.</span></li>
<li><code>validate_report</code> (f234_validate_nonfeed_live_report.py)
<details><summary>Validate a live sprint JSON report.</summary>
<div class="doc-comment">
<p>Validate a live sprint JSON report.</p>
<p></p>
<p>Returns (exit_code, result_dict):</p>
<p>0 — valid diagnostic report</p>
<p>1 — malformed / truth missing</p>
<p>2 — profile propagation failed</p>
<p>3 — KPI/scoring mismatch</p>
<p>4 — canonical acquisition fallback used</p>
<p>5 — source-family outcome consistency failure</p>
<p>6 — duplicate normalized source families (CT/ct, PUBLIC/public)</p>
<p>7 — profile/priority mismatch for expected nonfeed_diagnostic run</p>
<p>8 — CT prelude contradiction</p>
<p>9 — public DISCOVERY_ERROR without concrete discovery_empty_reason/provider surface</p>
</div>
</details>
</li>
<li><code>sanity_check</code> (live_result_sanity.py)
<details><summary>Load and sanity-check a result bundle.</summary>
<div class="doc-comment">
<p>Load and sanity-check a result bundle.</p>
<p></p>
<p>Can accept either file paths (for CLI use) or raw dicts (for test use).</p>
</div>
</details>
</li>
<li><code>main</code> (prelive_one_button_gate.py)</li>
<li><code>dedupe_url_list</code> (url_dedup.py)</li>
<li><code>_render_markdown</code> (prelive_one_button_gate.py) — <span class="doc-comment-inline">Render one-button result as markdown report.</span></li>
<li><code>_run_self_test</code> (prelive_one_button_gate.py)
<details><summary>Self-test mode: validates artifact resolution and expected assertion contract.</summary>
<div class="doc-comment">
<p>Self-test mode: validates artifact resolution and expected assertion contract.</p>
<p>NEVER runs live. No network. No MLX. No model load.</p>
</div>
</details>
</li>
<li><code>extract_links</code> (content_miner.py)
<details><summary>Extract links from HTML with anchor context and scoring - M1 8GB optimized.</summary>
<div class="doc-comment">
<p>Extract links from HTML with anchor context and scoring - M1 8GB optimized.</p>
<p></p>
<p>Args:</p>
<p>html_content: Raw HTML content</p>
<p>base_url: Base URL for resolving relative links</p>
<p>max_links: Maximum number of links to extract (hard limit)</p>
<p></p>
<p>Returns:</p>
<p>List of dicts with 'url', 'anchor_text', 'context_snippet', 'rel_flags', 'score'</p>
</div>
</details>
</li>
<li><code>_failures_from_dict</code> (live_multisource_validator.py)</li>
<li><code>execute_with_limits</code> (executor.py)</li>
<li><code>_check_kpi_module_boundary</code> (live_measurement_extraction_guard.py)</li>
<li><code>analyze_class</code> (migrate_dataclass_to_msgspec.py) — <span class="doc-comment-inline">Analyze a @dataclass class and determine migration eligibility.</span></li>
<li><code>_root_import_benchmark</code> (bench_f214_python314_runtime.py)</li>
<li><code>_replace_gather_calls</code> (migrate_gather_to_safe_gather.py)
<details><summary>Apply replacements to the file. Returns (new_source, applied_descriptions).</summary>
<div class="doc-comment">
<p>Apply replacements to the file. Returns (new_source, applied_descriptions).</p>
<p></p>
<p>Robust strategy: re-parse the (already-modified) source, find the gather</p>
<p>call by its textual representation (`asyncio.gather(...)` or</p>
<p>`_asyncio.gather(...)`), and replace that exact span. This handles</p>
<p>multi-line calls correctly without relying on line/col offsets.</p>
</div>
</details>
</li>
<li><code>_check_profile_priority_mismatch</code> (f234_validate_nonfeed_live_report.py)
<details><summary>Exit 7: profile/priority mismatch for expected nonfeed_diagnostic run.</summary>
<div class="doc-comment">
<p>Exit 7: profile/priority mismatch for expected nonfeed_diagnostic run.</p>
<p></p>
<p>When acquisition_prelude_missing_lanes contains 'CT' (or CT is absent from</p>
<p>outcomes due to failure), the run was expected to be nonfeed_diagnostic but</p>
<p>either acquisition_profile=default OR nonfeed_priority_enabled=False — both</p>
<p>indicate the nonfeed diagnostic intent was not properly propagated.</p>
</div>
</details>
</li>
<li><code>_check_live_kpi_input_wiring</code> (live_measurement_extraction_guard.py)
<details><summary>Check LiveKpiInput wiring in live_sprint_measurement.py:</summary>
<div class="doc-comment">
<p>Check LiveKpiInput wiring in live_sprint_measurement.py:</p>
<p>- LiveKpiInput dataclass exists</p>
<p>- _derive_live_kpi_from_input exists and has exactly one param named 'inp'</p>
<p>- _derive_live_kpi_from_input body must NOT load bare old param names</p>
<p>(status, runtime_truth, actual_duration_s, primary_signal_source, etc.)</p>
<p>as free variables — it must use inp.attr access.</p>
<p></p>
<p>Returns (has_violation, list_of_violations).</p>
</div>
</details>
</li>
<li><code>apply_migration</code> (migrate_dataclass_to_msgspec.py) — <span class="doc-comment-inline">Apply migration to file. Returns True if changes were made.</span></li>
<li><code>scan_qoder_wiki</code> (qoder_reality_check.py) — <span class="doc-comment-inline">Scan entire Qoder wiki tree and build reality matrix.</span></li>
<li><code>create_default_registry</code> (executor.py) — <span class="doc-comment-inline">Create ToolRegistry with all built-in tools registered.</span></li>
<li><code>write_markdown</code> (qoder_reality_check.py) — <span class="doc-comment-inline">Write Markdown reality matrix report.</span></li>
<li><code>main</code> (bench_m1_runtime_gates.py)</li>
<li><code>create_rotating_bloom_filter</code> (url_dedup.py)</li>
<li><code>extract_embedded_json</code> (content_miner.py)
<details><summary>Extract embedded JSON states from HTML (Next.js, React, etc.)</summary>
<div class="doc-comment">
<p>Extract embedded JSON states from HTML (Next.js, React, etc.)</p>
<p></p>
<p>Extracts:</p>
<p>- &lt;script id="__NEXT_DATA__" type="application/json"&gt;</p>
<p>- &lt;script type="application/json"&gt; (limited)</p>
<p></p>
<p>Args:</p>
<p>html_content: Raw HTML content</p>
<p>url: Source URL for logging</p>
<p>max_scripts: Maximum JSON scripts to extract (default: 3)</p>
<p>max_bytes_per_script: Max bytes per script (default: 10KB)</p>
<p>max_total_chars: Max total extracted characters (default: 2000)</p>
<p></p>
<p>Returns:</p>
<p>Dict with 'embedded_state' containing type, preview, size, extracted_chars</p>
</div>
</details>
</li>
<li><code>_is_pass</code> (prelive_decision_gate.py)
<details><summary>Check if a probe report passes.</summary>
<div class="doc-comment">
<p>Check if a probe report passes.</p>
<p>Supports multiple schemas:</p>
<p>- {"status": "PASS"|"FAIL"|"PASSED"|"COMPLETE"}</p>
<p>- {"test_results": {"probe_XXX": {"status": "PASS"|"FAIL"}}}</p>
<p>- {"tests": {"all_passed": true}}</p>
<p>- {"tests": {"all_passing": true}}       (F225B schema)</p>
<p>- {"verification": {"passed": true}}</p>
<p>- {"verification": {"status": "PASS"|"PASSED"|"COMPLETE"}}</p>
<p>- {"all_passed": true}</p>
<p>- {"passed": true}</p>
<p>- {"ready_for_controlled_smoke": true}</p>
<p>- {"verdict": "SANITY_PASS"}  (zero-findings sanity: PASS means no crash)</p>
<p>Fail-closed: explicit FAIL/FAILED status wins over weaker pass fields.</p>
</div>
</details>
</li>
<li><code>main</code> (migrate_gather_to_safe_gather.py)</li>
<li><code>render_markdown</code> (prelive_artifact_cockpit.py) — <span class="doc-comment-inline">Render cockpit result as markdown report.</span></li>
<li><code>normalize_url</code> (url_dedup.py)
<details><summary>Normalize URL for canonical representation.</summary>
<div class="doc-comment">
<p>Normalize URL for canonical representation.</p>
<p></p>
<p>Uses Rust implementation if available, falls back to Python.</p>
<p></p>
<p>Args:</p>
<p>url: Raw URL string to normalize</p>
<p></p>
<p>Returns:</p>
<p>Canonical URL string (lowercased host, sorted params, no fragment)</p>
</div>
</details>
</li>
<li><code>_duckdb_store_benchmark</code> (bench_f214_python314_runtime.py)
<details><summary>DuckDBShadowStore first-access benchmark.</summary>
<div class="doc-comment">
<p>DuckDBShadowStore first-access benchmark.</p>
<p></p>
<p>Cold first-access = import-chain benchmark.</p>
<p>The store module import triggers duckdb + orjson init, which is what we measure.</p>
<p>After import, DuckDBShadowStore() instantiation is cheap (~µs).</p>
<p></p>
<p>IMPORTANT: cold first-access variance is EXPECTED because:</p>
<p>- duckdb engine initialization is JIT-compiled on first call</p>
<p>- orjson FFI loads per-process</p>
<p>- Python module import involves path traversal, pyc load, bytecode verify</p>
<p>Variance of 10x–50x between runs is normal for cold import-chain benchmarks.</p>
</div>
</details>
</li>
<li><code>_render_markdown</code> (prelive_decision_gate.py) — <span class="doc-comment-inline">Render decision result as markdown report.</span></li>
<li><code>_check_source_family_outcomes</code> (f234_validate_nonfeed_live_report.py)
<details><summary>Source-family outcome consistency check (exit 5).</summary>
<div class="doc-comment">
<p>Source-family outcome consistency check (exit 5).</p>
<p></p>
<p>Rules:</p>
<p>- public_terminal_stage non-empty but PUBLIC not in source_family_outcomes -&gt; exit 5</p>
<p>- ct_terminal_stage or ct_provider_status non-empty but CT not in source_family_outcomes -&gt; exit 5</p>
<p>- Missing source_family_outcomes is OK (dry-run without terminal stages)</p>
</div>
</details>
</li>
<li><code>_extract_pdf</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Extract from PDF using PyMuPDF with FOCA-style deep analysis.</span></li>
<li><code>_extract_capability_fields</code> (evidence_delta_memory.py)
<details><summary>Extract capability-relevant fields from a report JSON.</summary>
<div class="doc-comment">
<p>Extract capability-relevant fields from a report JSON.</p>
<p></p>
<p>Handles two structures:</p>
<p>- live_sprint_measurement output (full report with runtime_truth, acquisition_report, etc.)</p>
<p>- research_quality_score output (live_artifact_result)</p>
<p></p>
<p>Returns a flat dict with capability-relevant fields for comparison.</p>
</div>
</details>
</li>
<li><code>format_markdown</code> (live_kpi_extraction_guard.py)</li>
<li><code>_extract_pdf_hidden_content</code> (document_metadata_extractor.py)
<details><summary>Extract PDF hidden content:</summary>
<div class="doc-comment">
<p>Extract PDF hidden content:</p>
<p>- Invisible text layers (OCR vs embedded text mismatch)</p>
<p>- Hidden form fields</p>
<p>- JavaScript actions</p>
<p>- Embedded files</p>
<p>- Incremental updates</p>
</div>
</details>
</li>
<li><code>_extract_windup_irrelevant_reason</code> (live_multisource_validator.py)
<details><summary>Extract windup irrelevant reason + windup_not_applicable from canonical aliases.</summary>
<div class="doc-comment">
<p>Extract windup irrelevant reason + windup_not_applicable from canonical aliases.</p>
<p></p>
<p>Returns (reason_string, windup_not_applicable_bool) or (None, None).</p>
<p>Supported reason aliases:</p>
<p>data["windup_guard_observation"]["last_reason"]</p>
<p>data["windup_guard_reason"]</p>
<p>data["live_kpi"]["windup_guard_last_reason"]</p>
<p>Supported not_applicable aliases:</p>
<p>data["windup_guard_observation"]["windup_guard_not_applicable"]</p>
<p>data["windup_guard_not_applicable"]</p>
<p>data["live_kpi"]["windup_guard_not_applicable"]</p>
</div>
</details>
</li>
<li><code>_extract_docx</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Extract from DOCX with FOCA-style revision history and fonts.</span></li>
<li><code>strip_tracking_params</code> (url_dedup.py)
<details><summary>Strip tracking parameters (UTM, fbclid, etc.) from URL.</summary>
<div class="doc-comment">
<p>Strip tracking parameters (UTM, fbclid, etc.) from URL.</p>
<p></p>
<p>Uses Rust implementation if available, falls back to Python.</p>
</div>
</details>
</li>
<li><code>format_markdown</code> (live_measurement_extraction_guard.py)</li>
<li><code>_zstd_benchmark</code> (bench_f214_python314_runtime.py)</li>
<li><code>main</code> (prelive_artifact_cockpit.py)</li>
<li><code>_extract_xlsx</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Extract from XLSX with embedded fonts.</span></li>
<li><code>bench_html_parser_characterization</code> (bench_m1_runtime_gates.py)
<details><summary>Characterize selectolax vs bs4 on a fixed HTML fixture.</summary>
<div class="doc-comment">
<p>Characterize selectolax vs bs4 on a fixed HTML fixture.</p>
<p></p>
<p>Fixture: mixed real-world HTML (title, links, paragraphs).</p>
<p>Measures parse time only — NO network, NO browser, NO OCR.</p>
<p></p>
<p>Returns both selectolax and bs4 results (when available) so the</p>
<p>characterization can be used for migration validation.</p>
</div>
</details>
</li>
<li><code>bench_body_limiter_throughput</code> (bench_m1_runtime_gates.py)
<details><summary>Measure read_body_with_cap throughput.</summary>
<div class="doc-comment">
<p>Measure read_body_with_cap throughput.</p>
<p></p>
<p>Fixture: synthetic async chunk stream (100× 1KB chunks).</p>
<p>NO network, NO browser, NO OCR, NO model load.</p>
</div>
</details>
</li>
<li><code>_check_kpi_runtime_counts_match</code> (f234_validate_nonfeed_live_report.py)
<details><summary>KPI/research_quality finding counts match runtime accepted findings.</summary>
<div class="doc-comment">
<p>KPI/research_quality finding counts match runtime accepted findings.</p>
<p></p>
<p>Validates: runtime_accepted_findings should equal sum of branch counts.</p>
<p>For FEED_ONLY reports (QUALITY_FAIL_FEED_ONLY), counts must NOT be zeroed.</p>
</div>
</details>
</li>
<li><code>_extract_acquisition_prelude</code> (live_multisource_validator.py)
<details><summary>Extract acquisition_prelude fields from all canonical locations.</summary>
<div class="doc-comment">
<p>Extract acquisition_prelude fields from all canonical locations.</p>
<p></p>
<p>Returns a dict with keys:</p>
<p>prelude_checked: bool | None</p>
<p>prelude_missing_lanes: list | None</p>
<p>prelude_terminal_lanes: list | None</p>
<p>terminal_lanes_from_prelude: bool  (whether terminal_lanes came from prelude vs terminality)</p>
<p></p>
<p>Checks these locations in order (first wins):</p>
<p>- top-level: data["acquisition_prelude"]</p>
<p>- live_kpi: data["live_kpi"]["acquisition_prelude"]</p>
<p>- acquisition_report: data["acquisition_report"]["acquisition_prelude"]</p>
<p>- canonical_run_summary: data["canonical_run_summary"]["acquisition_prelude"]</p>
</div>
</details>
</li>
<li><code>scan_probe_artifacts</code> (prelive_artifact_cockpit.py)
<details><summary>Scan probe_f* directories for sprint ID collisions.</summary>
<div class="doc-comment">
<p>Scan probe_f* directories for sprint ID collisions.</p>
<p></p>
<p>Detects:</p>
<p>- Multiple probe dirs with the same sprint ID (e.g. F223D product_value + F223D prewindup)</p>
<p>- Ambiguous aliases that could confuse operator reports</p>
<p></p>
<p>Returns SprintCollisionReport with collision list and warnings.</p>
</div>
</details>
</li>
<li><code>bench_batch_scheduler_queue_flush_smoke</code> (bench_m1_runtime_gates.py)
<details><summary>Smoke test: BatchScheduler flush with 1 item in queue.</summary>
<div class="doc-comment">
<p>Smoke test: BatchScheduler flush with 1 item in queue.</p>
<p></p>
<p>NO MLX, NO model load. Mock execute callback (no-op async).</p>
</div>
</details>
</li>
<li><code>_check_public_surface_present</code> (live_result_sanity.py)
<details><summary>F221E: Check PUBLIC lane surface is present when public was attempted.</summary>
<div class="doc-comment">
<p>F221E: Check PUBLIC lane surface is present when public was attempted.</p>
<p></p>
<p>F221E: Canonical surfaces — uses acquisition_report as authoritative:</p>
<p>1. acquisition_report.public_terminal_state (canonical, set when PUBLIC was scheduled)</p>
<p>2. acquisition_report.source_family_outcomes PUBLIC entry</p>
<p>3. live_kpi.public_fetch_attempted / runtime_truth.public_branch_timed_out (legacy fallback)</p>
<p></p>
<p>Fails if public was attempted (canonical signal) but PUBLIC is absent from</p>
<p>source_family_outcomes.</p>
</div>
</details>
</li>
<li><code>compute_research_quality_score</code> (research_quality_score.py)</li>
<li><code>_extract_pptx</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Extract from PPTX with speaker notes and hidden slides.</span></li>
<li><code>_analyze_macros_olevba</code> (document_metadata_extractor.py)
<details><summary>Analyze macros using olevba for C2 URLs and suspicious API calls.</summary>
<div class="doc-comment">
<p>Analyze macros using olevba for C2 URLs and suspicious API calls.</p>
<p>Returns analysis results with threat indicators.</p>
</div>
</details>
</li>
<li><code>bench_msgspec_dto_serialization</code> (bench_m1_runtime_gates.py)
<details><summary>Measure msgspec encode/decode throughput for a CanonicalFinding-like DTO.</summary>
<div class="doc-comment">
<p>Measure msgspec encode/decode throughput for a CanonicalFinding-like DTO.</p>
<p></p>
<p>NO live DB writes. Uses msgspec.Convert (lightweight Struct).</p>
</div>
</details>
</li>
<li><code>_check_live_kpi_integrity</code> (f234_validate_nonfeed_live_report.py)
<details><summary>Validates live_kpi consistency:</summary>
<div class="doc-comment">
<p>Validates live_kpi consistency:</p>
<p>- total_findings == accepted_findings + rejected_findings</p>
<p>- run_quality_verdict in VALID_VERDICTS</p>
<p>- findings_per_min &gt;= 0</p>
<p></p>
<p>live_kpi is only stamped by benchmarks/live_sprint_measurement.py (live measurement</p>
<p>harness). Canonical nonfeed/diagnostic runs go through core.__main__.run_sprint()</p>
<p>and do NOT produce live_kpi — this is legitimate absence for non-live modes.</p>
</div>
</details>
</li>
<li><code>_run_hash_identifier_file</code> (bench_f214_python314_runtime.py)</li>
<li><code>_json_benchmark</code> (bench_f214_python314_runtime.py)</li>
<li><code>_extract_email</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Extract email headers with forensics analysis.</span></li>
<li><code>main</code> (migrate_dataclass_to_msgspec.py)</li>
<li><code>__init__</code> (url_dedup.py)</li>
<li><code>time_many</code> (bench_f214_python314_runtime.py)
<details><summary>Run fn repeatedly `runs` times after `warmups` warm-up runs.</summary>
<div class="doc-comment">
<p>Run fn repeatedly `runs` times after `warmups` warm-up runs.</p>
<p>Warm-up runs are NOT counted in the samples.</p>
<p></p>
<p>Returns:</p>
<p>status: "ok" | "fail"</p>
<p>samples_ms: list of per-run timings in ms</p>
<p>summary: summarize_samples output</p>
<p>warmups: number of warm-up runs performed</p>
</div>
</details>
</li>
<li><code>_detect_interpreter_flags</code> (bench_m1_runtime_gates.py) — <span class="doc-comment-inline">Detect free-threaded / JIT flags without importing heavy modules.</span></li>
<li><code>_load_kpi</code> (evidence_delta_memory.py)
<details><summary>Load and return the live_kpi sub-dict from a JSON report file.</summary>
<div class="doc-comment">
<p>Load and return the live_kpi sub-dict from a JSON report file.</p>
<p></p>
<p>Handles two structures:</p>
<p>- Full measurement JSON (probe_f208g style): top-level with live_kpi key</p>
<p>- Direct report JSON: top-level with findings/source info</p>
<p>Returns empty dict on failure.</p>
<p></p>
<p>F221E: Also preserves acquisition_report fields needed for attempted derivation:</p>
<p>- acquisition_report (canonical source for source_family_outcomes)</p>
<p>- public_terminal_stage</p>
<p>- ct_provider_status</p>
<p>- ct_terminal_state</p>
</div>
</details>
</li>
<li><code>_check_public_acceptance_kpi</code> (live_multisource_validator.py)
<details><summary>Validates F207K public acceptance KPI fields.</summary>
<div class="doc-comment">
<p>Validates F207K public acceptance KPI fields.</p>
<p></p>
<p>FAIL: acceptance_rate &lt; 1% with attempted &gt; 100</p>
<p>(systemic rejection — likely misconfiguration)</p>
<p>WARN: acceptance_rate &lt; 10% with attempted &gt; 50</p>
<p>(low yield — possible quality gate too strict)</p>
<p>WARN: next_action not in VALID_NEXT_ACTIONS</p>
<p>INFO: public_acceptance_* absent (non-public report)</p>
</div>
</details>
</li>
<li><code>_time_it</code> (bench_m1_runtime_gates.py)
<details><summary>Run fn `runs` times (after `warmups` uncounted warm-ups).</summary>
<div class="doc-comment">
<p>Run fn `runs` times (after `warmups` uncounted warm-ups).</p>
<p>Returns {wall_s, samples_ms, summary, status}.</p>
</div>
</details>
</li>
<li><code>_get_ct_public_info</code> (evidence_delta_memory.py)
<details><summary>Return (ct_attempted, public_attempted).</summary>
<div class="doc-comment">
<p>Return (ct_attempted, public_attempted).</p>
<p></p>
<p>F221E: Canonical priority — reads acquisition_report.source_family_outcomes first.</p>
<p>Falls back to live_kpi source_family_outcomes.</p>
<p></p>
<p>PUBLIC attempted=True when source_family_outcomes says so OR when</p>
<p>public_terminal_stage is set and not NOT_SCHEDULED (timeout/error are terminal attempts).</p>
<p></p>
<p>CT attempted=True when source_family_outcomes says so OR when terminality signals</p>
<p>a terminal CT outcome (provider_failure/cooldown/timeout).</p>
</div>
</details>
</li>
<li><code>main</code> (live_artifact_triage.py)</li>
<li><code>_scan_recursive</code> (content_miner.py)</li>
<li><code>_run_hash_identifier</code> (bench_f214_python314_runtime.py)</li>
<li><code>_check_provider_surface</code> (prelive_decision_gate.py)
<details><summary>Unified provider surface check with F217→F219 aliasing.</summary>
<div class="doc-comment">
<p>Unified provider surface check with F217→F219 aliasing.</p>
<p>Returns (missing_required_old_probes, warnings, checked_dict).</p>
<p></p>
<p>missing_required_old_probes: old probe names with no passing alias</p>
<p>warnings: for optional alias probes absent</p>
<p>checked_dict: for DecisionResult.checked_reports</p>
</div>
</details>
</li>
<li><code>find_gather_sites</code> (migrate_gather_to_safe_gather.py) — <span class="doc-comment-inline">Parse `path` and return all gather call sites (sorted by position).</span></li>
<li><code>execute_dns_tunnel_async</code> (executor.py) — <span class="doc-comment-inline">Async execution of DNS tunnel check in M1-safe manner.</span></li>
<li><code>_run_post_extraction_checks</code> (live_kpi_extraction_guard.py) — <span class="doc-comment-inline">Run post-extraction checks on live_measurement_kpi.py.</span></li>
<li><code>main</code> (prelive_decision_gate.py)</li>
<li><code>_extract_return_guard_checked</code> (live_multisource_validator.py)
<details><summary>Extract return_guard_checked from canonical aliases.</summary>
<div class="doc-comment">
<p>Extract return_guard_checked from canonical aliases.</p>
<p>Supported aliases (checked in order):</p>
<p>data["return_guard"]["return_guard_checked"]</p>
<p>data["return_guard"]["checked"]</p>
<p>data["acquisition_report"]["return_guard"]["return_guard_checked"]</p>
<p>data["canonical_run_summary"]["return_guard"]["return_guard_checked"]</p>
<p>data["live_kpi"]["return_guard_checked"]</p>
<p>data["return_guard_checked"]  (top-level)</p>
<p>Returns None if no alias resolves to a truthy value.</p>
</div>
</details>
</li>
<li><code>_extract_windup_guard_call_count</code> (live_multisource_validator.py)
<details><summary>Extract windup_guard_call_count from canonical aliases.</summary>
<div class="doc-comment">
<p>Extract windup_guard_call_count from canonical aliases.</p>
<p>Supported aliases (checked in order):</p>
<p>data["windup_guard_observation"]["windup_guard_call_count"]</p>
<p>data["windup_guard_observation"]["call_count"]</p>
<p>data["acquisition_report"]["windup_guard_observation"]["windup_guard_call_count"]</p>
<p>data["canonical_run_summary"]["windup_guard_observation"]["windup_guard_call_count"]</p>
<p>data["live_kpi"]["windup_guard_call_count"]</p>
<p>data["windup_guard_call_count"]  (top-level)</p>
<p>Returns None if no alias resolves to an int.</p>
</div>
</details>
</li>
<li><code>main</code> (research_quality_score.py)</li>
<li><code>fingerprint_url</code> (url_dedup.py)
<details><summary>Compute 64-bit fingerprint of URL using xxhash3-64.</summary>
<div class="doc-comment">
<p>Compute 64-bit fingerprint of URL using xxhash3-64.</p>
<p></p>
<p>Uses Rust implementation if available, falls back to Python xxhash/blake2b.</p>
<p></p>
<p>Args:</p>
<p>url: URL string to fingerprint</p>
<p></p>
<p>Returns:</p>
<p>64-bit unsigned integer fingerprint</p>
</div>
</details>
</li>
<li><code>_check_public_discovery_error_missing_reason</code> (f234_validate_nonfeed_live_report.py)
<details><summary>Exit 9: public DISCOVERY_ERROR without concrete discovery_empty_reason/provider surface.</summary>
<div class="doc-comment">
<p>Exit 9: public DISCOVERY_ERROR without concrete discovery_empty_reason/provider surface.</p>
<p></p>
<p>When public_terminal_stage == DISCOVERY_ERROR, the report must surface</p>
<p>either public_discovery_empty_reason or provider_errors (or both) so</p>
<p>the error is diagnosable — not silent.</p>
</div>
</details>
</li>
<li><code>bench_wal_manager_single_write_smoke</code> (bench_m1_runtime_gates.py)
<details><summary>Smoke test: WALManager.wal_write_finding() × 1 in a temp LMDB env.</summary>
<div class="doc-comment">
<p>Smoke test: WALManager.wal_write_finding() × 1 in a temp LMDB env.</p>
<p></p>
<p>NO live DuckDB writes. Uses tempfile for LMDB path.</p>
<p>Skips if imports fail due to missing deps (aiohttp, etc.).</p>
</div>
</details>
</li>
<li><code>extract</code> (document_metadata_extractor.py)
<details><summary>Extract FOCA-style forensic metadata from document.</summary>
<div class="doc-comment">
<p>Extract FOCA-style forensic metadata from document.</p>
<p></p>
<p>Args:</p>
<p>content: Raw document bytes</p>
<p>url: Source URL for extension detection</p>
<p></p>
<p>Returns:</p>
<p>Dict with keys: author, creator, organization, company, template_path,</p>
<p>last_modified_by, revision_count, internal_paths, gps_coords,</p>
<p>has_macros, macro_analysis, embedded_fonts, hidden_content,</p>
<p>email_headers, presentation_notes, cad_metadata, format</p>
</div>
</details>
</li>
<li><code>_check_kpi_from_input_bare_params</code> (live_kpi_extraction_guard.py)
<details><summary>_derive_live_kpi_from_input body must use inp.* for old params, not bare names.</summary>
<div class="doc-comment">
<p>_derive_live_kpi_from_input body must use inp.* for old params, not bare names.</p>
<p>Returns (has_bare_params, detail).</p>
</div>
</details>
</li>
<li><code>_extract_dxf</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Extract metadata from DXF files (CAD drawings).</span></li>
<li><code>add_batch</code> (url_dedup.py)
<details><summary>Bulk add items using round-robin slot selection.</summary>
<div class="doc-comment">
<p>Bulk add items using round-robin slot selection.</p>
<p></p>
<p>Returns:</p>
<p>List[bool] — True for each new item, False for duplicates.</p>
<p></p>
<p>On hit: prewarms the OTHER slot in the background (if enabled).</p>
</div>
</details>
</li>
<li><code>_check_ct_prelude_contradiction</code> (f234_validate_nonfeed_live_report.py)
<details><summary>Exit 8: CT prelude contradiction.</summary>
<div class="doc-comment">
<p>Exit 8: CT prelude contradiction.</p>
<p></p>
<p>Fails when ALL of these are true:</p>
<p>- 'CT' is in acquisition_prelude_missing_lanes (CT was expected but not attempted)</p>
<p>- ct_attempted_error is present/true (CT lower-case error marker exists)</p>
<p>- ct_prelude_missing_but_final_attempted is False or absent</p>
<p>The contradiction: CT prelude says CT was missing from planned lanes,</p>
<p>but ct_attempted_error signals a final attempt was made — and the</p>
<p>ct_prelude_missing_but_final_attempted flag doesn't explain this.</p>
</div>
</details>
</li>
<li><code>_time_it_async</code> (bench_m1_runtime_gates.py) — <span class="doc-comment-inline">Async version of _time_it.</span></li>
<li><code>_academic_search_handler</code> (executor.py)</li>
<li><code>_check_feed_only_accepted_nonfeed_attempted</code> (live_result_sanity.py)
<details><summary>F221E: Uses acquisition_report.source_family_outcomes as canonical.</summary>
<div class="doc-comment">
<p>F221E: Uses acquisition_report.source_family_outcomes as canonical.</p>
<p>Falls back to live_kpi.source_family_outcomes.</p>
<p></p>
<p>CT attempted=True when source_family_outcomes says so OR when ct_provider_status</p>
<p>or ct_terminal_state indicates a terminal outcome (provider_failure/cooldown/timeout).</p>
<p>PUBLIC attempted=True when source_family_outcomes says so OR when public_terminal_stage</p>
<p>is set and not NOT_SCHEDULED.</p>
</div>
</details>
</li>
<li><code>_extract_svg</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Extract metadata from SVG files.</span></li>
<li><code>_run_async_semaphore</code> (bench_f214_python314_runtime.py)</li>
<li><code>_check_parse_sprint_report_delegation</code> (live_measurement_extraction_guard.py)
<details><summary>Check that _parse_sprint_report in runner delegates to extracted parser.</summary>
<div class="doc-comment">
<p>Check that _parse_sprint_report in runner delegates to extracted parser.</p>
<p>The runner may have its own wrapper but it must call the extracted parse_sprint_report.</p>
<p>Returns (has_violation, message).</p>
</div>
</details>
</li>
<li><code>_check_f224_artifacts</code> (prelive_decision_gate.py)
<details><summary>Check F224 artifact presence and return (core_ready, warnings, missing_blocking, checked_dict).</summary>
<div class="doc-comment">
<p>Check F224 artifact presence and return (core_ready, warnings, missing_blocking, checked_dict).</p>
<p>core_ready = True when all blocking probes are present for blocking profiles.</p>
<p>warnings = list of warning messages for missing warning probes.</p>
<p>missing_blocking = list of missing blocking probe names (for reasons list).</p>
</div>
</details>
</li>
<li><code>_classify</code> (migrate_gather_to_safe_gather.py)
<details><summary>Classify the gather call.</summary>
<div class="doc-comment">
<p>Classify the gather call.</p>
<p></p>
<p>Returns (pattern, replacement, is_bug, is_nested).</p>
</div>
</details>
</li>
<li><code>_extract_odt</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Extract from ODT (OpenDocument Text).</span></li>
<li><code>_analyze_received_chain</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Analyze Received headers to build infrastructure chain.</span></li>
<li><code>_check_runtime_truth_termination</code> (f234_validate_nonfeed_live_report.py)
<details><summary>Validates runtime_truth branch timeout flags.</summary>
<div class="doc-comment">
<p>Validates runtime_truth branch timeout flags.</p>
<p>WARN if ct_branch_timed_out=True (partial results).</p>
<p>WARN if branch_timeout_count &gt; 0.</p>
<p>FAIL if is_meaningful=False AND accepted_findings &gt; 0 (contradiction).</p>
</div>
</details>
</li>
<li><code>_mine_with_traflatura</code> (content_miner.py)
<details><summary>Mine using traflatura in minimal mode.</summary>
<div class="doc-comment">
<p>Mine using traflatura in minimal mode.</p>
<p></p>
<p>Memory optimization:</p>
<p>- disable_comments: Don't store comment nodes</p>
<p>- no_tables: Skip table extraction (expensive)</p>
<p>- include_tables: False to save memory</p>
<p>- deduplicate: True to reduce memory</p>
</div>
</details>
</li>
<li><code>_check_duplicate_normalized_source_families</code> (f234_validate_nonfeed_live_report.py)
<details><summary>Exit 6: duplicate normalized source families (CT/ct, PUBLIC/public).</summary>
<div class="doc-comment">
<p>Exit 6: duplicate normalized source families (CT/ct, PUBLIC/public).</p>
<p></p>
<p>After normalization (lowercase), source_family_outcomes must not contain</p>
<p>both 'CT' and 'ct' or both 'PUBLIC' and 'public' — they represent the same</p>
<p>family and duplication indicates a data-production bug.</p>
</div>
</details>
</li>
<li><code>_check_scheduler_exit</code> (f234_validate_nonfeed_live_report.py)
<details><summary>Validates scheduler_exit subobject.</summary>
<div class="doc-comment">
<p>Validates scheduler_exit subobject.</p>
<p>FAIL if exit_path not in EXPECTED_EXIT_PATHS.</p>
<p>WARN if elapsed_s &gt; 300 (5min timeout threshold).</p>
</div>
</details>
</li>
<li><code>main</code> (f234_validate_nonfeed_live_report.py)</li>
<li><code>_topk_benchmark</code> (bench_f214_python314_runtime.py)</li>
<li><code>_check_render_md_delegation</code> (live_measurement_extraction_guard.py)
<details><summary>Check that _render_md delegates to the extracted markdown module.</summary>
<div class="doc-comment">
<p>Check that _render_md delegates to the extracted markdown module.</p>
<p>Returns (has_violation, message).</p>
</div>
</details>
</li>
<li><code>_find_matching_paren</code> (migrate_gather_to_safe_gather.py)
<details><summary>Return index of matching `)`, or -1 if unbalanced.</summary>
<div class="doc-comment">
<p>Return index of matching `)`, or -1 if unbalanced.</p>
<p></p>
<p>Skips over string literals and char literals.</p>
</div>
</details>
</li>
<li><code>_run_pre_extraction_checks_inner</code> (live_kpi_extraction_guard.py)
<details><summary>Core pre-extraction checks shared between standalone and post-extraction.</summary>
<div class="doc-comment">
<p>Core pre-extraction checks shared between standalone and post-extraction.</p>
<p></p>
<p>In post-extraction mode (post_extraction=True), the runner no longer owns</p>
<p>LiveKpiInput, _derive_live_kpi, or _derive_live_kpi_from_input — they live in</p>
<p>the extracted KPI module. Skip those checks so pre-extraction verdicts don't</p>
<p>override the more specific post-extraction verdicts (runtime import, exports).</p>
</div>
</details>
</li>
<li><code>extract_file_refs</code> (qoder_reality_check.py)
<details><summary>Extract all file references from a markdown document.</summary>
<div class="doc-comment">
<p>Extract all file references from a markdown document.</p>
<p></p>
<p>Returns:</p>
<p>Dict with keys 'file_links', 'md_links', 'code_paths', 'module_names'</p>
</div>
</details>
</li>
<li><code>_extract_image</code> (content_miner.py) — <span class="doc-comment-inline">Extract metadata from images (EXIF).</span></li>
<li><code>_process_file</code> (content_miner.py)</li>
<li><code>_check_quality_gate_not_zeroed</code> (f234_validate_nonfeed_live_report.py)
<details><summary>quality_gate can be QUALITY_FAIL_FEED_ONLY but counts must not be zeroed.</summary>
<div class="doc-comment">
<p>quality_gate can be QUALITY_FAIL_FEED_ONLY but counts must not be zeroed.</p>
<p></p>
<p>This is the key F232 fix: FEED_ONLY gate must still preserve real counts.</p>
</div>
</details>
</li>
<li><code>_check_shadowed_helpers</code> (live_measurement_extraction_guard.py)</li>
<li><code>_check_next_action_wired_from_module</code> (live_kpi_extraction_guard.py)
<details><summary>live_sprint_measurement.py must import _derive_next_action from</summary>
<div class="doc-comment">
<p>live_sprint_measurement.py must import _derive_next_action from</p>
<p>live_measurement_next_action, not locally own it.</p>
</div>
</details>
</li>
<li><code>_memory_snapshot</code> (bench_f214_python314_runtime.py)</li>
<li><code>_is_thin_delegation</code> (live_measurement_extraction_guard.py)
<details><summary>Return True if the function body is a thin delegation alias:</summary>
<div class="doc-comment">
<p>Return True if the function body is a thin delegation alias:</p>
<p>- optional docstring</p>
<p>- exactly one remaining statement: Return(Call(...))</p>
<p>- call target may be ast.Name or ast.Attribute (e.g. _qm._helper)</p>
<p>- no other logic allowed</p>
</div>
</details>
</li>
<li><code>score_research_quality</code> (research_quality_score.py)
<details><summary>Compute research quality score from a benchmark or live KPI dict.</summary>
<div class="doc-comment">
<p>Compute research quality score from a benchmark or live KPI dict.</p>
<p></p>
<p>This is the canonical import-safe entry point for live_sprint_measurement.py.</p>
<p>No network, no MLX — pure scoring from the data dict already captured.</p>
<p></p>
<p>Returns a dict with all required quality surface fields:</p>
<p>- total_quality_score: float (0-100)</p>
<p>- grade: str ("FEED_ONLY", "MULTISOURCE_SHALLOW", "MULTISOURCE_USEFUL", "DEEP_RESEARCH_READY")</p>
<p>- quality_gate: str — QUALITY_PASS | QUALITY_FAIL_FEED_ONLY | QUALITY_FAIL_HARDWARE_TAINTED | QUALITY_FAIL_NONFEED_ZERO | QUALITY_WARN_MULTISOURCE_SHALLOW  # noqa: E501</p>
<p>- research_quality_comparable: bool — False when hardware_constrained or swap_gib &gt;= 3.0</p>
<p>- components: dict of component scores</p>
<p>- diagnostic_flags: dict (wallclock_exceeded, swap_gib, swap_warning, hardware_constrained, claims_extracted, ct_quarantine_count, ct_quarantine_without_loss)  # noqa: E501</p>
<p>- feed_dominance_score: float (0-1, penalty applied)</p>
<p>- swap_gib: float | None — post-sprint swap in GiB</p>
<p>- swap_warning: bool — memory pressure signal</p>
<p>- hardware_constrained: bool — from live_kpi hardware_constrained field</p>
<p>- evidence_depth: dict — F231M production evidence depth diagnostics</p>
</div>
</details>
</li>
<li><code>_extract_pdf_gps</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Extract GPS coordinates from embedded images.</span></li>
<li><code>_extract_strings_from_json</code> (content_miner.py)
<details><summary>Recursively extract string values from JSON that look like content (20-300 chars).</summary>
<div class="doc-comment">
<p>Recursively extract string values from JSON that look like content (20-300 chars).</p>
<p></p>
<p>Args:</p>
<p>obj: JSON object (dict, list, or primitive)</p>
<p>min_len: Minimum string length</p>
<p>max_len: Maximum string length</p>
<p>max_depth: Maximum recursion depth</p>
<p>current_depth: Current recursion depth</p>
<p></p>
<p>Returns:</p>
<p>List of extracted strings</p>
</div>
</details>
</li>
<li><code>_run_all</code> (bench_f214_python314_runtime.py) — <span class="doc-comment-inline">Run all benchmarks in a single event loop.</span></li>
<li><code>_run_main</code> (bench_f214_python314_runtime.py)</li>
<li><code>_ensure_imports</code> (migrate_gather_to_safe_gather.py)
<details><summary>Add a `from utils.async_helpers import ...` line if not present.</summary>
<div class="doc-comment">
<p>Add a `from utils.async_helpers import ...` line if not present.</p>
<p></p>
<p>Idempotent: re-running is a no-op.</p>
</div>
</details>
</li>
<li><code>_coerce_feed_dominance_score</code> (research_quality_score.py)
<details><summary>Coerce feed_dominance_score to numeric, fail-safe.</summary>
<div class="doc-comment">
<p>Coerce feed_dominance_score to numeric, fail-safe.</p>
<p></p>
<p>Returns 1.0 when:</p>
<p>- value is None, NaN, or non-numeric</p>
<p>- total_findings is 0 or negative</p>
<p>- feed/total ratio is unavailable</p>
<p></p>
<p>Otherwise returns feed/total clamped to [0.0, 1.0].</p>
</div>
</details>
</li>
<li><code>_resolve_ct_loss_stage_from_acquisition</code> (research_quality_score.py)
<details><summary>Resolve ct_loss_stage from acquisition_report when runtime_truth.lane_verdict.ct_loss_stage is missing.</summary>
<div class="doc-comment">
<p>Resolve ct_loss_stage from acquisition_report when runtime_truth.lane_verdict.ct_loss_stage is missing.</p>
<p></p>
<p>Maps provider errors and terminal stages to canonical ct_loss_stage values.</p>
</div>
</details>
</li>
<li><code>_parse_exif_gps</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Parse GPS from EXIF data.</span></li>
<li><code>_extract_pptx_hidden_slides</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Extract hidden slides from PPTX.</span></li>
<li><code>to_md</code> (live_result_sanity.py)</li>
<li><code>_extract_docx_revisions</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Extract revision history from DOCX (track changes).</span></li>
<li><code>__init__</code> (url_dedup.py)</li>
<li><code>_extract_pdf</code> (content_miner.py) — <span class="doc-comment-inline">Extract metadata from PDF.</span></li>
<li><code>main</code> (bench_f214_python314_runtime.py)</li>
<li><code>_render_self_test_markdown</code> (prelive_one_button_gate.py) — <span class="doc-comment-inline">Render self-test result as markdown.</span></li>
<li><code>_check_research_quality</code> (live_result_sanity.py)
<details><summary>Check research quality gate.</summary>
<div class="doc-comment">
<p>Check research quality gate.</p>
<p></p>
<p>Fails if:</p>
<p>- quality_gate is missing (None)</p>
<p>- quality_gate is QUALITY_FAIL_FEED_ONLY and not allow_feed_only</p>
<p>- quality_gate is any other QUALITY_FAIL_* (always fail)</p>
<p>- grade is below min_grade threshold (even for warnings)</p>
<p></p>
<p>Passes (with warning) for QUALITY_WARN_MULTISOURCE_SHALLOW only when above min_grade.</p>
</div>
</details>
</li>
<li><code>create_mmap_bloom_filter</code> (url_dedup.py)</li>
<li><code>normalize_url_parallel</code> (url_dedup.py)
<details><summary>Batch URL normalization — parallel Rust rayon for large batches.</summary>
<div class="doc-comment">
<p>Batch URL normalization — parallel Rust rayon for large batches.</p>
<p></p>
<p>Uses Rust ``rust_canonicalize_batch`` (rayon-parallel, M1 NEON-accelerated)</p>
<p>if available, falls back to Python urlencode.</p>
<p>Threshold: ≥256 items → Rust batch; &lt;256 → sequential.</p>
<p></p>
<p>M1 8GB safe: pure Python work, no GPU, no additional memory allocation</p>
<p>beyond the input list and result list.</p>
<p></p>
<p>Returns:</p>
<p>List of normalized URL strings in same order as input;</p>
<p>if ``normalize=False``, returns original URLs unchanged.</p>
</div>
</details>
</li>
<li><code>_check_f223_artifact</code> (prelive_one_button_gate.py) — <span class="doc-comment-inline">Check a single F223 probe artifact, trying alias paths if primary is missing.</span></li>
<li><code>main</code> (live_measurement_extraction_guard.py)</li>
<li><code>main</code> (live_kpi_extraction_guard.py)</li>
<li><code>format_report</code> (migrate_dataclass_to_msgspec.py)</li>
<li><code>check_and_add_batch</code> (url_dedup.py)
<details><summary>Atomic check-and-add batch — returns (seen_before, is_new) per item.</summary>
<div class="doc-comment">
<p>Atomic check-and-add batch — returns (seen_before, is_new) per item.</p>
<p></p>
<p>Canonical cross-process dedup primitive: distinguishes true negatives</p>
<p>(seen_before=False, is_new=True → fresh, first time ever seen)</p>
<p>from false positives (seen_before=True, is_new=False → deduped).</p>
<p></p>
<p>Args:</p>
<p>items: List of URL/fingerprint strings to add</p>
<p></p>
<p>Returns:</p>
<p>List[(seen_before, is_new)] — per item:</p>
<p>- seen_before: True if item was already in filter BEFORE this call</p>
<p>- is_new:      True if item was NOT in filter after this call</p>
<p></p>
<p>Uses Rust check_and_add_batch (parallel xxHash3-64, rayon-powered).</p>
<p>Single msync at the end. Thread-safe via threading.Lock.</p>
</div>
</details>
</li>
<li><code>_check_acquisition_profile</code> (f234_validate_nonfeed_live_report.py) — <span class="doc-comment-inline">Check acquisition_profile == 'nonfeed_diagnostic'.</span></li>
<li><code>_build_record</code> (bench_m1_runtime_gates.py)</li>
<li><code>_get_sfo_canonical</code> (live_result_sanity.py)
<details><summary>F221E: Return canonical source_family_outcomes list.</summary>
<div class="doc-comment">
<p>F221E: Return canonical source_family_outcomes list.</p>
<p></p>
<p>Priority:</p>
<p>1. acquisition_report.source_family_outcomes (canonical)</p>
<p>2. acquisition_report.live_kpi.source_family_outcomes (legacy wrap)</p>
<p>3. live_kpi.source_family_outcomes (live_kpi direct)</p>
<p></p>
<p>Returns [] if none available.</p>
</div>
</details>
</li>
<li><code>_check_ct_loss_stage_present</code> (live_result_sanity.py)
<details><summary>F214R2: Check CT loss telemetry is present when CT lane has raw evidence but zero accepted.</summary>
<div class="doc-comment">
<p>F214R2: Check CT loss telemetry is present when CT lane has raw evidence but zero accepted.</p>
<p></p>
<p>Fails if:</p>
<p>- CT raw_count &gt; 0 and accepted_count == 0 but ct_loss_stage is missing from live_kpi.</p>
</div>
</details>
</li>
<li><code>build_risks</code> (qoder_reality_check.py) — <span class="doc-comment-inline">Build risk list for a module.</span></li>
<li><code>_mine_with_trafilex</code> (content_miner.py)
<details><summary>Mine using trafilex (Rust-based, minimal memory).</summary>
<div class="doc-comment">
<p>Mine using trafilex (Rust-based, minimal memory).</p>
<p></p>
<p>trafilex is a Rust wrapper that processes HTML in streaming mode</p>
<p>without building large DOM trees.</p>
</div>
</details>
</li>
<li><code>_config_import_benchmark</code> (bench_f214_python314_runtime.py)</li>
<li><code>_async_semaphore_impl</code> (bench_f214_python314_runtime.py)</li>
<li><code>_resolve_branch_count</code> (live_multisource_validator.py)
<details><summary>Resolve branch count from branch_mix aliases with live_kpi fallback.</summary>
<div class="doc-comment">
<p>Resolve branch count from branch_mix aliases with live_kpi fallback.</p>
<p>Resolves these aliases for feed/public/ct keys:</p>
<p>branch_mix["feed"]           → feed_count</p>
<p>branch_mix["feed_findings"]  → feed_count  (benchmark shape)</p>
<p>branch_mix["public_findings"]→ public_count (benchmark shape)</p>
<p>branch_mix["ct_findings"]    → ct_count     (benchmark shape)</p>
<p>live_kpi["source_family_counts"]["feed"] → feed_count (live_kpi shape)</p>
</div>
</details>
</li>
<li><code>parse_args</code> (migrate_dataclass_to_msgspec.py)</li>
<li><code>_check_required_exports</code> (live_measurement_extraction_guard.py)
<details><summary>Check that a module exports the required symbols.</summary>
<div class="doc-comment">
<p>Check that a module exports the required symbols.</p>
<p>Returns (all_present, list_of_missing).</p>
</div>
</details>
</li>
<li><code>_detect_terminality_source_outcome_mismatch</code> (live_multisource_validator.py)
<details><summary>Detect lanes that appear terminal/attempted in source_family_outcomes</summary>
<div class="doc-comment">
<p>Detect lanes that appear terminal/attempted in source_family_outcomes</p>
<p>but are still listed in missing_lanes.</p>
<p></p>
<p>A mismatch means the acquisition terminality snapshot is stale — the lane</p>
<p>was resolved at execution time (source_family_outcomes reflects reality)</p>
<p>but the terminality record still lists it as missing.</p>
<p></p>
<p>Returns a list of mismatched lane names.</p>
</div>
</details>
</li>
<li><code>_check_kpi_module_exports</code> (live_kpi_extraction_guard.py) — <span class="doc-comment-inline">Check required exports from live_measurement_kpi.py. Returns (missing_any, list_missing).</span></li>
<li><code>mine_html</code> (content_miner.py)
<details><summary>Extract clean text from HTML with minimal memory usage.</summary>
<div class="doc-comment">
<p>Extract clean text from HTML with minimal memory usage.</p>
<p></p>
<p>Args:</p>
<p>html_content: Raw HTML content</p>
<p>url: Source URL for metadata</p>
<p>include_metadata: Extract metadata (title, author, date)</p>
<p></p>
<p>Returns:</p>
<p>MiningResult with extracted content</p>
</div>
</details>
</li>
<li><code>_extract_links_selectolax</code> (content_miner.py) — <span class="doc-comment-inline">Extract links using selectolax (fast, safe CSS selectors).</span></li>
<li><code>discover_feeds</code> (content_miner.py)
<details><summary>Discover RSS/Atom feeds in HTML content.</summary>
<div class="doc-comment">
<p>Discover RSS/Atom feeds in HTML content.</p>
<p></p>
<p>Args:</p>
<p>html_content: Raw HTML content</p>
<p>base_url: Base URL for resolving relative links</p>
<p></p>
<p>Returns:</p>
<p>FeedDiscoveryResult with discovered feed URLs</p>
</div>
</details>
</li>
<li><code>_check_public_discovery_empty_reason</code> (f234_validate_nonfeed_live_report.py) — <span class="doc-comment-inline">public_discovery_empty_reason present when public accepted=0.</span></li>
<li><code>_check_acquisition_terminality</code> (f234_validate_nonfeed_live_report.py)
<details><summary>Validates acquisition_report.terminality subobject.</summary>
<div class="doc-comment">
<p>Validates acquisition_report.terminality subobject.</p>
<p>Exit 1 if: terminality.checked=True AND satisfied=False</p>
<p>Exit 1 if: missing_lanes is non-empty list</p>
<p>INFO if: terminality absent (pre-F208 report)</p>
</div>
</details>
</li>
<li><code>_extract_terminality_fields</code> (live_multisource_validator.py) — <span class="doc-comment-inline">Extract terminality fields from top-level, acquisition_report.terminality, or live_kpi fallback.</span></li>
<li><code>_get_branch_accepted</code> (evidence_delta_memory.py)
<details><summary>Get accepted counts per branch from a KPI dict.</summary>
<div class="doc-comment">
<p>Get accepted counts per branch from a KPI dict.</p>
<p></p>
<p>Returns {family: accepted_count}. Handles multiple structural variants.</p>
</div>
</details>
</li>
<li><code>_extract_docx_extended_props</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Extract extended properties from DOCX ZIP.</span></li>
<li><code>_zero_findings_quality_sane</code> (prelive_decision_gate.py)
<details><summary>Check zero-findings quality probe does NOT crash and fails correctly.</summary>
<div class="doc-comment">
<p>Check zero-findings quality probe does NOT crash and fails correctly.</p>
<p>Returns (sane, detail).</p>
</div>
</details>
</li>
<li><code>_normalize_live</code> (research_quality_score.py) — <span class="doc-comment-inline">Convert live_active300 JSON format to normalized internal dict.</span></li>
<li><code>_check_kpi_compat_wrapper</code> (live_kpi_extraction_guard.py)
<details><summary>_derive_live_kpi compatibility wrapper must exist and accept the flat param list.</summary>
<div class="doc-comment">
<p>_derive_live_kpi compatibility wrapper must exist and accept the flat param list.</p>
<p>Wrapper builds LiveKpiInput and delegates to _derive_live_kpi_from_input.</p>
</div>
</details>
</li>
<li><code>_extract_pdf_fallback</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Fallback PDF extraction without PyMuPDF.</span></li>
<li><code>_extract_pdf_fonts</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Extract embedded fonts for geographic indication.</span></li>
<li><code>put_many</code> (url_dedup.py)
<details><summary>Bulk add items to the mmap-backed filter.</summary>
<div class="doc-comment">
<p>Bulk add items to the mmap-backed filter.</p>
<p></p>
<p>Args:</p>
<p>items: List of URL/fingerprint strings to add</p>
<p></p>
<p>Returns:</p>
<p>List[bool] — True for each new item, False for duplicates.</p>
<p></p>
<p>Uses Rust add_batch (parallel xxHash3-64, rayon-powered).</p>
<p>Single msync at the end amortizes sync overhead.</p>
<p>Thread-safe via threading.Lock.</p>
</div>
</details>
</li>
<li><code>_check_runtime_budget_guard</code> (f234_validate_nonfeed_live_report.py)
<details><summary>NOTE R1: budget_violations and return_guard_block_reason surfaced from scheduler runtime.</summary>
<div class="doc-comment">
<p>NOTE R1: budget_violations and return_guard_block_reason surfaced from scheduler runtime.</p>
<p></p>
<p>budget_violations &gt; 0 indicates sprint exceeded resource budget.</p>
<p>return_guard_block_reason non-empty indicates why sprint return was blocked.</p>
<p>Both are advisory telemetry — non-zero values produce a warning but do not fail validation.</p>
</div>
</details>
</li>
<li><code>_is_provider_surface_ok</code> (prelive_one_button_gate.py) — <span class="doc-comment-inline">Check provider surface is OK from decision gate data.</span></li>
<li><code>_check_module_imports_runtime</code> (live_measurement_extraction_guard.py)
<details><summary>Check if a module imports any runtime/problematic prefixes.</summary>
<div class="doc-comment">
<p>Check if a module imports any runtime/problematic prefixes.</p>
<p>Returns (has_violation, first_violation_message).</p>
</div>
</details>
</li>
<li><code>main</code> (live_multisource_validator.py)</li>
<li><code>run_guard</code> (live_kpi_extraction_guard.py)
<details><summary>Run the full guard.</summary>
<div class="doc-comment">
<p>Run the full guard.</p>
<p>Phase=pre (no KPI module yet): run pre-extraction checks.</p>
<p>Phase=post (KPI module exists): also run post-extraction checks.</p>
</div>
</details>
</li>
<li><code>_clean_html_basic</code> (content_miner.py)
<details><summary>Basic HTML cleaning — delegates to canonical html_text_fast when available.</summary>
<div class="doc-comment">
<p>Basic HTML cleaning — delegates to canonical html_text_fast when available.</p>
<p></p>
<p>Uses module-level compiled patterns as emergency fallback</p>
<p>when canonical helper is unavailable.</p>
</div>
</details>
</li>
<li><code>_check_return_guard</code> (f234_validate_nonfeed_live_report.py)
<details><summary>FAIL if return_guard.checked=True AND satisfied=False</summary>
<div class="doc-comment">
<p>FAIL if return_guard.checked=True AND satisfied=False</p>
<p>AND block_reason is not null (hard block → report invalid).</p>
</div>
</details>
</li>
<li><code>_check_schema_classes_not_in_runner</code> (live_measurement_extraction_guard.py)
<details><summary>Check that schema classes are NOT defined in live_sprint_measurement.py.</summary>
<div class="doc-comment">
<p>Check that schema classes are NOT defined in live_sprint_measurement.py.</p>
<p>Returns (has_violation, list_of_found_classes).</p>
</div>
</details>
</li>
<li><code>_check_f224_confidence_policy</code> (prelive_decision_gate.py)
<details><summary>Check F224D confidence policy via canonical path and aliases.</summary>
<div class="doc-comment">
<p>Check F224D confidence policy via canonical path and aliases.</p>
<p>Gate passes if any canonical/alias artifact exists and _is_pass() returns True.</p>
<p>Gate blocks if all are missing or all are failing.</p>
<p>Returns (pass, detail, checked_dict).</p>
</div>
</details>
</li>
<li><code>_extract_evidence_depth_inputs</code> (research_quality_score.py) — <span class="doc-comment-inline">F231M: Extract evidence depth inputs from live_kpi, resolving all F231A/B/C aliases.</span></li>
<li><code>quality_gate_verdict</code> (research_quality_score.py)
<details><summary>Determine quality gate verdict from research quality score components.</summary>
<div class="doc-comment">
<p>Determine quality gate verdict from research quality score components.</p>
<p></p>
<p>Verdict priority:</p>
<p>1. HARDWARE_TAINTED — hardware_constrained or heavy swap taints comparability</p>
<p>2. FEED_ONLY — grade is FEED_ONLY (not research; nonfeed_findings is always 0)</p>
<p>3. NONFEED_ZERO — nonfeed findings are zero despite attempting nonfeed sources</p>
<p>4. MULTISOURCE_SHALLOW — grade is MULTISOURCE_SHALLOW (warn, not fail)</p>
<p>5. QUALITY_PASS — all gates passed</p>
</div>
</details>
</li>
<li><code>create_cross_process_bloom_filter</code> (url_dedup.py)</li>
<li><code>_check_nonfeed_priority</code> (f234_validate_nonfeed_live_report.py) — <span class="doc-comment-inline">nonfeed_priority_enabled is true OR explicit skip reason present.</span></li>
<li><code>_hash_identifier_impl</code> (bench_f214_python314_runtime.py)</li>
<li><code>_check_runner_imports_schema</code> (live_measurement_extraction_guard.py)
<details><summary>Check that live_sprint_measurement.py imports from the schema module.</summary>
<div class="doc-comment">
<p>Check that live_sprint_measurement.py imports from the schema module.</p>
<p>Returns (imports_correctly, message).</p>
</div>
</details>
</li>
<li><code>main</code> (live_result_sanity.py)</li>
<li><code>_normalize_benchmark</code> (research_quality_score.py) — <span class="doc-comment-inline">Convert benchmark JSON format to normalized internal dict.</span></li>
<li><code>_init_cache</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Initialize SQLite cache.</span></li>
<li><code>_extract_docx_fallback</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Fallback DOCX extraction without python-docx.</span></li>
<li><code>_mine_with_fallback</code> (content_miner.py)
<details><summary>Ultimate fallback using regex-based extraction.</summary>
<div class="doc-comment">
<p>Ultimate fallback using regex-based extraction.</p>
<p></p>
<p>No dependencies - pure Python regex for maximum compatibility.</p>
</div>
</details>
</li>
<li><code>_check_f231_artifacts</code> (prelive_decision_gate.py)
<details><summary>Check F231 Evidence Lift Pack artifact presence.</summary>
<div class="doc-comment">
<p>Check F231 Evidence Lift Pack artifact presence.</p>
<p>Returns (core_ready, warnings, missing_blocking, checked_dict).</p>
<p>core_ready = True when all F231 blocking probes are present for blocking profiles.</p>
</div>
</details>
</li>
<li><code>_extract_source_family_outcomes</code> (live_multisource_validator.py)
<details><summary>Extract source_family_outcomes from acquisition_report, with top-level and live_kpi fallback.</summary>
<div class="doc-comment">
<p>Extract source_family_outcomes from acquisition_report, with top-level and live_kpi fallback.</p>
<p></p>
<p>Resolves these locations in priority order:</p>
<p>1. acq_report["source_family_outcomes"]           (internal shape)</p>
<p>2. data["source_family_outcomes"]                 (top-level / benchmark shape)</p>
<p>3. live_kpi["source_family_outcomes"]             (live_kpi fallback)</p>
</div>
</details>
</li>
<li><code>parse_quality</code> (live_result_sanity.py)</li>
<li><code>_extract_xlsx_fallback</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Fallback XLSX extraction without openpyxl.</span></li>
<li><code>_extract_msg</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Extract from Outlook MSG files.</span></li>
<li><code>fast_hash</code> (url_dedup.py)
<details><summary>Fast non-crypto hash for URL fingerprinting.</summary>
<div class="doc-comment">
<p>Fast non-crypto hash for URL fingerprinting.</p>
<p></p>
<p>Uses Rust xxhash3-64 (SIMD NEON on M1) if available,</p>
<p>falls back to Python xxhash, then blake2b.</p>
<p>xxhash is NOT cryptographically safe — use only for deduplication.</p>
</div>
</details>
</li>
<li><code>fast_hash_parallel</code> (url_dedup.py)
<details><summary>Batch fast hash — parallel ThreadPoolExecutor for large batches.</summary>
<div class="doc-comment">
<p>Batch fast hash — parallel ThreadPoolExecutor for large batches.</p>
<p></p>
<p>Uses xxhash (10x faster) if available, falls back to blake2b.</p>
<p>Threshold: ≥256 items → parallel (4 workers); &lt;256 → sequential.</p>
<p></p>
<p>M1 8GB safe: pure Python work, no GPU, no additional memory allocation</p>
<p>beyond the input list and result list (in-place compatible).</p>
<p></p>
<p>Returns:</p>
<p>List of hexdigest strings in same order as input.</p>
</div>
</details>
</li>
<li><code>_check_ct_terminal_stage</code> (f234_validate_nonfeed_live_report.py) — <span class="doc-comment-inline">ct_terminal_stage present when ct accepted=0.</span></li>
<li><code>summarize_samples</code> (bench_f214_python314_runtime.py)
<details><summary>Compute min / median / mean / p95 / max from a list of sample times in ms.</summary>
<div class="doc-comment">
<p>Compute min / median / mean / p95 / max from a list of sample times in ms.</p>
<p>Returns: min_ms, median_ms, mean_ms, p95_ms, max_ms, runs</p>
</div>
</details>
</li>
<li><code>_check_all_f223_artifacts</code> (prelive_one_button_gate.py)
<details><summary>Check all F223 artifacts using alias resolution. Returns (required_results, required_missing, optional_results).</summary>
<div class="doc-comment">
<p>Check all F223 artifacts using alias resolution. Returns (required_results, required_missing, optional_results).</p>
<p>Required missing blocks RUN_NOW / RESTART_THEN_RUN.</p>
</div>
</details>
</li>
<li><code>_derive_logical_name</code> (prelive_one_button_gate.py) — <span class="doc-comment-inline">Derive logical artifact name from probe directory.</span></li>
<li><code>_extract_guard_fields</code> (live_multisource_validator.py)
<details><summary>Extract windup/return guard fields from benchmark or internal shape.</summary>
<div class="doc-comment">
<p>Extract windup/return guard fields from benchmark or internal shape.</p>
<p></p>
<p>Uses F208K alias helpers to resolve all canonical nested locations.</p>
<p>Returns (windup_count, windup_reason, windup_not_applicable, return_guard_checked, scheduler_exit).</p>
</div>
</details>
</li>
<li><code>_close_dns_tunnel_executor</code> (executor.py) — <span class="doc-comment-inline">Close DNS tunnel executor. Call on shutdown.</span></li>
<li><code>parse_benchmark</code> (live_result_sanity.py)</li>
<li><code>_terminality_satisfied_from_report</code> (live_artifact_triage.py)
<details><summary>F223B SSOT: Read terminality satisfied directly from acquisition_report.terminality.satisfied.</summary>
<div class="doc-comment">
<p>F223B SSOT: Read terminality satisfied directly from acquisition_report.terminality.satisfied.</p>
<p></p>
<p>Priority:</p>
<p>1. acquisition_report.terminality.satisfied (canonical SSOT)</p>
<p>2. acquisition_report.terminality (boolean, backwards compat)</p>
<p>3. None if not available</p>
</div>
</details>
</li>
<li><code>analyze_file</code> (migrate_dataclass_to_msgspec.py) — <span class="doc-comment-inline">Analyze a single file and return migration results.</span></li>
<li><code>detect_high_risk_gaps</code> (qoder_reality_check.py) — <span class="doc-comment-inline">Detect high-risk architectural gaps.</span></li>
<li><code>_extract_pptx_speaker_notes</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Extract speaker notes from PPTX.</span></li>
<li><code>_extract_vba_code</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Extract VBA code from Office documents.</span></li>
<li><code>_extract_imports_ast</code> (content_miner.py) — <span class="doc-comment-inline">Extract imports using AST.</span></li>
<li><code>_check_schema_version</code> (f234_validate_nonfeed_live_report.py)
<details><summary>Validates schema_version field presence and known version.</summary>
<div class="doc-comment">
<p>Validates schema_version field presence and known version.</p>
<p>WARN if absent (pre-F208 report).</p>
<p>INFO if known but old version.</p>
</div>
</details>
</li>
<li><code>_check_public_fetch_telemetry</code> (live_multisource_validator.py)
<details><summary>Validates public fetch attempted vs acceptance ratio.</summary>
<div class="doc-comment">
<p>Validates public fetch attempted vs acceptance ratio.</p>
<p>Catches: fetch_attempted &gt;&gt; acceptance_attempted</p>
<p>(fetch succeeds but acceptance gate drops everything)</p>
</div>
</details>
</li>
<li><code>_build_replacement</code> (migrate_gather_to_safe_gather.py)
<details><summary>Build the source text that replaces `asyncio.gather(...)` at `site`.</summary>
<div class="doc-comment">
<p>Build the source text that replaces `asyncio.gather(...)` at `site`.</p>
<p></p>
<p>Returns the right-hand side of the new call, e.g. `safe_gather_ok(coro1(), coro2(), label="foo")`.</p>
</div>
</details>
</li>
<li><code>_get_source_families</code> (evidence_delta_memory.py) — <span class="doc-comment-inline">Extract family names from source_family_outcomes or source_family_counts.</span></li>
<li><code>main</code> (evidence_delta_memory.py)</li>
<li><code>render_collision_warning</code> (prelive_artifact_cockpit.py) — <span class="doc-comment-inline">Render collision warnings as markdown lines.</span></li>
<li><code>parse_quality_with_fallback</code> (live_result_sanity.py)
<details><summary>F215A: Parse quality surface, falling back to embedded research_quality from</summary>
<div class="doc-comment">
<p>F215A: Parse quality surface, falling back to embedded research_quality from</p>
<p>benchmark live_kpi when no explicit quality_json is provided.</p>
<p></p>
<p>Priority:</p>
<p>1. raw has quality_gate → use it</p>
<p>2. raw is empty + fallback has quality_gate → use fallback (embedded in benchmark)</p>
<p>3. raw is empty + fallback empty → N/A (quality_gate=None, no gate applied)</p>
<p>4. raw non-empty but quality_gate missing → malformed (quality_gate=None → fail)</p>
</div>
</details>
</li>
<li><code>_compute_evidence_depth</code> (research_quality_score.py) — <span class="doc-comment-inline">F231M: Compute evidence depth diagnostics from normalized KPI inputs.</span></li>
<li><code>_check_module_imports_runtime</code> (live_kpi_extraction_guard.py) — <span class="doc-comment-inline">Check if a module imports any runtime prefix. Returns (has_violation, message).</span></li>
<li><code>_get_cached</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Get cached extraction result.</span></li>
<li><code>_cache</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Cache extraction result.</span></li>
<li><code>_extract_company_from_pdf_producer</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Extract company name from PDF producer string.</span></li>
<li><code>mine_text</code> (content_miner.py)
<details><summary>Mine plain text (minimal processing).</summary>
<div class="doc-comment">
<p>Mine plain text (minimal processing).</p>
<p></p>
<p>Args:</p>
<p>text: Plain text content</p>
<p>url: Source URL</p>
<p></p>
<p>Returns:</p>
<p>MiningResult with cleaned text</p>
</div>
</details>
</li>
<li><code>_extract</code> (content_miner.py)</li>
<li><code>_extract_acquisition_report</code> (live_multisource_validator.py) — <span class="doc-comment-inline">Extract acquisition_report — prefer top-level, fall back to nested in live_kpi.</span></li>
<li><code>_get_ct_loss_stage</code> (evidence_delta_memory.py)
<details><summary>Extract ct_loss_stage from runtime_truth.lane_verdict.ct_loss_stage.</summary>
<div class="doc-comment">
<p>Extract ct_loss_stage from runtime_truth.lane_verdict.ct_loss_stage.</p>
<p></p>
<p>F215B: CT loss stage diagnostic for evidence delta reporting.</p>
<p>Returns 'no_loss' when CT raw &gt; 0 and accepted &gt; 0,</p>
<p>or when no CT data is present.</p>
</div>
</details>
</li>
<li><code>has_complex_post_init</code> (migrate_dataclass_to_msgspec.py)</li>
<li><code>find_all_dataclass_files</code> (migrate_dataclass_to_msgspec.py)</li>
<li><code>main</code> (qoder_reality_check.py)</li>
<li><code>_extract_docx_fonts</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Extract embedded fonts from DOCX document.</span></li>
<li><code>batch_mine</code> (content_miner.py)
<details><summary>Batch mine multiple HTML documents.</summary>
<div class="doc-comment">
<p>Batch mine multiple HTML documents.</p>
<p></p>
<p>Args:</p>
<p>html_list: List of (html_content, url) tuples</p>
<p>include_metadata: Extract metadata</p>
<p></p>
<p>Returns:</p>
<p>List of MiningResult objects</p>
</div>
</details>
</li>
<li><code>_extract_from_link_tags</code> (content_miner.py) — <span class="doc-comment-inline">Extract feed URLs from &lt;link rel="alternate"&gt; tags.</span></li>
<li><code>_read_prefix_bytes</code> (content_miner.py) — <span class="doc-comment-inline">Read first n bytes with fail-safe.</span></li>
<li><code>_all_source_family_outcomes</code> (f234_validate_nonfeed_live_report.py) — <span class="doc-comment-inline">Collect all source_family_outcomes from every location.</span></li>
<li><code>cold_first_access</code> (bench_f214_python314_runtime.py)</li>
<li><code>_check_f221_artifact</code> (prelive_one_button_gate.py) — <span class="doc-comment-inline">Check a single F221 probe artifact exists and is parseable JSON.</span></li>
<li><code>_load_report</code> (prelive_decision_gate.py) — <span class="doc-comment-inline">Load a single JSON report, return ProbeReport (never raises).</span></li>
<li><code>iter_python_files</code> (migrate_gather_to_safe_gather.py) — <span class="doc-comment-inline">Return the list of .py files to process.</span></li>
<li><code>_memory_taint_penalty</code> (research_quality_score.py)
<details><summary>Penalize memory taint (swap pressure).</summary>
<div class="doc-comment">
<p>Penalize memory taint (swap pressure).</p>
<p>- swap_gib &gt; 3GiB = 20pt penalty</p>
<p>- swap_gib 1-3GiB = 10pt</p>
<p>- swap_warning without numeric = 5pt</p>
</div>
</details>
</li>
<li><code>extract_domain</code> (url_dedup.py) — <span class="doc-comment-inline">Extract registrable domain from URL.</span></li>
<li><code>__init__</code> (content_miner.py)
<details><summary>Initialize RustMiner.</summary>
<div class="doc-comment">
<p>Initialize RustMiner.</p>
<p></p>
<p>Args:</p>
<p>prefer_rust: Prefer Rust-based libraries (trafilex) over Python</p>
</div>
</details>
</li>
<li><code>extract_jsonld</code> (content_miner.py) — <span class="doc-comment-inline">Extract JSON-LD script blocks from HTML.</span></li>
<li><code>extract_source_map_url</code> (content_miner.py)
<details><summary>Find //# sourceMappingURL= in HTML (usually on last lines).</summary>
<div class="doc-comment">
<p>Find //# sourceMappingURL= in HTML (usually on last lines).</p>
<p>Returns URL or None.</p>
</div>
</details>
</li>
<li><code>_hash_bytes</code> (content_miner.py) — <span class="doc-comment-inline">Hash bytes using Rust xxhash3-64 (SIMD NEON), Python xxhash, or sha256.</span></li>
<li><code>_check_public_query_variants</code> (f234_validate_nonfeed_live_report.py) — <span class="doc-comment-inline">public_query_variants present for domain query.</span></li>
<li><code>package_import_context</code> (bench_f214_python314_runtime.py) — <span class="doc-comment-inline">Add project root to sys.path for package-style imports (hledac.universal).</span></li>
<li><code>_check_nonfeed_candidate_ledger</code> (prelive_decision_gate.py) — <span class="doc-comment-inline">Verify nonfeed candidate ledger is present and bounded (MAX field exists).</span></li>
<li><code>emit_markdown</code> (live_multisource_validator.py)</li>
<li><code>_find_call_node</code> (migrate_gather_to_safe_gather.py) — <span class="doc-comment-inline">Re-parse and return the ast.Call at the given (line, col).</span></li>
<li><code>run_flush</code> (bench_m1_runtime_gates.py)</li>
<li><code>check_provider_surface</code> (prelive_artifact_cockpit.py)</li>
<li><code>_feed_share</code> (live_artifact_triage.py) — <span class="doc-comment-inline">Fraction of findings from feed source.</span></li>
<li><code>_wallclock_penalty</code> (research_quality_score.py)
<details><summary>Penalize wall-clock failures.</summary>
<div class="doc-comment">
<p>Penalize wall-clock failures.</p>
<p>- Actual &gt; planned + 20% = 30pt penalty</p>
<p>Returns (penalty, exceeded)</p>
</div>
</details>
</li>
<li><code>_check_live_kpi_input_exists</code> (live_kpi_extraction_guard.py) — <span class="doc-comment-inline">LiveKpiInput dataclass must exist in live_sprint_measurement.py.</span></li>
<li><code>_extract_sync</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Blocking extraction - runs in executor.</span></li>
<li><code>_extract_pdf_revisions</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Extract revision count from PDF.</span></li>
<li><code>_check_advisory_telemetry</code> (f234_validate_nonfeed_live_report.py) — <span class="doc-comment-inline">F235C / NOTE R3: informational fields — non-zero values produce a warning but do not fail validation.</span></li>
<li><code>_check_uma</code> (prelive_decision_gate.py)
<details><summary>Sample UMA status via core.resource_governor.</summary>
<div class="doc-comment">
<p>Sample UMA status via core.resource_governor.</p>
<p>This is a one-shot local read — no live sprint, no model load.</p>
</div>
</details>
</li>
<li><code>verdict_to_markdown</code> (evidence_delta_memory.py) — <span class="doc-comment-inline">Render evidence delta as human-readable markdown.</span></li>
<li><code>_canonical_base</code> (prelive_artifact_cockpit.py)
<details><summary>Return (base, qualifier) for disambiguation.</summary>
<div class="doc-comment">
<p>Return (base, qualifier) for disambiguation.</p>
<p></p>
<p>base=F223D, qualifier='' for plain F223D</p>
<p>base=F223D, qualifier='_PRODUCT_VALUE' for F223D_PRODUCT_VALUE</p>
</div>
</details>
</li>
<li><code>_derive_fail_verdict</code> (live_kpi_extraction_guard.py) — <span class="doc-comment-inline">Map failed check to the most specific verdict.</span></li>
<li><code>_extract_xlsx_extended_props</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Extract extended properties from XLSX ZIP.</span></li>
<li><code>_bloom_ready</code> (url_dedup.py)
<details><summary>TypeGuard narrowing for Rust MmapBloomFilter instance.</summary>
<div class="doc-comment">
<p>TypeGuard narrowing for Rust MmapBloomFilter instance.</p>
<p></p>
<p>Returns True iff `b` is a live Rust MmapBloomFilter (not the</p>
<p>ImportError sentinel `None`). Use at call sites where the field</p>
<p>may be the sentinel during the import-failure path so that</p>
<p>`ty` can narrow the type without a runtime `is not None` check</p>
<p>leaking into the call site.</p>
<p></p>
<p>M1 8GB safety: identity + isinstance, zero allocation, no PyObject</p>
<p>boxing beyond the existing field reference.</p>
</div>
</details>
</li>
<li><code>get_default_bloom_filter</code> (url_dedup.py)
<details><summary>Get the shared default BloomFilter instance (P1-3: mmap-backed).</summary>
<div class="doc-comment">
<p>Get the shared default BloomFilter instance (P1-3: mmap-backed).</p>
<p></p>
<p>P1-3F: Detects HOME change (test fixture monkeypatch) and invalidates</p>
<p>the cached singleton so each test gets a fresh filter at the new HOME.</p>
</div>
</details>
</li>
<li><code>_extract_imports_regex</code> (content_miner.py) — <span class="doc-comment-inline">Fallback: extract imports using regex.</span></li>
<li><code>_check_cwd_guard</code> (prelive_one_button_gate.py) — <span class="doc-comment-inline">Check CWD vs repo-root. Returns warning string or empty if OK.</span></li>
<li><code>validate_live_artifact</code> (live_multisource_validator.py)</li>
<li><code>to_dict</code> (migrate_gather_to_safe_gather.py)</li>
<li><code>_web_search_handler</code> (executor.py) — <span class="doc-comment-inline">Web search - staged gap.</span></li>
<li><code>_discovery_provider_status_debug</code> (live_artifact_triage.py) — <span class="doc-comment-inline">Extract provider_status_debug from live_kpi or acquisition_report.</span></li>
<li><code>_extract_uma_swap_gib</code> (research_quality_score.py) — <span class="doc-comment-inline">Extract swap in GiB from various UMA fields across formats.</span></li>
<li><code>detect_overclaims</code> (qoder_reality_check.py) — <span class="doc-comment-inline">Detect documentation overclaims in a single doc.</span></li>
<li><code>is_valid_url</code> (url_dedup.py) — <span class="doc-comment-inline">Check if URL is valid and uses http/https scheme.</span></li>
<li><code>_get_repo_root_reality</code> (prelive_one_button_gate.py) — <span class="doc-comment-inline">Hermetic CWD diagnostic — no live run, no network, no MLX.</span></li>
<li><code>_kwargs_to_source</code> (migrate_gather_to_safe_gather.py) — <span class="doc-comment-inline">Serialize kwargs, optionally dropping some by name.</span></li>
<li><code>_should_skip</code> (migrate_gather_to_safe_gather.py)</li>
<li><code>build_parser</code> (prelive_artifact_cockpit.py)</li>
<li><code>_check_hardware_constrained_comparable</code> (live_result_sanity.py)
<details><summary>F214R2: Check hardware_constrained and research_quality_comparable are consistent.</summary>
<div class="doc-comment">
<p>F214R2: Check hardware_constrained and research_quality_comparable are consistent.</p>
<p></p>
<p>Fails if:</p>
<p>- hardware_constrained=True but research_quality_comparable is True or None.</p>
</div>
</details>
</li>
<li><code>_extract_pdf_template_path</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Extract template path from PDF metadata.</span></li>
<li><code>_extract_email_field</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Extract field from email content.</span></li>
<li><code>_extract_xml_value</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Extract value from XML tag.</span></li>
<li><code>_score_link</code> (content_miner.py) — <span class="doc-comment-inline">Calculate link score (0-1).</span></li>
<li><code>create_rust_miner</code> (content_miner.py)
<details><summary>Factory function to create a RustMiner.</summary>
<div class="doc-comment">
<p>Factory function to create a RustMiner.</p>
<p></p>
<p>Args:</p>
<p>prefer_rust: Prefer Rust-based libraries</p>
<p></p>
<p>Returns:</p>
<p>RustMiner instance</p>
</div>
</details>
</li>
<li><code>_check_acquisition_fallback</code> (f234_validate_nonfeed_live_report.py)
<details><summary>Canonical acquisition fallback check (exit 4).</summary>
<div class="doc-comment">
<p>Canonical acquisition fallback check (exit 4).</p>
<p></p>
<p>Rule: acquisition_report.acquisition_report_fallback_used is True -&gt; exit 4</p>
<p>Rule: missing field is OK (not an error) — canonical runs may not have this key</p>
</div>
</details>
</li>
<li><code>_build_parser</code> (prelive_one_button_gate.py)</li>
<li><code>_check_surface_contract</code> (prelive_decision_gate.py)
<details><summary>Check F219A surface contract if its probe directory exists.</summary>
<div class="doc-comment">
<p>Check F219A surface contract if its probe directory exists.</p>
<p>Returns (pass, detail, report).</p>
</div>
</details>
</li>
<li><code>_get_dns_tunnel_executor</code> (executor.py)
<details><summary>Get or create DNS tunnel dedicated event loop.</summary>
<div class="doc-comment">
<p>Get or create DNS tunnel dedicated event loop.</p>
<p></p>
<p>F350M-R FIX: Thread-safe creation + try/finally cleanup guard.</p>
<p>Prevents event loop leak on M1 8GB (~30-50MB/session).</p>
</div>
</details>
</li>
<li><code>_file_read_handler</code> (executor.py) — <span class="doc-comment-inline">File read handler.</span></li>
<li><code>analyze_artifact_pack</code> (prelive_artifact_cockpit.py) — <span class="doc-comment-inline">Returns (total, ready, missing, stale, missing_probes).</span></li>
<li><code>parse_validator</code> (live_result_sanity.py)</li>
<li><code>parse_trace</code> (live_result_sanity.py)</li>
<li><code>_check_nonfeed_evidence_missing</code> (live_result_sanity.py)
<details><summary>F224C: FAIL_NONFEED_EVIDENCE_MISSING means terminality was satisfied but nonfeed</summary>
<div class="doc-comment">
<p>F224C: FAIL_NONFEED_EVIDENCE_MISSING means terminality was satisfied but nonfeed</p>
<p>evidence was insufficient. This is a research quality failure, not terminality.</p>
<p></p>
<p>Fails sanity if:</p>
<p>- run_quality_verdict is FAIL_NONFEED_EVIDENCE_MISSING</p>
</div>
</details>
</li>
<li><code>_get</code> (live_artifact_triage.py) — <span class="doc-comment-inline">Safe nested dict get.</span></li>
<li><code>_ct_quarantine_count</code> (live_artifact_triage.py) — <span class="doc-comment-inline">Extract ct_quarantine_count from live_kpi or public_pipeline.</span></li>
<li><code>_detect_format</code> (research_quality_score.py) — <span class="doc-comment-inline">Detect whether this is 'benchmark' (hermetic) or 'live' format.</span></li>
<li><code>get_base_name</code> (migrate_dataclass_to_msgspec.py)</li>
<li><code>has_super_call_in_post_init</code> (migrate_dataclass_to_msgspec.py)</li>
<li><code>add</code> (url_dedup.py)</li>
<li><code>__contains__</code> (url_dedup.py)</li>
<li><code>_prewarm_secondary</code> (url_dedup.py) — <span class="doc-comment-inline">Background: touch secondary slot to fault in its pages.</span></li>
<li><code>sync</code> (url_dedup.py) — <span class="doc-comment-inline">Sync all slots to disk.</span></li>
<li><code>_extract_title_fallback</code> (content_miner.py) — <span class="doc-comment-inline">Extract title using regex (fallback)</span></li>
<li><code>_resolve_url</code> (content_miner.py) — <span class="doc-comment-inline">Resolve relative URL to absolute.</span></li>
<li><code>extract</code> (content_miner.py) — <span class="doc-comment-inline">Extract metadata based on content-type.</span></li>
<li><code>_resolve_relative_import</code> (content_miner.py) — <span class="doc-comment-inline">Resolve relative import to absolute.</span></li>
<li><code>_check_all_f221_artifacts</code> (prelive_one_button_gate.py) — <span class="doc-comment-inline">Check all F221 required artifacts. Returns (required_results, missing).</span></li>
<li><code>_check_cross_sprint_artifacts</code> (prelive_one_button_gate.py) — <span class="doc-comment-inline">Check cross-sprint required artifacts.</span></li>
<li><code>_check_hermes_metal_finalizer</code> (prelive_decision_gate.py) — <span class="doc-comment-inline">Check F219B Hermes Metal finalizer if its probe directory exists.</span></li>
<li><code>_check_public_session_seal</code> (prelive_decision_gate.py) — <span class="doc-comment-inline">Check F219D public session seal if its probe directory exists.</span></li>
<li><code>_check_ct_cooldown</code> (prelive_decision_gate.py) — <span class="doc-comment-inline">Check F219E CT provider cooldown if its probe directory exists.</span></li>
<li><code>_build_nonfeed_block_reason</code> (prelive_decision_gate.py) — <span class="doc-comment-inline">Build human-readable nonfeed block reason for telemetry.</span></li>
<li><code>_getnested</code> (live_multisource_validator.py) — <span class="doc-comment-inline">Walk nested keys, return (value, found_bool).</span></li>
<li><code>_get_safe</code> (live_multisource_validator.py) — <span class="doc-comment-inline">Chained safe dict getter.</span></li>
<li><code>_enclosing_statement</code> (migrate_gather_to_safe_gather.py) — <span class="doc-comment-inline">Walk up to the closest Expr / Assign / Return / AugAssign node.</span></li>
<li><code>_is_gather_call</code> (migrate_gather_to_safe_gather.py) — <span class="doc-comment-inline">True if `node` is `asyncio.gather(...)` or `_asyncio.gather(...)`.</span></li>
<li><code>_rss_kb</code> (bench_m1_runtime_gates.py) — <span class="doc-comment-inline">Peak RSS in KB via resource.getrusage.</span></li>
<li><code>_entity_extraction_handler</code> (executor.py) — <span class="doc-comment-inline">Entity extraction - placeholder.</span></li>
<li><code>_get_ct_raw</code> (evidence_delta_memory.py)</li>
<li><code>_get_cwd_guard_state</code> (prelive_artifact_cockpit.py) — <span class="doc-comment-inline">Hermetic CWD diagnostic — no live run, no network, no MLX.</span></li>
<li><code>_check_benchmark_missing_source_family_outcomes</code> (live_result_sanity.py)</li>
<li><code>_check_swap_gate_comparable</code> (live_result_sanity.py)
<details><summary>F215D: active300/active600 with swap_gate_triggered=True must have comparable_result=False.</summary>
<div class="doc-comment">
<p>F215D: active300/active600 with swap_gate_triggered=True must have comparable_result=False.</p>
<p></p>
<p>Fails if:</p>
<p>- swap_gate_triggered=True but comparable_result is True or None.</p>
</div>
</details>
</li>
<li><code>_ct_all_rejected_by_bridge</code> (live_artifact_triage.py) — <span class="doc-comment-inline">True when CT candidates were built but ALL were rejected by the bridge.</span></li>
<li><code>_nonfeed_scheduler_gap</code> (live_artifact_triage.py)</li>
<li><code>_callback_executed_count</code> (live_artifact_triage.py)</li>
<li><code>_extract_swap_warning</code> (research_quality_score.py) — <span class="doc-comment-inline">Extract swap_warning flag.</span></li>
<li><code>_feed_dominance_penalty</code> (research_quality_score.py)
<details><summary>Penalize feed-only dominance.</summary>
<div class="doc-comment">
<p>Penalize feed-only dominance.</p>
<p>- Perfect feed (1.0) + near-zero nonfeed (&lt;5%) = max penalty 40pts</p>
<p>- Some nonfeed reduces penalty proportionally</p>
</div>
</details>
</li>
<li><code>get_decorator_name</code> (migrate_dataclass_to_msgspec.py) — <span class="doc-comment-inline">Return the decorator name for ast.Name, ast.Call(@dataclass(...)), or ast.Attribute.</span></li>
<li><code>sync</code> (url_dedup.py) — <span class="doc-comment-inline">Force durable sync to disk (MS_SYNC).</span></li>
<li><code>__contains__</code> (url_dedup.py) — <span class="doc-comment-inline">Check all slots (OR semantics — seen in any slot = seen).</span></li>
<li><code>__len__</code> (url_dedup.py) — <span class="doc-comment-inline">Total items across all slots (sum of per-slot counters).</span></li>
<li><code>filter_valid_urls</code> (url_dedup.py) — <span class="doc-comment-inline">Filter list to only valid http/https URLs.</span></li>
<li><code>_heuristic_discovery</code> (content_miner.py) — <span class="doc-comment-inline">Heuristic feed discovery based on common paths.</span></li>
<li><code>_extract</code> (content_miner.py)</li>
<li><code>_load_last_live_triage</code> (prelive_one_button_gate.py) — <span class="doc-comment-inline">Load optional last-live artifact triage result.</span></li>
<li><code>_resolve_next_action_capability</code> (prelive_decision_gate.py) — <span class="doc-comment-inline">Resolve next_action_capability from decision gates — flat, readable.</span></li>
<li><code>_parse_existing_names</code> (migrate_gather_to_safe_gather.py) — <span class="doc-comment-inline">Extract import names from the RHS of `from x import (...)`.</span></li>
<li><code>_load_full_report</code> (evidence_delta_memory.py) — <span class="doc-comment-inline">Load full report JSON, returns empty dict on failure.</span></li>
<li><code>_ct_accepted</code> (live_artifact_triage.py) — <span class="doc-comment-inline">Extract ct_accepted count.</span></li>
<li><code>_ct_attempted</code> (live_artifact_triage.py) — <span class="doc-comment-inline">Check if CT was attempted.</span></li>
<li><code>_sum_alias_fields</code> (research_quality_score.py) — <span class="doc-comment-inline">Return first non-zero value from src dict, or 0 if all None/zero.</span></li>
<li><code>normalize_path</code> (qoder_reality_check.py) — <span class="doc-comment-inline">Normalize a reference string to a repo-relative path.</span></li>
<li><code>_check_docx_macros</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Check if DOCX/XLSX/PPTX contains VBA macros.</span></li>
<li><code>_exif_to_float</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Handle EXIF rational (num, denom) tuples and plain numeric values.</span></li>
<li><code>add_batch</code> (url_dedup.py)
<details><summary>Bulk add — returns True per new item, False per duplicate.</summary>
<div class="doc-comment">
<p>Bulk add — returns True per new item, False per duplicate.</p>
<p></p>
<p>Optional method — implementations that don't provide it will</p>
<p>raise AttributeError, which dedupe_url_list catches and</p>
<p>falls back to per-item add().</p>
</div>
</details>
</li>
<li><code>__contains__</code> (url_dedup.py)
<details><summary>Check if an item might have been seen before.</summary>
<div class="doc-comment">
<p>Check if an item might have been seen before.</p>
<p></p>
<p>Specialised on `str` because every concrete implementation</p>
<p>(RustUrlSetAdapter, RotatingBloomFilterAdapter, BloomFilter)</p>
<p>keys exclusively on URLs.</p>
</div>
</details>
</li>
<li><code>add</code> (url_dedup.py)</li>
<li><code>_check_pymupdf</code> (content_miner.py)</li>
<li><code>_check_exifread</code> (content_miner.py)</li>
<li><code>_check_pillow</code> (content_miner.py)</li>
<li><code>_path_to_module</code> (content_miner.py) — <span class="doc-comment-inline">Convert file path to module name.</span></li>
<li><code>_get</code> (f234_validate_nonfeed_live_report.py) — <span class="doc-comment-inline">Safe nested key access.</span></li>
<li><code>make_import</code> (bench_f214_python314_runtime.py)</li>
<li><code>_write_hash_file</code> (bench_f214_python314_runtime.py)</li>
<li><code>_run_sync_benchmark</code> (bench_f214_python314_runtime.py)</li>
<li><code>_run_async_benchmark</code> (bench_f214_python314_runtime.py)</li>
<li><code>_sample_uma</code> (prelive_one_button_gate.py) — <span class="doc-comment-inline">Sample current UMA/swap state via core.resource_governor.</span></li>
<li><code>_load_decision_gate</code> (prelive_one_button_gate.py)</li>
<li><code>_get_import_names</code> (live_measurement_extraction_guard.py)</li>
<li><code>_build_parser</code> (prelive_decision_gate.py)</li>
<li><code>to_offset</code> (migrate_gather_to_safe_gather.py)</li>
<li><code>_rss_psutil</code> (bench_m1_runtime_gates.py) — <span class="doc-comment-inline">Current RSS in MiB via psutil (or None).</span></li>
<li><code>_execute_handler</code> (executor.py) — <span class="doc-comment-inline">Execute tool handler with validated arguments.</span></li>
<li><code>_has_feed</code> (live_artifact_triage.py)</li>
<li><code>_has_ct</code> (live_artifact_triage.py)</li>
<li><code>_discovery_selected_providers</code> (live_artifact_triage.py) — <span class="doc-comment-inline">Extract discovery_selected_providers from live_kpi.</span></li>
<li><code>_discovery_skipped_providers</code> (live_artifact_triage.py) — <span class="doc-comment-inline">Extract discovery_skipped_providers from live_kpi.</span></li>
<li><code>_discovery_stub_providers</code> (live_artifact_triage.py) — <span class="doc-comment-inline">Extract discovery_stub_providers from live_kpi.</span></li>
<li><code>_discovery_not_wired_providers</code> (live_artifact_triage.py) — <span class="doc-comment-inline">Extract discovery_not_wired_providers from live_kpi.</span></li>
<li><code>_ct_evidence_score</code> (research_quality_score.py) — <span class="doc-comment-inline">Certificate Transparency evidence score up to 20pts.</span></li>
<li><code>_public_evidence_score</code> (research_quality_score.py) — <span class="doc-comment-inline">Public/web evidence score up to 15pts.</span></li>
<li><code>_passive_evidence_score</code> (research_quality_score.py) — <span class="doc-comment-inline">Passive DNS/log evidence score up to 10pts.</span></li>
<li><code>_get_import_names</code> (live_kpi_extraction_guard.py)</li>
<li><code>has_own_init</code> (migrate_dataclass_to_msgspec.py)</li>
<li><code>_find_internal_paths</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Find internal file paths in text.</span></li>
<li><code>byte_size</code> (url_dedup.py)</li>
<li><code>__len__</code> (url_dedup.py)</li>
<li><code>reset</code> (url_dedup.py)</li>
<li><code>_check_trafilex</code> (content_miner.py) — <span class="doc-comment-inline">Check if trafilex (Rust-based) is available</span></li>
<li><code>_check_traflatura</code> (content_miner.py) — <span class="doc-comment-inline">Check if traflatura is available</span></li>
<li><code>_convert_gps</code> (content_miner.py) — <span class="doc-comment-inline">Convert EXIF GPS coordinates to decimal.</span></li>
<li><code>_check_ct_planned</code> (f234_validate_nonfeed_live_report.py) — <span class="doc-comment-inline">ct_planned present.</span></li>
<li><code>one_write</code> (bench_m1_runtime_gates.py)</li>
<li><code>execute_dns_tunnel_sync</code> (executor.py) — <span class="doc-comment-inline">Synchronous wrapper — runs in ThreadPoolExecutor for M1 safety.</span></li>
<li><code>_source_family_outcomes</code> (live_artifact_triage.py)</li>
<li><code>_terminality_report</code> (live_artifact_triage.py)</li>
<li><code>_terminality_required_lanes</code> (live_artifact_triage.py)</li>
<li><code>_terminality_observed_lanes</code> (live_artifact_triage.py)</li>
<li><code>_findings_volume_score</code> (research_quality_score.py) — <span class="doc-comment-inline">Volume is only rewarded if there's meaningful nonfeed content.</span></li>
<li><code>_nonfeed_evidence_score</code> (research_quality_score.py) — <span class="doc-comment-inline">Reward nonfeed findings proportion up to 25pts.</span></li>
<li><code>RotatingBloomFilter</code> (url_dedup.py) — <span class="doc-comment-inline">Factory — resolves and instantiates RotatingBloomFilter on first call.</span></li>
<li><code>_select_slot</code> (url_dedup.py) — <span class="doc-comment-inline">Select the next slot in round-robin order.</span></li>
<li><code>_prewarm_slot_bg</code> (url_dedup.py) — <span class="doc-comment-inline">Background prewarm: single contains to fault in pages.</span></li>
<li><code>_check_ct_scheduled</code> (f234_validate_nonfeed_live_report.py) — <span class="doc-comment-inline">ct_scheduled present.</span></li>
<li><code>_invoke</code> (bench_f214_python314_runtime.py) — <span class="doc-comment-inline">Call fn once. Async fns are always run in a fresh thread-loop to avoid nesting.</span></li>
<li><code>_inner</code> (bench_f214_python314_runtime.py)</li>
<li><code>_get_acquisition_profile_for_benchmark</code> (prelive_one_button_gate.py)
<details><summary>Map benchmark profile name to runtime acquisition profile.</summary>
<div class="doc-comment">
<p>Map benchmark profile name to runtime acquisition profile.</p>
<p></p>
<p>F223A: nonfeed_diagnostic180 benchmark → nonfeed_diagnostic acquisition.</p>
</div>
</details>
</li>
<li><code>_has_fallback_schema_marker</code> (prelive_decision_gate.py) — <span class="doc-comment-inline">Scan report raw text for fallback acquisition schema marker.</span></li>
<li><code>_extract_branch_mix</code> (live_multisource_validator.py) — <span class="doc-comment-inline">Extract branch_mix — benchmark nests under runtime_truth, internal has it top-level.</span></li>
<li><code>_build_parent_map</code> (migrate_gather_to_safe_gather.py)</li>
<li><code>_arg_to_source</code> (migrate_gather_to_safe_gather.py) — <span class="doc-comment-inline">Best-effort serialization of an AST node back to source code.</span></li>
<li><code>_file_write_handler</code> (executor.py) — <span class="doc-comment-inline">File write handler.</span></li>
<li><code>_check_benchmark_fail_validator_pass</code> (live_result_sanity.py)</li>
<li><code>_check_stale_terminality</code> (live_result_sanity.py)</li>
<li><code>_check_wallclock_budget</code> (live_result_sanity.py)</li>
<li><code>_hardware_constrained</code> (live_artifact_triage.py) — <span class="doc-comment-inline">Extract hardware_constrained flag.</span></li>
<li><code>_top_level_terminality_satisfied</code> (live_artifact_triage.py) — <span class="doc-comment-inline">Top-level acquisition_terminality_satisfied (set by core/__main__).</span></li>
<li><code>_source_diversity_score</code> (research_quality_score.py) — <span class="doc-comment-inline">Reward source diversity up to 25pts.</span></li>
<li><code>_build_parser</code> (research_quality_score.py)</li>
<li><code>_render_md</code> (research_quality_score.py)</li>
<li><code>write_json</code> (qoder_reality_check.py) — <span class="doc-comment-inline">Write JSON reality matrix.</span></li>
<li><code>_is_cache_valid</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Check if cache entry is still valid (within TTL).</span></li>
<li><code>_get_extension</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Get file extension from URL.</span></li>
<li><code>add_batch</code> (url_dedup.py) — <span class="doc-comment-inline">Bulk add — returns True per new item, False per duplicate.</span></li>
<li><code>create_rust_url_set</code> (url_dedup.py) — <span class="doc-comment-inline">Create a Rust-backed URL deduplication set (FNV-1a, O(1)).</span></li>
<li><code>_compute_fingerprint</code> (content_miner.py) — <span class="doc-comment-inline">Compute stable fingerprint from canonical JSON.</span></li>
<li><code>_fmt_ratio</code> (bench_f214_python314_runtime.py)</li>
<li><code>_clear_hledac_modules</code> (bench_f214_python314_runtime.py) — <span class="doc-comment-inline">Remove all hledac.* modules from sys.modules.</span></li>
<li><code>_has_fallback_schema</code> (prelive_one_button_gate.py) — <span class="doc-comment-inline">Check if any report has fallback acquisition schema marker.</span></li>
<li><code>load_readiness</code> (prelive_artifact_cockpit.py)</li>
<li><code>extract_uma</code> (prelive_artifact_cockpit.py)</li>
<li><code>_public_stage_counters</code> (live_artifact_triage.py)</li>
<li><code>_ct_provider_status</code> (live_artifact_triage.py) — <span class="doc-comment-inline">Extract ct_provider_status from live_kpi.</span></li>
<li><code>_acquisition_report</code> (live_artifact_triage.py)</li>
<li><code>_acquisition_schema_version</code> (live_artifact_triage.py)</li>
<li><code>_public_quality_rejected_result</code> (live_artifact_triage.py)</li>
<li><code>normalize_benchmark_json</code> (research_quality_score.py)</li>
<li><code>__init__</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Initialize extractor with SQLite cache.</span></li>
<li><code>_get_cache_key</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Generate cache key from first 1024 bytes.</span></li>
<li><code>__init__</code> (url_dedup.py)</li>
<li><code>byte_size</code> (url_dedup.py)</li>
<li><code>reset_default_bloom_filter</code> (url_dedup.py) — <span class="doc-comment-inline">Reset the default bloom filter (for testing).</span></li>
<li><code>__init__</code> (content_miner.py)</li>
<li><code>_section</code> (bench_f214_python314_runtime.py)</li>
<li><code>is_terminal</code> (live_multisource_validator.py)</li>
<li><code>mock_execute</code> (bench_m1_runtime_gates.py)</li>
<li><code>_swap_warning</code> (live_artifact_triage.py) — <span class="doc-comment-inline">Extract swap_warning flag.</span></li>
<li><code>build_evidence</code> (qoder_reality_check.py) — <span class="doc-comment-inline">Build evidence dict for a module.</span></li>
<li><code>PROBABLES_AVAILABLE</code> (url_dedup.py) — <span class="doc-comment-inline">True if either probables or pyprobables RotatingBloomFilter resolved.</span></li>
<li><code>add</code> (url_dedup.py) — <span class="doc-comment-inline">Add an item to the deduplication set.</span></li>
<li><code>__init__</code> (url_dedup.py)</li>
<li><code>__post_init__</code> (content_miner.py)</li>
<li><code>_extract_docx</code> (content_miner.py) — <span class="doc-comment-inline">Extract metadata from DOCX (placeholder - would need python-docx).</span></li>
<li><code>_gate</code> (f234_validate_nonfeed_live_report.py) — <span class="doc-comment-inline">Quality gate string from live_kpi.</span></li>
<li><code>_branch_counts</code> (f234_validate_nonfeed_live_report.py) — <span class="doc-comment-inline">Branch accepted counts from live_kpi.</span></li>
<li><code>_print_row</code> (bench_f214_python314_runtime.py)</li>
<li><code>cached_import</code> (bench_f214_python314_runtime.py)</li>
<li><code>cached_access</code> (bench_f214_python314_runtime.py)</li>
<li><code>plain_task</code> (bench_f214_python314_runtime.py)</li>
<li><code>sem_task</code> (bench_f214_python314_runtime.py)</li>
<li><code>semaphore_gather</code> (bench_f214_python314_runtime.py)</li>
<li><code>_read_source</code> (live_measurement_extraction_guard.py)</li>
<li><code>_extract_run_status</code> (live_multisource_validator.py) — <span class="doc-comment-inline">Extract run status from benchmark or internal report shape.</span></li>
<li><code>_extract_run_id</code> (live_multisource_validator.py) — <span class="doc-comment-inline">Extract run ID from benchmark or internal report shape.</span></li>
<li><code>_extract_live_kpi</code> (live_multisource_validator.py) — <span class="doc-comment-inline">Extract live_kpi from benchmark shape.</span></li>
<li><code>emit_json</code> (live_multisource_validator.py)</li>
<li><code>chunk_stream</code> (bench_m1_runtime_gates.py)</li>
<li><code>one_read</code> (bench_m1_runtime_gates.py)</li>
<li><code>bs4_parse</code> (bench_m1_runtime_gates.py)</li>
<li><code>_write_jsonl</code> (bench_m1_runtime_gates.py)</li>
<li><code>registry</code> (executor.py) — <span class="doc-comment-inline">Expose underlying registry for direct tool registration.</span></li>
<li><code>register</code> (executor.py) — <span class="doc-comment-inline">Delegate to underlying registry.</span></li>
<li><code>get_tool</code> (executor.py) — <span class="doc-comment-inline">Delegate to underlying registry.</span></li>
<li><code>capability_delta_to_dict</code> (evidence_delta_memory.py) — <span class="doc-comment-inline">Serialize CapabilityDelta to a JSON-serializable dict.</span></li>
<li><code>delta_to_dict</code> (evidence_delta_memory.py) — <span class="doc-comment-inline">Serialize EvidenceDelta to a JSON-serializable dict.</span></li>
<li><code>load_decision_gate</code> (prelive_artifact_cockpit.py)</li>
<li><code>load_artifact_pack</code> (prelive_artifact_cockpit.py)</li>
<li><code>_swap_gib</code> (live_artifact_triage.py) — <span class="doc-comment-inline">Extract post-sprint swap in GiB.</span></li>
<li><code>_public_fetch_attempted</code> (live_artifact_triage.py)</li>
<li><code>_read_source</code> (live_kpi_extraction_guard.py)</li>
<li><code>get_decorator_kw</code> (migrate_dataclass_to_msgspec.py) — <span class="doc-comment-inline">Extract keyword arguments from a decorator call like @dataclass(frozen=True).</span></li>
<li><code>__init__</code> (url_dedup.py)</li>
<li><code>add</code> (url_dedup.py)</li>
<li><code>__contains__</code> (url_dedup.py)</li>
<li><code>__contains__</code> (url_dedup.py)</li>
<li><code>add</code> (url_dedup.py)</li>
<li><code>__contains__</code> (url_dedup.py)</li>
<li><code>__len__</code> (url_dedup.py)</li>
<li><code>clear</code> (url_dedup.py)</li>
<li><code>path</code> (url_dedup.py)</li>
<li><code>capacity</code> (url_dedup.py)</li>
<li><code>fp_rate</code> (url_dedup.py)</li>
<li><code>num_slots</code> (url_dedup.py)</li>
<li><code>path</code> (url_dedup.py)</li>
<li><code>__init__</code> (content_miner.py)</li>
<li><code>_branch_mix</code> (f234_validate_nonfeed_live_report.py)</li>
<li><code>_zstd_compress</code> (bench_f214_python314_runtime.py)</li>
<li><code>_zstd_decompress</code> (bench_f214_python314_runtime.py)</li>
<li><code>_fmt_ms</code> (bench_f214_python314_runtime.py)</li>
<li><code>_print_import_summary</code> (bench_f214_python314_runtime.py)</li>
<li><code>batch</code> (bench_f214_python314_runtime.py)</li>
<li><code>scan</code> (bench_f214_python314_runtime.py)</li>
<li><code>compress_fn</code> (bench_f214_python314_runtime.py)</li>
<li><code>decompress_fn</code> (bench_f214_python314_runtime.py)</li>
<li><code>sorted_fn</code> (bench_f214_python314_runtime.py)</li>
<li><code>heapq_fn</code> (bench_f214_python314_runtime.py)</li>
<li><code>plain_gather</code> (bench_f214_python314_runtime.py)</li>
<li><code>to_dict</code> (prelive_one_button_gate.py)</li>
<li><code>to_dict</code> (prelive_one_button_gate.py)</li>
<li><code>_parse_ast</code> (live_measurement_extraction_guard.py)</li>
<li><code>_check_extracted_module_exists</code> (live_measurement_extraction_guard.py)</li>
<li><code>format_json</code> (live_measurement_extraction_guard.py)</li>
<li><code>to_dict</code> (live_multisource_validator.py)</li>
<li><code>_invoke</code> (bench_m1_runtime_gates.py)</li>
<li><code>sel_parse</code> (bench_m1_runtime_gates.py)</li>
<li><code>encode_fn</code> (bench_m1_runtime_gates.py)</li>
<li><code>decode_fn</code> (bench_m1_runtime_gates.py)</li>
<li><code>__init__</code> (executor.py)</li>
<li><code>_timeout_handler</code> (executor.py)</li>
<li><code>to_dict</code> (prelive_artifact_cockpit.py)</li>
<li><code>to_dict</code> (prelive_artifact_cockpit.py)</li>
<li><code>to_dict</code> (live_result_sanity.py)</li>
<li><code>_verdict</code> (live_artifact_triage.py)</li>
<li><code>_feed_dominance_score</code> (live_artifact_triage.py)</li>
<li><code>_total_findings</code> (live_artifact_triage.py)</li>
<li><code>_accepted_findings</code> (live_artifact_triage.py)</li>
<li><code>_public_acceptance_attempted</code> (live_artifact_triage.py)</li>
<li><code>_public_acceptance_accepted</code> (live_artifact_triage.py)</li>
<li><code>_public_rejected</code> (live_artifact_triage.py)</li>
<li><code>_top_public_reject_reason</code> (live_artifact_triage.py)</li>
<li><code>_nonfeed_accepted</code> (live_artifact_triage.py)</li>
<li><code>_quality_gate</code> (live_artifact_triage.py)</li>
<li><code>_query</code> (live_artifact_triage.py)</li>
<li><code>_profile</code> (live_artifact_triage.py)</li>
<li><code>_parse_ast</code> (live_kpi_extraction_guard.py)</li>
<li><code>format_json</code> (live_kpi_extraction_guard.py)</li>
<li><code>count_fields</code> (migrate_dataclass_to_msgspec.py)</li>
</ul>
</details>

<details><summary><strong>Class</strong> (77)</summary>
<ul>
<li><code>_DocumentMetadataExtractor</code> (document_metadata_extractor.py)
<details><summary>FOCA-style forensic metadata extractor for documents.</summary>
<div class="doc-comment">
<p>FOCA-style forensic metadata extractor for documents.</p>
<p></p>
<p>Extracts:</p>
<p>- Author, creator, organization, company, template path</p>
<p>- Internal file paths (Windows/UNC/Unix)</p>
<p>- GPS coordinates from embedded images</p>
<p>- Revision history (DOCX)</p>
<p>- Embedded fonts (geographic indication)</p>
<p>- PDF hidden content (invisible layers, forms, JS, embedded files)</p>
<p>- Macro analysis (olevba integration)</p>
<p>- Email header forensics</p>
<p>- Presentation forensics (speaker notes, hidden slides)</p>
<p>- CAD/SVG metadata</p>
<p></p>
<p>CPU-heavy operations run in executor with timeout.</p>
<p>Results cached in SQLite with 30-day TTL.</p>
</div>
</details>
</li>
<li><code>RustMiner</code> (content_miner.py)
<details><summary>Lightweight content miner using Rust-backed libraries.</summary>
<div class="doc-comment">
<p>Lightweight content miner using Rust-backed libraries.</p>
<p></p>
<p>Strategy:</p>
<p>1. Try trafilex (Rust, fastest) - minimal DOM</p>
<p>2. Fallback to traflatura (minimal mode) - streaming</p>
<p>3. Ultimate fallback to regex (no dependencies)</p>
</div>
</details>
</li>
<li><code>CrossProcessBloomFilter</code> (url_dedup.py)
<details><summary>Cross-process persistent Bloom filter with prewarm slots.</summary>
<div class="doc-comment">
<p>Cross-process persistent Bloom filter with prewarm slots.</p>
<p></p>
<p>Wraps N MmapBloomFilterAdapter instances (all pointing to the same mmap</p>
<p>file) in a round-robin ring. On ``add_batch``:</p>
<p>1. Round-robin to the next slot (index = counter % N).</p>
<p>2. Execute check_and_add_batch on that slot.</p>
<p>3. In the background, prewarm the OTHER slot with a no-op touch so</p>
<p>its pages are faulted in — next request to that slot hits hot cache.</p>
<p></p>
<p>This eliminates the 200-400 ms first-access page-fault cost that would</p>
<p>otherwise appear on every sprint start when the dedup filter is cold.</p>
<p></p>
<p>Invariants:</p>
<p>- Always-on: no feature flag, no env var toggle</p>
<p>- Bounded: exactly _PREWARM_SLOTS instances, never grows</p>
<p>- Fail-safe: any error → lazy runtime path, no exception</p>
<p>- M1 8GB safe: ~60 MB total for 4 slots (15 MB each at 100K capacity)</p>
<p>- Cross-process safe: MAP_SHARED mmap, kernel-level page coherency</p>
<p>- Thread-safe: threading.Lock per slot, background prewarm via Thread</p>
</div>
</details>
</li>
<li><code>MmapBloomFilterAdapter</code> (url_dedup.py)
<details><summary>Thread-safe adapter wrapping Rust MmapBloomFilter.</summary>
<div class="doc-comment">
<p>Thread-safe adapter wrapping Rust MmapBloomFilter.</p>
<p></p>
<p>The underlying Rust class is not Send+Sync at the bit level — concurrent</p>
<p>add/contains on the same filter would race on the bitmap. This adapter</p>
<p>adds a `threading.Lock` so multi-threaded dedup is safe.</p>
<p></p>
<p>Lifecycle:</p>
<p>- File is opened or created on first call to `create_mmap_bloom_filter`.</p>
<p>- State persists in `path` across process restarts (msync(MS_ASYNC) per</p>
<p>write + msync(MS_SYNC) on Drop).</p>
<p>- On `reset()` the file is truncated to empty state (in-place, no</p>
<p>re-alloc — the mmap region stays valid).</p>
<p></p>
<p>M1 8GB safety:</p>
<p>- Demand-paged: cold pages live on disk, not in RSS.</p>
<p>- Bounded: capacity is fixed at creation; FPR degrades past capacity.</p>
<p>- Fail-soft: every method is wrapped in try/except. On IO error the</p>
<p>dedup degrades to "definitely not present" so the caller can still</p>
<p>proceed without crashing the sprint.</p>
</div>
</details>
</li>
<li><code>ToolExecutor</code> (executor.py)
<details><summary>Canonical async tool executor.</summary>
<div class="doc-comment">
<p>Canonical async tool executor.</p>
<p></p>
<p>Separated from registry for testability — async patterns can be</p>
<p>tested in isolation without full registry initialization.</p>
</div>
</details>
</li>
<li><code>MetadataExtractor</code> (content_miner.py) — <span class="doc-comment-inline">Extract metadata from non-HTML documents (PDF, images, etc.) - M1 8GB.</span></li>
<li><code>FeedDiscoverer</code> (content_miner.py) — <span class="doc-comment-inline">Discover RSS/Atom feeds from HTML content.</span></li>
<li><code>SanityResult</code> (live_result_sanity.py)</li>
<li><code>OneButtonResult</code> (prelive_one_button_gate.py)</li>
<li><code>CockpitResult</code> (prelive_artifact_cockpit.py)</li>
<li><code>DeduplicationStrategy</code> (url_dedup.py)
<details><summary>Protocol for URL deduplication strategies.</summary>
<div class="doc-comment">
<p>Protocol for URL deduplication strategies.</p>
<p></p>
<p>`add()` return type is intentionally `Any` to accept both:</p>
<p>- probables.RotatingBloomFilter.add(...) -&gt; None</p>
<p>- hledac_rust_extensions.BloomFilter.add(...) -&gt; bool</p>
<p>Callers MUST NOT depend on the return value.</p>
<p></p>
<p>F7.5: add_batch() is optional — implementations that support it</p>
<p>provide O(N) bulk operations vs per-item O(N) individual adds.</p>
</div>
</details>
</li>
<li><code>RustUrlSetAdapter</code> (url_dedup.py)
<details><summary>Adapter wrapping Rust UrlSet (FNV-1a hash set) to satisfy DeduplicationStrategy.</summary>
<div class="doc-comment">
<p>Adapter wrapping Rust UrlSet (FNV-1a hash set) to satisfy DeduplicationStrategy.</p>
<p></p>
<p>Rust implementation: url_set.rs — FNV-1a hashing, O(1) add/contains.</p>
<p>Falls back to Python set if Rust unavailable (RUST_URL_DEDUP_AVAILABLE=False).</p>
</div>
</details>
</li>
<li><code>EvidenceDelta</code> (evidence_delta_memory.py)</li>
<li><code>DecisionResult</code> (prelive_decision_gate.py)</li>
<li><code>PersistentSetAdapter</code> (url_dedup.py)
<details><summary>Bounded set adapter for deduplication when BloomFilter unavailable.</summary>
<div class="doc-comment">
<p>Bounded set adapter for deduplication when BloomFilter unavailable.</p>
<p></p>
<p>Uses an OrderedDict-style eviction to maintain bounded memory.</p>
</div>
</details>
</li>
<li><code>Report</code> (migrate_gather_to_safe_gather.py)</li>
<li><code>RootCause</code> (live_artifact_triage.py)</li>
<li><code>ResearchQualityScore</code> (research_quality_score.py)</li>
<li><code>CapabilityDelta</code> (evidence_delta_memory.py)</li>
<li><code>RotatingBloomFilterAdapter</code> (url_dedup.py)
<details><summary>Adapter wrapping RotatingBloomFilter to satisfy DeduplicationStrategy.</summary>
<div class="doc-comment">
<p>Adapter wrapping RotatingBloomFilter to satisfy DeduplicationStrategy.</p>
<p></p>
<p>Sprint F214AD: Formerly used directly by FetchCoordinator — now encapsulated.</p>
</div>
</details>
</li>
<li><code>Verdict</code> (live_multisource_validator.py)</li>
<li><code>QualitySurface</code> (live_result_sanity.py) — <span class="doc-comment-inline">Parsed research quality surface.</span></li>
<li><code>BenchmarkSurface</code> (live_result_sanity.py) — <span class="doc-comment-inline">Parsed benchmark surface.</span></li>
<li><code>ExtractedMetadata</code> (content_miner.py) — <span class="doc-comment-inline">Metadata extracted from non-HTML documents.</span></li>
<li><code>SelfTestResult</code> (prelive_one_button_gate.py) — <span class="doc-comment-inline">Machine-checkable self-test output (Sprint F224H).</span></li>
<li><code>GatherSite</code> (migrate_gather_to_safe_gather.py) — <span class="doc-comment-inline">One `asyncio.gather(...)` call site.</span></li>
<li><code>MiningResult</code> (content_miner.py) — <span class="doc-comment-inline">Result of content mining operation</span></li>
<li><code>Verdict</code> (live_measurement_extraction_guard.py)</li>
<li><code>Verdict</code> (prelive_artifact_cockpit.py)</li>
<li><code>EvidenceDepth</code> (research_quality_score.py) — <span class="doc-comment-inline">F231M: Production evidence depth diagnostics from KPI field aliases.</span></li>
<li><code>ScoreComponents</code> (research_quality_score.py)</li>
<li><code>OneButtonVerdict</code> (prelive_one_button_gate.py)</li>
<li><code>F223ArtifactResult</code> (prelive_one_button_gate.py)</li>
<li><code>WebSearchResult</code> (executor.py)</li>
<li><code>UmaState</code> (prelive_artifact_cockpit.py)</li>
<li><code>TraceSurface</code> (live_result_sanity.py) — <span class="doc-comment-inline">Parsed trace surface.</span></li>
<li><code>TriageResult</code> (live_artifact_triage.py)</li>
<li><code>Verdict</code> (live_kpi_extraction_guard.py)</li>
<li><code>SprintFamily</code> (live_artifact_triage.py)</li>
<li><code>ClassMigration</code> (migrate_dataclass_to_msgspec.py)</li>
<li><code>Overclaim</code> (qoder_reality_check.py)</li>
<li><code>Decision</code> (prelive_decision_gate.py)</li>
<li><code>SprintIdCollisionWarning</code> (prelive_artifact_cockpit.py)</li>
<li><code>NextAction</code> (prelive_artifact_cockpit.py)</li>
<li><code>ValidatorSurface</code> (live_result_sanity.py) — <span class="doc-comment-inline">Parsed validator surface.</span></li>
<li><code>ModuleReality</code> (qoder_reality_check.py)</li>
<li><code>ValidationResult</code> (live_multisource_validator.py)</li>
<li><code>FindingStruct</code> (bench_m1_runtime_gates.py)</li>
<li><code>SanityVerdict</code> (live_result_sanity.py)</li>
<li><code>F221ArtifactResult</code> (prelive_one_button_gate.py)</li>
<li><code>AcademicSearchArgs</code> (executor.py)</li>
<li><code>PythonExecuteResult</code> (executor.py)</li>
<li><code>CapabilityDeltaVerdict</code> (evidence_delta_memory.py)</li>
<li><code>SprintIdCollision</code> (prelive_artifact_cockpit.py)</li>
<li><code>QualityGate</code> (research_quality_score.py)</li>
<li><code>HighRiskGap</code> (qoder_reality_check.py)</li>
<li><code>_PrewarmSlot</code> (url_dedup.py) — <span class="doc-comment-inline">Single slot in the prewarm ring buffer.</span></li>
<li><code>FeedDiscoveryResult</code> (content_miner.py) — <span class="doc-comment-inline">Result of feed discovery.</span></li>
<li><code>ProbeReport</code> (prelive_decision_gate.py)</li>
<li><code>FileReadResult</code> (executor.py)</li>
<li><code>FileWriteArgs</code> (executor.py)</li>
<li><code>Verdict</code> (evidence_delta_memory.py)</li>
<li><code>SprintCollisionReport</code> (prelive_artifact_cockpit.py)</li>
<li><code>Grade</code> (research_quality_score.py)</li>
<li><code>ValidationFailure</code> (live_multisource_validator.py)</li>
<li><code>WebSearchArgs</code> (executor.py)</li>
<li><code>AcademicSearchResult</code> (executor.py)</li>
<li><code>FileReadArgs</code> (executor.py)</li>
<li><code>FileWriteResult</code> (executor.py)</li>
<li><code>PythonExecuteArgs</code> (executor.py)</li>
<li><code>DNSTunnelCheckResult</code> (executor.py)</li>
<li><code>FieldTransform</code> (migrate_dataclass_to_msgspec.py)</li>
<li><code>MigrationResult</code> (migrate_dataclass_to_msgspec.py)</li>
<li><code>EntityExtractionArgs</code> (executor.py)</li>
<li><code>EntityExtractionResult</code> (executor.py)</li>
<li><code>DNSTunnelCheckArgs</code> (executor.py)</li>
<li><code>_TimeoutError</code> (executor.py)</li>
</ul>
</details>

<details><summary><strong>Method</strong> (122)</summary>
<ul>
<li><code>extract_links</code> (content_miner.py)
<details><summary>Extract links from HTML with anchor context and scoring - M1 8GB optimized.</summary>
<div class="doc-comment">
<p>Extract links from HTML with anchor context and scoring - M1 8GB optimized.</p>
<p></p>
<p>Args:</p>
<p>html_content: Raw HTML content</p>
<p>base_url: Base URL for resolving relative links</p>
<p>max_links: Maximum number of links to extract (hard limit)</p>
<p></p>
<p>Returns:</p>
<p>List of dicts with 'url', 'anchor_text', 'context_snippet', 'rel_flags', 'score'</p>
</div>
</details>
</li>
<li><code>execute_with_limits</code> (executor.py)</li>
<li><code>_extract_pdf</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Extract from PDF using PyMuPDF with FOCA-style deep analysis.</span></li>
<li><code>_extract_pdf_hidden_content</code> (document_metadata_extractor.py)
<details><summary>Extract PDF hidden content:</summary>
<div class="doc-comment">
<p>Extract PDF hidden content:</p>
<p>- Invisible text layers (OCR vs embedded text mismatch)</p>
<p>- Hidden form fields</p>
<p>- JavaScript actions</p>
<p>- Embedded files</p>
<p>- Incremental updates</p>
</div>
</details>
</li>
<li><code>_extract_docx</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Extract from DOCX with FOCA-style revision history and fonts.</span></li>
<li><code>_extract_xlsx</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Extract from XLSX with embedded fonts.</span></li>
<li><code>_extract_pptx</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Extract from PPTX with speaker notes and hidden slides.</span></li>
<li><code>_analyze_macros_olevba</code> (document_metadata_extractor.py)
<details><summary>Analyze macros using olevba for C2 URLs and suspicious API calls.</summary>
<div class="doc-comment">
<p>Analyze macros using olevba for C2 URLs and suspicious API calls.</p>
<p>Returns analysis results with threat indicators.</p>
</div>
</details>
</li>
<li><code>_extract_email</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Extract email headers with forensics analysis.</span></li>
<li><code>__init__</code> (url_dedup.py)</li>
<li><code>extract</code> (document_metadata_extractor.py)
<details><summary>Extract FOCA-style forensic metadata from document.</summary>
<div class="doc-comment">
<p>Extract FOCA-style forensic metadata from document.</p>
<p></p>
<p>Args:</p>
<p>content: Raw document bytes</p>
<p>url: Source URL for extension detection</p>
<p></p>
<p>Returns:</p>
<p>Dict with keys: author, creator, organization, company, template_path,</p>
<p>last_modified_by, revision_count, internal_paths, gps_coords,</p>
<p>has_macros, macro_analysis, embedded_fonts, hidden_content,</p>
<p>email_headers, presentation_notes, cad_metadata, format</p>
</div>
</details>
</li>
<li><code>_extract_dxf</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Extract metadata from DXF files (CAD drawings).</span></li>
<li><code>add_batch</code> (url_dedup.py)
<details><summary>Bulk add items using round-robin slot selection.</summary>
<div class="doc-comment">
<p>Bulk add items using round-robin slot selection.</p>
<p></p>
<p>Returns:</p>
<p>List[bool] — True for each new item, False for duplicates.</p>
<p></p>
<p>On hit: prewarms the OTHER slot in the background (if enabled).</p>
</div>
</details>
</li>
<li><code>_extract_svg</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Extract metadata from SVG files.</span></li>
<li><code>_extract_odt</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Extract from ODT (OpenDocument Text).</span></li>
<li><code>_analyze_received_chain</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Analyze Received headers to build infrastructure chain.</span></li>
<li><code>_mine_with_traflatura</code> (content_miner.py)
<details><summary>Mine using traflatura in minimal mode.</summary>
<div class="doc-comment">
<p>Mine using traflatura in minimal mode.</p>
<p></p>
<p>Memory optimization:</p>
<p>- disable_comments: Don't store comment nodes</p>
<p>- no_tables: Skip table extraction (expensive)</p>
<p>- include_tables: False to save memory</p>
<p>- deduplicate: True to reduce memory</p>
</div>
</details>
</li>
<li><code>_extract_image</code> (content_miner.py) — <span class="doc-comment-inline">Extract metadata from images (EXIF).</span></li>
<li><code>_extract_pdf_gps</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Extract GPS coordinates from embedded images.</span></li>
<li><code>_parse_exif_gps</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Parse GPS from EXIF data.</span></li>
<li><code>_extract_pptx_hidden_slides</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Extract hidden slides from PPTX.</span></li>
<li><code>to_md</code> (live_result_sanity.py)</li>
<li><code>_extract_docx_revisions</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Extract revision history from DOCX (track changes).</span></li>
<li><code>__init__</code> (url_dedup.py)</li>
<li><code>_extract_pdf</code> (content_miner.py) — <span class="doc-comment-inline">Extract metadata from PDF.</span></li>
<li><code>check_and_add_batch</code> (url_dedup.py)
<details><summary>Atomic check-and-add batch — returns (seen_before, is_new) per item.</summary>
<div class="doc-comment">
<p>Atomic check-and-add batch — returns (seen_before, is_new) per item.</p>
<p></p>
<p>Canonical cross-process dedup primitive: distinguishes true negatives</p>
<p>(seen_before=False, is_new=True → fresh, first time ever seen)</p>
<p>from false positives (seen_before=True, is_new=False → deduped).</p>
<p></p>
<p>Args:</p>
<p>items: List of URL/fingerprint strings to add</p>
<p></p>
<p>Returns:</p>
<p>List[(seen_before, is_new)] — per item:</p>
<p>- seen_before: True if item was already in filter BEFORE this call</p>
<p>- is_new:      True if item was NOT in filter after this call</p>
<p></p>
<p>Uses Rust check_and_add_batch (parallel xxHash3-64, rayon-powered).</p>
<p>Single msync at the end. Thread-safe via threading.Lock.</p>
</div>
</details>
</li>
<li><code>_mine_with_trafilex</code> (content_miner.py)
<details><summary>Mine using trafilex (Rust-based, minimal memory).</summary>
<div class="doc-comment">
<p>Mine using trafilex (Rust-based, minimal memory).</p>
<p></p>
<p>trafilex is a Rust wrapper that processes HTML in streaming mode</p>
<p>without building large DOM trees.</p>
</div>
</details>
</li>
<li><code>mine_html</code> (content_miner.py)
<details><summary>Extract clean text from HTML with minimal memory usage.</summary>
<div class="doc-comment">
<p>Extract clean text from HTML with minimal memory usage.</p>
<p></p>
<p>Args:</p>
<p>html_content: Raw HTML content</p>
<p>url: Source URL for metadata</p>
<p>include_metadata: Extract metadata (title, author, date)</p>
<p></p>
<p>Returns:</p>
<p>MiningResult with extracted content</p>
</div>
</details>
</li>
<li><code>_extract_links_selectolax</code> (content_miner.py) — <span class="doc-comment-inline">Extract links using selectolax (fast, safe CSS selectors).</span></li>
<li><code>discover_feeds</code> (content_miner.py)
<details><summary>Discover RSS/Atom feeds in HTML content.</summary>
<div class="doc-comment">
<p>Discover RSS/Atom feeds in HTML content.</p>
<p></p>
<p>Args:</p>
<p>html_content: Raw HTML content</p>
<p>base_url: Base URL for resolving relative links</p>
<p></p>
<p>Returns:</p>
<p>FeedDiscoveryResult with discovered feed URLs</p>
</div>
</details>
</li>
<li><code>_extract_docx_extended_props</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Extract extended properties from DOCX ZIP.</span></li>
<li><code>_extract_pdf_fallback</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Fallback PDF extraction without PyMuPDF.</span></li>
<li><code>_extract_pdf_fonts</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Extract embedded fonts for geographic indication.</span></li>
<li><code>put_many</code> (url_dedup.py)
<details><summary>Bulk add items to the mmap-backed filter.</summary>
<div class="doc-comment">
<p>Bulk add items to the mmap-backed filter.</p>
<p></p>
<p>Args:</p>
<p>items: List of URL/fingerprint strings to add</p>
<p></p>
<p>Returns:</p>
<p>List[bool] — True for each new item, False for duplicates.</p>
<p></p>
<p>Uses Rust add_batch (parallel xxHash3-64, rayon-powered).</p>
<p>Single msync at the end amortizes sync overhead.</p>
<p>Thread-safe via threading.Lock.</p>
</div>
</details>
</li>
<li><code>_clean_html_basic</code> (content_miner.py)
<details><summary>Basic HTML cleaning — delegates to canonical html_text_fast when available.</summary>
<div class="doc-comment">
<p>Basic HTML cleaning — delegates to canonical html_text_fast when available.</p>
<p></p>
<p>Uses module-level compiled patterns as emergency fallback</p>
<p>when canonical helper is unavailable.</p>
</div>
</details>
</li>
<li><code>_init_cache</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Initialize SQLite cache.</span></li>
<li><code>_extract_docx_fallback</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Fallback DOCX extraction without python-docx.</span></li>
<li><code>_mine_with_fallback</code> (content_miner.py)
<details><summary>Ultimate fallback using regex-based extraction.</summary>
<div class="doc-comment">
<p>Ultimate fallback using regex-based extraction.</p>
<p></p>
<p>No dependencies - pure Python regex for maximum compatibility.</p>
</div>
</details>
</li>
<li><code>_extract_xlsx_fallback</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Fallback XLSX extraction without openpyxl.</span></li>
<li><code>_extract_msg</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Extract from Outlook MSG files.</span></li>
<li><code>_extract_pptx_speaker_notes</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Extract speaker notes from PPTX.</span></li>
<li><code>_extract_vba_code</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Extract VBA code from Office documents.</span></li>
<li><code>_get_cached</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Get cached extraction result.</span></li>
<li><code>_cache</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Cache extraction result.</span></li>
<li><code>_extract_company_from_pdf_producer</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Extract company name from PDF producer string.</span></li>
<li><code>mine_text</code> (content_miner.py)
<details><summary>Mine plain text (minimal processing).</summary>
<div class="doc-comment">
<p>Mine plain text (minimal processing).</p>
<p></p>
<p>Args:</p>
<p>text: Plain text content</p>
<p>url: Source URL</p>
<p></p>
<p>Returns:</p>
<p>MiningResult with cleaned text</p>
</div>
</details>
</li>
<li><code>_extract_docx_fonts</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Extract embedded fonts from DOCX document.</span></li>
<li><code>batch_mine</code> (content_miner.py)
<details><summary>Batch mine multiple HTML documents.</summary>
<div class="doc-comment">
<p>Batch mine multiple HTML documents.</p>
<p></p>
<p>Args:</p>
<p>html_list: List of (html_content, url) tuples</p>
<p>include_metadata: Extract metadata</p>
<p></p>
<p>Returns:</p>
<p>List of MiningResult objects</p>
</div>
</details>
</li>
<li><code>_extract_from_link_tags</code> (content_miner.py) — <span class="doc-comment-inline">Extract feed URLs from &lt;link rel="alternate"&gt; tags.</span></li>
<li><code>__init__</code> (content_miner.py)
<details><summary>Initialize RustMiner.</summary>
<div class="doc-comment">
<p>Initialize RustMiner.</p>
<p></p>
<p>Args:</p>
<p>prefer_rust: Prefer Rust-based libraries (trafilex) over Python</p>
</div>
</details>
</li>
<li><code>extract_jsonld</code> (content_miner.py) — <span class="doc-comment-inline">Extract JSON-LD script blocks from HTML.</span></li>
<li><code>_extract_sync</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Blocking extraction - runs in executor.</span></li>
<li><code>_extract_pdf_revisions</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Extract revision count from PDF.</span></li>
<li><code>_extract_xlsx_extended_props</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Extract extended properties from XLSX ZIP.</span></li>
<li><code>to_dict</code> (migrate_gather_to_safe_gather.py)</li>
<li><code>_extract_pdf_template_path</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Extract template path from PDF metadata.</span></li>
<li><code>_extract_email_field</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Extract field from email content.</span></li>
<li><code>_extract_xml_value</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Extract value from XML tag.</span></li>
<li><code>_score_link</code> (content_miner.py) — <span class="doc-comment-inline">Calculate link score (0-1).</span></li>
<li><code>add</code> (url_dedup.py)</li>
<li><code>__contains__</code> (url_dedup.py)</li>
<li><code>_prewarm_secondary</code> (url_dedup.py) — <span class="doc-comment-inline">Background: touch secondary slot to fault in its pages.</span></li>
<li><code>sync</code> (url_dedup.py) — <span class="doc-comment-inline">Sync all slots to disk.</span></li>
<li><code>_extract_title_fallback</code> (content_miner.py) — <span class="doc-comment-inline">Extract title using regex (fallback)</span></li>
<li><code>_resolve_url</code> (content_miner.py) — <span class="doc-comment-inline">Resolve relative URL to absolute.</span></li>
<li><code>extract</code> (content_miner.py) — <span class="doc-comment-inline">Extract metadata based on content-type.</span></li>
<li><code>sync</code> (url_dedup.py) — <span class="doc-comment-inline">Force durable sync to disk (MS_SYNC).</span></li>
<li><code>__contains__</code> (url_dedup.py) — <span class="doc-comment-inline">Check all slots (OR semantics — seen in any slot = seen).</span></li>
<li><code>__len__</code> (url_dedup.py) — <span class="doc-comment-inline">Total items across all slots (sum of per-slot counters).</span></li>
<li><code>_heuristic_discovery</code> (content_miner.py) — <span class="doc-comment-inline">Heuristic feed discovery based on common paths.</span></li>
<li><code>_check_docx_macros</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Check if DOCX/XLSX/PPTX contains VBA macros.</span></li>
<li><code>add_batch</code> (url_dedup.py)
<details><summary>Bulk add — returns True per new item, False per duplicate.</summary>
<div class="doc-comment">
<p>Bulk add — returns True per new item, False per duplicate.</p>
<p></p>
<p>Optional method — implementations that don't provide it will</p>
<p>raise AttributeError, which dedupe_url_list catches and</p>
<p>falls back to per-item add().</p>
</div>
</details>
</li>
<li><code>__contains__</code> (url_dedup.py)
<details><summary>Check if an item might have been seen before.</summary>
<div class="doc-comment">
<p>Check if an item might have been seen before.</p>
<p></p>
<p>Specialised on `str` because every concrete implementation</p>
<p>(RustUrlSetAdapter, RotatingBloomFilterAdapter, BloomFilter)</p>
<p>keys exclusively on URLs.</p>
</div>
</details>
</li>
<li><code>add</code> (url_dedup.py)</li>
<li><code>_check_pymupdf</code> (content_miner.py)</li>
<li><code>_check_exifread</code> (content_miner.py)</li>
<li><code>_check_pillow</code> (content_miner.py)</li>
<li><code>_execute_handler</code> (executor.py) — <span class="doc-comment-inline">Execute tool handler with validated arguments.</span></li>
<li><code>_find_internal_paths</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Find internal file paths in text.</span></li>
<li><code>byte_size</code> (url_dedup.py)</li>
<li><code>__len__</code> (url_dedup.py)</li>
<li><code>reset</code> (url_dedup.py)</li>
<li><code>_check_trafilex</code> (content_miner.py) — <span class="doc-comment-inline">Check if trafilex (Rust-based) is available</span></li>
<li><code>_check_traflatura</code> (content_miner.py) — <span class="doc-comment-inline">Check if traflatura is available</span></li>
<li><code>_convert_gps</code> (content_miner.py) — <span class="doc-comment-inline">Convert EXIF GPS coordinates to decimal.</span></li>
<li><code>_select_slot</code> (url_dedup.py) — <span class="doc-comment-inline">Select the next slot in round-robin order.</span></li>
<li><code>_is_cache_valid</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Check if cache entry is still valid (within TTL).</span></li>
<li><code>_get_extension</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Get file extension from URL.</span></li>
<li><code>add_batch</code> (url_dedup.py) — <span class="doc-comment-inline">Bulk add — returns True per new item, False per duplicate.</span></li>
<li><code>__init__</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Initialize extractor with SQLite cache.</span></li>
<li><code>_get_cache_key</code> (document_metadata_extractor.py) — <span class="doc-comment-inline">Generate cache key from first 1024 bytes.</span></li>
<li><code>__init__</code> (url_dedup.py)</li>
<li><code>byte_size</code> (url_dedup.py)</li>
<li><code>__init__</code> (content_miner.py)</li>
<li><code>add</code> (url_dedup.py) — <span class="doc-comment-inline">Add an item to the deduplication set.</span></li>
<li><code>__init__</code> (url_dedup.py)</li>
<li><code>__post_init__</code> (content_miner.py)</li>
<li><code>_extract_docx</code> (content_miner.py) — <span class="doc-comment-inline">Extract metadata from DOCX (placeholder - would need python-docx).</span></li>
<li><code>registry</code> (executor.py) — <span class="doc-comment-inline">Expose underlying registry for direct tool registration.</span></li>
<li><code>register</code> (executor.py) — <span class="doc-comment-inline">Delegate to underlying registry.</span></li>
<li><code>get_tool</code> (executor.py) — <span class="doc-comment-inline">Delegate to underlying registry.</span></li>
<li><code>__init__</code> (url_dedup.py)</li>
<li><code>add</code> (url_dedup.py)</li>
<li><code>__contains__</code> (url_dedup.py)</li>
<li><code>__contains__</code> (url_dedup.py)</li>
<li><code>add</code> (url_dedup.py)</li>
<li><code>__contains__</code> (url_dedup.py)</li>
<li><code>__len__</code> (url_dedup.py)</li>
<li><code>clear</code> (url_dedup.py)</li>
<li><code>path</code> (url_dedup.py)</li>
<li><code>capacity</code> (url_dedup.py)</li>
<li><code>fp_rate</code> (url_dedup.py)</li>
<li><code>num_slots</code> (url_dedup.py)</li>
<li><code>path</code> (url_dedup.py)</li>
<li><code>__init__</code> (content_miner.py)</li>
<li><code>to_dict</code> (prelive_one_button_gate.py)</li>
<li><code>to_dict</code> (prelive_one_button_gate.py)</li>
<li><code>to_dict</code> (live_multisource_validator.py)</li>
<li><code>__init__</code> (executor.py)</li>
<li><code>to_dict</code> (prelive_artifact_cockpit.py)</li>
<li><code>to_dict</code> (prelive_artifact_cockpit.py)</li>
<li><code>to_dict</code> (live_result_sanity.py)</li>
</ul>
</details>

<details><summary><strong>Constant</strong> (161)</summary>
<ul>
<li><code>OLD_PARAM_NAMES</code> (live_kpi_extraction_guard.py)</li>
<li><code>RUNTIME_IMPORT_PREFIXES</code> (live_kpi_extraction_guard.py)</li>
<li><code>RUNTIME_IMPORT_PREFIXES</code> (live_measurement_extraction_guard.py)</li>
<li><code>_HASH_TEMPLATES</code> (bench_f214_python314_runtime.py)</li>
<li><code>SKIP_PATH_PARTS</code> (migrate_gather_to_safe_gather.py)</li>
<li><code>_INFORMATIONAL_FIELDS</code> (f234_validate_nonfeed_live_report.py)</li>
<li><code>_R3_FIELD_LABELS</code> (f234_validate_nonfeed_live_report.py)</li>
<li><code>C2_URL_PATTERNS</code> (document_metadata_extractor.py)</li>
<li><code>REPLACEMENT_MAP</code> (migrate_gather_to_safe_gather.py)</li>
<li><code>SUSPICIOUS_API_PATTERNS</code> (document_metadata_extractor.py)</li>
<li><code>BENCHMARKS</code> (bench_m1_runtime_gates.py)</li>
<li><code>SAFE_GATHER_FUNCTIONS</code> (migrate_gather_to_safe_gather.py)</li>
<li><code>INTERNAL_PATH_PATTERNS</code> (document_metadata_extractor.py)</li>
<li><code>VBA_PROJECT_PATTERNS</code> (document_metadata_extractor.py)</li>
<li><code>PDF_MACRO_PATTERNS</code> (document_metadata_extractor.py)</li>
<li><code>SVG_METADATA_PATTERNS</code> (document_metadata_extractor.py)</li>
<li><code>REQUIRED_EXPORTS</code> (live_measurement_extraction_guard.py)</li>
<li><code>QUALITY_HELPERS</code> (live_measurement_extraction_guard.py)</li>
<li><code>KPI_MODULE_REQUIRED_EXPORTS</code> (live_kpi_extraction_guard.py)</li>
<li><code>TERMINALITY_HELPERS</code> (live_measurement_extraction_guard.py)</li>
<li><code>_RE_FROM_ASYNC_HELPERS</code> (migrate_gather_to_safe_gather.py)</li>
<li><code>_RE_FROM_HLEDAC_ASYNC_HELPERS</code> (migrate_gather_to_safe_gather.py)</li>
<li><code>SUPPORTED_EXTENSIONS</code> (document_metadata_extractor.py)</li>
<li><code>MAX_INTERNAL_PATHS</code> (document_metadata_extractor.py)</li>
<li><code>MAX_EMBEDDED_FONTS</code> (document_metadata_extractor.py)</li>
<li><code>MAX_PDF_OBJECTS</code> (document_metadata_extractor.py)</li>
<li><code>MAX_MACRO_ANALYSIS_CHARS</code> (document_metadata_extractor.py)</li>
<li><code>MAX_EMAIL_HEADERS</code> (document_metadata_extractor.py)</li>
<li><code>MAX_REVISIONS</code> (document_metadata_extractor.py)</li>
<li><code>MAX_HIDDEN_SLIDES</code> (document_metadata_extractor.py)</li>
<li><code>MAX_GPS_COORDS</code> (document_metadata_extractor.py)</li>
<li><code>CACHE_TTL_DAYS</code> (document_metadata_extractor.py)</li>
<li><code>EXTRACTION_TIMEOUT</code> (document_metadata_extractor.py)</li>
<li><code>PDF_DEEP_TIMEOUT</code> (document_metadata_extractor.py)</li>
<li><code>CACHE_DIR</code> (document_metadata_extractor.py)</li>
<li><code>CACHE_DB_PATH</code> (document_metadata_extractor.py)</li>
<li><code>RECEIVED_HEADER_PATTERN</code> (document_metadata_extractor.py)</li>
<li><code>X_ORIGINATING_IP_PATTERN</code> (document_metadata_extractor.py)</li>
<li><code>MESSAGE_ID_DOMAIN_PATTERN</code> (document_metadata_extractor.py)</li>
<li><code>_PROBABLES_AVAILABLE</code> (url_dedup.py)</li>
<li><code>_RUST_XXHASH_AVAILABLE</code> (url_dedup.py)</li>
<li><code>_RUST_TEXT_NORM_AVAILABLE</code> (url_dedup.py)</li>
<li><code>_RUST_BATCH_HASH_AVAILABLE</code> (url_dedup.py)</li>
<li><code>DEFAULT_URL_ESTIMATE</code> (url_dedup.py)</li>
<li><code>DEFAULT_FPR</code> (url_dedup.py)</li>
<li><code>MAX_URL_ESTIMATE</code> (url_dedup.py)</li>
<li><code>_RUST_BLOOM_AVAILABLE</code> (url_dedup.py)</li>
<li><code>_RUST_URL_DEDUP_AVAILABLE</code> (url_dedup.py)</li>
<li><code>_RUST_URL_ENGINE_AVAILABLE</code> (url_dedup.py)</li>
<li><code>_RUST_TRACKING_PARAMS</code> (url_dedup.py)</li>
<li><code>_RUST_MMAP_BLOOM_AVAILABLE</code> (url_dedup.py)</li>
<li><code>_HAVE_BLOOM_PREWARM</code> (url_dedup.py)</li>
<li><code>_PREWARM_SLOTS</code> (url_dedup.py)</li>
<li><code>_PREFILTER_HASH_THRESHOLD</code> (url_dedup.py)</li>
<li><code>_PREFILTER_WORKERS</code> (url_dedup.py)</li>
<li><code>_NORMALIZE_PARALLEL_THRESHOLD</code> (url_dedup.py)</li>
<li><code>_NORMALIZE_WORKERS</code> (url_dedup.py)</li>
<li><code>_CLEAN_PATTERNS</code> (content_miner.py)</li>
<li><code>_RUST_XXHASH_AVAILABLE</code> (content_miner.py)</li>
<li><code>BENCH_FILE</code> (bench_f214_python314_runtime.py)</li>
<li><code>UNIVERSAL_ROOT</code> (bench_f214_python314_runtime.py)</li>
<li><code>PROJECT_ROOT</code> (bench_f214_python314_runtime.py)</li>
<li><code>_BENCHMARK_TO_ACQUISITION_PROFILE</code> (prelive_one_button_gate.py)</li>
<li><code>_EXPECTED_REPO_ROOT</code> (prelive_one_button_gate.py)</li>
<li><code>_UNIVERSAL_ROOT</code> (prelive_one_button_gate.py)</li>
<li><code>_F221_REQUIRED_PROBES</code> (prelive_one_button_gate.py)</li>
<li><code>_F223_ARTIFACT_ALIASES</code> (prelive_one_button_gate.py)</li>
<li><code>_F223_REQUIRED_PROBES</code> (prelive_one_button_gate.py)</li>
<li><code>_F223_OPTIONAL_PROBES</code> (prelive_one_button_gate.py)</li>
<li><code>_CROSS_SPRINT_REQUIRED</code> (prelive_one_button_gate.py)</li>
<li><code>REPO_ROOT</code> (live_measurement_extraction_guard.py)</li>
<li><code>BENCHMARKS</code> (live_measurement_extraction_guard.py)</li>
<li><code>LIVE_SPRINT_MEASUREMENT</code> (live_measurement_extraction_guard.py)</li>
<li><code>SCHEMA_MODULE</code> (live_measurement_extraction_guard.py)</li>
<li><code>PARSER_MODULE</code> (live_measurement_extraction_guard.py)</li>
<li><code>MARKDOWN_MODULE</code> (live_measurement_extraction_guard.py)</li>
<li><code>SCHEMA_CLASSES</code> (live_measurement_extraction_guard.py)</li>
<li><code>PROBE_ROOT_ENV</code> (prelive_decision_gate.py)</li>
<li><code>_F224_BLOCKING_PROBES</code> (prelive_decision_gate.py)</li>
<li><code>_F224_WARNING_PROBES</code> (prelive_decision_gate.py)</li>
<li><code>_F224_BLOCKING_PROFILES</code> (prelive_decision_gate.py)</li>
<li><code>_FALLBACK_SCHEMA_MARKERS</code> (prelive_decision_gate.py)</li>
<li><code>_PROVIDER_SURFACE_ALIASES</code> (prelive_decision_gate.py)</li>
<li><code>_F231_BLOCKING_PROFILES</code> (prelive_decision_gate.py)</li>
<li><code>_F231_BLOCKING_PROBES</code> (prelive_decision_gate.py)</li>
<li><code>_F224_CONFIDENCE_POLICY_CANONICAL</code> (prelive_decision_gate.py)</li>
<li><code>_F224_CONFIDENCE_POLICY_ALIASES</code> (prelive_decision_gate.py)</li>
<li><code>VALIDATOR_SCHEMA_VERSION</code> (live_multisource_validator.py)</li>
<li><code>TERMINAL_STATES</code> (live_multisource_validator.py)</li>
<li><code>TERMINAL_OUTCOME_STATES</code> (live_multisource_validator.py)</li>
<li><code>BENCH_FILE</code> (bench_m1_runtime_gates.py)</li>
<li><code>UNIVERSAL_ROOT</code> (bench_m1_runtime_gates.py)</li>
<li><code>REPORTS_DIR</code> (bench_m1_runtime_gates.py)</li>
<li><code>_HAS_PSUTIL</code> (bench_m1_runtime_gates.py)</li>
<li><code>_HAS_SELECTOLAX</code> (bench_m1_runtime_gates.py)</li>
<li><code>_HAS_BS4</code> (bench_m1_runtime_gates.py)</li>
<li><code>_DNS_TUNNEL_EXECUTOR</code> (executor.py)</li>
<li><code>_DNS_TUNNEL_EXECUTOR_LOCK</code> (executor.py)</li>
<li><code>_SPRINT_ID_RE</code> (prelive_artifact_cockpit.py)</li>
<li><code>_EXPECTED_REPO_ROOT</code> (prelive_artifact_cockpit.py)</li>
<li><code>_UNIVERSAL_ROOT</code> (prelive_artifact_cockpit.py)</li>
<li><code>_TERMINALITY_UNSATISFIED_VERDICTS</code> (live_result_sanity.py)</li>
<li><code>_NONFEED_EVIDENCE_MISSING_VERDICTS</code> (live_result_sanity.py)</li>
<li><code>_TRACE_STALE_VERDICTS</code> (live_result_sanity.py)</li>
<li><code>_SWAP_GATE_THRESHOLD_GIB</code> (live_artifact_triage.py)</li>
<li><code>_HIGH_SWAP_THRESHOLD_GIB</code> (live_artifact_triage.py)</li>
<li><code>REPO_ROOT</code> (live_kpi_extraction_guard.py)</li>
<li><code>BENCHMARKS</code> (live_kpi_extraction_guard.py)</li>
<li><code>LIVE_SPRINT_MEASUREMENT</code> (live_kpi_extraction_guard.py)</li>
<li><code>NEXT_ACTION_MODULE</code> (live_kpi_extraction_guard.py)</li>
<li><code>KPI_MODULE</code> (live_kpi_extraction_guard.py)</li>
<li><code>ROOT</code> (migrate_dataclass_to_msgspec.py)</li>
<li><code>REPO_ROOT</code> (qoder_reality_check.py)</li>
<li><code>QODER_ROOT_DEFAULT</code> (qoder_reality_check.py)</li>
<li><code>OUTPUT_JSON_DEFAULT</code> (qoder_reality_check.py)</li>
<li><code>OUTPUT_MD_DEFAULT</code> (qoder_reality_check.py)</li>
<li><code>VERDICT_CANONICAL_OWNER</code> (qoder_reality_check.py)</li>
<li><code>VERDICT_ACTIVE_RUNTIME</code> (qoder_reality_check.py)</li>
<li><code>VERDICT_ACTIVE_PIPELINE</code> (qoder_reality_check.py)</li>
<li><code>VERDICT_ACTIVE_SIDECAR</code> (qoder_reality_check.py)</li>
<li><code>VERDICT_ACTIVE_DIAGNOSTIC</code> (qoder_reality_check.py)</li>
<li><code>VERDICT_ACTIVE_SUPPORT</code> (qoder_reality_check.py)</li>
<li><code>VERDICT_ACTIVE_CAPABILITY</code> (qoder_reality_check.py)</li>
<li><code>VERDICT_ACTIVE_ENTRYPOINT</code> (qoder_reality_check.py)</li>
<li><code>VERDICT_PATH_AUTHORITY</code> (qoder_reality_check.py)</li>
<li><code>VERDICT_SECURITY_CRITICAL</code> (qoder_reality_check.py)</li>
<li><code>VERDICT_STORAGE_AUTHORITY</code> (qoder_reality_check.py)</li>
<li><code>VERDICT_TRANSPORT_AUTHORITY</code> (qoder_reality_check.py)</li>
<li><code>VERDICT_DONOR</code> (qoder_reality_check.py)</li>
<li><code>VERDICT_DONOR_OR_OPTIONAL</code> (qoder_reality_check.py)</li>
<li><code>VERDICT_LEGACY</code> (qoder_reality_check.py)</li>
<li><code>VERDICT_DEPRECATED</code> (qoder_reality_check.py)</li>
<li><code>VERDICT_TEST_ONLY</code> (qoder_reality_check.py)</li>
<li><code>VERDICT_DEAD_OR_UNWIRED</code> (qoder_reality_check.py)</li>
<li><code>VERDICT_MISSING_DOC_TARGET</code> (qoder_reality_check.py)</li>
<li><code>VERDICT_UNKNOWN_NEEDS_REVIEW</code> (qoder_reality_check.py)</li>
<li><code>CANONICAL_OWNER_PATHS</code> (qoder_reality_check.py)</li>
<li><code>ACTIVE_SUPPORT_PATHS</code> (qoder_reality_check.py)</li>
<li><code>ACTIVE_ENTRYPOINT_PATHS</code> (qoder_reality_check.py)</li>
<li><code>PATH_AUTHORITY_PATHS</code> (qoder_reality_check.py)</li>
<li><code>ACTIVE_CAPABILITY_PATHS</code> (qoder_reality_check.py)</li>
<li><code>ACTIVE_RUNTIME_PATHS</code> (qoder_reality_check.py)</li>
<li><code>ACTIVE_PIPELINE_PATHS</code> (qoder_reality_check.py)</li>
<li><code>ACTIVE_SIDECAR_PATHS</code> (qoder_reality_check.py)</li>
<li><code>STORAGE_AUTHORITY_PATHS</code> (qoder_reality_check.py)</li>
<li><code>TRANSPORT_AUTHORITY_PATHS</code> (qoder_reality_check.py)</li>
<li><code>SECURITY_CRITICAL_PATHS</code> (qoder_reality_check.py)</li>
<li><code>ACTIVE_DIAGNOSTIC_PATHS</code> (qoder_reality_check.py)</li>
<li><code>LEGACY_PATHS</code> (qoder_reality_check.py)</li>
<li><code>DEPRECATED_PATHS</code> (qoder_reality_check.py)</li>
<li><code>TEST_ONLY_PATHS</code> (qoder_reality_check.py)</li>
<li><code>DEAD_OR_UNWIRED_PATHS</code> (qoder_reality_check.py)</li>
<li><code>DONOR_OR_OPTIONAL_PATHS</code> (qoder_reality_check.py)</li>
<li><code>HAS_CANONICAL_WRITE</code> (qoder_reality_check.py)</li>
<li><code>HAS_NETWORK_PATH</code> (qoder_reality_check.py)</li>
<li><code>HAS_MLX_IMPORT</code> (qoder_reality_check.py)</li>
<li><code>HAS_SUBPROCESS_SPAWN</code> (qoder_reality_check.py)</li>
<li><code>HAS_SECRET_EXPORT_RISK</code> (qoder_reality_check.py)</li>
<li><code>PRIVATE_HELPER_PATHS</code> (qoder_reality_check.py)</li>
<li><code>PRODUCTION_CALL_PATHS</code> (qoder_reality_check.py)</li>
<li><code>OVERCLAIM_KEYWORDS</code> (qoder_reality_check.py)</li>
</ul>
</details>



## Metrics

| Metric | Value |
|---|---|
| Files | 119 |
| Total lines | 36873 |
| Avg lines/file | 309 |
| Languages | Python |
| Outgoing deps | 1 |
| Incoming deps | 2 |
| Tier | 1 |

