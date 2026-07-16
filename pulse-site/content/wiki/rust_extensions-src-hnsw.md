+++
title = "rust_extensions/src/hnsw/"
weight = 30
description = "<think> Let me analyze the structural context for the `rust_extensions/src/hnsw` module:  **Module Purpose:** This module implements a Hierarchical Navigable Small World (HNSW) approximate nearest nei..."

[extra]
tier = 2
file_count = 3
total_lines = 865
languages = "Rust"
parent_path = "rust_extensions"
has_mermaid = true
+++

<think>
Let me analyze the structural context for the `rust_extensions/src/hnsw` module:

**Module Purpose:**
This module implements a Hierarchical Navigable Small World (HNSW) approximate nearest neighbor (ANN) index in Rust, with Python bindings via PyO3. It provides efficient vector similarity search capabilities.

**Key observations:**
1. Small module: only 3 files, 865 total lines (288 avg per file) - relatively compact
2. Rust implementation with Python API bindings
3. Has two main structs: `HNSWIndex` (core implementation) and two Python wrappers (`PyHNSWIndex` and `PyHNSWBridge`)
4. The module has significant fan-in - it's used by 3 files (`embedding_index.rs`, `lancedb_bridge.rs`, `lib.rs`)
5. Key operations: insert, search, load, save, batch operations
6. GIL management details show it's designed to be called from Python
7. Supports dimension validation, SIMD vector normalization, cosine similarity
8. Has concurrency considerations - not safe for concurrent inserts + searches

**Architectural role:**
- It's a leaf for dependencies (only depends on 1 module)
- It's a hub for dependents (used by 3 modules, with `load` function referenced in 338 files!)
- The high reference count for `load` (338 files) and `search` (262 files) suggests this is a heavily used interface
- Provides ANN indexing capabilities to higher-level Python modules

**Dependencies:**
- Only depends on `rust_extensions/src/mod.rs` (1 file) - minimal dependencies

**Dependents:**
- `embedding_index.rs`
- `lancedb_bridge.rs`
- `lib.rs`

**Scale:**
- 3 files, 865 lines - compact for a module with this much utility
- 4 structs, 38 functions, 31 methods - decent API surface


I'll craft a concise overview that captures the module's essence. The HNSW module is a lightweight Rust-based approximate nearest neighbor indexing system with robust Python integration. Its compact design belies its powerful vector similarity search capabilities, supporting batch operations, dimension validation, and efficient SIMD-optimized computations.
</think>

Provides a hierarchical navigable small world (HNSW) approximate nearest neighbor index for high-dimensional vector similarity search, with PyO3 bindings enabling Python callers to insert vectors, search for k nearest neighbors, and persist the index to disk. The module exposes both a low-level `PyHNSWIndex` for mmap-backed persistence and a thread-safe `PyHNSWBridge` optimized for LanceDB entity storage, with batch operations that amortize Python GIL acquisition overhead across multiple queries. With only three Rust source files totaling 865 lines, the implementation is intentionally lean yet supports SIMD-accelerated vector normalization and returns typed errors on dimension mismatches. This module sits at tier 2 as a foundational building block—it has minimal upstream dependencies but serves three downstream consumers (`embedding_index`, `lancedb_bridge`, and `lib.rs`), making it a high-leverage component where changes to core indexing logic will ripple across the entire embedding pipeline.

## Dependency Diagram

{% mermaid() %}
graph LR
    m_rust_extensions_src_hnsw["<b>rust_extensions/src/hnsw/</b>"]
    style m_rust_extensions_src_hnsw fill:#a78bfa,color:#0d0d0d,stroke:#a78bfa
    m_rust_extensions_src["rust_extensions/src/"]
    m_rust_extensions_src_hnsw -->|2| m_rust_extensions_src
    m_rust_extensions_src -->|4| m_rust_extensions_src_hnsw
    classDef default fill:#1a1a2e,stroke:#a78bfa,color:#e0e0e0
    click m_rust_extensions_src_hnsw "/wiki/rust_extensions-src-hnsw/"
    click m_rust_extensions_src "/wiki/rust_extensions-src/"
{% end %}

