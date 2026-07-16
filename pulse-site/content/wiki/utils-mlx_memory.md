+++
title = "utils/mlx_memory/"
weight = 38
description = "<think> Let me analyze this module carefully to create a proper overview.  The module is `utils/mlx_memory` - it's a memory management layer for MLX (Apple Silicon ML framework).  Key observations: 1...."

[extra]
tier = 2
file_count = 6
total_lines = 1619
languages = "Python"
parent_path = "utils"
has_mermaid = true
+++

<think>
Let me analyze this module carefully to create a proper overview.

The module is `utils/mlx_memory` - it's a memory management layer for MLX (Apple Silicon ML framework).

Key observations:
1. This module manages memory for MLX (Apple's Metal-based ML framework)
2. It has 6 Python files, ~1619 lines total
3. It depends on only 1 file (mlx_memory.py) - so it's a relatively isolated module
4. It has NO incoming dependencies - meaning nothing depends on it (though that seems odd given all the key symbols referenced in other files)
5. Wait, the key symbols show references from many files like arrow_batch_builder.rs, batch_dns.py, claims_extraction.rs, etc. - but the "Dependents" section says "No incoming dependencies detected"

This is a utility module that provides:
- Memory allocation and cleanup for MLX/Metal
- Buffer pools (Metal BufferPool, Metal SlabPool)
- Model caching with LRU eviction
- Prompt caching (MLXPrompt Cache)
- Memory pressure detection
- Async/sync cleanup decorators

The module has high fan-out (depends on 1 module) but the key symbols are referenced in many places - this is a leaf module that provides services to many consumers.

It manages:
- Metal cache limits
- Memory allocation/deallocation
- Model loading/caching
- Buffer pools for embeddings
- Slab allocation for Metal buffers

This is clearly a critical infrastructure module - it handles all the low-level Metal memory management for the codebase. Even though it has "0 incoming dependencies" in the dependency graph, the key symbols are referenced in 21+ files, suggesting it's widely used but perhaps imported directly rather than through the module hierarchy.

Wait, looking more carefully - the key symbols show references from files like `arrow_batch_builder.rs`, `batch_dns.py`, `claims_extraction.rs`, etc. These might be cross-language references or the dependency tracking might be incomplete.
 
The module serves as a critical memory management hub for MLX, orchestrating Metal buffer allocation, model caching, and memory cleanup across the entire codebase. Its specialized focus on Apple's Metal framework means it acts as a crucial bridge between high-level ML operations and low-level GPU memory management. The module's design suggests a sophisticated approach to handling memory constraints, particularly on Apple Silicon devices, with advanced features like LRU caching, buffer pooling, and memory pressure detection.
</think>

Provides centralized memory management for MLX (Apple's Metal-based ML framework), including Metal cache configuration, buffer allocation, model loading, and cleanup orchestration. The module implements buffer pooling via `MetalBufferPool` and `MetalSlabPool` to avoid repeated Metal allocations, maintains an LRU model cache capped at two entries, and exposes memory pressure detection with NORMAL/WARNING/CRITICAL thresholds. Cleanup operations follow a canonical five-step sequence (Python reference release → GPU queue flush → Metal cache clear → second GC pass → slab pool release) that is exposed through decorators and standalone functions like `mlx_cleanup_sync` and `mlx_cleanup_aggressive`. The `allocate` function sees heavy reuse across the codebase (21 references), indicating this module serves as a foundational resource for any code path that touches MLX memory. With roughly 1,600 lines across six files and 81 exported functions, the module is moderately sized but highly specialized—changes to its cleanup order or cache configuration can ripple through every MLX consumer. Notably, the module uses lazy MLX imports to avoid Metal initialization at module load time, making it safe to import without triggering GPU memory configuration.

## Dependency Diagram

{% mermaid() %}
graph LR
    m_utils_mlx_memory["<b>utils/mlx_memory/</b>"]
    style m_utils_mlx_memory fill:#a78bfa,color:#0d0d0d,stroke:#a78bfa
    m_utils["utils/"]
    m_utils_mlx_memory -->|1| m_utils
    classDef default fill:#1a1a2e,stroke:#a78bfa,color:#e0e0e0
    click m_utils_mlx_memory "/wiki/utils-mlx_memory/"
    click m_utils "/wiki/utils/"
{% end %}

## Structure

| Language | Files |
|---|---|
| Python | 6 |

### Largest Files

- `_core.py` (900 lines)
- `__init__.py` (210 lines)
- `_slab.py` (197 lines)
- `_embedder.py` (166 lines)
- `_prompt.py` (83 lines)
- `_tensor.py` (63 lines)


## Dependencies

Depends on **1 files** across **1 modules**.

**[utils/](@/wiki/utils.md)** (1 files):
- `mlx_memory.py`



## Dependents

No incoming dependencies detected.

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
<p><code>allocate</code> (Function) in _embedder.py — referenced in 21 files</p>
<ul><li class="ref-list">Referenced by: arrow_batch_builder.rs, batch_dns.py, claims_extraction.rs, lmdb_bulk.py, madvise.rs +15 more</li></ul>
</li>
<li>
<p><code>mlx_cleanup_sync</code> (Function) in _core.py — referenced in 9 files</p>
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
<ul><li class="ref-list">Referenced by: __init__.py, _hermes_cache.py, _slab.py, mlx_cache.py, mlx_memory.py +2 more</li></ul>
</li>
<li>
<p><code>get_mlx_model</code> (Function) in _core.py — referenced in 8 files</p>
<details><summary>Get MLX model and tokenizer from cache or load from disk.</summary>
<div class="doc-comment">
<p>Get MLX model and tokenizer from cache or load from disk.</p>
<p>LRU eviction when cache exceeds max 2 models.</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py, explainer.py, mlx_cache.py, mlx_memory.py, pattern_mining.py +2 more</li></ul>
</li>
<li>
<p><code>mlx_cleanup_aggressive</code> (Function) in _core.py — referenced in 7 files</p>
<details><summary>Aggressive cleanup — sets cache to 64MB floor then restores limits.</summary>
<div class="doc-comment">
<p>Aggressive cleanup — sets cache to 64MB floor then restores limits.</p>
<p>Use during EMERGENCY memory pressure.</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py, mlx_cache.py, mlx_memory.py, pool.py, uma_budget.py</li></ul>
</li>
<li>
<p><code>MetalBufferPool</code> (Class) in _embedder.py — referenced in 5 files</p>
<details><summary>Pre-allocated Metal buffer pool for embedding inference.</summary>
<div class="doc-comment">
<p>Pre-allocated Metal buffer pool for embedding inference.</p>
<p></p>
<p>Usage:</p>
<p>pool = get_buffer_pool()</p>
<p>if pool.is_allocated():</p>
<p>ids = pool.get_buffer("input_ids")</p>
<p># ... use buffer ...</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py, metal_embedder_buffers.py, mlx_memory.py, model_lifecycle.py</li></ul>
</li>
</ul>

<details><summary><strong>Function</strong> (81)</summary>
<ul>
<li><code>mlx_cleanup_aggressive</code> (_core.py)
<details><summary>Aggressive cleanup — sets cache to 64MB floor then restores limits.</summary>
<div class="doc-comment">
<p>Aggressive cleanup — sets cache to 64MB floor then restores limits.</p>
<p>Use during EMERGENCY memory pressure.</p>
</div>
</details>
</li>
<li><code>acquire_slab</code> (_slab.py)
<details><summary>Acquire a slab of at least size_bytes.</summary>
<div class="doc-comment">
<p>Acquire a slab of at least size_bytes.</p>
<p></p>
<p>Returns a _Slab with a memoryview, or None on failure.</p>
<p>Caller must call release_slab() when done.</p>
</div>
</details>
</li>
<li><code>_apply_metal_limits_impl</code> (_core.py) — <span class="doc-comment-inline">Apply Metal limits. Called only from init_mlx_buffers under lock.</span></li>
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
<li><code>allocate</code> (_embedder.py)</li>
<li><code>get_mlx_memory_stats</code> (_core.py) — <span class="doc-comment-inline">Získat aktuální MLX memory statistiky.</span></li>
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
<li><code>get_mlx_memory_metrics</code> (_core.py) — <span class="doc-comment-inline">Convenience reporter for all MLX memory metrics.</span></li>
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
<li><code>get_dynamic_metal_cache_limit</code> (_core.py)
<details><summary>Dynamic Metal cache limit: 20% of available UMA, clamp [256MiB, 1.5GiB].</summary>
<div class="doc-comment">
<p>Dynamic Metal cache limit: 20% of available UMA, clamp [256MiB, 1.5GiB].</p>
<p>Called by init_mlx_buffers; not for direct use by callers.</p>
</div>
</details>
</li>
<li><code>safe_set_cache_limit</code> (_core.py) — <span class="doc-comment-inline">Set Metal cache limit. Returns True on success.</span></li>
<li><code>init_metal_embedder_buffers</code> (_embedder.py)</li>
<li><code>get_mlx_active_memory_mb</code> (_core.py) — <span class="doc-comment-inline">Get active MLX memory in MB.</span></li>
<li><code>get_mlx_peak_memory_mb</code> (_core.py) — <span class="doc-comment-inline">Get peak MLX memory in MB.</span></li>
<li><code>get_mlx_cache_memory_mb</code> (_core.py) — <span class="doc-comment-inline">Get MLX cache memory in MB.</span></li>
<li><code>get_mlx_memory_pressure</code> (_core.py) — <span class="doc-comment-inline">Return (usage_pct, level) where level is NORMAL|WARNING|CRITICAL.</span></li>
<li><code>safe_get_cache_limit</code> (_core.py) — <span class="doc-comment-inline">Get current Metal cache limit. Returns None on failure.</span></li>
<li><code>set_async</code> (_prompt.py) — <span class="doc-comment-inline">Store a (cache_state, size_bytes) tuple (async-safe).</span></li>
<li><code>_maybe_eval_async</code> (_core.py) — <span class="doc-comment-inline">Throttled mx.eval([]) to prevent excessive GPU sync.</span></li>
<li><code>_maybe_eval_sync</code> (_core.py) — <span class="doc-comment-inline">Synchronous throttled mx.eval([]).</span></li>
<li><code>_aggressive_cleanup</code> (_slab.py) — <span class="doc-comment-inline">Aggressive cleanup: clear MLX cache and retry.</span></li>
<li><code>set</code> (_prompt.py) — <span class="doc-comment-inline">Store a (cache_state, size_bytes) tuple (sync, non-blocking).</span></li>
<li><code>reset_metal_peak</code> (_core.py) — <span class="doc-comment-inline">Reset MLX peak memory counter.</span></li>
<li><code>get_cache_stats</code> (_core.py) — <span class="doc-comment-inline">Get model cache statistics including hit/miss metrics.</span></li>
<li><code>_clear_metal_cache_async</code> (_core.py) — <span class="doc-comment-inline">Async wrapper around safe_clear_metal_cache().</span></li>
<li><code>release_metal_embedder_buffers</code> (_embedder.py)
<details><summary>Release pre-allocated Metal buffers and free Metal cache.</summary>
<div class="doc-comment">
<p>Release pre-allocated Metal buffers and free Metal cache.</p>
<p></p>
<p>Does NOT destroy the singleton — pool instance persists so that</p>
<p>subsequent get_buffer_pool() skips re-allocation. This prevents</p>
<p>the triple-48MB-allocation bug on repeated unload/reload cycles.</p>
</div>
</details>
</li>
<li><code>_get_mx</code> (_tensor.py) — <span class="doc-comment-inline">Lazy MLX accessor.</span></li>
<li><code>format_mlx_memory_snapshot</code> (_core.py) — <span class="doc-comment-inline">Get a complete MLX memory snapshot.</span></li>
<li><code>get_metal_limits_status</code> (_core.py) — <span class="doc-comment-inline">Diagnostic surface for metal limit configuration status.</span></li>
<li><code>sync_wrapper</code> (_core.py)</li>
<li><code>async_wrapper</code> (_core.py)</li>
<li><code>sync_wrapper</code> (_core.py)</li>
<li><code>async_wrapper</code> (_core.py)</li>
<li><code>release</code> (_embedder.py) — <span class="doc-comment-inline">Release all buffers and free Metal memory.</span></li>
<li><code>get_buffer_pool</code> (_embedder.py) — <span class="doc-comment-inline">Get the singleton MetalBufferPool instance.</span></li>
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
<li><code>release_all</code> (_slab.py) — <span class="doc-comment-inline">Release all slabs back to the system.</span></li>
<li><code>_evict_slab</code> (_slab.py) — <span class="doc-comment-inline">Remove a slab from the pool.</span></li>
<li><code>__init__</code> (_tensor.py)</li>
<li><code>_detect_mlx_available</code> (_core.py) — <span class="doc-comment-inline">Return True only if mlx.core is importable (spec found, not None).</span></li>
<li><code>_release_slab_pool</code> (_core.py) — <span class="doc-comment-inline">Called by mlx_cleanup_sync to release slab pool memory.</span></li>
<li><code>get_instance</code> (_slab.py) — <span class="doc-comment-inline">Get the singleton MetalSlabPool instance.</span></li>
<li><code>release_slab_pool</code> (_slab.py) — <span class="doc-comment-inline">Release the singleton (called by mlx_cleanup_sync).</span></li>
<li><code>release_slab</code> (_slab.py) — <span class="doc-comment-inline">Return a slab to the pool (does not free, just marks free).</span></li>
<li><code>get_buffer_for_size</code> (_slab.py)
<details><summary>Convenience: acquire and return the memoryview directly.</summary>
<div class="doc-comment">
<p>Convenience: acquire and return the memoryview directly.</p>
<p>The slab is NOT released — caller is responsible for releasing.</p>
</div>
</details>
</li>
<li><code>evict_all</code> (_core.py) — <span class="doc-comment-inline">Synchronous eviction of entire MLX model cache (safe from any thread).</span></li>
<li><code>__init__</code> (_slab.py)</li>
<li><code>_size_class_for</code> (_slab.py) — <span class="doc-comment-inline">Return the smallest size class &gt;= size_bytes.</span></li>
<li><code>get_instance</code> (_embedder.py)</li>
<li><code>get</code> (_prompt.py) — <span class="doc-comment-inline">Get a (key, (cache_state, size_bytes)) tuple or None.</span></li>
<li><code>_get_mlx_safe</code> (_core.py) — <span class="doc-comment-inline">Safe lazy accessor for mlx.core (fallback None).</span></li>
<li><code>_clear_metal_cache_sync</code> (_core.py) — <span class="doc-comment-inline">Sync wrapper around safe_clear_metal_cache().</span></li>
<li><code>get_stats</code> (_slab.py) — <span class="doc-comment-inline">Return pool statistics.</span></li>
<li><code>__init__</code> (_prompt.py)</li>
<li><code>_format_limit_mib</code> (_core.py)</li>
<li><code>_get_cache_lock</code> (_core.py) — <span class="doc-comment-inline">Get or create the cache async lock.</span></li>
<li><code>__init__</code> (_embedder.py)</li>
<li><code>get_buffer</code> (_embedder.py) — <span class="doc-comment-inline">Get a buffer by name, or None if not found.</span></li>
<li><code>_ensure_mlx</code> (_core.py) — <span class="doc-comment-inline">Ensure MLX core is available.</span></li>
<li><code>_has_metal_api</code> (_core.py)</li>
<li><code>safe_clear_metal_cache</code> (_core.py) — <span class="doc-comment-inline">Alias for clear_mlx_cache() for backward compatibility.</span></li>
<li><code>get_semaphore_for_testing</code> (_core.py) — <span class="doc-comment-inline">Test hook for semaphore creation.</span></li>
<li><code>release_slab_pool</code> (_slab.py) — <span class="doc-comment-inline">Module-level convenience alias.</span></li>
<li><code>clear</code> (_prompt.py) — <span class="doc-comment-inline">Clear all entries.</span></li>
<li><code>total_size</code> (_prompt.py) — <span class="doc-comment-inline">Sum of all entry sizes in bytes.</span></li>
<li><code>array</code> (_tensor.py) — <span class="doc-comment-inline">Return the underlying mlx.core.array.</span></li>
<li><code>to_list</code> (_tensor.py) — <span class="doc-comment-inline">Convert to Python list.</span></li>
<li><code>is_allocated</code> (_embedder.py)</li>
<li><code>__len__</code> (_prompt.py)</li>
<li><code>__contains__</code> (_prompt.py)</li>
<li><code>keys</code> (_prompt.py)</li>
<li><code>__repr__</code> (_tensor.py)</li>
</ul>
</details>

<details><summary><strong>Class</strong> (7)</summary>
<ul>
<li><code>MetalSlabPool</code> (_slab.py)
<details><summary>Thread-safe slab allocator for Metal buffers.</summary>
<div class="doc-comment">
<p>Thread-safe slab allocator for Metal buffers.</p>
<p></p>
<p>Usage:</p>
<p>pool = MetalSlabPool.get_instance()</p>
<p>slab = pool.acquire_slab(1024 * 1024)  # 1MB slab</p>
<p>if slab is not None:</p>
<p>try:</p>
<p># use slab.memoryview</p>
<p>finally:</p>
<p>pool.release_slab(slab)</p>
</div>
</details>
</li>
<li><code>MetalBufferPool</code> (_embedder.py)
<details><summary>Pre-allocated Metal buffer pool for embedding inference.</summary>
<div class="doc-comment">
<p>Pre-allocated Metal buffer pool for embedding inference.</p>
<p></p>
<p>Usage:</p>
<p>pool = get_buffer_pool()</p>
<p>if pool.is_allocated():</p>
<p>ids = pool.get_buffer("input_ids")</p>
<p># ... use buffer ...</p>
</div>
</details>
</li>
<li><code>MLXPromptCache</code> (_prompt.py)
<details><summary>LRU cache for MLX prompt cache states with explicit size tracking.</summary>
<div class="doc-comment">
<p>LRU cache for MLX prompt cache states with explicit size tracking.</p>
<p></p>
<p>Usage:</p>
<p>cache = MLXPromptCache(max_entries=10, max_size_gb=0.5)</p>
<p>cache.set("prompt_key", (cache_state, size_bytes))</p>
<p>state = cache.get("prompt_key")</p>
<p>if state is not None:</p>
<p>cache_key, (cache_state, size_bytes) = state</p>
</div>
</details>
</li>
<li><code>SharedTensor</code> (_tensor.py)
<details><summary>Zero-copy wrapper for MLX arrays.</summary>
<div class="doc-comment">
<p>Zero-copy wrapper for MLX arrays.</p>
<p></p>
<p>Currently wraps an mlx.core.array. When Metal buffer sharing is</p>
<p>implemented, this will provide true zero-copy semantics across</p>
<p>thread/executor boundaries.</p>
<p></p>
<p>Usage:</p>
<p>t = SharedTensor([1.0, 2.0, 3.0])</p>
<p>arr = t.array  # underlying mlx.core.array</p>
</div>
</details>
</li>
<li><code>_Slab</code> (_slab.py) — <span class="doc-comment-inline">A single Metal buffer slab.</span></li>
<li><code>_MetalBuffer</code> (_embedder.py) — <span class="doc-comment-inline">A single pre-allocated Metal buffer.</span></li>
<li><code>ConcurrencyCategory</code> (_core.py)</li>
</ul>
</details>

<details><summary><strong>Method</strong> (30)</summary>
<ul>
<li><code>acquire_slab</code> (_slab.py)
<details><summary>Acquire a slab of at least size_bytes.</summary>
<div class="doc-comment">
<p>Acquire a slab of at least size_bytes.</p>
<p></p>
<p>Returns a _Slab with a memoryview, or None on failure.</p>
<p>Caller must call release_slab() when done.</p>
</div>
</details>
</li>
<li><code>allocate</code> (_embedder.py)</li>
<li><code>set_async</code> (_prompt.py) — <span class="doc-comment-inline">Store a (cache_state, size_bytes) tuple (async-safe).</span></li>
<li><code>_aggressive_cleanup</code> (_slab.py) — <span class="doc-comment-inline">Aggressive cleanup: clear MLX cache and retry.</span></li>
<li><code>set</code> (_prompt.py) — <span class="doc-comment-inline">Store a (cache_state, size_bytes) tuple (sync, non-blocking).</span></li>
<li><code>release</code> (_embedder.py) — <span class="doc-comment-inline">Release all buffers and free Metal memory.</span></li>
<li><code>release_all</code> (_slab.py) — <span class="doc-comment-inline">Release all slabs back to the system.</span></li>
<li><code>_evict_slab</code> (_slab.py) — <span class="doc-comment-inline">Remove a slab from the pool.</span></li>
<li><code>__init__</code> (_tensor.py)</li>
<li><code>get_instance</code> (_slab.py) — <span class="doc-comment-inline">Get the singleton MetalSlabPool instance.</span></li>
<li><code>release_slab_pool</code> (_slab.py) — <span class="doc-comment-inline">Release the singleton (called by mlx_cleanup_sync).</span></li>
<li><code>release_slab</code> (_slab.py) — <span class="doc-comment-inline">Return a slab to the pool (does not free, just marks free).</span></li>
<li><code>get_buffer_for_size</code> (_slab.py)
<details><summary>Convenience: acquire and return the memoryview directly.</summary>
<div class="doc-comment">
<p>Convenience: acquire and return the memoryview directly.</p>
<p>The slab is NOT released — caller is responsible for releasing.</p>
</div>
</details>
</li>
<li><code>__init__</code> (_slab.py)</li>
<li><code>_size_class_for</code> (_slab.py) — <span class="doc-comment-inline">Return the smallest size class &gt;= size_bytes.</span></li>
<li><code>get_instance</code> (_embedder.py)</li>
<li><code>get</code> (_prompt.py) — <span class="doc-comment-inline">Get a (key, (cache_state, size_bytes)) tuple or None.</span></li>
<li><code>get_stats</code> (_slab.py) — <span class="doc-comment-inline">Return pool statistics.</span></li>
<li><code>__init__</code> (_prompt.py)</li>
<li><code>__init__</code> (_embedder.py)</li>
<li><code>get_buffer</code> (_embedder.py) — <span class="doc-comment-inline">Get a buffer by name, or None if not found.</span></li>
<li><code>clear</code> (_prompt.py) — <span class="doc-comment-inline">Clear all entries.</span></li>
<li><code>total_size</code> (_prompt.py) — <span class="doc-comment-inline">Sum of all entry sizes in bytes.</span></li>
<li><code>array</code> (_tensor.py) — <span class="doc-comment-inline">Return the underlying mlx.core.array.</span></li>
<li><code>to_list</code> (_tensor.py) — <span class="doc-comment-inline">Convert to Python list.</span></li>
<li><code>is_allocated</code> (_embedder.py)</li>
<li><code>__len__</code> (_prompt.py)</li>
<li><code>__contains__</code> (_prompt.py)</li>
<li><code>keys</code> (_prompt.py)</li>
<li><code>__repr__</code> (_tensor.py)</li>
</ul>
</details>

<details><summary><strong>Constant</strong> (28)</summary>
<ul>
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
<li><code>MLX_AVAILABLE</code> (__init__.py)</li>
<li><code>_MLX_CACHE_LIMIT</code> (__init__.py)</li>
<li><code>_MLX_WIRED_LIMIT</code> (__init__.py)</li>
<li><code>_SLAB_CLASSES_BYTES</code> (_slab.py)</li>
<li><code>_SLAB_CLASS_NAMES</code> (_slab.py)</li>
<li><code>_SLABS_PER_CLASS</code> (_slab.py)</li>
<li><code>_MAX_SLAB_TOTAL_BYTES</code> (_slab.py)</li>
<li><code>_MAX_BATCH_SIZE</code> (_embedder.py)</li>
<li><code>_MAX_SEQ_LEN</code> (_embedder.py)</li>
<li><code>_HIDDEN_DIM</code> (_embedder.py)</li>
<li><code>_MAX_BUFFER_BYTES</code> (_embedder.py)</li>
</ul>
</details>



## Metrics

| Metric | Value |
|---|---|
| Files | 6 |
| Total lines | 1619 |
| Avg lines/file | 269 |
| Languages | Python |
| Outgoing deps | 1 |
| Incoming deps | 0 |
| Tier | 2 |

