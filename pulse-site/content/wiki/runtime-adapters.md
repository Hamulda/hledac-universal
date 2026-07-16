+++
title = "runtime/adapters/"
weight = 17
description = "<think> Let me analyze the module structure and purpose from the provided context.  **Module: runtime/adapters**  This module provides adapter/wrapper classes that implement protocol interfaces for di..."

[extra]
tier = 2
file_count = 4
total_lines = 843
languages = "Python"
parent_path = "runtime"
+++

<think>
Let me analyze the module structure and purpose from the provided context.

**Module: runtime/adapters**

This module provides adapter/wrapper classes that implement protocol interfaces for different backend systems. The key components are:

1. **Graph Facade** - A unified facade that consolidates access to three different graph implementations (IOCGraph for analytics, STIX graph for export, and truth-write graph for buffered writes). This is described as F270 Phase 2.

2. **DuckPGQGraph Adapter** - Wraps DuckPGQGraph to implement Graph Protocol

3. **IOCGraph Adapter** - Wraps IOCGraph (Kuzu) to implement Graph Protocol for STIX-compliant truth-write backend

4. **DuckDBStore Adapter** - Wraps DuckDBShadow Store to implement Storage Protocol

5. **Fetch CoordinatorAdapter** - Wraps Fetch Coordinator to implement Fetch Protocol

Key observations:
- This is an adapter pattern module - it wraps existing implementations to conform to protocol interfaces
- It has NO outgoing dependencies (the adapters delegate to their wrapped objects)
- It has NO incoming dependencies detected - interesting, but this might be because the structural analysis tool didn't detect imports yet
- The `__init__` function is referenced in 641 files, which is a massive fan-out - but this seems to be imports of the module itself
- `pivot` is referenced in 68 files
- `async_initialize` is referenced in 19 files

The module:
- Is relatively small (4 files, 843 lines total, ~210 lines/file average)
- Has a central class `GraphFacade` that unifies access to multiple graph backends
- Provides uniform interfaces (protocols) for different storage and graph implementations
- Acts as a bridge layer - consumers can use Graph Protocol/Storage Protocol/Fetch Protocol without knowing the specific backend

The architectural role:
- This is a bridge layer that normalizes access to different backends


- It exhibits a high fan-out, with its initialization referenced across a substantial portion of the codebase
- Despite no detected dependencies, the module plays a critical role in providing standardized interfaces for various system components

I'll focus on how this module serves as a critical abstraction layer, enabling flexible backend integration through protocol-based design.
</think>

This module provides protocol-compliant adapter classes that wrap backend implementations, allowing consumers to interact with storage, graph, and fetch systems through uniform interfaces without coupling to specific implementations. The central abstraction is `GraphFacade`, a unified access layer that consolidates three distinct graph capabilities—analytics queries, STIX export, and buffered truth writes—into a single capability-based interface, eliminating the need for consumers to track which backend holds which graph slot. The module contains five adapter classes: `DuckPGQGraphAdapter` and `IOCGraphAdapter` both implement `GraphProtocol` for different graph engines, `DuckDBStoreAdapter` implements `StorageProtocol` for DuckDB-backed persistence, and `FetchCoordinatorAdapter` implements `FetchProtocol` for work coordination. With 843 total lines across 4 files and 59 function definitions, this module operates as a pure delegation layer with zero outgoing dependencies—adapters simply forward calls to their wrapped instances while exposing consistent protocol contracts. The absence of detected incoming dependencies is notable given the `__init__` function appears referenced across 641 files, suggesting either recent integration or a decoupling phase; regardless, modifications here carry wide blast radius due to the module's foundational role in normalizing backend access throughout the system.

## Structure

| Language | Files |
|---|---|
| Python | 4 |

### Largest Files

- `graph_adapter.py` (653 lines)
- `duckdb_adapter.py` (86 lines)
- `fetch_adapter.py` (66 lines)
- `__init__.py` (38 lines)


## Dependencies

No outgoing dependencies detected.

## Dependents

No incoming dependencies detected.

## Key Symbols