## Structure

| Language | Files |
|---|---|
| Rust | 3 |

### Largest Files

- `index.rs` (435 lines)
- `py_api.rs` (388 lines)
- `mod.rs` (42 lines)


## Dependencies

Depends on **1 files** across **1 modules**.

**[rust_extensions/src/](@/wiki/rust_extensions-src.md)** (1 files):
- `mod.rs`



## Dependents

Used by **3 files** across **1 modules**.

**[rust_extensions/src/](@/wiki/rust_extensions-src.md)** (3 files):
- `embedding_index.rs`
- `lancedb_bridge.rs`
- `lib.rs`



## Key Symbols

<p><strong>Key definitions:</strong></p>
<ul>
<li>
<p><code>load</code> (Function) in index.rs — referenced in 338 files</p>
<ul><li class="ref-list">Referenced by: __init__.py, __main__.py, _analyze_deep.py, _base.py, _catalog.py +306 more</li></ul>
</li>
<li>
<p><code>search</code> (Function) in index.rs — referenced in 262 files</p>
<details><summary>Search for k nearest neighbors.</summary>
<div class="doc-comment">
<p>Search for k nearest neighbors.</p>
<p>ISSUE-007: raises PyValueError on query dimension mismatch.</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py, _catalog.py, _discovery_stage.py, _fix_rename.py, _models.py +250 more</li></ul>
</li>
<li>
<p><code>insert</code> (Function) in index.rs — referenced in 164 files</p>
<details><summary>Insert a vector into the index.</summary>
<div class="doc-comment">
<p>Insert a vector into the index.</p>
<p>ISSUE-007: raises PyValueError on dimension mismatch.</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __main__.py, _debug_test.py, _debug_test2.py, _debug_test3.py, _duckdb_exporter.py +155 more</li></ul>
</li>
<li>
<p><code>save</code> (Function) in index.rs — referenced in 60 files</p>
<ul><li class="ref-list">Referenced by: advanced_image_osint.py, benchmark_pipeline.py, benchmark_rust_vs_python.py, bloom_filter.py, cache.py +54 more</li></ul>
</li>
<li>
<p><code>__repr__</code> (Function) in py_api.rs — referenced in 37 files</p>
<ul><li class="ref-list">Referenced by: __init__.py, _tensor.py, async_generators.py, async_utils.py, base.py +31 more</li></ul>
</li>
</ul>

