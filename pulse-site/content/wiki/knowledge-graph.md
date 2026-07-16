+++
title = "knowledge/graph/"
weight = 7
description = "<think> Let me analyze this module carefully.  The module is `knowledge/graph` and it contains: - 3 Python files (backend_protocol.py, router.py, __init__.py) - 824 total lines - No outgoing dependenc..."

[extra]
tier = 2
file_count = 3
total_lines = 824
languages = "Python"
parent_path = "knowledge"
+++

<think>
Let me analyze this module carefully.

The module is `knowledge/graph` and it contains:
- 3 Python files (backend_protocol.py, router.py, __init__.py)
- 824 total lines
- No outgoing dependencies
- No incoming dependencies

Key characteristics:
1. It's a Tier 2 module (not top-level, not leaf-level)
2. No detected dependencies - both incoming and outgoing
3. This is interesting - a module with no dependencies suggests it might be foundational or self-contained

Key symbols:
- Graph Backend - unified protocol implemented by DuckPGQGraph and IOCGraph
- Graph AnalyticsBackend - Page Rank, community detection, top nodes
- Graph TraversalBackend - BFS, DFS, shortest path, pivot
- Similarity Backend - k-NN, ANN, LSH near-duplicate detection
- Router functions that choose/ instantiate different backends
- Various operations like buffer_ioc, upsert_ioc, find_connected, etc.

The module appears to be:
1. A protocol/abstraction layer for graph operations
2. A router that selects appropriate backends (DuckPGQ, LSH, ANN, etc.)
3. Defines interfaces for graph storage, analytics, traversal, and similarity search
4. Has both buffered write interface and analytics read interface

Architectural role:
- This is a "hub" module in terms of defining the protocol - it defines what backends must implement
- It routes operations to different backends based on operation type and dataset characteristics
- It's self-contained with no outgoing dependencies

The module has:
- 6 classes defining protocols
- 45 functions
- 24 methods
- 10 constants

This is a substantial module that defines the contract between graph operations and their implementations.

Now let me write the overview following the rules:


I'll draft an overview that captures the module's essence while meeting the specified guidelines. The module acts as a sophisticated routing mechanism for graph operations, providing a flexible abstraction layer between high-level graph interactions and specific backend implementations. Its core function is to intelligently direct different graph-related tasks to the most appropriate computational backend, ensuring optimal performance across various operation types and dataset scales.

The design emphasizes adaptability, allowing seamless switching between different graph processing strategies without changing the core application logic. By defining clear protocols and routing mechanisms, the module creates a robust, extensible framework for graph computations.
</think>

This module serves as the central protocol layer and runtime router for all graph operations, abstracting away the differences between backend implementations and directing each operation kind—buffered writes, traversal, analytics, and similarity search—to the most appropriate available engine based on dataset size and capability availability. The router examines system resources and operation characteristics to select between DuckPGQ for SQL-based traversal and analytics, USEARCH or DuckDB native vectors for approximate nearest neighbor search, and Rust-based LSH for near-duplicate detection, with automatic fallback paths when certain backends are unavailable. It defines four complementary protocol classes (`GraphBackend`, `GraphTraversalBackend`, `GraphAnalyticsBackend`, `SimilarityBackend`) that concrete implementations must satisfy, establishing a contract for capabilities like buffered IOC accumulation, bulk flush cycles, shortest-path queries, Page Rank computation, and k-NN/ANN search. The module is entirely self-contained at the dependency level (no outbound imports detected) and occupies Tier 2, indicating it sits between foundational infrastructure and higher-level application logic—though the absence of any incoming dependents suggests the current codebase has not yet begun wiring consumers into this abstraction layer. With roughly 824 lines across three files and 45 top-level functions, this is a moderately complex module where the high symbol density reflects its role as a crossroads for routing decisions rather than a repository of heavy computational logic; the actual graph processing delegates to DuckPGQGraph and external libraries like USEARCH and LanceDB.

## Structure

| Language | Files |
|---|---|
| Python | 3 |

### Largest Files

