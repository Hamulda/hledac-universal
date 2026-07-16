+++
title = "rust_extensions/src/data/"
weight = 29
description = "<think> Let me analyze the structural context for the `rust_extensions/src/data` module and create a module overview.  Key observations: 1. This is a Rust module with 3 files (548 total lines) 2. It h..."

[extra]
tier = 2
file_count = 3
total_lines = 548
languages = "Rust"
parent_path = "rust_extensions"
+++

<think>
Let me analyze the structural context for the `rust_extensions/src/data` module and create a module overview.

Key observations:
1. This is a Rust module with 3 files (548 total lines)
2. It has NO outgoing dependencies and NO incoming dependencies (isolated module)
3. It has two main areas of functionality:
 - Graph traversal operations (graph_traverse.rs - 225 lines)
 - Cache management (cache.rs - 203 lines)
 - Database connection handling (connection.rs - 120 lines)

4. The module appears to be a leaf module in the architecture - no dependents, no dependencies
5. Key abstractions:
 - TinyLFU LRU cache for graph traversal results
 - Graph traversal functions (single and batch)
 - DuckDB connection management

6. This module provides:
 - Caching with TinyLFU admission policy
 - Graph traversal (single and batch) for IOC (Indicators of Compromise) data
 - DuckDB connection management with M1 optimizations

7. The key symbols that are heavily referenced (though not by other modules in this codebase):
 - Cache Entry - referenced in 15 files (but these are Python files referencing the Python-exposed version)
 - graph_stats - referenced in 15 files
 - batch_graph_traverse - referenced in 6 files

Wait, the "Key Symbols" section shows references from Python files, but the "Dependents" section shows "No incoming dependencies detected". This seems contradictory. Let me re-read...

Ah, I see. The Key Symbols show "referenced in X files" which are Python files that use these functions. But the Dependents section says "No incoming dependencies detected" - this might mean the module itself doesn't have direct Rust dependents in the codebase, but it does expose functions to Python.

Actually, looking more carefully, this module is in `rust_extensions/src/data` - so it's a Rust extension that exposes functions to Python. 

This module functions as a self-contained performance layer combining DuckDB connectivity with graph analytics and intelligent caching. The architecture isolates computation-heavy operations in Rust for speed while bridging to Python, with safety boundaries preventing uncontrolled memory growth through TinyLFU and batch processing constraints. Despite appearing standalone, the extensive cross-referencing suggests it's a foundational component for graph-based operations.
</think>

This module provides high-performance data access primitives for graph-based operations, combining DuckDB database connectivity with a TinyLFU + LRU hybrid cache and graph traversal capabilities. It operates as a leaf-level performance layer, exposing core graph traversal logic, connection management, and caching to Python consumers while maintaining no internal Rust dependencies. The module enforces bounded memory usage through TinyLFU admission control and batch size limits (MAX_BATCH_SIZE), which prevents out-of-memory conditions during graph queries. With 548 lines across three focused files, the module maintains a compact footprint appropriate for a specialized utility component. Connection pooling is optimized for M1 Mac environments with 8GB RAM, using read-only connections and single-threaded pragmas. Graph traversal supports both single-root and batch operations, returning connected IOCs while capping results per root value. The lack of incoming Rust dependencies suggests this module serves as a boundary between native performance code and Python, acting as an operational core rather than a structural hub in the architecture.

## Structure

| Language | Files |
|---|---|
| Rust | 3 |

### Largest Files

- `graph_traverse.rs` (225 lines)
- `cache.rs` (203 lines)
- `connection.rs` (120 lines)


## Dependencies

No outgoing dependencies detected.

## Dependents

No incoming dependencies detected.

## Key Symbols