<details><summary><strong>Function</strong> (38)</summary>
<ul>
<li><code>search_layer</code> (index.rs) — <span class="doc-comment-inline">Search layer — ISSUE-007: returns Result, propagates dimension errors.</span></li>
<li><code>add_batch</code> (py_api.rs)
<details><summary>Add a batch of entities to the ANN index.</summary>
<div class="doc-comment">
<p>Add a batch of entities to the ANN index.</p>
<p></p>
<p>GIL is held only for HashMap updates; HNSW insert releases GIL via</p>
<p>rayon (embedding_index uses cpu_pool internally).</p>
<p></p>
<p>Args:</p>
<p>entity_ids: List of entity ID strings (must match embeddings 1:1)</p>
<p>embeddings: List of embedding vectors (list of floats)</p>
<p></p>
<p>Returns:</p>
<p>Number of entities successfully inserted</p>
</div>
</details>
</li>
<li><code>insert</code> (index.rs)</li>
<li><code>load</code> (index.rs)</li>
<li><code>search_batch</code> (py_api.rs)
<details><summary>Batch search — search multiple query vectors at once.</summary>
<div class="doc-comment">
<p>Batch search — search multiple query vectors at once.</p>
<p></p>
<p>More efficient than calling search() multiple times due to</p>
<p>reduced GIL acquisition overhead (one GIL acquire for all queries).</p>
<p>Per-query locking — releases index lock between queries to avoid</p>
<p>holding a long-duration exclusive lock over all searches.</p>
<p></p>
<p>Args:</p>
<p>query_embeddings: List of query vectors</p>
<p>k: Number of results per query</p>
<p></p>
<p>Returns:</p>
<p>List of result lists (one per query), each as (entity_id, score) tuples</p>
</div>
</details>
</li>
<li><code>search</code> (py_api.rs)
<details><summary>Search for k nearest neighbors.</summary>
<div class="doc-comment">
<p>Search for k nearest neighbors.</p>
<p></p>
<p>GIL is released during search — rayon cpu_pool handles parallelism.</p>
<p></p>
<p>Args:</p>
<p>query_embedding: Query vector (list of floats)</p>
<p>k: Number of results to return</p>
<p></p>
<p>Returns:</p>
<p>List of (entity_id, similarity_score) tuples, sorted descending by score</p>
</div>
</details>
</li>
<li><code>search</code> (index.rs)</li>
<li><code>save</code> (index.rs)</li>
<li><code>new</code> (py_api.rs)</li>
<li><code>random_layer</code> (index.rs)</li>
<li><code>load</code> (py_api.rs)</li>
<li><code>insert</code> (py_api.rs)
<details><summary>Insert a vector into the index.</summary>
<div class="doc-comment">
<p>Insert a vector into the index.</p>
<p>ISSUE-007: raises PyValueError on dimension mismatch.</p>
</div>
</details>
</li>
<li><code>search</code> (py_api.rs)
<details><summary>Search for k nearest neighbors.</summary>
<div class="doc-comment">
<p>Search for k nearest neighbors.</p>
<p>ISSUE-007: raises PyValueError on query dimension mismatch.</p>
</div>
</details>
</li>
<li><code>test_hnsw_insert_search</code> (index.rs)</li>
<li><code>save</code> (py_api.rs)</li>
<li><code>new</code> (index.rs)</li>
<li><code>test_insert_dimension_mismatch</code> (index.rs)</li>
<li><code>test_search_dimension_mismatch</code> (index.rs)</li>
<li><code>new</code> (py_api.rs)</li>
<li><code>new_bridge</code> (py_api.rs)
<details><summary>Create a new HNSW bridge with the given embedding dimension.</summary>
<div class="doc-comment">
<p>Create a new HNSW bridge with the given embedding dimension.</p>
<p></p>
<p>Example:</p>
<p>bridge = PyHNSWBridge(dim=384)</p>
</div>
</details>
</li>
<li><code>new</code> (index.rs)</li>
<li><code>__repr__</code> (py_api.rs)</li>
<li><code>normalize_vector</code> (index.rs)
<details><summary>Normalize vector using best available SIMD, with scalar fallback.</summary>
<div class="doc-comment">
<p>Normalize vector using best available SIMD, with scalar fallback.</p>
<p>ISSUE-007: Returns Result — zero/near-zero vector is Err.</p>
</div>
</details>
</li>
<li><code>clear</code> (py_api.rs)</li>
<li><code>test_normalize</code> (index.rs)</li>
<li><code>test_cosine_dimension_mismatch</code> (index.rs)</li>
<li><code>test_zero_vector_rejected</code> (index.rs)</li>
<li><code>test_normalize_nan_vector</code> (index.rs)</li>
<li><code>cosine</code> (index.rs)
<details><summary>Compute cosine similarity between two vectors.</summary>
<div class="doc-comment">
<p>Compute cosine similarity between two vectors.</p>
<p>ISSUE-007: Returns Result — dimension mismatch is Err.</p>
</div>
</details>
</li>
<li><code>len</code> (index.rs)</li>
<li><code>is_empty</code> (index.rs)</li>
<li><code>default</code> (index.rs)</li>
<li><code>len</code> (py_api.rs)</li>
<li><code>is_empty</code> (py_api.rs)</li>
<li><code>len</code> (py_api.rs)</li>
<li><code>is_empty</code> (py_api.rs)</li>
<li><code>get_node_id</code> (py_api.rs)</li>
<li><code>get_entity_id</code> (py_api.rs)</li>
</ul>
</details>