- `backend_protocol.py` (414 lines)
- `router.py` (378 lines)
- `__init__.py` (32 lines)


## Dependencies

No outgoing dependencies detected.

## Dependents

No incoming dependencies detected.

## Key Symbols

<p><strong>Key definitions:</strong></p>
<ul>
<li>
<p><code>GraphAnalyticsBackend</code> (Class) in backend_protocol.py — referenced in 3 files</p>
<details><summary>Graph analytics operations: PageRank, community detection, top nodes.</summary>
<div class="doc-comment">
<p>Graph analytics operations: PageRank, community detection, top nodes.</p>
<p></p>
<p>Implemented by: DuckPGQGraph</p>
<p></p>
<p>M1 8GB: DuckDB SQL analytics, bounded node limits, memory-mapped spill.</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py, quantum_pathfinder.py</li></ul>
</li>
<li>
<p><code>GraphTraversalBackend</code> (Class) in backend_protocol.py — referenced in 3 files</p>
<details><summary>Graph traversal operations: BFS, DFS, shortest path, pivot.</summary>
<div class="doc-comment">
<p>Graph traversal operations: BFS, DFS, shortest path, pivot.</p>
<p></p>
<p>Implemented by: DuckPGQGraph</p>
<p></p>
<p>M1 8GB: bounded fetch (2048 rows/batch), SQL:2023 MATCH or recursive CTE.</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py, quantum_pathfinder.py</li></ul>
</li>
<li>
<p><code>_check_lsh_available</code> (Function) in backend_protocol.py — referenced in 3 files</p>
<details><summary>Check if Rust LSH index is available.</summary></details>
<ul><li class="ref-list">Referenced by: __init__.py, router.py</li></ul>
</li>
<li>
<p><code>GraphBackend</code> (Class) in backend_protocol.py — referenced in 2 files</p>
<details><summary>Unified graph backend protocol — implemented by DuckPGQGraph and IOCGraph.</summary>
<div class="doc-comment">
<p>Unified graph backend protocol — implemented by DuckPGQGraph and IOCGraph.</p>
<p></p>
<p>BUFFERED WRITE INTERFACE (ACTIVE-phase truth-write slot):</p>
<p>buffer_ioc()      — accumulate IOCs, zero I/O until flush</p>
<p>buffer_observation() — accumulate observations</p>
<p>flush_buffers()   — bulk write to storage (called in WINDUP)</p>
<p></p>
<p>ANALYTICS INTERFACE (read-side):</p>
<p>upsert_ioc()      — immediate upsert (analytics path)</p>
<p>upsert_ioc_batch()</p>
<p>upsert_relation()</p>
<p>find_connected()</p>
<p>find_connected_batch()</p>
<p>pivot()</p>
<p></p>
<p>METADATA:</p>
<p>graph_stats()     — {node_count, edge_count}</p>
<p>checkpoint()      — persist WAL to main file</p>
<p>export_stix_bundle()</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py</li></ul>
</li>
<li>
<p><code>SimilarityBackend</code> (Class) in backend_protocol.py — referenced in 2 files</p>
<details><summary>Similarity search operations: k-NN, ANN, LSH near-duplicate detection.</summary>
<div class="doc-comment">
<p>Similarity search operations: k-NN, ANN, LSH near-duplicate detection.</p>
<p></p>
<p>Implemented by:</p>
<p>- _ANNIndex (knowledge/ann_index.py) — USEARCH + LanceDB</p>
<p>- LSHIndex (rust.lsh) — near-duplicate detection</p>
<p></p>
<p>M1 8GB: USEARCH Metal SIMD (~10x faster than brute-force),</p>
<p>MLX cosine re-ranking on GPU, LSH O(1) lookup.</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py</li></ul>
</li>
</ul>

