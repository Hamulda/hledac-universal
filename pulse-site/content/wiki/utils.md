+++
title = "utils/"
weight = 36
description = "<think> Let me analyze the STRUCTURAL CONTEXT for the `utils` module and create a module overview.  Key observations from the context:  1. **Module Purpose**: This is a large utilities module with man..."

[extra]
tier = 1
file_count = 130
total_lines = 35916
languages = "Python"
has_mermaid = true
+++

<think>
Let me analyze the STRUCTURAL CONTEXT for the `utils` module and create a module overview.

Key observations from the context:

1. **Module Purpose**: This is a large utilities module with many helper functions, classes, and utilities. It provides foundational infrastructure for the larger system.

2. **Scale**: 
 - 130 Python files
 - 35,916 total lines
 - Average 276 lines per file
 - 4 sub-modules (coreml, mlx_memory, patterns, text)

3. **No outgoing dependencies** - This means it doesn't import from other modules in this codebase, making it a foundation layer.

4. **Incoming dependencies (12 files across 9 modules)**:
 - export/ (3 files)
 - brain/ (2 files)
 - coordinators/ (1 file)
 - enhanced_research.py/ (1 file)
 - core/ (1 file)
 - recon/ (1 file)
 - cache/ (1 file)
 - layers/ (1 file)
 - runtime/ (1 file)

5. **Key Symbols** (important abstractions):
 - Sprint LifecycleManager - manages sprint lifecycle state machine
 - PyCache Dict - bounded LRU cache with TTL
 - Parallel ExecutionOptimizer - parallel execution optimization
 - Semantic Deduplicator - semantic deduplication using embeddings
 - Intelligent Cache - ML-enhanced cache with ARC eviction
 - UmaWatchdog - memory pressure monitoring
 - Pattern Matcher - IOC extraction with Aho-Corasick

6. **Functional areas**:
 - Caching (multiple cache implementations)
 - Memory management (MLX, UMA budget)
 - Async utilities (parallel execution, task management)
 - Pattern matching (IOC extraction)
 - Semantic analysis (deduplication, embeddings)
 - Text utilities (Unicode analysis, encoding)
 - Query expansion (search query generation)
 - Sprint lifecycle management
 - DNS resolution (batch DNS)
 - JSON handling (msgspec_json)
 - URL filtering
 - Execution optimization

7. **Architectural role**: This is a foundational utility module with no outgoing dependencies - it provides infrastructure services to many other modules. It has moderate fan-in (12 dependent files across 9 modules), meaning changes here could impact multiple parts of the system.

8. **Complexity**: 
 - Very large module (130 files, 35K+ lines)
 - High symbol density (classes, functions)
 - Multiple specialized sub-modules

Now I'll write the module overview following the rules.
</think>

Provides foundational infrastructure utilities across caching, memory management, async execution, pattern matching, and semantic analysis. The module is a self-contained foundation layer with no internal dependencies, delivering services to nine other modules including export, brain, coordinators, and runtime. Key abstractions include the sprint lifecycle state machine, multiple cache implementations (LRU with TTL, generational weakref, ARC-based intelligent cache), parallel execution optimization with M1-aware resource allocation, semantic deduplication via embeddings, and Unicode attack analysis for security hardening. Memory management functionality monitors unified memory pressure and dynamically adjusts MLX cache limits based on available capacity. Pattern matching capabilities extract indicators of compromise (CVEs, hashes, cryptocurrency addresses) using Aho-Corasick automata. The module's breadth—spanning 130 files and 35,916 lines—makes it a high-value target for optimization, though its moderate fan-in of 12 dependent files means alterations carry limited but non-trivial blast radius.

## Dependency Diagram

{% mermaid() %}
graph LR
    m_utils["<b>utils/</b>"]
    style m_utils fill:#a78bfa,color:#0d0d0d,stroke:#a78bfa
    m_layers["layers/"]
    m_layers -->|4| m_utils
    m_enhanced_research_py["enhanced_research.py/"]
    m_enhanced_research_py -->|4| m_utils
    m_brain["brain/"]
    m_brain -->|4| m_utils
    m_export["export/"]
    m_export -->|3| m_utils
    m_cache["cache/"]
    m_cache -->|1| m_utils
    m_core["core/"]
    m_core -->|1| m_utils
    m_recon["recon/"]
    m_recon -->|1| m_utils
    m_runtime["runtime/"]
    m_runtime -->|1| m_utils
    classDef default fill:#1a1a2e,stroke:#a78bfa,color:#e0e0e0
    click m_utils "/wiki/utils/"
    click m_layers "/wiki/layers/"
    click m_enhanced_research_py "/wiki/enhanced_research.py/"
    click m_brain "/wiki/brain/"
    click m_export "/wiki/export/"
    click m_cache "/wiki/cache/"
    click m_core "/wiki/core/"
    click m_recon "/wiki/recon/"
    click m_runtime "/wiki/runtime/"
{% end %}

## Structure

### Sub-modules