<p><strong>Key definitions:</strong></p>
<ul>
<li>
<p><code>__init__</code> (Function) in graph_adapter.py — referenced in 641 files</p>
<details><summary>Initialize adapter with existing DuckPGQGraph.</summary>
<div class="doc-comment">
<p>Initialize adapter with existing DuckPGQGraph.</p>
<p></p>
<p>Args:</p>
<p>graph: DuckPGQGraph instance to wrap</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py, __main__.py, _base.py, _body_hash.py, _buffer.py +592 more</li></ul>
</li>
<li>
<p><code>pivot</code> (Function) in graph_adapter.py — referenced in 68 files</p>
<ul><li class="ref-list">Referenced by: __init__.py, __main__.py, acquisition_strategy.py, analyst_workbench.py, args.py +62 more</li></ul>
</li>
<li>
<p><code>async_initialize</code> (Function) in duckdb_adapter.py — referenced in 19 files</p>
<details><summary>Initialize underlying store.</summary></details>
<ul><li class="ref-list">Referenced by: __main__.py, analytics_hook.py, benchmark_sprint_probe.py, conftest.py, duckdb_store.py +13 more</li></ul>
</li>
<li>
<p><code>upsert_ioc</code> (Function) in graph_adapter.py — referenced in 14 files</p>
<ul><li class="ref-list">Referenced by: _debug_test.py, _debug_test2.py, _debug_test3.py, _test_graph_debug.py, backend_protocol.py +8 more</li></ul>
</li>
<li>
<p><code>upsert_relation</code> (Function) in graph_adapter.py — referenced in 13 files</p>
<ul><li class="ref-list">Referenced by: _debug_test.py, _debug_test3.py, _test_graph_debug.py, backend_protocol.py, graph_accumulator.py +7 more</li></ul>
</li>
</ul>

<details><summary><strong>Function</strong> (59)</summary>
<ul>
<li><code>upsert_ioc</code> (graph_adapter.py)</li>
<li><code>upsert_relation</code> (graph_adapter.py)</li>
<li><code>record_observation</code> (graph_adapter.py)</li>
<li><code>upsert_ioc</code> (graph_adapter.py)</li>
<li><code>stats</code> (graph_adapter.py) — <span class="doc-comment-inline">Delegate to IOCGraph.graph_stats().</span></li>
<li><code>graph_stats</code> (graph_adapter.py) — <span class="doc-comment-inline">Delegate to IOCGraph.graph_stats().</span></li>
<li><code>pivot</code> (graph_adapter.py)</li>
<li><code>graph_stats</code> (graph_adapter.py) — <span class="doc-comment-inline">STIX graph stats — STIX path.</span></li>
<li><code>export_stix_bundle</code> (graph_adapter.py) — <span class="doc-comment-inline">Export STIX bundle — STIX path.</span></li>
<li><code>buffer_ioc</code> (graph_adapter.py)</li>
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
<li><code>upsert_ioc</code> (graph_adapter.py)</li>
<li><code>buffer_observation</code> (graph_adapter.py)</li>
<li><code>record_observation</code> (graph_adapter.py)</li>
<li><code>record_observation</code> (graph_adapter.py)</li>
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
<li><code>upsert_relation</code> (graph_adapter.py)</li>
<li><code>checkpoint</code> (graph_adapter.py) — <span class="doc-comment-inline">Flush WAL — analytics path.</span></li>
<li><code>async_ingest_findings</code> (duckdb_adapter.py)</li>
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
<li><code>__init__</code> (duckdb_adapter.py)
<details><summary>Initialize adapter with existing DuckDBShadowStore.</summary>
<div class="doc-comment">
<p>Initialize adapter with existing DuckDBShadowStore.</p>
<p></p>
<p>Args:</p>
<p>store: DuckDBShadowStore instance to wrap</p>
</div>
</details>
</li>
<li><code>__init__</code> (fetch_adapter.py)
<details><summary>Initialize adapter with existing FetchCoordinator.</summary>
<div class="doc-comment">
<p>Initialize adapter with existing FetchCoordinator.</p>
<p></p>
<p>Args:</p>
<p>coordinator: FetchCoordinator instance to wrap</p>
</div>
</details>
</li>
<li><code>stats</code> (graph_adapter.py) — <span class="doc-comment-inline">Delegate to DuckPGQGraph.stats().</span></li>
<li><code>graph_stats</code> (graph_adapter.py) — <span class="doc-comment-inline">DuckPGQGraph: graph_stats (F271).</span></li>
<li><code>find_connected</code> (graph_adapter.py)</li>
<li><code>upsert_ioc_batch</code> (graph_adapter.py) — <span class="doc-comment-inline">Delegate batch upsert to IOCGraph.upsert_ioc_batch().</span></li>
<li><code>find_connected_batch</code> (graph_adapter.py)</li>
<li><code>async_initialize</code> (duckdb_adapter.py) — <span class="doc-comment-inline">Initialize underlying store.</span></li>
<li><code>get_semaphore</code> (fetch_adapter.py) — <span class="doc-comment-inline">Return coordinator's semaphore.</span></li>
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
<li><code>async_flush_arrow</code> (duckdb_adapter.py) — <span class="doc-comment-inline">Flush pending Arrow batches.</span></li>
<li><code>open_lmdb</code> (duckdb_adapter.py) — <span class="doc-comment-inline">Delegate LMDB open to store.</span></li>
<li><code>query_sprint_results</code> (duckdb_adapter.py) — <span class="doc-comment-inline">Execute SQL query against store.</span></li>
<li><code>fetch</code> (fetch_adapter.py) — <span class="doc-comment-inline">Delegate fetch to coordinator.</span></li>
<li><code>get_backpressure</code> (fetch_adapter.py) — <span class="doc-comment-inline">Return coordinator's backpressure ratio.</span></li>
<li><code>get_top_nodes_by_degree</code> (graph_adapter.py) — <span class="doc-comment-inline">IOCGraph does not support this — returns []. Use graph_stats().</span></li>
<li><code>export_edge_list</code> (graph_adapter.py) — <span class="doc-comment-inline">IOCGraph does not export edge lists — returns []. Use export_stix_bundle().</span></li>
<li><code>checkpoint</code> (graph_adapter.py) — <span class="doc-comment-inline">IOCGraph does not have a checkpoint — no-op.</span></li>
</ul>
</details>