<details><summary><strong>Function</strong> (45)</summary>
<ul>
<li><code>choose_graph_backend</code> (backend_protocol.py)</li>
<li><code>choose_similarity_backend</code> (router.py)</li>
<li><code>choose_analytics_backend</code> (router.py)
<details><summary>Choose analytics backend strategy.</summary>
<div class="doc-comment">
<p>Choose analytics backend strategy.</p>
<p></p>
<p>Args:</p>
<p>dataset_size: Estimated number of nodes. None = auto-detect.</p>
<p></p>
<p>Returns:</p>
<p>"duckpgq_pagerank" | "duckpgq_sql" | "none"</p>
</div>
</details>
</li>
<li><code>instantiate_similarity_backend</code> (router.py)</li>
<li><code>route_operation</code> (router.py)
<details><summary>Route an operation kind to the best backend name.</summary>
<div class="doc-comment">
<p>Route an operation kind to the best backend name.</p>
<p></p>
<p>Args:</p>
<p>operation: One of GraphOperationKind values:</p>
<p>- "buffered_write"</p>
<p>- "traversal"</p>
<p>- "analytics"</p>
<p>- "similarity"</p>
<p></p>
<p>Returns:</p>
<p>Backend identifier string suitable for instantiate_* functions.</p>
</div>
</details>
</li>
<li><code>instantiate_analytics_backend</code> (router.py)</li>
<li><code>_instantiate_usearch_backend</code> (router.py) — <span class="doc-comment-inline">Instantiate _ANNIndex (USEARCH + LanceDB).</span></li>
<li><code>_check_lsh_available</code> (backend_protocol.py) — <span class="doc-comment-inline">Check if Rust LSH index is available.</span></li>
<li><code>choose_buffered_write_backend</code> (router.py)
<details><summary>Choose buffered write backend — DuckPGQGraph (has buffer_ioc since F272).</summary>
<div class="doc-comment">
<p>Choose buffered write backend — DuckPGQGraph (has buffer_ioc since F272).</p>
<p></p>
<p>Note: IOCGraph (Kuzu) is DEPRECATED (F300 consolidation).</p>
<p>DuckPGQGraph now owns the canonical buffered write path.</p>
<p></p>
<p>Returns:</p>
<p>"duckpgq" | "none"</p>
</div>
</details>
</li>
<li><code>_instantiate_lsh_backend</code> (router.py) — <span class="doc-comment-inline">Instantiate LSHIndex from Rust backend.</span></li>
<li><code>choose_traversal_backend</code> (router.py)
<details><summary>Choose traversal backend — always DuckPGQGraph (SQL MATCH / recursive CTE).</summary>
<div class="doc-comment">
<p>Choose traversal backend — always DuckPGQGraph (SQL MATCH / recursive CTE).</p>
<p></p>
<p>Returns:</p>
<p>"duckpgq" | "none"</p>
</div>
</details>
</li>
<li><code>_check_ann_available</code> (backend_protocol.py) — <span class="doc-comment-inline">Check if USEARCH + LanceDB ANN is available.</span></li>
<li><code>_instantiate_duckdb_vector_backend</code> (router.py) — <span class="doc-comment-inline">Use DuckDB native vector search as fallback.</span></li>
<li><code>instantiate_traversal_backend</code> (router.py) — <span class="doc-comment-inline">Instantiate DuckPGQGraph for traversal.</span></li>
<li><code>_check_duckpgq_available</code> (backend_protocol.py)</li>
<li><code>_check_iocgraph_available</code> (backend_protocol.py)</li>
<li><code>_estimate_vector_count</code> (router.py) — <span class="doc-comment-inline">Estimate current vector count from DuckDB ioc_nodes table.</span></li>
<li><code>get_available_backends</code> (backend_protocol.py) — <span class="doc-comment-inline">Return list of available backend kinds.</span></li>
<li><code>_has_buffered_write_support</code> (backend_protocol.py) — <span class="doc-comment-inline">Check if graph implements buffer_ioc + flush_buffers.</span></li>
<li><code>buffer_observation</code> (backend_protocol.py)</li>
<li><code>ann_search</code> (backend_protocol.py)</li>
<li><code>lsh_near_duplicate</code> (backend_protocol.py)</li>
<li><code>upsert_similarity</code> (backend_protocol.py)</li>
<li><code>_is_duckpgq_graph</code> (backend_protocol.py) — <span class="doc-comment-inline">Check if graph is DuckPGQGraph instance.</span></li>
<li><code>_is_ioc_graph</code> (backend_protocol.py) — <span class="doc-comment-inline">Check if graph is IOCGraph instance.</span></li>
<li><code>buffer_ioc</code> (backend_protocol.py)</li>
<li><code>flush_buffers</code> (backend_protocol.py)</li>
<li><code>upsert_ioc</code> (backend_protocol.py)</li>
<li><code>upsert_ioc_batch</code> (backend_protocol.py)</li>
<li><code>upsert_relation</code> (backend_protocol.py)</li>
<li><code>find_connected</code> (backend_protocol.py)</li>
<li><code>find_connected_batch</code> (backend_protocol.py)</li>
<li><code>pivot</code> (backend_protocol.py)</li>
<li><code>graph_stats</code> (backend_protocol.py)</li>
<li><code>checkpoint</code> (backend_protocol.py)</li>
<li><code>export_stix_bundle</code> (backend_protocol.py)</li>
<li><code>find_connected</code> (backend_protocol.py)</li>
<li><code>find_connected_batch</code> (backend_protocol.py)</li>
<li><code>pivot</code> (backend_protocol.py)</li>
<li><code>shortest_path</code> (backend_protocol.py)</li>
<li><code>get_top_nodes_by_degree</code> (backend_protocol.py)</li>
<li><code>pagerank</code> (backend_protocol.py)</li>
<li><code>community_detection</code> (backend_protocol.py)</li>
<li><code>graph_stats</code> (backend_protocol.py)</li>
<li><code>export_edge_list</code> (backend_protocol.py)</li>
</ul>
</details>