- [**coreml/**](/wiki/utils-coreml/) — 5 files, 786 lines (Python)
- [**mlx_memory/**](/wiki/utils-mlx_memory/) — 6 files, 1619 lines (Python)
- [**patterns/**](/wiki/utils-patterns/) — 3 files, 1225 lines (Python)
- [**text/**](/wiki/utils-text/) — 4 files, 1348 lines (Python)

| Language | Files |
|---|---|
| Python | 130 |

### Directories

| Directory | Files | Lines |
|---|---|---|
| mlx_memory/ | 6 | 1619 |
| text/ | 4 | 1348 |
| patterns/ | 3 | 1225 |
| coreml/ | 5 | 786 |

### Largest Files

- `async_helpers.py` (2155 lines)
- `cache.py` (1492 lines)
- `deduplication.py` (1226 lines)
- `execution_optimizer.py` (1223 lines)
- `patterns/pattern_matcher.py` (1111 lines)
- `flow_trace.py` (958 lines)
- `mlx_memory/_core.py` (900 lines)
- `hydration_extractor.py` (744 lines)
- `batch_dns.py` (682 lines)
- `mlx_cache.py` (635 lines)

<details><summary><strong>Show 120 more files</strong></summary>

- `filtering.py` (632 lines)
- `intelligent_cache.py` (584 lines)
- `persistent_kv_cache.py` (520 lines)
- `semantic.py` (513 lines)
- `query_expansion.py` (509 lines)
- `text/unicode_analyzer.py` (469 lines)
- `sprint_lifecycle.py` (463 lines)
- `uma_budget.py` (453 lines)
- `msgspec_json.py` (444 lines)
- `rayon_pool.py` (443 lines)
- `text/encoding_detector.py` (443 lines)
- `domain_rate_limiter.py` (412 lines)
- `performance_monitor.py` (384 lines)
- `pivot_seed_extractor.py` (382 lines)
- `concurrency.py` (380 lines)
- `__init__.py` (352 lines)
- `coreml/service.py` (349 lines)
- `validation.py` (345 lines)
- `bloom_filter.py` (341 lines)
- `py314_executors.py` (339 lines)
- `sketches.py` (329 lines)
- `text/hash_identifier.py` (325 lines)
- `ane_pipelines.py` (318 lines)
- `mps_graph.py` (318 lines)
- `logging_config.py` (317 lines)
- `streaming_json.py` (310 lines)
- `async_utils.py` (306 lines)
- `robots_parser.py` (301 lines)
- `workflow_engine.py` (299 lines)
- `async_generators.py` (295 lines)
- `capability_prober.py` (287 lines)
- `exception_policy.py` (283 lines)
- `ioc_batch.py` (278 lines)
- `platform_info.py` (277 lines)
- `domain_executors.py` (273 lines)
- `ranking.py` (268 lines)
- `sync_bridge.py` (268 lines)
- `silent_except_helper.py` (267 lines)
- `flag_registry.py` (265 lines)
- `jsonl_lz4_writer.py` (261 lines)
- `shadow_dtos.py` (261 lines)
- `aho_extractor.py` (252 lines)
- `predictive_planner.py` (250 lines)
- `sqlite_vec_helpers.py` (242 lines)
- `thread_pools.py` (242 lines)
- `html_text_fast.py` (237 lines)
- `language.py` (235 lines)
- `lsh_deduplicator.py` (230 lines)
- `rate_limiters.py` (225 lines)
- `optional_imports.py` (225 lines)
- `source_types.py` (224 lines)
- `sys_metrics.py` (222 lines)
- `lazy_imports.py` (216 lines)
- `two_pass_pipeline.py` (215 lines)
- `mlx_memory/__init__.py` (210 lines)
- `flag_presets.py` (210 lines)
- `import_resolver.py` (208 lines)
- `async_task.py` (205 lines)
- `hashing.py` (204 lines)
- `thermal.py` (201 lines)
- `t_string_helpers.py` (199 lines)
- `mlx_memory/_slab.py` (197 lines)
- `lmdb_bulk.py` (195 lines)
- `optimize_imports.py` (190 lines)
- `memory_dashboard.py` (190 lines)
- `entity_extractor.py` (182 lines)
- `html_parse_pool.py` (182 lines)
- `tech_detection.py` (174 lines)
- `ioc_extract.py` (173 lines)
- `mlx_memory/_embedder.py` (166 lines)
- `encoding.py` (161 lines)
- `async_fs_helpers.py` (160 lines)
- `sprint_context.py` (152 lines)
- `serialization.py` (150 lines)
- `coreml/manager.py` (149 lines)
- `find_files.py` (147 lines)
- `tracked_task.py` (144 lines)
- `coreml/client.py` (126 lines)
- `coreml/models.py` (124 lines)
- `feature_flags.py` (122 lines)
- `safe_render.py` (118 lines)
- `tstring.py` (118 lines)
- `encryption.py` (113 lines)
- `text/__init__.py` (111 lines)
- `patterns/feed_pipeline_wrapper.py` (110 lines)
- `json_codec.py` (103 lines)
- `lock_helpers.py` (99 lines)
- `lazy_singleton.py` (96 lines)
- `confidence.py` (95 lines)
- `mlx_memory.py` (87 lines)
- `mlx_memory/_prompt.py` (83 lines)
- `privacy_utils.py` (83 lines)
- `eig.py` (81 lines)
- `_deprecated.py` (80 lines)
- `exceptions.py` (79 lines)
- `signpost_profiler.py` (78 lines)
- `grounding_validator.py` (75 lines)
- `config_introspection.py` (65 lines)
- `mlx_memory/_tensor.py` (63 lines)
- `_warnings.py` (59 lines)
- `executors.py` (52 lines)
- `f11_budget.py` (49 lines)
- `queue_policy.py` (46 lines)
- `graph_utils.py` (46 lines)
- `rate_limiter.py` (40 lines)
- `_time.py` (40 lines)
- `action_result.py` (39 lines)
- `coreml/__init__.py` (38 lines)
- `config.py` (37 lines)
- `uuid7.py` (35 lines)
- `mlx_utils.py` (27 lines)
- `metal_embedder_buffers.py` (27 lines)
- `mem_stats.py` (24 lines)
- `metal_slab_pool.py` (23 lines)
- `mlx_lazy.py` (19 lines)
- `mlx_prompt_cache.py` (17 lines)
- `shared_tensor.py` (17 lines)
- `content_expander.py` (10 lines)
- `dataclass_transform.py` (5 lines)
- `patterns/__init__.py` (4 lines)

</details>


## Dependencies

No outgoing dependencies detected.

## Dependents

Used by **12 files** across **9 modules**.

**[export/](@/wiki/export.md)** (3 files):
- `export_manager.py`
- `markdown_reporter.py`
- `sprint_markdown_reporter.py`

**[brain/](@/wiki/brain.md)** (2 files):
- `deephermes3_engine.py`
- `model_lifecycle.py`

**[coordinators/](@/wiki/coordinators.md)** (1 files):
- `fetch_coordinator.py`

**[enhanced_research.py/](@/wiki/enhanced_research.py.md)** (1 files):
- `enhanced_research.py`

**[core/](@/wiki/core.md)** (1 files):
- `resource_governor.py`

**[recon/](@/wiki/recon.md)** (1 files):
- `social_identity_miner.py`

**[cache/](@/wiki/cache.md)** (1 files):
- `budget_manager.py`

**[layers/](@/wiki/layers.md)** (1 files):
- `layer_manager.py`

**[runtime/](@/wiki/runtime.md)** (1 files):
- `sprint_scheduler_v1_archived.py`



## Circular Dependencies

**6 circular dependencies** involving this module:

1. __init__.py
2. __init__.py
3. __init__.py
4. __init__.py
5. __init__.py
6. __init__.py


## Key Symbols

<p><strong>Key definitions:</strong></p>
<ul>
<li>
<p><code>SprintLifecycleManager</code> (Class) in sprint_lifecycle.py — referenced in 19 files</p>
<details><summary>Manages sprint lifecycle state machine with fail-open design.</summary>
<div class="doc-comment">
<p>Manages sprint lifecycle state machine with fail-open design.</p>
<p></p>
<p>State transitions:</p>
<p>BOOT → WARMUP → ACTIVE → WINDUP → EXPORT → TEARDOWN</p>
<p></p>
<p>The manager:</p>
<p>- Tracks sprint start time and duration</p>
<p>- Fires wind-down hook T-3min before sprint end</p>
<p>- Provides remaining_time read-only signal</p>
<p>- Registers SIGINT/SIGTERM handlers pointing to unified shutdown</p>
<p>- All methods are async-safe and fail-open</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __main__.py, _lazy_imports.py, composition_root.py, conftest.py, htn_planner.py +12 more</li></ul>
</li>
<li>
<p><code>PyCacheDict</code> (Class) in cache.py — referenced in 12 files</p>
<details><summary>Bounded OrderedDict cache with per-entry TTL.</summary>
<div class="doc-comment">
<p>Bounded OrderedDict cache with per-entry TTL.</p>
<p></p>
<p>Eviction: O(1) LRU via move_to_end() + popitem(last=False).</p>
<p></p>
<p>Invariants:</p>
<p>- maxsize enforced on write: oldest evicted when full</p>
<p>- ttl enforced on read: expired entries return None (lazy purge)</p>
<p>- thread-safe: threading.Lock protects _data</p>
<p>- fail-safe: any error returns None / False, never raises</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: _lazy.py, circuit_breaker.py, confidence.py, deephermes3_engine.py, identity_stitching.py +6 more</li></ul>
</li>
<li>
<p><code>ParallelExecutionOptimizer</code> (Class) in execution_optimizer.py — referenced in 5 files</p>
<details><summary>Advanced parallel execution optimization system</summary></details>
<ul><li class="ref-list">Referenced by: __init__.py, bench_py314_jit.py, execution_coordinator.py</li></ul>
</li>
<li>
<p><code>SemanticDeduplicator</code> (Class) in deduplication.py — referenced in 5 files</p>
<details><summary>Semantic deduplication using vector embeddings.</summary></details>
<ul><li class="ref-list">Referenced by: __init__.py, dedup_determinism_benchmark.py, embedding_cache.py, semantic_deduplicator.py</li></ul>
</li>
<li>
<p><code>IntelligentCache</code> (Class) in intelligent_cache.py — referenced in 3 files</p>
<details><summary>ML-enhanced intelligent cache with ARC eviction.</summary>
<div class="doc-comment">
<p>ML-enhanced intelligent cache with ARC eviction.</p>
<p></p>
<p>Features:</p>
<p>- ARC (Adaptive Replacement Cache) for O(1) eviction</p>
<p>- Automatic memory management for M1 8GB</p>
<p>- Async operations for non-blocking access</p>
<p>- Optional persistence to disk</p>
<p>- sys.getsizeof for size estimation</p>
<p></p>
<p>Example:</p>
<p>cache = IntelligentCache(CacheConfig(max_size_bytes=50*1024*1024))</p>
<p>await cache.initialize()</p>
<p></p>
<p>await cache.set("key", value, ttl=300)</p>
<p>result = await cache.get("key")</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py, layer_manager.py</li></ul>
</li>
</ul>

<details><summary><strong>Function</strong> (736)</summary>
<ul>
<li><code>extract_static_hydration</code> (hydration_extractor.py)</li>
<li><code>resolve_many</code> (batch_dns.py)</li>
<li><code>parallel</code> (async_helpers.py)</li>
<li><code>match_text</code> (pattern_matcher.py)</li>
<li><code>chunked_taskgroup</code> (async_helpers.py)</li>
<li><code>_ensure_metal_memory_limits</code> (mlx_cache.py)
<details><summary>Ensure Metal memory limits are set exactly once per process (thread-safe).</summary>
<div class="doc-comment">
<p>Ensure Metal memory limits are set exactly once per process (thread-safe).</p>
<p></p>
<p>Uses double-checked locking:</p>
<p>1. Fast path: check _MLX_METAL_LIMITS_CONFIGURED without lock</p>
<p>2. Slow path: acquire lock, re-check, then call set_cache_limit + set_wired_limit</p>
<p></p>
<p>Cache limit is DYNAMIC (MEM-2): min(max(available*0.2, 512MiB), 1.5GiB).</p>
<p>Wired limit stays fixed at 768 MiB (pinned Metal memory, non-swappable).</p>
<p></p>
<p>Returns:</p>
<p>True if limits are now configured (or were already configured), False on failure.</p>
</div>
</details>
</li>
<li><code>race_first_success</code> (async_helpers.py)</li>
<li><code>_parallel_taskgroup</code> (async_helpers.py)</li>
<li><code>safe_gather_shielded</code> (async_helpers.py)</li>
<li><code>_check_gathered</code> (async_helpers.py)</li>
<li><code>bounded_parallel_map</code> (async_helpers.py)</li>
<li><code>save</code> (persistent_kv_cache.py)</li>
<li><code>safe_gather_strict</code> (async_helpers.py)</li>
<li><code>prewarm</code> (batch_dns.py)</li>
<li><code>safe_gather_fire_and_forget</code> (async_helpers.py)</li>
<li><code>_classify_gathered</code> (async_helpers.py)</li>
<li><code>safe_gather_return_exceptions</code> (async_helpers.py)</li>
<li><code>match_text_batch</code> (pattern_matcher.py)</li>
<li><code>load</code> (persistent_kv_cache.py)
<details><summary>Load KV cache from persistent storage.</summary>
<div class="doc-comment">
<p>Load KV cache from persistent storage.</p>
<p></p>
<p>Args:</p>
<p>prompt: The prompt to look up</p>
<p></p>
<p>Returns:</p>
<p>(kv_cache, token_count) if found, (None, None) if not found or error</p>
</div>
</details>
</li>
<li><code>safe_gather_ok</code> (async_helpers.py)</li>
<li><code>_compute_hydration_score</code> (hydration_extractor.py)
<details><summary>Compute conservative hydration quality score (0.0–1.0).</summary>
<div class="doc-comment">
<p>Compute conservative hydration quality score (0.0–1.0).</p>
<p></p>
<p>Scoring rules (conservative):</p>
<p>- title/headline found: +0.2</p>
<p>- meaningful description/body: +0.3</p>
<p>- JSON-LD Article/NewsArticle/BlogPosting: +0.3</p>
<p>- canonical URL: +0.1</p>
<p>- feed/alternate RSS/Atom: +0.1</p>
<p>- Next/Nuxt/generic hydration payload with content-like fields: +0.4</p>
<p>- truncated input: penalize</p>
<p>- very short extracted text: penalize</p>
<p></p>
<p>Returns (score, quality_signals).</p>
</div>
</details>
</li>
<li><code>parallel_close_async</code> (async_helpers.py)</li>
<li><code>run_in_mixed_pool</code> (rayon_pool.py)
<details><summary>Run mixed workload on rayon mixed_pool (1-2 threads, adaptive).</summary>
<div class="doc-comment">
<p>Run mixed workload on rayon mixed_pool (1-2 threads, adaptive).</p>
<p></p>
<p>Thread count is MLX Metal-aware via mx.metal.get_active_memory():</p>
<p>- Metal &lt; 2 GiB active  → threshold 16  (eager parallelism)</p>
<p>- Metal 2–4 GiB active   → threshold 32  (normal, F270 calibration)</p>
<p>- Metal &gt; 4 GiB active  → threshold 64  (conservative, sequential)</p>
<p>Eliminates pool spawn overhead (~0.5ms) for small batches.</p>
<p></p>
<p>Use for: IOC extract, url_ops, simhash, html_parse workloads.</p>
<p></p>
<p>Args:</p>
<p>n_items: Number of items in batch (determines thread count)</p>
<p>fn: Synchronous callable to run on the rayon pool</p>
<p>*args: Positional arguments passed to fn</p>
<p>**kwargs: Keyword arguments passed to fn</p>
<p></p>
<p>Returns:</p>
<p>Result of fn(*args, **kwargs), or None if pool unavailable</p>
<p></p>
<p>Fail-safe:</p>
<p>- If rayon unavailable: logs warning, returns None</p>
<p>- If fn raises: exception propagates (caller should handle)</p>
<p></p>
<p>Example:</p>
<p># Batch size adaptive:</p>
<p>result = await asyncio.to_thread(</p>
<p>run_in_mixed_pool, len(items), ioc_extract, text</p>
<p>)</p>
</div>
</details>
</li>
<li><code>trace_event</code> (flow_trace.py)</li>
<li><code>analyze_file</code> (unicode_analyzer.py)
<details><summary>Stream-analyze a file for Unicode attacks.</summary>
<div class="doc-comment">
<p>Stream-analyze a file for Unicode attacks.</p>
<p></p>
<p>Args:</p>
<p>file_path: Path to the file to analyze</p>
<p></p>
<p>Returns:</p>
<p>UnicodeAnalysisResult with all findings</p>
</div>
</details>
</li>
<li><code>run_in_cpu_pool</code> (rayon_pool.py)
<details><summary>Run CPU-bound function on rayon cpu_pool (4 P-cores).</summary>
<div class="doc-comment">
<p>Run CPU-bound function on rayon cpu_pool (4 P-cores).</p>
<p></p>
<p>Use for: SIMD operations, xxhash parallel, quality_gate, pattern matching.</p>
<p></p>
<p>Args:</p>
<p>fn: Synchronous callable to run on the rayon pool</p>
<p>*args: Positional arguments passed to fn</p>
<p>**kwargs: Keyword arguments passed to fn</p>
<p></p>
<p>Returns:</p>
<p>Result of fn(*args, **kwargs), or None if pool unavailable</p>
<p></p>
<p>Fail-safe:</p>
<p>- If rayon unavailable: logs warning, returns None</p>
<p>- If fn raises: exception propagates (caller should handle)</p>
<p></p>
<p>Example:</p>
<p># From async context:</p>
<p>result = await asyncio.to_thread(run_in_cpu_pool, hash_func, data)</p>
<p></p>
<p># From sync context:</p>
<p>result = run_in_cpu_pool(some_cpu_bound_func, arg1, arg2)</p>
</div>
</details>
</li>
<li><code>safe_gather</code> (async_helpers.py)</li>
<li><code>run_in_io_pool</code> (rayon_pool.py)
<details><summary>Run I/O-bound function on rayon io_pool (2 threads).</summary>
<div class="doc-comment">
<p>Run I/O-bound function on rayon io_pool (2 threads).</p>
<p></p>
<p>Use for: DuckDB queries, graph_traverse, compress operations.</p>
<p></p>
<p>Args:</p>
<p>fn: Synchronous callable to run on the rayon pool</p>
<p>*args: Positional arguments passed to fn</p>
<p>**kwargs: Keyword arguments passed to fn</p>
<p></p>
<p>Returns:</p>
<p>Result of fn(*args, **kwargs), or None if pool unavailable</p>
<p></p>
<p>Fail-safe:</p>
<p>- If rayon unavailable: logs warning, returns None</p>
<p>- If fn raises: exception propagates (caller should handle)</p>
<p></p>
<p>Example:</p>
<p># From async context:</p>
<p>result = await asyncio.to_thread(run_in_io_pool, duckdb_query, sql)</p>
<p></p>
<p># From sync context:</p>
<p>result = run_in_io_pool(read_duckdb, query)</p>
</div>
</details>
</li>
<li><code>parallel_close</code> (async_helpers.py)</li>
<li><code>reconfigure_metal_cache_limit</code> (mlx_cache.py)
<details><summary>F265H: Runtime reconfigure of Metal cache limit — called on UMA state transitions.</summary>
<div class="doc-comment">
<p>F265H: Runtime reconfigure of Metal cache limit — called on UMA state transitions.</p>
<p></p>
<p>This function re-applies the dynamic cache limit formula with the current</p>
<p>UMA state, allowing the cache to shrink at EMERGENCY (256 MiB floor) and</p>
<p>restore to normal floors when pressure subsides.</p>
<p></p>
<p>Called by the EMERGENCY/CRITICAL callbacks in __main__.py to dynamically</p>
<p>adjust the Metal cache ceiling based on memory pressure.</p>
<p></p>
<p>Args:</p>
<p>uma_state: Current UMA state string ("ok"|"soft_warn"|"warn"|"critical"|"emergency").</p>
<p>When None, uses normal 512 MiB floor.</p>
<p></p>
<p>Returns:</p>
<p>True if reconfiguration succeeded, False otherwise.</p>
</div>
</details>
</li>
<li><code>safe_create_task</code> (async_helpers.py)</li>
<li><code>safe_wait_for</code> (async_helpers.py)</li>
<li><code>_flatten_text</code> (hydration_extractor.py)
<details><summary>Recursively extract text from a parsed JSON object.</summary>
<div class="doc-comment">
<p>Recursively extract text from a parsed JSON object.</p>
<p>Handles cycles, depth limit, and size cap.</p>
</div>
</details>
</li>
<li><code>extract_high_precision_entities</code> (pattern_matcher.py)
<details><summary>Extract high-precision structured entities via regex.</summary>
<div class="doc-comment">
<p>Extract high-precision structured entities via regex.</p>
<p></p>
<p>Covers: CVE, GHSA, onion v3, SHA256, MD5, SHA1, ETH.</p>
<p>Returns ExtractedEntity list sorted by start offset.</p>
</div>
</details>
</li>
<li><code>cancel_scope_drain</code> (async_helpers.py)</li>
<li><code>get_mlx_model</code> (mlx_cache.py)
<details><summary>Get MLX model and tokenizer from cache or load from disk.</summary>
<div class="doc-comment">
<p>Get MLX model and tokenizer from cache or load from disk.</p>
<p></p>
<p>Uses LRU eviction when cache exceeds max 2 models.</p>
<p></p>
<p>Args:</p>
<p>model_name: The model identifier (e.g., 'mlx-community/mamba2-370m-4bit')</p>
<p></p>
<p>Returns:</p>
<p>Tuple of (model, tokenizer) or (None, None) on failure</p>
</div>
</details>
</li>
<li><code>_execute_interpreter_pool</code> (execution_optimizer.py)
<details><summary>Execute pure-Python CPU-bound batch via InterpreterPoolExecutor (P2-1).</summary>
<div class="doc-comment">
<p>Execute pure-Python CPU-bound batch via InterpreterPoolExecutor (P2-1).</p>
<p></p>
<p>Uses Python 3.14 subinterpreters for true parallelism without GIL contention.</p>
<p>Each subinterpreter has its own GIL → unlike ThreadPool, no GIL serialization.</p>
<p>M1 8GB: ~1-2MB overhead per subinterpreter, max_workers capped at 2.</p>
<p></p>
<p>Falls back to ThreadPoolExecutor if InterpreterPool unavailable.</p>
<p></p>
<p>NOTE: This method expects tasks to be (data, func) tuples where func is a</p>
<p>module-level callable that can be pickled for subinterpreter dispatch.</p>
<p>Use execute_batch_interpreter() for the canonical batch(data, func) API.</p>
<p></p>
<p>Args:</p>
<p>tasks: List of (data, func) tuples from caller.</p>
<p>max_workers: Max subinterpreters. Capped at 2 for M1 8GB safety.</p>
<p></p>
<p>Returns:</p>
<p>Flattened results from all subinterpreter workers.</p>
</div>
</details>
</li>
<li><code>trace_source_accepted</code> (flow_trace.py)</li>
<li><code>_cluster_by_simhash</code> (deduplication.py)
<details><summary>Group items into LSH buckets using SimHash.</summary>
<div class="doc-comment">
<p>Group items into LSH buckets using SimHash.</p>
<p></p>
<p>Uses rust.lsh.LSHIndex.batch_query() when available (&lt;50ms for 10k sigs).</p>
<p>Falls back to pure Python OrderedDict bucketing.</p>
</div>
</details>
</li>
<li><code>_execute_with_resource_constraints</code> (execution_optimizer.py) — <span class="doc-comment-inline">Execute tasks with resource constraints</span></li>
<li><code>bounded_gather</code> (async_helpers.py)</li>
<li><code>trace_span_end</code> (flow_trace.py)</li>
<li><code>mlx_cleanup_aggressive</code> (_core.py)
<details><summary>Aggressive cleanup — sets cache to 64MB floor then restores limits.</summary>
<div class="doc-comment">
<p>Aggressive cleanup — sets cache to 64MB floor then restores limits.</p>
<p>Use during EMERGENCY memory pressure.</p>
</div>
</details>
</li>
<li><code>allocate_task</code> (execution_optimizer.py)
<details><summary>Allocate a task to appropriate core type</summary>
<div class="doc-comment">
<p>Allocate a task to appropriate core type</p>
<p></p>
<p>Args:</p>
<p>task_priority: "low", "normal", "high", "critical"</p>
<p>cpu_intensity: 0.0-1.0 scale of CPU intensity</p>
<p></p>
<p>Returns:</p>
<p>Allocation configuration with CPU affinity</p>
</div>
</details>
</li>
<li><code>_apply_metal_limits_impl</code> (_core.py) — <span class="doc-comment-inline">Apply Metal limits. Called only from init_mlx_buffers under lock.</span></li>
<li><code>mlx_cleanup_aggressive</code> (mlx_cache.py) — <span class="doc-comment-inline">Agresivní cleanup – dočasně sníží cache limit pro uvolnění fragmentace.</span></li>
<li><code>deduplicate</code> (deduplication.py) — <span class="doc-comment-inline">Deduplicate list of query items.</span></li>
<li><code>execute_batch_interpreter</code> (execution_optimizer.py)
<details><summary>Synchronous batch executor — call from async context via asyncio.to_thread().</summary>
<div class="doc-comment">
<p>Synchronous batch executor — call from async context via asyncio.to_thread().</p>
<p></p>
<p>P2-1: Canonical API for InterpreterPoolExecutor batch execution.</p>
<p>Chunks data and distributes to subinterpreter workers for true parallelism.</p>
<p></p>
<p>Args:</p>
<p>data: Input data (list of items to process).</p>
<p>func: Pure-Python function (list -&gt; list). Must be module-level</p>
<p>and pickle-able for subinterpreter dispatch.</p>
<p>max_workers: Subinterpreters count. Default 2 (M1 8GB safe).</p>
<p></p>
<p>Returns:</p>
<p>Flattened results from all workers.</p>
<p></p>
<p>Example:</p>
<p>results = await asyncio.to_thread(</p>
<p>optimizer.execute_batch_interpreter,</p>
<p>items,</p>
<p>normalize_text,</p>
<p>)</p>
</div>
</details>
</li>
<li><code>extract_structured_entities</code> (pattern_matcher.py)
<details><summary>Extract IOCs and return as structured list of dicts for GraphManager.</summary>
<div class="doc-comment">
<p>Extract IOCs and return as structured list of dicts for GraphManager.</p>
<p></p>
<p>FÁZE P9: Pipeline consumable format — list[dict] with entity_type + value.</p>
<p>Combines both AC automaton hits and regex post-pass results.</p>
<p>Memory-bounded: max 1000 entries per call (M1 8GB safe).</p>
<p></p>
<p>Returns:</p>
<p>List of {"entity_type": str, "value": str, "label": str} dicts.</p>
<p>Deduplicated by (entity_type, value) pair.</p>
</div>
</details>
</li>
<li><code>get_system_memory_mb</code> (uma_budget.py)
<details><summary>Get system memory info.</summary>
<div class="doc-comment">
<p>Get system memory info.</p>
<p></p>
<p>Returns:</p>
<p>(total_mb, used_mb, available_mb)</p>
<p>Returns (0, 0, 0) on failure.</p>
<p></p>
<p>Issue #38 SSOT: Delegates to core.memory (Rust SSOT surface).</p>
<p>Falls back to cached psutil reader for compatibility.</p>
</div>
</details>
</li>
<li><code>_evict_from_gen</code> (cache.py)</li>
<li><code>get_dynamic_metal_cache_limit</code> (mlx_cache.py)
<details><summary>Compute Metal cache limit dynamically based on available system memory.</summary>
<div class="doc-comment">
<p>Compute Metal cache limit dynamically based on available system memory.</p>
<p></p>
<p>Formula (normal): min(max(available * 0.2, 512 MiB), 1.5 GiB)</p>
<p>Formula (EMERGENCY): min(max(available * 0.2, 256 MiB), 1.5 GiB)</p>
<p>- 20% of available memory (adaptive to workload)</p>
<p>- Floor: 256 MiB EMERGENCY / 512 MiB normal (ensures minimum caching)</p>
<p>- Ceiling: 1.5 GiB (M1 8GB safe upper bound, raised from 1 GiB in F267)</p>
<p></p>
<p>F265H: EMERGENCY floor is 256 MiB — half of normal floor. This gives</p>
<p>the draft model more Metal memory headroom during EMERGENCY state, trading</p>
<p>cache for model workspace.</p>
<p></p>
<p>Args:</p>
<p>uma_state: Optional UMA state string ("ok"|"soft_warn"|"warn"|"critical"|"emergency").</p>
<p>When "emergency", uses 256 MiB floor instead of 512 MiB.</p>
<p></p>
<p>Called inside _ensure_metal_memory_limits() so it reflects memory state</p>
<p>at init time (~5.5 GiB available on 8GB M1 at boot), not at module import.</p>
<p>Also called by reconfigure_metal_cache_limit() for runtime re-adjustment.</p>
<p>At 5.5 GiB available: cache_limit = min(1.1, 1.5) = 1.1 GiB</p>
<p>→ model(2GB) + cache(1.1GB) + KV(0.75GB) = ~3.85GB total MLX footprint,</p>
<p>leaving ~4.15GB for macOS → stays in warn zone, not critical.</p>
</div>
</details>
</li>
<li><code>mlx_cleanup_sync</code> (mlx_cache.py)
<details><summary>Sync cleanup – vždy v thread executoru.</summary>
<div class="doc-comment">
<p>Sync cleanup – vždy v thread executoru.</p>
<p></p>
<p>F183C: Canonical cleanup order (srovnáno s model_manager + model_lifecycle):</p>
<p>1. gc.collect() — uvolní Python refs na MLX objekty PRVNÍ</p>
<p>2. mx.eval([])  — barrier: vyprázdní GPU queue PŘED clear_cache</p>
<p>3. clear_cache() — uvolní Metal cache</p>
<p></p>
<p>Dřívější pořadí (clear_cache → gc.collect) bylo špatně: Python objekty držely</p>
<p>MLX tensory ještě při clear_cache, což mohlo na M1 8GB způsobit brief over-budget.</p>
</div>
</details>
</li>
<li><code>generate_complex_queries</code> (query_expansion.py)
<details><summary>Generate complex dorking queries for a topic.</summary>
<div class="doc-comment">
<p>Generate complex dorking queries for a topic.</p>
<p></p>
<p>Args:</p>
<p>topic: Search topic or domain</p>
<p>query_type: Type of queries ('academic', 'technical', 'financial',</p>
<p>'government', 'security', 'hidden')</p>
<p>include_variations: Whether to include filetype variations</p>
<p></p>
<p>Returns:</p>
<p>List of dorking queries</p>
</div>
</details>
</li>
<li><code>_start_uma_watchdog</code> (sprint_lifecycle.py)
<details><summary>Start UmaWatchdog when entering ACTIVE state.</summary>
<div class="doc-comment">
<p>Start UmaWatchdog when entering ACTIVE state.</p>
<p>Fails silently if no event loop or watchdog import fails.</p>
<p>Watchdog is tracked via track_task() for lifecycle management.</p>
</div>
</details>
</li>
<li><code>get_mlx_memory_mb</code> (uma_budget.py)
<details><summary>Get MLX memory usage.</summary>
<div class="doc-comment">
<p>Get MLX memory usage.</p>
<p></p>
<p>Returns:</p>
<p>(active_mb, peak_mb, cache_mb)</p>
<p>Returns (0, 0, 0) if MLX unavailable.</p>
<p></p>
<p>Issue #38 SSOT: Delegates to core.memory (Rust MLX probe).</p>
<p>Falls back to direct mlx.core inspection for peak/cache unavailable in Rust.</p>
</div>
</details>
</li>
<li><code>get_uma_pressure_level</code> (uma_budget.py)
<details><summary>Calculate UMA pressure percentage and level.</summary>
<div class="doc-comment">
<p>Calculate UMA pressure percentage and level.</p>
<p></p>
<p>Returns:</p>
<p>(usage_pct: int, level: str)</p>
<p>level: "normal" / "warn" / "critical" / "emergency"</p>
<p></p>
<p>Uses dynamically detected _UMA_TOTAL_MB as denominator.</p>
<p>Swap signal: adaptive thresholds based on total swap size.</p>
<p>Fails open to (0, "normal") if measurement unavailable.</p>
</div>
</details>
</li>
<li><code>_evict_if_needed</code> (intelligent_cache.py) — <span class="doc-comment-inline">KVP-based eviction: O(1) scoring of top-10 ARC candidates only.</span></li>
<li><code>auto_optimize</code> (execution_optimizer.py)
<details><summary>Decorator for automatic function optimization.</summary>
<div class="doc-comment">
<p>Decorator for automatic function optimization.</p>
<p></p>
<p>Args:</p>
<p>cache_results: Whether to cache function results</p>
<p>max_workers: Max parallel workers (None = auto)</p>
<p>memory_limit_mb: Memory limit for execution</p>
</div>
</details>
</li>
<li><code>_is_sufficient</code> (hydration_extractor.py)
<details><summary>Conservative sufficiency check.</summary>
<div class="doc-comment">
<p>Conservative sufficiency check.</p>
<p>Returns (sufficient, reason_str).</p>
<p></p>
<p>F265C: body-content depth check — pages with only metadata (title + canonical/feed)</p>
<p>but no actual body content elements in HTML are NOT sufficient. They need JS</p>
<p>rendering to extract real article content. Pass raw html for the depth check.</p>
</div>
</details>
</li>
<li><code>expand</code> (query_expansion.py)
<details><summary>Generate query variations.</summary>
<div class="doc-comment">
<p>Generate query variations.</p>
<p></p>
<p>Args:</p>
<p>query: Original search query</p>
<p></p>
<p>Returns:</p>
<p>List of query variations</p>
</div>
</details>
</li>
<li><code>_detect_bidi_attacks</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Detect bidirectional text attacks in text - optimized version.</span></li>
<li><code>compute_skeleton_hash</code> (unicode_analyzer.py)
<details><summary>Compute UTS #39 skeleton hash for confusables detection.</summary>
<div class="doc-comment">
<p>Compute UTS #39 skeleton hash for confusables detection.</p>
<p></p>
<p>Applies:</p>
<p>- NFD normalization</p>
<p>- Basic confusable mapping (using loaded mappings if available)</p>
<p>- Re-NFD normalization</p>
<p>- Returns sha256(skeleton)[:16]</p>
<p></p>
<p>This is used for:</p>
<p>- Spoof network clustering (same skeleton = possible confusables)</p>
<p>- Internal signal only (skeleton text is NOT stored)</p>
<p></p>
<p>Args:</p>
<p>text: Input text (typically hostname or URL segment)</p>
<p></p>
<p>Returns:</p>
<p>16-char hex digest of skeleton hash</p>
</div>
</details>
</li>
<li><code>evict_orphaned</code> (cache.py)
<details><summary>Evict entries with refcount ≤ baseline (orphaned).</summary>
<div class="doc-comment">
<p>Evict entries with refcount ≤ baseline (orphaned).</p>
<p></p>
<p>Call this during memory pressure events to reclaim abandoned sessions.</p>
<p></p>
<p>Args:</p>
<p>max_evict: Maximum entries to evict in this call.</p>
<p></p>
<p>Returns:</p>
<p>Number of entries evicted.</p>
</div>
</details>
</li>
<li><code>execute_parallel</code> (execution_optimizer.py) — <span class="doc-comment-inline">Execute tasks in parallel with optimal strategy</span></li>
<li><code>get_mlx_model</code> (_core.py)
<details><summary>Get MLX model and tokenizer from cache or load from disk.</summary>
<div class="doc-comment">
<p>Get MLX model and tokenizer from cache or load from disk.</p>
<p>LRU eviction when cache exceeds max 2 models.</p>
</div>
</details>
</li>
<li><code>mlx_managed</code> (_core.py)
<details><summary>Decorator: auto mx.eval([]) + clear_cache() after MLX operation.</summary>
<div class="doc-comment">
<p>Decorator: auto mx.eval([]) + clear_cache() after MLX operation.</p>
<p></p>
<p>Sync function → _maybe_eval_sync() + _clear_metal_cache_sync()</p>
<p>Async function → await _maybe_eval_async() + await _clear_metal_cache_async()</p>
</div>
</details>
</li>
<li><code>encode</code> (semantic.py)
<details><summary>Encode text to embedding vector.</summary>
<div class="doc-comment">
<p>Encode text to embedding vector.</p>
<p></p>
<p>Args:</p>
<p>text: Text to encode</p>
<p></p>
<p>Returns:</p>
<p>Embedding vector</p>
</div>
</details>
</li>
<li><code>async_getaddrinfo</code> (async_helpers.py)</li>
<li><code>__init__</code> (cache.py)</li>
<li><code>_compute_minhash</code> (deduplication.py)
<details><summary>Compute MinHash signature for content similarity.</summary>
<div class="doc-comment">
<p>Compute MinHash signature for content similarity.</p>
<p></p>
<p>F214OPT-J: Note on mmh3 seed optimization — mmh3.hash does accept a seed</p>
<p>argument (mmh3.hash(key, seed=N, signed=False)). However, using</p>
<p>mmh3.hash(ngram, seed=i) instead of f"{ngram}_{i}" would change the</p>
<p>computed hash values, which would invalidate existing stored MinHash</p>
<p>signatures. To preserve exact signature compatibility, the current</p>
<p>f-string approach is retained. The allocation overhead is bounded by</p>
<p>HLEDAC_DEDUP_MAX_NGRAMS (default 50000).</p>
</div>
</details>
</li>
<li><code>_bounded_resolve</code> (batch_dns.py)</li>
<li><code>_run</code> (uma_budget.py) — <span class="doc-comment-inline">Main polling loop — runs until cancelled.</span></li>
<li><code>get_mlx_memory_stats</code> (_core.py) — <span class="doc-comment-inline">Získat aktuální MLX memory statistiky.</span></li>
<li><code>init_mlx_buffers</code> (mlx_cache.py)
<details><summary>Initialize MLX buffer limits for M1 8GB.</summary>
<div class="doc-comment">
<p>Initialize MLX buffer limits for M1 8GB.</p>
<p></p>
<p>Sprint 8T: Delegates to _ensure_metal_memory_limits() which sets:</p>
<p>- cache_limit: dynamic (20% of available, ceiling 1.5 GiB)</p>
<p>- wired_limit: fixed 768 MiB (pinned Metal memory)</p>
<p>Uses thread-safe double-checked locking. Must be called before MLX</p>
<p>inference to ensure proper memory budget.</p>
<p></p>
<p>Returns:</p>
<p>True if initialization successful, False otherwise.</p>
<p>Returns False (no crash) when MLX is unavailable.</p>
</div>
</details>
</li>
<li><code>mlx_cleanup_decorator</code> (mlx_cache.py) — <span class="doc-comment-inline">Dekorátor pro async i sync funkce – přidá cleanup po dokončení.</span></li>
<li><code>set</code> (intelligent_cache.py)
<details><summary>Set value in cache.</summary>
<div class="doc-comment">
<p>Set value in cache.</p>
<p></p>
<p>Args:</p>
<p>key: Cache key</p>
<p>value: Value to cache</p>
<p>ttl: Time-to-live in seconds (uses default if None)</p>
<p>size_bytes: Size hint for value (auto-calculated if None)</p>
<p></p>
<p>Returns:</p>
<p>True if successfully cached</p>
</div>
</details>
</li>
<li><code>register_signal_handlers</code> (sprint_lifecycle.py)
<details><summary>Register SIGINT/SIGTERM handlers that call shutdown_coro.</summary>
<div class="doc-comment">
<p>Register SIGINT/SIGTERM handlers that call shutdown_coro.</p>
<p>Must be called from the main thread / before asyncio loop is created.</p>
<p>Idempotent.</p>
<p></p>
<p>Args:</p>
<p>shutdown_coro: async callable that initiates graceful shutdown</p>
<p>(e.g., orchestrator.shutdown_all)</p>
</div>
</details>
</li>
<li><code>decode</code> (msgspec_json.py)
<details><summary>Fast decode JSON bytes/str/memoryview/bytearray → Python object.</summary>
<div class="doc-comment">
<p>Fast decode JSON bytes/str/memoryview/bytearray → Python object.</p>
<p></p>
<p>Uses per-thread pool of ``msgspec.json.Decoder``. Falls back to</p>
<p>``orjson``/``json`` on errors.</p>
<p></p>
<p>Args:</p>
<p>data: JSON payload (bytes, str, memoryview, bytearray).</p>
<p></p>
<p>Returns:</p>
<p>Decoded Python object (dict, list, etc.).</p>
</div>
</details>
</li>
<li><code>evict_low_refcount</code> (cache.py)
<details><summary>Force-evict entries with refcount ≤ baseline across all generations.</summary>
<div class="doc-comment">
<p>Force-evict entries with refcount ≤ baseline across all generations.</p>
<p></p>
<p>Use during memory pressure events to aggressively reclaim orphaned entries.</p>
<p></p>
<p>Args:</p>
<p>max_evict: Maximum entries to evict in this call.</p>
<p></p>
<p>Returns:</p>
<p>Number of entries evicted.</p>
</div>
</details>
</li>
<li><code>mlx_cleanup_sync</code> (_core.py)
<details><summary>Sync cleanup – always call in thread executor (never asyncio.run).</summary>
<div class="doc-comment">
<p>Sync cleanup – always call in thread executor (never asyncio.run).</p>
<p></p>
<p>F183C canonical cleanup order:</p>
<p>1. gc.collect() — release Python refs to MLX objects FIRST</p>
<p>2. mx.eval([])  — barrier: flush GPU queue BEFORE clear_cache</p>
<p>3. clear_cache() — release Metal cache</p>
<p>4. gc.collect()  — second pass for circular refs created during Metal free</p>
<p>5. slab pool release</p>
</div>
</details>
</li>
<li><code>mlx_cleanup_after</code> (_core.py) — <span class="doc-comment-inline">Decorator: cleanup after function (eval + clear) regardless of outcome.</span></li>
<li><code>__init__</code> (batch_dns.py)</li>
<li><code>check_url</code> (filtering.py)
<details><summary>Check if URL is allowed (not blocked).</summary>
<div class="doc-comment">
<p>Check if URL is allowed (not blocked).</p>
<p></p>
<p>Returns:</p>
<p>True if allowed, False if blocked</p>
</div>
</details>
</li>
<li><code>get</code> (intelligent_cache.py)
<details><summary>Get value from cache.</summary>
<div class="doc-comment">
<p>Get value from cache.</p>
<p></p>
<p>Args:</p>
<p>key: Cache key</p>
<p></p>
<p>Returns:</p>
<p>Cached value or None if not found/expired</p>
</div>
</details>
</li>
<li><code>_init_lmdb</code> (persistent_kv_cache.py) — <span class="doc-comment-inline">Initialize LMDB metadata index.</span></li>
<li><code>gather_taskgroup</code> (async_helpers.py)</li>
<li><code>find_duplicates</code> (deduplication.py) — <span class="doc-comment-inline">Find content-based duplicates using LSH clustering for O(n) performance.</span></li>
<li><code>configure_patterns</code> (pattern_matcher.py)
<details><summary>Update the active pattern registry.</summary>
<div class="doc-comment">
<p>Update the active pattern registry.</p>
<p></p>
<p>Args:</p>
<p>registry: Tuple of (pattern, label) pairs.</p>
<p>Pass _SEED_REGISTRY for test seeding.</p>
<p>Pass () to clear all patterns.</p>
</div>
</details>
</li>
<li><code>get_mlx_memory_module</code> (_core.py)
<details><summary>Lazy accessor for the mlx_memory package.</summary>
<div class="doc-comment">
<p>Lazy accessor for the mlx_memory package.</p>
<p></p>
<p>Avoids import at module load time. Returns the mlx_memory module</p>
<p>or None if unavailable.</p>
<p></p>
<p>Canonical replacement for per-class lazy-import patterns:</p>
<p># BEFORE (duplicated in 3 files):</p>
<p>def _get_mlx_memory(self):</p>
<p>if self._mlx_memory is None:</p>
<p>try:</p>
<p>from hledac.universal.utils import mlx_memory</p>
<p>self._mlx_memory = mlx_memory</p>
<p>except ImportError:</p>
<p>self._mlx_memory = None</p>
<p>return self._mlx_memory</p>
<p></p>
<p># AFTER (centralized):</p>
<p>mlx_mem = get_mlx_memory_module()</p>
</div>
</details>
</li>
<li><code>configure_mlx_limits</code> (_core.py)
<details><summary>Configure MLX cache and memory limits for M1 8GB.</summary>
<div class="doc-comment">
<p>Configure MLX cache and memory limits for M1 8GB.</p>
<p>Returns dict with success status and any errors.</p>
</div>
</details>
</li>
<li><code>compute_similarity</code> (semantic.py)
<details><summary>Compute semantic similarity between two texts.</summary>
<div class="doc-comment">
<p>Compute semantic similarity between two texts.</p>
<p></p>
<p>Args:</p>
<p>text1: First text</p>
<p>text2: Second text</p>
<p></p>
<p>Returns:</p>
<p>Similarity score (0-1)</p>
</div>
</details>
</li>
<li><code>extract_relevant_snippets</code> (semantic.py)
<details><summary>Extract most relevant snippets from content.</summary>
<div class="doc-comment">
<p>Extract most relevant snippets from content.</p>
<p></p>
<p>Args:</p>
<p>content: Content to extract snippets from</p>
<p>query: Query to match against</p>
<p>max_snippets: Maximum number of snippets to return</p>
<p>snippet_length: Maximum length of each snippet</p>
<p></p>
<p>Returns:</p>
<p>List of relevant snippets</p>
</div>
</details>
</li>
<li><code>analyze_text</code> (unicode_analyzer.py)
<details><summary>Analyze text for Unicode attacks.</summary>
<div class="doc-comment">
<p>Analyze text for Unicode attacks.</p>
<p></p>
<p>Args:</p>
<p>text: The text to analyze</p>
<p></p>
<p>Returns:</p>
<p>UnicodeAnalysisResult with all findings</p>
</div>
</details>
</li>
<li><code>maybe_resume</code> (sprint_lifecycle.py)
<details><summary>Return True if an unfinished sprint can be resumed from checkpoint.</summary>
<div class="doc-comment">
<p>Return True if an unfinished sprint can be resumed from checkpoint.</p>
<p></p>
<p>Canonical LMDB keys read:</p>
<p>b"sprint:last_phase"   — phase string</p>
<p>b"sprint:current_id"    — sprint id string</p>
<p></p>
<p>Unfinished means phase exists and is NOT "export" nor "teardown".</p>
<p></p>
<p>Fail-open: any error (MissingError, AttributeError, OSError) → returns False.</p>
<p></p>
<p>Args:</p>
<p>lmdb_env: optional LMDB.Environment instance. If None, returns False.</p>
<p></p>
<p>Returns:</p>
<p>True if sprint is resumable, False otherwise.</p>
</div>
</details>
</li>
<li><code>__init__</code> (cache.py)</li>
<li><code>compute_embedding_batch</code> (deduplication.py)
<details><summary>MLX-accelerated SimHash for embedding matrix (batch, dim).</summary>
<div class="doc-comment">
<p>MLX-accelerated SimHash for embedding matrix (batch, dim).</p>
<p>Lazy import MLX, fallback to numpy.</p>
</div>
</details>
</li>
<li><code>_detect_m1_cores</code> (execution_optimizer.py) — <span class="doc-comment-inline">Detect M1 P/E core topology using sysctl</span></li>
<li><code>_is_dns_negative_error</code> (batch_dns.py) — <span class="doc-comment-inline">Return True if exception represents a DNS negative response (NXDOMAIN/SERVFAIL).</span></li>
<li><code>_prewarm_host</code> (batch_dns.py)</li>
<li><code>expand</code> (query_expansion.py) — <span class="doc-comment-inline">Expand query using semantic variations.</span></li>
<li><code>encode</code> (msgspec_json.py)
<details><summary>Fast encode Python object → JSON bytes.</summary>
<div class="doc-comment">
<p>Fast encode Python object → JSON bytes.</p>
<p></p>
<p>Uses per-thread pool of ``msgspec.json.Encoder`` to avoid lock contention.</p>
<p>Falls back to ``orjson`` on type errors (e.g. ``set``), then to</p>
<p>``json`` if neither is usable.</p>
<p></p>
<p>Args:</p>
<p>obj: JSON-serializable Python object.</p>
<p></p>
<p>Returns:</p>
<p>UTF-8 encoded JSON ``bytes``.</p>
</div>
</details>
</li>
<li><code>__init__</code> (deduplication.py)</li>
<li><code>_get_batch_embeddings</code> (deduplication.py) — <span class="doc-comment-inline">Get embeddings for a batch of items.</span></li>
<li><code>init_mlx_buffers</code> (_core.py)
<details><summary>Initialize MLX Metal memory limits.</summary>
<div class="doc-comment">
<p>Initialize MLX Metal memory limits.</p>
<p></p>
<p>DO NOT call at module import time — importing utils.mlx_memory must not</p>
<p>import mlx.core or configure Metal limits. Call explicitly when MLX is</p>
<p>about to be used.</p>
<p></p>
<p>M1 8GB: dynamic cache ceiling 1.5 GiB, wired 768 MiB fixed.</p>
</div>
</details>
</li>
<li><code>stats</code> (cache.py) — <span class="doc-comment-inline">Full stats including refcount telemetry.</span></li>
<li><code>_execute_adaptive</code> (execution_optimizer.py) — <span class="doc-comment-inline">Execute tasks with adaptive strategy</span></li>
<li><code>_load_from_disk</code> (filtering.py) — <span class="doc-comment-inline">Load frontier from disk.</span></li>
<li><code>expand</code> (query_expansion.py) — <span class="doc-comment-inline">Expand query using syntactic variations.</span></li>
<li><code>expand</code> (query_expansion.py)
<details><summary>Expand query using all configured strategies.</summary>
<div class="doc-comment">
<p>Expand query using all configured strategies.</p>
<p></p>
<p>Args:</p>
<p>query: Original query</p>
<p>context: Optional context (domain hints, etc.)</p>
<p></p>
<p>Returns:</p>
<p>List of query variations from all strategies</p>
</div>
</details>
</li>
<li><code>get</code> (cache.py)
<details><summary>Get value by key. Returns None on miss or if entry is expired.</summary>
<div class="doc-comment">
<p>Get value by key. Returns None on miss or if entry is expired.</p>
<p></p>
<p>Thread-safe. Refreshes TTL on hit (move to end).</p>
</div>
</details>
</li>
<li><code>get</code> (cache.py)
<details><summary>Get value by key. Returns None on miss or if entry is expired.</summary>
<div class="doc-comment">
<p>Get value by key. Returns None on miss or if entry is expired.</p>
<p></p>
<p>Async-safe. Refreshes TTL on hit (move to end).</p>
</div>
</details>
</li>
<li><code>set</code> (cache.py)
<details><summary>Set key-value pair. Lands in gen0 (youngest).</summary>
<div class="doc-comment">
<p>Set key-value pair. Lands in gen0 (youngest).</p>
<p></p>
<p>If gen0 is at capacity, promotes oldest 25% to gen1.</p>
<p>Thread-safe. Returns True on success, False on error.</p>
</div>
</details>
</li>
<li><code>promote</code> (cache.py)
<details><summary>Explicitly promote an entry one generation older (gen0 → gen1 → gen2).</summary>
<div class="doc-comment">
<p>Explicitly promote an entry one generation older (gen0 → gen1 → gen2).</p>
<p></p>
<p>Thread-safe. Returns True if entry was found and promoted.</p>
</div>
</details>
</li>
<li><code>trace_evidence_append_ext</code> (flow_trace.py)</li>
<li><code>filter_batch</code> (semantic.py)
<details><summary>Filter multiple contents against a query.</summary>
<div class="doc-comment">
<p>Filter multiple contents against a query.</p>
<p></p>
<p>Args:</p>
<p>contents: List of contents to filter</p>
<p>query: Query to match against</p>
<p>threshnew: Optional custom threshnew</p>
<p></p>
<p>Returns:</p>
<p>List of FilterResults</p>
</div>
</details>
</li>
<li><code>expand_for_discovery</code> (query_expansion.py)
<details><summary>Generate discovery-focused query variations.</summary>
<div class="doc-comment">
<p>Generate discovery-focused query variations.</p>
<p></p>
<p>Args:</p>
<p>base_terms: Base search terms</p>
<p>modifiers: Additional modifiers</p>
<p></p>
<p>Returns:</p>
<p>Combined expanded queries</p>
</div>
</details>
</li>
<li><code>_start_windown_monitor</code> (sprint_lifecycle.py) — <span class="doc-comment-inline">Start background task that fires wind-up at T-3min. Fail-open if no event loop.</span></li>
<li><code>decode_zstd</code> (msgspec_json.py)
<details><summary>Decode zstd-compressed JSON bytes (with length prefix).</summary>
<div class="doc-comment">
<p>Decode zstd-compressed JSON bytes (with length prefix).</p>
<p></p>
<p>Args:</p>
<p>data: Payload from :func:`encode_zstd`.</p>
<p></p>
<p>Returns:</p>
<p>Decoded Python object.</p>
<p></p>
<p>Raises:</p>
<p>RuntimeError: If zstd is not available.</p>
<p>ValueError: On length-prefix mismatch.</p>
</div>
</details>
</li>
<li><code>encode_for_arrow</code> (msgspec_json.py)
<details><summary>Encode for Arrow ``pa.array(bytes, type=pa.string())`` ingestion.</summary>
<div class="doc-comment">
<p>Encode for Arrow ``pa.array(bytes, type=pa.string())`` ingestion.</p>
<p></p>
<p>Arrow accepts ``bytes`` natively for UTF-8 string columns — this function</p>
<p>returns ``bytes | None`` so the caller can pass directly to ``pa.array()``</p>
<p>without an intermediate Python str decode.</p>
<p></p>
<p>Canonical use: ``_provenance_to_arrow_native`` in ``knowledge/duckdb_store.py``.</p>
<p>msgspec encodes tuples natively — no ``list()`` conversion needed.</p>
<p>Empty/None input returns ``None`` (SQL NULL / Arrow null).</p>
<p></p>
<p>Args:</p>
<p>obj: ``tuple[str, ...]``, ``list[str]``, or any JSON-serializable.</p>
<p>``None`` → returns ``None``.</p>
<p></p>
<p>Returns:</p>
<p>``bytes``: msgspec-encoded JSON, ready for ``pa.array(bytes, type=pa.string())``.</p>
<p>``None``: for ``None`` or empty input (Arrow null / SQL NULL).</p>
</div>
</details>
</li>
<li><code>__init__</code> (cache.py)</li>
<li><code>set</code> (cache.py)
<details><summary>Set key-value pair. Evicts oldest entry if at capacity.</summary>
<div class="doc-comment">
<p>Set key-value pair. Evicts oldest entry if at capacity.</p>
<p></p>
<p>Async-safe. Returns True on success, False on error.</p>
</div>
</details>
</li>
<li><code>trace_provider_fallback</code> (flow_trace.py)</li>
<li><code>get_metal_stream_context</code> (_core.py)
<details><summary>Return a thread-local mx.stream(gpu) context manager.</summary>
<div class="doc-comment">
<p>Return a thread-local mx.stream(gpu) context manager.</p>
<p>M1 8GB: cached per-thread, prevents "Stream(gpu,1) not in current thread" errors</p>
<p>when MLX is called from worker threads (MLXWorkerThread, asyncio.to_thread).</p>
<p>NOTE: threading.local is intentional — dedicated thread, not shared async pool.</p>
</div>
</details>
</li>
<li><code>decorator</code> (mlx_cache.py)</li>
<li><code>_load_lru_order</code> (persistent_kv_cache.py) — <span class="doc-comment-inline">Load LRU order from LMDB at startup.</span></li>
<li><code>cosine_similarity</code> (semantic.py)
<details><summary>Compute cosine similarity between two vectors.</summary>
<div class="doc-comment">
<p>Compute cosine similarity between two vectors.</p>
<p></p>
<p>Args:</p>
<p>vec1: First vector</p>
<p>vec2: Second vector</p>
<p></p>
<p>Returns:</p>
<p>Cosine similarity (-1 to 1)</p>
</div>
</details>
</li>
<li><code>_calculate_risk_score</code> (unicode_analyzer.py)
<details><summary>Calculate overall risk score based on findings.</summary>
<div class="doc-comment">
<p>Calculate overall risk score based on findings.</p>
<p></p>
<p>Returns:</p>
<p>Risk score from 0.0 (no risk) to 100.0 (critical)</p>
</div>
</details>
</li>
<li><code>detect_mixed_script</code> (unicode_analyzer.py)
<details><summary>Detect mixed-script usage in text (potential spoofing indicator).</summary>
<div class="doc-comment">
<p>Detect mixed-script usage in text (potential spoofing indicator).</p>
<p></p>
<p>Args:</p>
<p>text: Input text to check</p>
<p></p>
<p>Returns:</p>
<p>True if mixed scripts detected</p>
</div>
</details>
</li>
<li><code>run_in_rayon_pool</code> (rayon_pool.py)</li>
<li><code>stop_task</code> (async_helpers.py)
<details><summary>Stop a background task gracefully — cancel and await CancelledError.</summary>
<div class="doc-comment">
<p>Stop a background task gracefully — cancel and await CancelledError.</p>
<p></p>
<p>Standardises the ``_running + _task`` cancellation pattern used across</p>
<p>SprintScheduler, SystemResourcesSampler, ResourceGovernor and similar</p>
<p>run-loops.</p>
<p></p>
<p>Pattern::</p>
<p></p>
<p>self._running = False</p>
<p>await stop_task(self._task)</p>
<p>self._task = None</p>
<p></p>
<p>Args:</p>
<p>coro: The asyncio.Task to cancel. None or already-finished tasks</p>
<p>are handled silently (no-op).</p>
</div>
</details>
</li>
<li><code>set</code> (cache.py)
<details><summary>Set key-value pair. Evicts oldest entry if at capacity.</summary>
<div class="doc-comment">
<p>Set key-value pair. Evicts oldest entry if at capacity.</p>
<p></p>
<p>Thread-safe.</p>
<p>Returns True on success, False on error.</p>
</div>
</details>
</li>
<li><code>put</code> (cache.py)
<details><summary>Store (lora_model, lora_tokenizer) tuple for an adapter path.</summary>
<div class="doc-comment">
<p>Store (lora_model, lora_tokenizer) tuple for an adapter path.</p>
<p></p>
<p>Thread-safe. Evicts oldest entry when at capacity (LRU).</p>
<p>Returns True on success, False on error.</p>
</div>
</details>
</li>
<li><code>decorator</code> (execution_optimizer.py)</li>
<li><code>benchmark_match</code> (pattern_matcher.py)</li>
<li><code>get_mlx_memory_metrics</code> (_core.py) — <span class="doc-comment-inline">Convenience reporter for all MLX memory metrics.</span></li>
<li><code>resolve</code> (batch_dns.py)
<details><summary>Resolve hostname using c-ares (aiodns).</summary>
<div class="doc-comment">
<p>Resolve hostname using c-ares (aiodns).</p>
<p></p>
<p>Returns IPv4 addresses sorted and deduplicated.</p>
<p>Raises on failure (caller handles exceptions).</p>
</div>
</details>
</li>
<li><code>expand</code> (query_expansion.py) — <span class="doc-comment-inline">Expand query using domain-specific knowledge.</span></li>
<li><code>_detect_normalization_anomalies</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Detect Unicode normalization anomalies in text - optimized version.</span></li>
<li><code>acquire</code> (async_helpers.py)
<details><summary>Acquire a per-host concurrency slot.</summary>
<div class="doc-comment">
<p>Acquire a per-host concurrency slot.</p>
<p></p>
<p>Returns (semaphore_instance, op_id) where op_id is 'hit' or 'miss'.</p>
<p>The caller MUST pass the returned semaphore to ``release()`` —</p>
<p>NOT self._gates[host], which may have been evicted and replaced.</p>
</div>
</details>
</li>
<li><code>_safe_aclose</code> (async_helpers.py)</li>
<li><code>touch</code> (cache.py)
<details><summary>Refresh TTL for an existing key.</summary>
<div class="doc-comment">
<p>Refresh TTL for an existing key.</p>
<p></p>
<p>Thread-safe. Returns True if key existed (and is not expired),</p>
<p>False otherwise.</p>
</div>
</details>
</li>
<li><code>items</code> (cache.py)
<details><summary>Return list of (key, value) pairs, excluding expired.</summary>
<div class="doc-comment">
<p>Return list of (key, value) pairs, excluding expired.</p>
<p></p>
<p>Thread-safe. O(n) scan.</p>
</div>
</details>
</li>
<li><code>_promote_gen0_to_gen1</code> (cache.py) — <span class="doc-comment-inline">Promote oldest 25% of gen0 to gen1. Returns count promoted.</span></li>
<li><code>_promote_gen1_to_gen2</code> (cache.py) — <span class="doc-comment-inline">Promote oldest 25% of gen1 to gen2. Returns count promoted.</span></li>
<li><code>_make_room</code> (cache.py) — <span class="doc-comment-inline">Ensure space in gen0. If full, promote gen0→gen1→gen2, then evict gen2.</span></li>
<li><code>throttle_delay_ms</code> (deduplication.py)
<details><summary>Calculate throttle delay based on domain health.</summary>
<div class="doc-comment">
<p>Calculate throttle delay based on domain health.</p>
<p>Increases delay if stale cache is used frequently.</p>
</div>
</details>
</li>
<li><code>_execute_with_semaphore</code> (execution_optimizer.py)
<details><summary>Execute a single task with semaphore gating.</summary>
<div class="doc-comment">
<p>Execute a single task with semaphore gating.</p>
<p></p>
<p>F214OPT-D: Wraps task execution with pending semaphore to prevent</p>
<p>unbounded concurrent task creation. Tracks throttling for telemetry.</p>
<p></p>
<p>CPU-bound work routes to Rust rayon pools via _rust_pool_dispatch().</p>
</div>
</details>
</li>
<li><code>trace_fallback_after_429</code> (flow_trace.py)</li>
<li><code>trace_queue_snapshot</code> (flow_trace.py)</li>
<li><code>_save_to_disk</code> (filtering.py) — <span class="doc-comment-inline">Save frontier to disk.</span></li>
<li><code>_select_eviction_candidate</code> (intelligent_cache.py) — <span class="doc-comment-inline">Select key to evict based on strategy.</span></li>
<li><code>_init_embedding</code> (semantic.py) — <span class="doc-comment-inline">Initialize embedding model.</span></li>
<li><code>_expand_acronyms</code> (query_expansion.py) — <span class="doc-comment-inline">Expand acronyms in query</span></li>
<li><code>_runner</code> (async_helpers.py)</li>
<li><code>set</code> (cache.py)
<details><summary>Set key-value pair. Lands in gen0.</summary>
<div class="doc-comment">
<p>Set key-value pair. Lands in gen0.</p>
<p></p>
<p>Thread-safe. Returns True on success.</p>
</div>
</details>
</li>
<li><code>_generate_batch_embeddings</code> (deduplication.py) — <span class="doc-comment-inline">Generate embeddings for batch of contents using dedup-specific task.</span></li>
<li><code>_load_model</code> (deduplication.py) — <span class="doc-comment-inline">Load MLXEmbeddingManager first, then sentence-transformers fallback, then hash-based.</span></li>
<li><code>record_request</code> (deduplication.py) — <span class="doc-comment-inline">Zaznamena vysledek requestu a aktualizuje yield.</span></li>
<li><code>_adapt_worker_count</code> (execution_optimizer.py) — <span class="doc-comment-inline">Adapt worker count based on performance and resources</span></li>
<li><code>configure_default_bootstrap_patterns_if_empty</code> (pattern_matcher.py)
<details><summary>Bootstrap the matcher with OSINT literal pack if registry is empty.</summary>
<div class="doc-comment">
<p>Bootstrap the matcher with OSINT literal pack if registry is empty.</p>
<p></p>
<p>Idempotent: does nothing when registry already contains patterns.</p>
<p>Does not overwrite existing registry.</p>
<p></p>
<p>Returns:</p>
<p>True if bootstrap was applied, False if registry was non-empty</p>
<p>or bootstrap failed.</p>
</div>
</details>
</li>
<li><code>trace_challenge_issued</code> (flow_trace.py)</li>
<li><code>trace_challenge_passed</code> (flow_trace.py)</li>
<li><code>trace_challenge_failed</code> (flow_trace.py)</li>
<li><code>trace_evidence_flush_persisted</code> (flow_trace.py)</li>
<li><code>trace_periodic_flow_snapshot</code> (flow_trace.py)</li>
<li><code>_evict_entry</code> (persistent_kv_cache.py) — <span class="doc-comment-inline">Evict a single entry by key. Returns bytes freed.</span></li>
<li><code>begin_sprint</code> (sprint_lifecycle.py)
<details><summary>Mark sprint as started, transition to WARMUP.</summary>
<div class="doc-comment">
<p>Mark sprint as started, transition to WARMUP.</p>
<p></p>
<p>Sprint F4 metadata:</p>
<p>alias_for: runtime.SprintLifecycleManager.start()</p>
<p>future_owner: __main__.py, legacy autonomous_orchestrator</p>
<p>caller_class: legacy autonomous_orchestrator (line ~11723)</p>
<p>removal_condition: All callers migrated to runtime version</p>
<p>why_still_needed: legacy AO still imports utils version directly</p>
</div>
</details>
</li>
<li><code>request_teardown</code> (sprint_lifecycle.py)
<details><summary>Transition from any winding-down state to TEARDOWN.</summary>
<div class="doc-comment">
<p>Transition from any winding-down state to TEARDOWN.</p>
<p></p>
<p>Sprint F4 metadata:</p>
<p>alias_for: runtime.SprintLifecycleManager.mark_teardown_started()</p>
<p>future_owner: __main__.py</p>
<p>caller_class: legacy autonomous_orchestrator (line ~12690)</p>
<p>removal_condition: All callers migrated to runtime version</p>
<p>why_still_needed: legacy AO still calls this; __main__.py does not call this method</p>
</div>
</details>
</li>
<li><code>run_in_mixed_pool_async</code> (rayon_pool.py)</li>
<li><code>find_duplicates</code> (deduplication.py) — <span class="doc-comment-inline">Find semantically similar items.</span></li>
<li><code>_fallback_embedding</code> (deduplication.py) — <span class="doc-comment-inline">Generate fallback embedding using hash-based approach.</span></li>
<li><code>_deduplicate_matches</code> (deduplication.py) — <span class="doc-comment-inline">Deduplicate matches and apply decision logic.</span></li>
<li><code>_monitor_loop</code> (execution_optimizer.py) — <span class="doc-comment-inline">Background loop that adjusts concurrency limit based on memory.</span></li>
<li><code>_execute_with_dynamic_workers</code> (execution_optimizer.py) — <span class="doc-comment-inline">Execute tasks with dynamic worker allocation</span></li>
<li><code>trace_source_dedup_dropped</code> (flow_trace.py)</li>
<li><code>evict_one</code> (intelligent_cache.py) — <span class="doc-comment-inline">Evict one item and return its key. Returns None if nothing to evict.</span></li>
<li><code>add</code> (intelligent_cache.py)
<details><summary>Add URL if not already present and within memory limit.</summary>
<div class="doc-comment">
<p>Add URL if not already present and within memory limit.</p>
<p></p>
<p>Args:</p>
<p>url: URL to add</p>
<p></p>
<p>Returns:</p>
<p>True if added, False if already present or memory limit reached</p>
</div>
</details>
</li>
<li><code>contains_keywords</code> (semantic.py)
<details><summary>Check if content contains minimum number of keywords.</summary>
<div class="doc-comment">
<p>Check if content contains minimum number of keywords.</p>
<p></p>
<p>Args:</p>
<p>content: Content to check</p>
<p>keywords: List of keywords to look for</p>
<p>min_matches: Minimum number of keyword matches</p>
<p></p>
<p>Returns:</p>
<p>True if enough keywords found</p>
</div>
</details>
</li>
<li><code>get_uma_budget</code> (uma_budget.py)
<details><summary>Back-compat alias for `get_uma_snapshot()`.</summary>
<div class="doc-comment">
<p>Back-compat alias for `get_uma_snapshot()`.</p>
<p></p>
<p>Several callers (e.g. `rl/sprint_policy_manager.py`,</p>
<p>`tests/probe_f261_qmix_activation.py`, and the historical sprint-260</p>
<p>era mocks) reference `get_uma_budget` by name. The canonical contract</p>
<p>is `get_uma_snapshot` — this alias is a thin pass-through so any</p>
<p>future refactor of the snapshot shape only needs to update a single</p>
<p>source of truth.</p>
<p></p>
<p>Returns:</p>
<p>The same dict as `get_uma_snapshot()` — see that function for</p>
<p>the full key list (`uma_total_mb`, `warn_threshold_mb`,</p>
<p>`critical_threshold_mb`, `emergency_threshold_mb`,</p>
<p>`system_total_mb`, `system_used_mb`, `system_available_mb`,</p>
<p>`mlx_active_mb`, `mlx_peak_mb`, `mlx_cache_mb`, `uma_used_mb`,</p>
<p>`uma_usage_pct`, `uma_pressure_level`, `is_warn`, `is_critical`,</p>
<p>`is_emergency`, `platform`).</p>
</div>
</details>
</li>
<li><code>decode_typed</code> (msgspec_json.py)
<details><summary>Typed msgspec decode — use for known-schema hot paths.</summary>
<div class="doc-comment">
<p>Typed msgspec decode — use for known-schema hot paths.</p>
<p></p>
<p>Falls back to untyped dict on ``msgspec.ValidationError`` (schema</p>
<p>mismatch tolerance: unknown fields, missing optionals, type drift).</p>
<p></p>
<p>Args:</p>
<p>raw: JSON bytes payload.</p>
<p>typ: A ``msgspec.Struct`` subclass to decode into.</p>
<p></p>
<p>Returns:</p>
<p>Instance of ``typ`` on success, plain ``dict`` (or list/scalar)</p>
<p>on schema mismatch.</p>
</div>
</details>
</li>
<li><code>run_in_cpu_pool_async</code> (rayon_pool.py)</li>
<li><code>run_in_io_pool_async</code> (rayon_pool.py)</li>
<li><code>__getitem__</code> (cache.py) — <span class="doc-comment-inline">Raise KeyError on miss/expired — unlike get().</span></li>
<li><code>touch</code> (cache.py) — <span class="doc-comment-inline">Refresh TTL for an existing key. Async-safe.</span></li>
<li><code>get</code> (cache.py)
<details><summary>Get value by key. Checks gen0 → gen1 → gen2 (youngest first).</summary>
<div class="doc-comment">
<p>Get value by key. Checks gen0 → gen1 → gen2 (youngest first).</p>
<p></p>
<p>Thread-safe. Returns None on miss (including GC'd entries).</p>
</div>
</details>
</li>
<li><code>find_duplicates</code> (deduplication.py) — <span class="doc-comment-inline">Find metadata-based duplicates.</span></li>
<li><code>__init__</code> (deduplication.py)</li>
<li><code>compute</code> (deduplication.py) — <span class="doc-comment-inline">Compute SimHash for text - classical token-based approach.</span></li>
<li><code>_execute_load_balanced</code> (execution_optimizer.py) — <span class="doc-comment-inline">Execute tasks with load balancing</span></li>
<li><code>trace_span_start</code> (flow_trace.py)
<details><summary>Start a trace span.</summary>
<div class="doc-comment">
<p>Start a trace span.</p>
<p></p>
<p>Args:</p>
<p>span_id: Unique span identifier</p>
<p>metadata: Optional span metadata</p>
<p></p>
<p>Returns:</p>
<p>Start timestamp for span</p>
</div>
</details>
</li>
<li><code>get_summary</code> (flow_trace.py)
<details><summary>Generate trace summary statistics.</summary>
<div class="doc-comment">
<p>Generate trace summary statistics.</p>
<p></p>
<p>Returns:</p>
<p>Summary dict with counts, p50/p95 durations, top stages, etc.</p>
</div>
</details>
</li>
<li><code>get_metal_limits_status</code> (mlx_cache.py)
<details><summary>Observability surface for Metal memory limit configuration.</summary>
<div class="doc-comment">
<p>Observability surface for Metal memory limit configuration.</p>
<p></p>
<p>Returns:</p>
<p>dict with keys:</p>
<p>- mlx_available: bool — whether mlx.core spec was found at import time</p>
<p>- configured: bool — whether limits have been initialized</p>
<p>- cache_limit_bytes: int or None</p>
<p>- wired_limit_bytes: int or None</p>
<p>- last_error: str or None</p>
</div>
</details>
</li>
<li><code>_load_persisted</code> (intelligent_cache.py) — <span class="doc-comment-inline">Load persisted cache from disk.</span></li>
<li><code>cosine_similarity</code> (semantic.py)
<details><summary>Compute cosine similarity between two vectors.</summary>
<div class="doc-comment">
<p>Compute cosine similarity between two vectors.</p>
<p></p>
<p>Args:</p>
<p>vec1: First vector</p>
<p>vec2: Second vector</p>
<p></p>
<p>Returns:</p>
<p>Cosine similarity (-1 to 1)</p>
</div>
</details>
</li>
<li><code>request_export</code> (sprint_lifecycle.py)
<details><summary>Transition from WINDUP to EXPORT. Called after synthesis phase.</summary>
<div class="doc-comment">
<p>Transition from WINDUP to EXPORT. Called after synthesis phase.</p>
<p></p>
<p>Sprint F4 metadata:</p>
<p>alias_for: runtime.SprintLifecycleManager.mark_export_started()</p>
<p>future_owner: __main__.py</p>
<p>caller_class: legacy autonomous_orchestrator (line ~12357)</p>
<p>removal_condition: All callers migrated to runtime version</p>
<p>why_still_needed: legacy AO still calls this; __main__.py uses canonical directly</p>
</div>
</details>
</li>
<li><code>encode_zstd</code> (msgspec_json.py)
<details><summary>Encode + zstd-compress with 4-byte length prefix.</summary>
<div class="doc-comment">
<p>Encode + zstd-compress with 4-byte length prefix.</p>
<p></p>
<p>Args:</p>
<p>obj: JSON-serializable object.</p>
<p>level: zstd compression level (1 fast — 22 max; default 3 is</p>
<p>a good speed/ratio trade-off for small payloads).</p>
<p></p>
<p>Returns:</p>
<p>``struct.pack('&lt;I', raw_len) + zstd_compressed(raw)`` bytes.</p>
<p></p>
<p>Raises:</p>
<p>RuntimeError: If zstd is not available.</p>
</div>
</details>
</li>
<li><code>_wrap_awaitable</code> (async_helpers.py)
<details><summary>Wrap a plain value in a coroutine so asyncio.gather accepts it.</summary>
<div class="doc-comment">
<p>Wrap a plain value in a coroutine so asyncio.gather accepts it.</p>
<p></p>
<p>asyncio.gather (Python 3.10+) requires awaitables, not plain values.</p>
<p>When callers mix `safe_gather(coro1, 42, coro2)`, plain values must</p>
<p>be wrapped. M1-safe: a one-line async lambda per plain value (≈ 200B</p>
<p>per closure), reused only for the duration of the gather call.</p>
<p></p>
<p>If `value` is already awaitable (coroutine, Future, Task, or has</p>
<p>__await__), it's returned unchanged.</p>
</div>
</details>
</li>
<li><code>get</code> (cache.py)
<details><summary>Get (lora_model, lora_tokenizer) tuple by adapter path.</summary>
<div class="doc-comment">
<p>Get (lora_model, lora_tokenizer) tuple by adapter path.</p>
<p></p>
<p>Thread-safe. Refreshes LRU order on hit.</p>
<p>Returns None on miss.</p>
</div>
</details>
</li>
<li><code>_find_duplicates</code> (deduplication.py) — <span class="doc-comment-inline">Find duplicates for an item.</span></li>
<li><code>add</code> (deduplication.py)
<details><summary>Add fingerprint, return True if near-duplicate found.</summary>
<div class="doc-comment">
<p>Add fingerprint, return True if near-duplicate found.</p>
<p></p>
<p>Scans only neighboring buckets (bucket itself + 2-3 nearby buckets</p>
<p>defined by 1-bit flips in top-K space). Full Hamming check only</p>
<p>against candidates in those buckets.</p>
</div>
</details>
</li>
<li><code>main</code> (execution_optimizer.py) — <span class="doc-comment-inline">Main function for parallel execution optimizer testing</span></li>
<li><code>_safe_metadata</code> (flow_trace.py) — <span class="doc-comment-inline">Sanitize metadata dict for trace safety.</span></li>
<li><code>trace_fallback_after_403</code> (flow_trace.py)</li>
<li><code>trace_challenge_loop_detected</code> (flow_trace.py)</li>
<li><code>trace_clearance_reused</code> (flow_trace.py)</li>
<li><code>trace_evidence_emitted</code> (flow_trace.py)</li>
<li><code>trace_evidence_corroborated</code> (flow_trace.py)</li>
<li><code>trace_evidence_rejected_low_quality</code> (flow_trace.py)</li>
<li><code>clear_mlx_cache</code> (_core.py)
<details><summary>Canonical Metal cache clear — delegates to mlx_cleanup_sync().</summary>
<div class="doc-comment">
<p>Canonical Metal cache clear — delegates to mlx_cleanup_sync().</p>
<p></p>
<p>Sequence (per GHOST_INVARIANTS.md:80): gc.collect() → mx.eval([]) →</p>
<p>mx.clear_cache() → gc.collect()</p>
<p></p>
<p>F330-DUP: this was the legacy duplicate implementation. Now delegates</p>
<p>to mlx_cleanup_sync() which is the single canonical source of truth.</p>
</div>
</details>
</li>
<li><code>_has_body_content_html</code> (hydration_extractor.py)
<details><summary>F265C: Check if raw HTML contains actual body content elements.</summary>
<div class="doc-comment">
<p>F265C: Check if raw HTML contains actual body content elements.</p>
<p></p>
<p>This is the content-depth check that prevents metadata-only pages</p>
<p>(OpenSearch JSON, JSON-LD without article body, etc.) from being</p>
<p>marked as sufficient. Pages with only &lt;meta&gt; tags but no &lt;p&gt;,</p>
<p>&lt;article&gt;, &lt;main&gt;, &lt;section&gt;, &lt;ul&gt;, &lt;ol&gt;, &lt;dl&gt;, &lt;table&gt;, &lt;blockquote&gt;,</p>
<p>or heading tags are NOT sufficient — they need JS rendering.</p>
<p></p>
<p>Returns True if at least one body-content tag is found after</p>
<p>stripping skip tags (script/style/noscript/svg/canvas).</p>
</div>
</details>
</li>
<li><code>initialize</code> (intelligent_cache.py)
<details><summary>Initialize cache and load persisted data.</summary>
<div class="doc-comment">
<p>Initialize cache and load persisted data.</p>
<p></p>
<p>Returns:</p>
<p>True if initialization successful</p>
</div>
</details>
</li>
<li><code>__init__</code> (semantic.py)
<details><summary>Initialize ModernBERTEmbedding.</summary>
<div class="doc-comment">
<p>Initialize ModernBERTEmbedding.</p>
<p></p>
<p>Args:</p>
<p>model_path: Optional custom model path (default: 6bit ModernBERT)</p>
</div>
</details>
</li>
<li><code>encode</code> (semantic.py)
<details><summary>Encode text to embedding vector.</summary>
<div class="doc-comment">
<p>Encode text to embedding vector.</p>
<p></p>
<p>Args:</p>
<p>text: Text to encode</p>
<p></p>
<p>Returns:</p>
<p>Embedding vector (768 dimensions)</p>
</div>
</details>
</li>
<li><code>fit</code> (semantic.py)
<details><summary>Build vocabulary from documents.</summary>
<div class="doc-comment">
<p>Build vocabulary from documents.</p>
<p></p>
<p>Args:</p>
<p>documents: List of documents</p>
</div>
</details>
</li>
<li><code>request_windup</code> (sprint_lifecycle.py)
<details><summary>Request wind-down. Can be called from timer, SIGINT/SIGTERM, or manual trigger.</summary>
<div class="doc-comment">
<p>Request wind-down. Can be called from timer, SIGINT/SIGTERM, or manual trigger.</p>
<p>Idempotent — only fires once.</p>
<p></p>
<p>Sprint F4 metadata:</p>
<p>alias_for: runtime.SprintLifecycleManager.transition_to(WINDUP)</p>
<p>future_owner: __main__.py</p>
<p>caller_class: legacy autonomous_orchestrator (not called per grep)</p>
<p>removal_condition: All callers migrated to runtime version</p>
<p>why_still_needed: legacy AO has this as available method but no active call-sites</p>
</div>
</details>
</li>
<li><code>on_critical</code> (uma_budget.py) — <span class="doc-comment-inline">Trigger MLX cache cleanup on CRITICAL state.</span></li>
<li><code>on_emergency</code> (uma_budget.py) — <span class="doc-comment-inline">Trigger aggressive cleanup on EMERGENCY state.</span></li>
<li><code>stats</code> (cache.py)
<details><summary>Hit/miss/eviction/expiration stats for cache efficiency monitoring.</summary>
<div class="doc-comment">
<p>Hit/miss/eviction/expiration stats for cache efficiency monitoring.</p>
<p></p>
<p>Returns a copy — safe for read-only access.</p>
</div>
</details>
</li>
<li><code>stats</code> (cache.py) — <span class="doc-comment-inline">Hit/miss/eviction/promotion stats.</span></li>
<li><code>get</code> (cache.py)
<details><summary>Get value by key. Returns None on miss.</summary>
<div class="doc-comment">
<p>Get value by key. Returns None on miss.</p>
<p></p>
<p>Thread-safe.</p>
</div>
</details>
</li>
<li><code>clear</code> (cache.py) — <span class="doc-comment-inline">Clear all generations. Thread-safe.</span></li>
<li><code>_compute_field_similarities</code> (deduplication.py) — <span class="doc-comment-inline">Compute similarities for each field.</span></li>
<li><code>__init__</code> (execution_optimizer.py)</li>
<li><code>_run_in_executor_safe</code> (execution_optimizer.py)
<details><summary>Run coroutine func in executor safely - handles running loop correctly.</summary>
<div class="doc-comment">
<p>Run coroutine func in executor safely - handles running loop correctly.</p>
<p></p>
<p>M1-SAFE: When a loop is already running, use run_until_complete on the</p>
<p>existing loop from the worker thread. This avoids creating a nested event</p>
<p>loop with asyncio.run() which crashes Metal on Apple Silicon M1.</p>
</div>
</details>
</li>
<li><code>analyze_workload_pattern</code> (execution_optimizer.py) — <span class="doc-comment-inline">Analyze workload patterns for optimization recommendations.</span></li>
<li><code>get_optimal_thread_count</code> (execution_optimizer.py)
<details><summary>Get optimal thread count based on task type and core topology</summary>
<div class="doc-comment">
<p>Get optimal thread count based on task type and core topology</p>
<p></p>
<p>Args:</p>
<p>task_type: "cpu_bound", "io_bound", "mixed"</p>
<p></p>
<p>Returns:</p>
<p>Recommended thread count</p>
</div>
</details>
</li>
<li><code>wrapper</code> (execution_optimizer.py)</li>
<li><code>get_dynamic_metal_cache_limit</code> (_core.py)
<details><summary>Dynamic Metal cache limit: 20% of available UMA, clamp [256MiB, 1.5GiB].</summary>
<div class="doc-comment">
<p>Dynamic Metal cache limit: 20% of available UMA, clamp [256MiB, 1.5GiB].</p>
<p>Called by init_mlx_buffers; not for direct use by callers.</p>
</div>
</details>
</li>
<li><code>safe_set_cache_limit</code> (_core.py) — <span class="doc-comment-inline">Set Metal cache limit. Returns True on success.</span></li>
<li><code>is_blocked</code> (filtering.py) — <span class="doc-comment-inline">Check if URL is blocked.</span></li>
<li><code>load_blocklist_file</code> (filtering.py) — <span class="doc-comment-inline">Load blocklist from file (one entry per line).</span></li>
<li><code>on_access</code> (intelligent_cache.py) — <span class="doc-comment-inline">Record cache hit - move from T1 to T2 or update in T2.</span></li>
<li><code>__init__</code> (intelligent_cache.py)
<details><summary>Initialize intelligent cache.</summary>
<div class="doc-comment">
<p>Initialize intelligent cache.</p>
<p></p>
<p>Args:</p>
<p>config: Cache configuration</p>
</div>
</details>
</li>
<li><code>close</code> (intelligent_cache.py) — <span class="doc-comment-inline">Close cache and cleanup resources.</span></li>
<li><code>__init__</code> (persistent_kv_cache.py)</li>
<li><code>filter</code> (semantic.py)
<details><summary>Filter content based on semantic similarity to query.</summary>
<div class="doc-comment">
<p>Filter content based on semantic similarity to query.</p>
<p></p>
<p>Args:</p>
<p>content: Content to filter</p>
<p>query: Query to match against</p>
<p>threshnew: Optional custom threshnew</p>
<p></p>
<p>Returns:</p>
<p>FilterResult with filtering result</p>
</div>
</details>
</li>
<li><code>extract_matching_keywords</code> (semantic.py)
<details><summary>Extract keywords that appear in content.</summary>
<div class="doc-comment">
<p>Extract keywords that appear in content.</p>
<p></p>
<p>Args:</p>
<p>content: Content to extract from</p>
<p>keywords: List of keywords to check</p>
<p></p>
<p>Returns:</p>
<p>List of matching keywords</p>
</div>
</details>
</li>
<li><code>_generate_synonym_variations</code> (query_expansion.py) — <span class="doc-comment-inline">Generate variations by replacing words with synonyms</span></li>
<li><code>__init__</code> (sprint_lifecycle.py)</li>
<li><code>_safe_close_async</code> (async_helpers.py)</li>
<li><code>purge_expired</code> (cache.py)
<details><summary>Remove all expired entries. O(n) scan with lock held.</summary>
<div class="doc-comment">
<p>Remove all expired entries. O(n) scan with lock held.</p>
<p></p>
<p>Thread-safe. Returns number of purged entries.</p>
</div>
</details>
</li>
<li><code>purge_expired</code> (cache.py) — <span class="doc-comment-inline">Remove all expired entries. Async-safe. Returns purge count.</span></li>
<li><code>evict_oldest</code> (cache.py)
<details><summary>Evict and return the oldest (LRU) entry, or None if cache is empty.</summary>
<div class="doc-comment">
<p>Evict and return the oldest (LRU) entry, or None if cache is empty.</p>
<p></p>
<p>Thread-safe.</p>
</div>
</details>
</li>
<li><code>_text_similarity</code> (deduplication.py) — <span class="doc-comment-inline">Compute text similarity.</span></li>
<li><code>_execute_round_robin</code> (execution_optimizer.py) — <span class="doc-comment-inline">Execute tasks using round-robin distribution</span></li>
<li><code>put</code> (execution_optimizer.py) — <span class="doc-comment-inline">Put value into cache with predictive eviction.</span></li>
<li><code>_evict_one</code> (execution_optimizer.py) — <span class="doc-comment-inline">Evict one item using predictive strategy.</span></li>
<li><code>_extract_from_script</code> (hydration_extractor.py) — <span class="doc-comment-inline">Extract JSON string content from first matching script tag.</span></li>
<li><code>on_warn</code> (uma_budget.py) — <span class="doc-comment-inline">F265H-EXT: Lightweight GC on normal→warn transition (prevents cascade).</span></li>
<li><code>__contains__</code> (cache.py) — <span class="doc-comment-inline">Check key exists and is not expired. O(1). Thread-safe.</span></li>
<li><code>clear</code> (cache.py) — <span class="doc-comment-inline">Clear all entries. Async-safe.</span></li>
<li><code>_evict_lru_from</code> (cache.py) — <span class="doc-comment-inline">Evict `count` oldest entries (first in dict order) from gen. Returns evicted.</span></li>
<li><code>from_dict</code> (deduplication.py) — <span class="doc-comment-inline">Create from dict.</span></li>
<li><code>_resolve_max_pending_ops</code> (execution_optimizer.py)
<details><summary>Resolve max pending ops from env or return M1-safe default.</summary>
<div class="doc-comment">
<p>Resolve max pending ops from env or return M1-safe default.</p>
<p></p>
<p>F214OPT-D: M1 8GB can only handle ~4-8 concurrent tasks before Metal</p>
<p>memory pressure causes OOM. Default to 4 (conservative) to leave headroom</p>
<p>for the LLM itself (~2GB KV cache + activations).</p>
</div>
</details>
</li>
<li><code>_train_prediction_model</code> (execution_optimizer.py) — <span class="doc-comment-inline">Train prediction model on historical task data</span></li>
<li><code>get_mlx_active_memory_mb</code> (_core.py) — <span class="doc-comment-inline">Get active MLX memory in MB.</span></li>
<li><code>get_mlx_peak_memory_mb</code> (_core.py) — <span class="doc-comment-inline">Get peak MLX memory in MB.</span></li>
<li><code>get_mlx_cache_memory_mb</code> (_core.py) — <span class="doc-comment-inline">Get MLX cache memory in MB.</span></li>
<li><code>get_mlx_memory_pressure</code> (_core.py) — <span class="doc-comment-inline">Return (usage_pct, level) where level is NORMAL|WARNING|CRITICAL.</span></li>
<li><code>safe_get_cache_limit</code> (_core.py) — <span class="doc-comment-inline">Get current Metal cache limit. Returns None on failure.</span></li>
<li><code>build</code> (filtering.py) — <span class="doc-comment-inline">Build the filter from added items.</span></li>
<li><code>__init__</code> (filtering.py)</li>
<li><code>_save_sqlite</code> (filtering.py) — <span class="doc-comment-inline">Save frontier to SQLite.</span></li>
<li><code>delete</code> (intelligent_cache.py)
<details><summary>Delete entry from cache.</summary>
<div class="doc-comment">
<p>Delete entry from cache.</p>
<p></p>
<p>Args:</p>
<p>key: Cache key</p>
<p></p>
<p>Returns:</p>
<p>True if deleted, False if not found</p>
</div>
</details>
</li>
<li><code>update</code> (intelligent_cache.py)
<details><summary>Add multiple URLs.</summary>
<div class="doc-comment">
<p>Add multiple URLs.</p>
<p></p>
<p>Args:</p>
<p>urls: List of URLs to add</p>
<p></p>
<p>Returns:</p>
<p>Number of URLs actually added</p>
</div>
</details>
</li>
<li><code>tokenize</code> (semantic.py)
<details><summary>Tokenize text into words.</summary>
<div class="doc-comment">
<p>Tokenize text into words.</p>
<p></p>
<p>Args:</p>
<p>text: Text to tokenize</p>
<p></p>
<p>Returns:</p>
<p>List of tokens</p>
</div>
</details>
</li>
<li><code>extract_keywords</code> (semantic.py)
<details><summary>Extract top keywords from text.</summary>
<div class="doc-comment">
<p>Extract top keywords from text.</p>
<p></p>
<p>Args:</p>
<p>text: Text to extract keywords from</p>
<p>top_k: Number of keywords to return</p>
<p></p>
<p>Returns:</p>
<p>List of top keywords</p>
</div>
</details>
</li>
<li><code>expand_query</code> (query_expansion.py)
<details><summary>Quick query expansion.</summary>
<div class="doc-comment">
<p>Quick query expansion.</p>
<p></p>
<p>Args:</p>
<p>query: Original query</p>
<p>domain: Domain context ('academic', 'medical', 'tech')</p>
<p>max_variations: Maximum variations to generate</p>
<p></p>
<p>Returns:</p>
<p>List of query variations</p>
</div>
</details>
</li>
<li><code>__exit__</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Context manager exit.</span></li>
<li><code>create_unicode_analyzer</code> (unicode_analyzer.py)
<details><summary>Factory function to create a Unicode attack analyzer.</summary>
<div class="doc-comment">
<p>Factory function to create a Unicode attack analyzer.</p>
<p></p>
<p>Args:</p>
<p>config: Optional configuration for the analyzer</p>
<p></p>
<p>Returns:</p>
<p>UnicodeAttackAnalyzer instance or None if creation fails</p>
</div>
</details>
</li>
<li><code>mark_warmup_done</code> (sprint_lifecycle.py)
<details><summary>Transition from WARMUP to ACTIVE. Idempotent.</summary>
<div class="doc-comment">
<p>Transition from WARMUP to ACTIVE. Idempotent.</p>
<p></p>
<p>Sprint F4 metadata:</p>
<p>alias_for: runtime.SprintLifecycleManager.transition_to(ACTIVE)</p>
<p>future_owner: __main__.py</p>
<p>caller_class: legacy autonomous_orchestrator (not currently called per grep)</p>
<p>removal_condition: All callers migrated to runtime version</p>
<p>why_still_needed: legacy AO imports this module; no active call-sites in legacy AO per current grep</p>
</div>
</details>
</li>
<li><code>get_uma_usage_mb</code> (uma_budget.py)
<details><summary>Estimate of "used" UMA memory.</summary>
<div class="doc-comment">
<p>Estimate of "used" UMA memory.</p>
<p></p>
<p>On M1 unified memory architecture, system RSS includes MLX allocations,</p>
<p>so we take the maximum to avoid double-counting:</p>
<p>- sys_used &gt;= mlx_active → MLX is subset of RSS, use sys_used</p>
<p>- mlx_active &gt; sys_used → edge case: MLX alloc without RSS footprint</p>
<p></p>
<p>Returns None if system memory unavailable.</p>
</div>
</details>
</li>
<li><code>_check_rayon_availability</code> (rayon_pool.py) — <span class="doc-comment-inline">Check if Rust rayon extension is available (not all builds have it).</span></li>
<li><code>_evict_idle</code> (async_helpers.py)
<details><summary>Evict LRU hosts when over capacity (called lazily on miss).</summary>
<div class="doc-comment">
<p>Evict LRU hosts when over capacity (called lazily on miss).</p>
<p></p>
<p>Uses OrderedDict LRU ordering: move_to_end() marks recent access,</p>
<p>popitem(last=False) evicts oldest — both O(1) C-implemented.</p>
</div>
</details>
</li>
<li><code>clear</code> (cache.py) — <span class="doc-comment-inline">Clear all generations. Thread-safe.</span></li>
<li><code>get_refcount</code> (cache.py)
<details><summary>Get current refcount for an entry. Useful for telemetry.</summary>
<div class="doc-comment">
<p>Get current refcount for an entry. Useful for telemetry.</p>
<p></p>
<p>Returns 0 if key not found.</p>
</div>
</details>
</li>
<li><code>evict_gen2</code> (cache.py)
<details><summary>Evict oldest generation (gen2) entries by LRU.</summary>
<div class="doc-comment">
<p>Evict oldest generation (gen2) entries by LRU.</p>
<p></p>
<p>Call after evict_orphaned() to clear aged entries.</p>
<p></p>
<p>Returns:</p>
<p>Number of entries evicted.</p>
</div>
</details>
</li>
<li><code>_get_embedding</code> (deduplication.py) — <span class="doc-comment-inline">Get embedding for a single item.</span></li>
<li><code>get</code> (execution_optimizer.py) — <span class="doc-comment-inline">Get value from cache with access tracking.</span></li>
<li><code>schedule</code> (execution_optimizer.py) — <span class="doc-comment-inline">Schedule task with memory awareness.</span></li>
<li><code>trace_counter</code> (flow_trace.py)
<details><summary>Increment a named counter.</summary>
<div class="doc-comment">
<p>Increment a named counter.</p>
<p></p>
<p>Args:</p>
<p>name: Counter name</p>
<p>value: Increment value (default 1)</p>
<p>metadata: Optional metadata</p>
</div>
</details>
</li>
<li><code>trace_evidence_flush</code> (flow_trace.py)</li>
<li><code>trace_transport_mix_snapshot</code> (flow_trace.py)</li>
<li><code>_json_ld_types</code> (hydration_extractor.py) — <span class="doc-comment-inline">Recursively collect @type values from JSON-LD structure.</span></li>
<li><code>to_dict</code> (batch_dns.py)</li>
<li><code>_ensure_aiodns</code> (batch_dns.py) — <span class="doc-comment-inline">Lazily init aiodns resolver. Returns True if available.</span></li>
<li><code>get_mx</code> (mlx_cache.py)
<details><summary>Lazy accessor for mlx.core module — never holds a module-level reference.</summary>
<div class="doc-comment">
<p>Lazy accessor for mlx.core module — never holds a module-level reference.</p>
<p>Returns the mlx.core module object if available, otherwise None.</p>
<p></p>
<p>Usage pattern:</p>
<p>mx = get_mx()</p>
<p>if mx is None:</p>
<p>return fallback_result</p>
<p>arr = mx.array([1, 2, 3])</p>
</div>
</details>
</li>
<li><code>on_set</code> (intelligent_cache.py) — <span class="doc-comment-inline">Record new item set.</span></li>
<li><code>_track_task</code> (intelligent_cache.py) — <span class="doc-comment-inline">F196B: Track background tasks for proper cleanup.</span></li>
<li><code>__init__</code> (semantic.py)
<details><summary>Initialize SemanticFilter.</summary>
<div class="doc-comment">
<p>Initialize SemanticFilter.</p>
<p></p>
<p>Args:</p>
<p>threshnew: Default similarity threshnew (0-1)</p>
<p>use_fallback: Whether to use fallback if ModernBERT unavailable</p>
</div>
</details>
</li>
<li><code>initialize</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Initialize the analyzer by loading confusable mappings.</span></li>
<li><code>_load_confusable_mappings</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Load confusable character mappings - optimized version.</span></li>
<li><code>create_and_initialize_unicode_analyzer</code> (unicode_analyzer.py)
<details><summary>Factory function to create and initialize a Unicode attack analyzer.</summary>
<div class="doc-comment">
<p>Factory function to create and initialize a Unicode attack analyzer.</p>
<p></p>
<p>Args:</p>
<p>config: Optional configuration for the analyzer</p>
<p></p>
<p>Returns:</p>
<p>Initialized UnicodeAttackAnalyzer instance or None if creation fails</p>
</div>
</details>
</li>
<li><code>_monitor</code> (sprint_lifecycle.py)</li>
<li><code>load_from_checkpoint</code> (sprint_lifecycle.py)
<details><summary>Restore lifecycle state from checkpoint payload.</summary>
<div class="doc-comment">
<p>Restore lifecycle state from checkpoint payload.</p>
<p>Sprint 1B will call this in CheckpointManager.load().</p>
</div>
</details>
</li>
<li><code>stats</code> (cache.py) — <span class="doc-comment-inline">Hit/miss/eviction stats for cache efficiency monitoring.</span></li>
<li><code>_generate_embedding</code> (deduplication.py) — <span class="doc-comment-inline">Generate embedding for content using dedup-specific task.</span></li>
<li><code>_compute_hash_similarity</code> (deduplication.py) — <span class="doc-comment-inline">Compute hash-based similarity.</span></li>
<li><code>_neighbor_buckets</code> (deduplication.py)
<details><summary>Generate all 16 neighboring bucket keys (all 1-bit flips in top-K space).</summary>
<div class="doc-comment">
<p>Generate all 16 neighboring bucket keys (all 1-bit flips in top-K space).</p>
<p></p>
<p>For Hamming distance &lt;= 3, a near-duplicate's top-K bits differ by at most</p>
<p>3 bit flips. We must check ALL possible 1-bit flips of the bucket key to</p>
<p>ensure ~95% recall (threshold=3, top_k=16).</p>
</div>
</details>
</li>
<li><code>_token_hash</code> (deduplication.py) — <span class="doc-comment-inline">64-bit hash of token (seeded), with cache for repeated tokens.</span></li>
<li><code>_determine_optimal_workers</code> (execution_optimizer.py) — <span class="doc-comment-inline">Determine optimal number of workers based on task type and system resources</span></li>
<li><code>_classify_tasks_by_resources</code> (execution_optimizer.py) — <span class="doc-comment-inline">Classify tasks by their resource requirements</span></li>
<li><code>_predict_task_times</code> (execution_optimizer.py) — <span class="doc-comment-inline">Predict execution times for tasks</span></li>
<li><code>_get_trace_root</code> (flow_trace.py) — <span class="doc-comment-inline">Get trace output directory with fallbacks.</span></li>
<li><code>trace_fetch_end</code> (flow_trace.py)</li>
<li><code>trace_source_family_counts</code> (flow_trace.py)</li>
<li><code>_maybe_eval_async</code> (_core.py) — <span class="doc-comment-inline">Throttled mx.eval([]) to prevent excessive GPU sync.</span></li>
<li><code>_maybe_eval_sync</code> (_core.py) — <span class="doc-comment-inline">Synchronous throttled mx.eval([]).</span></li>
<li><code>get_cache_stats</code> (mlx_cache.py) — <span class="doc-comment-inline">Get cache statistics including hit/miss metrics.</span></li>
<li><code>_load_sqlite</code> (filtering.py) — <span class="doc-comment-inline">Load frontier from SQLite.</span></li>
<li><code>async_init</code> (persistent_kv_cache.py) — <span class="doc-comment-inline">Async initialization — call once at startup.</span></li>
<li><code>stats</code> (persistent_kv_cache.py) — <span class="doc-comment-inline">Return cache statistics.</span></li>
<li><code>_detect_homoglyphs</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Detect homoglyph/confusable characters in text - optimized version.</span></li>
<li><code>encode_fast</code> (msgspec_json.py)
<details><summary>Zero-overhead encode using the module singleton encoder.</summary>
<div class="doc-comment">
<p>Zero-overhead encode using the module singleton encoder.</p>
<p></p>
<p>Use in single-threaded / single-task hot paths. No pool locking.</p>
</div>
</details>
</li>
<li><code>clear</code> (cache.py) — <span class="doc-comment-inline">Clear all entries. Thread-safe. Returns True.</span></li>
<li><code>__contains__</code> (cache.py) — <span class="doc-comment-inline">Check key exists and is not expired. O(1).</span></li>
<li><code>stats</code> (cache.py) — <span class="doc-comment-inline">Hit/miss/eviction/expiration stats.</span></li>
<li><code>_promote_generations</code> (cache.py) — <span class="doc-comment-inline">Promote oldest 25% of each generation to the next older generation.</span></li>
<li><code>__repr__</code> (cache.py)</li>
<li><code>_load_stats</code> (deduplication.py) — <span class="doc-comment-inline">Nacte statistiky z disku.</span></li>
<li><code>_load_config</code> (execution_optimizer.py) — <span class="doc-comment-inline">Load parallel execution configuration</span></li>
<li><code>detect_anomalies</code> (execution_optimizer.py) — <span class="doc-comment-inline">Detect anomalies in resource metrics.</span></li>
<li><code>_is_anomaly</code> (execution_optimizer.py) — <span class="doc-comment-inline">Check if latest value is anomalous using Z-score.</span></li>
<li><code>reset_metal_peak</code> (_core.py) — <span class="doc-comment-inline">Reset MLX peak memory counter.</span></li>
<li><code>get_cache_stats</code> (_core.py) — <span class="doc-comment-inline">Get model cache statistics including hit/miss metrics.</span></li>
<li><code>__init__</code> (batch_dns.py)</li>
<li><code>_ensure_async_primitives</code> (batch_dns.py)
<details><summary>Lazily allocate async primitives on first async use.</summary>
<div class="doc-comment">
<p>Lazily allocate async primitives on first async use.</p>
<p></p>
<p>Avoids binding the resolver to a specific event loop at</p>
<p>construction time. Lets the resolver be passed across loops</p>
<p>(rare in this codebase, but cheap to support).</p>
</div>
</details>
</li>
<li><code>get_batch_dns_resolver</code> (batch_dns.py)
<details><summary>Return the process-wide ``BatchDNSResolver`` singleton.</summary>
<div class="doc-comment">
<p>Return the process-wide ``BatchDNSResolver`` singleton.</p>
<p></p>
<p>Single resolver = single c-ares channel = bounded memory. Tests</p>
<p>that need a fresh instance should call ``reset_batch_dns_resolver()``</p>
<p>first (or instantiate ``BatchDNSResolver()`` directly).</p>
</div>
</details>
</li>
<li><code>_normalize_url</code> (filtering.py) — <span class="doc-comment-inline">Normalize URL for consistent deduplication.</span></li>
<li><code>_get_mlx</code> (intelligent_cache.py) — <span class="doc-comment-inline">Lazy import MLX core - returns None if MLX not available.</span></li>
<li><code>_persist</code> (intelligent_cache.py) — <span class="doc-comment-inline">Persist cache to disk.</span></li>
<li><code>_get_synonyms</code> (query_expansion.py) — <span class="doc-comment-inline">Get synonyms for a word</span></li>
<li><code>_generate_permutations</code> (query_expansion.py) — <span class="doc-comment-inline">Generate permutations of query terms</span></li>
<li><code>_detect_zero_width</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Detect zero-width characters in text - optimized version.</span></li>
<li><code>start</code> (uma_budget.py)
<details><summary>Start the watchdog in the current event loop.</summary>
<div class="doc-comment">
<p>Start the watchdog in the current event loop.</p>
<p></p>
<p>Returns the asyncio.Task so caller can track it.</p>
<p>Raises RuntimeError if already running.</p>
</div>
</details>
</li>
<li><code>decode_fast</code> (msgspec_json.py) — <span class="doc-comment-inline">Zero-overhead decode using the module singleton decoder.</span></li>
<li><code>clear</code> (cache.py) — <span class="doc-comment-inline">Clear all entries. Thread-safe. Returns True.</span></li>
<li><code>_refcount</code> (cache.py) — <span class="doc-comment-inline">Return sys.getrefcount for an entry in the given generation.</span></li>
<li><code>__repr__</code> (cache.py)</li>
<li><code>_get_refcount</code> (cache.py) — <span class="doc-comment-inline">Get refcount for an entry. Returns 0 if not found.</span></li>
<li><code>get_refcounts</code> (cache.py)
<details><summary>Get refcounts for all entries. For telemetry/debugging.</summary>
<div class="doc-comment">
<p>Get refcounts for all entries. For telemetry/debugging.</p>
<p></p>
<p>Returns {key: refcount} for all entries.</p>
</div>
</details>
</li>
<li><code>_get_content_signature</code> (deduplication.py) — <span class="doc-comment-inline">Generate content signature for an item.</span></li>
<li><code>_generate_signature</code> (deduplication.py) — <span class="doc-comment-inline">Generate complete content signature.</span></li>
<li><code>_extract_and_normalize_metadata</code> (deduplication.py) — <span class="doc-comment-inline">Extract and normalize metadata fields.</span></li>
<li><code>_record_execution_metrics</code> (execution_optimizer.py) — <span class="doc-comment-inline">Record execution metrics for group</span></li>
<li><code>predict_scaling_needs</code> (execution_optimizer.py) — <span class="doc-comment-inline">Predict scaling needs based on historical data.</span></li>
<li><code>apply_thermal_throttling</code> (execution_optimizer.py)
<details><summary>Apply thermal throttling state</summary>
<div class="doc-comment">
<p>Apply thermal throttling state</p>
<p></p>
<p>Args:</p>
<p>state: "normal", "elevated", "critical"</p>
</div>
</details>
</li>
<li><code>get_backend_info</code> (pattern_matcher.py) — <span class="doc-comment-inline">Return backend info — Rust ACO primary, linear scan fallback.</span></li>
<li><code>reset_pattern_matcher</code> (pattern_matcher.py)
<details><summary>Reset singleton to pristine state. FOR TEST USE ONLY.</summary>
<div class="doc-comment">
<p>Reset singleton to pristine state. FOR TEST USE ONLY.</p>
<p></p>
<p>Clears automaton, resets version, marks dirty.</p>
<p>After reset, get_pattern_matcher() returns the same state object</p>
<p>but in un-built (dirty) condition.</p>
</div>
</details>
</li>
<li><code>flush</code> (flow_trace.py) — <span class="doc-comment-inline">Flush trace buffers to disk.</span></li>
<li><code>trace_fetch_start</code> (flow_trace.py) — <span class="doc-comment-inline">Trace fetch start event.</span></li>
<li><code>trace_evidence_append</code> (flow_trace.py)</li>
<li><code>_clear_metal_cache_async</code> (_core.py) — <span class="doc-comment-inline">Async wrapper around safe_clear_metal_cache().</span></li>
<li><code>get_mlx_semaphore</code> (mlx_cache.py)
<details><summary>Get or create the shared semaphore for MLX inference.</summary>
<div class="doc-comment">
<p>Get or create the shared semaphore for MLX inference.</p>
<p></p>
<p>Limits concurrent MLX inference to 1 to prevent memory overflow on M1 8GB.</p>
</div>
</details>
</li>
<li><code>_normalize_url</code> (filtering.py) — <span class="doc-comment-inline">Normalize URL for consistent matching.</span></li>
<li><code>_check_cache</code> (filtering.py) — <span class="doc-comment-inline">Check cache for URL.</span></li>
<li><code>__init__</code> (filtering.py)</li>
<li><code>contains</code> (filtering.py) — <span class="doc-comment-inline">Check if URL is in frontier.</span></li>
<li><code>clear</code> (filtering.py) — <span class="doc-comment-inline">Clear all URLs from frontier.</span></li>
<li><code>__init__</code> (intelligent_cache.py)
<details><summary>Initialize memory-optimized URL set.</summary>
<div class="doc-comment">
<p>Initialize memory-optimized URL set.</p>
<p></p>
<p>Args:</p>
<p>max_memory_mb: Maximum memory to use in MB</p>
</div>
</details>
</li>
<li><code>clear</code> (persistent_kv_cache.py) — <span class="doc-comment-inline">Clear all cache entries.</span></li>
<li><code>generate_all_categories</code> (query_expansion.py)
<details><summary>Generate queries for all categories.</summary>
<div class="doc-comment">
<p>Generate queries for all categories.</p>
<p></p>
<p>Args:</p>
<p>topic: Search topic</p>
<p></p>
<p>Returns:</p>
<p>Dictionary mapping category to list of queries</p>
</div>
</details>
</li>
<li><code>add_custom_pattern</code> (query_expansion.py)
<details><summary>Add custom pattern to a category.</summary>
<div class="doc-comment">
<p>Add custom pattern to a category.</p>
<p></p>
<p>Args:</p>
<p>category: Category name (creates new if doesn't exist)</p>
<p>pattern: Pattern string with {domain} placeholder</p>
</div>
</details>
</li>
<li><code>_handler</code> (sprint_lifecycle.py)</li>
<li><code>cancel</code> (sprint_lifecycle.py) — <span class="doc-comment-inline">Cancel all internal background tasks.</span></li>
<li><code>get_uma_snapshot</code> (uma_budget.py)
<details><summary>Return a complete unified memory snapshot.</summary>
<div class="doc-comment">
<p>Return a complete unified memory snapshot.</p>
<p></p>
<p>Includes system RAM, MLX memory, thresholds, and pressure level.</p>
</div>
</details>
</li>
<li><code>_run</code> (async_helpers.py)</li>
<li><code>release</code> (async_helpers.py)
<details><summary>Release a per-host slot using the instance returned by ``acquire()``.</summary>
<div class="doc-comment">
<p>Release a per-host slot using the instance returned by ``acquire()``.</p>
<p></p>
<p>Safe against double-release (ValueError is swallowed).</p>
</div>
</details>
</li>
<li><code>__repr__</code> (cache.py)</li>
<li><code>_get_lock</code> (cache.py)
<details><summary>Lazy lock acquisition — creates Lock on first await inside an event loop.</summary>
<div class="doc-comment">
<p>Lazy lock acquisition — creates Lock on first await inside an event loop.</p>
<p></p>
<p>This is the CORRECT pattern for asyncio.Lock in async classes.</p>
<p>NEVER use self._lock = asyncio.Lock() at __init__ / module level.</p>
</div>
</details>
</li>
<li><code>__repr__</code> (cache.py)</li>
<li><code>__repr__</code> (cache.py)</li>
<li><code>_compute_cosine_similarity</code> (deduplication.py) — <span class="doc-comment-inline">Compute cosine similarity between two embeddings.</span></li>
<li><code>__init__</code> (deduplication.py)</li>
<li><code>save_stats</code> (deduplication.py) — <span class="doc-comment-inline">Ulozi statistiky na disk.</span></li>
<li><code>_pending_limit</code> (execution_optimizer.py)
<details><summary>Lazy semaphore for bounded pending ops.</summary>
<div class="doc-comment">
<p>Lazy semaphore for bounded pending ops.</p>
<p></p>
<p>F214OPT-D: Created on first access inside async context to avoid</p>
<p>creating asyncio primitives outside a running loop.</p>
</div>
</details>
</li>
<li><code>execution_predictor</code> (execution_optimizer.py) — <span class="doc-comment-inline">Lazy-loaded predictor to avoid eager sklearn import (1478 modules).</span></li>
<li><code>_execute_predictive</code> (execution_optimizer.py) — <span class="doc-comment-inline">Execute tasks with predictive optimization</span></li>
<li><code>_estimate_completion_time</code> (execution_optimizer.py) — <span class="doc-comment-inline">Estimate completion time for task group</span></li>
<li><code>cleanup</code> (execution_optimizer.py) — <span class="doc-comment-inline">Clean up resources</span></li>
<li><code>_predict_next_access</code> (execution_optimizer.py) — <span class="doc-comment-inline">Predict when key will be accessed next.</span></li>
<li><code>prewarm</code> (pattern_matcher.py)
<details><summary>Eagerly initialize the pattern matcher before first use.</summary>
<div class="doc-comment">
<p>Eagerly initialize the pattern matcher before first use.</p>
<p></p>
<p>Called during sprint initialization to ensure Rust AhoCorasickMatcher</p>
<p>is built before the first match_text() call.</p>
<p></p>
<p>No-op if registry is empty or if Rust ACO already available.</p>
</div>
</details>
</li>
<li><code>_ensure_file_open</code> (flow_trace.py) — <span class="doc-comment-inline">Lazily open trace files.</span></li>
<li><code>trace_queue_drop</code> (flow_trace.py) — <span class="doc-comment-inline">Trace queue drop event.</span></li>
<li><code>_merge_metadata</code> (flow_trace.py)</li>
<li><code>_init_quotient_filter</code> (filtering.py) — <span class="doc-comment-inline">Initialize quotient filter.</span></li>
<li><code>remove</code> (filtering.py) — <span class="doc-comment-inline">Remove URL from frontier.</span></li>
<li><code>_background_cleanup</code> (intelligent_cache.py) — <span class="doc-comment-inline">Background task for periodic cleanup.</span></li>
<li><code>_close</code> (persistent_kv_cache.py) — <span class="doc-comment-inline">Close LMDB environment.</span></li>
<li><code>_build_synonym_map</code> (query_expansion.py) — <span class="doc-comment-inline">Build combined synonym map based on domain context</span></li>
<li><code>_generate_plural</code> (query_expansion.py) — <span class="doc-comment-inline">Generate plural form of word</span></li>
<li><code>transition_to</code> (sprint_lifecycle.py)
<details><summary>Transition to a new state. Idempotent — same-state transition is a no-op.</summary>
<div class="doc-comment">
<p>Transition to a new state. Idempotent — same-state transition is a no-op.</p>
<p>Logs all transitions.</p>
</div>
</details>
</li>
<li><code>_on_task_done</code> (sprint_lifecycle.py) — <span class="doc-comment-inline">Done-callback: log exception if task failed, then remove from _bg_tasks.</span></li>
<li><code>is_uma_warn</code> (uma_budget.py)
<details><summary>Return True if UMA usage &gt;= warn threshold (6.0 GB).</summary>
<div class="doc-comment">
<p>Return True if UMA usage &gt;= warn threshold (6.0 GB).</p>
<p></p>
<p>Note: This returns True for warn, critical, AND emergency levels.</p>
<p>For exact level checking, use get_uma_pressure_level() directly.</p>
<p>Use is_uma_critical() or is_uma_emergency() for specific thresholds.</p>
</div>
</details>
</li>
<li><code>__init__</code> (cache.py)</li>
<li><code>_compute_weighted_similarity</code> (deduplication.py) — <span class="doc-comment-inline">Compute weighted similarity.</span></li>
<li><code>execute_worker_tasks</code> (execution_optimizer.py)</li>
<li><code>_adjust_workers_for_resources</code> (execution_optimizer.py) — <span class="doc-comment-inline">Adjust worker count based on available resources</span></li>
<li><code>get_stats</code> (execution_optimizer.py) — <span class="doc-comment-inline">Get cache statistics.</span></li>
<li><code>get_status</code> (pattern_matcher.py) — <span class="doc-comment-inline">Return current matcher status. O(1), side-effect free.</span></li>
<li><code>_get_trace_paths</code> (flow_trace.py) — <span class="doc-comment-inline">Get JSONL and summary paths.</span></li>
<li><code>trace_dedup_decision</code> (flow_trace.py) — <span class="doc-comment-inline">Trace URL dedup decision.</span></li>
<li><code>format_mlx_memory_snapshot</code> (_core.py) — <span class="doc-comment-inline">Get a complete MLX memory snapshot.</span></li>
<li><code>get_metal_limits_status</code> (_core.py) — <span class="doc-comment-inline">Diagnostic surface for metal limit configuration status.</span></li>
<li><code>sync_wrapper</code> (_core.py)</li>
<li><code>async_wrapper</code> (_core.py)</li>
<li><code>sync_wrapper</code> (_core.py)</li>
<li><code>async_wrapper</code> (_core.py)</li>
<li><code>_safe_json_parse</code> (hydration_extractor.py) — <span class="doc-comment-inline">Fail-soft JSON parse — never raises, returns None on error.</span></li>
<li><code>_detect_mlx_available</code> (mlx_cache.py) — <span class="doc-comment-inline">Return True only if mlx.core is importable (spec found, not None).</span></li>
<li><code>_update_cache</code> (filtering.py) — <span class="doc-comment-inline">Update cache with URL result.</span></li>
<li><code>__init__</code> (filtering.py)</li>
<li><code>__init__</code> (intelligent_cache.py)</li>
<li><code>clear</code> (intelligent_cache.py) — <span class="doc-comment-inline">Clear all cache entries.</span></li>
<li><code>_remove_entry</code> (intelligent_cache.py) — <span class="doc-comment-inline">Remove entry from all data structures.</span></li>
<li><code>_get_xxhash</code> (persistent_kv_cache.py) — <span class="doc-comment-inline">Lazy xxhash import.</span></li>
<li><code>_evict_lru</code> (persistent_kv_cache.py) — <span class="doc-comment-inline">Evict oldest LRU entries until within bounds. Returns count evicted.</span></li>
<li><code>_do_load</code> (persistent_kv_cache.py)</li>
<li><code>remaining_time</code> (sprint_lifecycle.py)
<details><summary>Estimated seconds remaining in sprint. Returns 0.0 if not started.</summary>
<div class="doc-comment">
<p>Estimated seconds remaining in sprint. Returns 0.0 if not started.</p>
<p>This is a read-only signal — never blocks.</p>
</div>
</details>
</li>
<li><code>_stop_uma_watchdog</code> (sprint_lifecycle.py) — <span class="doc-comment-inline">Stop UMA watchdog. Called when exiting ACTIVE state.</span></li>
<li><code>_detect_total_memory_mb</code> (uma_budget.py) — <span class="doc-comment-inline">Detect real system RAM. Floor 4 GB, ceil 64 GB, fallback 8 GB.</span></li>
<li><code>_get_local_pool</code> (msgspec_json.py)</li>
<li><code>_run</code> (async_helpers.py)</li>
<li><code>_cluster_by_simhash</code> (deduplication.py) — <span class="doc-comment-inline">Group items into LSH buckets using SimHash for near-linear deduplication.</span></li>
<li><code>__init__</code> (deduplication.py)</li>
<li><code>_cluster_by_simhash</code> (deduplication.py) — <span class="doc-comment-inline">Group items into LSH buckets using SimHash for near-linear deduplication.</span></li>
<li><code>_compute_hash</code> (deduplication.py) — <span class="doc-comment-inline">Compute exact content hash.</span></li>
<li><code>_compute_minhash_similarity</code> (deduplication.py) — <span class="doc-comment-inline">Compute MinHash Jaccard similarity.</span></li>
<li><code>_normalize_field_value</code> (deduplication.py) — <span class="doc-comment-inline">Normalize a metadata field value.</span></li>
<li><code>_normalize_text</code> (deduplication.py) — <span class="doc-comment-inline">Normalize text for comparison.</span></li>
<li><code>_normalize_text_sync</code> (deduplication.py) — <span class="doc-comment-inline">Synchronous text normalization.</span></li>
<li><code>get_stats</code> (deduplication.py) — <span class="doc-comment-inline">Vrati statistiky pro domenu (vytvori nove pokud neexistuji).</span></li>
<li><code>get_yield_penalty</code> (deduplication.py) — <span class="doc-comment-inline">Vrati yield-based penalty pro domenu (0-1, vyssi = vice penalizace).</span></li>
<li><code>_is_near_duplicate</code> (deduplication.py)
<details><summary>Check if fingerprint is near-duplicate of any seen fingerprint.</summary>
<div class="doc-comment">
<p>Check if fingerprint is near-duplicate of any seen fingerprint.</p>
<p></p>
<p>Uses TopKBucketIndex for O(1) average lookup instead of O(n) full scan.</p>
<p>Scans only neighboring buckets (same top-K bits ± 1 bit flip).</p>
<p>Threshold = 3 bits (~95% recall for 64-bit SimHash).</p>
</div>
</details>
</li>
<li><code>hamming_distance</code> (deduplication.py) — <span class="doc-comment-inline">Compute Hamming distance between two hashes.</span></li>
<li><code>stop_monitoring</code> (execution_optimizer.py) — <span class="doc-comment-inline">Stop the background memory monitor.</span></li>
<li><code>_prune_parallel_groups</code> (execution_optimizer.py) — <span class="doc-comment-inline">Prune oldest and expired parallel groups.</span></li>
<li><code>_execute_resource_aware</code> (execution_optimizer.py) — <span class="doc-comment-inline">Execute tasks with resource awareness</span></li>
<li><code>_run_batch</code> (execution_optimizer.py)</li>
<li><code>_distribute_tasks_load_balanced</code> (execution_optimizer.py) — <span class="doc-comment-inline">Distribute tasks among workers based on current loads</span></li>
<li><code>__init__</code> (execution_optimizer.py)</li>
<li><code>_fallback_to_generic_topology</code> (execution_optimizer.py) — <span class="doc-comment-inline">Fallback to generic CPU topology detection</span></li>
<li><code>__init__</code> (execution_optimizer.py)</li>
<li><code>_should_sample</code> (flow_trace.py) — <span class="doc-comment-inline">Determine if this event should be sampled.</span></li>
<li><code>get_mx</code> (_core.py)
<details><summary>Lazy accessor for mlx.core module — never holds a module-level reference.</summary>
<div class="doc-comment">
<p>Lazy accessor for mlx.core module — never holds a module-level reference.</p>
<p>Returns the mlx.core module object if available, otherwise None.</p>
</div>
</details>
</li>
<li><code>clear_mlx_cache_debounced</code> (_core.py) — <span class="doc-comment-inline">Clear MLX cache with debounce to prevent rapid repeated clears.</span></li>
<li><code>set_cache_limit_with_debounce</code> (_core.py) — <span class="doc-comment-inline">Set MLX cache limit with debounce protection.</span></li>
<li><code>get_semaphore</code> (_core.py) — <span class="doc-comment-inline">Get the shared MLX inference semaphore (max 1 concurrent inference).</span></li>
<li><code>_has_metadata_signal</code> (hydration_extractor.py) — <span class="doc-comment-inline">Check if info has canonical/feed/alternate links.</span></li>
<li><code>_evict_neg_cache_oldest</code> (batch_dns.py) — <span class="doc-comment-inline">Evict oldest 25% of negative cache to maintain bounded size.</span></li>
<li><code>async_wrapper</code> (mlx_cache.py)</li>
<li><code>sync_wrapper</code> (mlx_cache.py)</li>
<li><code>_init_filter</code> (filtering.py) — <span class="doc-comment-inline">Initialize pyxorfilter.</span></li>
<li><code>contains</code> (filtering.py) — <span class="doc-comment-inline">Check if item is in filter.</span></li>
<li><code>_load_default_blocklists</code> (filtering.py) — <span class="doc-comment-inline">Load default blocked domains and patterns.</span></li>
<li><code>_cleanup_expired</code> (intelligent_cache.py) — <span class="doc-comment-inline">Remove expired entries.</span></li>
<li><code>__init__</code> (semantic.py) — <span class="doc-comment-inline">Initialize SimpleEmbedding.</span></li>
<li><code>_detect_domain</code> (query_expansion.py) — <span class="doc-comment-inline">Detect domain from query terms.</span></li>
<li><code>__init__</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Initialize the Unicode attack analyzer.</span></li>
<li><code>cleanup</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Clean up resources and free memory.</span></li>
<li><code>_set_winner</code> (async_helpers.py) — <span class="doc-comment-inline">Set winner if not already set. Returns True if THIS call set the winner.</span></li>
<li><code>get_stats</code> (async_helpers.py) — <span class="doc-comment-inline">Return telemetry snapshot.</span></li>
<li><code>size</code> (cache.py) — <span class="doc-comment-inline">Current number of entries (including potentially expired).</span></li>
<li><code>__init__</code> (cache.py)</li>
<li><code>contains</code> (cache.py) — <span class="doc-comment-inline">Check key exists. Thread-safe. O(1).</span></li>
<li><code>size</code> (cache.py) — <span class="doc-comment-inline">Current number of entries.</span></li>
<li><code>size</code> (cache.py) — <span class="doc-comment-inline">Total entries across all generations (approximate — WVD may differ).</span></li>
<li><code>_scan_refcounts</code> (cache.py) — <span class="doc-comment-inline">Scan all generations and return {key: (refcount, gen)} for all entries.</span></li>
<li><code>size</code> (cache.py) — <span class="doc-comment-inline">Total entries across all generations.</span></li>
<li><code>cleanup</code> (deduplication.py) — <span class="doc-comment-inline">Cleanup resources.</span></li>
<li><code>_generate_ngrams</code> (deduplication.py) — <span class="doc-comment-inline">Generate n-grams from content.</span></li>
<li><code>__init__</code> (deduplication.py)</li>
<li><code>_generic_similarity</code> (deduplication.py) — <span class="doc-comment-inline">Compute generic similarity.</span></li>
<li><code>_process_batch</code> (deduplication.py) — <span class="doc-comment-inline">Process a batch of items.</span></li>
<li><code>__init__</code> (deduplication.py)</li>
<li><code>get_summary</code> (deduplication.py) — <span class="doc-comment-inline">Get summary stats for all domains.</span></li>
<li><code>__init__</code> (execution_optimizer.py)</li>
<li><code>add_parallel_group</code> (execution_optimizer.py) — <span class="doc-comment-inline">Add a parallel group with bounded storage and TTL.</span></li>
<li><code>_calculate_resource_allocation</code> (execution_optimizer.py) — <span class="doc-comment-inline">Calculate optimal resource allocation for task group</span></li>
<li><code>get_performance_statistics</code> (execution_optimizer.py) — <span class="doc-comment-inline">Get performance statistics</span></li>
<li><code>_detect_mlx_available</code> (_core.py) — <span class="doc-comment-inline">Return True only if mlx.core is importable (spec found, not None).</span></li>
<li><code>_release_slab_pool</code> (_core.py) — <span class="doc-comment-inline">Called by mlx_cleanup_sync to release slab pool memory.</span></li>
<li><code>_has_meaningful_body</code> (hydration_extractor.py) — <span class="doc-comment-inline">Check if info dict has meaningful body/description &gt;= MIN_BODY_LEN.</span></li>
<li><code>reset_batch_dns_resolver</code> (batch_dns.py)
<details><summary>Drop the singleton (for tests + teardown). The next</summary>
<div class="doc-comment">
<p>Drop the singleton (for tests + teardown). The next</p>
<p>``get_batch_dns_resolver()`` call instantiates a fresh resolver.</p>
</div>
</details>
</li>
<li><code>_get_mx</code> (mlx_cache.py) — <span class="doc-comment-inline">Lazily import mlx.core on first use.</span></li>
<li><code>add_pattern</code> (filtering.py) — <span class="doc-comment-inline">Add blocked URL pattern (regex).</span></li>
<li><code>_get_domain</code> (filtering.py) — <span class="doc-comment-inline">Extract domain from URL.</span></li>
<li><code>add_blocked_domain</code> (filtering.py) — <span class="doc-comment-inline">Add domain to blocklist.</span></li>
<li><code>add_blocked_url</code> (filtering.py) — <span class="doc-comment-inline">Add URL to blocklist.</span></li>
<li><code>add</code> (filtering.py) — <span class="doc-comment-inline">Add URL to frontier.</span></li>
<li><code>_warm_cache</code> (intelligent_cache.py) — <span class="doc-comment-inline">Warm cache with keys using async loader (Fix 4).</span></li>
<li><code>get_global_cache</code> (intelligent_cache.py) — <span class="doc-comment-inline">Get global cache instance.</span></li>
<li><code>_do_save</code> (persistent_kv_cache.py)</li>
<li><code>unload</code> (semantic.py) — <span class="doc-comment-inline">Unload model from memory.</span></li>
<li><code>_get_context</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Extract context around a position in text.</span></li>
<li><code>checkpoint_seam_ready</code> (sprint_lifecycle.py)
<details><summary>True when checkpoint save/load is safe to call.</summary>
<div class="doc-comment">
<p>True when checkpoint save/load is safe to call.</p>
<p>Always True in this implementation — checkpoint.py exists.</p>
<p>Wiring to CheckpointManager is Sprint 1B scope.</p>
</div>
</details>
</li>
<li><code>_get_mlx_core</code> (uma_budget.py) — <span class="doc-comment-inline">Lazy MLX import for memory metrics.</span></li>
<li><code>format_uma_budget_report</code> (uma_budget.py) — <span class="doc-comment-inline">Format a human-readable UMA budget report.</span></li>
<li><code>__init__</code> (uma_budget.py)</li>
<li><code>_should_fire</code> (uma_budget.py) — <span class="doc-comment-inline">Return True if level should trigger a callback (debounce-aware).</span></li>
<li><code>_run</code> (async_helpers.py)</li>
<li><code>size</code> (cache.py) — <span class="doc-comment-inline">Current number of entries (including potentially expired).</span></li>
<li><code>_get_max_ngrams</code> (deduplication.py) — <span class="doc-comment-inline">Get ngram cap from environment with safe fallback.</span></li>
<li><code>cleanup</code> (deduplication.py) — <span class="doc-comment-inline">Cleanup resources.</span></li>
<li><code>close</code> (deduplication.py) — <span class="doc-comment-inline">F196B: Non-blocking close of all thread pools.</span></li>
<li><code>__init__</code> (deduplication.py)</li>
<li><code>_hamming</code> (deduplication.py) — <span class="doc-comment-inline">Hamming distance — uses Rust hamming_dist if available.</span></li>
<li><code>_tokenize</code> (deduplication.py) — <span class="doc-comment-inline">Tokenization - shingle by 3 words.</span></li>
<li><code>hamming_distance</code> (deduplication.py) — <span class="doc-comment-inline">Compute Hamming distance between two SimHash fingerprints. O(1).</span></li>
<li><code>update_worker_metrics</code> (execution_optimizer.py) — <span class="doc-comment-inline">Update worker metrics with bounded storage.</span></li>
<li><code>_init_execution_pools</code> (execution_optimizer.py) — <span class="doc-comment-inline">Initialize execution pools</span></li>
<li><code>execute_chunk</code> (execution_optimizer.py)</li>
<li><code>get_bounded_ops_telemetry</code> (execution_optimizer.py)
<details><summary>Return telemetry for bounded pending ops.</summary>
<div class="doc-comment">
<p>Return telemetry for bounded pending ops.</p>
<p></p>
<p>F214OPT-D: Exposes pending ops limits and throttling metrics.</p>
</div>
</details>
</li>
<li><code>export_performance_report</code> (execution_optimizer.py) — <span class="doc-comment-inline">Export detailed performance report</span></li>
<li><code>_are_p_cores_overloaded</code> (execution_optimizer.py) — <span class="doc-comment-inline">Check if P-cores are overloaded based on recent allocations</span></li>
<li><code>_calculate_p_core_ratio</code> (execution_optimizer.py) — <span class="doc-comment-inline">Calculate ratio of P-core to total allocations</span></li>
<li><code>clear</code> (execution_optimizer.py) — <span class="doc-comment-inline">Clear all cache entries.</span></li>
<li><code>__init__</code> (pattern_matcher.py)</li>
<li><code>get_pattern_matcher</code> (pattern_matcher.py)
<details><summary>Return the singleton PatternMatcher state.</summary>
<div class="doc-comment">
<p>Return the singleton PatternMatcher state.</p>
<p></p>
<p>Does NOT trigger a build — build is deferred to first match_text() call.</p>
</div>
</details>
</li>
<li><code>get_default_bootstrap_patterns</code> (pattern_matcher.py)
<details><summary>Return the current default bootstrap patterns tuple.</summary>
<div class="doc-comment">
<p>Return the current default bootstrap patterns tuple.</p>
<p></p>
<p>Side-effect free. No matcher state is consulted or modified.</p>
</div>
</details>
</li>
<li><code>benchmark_build</code> (pattern_matcher.py) — <span class="doc-comment-inline">Measure automaton build time for a given registry.</span></li>
<li><code>evict_all</code> (_core.py) — <span class="doc-comment-inline">Synchronous eviction of entire MLX model cache (safe from any thread).</span></li>
<li><code>__init__</code> (batch_dns.py)</li>
<li><code>_get_cache_lock</code> (mlx_cache.py) — <span class="doc-comment-inline">Get or create the cache lock (lazy initialization).</span></li>
<li><code>evict_all</code> (mlx_cache.py) — <span class="doc-comment-inline">Synchronní vyčištění celé cache (bezpečné z jakéhokoli vlákna).</span></li>
<li><code>__init__</code> (filtering.py)</li>
<li><code>contains</code> (filtering.py) — <span class="doc-comment-inline">Check if URL is in frontier.</span></li>
<li><code>add_batch</code> (filtering.py) — <span class="doc-comment-inline">Add multiple URLs to frontier.</span></li>
<li><code>get_fast_filter</code> (filtering.py) — <span class="doc-comment-inline">Get global FastFilter instance.</span></li>
<li><code>get_frontier</code> (filtering.py) — <span class="doc-comment-inline">Get global EfficientFrontier instance.</span></li>
<li><code>_hash_prompt</code> (persistent_kv_cache.py) — <span class="doc-comment-inline">Generate 16-char hash of prompt for cache key.</span></li>
<li><code>_read_lmdb</code> (persistent_kv_cache.py)</li>
<li><code>has</code> (persistent_kv_cache.py) — <span class="doc-comment-inline">Check if cache entry exists (synchronous, for hot path).</span></li>
<li><code>_build_synonym_map</code> (query_expansion.py) — <span class="doc-comment-inline">Build combined synonym map.</span></li>
<li><code>is_windup_phase</code> (sprint_lifecycle.py)
<details><summary>Sprint 8PC: True when remaining_time &lt; 180 seconds.</summary>
<div class="doc-comment">
<p>Sprint 8PC: True when remaining_time &lt; 180 seconds.</p>
<p>Used by concurrency matrix to apply windup multiplier.</p>
</div>
</details>
</li>
<li><code>get_checkpoint_seam</code> (sprint_lifecycle.py)
<details><summary>Return a minimal checkpoint payload for this layer.</summary>
<div class="doc-comment">
<p>Return a minimal checkpoint payload for this layer.</p>
<p>Sprint 1B will wire this into CheckpointManager.save().</p>
</div>
</details>
</li>
<li><code>_swap_pct</code> (uma_budget.py) — <span class="doc-comment-inline">Helper: vrátí swap usage %, fail-open 0.0.</span></li>
<li><code>stop</code> (uma_budget.py) — <span class="doc-comment-inline">Stop the watchdog gracefully.</span></li>
<li><code>_get_thread_encoder</code> (msgspec_json.py) — <span class="doc-comment-inline">Get an encoder for the current thread, preferring a pooled instance.</span></li>
<li><code>_get_thread_decoder</code> (msgspec_json.py) — <span class="doc-comment-inline">Get a decoder for the current thread, preferring a pooled instance.</span></li>
<li><code>__init__</code> (async_helpers.py)</li>
<li><code>_wvd_delete</code> (cache.py) — <span class="doc-comment-inline">Remove key from secondary WVD if active.</span></li>
<li><code>_wvd_set</code> (cache.py) — <span class="doc-comment-inline">Add value to secondary WVD if active.</span></li>
<li><code>_is_orphaned</code> (cache.py) — <span class="doc-comment-inline">Return True if entry's refcount suggests it's only held by the cache.</span></li>
<li><code>_is_orphaned</code> (cache.py) — <span class="doc-comment-inline">Return True if entry's refcount suggests it's only held by cache.</span></li>
<li><code>deduplication_rate</code> (deduplication.py) — <span class="doc-comment-inline">Calculate deduplication rate.</span></li>
<li><code>_can_cache_embedding</code> (deduplication.py) — <span class="doc-comment-inline">Check if we can cache embedding within memory limits.</span></li>
<li><code>cleanup</code> (deduplication.py) — <span class="doc-comment-inline">Cleanup resources.</span></li>
<li><code>cleanup</code> (deduplication.py) — <span class="doc-comment-inline">Cleanup resources.</span></li>
<li><code>_get_lock</code> (deduplication.py) — <span class="doc-comment-inline">Lazy create asyncio.Lock for async context.</span></li>
<li><code>_get_lock</code> (execution_optimizer.py) — <span class="doc-comment-inline">ISSUE-014 FIX: Lazily create lock in the current event loop.</span></li>
<li><code>_optimize_execution_order</code> (execution_optimizer.py) — <span class="doc-comment-inline">Optimize task execution order based on predictions</span></li>
<li><code>__init__</code> (execution_optimizer.py)</li>
<li><code>_get_mlx_safe</code> (_core.py) — <span class="doc-comment-inline">Safe lazy accessor for mlx.core (fallback None).</span></li>
<li><code>_clear_metal_cache_sync</code> (_core.py) — <span class="doc-comment-inline">Sync wrapper around safe_clear_metal_cache().</span></li>
<li><code>_has_content_json_ld</code> (hydration_extractor.py) — <span class="doc-comment-inline">Check if info has JSON-LD type from CONTENT_TYPES.</span></li>
<li><code>clear_mlx_cache</code> (mlx_cache.py) — <span class="doc-comment-inline">Clear the MLX model cache.</span></li>
<li><code>reset_cache_stats</code> (mlx_cache.py) — <span class="doc-comment-inline">Reset cache hit/miss statistics.</span></li>
<li><code>_format_limit_mib</code> (mlx_cache.py) — <span class="doc-comment-inline">Format a memory limit in MiB for safe logging.</span></li>
<li><code>block_rate</code> (filtering.py) — <span class="doc-comment-inline">Calculate block rate.</span></li>
<li><code>add</code> (filtering.py) — <span class="doc-comment-inline">Add URL to frontier.</span></li>
<li><code>remove</code> (filtering.py) — <span class="doc-comment-inline">Remove URL from frontier.</span></li>
<li><code>clear</code> (filtering.py) — <span class="doc-comment-inline">Clear all URLs from frontier.</span></li>
<li><code>get_stats</code> (intelligent_cache.py) — <span class="doc-comment-inline">Get cache statistics.</span></li>
<li><code>_update_hit_rate</code> (intelligent_cache.py) — <span class="doc-comment-inline">Update hit rate statistic.</span></li>
<li><code>get_instance</code> (persistent_kv_cache.py) — <span class="doc-comment-inline">Get or create the singleton PersistentKVCache instance.</span></li>
<li><code>reset_instance</code> (persistent_kv_cache.py) — <span class="doc-comment-inline">Reset singleton (for testing).</span></li>
<li><code>_update_lru</code> (persistent_kv_cache.py) — <span class="doc-comment-inline">Update LRU order on access.</span></li>
<li><code>async_init_persistent_kv_cache</code> (persistent_kv_cache.py) — <span class="doc-comment-inline">Initialize the global PersistentKVCache (call at startup).</span></li>
<li><code>_load_model</code> (semantic.py) — <span class="doc-comment-inline">Load ModernBERT embedder.</span></li>
<li><code>unload</code> (semantic.py) — <span class="doc-comment-inline">Unload embedding model from memory.</span></li>
<li><code>_release_thread_encoder</code> (msgspec_json.py) — <span class="doc-comment-inline">Return an encoder to the per-thread pool (bounded).</span></li>
<li><code>_release_thread_decoder</code> (msgspec_json.py) — <span class="doc-comment-inline">Return a decoder to the per-thread pool (bounded).</span></li>
<li><code>dumps_str</code> (msgspec_json.py) — <span class="doc-comment-inline">Encode to JSON string (not bytes) with optional formatting.</span></li>
<li><code>content_hash</code> (deduplication.py) — <span class="doc-comment-inline">Generate content hash.</span></li>
<li><code>_compute_character_hash</code> (deduplication.py) — <span class="doc-comment-inline">Compute character-level hash.</span></li>
<li><code>avg_latency_ms</code> (deduplication.py)</li>
<li><code>_prune_worker_metrics</code> (execution_optimizer.py) — <span class="doc-comment-inline">Prune oldest worker metrics if over cap.</span></li>
<li><code>_init_predictor</code> (execution_optimizer.py) — <span class="doc-comment-inline">Initialize execution time predictor - lazy import to avoid eager sklearn load.</span></li>
<li><code>set_run_id</code> (flow_trace.py) — <span class="doc-comment-inline">Set the current run ID for trace correlation.</span></li>
<li><code>_format_limit_mib</code> (_core.py)</li>
<li><code>_get_cache_lock</code> (_core.py) — <span class="doc-comment-inline">Get or create the cache async lock.</span></li>
<li><code>_truncate</code> (hydration_extractor.py)</li>
<li><code>_has_meaningful_title</code> (hydration_extractor.py) — <span class="doc-comment-inline">Check if info dict has a meaningful title &gt;= MIN_TITLE_LEN.</span></li>
<li><code>_meta_val</code> (hydration_extractor.py)</li>
<li><code>clear_cache</code> (batch_dns.py) — <span class="doc-comment-inline">Drop all cached entries. Safe to call from sync context.</span></li>
<li><code>__init__</code> (filtering.py)</li>
<li><code>add_blocked_pattern</code> (filtering.py) — <span class="doc-comment-inline">Add regex pattern to blocklist.</span></li>
<li><code>reset_stats</code> (filtering.py) — <span class="doc-comment-inline">Reset statistics.</span></li>
<li><code>_get_storage_file</code> (filtering.py) — <span class="doc-comment-inline">Get path to storage file.</span></li>
<li><code>add</code> (filtering.py) — <span class="doc-comment-inline">Add normalized URL to frontier.</span></li>
<li><code>contains</code> (filtering.py) — <span class="doc-comment-inline">Check if normalized URL is in frontier.</span></li>
<li><code>remove</code> (filtering.py) — <span class="doc-comment-inline">Remove normalized URL from frontier.</span></li>
<li><code>_get_size</code> (intelligent_cache.py) — <span class="doc-comment-inline">Get size of entry from cache.</span></li>
<li><code>clear</code> (intelligent_cache.py) — <span class="doc-comment-inline">Clear all URLs and reset memory usage.</span></li>
<li><code>__init__</code> (semantic.py) — <span class="doc-comment-inline">Initialize LightweightTokenizer.</span></li>
<li><code>__init__</code> (query_expansion.py)</li>
<li><code>__init__</code> (query_expansion.py)</li>
<li><code>get_instance</code> (sprint_lifecycle.py)</li>
<li><code>on_emergency</code> (sprint_lifecycle.py)</li>
<li><code>track_task</code> (sprint_lifecycle.py) — <span class="doc-comment-inline">Add task to internal registry with done-callback that logs exceptions.</span></li>
<li><code>is_uma_critical</code> (uma_budget.py) — <span class="doc-comment-inline">Return True if UMA usage &gt;= 6.5 GB.</span></li>
<li><code>is_uma_emergency</code> (uma_budget.py) — <span class="doc-comment-inline">Return True if UMA usage &gt;= 7.0 GB.</span></li>
<li><code>monotonic_ms</code> (async_helpers.py) — <span class="doc-comment-inline">Return current monotonic time in milliseconds (float).</span></li>
<li><code>_wrap</code> (async_helpers.py)</li>
<li><code>_with_timeout</code> (async_helpers.py)</li>
<li><code>keys</code> (cache.py) — <span class="doc-comment-inline">Return list of keys, excluding expired. Thread-safe.</span></li>
<li><code>values</code> (cache.py) — <span class="doc-comment-inline">Return list of values, excluding expired. Thread-safe.</span></li>
<li><code>capacity</code> (cache.py) — <span class="doc-comment-inline">Maximum number of entries (maxsize).</span></li>
<li><code>capacity</code> (cache.py) — <span class="doc-comment-inline">Maximum number of entries (maxsize).</span></li>
<li><code>capacity</code> (cache.py) — <span class="doc-comment-inline">Maximum number of entries (maxsize).</span></li>
<li><code>_gens</code> (cache.py) — <span class="doc-comment-inline">Return generations in eviction order (oldest first).</span></li>
<li><code>__init__</code> (deduplication.py)</li>
<li><code>find_duplicates</code> (deduplication.py) — <span class="doc-comment-inline">Find duplicates for an item among candidates.</span></li>
<li><code>cleanup</code> (deduplication.py) — <span class="doc-comment-inline">Cleanup resources.</span></li>
<li><code>close</code> (deduplication.py) — <span class="doc-comment-inline">F196B: Non-blocking close of thread pool.</span></li>
<li><code>close</code> (deduplication.py) — <span class="doc-comment-inline">F196B: Non-blocking close of thread pool.</span></li>
<li><code>_get_stop_words</code> (deduplication.py) — <span class="doc-comment-inline">Get common stop words.</span></li>
<li><code>close</code> (deduplication.py) — <span class="doc-comment-inline">F196B: Non-blocking close of thread pool.</span></li>
<li><code>get_statistics</code> (deduplication.py) — <span class="doc-comment-inline">Get current statistics.</span></li>
<li><code>to_dict</code> (deduplication.py) — <span class="doc-comment-inline">Serialize to dict for persistence.</span></li>
<li><code>_bucket_key</code> (deduplication.py) — <span class="doc-comment-inline">Top-K bits as bucket key.</span></li>
<li><code>clear</code> (deduplication.py)</li>
<li><code>is_near_duplicate</code> (deduplication.py) — <span class="doc-comment-inline">Check if two hashes are near-duplicates (Hamming &lt;= threshold).</span></li>
<li><code>start_monitoring</code> (execution_optimizer.py) — <span class="doc-comment-inline">Start the background memory monitor.</span></li>
<li><code>acquire</code> (execution_optimizer.py) — <span class="doc-comment-inline">Acquire a concurrency slot. Blocks if limit reached.</span></li>
<li><code>release</code> (execution_optimizer.py) — <span class="doc-comment-inline">Release a concurrency slot.</span></li>
<li><code>initialize</code> (execution_optimizer.py) — <span class="doc-comment-inline">Initialize async components like concurrency controller.</span></li>
<li><code>get_worker_loads</code> (execution_optimizer.py) — <span class="doc-comment-inline">Get current worker loads</span></li>
<li><code>update_worker_load</code> (execution_optimizer.py) — <span class="doc-comment-inline">Update worker load</span></li>
<li><code>get_current_resources</code> (execution_optimizer.py) — <span class="doc-comment-inline">Get current system resources</span></li>
<li><code>get_core_statistics</code> (execution_optimizer.py) — <span class="doc-comment-inline">Get core allocation statistics</span></li>
<li><code>create_m1_resource_allocator</code> (execution_optimizer.py) — <span class="doc-comment-inline">Factory function to create M1-optimized resource allocator</span></li>
<li><code>example_task</code> (execution_optimizer.py)</li>
<li><code>get_active_count</code> (execution_optimizer.py) — <span class="doc-comment-inline">Get number of active tasks.</span></li>
<li><code>get_pattern_pack_metadata</code> (pattern_matcher.py) — <span class="doc-comment-inline">Return metadata for a pattern, or None if not found.</span></li>
<li><code>pattern_count</code> (pattern_matcher.py) — <span class="doc-comment-inline">Return number of configured patterns. O(1).</span></li>
<li><code>is_enabled</code> (flow_trace.py) — <span class="doc-comment-inline">Check if tracing is enabled.</span></li>
<li><code>_flush_atexit</code> (flow_trace.py) — <span class="doc-comment-inline">Ensure trace flush on interpreter exit.</span></li>
<li><code>_ensure_mlx</code> (_core.py) — <span class="doc-comment-inline">Ensure MLX core is available.</span></li>
<li><code>_has_metal_api</code> (_core.py)</li>
<li><code>safe_clear_metal_cache</code> (_core.py) — <span class="doc-comment-inline">Alias for clear_mlx_cache() for backward compatibility.</span></li>
<li><code>get_semaphore_for_testing</code> (_core.py) — <span class="doc-comment-inline">Test hook for semaphore creation.</span></li>
<li><code>cache_size</code> (batch_dns.py) — <span class="doc-comment-inline">Return current LRU cache size (for tests + telemetry).</span></li>
<li><code>neg_cache_size</code> (batch_dns.py) — <span class="doc-comment-inline">Return current negative cache size (for tests + telemetry).</span></li>
<li><code>stats</code> (batch_dns.py) — <span class="doc-comment-inline">Return bounded telemetry snapshot.</span></li>
<li><code>reset_stats</code> (batch_dns.py) — <span class="doc-comment-inline">Reset telemetry counters (does not clear the cache).</span></li>
<li><code>is_empty</code> (batch_dns.py) — <span class="doc-comment-inline">Return True if cache is empty.</span></li>
<li><code>_is_disabled</code> (batch_dns.py) — <span class="doc-comment-inline">Return True if the env-var opt-out is set.</span></li>
<li><code>add_domain</code> (filtering.py) — <span class="doc-comment-inline">Add blocked domain.</span></li>
<li><code>add_url</code> (filtering.py) — <span class="doc-comment-inline">Add blocked URL.</span></li>
<li><code>size</code> (filtering.py) — <span class="doc-comment-inline">Get filter size.</span></li>
<li><code>add</code> (filtering.py) — <span class="doc-comment-inline">Add item to filter.</span></li>
<li><code>is_available</code> (filtering.py) — <span class="doc-comment-inline">Check if filter is available.</span></li>
<li><code>check_urls_batch</code> (filtering.py) — <span class="doc-comment-inline">Check multiple URLs.</span></li>
<li><code>get_stats</code> (filtering.py) — <span class="doc-comment-inline">Get filter statistics.</span></li>
<li><code>is_bff_available</code> (filtering.py) — <span class="doc-comment-inline">Check if Binary Fuse Filter is available.</span></li>
<li><code>_init_fallback</code> (filtering.py) — <span class="doc-comment-inline">Initialize fallback using set.</span></li>
<li><code>get_stats</code> (filtering.py) — <span class="doc-comment-inline">Get frontier statistics.</span></li>
<li><code>get_size</code> (filtering.py) — <span class="doc-comment-inline">Get current number of URLs in frontier.</span></li>
<li><code>get_stats</code> (filtering.py) — <span class="doc-comment-inline">Get frontier statistics.</span></li>
<li><code>get_size</code> (filtering.py) — <span class="doc-comment-inline">Get current number of URLs in frontier.</span></li>
<li><code>iter_urls</code> (filtering.py) — <span class="doc-comment-inline">Iterate over all URLs in frontier.</span></li>
<li><code>get_all_urls</code> (filtering.py) — <span class="doc-comment-inline">Get all URLs in frontier.</span></li>
<li><code>__init__</code> (filtering.py)</li>
<li><code>check_batch</code> (filtering.py) — <span class="doc-comment-inline">Check multiple URLs against frontier.</span></li>
<li><code>_estimate_size</code> (intelligent_cache.py) — <span class="doc-comment-inline">Estimate size of value in bytes using sys.getsizeof (Fix 4).</span></li>
<li><code>__contains__</code> (intelligent_cache.py) — <span class="doc-comment-inline">Check if URL is in set.</span></li>
<li><code>__len__</code> (intelligent_cache.py) — <span class="doc-comment-inline">Get number of URLs in set.</span></li>
<li><code>__iter__</code> (intelligent_cache.py) — <span class="doc-comment-inline">Iterate over URLs.</span></li>
<li><code>get_memory_usage_mb</code> (intelligent_cache.py) — <span class="doc-comment-inline">Get current memory usage in MB.</span></li>
<li><code>get_statistics</code> (intelligent_cache.py) — <span class="doc-comment-inline">Get URL set statistics.</span></li>
<li><code>encode</code> (persistent_kv_cache.py)</li>
<li><code>decode</code> (persistent_kv_cache.py)</li>
<li><code>_write_lmdb</code> (persistent_kv_cache.py)</li>
<li><code>_update_lmdb</code> (persistent_kv_cache.py)</li>
<li><code>close</code> (persistent_kv_cache.py) — <span class="doc-comment-inline">Close the cache manager.</span></li>
<li><code>get_persistent_kv_cache</code> (persistent_kv_cache.py) — <span class="doc-comment-inline">Get the singleton PersistentKVCache instance.</span></li>
<li><code>__init__</code> (semantic.py) — <span class="doc-comment-inline">Initialize KeywordFilter.</span></li>
<li><code>_tokenize</code> (query_expansion.py) — <span class="doc-comment-inline">Tokenize query into words</span></li>
<li><code>get_statistics</code> (query_expansion.py) — <span class="doc-comment-inline">Get expander statistics</span></li>
<li><code>expand</code> (query_expansion.py) — <span class="doc-comment-inline">Expand query into multiple variations.</span></li>
<li><code>strategy_type</code> (query_expansion.py) — <span class="doc-comment-inline">Get strategy type identifier.</span></li>
<li><code>__init__</code> (query_expansion.py)</li>
<li><code>__init__</code> (query_expansion.py)</li>
<li><code>has_findings</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Check if any findings were detected.</span></li>
<li><code>get_finding_count</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Get total number of findings.</span></li>
<li><code>get_summary</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Get summary of analysis results.</span></li>
<li><code>__enter__</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Context manager entry.</span></li>
<li><code>_get_sprint_duration_seconds</code> (sprint_lifecycle.py) — <span class="doc-comment-inline">Read sprint duration from env, default 30 min.</span></li>
<li><code>_get_windup_lead_seconds</code> (sprint_lifecycle.py) — <span class="doc-comment-inline">Read T-3min wind-down lead time from env, default 180 s.</span></li>
<li><code>is_active</code> (sprint_lifecycle.py) — <span class="doc-comment-inline">True when in ACTIVE state (normal operations).</span></li>
<li><code>is_winding_down</code> (sprint_lifecycle.py) — <span class="doc-comment-inline">True when in WINDUP, EXPORT, or TEARDOWN states.</span></li>
<li><code>shutdown_requested</code> (sprint_lifecycle.py) — <span class="doc-comment-inline">True when SIGINT/SIGTERM has been received.</span></li>
<li><code>windup_fired</code> (sprint_lifecycle.py) — <span class="doc-comment-inline">True when wind-down has been triggered (always True once fired).</span></li>
<li><code>on_critical</code> (sprint_lifecycle.py)</li>
<li><code>set_windup_hook</code> (sprint_lifecycle.py) — <span class="doc-comment-inline">Set callback to run when wind-down is triggered.</span></li>
<li><code>set_export_hook</code> (sprint_lifecycle.py) — <span class="doc-comment-inline">Set callback to run when export phase begins.</span></li>
<li><code>set_teardown_hook</code> (sprint_lifecycle.py) — <span class="doc-comment-inline">Set callback to run when teardown is triggered.</span></li>
<li><code>is_running</code> (uma_budget.py) — <span class="doc-comment-inline">True if the watchdog loop is active.</span></li>
<li><code>interval</code> (uma_budget.py) — <span class="doc-comment-inline">Return the polling interval in seconds.</span></li>
<li><code>last_fired_level</code> (uma_budget.py) — <span class="doc-comment-inline">Return the last level that triggered a callback.</span></li>
<li><code>json_dumps</code> (msgspec_json.py) — <span class="doc-comment-inline">Alias for :func:`encode` (legacy naming).</span></li>
<li><code>json_loads</code> (msgspec_json.py) — <span class="doc-comment-inline">Alias for :func:`decode` (legacy naming).</span></li>
<li><code>RayonPoolsAvailable</code> (rayon_pool.py) — <span class="doc-comment-inline">Return True if Rust rayon pools are available.</span></li>
<li><code>_lift</code> (async_helpers.py)</li>
<li><code>_runner</code> (async_helpers.py)</li>
<li><code>__init__</code> (async_helpers.py)</li>
<li><code>__call__</code> (async_helpers.py)</li>
<li><code>__setitem__</code> (cache.py)</li>
<li><code>__len__</code> (cache.py)</li>
<li><code>__len__</code> (cache.py)</li>
<li><code>__len__</code> (cache.py)</li>
<li><code>__len__</code> (cache.py)</li>
<li><code>__len__</code> (cache.py)</li>
<li><code>_get_storage_path</code> (deduplication.py)</li>
<li><code>get_all_stats</code> (deduplication.py)</li>
<li><code>__len__</code> (deduplication.py)</li>
<li><code>__init__</code> (execution_optimizer.py)</li>
<li><code>__init__</code> (execution_optimizer.py)</li>
<li><code>__repr__</code> (pattern_matcher.py)</li>
<li><code>__init__</code> (semantic.py)</li>
<li><code>__init__</code> (semantic.py)</li>
<li><code>strategy_type</code> (query_expansion.py)</li>
<li><code>__init__</code> (query_expansion.py)</li>
<li><code>strategy_type</code> (query_expansion.py)</li>
<li><code>strategy_type</code> (query_expansion.py)</li>
<li><code>__init__</code> (query_expansion.py)</li>
<li><code>state</code> (sprint_lifecycle.py)</li>
<li><code>sprint_duration</code> (sprint_lifecycle.py)</li>
<li><code>on_warn</code> (sprint_lifecycle.py)</li>
<li><code>on_warn</code> (uma_budget.py) — <span class="doc-comment-inline">Called when UMA enters WARN state (&gt;= 6.0 GB).</span></li>
<li><code>on_critical</code> (uma_budget.py) — <span class="doc-comment-inline">Called when UMA enters CRITICAL state (&gt;= 6.5 GB).</span></li>
<li><code>on_emergency</code> (uma_budget.py) — <span class="doc-comment-inline">Called when UMA enters EMERGENCY state (&gt;= 7.0 GB).</span></li>
<li><code>_noop_current_otel_context</code> (async_helpers.py)</li>
</ul>
</details>

<details><summary><strong>Class</strong> (105)</summary>
<ul>
<li><code>ParallelExecutionOptimizer</code> (execution_optimizer.py) — <span class="doc-comment-inline">Advanced parallel execution optimization system</span></li>
<li><code>PersistentKVCache</code> (persistent_kv_cache.py)
<details><summary>Persistent KV cache s LMDB metadata index a safetensors storage.</summary>
<div class="doc-comment">
<p>Persistent KV cache s LMDB metadata index a safetensors storage.</p>
<p></p>
<p>Features:</p>
<p>- LMDB-backed metadata index (fast lookups, crash-safe)</p>
<p>- Safetensors storage (efficient, mlx-native)</p>
<p>- LRU eviction s bounded disk usage</p>
<p>- Async save/load (non-blocking disk I/O)</p>
<p>- Cross-sprint shared cache (singleton)</p>
<p>- Fail-safe: graceful degradation on any error</p>
</div>
</details>
</li>
<li><code>BatchDNSResolver</code> (batch_dns.py)
<details><summary>Bounded LRU + negative cache + optional aiodns backend.</summary>
<div class="doc-comment">
<p>Bounded LRU + negative cache + optional aiodns backend.</p>
<p></p>
<p>Single-process singleton: ``get_batch_dns_resolver()``. Reuse the</p>
<p>same c-ares channel across all fetch batches. Fail-soft on every</p>
<p>error path — partial results are returned, never raise.</p>
</div>
</details>
</li>
<li><code>SprintLifecycleManager</code> (sprint_lifecycle.py)
<details><summary>Manages sprint lifecycle state machine with fail-open design.</summary>
<div class="doc-comment">
<p>Manages sprint lifecycle state machine with fail-open design.</p>
<p></p>
<p>State transitions:</p>
<p>BOOT → WARMUP → ACTIVE → WINDUP → EXPORT → TEARDOWN</p>
<p></p>
<p>The manager:</p>
<p>- Tracks sprint start time and duration</p>
<p>- Fires wind-down hook T-3min before sprint end</p>
<p>- Provides remaining_time read-only signal</p>
<p>- Registers SIGINT/SIGTERM handlers pointing to unified shutdown</p>
<p>- All methods are async-safe and fail-open</p>
</div>
</details>
</li>
<li><code>UnicodeAttackAnalyzer</code> (unicode_analyzer.py)
<details><summary>High-speed Unicode attack surface analyzer.</summary>
<div class="doc-comment">
<p>High-speed Unicode attack surface analyzer.</p>
<p></p>
<p>Detects various Unicode-based attacks including zero-width characters,</p>
<p>homoglyph substitution, bidirectional text attacks, and normalization anomalies.</p>
<p>Optimized for 100+ MB/s processing speed.</p>
</div>
</details>
</li>
<li><code>RefcountEvictionCache</code> (cache.py)
<details><summary>Embedder-session cache with sys.getrefcount-based eviction.</summary>
<div class="doc-comment">
<p>Embedder-session cache with sys.getrefcount-based eviction.</p>
<p></p>
<p>Primary eviction signal: refcount ≤ baseline (orphaned entries).</p>
<p>Secondary signal: generational age (gen0 → gen1 → gen2).</p>
<p></p>
<p>Thread-safe via threading.RLock. Fail-safe: any error returns safely.</p>
<p></p>
<p>Invariants:</p>
<p>- maxsize enforced on write</p>
<p>- orphaned entries evicted before LRU when refcount_check=True</p>
<p>- generational promotion every N set() calls</p>
<p>- fail-safe: never raises, returns safely on error</p>
</div>
</details>
</li>
<li><code>GenerationalCache</code> (cache.py)
<details><summary>3-Generation WeakRef cache with age-based eviction.</summary>
<div class="doc-comment">
<p>3-Generation WeakRef cache with age-based eviction.</p>
<p></p>
<p>Eviction order: gen2 (oldest) → gen1 → gen0 (youngest).</p>
<p>Each generation is a WeakValueDictionary so values are auto-GC'd</p>
<p>by Python's cyclic garbage collector when memory pressure rises.</p>
<p></p>
<p>Invariants:</p>
<p>- maxsize enforced per generation</p>
<p>- weak references: values GC'd by Python when unreferenced elsewhere</p>
<p>- age-based eviction: oldest generation evicted first</p>
<p>- optional refcount threshold: force-evict entries with refcount≤baseline</p>
<p>- fail-safe: any error returns None/False, never raises</p>
</div>
</details>
</li>
<li><code>IntelligentCache</code> (intelligent_cache.py)
<details><summary>ML-enhanced intelligent cache with ARC eviction.</summary>
<div class="doc-comment">
<p>ML-enhanced intelligent cache with ARC eviction.</p>
<p></p>
<p>Features:</p>
<p>- ARC (Adaptive Replacement Cache) for O(1) eviction</p>
<p>- Automatic memory management for M1 8GB</p>
<p>- Async operations for non-blocking access</p>
<p>- Optional persistence to disk</p>
<p>- sys.getsizeof for size estimation</p>
<p></p>
<p>Example:</p>
<p>cache = IntelligentCache(CacheConfig(max_size_bytes=50*1024*1024))</p>
<p>await cache.initialize()</p>
<p></p>
<p>await cache.set("key", value, ttl=300)</p>
<p>result = await cache.get("key")</p>
</div>
</details>
</li>
<li><code>PyCacheDict</code> (cache.py)
<details><summary>Bounded OrderedDict cache with per-entry TTL.</summary>
<div class="doc-comment">
<p>Bounded OrderedDict cache with per-entry TTL.</p>
<p></p>
<p>Eviction: O(1) LRU via move_to_end() + popitem(last=False).</p>
<p></p>
<p>Invariants:</p>
<p>- maxsize enforced on write: oldest evicted when full</p>
<p>- ttl enforced on read: expired entries return None (lazy purge)</p>
<p>- thread-safe: threading.Lock protects _data</p>
<p>- fail-safe: any error returns None / False, never raises</p>
</div>
</details>
</li>
<li><code>SemanticDeduplicator</code> (deduplication.py) — <span class="doc-comment-inline">Semantic deduplication using vector embeddings.</span></li>
<li><code>AsyncPyCacheDict</code> (cache.py)
<details><summary>Async-safe bounded OrderedDict cache with per-entry TTL.</summary>
<div class="doc-comment">
<p>Async-safe bounded OrderedDict cache with per-entry TTL.</p>
<p></p>
<p>Eviction: O(1) LRU via move_to_end() + popitem(last=False).</p>
<p>Lock: lazy asyncio.Lock() — NEVER instantiate at module import time.</p>
<p></p>
<p>Invariants:</p>
<p>- maxsize enforced on write: oldest evicted when full</p>
<p>- ttl enforced on read: expired entries return None (lazy purge)</p>
<p>- async-safe: asyncio.Lock protects _data mutations</p>
<p>- fail-safe: any error returns None / False / empty, never raises</p>
<p></p>
<p>Python 3.14 note: pass weak_values=True to use</p>
<p>WeakValueDictionary backing for auto-GC of values (numpy arrays,</p>
<p>embeddings) when they are only held by the cache. The WVD is a</p>
<p>secondary GC reference; the primary (key, (value, ts)) entries</p>
<p>always live in the OrderedDict.</p>
</div>
</details>
</li>
<li><code>QueryExpander</code> (query_expansion.py)
<details><summary>Generate intelligent search query variations.</summary>
<div class="doc-comment">
<p>Generate intelligent search query variations.</p>
<p></p>
<p>Example:</p>
<p>&gt;&gt;&gt; expander = QueryExpander()</p>
<p>&gt;&gt;&gt; variations = expander.expand("machine learning healthcare")</p>
<p>&gt;&gt;&gt; print(variations)</p>
<p>['machine learning healthcare', 'ml healthcare', 'machine learning medicine', ...]</p>
</div>
</details>
</li>
<li><code>ContentDeduplicator</code> (deduplication.py) — <span class="doc-comment-inline">Content-based deduplication using hashing and MinHash.</span></li>
<li><code>SemanticFilter</code> (semantic.py)
<details><summary>Semantic filter for content relevance checking.</summary>
<div class="doc-comment">
<p>Semantic filter for content relevance checking.</p>
<p></p>
<p>Uses ModernBERT for fast, memory-efficient similarity computation.</p>
<p>Filters content before it reaches DeepSeek to save tokens.</p>
<p></p>
<p>Usage context:</p>
<p>- Placed BEFORE Context Manager</p>
<p>- Web data does NOT go to DeepSeek until passing this filter</p>
<p>- Saves tokens by filtering irrelevant content early</p>
<p></p>
<p>Example:</p>
<p>filter = SemanticFilter()</p>
<p>result = filter.filter(</p>
<p>content="Python is a great programming language",</p>
<p>query="best programming languages",</p>
<p>threshnew=0.7</p>
<p>)</p>
<p>if result.passed:</p>
<p># Send to DeepSeek</p>
</div>
</details>
</li>
<li><code>FastFilter</code> (filtering.py)
<details><summary>Memory-efficient URL filtering using Binary Fuse Filter.</summary>
<div class="doc-comment">
<p>Memory-efficient URL filtering using Binary Fuse Filter.</p>
<p>Optimized for M1 Silicon (8GB RAM).</p>
<p>Falls back to Python set if pyxorfilter unavailable.</p>
</div>
</details>
</li>
<li><code>BoundedLoRACache</code> (cache.py)
<details><summary>Bounded LRU cache for MLX LoRA adapter models.</summary>
<div class="doc-comment">
<p>Bounded LRU cache for MLX LoRA adapter models.</p>
<p></p>
<p>Enforces maxsize with O(1) LRU eviction (move_to_end + popitem).</p>
<p>Thread-safe. Fail-safe: any error returns None/False.</p>
<p></p>
<p>Invariants:</p>
<p>- maxsize enforced on every put(): oldest entry evicted if at capacity</p>
<p>- get() refreshes LRU order (move_to_end)</p>
<p>- clear() removes all entries</p>
<p>- Memory: maxsize × ~200 MB ≈ 400 MB absolute ceiling on M1 8GB</p>
</div>
</details>
</li>
<li><code>IntelligentResourceAllocator</code> (execution_optimizer.py)
<details><summary>Intelligent Resource Allocator - M1-Optimized Resource Management</summary>
<div class="doc-comment">
<p>Intelligent Resource Allocator - M1-Optimized Resource Management</p>
<p></p>
<p>Dynamically allocates tasks to Performance (P) or Efficiency (E) cores</p>
<p>based on workload characteristics and system state.</p>
<p></p>
<p>M1-Specific Features:</p>
<p>- P-core detection: hw.perflevel0.logicalcpu (cores 1-3 on M1 Air)</p>
<p>- E-core detection: hw.perflevel1.logicalcpu (core 0 on M1 Air)</p>
<p>- Dynamic workload balancing between core types</p>
<p>- Thermal-aware throttling</p>
</div>
</details>
</li>
<li><code>PersistentFrontier</code> (filtering.py)
<details><summary>Persistent URL frontier with disk storage.</summary>
<div class="doc-comment">
<p>Persistent URL frontier with disk storage.</p>
<p>Supports multiple storage backends (JSON, Pickle, SQLite).</p>
</div>
</details>
</li>
<li><code>MetadataDeduplicator</code> (deduplication.py) — <span class="doc-comment-inline">Metadata-based deduplication using field comparison.</span></li>
<li><code>DeduplicationEngine</code> (deduplication.py) — <span class="doc-comment-inline">Main deduplication engine with multi-strategy support.</span></li>
<li><code>SimHash</code> (deduplication.py) — <span class="doc-comment-inline">SimHash (64-bit) s persistetním seedem a thread-safe token cache - M1 8GB optimized.</span></li>
<li><code>UmaWatchdog</code> (uma_budget.py)
<details><summary>Async UMA memory watchdog with state-change debounce.</summary>
<div class="doc-comment">
<p>Async UMA memory watchdog with state-change debounce.</p>
<p></p>
<p>Polls get_uma_pressure_level() every `interval` seconds (default 0.5s).</p>
<p>Fires callbacks only on state *changes* (not every poll).</p>
<p>All callbacks run inside the watchdog's own async loop — never block the caller.</p>
<p></p>
<p>Invariants:</p>
<p>- Default polling interval = 0.5s (not 5s)</p>
<p>- Fail-open: if get_uma_pressure_level() throws, treats as "normal"</p>
<p>- Debounce: same level re-trigger only after DEBOUNCE_SECONDS have passed</p>
<p>- Non-blocking: asyncio.sleep is used, never time.sleep</p>
</div>
</details>
</li>
<li><code>PredictiveCacheManager</code> (execution_optimizer.py)
<details><summary>Advanced caching with predictive eviction.</summary>
<div class="doc-comment">
<p>Advanced caching with predictive eviction.</p>
<p></p>
<p>Uses access pattern analysis to predict future accesses</p>
<p>and evict items that won't be needed soon.</p>
</div>
</details>
</li>
<li><code>MemoryOptimizedURLSet</code> (intelligent_cache.py)
<details><summary>Memory-efficient URL set with configurable memory limit.</summary>
<div class="doc-comment">
<p>Memory-efficient URL set with configurable memory limit.</p>
<p></p>
<p>Optimized for M1 8GB - tracks memory usage and enforces limits.</p>
<p>Used for tracking discovered URLs during deep web scanning</p>
<p>without consuming excessive memory.</p>
<p></p>
<p>Example:</p>
<p>&gt;&gt;&gt; url_set = MemoryOptimizedURLSet(max_memory_mb=50)</p>
<p>&gt;&gt;&gt; url_set.add("https://example.com/page1")</p>
<p>&gt;&gt;&gt; url_set.add("https://example.com/page2")</p>
<p>&gt;&gt;&gt; print(len(url_set))</p>
<p>2</p>
<p>&gt;&gt;&gt; print("https://example.com/page1" in url_set)</p>
<p>True</p>
</div>
</details>
</li>
<li><code>SimpleEmbedding</code> (semantic.py)
<details><summary>Simple word embedding using TF-IDF style weighting.</summary>
<div class="doc-comment">
<p>Simple word embedding using TF-IDF style weighting.</p>
<p></p>
<p>Fallback when ModernBERT is not available.</p>
<p>Memory-efficient for M1 Silicon.</p>
<p></p>
<p>DEPRECATED: Use ModernBERTEmbedding instead.</p>
</div>
</details>
</li>
<li><code>QuotientFilterFrontier</code> (filtering.py)
<details><summary>URL frontier using PyProbables Quotient Filter.</summary>
<div class="doc-comment">
<p>URL frontier using PyProbables Quotient Filter.</p>
<p></p>
<p>Quotient Filter advantages:</p>
<p>- Constant-time lookup</p>
<p>- Minimal false positive rate</p>
<p>- Lower memory usage than Bloom Filter</p>
<p>- Supports deletion operations</p>
</div>
</details>
</li>
<li><code>ModernBERTEmbedding</code> (semantic.py)
<details><summary>ModernBERT-based embedding for semantic filtering.</summary>
<div class="doc-comment">
<p>ModernBERT-based embedding for semantic filtering.</p>
<p></p>
<p>Uses ModernBERT via MLX for 768-dimensional embeddings.</p>
<p>Optimized for M1 Silicon (8GB RAM).</p>
<p></p>
<p>REPLACES: Model2VecEmbedding, SentenceTransformerEmbedding</p>
</div>
</details>
</li>
<li><code>DomainStats</code> (deduplication.py) — <span class="doc-comment-inline">Per-domain statistiky pro yield tracking a domain diversity - M1 8GB.</span></li>
<li><code>BoundedPerHostGate</code> (async_helpers.py)
<details><summary>Bounded per-host concurrency gate with LRU eviction.</summary>
<div class="doc-comment">
<p>Bounded per-host concurrency gate with LRU eviction.</p>
<p></p>
<p>Prevents unbounded growth of per-host Semaphore objects in</p>
<p>FetchCoordinator when crawling high-diversity URL sets.</p>
<p></p>
<p>Invariants:</p>
<p>- max_hosts cap bounds RAM usage (~512 hosts × ~250 B ≈ 128 KB)</p>
<p>- LRU eviction keeps hot hosts resident</p>
<p>- Telemetry: evicted / hits / misses counters</p>
</div>
</details>
</li>
<li><code>DorkingEngine</code> (query_expansion.py)
<details><summary>Advanced dorking engine for generating complex search queries.</summary>
<div class="doc-comment">
<p>Advanced dorking engine for generating complex search queries.</p>
<p></p>
<p>Generates sophisticated search queries (Google dorks) for discovering</p>
<p>hidden content, academic papers, technical documents, and more.</p>
<p></p>
<p>Categories:</p>
<p>- academic: Research papers, publications, studies</p>
<p>- technical: Specifications, documentation, manuals</p>
<p>- financial: Reports, annual statements, investor docs</p>
<p>- government: Classified docs, FOIA releases, archives</p>
<p></p>
<p>Example:</p>
<p>&gt;&gt;&gt; engine = DorkingEngine()</p>
<p>&gt;&gt;&gt; queries = engine.generate_complex_queries('ai research', 'academic')</p>
<p>&gt;&gt;&gt; print(queries[:3])</p>
<p>['site:ai.edu filetype:pdf "research"', 'site:ai.gov filetype:pdf "study"', ...]</p>
</div>
</details>
</li>
<li><code>_ARC</code> (intelligent_cache.py)
<details><summary>Adaptive Replacement Cache (ARC) - O(1) eviction policy.</summary>
<div class="doc-comment">
<p>Adaptive Replacement Cache (ARC) - O(1) eviction policy.</p>
<p></p>
<p>Maintains four lists:</p>
<p>- T1: Recently used pages (recency)</p>
<p>- T2: Frequently used pages (both recency and frequency)</p>
<p>- B1: Ghosts of recently evicted T1 pages</p>
<p>- B2: Ghosts of recently evicted T2 pages</p>
<p></p>
<p>Uses OrderedDict for O(1) operations on list boundaries.</p>
</div>
</details>
</li>
<li><code>TopKBucketIndex</code> (deduplication.py)
<details><summary>Top-K bit bucketing for O(1) near-duplicate SimHash lookup.</summary>
<div class="doc-comment">
<p>Top-K bit bucketing for O(1) near-duplicate SimHash lookup.</p>
<p></p>
<p>Partitions 64-bit fingerprints into 2^top_k_buckets buckets by their top-K bits.</p>
<p>Near-duplicates (Hamming distance &lt;= threshold) share the same top-K bits with</p>
<p>high probability, so we only scan neighboring buckets instead of all N fingerprints.</p>
<p></p>
<p>Performance:</p>
<p>- Add: O(1) average — hash to bucket, scan 3-4 neighboring buckets</p>
<p>- Memory: O(N) fingerprints stored + O(2^top_k_buckets) bucket overhead</p>
<p>- Recall: ~95% for threshold=3, top_k=16 (same as full scan)</p>
<p></p>
<p>Args:</p>
<p>hashbits: Number of bits in fingerprint (default 64)</p>
<p>top_k_bits: Number of leading bits for bucket key (default 16 → 65536 buckets)</p>
<p>threshold: Max Hamming distance to consider near-duplicate (default 3)</p>
</div>
</details>
</li>
<li><code>DomainStatsManager</code> (deduplication.py) — <span class="doc-comment-inline">Spravuje DomainStats s persistenci na disk - M1 8GB.</span></li>
<li><code>DefaultUmaWatchdogCallbacks</code> (uma_budget.py)
<details><summary>Default auto-action callbacks for memory pressure responses.</summary>
<div class="doc-comment">
<p>Default auto-action callbacks for memory pressure responses.</p>
<p></p>
<p>P2-12: Built-in auto-actions when memory pressure is detected.</p>
<p>F265H-EXT: on_warn now triggers GC on normal→warn transition (not just logging).</p>
<p></p>
<p>Actions:</p>
<p>- WARN: Trigger lightweight GC (gc.collect + mx.eval + clear_cache)</p>
<p>- CRITICAL: Trigger MLX cache cleanup + log</p>
<p>- EMERGENCY: Trigger aggressive MLX cleanup + log + alert</p>
</div>
</details>
</li>
<li><code>_ConcurrencyController</code> (execution_optimizer.py)
<details><summary>Dynamic concurrency controller based on system memory.</summary>
<div class="doc-comment">
<p>Dynamic concurrency controller based on system memory.</p>
<p></p>
<p>Limits concurrent CPU-bound tasks based on available memory.</p>
<p>Uses background monitor to adjust limit dynamically.</p>
</div>
</details>
</li>
<li><code>BinaryFuseFilter</code> (filtering.py)
<details><summary>Binary Fuse Filter wrapper using pyxorfilter.</summary>
<div class="doc-comment">
<p>Binary Fuse Filter wrapper using pyxorfilter.</p>
<p>Memory-efficient probabilistic filter with 0% false negatives.</p>
</div>
</details>
</li>
<li><code>SemanticExpansionStrategy</code> (query_expansion.py)
<details><summary>Semantic query expansion using synonyms and related terms.</summary>
<div class="doc-comment">
<p>Semantic query expansion using synonyms and related terms.</p>
<p>From MSQES - optimized for academic research.</p>
</div>
</details>
</li>
<li><code>KeywordFilter</code> (semantic.py)
<details><summary>Simple keyword-based filter for fast pre-filtering.</summary>
<div class="doc-comment">
<p>Simple keyword-based filter for fast pre-filtering.</p>
<p></p>
<p>Used as a first-pass filter before semantic filtering</p>
<p>to save computational resources.</p>
</div>
</details>
</li>
<li><code>SimpleSetFilter</code> (filtering.py)
<details><summary>Python set-based filter as fallback.</summary>
<div class="doc-comment">
<p>Python set-based filter as fallback.</p>
<p>Simple but memory-intensive for large datasets.</p>
</div>
</details>
</li>
<li><code>EfficientFrontier</code> (filtering.py)
<details><summary>High-level frontier interface with smart deduplication.</summary>
<div class="doc-comment">
<p>High-level frontier interface with smart deduplication.</p>
<p>Combines quotient filter efficiency with intelligent URL normalization.</p>
</div>
</details>
</li>
<li><code>DomainSpecificExpansionStrategy</code> (query_expansion.py) — <span class="doc-comment-inline">Domain-specific query expansion using field knowledge.</span></li>
<li><code>LightweightTokenizer</code> (semantic.py)
<details><summary>Lightweight tokenizer for fast text processing.</summary>
<div class="doc-comment">
<p>Lightweight tokenizer for fast text processing.</p>
<p></p>
<p>Uses simple whitespace and punctuation tokenization</p>
<p>for M1 Silicon memory efficiency.</p>
</div>
</details>
</li>
<li><code>BatchDNSStats</code> (batch_dns.py) — <span class="doc-comment-inline">Bounded telemetry counter snapshot for BatchDNSResolver.</span></li>
<li><code>_AiodnsResolver</code> (batch_dns.py)
<details><summary>Optional c-ares backed DNS resolver using aiodns.</summary>
<div class="doc-comment">
<p>Optional c-ares backed DNS resolver using aiodns.</p>
<p></p>
<p>Provides connection pooling and multiplexing for faster parallel DNS queries.</p>
<p>Falls back gracefully when aiodns is unavailable.</p>
</div>
</details>
</li>
<li><code>SyntacticExpansionStrategy</code> (query_expansion.py)
<details><summary>Syntactic query expansion - generates different phrasings</summary>
<div class="doc-comment">
<p>Syntactic query expansion - generates different phrasings</p>
<p>without changing semantic meaning.</p>
</div>
</details>
</li>
<li><code>AnomalyDetector</code> (execution_optimizer.py)
<details><summary>Anomaly detection for resource monitoring.</summary>
<div class="doc-comment">
<p>Anomaly detection for resource monitoring.</p>
<p></p>
<p>Detects resource usage spikes using statistical analysis</p>
<p>(Z-score based detection with configurable thresholds).</p>
</div>
</details>
</li>
<li><code>PredictiveScaler</code> (execution_optimizer.py)
<details><summary>Predictive scaling based on workload patterns.</summary>
<div class="doc-comment">
<p>Predictive scaling based on workload patterns.</p>
<p></p>
<p>Analyzes resource usage trends to predict scaling needs</p>
<p>and provide recommendations for workload optimization.</p>
</div>
</details>
</li>
<li><code>MultiStrategyExpander</code> (query_expansion.py)
<details><summary>Combines multiple expansion strategies for comprehensive query expansion.</summary>
<div class="doc-comment">
<p>Combines multiple expansion strategies for comprehensive query expansion.</p>
<p>From MSQES - Multi-Source Query Expansion System.</p>
</div>
</details>
</li>
<li><code>MemoryAwareScheduler</code> (execution_optimizer.py)
<details><summary>Task scheduler that respects memory constraints.</summary>
<div class="doc-comment">
<p>Task scheduler that respects memory constraints.</p>
<p>Prevents OOM by controlling concurrent task execution.</p>
</div>
</details>
</li>
<li><code>HydrationExtractionResult</code> (hydration_extractor.py)
<details><summary>Result of static hydration extraction from HTML.</summary>
<div class="doc-comment">
<p>Result of static hydration extraction from HTML.</p>
<p></p>
<p>Attributes</p>
<p>----------</p>
<p>found : bool</p>
<p>True if any hydration data was located in the HTML.</p>
<p>sufficient : bool</p>
<p>True if the found data is rich enough to skip JS rendering.</p>
<p>sources : tuple[str, ...]</p>
<p>Which extraction sources produced content (e.g. "next_data", "nuxt_data").</p>
<p>text : str</p>
<p>Extracted meaningful text content (title + body/description).</p>
<p>metadata : dict[str, object]</p>
<p>Structured metadata: title, description, canonical, og:*, JSON-LD types,</p>
<p>extracted links (canonical, RSS, Atom).</p>
<p>reason : str | None</p>
<p>Telemetry reason string for logging/analytics.</p>
</div>
</details>
</li>
<li><code>_PatternMatcherState</code> (pattern_matcher.py) — <span class="doc-comment-inline">Holds the singleton PatternMatcher instance and its lifecycle state.</span></li>
<li><code>UnicodeAnalysisResult</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Complete result of Unicode attack analysis.</span></li>
<li><code>DeduplicationConfig</code> (deduplication.py) — <span class="doc-comment-inline">Configuration for deduplication engine.</span></li>
<li><code>_BatchCapture</code> (async_helpers.py)
<details><summary>Closure-free result capture for a single batch.</summary>
<div class="doc-comment">
<p>Closure-free result capture for a single batch.</p>
<p></p>
<p>ISSUE-008: Replaces nested _run_with_capture + nonlocal closure cell.</p>
<p>Using __slots__ eliminates per-task closure allocation and reduces GC pressure</p>
<p>in tight loops (batch_size=20 × thousands of batches = significant savings).</p>
<p></p>
<p>__slots__ ensures:</p>
<p>- No __dict__ per instance (~48 bytes saved/instance)</p>
<p>- No closure cell objects for nonlocal variables</p>
<p>- Faster attribute access on M1 (direct offset indexing)</p>
</div>
</details>
</li>
<li><code>CacheEntry</code> (persistent_kv_cache.py) — <span class="doc-comment-inline">Metadata entry for one cached KV cache.</span></li>
<li><code>PatternHit</code> (pattern_matcher.py)
<details><summary>Single pattern match result.</summary>
<div class="doc-comment">
<p>Single pattern match result.</p>
<p></p>
<p>Invariants:</p>
<p>- pattern, label are sys.intern()'d (dedup + fast compare)</p>
<p>- value is a direct substring slice from input text (NOT interned)</p>
<p>- start/end are byte offsets matching value extraction</p>
</div>
</details>
</li>
<li><code>BaseDeduplicator</code> (deduplication.py) — <span class="doc-comment-inline">Abstract base class for deduplicators.</span></li>
<li><code>QueryItem</code> (deduplication.py) — <span class="doc-comment-inline">Item for deduplication processing.</span></li>
<li><code>DeduplicationResult</code> (deduplication.py) — <span class="doc-comment-inline">Result of deduplication process.</span></li>
<li><code>LoadBalancer</code> (execution_optimizer.py) — <span class="doc-comment-inline">Load balancer for task distribution</span></li>
<li><code>UmaWatchdogCallbacks</code> (uma_budget.py)
<details><summary>Callback interface for UmaWatchdog reactions.</summary>
<div class="doc-comment">
<p>Callback interface for UmaWatchdog reactions.</p>
<p>All methods are optional — unactioned callbacks are no-ops.</p>
</div>
</details>
</li>
<li><code>FilterStats</code> (filtering.py) — <span class="doc-comment-inline">Statistics for fast filter.</span></li>
<li><code>ExpansionStrategy</code> (query_expansion.py) — <span class="doc-comment-inline">Abstract base class for query expansion strategies (from MSQES).</span></li>
<li><code>_LifecycleWatchdogCallbacks</code> (sprint_lifecycle.py)</li>
<li><code>ParallelResult</code> (async_helpers.py)
<details><summary>Canonical result of ``parallel()`` with policy-driven error routing.</summary>
<div class="doc-comment">
<p>Canonical result of ``parallel()`` with policy-driven error routing.</p>
<p></p>
<p>Attributes:</p>
<p>ok:        Successful results, in original order.</p>
<p>errors:    Exception instances (only populated when policy="collect").</p>
<p>re_raised: BaseException re-raised per I6/I7 (CancelledError, etc.).</p>
</div>
</details>
</li>
<li><code>SafeGatherResult</code> (async_helpers.py)
<details><summary>Result of `safe_gather` — msgspec.Struct for ~3× faster instantiation.</summary>
<div class="doc-comment">
<p>Result of `safe_gather` — msgspec.Struct for ~3× faster instantiation.</p>
<p></p>
<p>Attributes:</p>
<p>ok:       List of successful results (order preserved)</p>
<p>errors:   List of exception instances (excluding BaseException)</p>
<p>re_raised:BaseException instance if one was re-raised (caller should handle)</p>
</div>
</details>
</li>
<li><code>DeduplicationStats</code> (deduplication.py) — <span class="doc-comment-inline">Statistics for deduplication.</span></li>
<li><code>TaskMetrics</code> (execution_optimizer.py) — <span class="doc-comment-inline">Task execution metrics</span></li>
<li><code>ResourceMetrics</code> (execution_optimizer.py) — <span class="doc-comment-inline">Current resource utilization metrics.</span></li>
<li><code>_BoundedExceptionLog</code> (async_helpers.py)
<details><summary>Single bounded log line summarizing suppressed exceptions.</summary>
<div class="doc-comment">
<p>Single bounded log line summarizing suppressed exceptions.</p>
<p></p>
<p>Returned by safe_gather_fire_and_forget so callers can decide whether to</p>
<p>escalate (e.g. for telemetry). msgspec.Struct keeps it cheap on M1 UMA.</p>
</div>
</details>
</li>
<li><code>WorkerMetrics</code> (execution_optimizer.py) — <span class="doc-comment-inline">Worker performance metrics</span></li>
<li><code>CacheConfig</code> (intelligent_cache.py) — <span class="doc-comment-inline">Configuration for intelligent cache.</span></li>
<li><code>UnicodeConfig</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Configuration for Unicode attack analysis.</span></li>
<li><code>CacheEntry</code> (intelligent_cache.py) — <span class="doc-comment-inline">Single cache entry with metadata.</span></li>
<li><code>Model2VecEmbedding</code> (semantic.py)
<details><summary>DEPRECATED: Use ModernBERTEmbedding instead.</summary>
<div class="doc-comment">
<p>DEPRECATED: Use ModernBERTEmbedding instead.</p>
<p></p>
<p>Model2Vec-based embedding for efficient semantic filtering.</p>
</div>
</details>
</li>
<li><code>SentenceTransformerEmbedding</code> (semantic.py)
<details><summary>DEPRECATED: Use ModernBERTEmbedding instead.</summary>
<div class="doc-comment">
<p>DEPRECATED: Use ModernBERTEmbedding instead.</p>
<p></p>
<p>SentenceTransformer-based embedding for semantic filtering.</p>
</div>
</details>
</li>
<li><code>RaceFirstSuccessResult</code> (async_helpers.py) — <span class="doc-comment-inline">Result of `race_first_success` — msgspec.Struct for ~3× faster instantiation.</span></li>
<li><code>ExecutionStrategy</code> (execution_optimizer.py) — <span class="doc-comment-inline">Parallel execution strategies</span></li>
<li><code>ParallelGroup</code> (execution_optimizer.py) — <span class="doc-comment-inline">Parallel execution group</span></li>
<li><code>CacheEntry</code> (execution_optimizer.py) — <span class="doc-comment-inline">Entry in predictive cache.</span></li>
<li><code>CacheStats</code> (intelligent_cache.py) — <span class="doc-comment-inline">Cache performance statistics.</span></li>
<li><code>ExpansionConfig</code> (query_expansion.py) — <span class="doc-comment-inline">Configuration for query expansion</span></li>
<li><code>DeduplicationMatch</code> (deduplication.py) — <span class="doc-comment-inline">Match between two items.</span></li>
<li><code>TaskType</code> (execution_optimizer.py) — <span class="doc-comment-inline">Task types for optimization</span></li>
<li><code>ResourceType</code> (execution_optimizer.py) — <span class="doc-comment-inline">Types of system resources.</span></li>
<li><code>ResourceLimits</code> (execution_optimizer.py) — <span class="doc-comment-inline">Resource utilization limits for M1 8GB systems.</span></li>
<li><code>FrontierStats</code> (filtering.py) — <span class="doc-comment-inline">Statistics for frontier operations.</span></li>
<li><code>HomoglyphFinding</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Sprint F300: msgspec.Struct for homoglyph/confusable character detection.</span></li>
<li><code>BidiFinding</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Sprint F300: msgspec.Struct for bidirectional text attack detection.</span></li>
<li><code>NormalizationFinding</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Sprint F300: msgspec.Struct for Unicode normalization anomaly detection.</span></li>
<li><code>SprintLifecycleState</code> (sprint_lifecycle.py)</li>
<li><code>SearchResult</code> (msgspec_json.py) — <span class="doc-comment-inline">Typed result for ANN / hybrid search hot paths.</span></li>
<li><code>SprintSeed</code> (msgspec_json.py) — <span class="doc-comment-inline">Typed seed for knowledge/sprint_seeds_store.py hot path.</span></li>
<li><code>SafeGatherShieldedResult</code> (async_helpers.py) — <span class="doc-comment-inline">Result of `safe_gather_shielded` — msgspec.Struct for ~3× faster instantiation.</span></li>
<li><code>DeduplicationStrategy</code> (deduplication.py) — <span class="doc-comment-inline">Deduplication strategy types.</span></li>
<li><code>SimilarityScore</code> (deduplication.py) — <span class="doc-comment-inline">Similarity score with details.</span></li>
<li><code>ResourceMonitor</code> (execution_optimizer.py) — <span class="doc-comment-inline">Resource monitoring for optimization</span></li>
<li><code>ExtractedEntity</code> (pattern_matcher.py) — <span class="doc-comment-inline">High-precision entity extracted via regex post-processing.</span></li>
<li><code>FilterResult</code> (semantic.py) — <span class="doc-comment-inline">Result of semantic filtering.</span></li>
<li><code>QueryVariation</code> (query_expansion.py) — <span class="doc-comment-inline">A single query variation with metadata.</span></li>
<li><code>ZeroWidthFinding</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Sprint F300: msgspec.Struct for zero-width character detection.</span></li>
<li><code>CacheEntry</code> (msgspec_json.py) — <span class="doc-comment-inline">Typed entry for context_optimization/context_cache.py.</span></li>
<li><code>OptimizationLevel</code> (execution_optimizer.py) — <span class="doc-comment-inline">Optimization aggressiveness levels.</span></li>
<li><code>EvictionStrategy</code> (intelligent_cache.py) — <span class="doc-comment-inline">Cache eviction strategies.</span></li>
<li><code>ConcurrencyCategory</code> (_core.py)</li>
</ul>
</details>

<details><summary><strong>Method</strong> (493)</summary>
<ul>
<li><code>resolve_many</code> (batch_dns.py)</li>
<li><code>save</code> (persistent_kv_cache.py)</li>
<li><code>prewarm</code> (batch_dns.py)</li>
<li><code>load</code> (persistent_kv_cache.py)
<details><summary>Load KV cache from persistent storage.</summary>
<div class="doc-comment">
<p>Load KV cache from persistent storage.</p>
<p></p>
<p>Args:</p>
<p>prompt: The prompt to look up</p>
<p></p>
<p>Returns:</p>
<p>(kv_cache, token_count) if found, (None, None) if not found or error</p>
</div>
</details>
</li>
<li><code>analyze_file</code> (unicode_analyzer.py)
<details><summary>Stream-analyze a file for Unicode attacks.</summary>
<div class="doc-comment">
<p>Stream-analyze a file for Unicode attacks.</p>
<p></p>
<p>Args:</p>
<p>file_path: Path to the file to analyze</p>
<p></p>
<p>Returns:</p>
<p>UnicodeAnalysisResult with all findings</p>
</div>
</details>
</li>
<li><code>_execute_interpreter_pool</code> (execution_optimizer.py)
<details><summary>Execute pure-Python CPU-bound batch via InterpreterPoolExecutor (P2-1).</summary>
<div class="doc-comment">
<p>Execute pure-Python CPU-bound batch via InterpreterPoolExecutor (P2-1).</p>
<p></p>
<p>Uses Python 3.14 subinterpreters for true parallelism without GIL contention.</p>
<p>Each subinterpreter has its own GIL → unlike ThreadPool, no GIL serialization.</p>
<p>M1 8GB: ~1-2MB overhead per subinterpreter, max_workers capped at 2.</p>
<p></p>
<p>Falls back to ThreadPoolExecutor if InterpreterPool unavailable.</p>
<p></p>
<p>NOTE: This method expects tasks to be (data, func) tuples where func is a</p>
<p>module-level callable that can be pickled for subinterpreter dispatch.</p>
<p>Use execute_batch_interpreter() for the canonical batch(data, func) API.</p>
<p></p>
<p>Args:</p>
<p>tasks: List of (data, func) tuples from caller.</p>
<p>max_workers: Max subinterpreters. Capped at 2 for M1 8GB safety.</p>
<p></p>
<p>Returns:</p>
<p>Flattened results from all subinterpreter workers.</p>
</div>
</details>
</li>
<li><code>_cluster_by_simhash</code> (deduplication.py)
<details><summary>Group items into LSH buckets using SimHash.</summary>
<div class="doc-comment">
<p>Group items into LSH buckets using SimHash.</p>
<p></p>
<p>Uses rust.lsh.LSHIndex.batch_query() when available (&lt;50ms for 10k sigs).</p>
<p>Falls back to pure Python OrderedDict bucketing.</p>
</div>
</details>
</li>
<li><code>_execute_with_resource_constraints</code> (execution_optimizer.py) — <span class="doc-comment-inline">Execute tasks with resource constraints</span></li>
<li><code>allocate_task</code> (execution_optimizer.py)
<details><summary>Allocate a task to appropriate core type</summary>
<div class="doc-comment">
<p>Allocate a task to appropriate core type</p>
<p></p>
<p>Args:</p>
<p>task_priority: "low", "normal", "high", "critical"</p>
<p>cpu_intensity: 0.0-1.0 scale of CPU intensity</p>
<p></p>
<p>Returns:</p>
<p>Allocation configuration with CPU affinity</p>
</div>
</details>
</li>
<li><code>deduplicate</code> (deduplication.py) — <span class="doc-comment-inline">Deduplicate list of query items.</span></li>
<li><code>execute_batch_interpreter</code> (execution_optimizer.py)
<details><summary>Synchronous batch executor — call from async context via asyncio.to_thread().</summary>
<div class="doc-comment">
<p>Synchronous batch executor — call from async context via asyncio.to_thread().</p>
<p></p>
<p>P2-1: Canonical API for InterpreterPoolExecutor batch execution.</p>
<p>Chunks data and distributes to subinterpreter workers for true parallelism.</p>
<p></p>
<p>Args:</p>
<p>data: Input data (list of items to process).</p>
<p>func: Pure-Python function (list -&gt; list). Must be module-level</p>
<p>and pickle-able for subinterpreter dispatch.</p>
<p>max_workers: Subinterpreters count. Default 2 (M1 8GB safe).</p>
<p></p>
<p>Returns:</p>
<p>Flattened results from all workers.</p>
<p></p>
<p>Example:</p>
<p>results = await asyncio.to_thread(</p>
<p>optimizer.execute_batch_interpreter,</p>
<p>items,</p>
<p>normalize_text,</p>
<p>)</p>
</div>
</details>
</li>
<li><code>_evict_from_gen</code> (cache.py)</li>
<li><code>generate_complex_queries</code> (query_expansion.py)
<details><summary>Generate complex dorking queries for a topic.</summary>
<div class="doc-comment">
<p>Generate complex dorking queries for a topic.</p>
<p></p>
<p>Args:</p>
<p>topic: Search topic or domain</p>
<p>query_type: Type of queries ('academic', 'technical', 'financial',</p>
<p>'government', 'security', 'hidden')</p>
<p>include_variations: Whether to include filetype variations</p>
<p></p>
<p>Returns:</p>
<p>List of dorking queries</p>
</div>
</details>
</li>
<li><code>_start_uma_watchdog</code> (sprint_lifecycle.py)
<details><summary>Start UmaWatchdog when entering ACTIVE state.</summary>
<div class="doc-comment">
<p>Start UmaWatchdog when entering ACTIVE state.</p>
<p>Fails silently if no event loop or watchdog import fails.</p>
<p>Watchdog is tracked via track_task() for lifecycle management.</p>
</div>
</details>
</li>
<li><code>_evict_if_needed</code> (intelligent_cache.py) — <span class="doc-comment-inline">KVP-based eviction: O(1) scoring of top-10 ARC candidates only.</span></li>
<li><code>expand</code> (query_expansion.py)
<details><summary>Generate query variations.</summary>
<div class="doc-comment">
<p>Generate query variations.</p>
<p></p>
<p>Args:</p>
<p>query: Original search query</p>
<p></p>
<p>Returns:</p>
<p>List of query variations</p>
</div>
</details>
</li>
<li><code>_detect_bidi_attacks</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Detect bidirectional text attacks in text - optimized version.</span></li>
<li><code>compute_skeleton_hash</code> (unicode_analyzer.py)
<details><summary>Compute UTS #39 skeleton hash for confusables detection.</summary>
<div class="doc-comment">
<p>Compute UTS #39 skeleton hash for confusables detection.</p>
<p></p>
<p>Applies:</p>
<p>- NFD normalization</p>
<p>- Basic confusable mapping (using loaded mappings if available)</p>
<p>- Re-NFD normalization</p>
<p>- Returns sha256(skeleton)[:16]</p>
<p></p>
<p>This is used for:</p>
<p>- Spoof network clustering (same skeleton = possible confusables)</p>
<p>- Internal signal only (skeleton text is NOT stored)</p>
<p></p>
<p>Args:</p>
<p>text: Input text (typically hostname or URL segment)</p>
<p></p>
<p>Returns:</p>
<p>16-char hex digest of skeleton hash</p>
</div>
</details>
</li>
<li><code>evict_orphaned</code> (cache.py)
<details><summary>Evict entries with refcount ≤ baseline (orphaned).</summary>
<div class="doc-comment">
<p>Evict entries with refcount ≤ baseline (orphaned).</p>
<p></p>
<p>Call this during memory pressure events to reclaim abandoned sessions.</p>
<p></p>
<p>Args:</p>
<p>max_evict: Maximum entries to evict in this call.</p>
<p></p>
<p>Returns:</p>
<p>Number of entries evicted.</p>
</div>
</details>
</li>
<li><code>execute_parallel</code> (execution_optimizer.py) — <span class="doc-comment-inline">Execute tasks in parallel with optimal strategy</span></li>
<li><code>encode</code> (semantic.py)
<details><summary>Encode text to embedding vector.</summary>
<div class="doc-comment">
<p>Encode text to embedding vector.</p>
<p></p>
<p>Args:</p>
<p>text: Text to encode</p>
<p></p>
<p>Returns:</p>
<p>Embedding vector</p>
</div>
</details>
</li>
<li><code>__init__</code> (cache.py)</li>
<li><code>_compute_minhash</code> (deduplication.py)
<details><summary>Compute MinHash signature for content similarity.</summary>
<div class="doc-comment">
<p>Compute MinHash signature for content similarity.</p>
<p></p>
<p>F214OPT-J: Note on mmh3 seed optimization — mmh3.hash does accept a seed</p>
<p>argument (mmh3.hash(key, seed=N, signed=False)). However, using</p>
<p>mmh3.hash(ngram, seed=i) instead of f"{ngram}_{i}" would change the</p>
<p>computed hash values, which would invalidate existing stored MinHash</p>
<p>signatures. To preserve exact signature compatibility, the current</p>
<p>f-string approach is retained. The allocation overhead is bounded by</p>
<p>HLEDAC_DEDUP_MAX_NGRAMS (default 50000).</p>
</div>
</details>
</li>
<li><code>_run</code> (uma_budget.py) — <span class="doc-comment-inline">Main polling loop — runs until cancelled.</span></li>
<li><code>set</code> (intelligent_cache.py)
<details><summary>Set value in cache.</summary>
<div class="doc-comment">
<p>Set value in cache.</p>
<p></p>
<p>Args:</p>
<p>key: Cache key</p>
<p>value: Value to cache</p>
<p>ttl: Time-to-live in seconds (uses default if None)</p>
<p>size_bytes: Size hint for value (auto-calculated if None)</p>
<p></p>
<p>Returns:</p>
<p>True if successfully cached</p>
</div>
</details>
</li>
<li><code>register_signal_handlers</code> (sprint_lifecycle.py)
<details><summary>Register SIGINT/SIGTERM handlers that call shutdown_coro.</summary>
<div class="doc-comment">
<p>Register SIGINT/SIGTERM handlers that call shutdown_coro.</p>
<p>Must be called from the main thread / before asyncio loop is created.</p>
<p>Idempotent.</p>
<p></p>
<p>Args:</p>
<p>shutdown_coro: async callable that initiates graceful shutdown</p>
<p>(e.g., orchestrator.shutdown_all)</p>
</div>
</details>
</li>
<li><code>evict_low_refcount</code> (cache.py)
<details><summary>Force-evict entries with refcount ≤ baseline across all generations.</summary>
<div class="doc-comment">
<p>Force-evict entries with refcount ≤ baseline across all generations.</p>
<p></p>
<p>Use during memory pressure events to aggressively reclaim orphaned entries.</p>
<p></p>
<p>Args:</p>
<p>max_evict: Maximum entries to evict in this call.</p>
<p></p>
<p>Returns:</p>
<p>Number of entries evicted.</p>
</div>
</details>
</li>
<li><code>__init__</code> (batch_dns.py)</li>
<li><code>check_url</code> (filtering.py)
<details><summary>Check if URL is allowed (not blocked).</summary>
<div class="doc-comment">
<p>Check if URL is allowed (not blocked).</p>
<p></p>
<p>Returns:</p>
<p>True if allowed, False if blocked</p>
</div>
</details>
</li>
<li><code>get</code> (intelligent_cache.py)
<details><summary>Get value from cache.</summary>
<div class="doc-comment">
<p>Get value from cache.</p>
<p></p>
<p>Args:</p>
<p>key: Cache key</p>
<p></p>
<p>Returns:</p>
<p>Cached value or None if not found/expired</p>
</div>
</details>
</li>
<li><code>_init_lmdb</code> (persistent_kv_cache.py) — <span class="doc-comment-inline">Initialize LMDB metadata index.</span></li>
<li><code>find_duplicates</code> (deduplication.py) — <span class="doc-comment-inline">Find content-based duplicates using LSH clustering for O(n) performance.</span></li>
<li><code>compute_similarity</code> (semantic.py)
<details><summary>Compute semantic similarity between two texts.</summary>
<div class="doc-comment">
<p>Compute semantic similarity between two texts.</p>
<p></p>
<p>Args:</p>
<p>text1: First text</p>
<p>text2: Second text</p>
<p></p>
<p>Returns:</p>
<p>Similarity score (0-1)</p>
</div>
</details>
</li>
<li><code>extract_relevant_snippets</code> (semantic.py)
<details><summary>Extract most relevant snippets from content.</summary>
<div class="doc-comment">
<p>Extract most relevant snippets from content.</p>
<p></p>
<p>Args:</p>
<p>content: Content to extract snippets from</p>
<p>query: Query to match against</p>
<p>max_snippets: Maximum number of snippets to return</p>
<p>snippet_length: Maximum length of each snippet</p>
<p></p>
<p>Returns:</p>
<p>List of relevant snippets</p>
</div>
</details>
</li>
<li><code>analyze_text</code> (unicode_analyzer.py)
<details><summary>Analyze text for Unicode attacks.</summary>
<div class="doc-comment">
<p>Analyze text for Unicode attacks.</p>
<p></p>
<p>Args:</p>
<p>text: The text to analyze</p>
<p></p>
<p>Returns:</p>
<p>UnicodeAnalysisResult with all findings</p>
</div>
</details>
</li>
<li><code>__init__</code> (cache.py)</li>
<li><code>compute_embedding_batch</code> (deduplication.py)
<details><summary>MLX-accelerated SimHash for embedding matrix (batch, dim).</summary>
<div class="doc-comment">
<p>MLX-accelerated SimHash for embedding matrix (batch, dim).</p>
<p>Lazy import MLX, fallback to numpy.</p>
</div>
</details>
</li>
<li><code>_detect_m1_cores</code> (execution_optimizer.py) — <span class="doc-comment-inline">Detect M1 P/E core topology using sysctl</span></li>
<li><code>expand</code> (query_expansion.py) — <span class="doc-comment-inline">Expand query using semantic variations.</span></li>
<li><code>__init__</code> (deduplication.py)</li>
<li><code>_get_batch_embeddings</code> (deduplication.py) — <span class="doc-comment-inline">Get embeddings for a batch of items.</span></li>
<li><code>stats</code> (cache.py) — <span class="doc-comment-inline">Full stats including refcount telemetry.</span></li>
<li><code>_execute_adaptive</code> (execution_optimizer.py) — <span class="doc-comment-inline">Execute tasks with adaptive strategy</span></li>
<li><code>_load_from_disk</code> (filtering.py) — <span class="doc-comment-inline">Load frontier from disk.</span></li>
<li><code>expand</code> (query_expansion.py) — <span class="doc-comment-inline">Expand query using syntactic variations.</span></li>
<li><code>expand</code> (query_expansion.py)
<details><summary>Expand query using all configured strategies.</summary>
<div class="doc-comment">
<p>Expand query using all configured strategies.</p>
<p></p>
<p>Args:</p>
<p>query: Original query</p>
<p>context: Optional context (domain hints, etc.)</p>
<p></p>
<p>Returns:</p>
<p>List of query variations from all strategies</p>
</div>
</details>
</li>
<li><code>get</code> (cache.py)
<details><summary>Get value by key. Returns None on miss or if entry is expired.</summary>
<div class="doc-comment">
<p>Get value by key. Returns None on miss or if entry is expired.</p>
<p></p>
<p>Thread-safe. Refreshes TTL on hit (move to end).</p>
</div>
</details>
</li>
<li><code>get</code> (cache.py)
<details><summary>Get value by key. Returns None on miss or if entry is expired.</summary>
<div class="doc-comment">
<p>Get value by key. Returns None on miss or if entry is expired.</p>
<p></p>
<p>Async-safe. Refreshes TTL on hit (move to end).</p>
</div>
</details>
</li>
<li><code>set</code> (cache.py)
<details><summary>Set key-value pair. Lands in gen0 (youngest).</summary>
<div class="doc-comment">
<p>Set key-value pair. Lands in gen0 (youngest).</p>
<p></p>
<p>If gen0 is at capacity, promotes oldest 25% to gen1.</p>
<p>Thread-safe. Returns True on success, False on error.</p>
</div>
</details>
</li>
<li><code>promote</code> (cache.py)
<details><summary>Explicitly promote an entry one generation older (gen0 → gen1 → gen2).</summary>
<div class="doc-comment">
<p>Explicitly promote an entry one generation older (gen0 → gen1 → gen2).</p>
<p></p>
<p>Thread-safe. Returns True if entry was found and promoted.</p>
</div>
</details>
</li>
<li><code>filter_batch</code> (semantic.py)
<details><summary>Filter multiple contents against a query.</summary>
<div class="doc-comment">
<p>Filter multiple contents against a query.</p>
<p></p>
<p>Args:</p>
<p>contents: List of contents to filter</p>
<p>query: Query to match against</p>
<p>threshnew: Optional custom threshnew</p>
<p></p>
<p>Returns:</p>
<p>List of FilterResults</p>
</div>
</details>
</li>
<li><code>expand_for_discovery</code> (query_expansion.py)
<details><summary>Generate discovery-focused query variations.</summary>
<div class="doc-comment">
<p>Generate discovery-focused query variations.</p>
<p></p>
<p>Args:</p>
<p>base_terms: Base search terms</p>
<p>modifiers: Additional modifiers</p>
<p></p>
<p>Returns:</p>
<p>Combined expanded queries</p>
</div>
</details>
</li>
<li><code>_start_windown_monitor</code> (sprint_lifecycle.py) — <span class="doc-comment-inline">Start background task that fires wind-up at T-3min. Fail-open if no event loop.</span></li>
<li><code>__init__</code> (cache.py)</li>
<li><code>set</code> (cache.py)
<details><summary>Set key-value pair. Evicts oldest entry if at capacity.</summary>
<div class="doc-comment">
<p>Set key-value pair. Evicts oldest entry if at capacity.</p>
<p></p>
<p>Async-safe. Returns True on success, False on error.</p>
</div>
</details>
</li>
<li><code>_load_lru_order</code> (persistent_kv_cache.py) — <span class="doc-comment-inline">Load LRU order from LMDB at startup.</span></li>
<li><code>cosine_similarity</code> (semantic.py)
<details><summary>Compute cosine similarity between two vectors.</summary>
<div class="doc-comment">
<p>Compute cosine similarity between two vectors.</p>
<p></p>
<p>Args:</p>
<p>vec1: First vector</p>
<p>vec2: Second vector</p>
<p></p>
<p>Returns:</p>
<p>Cosine similarity (-1 to 1)</p>
</div>
</details>
</li>
<li><code>_calculate_risk_score</code> (unicode_analyzer.py)
<details><summary>Calculate overall risk score based on findings.</summary>
<div class="doc-comment">
<p>Calculate overall risk score based on findings.</p>
<p></p>
<p>Returns:</p>
<p>Risk score from 0.0 (no risk) to 100.0 (critical)</p>
</div>
</details>
</li>
<li><code>detect_mixed_script</code> (unicode_analyzer.py)
<details><summary>Detect mixed-script usage in text (potential spoofing indicator).</summary>
<div class="doc-comment">
<p>Detect mixed-script usage in text (potential spoofing indicator).</p>
<p></p>
<p>Args:</p>
<p>text: Input text to check</p>
<p></p>
<p>Returns:</p>
<p>True if mixed scripts detected</p>
</div>
</details>
</li>
<li><code>set</code> (cache.py)
<details><summary>Set key-value pair. Evicts oldest entry if at capacity.</summary>
<div class="doc-comment">
<p>Set key-value pair. Evicts oldest entry if at capacity.</p>
<p></p>
<p>Thread-safe.</p>
<p>Returns True on success, False on error.</p>
</div>
</details>
</li>
<li><code>put</code> (cache.py)
<details><summary>Store (lora_model, lora_tokenizer) tuple for an adapter path.</summary>
<div class="doc-comment">
<p>Store (lora_model, lora_tokenizer) tuple for an adapter path.</p>
<p></p>
<p>Thread-safe. Evicts oldest entry when at capacity (LRU).</p>
<p>Returns True on success, False on error.</p>
</div>
</details>
</li>
<li><code>resolve</code> (batch_dns.py)
<details><summary>Resolve hostname using c-ares (aiodns).</summary>
<div class="doc-comment">
<p>Resolve hostname using c-ares (aiodns).</p>
<p></p>
<p>Returns IPv4 addresses sorted and deduplicated.</p>
<p>Raises on failure (caller handles exceptions).</p>
</div>
</details>
</li>
<li><code>expand</code> (query_expansion.py) — <span class="doc-comment-inline">Expand query using domain-specific knowledge.</span></li>
<li><code>_detect_normalization_anomalies</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Detect Unicode normalization anomalies in text - optimized version.</span></li>
<li><code>acquire</code> (async_helpers.py)
<details><summary>Acquire a per-host concurrency slot.</summary>
<div class="doc-comment">
<p>Acquire a per-host concurrency slot.</p>
<p></p>
<p>Returns (semaphore_instance, op_id) where op_id is 'hit' or 'miss'.</p>
<p>The caller MUST pass the returned semaphore to ``release()`` —</p>
<p>NOT self._gates[host], which may have been evicted and replaced.</p>
</div>
</details>
</li>
<li><code>touch</code> (cache.py)
<details><summary>Refresh TTL for an existing key.</summary>
<div class="doc-comment">
<p>Refresh TTL for an existing key.</p>
<p></p>
<p>Thread-safe. Returns True if key existed (and is not expired),</p>
<p>False otherwise.</p>
</div>
</details>
</li>
<li><code>items</code> (cache.py)
<details><summary>Return list of (key, value) pairs, excluding expired.</summary>
<div class="doc-comment">
<p>Return list of (key, value) pairs, excluding expired.</p>
<p></p>
<p>Thread-safe. O(n) scan.</p>
</div>
</details>
</li>
<li><code>_promote_gen0_to_gen1</code> (cache.py) — <span class="doc-comment-inline">Promote oldest 25% of gen0 to gen1. Returns count promoted.</span></li>
<li><code>_promote_gen1_to_gen2</code> (cache.py) — <span class="doc-comment-inline">Promote oldest 25% of gen1 to gen2. Returns count promoted.</span></li>
<li><code>_make_room</code> (cache.py) — <span class="doc-comment-inline">Ensure space in gen0. If full, promote gen0→gen1→gen2, then evict gen2.</span></li>
<li><code>throttle_delay_ms</code> (deduplication.py)
<details><summary>Calculate throttle delay based on domain health.</summary>
<div class="doc-comment">
<p>Calculate throttle delay based on domain health.</p>
<p>Increases delay if stale cache is used frequently.</p>
</div>
</details>
</li>
<li><code>_execute_with_semaphore</code> (execution_optimizer.py)
<details><summary>Execute a single task with semaphore gating.</summary>
<div class="doc-comment">
<p>Execute a single task with semaphore gating.</p>
<p></p>
<p>F214OPT-D: Wraps task execution with pending semaphore to prevent</p>
<p>unbounded concurrent task creation. Tracks throttling for telemetry.</p>
<p></p>
<p>CPU-bound work routes to Rust rayon pools via _rust_pool_dispatch().</p>
</div>
</details>
</li>
<li><code>_save_to_disk</code> (filtering.py) — <span class="doc-comment-inline">Save frontier to disk.</span></li>
<li><code>_select_eviction_candidate</code> (intelligent_cache.py) — <span class="doc-comment-inline">Select key to evict based on strategy.</span></li>
<li><code>_init_embedding</code> (semantic.py) — <span class="doc-comment-inline">Initialize embedding model.</span></li>
<li><code>_expand_acronyms</code> (query_expansion.py) — <span class="doc-comment-inline">Expand acronyms in query</span></li>
<li><code>set</code> (cache.py)
<details><summary>Set key-value pair. Lands in gen0.</summary>
<div class="doc-comment">
<p>Set key-value pair. Lands in gen0.</p>
<p></p>
<p>Thread-safe. Returns True on success.</p>
</div>
</details>
</li>
<li><code>_generate_batch_embeddings</code> (deduplication.py) — <span class="doc-comment-inline">Generate embeddings for batch of contents using dedup-specific task.</span></li>
<li><code>_load_model</code> (deduplication.py) — <span class="doc-comment-inline">Load MLXEmbeddingManager first, then sentence-transformers fallback, then hash-based.</span></li>
<li><code>record_request</code> (deduplication.py) — <span class="doc-comment-inline">Zaznamena vysledek requestu a aktualizuje yield.</span></li>
<li><code>_adapt_worker_count</code> (execution_optimizer.py) — <span class="doc-comment-inline">Adapt worker count based on performance and resources</span></li>
<li><code>_evict_entry</code> (persistent_kv_cache.py) — <span class="doc-comment-inline">Evict a single entry by key. Returns bytes freed.</span></li>
<li><code>begin_sprint</code> (sprint_lifecycle.py)
<details><summary>Mark sprint as started, transition to WARMUP.</summary>
<div class="doc-comment">
<p>Mark sprint as started, transition to WARMUP.</p>
<p></p>
<p>Sprint F4 metadata:</p>
<p>alias_for: runtime.SprintLifecycleManager.start()</p>
<p>future_owner: __main__.py, legacy autonomous_orchestrator</p>
<p>caller_class: legacy autonomous_orchestrator (line ~11723)</p>
<p>removal_condition: All callers migrated to runtime version</p>
<p>why_still_needed: legacy AO still imports utils version directly</p>
</div>
</details>
</li>
<li><code>request_teardown</code> (sprint_lifecycle.py)
<details><summary>Transition from any winding-down state to TEARDOWN.</summary>
<div class="doc-comment">
<p>Transition from any winding-down state to TEARDOWN.</p>
<p></p>
<p>Sprint F4 metadata:</p>
<p>alias_for: runtime.SprintLifecycleManager.mark_teardown_started()</p>
<p>future_owner: __main__.py</p>
<p>caller_class: legacy autonomous_orchestrator (line ~12690)</p>
<p>removal_condition: All callers migrated to runtime version</p>
<p>why_still_needed: legacy AO still calls this; __main__.py does not call this method</p>
</div>
</details>
</li>
<li><code>find_duplicates</code> (deduplication.py) — <span class="doc-comment-inline">Find semantically similar items.</span></li>
<li><code>_fallback_embedding</code> (deduplication.py) — <span class="doc-comment-inline">Generate fallback embedding using hash-based approach.</span></li>
<li><code>_deduplicate_matches</code> (deduplication.py) — <span class="doc-comment-inline">Deduplicate matches and apply decision logic.</span></li>
<li><code>_monitor_loop</code> (execution_optimizer.py) — <span class="doc-comment-inline">Background loop that adjusts concurrency limit based on memory.</span></li>
<li><code>_execute_with_dynamic_workers</code> (execution_optimizer.py) — <span class="doc-comment-inline">Execute tasks with dynamic worker allocation</span></li>
<li><code>evict_one</code> (intelligent_cache.py) — <span class="doc-comment-inline">Evict one item and return its key. Returns None if nothing to evict.</span></li>
<li><code>add</code> (intelligent_cache.py)
<details><summary>Add URL if not already present and within memory limit.</summary>
<div class="doc-comment">
<p>Add URL if not already present and within memory limit.</p>
<p></p>
<p>Args:</p>
<p>url: URL to add</p>
<p></p>
<p>Returns:</p>
<p>True if added, False if already present or memory limit reached</p>
</div>
</details>
</li>
<li><code>contains_keywords</code> (semantic.py)
<details><summary>Check if content contains minimum number of keywords.</summary>
<div class="doc-comment">
<p>Check if content contains minimum number of keywords.</p>
<p></p>
<p>Args:</p>
<p>content: Content to check</p>
<p>keywords: List of keywords to look for</p>
<p>min_matches: Minimum number of keyword matches</p>
<p></p>
<p>Returns:</p>
<p>True if enough keywords found</p>
</div>
</details>
</li>
<li><code>__getitem__</code> (cache.py) — <span class="doc-comment-inline">Raise KeyError on miss/expired — unlike get().</span></li>
<li><code>touch</code> (cache.py) — <span class="doc-comment-inline">Refresh TTL for an existing key. Async-safe.</span></li>
<li><code>get</code> (cache.py)
<details><summary>Get value by key. Checks gen0 → gen1 → gen2 (youngest first).</summary>
<div class="doc-comment">
<p>Get value by key. Checks gen0 → gen1 → gen2 (youngest first).</p>
<p></p>
<p>Thread-safe. Returns None on miss (including GC'd entries).</p>
</div>
</details>
</li>
<li><code>find_duplicates</code> (deduplication.py) — <span class="doc-comment-inline">Find metadata-based duplicates.</span></li>
<li><code>__init__</code> (deduplication.py)</li>
<li><code>compute</code> (deduplication.py) — <span class="doc-comment-inline">Compute SimHash for text - classical token-based approach.</span></li>
<li><code>_execute_load_balanced</code> (execution_optimizer.py) — <span class="doc-comment-inline">Execute tasks with load balancing</span></li>
<li><code>_load_persisted</code> (intelligent_cache.py) — <span class="doc-comment-inline">Load persisted cache from disk.</span></li>
<li><code>cosine_similarity</code> (semantic.py)
<details><summary>Compute cosine similarity between two vectors.</summary>
<div class="doc-comment">
<p>Compute cosine similarity between two vectors.</p>
<p></p>
<p>Args:</p>
<p>vec1: First vector</p>
<p>vec2: Second vector</p>
<p></p>
<p>Returns:</p>
<p>Cosine similarity (-1 to 1)</p>
</div>
</details>
</li>
<li><code>request_export</code> (sprint_lifecycle.py)
<details><summary>Transition from WINDUP to EXPORT. Called after synthesis phase.</summary>
<div class="doc-comment">
<p>Transition from WINDUP to EXPORT. Called after synthesis phase.</p>
<p></p>
<p>Sprint F4 metadata:</p>
<p>alias_for: runtime.SprintLifecycleManager.mark_export_started()</p>
<p>future_owner: __main__.py</p>
<p>caller_class: legacy autonomous_orchestrator (line ~12357)</p>
<p>removal_condition: All callers migrated to runtime version</p>
<p>why_still_needed: legacy AO still calls this; __main__.py uses canonical directly</p>
</div>
</details>
</li>
<li><code>get</code> (cache.py)
<details><summary>Get (lora_model, lora_tokenizer) tuple by adapter path.</summary>
<div class="doc-comment">
<p>Get (lora_model, lora_tokenizer) tuple by adapter path.</p>
<p></p>
<p>Thread-safe. Refreshes LRU order on hit.</p>
<p>Returns None on miss.</p>
</div>
</details>
</li>
<li><code>_find_duplicates</code> (deduplication.py) — <span class="doc-comment-inline">Find duplicates for an item.</span></li>
<li><code>add</code> (deduplication.py)
<details><summary>Add fingerprint, return True if near-duplicate found.</summary>
<div class="doc-comment">
<p>Add fingerprint, return True if near-duplicate found.</p>
<p></p>
<p>Scans only neighboring buckets (bucket itself + 2-3 nearby buckets</p>
<p>defined by 1-bit flips in top-K space). Full Hamming check only</p>
<p>against candidates in those buckets.</p>
</div>
</details>
</li>
<li><code>initialize</code> (intelligent_cache.py)
<details><summary>Initialize cache and load persisted data.</summary>
<div class="doc-comment">
<p>Initialize cache and load persisted data.</p>
<p></p>
<p>Returns:</p>
<p>True if initialization successful</p>
</div>
</details>
</li>
<li><code>__init__</code> (semantic.py)
<details><summary>Initialize ModernBERTEmbedding.</summary>
<div class="doc-comment">
<p>Initialize ModernBERTEmbedding.</p>
<p></p>
<p>Args:</p>
<p>model_path: Optional custom model path (default: 6bit ModernBERT)</p>
</div>
</details>
</li>
<li><code>encode</code> (semantic.py)
<details><summary>Encode text to embedding vector.</summary>
<div class="doc-comment">
<p>Encode text to embedding vector.</p>
<p></p>
<p>Args:</p>
<p>text: Text to encode</p>
<p></p>
<p>Returns:</p>
<p>Embedding vector (768 dimensions)</p>
</div>
</details>
</li>
<li><code>fit</code> (semantic.py)
<details><summary>Build vocabulary from documents.</summary>
<div class="doc-comment">
<p>Build vocabulary from documents.</p>
<p></p>
<p>Args:</p>
<p>documents: List of documents</p>
</div>
</details>
</li>
<li><code>request_windup</code> (sprint_lifecycle.py)
<details><summary>Request wind-down. Can be called from timer, SIGINT/SIGTERM, or manual trigger.</summary>
<div class="doc-comment">
<p>Request wind-down. Can be called from timer, SIGINT/SIGTERM, or manual trigger.</p>
<p>Idempotent — only fires once.</p>
<p></p>
<p>Sprint F4 metadata:</p>
<p>alias_for: runtime.SprintLifecycleManager.transition_to(WINDUP)</p>
<p>future_owner: __main__.py</p>
<p>caller_class: legacy autonomous_orchestrator (not called per grep)</p>
<p>removal_condition: All callers migrated to runtime version</p>
<p>why_still_needed: legacy AO has this as available method but no active call-sites</p>
</div>
</details>
</li>
<li><code>on_critical</code> (uma_budget.py) — <span class="doc-comment-inline">Trigger MLX cache cleanup on CRITICAL state.</span></li>
<li><code>on_emergency</code> (uma_budget.py) — <span class="doc-comment-inline">Trigger aggressive cleanup on EMERGENCY state.</span></li>
<li><code>stats</code> (cache.py)
<details><summary>Hit/miss/eviction/expiration stats for cache efficiency monitoring.</summary>
<div class="doc-comment">
<p>Hit/miss/eviction/expiration stats for cache efficiency monitoring.</p>
<p></p>
<p>Returns a copy — safe for read-only access.</p>
</div>
</details>
</li>
<li><code>stats</code> (cache.py) — <span class="doc-comment-inline">Hit/miss/eviction/promotion stats.</span></li>
<li><code>get</code> (cache.py)
<details><summary>Get value by key. Returns None on miss.</summary>
<div class="doc-comment">
<p>Get value by key. Returns None on miss.</p>
<p></p>
<p>Thread-safe.</p>
</div>
</details>
</li>
<li><code>clear</code> (cache.py) — <span class="doc-comment-inline">Clear all generations. Thread-safe.</span></li>
<li><code>_compute_field_similarities</code> (deduplication.py) — <span class="doc-comment-inline">Compute similarities for each field.</span></li>
<li><code>__init__</code> (execution_optimizer.py)</li>
<li><code>_run_in_executor_safe</code> (execution_optimizer.py)
<details><summary>Run coroutine func in executor safely - handles running loop correctly.</summary>
<div class="doc-comment">
<p>Run coroutine func in executor safely - handles running loop correctly.</p>
<p></p>
<p>M1-SAFE: When a loop is already running, use run_until_complete on the</p>
<p>existing loop from the worker thread. This avoids creating a nested event</p>
<p>loop with asyncio.run() which crashes Metal on Apple Silicon M1.</p>
</div>
</details>
</li>
<li><code>analyze_workload_pattern</code> (execution_optimizer.py) — <span class="doc-comment-inline">Analyze workload patterns for optimization recommendations.</span></li>
<li><code>get_optimal_thread_count</code> (execution_optimizer.py)
<details><summary>Get optimal thread count based on task type and core topology</summary>
<div class="doc-comment">
<p>Get optimal thread count based on task type and core topology</p>
<p></p>
<p>Args:</p>
<p>task_type: "cpu_bound", "io_bound", "mixed"</p>
<p></p>
<p>Returns:</p>
<p>Recommended thread count</p>
</div>
</details>
</li>
<li><code>is_blocked</code> (filtering.py) — <span class="doc-comment-inline">Check if URL is blocked.</span></li>
<li><code>load_blocklist_file</code> (filtering.py) — <span class="doc-comment-inline">Load blocklist from file (one entry per line).</span></li>
<li><code>on_access</code> (intelligent_cache.py) — <span class="doc-comment-inline">Record cache hit - move from T1 to T2 or update in T2.</span></li>
<li><code>__init__</code> (intelligent_cache.py)
<details><summary>Initialize intelligent cache.</summary>
<div class="doc-comment">
<p>Initialize intelligent cache.</p>
<p></p>
<p>Args:</p>
<p>config: Cache configuration</p>
</div>
</details>
</li>
<li><code>close</code> (intelligent_cache.py) — <span class="doc-comment-inline">Close cache and cleanup resources.</span></li>
<li><code>__init__</code> (persistent_kv_cache.py)</li>
<li><code>filter</code> (semantic.py)
<details><summary>Filter content based on semantic similarity to query.</summary>
<div class="doc-comment">
<p>Filter content based on semantic similarity to query.</p>
<p></p>
<p>Args:</p>
<p>content: Content to filter</p>
<p>query: Query to match against</p>
<p>threshnew: Optional custom threshnew</p>
<p></p>
<p>Returns:</p>
<p>FilterResult with filtering result</p>
</div>
</details>
</li>
<li><code>extract_matching_keywords</code> (semantic.py)
<details><summary>Extract keywords that appear in content.</summary>
<div class="doc-comment">
<p>Extract keywords that appear in content.</p>
<p></p>
<p>Args:</p>
<p>content: Content to extract from</p>
<p>keywords: List of keywords to check</p>
<p></p>
<p>Returns:</p>
<p>List of matching keywords</p>
</div>
</details>
</li>
<li><code>_generate_synonym_variations</code> (query_expansion.py) — <span class="doc-comment-inline">Generate variations by replacing words with synonyms</span></li>
<li><code>__init__</code> (sprint_lifecycle.py)</li>
<li><code>purge_expired</code> (cache.py)
<details><summary>Remove all expired entries. O(n) scan with lock held.</summary>
<div class="doc-comment">
<p>Remove all expired entries. O(n) scan with lock held.</p>
<p></p>
<p>Thread-safe. Returns number of purged entries.</p>
</div>
</details>
</li>
<li><code>purge_expired</code> (cache.py) — <span class="doc-comment-inline">Remove all expired entries. Async-safe. Returns purge count.</span></li>
<li><code>evict_oldest</code> (cache.py)
<details><summary>Evict and return the oldest (LRU) entry, or None if cache is empty.</summary>
<div class="doc-comment">
<p>Evict and return the oldest (LRU) entry, or None if cache is empty.</p>
<p></p>
<p>Thread-safe.</p>
</div>
</details>
</li>
<li><code>_text_similarity</code> (deduplication.py) — <span class="doc-comment-inline">Compute text similarity.</span></li>
<li><code>_execute_round_robin</code> (execution_optimizer.py) — <span class="doc-comment-inline">Execute tasks using round-robin distribution</span></li>
<li><code>put</code> (execution_optimizer.py) — <span class="doc-comment-inline">Put value into cache with predictive eviction.</span></li>
<li><code>_evict_one</code> (execution_optimizer.py) — <span class="doc-comment-inline">Evict one item using predictive strategy.</span></li>
<li><code>on_warn</code> (uma_budget.py) — <span class="doc-comment-inline">F265H-EXT: Lightweight GC on normal→warn transition (prevents cascade).</span></li>
<li><code>__contains__</code> (cache.py) — <span class="doc-comment-inline">Check key exists and is not expired. O(1). Thread-safe.</span></li>
<li><code>clear</code> (cache.py) — <span class="doc-comment-inline">Clear all entries. Async-safe.</span></li>
<li><code>_evict_lru_from</code> (cache.py) — <span class="doc-comment-inline">Evict `count` oldest entries (first in dict order) from gen. Returns evicted.</span></li>
<li><code>from_dict</code> (deduplication.py) — <span class="doc-comment-inline">Create from dict.</span></li>
<li><code>_resolve_max_pending_ops</code> (execution_optimizer.py)
<details><summary>Resolve max pending ops from env or return M1-safe default.</summary>
<div class="doc-comment">
<p>Resolve max pending ops from env or return M1-safe default.</p>
<p></p>
<p>F214OPT-D: M1 8GB can only handle ~4-8 concurrent tasks before Metal</p>
<p>memory pressure causes OOM. Default to 4 (conservative) to leave headroom</p>
<p>for the LLM itself (~2GB KV cache + activations).</p>
</div>
</details>
</li>
<li><code>_train_prediction_model</code> (execution_optimizer.py) — <span class="doc-comment-inline">Train prediction model on historical task data</span></li>
<li><code>build</code> (filtering.py) — <span class="doc-comment-inline">Build the filter from added items.</span></li>
<li><code>__init__</code> (filtering.py)</li>
<li><code>_save_sqlite</code> (filtering.py) — <span class="doc-comment-inline">Save frontier to SQLite.</span></li>
<li><code>delete</code> (intelligent_cache.py)
<details><summary>Delete entry from cache.</summary>
<div class="doc-comment">
<p>Delete entry from cache.</p>
<p></p>
<p>Args:</p>
<p>key: Cache key</p>
<p></p>
<p>Returns:</p>
<p>True if deleted, False if not found</p>
</div>
</details>
</li>
<li><code>update</code> (intelligent_cache.py)
<details><summary>Add multiple URLs.</summary>
<div class="doc-comment">
<p>Add multiple URLs.</p>
<p></p>
<p>Args:</p>
<p>urls: List of URLs to add</p>
<p></p>
<p>Returns:</p>
<p>Number of URLs actually added</p>
</div>
</details>
</li>
<li><code>tokenize</code> (semantic.py)
<details><summary>Tokenize text into words.</summary>
<div class="doc-comment">
<p>Tokenize text into words.</p>
<p></p>
<p>Args:</p>
<p>text: Text to tokenize</p>
<p></p>
<p>Returns:</p>
<p>List of tokens</p>
</div>
</details>
</li>
<li><code>extract_keywords</code> (semantic.py)
<details><summary>Extract top keywords from text.</summary>
<div class="doc-comment">
<p>Extract top keywords from text.</p>
<p></p>
<p>Args:</p>
<p>text: Text to extract keywords from</p>
<p>top_k: Number of keywords to return</p>
<p></p>
<p>Returns:</p>
<p>List of top keywords</p>
</div>
</details>
</li>
<li><code>__exit__</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Context manager exit.</span></li>
<li><code>mark_warmup_done</code> (sprint_lifecycle.py)
<details><summary>Transition from WARMUP to ACTIVE. Idempotent.</summary>
<div class="doc-comment">
<p>Transition from WARMUP to ACTIVE. Idempotent.</p>
<p></p>
<p>Sprint F4 metadata:</p>
<p>alias_for: runtime.SprintLifecycleManager.transition_to(ACTIVE)</p>
<p>future_owner: __main__.py</p>
<p>caller_class: legacy autonomous_orchestrator (not currently called per grep)</p>
<p>removal_condition: All callers migrated to runtime version</p>
<p>why_still_needed: legacy AO imports this module; no active call-sites in legacy AO per current grep</p>
</div>
</details>
</li>
<li><code>_evict_idle</code> (async_helpers.py)
<details><summary>Evict LRU hosts when over capacity (called lazily on miss).</summary>
<div class="doc-comment">
<p>Evict LRU hosts when over capacity (called lazily on miss).</p>
<p></p>
<p>Uses OrderedDict LRU ordering: move_to_end() marks recent access,</p>
<p>popitem(last=False) evicts oldest — both O(1) C-implemented.</p>
</div>
</details>
</li>
<li><code>clear</code> (cache.py) — <span class="doc-comment-inline">Clear all generations. Thread-safe.</span></li>
<li><code>get_refcount</code> (cache.py)
<details><summary>Get current refcount for an entry. Useful for telemetry.</summary>
<div class="doc-comment">
<p>Get current refcount for an entry. Useful for telemetry.</p>
<p></p>
<p>Returns 0 if key not found.</p>
</div>
</details>
</li>
<li><code>evict_gen2</code> (cache.py)
<details><summary>Evict oldest generation (gen2) entries by LRU.</summary>
<div class="doc-comment">
<p>Evict oldest generation (gen2) entries by LRU.</p>
<p></p>
<p>Call after evict_orphaned() to clear aged entries.</p>
<p></p>
<p>Returns:</p>
<p>Number of entries evicted.</p>
</div>
</details>
</li>
<li><code>_get_embedding</code> (deduplication.py) — <span class="doc-comment-inline">Get embedding for a single item.</span></li>
<li><code>get</code> (execution_optimizer.py) — <span class="doc-comment-inline">Get value from cache with access tracking.</span></li>
<li><code>schedule</code> (execution_optimizer.py) — <span class="doc-comment-inline">Schedule task with memory awareness.</span></li>
<li><code>to_dict</code> (batch_dns.py)</li>
<li><code>_ensure_aiodns</code> (batch_dns.py) — <span class="doc-comment-inline">Lazily init aiodns resolver. Returns True if available.</span></li>
<li><code>on_set</code> (intelligent_cache.py) — <span class="doc-comment-inline">Record new item set.</span></li>
<li><code>_track_task</code> (intelligent_cache.py) — <span class="doc-comment-inline">F196B: Track background tasks for proper cleanup.</span></li>
<li><code>__init__</code> (semantic.py)
<details><summary>Initialize SemanticFilter.</summary>
<div class="doc-comment">
<p>Initialize SemanticFilter.</p>
<p></p>
<p>Args:</p>
<p>threshnew: Default similarity threshnew (0-1)</p>
<p>use_fallback: Whether to use fallback if ModernBERT unavailable</p>
</div>
</details>
</li>
<li><code>initialize</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Initialize the analyzer by loading confusable mappings.</span></li>
<li><code>_load_confusable_mappings</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Load confusable character mappings - optimized version.</span></li>
<li><code>load_from_checkpoint</code> (sprint_lifecycle.py)
<details><summary>Restore lifecycle state from checkpoint payload.</summary>
<div class="doc-comment">
<p>Restore lifecycle state from checkpoint payload.</p>
<p>Sprint 1B will call this in CheckpointManager.load().</p>
</div>
</details>
</li>
<li><code>stats</code> (cache.py) — <span class="doc-comment-inline">Hit/miss/eviction stats for cache efficiency monitoring.</span></li>
<li><code>_generate_embedding</code> (deduplication.py) — <span class="doc-comment-inline">Generate embedding for content using dedup-specific task.</span></li>
<li><code>_compute_hash_similarity</code> (deduplication.py) — <span class="doc-comment-inline">Compute hash-based similarity.</span></li>
<li><code>_neighbor_buckets</code> (deduplication.py)
<details><summary>Generate all 16 neighboring bucket keys (all 1-bit flips in top-K space).</summary>
<div class="doc-comment">
<p>Generate all 16 neighboring bucket keys (all 1-bit flips in top-K space).</p>
<p></p>
<p>For Hamming distance &lt;= 3, a near-duplicate's top-K bits differ by at most</p>
<p>3 bit flips. We must check ALL possible 1-bit flips of the bucket key to</p>
<p>ensure ~95% recall (threshold=3, top_k=16).</p>
</div>
</details>
</li>
<li><code>_token_hash</code> (deduplication.py) — <span class="doc-comment-inline">64-bit hash of token (seeded), with cache for repeated tokens.</span></li>
<li><code>_determine_optimal_workers</code> (execution_optimizer.py) — <span class="doc-comment-inline">Determine optimal number of workers based on task type and system resources</span></li>
<li><code>_classify_tasks_by_resources</code> (execution_optimizer.py) — <span class="doc-comment-inline">Classify tasks by their resource requirements</span></li>
<li><code>_predict_task_times</code> (execution_optimizer.py) — <span class="doc-comment-inline">Predict execution times for tasks</span></li>
<li><code>_load_sqlite</code> (filtering.py) — <span class="doc-comment-inline">Load frontier from SQLite.</span></li>
<li><code>async_init</code> (persistent_kv_cache.py) — <span class="doc-comment-inline">Async initialization — call once at startup.</span></li>
<li><code>stats</code> (persistent_kv_cache.py) — <span class="doc-comment-inline">Return cache statistics.</span></li>
<li><code>_detect_homoglyphs</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Detect homoglyph/confusable characters in text - optimized version.</span></li>
<li><code>clear</code> (cache.py) — <span class="doc-comment-inline">Clear all entries. Thread-safe. Returns True.</span></li>
<li><code>__contains__</code> (cache.py) — <span class="doc-comment-inline">Check key exists and is not expired. O(1).</span></li>
<li><code>stats</code> (cache.py) — <span class="doc-comment-inline">Hit/miss/eviction/expiration stats.</span></li>
<li><code>_promote_generations</code> (cache.py) — <span class="doc-comment-inline">Promote oldest 25% of each generation to the next older generation.</span></li>
<li><code>__repr__</code> (cache.py)</li>
<li><code>_load_stats</code> (deduplication.py) — <span class="doc-comment-inline">Nacte statistiky z disku.</span></li>
<li><code>_load_config</code> (execution_optimizer.py) — <span class="doc-comment-inline">Load parallel execution configuration</span></li>
<li><code>detect_anomalies</code> (execution_optimizer.py) — <span class="doc-comment-inline">Detect anomalies in resource metrics.</span></li>
<li><code>_is_anomaly</code> (execution_optimizer.py) — <span class="doc-comment-inline">Check if latest value is anomalous using Z-score.</span></li>
<li><code>__init__</code> (batch_dns.py)</li>
<li><code>_ensure_async_primitives</code> (batch_dns.py)
<details><summary>Lazily allocate async primitives on first async use.</summary>
<div class="doc-comment">
<p>Lazily allocate async primitives on first async use.</p>
<p></p>
<p>Avoids binding the resolver to a specific event loop at</p>
<p>construction time. Lets the resolver be passed across loops</p>
<p>(rare in this codebase, but cheap to support).</p>
</div>
</details>
</li>
<li><code>_normalize_url</code> (filtering.py) — <span class="doc-comment-inline">Normalize URL for consistent deduplication.</span></li>
<li><code>_persist</code> (intelligent_cache.py) — <span class="doc-comment-inline">Persist cache to disk.</span></li>
<li><code>_get_synonyms</code> (query_expansion.py) — <span class="doc-comment-inline">Get synonyms for a word</span></li>
<li><code>_generate_permutations</code> (query_expansion.py) — <span class="doc-comment-inline">Generate permutations of query terms</span></li>
<li><code>_detect_zero_width</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Detect zero-width characters in text - optimized version.</span></li>
<li><code>start</code> (uma_budget.py)
<details><summary>Start the watchdog in the current event loop.</summary>
<div class="doc-comment">
<p>Start the watchdog in the current event loop.</p>
<p></p>
<p>Returns the asyncio.Task so caller can track it.</p>
<p>Raises RuntimeError if already running.</p>
</div>
</details>
</li>
<li><code>clear</code> (cache.py) — <span class="doc-comment-inline">Clear all entries. Thread-safe. Returns True.</span></li>
<li><code>_refcount</code> (cache.py) — <span class="doc-comment-inline">Return sys.getrefcount for an entry in the given generation.</span></li>
<li><code>__repr__</code> (cache.py)</li>
<li><code>_get_refcount</code> (cache.py) — <span class="doc-comment-inline">Get refcount for an entry. Returns 0 if not found.</span></li>
<li><code>get_refcounts</code> (cache.py)
<details><summary>Get refcounts for all entries. For telemetry/debugging.</summary>
<div class="doc-comment">
<p>Get refcounts for all entries. For telemetry/debugging.</p>
<p></p>
<p>Returns {key: refcount} for all entries.</p>
</div>
</details>
</li>
<li><code>_get_content_signature</code> (deduplication.py) — <span class="doc-comment-inline">Generate content signature for an item.</span></li>
<li><code>_generate_signature</code> (deduplication.py) — <span class="doc-comment-inline">Generate complete content signature.</span></li>
<li><code>_extract_and_normalize_metadata</code> (deduplication.py) — <span class="doc-comment-inline">Extract and normalize metadata fields.</span></li>
<li><code>_record_execution_metrics</code> (execution_optimizer.py) — <span class="doc-comment-inline">Record execution metrics for group</span></li>
<li><code>predict_scaling_needs</code> (execution_optimizer.py) — <span class="doc-comment-inline">Predict scaling needs based on historical data.</span></li>
<li><code>apply_thermal_throttling</code> (execution_optimizer.py)
<details><summary>Apply thermal throttling state</summary>
<div class="doc-comment">
<p>Apply thermal throttling state</p>
<p></p>
<p>Args:</p>
<p>state: "normal", "elevated", "critical"</p>
</div>
</details>
</li>
<li><code>_normalize_url</code> (filtering.py) — <span class="doc-comment-inline">Normalize URL for consistent matching.</span></li>
<li><code>_check_cache</code> (filtering.py) — <span class="doc-comment-inline">Check cache for URL.</span></li>
<li><code>__init__</code> (filtering.py)</li>
<li><code>contains</code> (filtering.py) — <span class="doc-comment-inline">Check if URL is in frontier.</span></li>
<li><code>clear</code> (filtering.py) — <span class="doc-comment-inline">Clear all URLs from frontier.</span></li>
<li><code>__init__</code> (intelligent_cache.py)
<details><summary>Initialize memory-optimized URL set.</summary>
<div class="doc-comment">
<p>Initialize memory-optimized URL set.</p>
<p></p>
<p>Args:</p>
<p>max_memory_mb: Maximum memory to use in MB</p>
</div>
</details>
</li>
<li><code>clear</code> (persistent_kv_cache.py) — <span class="doc-comment-inline">Clear all cache entries.</span></li>
<li><code>generate_all_categories</code> (query_expansion.py)
<details><summary>Generate queries for all categories.</summary>
<div class="doc-comment">
<p>Generate queries for all categories.</p>
<p></p>
<p>Args:</p>
<p>topic: Search topic</p>
<p></p>
<p>Returns:</p>
<p>Dictionary mapping category to list of queries</p>
</div>
</details>
</li>
<li><code>add_custom_pattern</code> (query_expansion.py)
<details><summary>Add custom pattern to a category.</summary>
<div class="doc-comment">
<p>Add custom pattern to a category.</p>
<p></p>
<p>Args:</p>
<p>category: Category name (creates new if doesn't exist)</p>
<p>pattern: Pattern string with {domain} placeholder</p>
</div>
</details>
</li>
<li><code>cancel</code> (sprint_lifecycle.py) — <span class="doc-comment-inline">Cancel all internal background tasks.</span></li>
<li><code>release</code> (async_helpers.py)
<details><summary>Release a per-host slot using the instance returned by ``acquire()``.</summary>
<div class="doc-comment">
<p>Release a per-host slot using the instance returned by ``acquire()``.</p>
<p></p>
<p>Safe against double-release (ValueError is swallowed).</p>
</div>
</details>
</li>
<li><code>__repr__</code> (cache.py)</li>
<li><code>_get_lock</code> (cache.py)
<details><summary>Lazy lock acquisition — creates Lock on first await inside an event loop.</summary>
<div class="doc-comment">
<p>Lazy lock acquisition — creates Lock on first await inside an event loop.</p>
<p></p>
<p>This is the CORRECT pattern for asyncio.Lock in async classes.</p>
<p>NEVER use self._lock = asyncio.Lock() at __init__ / module level.</p>
</div>
</details>
</li>
<li><code>__repr__</code> (cache.py)</li>
<li><code>__repr__</code> (cache.py)</li>
<li><code>_compute_cosine_similarity</code> (deduplication.py) — <span class="doc-comment-inline">Compute cosine similarity between two embeddings.</span></li>
<li><code>__init__</code> (deduplication.py)</li>
<li><code>save_stats</code> (deduplication.py) — <span class="doc-comment-inline">Ulozi statistiky na disk.</span></li>
<li><code>_pending_limit</code> (execution_optimizer.py)
<details><summary>Lazy semaphore for bounded pending ops.</summary>
<div class="doc-comment">
<p>Lazy semaphore for bounded pending ops.</p>
<p></p>
<p>F214OPT-D: Created on first access inside async context to avoid</p>
<p>creating asyncio primitives outside a running loop.</p>
</div>
</details>
</li>
<li><code>execution_predictor</code> (execution_optimizer.py) — <span class="doc-comment-inline">Lazy-loaded predictor to avoid eager sklearn import (1478 modules).</span></li>
<li><code>_execute_predictive</code> (execution_optimizer.py) — <span class="doc-comment-inline">Execute tasks with predictive optimization</span></li>
<li><code>_estimate_completion_time</code> (execution_optimizer.py) — <span class="doc-comment-inline">Estimate completion time for task group</span></li>
<li><code>cleanup</code> (execution_optimizer.py) — <span class="doc-comment-inline">Clean up resources</span></li>
<li><code>_predict_next_access</code> (execution_optimizer.py) — <span class="doc-comment-inline">Predict when key will be accessed next.</span></li>
<li><code>_init_quotient_filter</code> (filtering.py) — <span class="doc-comment-inline">Initialize quotient filter.</span></li>
<li><code>remove</code> (filtering.py) — <span class="doc-comment-inline">Remove URL from frontier.</span></li>
<li><code>_background_cleanup</code> (intelligent_cache.py) — <span class="doc-comment-inline">Background task for periodic cleanup.</span></li>
<li><code>_close</code> (persistent_kv_cache.py) — <span class="doc-comment-inline">Close LMDB environment.</span></li>
<li><code>_build_synonym_map</code> (query_expansion.py) — <span class="doc-comment-inline">Build combined synonym map based on domain context</span></li>
<li><code>_generate_plural</code> (query_expansion.py) — <span class="doc-comment-inline">Generate plural form of word</span></li>
<li><code>transition_to</code> (sprint_lifecycle.py)
<details><summary>Transition to a new state. Idempotent — same-state transition is a no-op.</summary>
<div class="doc-comment">
<p>Transition to a new state. Idempotent — same-state transition is a no-op.</p>
<p>Logs all transitions.</p>
</div>
</details>
</li>
<li><code>_on_task_done</code> (sprint_lifecycle.py) — <span class="doc-comment-inline">Done-callback: log exception if task failed, then remove from _bg_tasks.</span></li>
<li><code>__init__</code> (cache.py)</li>
<li><code>_compute_weighted_similarity</code> (deduplication.py) — <span class="doc-comment-inline">Compute weighted similarity.</span></li>
<li><code>_adjust_workers_for_resources</code> (execution_optimizer.py) — <span class="doc-comment-inline">Adjust worker count based on available resources</span></li>
<li><code>get_stats</code> (execution_optimizer.py) — <span class="doc-comment-inline">Get cache statistics.</span></li>
<li><code>get_status</code> (pattern_matcher.py) — <span class="doc-comment-inline">Return current matcher status. O(1), side-effect free.</span></li>
<li><code>_update_cache</code> (filtering.py) — <span class="doc-comment-inline">Update cache with URL result.</span></li>
<li><code>__init__</code> (filtering.py)</li>
<li><code>__init__</code> (intelligent_cache.py)</li>
<li><code>clear</code> (intelligent_cache.py) — <span class="doc-comment-inline">Clear all cache entries.</span></li>
<li><code>_remove_entry</code> (intelligent_cache.py) — <span class="doc-comment-inline">Remove entry from all data structures.</span></li>
<li><code>_get_xxhash</code> (persistent_kv_cache.py) — <span class="doc-comment-inline">Lazy xxhash import.</span></li>
<li><code>_evict_lru</code> (persistent_kv_cache.py) — <span class="doc-comment-inline">Evict oldest LRU entries until within bounds. Returns count evicted.</span></li>
<li><code>remaining_time</code> (sprint_lifecycle.py)
<details><summary>Estimated seconds remaining in sprint. Returns 0.0 if not started.</summary>
<div class="doc-comment">
<p>Estimated seconds remaining in sprint. Returns 0.0 if not started.</p>
<p>This is a read-only signal — never blocks.</p>
</div>
</details>
</li>
<li><code>_stop_uma_watchdog</code> (sprint_lifecycle.py) — <span class="doc-comment-inline">Stop UMA watchdog. Called when exiting ACTIVE state.</span></li>
<li><code>_cluster_by_simhash</code> (deduplication.py) — <span class="doc-comment-inline">Group items into LSH buckets using SimHash for near-linear deduplication.</span></li>
<li><code>__init__</code> (deduplication.py)</li>
<li><code>_cluster_by_simhash</code> (deduplication.py) — <span class="doc-comment-inline">Group items into LSH buckets using SimHash for near-linear deduplication.</span></li>
<li><code>_compute_hash</code> (deduplication.py) — <span class="doc-comment-inline">Compute exact content hash.</span></li>
<li><code>_compute_minhash_similarity</code> (deduplication.py) — <span class="doc-comment-inline">Compute MinHash Jaccard similarity.</span></li>
<li><code>_normalize_field_value</code> (deduplication.py) — <span class="doc-comment-inline">Normalize a metadata field value.</span></li>
<li><code>_normalize_text</code> (deduplication.py) — <span class="doc-comment-inline">Normalize text for comparison.</span></li>
<li><code>_normalize_text_sync</code> (deduplication.py) — <span class="doc-comment-inline">Synchronous text normalization.</span></li>
<li><code>get_stats</code> (deduplication.py) — <span class="doc-comment-inline">Vrati statistiky pro domenu (vytvori nove pokud neexistuji).</span></li>
<li><code>get_yield_penalty</code> (deduplication.py) — <span class="doc-comment-inline">Vrati yield-based penalty pro domenu (0-1, vyssi = vice penalizace).</span></li>
<li><code>_is_near_duplicate</code> (deduplication.py)
<details><summary>Check if fingerprint is near-duplicate of any seen fingerprint.</summary>
<div class="doc-comment">
<p>Check if fingerprint is near-duplicate of any seen fingerprint.</p>
<p></p>
<p>Uses TopKBucketIndex for O(1) average lookup instead of O(n) full scan.</p>
<p>Scans only neighboring buckets (same top-K bits ± 1 bit flip).</p>
<p>Threshold = 3 bits (~95% recall for 64-bit SimHash).</p>
</div>
</details>
</li>
<li><code>hamming_distance</code> (deduplication.py) — <span class="doc-comment-inline">Compute Hamming distance between two hashes.</span></li>
<li><code>stop_monitoring</code> (execution_optimizer.py) — <span class="doc-comment-inline">Stop the background memory monitor.</span></li>
<li><code>_prune_parallel_groups</code> (execution_optimizer.py) — <span class="doc-comment-inline">Prune oldest and expired parallel groups.</span></li>
<li><code>_execute_resource_aware</code> (execution_optimizer.py) — <span class="doc-comment-inline">Execute tasks with resource awareness</span></li>
<li><code>_distribute_tasks_load_balanced</code> (execution_optimizer.py) — <span class="doc-comment-inline">Distribute tasks among workers based on current loads</span></li>
<li><code>__init__</code> (execution_optimizer.py)</li>
<li><code>_fallback_to_generic_topology</code> (execution_optimizer.py) — <span class="doc-comment-inline">Fallback to generic CPU topology detection</span></li>
<li><code>__init__</code> (execution_optimizer.py)</li>
<li><code>_evict_neg_cache_oldest</code> (batch_dns.py) — <span class="doc-comment-inline">Evict oldest 25% of negative cache to maintain bounded size.</span></li>
<li><code>_init_filter</code> (filtering.py) — <span class="doc-comment-inline">Initialize pyxorfilter.</span></li>
<li><code>contains</code> (filtering.py) — <span class="doc-comment-inline">Check if item is in filter.</span></li>
<li><code>_load_default_blocklists</code> (filtering.py) — <span class="doc-comment-inline">Load default blocked domains and patterns.</span></li>
<li><code>_cleanup_expired</code> (intelligent_cache.py) — <span class="doc-comment-inline">Remove expired entries.</span></li>
<li><code>__init__</code> (semantic.py) — <span class="doc-comment-inline">Initialize SimpleEmbedding.</span></li>
<li><code>_detect_domain</code> (query_expansion.py) — <span class="doc-comment-inline">Detect domain from query terms.</span></li>
<li><code>__init__</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Initialize the Unicode attack analyzer.</span></li>
<li><code>cleanup</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Clean up resources and free memory.</span></li>
<li><code>get_stats</code> (async_helpers.py) — <span class="doc-comment-inline">Return telemetry snapshot.</span></li>
<li><code>size</code> (cache.py) — <span class="doc-comment-inline">Current number of entries (including potentially expired).</span></li>
<li><code>__init__</code> (cache.py)</li>
<li><code>contains</code> (cache.py) — <span class="doc-comment-inline">Check key exists. Thread-safe. O(1).</span></li>
<li><code>size</code> (cache.py) — <span class="doc-comment-inline">Current number of entries.</span></li>
<li><code>size</code> (cache.py) — <span class="doc-comment-inline">Total entries across all generations (approximate — WVD may differ).</span></li>
<li><code>_scan_refcounts</code> (cache.py) — <span class="doc-comment-inline">Scan all generations and return {key: (refcount, gen)} for all entries.</span></li>
<li><code>size</code> (cache.py) — <span class="doc-comment-inline">Total entries across all generations.</span></li>
<li><code>cleanup</code> (deduplication.py) — <span class="doc-comment-inline">Cleanup resources.</span></li>
<li><code>_generate_ngrams</code> (deduplication.py) — <span class="doc-comment-inline">Generate n-grams from content.</span></li>
<li><code>__init__</code> (deduplication.py)</li>
<li><code>_generic_similarity</code> (deduplication.py) — <span class="doc-comment-inline">Compute generic similarity.</span></li>
<li><code>_process_batch</code> (deduplication.py) — <span class="doc-comment-inline">Process a batch of items.</span></li>
<li><code>__init__</code> (deduplication.py)</li>
<li><code>get_summary</code> (deduplication.py) — <span class="doc-comment-inline">Get summary stats for all domains.</span></li>
<li><code>__init__</code> (execution_optimizer.py)</li>
<li><code>add_parallel_group</code> (execution_optimizer.py) — <span class="doc-comment-inline">Add a parallel group with bounded storage and TTL.</span></li>
<li><code>_calculate_resource_allocation</code> (execution_optimizer.py) — <span class="doc-comment-inline">Calculate optimal resource allocation for task group</span></li>
<li><code>get_performance_statistics</code> (execution_optimizer.py) — <span class="doc-comment-inline">Get performance statistics</span></li>
<li><code>add_pattern</code> (filtering.py) — <span class="doc-comment-inline">Add blocked URL pattern (regex).</span></li>
<li><code>_get_domain</code> (filtering.py) — <span class="doc-comment-inline">Extract domain from URL.</span></li>
<li><code>add_blocked_domain</code> (filtering.py) — <span class="doc-comment-inline">Add domain to blocklist.</span></li>
<li><code>add_blocked_url</code> (filtering.py) — <span class="doc-comment-inline">Add URL to blocklist.</span></li>
<li><code>add</code> (filtering.py) — <span class="doc-comment-inline">Add URL to frontier.</span></li>
<li><code>_warm_cache</code> (intelligent_cache.py) — <span class="doc-comment-inline">Warm cache with keys using async loader (Fix 4).</span></li>
<li><code>unload</code> (semantic.py) — <span class="doc-comment-inline">Unload model from memory.</span></li>
<li><code>_get_context</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Extract context around a position in text.</span></li>
<li><code>checkpoint_seam_ready</code> (sprint_lifecycle.py)
<details><summary>True when checkpoint save/load is safe to call.</summary>
<div class="doc-comment">
<p>True when checkpoint save/load is safe to call.</p>
<p>Always True in this implementation — checkpoint.py exists.</p>
<p>Wiring to CheckpointManager is Sprint 1B scope.</p>
</div>
</details>
</li>
<li><code>__init__</code> (uma_budget.py)</li>
<li><code>_should_fire</code> (uma_budget.py) — <span class="doc-comment-inline">Return True if level should trigger a callback (debounce-aware).</span></li>
<li><code>size</code> (cache.py) — <span class="doc-comment-inline">Current number of entries (including potentially expired).</span></li>
<li><code>_get_max_ngrams</code> (deduplication.py) — <span class="doc-comment-inline">Get ngram cap from environment with safe fallback.</span></li>
<li><code>cleanup</code> (deduplication.py) — <span class="doc-comment-inline">Cleanup resources.</span></li>
<li><code>close</code> (deduplication.py) — <span class="doc-comment-inline">F196B: Non-blocking close of all thread pools.</span></li>
<li><code>__init__</code> (deduplication.py)</li>
<li><code>_hamming</code> (deduplication.py) — <span class="doc-comment-inline">Hamming distance — uses Rust hamming_dist if available.</span></li>
<li><code>_tokenize</code> (deduplication.py) — <span class="doc-comment-inline">Tokenization - shingle by 3 words.</span></li>
<li><code>hamming_distance</code> (deduplication.py) — <span class="doc-comment-inline">Compute Hamming distance between two SimHash fingerprints. O(1).</span></li>
<li><code>update_worker_metrics</code> (execution_optimizer.py) — <span class="doc-comment-inline">Update worker metrics with bounded storage.</span></li>
<li><code>_init_execution_pools</code> (execution_optimizer.py) — <span class="doc-comment-inline">Initialize execution pools</span></li>
<li><code>get_bounded_ops_telemetry</code> (execution_optimizer.py)
<details><summary>Return telemetry for bounded pending ops.</summary>
<div class="doc-comment">
<p>Return telemetry for bounded pending ops.</p>
<p></p>
<p>F214OPT-D: Exposes pending ops limits and throttling metrics.</p>
</div>
</details>
</li>
<li><code>export_performance_report</code> (execution_optimizer.py) — <span class="doc-comment-inline">Export detailed performance report</span></li>
<li><code>_are_p_cores_overloaded</code> (execution_optimizer.py) — <span class="doc-comment-inline">Check if P-cores are overloaded based on recent allocations</span></li>
<li><code>_calculate_p_core_ratio</code> (execution_optimizer.py) — <span class="doc-comment-inline">Calculate ratio of P-core to total allocations</span></li>
<li><code>clear</code> (execution_optimizer.py) — <span class="doc-comment-inline">Clear all cache entries.</span></li>
<li><code>__init__</code> (pattern_matcher.py)</li>
<li><code>__init__</code> (batch_dns.py)</li>
<li><code>__init__</code> (filtering.py)</li>
<li><code>contains</code> (filtering.py) — <span class="doc-comment-inline">Check if URL is in frontier.</span></li>
<li><code>add_batch</code> (filtering.py) — <span class="doc-comment-inline">Add multiple URLs to frontier.</span></li>
<li><code>_hash_prompt</code> (persistent_kv_cache.py) — <span class="doc-comment-inline">Generate 16-char hash of prompt for cache key.</span></li>
<li><code>has</code> (persistent_kv_cache.py) — <span class="doc-comment-inline">Check if cache entry exists (synchronous, for hot path).</span></li>
<li><code>_build_synonym_map</code> (query_expansion.py) — <span class="doc-comment-inline">Build combined synonym map.</span></li>
<li><code>is_windup_phase</code> (sprint_lifecycle.py)
<details><summary>Sprint 8PC: True when remaining_time &lt; 180 seconds.</summary>
<div class="doc-comment">
<p>Sprint 8PC: True when remaining_time &lt; 180 seconds.</p>
<p>Used by concurrency matrix to apply windup multiplier.</p>
</div>
</details>
</li>
<li><code>get_checkpoint_seam</code> (sprint_lifecycle.py)
<details><summary>Return a minimal checkpoint payload for this layer.</summary>
<div class="doc-comment">
<p>Return a minimal checkpoint payload for this layer.</p>
<p>Sprint 1B will wire this into CheckpointManager.save().</p>
</div>
</details>
</li>
<li><code>stop</code> (uma_budget.py) — <span class="doc-comment-inline">Stop the watchdog gracefully.</span></li>
<li><code>__init__</code> (async_helpers.py)</li>
<li><code>_wvd_delete</code> (cache.py) — <span class="doc-comment-inline">Remove key from secondary WVD if active.</span></li>
<li><code>_wvd_set</code> (cache.py) — <span class="doc-comment-inline">Add value to secondary WVD if active.</span></li>
<li><code>_is_orphaned</code> (cache.py) — <span class="doc-comment-inline">Return True if entry's refcount suggests it's only held by the cache.</span></li>
<li><code>_is_orphaned</code> (cache.py) — <span class="doc-comment-inline">Return True if entry's refcount suggests it's only held by cache.</span></li>
<li><code>deduplication_rate</code> (deduplication.py) — <span class="doc-comment-inline">Calculate deduplication rate.</span></li>
<li><code>_can_cache_embedding</code> (deduplication.py) — <span class="doc-comment-inline">Check if we can cache embedding within memory limits.</span></li>
<li><code>cleanup</code> (deduplication.py) — <span class="doc-comment-inline">Cleanup resources.</span></li>
<li><code>cleanup</code> (deduplication.py) — <span class="doc-comment-inline">Cleanup resources.</span></li>
<li><code>_get_lock</code> (deduplication.py) — <span class="doc-comment-inline">Lazy create asyncio.Lock for async context.</span></li>
<li><code>_get_lock</code> (execution_optimizer.py) — <span class="doc-comment-inline">ISSUE-014 FIX: Lazily create lock in the current event loop.</span></li>
<li><code>_optimize_execution_order</code> (execution_optimizer.py) — <span class="doc-comment-inline">Optimize task execution order based on predictions</span></li>
<li><code>__init__</code> (execution_optimizer.py)</li>
<li><code>block_rate</code> (filtering.py) — <span class="doc-comment-inline">Calculate block rate.</span></li>
<li><code>add</code> (filtering.py) — <span class="doc-comment-inline">Add URL to frontier.</span></li>
<li><code>remove</code> (filtering.py) — <span class="doc-comment-inline">Remove URL from frontier.</span></li>
<li><code>clear</code> (filtering.py) — <span class="doc-comment-inline">Clear all URLs from frontier.</span></li>
<li><code>get_stats</code> (intelligent_cache.py) — <span class="doc-comment-inline">Get cache statistics.</span></li>
<li><code>_update_hit_rate</code> (intelligent_cache.py) — <span class="doc-comment-inline">Update hit rate statistic.</span></li>
<li><code>get_instance</code> (persistent_kv_cache.py) — <span class="doc-comment-inline">Get or create the singleton PersistentKVCache instance.</span></li>
<li><code>reset_instance</code> (persistent_kv_cache.py) — <span class="doc-comment-inline">Reset singleton (for testing).</span></li>
<li><code>_update_lru</code> (persistent_kv_cache.py) — <span class="doc-comment-inline">Update LRU order on access.</span></li>
<li><code>_load_model</code> (semantic.py) — <span class="doc-comment-inline">Load ModernBERT embedder.</span></li>
<li><code>unload</code> (semantic.py) — <span class="doc-comment-inline">Unload embedding model from memory.</span></li>
<li><code>content_hash</code> (deduplication.py) — <span class="doc-comment-inline">Generate content hash.</span></li>
<li><code>_compute_character_hash</code> (deduplication.py) — <span class="doc-comment-inline">Compute character-level hash.</span></li>
<li><code>avg_latency_ms</code> (deduplication.py)</li>
<li><code>_prune_worker_metrics</code> (execution_optimizer.py) — <span class="doc-comment-inline">Prune oldest worker metrics if over cap.</span></li>
<li><code>_init_predictor</code> (execution_optimizer.py) — <span class="doc-comment-inline">Initialize execution time predictor - lazy import to avoid eager sklearn load.</span></li>
<li><code>clear_cache</code> (batch_dns.py) — <span class="doc-comment-inline">Drop all cached entries. Safe to call from sync context.</span></li>
<li><code>__init__</code> (filtering.py)</li>
<li><code>add_blocked_pattern</code> (filtering.py) — <span class="doc-comment-inline">Add regex pattern to blocklist.</span></li>
<li><code>reset_stats</code> (filtering.py) — <span class="doc-comment-inline">Reset statistics.</span></li>
<li><code>_get_storage_file</code> (filtering.py) — <span class="doc-comment-inline">Get path to storage file.</span></li>
<li><code>add</code> (filtering.py) — <span class="doc-comment-inline">Add normalized URL to frontier.</span></li>
<li><code>contains</code> (filtering.py) — <span class="doc-comment-inline">Check if normalized URL is in frontier.</span></li>
<li><code>remove</code> (filtering.py) — <span class="doc-comment-inline">Remove normalized URL from frontier.</span></li>
<li><code>_get_size</code> (intelligent_cache.py) — <span class="doc-comment-inline">Get size of entry from cache.</span></li>
<li><code>clear</code> (intelligent_cache.py) — <span class="doc-comment-inline">Clear all URLs and reset memory usage.</span></li>
<li><code>__init__</code> (semantic.py) — <span class="doc-comment-inline">Initialize LightweightTokenizer.</span></li>
<li><code>__init__</code> (query_expansion.py)</li>
<li><code>__init__</code> (query_expansion.py)</li>
<li><code>get_instance</code> (sprint_lifecycle.py)</li>
<li><code>on_emergency</code> (sprint_lifecycle.py)</li>
<li><code>track_task</code> (sprint_lifecycle.py) — <span class="doc-comment-inline">Add task to internal registry with done-callback that logs exceptions.</span></li>
<li><code>keys</code> (cache.py) — <span class="doc-comment-inline">Return list of keys, excluding expired. Thread-safe.</span></li>
<li><code>values</code> (cache.py) — <span class="doc-comment-inline">Return list of values, excluding expired. Thread-safe.</span></li>
<li><code>capacity</code> (cache.py) — <span class="doc-comment-inline">Maximum number of entries (maxsize).</span></li>
<li><code>capacity</code> (cache.py) — <span class="doc-comment-inline">Maximum number of entries (maxsize).</span></li>
<li><code>capacity</code> (cache.py) — <span class="doc-comment-inline">Maximum number of entries (maxsize).</span></li>
<li><code>_gens</code> (cache.py) — <span class="doc-comment-inline">Return generations in eviction order (oldest first).</span></li>
<li><code>__init__</code> (deduplication.py)</li>
<li><code>find_duplicates</code> (deduplication.py) — <span class="doc-comment-inline">Find duplicates for an item among candidates.</span></li>
<li><code>cleanup</code> (deduplication.py) — <span class="doc-comment-inline">Cleanup resources.</span></li>
<li><code>close</code> (deduplication.py) — <span class="doc-comment-inline">F196B: Non-blocking close of thread pool.</span></li>
<li><code>close</code> (deduplication.py) — <span class="doc-comment-inline">F196B: Non-blocking close of thread pool.</span></li>
<li><code>_get_stop_words</code> (deduplication.py) — <span class="doc-comment-inline">Get common stop words.</span></li>
<li><code>close</code> (deduplication.py) — <span class="doc-comment-inline">F196B: Non-blocking close of thread pool.</span></li>
<li><code>get_statistics</code> (deduplication.py) — <span class="doc-comment-inline">Get current statistics.</span></li>
<li><code>to_dict</code> (deduplication.py) — <span class="doc-comment-inline">Serialize to dict for persistence.</span></li>
<li><code>_bucket_key</code> (deduplication.py) — <span class="doc-comment-inline">Top-K bits as bucket key.</span></li>
<li><code>clear</code> (deduplication.py)</li>
<li><code>is_near_duplicate</code> (deduplication.py) — <span class="doc-comment-inline">Check if two hashes are near-duplicates (Hamming &lt;= threshold).</span></li>
<li><code>start_monitoring</code> (execution_optimizer.py) — <span class="doc-comment-inline">Start the background memory monitor.</span></li>
<li><code>acquire</code> (execution_optimizer.py) — <span class="doc-comment-inline">Acquire a concurrency slot. Blocks if limit reached.</span></li>
<li><code>release</code> (execution_optimizer.py) — <span class="doc-comment-inline">Release a concurrency slot.</span></li>
<li><code>initialize</code> (execution_optimizer.py) — <span class="doc-comment-inline">Initialize async components like concurrency controller.</span></li>
<li><code>get_worker_loads</code> (execution_optimizer.py) — <span class="doc-comment-inline">Get current worker loads</span></li>
<li><code>update_worker_load</code> (execution_optimizer.py) — <span class="doc-comment-inline">Update worker load</span></li>
<li><code>get_current_resources</code> (execution_optimizer.py) — <span class="doc-comment-inline">Get current system resources</span></li>
<li><code>get_core_statistics</code> (execution_optimizer.py) — <span class="doc-comment-inline">Get core allocation statistics</span></li>
<li><code>get_active_count</code> (execution_optimizer.py) — <span class="doc-comment-inline">Get number of active tasks.</span></li>
<li><code>pattern_count</code> (pattern_matcher.py) — <span class="doc-comment-inline">Return number of configured patterns. O(1).</span></li>
<li><code>cache_size</code> (batch_dns.py) — <span class="doc-comment-inline">Return current LRU cache size (for tests + telemetry).</span></li>
<li><code>neg_cache_size</code> (batch_dns.py) — <span class="doc-comment-inline">Return current negative cache size (for tests + telemetry).</span></li>
<li><code>stats</code> (batch_dns.py) — <span class="doc-comment-inline">Return bounded telemetry snapshot.</span></li>
<li><code>reset_stats</code> (batch_dns.py) — <span class="doc-comment-inline">Reset telemetry counters (does not clear the cache).</span></li>
<li><code>is_empty</code> (batch_dns.py) — <span class="doc-comment-inline">Return True if cache is empty.</span></li>
<li><code>_is_disabled</code> (batch_dns.py) — <span class="doc-comment-inline">Return True if the env-var opt-out is set.</span></li>
<li><code>add_domain</code> (filtering.py) — <span class="doc-comment-inline">Add blocked domain.</span></li>
<li><code>add_url</code> (filtering.py) — <span class="doc-comment-inline">Add blocked URL.</span></li>
<li><code>size</code> (filtering.py) — <span class="doc-comment-inline">Get filter size.</span></li>
<li><code>add</code> (filtering.py) — <span class="doc-comment-inline">Add item to filter.</span></li>
<li><code>is_available</code> (filtering.py) — <span class="doc-comment-inline">Check if filter is available.</span></li>
<li><code>check_urls_batch</code> (filtering.py) — <span class="doc-comment-inline">Check multiple URLs.</span></li>
<li><code>get_stats</code> (filtering.py) — <span class="doc-comment-inline">Get filter statistics.</span></li>
<li><code>is_bff_available</code> (filtering.py) — <span class="doc-comment-inline">Check if Binary Fuse Filter is available.</span></li>
<li><code>_init_fallback</code> (filtering.py) — <span class="doc-comment-inline">Initialize fallback using set.</span></li>
<li><code>get_stats</code> (filtering.py) — <span class="doc-comment-inline">Get frontier statistics.</span></li>
<li><code>get_size</code> (filtering.py) — <span class="doc-comment-inline">Get current number of URLs in frontier.</span></li>
<li><code>get_stats</code> (filtering.py) — <span class="doc-comment-inline">Get frontier statistics.</span></li>
<li><code>get_size</code> (filtering.py) — <span class="doc-comment-inline">Get current number of URLs in frontier.</span></li>
<li><code>iter_urls</code> (filtering.py) — <span class="doc-comment-inline">Iterate over all URLs in frontier.</span></li>
<li><code>get_all_urls</code> (filtering.py) — <span class="doc-comment-inline">Get all URLs in frontier.</span></li>
<li><code>__init__</code> (filtering.py)</li>
<li><code>check_batch</code> (filtering.py) — <span class="doc-comment-inline">Check multiple URLs against frontier.</span></li>
<li><code>_estimate_size</code> (intelligent_cache.py) — <span class="doc-comment-inline">Estimate size of value in bytes using sys.getsizeof (Fix 4).</span></li>
<li><code>__contains__</code> (intelligent_cache.py) — <span class="doc-comment-inline">Check if URL is in set.</span></li>
<li><code>__len__</code> (intelligent_cache.py) — <span class="doc-comment-inline">Get number of URLs in set.</span></li>
<li><code>__iter__</code> (intelligent_cache.py) — <span class="doc-comment-inline">Iterate over URLs.</span></li>
<li><code>get_memory_usage_mb</code> (intelligent_cache.py) — <span class="doc-comment-inline">Get current memory usage in MB.</span></li>
<li><code>get_statistics</code> (intelligent_cache.py) — <span class="doc-comment-inline">Get URL set statistics.</span></li>
<li><code>encode</code> (persistent_kv_cache.py)</li>
<li><code>decode</code> (persistent_kv_cache.py)</li>
<li><code>close</code> (persistent_kv_cache.py) — <span class="doc-comment-inline">Close the cache manager.</span></li>
<li><code>__init__</code> (semantic.py) — <span class="doc-comment-inline">Initialize KeywordFilter.</span></li>
<li><code>_tokenize</code> (query_expansion.py) — <span class="doc-comment-inline">Tokenize query into words</span></li>
<li><code>get_statistics</code> (query_expansion.py) — <span class="doc-comment-inline">Get expander statistics</span></li>
<li><code>expand</code> (query_expansion.py) — <span class="doc-comment-inline">Expand query into multiple variations.</span></li>
<li><code>strategy_type</code> (query_expansion.py) — <span class="doc-comment-inline">Get strategy type identifier.</span></li>
<li><code>__init__</code> (query_expansion.py)</li>
<li><code>__init__</code> (query_expansion.py)</li>
<li><code>has_findings</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Check if any findings were detected.</span></li>
<li><code>get_finding_count</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Get total number of findings.</span></li>
<li><code>get_summary</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Get summary of analysis results.</span></li>
<li><code>__enter__</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Context manager entry.</span></li>
<li><code>is_active</code> (sprint_lifecycle.py) — <span class="doc-comment-inline">True when in ACTIVE state (normal operations).</span></li>
<li><code>is_winding_down</code> (sprint_lifecycle.py) — <span class="doc-comment-inline">True when in WINDUP, EXPORT, or TEARDOWN states.</span></li>
<li><code>shutdown_requested</code> (sprint_lifecycle.py) — <span class="doc-comment-inline">True when SIGINT/SIGTERM has been received.</span></li>
<li><code>windup_fired</code> (sprint_lifecycle.py) — <span class="doc-comment-inline">True when wind-down has been triggered (always True once fired).</span></li>
<li><code>on_critical</code> (sprint_lifecycle.py)</li>
<li><code>set_windup_hook</code> (sprint_lifecycle.py) — <span class="doc-comment-inline">Set callback to run when wind-down is triggered.</span></li>
<li><code>set_export_hook</code> (sprint_lifecycle.py) — <span class="doc-comment-inline">Set callback to run when export phase begins.</span></li>
<li><code>set_teardown_hook</code> (sprint_lifecycle.py) — <span class="doc-comment-inline">Set callback to run when teardown is triggered.</span></li>
<li><code>is_running</code> (uma_budget.py) — <span class="doc-comment-inline">True if the watchdog loop is active.</span></li>
<li><code>interval</code> (uma_budget.py) — <span class="doc-comment-inline">Return the polling interval in seconds.</span></li>
<li><code>last_fired_level</code> (uma_budget.py) — <span class="doc-comment-inline">Return the last level that triggered a callback.</span></li>
<li><code>__init__</code> (async_helpers.py)</li>
<li><code>__call__</code> (async_helpers.py)</li>
<li><code>__setitem__</code> (cache.py)</li>
<li><code>__len__</code> (cache.py)</li>
<li><code>__len__</code> (cache.py)</li>
<li><code>__len__</code> (cache.py)</li>
<li><code>__len__</code> (cache.py)</li>
<li><code>__len__</code> (cache.py)</li>
<li><code>_get_storage_path</code> (deduplication.py)</li>
<li><code>get_all_stats</code> (deduplication.py)</li>
<li><code>__len__</code> (deduplication.py)</li>
<li><code>__init__</code> (execution_optimizer.py)</li>
<li><code>__init__</code> (execution_optimizer.py)</li>
<li><code>__repr__</code> (pattern_matcher.py)</li>
<li><code>__init__</code> (semantic.py)</li>
<li><code>__init__</code> (semantic.py)</li>
<li><code>strategy_type</code> (query_expansion.py)</li>
<li><code>__init__</code> (query_expansion.py)</li>
<li><code>strategy_type</code> (query_expansion.py)</li>
<li><code>strategy_type</code> (query_expansion.py)</li>
<li><code>__init__</code> (query_expansion.py)</li>
<li><code>state</code> (sprint_lifecycle.py)</li>
<li><code>sprint_duration</code> (sprint_lifecycle.py)</li>
<li><code>on_warn</code> (sprint_lifecycle.py)</li>
<li><code>on_warn</code> (uma_budget.py) — <span class="doc-comment-inline">Called when UMA enters WARN state (&gt;= 6.0 GB).</span></li>
<li><code>on_critical</code> (uma_budget.py) — <span class="doc-comment-inline">Called when UMA enters CRITICAL state (&gt;= 6.5 GB).</span></li>
<li><code>on_emergency</code> (uma_budget.py) — <span class="doc-comment-inline">Called when UMA enters EMERGENCY state (&gt;= 7.0 GB).</span></li>
</ul>
</details>

<details><summary><strong>Constant</strong> (153)</summary>
<ul>
<li><code>_BOOTSTRAP_PATTERNS_V3</code> (pattern_matcher.py)</li>
<li><code>_PATTERN_PACK_METADATA</code> (pattern_matcher.py)</li>
<li><code>DEFAULT_PREWARM_DOMAINS</code> (batch_dns.py)</li>
<li><code>_CONTENT_TYPES</code> (hydration_extractor.py)</li>
<li><code>_SEED_REGISTRY</code> (pattern_matcher.py)</li>
<li><code>SOURCE_FAMILY_ENUM</code> (flow_trace.py)</li>
<li><code>_RE_DOGE_ADDR</code> (pattern_matcher.py)</li>
<li><code>ACQUISITION_MODE_ENUM</code> (flow_trace.py)</li>
<li><code>CHALLENGE_TYPE_ENUM</code> (flow_trace.py)</li>
<li><code>_RE_BODY_TAGS</code> (hydration_extractor.py)</li>
<li><code>_RE_SKIP_TAGS</code> (hydration_extractor.py)</li>
<li><code>_RE_NEXT_DATA</code> (hydration_extractor.py)</li>
<li><code>_RE_NUXT_DATA</code> (hydration_extractor.py)</li>
<li><code>_RE_NUXT_GLOBAL</code> (hydration_extractor.py)</li>
<li><code>_RE_INITIAL_STATE</code> (hydration_extractor.py)</li>
<li><code>_RE_PRELOADED_STATE</code> (hydration_extractor.py)</li>
<li><code>_RE_APOLLO_STATE</code> (hydration_extractor.py)</li>
<li><code>_RE_JSON_LD</code> (hydration_extractor.py)</li>
<li><code>_RE_CANONICAL</code> (hydration_extractor.py)</li>
<li><code>_RE_RSS</code> (hydration_extractor.py)</li>
<li><code>_RE_ATOM</code> (hydration_extractor.py)</li>
<li><code>_RE_OG_TITLE</code> (hydration_extractor.py)</li>
<li><code>_RE_OG_DESC</code> (hydration_extractor.py)</li>
<li><code>_RE_META_DESC</code> (hydration_extractor.py)</li>
<li><code>_RE_TITLE_TAG</code> (hydration_extractor.py)</li>
<li><code>_RE_OG_IMAGE</code> (hydration_extractor.py)</li>
<li><code>_RE_OG_URL</code> (hydration_extractor.py)</li>
<li><code>_RE_ARTICLE_PUBLISHED</code> (hydration_extractor.py)</li>
<li><code>_RE_ONION_V3</code> (pattern_matcher.py)</li>
<li><code>_RE_SHA256</code> (pattern_matcher.py)</li>
<li><code>_RE_MD5</code> (pattern_matcher.py)</li>
<li><code>_RE_SHA1</code> (pattern_matcher.py)</li>
<li><code>_RE_MISP_UUID</code> (pattern_matcher.py)</li>
<li><code>CHALLENGE_OUTCOME_ENUM</code> (flow_trace.py)</li>
<li><code>T</code> (async_helpers.py)</li>
<li><code>_PY_312_PLUS</code> (async_helpers.py)</li>
<li><code>_EAGER_START_SUPPORTED</code> (async_helpers.py)</li>
<li><code>_T</code> (async_helpers.py)</li>
<li><code>_SAFE_GATHER_SAMPLE_CAP</code> (async_helpers.py)</li>
<li><code>K</code> (cache.py)</li>
<li><code>V</code> (cache.py)</li>
<li><code>_TOKEN_HASH_CACHE</code> (deduplication.py)</li>
<li><code>_TOKEN_HASH_CACHE_LOCK</code> (deduplication.py)</li>
<li><code>_MAX_TOKEN_CACHE</code> (deduplication.py)</li>
<li><code>PSUTIL_AVAILABLE</code> (execution_optimizer.py)</li>
<li><code>_RUST_ACO_AVAILABLE</code> (pattern_matcher.py)</li>
<li><code>_BOOTSTRAP_PATTERNS</code> (pattern_matcher.py)</li>
<li><code>_BOOTSTRAP_PACK_VERSION</code> (pattern_matcher.py)</li>
<li><code>_RE_CVE</code> (pattern_matcher.py)</li>
<li><code>_RE_GHSA</code> (pattern_matcher.py)</li>
<li><code>_RE_BTC_LEGACY</code> (pattern_matcher.py)</li>
<li><code>_RE_BTC_BECH32</code> (pattern_matcher.py)</li>
<li><code>_RE_ETH_ADDR</code> (pattern_matcher.py)</li>
<li><code>_RE_TELEGRAM</code> (pattern_matcher.py)</li>
<li><code>_RE_ONION_V3</code> (pattern_matcher.py)</li>
<li><code>_RE_XMR_ADDR</code> (pattern_matcher.py)</li>
<li><code>_RE_I2P_ADDR</code> (pattern_matcher.py)</li>
<li><code>_RE_PGP_FP</code> (pattern_matcher.py)</li>
<li><code>_RE_IPFS_CID</code> (pattern_matcher.py)</li>
<li><code>_RE_USDT_TRC20</code> (pattern_matcher.py)</li>
<li><code>_RE_LTC_ADDR</code> (pattern_matcher.py)</li>
<li><code>_RE_ETH_CONTRACT</code> (pattern_matcher.py)</li>
<li><code>_RE_AWS_KEY_ID</code> (pattern_matcher.py)</li>
<li><code>_RE_GOOGLE_API_KEY</code> (pattern_matcher.py)</li>
<li><code>_RE_STRIPE_SK</code> (pattern_matcher.py)</li>
<li><code>_RE_SLACK_TOKEN</code> (pattern_matcher.py)</li>
<li><code>_PATTERN_LABEL_INDEX</code> (pattern_matcher.py)</li>
<li><code>TRACE_ENABLED</code> (flow_trace.py)</li>
<li><code>TRACE_SAMPLE_RATE</code> (flow_trace.py)</li>
<li><code>TRACE_MAX_EVENTS</code> (flow_trace.py)</li>
<li><code>_MAX_BUFFER_SIZE</code> (flow_trace.py)</li>
<li><code>MLX_AVAILABLE</code> (_core.py)</li>
<li><code>_MISSING</code> (_core.py)</li>
<li><code>_METAL_WIRED_LIMIT_BYTES</code> (_core.py)</li>
<li><code>_METAL_CACHE_LIMIT_BYTES</code> (_core.py)</li>
<li><code>_EMERGENCY_FLOOR_BYTES</code> (_core.py)</li>
<li><code>_METAL_CACHE_LIMIT_BYTES</code> (_core.py)</li>
<li><code>_METAL_WIRED_LIMIT_BYTES</code> (_core.py)</li>
<li><code>_DEBOUNCE_SECONDS</code> (_core.py)</li>
<li><code>_MLX_CACHE</code> (_core.py)</li>
<li><code>_MLX_CACHE_MAX</code> (_core.py)</li>
<li><code>_MLX_CACHE_LIMIT</code> (_core.py)</li>
<li><code>_MLX_WIRED_LIMIT</code> (_core.py)</li>
<li><code>_MLX_SEMAPHORE</code> (_core.py)</li>
<li><code>_MLX_SEMAPHORE_INIT</code> (_core.py)</li>
<li><code>_CACHE_HITS</code> (_core.py)</li>
<li><code>_CACHE_MISSES</code> (_core.py)</li>
<li><code>_MIN_EVAL_INTERVAL</code> (_core.py)</li>
<li><code>MAX_HTML_BYTES</code> (hydration_extractor.py)</li>
<li><code>MAX_EXTRACTED_TEXT</code> (hydration_extractor.py)</li>
<li><code>MAX_JSON_LD_BLOCKS</code> (hydration_extractor.py)</li>
<li><code>MAX_JSON_DEPTH</code> (hydration_extractor.py)</li>
<li><code>MAX_SCRIPT_LEN</code> (hydration_extractor.py)</li>
<li><code>MAX_TITLE_LEN</code> (hydration_extractor.py)</li>
<li><code>MAX_METADATA_LEN</code> (hydration_extractor.py)</li>
<li><code>MAX_CANDIDATE_LEN</code> (hydration_extractor.py)</li>
<li><code>_REASON_SUFFICIENT_NEXT</code> (hydration_extractor.py)</li>
<li><code>_REASON_SUFFICIENT_NUXT</code> (hydration_extractor.py)</li>
<li><code>_REASON_SUFFICIENT_JSON_LD</code> (hydration_extractor.py)</li>
<li><code>_REASON_SUFFICIENT_METADATA</code> (hydration_extractor.py)</li>
<li><code>_REASON_FOUND_INSUFFICIENT</code> (hydration_extractor.py)</li>
<li><code>_REASON_NONE</code> (hydration_extractor.py)</li>
<li><code>_MIN_TITLE_LEN</code> (hydration_extractor.py)</li>
<li><code>_MIN_BODY_LEN</code> (hydration_extractor.py)</li>
<li><code>DEFAULT_CACHE_MAX</code> (batch_dns.py)</li>
<li><code>DEFAULT_NEG_CACHE_MAX</code> (batch_dns.py)</li>
<li><code>DEFAULT_TTL_S</code> (batch_dns.py)</li>
<li><code>DEFAULT_NEG_TTL_S</code> (batch_dns.py)</li>
<li><code>DEFAULT_CONCURRENCY</code> (batch_dns.py)</li>
<li><code>DEFAULT_PER_HOST_TIMEOUT_S</code> (batch_dns.py)</li>
<li><code>ENV_OPT_OUT</code> (batch_dns.py)</li>
<li><code>HAS_AIODNS</code> (batch_dns.py)</li>
<li><code>MLX_AVAILABLE</code> (mlx_cache.py)</li>
<li><code>_MLX_CACHE</code> (mlx_cache.py)</li>
<li><code>_MLX_CACHE_MAX</code> (mlx_cache.py)</li>
<li><code>_MLX_CACHE_LOCK</code> (mlx_cache.py)</li>
<li><code>_MLX_SEMAPHORE</code> (mlx_cache.py)</li>
<li><code>_MLX_EVICT_LOCK</code> (mlx_cache.py)</li>
<li><code>_CACHE_HITS</code> (mlx_cache.py)</li>
<li><code>_CACHE_MISSES</code> (mlx_cache.py)</li>
<li><code>_METAL_CACHE_LIMIT_BYTES</code> (mlx_cache.py)</li>
<li><code>_METAL_WIRED_LIMIT_BYTES</code> (mlx_cache.py)</li>
<li><code>_METAL_CACHE_EMERGENCY_FLOOR_BYTES</code> (mlx_cache.py)</li>
<li><code>_MLX_CACHE_LIMIT</code> (mlx_cache.py)</li>
<li><code>_MLX_WIRED_LIMIT</code> (mlx_cache.py)</li>
<li><code>_MLX_METAL_LIMITS_CONFIGURED</code> (mlx_cache.py)</li>
<li><code>_MLX_METAL_LIMITS_LOCK</code> (mlx_cache.py)</li>
<li><code>_MLX_INITIALIZED</code> (mlx_cache.py)</li>
<li><code>_MLX_AVAILABLE</code> (intelligent_cache.py)</li>
<li><code>_MLX_CORE</code> (intelligent_cache.py)</li>
<li><code>_DEFAULT_CACHE_DIR</code> (persistent_kv_cache.py)</li>
<li><code>_CACHE_SUBDIR</code> (persistent_kv_cache.py)</li>
<li><code>_META_LMDB</code> (persistent_kv_cache.py)</li>
<li><code>_LMDB_MAP_SIZE</code> (persistent_kv_cache.py)</li>
<li><code>_MAX_SIZE_GB</code> (persistent_kv_cache.py)</li>
<li><code>_MAX_ENTRIES</code> (persistent_kv_cache.py)</li>
<li><code>_ENTRY_TTL_S</code> (persistent_kv_cache.py)</li>
<li><code>_UMA_TOTAL_MB</code> (uma_budget.py)</li>
<li><code>_WARN_THRESHOLD_MB</code> (uma_budget.py)</li>
<li><code>_CRITICAL_THRESHOLD_MB</code> (uma_budget.py)</li>
<li><code>_EMERGENCY_THRESHOLD_MB</code> (uma_budget.py)</li>
<li><code>UMA_WARN_GIB</code> (uma_budget.py)</li>
<li><code>UMA_CRITICAL_GIB</code> (uma_budget.py)</li>
<li><code>UMA_EMERGENCY_GIB</code> (uma_budget.py)</li>
<li><code>M1_FETCH_SOFT_CEILING_GB</code> (uma_budget.py)</li>
<li><code>GENERAL_HIGH_WATER_RATIO</code> (uma_budget.py)</li>
<li><code>MAX_L2_CACHE_SIZE_MB</code> (uma_budget.py)</li>
<li><code>_DEFAULT_ENCODER</code> (msgspec_json.py)</li>
<li><code>_DEFAULT_DECODER</code> (msgspec_json.py)</li>
<li><code>_POOL_MAX</code> (msgspec_json.py)</li>
<li><code>_RAYON_AVAILABLE</code> (rayon_pool.py)</li>
<li><code>T</code> (rayon_pool.py)</li>
<li><code>F</code> (rayon_pool.py)</li>
</ul>
</details>



## Metrics

| Metric | Value |
|---|---|
| Files | 130 |
| Total lines | 35916 |
| Avg lines/file | 276 |
| Languages | Python |
| Outgoing deps | 0 |
| Incoming deps | 12 |
| Tier | 1 |