<details><summary><strong>Struct</strong> (4)</summary>
<ul>
<li><code>HNSWIndex</code> (index.rs)</li>
<li><code>PyHNSWBridge</code> (py_api.rs)
<details><summary>Thread-safe HNSW ANN index for LanceDB entity store.</summary>
<div class="doc-comment">
<p>Thread-safe HNSW ANN index for LanceDB entity store.</p>
<p></p>
<p>Wraps `HNSWIndex` with:</p>
<p>- GIL-safe API for Python calls (runs in asyncio.to_thread)</p>
<p>- Batch operations for efficiency</p>
<p>- Entity ID → node ID mapping (LanceDB metadata lives in Python layer)</p>
<p></p>
<p>NOTE: Not safe for concurrent inserts + searches from multiple threads.</p>
<p>LanceDBIdentityStore guarantees single-threaded access via asyncio.to_thread.</p>
</div>
</details>
</li>
<li><code>Node</code> (index.rs)</li>
<li><code>PyHNSWIndex</code> (py_api.rs)
<details><summary>Low-level HNSW ANN index with PyO3 bindings.</summary>
<div class="doc-comment">
<p>Low-level HNSW ANN index with PyO3 bindings.</p>
<p></p>
<p>Used for MLX embeddings re-ranking. Provides explicit save/load</p>
<p>for mmap-backed persistence.</p>
<p></p>
<p># Example</p>
<p>```python</p>
<p>index = PyHNSWIndex(cache_dir="/tmp/ann_index")</p>
<p>index.insert(1, [0.1] * 384)</p>
<p>results = index.search([0.9] + [0.0] * 383, k=5)</p>
<p>index.save()</p>
<p>```</p>
</div>
</details>
</li>
</ul>
</details>