<details><summary><strong>Class</strong> (6)</summary>
<ul>
<li><code>GraphBackend</code> (backend_protocol.py)
<details><summary>Unified graph backend protocol — implemented by DuckPGQGraph and IOCGraph.</summary>
<div class="doc-comment">
<p>Unified graph backend protocol — implemented by DuckPGQGraph and IOCGraph.</p>
<p></p>
<p>BUFFERED WRITE INTERFACE (ACTIVE-phase truth-write slot):</p>
<p>buffer_ioc()      — accumulate IOCs, zero I/O until flush</p>
<p>buffer_observation() — accumulate observations</p>
<p>flush_buffers()   — bulk write to storage (called in WINDUP)</p>
<p></p>
<p>ANALYTICS INTERFACE (read-side):</p>
<p>upsert_ioc()      — immediate upsert (analytics path)</p>
<p>upsert_ioc_batch()</p>
<p>upsert_relation()</p>
<p>find_connected()</p>
<p>find_connected_batch()</p>
<p>pivot()</p>
<p></p>
<p>METADATA:</p>
<p>graph_stats()     — {node_count, edge_count}</p>
<p>checkpoint()      — persist WAL to main file</p>
<p>export_stix_bundle()</p>
</div>
</details>
</li>
<li><code>SimilarityBackend</code> (backend_protocol.py)
<details><summary>Similarity search operations: k-NN, ANN, LSH near-duplicate detection.</summary>
<div class="doc-comment">
<p>Similarity search operations: k-NN, ANN, LSH near-duplicate detection.</p>
<p></p>
<p>Implemented by:</p>
<p>- _ANNIndex (knowledge/ann_index.py) — USEARCH + LanceDB</p>
<p>- LSHIndex (rust.lsh) — near-duplicate detection</p>
<p></p>
<p>M1 8GB: USEARCH Metal SIMD (~10x faster than brute-force),</p>
<p>MLX cosine re-ranking on GPU, LSH O(1) lookup.</p>
</div>
</details>
</li>
<li><code>GraphAnalyticsBackend</code> (backend_protocol.py)
<details><summary>Graph analytics operations: PageRank, community detection, top nodes.</summary>
<div class="doc-comment">
<p>Graph analytics operations: PageRank, community detection, top nodes.</p>
<p></p>
<p>Implemented by: DuckPGQGraph</p>
<p></p>
<p>M1 8GB: DuckDB SQL analytics, bounded node limits, memory-mapped spill.</p>
</div>
</details>
</li>
<li><code>GraphTraversalBackend</code> (backend_protocol.py)
<details><summary>Graph traversal operations: BFS, DFS, shortest path, pivot.</summary>
<div class="doc-comment">
<p>Graph traversal operations: BFS, DFS, shortest path, pivot.</p>
<p></p>
<p>Implemented by: DuckPGQGraph</p>
<p></p>
<p>M1 8GB: bounded fetch (2048 rows/batch), SQL:2023 MATCH or recursive CTE.</p>
</div>
</details>
</li>
<li><code>GraphOperationKind</code> (backend_protocol.py) — <span class="doc-comment-inline">ISSUE #14: Operation kinds for stratified backend selection.</span></li>
<li><code>GraphBackendKind</code> (backend_protocol.py) — <span class="doc-comment-inline">Enum-like for graph backend kinds.</span></li>
</ul>
</details>