<details><summary><strong>Class</strong> (5)</summary>
<ul>
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
<li><code>DuckDBStoreAdapter</code> (duckdb_adapter.py)
<details><summary>Adapter wrapping DuckDBShadowStore to implement StorageProtocol.</summary>
<div class="doc-comment">
<p>Adapter wrapping DuckDBShadowStore to implement StorageProtocol.</p>
<p></p>
<p>This adapter is non-breaking — it wraps the existing store</p>
<p>and delegates to it without changing behavior.</p>
<p></p>
<p>Usage:</p>
<p>store = DuckDBShadowStore(...)</p>
<p>adapter = DuckDBStoreAdapter(store)</p>
<p># Use as StorageProtocol</p>
<p>await adapter.async_ingest_findings(findings, sprint_id)</p>
</div>
</details>
</li>
<li><code>FetchCoordinatorAdapter</code> (fetch_adapter.py)
<details><summary>Adapter wrapping FetchCoordinator to implement FetchProtocol.</summary>
<div class="doc-comment">
<p>Adapter wrapping FetchCoordinator to implement FetchProtocol.</p>
<p></p>
<p>Non-breaking: wraps existing FetchCoordinator and delegates</p>
<p>to it without changing behavior.</p>
<p></p>
<p>Usage:</p>
<p>fc = FetchCoordinator(...)</p>
<p>adapter = FetchCoordinatorAdapter(fc)</p>
<p># Use as FetchProtocol</p>
<p>result = await adapter.fetch(work)</p>
</div>
</details>
</li>
</ul>
</details>