<details><summary><strong>Method</strong> (31)</summary>
<ul>
<li><code>search_layer</code> (index.rs) — <span class="doc-comment-inline">Search layer — ISSUE-007: returns Result, propagates dimension errors.</span></li>
<li><code>add_batch</code> (py_api.rs)
<details><summary>Add a batch of entities to the ANN index.</summary>
<div class="doc-comment">
<p>Add a batch of entities to the ANN index.</p>
<p></p>
<p>GIL is held only for HashMap updates; HNSW insert releases GIL via</p>
<p>rayon (embedding_index uses cpu_pool internally).</p>
<p></p>
<p>Args:</p>
<p>entity_ids: List of entity ID strings (must match embeddings 1:1)</p>
<p>embeddings: List of embedding vectors (list of floats)</p>
<p></p>
<p>Returns:</p>
<p>Number of entities successfully inserted</p>
</div>
</details>
</li>
<li><code>insert</code> (index.rs)</li>
<li><code>load</code> (index.rs)</li>
<li><code>search_batch</code> (py_api.rs)
<details><summary>Batch search — search multiple query vectors at once.</summary>
<div class="doc-comment">
<p>Batch search — search multiple query vectors at once.</p>
<p></p>
<p>More efficient than calling search() multiple times due to</p>
<p>reduced GIL acquisition overhead (one GIL acquire for all queries).</p>
<p>Per-query locking — releases index lock between queries to avoid</p>
<p>holding a long-duration exclusive lock over all searches.</p>
<p></p>
<p>Args:</p>
<p>query_embeddings: List of query vectors</p>
<p>k: Number of results per query</p>
<p></p>
<p>Returns:</p>
<p>List of result lists (one per query), each as (entity_id, score) tuples</p>
</div>
</details>
</li>
<li><code>search</code> (py_api.rs)
<details><summary>Search for k nearest neighbors.</summary>
<div class="doc-comment">
<p>Search for k nearest neighbors.</p>
<p></p>
<p>GIL is released during search — rayon cpu_pool handles parallelism.</p>
<p></p>
<p>Args:</p>
<p>query_embedding: Query vector (list of floats)</p>
<p>k: Number of results to return</p>
<p></p>
<p>Returns:</p>
<p>List of (entity_id, similarity_score) tuples, sorted descending by score</p>
</div>
</details>
</li>
<li><code>search</code> (index.rs)</li>
<li><code>save</code> (index.rs)</li>
<li><code>new</code> (py_api.rs)</li>
<li><code>random_layer</code> (index.rs)</li>
<li><code>load</code> (py_api.rs)</li>
<li><code>insert</code> (py_api.rs)
<details><summary>Insert a vector into the index.</summary>
<div class="doc-comment">
<p>Insert a vector into the index.</p>
<p>ISSUE-007: raises PyValueError on dimension mismatch.</p>
</div>
</details>
</li>
<li><code>search</code> (py_api.rs)
<details><summary>Search for k nearest neighbors.</summary>
<div class="doc-comment">
<p>Search for k nearest neighbors.</p>
<p>ISSUE-007: raises PyValueError on query dimension mismatch.</p>
</div>
</details>
</li>
<li><code>save</code> (py_api.rs)</li>
<li><code>new</code> (index.rs)</li>
<li><code>new</code> (py_api.rs)</li>
<li><code>new_bridge</code> (py_api.rs)
<details><summary>Create a new HNSW bridge with the given embedding dimension.</summary>
<div class="doc-comment">
<p>Create a new HNSW bridge with the given embedding dimension.</p>
<p></p>
<p>Example:</p>
<p>bridge = PyHNSWBridge(dim=384)</p>
</div>
</details>
</li>
<li><code>new</code> (index.rs)</li>
<li><code>__repr__</code> (py_api.rs)</li>
<li><code>normalize_vector</code> (index.rs)
<details><summary>Normalize vector using best available SIMD, with scalar fallback.</summary>
<div class="doc-comment">
<p>Normalize vector using best available SIMD, with scalar fallback.</p>
<p>ISSUE-007: Returns Result — zero/near-zero vector is Err.</p>
</div>
</details>
</li>
<li><code>clear</code> (py_api.rs)</li>
<li><code>cosine</code> (index.rs)
<details><summary>Compute cosine similarity between two vectors.</summary>
<div class="doc-comment">
<p>Compute cosine similarity between two vectors.</p>
<p>ISSUE-007: Returns Result — dimension mismatch is Err.</p>
</div>
</details>
</li>
<li><code>len</code> (index.rs)</li>
<li><code>is_empty</code> (index.rs)</li>
<li><code>default</code> (index.rs)</li>
<li><code>len</code> (py_api.rs)</li>
<li><code>is_empty</code> (py_api.rs)</li>
<li><code>len</code> (py_api.rs)</li>
<li><code>is_empty</code> (py_api.rs)</li>
<li><code>get_node_id</code> (py_api.rs)</li>
<li><code>get_entity_id</code> (py_api.rs)</li>
</ul>
</details>