<p><strong>Key definitions:</strong></p>
<ul>
<li>
<p><code>CacheEntry</code> (Struct) in cache.rs — referenced in 15 files</p>
<details><summary>Cache entry with frequency count for TinyLFU.</summary></details>
<ul><li class="ref-list">Referenced by: __init__.py, _catalog.py, cache.py, communication_layer.py, conditional_cache.py +8 more</li></ul>
</li>
<li>
<p><code>graph_stats</code> (Function) in graph_traverse.rs — referenced in 15 files</p>
<details><summary>Get graph statistics from DuckDB.</summary></details>
<ul><li class="ref-list">Referenced by: backend_protocol.py, duckdb_store.py, duckdb_subprocess_adapter.py, graph_adapter.py, graph_protocol.py +8 more</li></ul>
</li>
<li>
<p><code>batch_graph_traverse</code> (Function) in graph_traverse.rs — referenced in 6 files</p>
<details><summary>Traverse graph from a list of root values and return connected IOCs.</summary></details>
<ul><li class="ref-list">Referenced by: lib.rs, misc.py, prefetch_oracle_integration.py, test_rust_backend.py</li></ul>
</li>
<li>
<p><code>TraversalResult</code> (Struct) in graph_traverse.rs — referenced in 3 files</p>
<details><summary>Result for a single traversal from one root IOC to its connected nodes.</summary></details>
<ul><li class="ref-list">Referenced by: cache.rs</li></ul>
</li>
<li>
<p><code>traverse_single</code> (Function) in graph_traverse.rs — referenced in 3 files</p>
<details><summary>Run a single traversal query for one root value.</summary></details>
<ul><li class="ref-list">Referenced by: cache.rs</li></ul>
</li>
</ul>

<details><summary><strong>Function</strong> (28)</summary>
<ul>
<li><code>traverse_single</code> (graph_traverse.rs) — <span class="doc-comment-inline">Run a single traversal query for one root value.</span></li>
<li><code>batch_graph_traverse</code> (graph_traverse.rs) — <span class="doc-comment-inline">Traverse graph from a list of root values and return connected IOCs.</span></li>
<li><code>graph_stats</code> (graph_traverse.rs) — <span class="doc-comment-inline">Get graph statistics from DuckDB.</span></li>
<li><code>put</code> (cache.rs) — <span class="doc-comment-inline">Put a value into cache with TinyLFU admission.</span></li>
<li><code>execute_query</code> (connection.rs) — <span class="doc-comment-inline">Execute a query and return results as a vector of rows (each row is Vec&lt;String&gt;).</span></li>
<li><code>format_value_ref</code> (connection.rs)
<details><summary>Format a DuckDB ValueRef as a string for Python consumption.</summary>
<div class="doc-comment">
<p>Format a DuckDB ValueRef as a string for Python consumption.</p>
<p>Uses Debug format for all non-trivial variants for forward compatibility</p>
<p>across DuckDB versions.</p>
</div>
</details>
</li>
<li><code>evict_one</code> (cache.rs) — <span class="doc-comment-inline">Evict one entry using TinyLFU + LRU hybrid.</span></li>
<li><code>get_thread_connection</code> (connection.rs)
<details><summary>Open a DuckDB connection with M1 8GB optimizations.</summary>
<div class="doc-comment">
<p>Open a DuckDB connection with M1 8GB optimizations.</p>
<p>Connection is opened read-only with PRAGMA threads=1.</p>
</div>
</details>
</li>
<li><code>test_connection_reuse</code> (graph_traverse.rs)</li>
<li><code>test_cache_eviction</code> (cache.rs)</li>
<li><code>get</code> (cache.rs) — <span class="doc-comment-inline">Get a value from cache, updating access metadata.</span></li>
<li><code>duckdb_health_check</code> (connection.rs)</li>
<li><code>new</code> (cache.rs)</li>
<li><code>test_cache_basic</code> (cache.rs)</li>
<li><code>test_cache_clear</code> (cache.rs)</li>
<li><code>register_functions</code> (graph_traverse.rs) — <span class="doc-comment-inline">Register graph_traverse functions with Python module.</span></li>
<li><code>clear</code> (cache.rs) — <span class="doc-comment-inline">Clear all entries.</span></li>
<li><code>new</code> (cache.rs)</li>
<li><code>duckdb_open_connection</code> (connection.rs)</li>
<li><code>register_functions</code> (connection.rs) — <span class="doc-comment-inline">Register connection functions with Python module.</span></li>
<li><code>test_health_check</code> (connection.rs)</li>
<li><code>register_functions</code> (cache.rs) — <span class="doc-comment-inline">Register cache functions with Python module.</span></li>
<li><code>should_evict</code> (cache.rs) — <span class="doc-comment-inline">Check if we need to evict to make room.</span></li>
<li><code>stats</code> (cache.rs) — <span class="doc-comment-inline">Get cache statistics.</span></li>
<li><code>get</code> (cache.rs) — <span class="doc-comment-inline">Get a value from cache.</span></li>
<li><code>put</code> (cache.rs) — <span class="doc-comment-inline">Put a value into cache.</span></li>
<li><code>clear</code> (cache.rs) — <span class="doc-comment-inline">Clear the cache.</span></li>
<li><code>stats</code> (cache.rs) — <span class="doc-comment-inline">Get cache statistics (entries, current_bytes, max_bytes).</span></li>
</ul>
</details>