<details><summary><strong>Method</strong> (24)</summary>
<ul>
<li><code>buffer_observation</code> (backend_protocol.py)</li>
<li><code>ann_search</code> (backend_protocol.py)</li>
<li><code>lsh_near_duplicate</code> (backend_protocol.py)</li>
<li><code>upsert_similarity</code> (backend_protocol.py)</li>
<li><code>buffer_ioc</code> (backend_protocol.py)</li>
<li><code>flush_buffers</code> (backend_protocol.py)</li>
<li><code>upsert_ioc</code> (backend_protocol.py)</li>
<li><code>upsert_ioc_batch</code> (backend_protocol.py)</li>
<li><code>upsert_relation</code> (backend_protocol.py)</li>
<li><code>find_connected</code> (backend_protocol.py)</li>
<li><code>find_connected_batch</code> (backend_protocol.py)</li>
<li><code>pivot</code> (backend_protocol.py)</li>
<li><code>graph_stats</code> (backend_protocol.py)</li>
<li><code>checkpoint</code> (backend_protocol.py)</li>
<li><code>export_stix_bundle</code> (backend_protocol.py)</li>
<li><code>find_connected</code> (backend_protocol.py)</li>
<li><code>find_connected_batch</code> (backend_protocol.py)</li>
<li><code>pivot</code> (backend_protocol.py)</li>
<li><code>shortest_path</code> (backend_protocol.py)</li>
<li><code>get_top_nodes_by_degree</code> (backend_protocol.py)</li>
<li><code>pagerank</code> (backend_protocol.py)</li>
<li><code>community_detection</code> (backend_protocol.py)</li>
<li><code>graph_stats</code> (backend_protocol.py)</li>
<li><code>export_edge_list</code> (backend_protocol.py)</li>
</ul>
</details>

<details><summary><strong>Constant</strong> (10)</summary>
<ul>
<li><code>_DUCKPGQ_AVAILABLE</code> (backend_protocol.py)</li>
<li><code>_IOCGRAPH_AVAILABLE</code> (backend_protocol.py)</li>
<li><code>_ANN_AVAILABLE</code> (backend_protocol.py)</li>
<li><code>_LSH_AVAILABLE</code> (backend_protocol.py)</li>
<li><code>_USEARCH_MIN_VECTORS</code> (router.py)</li>
<li><code>_LSH_THRESHOLD_VECTORS</code> (router.py)</li>
<li><code>_PAGERANK_THRESHOLD_NODES</code> (router.py)</li>
<li><code>_IVFPQ_THRESHOLD_VECTORS</code> (router.py)</li>
<li><code>_SMALL_DATASET</code> (router.py)</li>
<li><code>_MEDIUM_DATASET</code> (router.py)</li>
</ul>
</details>



## Metrics

| Metric | Value |
|---|---|
| Files | 3 |
| Total lines | 824 |
| Avg lines/file | 274 |
| Languages | Python |
| Outgoing deps | 0 |
| Incoming deps | 0 |
| Tier | 2 |