<details><summary><strong>Constant</strong> (9)</summary>
<ul>
<li><code>MAX_DIM</code> (index.rs)</li>
<li><code>MAX_NODES</code> (index.rs)</li>
<li><code>MAX_M</code> (index.rs)</li>
<li><code>MAX_EF_CONSTRUCTION</code> (index.rs)</li>
<li><code>HNSW_LAYERS</code> (index.rs)</li>
<li><code>BRUTE_FORCE_THRESHOLD</code> (index.rs)</li>
<li><code>FILE_VERSION</code> (index.rs)</li>
<li><code>BRIDGE_MAX_DIM</code> (py_api.rs)
<details><summary>Maximum embedding dimension this bridge accepts.</summary>
<div class="doc-comment">
<p>Maximum embedding dimension this bridge accepts.</p>
<p>384 = MLX embedding dimension (Hermes-3 3B 4bit).</p>
</div>
</details>
</li>
<li><code>BRIDGE_MAX_BATCH_SIZE</code> (py_api.rs) — <span class="doc-comment-inline">Maximum batch size per add_batch() call.</span></li>
</ul>
</details>

<details><summary><strong>Module</strong> (3)</summary>
<ul>
<li><code>tests</code> (index.rs)</li>
<li><code>index</code> (mod.rs)</li>
<li><code>py_api</code> (mod.rs)</li>
</ul>
</details>

<details><summary><strong>Attribute</strong> (19)</summary>
<ul>
<li><code>allow</code> (index.rs)</li>
<li><code>derive</code> (index.rs)</li>
<li><code>cfg</code> (index.rs)</li>
<li><code>test</code> (index.rs)</li>
<li><code>test</code> (index.rs)</li>
<li><code>test</code> (index.rs)</li>
<li><code>test</code> (index.rs)</li>
<li><code>test</code> (index.rs)</li>
<li><code>test</code> (index.rs)</li>
<li><code>test</code> (index.rs)</li>
<li><code>pyclass</code> (py_api.rs)
<details><summary>Low-level HNSW ANN index with PyO3 bindings.</summary>
<div class="doc-comment">
<p>Low-level HNSW ANN index with PyO3 bindings.</p>
<p></p>
<p>Used for MLX embeddings re-ranking. Provides explicit save/load</p>
<p>for mmap-backed persistence.</p>
<p></p>
<p># Example</p>
<p>```python</p>
<p>index = PyHNSWIndex(cache_dir="/tmp/ann_index")</p>
<p>index.insert(1, [0.1] * 384)</p>
<p>results = index.search([0.9] + [0.0] * 383, k=5)</p>
<p>index.save()</p>
<p>```</p>
</div>
</details>
</li>
<li><code>pymethods</code> (py_api.rs)</li>
<li><code>new</code> (py_api.rs)</li>
<li><code>staticmethod</code> (py_api.rs)</li>
<li><code>pyclass</code> (py_api.rs)
<details><summary>Thread-safe HNSW ANN index for LanceDB entity store.</summary>
<div class="doc-comment">
<p>Thread-safe HNSW ANN index for LanceDB entity store.</p>
<p></p>
<p>Wraps `HNSWIndex` with:</p>
<p>- GIL-safe API for Python calls (runs in asyncio.to_thread)</p>
<p>- Batch operations for efficiency</p>
<p>- Entity ID → node ID mapping (LanceDB metadata lives in Python layer)</p>
<p></p>
<p>NOTE: Not safe for concurrent inserts + searches from multiple threads.</p>
<p>LanceDBIdentityStore guarantees single-threaded access via asyncio.to_thread.</p>
</div>
</details>
</li>
<li><code>pymethods</code> (py_api.rs)</li>
<li><code>new</code> (py_api.rs)
<details><summary>Create a new HNSW bridge with the given embedding dimension.</summary>
<div class="doc-comment">
<p>Create a new HNSW bridge with the given embedding dimension.</p>
<p></p>
<p>Example:</p>
<p>bridge = PyHNSWBridge(dim=384)</p>
</div>
</details>
</li>
<li><code>getter</code> (py_api.rs)</li>
<li><code>getter</code> (py_api.rs)</li>
</ul>
</details>



## Metrics

| Metric | Value |
|---|---|
| Files | 3 |
| Total lines | 865 |
| Avg lines/file | 288 |
| Languages | Rust |
| Outgoing deps | 1 |
| Incoming deps | 3 |
| Tier | 2 |