<details><summary><strong>Struct</strong> (4)</summary>
<ul>
<li><code>GraphLRUCache</code> (cache.rs) — <span class="doc-comment-inline">TinyLFU LRU cache with bounded memory.</span></li>
<li><code>TraversalResult</code> (graph_traverse.rs) — <span class="doc-comment-inline">Result for a single traversal from one root IOC to its connected nodes.</span></li>
<li><code>CacheEntry</code> (cache.rs) — <span class="doc-comment-inline">Cache entry with frequency count for TinyLFU.</span></li>
<li><code>PyGraphLRUCache</code> (cache.rs) — <span class="doc-comment-inline">Python-accessible LRU cache wrapper.</span></li>
</ul>
</details>

<details><summary><strong>Method</strong> (12)</summary>
<ul>
<li><code>put</code> (cache.rs) — <span class="doc-comment-inline">Put a value into cache with TinyLFU admission.</span></li>
<li><code>evict_one</code> (cache.rs) — <span class="doc-comment-inline">Evict one entry using TinyLFU + LRU hybrid.</span></li>
<li><code>get</code> (cache.rs) — <span class="doc-comment-inline">Get a value from cache, updating access metadata.</span></li>
<li><code>new</code> (cache.rs)</li>
<li><code>clear</code> (cache.rs) — <span class="doc-comment-inline">Clear all entries.</span></li>
<li><code>new</code> (cache.rs)</li>
<li><code>should_evict</code> (cache.rs) — <span class="doc-comment-inline">Check if we need to evict to make room.</span></li>
<li><code>stats</code> (cache.rs) — <span class="doc-comment-inline">Get cache statistics.</span></li>
<li><code>get</code> (cache.rs) — <span class="doc-comment-inline">Get a value from cache.</span></li>
<li><code>put</code> (cache.rs) — <span class="doc-comment-inline">Put a value into cache.</span></li>
<li><code>clear</code> (cache.rs) — <span class="doc-comment-inline">Clear the cache.</span></li>
<li><code>stats</code> (cache.rs) — <span class="doc-comment-inline">Get cache statistics (entries, current_bytes, max_bytes).</span></li>
</ul>
</details>

<details><summary><strong>Constant</strong> (2)</summary>
<ul>
<li><code>MAX_RESULTS_PER_ROOT</code> (graph_traverse.rs) — <span class="doc-comment-inline">Maximum number of results per root IOC.</span></li>
<li><code>MAX_BATCH_SIZE</code> (graph_traverse.rs) — <span class="doc-comment-inline">Maximum batch size to prevent OOM.</span></li>
</ul>
</details>

<details><summary><strong>Module</strong> (3)</summary>
<ul>
<li><code>tests</code> (cache.rs)</li>
<li><code>tests</code> (graph_traverse.rs)</li>
<li><code>tests</code> (connection.rs)</li>
</ul>
</details>

<details><summary><strong>Attribute</strong> (16)</summary>
<ul>
<li><code>derive</code> (graph_traverse.rs) — <span class="doc-comment-inline">Result for a single traversal from one root IOC to its connected nodes.</span></li>
<li><code>pyfunction</code> (graph_traverse.rs) — <span class="doc-comment-inline">Traverse graph from a list of root values and return connected IOCs.</span></li>
<li><code>pyfunction</code> (graph_traverse.rs) — <span class="doc-comment-inline">Get graph statistics from DuckDB.</span></li>
<li><code>cfg</code> (graph_traverse.rs)</li>
<li><code>test</code> (graph_traverse.rs)</li>
<li><code>pyclass</code> (cache.rs) — <span class="doc-comment-inline">Python-accessible LRU cache wrapper.</span></li>
<li><code>pymethods</code> (cache.rs)</li>
<li><code>new</code> (cache.rs)</li>
<li><code>cfg</code> (cache.rs)</li>
<li><code>test</code> (cache.rs)</li>
<li><code>test</code> (cache.rs)</li>
<li><code>test</code> (cache.rs)</li>
<li><code>pyfunction</code> (connection.rs)</li>
<li><code>pyfunction</code> (connection.rs)</li>
<li><code>cfg</code> (connection.rs)</li>
<li><code>test</code> (connection.rs)</li>
</ul>
</details>



## Metrics

| Metric | Value |
|---|---|
| Files | 3 |
| Total lines | 548 |
| Avg lines/file | 182 |
| Languages | Rust |
| Outgoing deps | 0 |
| Incoming deps | 0 |
| Tier | 2 |