<details><summary><strong>Method</strong> (59)</summary>
<ul>
<li><code>upsert_ioc</code> (graph_adapter.py)</li>
<li><code>upsert_relation</code> (graph_adapter.py)</li>
<li><code>record_observation</code> (graph_adapter.py)</li>
<li><code>upsert_ioc</code> (graph_adapter.py)</li>
<li><code>stats</code> (graph_adapter.py) — <span class="doc-comment-inline">Delegate to IOCGraph.graph_stats().</span></li>
<li><code>graph_stats</code> (graph_adapter.py) — <span class="doc-comment-inline">Delegate to IOCGraph.graph_stats().</span></li>
<li><code>pivot</code> (graph_adapter.py)</li>
<li><code>graph_stats</code> (graph_adapter.py) — <span class="doc-comment-inline">STIX graph stats — STIX path.</span></li>
<li><code>export_stix_bundle</code> (graph_adapter.py) — <span class="doc-comment-inline">Export STIX bundle — STIX path.</span></li>
<li><code>buffer_ioc</code> (graph_adapter.py)</li>
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
<li><code>upsert_ioc</code> (graph_adapter.py)</li>
<li><code>buffer_observation</code> (graph_adapter.py)</li>
<li><code>record_observation</code> (graph_adapter.py)</li>
<li><code>record_observation</code> (graph_adapter.py)</li>
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
<li><code>upsert_relation</code> (graph_adapter.py)</li>
<li><code>checkpoint</code> (graph_adapter.py) — <span class="doc-comment-inline">Flush WAL — analytics path.</span></li>
<li><code>async_ingest_findings</code> (duckdb_adapter.py)</li>
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
<li><code>__init__</code> (duckdb_adapter.py)
<details><summary>Initialize adapter with existing DuckDBShadowStore.</summary>
<div class="doc-comment">
<p>Initialize adapter with existing DuckDBShadowStore.</p>
<p></p>
<p>Args:</p>
<p>store: DuckDBShadowStore instance to wrap</p>
</div>
</details>
</li>
<li><code>__init__</code> (fetch_adapter.py)
<details><summary>Initialize adapter with existing FetchCoordinator.</summary>
<div class="doc-comment">
<p>Initialize adapter with existing FetchCoordinator.</p>
<p></p>
<p>Args:</p>
<p>coordinator: FetchCoordinator instance to wrap</p>
</div>
</details>
</li>
<li><code>stats</code> (graph_adapter.py) — <span class="doc-comment-inline">Delegate to DuckPGQGraph.stats().</span></li>
<li><code>graph_stats</code> (graph_adapter.py) — <span class="doc-comment-inline">DuckPGQGraph: graph_stats (F271).</span></li>
<li><code>find_connected</code> (graph_adapter.py)</li>
<li><code>upsert_ioc_batch</code> (graph_adapter.py) — <span class="doc-comment-inline">Delegate batch upsert to IOCGraph.upsert_ioc_batch().</span></li>
<li><code>find_connected_batch</code> (graph_adapter.py)</li>
<li><code>async_initialize</code> (duckdb_adapter.py) — <span class="doc-comment-inline">Initialize underlying store.</span></li>
<li><code>get_semaphore</code> (fetch_adapter.py) — <span class="doc-comment-inline">Return coordinator's semaphore.</span></li>
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
<li><code>async_flush_arrow</code> (duckdb_adapter.py) — <span class="doc-comment-inline">Flush pending Arrow batches.</span></li>
<li><code>open_lmdb</code> (duckdb_adapter.py) — <span class="doc-comment-inline">Delegate LMDB open to store.</span></li>
<li><code>query_sprint_results</code> (duckdb_adapter.py) — <span class="doc-comment-inline">Execute SQL query against store.</span></li>
<li><code>fetch</code> (fetch_adapter.py) — <span class="doc-comment-inline">Delegate fetch to coordinator.</span></li>
<li><code>get_backpressure</code> (fetch_adapter.py) — <span class="doc-comment-inline">Return coordinator's backpressure ratio.</span></li>
<li><code>get_top_nodes_by_degree</code> (graph_adapter.py) — <span class="doc-comment-inline">IOCGraph does not support this — returns []. Use graph_stats().</span></li>
<li><code>export_edge_list</code> (graph_adapter.py) — <span class="doc-comment-inline">IOCGraph does not export edge lists — returns []. Use export_stix_bundle().</span></li>
<li><code>checkpoint</code> (graph_adapter.py) — <span class="doc-comment-inline">IOCGraph does not have a checkpoint — no-op.</span></li>
</ul>
</details>



## Metrics

| Metric | Value |
|---|---|
| Files | 4 |
| Total lines | 843 |
| Avg lines/file | 210 |
| Languages | Python |
| Outgoing deps | 0 |
| Incoming deps | 0 |
| Tier | 2 |

