+++
title = "knowledge/"
weight = 5
description = "<think> The user wants me to create a module overview for the 'knowledge' module based on the provided structural context. Let me analyze the data:  **Key observations:**  1. **Module Purpose**: This ..."

[extra]
tier = 1
file_count = 63
total_lines = 33861
languages = "Python"
has_mermaid = true
+++

<think>
The user wants me to create a module overview for the "knowledge" module based on the provided structural context. Let me analyze the data:

**Key observations:**

1. **Module Purpose**: This is the "knowledge" module - it appears to be a comprehensive data storage and retrieval system for OSINT/findings management. It handles:
 - DuckDB-based findings storage with WAL semantics
 - IOC (Indicators of Compromise) graph storage
 - RAG (Retrieval Augmented Generation) capabilities
 - Quality assessment and deduplication
 - Entity linking and knowledge graph operations
 - Analyst workbench for querying findings

2. **Architecture Role**: This is a foundational/leaf module with NO outgoing dependencies but has 4 incoming dependencies from recon, runtime, and layers modules. It's Tier 1 (core infrastructure).

3. **Scale**: 
 - 63 Python files
 - 33,861 total lines
 - Average of 537 lines per file
 - Sub-modules: graph/ (824 lines), sprint_facts/ (586 lines), explainer/ (207 lines)

4. **Key Components**:
 - `DuckDBShadowStore` - Referenced in 79 files (most critical)
 - `IOCGraph` - Kuzu-backed graph for IOC storage
 - `GraphRAGOrchestrator` - Multi-hop reasoning
 - `GraphService` - DuckPGQGraph backed service
 - `LanceDBIdentityStore` - Entity resolution
 - `AnalystWorkbench` - Read-side facade
 - `RAGEngine` - Context grounding

5. **Storage Stack**:
 - DuckDB for structured analytics
 - LMDB for WAL and dedup caches
 - LanceDB for vector storage
 - Kuzu for IOC graph (truth store)

6. **Complexity**: 
 - Very large module with extensive deduplication, quality assessment, and multi-layered storage mechanisms layered throughout

The module functions as the foundational storage and retrieval layer for OSINT research, combining DuckDB for analytics, Kuzu for IOC relationships, and LanceDB for vector search capabilities. Its core function is serving as the authoritative findings store with integrated deduplication, quality controls, and RAG capabilities, though it remains a specialized system rather than a general-purpose database solution.
</think>

This module serves as the authoritative storage and retrieval layer for OSINT research findings, combining structured analytics with graph-based intelligence operations. It provides multi-backend storage spanning DuckDB for analytical queries, Kuzu for IOC entity graphs, LanceDB for vector similarity, and LMDB for write-ahead logging and deduplication caches.

As a Tier 1 foundational module with zero outgoing dependencies, it sits at the core of the system—consuming no other modules while providing storage, quality gating, and retrieval services to `recon`, `runtime`, and `layers`. The `DuckDBShadowStore` class alone is referenced across 79 files, indicating that changes to this module carry substantial blast radius.

The architecture emphasizes reliability through layered durability: a WAL-first write path writes to LMDB before acknowledging DuckDB, with background recovery mechanisms replaying any pending markers after crashes. Quality assessment operates as a multi-stage gate applying entropy thresholds, URL-based deduplication, and persistent LMDB checks before acceptance.

Beyond simple storage, the module orchestrates complex retrieval patterns—`GraphRAGOrchestrator` performs multi-hop traversal across the knowledge graph, while `AnalystWorkbench` provides an extractive query interface that synthesizes answers from findings without requiring LLM inference. The `NeuromorphicMemoryManager` implements spike-timing-dependent plasticity for pattern consolidation.

With 63 files totaling 33,861 lines, this is the largest module in the codebase by a significant margin, reflecting the breadth of its responsibilities across storage, retrieval, graph analytics, and memory management.

## Dependency Diagram

{% mermaid() %}
graph LR
    m_knowledge["<b>knowledge/</b>"]
    style m_knowledge fill:#a78bfa,color:#0d0d0d,stroke:#a78bfa
    m_recon["recon/"]
    m_recon -->|2| m_knowledge
    m_runtime["runtime/"]
    m_runtime -->|1| m_knowledge
    m_layers["layers/"]
    m_layers -->|1| m_knowledge
    classDef default fill:#1a1a2e,stroke:#a78bfa,color:#e0e0e0
    click m_knowledge "/wiki/knowledge/"
    click m_recon "/wiki/recon/"
    click m_runtime "/wiki/runtime/"
    click m_layers "/wiki/layers/"
{% end %}

## Structure

### Sub-modules

- [**explainer/**](/wiki/knowledge-explainer/) — 3 files, 207 lines (Python)
- [**graph/**](/wiki/knowledge-graph/) — 3 files, 824 lines (Python)
- [**sprint_facts/**](/wiki/knowledge-sprint_facts/) — 5 files, 586 lines (Python)

| Language | Files |
|---|---|
| Python | 63 |

### Directories

| Directory | Files | Lines |
|---|---|---|
| graph/ | 3 | 824 |
| sprint_facts/ | 5 | 586 |
| explainer/ | 3 | 207 |

### Largest Files

- `duckdb_store.py` (9242 lines)
- `lancedb_store.py` (1952 lines)
- `graph_rag.py` (1699 lines)
- `rag_engine.py` (1280 lines)
- `analyst_workbench.py` (1273 lines)
- `hot_edges_cache.py` (971 lines)
- `quality_assessment.py` (956 lines)
- `graph_service.py` (868 lines)
- `dedup.py` (837 lines)
- `ann_index.py` (779 lines)

<details><summary><strong>Show 53 more files</strong></summary>

- `entity_linker.py` (647 lines)
- `lancedb_auto_tuner.py` (582 lines)
- `ioc_graph.py` (571 lines)
- `semantic_store.py` (569 lines)
- `db.py` (512 lines)
- `graph_attachment.py` (507 lines)
- `wal.py` (503 lines)
- `neuromorphic.py` (498 lines)
- `ioc_dedup_adapter.py` (442 lines)
- `evidence_chain.py` (428 lines)
- `graph/backend_protocol.py` (414 lines)
- `semantic_deduplicator.py` (410 lines)
- `duckdb_subprocess_adapter.py` (389 lines)
- `graph/router.py` (378 lines)
- `embedding_dedup_index.py` (373 lines)
- `sprint_diff_engine.py` (365 lines)
- `lmdb_subdb.py` (348 lines)
- `analytics_hook.py` (345 lines)
- `duckdb_audit_store.py` (338 lines)
- `lmdb_boot_guard.py` (300 lines)
- `lancedb_rag_engine.py` (300 lines)
- `lancedb_pool.py` (291 lines)
- `research_memory.py` (287 lines)
- `pq_index.py` (275 lines)
- `duckdb_vector_store.py` (271 lines)
- `__init__.py` (238 lines)
- `duckdb_forensics_store.py` (228 lines)
- `sprint_facts/migration.py` (216 lines)
- `pipelined_ingestor.py` (215 lines)
- `vector_store.py` (202 lines)
- `test_retrieval_boundaries.py` (202 lines)
- `target_memory.py` (187 lines)
- `search_index.py` (184 lines)
- `duckdb_ct_cache_store.py` (183 lines)
- `sprint_seeds_store.py` (171 lines)
- `ioc_pattern_matcher.py` (150 lines)
- `graph_builder.py` (146 lines)
- `assertions.py` (145 lines)
- `finding_envelope.py` (139 lines)
- `sprint_facts/lazy_import.py` (133 lines)
- `sprint_facts/canonical_finding.py` (108 lines)
- `cross_sprint_memory.py` (106 lines)
- `graph_layer.py` (97 lines)
- `explainer/deep.py` (89 lines)
- `sprint_facts/source_attribution.py` (83 lines)
- `semantic_store_buffer.py` (81 lines)
- `sprint_boundary.py` (68 lines)
- `explainer/fast.py` (66 lines)
- `atomic_storage.py` (54 lines)
- `explainer/__init__.py` (52 lines)
- `sprint_facts/__init__.py` (46 lines)
- `context_graph.py` (40 lines)
- `graph/__init__.py` (32 lines)

</details>


## Dependencies

No outgoing dependencies detected.

## Dependents

Used by **4 files** across **3 modules**.

**[recon/](@/wiki/recon.md)** (2 files):
- `identity_stitching_canonical.py`
- `temporal_archaeologist_adapter.py`

**[runtime/](@/wiki/runtime.md)** (1 files):
- `nonfeed_seed_runtime.py`

**[layers/](@/wiki/layers.md)** (1 files):
- `layer_manager.py`



## Circular Dependencies

**3 circular dependencies** involving this module:

1. duckdb_store.py → sprint_boundary.py
2. duckdb_store.py → quality_assessment.py
3. duckdb_store.py → quality_assessment.py


## Key Symbols

<p><strong>Key definitions:</strong></p>
<ul>
<li>
<p><code>DuckDBShadowStore</code> (Class) in duckdb_store.py — referenced in 79 files</p>
<ul><li class="ref-list">Referenced by: __init__.py, __main__.py, _lazy_imports.py, _pipeline_orchestrator.py, _store_stage.py +71 more</li></ul>
</li>
<li>
<p><code>IOCGraph</code> (Class) in ioc_graph.py — referenced in 21 files</p>
<details><summary>Kuzu-backed IOC entity graph with async-safe operations.</summary>
<div class="doc-comment">
<p>Kuzu-backed IOC entity graph with async-safe operations.</p>
<p></p>
<p>GRAPH TRUTH STORE — owns authoritative IOC entity storage.</p>
<p>- buffer_ioc(), flush_buffers(), upsert_ioc_batch(), export_stix_bundle(), pivot()</p>
<p>- NOT analytics backend — DuckPGQGraph serves that role.</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __main__.py, analytics_hook.py, backend_protocol.py, conftest.py, context_graph.py +15 more</li></ul>
</li>
<li>
<p><code>GraphRAGOrchestrator</code> (Class) in graph_rag.py — referenced in 12 files</p>
<details><summary>GraphRAG orchestrator for multi-hop reasoning.</summary>
<div class="doc-comment">
<p>GraphRAG orchestrator for multi-hop reasoning.</p>
<p></p>
<p>ROLE: Consumer/Orchestrator (NOT backend owner)</p>
<p>================================================</p>
<p>- multi-hop graph traversal (consumer přes knowledge_layer)</p>
<p>- NENÍ owner backend storage → persistent_layer (deprecated!)</p>
<p>- NENÍ owner embedding → MLXEmbeddingManager singleton přes _get_embedder()</p>
<p>- NENÍ owner primary retrieval → rag_engine</p>
<p></p>
<p>Performs multi-hop search over knowledge graph to find</p>
<p>relationships that aren't visible in single documents.</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py, assertions.py, dspy_programs.py, explainer.py, graph_layer.py +5 more</li></ul>
</li>
<li>
<p><code>GraphService</code> (Class) in graph_service.py — referenced in 12 files</p>
<details><summary>Instance-isolated graph service with DuckPGQGraph backing.</summary>
<div class="doc-comment">
<p>Instance-isolated graph service with DuckPGQGraph backing.</p>
<p></p>
<p>Instance state:</p>
<p>- _seen_iocs: idempotency set for IOCs (owned by instance)</p>
<p>- _seen_rels: idempotency set for relations (owned by instance)</p>
<p></p>
<p>The DuckPGQGraph backend is NOT stored on the instance — instance methods and</p>
<p>module-level functions alike call module-level _get_graph() for the shared</p>
<p>module-level singleton. This means patching graph_service._get_graph affects</p>
<p>all callers uniformly, which is the intended test isolation mechanism.</p>
<p></p>
<p>Use this class directly for test isolation or cross-sprint tenant isolation.</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: _debug_test.py, _debug_test2.py, _debug_test3.py, _test_graph_debug.py, conftest.py +6 more</li></ul>
</li>
<li>
<p><code>LanceDBIdentityStore</code> (Class) in lancedb_store.py — referenced in 12 files</p>
<details><summary>Identity store using LanceDB for entity resolution.</summary>
<div class="doc-comment">
<p>Identity store using LanceDB for entity resolution.</p>
<p></p>
<p>ROLE: Identity/Entity Store (NOT grounding authority)</p>
<p>====================================================</p>
<p>- entity identity storage (add_entity, search_similar)</p>
<p>- NENÍ owner context grounding → rag_engine</p>
<p>- NENÍ owner document retrieval → rag_engine HNSWVectorIndex</p>
<p>- Embedding policy: MLXEmbeddingManager singleton přes _mlx_embed_manager</p>
<p>- Thermal awareness coupling: volá self._orch._memory_mgr (optional, debt)</p>
<p></p>
<p>Features:</p>
<p>- Hybrid search (vector + FTS)</p>
<p>- Bounded storage</p>
<p>- MLX acceleration for similarity computation</p>
<p>- Fail-safe degradation</p>
<p>- Sprint 76: LMDB embedding cache with float16 quantization (50% RAM savings)</p>
<p>- Sprint 76: Binary embeddings for fast pre-filter (32x compression)</p>
<p>- Sprint 76: MMR diversity filtering</p>
<p>- Sprint 76: Adaptive reranking (ColBERT/FlashRank/MLX)</p>
<p>- Sprint 76: usearch index support (lazy)</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: ane_embedder.py, assertions.py, duckdb_vector_store.py, enhanced_research.py, graph_service.py +6 more</li></ul>
</li>
</ul>

<details><summary><strong>Function</strong> (962)</summary>
<ul>
<li><code>async_ingest_findings_batch</code> (duckdb_store.py)</li>
<li><code>_assess_finding_quality_batch</code> (duckdb_store.py)
<details><summary>Sprint P1-2: Batch quality gate — rayon-parallel via Rust batch_* APIs.</summary>
<div class="doc-comment">
<p>Sprint P1-2: Batch quality gate — rayon-parallel via Rust batch_* APIs.</p>
<p></p>
<p>P1-07: Added IOC-level dedup — extracted IOCs are checked against</p>
<p>Rust MmapIocDedupStore before the finding is accepted.</p>
<p></p>
<p>ISSUE-022: Tries assess_findings_quality_batch() Rust fast path first —</p>
<p>pure-compute decisions (URL fp, normalize, entropy, dedup fp) in a single</p>
<p>rayon pass. Stateful checks (hot_cache, LMDB, semantic dedup) run in Python</p>
<p>after Rust returns.</p>
<p></p>
<p>Falls back to the full per-finding loop if Rust is unavailable or fails.</p>
<p></p>
<p>Bounded: caller should chunk at 4096 max (Rust BATCH_HARD_CAP).</p>
<p>Returns list[FindingQualityDecision] in same order as findings.</p>
<p>Fail-soft: any exception propagates to caller for per-row fallback.</p>
</div>
</details>
</li>
<li><code>_apply_stateful_quality_checks</code> (duckdb_store.py)</li>
<li><code>assess_batch</code> (quality_assessment.py)</li>
<li><code>assess</code> (quality_assessment.py)
<details><summary>Sprint 8W + 8AG + 8AK: Assess a single finding's quality via entropy + dedup.</summary>
<div class="doc-comment">
<p>Sprint 8W + 8AG + 8AK: Assess a single finding's quality via entropy + dedup.</p>
<p></p>
<p>Sprint 8AK: URL-first fingerprint — if a canonical URL is present in</p>
<p>provenance, use it (normalized) as the primary dedup signal, independent</p>
<p>of source_type or payload position. Falls back to payload_text.</p>
<p></p>
<p>Sprint 8AG §6.17: Persistent dedup via LMDB with hot-cache read-through.</p>
<p>Lookup order: hot cache → persistent LMDB → store if miss.</p>
<p>LMDB is the authority; hot cache is a bounded read-through cache.</p>
<p></p>
<p>Returns FindingQualityDecision (frozen, immutable).</p>
<p>Fail-open: any exception → accept with reason="quality_check_error".</p>
<p></p>
<p>Text mapping: URL (if present) or payload_text (if exists and non-empty), else query.</p>
<p>If both are empty, falls back to query (may accept trivially).</p>
</div>
</details>
</li>
<li><code>_assess_finding_quality</code> (duckdb_store.py)
<details><summary>Sprint 8W + 8AG + 8AK: Assess a single finding's quality via entropy + dedup.</summary>
<div class="doc-comment">
<p>Sprint 8W + 8AG + 8AK: Assess a single finding's quality via entropy + dedup.</p>
<p></p>
<p>Sprint 8AK: URL-first fingerprint - if a canonical URL is present in</p>
<p>provenance, use it (normalized) as the primary dedup signal, independent</p>
<p>of source_type or payload position. Falls back to payload_text.</p>
<p></p>
<p>Sprint 8AG §6.17: Persistent dedup via LMDB with hot-cache read-through.</p>
<p>Lookup order: hot cache -&gt; persistent LMDB -&gt; store if miss.</p>
<p>LMDB is the authority; hot cache is a bounded read-through cache.</p>
<p></p>
<p>Returns FindingQualityDecision (frozen, immutable).</p>
<p>Fail-open: any exception -&gt; accept with reason="quality_check_error".</p>
<p></p>
<p>Text mapping: URL (if present) or payload_text (if exists and non-empty), else query.</p>
<p>If both are empty, falls back to query (may accept trivially).</p>
</div>
</details>
</li>
<li><code>build_sprint_brief</code> (analyst_workbench.py)
<details><summary>F204E: Build a model-free analyst brief at sprint teardown.</summary>
<div class="doc-comment">
<p>F204E: Build a model-free analyst brief at sprint teardown.</p>
<p></p>
<p>Generates a summary of sprint results: what changed, strongest evidence,</p>
<p>next best pivots, and open questions. Uses extractive analysis only --</p>
<p>no model loading required.</p>
<p></p>
<p>RAM guard: if governor is critical/emergency, generates minimal brief</p>
<p>from counts only (no graph queries).</p>
<p></p>
<p>F205J: If duckdb_store is available, reads cross-sprint target memory</p>
<p>via get_target_memory_summary(target_id) and incorporates it into</p>
<p>headline, key_findings, and open_questions.</p>
<p></p>
<p>F223F: store_findings_count, when provided, distinguishes runtime findings</p>
<p>(from the current sprint) from store findings (canonical total accepted).</p>
<p>The headline uses runtime findings as "sprint findings"; store findings</p>
<p>are surfaced separately in key_findings when they differ from runtime.</p>
<p></p>
<p>Bounds:</p>
<p>- MAX_BRIEF_FINDINGS = 20</p>
<p>- MAX_BRIEF_CHAINS = 5</p>
<p>- MAX_BRIEF_NEXT_ACTIONS = 10</p>
<p>- MAX_CONTEXT_BYTES = 8192</p>
<p></p>
<p>Args:</p>
<p>sprint_id: Sprint identifier</p>
<p>target_id: Research target (query or canonical target_id)</p>
<p>findings: List of findings from the current sprint run (runtime findings)</p>
<p>graph_signal: Graph signal dict from _get_graph_signal()</p>
<p>governor: Optional M1ResourceGovernor for RAM check</p>
<p>duckdb_store: Optional DuckDBShadowStore for target memory read</p>
<p>store_findings_count: Optional canonical store count of total accepted</p>
<p>findings for this target/sprint. When provided and different from</p>
<p>len(findings), the headline uses runtime findings and store findings</p>
<p>are noted in key_findings when they differ.</p>
</div>
</details>
</li>
<li><code>async_record_canonical_findings_batch_arrow</code> (duckdb_store.py)</li>
<li><code>flush</code> (semantic_store.py)
<details><summary>Batch embed + LanceDB upsert.</summary>
<div class="doc-comment">
<p>Batch embed + LanceDB upsert.</p>
<p></p>
<p>ANE path: CoreMLEmbedder.embed() → CoreML → ANE (F228B, preferred)</p>
<p>CPU fallback: self._model.embed() → FastEmbed onnxruntime</p>
</div>
</details>
</li>
<li><code>_init_connection</code> (duckdb_store.py)
<details><summary>Initialize the DuckDB connection. Must be called from the worker thread.</summary>
<div class="doc-comment">
<p>Initialize the DuckDB connection. Must be called from the worker thread.</p>
<p>Sets up file or :memory: mode, applies PRAGMAs and schema.</p>
<p>For file mode, creates persistent _file_conn (Sprint 7H).</p>
<p></p>
<p>F231: Uses _resolve_duckdb_runtime_settings() for UMA-aware configuration.</p>
<p>DRY: All PRAGMA/SET configuration consolidated in _configure_connection().</p>
</div>
</details>
</li>
<li><code>find_connected_with_lancedb_rerank</code> (graph_service.py)</li>
<li><code>export_findings_to_parquet</code> (duckdb_store.py)</li>
<li><code>ann_search</code> (ann_index.py)</li>
<li><code>summarize_feed_clusters</code> (analyst_workbench.py)
<details><summary>F225E: Deterministic feed cluster summary from findings.</summary>
<div class="doc-comment">
<p>F225E: Deterministic feed cluster summary from findings.</p>
<p></p>
<p>Clusters findings by shared IOC/entity tokens or by source_type+domain</p>
<p>fallback. Feed-heavy runs show compact clusters instead of raw volume.</p>
<p></p>
<p>Bounds:</p>
<p>- max_clusters: max number of clusters (default MAX_FEED_CLUSTERS=20)</p>
<p>- max sample IDs per cluster: MAX_SAMPLE_IDS_PER_CLUSTER=5</p>
<p>- max text per cluster line: MAX_TEXT_PER_CLUSTER=200 chars</p>
<p></p>
<p>No model, no embeddings, no network calls.</p>
<p>Fail-soft: returns ("Feed clustering unavailable",) on any error.</p>
</div>
</details>
</li>
<li><code>__init__</code> (duckdb_store.py)</li>
<li><code>async_bulk_insert_findings</code> (duckdb_store.py)
<details><summary>Sprint F800A: Controller-facing async adapter for bulk findings insert.</summary>
<div class="doc-comment">
<p>Sprint F800A: Controller-facing async adapter for bulk findings insert.</p>
<p></p>
<p>Accepts CanonicalFinding instances OR plain dicts (controller dict format).</p>
<p>Dicts are converted to CanonicalFinding before delegating to the existing</p>
<p>async_record_canonical_findings_batch truth path.</p>
<p></p>
<p>Thread-safe, non-blocking - delegates to async_record_canonical_findings_batch</p>
<p>which uses the single-worker executor.</p>
<p></p>
<p>Args:</p>
<p>findings: List of CanonicalFinding or dict with keys:</p>
<p>finding_id, query, source_type, confidence, ts, provenance.</p>
<p></p>
<p>Returns:</p>
<p>list[ActivationResult] - 1:1 mapping, len(results) == len(findings).</p>
<p>Empty list if input is empty or store is closed.</p>
</div>
</details>
</li>
<li><code>_do_sync_close</code> (duckdb_store.py)
<details><summary>Synchronous full cleanup — called by both close() and aclose().</summary>
<div class="doc-comment">
<p>Synchronous full cleanup — called by both close() and aclose().</p>
<p></p>
<p>Args:</p>
<p>emergency: If True (close() path), skips async graph/semantic closes</p>
<p>since no event loop is guaranteed to be running.</p>
<p>Async cleanup is handled by _do_async_close() in aclose() path.</p>
</div>
</details>
</li>
<li><code>async_replay_single_pending_marker</code> (duckdb_store.py)
<details><summary>Sprint 8H: Replay a single pending marker by finding_id.</summary>
<div class="doc-comment">
<p>Sprint 8H: Replay a single pending marker by finding_id.</p>
<p></p>
<p>Recovery semantics per marker:</p>
<p>1. Marker exists? -&gt; marker_found</p>
<p>2. WAL finding:{id} truth exists? -&gt; wal_truth_found</p>
<p>3. If truth missing -&gt; failure (can't recover)</p>
<p>4. DuckDB write via same safe path as activation</p>
<p>5. Fresh read-back from new connection confirms durability</p>
<p>6. Success -&gt; clear pending marker</p>
<p>7. Failure -&gt; bump retry count; if &gt;= MAX_RETRY_COUNT -&gt; dead-letter</p>
<p></p>
<p>Idempotency: if DuckDB already has the record, consider it a success.</p>
<p></p>
<p>Args:</p>
<p>finding_id: The finding identifier to replay.</p>
<p></p>
<p>Returns:</p>
<p>ReplayResult with all fields populated.</p>
</div>
</details>
</li>
<li><code>initialize</code> (semantic_store.py) — <span class="doc-comment-inline">BOOT — load FastEmbed model + open LanceDB conn.</span></li>
<li><code>async_query_arrow_batches</code> (duckdb_store.py)</li>
<li><code>_canonical_findings_batch_to_activation_results</code> (duckdb_store.py)
<details><summary>Sync batch: CanonicalFinding list -&gt; list[dict] (not ActivationResult, avoid circular import).</summary>
<div class="doc-comment">
<p>Sync batch: CanonicalFinding list -&gt; list[dict] (not ActivationResult, avoid circular import).</p>
<p></p>
<p>Returns one dict per finding in input order.</p>
<p>LMDB WAL uses msgspec.json.encode for provenance serialization.</p>
<p>DuckDB insert uses tuple rows (list of lists).</p>
</div>
</details>
</li>
<li><code>_record_edge_lmdb</code> (hot_edges_cache.py)</li>
<li><code>multi_hop_search</code> (graph_rag.py)
<details><summary>Perform multi-hop search over the knowledge graph with path evidence.</summary>
<div class="doc-comment">
<p>Perform multi-hop search over the knowledge graph with path evidence.</p>
<p></p>
<p>Hop 0: Find starting nodes via semantic search</p>
<p>Hop 1..N: Traverse graph to find related nodes</p>
<p>Synthesis: Return paths with novelty filtering</p>
<p></p>
<p>Args:</p>
<p>query: Search query</p>
<p>hops: Number of hops to traverse (default: 2)</p>
<p>max_nodes: Maximum nodes to return (default: 20)</p>
<p>timeline: Enable timeline mode (default: False)</p>
<p>time_min: ISO date/time filter (inclusive)</p>
<p>time_max: ISO date/time filter (inclusive)</p>
<p>prefer_recent: Prefer newer evidence in ranking</p>
<p>bucket: Time bucketing for timeline ("month" or "year")</p>
<p>max_timeline_points: Max timeline points to return (default: 12, max: 12)</p>
<p></p>
<p>Returns:</p>
<p>Dict with:</p>
<p>- insights: List of relevant facts with path evidence</p>
<p>- paths: List of graph paths with nodes, relations, evidence</p>
<p>- summary_text: Human-readable summary</p>
<p>- novelty_stats: Stats about novelty filtering</p>
<p>- contested: Whether contradictions were found</p>
<p>- counter_paths: Alternative paths (if contested)</p>
<p>- timeline_points: Temporal analysis (if timeline=True)</p>
<p>- drift_events: Detected drift events (if timeline=True)</p>
<p>- narratives: Competing narratives (if contested)</p>
</div>
</details>
</li>
<li><code>_detect_contradictions</code> (graph_rag.py)
<details><summary>Detect contradictions in facts using lightweight heuristics.</summary>
<div class="doc-comment">
<p>Detect contradictions in facts using lightweight heuristics.</p>
<p></p>
<p>Identifies contradictions when:</p>
<p>1. Same (subject, predicate) with different objects</p>
<p>2. Explicit negations in predicates (e.g., "is" vs "is_not")</p>
<p></p>
<p>Args:</p>
<p>facts: List of facts to analyze</p>
<p></p>
<p>Returns:</p>
<p>Tuple of (contested: bool, primary_paths: list, counter_paths: list)</p>
</div>
</details>
</li>
<li><code>async_record_canonical_findings_batch</code> (duckdb_store.py)
<details><summary>Sprint 8P: Batch typed ingest API for CanonicalFinding DTO list.</summary>
<div class="doc-comment">
<p>Sprint 8P: Batch typed ingest API for CanonicalFinding DTO list.</p>
<p></p>
<p>Adapts DTO list -&gt; existing WAL-first batch activation path.</p>
<p>Používá stejný single-thread write executor jako stávající API.</p>
<p></p>
<p>Returns list[ActivationResult] - 1:1 mapping, len(results) == len(findings).</p>
<p>Partial failure: pokud nějaký finding selže, ostatní jsou still processed.</p>
<p>Celý batch neshodí kvůli jednomu vadnému findingu.</p>
</div>
</details>
</li>
<li><code>arrow_fetch_batch</code> (duckdb_store.py)</li>
<li><code>async_record_activation_batch</code> (duckdb_store.py)
<details><summary>Record multiple findings with WAL-first semantics.</summary>
<div class="doc-comment">
<p>Record multiple findings with WAL-first semantics.</p>
<p></p>
<p>Order: LMDB WAL first (via put_many) -&gt; DuckDB second (chunked batch).</p>
<p>Returns one ActivationResult per finding in input order.</p>
<p>Partial failure: if LMDB OK but DuckDB fails for some/all,</p>
<p>those entries get desync=True.</p>
<p></p>
<p>Args:</p>
<p>findings: List of dicts, each must contain:</p>
<p>id, query, source_type, confidence</p>
<p></p>
<p>Returns:</p>
<p>list[ActivationResult] - one per finding</p>
</div>
</details>
</li>
<li><code>async_record_canonical_finding</code> (duckdb_store.py)
<details><summary>Sprint 8P: Typed ingest API for CanonicalFinding DTO.</summary>
<div class="doc-comment">
<p>Sprint 8P: Typed ingest API for CanonicalFinding DTO.</p>
<p></p>
<p>Adapts DTO -&gt; existing WAL-first activation path.</p>
<p>Používá stejný single-thread write executor jako stávající API.</p>
<p></p>
<p>DTO -&gt; storage contract mapping:</p>
<p>finding.finding_id  -&gt; id</p>
<p>finding.query       -&gt; query</p>
<p>finding.source_type -&gt; source_type</p>
<p>finding.confidence  -&gt; confidence</p>
<p>finding.ts          -&gt; ts (in WAL only)</p>
<p>finding.provenance  -&gt; LMDB WAL payload (DuckDB nemá provenance sloupec)</p>
<p>finding.payload_text -&gt; LMDB WAL payload (DuckDB nemá payload_text sloupec)</p>
<p></p>
<p>Returns ActivationResult with same contract as async_record_activation.</p>
<p></p>
<p>Provenance: tvrdý invariant - stored in LMDB WAL payload only</p>
<p>(DuckDB schema nemá provenance_sloupec; backward-compatible,</p>
<p>probe_8l/probe_8h/probe_8f/probe_8b zůstávají kompatibilní)</p>
</div>
</details>
</li>
<li><code>_record_fail_open_batch</code> (duckdb_store.py)</li>
<li><code>multi_hop_search_sync</code> (graph_rag.py)
<details><summary>Synchronous version of multi-hop search with path evidence.</summary>
<div class="doc-comment">
<p>Synchronous version of multi-hop search with path evidence.</p>
<p></p>
<p>Uses search_sync() for synchronous contexts.</p>
<p></p>
<p>Args:</p>
<p>query: Search query</p>
<p>hops: Number of hops to traverse (default: 2)</p>
<p>max_nodes: Maximum nodes to return (default: 20)</p>
<p>timeline: Enable timeline mode (default: False)</p>
<p>time_min: ISO date/time filter (inclusive)</p>
<p>time_max: ISO date/time filter (inclusive)</p>
<p>prefer_recent: Prefer newer evidence in ranking</p>
<p>bucket: Time bucketing for timeline ("month" or "year")</p>
<p>max_timeline_points: Max timeline points to return (default: 12)</p>
<p></p>
<p>Returns:</p>
<p>Dict with insights, paths, summary_text, novelty_stats, contested, counter_paths,</p>
<p>timeline_points (if timeline=True), drift_events (if timeline=True), narratives (if contested)</p>
</div>
</details>
</li>
<li><code>graph_analytics_summary</code> (graph_service.py)</li>
<li><code>_load_embeddings_to_mlx</code> (lancedb_store.py)
<details><summary>Load embeddings to MLX using chunked streaming for M1 8GB safety.</summary>
<div class="doc-comment">
<p>Load embeddings to MLX using chunked streaming for M1 8GB safety.</p>
<p></p>
<p>P6-fix: original loaded ALL embeddings at once (~400MB+ for 100k rows).</p>
<p>Now streams in chunks of _mlx_load_chunk_size rows, building index incrementally.</p>
<p>Memory budget: 10k rows × 256 dims × 4 bytes ≈ 10MB per chunk.</p>
<p>F265FIX: Added RAM guard before loading — skip MLX path when available memory &lt; 3GB.</p>
</div>
</details>
</li>
<li><code>async_initialize</code> (duckdb_store.py)
<details><summary>Async initialize - creates connection on the worker thread.</summary>
<div class="doc-comment">
<p>Async initialize - creates connection on the worker thread.</p>
<p></p>
<p>Optional bounded startup replay runs after connection init, before the store</p>
<p>accepts new activation writes. This integrates the Sprint 8H recovery API</p>
<p>into the real init/startup path.</p>
<p></p>
<p>Args:</p>
<p>replay_pending_limit: Max number of pending markers to replay at startup.</p>
<p>None or 0 = no startup replay.</p>
<p>replay_timeout_s:    Wall-time budget for startup replay in seconds.</p>
<p>If exceeded, replay is stopped and remaining</p>
<p>markers are left for a future recovery run.</p>
<p></p>
<p>Returns:</p>
<p>True if initialization succeeded, False otherwise.</p>
<p>Sidecar is safe to use even if this returns False.</p>
<p></p>
<p>Boot barrier semantics (Sprint 8L):</p>
<p>While startup replay is running, _startup_ready is NOT set.</p>
<p>All async activation write methods check this and refuse to proceed</p>
<p>until the barrier is lifted (or the store is closed).</p>
<p>After bounded replay completes (success, limit, or timeout),</p>
<p>_startup_ready is set and writes are accepted.</p>
<p></p>
<p>NOTE: after aclose(), _closed is True and _initialized is False.</p>
<p>We allow re-initialization by clearing _closed here.</p>
</div>
</details>
</li>
<li><code>score_path</code> (graph_rag.py)
<details><summary>Score a path in the knowledge graph based on:</summary>
<div class="doc-comment">
<p>Score a path in the knowledge graph based on:</p>
<p>- Path length (shorter is better)</p>
<p>- Node relevance to hypothesis (via embeddings)</p>
<p>- Average node credibility</p>
<p></p>
<p>Args:</p>
<p>path: List of node IDs forming the path</p>
<p>hypothesis: The hypothesis to score against</p>
<p>hypothesis_emb: Pre-computed hypothesis embedding (optional)</p>
<p>max_nodes: Maximum nodes to score (budget)</p>
<p></p>
<p>Returns:</p>
<p>Score between 0 and 1</p>
</div>
</details>
</li>
<li><code>_resolve_duckdb_runtime_settings</code> (duckdb_store.py)
<details><summary>Resolve DuckDB runtime settings based on UMA memory pressure state.</summary>
<div class="doc-comment">
<p>Resolve DuckDB runtime settings based on UMA memory pressure state.</p>
<p></p>
<p>DuckDB store receives explicit uma_state from resource_governor/scheduler</p>
<p>- it MUST NOT import heavy runtime schedulers to determine this internally.</p>
<p></p>
<p>Args:</p>
<p>uma_state: One of "WARN", "CRITICAL", "EMERGENCY", or None for normal.</p>
<p>swap_detected: True if system-level swap pressure is detected.</p>
<p></p>
<p>Returns:</p>
<p>dict with keys: memory_limit (str), max_temp (str),</p>
<p>threads (int), preserve_insertion_order (bool),</p>
<p>safe_mode (bool), write_buffer_limit (str),</p>
<p>allocator_flush_threshold (str),</p>
<p>allocator_bulk_dealloc_threshold (str),</p>
<p>enable_fsst_vectors (bool),</p>
<p>temp_file_encryption (bool).</p>
</div>
</details>
</li>
<li><code>upsert_relation</code> (graph_service.py)</li>
<li><code>_graph_ingest_findings</code> (duckdb_store.py)
<details><summary>Background task: ingest findings into IOC graph.</summary>
<div class="doc-comment">
<p>Background task: ingest findings into IOC graph.</p>
<p></p>
<p>Called via _bg_tasks tracking after async_ingest_findings_batch succeeds.</p>
<p>Fail-open: any exception is caught and logged.</p>
<p></p>
<p>Architecture (P0 Batch IOC):</p>
<p>1. Batch extract IOCs from all findings in parallel (4-thread pool)</p>
<p>2. Collect all (ioc_type, value) tuples → batch buffer_ioc calls</p>
<p>3. Collect all observations → batch buffer_observation calls</p>
<p>4. O(n) per-finding extraction → O(1) batched graph writes</p>
</div>
</details>
</li>
<li><code>_sync_record_canonical_findings_batch_arrow</code> (duckdb_store.py)
<details><summary>Sprint P0-4: Arrow zero-copy bulk insert for CanonicalFinding list.</summary>
<div class="doc-comment">
<p>Sprint P0-4: Arrow zero-copy bulk insert for CanonicalFinding list.</p>
<p></p>
<p>MUST be called on the worker thread (thread-affine connection).</p>
<p>Returns (inserted_count, error_type):</p>
<p>- (n, None) on success where n = number of rows in input table</p>
<p>- (0, error_type) on any failure, where error_type is one of:</p>
<p>"pyarrow_not_installed" - pyarrow import failed</p>
<p>"table_build_failed"    - pa.Table.from_arrays failed</p>
<p>"duckdb_insert_failed" - QueryExecutor.insert_findings_bulk_arrow failed</p>
<p></p>
<p>Distinct from the legacy `_canonical_findings_batch_to_activation_results`</p>
<p>in three ways:</p>
<p>1. Builds a single pyarrow.Table with columnar zero-copy arrays.</p>
<p>2. Calls QueryExecutor.insert_findings_bulk_arrow (register + INSERT...SELECT).</p>
<p>3. Does NOT touch LMDB WAL - caller is responsible for that half (or falls</p>
<p>back to the legacy path which does both). This split keeps the Arrow</p>
<p>path optional and side-effect-free at the WAL layer.</p>
<p></p>
<p>Fail-soft: any error returns (0, error_type) and the async wrapper falls back</p>
<p>to legacy. The error_type is used for typed telemetry.</p>
</div>
</details>
</li>
<li><code>_evict_oldest_pending_markers</code> (wal.py)
<details><summary>Evict oldest pending sync markers to enforce MAX_PENDING_SYNC_MARKERS bound.</summary>
<div class="doc-comment">
<p>Evict oldest pending sync markers to enforce MAX_PENDING_SYNC_MARKERS bound.</p>
<p></p>
<p>Removes (total_count - keep_count) oldest markers by timestamp.</p>
<p>Returns number of markers evicted.</p>
<p></p>
<p>M1-safe: uses bounded heap instead of full sort, single write transaction</p>
<p>for all deletions, and processes in chunks to limit memory pressure.</p>
</div>
</details>
</li>
<li><code>_hybrid_retrieve_hnsw</code> (rag_engine.py)
<details><summary>Internal hybrid retrieval using HNSW for dense search.</summary>
<div class="doc-comment">
<p>Internal hybrid retrieval using HNSW for dense search.</p>
<p></p>
<p>ISSUE-021: Paralelní — embed(query) + BM25.build běží concurrent.</p>
<p>ANN HNSW search (Rust, GIL-free) běží sequential po embed.</p>
</div>
</details>
</li>
<li><code>measure_recall</code> (lancedb_auto_tuner.py)
<details><summary>Measure recall@K on a bounded random sample.</summary>
<div class="doc-comment">
<p>Measure recall@K on a bounded random sample.</p>
<p></p>
<p>Returns ``(recall_at_k, avg_search_ms)``. ``recall_at_k`` is in</p>
<p>``[0.0, 1.0]`` (1.0 = perfect overlap with brute-force top-K excluding</p>
<p>the query itself). Returns ``(0.0, 0.0)`` on any failure.</p>
<p></p>
<p>Algorithm:</p>
<p>1. Extract up to ``MAX_BRUTE_FORCE_ROWS`` vectors via ``to_polars()``.</p>
<p>2. Sample ``sample_size`` query vectors (deterministic seed).</p>
<p>3. For each query: compute brute top-(K+1) via numpy matmul, exclude</p>
<p>self, compare with ANN top-K from ``table.search(...).limit(K)``.</p>
<p>4. ``recall = mean(|ANN ∩ brute| / K)``</p>
</div>
</details>
</li>
<li><code>hybrid_retrieve</code> (rag_engine.py)
<details><summary>Retrieve relevant documents using hybrid search (dense + sparse).</summary>
<div class="doc-comment">
<p>Retrieve relevant documents using hybrid search (dense + sparse).</p>
<p></p>
<p>ISSUE-021: Parallel retrieval — embed + BM25 paralelně přes asyncio.gather.</p>
<p>embed(query + docs) a BM25.index_build běží concurrent:</p>
<p>- MLX GPU embed: [query] + [doc_contents] v jednom batch call</p>
<p>- CPU: BM25 add_documents v thread pool</p>
<p>- Po embed dokončení: dense_retrieval + sparse BM25.search → fusion</p>
<p></p>
<p>Args:</p>
<p>query: Search query</p>
<p>documents: List of documents to search</p>
<p>top_k: Number of results to return</p>
<p>filters: Optional metadata filters</p>
<p></p>
<p>Returns:</p>
<p>List of retrieved chunks with scores</p>
</div>
</details>
</li>
<li><code>_canonical_finding_to_activation_result</code> (duckdb_store.py)
<details><summary>Sync wrapper: CanonicalFinding DTO -&gt; ActivationResult dict.</summary>
<div class="doc-comment">
<p>Sync wrapper: CanonicalFinding DTO -&gt; ActivationResult dict.</p>
<p></p>
<p>Sprint 8R: DTO -&gt; storage contract mapping:</p>
<p>finding.finding_id  -&gt; id</p>
<p>finding.query       -&gt; query</p>
<p>finding.source_type -&gt; source_type</p>
<p>finding.confidence  -&gt; confidence</p>
<p>finding.ts          -&gt; ts (DOUBLE in DuckDB)</p>
<p>finding.provenance  -&gt; provenance_json (JSON TEXT in DuckDB via msgspec)</p>
<p>finding.payload_text -&gt; LMDB WAL payload only</p>
<p></p>
<p>LMDB WAL uses msgspec.json.encode for consistent serialization.</p>
<p>DuckDB insert uses tuple row (efficient, not dict list).</p>
</div>
</details>
</li>
<li><code>_sync_record_canonical_findings_batch_arrow_standalone</code> (duckdb_store.py)
<details><summary>Arrow zero-copy fallback for legacy batch path (async_record_canonical_findings_batch).</summary>
<div class="doc-comment">
<p>Arrow zero-copy fallback for legacy batch path (async_record_canonical_findings_batch).</p>
<p></p>
<p>Combines WAL + DuckDB Arrow into a single sync helper so the legacy fallback</p>
<p>path also benefits from zero-copy Arrow INSERT. Replaces the tuple-based</p>
<p>_canonical_findings_batch_to_activation_results path entirely.</p>
<p></p>
<p>MUST be called on the worker thread.</p>
<p>Returns list[dict] with 1:1 mapping.</p>
</div>
</details>
</li>
<li><code>upsert</code> (ann_index.py)
<details><summary>Upsert into both USEARCH (primary) and LanceDB (persistence).</summary>
<div class="doc-comment">
<p>Upsert into both USEARCH (primary) and LanceDB (persistence).</p>
<p></p>
<p>Returns True on success, False on error (fail-open).</p>
<p>Thread-safe via lock.</p>
</div>
</details>
</li>
<li><code>_apply_schema</code> (duckdb_store.py)
<details><summary>Apply multi-statement schema via DuckDB's official tokenizer.</summary>
<div class="doc-comment">
<p>Apply multi-statement schema via DuckDB's official tokenizer.</p>
<p></p>
<p>DuckDB 1.5+ provides ``connection.extract_statements()`` which correctly</p>
<p>parses SQL including semicolons inside string literals.  Primary path uses it.</p>
<p>Fallback regex tokenizer is a proper state-machine (not a single re.split)</p>
<p>that also handles ';'-inside-strings and produces clean statements.</p>
<p></p>
<p>Idempotent: ``CREATE INDEX`` / ``CREATE TABLE`` errors (already exists) are</p>
<p>silenced so schema can be re-applied on every init without complaint.</p>
</div>
</details>
</li>
<li><code>_activation_record_findings_batch</code> (duckdb_store.py)
<details><summary>Sprint 8A: Batch activation - LMDB WAL first, DuckDB second.</summary>
<div class="doc-comment">
<p>Sprint 8A: Batch activation - LMDB WAL first, DuckDB second.</p>
<p></p>
<p>Each finding dict must contain: id, query, source_type, confidence</p>
<p>(id is generated by caller if not present)</p>
<p></p>
<p>Returns dict with keys: lmdb_success, duckdb_success, count,</p>
<p>failed_ids (list of ids that failed)</p>
</div>
</details>
</li>
<li><code>async_record_activation</code> (duckdb_store.py)</li>
<li><code>find_entity_history</code> (graph_service.py)</li>
<li><code>semantic_pivot</code> (semantic_store.py)</li>
<li><code>_get_mlx_chunk_size</code> (lancedb_store.py)
<details><summary>Sprint #15: Adaptive chunk sizing based on current memory pressure.</summary>
<div class="doc-comment">
<p>Sprint #15: Adaptive chunk sizing based on current memory pressure.</p>
<p></p>
<p>Memoizes the result within a loading session — callers get a consistent</p>
<p>chunk size without re-sampling on every chunk iteration.</p>
<p></p>
<p>Returns:</p>
<p>1_000 if state == "emergency" (minimal, fail-safe)</p>
<p>3_000 if state == "critical" (reduced)</p>
<p>5_000 if state == "warn" (moderate)</p>
<p>1_000 if swap_detected (abort mid-load signal — use minimal)</p>
<p>10_000 if state == "ok" / "soft_warn" / error (default, safe)</p>
</div>
</details>
</li>
<li><code>upsert_ioc</code> (graph_service.py)</li>
<li><code>annotate_findings_with_graph_context</code> (graph_attachment.py)
<details><summary>Sprint F193A §1: Read-only enrichment pass — attaches graph context to findings.</summary>
<div class="doc-comment">
<p>Sprint F193A §1: Read-only enrichment pass — attaches graph context to findings.</p>
<p></p>
<p>PURPOSE</p>
<p>-------</p>
<p>Minimal annotation layer that reads persisted findings, queries connected IOCs</p>
<p>from the graph donor backend, and attaches lightweight annotations for</p>
<p>export/report use. Does NOT make DuckDBShadowStore a graph authority.</p>
<p></p>
<p>READ-ONLY SEAM — STORE IS NOT GRAPH TRUTH OWNER</p>
<p>-------------------------------------------------</p>
<p>This method is a thin pass-through to graph donor backend seams:</p>
<p>- get_connected_iocs() for IOC linkage</p>
<p>- get_top_seed_nodes() for degree context</p>
<p>It never writes to the graph. The graph (DuckPGQGraph) remains the analytics</p>
<p>donor backend, not the truth owner.</p>
<p></p>
<p>BEHAVIOR</p>
<p>--------</p>
<p>- Iterates through findings and extracts IOC values</p>
<p>- For each unique IOC, queries get_connected_iocs() from donor graph</p>
<p>- Attaches annotations as lightweight dict (no heavy objects)</p>
<p>- Fail-open: returns original findings unchanged on any error</p>
<p>- Bounded: max_annotations limits work to prevent unbounded work</p>
<p></p>
<p>Args:</p>
<p>findings: List of finding dicts (must have 'id' field).</p>
<p>max_hops: Max traversal depth for find_connected (default 2).</p>
<p>max_annotations: Max number of findings to annotate (default 50).</p>
<p></p>
<p>Returns:</p>
<p>list[dict]: Findings with optional 'graph_annotation' key attached.</p>
<p>Unannotated fields are returned unchanged on failure.</p>
</div>
</details>
</li>
<li><code>async_ingest_dht_metadata</code> (duckdb_store.py)
<details><summary>Sprint F224A: Ingest DHT metadata from torrent discovery.</summary>
<div class="doc-comment">
<p>Sprint F224A: Ingest DHT metadata from torrent discovery.</p>
<p></p>
<p>Args:</p>
<p>metadata: List of DHT metadata dicts with keys:</p>
<p>- infohash: str (required, primary key)</p>
<p>- name: str (optional)</p>
<p>- files: list[str] (optional, stored as JSON)</p>
<p>- size_bytes: int (optional)</p>
<p>- first_seen: float (optional, defaults to now)</p>
<p>- last_seen: float (optional, defaults to now)</p>
<p>- peer_count: int (optional)</p>
<p>- sources: list[str] (optional, stored as JSON)</p>
<p></p>
<p>Returns:</p>
<p>Number of records ingested</p>
</div>
</details>
</li>
<li><code>_traverse_hop_with_paths</code> (graph_rag.py)
<details><summary>Traverse one hop with full path tracking.</summary>
<div class="doc-comment">
<p>Traverse one hop with full path tracking.</p>
<p></p>
<p>Args:</p>
<p>visited: Set of already visited node IDs</p>
<p>hop: Current hop number</p>
<p>max_nodes: Maximum nodes to collect</p>
<p>seed_entities: Entities from seed documents</p>
<p>seed_doc_entities: Entities from the top seed document only</p>
<p>max_edges: Maximum edges to traverse</p>
<p></p>
<p>Returns:</p>
<p>Tuple of (new_facts, new_paths)</p>
</div>
</details>
</li>
<li><code>get_top_entities_for_ghost_global</code> (graph_attachment.py)
<details><summary>Sprint 8TF §2: Bounded read-only seam for ghost_global cross-sprint entity accumulation.</summary>
<div class="doc-comment">
<p>Sprint 8TF §2: Bounded read-only seam for ghost_global cross-sprint entity accumulation.</p>
<p></p>
<p>PURPOSE</p>
<p>-------</p>
<p>Provides a store-facing surface for the ghost_global upsert use case.</p>
<p>__main__.py previously spelunked graph attachment internals directly:</p>
<p>graph.get_nodes()[:100]  ← method does not exist on any graph backend</p>
<p>This method wraps the correct capability query so __main__.py never accesses</p>
<p>_ioc_graph internals for this use case.</p>
<p></p>
<p>STORE IS NOT GRAPH TRUTH OWNER</p>
<p>--------------------------------</p>
<p>The injected graph is the authoritative store (IOCGraph=Kuzu or DuckPGQGraph=DuckDB).</p>
<p>This seam is a thin, fail-open adapter for one specific consumer: ghost_global upsert.</p>
<p>It does NOT make DuckDBShadowStore a graph authority.</p>
<p></p>
<p>PAYLOAD SHAPE</p>
<p>-------------</p>
<p>Returns list[tuple[str, str, float]] — exactly the shape required by</p>
<p>upsert_global_entities(entities: list[tuple[str, str, float]]).</p>
<p>Each tuple: (entity_value, entity_type, confidence_cumulative)</p>
<p></p>
<p>FUTURE OWNER / REMOVAL CONDITION</p>
<p>---------------------------------</p>
<p>- Future graph truth owner: IOCGraph (Kuzu) — should expose this directly</p>
<p>- Removal condition: IOCGraph.get_top_entities_for_ghost_global(n=100)</p>
<p>covers this use case with no remaining __main__.py consumer</p>
<p></p>
<p>CAPABILITY REQUIREMENTS</p>
<p>------------------------</p>
<p>Requires the attached graph to implement get_top_nodes_by_degree(n).</p>
<p>DuckPGQGraph (DuckDB): has this method, returns dicts with value/ioc_type/confidence.</p>
<p>IOCGraph (Kuzu): does NOT have this method — returns [] (fail-open).</p>
<p>Fail-open: returns [] if graph is None or method is absent.</p>
<p></p>
<p>Args:</p>
<p>n: Number of top entities to return (default 100).</p>
<p></p>
<p>Returns:</p>
<p>list[tuple[str, str, float]]: Bounded entity payload for ghost_global upsert.</p>
<p>Returns [] if no graph attached or call fails.</p>
</div>
</details>
</li>
<li><code>summarize_chain_support</code> (evidence_chain.py)
<details><summary>F225C: Produce a deterministic corroboration summary from chains or findings.</summary>
<div class="doc-comment">
<p>F225C: Produce a deterministic corroboration summary from chains or findings.</p>
<p></p>
<p>Accepts:</p>
<p>- list[EvidenceChain]  (serialized chains from evidence_chain registry)</p>
<p>- list[dict]           (finding dicts with source_type)</p>
<p>- list[None]           (fail-soft, returns empty)</p>
<p></p>
<p>Returns dict:</p>
<p>{</p>
<p>"corroboration_level": str,      # none | single_source | multi_source</p>
<p>"source_families": list[str],    # unique families present</p>
<p>"family_counts": dict[str, int], # count per family</p>
<p>"corroboration_summary": list[str],  # human-readable lines (max 10)</p>
<p>}</p>
<p></p>
<p>Fail-soft: returns "none" corroboration for any parsing error.</p>
<p>Bounds: corroboration_summary max 10 lines.</p>
</div>
</details>
</li>
<li><code>reembed_all</code> (lancedb_store.py)
<details><summary>One-shot re-embed admin operation. NOT a per-sprint hot path.</summary>
<div class="doc-comment">
<p>One-shot re-embed admin operation. NOT a per-sprint hot path.</p>
<p></p>
<p>F265X: migrated to polars native path. Uses self._table.to_polars()</p>
<p>to skip the intermediate Arrow allocation that pl.from_arrow(.to_arrow())</p>
<p>required. Falls back to .to_pandas() on polars ImportError or if</p>
<p>.to_polars() itself fails. Polars 1.x + LanceDB ≥0.9.</p>
</div>
</details>
</li>
<li><code>insert_findings_bulk_arrow</code> (duckdb_store.py)
<details><summary>Sprint P0-4: Zero-copy Arrow bulk insert via DuckDB register() + INSERT...SELECT.</summary>
<div class="doc-comment">
<p>Sprint P0-4: Zero-copy Arrow bulk insert via DuckDB register() + INSERT...SELECT.</p>
<p></p>
<p>MUST be called on the worker thread (thread-affine connection).</p>
<p>Returns (row_count, error_type) on success: (n_rows, None).</p>
<p>On any failure returns (0, error_type) where error_type is one of:</p>
<p>"table_none"    - table is None</p>
<p>"num_rows_err"  - failed to read num_rows</p>
<p>"zero_rows"     - table has 0 rows</p>
<p>"no_conn"       - could not acquire connection</p>
<p>"pyarrow_build" - pa.Table.from_arrays failed (inside DuckDB register)</p>
<p>"duckdb_error"  - DuckDB register/execute/unregister failed</p>
<p></p>
<p>Why: executemany with N prepared stmt.execute() Python calls has ~3-5x the</p>
<p>per-row Python overhead of one Arrow register() + one INSERT...SELECT.</p>
<p>Provenance is already serialized in `table` (caller builds pa.array of JSON strs),</p>
<p>so this method does no Python-level encoding.</p>
<p></p>
<p>ON CONFLICT (id) DO NOTHING handles primary-key collisions silently.</p>
<p>The secondary UNIQUE(query, source_type) constraint is NOT protected here;</p>
<p>caller is expected to pre-dedupe or accept the failure (logged + return 0).</p>
</div>
</details>
</li>
<li><code>_derive_target_memory_feedback</code> (analyst_workbench.py)
<details><summary>F226E: Derive next-run advice from target memory history.</summary>
<div class="doc-comment">
<p>F226E: Derive next-run advice from target memory history.</p>
<p></p>
<p>Computed from existing surfaces only (target_memory + findings).</p>
<p>NO new DB API, NO network, NO model.</p>
<p></p>
<p>Returns:</p>
<p>dict with keys:</p>
<p>- repeated_feed_dominance: bool</p>
<p>- prior_nonfeed_weakness: bool</p>
<p>- prior_public_accepted_count: int</p>
<p>- prior_ct_accepted_count: int</p>
<p>- suggested_next_profile: str</p>
<p>- suggested_feed_cap_reason: str</p>
<p>- suggested_nonfeed_lanes: str</p>
</div>
</details>
</li>
<li><code>aiter_recent_findings</code> (duckdb_store.py)</li>
<li><code>search_similar</code> (lancedb_store.py)
<details><summary>Search for similar entities.</summary>
<div class="doc-comment">
<p>Search for similar entities.</p>
<p></p>
<p>Args:</p>
<p>embedding: Query embedding.</p>
<p>text_hint: Optional text query for FTS.</p>
<p>threshold: Similarity threshold (0-1). Applied only for pure vector</p>
<p>results; bypassed for RRF reranked hybrid (RRF is the final ranking).</p>
<p>limit: Maximum results to return.</p>
<p>query_type: Search mode — "auto" delegates to _detect_query_type(),</p>
<p>or explicit "vector"/"fts"/"hybrid". Default "auto".</p>
<p>AREA H+: 2026 cutting-edge — when "hybrid" + FTS available, applies</p>
<p>native RRFReranker (LanceDB 0.8+) for 15-30% better OSINT recall.</p>
<p></p>
<p>Returns:</p>
<p>List of matching entities with similarity scores.</p>
</div>
</details>
</li>
<li><code>_build_raptor_tree</code> (rag_engine.py) — <span class="doc-comment-inline">Build RAPTOR summarization tree. Returns node_id -&gt; RaptorNode dict.</span></li>
<li><code>ask</code> (analyst_workbench.py)
<details><summary>Answer an analyst question using local data sources.</summary>
<div class="doc-comment">
<p>Answer an analyst question using local data sources.</p>
<p></p>
<p>PIPELINE:</p>
<p>1. query_findings() — keyword search over recent findings</p>
<p>2. query_graph() — entity history for key entities in question</p>
<p>3. _extract_answer() — deterministic extractive answer from chunks</p>
<p>4. get_evidence_pointers() — build EvidencePointer list</p>
<p>5. get_related_entities() — build RelatedEntity list</p>
<p>6. (Optional) LLM answer via model_lifecycle.load_model()</p>
<p></p>
<p>Args:</p>
<p>question: Natural language analyst question</p>
<p>use_model: If True, generate LLM answer after extractive</p>
<p>model_name: Model to load (required if use_model=True)</p>
<p></p>
<p>Returns:</p>
<p>AnalystAnswer with extractive_answer always populated.</p>
<p>llm_answer is None unless use_model=True and model loads successfully.</p>
</div>
</details>
</li>
<li><code>_flush_l1_to_lmdb</code> (hot_edges_cache.py)
<details><summary>Drain all dirty entries from L1 write buffer and persist to LMDB.</summary>
<div class="doc-comment">
<p>Drain all dirty entries from L1 write buffer and persist to LMDB.</p>
<p></p>
<p>Opens a SINGLE LMDB write transaction for all pending entries.</p>
<p>Groups by src_id, reads existing neighbor lists, applies deltas as</p>
<p>batched saturating increments, sorts, truncates to MAX_HOT_NEIGHBORS_PER_NODE,</p>
<p>then writes back in one transaction.</p>
<p></p>
<p>Returns True on success, False on any exception (fail-soft).</p>
</div>
</details>
</li>
<li><code>record_edge</code> (hot_edges_cache.py)</li>
<li><code>async_get_previous_findings_for_target</code> (duckdb_store.py)</li>
<li><code>tune_if_due</code> (lancedb_auto_tuner.py)
<details><summary>Decide-and-execute a tune cycle (synchronous core).</summary>
<div class="doc-comment">
<p>Decide-and-execute a tune cycle (synchronous core).</p>
<p></p>
<p>P0-1 + P0-2 Enhancement: Tunes BOTH num_partitions AND num_sub_vectors.</p>
<p></p>
<p>Steps:</p>
<p>1. Update inserts_since_tune counter (in-memory only).</p>
<p>2. If not enabled OR cooldown not satisfied → return early</p>
<p>with ``triggered=False``.</p>
<p>3. Else: measure recall, compute optimal partitions + sub_vectors,</p>
<p>retrain if either changed, persist new state.</p>
<p></p>
<p>Always returns a ``TuneResult``. Never raises. The caller should</p>
<p>apply ``result.new_partitions`` and ``result.new_num_sub_vectors``</p>
<p>to its own state if ``result.changed()``.</p>
</div>
</details>
</li>
<li><code>_run</code> (duckdb_store.py)</li>
<li><code>_schedule_graph_update</code> (duckdb_store.py)
<details><summary>Fire graph update as non-blocking asyncio task (Python 3.10+ safe).</summary>
<div class="doc-comment">
<p>Fire graph update as non-blocking asyncio task (Python 3.10+ safe).</p>
<p></p>
<p>Sprint F241: Writes accepted findings to DuckPGQGraph for cross-sprint</p>
<p>entity accumulation. Graph is ADVISORY ONLY - failures are silently</p>
<p>swallowed.</p>
<p></p>
<p>Sprint F-CLEAN fix: replaced `asyncio.coroutine(_graph_update_task)()`</p>
<p>(removed in Python 3.11) with the modern `async def` +</p>
<p>`loop.run_in_executor()` pattern. M1 EIGHTGB safe - DuckDB sync ops run</p>
<p>in the default ThreadPoolExecutor, not a separate process. Bounded</p>
<p>by `_MAX_INFLIGHT_GRAPH_UPDATES` via the existing `self._bg_tasks`</p>
<p>set (Sprint 8QA), auto-drained on completion.</p>
<p></p>
<p>Sync context (no running event loop - tests / sync CLI / F8H worker</p>
<p>threads) is a no-op; the graph update is advisory and not required</p>
<p>for correctness.</p>
<p></p>
<p>LAZY IMPORT: graph_store accessed here to avoid circular deps</p>
<p>with duckdb_store.</p>
</div>
</details>
</li>
<li><code>search_similar_adaptive</code> (lancedb_store.py)
<details><summary>Hybrid search with adaptive reranking and MMR (Sprint 76).</summary>
<div class="doc-comment">
<p>Hybrid search with adaptive reranking and MMR (Sprint 76).</p>
<p></p>
<p>Args:</p>
<p>query_text: Original query text for reranking.</p>
<p>query_emb: Query embedding vector.</p>
<p>top_k: Number of results to return.</p>
<p></p>
<p>Returns:</p>
<p>List of ranked documents.</p>
</div>
</details>
</li>
<li><code>search_similar</code> (lancedb_store.py)
<details><summary>Semantic search for similar papers.</summary>
<div class="doc-comment">
<p>Semantic search for similar papers.</p>
<p></p>
<p>Args:</p>
<p>query: Search query text.</p>
<p>top_k: Number of results to return.</p>
<p>filters: Optional filters (e.g., {"source": "arxiv"}).</p>
<p>query_type: Search mode — "auto" (default, uses _detect_query_type),</p>
<p>or explicit "vector"/"fts"/"hybrid". AREA H+: "hybrid" applies</p>
<p>native RRFReranker for 15-30% better recall on academic text.</p>
<p></p>
<p>Returns:</p>
<p>List of AcademicPaper instances.</p>
</div>
</details>
</li>
<li><code>_flush_l1_to_lmdb_from_drain</code> (hot_edges_cache.py)
<details><summary>F271: Persist pre-drained dirty entries from Rust L1 to LMDB.</summary>
<div class="doc-comment">
<p>F271: Persist pre-drained dirty entries from Rust L1 to LMDB.</p>
<p></p>
<p>This is the second half of flush_to_lmdb() — the Rust buffer has already</p>
<p>been drained (so dirty_count is 0), and this function applies the merge</p>
<p>logic (group by src_id, merge with existing neighbors, saturating</p>
<p>increment, sort, truncate, write).</p>
<p></p>
<p>Args:</p>
<p>dirty: List of (src_id, dst_id, count) tuples as returned by</p>
<p>HotEdgeCounterRust.flush_to_lmdb().</p>
<p></p>
<p>Returns True on success, False on any exception (fail-soft).</p>
</div>
</details>
</li>
<li><code>_wal_delete_mode</code> (duckdb_store.py)
<details><summary>F275-2: Context manager — WAL→DELETE journal mode switch for bulk inserts.</summary>
<div class="doc-comment">
<p>F275-2: Context manager — WAL→DELETE journal mode switch for bulk inserts.</p>
<p></p>
<p>For bulk inserts (≥CHUNK_SIZE=2048), temporarily switch from WAL to DELETE</p>
<p>journal mode. WAL mode costs 2× fsync per write (WAL write + DB write);</p>
<p>DELETE costs 1× fsync. M1 SSD is safe for DELETE — single write is sufficient.</p>
<p></p>
<p>The LMDB WAL layer is unaffected (separate journal).</p>
<p></p>
<p>Restores WAL on exit regardless of success/failure.</p>
<p>Fail-soft: any error is logged and swallowed — caller continues.</p>
<p></p>
<p>P2-22 FIX: Cache original_mode on the connection object so subsequent</p>
<p>calls within the same session skip the PRAGMA query (2 round-trips saved</p>
<p>per chunk). The cache is stored on the QueryExecutor instance, which is</p>
<p>a process-wide singleton per DuckDBShadowStore instance.</p>
</div>
</details>
</li>
<li><code>_acquire_process_lock</code> (duckdb_store.py)
<details><summary>F269: Process-level lock using GraphLockManager (consolidated from F266-U5).</summary>
<div class="doc-comment">
<p>F269: Process-level lock using GraphLockManager (consolidated from F266-U5).</p>
<p></p>
<p>Uses GraphLockManager singleton per db_path — same fcntl.flock-based locking</p>
<p>as DuckPGQGraph. This unifies the 3 independent locking strategies into one.</p>
<p></p>
<p>Three-tier locking strategy:</p>
<p>1. 'excl' — we are the exclusive writer (lock acquired)</p>
<p>2. 'ro'  — another process holds the lock, open READ-ONLY</p>
<p>3. None  — lock unavailable, fall back to :memory:</p>
<p></p>
<p>Returns:</p>
<p>tuple: (lock_mode: str, message: str)</p>
</div>
</details>
</li>
<li><code>_get_cached_embedding</code> (lancedb_store.py) — <span class="doc-comment-inline">Get embedding from LMDB cache with writeback buffer.</span></li>
<li><code>_generate_timeline</code> (graph_rag.py)
<details><summary>Generate timeline points from facts.</summary>
<div class="doc-comment">
<p>Generate timeline points from facts.</p>
<p></p>
<p>Args:</p>
<p>facts: Facts with timestamps</p>
<p>bucket: Time bucketing ("month" or "year")</p>
<p>max_points: Maximum timeline points (hard limit: 12)</p>
<p></p>
<p>Returns:</p>
<p>List of timeline points</p>
</div>
</details>
</li>
<li><code>_normalize_osint_url</code> (quality_assessment.py)
<details><summary>Sprint 8AK: Normalize an OSINT URL for deterministic dedup fingerprinting.</summary>
<div class="doc-comment">
<p>Sprint 8AK: Normalize an OSINT URL for deterministic dedup fingerprinting.</p>
<p></p>
<p>Rules:</p>
<p>- lowercase scheme + host</p>
<p>- strip fragment (#...)</p>
<p>- strip trailing slash from non-root paths</p>
<p>- remove common tracking query params (utm_source, utm_medium, utm_campaign, ref, etc.)</p>
<p>- preserve query params that may affect content identity</p>
<p></p>
<p>Returns normalized URL string.</p>
</div>
</details>
</li>
<li><code>init</code> (ann_index.py)
<details><summary>Initialize LanceDB connection and table.</summary>
<div class="doc-comment">
<p>Initialize LanceDB connection and table.</p>
<p></p>
<p>Returns True on success, False on any error.</p>
<p>Stores error string in _boot_error on failure.</p>
</div>
</details>
</li>
<li><code>get_top_seed_nodes</code> (graph_attachment.py)
<details><summary>Sprint 8TF §1: Export-facing read-only seam for top seed nodes.</summary>
<div class="doc-comment">
<p>Sprint 8TF §1: Export-facing read-only seam for top seed nodes.</p>
<p></p>
<p>PURPOSE</p>
<p>-------</p>
<p>Provides a store-facing surface for the export handoff's seed-node use case.</p>
<p>export_sprint() currently falls back to store._ioc_graph.get_top_nodes_by_degree(n=5)</p>
<p>directly; this method wraps that call so export consumers don't need to spelunk</p>
<p>_ioc_graph internals.</p>
<p></p>
<p>STORE IS NOT GRAPH TRUTH OWNER</p>
<p>--------------------------------</p>
<p>The injected graph may be IOCGraph (Kuzu, truth) or DuckPGQGraph (donor/alternate).</p>
<p>This seam does NOT make DuckDBShadowStore a graph authority.</p>
<p>It is a thin, fail-open adapter for one specific export-facing read-only operation.</p>
<p></p>
<p>FUTURE OWNER / REMOVAL CONDITION</p>
<p>---------------------------------</p>
<p>- Future graph truth owner: IOCGraph (Kuzu) or its successor</p>
<p>- Removal condition: export_sprint() replaces its store._ioc_graph fallback</p>
<p>entirely with this method, AND no other consumer accesses _ioc_graph directly</p>
<p>for seed node queries</p>
<p></p>
<p>CAPABILITY REQUIREMENTS</p>
<p>-----------------------</p>
<p>Requires the attached graph to implement get_top_nodes_by_degree(n).</p>
<p>IOCGraph (Kuzu): has this method.</p>
<p>DuckPGQGraph (DuckDB): has this method.</p>
<p>If the method is absent or call fails, returns [] (fail-open).</p>
<p></p>
<p>Args:</p>
<p>n: Number of top nodes to return (default 5).</p>
<p></p>
<p>Returns:</p>
<p>list[dict]: Each dict has at least "value" and "ioc_type" keys.</p>
<p>Returns [] if no graph attached or call fails.</p>
</div>
</details>
</li>
<li><code>extract_iocs_from_texts</code> (duckdb_store.py)
<details><summary>Extract IOCs from a list of texts using Rust's batch_ioc_extract_unified.</summary>
<div class="doc-comment">
<p>Extract IOCs from a list of texts using Rust's batch_ioc_extract_unified.</p>
<p>Yields (ioc_value, ioc_type) tuples lazily — no intermediate flat list allocated.</p>
<p></p>
<p>PAR-1 P2 / F266-2.3: Three-tier fallback chain for zero-copy memory:</p>
<p></p>
<p>Tier 1 — batch_ioc_extract_unified_python (F266-2.3):</p>
<p>Zero-copy path. Rust writes results directly into Python heap</p>
<p>via PyList::append / PyTuple::new — no intermediate Rust</p>
<p>Vec&lt;(String,String)&gt; that Python must copy.</p>
<p></p>
<p>Tier 2 — batch_ioc_extract_unified (rayon Vec return):</p>
<p>Original path. Rust collects results in Vec&lt;Vec&lt;…&gt;&gt; then</p>
<p>PyO3 auto-converts to Python list.  Tuples are copied by</p>
<p>PyO3 at the GIL boundary (PyTuple::new for each element).</p>
<p></p>
<p>Tier 3 — pure Python ioc_qs.extract_iocs_from_text:</p>
<p>Slowest; used only when Rust is unavailable.</p>
<p></p>
<p>Args:</p>
<p>texts: List of text strings to scan for IOCs.</p>
<p></p>
<p>Yields:</p>
<p>Tuples of (ioc_value, ioc_type) from all texts combined.</p>
<p>IOC types: ipv4, ipv6, domain, md5, sha1, sha256, email, cve.</p>
</div>
</details>
</li>
<li><code>_configure_connection</code> (duckdb_store.py)
<details><summary>Apply all PRAGMAs/SETs to a DuckDB connection. DRY — called once per connection</summary>
<div class="doc-comment">
<p>Apply all PRAGMAs/SETs to a DuckDB connection. DRY — called once per connection</p>
<p>in _init_connection.</p>
<p></p>
<p>F231: UMA-aware configuration via _resolve_duckdb_runtime_settings().</p>
<p>F265B: WAL pragmas (file-backed DB only; N/A for :memory:).</p>
<p>F273F: madvise/F_NOCACHE for zero-copy mmap reads (file-backed only).</p>
<p>Idempotent — safe to call on any freshly-connected DuckDB connection.</p>
</div>
</details>
</li>
<li><code>async_get_recent_findings</code> (duckdb_store.py)
<details><summary>Sprint F800A: Controller-facing async adapter for recent findings.</summary>
<div class="doc-comment">
<p>Sprint F800A: Controller-facing async adapter for recent findings.</p>
<p></p>
<p>Thin wrapper around async_query_recent_findings - converts raw dict rows</p>
<p>to CanonicalFinding instances so callers receive typed DTOs.</p>
<p></p>
<p>Thread-safe, non-blocking - runs on duckdb_worker via run_in_executor.</p>
<p>Returns empty list if store is closed or uninitialized.</p>
<p></p>
<p>Args:</p>
<p>limit: Maximum number of findings to return (ordered by ts DESC).</p>
<p></p>
<p>Returns:</p>
<p>list[CanonicalFinding] - ordered by ts descending, most recent first.</p>
</div>
</details>
</li>
<li><code>invariant_validate</code> (duckdb_store.py)
<details><summary>Validate hardening invariants.</summary>
<div class="doc-comment">
<p>Validate hardening invariants.</p>
<p></p>
<p>Returns dict with keys:</p>
<p>- has_no_gpu_pragma: bool</p>
<p>- memory_limit_ok: bool (1GB or less)</p>
<p>- temp_size_ok: bool (1GB or 0GB for :memory:)</p>
<p>- temp_dir_on_ramdisk: bool (temp_dir under RAMDISK_ROOT if set)</p>
</div>
</details>
</li>
<li><code>_mlx_rerank</code> (ann_index.py)</li>
<li><code>_sync_insert_sprint_delta</code> (duckdb_store.py)
<details><summary>Sync insert - MUST be called on the worker thread.</summary>
<div class="doc-comment">
<p>Sync insert - MUST be called on the worker thread.</p>
<p></p>
<p>Sprint F192F §2: uses findings_per_minute (matches sprint_scorecard naming).</p>
</div>
</details>
</li>
<li><code>_binary_prefilter</code> (lancedb_store.py)
<details><summary>Fast pre-filter using binary embeddings (Hamming distance).</summary>
<div class="doc-comment">
<p>Fast pre-filter using binary embeddings (Hamming distance).</p>
<p></p>
<p>Tier 0: Rust SIMD hamming (batch_hamming_scores) — correct bit-level popcount</p>
<p>Tier 1: MLX fallback with popcount lookup table — correct bit-level popcount</p>
<p></p>
<p>BUG FIX (vs dead-code path):</p>
<p>- Old: checked _binary_embeddings (always None) → early return</p>
<p>- Old: mx.sum(xor_res, axis=1) summed bytes, not bits — WRONG</p>
<p>- Now: operates on _mlx_embeddings (always populated when binary path runs)</p>
<p>- Now: uses popcount (Rust or MLX lookup) for correct bit-level Hamming</p>
</div>
</details>
</li>
<li><code>_calculate_centrality_igraph</code> (graph_rag.py)
<details><summary>Calculate all centrality metrics via igraph C-core.</summary>
<div class="doc-comment">
<p>Calculate all centrality metrics via igraph C-core.</p>
<p></p>
<p>Returns {node_id: {degree, betweenness, closeness, eigenvector, pagerank}}.</p>
<p>Falls back to empty dict on error.</p>
</div>
</details>
</li>
<li><code>query_duckdb</code> (db.py)</li>
<li><code>_do_async_close</code> (duckdb_store.py)
<details><summary>Async graph/semantic store close — properly awaits coroutines.</summary>
<div class="doc-comment">
<p>Async graph/semantic store close — properly awaits coroutines.</p>
<p></p>
<p>Called only from aclose() path where an event loop is guaranteed to exist.</p>
<p>Extracts and awaits all async close() calls that _do_sync_close skips</p>
<p>when emergency=True.</p>
</div>
</details>
</li>
<li><code>_wal_put_many_sync</code> (duckdb_store.py)
<details><summary>Sprint P1-2: WAL-only sync helper - DuckDB Single-Writer Variant 2.</summary>
<div class="doc-comment">
<p>Sprint P1-2: WAL-only sync helper - DuckDB Single-Writer Variant 2.</p>
<p></p>
<p>Runs on _wal_executor. LMDB WAL is pure I/O so executor occupancy is brief.</p>
<p>Caller is responsible for DuckDB step (separate executor, sequential invariant).</p>
<p></p>
<p>Returns True if WAL succeeded for all findings.</p>
</div>
</details>
</li>
<li><code>rrf_rank_findings</code> (duckdb_store.py)
<details><summary>Sprint 8TC B.1: Reciprocal Rank Fusion přes 4 signály.</summary>
<div class="doc-comment">
<p>Sprint 8TC B.1: Reciprocal Rank Fusion přes 4 signály.</p>
<p></p>
<p>Signály:</p>
<p>1. semantic_score  - z LanceDB ANN (pokud dostupný)</p>
<p>2. pattern_count   - počet pattern matche</p>
<p>3. ioc_degree      - počet navázaných IOC uzlů</p>
<p>4. recency_score   - inverzní age (novější = vyšší)</p>
<p></p>
<p>SQL RRF: SUM(1.0 / (k + rank_i)) přes všechny signály.</p>
<p>Chybějící sloupce se přidávají dynamicky přes ALTER TABLE.</p>
<p></p>
<p>Args:</p>
<p>query: Search query string to filter canonical_findings</p>
<p>k: RRF constant (default 30 - snižuje vliv nízkých ranků)</p>
<p></p>
<p>Returns:</p>
<p>list[dict] s keys: finding_id, content, rrf_score, semantic_score,</p>
<p>pattern_count, ioc_degree, ts</p>
</div>
</details>
</li>
<li><code>_wal_evict_oldest_pending_markers</code> (duckdb_store.py)
<details><summary>P0-9 fix: Evict oldest pending sync markers to enforce MAX_PENDING_SYNC_MARKERS bound.</summary>
<div class="doc-comment">
<p>P0-9 fix: Evict oldest pending sync markers to enforce MAX_PENDING_SYNC_MARKERS bound.</p>
<p></p>
<p>Removes (total_count - keep_count) oldest markers by timestamp.</p>
<p>Returns number of markers evicted.</p>
</div>
</details>
</li>
<li><code>__init__</code> (neuromorphic.py)</li>
<li><code>_sync_query_top_entities_by_sprint</code> (duckdb_store.py) — <span class="doc-comment-inline">Sync - MUST be called on worker thread.</span></li>
<li><code>_bounded_startup_replay</code> (duckdb_store.py)
<details><summary>Sprint 8L: Time-boxed startup replay integrated into async_initialize.</summary>
<div class="doc-comment">
<p>Sprint 8L: Time-boxed startup replay integrated into async_initialize.</p>
<p></p>
<p>Scans pending_duckdb_sync:* markers, replays up to replay_pending_limit</p>
<p>of them, and respects replay_timeout_s wall-time budget.</p>
<p></p>
<p>Boot barrier: _startup_ready is NOT set during replay, so activation</p>
<p>writes are held off until replay completes or times out.</p>
<p></p>
<p>Kooperativní yield: asyncio.sleep(0) between chunks to avoid</p>
<p>starving the event loop during long replay runs.</p>
<p></p>
<p>Args:</p>
<p>replay_pending_limit: Maximum markers to replay</p>
<p>replay_timeout_s:    Wall-time budget in seconds</p>
</div>
</details>
</li>
<li><code>_init_mmap_ioc_dedup_store</code> (dedup.py)
<details><summary>Initialize Rust MmapIocDedupStore for persistent IOC dedup.</summary>
<div class="doc-comment">
<p>Initialize Rust MmapIocDedupStore for persistent IOC dedup.</p>
<p></p>
<p>F267: Mmap-backed IOC dedup replaces LMDB-based IOC dedup.</p>
<p>Persists across process restarts with zero warm-up cost.</p>
<p>M1 8GB safe: demand-paged, HashSet rebuilt on load.</p>
<p></p>
<p>G-9 FIX (2026-07-06): Clarified that signature drift reported in</p>
<p>G-9 was a false alarm. Rust MmapIocDedupStore.add() and Python-side</p>
<p>_PythonMmapIocDedupStore.add() both accept</p>
<p>(value: str, ioc_type_str: str, confidence: float) — NO drift.</p>
<p>The G-9 comment referred to the fallback PATH, not signature mismatch.</p>
<p></p>
<p>Fails softly: falls back to pure-Python _PythonMmapIocDedupStore</p>
<p>if Rust unavailable. Any exception stored in _ioc_dedup_store_error.</p>
</div>
</details>
</li>
<li><code>_detect_drift</code> (graph_rag.py)
<details><summary>Detect drift events - when claims about same (subject, predicate) change over time.</summary>
<div class="doc-comment">
<p>Detect drift events - when claims about same (subject, predicate) change over time.</p>
<p></p>
<p>Args:</p>
<p>facts: Facts to analyze</p>
<p>bucket: Time bucketing for detecting change points</p>
<p></p>
<p>Returns:</p>
<p>List of drift events (max 10)</p>
</div>
</details>
</li>
<li><code>__init__</code> (ann_index.py)</li>
<li><code>_build_usearch_index</code> (ann_index.py) — <span class="doc-comment-inline">Build USEARCH index from LanceDB data (M1 Metal SIMD accelerated).</span></li>
<li><code>async_replay_all_pending_duckdb_sync</code> (duckdb_store.py)
<details><summary>Sprint 8H: Replay all pending markers with chunking and event-loop yields.</summary>
<div class="doc-comment">
<p>Sprint 8H: Replay all pending markers with chunking and event-loop yields.</p>
<p></p>
<p>Uses per-instance replay lock to prevent concurrent replay of same markers.</p>
<p>Processes markers in chunks of REPLAY_CHUNK_SIZE, yielding to event loop</p>
<p>between chunks to avoid starving live operations.</p>
<p></p>
<p>Idempotency: markers that already exist in DuckDB are treated as success.</p>
<p></p>
<p>Args:</p>
<p>limit: Optional maximum number of markers to replay. None = all.</p>
<p></p>
<p>Returns:</p>
<p>list[ReplayResult], one per processed marker.</p>
</div>
</details>
</li>
<li><code>load_index</code> (rag_engine.py)
<details><summary>Load index from disk.</summary>
<div class="doc-comment">
<p>Load index from disk.</p>
<p></p>
<p>Args:</p>
<p>path: Path to load index from. Uses index_path from init if not provided.</p>
</div>
</details>
</li>
<li><code>upsert_ioc_batch</code> (graph_service.py)
<details><summary>Batch upsert IOCs — single DuckDB round-trip for N rows.</summary>
<div class="doc-comment">
<p>Batch upsert IOCs — single DuckDB round-trip for N rows.</p>
<p></p>
<p>Idempotency is enforced via _seen_iocs (in-memory dedup set) so duplicate</p>
<p>values within a sprint are filtered before the batch is sent to DuckDB.</p>
<p></p>
<p>Args:</p>
<p>rows: List of (value, ioc_type, confidence, source) tuples.</p>
<p>Returns:</p>
<p>Number of rows passed to DuckDB (not number actually inserted).</p>
</div>
</details>
</li>
<li><code>lookup_persistent_dedup</code> (dedup.py)
<details><summary>Lookup a fingerprint in the persistent dedup LMDB.</summary>
<div class="doc-comment">
<p>Lookup a fingerprint in the persistent dedup LMDB.</p>
<p></p>
<p>P1-4: Bloom filter pre-check — O(1) negative dedup, skip LMDB if Bloom says "not seen".</p>
<p>LMDB remains authoritative for positive matches.</p>
<p></p>
<p>F272: Lazy init — each sub-system initializes on first actual use, not at sprint start.</p>
<p>Saves ~2s from sprint boot when dedup LMDB mmap files are cold.</p>
<p></p>
<p>Args:</p>
<p>fp: 32-char BLAKE2b fingerprint hex string</p>
<p></p>
<p>Returns:</p>
<p>finding_id string if found, None otherwise (miss or LMDB unavailable)</p>
</div>
</details>
</li>
<li><code>_upsert_ioc_batch_sync</code> (ioc_graph.py)
<details><summary>Synchronous batch upsert — runs on _executor thread.</summary>
<div class="doc-comment">
<p>Synchronous batch upsert — runs on _executor thread.</p>
<p></p>
<p>N+1 elimination via UNWIND batch queries:</p>
<p>Phase 1: 1 query — UNWIND batch existence check</p>
<p>Phase 3: 1 query — UNWIND batch CREATE for new nodes</p>
<p>Phase 4: 1 query — UNWIND batch SET last_seen for existing nodes</p>
<p>Total: 3 queries regardless of batch size (was 2N+1).</p>
</div>
</details>
</li>
<li><code>embed_query</code> (semantic_store.py)
<details><summary>Embed a single query string — uses MLX path if available.</summary>
<div class="doc-comment">
<p>Embed a single query string — uses MLX path if available.</p>
<p></p>
<p>Returns:</p>
<p>ndarray dtype=float32, shape=(384,)</p>
</div>
</details>
</li>
<li><code>_generate_path_summary</code> (graph_rag.py)
<details><summary>Generate human-readable summary of graph paths.</summary>
<div class="doc-comment">
<p>Generate human-readable summary of graph paths.</p>
<p></p>
<p>Args:</p>
<p>facts: List of facts to summarize</p>
<p>query: Original query</p>
<p>contested: Whether results contain contradictions</p>
<p>counter_paths: Alternative paths showing contradictions</p>
<p></p>
<p>Returns:</p>
<p>Summary text (Hermes-friendly)</p>
</div>
</details>
</li>
<li><code>build_hnsw_index</code> (rag_engine.py)
<details><summary>Build HNSW index from documents.</summary>
<div class="doc-comment">
<p>Build HNSW index from documents.</p>
<p></p>
<p>Args:</p>
<p>documents: List of documents to index</p>
<p>embeddings: Optional pre-computed embeddings {doc_id: embedding}</p>
<p>If not provided, embeddings will be generated</p>
</div>
</details>
</li>
<li><code>_build_pivot_recommendations</code> (analyst_workbench.py)
<details><summary>F225B: Build bounded pivot recommendations from findings and graph signal.</summary>
<div class="doc-comment">
<p>F225B: Build bounded pivot recommendations from findings and graph signal.</p>
<p></p>
<p>Max 5 recommendations. Uses findings IOC values/types and graph entity data.</p>
<p>No new planner — summarizes existing pivots if present.</p>
</div>
</details>
</li>
<li><code>_sync_query_delta_comparison</code> (duckdb_store.py)
<details><summary>Sync - MUST be called on the worker thread.</summary>
<div class="doc-comment">
<p>Sync - MUST be called on the worker thread.</p>
<p></p>
<p>Sprint F192F §2: uses findings_per_minute (matches sprint_scorecard naming).</p>
</div>
</details>
</li>
<li><code>async_get_hypothesis_feedback</code> (duckdb_store.py)
<details><summary>Sprint F203G: Fetch aggregated hypothesis_feedback records.</summary>
<div class="doc-comment">
<p>Sprint F203G: Fetch aggregated hypothesis_feedback records.</p>
<p></p>
<p>Thread-safe, non-blocking - runs on duckdb_worker via run_in_executor.</p>
<p></p>
<p>Args:</p>
<p>target_id: If provided, filter by target_id. If None, returns all.</p>
<p>limit: Maximum records to return (default 1000).</p>
<p></p>
<p>Returns:</p>
<p>List of HypothesisFeedbackRecord instances ordered by ts DESC.</p>
<p>Returns empty list if store is closed or uninitialized.</p>
</div>
</details>
</li>
<li><code>_evict_if_needed</code> (lancedb_store.py) — <span class="doc-comment-inline">F214OPT-C: Pre-emptive eviction when LMDB map is near full.</span></li>
<li><code>_record_observation_batch_sync</code> (ioc_graph.py)
<details><summary>Synchronous batch observation — runs on _executor thread.</summary>
<div class="doc-comment">
<p>Synchronous batch observation — runs on _executor thread.</p>
<p></p>
<p>N+1 elimination via UNWIND batch queries:</p>
<p>Phase 1: 1 query — UNWIND batch existence check for all edges</p>
<p>Phase 3: 1 query — UNWIND batch CREATE for missing edges</p>
<p>Phase 4: 1 query — UNWIND batch SET last_seen for existing edges</p>
<p>Total: 3 queries regardless of batch size (was 2N+1).</p>
</div>
</details>
</li>
<li><code>get_connected_iocs</code> (graph_attachment.py)
<details><summary>Sprint 8VY: Read-only seam for analytics graph find_connected() (DuckPGQGraph).</summary>
<div class="doc-comment">
<p>Sprint 8VY: Read-only seam for analytics graph find_connected() (DuckPGQGraph).</p>
<p></p>
<p>PURPOSE</p>
<p>-------</p>
<p>Replaces direct shell access to store._ioc_graph.find_connected() in</p>
<p>__main__._run_sprint_mode(). Diagnostic use case: log connected nodes for top IOC.</p>
<p>DuckDBShadowStore is NOT a graph authority — thin fail-open adapter.</p>
<p></p>
<p>CONSUMER</p>
<p>--------</p>
<p>__main__._run_sprint_mode(): logging {first_ioc} → {len(connected)} connected nodes.</p>
<p></p>
<p>STORE IS NOT GRAPH TRUTH OWNER</p>
<p>-------------------------------</p>
<p>The analytics _ioc_graph (DuckPGQGraph) is the donor backend.</p>
<p>Returns [] (fail-open) if no graph attached or call fails.</p>
<p></p>
<p>CAPABILITY REQUIREMENTS</p>
<p>-----------------------</p>
<p>Requires attached graph to implement find_connected(value, max_hops) → list.</p>
<p>DuckPGQGraph: has this method.</p>
<p>IOCGraph: does NOT have this method → returns [] (fail-open).</p>
<p></p>
<p>Args:</p>
<p>ioc_value: The IOC value to find connections for.</p>
<p>max_hops: Maximum traversal depth (default 2).</p>
<p></p>
<p>Returns:</p>
<p>list: Connected IOC nodes or [] if unavailable.</p>
</div>
</details>
</li>
<li><code>calculate_centrality</code> (graph_rag.py)
<details><summary>Calculate centrality measures for nodes in the graph.</summary>
<div class="doc-comment">
<p>Calculate centrality measures for nodes in the graph.</p>
<p></p>
<p>Uses igraph C-core when available (50-100x faster than pure-Python).</p>
<p>Falls back to simplified pure-Python on igraph unavailable / RAM constraint.</p>
<p></p>
<p>Args:</p>
<p>node_ids: Specific nodes to analyze (None = all)</p>
<p>top_k: Return top K most central nodes</p>
<p></p>
<p>Returns:</p>
<p>List of CentralityScores sorted by overall influence</p>
</div>
</details>
</li>
<li><code>_export_stix_bundle_sync</code> (ioc_graph.py) — <span class="doc-comment-inline">Synchronous STIX 2.1 export — runs on _executor thread.</span></li>
<li><code>_sync_fetch_batches</code> (duckdb_store.py)</li>
<li><code>async_ingest_findings_with_envelope</code> (duckdb_store.py)</li>
<li><code>get_dedup_runtime_status</code> (duckdb_store.py)
<details><summary>Sprint 8AG §6.17 + 8AK + 8AV + F222: Typed/cheap status surface for dedup subsystem.</summary>
<div class="doc-comment">
<p>Sprint 8AG §6.17 + 8AK + 8AV + F222: Typed/cheap status surface for dedup subsystem.</p>
<p></p>
<p>Sprint F222: Now delegates to DedupManager.get_runtime_status() for dedup-specific</p>
<p>fields. QualityAssessmentState fields still pulled from _quality_state.</p>
</div>
</details>
</li>
<li><code>_traverse_hop</code> (graph_rag.py)
<details><summary>Traverse one hop in the graph with RAM-efficient frontier management.</summary>
<div class="doc-comment">
<p>Traverse one hop in the graph with RAM-efficient frontier management.</p>
<p></p>
<p>Args:</p>
<p>visited: Set of already visited node IDs</p>
<p>hop: Current hop number</p>
<p>max_nodes: Maximum nodes to collect</p>
<p>max_edges: Maximum edges to traverse (default: 500)</p>
<p></p>
<p>Returns:</p>
<p>List of new facts discovered in this hop</p>
</div>
</details>
</li>
<li><code>get_graph_stats</code> (graph_attachment.py)
<details><summary>Sprint 8VY: Read-only seam for analytics graph stats (DuckPGQGraph.stats()).</summary>
<div class="doc-comment">
<p>Sprint 8VY: Read-only seam for analytics graph stats (DuckPGQGraph.stats()).</p>
<p></p>
<p>PURPOSE</p>
<p>-------</p>
<p>Replaces direct shell access to store._ioc_graph.stats() in __main__._run_sprint_mode().</p>
<p>DuckDBShadowStore is NOT a graph authority — this is a thin fail-open adapter</p>
<p>for the diagnostics use case only.</p>
<p></p>
<p>CONSUMER</p>
<p>--------</p>
<p>__main__._run_sprint_mode(): logging [GRAPH] nodes/edges/pgq stats.</p>
<p></p>
<p>STORE IS NOT GRAPH TRUTH OWNER</p>
<p>-------------------------------</p>
<p>The analytics _ioc_graph (DuckPGQGraph) is the donor backend.</p>
<p>Returns {} (fail-open) if no graph attached or call fails.</p>
<p></p>
<p>CAPABILITY REQUIREMENTS</p>
<p>-----------------------</p>
<p>Requires attached graph to implement stats() → {nodes, edges, pgq_active}.</p>
<p>DuckPGQGraph: has this method.</p>
<p>IOCGraph: has this method.</p>
<p></p>
<p>Returns:</p>
<p>dict: {nodes, edges, pgq_active} or {} if unavailable.</p>
</div>
</details>
</li>
<li><code>store_pattern</code> (neuromorphic.py)</li>
<li><code>_apply_schema_migrations</code> (duckdb_store.py)
<details><summary>ALTER TABLE ADD COLUMN for any sprint_delta columns missing from old DBs.</summary>
<div class="doc-comment">
<p>ALTER TABLE ADD COLUMN for any sprint_delta columns missing from old DBs.</p>
<p>DuckDB does not have IF NOT EXISTS for ALTER, so we catch and ignore errors.</p>
<p></p>
<p>Sprint F192F §2: findings_per_min -&gt; findings_per_minute rename.</p>
<p>Migration order matters - add new column first, then handle legacy column:</p>
<p>1. Add findings_per_minute (new canonical name, matches sprint_scorecard)</p>
<p>2. Add top_source_type (may already exist on very old DBs)</p>
<p>3. Add synthesis_confidence (may already exist on very old DBs)</p>
<p>Legacy findings_per_min column is retained but not written to (inserts use</p>
<p>findings_per_minute). Queries read findings_per_minute which is populated</p>
<p>by current insert logic.</p>
</div>
</details>
</li>
<li><code>_sync_get_hypothesis_feedback</code> (duckdb_store.py)
<details><summary>Sprint F203G: Fetch hypothesis_feedback records ordered by ts DESC.</summary>
<div class="doc-comment">
<p>Sprint F203G: Fetch hypothesis_feedback records ordered by ts DESC.</p>
<p></p>
<p>Thread-safe: MUST be called on the duckdb_worker thread.</p>
<p></p>
<p>Args:</p>
<p>target_id: If provided, filter by target_id. If None, returns all.</p>
<p>limit: Maximum number of records to return.</p>
<p></p>
<p>Returns:</p>
<p>List of dicts with keys: id, target_id, pivot_type, ioc_type,</p>
<p>produced_count, accepted_count, signal_value, ts.</p>
</div>
</details>
</li>
<li><code>read_target_memory</code> (duckdb_store.py)
<details><summary>Sprint F204D: Read a TargetMemory record by target_id.</summary>
<div class="doc-comment">
<p>Sprint F204D: Read a TargetMemory record by target_id.</p>
<p>Returns None if not found. Deserializes JSON TEXT columns.</p>
</div>
</details>
</li>
<li><code>add_entity</code> (lancedb_store.py)
<details><summary>Add entity to identity store.</summary>
<div class="doc-comment">
<p>Add entity to identity store.</p>
<p></p>
<p>Args:</p>
<p>entity_id: Unique entity identifier.</p>
<p>embedding: Vector embedding for semantic similarity.</p>
<p>aliases: List of aliases/alternate names.</p>
<p></p>
<p>Returns:</p>
<p>True if added successfully, False otherwise.</p>
</div>
</details>
</li>
<li><code>search</code> (rag_engine.py)
<details><summary>Search for k nearest neighbors.</summary>
<div class="doc-comment">
<p>Search for k nearest neighbors.</p>
<p></p>
<p>Args:</p>
<p>query_vector: Query vector of shape (dim,)</p>
<p>k: Number of results to return</p>
<p>filter_ids: Optional list of ids to filter results</p>
<p></p>
<p>Returns:</p>
<p>Tuple of (list of ids, list of distances/scores)</p>
</div>
</details>
</li>
<li><code>_derive_open_questions</code> (analyst_workbench.py)
<details><summary>Derive open questions from gaps in findings and graph.</summary>
<div class="doc-comment">
<p>Derive open questions from gaps in findings and graph.</p>
<p></p>
<p>Checks for common gaps: low finding count, no high-confidence findings,</p>
<p>sparse graph, missing IOC types.</p>
</div>
</details>
</li>
<li><code>_parse_sparql_results</code> (entity_linker.py)
<details><summary>Parse SPARQL results into EntityCandidate objects.</summary>
<div class="doc-comment">
<p>Parse SPARQL results into EntityCandidate objects.</p>
<p></p>
<p>Args:</p>
<p>entity_text: Original entity text</p>
<p>data: SPARQL JSON response</p>
<p></p>
<p>Returns:</p>
<p>List of EntityCandidate objects</p>
</div>
</details>
</li>
<li><code>_do_close</code> (duckdb_store.py)
<details><summary>Synchronous close helper - idempotent.</summary>
<div class="doc-comment">
<p>Synchronous close helper - idempotent.</p>
<p></p>
<p>Note: _closed guard removed - close() and _do_close() are always called</p>
<p>together in the same call chain; close() sets _closed=True first and</p>
<p>guards against re-entry. _do_close() always runs its cleanup.</p>
</div>
</details>
</li>
<li><code>_try_python_fallback</code> (dedup.py)
<details><summary>Last-resort Python fallback — in-memory only, no file race possible.</summary>
<div class="doc-comment">
<p>Last-resort Python fallback — in-memory only, no file race possible.</p>
<p></p>
<p>P1-10: Uses set-based in-memory filter. No mmap persistence</p>
<p>(cross-run state is lost on crash, but dedup is best-effort anyway).</p>
<p>No os.path.exists race because there are no files to race on.</p>
</div>
</details>
</li>
<li><code>_init_bloom_filter_precheck</code> (dedup.py)
<details><summary>Initialize Rust MmapBloomFilter pre-check for fast negative dedup.</summary>
<div class="doc-comment">
<p>Initialize Rust MmapBloomFilter pre-check for fast negative dedup.</p>
<p></p>
<p>P1-4: Bloom filter sits in front of LMDB for O(1) negative dedup —</p>
<p>if Bloom says "not seen", skip LMDB entirely. If Bloom says "seen",</p>
<p>verify against LMDB (authoritative).</p>
<p></p>
<p>Fails softly: any exception stored in _bloom_filter_error.</p>
</div>
</details>
</li>
<li><code>_sync_get_previous_findings_for_target</code> (duckdb_store.py)</li>
<li><code>duckdb_fetch_polars</code> (duckdb_store.py)
<details><summary>F320-431: Zero-copy DuckDB → Polars via Arrow C Data Interface.</summary>
<div class="doc-comment">
<p>F320-431: Zero-copy DuckDB → Polars via Arrow C Data Interface.</p>
<p></p>
<p>Uses `conn.execute(sql).pl()` (DuckDB 1.5+) which reads Arrow buffers</p>
<p>directly via DuckDB's C Data Interface — no Python copies, no IPC</p>
<p>serialization round-trip. Single GIL acquire/release for the entire</p>
<p>result set.</p>
<p></p>
<p>MUST be called on the DuckDB worker thread (thread-affine connection).</p>
<p>Caller is responsible for thread safety.</p>
<p></p>
<p>Args:</p>
<p>conn: DuckDB connection (thread-affine, from _qe()._conn()).</p>
<p>sql: SQL query.</p>
<p>params: Optional query parameters.</p>
<p></p>
<p>Returns:</p>
<p>pl.DataFrame or None on error. DataFrame column order matches</p>
<p>SQL projection order.</p>
<p></p>
<p>Zero-copy guarantees:</p>
<p>- DuckDB Arrow buffers live in DuckDB's heap</p>
<p>- Polars adopts buffers via C Data Interface (zero-copy)</p>
<p>- No IPC bytes serialization (unlike Rust arrow_batch_builder path)</p>
<p>- Single GIL acquire/release vs N× for row-by-row iteration</p>
</div>
</details>
</li>
<li><code>async_get_target_memory</code> (duckdb_store.py)
<details><summary>Sprint F204D: Get target memory by target_id.</summary>
<div class="doc-comment">
<p>Sprint F204D: Get target memory by target_id.</p>
<p></p>
<p>Thread-safe, non-blocking - runs on duckdb_worker via run_in_executor.</p>
<p>Returns None if not found or on error.</p>
</div>
</details>
</li>
<li><code>async_record_research_session</code> (duckdb_store.py)</li>
<li><code>_ensure_ivf_pq_index_async</code> (lancedb_store.py)
<details><summary>Sprint F264D: Lazy IVF-PQ training (M1 8GB friendly, fail-soft).</summary>
<div class="doc-comment">
<p>Sprint F264D: Lazy IVF-PQ training (M1 8GB friendly, fail-soft).</p>
<p></p>
<p>Called from add_entity/search_similar on first invocation. Gated by</p>
<p>HLEDAC_LANCEDB_QUANTIZE=1. Skipped if table has &lt; 256 rows (insufficient</p>
<p>training data — IVF-PQ on small data degrades recall). Errors are logged</p>
<p>+ ignored → falls back to brute-force cosine. Double-checked locking</p>
<p>prevents concurrent training on first parallel query burst.</p>
<p></p>
<p>NOTE: Uses ``getattr`` for flags so the helper is safe under ``__new__``</p>
<p>test-mock paths that bypass ``__init__``.</p>
</div>
</details>
</li>
<li><code>search_with_mmr</code> (lancedb_store.py)
<details><summary>Diversity-aware search using Maximal Marginal Relevance from context_optimization.</summary>
<div class="doc-comment">
<p>Diversity-aware search using Maximal Marginal Relevance from context_optimization.</p>
<p></p>
<p>Args:</p>
<p>query_text: Original query text for reranking.</p>
<p>query_emb: Query embedding vector.</p>
<p>top_k: Number of results to return.</p>
<p>lambda_mult: Balance relevance (1.0) vs diversity (0.0). Default 0.5.</p>
<p>fetch_k: Number of candidates to fetch before reranking.</p>
<p></p>
<p>Returns:</p>
<p>List of diverse, relevant documents.</p>
</div>
</details>
</li>
<li><code>_traversal_worker</code> (graph_rag.py)
<details><summary>Worker that performs graph traversal and pushes discovered nodes to queue.</summary>
<div class="doc-comment">
<p>Worker that performs graph traversal and pushes discovered nodes to queue.</p>
<p></p>
<p>Args:</p>
<p>query: Search query</p>
<p>hops: Number of hops to traverse</p>
<p>max_nodes: Maximum nodes to discover</p>
<p>queue: Queue to push discovered nodes to</p>
</div>
</details>
</li>
<li><code>add_vectors</code> (rag_engine.py)
<details><summary>Add vectors to the index.</summary>
<div class="doc-comment">
<p>Add vectors to the index.</p>
<p></p>
<p>Args:</p>
<p>vectors: Array of shape (n_vectors, dim) or (dim,) for single vector</p>
<p>ids: List of unique string identifiers for each vector</p>
</div>
</details>
</li>
<li><code>_hnsw_retrieval</code> (rag_engine.py)
<details><summary>Retrieve documents using HNSW index.</summary>
<div class="doc-comment">
<p>Retrieve documents using HNSW index.</p>
<p></p>
<p>Args:</p>
<p>query_embedding: Query embedding vector</p>
<p>top_k: Number of results to return</p>
<p>filters: Optional metadata filters</p>
<p></p>
<p>Returns:</p>
<p>List of retrieved chunks with scores</p>
</div>
</details>
</li>
<li><code>get_hot_neighbors</code> (hot_edges_cache.py)</li>
<li><code>_decode_neighbors_denorm</code> (hot_edges_cache.py)
<details><summary>Decode v2 denormalized wire format → list[(dst_id, count, value, ioc_type)].</summary>
<div class="doc-comment">
<p>Decode v2 denormalized wire format → list[(dst_id, count, value, ioc_type)].</p>
<p></p>
<p>Handles backward compat: v1 blobs decoded via _decode_neighbors.</p>
</div>
</details>
</li>
<li><code>link_entities</code> (entity_linker.py)
<details><summary>Link entities in text to Wikidata.</summary>
<div class="doc-comment">
<p>Link entities in text to Wikidata.</p>
<p></p>
<p>Args:</p>
<p>text: Input text to extract and link entities from</p>
<p>context: Optional context for disambiguation</p>
<p></p>
<p>Returns:</p>
<p>List of LinkedEntity objects</p>
</div>
</details>
</li>
<li><code>compute_optimal_partitions</code> (lancedb_auto_tuner.py)
<details><summary>Decide next ``num_partitions`` based on observed recall and search latency.</summary>
<div class="doc-comment">
<p>Decide next ``num_partitions`` based on observed recall and search latency.</p>
<p></p>
<p>P0-2 Enhancement: Trend-aware PID controller.</p>
<p></p>
<p>Instead of reacting to a single noisy recall sample, we compute an EMA</p>
<p>(exponential moving average) of recall and use its *direction* to guide</p>
<p>the adjustment. This provides closed-loop stability — the controller</p>
<p>damps oscillations that plague open-loop threshold-only approaches.</p>
<p></p>
<p>Branches:</p>
<p>- **recall_ema &lt; RECALL_TOO_LOW (0.85)** → grow by 50% (clamp upper).</p>
<p>IVF-PQ with too few partitions is hitting quantization error.</p>
<p>- **recall_ema ≥ RECALL_EXCELLENT (0.97) AND avg_search_ms &gt; 50** → shrink</p>
<p>by 25% (clamp lower). Index is over-partitioned for current data.</p>
<p>- **EMA trend is falling significantly** (3 consecutive drops) → early grow</p>
<p>signal before hitting hard threshold. Detects degradation trajectory.</p>
<p>- otherwise → no change. Index is well-tuned.</p>
<p></p>
<p>Heuristic floor: never grow above 1 partition per ~16 rows. Clamped to</p>
<p>``MAX_NUM_PARTITIONS=256`` to keep M1 RSS bounded.</p>
</div>
</details>
</li>
<li><code>add_text</code> (semantic_store.py)</li>
<li><code>wal_scan_pending_sync_markers</code> (wal.py)
<details><summary>Efficient prefix scan for all pending_duckdb_sync markers.</summary>
<div class="doc-comment">
<p>Efficient prefix scan for all pending_duckdb_sync markers.</p>
<p></p>
<p>Returns list of marker values (dicts with id, query, source_type, confidence, ts).</p>
<p>Uses LMDB cursor with prefix iteration — O(n) where n = number of pending markers.</p>
</div>
</details>
</li>
<li><code>_adjust_executor_pool</code> (duckdb_store.py)
<details><summary>Adjust _shared_executor worker count based on M1 UMA memory pressure.</summary>
<div class="doc-comment">
<p>Adjust _shared_executor worker count based on M1 UMA memory pressure.</p>
<p></p>
<p>F300S: Reduced defaults for M1 8GB UMA:</p>
<p>CRITICAL/EMERGENCY: 1 worker (~50 MB saved vs 2 workers baseline)</p>
<p>SOFT_WARN: 1 worker (conservative, leaves headroom for MLX)</p>
<p>OK: 2 workers (baseline, set at __init__)</p>
<p></p>
<p>F285-U1: Unified executor — all 4 former pools are now _shared_executor aliases.</p>
<p>This method adjusts the single shared pool's max_workers.</p>
<p></p>
<p>This is a best-effort advisory — executor is NOT restarted, only the</p>
<p>reference to max_workers is capped for future task submissions.</p>
<p>Thread count change takes effect on the NEXT submit() call.</p>
<p></p>
<p>Lazy import of resource_governor to avoid circular deps and cold-start cost.</p>
</div>
</details>
</li>
<li><code>_sync_record_research_session</code> (duckdb_store.py)</li>
<li><code>_wal_write_pending_sync_marker</code> (duckdb_store.py)
<details><summary>Sprint 8F: Write a pending-sync recovery marker to LMDB.</summary>
<div class="doc-comment">
<p>Sprint 8F: Write a pending-sync recovery marker to LMDB.</p>
<p>P0-9 fix: Enforces MAX_PENDING_SYNC_MARKERS bound via oldest eviction.</p>
<p></p>
<p>Marker key:  pending_duckdb_sync:{id}</p>
<p>Value:       same structure as WAL finding (id, query, source_type, confidence, ts)</p>
<p></p>
<p>This marker is written ONLY when LMDB succeeded but DuckDB failed.</p>
<p>A future recovery sprint can find it via prefix scan and retry the DuckDB write.</p>
</div>
</details>
</li>
<li><code>_filter_by_time</code> (graph_rag.py)
<details><summary>Filter facts by time range.</summary>
<div class="doc-comment">
<p>Filter facts by time range.</p>
<p></p>
<p>Args:</p>
<p>facts: List of facts to filter</p>
<p>time_min: ISO datetime minimum (inclusive)</p>
<p>time_max: ISO datetime maximum (inclusive)</p>
<p></p>
<p>Returns:</p>
<p>Filtered list of facts</p>
</div>
</details>
</li>
<li><code>_build_narratives</code> (graph_rag.py)
<details><summary>Build competing narratives from contradictory evidence.</summary>
<div class="doc-comment">
<p>Build competing narratives from contradictory evidence.</p>
<p></p>
<p>Args:</p>
<p>primary_paths: Primary evidence paths</p>
<p>counter_paths: Counter evidence paths</p>
<p></p>
<p>Returns:</p>
<p>List of narrative objects (max 3)</p>
</div>
</details>
</li>
<li><code>_derive_next_actions</code> (analyst_workbench.py)
<details><summary>Derive next actions from high-confidence findings.</summary>
<div class="doc-comment">
<p>Derive next actions from high-confidence findings.</p>
<p></p>
<p>Uses source_type and ioc_type patterns to suggest follow-ups.</p>
<p>No model required.</p>
</div>
</details>
</li>
<li><code>store_persistent_dedup_batch</code> (dedup.py)
<details><summary>Store multiple fingerprint → finding_id mappings in persistent dedup LMDB.</summary>
<div class="doc-comment">
<p>Store multiple fingerprint → finding_id mappings in persistent dedup LMDB.</p>
<p></p>
<p>S3: Single transaction for batch insert, reduces N txn.begin() to 1.</p>
<p></p>
<p>Args:</p>
<p>items: List of (fp, finding_id) tuples</p>
</div>
</details>
</li>
<li><code>_load_lmdb</code> (ioc_dedup_adapter.py)
<details><summary>Load persisted state from LMDB.</summary>
<div class="doc-comment">
<p>Load persisted state from LMDB.</p>
<p>Called lazily on first add() after init or after advance_sprint().</p>
</div>
</details>
</li>
<li><code>_activation_record_finding</code> (duckdb_store.py)
<details><summary>Sprint 8A: Record a structured finding - LMDB WAL first, DuckDB second.</summary>
<div class="doc-comment">
<p>Sprint 8A: Record a structured finding - LMDB WAL first, DuckDB second.</p>
<p></p>
<p>Mapping:</p>
<p>result.id or uuid4() -&gt; id</p>
<p>context.query or "" -&gt; query</p>
<p>source_type from schema/type name -&gt; source_type</p>
<p>result.confidence or 1.0 -&gt; confidence</p>
<p>time.time() -&gt; ts</p>
<p></p>
<p>Partial failure semantics:</p>
<p>- LMDB OK + DuckDB FAIL -&gt; LMDB remains truth, log desync, return duckdb_success=False</p>
<p>- LMDB FAIL + DuckDB SKIP -&gt; return lmdb_success=False, duckdb_success=None</p>
<p></p>
<p>Returns dict with keys: lmdb_success, duckdb_success, finding_id, query</p>
</div>
</details>
</li>
<li><code>_embed_batch</code> (lancedb_store.py) — <span class="doc-comment-inline">Generate embeddings in batches - thread-safe (uses embed_document for indexing).</span></li>
<li><code>detect_communities</code> (graph_rag.py)
<details><summary>Detect communities in the knowledge graph.</summary>
<div class="doc-comment">
<p>Detect communities in the knowledge graph.</p>
<p></p>
<p>Uses igraph C-core label propagation when available (5-10x faster than pure-Python).</p>
<p>Falls back to pure-Python label propagation on igraph unavailable / RAM constraint.</p>
<p></p>
<p>Args:</p>
<p>num_communities: Target number of communities</p>
<p></p>
<p>Returns:</p>
<p>List of detected communities</p>
</div>
</details>
</li>
<li><code>query_findings</code> (analyst_workbench.py)
<details><summary>Query recent findings using keyword/BM25 search.</summary>
<div class="doc-comment">
<p>Query recent findings using keyword/BM25 search.</p>
<p></p>
<p>Args:</p>
<p>query: Search query string</p>
<p>limit: Max results (capped to MAX_TOP_K)</p>
<p>source_type: Optional filter by source_type</p>
<p></p>
<p>Returns:</p>
<p>List of finding dicts ordered by relevance (keyword match).</p>
<p>Each dict has: id, query, source_type, confidence, ts, provenance,</p>
<p>payload_text (if available).</p>
</div>
</details>
</li>
<li><code>query_graph</code> (analyst_workbench.py)
<details><summary>Query entity history from DuckPGQGraph.</summary>
<div class="doc-comment">
<p>Query entity history from DuckPGQGraph.</p>
<p></p>
<p>Args:</p>
<p>entity_value: IOC value to traverse from (e.g., domain, IP)</p>
<p>max_hops: Max traversal depth (capped to MAX_GRAPH_HOPS)</p>
<p></p>
<p>Returns:</p>
<p>List of RelatedEntity ordered by hops then confidence.</p>
</div>
</details>
</li>
<li><code>check_ann_duplicate</code> (ann_index.py)</li>
<li><code>source_family_from_step_or_finding</code> (evidence_chain.py)
<details><summary>F225C: Derive the source family from a ChainStep, finding dict, or source_type string.</summary>
<div class="doc-comment">
<p>F225C: Derive the source family from a ChainStep, finding dict, or source_type string.</p>
<p></p>
<p>Source families:</p>
<p>- feed:  CT feed sources (ct_log, certificate_transparency)</p>
<p>- ct:    certificate transparency (alias for feed in some contexts)</p>
<p>- public: public sources (public_wiki, public WHOIS, etc.)</p>
<p>- deep:  deep probe sources (deep_probe, s3, ipfs)</p>
<p>- document: document/triage sources (document, evidence_triage, multimodal)</p>
<p></p>
<p>Returns "unknown" for unparseable input (fail-soft).</p>
</div>
</details>
</li>
<li><code>async_record_hypothesis_tracking</code> (duckdb_store.py)</li>
<li><code>_wal_scan_pending_sync_markers</code> (duckdb_store.py)
<details><summary>Sprint 8F: Efficient prefix scan for all pending_duckdb_sync markers.</summary>
<div class="doc-comment">
<p>Sprint 8F: Efficient prefix scan for all pending_duckdb_sync markers.</p>
<p></p>
<p>Returns list of marker values (dicts with id, query, source_type, confidence, ts).</p>
<p>Uses LMDB cursor with prefix iteration - O(n) where n = number of pending markers,</p>
<p>NOT O(N) full database scan.</p>
</div>
</details>
</li>
<li><code>_extract_vectors_and_keys</code> (lancedb_auto_tuner.py)
<details><summary>Extract the vector column and a key column from the table as numpy.</summary>
<div class="doc-comment">
<p>Extract the vector column and a key column from the table as numpy.</p>
<p></p>
<p>Returns ``(vectors_normalized, key_list)``. Vectors are L2-normalized</p>
<p>for cosine-similarity. If the table is too large (&gt;MAX_BRUTE_FORCE_ROWS)</p>
<p>a deterministic random sample is taken for the brute-force baseline.</p>
<p></p>
<p>Fail-soft: any error returns empty arrays.</p>
</div>
</details>
</li>
<li><code>retrain</code> (lancedb_auto_tuner.py)
<details><summary>Re-train IVF-PQ with new ``num_partitions`` and optionally ``num_sub_vectors``.</summary>
<div class="doc-comment">
<p>Re-train IVF-PQ with new ``num_partitions`` and optionally ``num_sub_vectors``.</p>
<p></p>
<p>P1-2 Enhancement: Both IVF-PQ knobs are now tuned together.</p>
<p></p>
<p>Uses the canonical ``Table.create_index(..., replace=True)`` API</p>
<p>(LanceDB 0.4+). ``Table.optimize(retrain=True)`` is DEPRECATED and</p>
<p>does NOT re-train IVF-PQ centroids — it only compacts files. This</p>
<p>method is the only correct way to re-train with new params.</p>
<p></p>
<p>P1-1 Enhancement: LanceDB 0.4x API compatibility — passes</p>
<p>``max_iterations`` only when confirmed supported by the table, with</p>
<p>graceful fallback to signature-based detection.</p>
<p></p>
<p>Returns True on success, False on any error (fail-soft).</p>
</div>
</details>
</li>
<li><code>_sync_ingest</code> (duckdb_store.py)</li>
<li><code>_apply_recency_boost</code> (graph_rag.py)
<details><summary>Boost scores of more recent facts.</summary>
<div class="doc-comment">
<p>Boost scores of more recent facts.</p>
<p></p>
<p>Args:</p>
<p>facts: List of facts to boost</p>
<p></p>
<p>Returns:</p>
<p>Facts with boosted scores</p>
</div>
</details>
</li>
<li><code>_build_risk_hypotheses</code> (analyst_workbench.py)
<details><summary>F225B: Build bounded deterministic risk hypotheses based on findings.</summary>
<div class="doc-comment">
<p>F225B: Build bounded deterministic risk hypotheses based on findings.</p>
<p></p>
<p>Max 5 hypotheses based on: source diversity, IOC density,</p>
<p>non-feed absence, CT/public presence.</p>
</div>
</details>
</li>
<li><code>get_hot_neighbors_denorm</code> (hot_edges_cache.py)</li>
<li><code>is_duplicate_ioc_batch</code> (dedup.py)
<details><summary>Batch IOC dedup check via Rust MmapIocDedupStore.</summary>
<div class="doc-comment">
<p>Batch IOC dedup check via Rust MmapIocDedupStore.</p>
<p></p>
<p>Args:</p>
<p>items: List of (ioc_value, ioc_type) tuples.</p>
<p></p>
<p>Returns:</p>
<p>List[bool] — True = duplicate (already seen).</p>
<p></p>
<p>P1-07 invariants:</p>
<p>- Always-on: no feature flag, no env var toggle</p>
<p>- Bounded: Rust store has internal capacity limits</p>
<p>- Fail-safe: any error returns [False, ...] (allow all)</p>
<p>- Thread-safe: parking_lot::RwLock in Rust store</p>
</div>
</details>
</li>
<li><code>query_wikidata</code> (entity_linker.py)
<details><summary>Query Wikidata for entity candidates.</summary>
<div class="doc-comment">
<p>Query Wikidata for entity candidates.</p>
<p></p>
<p>Args:</p>
<p>entity_text: Entity text to search</p>
<p></p>
<p>Returns:</p>
<p>List of EntityCandidate objects</p>
</div>
</details>
</li>
<li><code>extract_iocs_batch</code> (ioc_graph.py)
<details><summary>Batch extract IOCs from multiple texts in parallel using ThreadPoolExecutor.</summary>
<div class="doc-comment">
<p>Batch extract IOCs from multiple texts in parallel using ThreadPoolExecutor.</p>
<p></p>
<p>Architecture:</p>
<p>- Parallel O(n) regex scans across N texts (4 worker threads)</p>
<p>- Bounded: MAX_EXTRACT_BATCH per call (memory guard)</p>
<p>- Fail-soft: individual text failures return [] not exceptions</p>
<p>- Returns: list of result lists, matching input order</p>
<p></p>
<p>Args:</p>
<p>items: List of (text, pattern_matches) tuples.</p>
<p></p>
<p>Returns:</p>
<p>List of (ioc_value, ioc_type) lists per input text.</p>
</div>
</details>
</li>
<li><code>shutdown</code> (lancedb_store.py) — <span class="doc-comment-inline">Cleanup resources.</span></li>
<li><code>find_contradictions</code> (graph_rag.py)
<details><summary>Find contradictions between nodes in the graph.</summary>
<div class="doc-comment">
<p>Find contradictions between nodes in the graph.</p>
<p></p>
<p>From evidence_network_analyzer.py comments:</p>
<p>"Step 5: Identify contradictions"</p>
<p>"Find contradiction edges"</p>
<p>"Assess severity"</p>
<p></p>
<p>Args:</p>
<p>confidence_threshold: Minimum confidence to report</p>
<p></p>
<p>Returns:</p>
<p>List of detected contradictions</p>
</div>
</details>
</li>
<li><code>__init__</code> (rag_engine.py)
<details><summary>Initialize USearch Vector Index.</summary>
<div class="doc-comment">
<p>Initialize USearch Vector Index.</p>
<p></p>
<p>Args:</p>
<p>dim: Vector dimension (default 768 for typical embeddings)</p>
<p>max_elements: Maximum number of vectors in index</p>
<p>M: Number of bi-directional links for each node (higher = better recall, more memory)</p>
<p>ef_construction: Size of dynamic candidate list for construction (higher = better quality)</p>
<p>ef_search: Size of dynamic candidate list for search (higher = better recall)</p>
<p>space: Distance metric - "cosine", "l2", or "ip" (inner product)</p>
<p>index_path: Optional path for persistent index storage</p>
</div>
</details>
</li>
<li><code>_compress_chunks</code> (rag_engine.py)
<details><summary>Komprimovat chunky pomocí SPR — paralelně přes bounded TaskGroup.</summary>
<div class="doc-comment">
<p>Komprimovat chunky pomocí SPR — paralelně přes bounded TaskGroup.</p>
<p></p>
<p>M1 8GB: GRAPH_RAG limit (3,2,1,1) z ConcurrencyBudgetRegistry zamezuje</p>
<p>Metal alloc pressure. 50 chunků × 10 ms serial → ~170 ms parallel při limit=3.</p>
<p></p>
<p>Dynamic concurrency: adapts to memory pressure (lower = fewer concurrent).</p>
<p>Per-chunk timeout: prevents one stuck chunk from blocking the entire batch.</p>
</div>
</details>
</li>
<li><code>_secure_process</code> (rag_engine.py)
<details><summary>Process chunks through Secure Enclave for batch attestation.</summary>
<div class="doc-comment">
<p>Process chunks through Secure Enclave for batch attestation.</p>
<p></p>
<p>IMPORTANT: This does NOT mutate chunk text. The enclave is used for</p>
<p>hardware-backed attestation of chunk batch existence via signed digest.</p>
<p></p>
<p>Architecture:</p>
<p>- Build canonical BatchManifest (chunk_count, per-chunk SHA-256, batch_digest)</p>
<p>- Request one signature for the batch digest (NOT per-chunk)</p>
<p>- Store signature in enclave status for telemetry</p>
<p>- Return chunks unchanged</p>
</div>
</details>
</li>
<li><code>_store_embedding</code> (lancedb_store.py) — <span class="doc-comment-inline">Store embedding with float16 quantization (50% memory savings) and writeback buffer.</span></li>
<li><code>score_paths_parallel</code> (graph_rag.py)
<details><summary>Score multiple paths in parallel with bounded concurrency.</summary>
<div class="doc-comment">
<p>Score multiple paths in parallel with bounded concurrency.</p>
<p></p>
<p>M1 8GB: Uses Semaphore(4) to limit concurrent scoring operations.</p>
<p>Each scoring operation fetches embeddings via MLX (I/O bound).</p>
<p></p>
<p>Args:</p>
<p>paths: List of paths (each path is a list of node IDs)</p>
<p>hypothesis: The hypothesis to score against</p>
<p>max_nodes: Maximum nodes to score per path (budget)</p>
<p></p>
<p>Returns:</p>
<p>List of scores (one per path), in same order as input</p>
</div>
</details>
</li>
<li><code>_extract_key_findings</code> (analyst_workbench.py)
<details><summary>Extract key findings as strings from the findings list.</summary>
<div class="doc-comment">
<p>Extract key findings as strings from the findings list.</p>
<p></p>
<p>Uses extractive pattern: sorts by confidence and takes top items.</p>
<p>No model required.</p>
</div>
</details>
</li>
<li><code>_build_source_family_summary</code> (analyst_workbench.py)
<details><summary>F225B: Count source families from findings and summarize presence.</summary>
<div class="doc-comment">
<p>F225B: Count source families from findings and summarize presence.</p>
<p></p>
<p>Counts source_type/provenance families, identifies feed-only gap,</p>
<p>non-feed evidence, and CT/PUBLIC/PASSIVE_DNS support.</p>
<p></p>
<p>No model required.</p>
</div>
</details>
</li>
<li><code>lookup_ioc_values_by_ids</code> (hot_edges_cache.py)</li>
<li><code>_register_dedup_manager_finalizer</code> (dedup.py)
<details><summary>F267: Register a DedupManager instance for atexit + SIGTERM cleanup.</summary>
<div class="doc-comment">
<p>F267: Register a DedupManager instance for atexit + SIGTERM cleanup.</p>
<p></p>
<p>Returns the finalizer. Call this from DedupManager.__init__ or from</p>
<p>the code that creates the instance.</p>
<p></p>
<p>Uses weakref.finalize (not atexit.register directly) because:</p>
<p>1. weakref.finalize is called when the object is garbage-collected</p>
<p>2. atexit.register ensures cleanup also happens when the process exits</p>
<p>even if the object is still alive</p>
<p>3. This combination handles both explicit close() and implicit GC/exit</p>
</div>
</details>
</li>
<li><code>close</code> (dedup.py) — <span class="doc-comment-inline">Close all LMDB stores and Bloom filter.</span></li>
<li><code>_ensure_ivf_pq_index</code> (ann_index.py) — <span class="doc-comment-inline">Lazy IVF-PQ training (M1 8GB friendly, fail-soft, sync).</span></li>
<li><code>to_polars_lazy</code> (duckdb_store.py)
<details><summary>Convert parquet file to Polars LazyFrame with filter pushdown.</summary>
<div class="doc-comment">
<p>Convert parquet file to Polars LazyFrame with filter pushdown.</p>
<p></p>
<p>This enables full Polars query optimization including:</p>
<p>- Column pruning</p>
<p>- Predicate pushdown</p>
<p>- Parallel execution</p>
<p></p>
<p>Returns:</p>
<p>polars.LazyFrame — collect() when ready to execute.</p>
</div>
</details>
</li>
<li><code>get_top_findings</code> (duckdb_store.py)
<details><summary>Sprint 8VE B.4: Return top findings by confidence for IOC graph display.</summary>
<div class="doc-comment">
<p>Sprint 8VE B.4: Return top findings by confidence for IOC graph display.</p>
<p></p>
<p>Queries canonical_findings ordered by confidence DESC, returns dicts</p>
<p>with ioc, source_type, query, and confidence fields.</p>
</div>
</details>
</li>
<li><code>_get_insert_stmt</code> (duckdb_store.py)
<details><summary>Sprint F264: Lazy-init prepared INSERT statement for canonical_findings.</summary>
<div class="doc-comment">
<p>Sprint F264: Lazy-init prepared INSERT statement for canonical_findings.</p>
<p></p>
<p>Returns the cached prepared statement for `_SQL_INSERT_SHADOW_FINDING`</p>
<p>if the underlying connection is unchanged. On reconnect the conn</p>
<p>identity differs and the statement is transparently re-prepared.</p>
<p></p>
<p>Fail-safe: if conn.prepare() raises, returns None and emits a</p>
<p>one-shot warning. The caller MUST fall back to</p>
<p>`conn.execute(self._SQL_INSERT_SHADOW_FINDING, params)` on None</p>
<p>so the canonical write path stays alive (CLAUDE.md invariant #5).</p>
<p></p>
<p>MUST be called on the worker thread (DuckDB conn is thread-affine).</p>
</div>
</details>
</li>
<li><code>classify_ingest_outcome</code> (duckdb_store.py)
<details><summary>Sprint 8AV: Classify the canonical reason string for an ingest outcome.</summary>
<div class="doc-comment">
<p>Sprint 8AV: Classify the canonical reason string for an ingest outcome.</p>
<p></p>
<p>Internal use - maps internal FindingQualityDecision or ActivationResult</p>
<p>to a human-readable reason string.</p>
<p></p>
<p>Returns one of:</p>
<p>- "accepted"                          - finding passed quality gate</p>
<p>- "low_information_rejected"         - entropy below threshold</p>
<p>- "in_memory_duplicate_rejected"     - hot-cache duplicate</p>
<p>- "persistent_duplicate_rejected"   - LMDB cross-source duplicate</p>
<p>- "other_rejected"                   - fail-open or unknown</p>
<p>- "error_rejected"                   - store/LMDB error</p>
</div>
</details>
</li>
<li><code>_rrf_fusion</code> (lancedb_store.py) — <span class="doc-comment-inline">Reciprocal Rank Fusion with robust keying — NumPy vectorized.</span></li>
<li><code>_embed_text</code> (rag_engine.py) — <span class="doc-comment-inline">Embed text using CoreML if available, fallback to MLX.</span></li>
<li><code>_normalize_for_quality</code> (quality_assessment.py)
<details><summary>Sprint 8W + P1-5: Normalize text for entropy and dedup quality checks.</summary>
<div class="doc-comment">
<p>Sprint 8W + P1-5: Normalize text for entropy and dedup quality checks.</p>
<p></p>
<p>Normalization rules:</p>
<p>- lowercase</p>
<p>- strip leading/trailing whitespace</p>
<p>- collapse internal whitespace to single space (includes tabs/newlines)</p>
<p>- remove non-printable chars (ord &lt; 32) that are NOT whitespace</p>
<p></p>
<p>Tabs and newlines (ord &lt; 32) are whitespace and get collapsed to space first.</p>
<p>Other non-printable chars (BEL, NUL, etc.) are removed after whitespace normalization.</p>
<p></p>
<p>No stemming, lemmatization, transliteration, or locale-dependent logic.</p>
<p></p>
<p>Sprint P1-5: Try Rust fast-path first (NEON-vectorized, ~5-8x faster on</p>
<p>Apple Silicon). On any exception fall through to the Python implementation</p>
<p>— bit-identical output verified by tests/probe_p15_quality_gate.py.</p>
</div>
</details>
</li>
<li><code>find_connected_batch</code> (graph_service.py)
<details><summary>P1-1: Batch version of find_entity_history — single DuckDB round-trip.</summary>
<div class="doc-comment">
<p>P1-1: Batch version of find_entity_history — single DuckDB round-trip.</p>
<p></p>
<p>Args:</p>
<p>values: List of IOC values to query.</p>
<p>max_hops: Maximum traversal depth (default 2).</p>
<p></p>
<p>Returns:</p>
<p>Dict mapping each input value to its list of connected node dicts.</p>
<p>Falls back to individual find_entity_history calls on error.</p>
</div>
</details>
</li>
<li><code>add_ioc_batch</code> (dedup.py)
<details><summary>Batch add IOCs to Rust MmapIocDedupStore.</summary>
<div class="doc-comment">
<p>Batch add IOCs to Rust MmapIocDedupStore.</p>
<p></p>
<p>Args:</p>
<p>items: List of (ioc_value, ioc_type, confidence) tuples.</p>
<p></p>
<p>Returns:</p>
<p>List[bool] — True = new (added), False = duplicate (updated stats).</p>
<p></p>
<p>P1-07 invariants:</p>
<p>- Always-on, bounded, fail-safe (same as is_duplicate_ioc_batch)</p>
</div>
</details>
</li>
<li><code>disambiguate</code> (entity_linker.py)
<details><summary>Disambiguate entity candidates using context.</summary>
<div class="doc-comment">
<p>Disambiguate entity candidates using context.</p>
<p></p>
<p>Args:</p>
<p>entity_text: Original entity text</p>
<p>candidates: List of candidate entities</p>
<p>context: Context for disambiguation</p>
<p></p>
<p>Returns:</p>
<p>Best matching candidate or None</p>
</div>
</details>
</li>
<li><code>compute_optimal_sub_vectors</code> (lancedb_auto_tuner.py)
<details><summary>Decide next ``num_sub_vectors`` based on recall and embedding dimension.</summary>
<div class="doc-comment">
<p>Decide next ``num_sub_vectors`` based on recall and embedding dimension.</p>
<p></p>
<p>P1-2 Enhancement: Adaptive compression ratio for IVF-PQ.</p>
<p></p>
<p>num_sub_vectors controls the compression ratio:</p>
<p>- More sub_vectors = smaller storage, faster search, lower accuracy</p>
<p>- Fewer sub_vectors = larger storage, slower search, higher accuracy</p>
<p></p>
<p>For 256d embeddings: 12 sub_vectors = ~21 bytes/vector (256/12 ≈ 21)</p>
<p>For 384d embeddings: 16 sub_vectors = ~24 bytes/vector (384/16 = 24)</p>
<p></p>
<p>Heuristic (mirrors partition logic — only act when there's a problem):</p>
<p>- **recall &lt; 0.80** → grow sub_vectors (reduce compression, improve recall)</p>
<p>- **recall ≥ 0.95 AND avg_search_ms &gt; SEARCH_MS_EXCESSIVE (50ms)</p>
<p>AND current &gt; MIN** → shrink (save memory, still accurate)</p>
<p>- otherwise → no change</p>
<p></p>
<p>Clamped to [MIN_NUM_SUB_VECTORS, MAX_NUM_SUB_VECTORS] and also bounded</p>
<p>by embedding_dim (can't have more sub_vectors than dimensions).</p>
</div>
</details>
</li>
<li><code>extract_iocs_from_text</code> (ioc_graph.py)
<details><summary>Extract IOCs from raw text and PatternMatcher hits.</summary>
<div class="doc-comment">
<p>Extract IOCs from raw text and PatternMatcher hits.</p>
<p></p>
<p>Returns list of (value, ioc_type) tuples, deduplicated.</p>
<p>Private/routable IPs are filtered out.</p>
</div>
</details>
</li>
<li><code>bulk_insert_arrow</code> (db.py)</li>
<li><code>insert_findings_bulk</code> (duckdb_store.py)
<details><summary>Bulk insert shadow findings. Returns number of successfully inserted records.</summary>
<div class="doc-comment">
<p>Bulk insert shadow findings. Returns number of successfully inserted records.</p>
<p>MUST be called on the worker thread.</p>
</div>
</details>
</li>
<li><code>search_similar_adaptive</code> (lancedb_store.py)
<details><summary>Hybrid search with adaptive reranking. API-compatible with LanceDBIdentityStore.</summary>
<div class="doc-comment">
<p>Hybrid search with adaptive reranking. API-compatible with LanceDBIdentityStore.</p>
<p></p>
<p>sqlite-vec limitation: no native FTS, no FlashRank/ColBERT reranker.</p>
<p>Falls back to pure ANN search with MLX cosine similarity fallback.</p>
<p></p>
<p>Args:</p>
<p>query_text: Query text (used for reranking context if available).</p>
<p>query_emb: Query embedding vector.</p>
<p>top_k: Number of results.</p>
<p></p>
<p>Returns:</p>
<p>List of ranked entity dicts.</p>
</div>
</details>
</li>
<li><code>_brute_force_search</code> (rag_engine.py) — <span class="doc-comment-inline">Brute-force search fallback.</span></li>
<li><code>_init_coreml_embedder</code> (rag_engine.py)
<details><summary>Initialize CoreML embedder via lazy import (compat seam).</summary>
<div class="doc-comment">
<p>Initialize CoreML embedder via lazy import (compat seam).</p>
<p></p>
<p>RAGEngine is grounding authority, NOT model owner.</p>
<p>CoreML model lifecycle stays in brain/model_manager.py.</p>
<p>This method is the ONLY entry point for model-plane coupling.</p>
</div>
</details>
</li>
<li><code>_build_feed_cluster_summary</code> (analyst_workbench.py) — <span class="doc-comment-inline">F225B: Summarize feed/public/CT cluster distribution from findings.</span></li>
<li><code>get_stats</code> (duckdb_store.py)
<details><summary>Sprint P2-B: Return store statistics for sprint report.</summary>
<div class="doc-comment">
<p>Sprint P2-B: Return store statistics for sprint report.</p>
<p></p>
<p>Returns duckdb_stats section: findings count, graph stats,UMA state.</p>
</div>
</details>
</li>
<li><code>_sync_record_hypothesis_feedback</code> (duckdb_store.py)
<details><summary>Sprint F203G: Insert a single hypothesis_feedback record.</summary>
<div class="doc-comment">
<p>Sprint F203G: Insert a single hypothesis_feedback record.</p>
<p></p>
<p>Thread-safe: MUST be called on the duckdb_worker thread.</p>
<p>Silently fails if store is closed or uninitialized.</p>
<p></p>
<p>Returns True if inserted, False otherwise.</p>
</div>
</details>
</li>
<li><code>_sync_query_consistency_check</code> (duckdb_store.py)
<details><summary>Sync - MUST be called on the worker thread. Uses read pool for parallelism.</summary>
<div class="doc-comment">
<p>Sync - MUST be called on the worker thread. Uses read pool for parallelism.</p>
<p></p>
<p>Sprint F192F §2: both sprint_scorecard and sprint_delta now use findings_per_minute.</p>
</div>
</details>
</li>
<li><code>_sync_record_hypothesis_tracking</code> (duckdb_store.py)</li>
<li><code>async_ingest_finding</code> (duckdb_store.py)
<details><summary>Sprint 8W: Quality-gated single-finding ingest.</summary>
<div class="doc-comment">
<p>Sprint 8W: Quality-gated single-finding ingest.</p>
<p></p>
<p>Layer ABOVE async_record_canonical_finding - applies quality gate first,</p>
<p>then delegates to legacy storage path on accept.</p>
<p></p>
<p>Quality gate is CPU-only, deterministic, and cheap.</p>
<p>Fail-open: if quality helpers raise, the finding is stored via legacy path.</p>
<p></p>
<p>Returns FindingQualityDecision when rejected/duplicate.</p>
<p>Returns ActivationResult on accept or fail-open.</p>
</div>
</details>
</li>
<li><code>deadletter_marker_count</code> (duckdb_store.py)
<details><summary>Sprint 8L: Return the number of deadletter_duckdb_sync:* markers in WAL LMDB.</summary>
<div class="doc-comment">
<p>Sprint 8L: Return the number of deadletter_duckdb_sync:* markers in WAL LMDB.</p>
<p></p>
<p>Cheap O(n) prefix scan.</p>
<p>Used for observability and monitoring.</p>
</div>
</details>
</li>
<li><code>_cleanup_orphaned_locks</code> (duckdb_store.py)
<details><summary>F11C-2: Remove orphaned DuckDB and GraphLockManager lock files at startup.</summary>
<div class="doc-comment">
<p>F11C-2: Remove orphaned DuckDB and GraphLockManager lock files at startup.</p>
<p></p>
<p>Called from async_initialize() before connecting. Uses the same stale</p>
<p>detection as GraphLockManager to avoid removing locks held by live processes.</p>
<p></p>
<p>DuckDB WAL lock path is: str(db_path) + ".lock"</p>
<p>GraphLockManager lock path is: db_path.with_suffix(".lock") — same as DuckDB!</p>
</div>
</details>
</li>
<li><code>_maybe_compact_blocking</code> (lancedb_store.py)
<details><summary>Run lancedb optimize/compact_files in calling thread. Fail-soft.</summary>
<div class="doc-comment">
<p>Run lancedb optimize/compact_files in calling thread. Fail-soft.</p>
<p></p>
<p>LanceDB &gt;= 0.4 API: Table.optimize() returns OptimizeResult.</p>
<p>Older API used compact_files(). Try optimize() first, then</p>
<p>compact_files(), else no-op. Never raises.</p>
</div>
</details>
</li>
<li><code>add_document</code> (rag_engine.py) — <span class="doc-comment-inline">Add document to index. Silently drops if MAX_BM25_DOCUMENTS reached.</span></li>
<li><code>_ensure_coreml_model</code> (rag_engine.py)
<details><summary>Convert ModernBERT to CoreML if not already done.</summary>
<div class="doc-comment">
<p>Convert ModernBERT to CoreML if not already done.</p>
<p>Returns True if conversion succeeded or already exists.</p>
</div>
</details>
</li>
<li><code>_open_env</code> (hot_edges_cache.py)
<details><summary>Open LMDB env lazily. Idempotent. Returns None on failure.</summary>
<div class="doc-comment">
<p>Open LMDB env lazily. Idempotent. Returns None on failure.</p>
<p></p>
<p>LMDB internally locks per-env — concurrent open_env() calls are safe</p>
<p>but only the first creates the file/mmap. Cached at module level.</p>
</div>
</details>
</li>
<li><code>_encode_neighbors_batch</code> (hot_edges_cache.py)</li>
<li><code>_ensure_filter</code> (dedup.py)
<details><summary>Lazy-init filter under fcntl.flock — race-free across processes.</summary>
<div class="doc-comment">
<p>Lazy-init filter under fcntl.flock — race-free across processes.</p>
<p></p>
<p>P1-10: Single import block, fcntl.flock prevents concurrent init race.</p>
<p>Fallback Python in-memory filter has no file race (no persistence).</p>
</div>
</details>
</li>
<li><code>__init__</code> (dedup.py)
<details><summary>Args:</summary>
<div class="doc-comment">
<p>Args:</p>
<p>dedup_lmdb_path: Path to dedup LMDB. If None, resolved from HLEDAC_DEDUP_LMDB_PATH env</p>
<p>or LMDB_ROOT/dedup.lmdb fallback.</p>
<p>semantic_lmdb_path: Path to semantic dedup LMDB. If None, uses default.</p>
<p>map_size: LMDB map size in bytes for dedup store.</p>
<p>max_keys: Max keys in dedup LMDB.</p>
</div>
</details>
</li>
<li><code>flush_buffers</code> (ioc_graph.py)
<details><summary>Bulk flush both buffers to Kuzu — call in WINDUP or at buffer limit.</summary>
<div class="doc-comment">
<p>Bulk flush both buffers to Kuzu — call in WINDUP or at buffer limit.</p>
<p></p>
<p>Returns:</p>
<p>ioc_created: count of IOC nodes NEWLY CREATED in this flush.</p>
<p>IOCs that already existed are updated (last_seen bump)</p>
<p>but NOT counted here. Call graph_stats() for total count.</p>
<p>obs_flushed: count of observation edges written to the graph.</p>
</div>
</details>
</li>
<li><code>_sync_upsert_target_memory</code> (duckdb_store.py) — <span class="doc-comment-inline">Sync upsert target memory - MUST be called on worker thread.</span></li>
<li><code>_sync_query_yield_trend</code> (duckdb_store.py) — <span class="doc-comment-inline">Sync - MUST be called on the worker thread. Uses read pool for parallelism.</span></li>
<li><code>vacuum_async</code> (duckdb_store.py)
<details><summary>Execute VACUUM ANALYZE on the DuckDB file to reclaim space after deletions.</summary>
<div class="doc-comment">
<p>Execute VACUUM ANALYZE on the DuckDB file to reclaim space after deletions.</p>
<p></p>
<p>Only available for file mode (_db_path is not None). Returns True on success.</p>
<p>Fail-safe: any error is logged and False is returned.</p>
</div>
</details>
</li>
<li><code>_ensure_usearch_index</code> (lancedb_store.py) — <span class="doc-comment-inline">Lazy load usearch index (experimental).</span></li>
<li><code>find_connections</code> (graph_rag.py)
<details><summary>Find connection paths between two entities (async, parallel node fetch).</summary>
<div class="doc-comment">
<p>Find connection paths between two entities (async, parallel node fetch).</p>
<p></p>
<p>M1 8GB: Runs BFS in Rust rayon io_pool (2 threads) to avoid blocking event loop.</p>
<p>Previously used asyncio.to_thread (default executor) → now uses run_in_io_pool.</p>
<p></p>
<p>Args:</p>
<p>entity1: First entity name</p>
<p>entity2: Second entity name</p>
<p>max_hops: Maximum hops to search</p>
<p></p>
<p>Returns:</p>
<p>List of connection paths</p>
</div>
</details>
</li>
<li><code>_extract_entities_from_node</code> (graph_rag.py)
<details><summary>Extract entity mentions from a node for novelty detection.</summary>
<div class="doc-comment">
<p>Extract entity mentions from a node for novelty detection.</p>
<p></p>
<p>Simple entity extraction based on capitalization patterns</p>
<p>and known entity markers.</p>
<p></p>
<p>Args:</p>
<p>node: Knowledge node to extract entities from</p>
<p></p>
<p>Returns:</p>
<p>Set of extracted entity strings</p>
</div>
</details>
</li>
<li><code>_calculate_narrative_confidence</code> (graph_rag.py)
<details><summary>Calculate narrative confidence score (0-1).</summary>
<div class="doc-comment">
<p>Calculate narrative confidence score (0-1).</p>
<p></p>
<p>Factors:</p>
<p>- Number of unique evidence sources</p>
<p>- Domain diversity</p>
<p>- Recency</p>
<p>- Echo penalty</p>
</div>
</details>
</li>
<li><code>multi_hop_search_streaming</code> (graph_rag.py)
<details><summary>Streaming version of multi-hop search that yields nodes as they are discovered.</summary>
<div class="doc-comment">
<p>Streaming version of multi-hop search that yields nodes as they are discovered.</p>
<p></p>
<p>Enables early processing of results before full traversal completes.</p>
<p>Uses asyncio.Queue for backpressure control.</p>
<p></p>
<p>Args:</p>
<p>query: Search query</p>
<p>hops: Number of hops to traverse (default: 2)</p>
<p>max_nodes: Maximum nodes to return (default: 20)</p>
<p></p>
<p>Yields:</p>
<p>Dict representing a discovered node with its metadata</p>
</div>
</details>
</li>
<li><code>_build_evidence_gaps</code> (analyst_workbench.py)
<details><summary>F225B: Identify evidence gaps from findings and source family summary.</summary>
<div class="doc-comment">
<p>F225B: Identify evidence gaps from findings and source family summary.</p>
<p></p>
<p>Checks for: feed-only (no public/CT corroboration), no high-confidence,</p>
<p>no multi-IOC type, missing graph connectivity.</p>
</div>
</details>
</li>
<li><code>_encode_neighbors_denorm</code> (hot_edges_cache.py)</li>
<li><code>stats</code> (hot_edges_cache.py)
<details><summary>Return cache statistics: {node_count, env_open, enabled}.</summary>
<div class="doc-comment">
<p>Return cache statistics: {node_count, env_open, enabled}.</p>
<p></p>
<p>Cheap: single LMDB stat call. Safe to call frequently.</p>
</div>
</details>
</li>
<li><code>store_persistent_dedup</code> (dedup.py)
<details><summary>Store a fingerprint → finding_id mapping in persistent dedup LMDB.</summary>
<div class="doc-comment">
<p>Store a fingerprint → finding_id mapping in persistent dedup LMDB.</p>
<p></p>
<p>P1-4: Also update Bloom filter for fast negative dedup.</p>
<p>F272: Lazy init on first use.</p>
<p></p>
<p>Args:</p>
<p>fp: 32-char BLAKE2b fingerprint hex string</p>
<p>finding_id: canonical finding ID</p>
</div>
</details>
</li>
<li><code>_maybe_compact_blocking</code> (ann_index.py) — <span class="doc-comment-inline">LanceDB compaction trigger (sync, fail-soft).</span></li>
<li><code>forget_weak_memories</code> (neuromorphic.py)
<details><summary>Remove weak memories below threshold strength.</summary>
<div class="doc-comment">
<p>Remove weak memories below threshold strength.</p>
<p></p>
<p>Args:</p>
<p>threshold: Minimum strength to keep</p>
<p></p>
<p>Returns:</p>
<p>Number of patterns forgotten</p>
</div>
</details>
</li>
<li><code>insert_findings_bulk_as_tuples</code> (duckdb_store.py)
<details><summary>Bulk insert shadow findings from pre-built tuple rows.</summary>
<div class="doc-comment">
<p>Bulk insert shadow findings from pre-built tuple rows.</p>
<p>MUST be called on the worker thread.</p>
<p>Returns number of successfully inserted records.</p>
</div>
</details>
</li>
<li><code>_sync_record_entity_observations_bulk</code> (duckdb_store.py) — <span class="doc-comment-inline">Sprint F350M: Bulk insert entity_observations.</span></li>
<li><code>initialize</code> (duckdb_store.py)
<details><summary>Initialize DuckDB connection synchronously (backward compat wrapper).</summary>
<div class="doc-comment">
<p>Initialize DuckDB connection synchronously (backward compat wrapper).</p>
<p></p>
<p>For async code prefer async_initialize().</p>
</div>
</details>
</li>
<li><code>_mlx_rerank</code> (lancedb_store.py)
<details><summary>Rerank candidates using MLX cosine similarity.</summary>
<div class="doc-comment">
<p>Rerank candidates using MLX cosine similarity.</p>
<p></p>
<p>P4.2: Uses module-level _cosine_sim_batch (compiled once at import).</p>
<p>Supports (B, D) × (N, D) → (B, N) for flexible batching.</p>
</div>
</details>
</li>
<li><code>close</code> (lancedb_store.py) — <span class="doc-comment-inline">Close database connection and cache.</span></li>
<li><code>calculate_network_metrics</code> (graph_rag.py)
<details><summary>Calculate comprehensive network metrics.</summary>
<div class="doc-comment">
<p>Calculate comprehensive network metrics.</p>
<p></p>
<p>From evidence_network_analyzer.py comments:</p>
<p>"Step 7: Calculate network metrics"</p>
<p>"Basic metrics"</p>
<p>"Clustering metrics"</p>
<p>"Path metrics"</p>
<p>"Evidence-specific metrics"</p>
<p></p>
<p>Returns:</p>
<p>Dictionary of network metrics</p>
</div>
</details>
</li>
<li><code>_build_ig_graph</code> (graph_rag.py) — <span class="doc-comment-inline">Build an igraph from adjacency list. M1-optimized, C-core.</span></li>
<li><code>_extract_snippet</code> (analyst_workbench.py)
<details><summary>Extract relevant snippet from payload_text using keyword proximity.</summary>
<div class="doc-comment">
<p>Extract relevant snippet from payload_text using keyword proximity.</p>
<p></p>
<p>Fail-soft: returns None if no match or payload_text is None.</p>
</div>
</details>
</li>
<li><code>create_analyst_workbench</code> (analyst_workbench.py)
<details><summary>Create AnalystWorkbench with lazily-initialized store references.</summary>
<div class="doc-comment">
<p>Create AnalystWorkbench with lazily-initialized store references.</p>
<p></p>
<p>Stores are resolved from global singletons where available:</p>
<p>- VectorStore via vector_store.get_vector_store() (singleton)</p>
<p>- DuckPGQGraph via knowledge.graph_service._get_graph() (singleton)</p>
<p></p>
<p>DuckDBShadowStore and SemanticStore have no module-level singletons —</p>
<p>pass them explicitly if available.</p>
<p></p>
<p>Fail-soft: if any store is unavailable, workbench operates without it.</p>
</div>
</details>
</li>
<li><code>_upsert_lancedb_entity_async</code> (graph_service.py)</li>
<li><code>resolve_aliases</code> (entity_linker.py)
<details><summary>Resolve entity aliases to canonical Wikidata labels.</summary>
<div class="doc-comment">
<p>Resolve entity aliases to canonical Wikidata labels.</p>
<p></p>
<p>Args:</p>
<p>entities: List of entity texts to resolve</p>
<p></p>
<p>Returns:</p>
<p>Dictionary mapping original text to canonical label</p>
</div>
</details>
</li>
<li><code>get_analytics_graph_for_synthesis</code> (graph_attachment.py)
<details><summary>Sprint 8VY: Read-only seam replacing store._ioc_graph fallback in _windup_synthesis().</summary>
<div class="doc-comment">
<p>Sprint 8VY: Read-only seam replacing store._ioc_graph fallback in _windup_synthesis().</p>
<p></p>
<p>PURPOSE</p>
<p>-------</p>
<p>Replaces the elif hasattr(store, "_ioc_graph") and store._ioc_graph fallback in</p>
<p>_windup_synthesis(). This is the Priority 2 / analytics-donor path for synthesis.</p>
<p></p>
<p>CONSUMER</p>
<p>--------</p>
<p>_windup_synthesis(): runner.inject_graph(store.get_analytics_graph_for_synthesis())</p>
<p></p>
<p>STORE IS NOT GRAPH TRUTH OWNER</p>
<p>-------------------------------</p>
<p>DuckDBShadowStore is NOT graph authority. This seam explicitly labels the</p>
<p>analytics donor backend. Callers must handle None.</p>
<p></p>
<p>CAPABILITY REQUIREMENTS</p>
<p>-----------------------</p>
<p>DuckPGQGraph (analytics donor) has: stats, get_top_nodes_by_degree, export_edge_list.</p>
<p>DuckPGQGraph does NOT have: export_stix_bundle, buffer_ioc, flush_buffers.</p>
<p>For STIX, use store.get_stix_graph() (Priority 1).</p>
<p></p>
<p>Returns:</p>
<p>Any: The attached analytics graph (DuckPGQGraph) or None.</p>
</div>
</details>
</li>
<li><code>corroboration_level</code> (evidence_chain.py)
<details><summary>F225C: Determine corroboration level from a list of source families.</summary>
<div class="doc-comment">
<p>F225C: Determine corroboration level from a list of source families.</p>
<p></p>
<p>Rules:</p>
<p>- FEED + PUBLIC → multi_source</p>
<p>- FEED + CT     → multi_source</p>
<p>- multiple feed sources only → single_source (duplicates don't multi-source)</p>
<p>- CT-only or PUBLIC-only or DEEP-only or DOC-only → single_source</p>
<p>- no support    → none</p>
<p></p>
<p>Distinguishes single-source duplicates from genuine multi-source corroboration.</p>
</div>
</details>
</li>
<li><code>insert_finding</code> (duckdb_store.py)</li>
<li><code>_sync_close_on_worker</code> (duckdb_store.py) — <span class="doc-comment-inline">Close all connections - MUST be called on the worker thread.</span></li>
<li><code>_sync_query_best_sprints</code> (duckdb_store.py)
<details><summary>Sync - MUST be called on the worker thread. Uses read pool for parallelism.</summary>
<div class="doc-comment">
<p>Sync - MUST be called on the worker thread. Uses read pool for parallelism.</p>
<p></p>
<p>Sprint F192F §2: uses findings_per_minute (matches sprint_scorecard naming).</p>
</div>
</details>
</li>
<li><code>_sync_query_worst_sprints</code> (duckdb_store.py)
<details><summary>Sync - MUST be called on the worker thread. Uses read pool for parallelism.</summary>
<div class="doc-comment">
<p>Sync - MUST be called on the worker thread. Uses read pool for parallelism.</p>
<p></p>
<p>Sprint F192F §2: uses findings_per_minute (matches sprint_scorecard naming).</p>
</div>
</details>
</li>
<li><code>_wal_write_deadletter_marker</code> (duckdb_store.py)</li>
<li><code>_write_worker</code> (lancedb_store.py)
<details><summary>Background worker that drains the write queue serially.</summary>
<div class="doc-comment">
<p>Background worker that drains the write queue serially.</p>
<p></p>
<p>This is the single-writer bottleneck that prevents LanceDB 0.33+ segfaults</p>
<p>when multiple asyncio tasks try to write concurrently.</p>
</div>
</details>
</li>
<li><code>_extract_answer</code> (analyst_workbench.py)
<details><summary>Deterministic extractive answer from context chunks.</summary>
<div class="doc-comment">
<p>Deterministic extractive answer from context chunks.</p>
<p></p>
<p>Returns the longest contiguous text span that contains</p>
<p>the most question keywords. No model required.</p>
<p></p>
<p>Fail-soft: returns "No relevant information found." on any error.</p>
</div>
</details>
</li>
<li><code>_build_corroboration_summary</code> (analyst_workbench.py)
<details><summary>F225C: Build corroboration summary from findings source families.</summary>
<div class="doc-comment">
<p>F225C: Build corroboration summary from findings source families.</p>
<p></p>
<p>Uses summarize_chain_support if chains are available via the evidence_chain</p>
<p>module global registry, otherwise falls back to findings source_type.</p>
<p></p>
<p>Bounds: max MAX_CORROBORATION_SUMMARY lines.</p>
<p>Fail-soft: returns ("Corroboration unavailable",) on any error.</p>
</div>
</details>
</li>
<li><code>_init_semantic_dedup_cache</code> (dedup.py)
<details><summary>Initialize semantic dedup cache (Sprint F195).</summary>
<div class="doc-comment">
<p>Initialize semantic dedup cache (Sprint F195).</p>
<p></p>
<p>Memory-aware: skips init if RSS &gt; 6GB threshold.</p>
<p>Fail-soft: any exception stored in _semantic_dedup_boot_error.</p>
</div>
</details>
</li>
<li><code>wal_write_pending_sync_marker</code> (wal.py)
<details><summary>Write a pending-sync recovery marker to LMDB.</summary>
<div class="doc-comment">
<p>Write a pending-sync recovery marker to LMDB.</p>
<p></p>
<p>Marker key:  pending_duckdb_sync:{id}</p>
<p>Value:       same structure as WAL finding (id, query, source_type, confidence, ts)</p>
<p></p>
<p>Written ONLY when LMDB succeeded but DuckDB failed.</p>
<p>A future recovery sprint can find it via prefix scan and retry the DuckDB write.</p>
<p></p>
<p>Evicts oldest markers if at or above MAX_PENDING_SYNC_MARKERS bound.</p>
</div>
</details>
</li>
<li><code>compact</code> (wal.py)
<details><summary>Compact the WAL LMDB if interval OR write count threshold reached.</summary>
<div class="doc-comment">
<p>Compact the WAL LMDB if interval OR write count threshold reached.</p>
<p></p>
<p>Compaction is triggered when EITHER:</p>
<p>- Time since last compaction &gt;= _compact_interval_s (default: 1h)</p>
<p>- Writes since last compaction &gt;= _compact_write_threshold (default: 5000)</p>
<p>- WAL LMDB is available (not using unified store)</p>
<p></p>
<p>Returns compaction stats dict or None if skipped / unavailable.</p>
</div>
</details>
</li>
<li><code>_normalize_ioc_value</code> (ioc_dedup_adapter.py)
<details><summary>Normalize IOC value according to type rules.</summary>
<div class="doc-comment">
<p>Normalize IOC value according to type rules.</p>
<p>Mirrors Rust ioc_dedup.rs::normalize_ioc() for Python fallback path.</p>
</div>
</details>
</li>
<li><code>_sync_query_sprint_trend</code> (duckdb_store.py) — <span class="doc-comment-inline">Sync query - MUST be called on the worker thread. Uses persistent _file_conn.</span></li>
<li><code>upsert_episode</code> (duckdb_store.py) — <span class="doc-comment-inline">Sprint 8UC B.2: Zapsat sprint epizodu pro budoucí recall.</span></li>
<li><code>_sync_upsert_global_entities</code> (duckdb_store.py)
<details><summary>Sync upsert global entities - MUST be called on worker thread.</summary>
<div class="doc-comment">
<p>Sync upsert global entities - MUST be called on worker thread.</p>
<p></p>
<p>Uses DuckDB's built-in file locking via access_mode='automatic'.</p>
<p>DuckDB handles crash-safety internally - no external lock file needed.</p>
</div>
</details>
</li>
<li><code>_store_envelope_payload</code> (duckdb_store.py)
<details><summary>Sprint F202A §2: Update LMDB WAL entry with envelope payload_text.</summary>
<div class="doc-comment">
<p>Sprint F202A §2: Update LMDB WAL entry with envelope payload_text.</p>
<p></p>
<p>Called after initial ingest when envelope is attached post-hoc.</p>
<p>Returns True if LMDB update succeeded.</p>
</div>
</details>
</li>
<li><code>_checkpoint_loop</code> (duckdb_store.py)
<details><summary>Background checkpoint task for DuckDB native WAL.</summary>
<div class="doc-comment">
<p>Background checkpoint task for DuckDB native WAL.</p>
<p></p>
<p>Runs every 300s (O3) to flush WAL to main database file, bounding WAL growth.</p>
<p>duckdb_autocheckpoint=262144 (256MB) provides a secondary safety valve between</p>
<p>runs. Fail-safe: any error is silently caught and logged.</p>
<p>Only active for file mode; _checkpoint_task is None for :memory: mode.</p>
</div>
</details>
</li>
<li><code>create_owned_store</code> (duckdb_store.py)
<details><summary>Sprint 8AM C.3.a: Create an owned DuckDBShadowStore instance.</summary>
<div class="doc-comment">
<p>Sprint 8AM C.3.a: Create an owned DuckDBShadowStore instance.</p>
<p></p>
<p>Uses paths.py SSOT for RAMDisk-aware path resolution.</p>
<p>RAMDISK_ACTIVE=True: db at DUCKDB_STORE_ROOT, temp at RAMDISK_ROOT/duckdb_tmp</p>
<p>RAMDISK_ACTIVE=False: degraded :memory: fallback</p>
<p></p>
<p>This is the ONE place in main.py where DuckDBShadowStore is instantiated</p>
<p>for the owned runtime path. Avoids coupling __main__.py to DuckDBShadowStore</p>
<p>internals.</p>
<p></p>
<p>Returns:</p>
<p>DuckDBShadowStore: initialized store ready for async_initialize()</p>
</div>
</details>
</li>
<li><code>_initialize</code> (lancedb_store.py) — <span class="doc-comment-inline">Initialize database and table.</span></li>
<li><code>compute_similarity</code> (lancedb_store.py)
<details><summary>Compute cosine similarity between two embeddings.</summary>
<div class="doc-comment">
<p>Compute cosine similarity between two embeddings.</p>
<p></p>
<p>Args:</p>
<p>emb1: First embedding.</p>
<p>emb2: Second embedding.</p>
<p></p>
<p>Returns:</p>
<p>Cosine similarity score (0-1).</p>
</div>
</details>
</li>
<li><code>_label_propagation_igraph</code> (graph_rag.py)
<details><summary>Community detection via igraph C-core label propagation (5-10x faster).</summary>
<div class="doc-comment">
<p>Community detection via igraph C-core label propagation (5-10x faster).</p>
<p></p>
<p>Returns None on igraph unavailable / RAM constraint.</p>
</div>
</details>
</li>
<li><code>_decode_neighbors_batch</code> (hot_edges_cache.py)</li>
<li><code>_compute_entropy</code> (quality_assessment.py)
<details><summary>Sprint 8W + P1-5: Compute Shannon entropy in bits per character.</summary>
<div class="doc-comment">
<p>Sprint 8W + P1-5: Compute Shannon entropy in bits per character.</p>
<p></p>
<p>Uses collections.Counter for efficiency (no Python for-loop over characters).</p>
<p>Returns 0.0 for empty text.</p>
<p></p>
<p>Sprint P1-5: Try Rust fast-path first (256-bin histogram + f64::log2 in</p>
<p>native code, ~10-30x faster than Counter() on Apple Silicon). On any</p>
<p>exception fall through to the Python implementation — output is</p>
<p>bit-identical because both paths operate on UTF-8 bytes after lowercase.</p>
</div>
</details>
</li>
<li><code>_compute_url_fingerprint</code> (quality_assessment.py)
<details><summary>Sprint 8AK: URL-first dedup fingerprint.</summary>
<div class="doc-comment">
<p>Sprint 8AK: URL-first dedup fingerprint.</p>
<p></p>
<p>If a canonical URL is available in provenance, use it as the primary</p>
<p>dedup signal (source-independent, deterministic). Falls back to</p>
<p>BLAKE2b(text) when no URL is present.</p>
<p></p>
<p>URL is normalized before fingerprinting per OSINT URL normalization rules.</p>
<p></p>
<p>Returns 32-char hex BLAKE2b-128 fingerprint.</p>
<p></p>
<p>Sprint F216R: Uses Rust url_engine.fingerprint (xxHash64 u64) when available,</p>
<p>converting to hex string for backward compatibility with existing callers.</p>
</div>
</details>
</li>
<li><code>init_audit_schema</code> (db.py) — <span class="doc-comment-inline">Initialize audit events table in DuckDB.</span></li>
<li><code>get_connected_iocs_batch</code> (graph_attachment.py)
<details><summary>P1-1 fix: Batch version of get_connected_iocs for N+1 query optimization.</summary>
<div class="doc-comment">
<p>P1-1 fix: Batch version of get_connected_iocs for N+1 query optimization.</p>
<p>Returns dict mapping each value to its connected IOC list.</p>
<p></p>
<p>CAPABILITY REQUIREMENTS</p>
<p>-----------------------</p>
<p>Requires attached graph to implement find_connected_batch(values, max_hops) → dict.</p>
<p>DuckPGQGraph: has this method (P1-1 fix).</p>
<p>IOCGraph: does NOT have this method → returns {} (fail-open).</p>
</div>
</details>
</li>
<li><code>_init_synaptic_weights</code> (neuromorphic.py) — <span class="doc-comment-inline">Initialize sparse synaptic weight matrix.</span></li>
<li><code>_encode_pattern</code> (neuromorphic.py) — <span class="doc-comment-inline">Encode arbitrary data into a neuron activation vector.</span></li>
<li><code>_get_memory_pressure</code> (duckdb_store.py)
<details><summary>Get current memory pressure level using psutil.</summary>
<div class="doc-comment">
<p>Get current memory pressure level using psutil.</p>
<p></p>
<p>Returns MemoryPressureLevel.NORMAL if unavailable.</p>
<p>Uses same thresholds as BaseCoordinator.check_memory_pressure().</p>
<p></p>
<p>ISSUE-025: Memory pressure gating for parquet export on M1 8GB.</p>
</div>
</details>
</li>
<li><code>query_findings</code> (duckdb_store.py) — <span class="doc-comment-inline">Select recent shadow findings. Returns list of dicts.</span></li>
<li><code>ensure_connected</code> (duckdb_store.py)
<details><summary>Lazy connection init — called on first actual query.</summary>
<div class="doc-comment">
<p>Lazy connection init — called on first actual query.</p>
<p></p>
<p>When lazy=True (default): defers actual DuckDB connection to this method.</p>
<p>When lazy=False: no-op (already connected via async_initialize).</p>
<p></p>
<p>This is the on-demand bootstrap that enables ~0s sprint boot with no findings.</p>
<p>All async write methods call ensure_connected() before their run_in_executor.</p>
<p></p>
<p>Barrier semantics (Sprint DuckDB Lazy Init F265X):</p>
<p>In lazy mode, _startup_ready is cleared here BEFORE connecting, then</p>
<p>set again AFTER connecting. This ensures writes always wait for the</p>
<p>connection to be ready (no spurious proceeds before connection exists).</p>
</div>
</details>
</li>
<li><code>async_record_shadow_findings_batch</code> (duckdb_store.py)</li>
<li><code>async_record_source_hit</code> (duckdb_store.py)</li>
<li><code>async_ingest_cooccurrence_batch</code> (duckdb_store.py)
<details><summary>Batch upsert IOC co-occurrence pairs into DuckDB.</summary>
<div class="doc-comment">
<p>Batch upsert IOC co-occurrence pairs into DuckDB.</p>
<p></p>
<p>Replaces raw sqlite3 DELETE+INSERT in IOCooccurrenceMiner.persist().</p>
<p>Uses DELETE + per-item INSERT (support &gt;= 2 filter applied by caller).</p>
<p></p>
<p>Args:</p>
<p>pairs: List of dicts with keys:</p>
<p>ioc_a, ioc_b, ioc_type_a, ioc_type_b,</p>
<p>support, confidence, score, last_seen</p>
<p></p>
<p>Returns:</p>
<p>True on success, False on failure.</p>
</div>
</details>
</li>
<li><code>_sync_ingest_cooccurrence_batch</code> (duckdb_store.py) — <span class="doc-comment-inline">Synchronous batch upsert for IOC co-occurrence pairs.</span></li>
<li><code>_sync_load_cooccurrence</code> (duckdb_store.py) — <span class="doc-comment-inline">Synchronous load of IOC co-occurrence pairs from DuckDB.</span></li>
<li><code>async_query_findings_by_keywords</code> (duckdb_store.py)
<details><summary>P1-2: Read canonical_findings rows matching ANY of the given keywords.</summary>
<div class="doc-comment">
<p>P1-2: Read canonical_findings rows matching ANY of the given keywords.</p>
<p>Uses OR across keywords so "ransomware breach" matches findings</p>
<p>containing either "ransomware" OR "breach".</p>
<p>Used by run_runtime_pivot_prelude() for cross-sprint seed extraction</p>
<p>when the full query string has no direct match.</p>
<p></p>
<p>Args:</p>
<p>keywords: List of keywords to search in query/title/payload_text.</p>
<p>limit: Max rows to return (default 1000).</p>
<p></p>
<p>Returns:</p>
<p>list[dict] with keys: id, query, source_type, title, payload_text, ts.</p>
<p>Fail-soft: returns [] on any error.</p>
</div>
</details>
</li>
<li><code>_sync_query_findings_by_keywords_impl</code> (duckdb_store.py) — <span class="doc-comment-inline">Sync - MUST be called on worker thread. Internal: actual query without cache.</span></li>
<li><code>_sync</code> (duckdb_store.py)</li>
<li><code>_embed_single</code> (lancedb_store.py) — <span class="doc-comment-inline">Embed single text via current embedder (for indexing - uses embed_document).</span></li>
<li><code>_maybe_compact_async</code> (lancedb_store.py) — <span class="doc-comment-inline">Non-blocking compaction trigger; actual work in executor.</span></li>
<li><code>get_citation_context</code> (lancedb_store.py)
<details><summary>Get papers that cite or are cited by the given paper.</summary>
<div class="doc-comment">
<p>Get papers that cite or are cited by the given paper.</p>
<p></p>
<p>Args:</p>
<p>paper_id: Paper ID to find citation context for.</p>
<p>max_papers: Max papers to return.</p>
<p></p>
<p>Returns:</p>
<p>List of related AcademicPaper instances.</p>
</div>
</details>
</li>
<li><code>ask_with_reasoning</code> (graph_rag.py)
<details><summary>Ask a question with multi-hop reasoning.</summary>
<div class="doc-comment">
<p>Ask a question with multi-hop reasoning.</p>
<p></p>
<p>Returns both the facts and the reasoning paths.</p>
<p></p>
<p>Args:</p>
<p>question: Question to ask</p>
<p>hops: Number of hops to traverse</p>
<p>max_nodes: Maximum nodes to return</p>
<p></p>
<p>Returns:</p>
<p>Dictionary with facts and reasoning paths</p>
</div>
</details>
</li>
<li><code>analyze_key_paths</code> (graph_rag.py)
<details><summary>Analyze key paths between two nodes (async).</summary>
<div class="doc-comment">
<p>Analyze key paths between two nodes (async).</p>
<p></p>
<p>From evidence_network_analyzer.py comments:</p>
<p>"Step 6: Analyze key paths in the network"</p>
<p>"Find shortest paths between central nodes"</p>
<p>"Look for paths that might be important reasoning chains"</p>
<p>"Calculate path confidence"</p>
<p></p>
<p>Args:</p>
<p>start_node_id: Starting node</p>
<p>target_node_id: Target node</p>
<p>max_hops: Maximum path length</p>
<p></p>
<p>Returns:</p>
<p>List of paths with confidence scores</p>
</div>
</details>
</li>
<li><code>save_index</code> (rag_engine.py)
<details><summary>Save index to disk.</summary>
<div class="doc-comment">
<p>Save index to disk.</p>
<p></p>
<p>Args:</p>
<p>path: Path to save index. Uses index_path from init if not provided.</p>
</div>
</details>
</li>
<li><code>__init__</code> (quality_assessment.py)</li>
<li><code>record_rejection</code> (quality_assessment.py)</li>
<li><code>compute_context_similarity</code> (entity_linker.py)
<details><summary>Compute semantic similarity between entity description and context.</summary>
<div class="doc-comment">
<p>Compute semantic similarity between entity description and context.</p>
<p></p>
<p>Uses rapidfuzz for fuzzy matching (lightweight, no ML models).</p>
<p></p>
<p>Args:</p>
<p>entity_desc: Entity description</p>
<p>context: Context text</p>
<p></p>
<p>Returns:</p>
<p>Similarity score (0-1)</p>
</div>
</details>
</li>
<li><code>close</code> (semantic_store.py) — <span class="doc-comment-inline">TEARDOWN — final flush + close connections.</span></li>
<li><code>__init__</code> (ioc_dedup_adapter.py)</li>
<li><code>_persist_lmdb</code> (ioc_dedup_adapter.py)
<details><summary>Persist current state to LMDB.</summary>
<div class="doc-comment">
<p>Persist current state to LMDB.</p>
<p>Called on advance_sprint() and during graceful shutdown.</p>
</div>
</details>
</li>
<li><code>deserialize_chain</code> (evidence_chain.py)
<details><summary>Deserialize EvidenceChain from JSON string in payload_text.</summary>
<div class="doc-comment">
<p>Deserialize EvidenceChain from JSON string in payload_text.</p>
<p></p>
<p>Returns None if payload_text is None/empty or parsing fails.</p>
</div>
</details>
</li>
<li><code>_sync_get_research_sessions_by_sprint</code> (duckdb_store.py) — <span class="doc-comment-inline">Sprint F350M: Fetch research_sessions by sprint_id.</span></li>
<li><code>_sync_get_recent_research_sessions</code> (duckdb_store.py) — <span class="doc-comment-inline">Sprint F350M: Fetch recent research_sessions.</span></li>
<li><code>_sync_query_findings_by_text_impl</code> (duckdb_store.py) — <span class="doc-comment-inline">Sync - MUST be called on worker thread. Internal: actual query without cache.</span></li>
<li><code>_sync_upsert_scorecard</code> (duckdb_store.py) — <span class="doc-comment-inline">Sync upsert scorecard - MUST be called on worker thread.</span></li>
<li><code>_sync_query_high_value_ranking</code> (duckdb_store.py) — <span class="doc-comment-inline">Sync - MUST be called on the worker thread. Uses read pool for parallelism.</span></li>
<li><code>_sync_verify_duckdb_record</code> (duckdb_store.py)
<details><summary>Sprint 8H: Fresh read-back verification from a NEW DuckDB connection.</summary>
<div class="doc-comment">
<p>Sprint 8H: Fresh read-back verification from a NEW DuckDB connection.</p>
<p></p>
<p>Called after write commit to confirm the record is durable.</p>
<p>Uses a non-read-only fresh connection so the WAL is flushed.</p>
<p>MUST be called on the worker thread.</p>
</div>
</details>
</li>
<li><code>_store_persistent_dedup</code> (duckdb_store.py)
<details><summary>Store a fingerprint -&gt; finding_id mapping in persistent dedup LMDB.</summary>
<div class="doc-comment">
<p>Store a fingerprint -&gt; finding_id mapping in persistent dedup LMDB.</p>
<p></p>
<p>DEPRECATED (Sprint F222): Delegates to DedupManager.store_persistent_dedup().</p>
<p>Kept for backward compat during migration - remove when all callers migrated.</p>
<p></p>
<p>P1-4: Also update Bloom filter in DedupManager when available.</p>
<p>Falls back to store._dedup_lmdb directly for backward compat with tests</p>
<p>that mock store._dedup_lmdb without going through DedupManager.</p>
</div>
</details>
</li>
<li><code>_find_paths_bfs</code> (graph_rag.py)
<details><summary>BFS to find paths between nodes (runs in thread pool).</summary>
<div class="doc-comment">
<p>BFS to find paths between nodes (runs in thread pool).</p>
<p></p>
<p>Returns:</p>
<p>List of connection paths</p>
</div>
</details>
</li>
<li><code>save_hnsw_index</code> (rag_engine.py)
<details><summary>Save HNSW index to disk.</summary>
<div class="doc-comment">
<p>Save HNSW index to disk.</p>
<p></p>
<p>Args:</p>
<p>path: Path to save index. Uses config.hnsw_index_path if not provided.</p>
</div>
</details>
</li>
<li><code>load_hnsw_index</code> (rag_engine.py)
<details><summary>Load HNSW index from disk.</summary>
<div class="doc-comment">
<p>Load HNSW index from disk.</p>
<p></p>
<p>Args:</p>
<p>path: Path to load index from. Uses config.hnsw_index_path if not provided.</p>
</div>
</details>
</li>
<li><code>_compute_dedup_fingerprint</code> (quality_assessment.py)
<details><summary>Sprint 8W + P1-5: Compute BLAKE2b-128 fingerprint of normalized text.</summary>
<div class="doc-comment">
<p>Sprint 8W + P1-5: Compute BLAKE2b-128 fingerprint of normalized text.</p>
<p></p>
<p>Uses hashlib.blake2b (NOT Python built-in hash()).</p>
<p>digest_size=16 → 32 hex chars.</p>
<p>Stable across process restarts.</p>
<p></p>
<p>Sprint P1-5: Try Rust fast-path first (NEON-vectorized BLAKE2b in Rust,</p>
<p>~2-3x faster than the CPython C extension on Apple Silicon). The Rust</p>
<p>implementation is bit-for-bit compatible with hashlib.blake2b(digest_size=16)</p>
<p>so existing LMDB-persisted fingerprints remain valid. On any exception</p>
<p>fall through to the Python fallback.</p>
<p></p>
<p>IMPORTANT: Only payload text goes through this path. URL fingerprints use</p>
<p>`_compute_url_fingerprint` (Sprint F216R, xxHash64 format) to preserve</p>
<p>the existing LMDB key format.</p>
</div>
</details>
</li>
<li><code>_dedup_manager_sigterm_handler</code> (dedup.py)
<details><summary>F267: SIGTERM handler — calls close() on all tracked DedupManager instances.</summary>
<div class="doc-comment">
<p>F267: SIGTERM handler — calls close() on all tracked DedupManager instances.</p>
<p></p>
<p>Called synchronously on the signal-receiving thread. We ONLY call close()</p>
<p>here (not __del__), so it's safe: close() persists mmap + releases fd.</p>
<p>We then re-raise the signal so the OS can deliver it to the default handler,</p>
<p>which will terminate the process.</p>
<p></p>
<p>Note: signal handlers run on a different thread in Python, so we use</p>
<p>an interrupt-driven approach — close() is thread-safe for our use case</p>
<p>(DashMap + Arc&lt;File&gt; are Send+Sync on Unix).</p>
</div>
</details>
</li>
<li><code>serialize_chain</code> (evidence_chain.py)
<details><summary>Serialize EvidenceChain to JSON string for storage in payload_text/envelope.</summary>
<div class="doc-comment">
<p>Serialize EvidenceChain to JSON string for storage in payload_text/envelope.</p>
<p></p>
<p>Returns None if serialization fails OR if result exceeds MAX_CHAIN_JSON_BYTES.</p>
</div>
</details>
</li>
<li><code>_compute_rg_stats_fallback</code> (duckdb_store.py) — <span class="doc-comment-inline">Compute row-group stats using PyArrow (fallback).</span></li>
<li><code>_regex_split_statements</code> (duckdb_store.py) — <span class="doc-comment-inline">Split on ';' respecting string literals — safe fallback for older DuckDB.</span></li>
<li><code>_sync_query_source_leaderboard</code> (duckdb_store.py) — <span class="doc-comment-inline">Sync query - MUST be called on the worker thread. Uses persistent _file_conn.</span></li>
<li><code>_resolve_path</code> (duckdb_store.py)
<details><summary>Resolve _db_path and _temp_dir based on RAMDISK availability.</summary>
<div class="doc-comment">
<p>Resolve _db_path and _temp_dir based on RAMDISK availability.</p>
<p></p>
<p>RAMDISK_ACTIVE=True:  DUCKDB_STORE_ROOT / "shadow_analytics.duckdb", temp = RAMDISK_ROOT / "duckdb_tmp"</p>
<p>RAMDISK_ACTIVE=False: DUCKDB_STORE_ROOT / "analytics.duckdb",     temp = None (no spill to SSD)</p>
<p></p>
<p>Sprint F265B: All hot DuckDB data now uses DUCKDB_STORE_ROOT (co-located with LMDB_STORE_ROOT</p>
<p>for atomic WAL operations). DUCKDB_STORE_ROOT defaults to SPRINT_STORE_ROOT.parent / "duckdb_store"</p>
<p>which is ~/.hledac/duckdb_store — or RAMDISK-backed when HLEDAC_RAMDISK/HLEDAC_DUCKDB_STORE is set.</p>
</div>
</details>
</li>
<li><code>async_initialize_schema</code> (duckdb_store.py)
<details><summary>F275: Explicit schema initialization - creates/touches the DB file and</summary>
<div class="doc-comment">
<p>F275: Explicit schema initialization - creates/touches the DB file and</p>
<p>runs CREATE TABLE IF NOT EXISTS for all canonical tables.</p>
<p></p>
<p>Safe to call multiple times (idempotent). Does NOT run full</p>
<p>async_initialize() - no WAL replay, no DedupManager init.</p>
<p>This is the minimal init path for the zero-findings case.</p>
<p></p>
<p>Returns True if schema is ready, False on error.</p>
</div>
</details>
</li>
<li><code>_sync_query_sprint_ioc_summary_impl</code> (duckdb_store.py) — <span class="doc-comment-inline">Sync - MUST be called on worker thread. Internal: actual query without cache.</span></li>
<li><code>async_record_hypothesis_feedback</code> (duckdb_store.py)
<details><summary>Sprint F203G: Record a single hypothesis_feedback entry.</summary>
<div class="doc-comment">
<p>Sprint F203G: Record a single hypothesis_feedback entry.</p>
<p></p>
<p>Thread-safe, non-blocking - runs on duckdb_worker via run_in_executor.</p>
<p>Silently fails if store is closed or uninitialized.</p>
<p></p>
<p>Args:</p>
<p>record: HypothesisFeedbackRecord (frozen dataclass) with fields:</p>
<p>id, target_id, pivot_type, ioc_type, produced_count,</p>
<p>accepted_count, signal_value, ts.</p>
<p></p>
<p>Returns:</p>
<p>True if recorded, False otherwise.</p>
</div>
</details>
</li>
<li><code>_sync_read_envelope</code> (duckdb_store.py)
<details><summary>Sprint F202A §3: Read and deserialize envelope from LMDB WAL entry.</summary>
<div class="doc-comment">
<p>Sprint F202A §3: Read and deserialize envelope from LMDB WAL entry.</p>
<p></p>
<p>Returns None if finding doesn't exist or has no valid envelope.</p>
<p>Fail-soft: does not raise.</p>
</div>
</details>
</li>
<li><code>_flush_writeback</code> (lancedb_store.py) — <span class="doc-comment-inline">Flush writeback buffer to LMDB — single batch transaction.</span></li>
<li><code>_scan_and_evict</code> (lancedb_store.py)</li>
<li><code>_rank_facts_with_novelty</code> (graph_rag.py)
<details><summary>Rank facts considering novelty score.</summary>
<div class="doc-comment">
<p>Rank facts considering novelty score.</p>
<p></p>
<p>Args:</p>
<p>facts: List of facts to rank</p>
<p></p>
<p>Returns:</p>
<p>Ranked list with novelty bonus</p>
</div>
</details>
</li>
<li><code>query</code> (rag_engine.py)
<details><summary>Procesovat RAG query.</summary>
<div class="doc-comment">
<p>Procesovat RAG query.</p>
<p></p>
<p>Args:</p>
<p>query: Uživatelský dotaz</p>
<p>context_chunks: Kontextové chunky</p>
<p>use_compression: Použít kompresi (auto-detect pokud None)</p>
<p>secure: Použít secure enclave</p>
<p></p>
<p>Returns:</p>
<p>Výsledek RAG query</p>
</div>
</details>
</li>
<li><code>hybrid_retrieve_with_hnsw</code> (rag_engine.py)
<details><summary>Retrieve relevant documents using hybrid search (dense + sparse) with optional HNSW.</summary>
<div class="doc-comment">
<p>Retrieve relevant documents using hybrid search (dense + sparse) with optional HNSW.</p>
<p></p>
<p>This is an enhanced version of hybrid_retrieve that uses HNSW for fast</p>
<p>dense retrieval when available.</p>
<p></p>
<p>Args:</p>
<p>query: Search query</p>
<p>documents: List of documents to search (only needed if HNSW not built)</p>
<p>top_k: Number of results to return</p>
<p>filters: Optional metadata filters</p>
<p>use_hnsw: Override HNSW usage (None = use config setting)</p>
<p></p>
<p>Returns:</p>
<p>List of retrieved chunks with scores</p>
</div>
</details>
</li>
<li><code>query_vectors</code> (analyst_workbench.py)
<details><summary>Query LanceDB text index for ANN similar vectors.</summary>
<div class="doc-comment">
<p>Query LanceDB text index for ANN similar vectors.</p>
<p></p>
<p>Args:</p>
<p>query_embedding: 256d numpy array (MRL dimension for text)</p>
<p>k: Number of results (capped to MAX_TOP_K)</p>
<p></p>
<p>Returns:</p>
<p>List of (finding_id, similarity_score) tuples ordered by similarity.</p>
</div>
</details>
</li>
<li><code>upsert_identity_edge</code> (graph_service.py)</li>
<li><code>__init__</code> (entity_linker.py)
<details><summary>Initialize EntityLinker.</summary>
<div class="doc-comment">
<p>Initialize EntityLinker.</p>
<p></p>
<p>Args:</p>
<p>wikidata_endpoint: SPARQL endpoint URL</p>
<p>cache_size: Maximum cache entries</p>
<p>cache_ttl: Cache TTL in seconds</p>
<p>max_candidates: Maximum candidates to fetch per entity</p>
<p>confidence_threshold: Minimum confidence for linking</p>
<p>request_timeout: HTTP request timeout in seconds</p>
<p>use_gliner: Whether to use GLiNER for NER if available</p>
</div>
</details>
</li>
<li><code>canonicalize_entity</code> (entity_linker.py)
<details><summary>Canonicalize entity text to a standard form.</summary>
<div class="doc-comment">
<p>Canonicalize entity text to a standard form.</p>
<p></p>
<p>Args:</p>
<p>entity_text: Original entity text</p>
<p>entity_type: Entity type</p>
<p></p>
<p>Returns:</p>
<p>Canonicalized entity text</p>
</div>
</details>
</li>
<li><code>tune_if_due_async</code> (lancedb_auto_tuner.py)
<details><summary>Async-safe wrapper — runs the synchronous ``tune_if_due`` in executor.</summary>
<div class="doc-comment">
<p>Async-safe wrapper — runs the synchronous ``tune_if_due`` in executor.</p>
<p></p>
<p>P1-2 Enhancement: Passes current_num_sub_vectors through to the</p>
<p>synchronous core so both IVF-PQ knobs are tuned.</p>
<p></p>
<p>Use this from async code paths (e.g. ``LanceDBIdentityStore.add_entity``).</p>
<p>Off-loads the blocking ``to_polars``, ``search``, ``create_index`` calls</p>
<p>to the default executor so the event loop stays responsive.</p>
</div>
</details>
</li>
<li><code>close</code> (ioc_graph.py)
<details><summary>Gracefully close the Kuzu connection.</summary>
<div class="doc-comment">
<p>Gracefully close the Kuzu connection.</p>
<p></p>
<p>Flushes any pending IOC and observation buffers before shutdown</p>
<p>to prevent silent data loss when close() is called without</p>
<p>an intervening WINDUP phase.</p>
<p></p>
<p>close() is idempotent and data-safe: pending buffered writes are</p>
<p>flushed BEFORE _closed is set to True, so no buffered IOC or</p>
<p>observation data is lost on normal shutdown.</p>
</div>
</details>
</li>
<li><code>wal_write_finding</code> (wal.py)
<details><summary>Write a finding to the WAL LMDB (sync, no await).</summary>
<div class="doc-comment">
<p>Write a finding to the WAL LMDB (sync, no await).</p>
<p></p>
<p>LMDB key:   finding:{id}</p>
<p>Value:      serialized dict with id, query, source_type, confidence, ts</p>
<p></p>
<p>Returns True if LMDB write succeeded.</p>
</div>
</details>
</li>
<li><code>num_row_groups</code> (duckdb_store.py) — <span class="doc-comment-inline">Return number of row-groups (metadata only, no data read).</span></li>
<li><code>_iter_rust_filtered</code> (duckdb_store.py) — <span class="doc-comment-inline">Rust-accelerated row-group iteration via IPC bytes with filter.</span></li>
<li><code>_check_pyarrow_available</code> (duckdb_store.py)
<details><summary>Sprint F265C: Cache-aware pyarrow availability check.</summary>
<div class="doc-comment">
<p>Sprint F265C: Cache-aware pyarrow availability check.</p>
<p></p>
<p>Called from tight loops (executor overhead path) so we optimize for the</p>
<p>common case: pyarrow already imported -&gt; O(1) sys.modules lookup, zero I/O.</p>
<p>Only falls back to find_spec when pyarrow is not yet loaded.</p>
<p></p>
<p>Caches result in module-level _PYARROW_AVAILABLE so repeated calls in the</p>
<p>same process are always O(1).</p>
</div>
</details>
</li>
<li><code>for_testing</code> (duckdb_store.py)
<details><summary>Create a DuckDB store for test isolation.</summary>
<div class="doc-comment">
<p>Create a DuckDB store for test isolation.</p>
<p></p>
<p>Not for production use - provides a predictable temp path that is</p>
<p>cleaned up by the caller after the test.</p>
<p></p>
<p>Args:</p>
<p>name:  Identifier used in the temp path (default "test").</p>
<p>Pass unique names per test to avoid collisions.</p>
<p>temp_dir:  Optional temp directory. If None, a temp dir is created</p>
<p>via tempfile.mkdtemp and the caller is responsible for</p>
<p>cleaning it up.</p>
</div>
</details>
</li>
<li><code>_conn</code> (duckdb_store.py)
<details><summary>Return the active write connection (MODE A file or MODE B persistent).</summary>
<div class="doc-comment">
<p>Return the active write connection (MODE A file or MODE B persistent).</p>
<p></p>
<p>F265X-LAZY-FIX: triggers ensure_connected() if connection is not yet</p>
<p>established in lazy mode. In lazy mode, __aenter__ sets _initialized=True</p>
<p>but leaves _file_conn=None and _persistent_conn=None. First actual use</p>
<p>via this property establishes the connection on-demand.</p>
<p></p>
<p>P2-22 FIX: Removed redundant _prewarm_file_conn() call from hot path.</p>
<p>The prewarm SELECT 1 was being issued on EVERY _conn() call in the</p>
<p>hot ingest loop (~millions of times), adding ~0.1-0.3ms per call</p>
<p>overhead for no benefit after the first prewarm. Prewarm is now</p>
<p>called exactly once after initial connection in _init_connection().</p>
</div>
</details>
</li>
<li><code>async_load_cooccurrence</code> (duckdb_store.py)
<details><summary>Load IOC co-occurrence pairs from DuckDB.</summary>
<div class="doc-comment">
<p>Load IOC co-occurrence pairs from DuckDB.</p>
<p></p>
<p>Replaces raw sqlite3 SELECT in IOCooccurrenceMiner._load_sync().</p>
<p></p>
<p>Args:</p>
<p>limit: Max pairs to load (default 100_000).</p>
<p></p>
<p>Returns:</p>
<p>List of dicts with keys:</p>
<p>ioc_a, ioc_b, ioc_type_a, ioc_type_b,</p>
<p>support, confidence, score, last_seen</p>
</div>
</details>
</li>
<li><code>async_query_findings_by_text</code> (duckdb_store.py)
<details><summary>F251A: Read canonical_findings rows matching a text/keyword pattern.</summary>
<div class="doc-comment">
<p>F251A: Read canonical_findings rows matching a text/keyword pattern.</p>
<p>Used by run_runtime_pivot_prelude() for offline memory seed extraction</p>
<p>when a text query has no direct IOC seeds.</p>
<p></p>
<p>Args:</p>
<p>like_pattern: Keyword to search in query/title/payload_text.</p>
<p>limit: Max rows to return (default 1000).</p>
<p></p>
<p>Returns:</p>
<p>list[dict] with keys: id, query, source_type, title, payload_text, ts.</p>
<p>Fail-soft: returns [] on any error.</p>
</div>
</details>
</li>
<li><code>async_upsert_target_memory</code> (duckdb_store.py)
<details><summary>Sprint F204D: Insert or update target memory from a TargetMemory.</summary>
<div class="doc-comment">
<p>Sprint F204D: Insert or update target memory from a TargetMemory.</p>
<p></p>
<p>Thread-safe, non-blocking - runs on duckdb_worker via run_in_executor.</p>
<p>Silently fails if store is closed or uninitialized.</p>
<p></p>
<p>F206H FIX: Previously accepted TargetMemoryUpdate and silently failed</p>
<p>(type mismatch with _sync_upsert_target_memory which expects TargetMemory).</p>
<p>Now accepts TargetMemory directly - caller (SprintScheduler) passes</p>
<p>the already-merged memory from TargetMemoryService.merge_update().</p>
</div>
</details>
</li>
<li><code>_sync_rrf_rank</code> (duckdb_store.py) — <span class="doc-comment-inline">ISSUE-008 P1: Uses read pool for parallel analytical queries.</span></li>
<li><code>_mmr</code> (lancedb_store.py) — <span class="doc-comment-inline">Maximal Marginal Relevance - reduce duplicates in results.</span></li>
<li><code>search_similar</code> (lancedb_store.py)
<details><summary>ANN search for similar entities. API-compatible with LanceDBIdentityStore.</summary>
<div class="doc-comment">
<p>ANN search for similar entities. API-compatible with LanceDBIdentityStore.</p>
<p></p>
<p>Args:</p>
<p>embedding: Query embedding vector.</p>
<p>text_hint: Optional text for FTS (ignored — sqlite-vec has no FTS).</p>
<p>threshold: Similarity threshold (0-1). Applied as 1 - vec0_distance.</p>
<p>limit: Maximum results.</p>
<p>query_type: "auto"/"vector"/"fts"/"hybrid" (fts/hybrid fall back to vector).</p>
<p></p>
<p>Returns:</p>
<p>List of matching entities with id, aliases, similarity, first_seen, last_seen.</p>
</div>
</details>
</li>
<li><code>search</code> (rag_engine.py) — <span class="doc-comment-inline">Search documents using BM25</span></li>
<li><code>_generate_embeddings</code> (rag_engine.py)
<details><summary>Generate embeddings using UnifiedEmbeddingManager (MLX primary).</summary>
<div class="doc-comment">
<p>Generate embeddings using UnifiedEmbeddingManager (MLX primary).</p>
<p></p>
<p>Priority: MLXEmbeddingManager (ModernBERT) → SHA256 hash fallback.</p>
<p>FastEmbed removed — unified MLX is faster on M1 8GB.</p>
<p></p>
<p>M1 8GB: MLXEmbeddingManager runs on GPU via unified memory, no CPU transfer.</p>
</div>
</details>
</li>
<li><code>query_semantic</code> (analyst_workbench.py)
<details><summary>Query SemanticStore (FastEmbed) for finding_ids by keyword.</summary>
<div class="doc-comment">
<p>Query SemanticStore (FastEmbed) for finding_ids by keyword.</p>
<p></p>
<p>Args:</p>
<p>query: Search query</p>
<p>limit: Max results (capped to MAX_TOP_K)</p>
<p></p>
<p>Returns:</p>
<p>List of finding_ids ordered by semantic relevance.</p>
</div>
</details>
</li>
<li><code>get_evidence_chain</code> (analyst_workbench.py)
<details><summary>F203D: Retrieve the evidence chain for a given finding_id.</summary>
<div class="doc-comment">
<p>F203D: Retrieve the evidence chain for a given finding_id.</p>
<p></p>
<p>Chains are accumulated by the EvidenceChainBuilder during sprint teardown</p>
<p>and stored as a sprint artifact. This method queries the module-level</p>
<p>registry for the chain.</p>
<p></p>
<p>Args:</p>
<p>finding_id: The finding ID to look up.</p>
<p></p>
<p>Returns:</p>
<p>EvidenceChain if found, None otherwise.</p>
<p>Returns None if no sprint has been run yet or if the finding_id</p>
<p>is not part of any tracked chain.</p>
</div>
</details>
</li>
<li><code>_pattern_completion</code> (neuromorphic.py) — <span class="doc-comment-inline">Auto-associative pattern completion using synaptic weights.</span></li>
<li><code>consolidate_memories</code> (neuromorphic.py)
<details><summary>Consolidate strong working memories to long-term memory.</summary>
<div class="doc-comment">
<p>Consolidate strong working memories to long-term memory.</p>
<p></p>
<p>Args:</p>
<p>strength_threshold: Minimum strength to consolidate</p>
<p></p>
<p>Returns:</p>
<p>Number of patterns consolidated</p>
</div>
</details>
</li>
<li><code>_memory_replay</code> (neuromorphic.py)
<details><summary>Strengthen memories through replay (sleep-like consolidation).</summary>
<div class="doc-comment">
<p>Strengthen memories through replay (sleep-like consolidation).</p>
<p></p>
<p>Args:</p>
<p>n_replays: Number of memory replays</p>
</div>
</details>
</li>
<li><code>record_step</code> (evidence_chain.py)
<details><summary>Record a processing step into the chain for root_finding_id.</summary>
<div class="doc-comment">
<p>Record a processing step into the chain for root_finding_id.</p>
<p></p>
<p>If no chain exists for root_finding_id, one is created with the root</p>
<p>as the first (ingest) step. Subsequent calls add derivative steps.</p>
<p></p>
<p>Silently drops steps once MAX_CHAIN_DEPTH or MAX_CHAINS_PER_SPRINT is reached.</p>
</div>
</details>
</li>
<li><code>_duckdb_at_exit_shutdown</code> (duckdb_store.py)
<details><summary>Called by weakref.finalize at interpreter exit if explicit aclose() was not called.</summary>
<div class="doc-comment">
<p>Called by weakref.finalize at interpreter exit if explicit aclose() was not called.</p>
<p></p>
<p>DuckDBShadowStore keeps _shared_executor alive per Sprint 8L contract</p>
<p>(for re-init safety after aclose()), but we add finalizer to ensure</p>
<p>atexit cleanup if aclose() was never called.</p>
<p></p>
<p>This is synchronous (runs in main thread at shutdown):</p>
<p>1. Signal worker thread to stop via _executor.shutdown()</p>
<p>2. Best-effort — DuckDB connections are complex to clean up safely</p>
<p></p>
<p>Issue #40 fix: cancel_futures=True ensures that any pending async tasks</p>
<p>(graph ingest, semantic buffering) are cancelled immediately at interpreter</p>
<p>exit rather than blocking shutdown. Data in-flight at shutdown time is</p>
<p>best-effort — explicit aclose() should be called for guaranteed flush.</p>
</div>
</details>
</li>
<li><code>_sync_get_entity_observations_by_entity</code> (duckdb_store.py) — <span class="doc-comment-inline">Sprint F350M: Fetch entity_observations by entity_value.</span></li>
<li><code>__aenter__</code> (duckdb_store.py)
<details><summary>Async context manager entry - initializes the store.</summary>
<div class="doc-comment">
<p>Async context manager entry - initializes the store.</p>
<p></p>
<p>Usage:</p>
<p>async with DuckDBShadowStore() as store:</p>
<p>await store.async_insert_finding(...)</p>
<p># aclose() called automatically on exit</p>
<p></p>
<p>Sprint DuckDB Lazy Init (F265X): when lazy=True (default), this returns</p>
<p>immediately without connecting. Connection is deferred to the first actual</p>
<p>query via ensure_connected(). This saves ~1-2s from sprint boot.</p>
</div>
</details>
</li>
<li><code>submit_findings</code> (duckdb_store.py)
<details><summary>Fire-and-forget async write — delegates directly to async_ingest_findings_batch().</summary>
<div class="doc-comment">
<p>Fire-and-forget async write — delegates directly to async_ingest_findings_batch().</p>
<p></p>
<p>async_ingest_findings_batch() has its own built-in Arrow pipeline batching</p>
<p>(1024-item chunks, 4-slot pipeline queue, concurrent WAL+DuckDB via asyncio.gather),</p>
<p>so no separate coalescer layer is needed.</p>
<p></p>
<p>NOTE: findings list must not be mutated after this call returns.</p>
<p>Caller is responsible for ensuring this.</p>
<p></p>
<p>Returns: None (fire-and-forget async write).</p>
</div>
</details>
</li>
<li><code>_sync_query_source_mix_trend</code> (duckdb_store.py) — <span class="doc-comment-inline">Sync - MUST be called on the worker thread. Uses read pool for parallelism.</span></li>
<li><code>_wal_write_finding</code> (duckdb_store.py)
<details><summary>Sprint 8A: Write a single finding to LMDB WAL (sync, no await).</summary>
<div class="doc-comment">
<p>Sprint 8A: Write a single finding to LMDB WAL (sync, no await).</p>
<p></p>
<p>LMDB key format:  finding:{id}</p>
<p>Value: serialized dict with id, query, source_type, confidence, ts</p>
<p></p>
<p>Returns True if LMDB write succeeded.</p>
<p></p>
<p>Delegation: Sprint F233A micro-cleanup - routes through WALManager</p>
<p>to eliminate the residual direct LMDB WAL path.</p>
</div>
</details>
</li>
<li><code>_resolve_lancedb_cache_size</code> (lancedb_store.py) — <span class="doc-comment-inline">Resolve LMDB map_size from env with M1-safe defaults.</span></li>
<li><code>__init__</code> (lancedb_store.py)
<details><summary>Initialize LanceDB identity store.</summary>
<div class="doc-comment">
<p>Initialize LanceDB identity store.</p>
<p></p>
<p>Args:</p>
<p>uri: Path to LanceDB database.</p>
<p>orchestrator: Optional orchestrator reference for memory context.</p>
</div>
</details>
</li>
<li><code>ensure_index</code> (lancedb_store.py) — <span class="doc-comment-inline">Create index with respect to available RAM and thermal state.</span></li>
<li><code>_pack_query_to_binary</code> (lancedb_store.py)
<details><summary>Pack float32 query vector to packed binary bytes.</summary>
<div class="doc-comment">
<p>Pack float32 query vector to packed binary bytes.</p>
<p></p>
<p>Matches the big-endian packing used in _load_embeddings_to_mlx:</p>
<p>signs = (emb &gt; 0).astype(uint8)   -- 1 if &gt;= 0, 0 if &lt; 0</p>
<p>packed[i] = bits[i*8]&lt;&lt;7 | bits[i*8+1]&lt;&lt;6 | ... | bits[i*8+7]&lt;&lt;0</p>
<p></p>
<p>Returns:</p>
<p>Packed bytes (num_bytes = (dim + 7) // 8).</p>
</div>
</details>
</li>
<li><code>initialize</code> (lancedb_store.py) — <span class="doc-comment-inline">Initialize table and embedder.</span></li>
<li><code>_get_embedder</code> (graph_rag.py)
<details><summary>Get shared MLXEmbeddingManager singleton (memory-convergent).</summary>
<div class="doc-comment">
<p>Get shared MLXEmbeddingManager singleton (memory-convergent).</p>
<p></p>
<p>M1 8GB: graph_rag NENÍ embedder owner. Používá sdílený</p>
<p>MLXEmbeddingManager singleton z core/mlx_embeddings.py.</p>
<p>Žádné duplikátní RAGEngine() vytváření.</p>
</div>
</details>
</li>
<li><code>_rank_facts</code> (graph_rag.py)
<details><summary>Rank facts by relevance (similarity, hop distance, type).</summary>
<div class="doc-comment">
<p>Rank facts by relevance (similarity, hop distance, type).</p>
<p></p>
<p>Args:</p>
<p>facts: List of facts to rank</p>
<p></p>
<p>Returns:</p>
<p>Ranked list of facts</p>
</div>
</details>
</li>
<li><code>_raptor_retrieve</code> (rag_engine.py) — <span class="doc-comment-inline">Retrieve top-K nodes from all RAPTOR levels by cosine similarity.</span></li>
<li><code>_rrf_merge</code> (rag_engine.py) — <span class="doc-comment-inline">Merge two ranked lists via Reciprocal Rank Fusion. Stable key = hash of content.</span></li>
<li><code>_get_duckdb_ro</code> (hot_edges_cache.py)
<details><summary>Return a reusable read-only DuckDB connection.</summary>
<div class="doc-comment">
<p>Return a reusable read-only DuckDB connection.</p>
<p></p>
<p>P0-2 fix: Previously every lookup_ioc_values_by_ids() call opened a new</p>
<p>connection (5-10ms overhead). Now we reuse a single read-only connection</p>
<p>for the lifetime of the process.</p>
<p></p>
<p>Thread-safety: DuckDB read-only connections are safe for concurrent reads</p>
<p>from multiple threads (MVCC). The connection is opened lazily on first use.</p>
</div>
</details>
</li>
<li><code>_extract_url_from_provenance</code> (quality_assessment.py)
<details><summary>Extract the first HTTP(S) URL from a provenance tuple.</summary>
<div class="doc-comment">
<p>Extract the first HTTP(S) URL from a provenance tuple.</p>
<p></p>
<p>Handles two formats:</p>
<p>- Raw URL: "https://example.com"</p>
<p>- Tagged URL: "url:https://example.com" (PUBLIC lane format from _build_public_finding)</p>
</div>
</details>
</li>
<li><code>__init__</code> (quality_assessment.py)</li>
<li><code>add</code> (dedup.py)
<details><summary>Add item hash to active filter. Rotate if active is full.</summary>
<div class="doc-comment">
<p>Add item hash to active filter. Rotate if active is full.</p>
<p></p>
<p>Args:</p>
<p>item: URL or fingerprint string to add.</p>
</div>
</details>
</li>
<li><code>prewarm</code> (ann_index.py)
<details><summary>Pre-warm the ANN index for faster first-query latency.</summary>
<div class="doc-comment">
<p>Pre-warm the ANN index for faster first-query latency.</p>
<p></p>
<p>Ensures USEARCH index is loaded and pre-warms Metal memory.</p>
</div>
</details>
</li>
<li><code>upsert_ioc_batch</code> (ioc_graph.py)
<details><summary>Batch upsert of IOC nodes.</summary>
<div class="doc-comment">
<p>Batch upsert of IOC nodes.</p>
<p></p>
<p>Args:</p>
<p>iocs: list of (ioc_type, value, confidence) tuples.</p>
<p>Returns:</p>
<p>List of node IDs newly created in this batch.</p>
<p>Duplicate calls with the same inputs return [] on subsequent calls.</p>
</div>
</details>
</li>
<li><code>close</code> (wal.py) — <span class="doc-comment-inline">Close the WAL LMDB and release the lock file.</span></li>
<li><code>_stdp_update</code> (neuromorphic.py)
<details><summary>Apply STDP update to synaptic weight.</summary>
<div class="doc-comment">
<p>Apply STDP update to synaptic weight.</p>
<p></p>
<p>Args:</p>
<p>pre_idx: Pre-synaptic neuron index (unused in simplified model)</p>
<p>post_idx: Post-synaptic neuron index (unused in simplified model)</p>
<p>delta_t: Time difference (pre - post)</p>
<p></p>
<p>Returns:</p>
<p>Weight change value</p>
</div>
</details>
</li>
<li><code>_update_weights_from_pattern</code> (neuromorphic.py) — <span class="doc-comment-inline">Update synaptic weights based on neuron activations.</span></li>
<li><code>recall_pattern</code> (neuromorphic.py)
<details><summary>Recall a pattern from memory.</summary>
<div class="doc-comment">
<p>Recall a pattern from memory.</p>
<p></p>
<p>Args:</p>
<p>pattern_id: Pattern to recall</p>
<p>completion: Whether to perform pattern completion</p>
<p></p>
<p>Returns:</p>
<p>Recalled pattern or None</p>
</div>
</details>
</li>
<li><code>add</code> (ioc_dedup_adapter.py)
<details><summary>Add IOC to dedup store. Returns True if NEW (not duplicate), False if duplicate.</summary>
<div class="doc-comment">
<p>Add IOC to dedup store. Returns True if NEW (not duplicate), False if duplicate.</p>
<p></p>
<p>Args:</p>
<p>value: IOC value (domain, URL, IP, hash, CVE, email)</p>
<p>ioc_type: IOC type string (domain, url, ip, md5, sha1, sha256, cve, email, etc.)</p>
<p>confidence: Confidence score [0.0, 1.0], used for confidence_max tracking</p>
<p></p>
<p>Returns:</p>
<p>True if this is a NEW IOC (accepted), False if duplicate</p>
</div>
</details>
</li>
<li><code>_sync_query_sprint_source_stats</code> (duckdb_store.py)
<details><summary>Sprint 8RC: Query source_type hit-rate stats for weight loading.</summary>
<div class="doc-comment">
<p>Sprint 8RC: Query source_type hit-rate stats for weight loading.</p>
<p>Returns avg_hit_rate per source_type over the last 5 days.</p>
<p>MUST be called on the worker thread.</p>
</div>
</details>
</li>
<li><code>async_query_recent_findings</code> (duckdb_store.py)
<details><summary>Query recent findings ordered by timestamp descending.</summary>
<div class="doc-comment">
<p>Query recent findings ordered by timestamp descending.</p>
<p></p>
<p>Thread-safe, non-blocking - runs on duckdb_worker via run_in_executor.</p>
</div>
</details>
</li>
<li><code>drain_and_get_accepted</code> (duckdb_store.py)
<details><summary>Direct ingest — calls async_ingest_findings_batch() and returns results.</summary>
<div class="doc-comment">
<p>Direct ingest — calls async_ingest_findings_batch() and returns results.</p>
<p></p>
<p>This is the canonical write path for call sites that need the</p>
<p>accepted/stored counts from async_ingest_findings_batch().</p>
<p></p>
<p>Args:</p>
<p>findings: findings to ingest.</p>
<p></p>
<p>Returns:</p>
<p>List of FindingQualityDecision/ActivationResult objects,</p>
<p>one per finding submitted. Empty list on failure.</p>
</div>
</details>
</li>
<li><code>recall_episodes</code> (duckdb_store.py) — <span class="doc-comment-inline">Sprint 8UC B.2: Načíst posledních `limit` epizod (recency-based).</span></li>
<li><code>get_identity_store</code> (lancedb_store.py)
<details><summary>Get or create the singleton identity store (async-safe).</summary>
<div class="doc-comment">
<p>Get or create the singleton identity store (async-safe).</p>
<p></p>
<p>Phase 11.2: Primary is now SqliteVecIdentityStore (zero-process, M1-native).</p>
<p>LanceDBIdentityStore is kept as a wired-but-dormant fallback for cases</p>
<p>where sqlite-vec is unavailable (e.g., CI without sqlite-vec extension).</p>
<p></p>
<p>To force LanceDB explicitly:</p>
<p>store = LanceDBIdentityStore()</p>
</div>
</details>
</li>
<li><code>__init__</code> (lancedb_store.py)
<details><summary>Args:</summary>
<div class="doc-comment">
<p>Args:</p>
<p>db_path: Path to LanceDB database. If None, uses default.</p>
<p>dim: Embedding dimension (default 384 for FastEmbed BAAI).</p>
</div>
</details>
</li>
<li><code>_label_propagation</code> (graph_rag.py) — <span class="doc-comment-inline">Simple label propagation for community detection.</span></li>
<li><code>get_target_memory_summary</code> (analyst_workbench.py)
<details><summary>F204D: Get target memory summary for a target.</summary>
<div class="doc-comment">
<p>F204D: Get target memory summary for a target.</p>
<p></p>
<p>Returns dict with keys: target_id, sprint_count, cumulative_finding_count,</p>
<p>entity_facets, exposure_facets, pivot_facets, confidence_drift,</p>
<p>updated_by_sprint_id or None if not found.</p>
<p></p>
<p>Thread-safe: runs on duckdb_worker via run_in_executor.</p>
<p>Fail-soft: returns None on any error.</p>
</div>
</details>
</li>
<li><code>ainitialize</code> (dedup.py)
<details><summary>Async version of initialize() — runs all sync I/O in thread pool.</summary>
<div class="doc-comment">
<p>Async version of initialize() — runs all sync I/O in thread pool.</p>
<p></p>
<p>F268: Prevents event-loop blocking during DedupManager init.</p>
<p>All 4 init methods do file I/O (LMDB open, mmap files).</p>
<p>Running them in thread pool keeps event loop responsive.</p>
</div>
</details>
</li>
<li><code>_init_persistent_dedup_lmdb</code> (dedup.py)
<details><summary>Initialize persistent dedup LMDB.</summary>
<div class="doc-comment">
<p>Initialize persistent dedup LMDB.</p>
<p></p>
<p>Fails softly: any exception is caught and stored in _dedup_lmdb_boot_error.</p>
</div>
</details>
</li>
<li><code>_extract_entities_fallback</code> (entity_linker.py)
<details><summary>Extract entities using regex patterns (fallback when GLiNER unavailable).</summary>
<div class="doc-comment">
<p>Extract entities using regex patterns (fallback when GLiNER unavailable).</p>
<p></p>
<p>Returns:</p>
<p>List of (entity_text, start, end, entity_type) tuples</p>
</div>
</details>
</li>
<li><code>read_table</code> (duckdb_store.py)
<details><summary>Read entire parquet file as a single Arrow Table.</summary>
<div class="doc-comment">
<p>Read entire parquet file as a single Arrow Table.</p>
<p>WARNING: may OOM for 100GB+ files — prefer iter_batches().</p>
<p></p>
<p>Returns:</p>
<p>pyarrow.Table or None on error.</p>
</div>
</details>
</li>
<li><code>_sync_insert_source_hit</code> (duckdb_store.py)</li>
<li><code>async_record_shadow_run</code> (duckdb_store.py)</li>
<li><code>async_record_shadow_finding</code> (duckdb_store.py)</li>
<li><code>_execute_in_thread_sync</code> (duckdb_store.py)
<details><summary>Execute synchronous function on the duckdb executor and return its result.</summary>
<div class="doc-comment">
<p>Execute synchronous function on the duckdb executor and return its result.</p>
<p></p>
<p>MUST be called from the main thread. The callable fn runs on the</p>
<p>single-worker ThreadPoolExecutor and blocks until complete.</p>
<p></p>
<p>Returns:</p>
<p>The return value of fn(), or None if the executor raised an exception.</p>
<p></p>
<p>NOTE: This is a synchronous helper. Async callers MUST await the result:</p>
<p>result = await loop.run_in_executor(self._executor, self._execute_in_thread_sync, fn)</p>
<p>For direct async wrappers, prefer loop.run_in_executor() directly.</p>
</div>
</details>
</li>
<li><code>_sync</code> (duckdb_store.py)</li>
<li><code>_sync_query_scorecard_trend</code> (duckdb_store.py)
<details><summary>Sync - MUST be called on the worker thread.</summary>
<div class="doc-comment">
<p>Sync - MUST be called on the worker thread.</p>
<p></p>
<p>F320-6.6: Polars LazyFrame for analytics queries.</p>
<p>Uses duckdb_fetch_polars() zero-copy path (DuckDB 1.5+ Arrow C Data Interface).</p>
<p>Streaming collection for bounded memory on large result sets.</p>
</div>
</details>
</li>
<li><code>get_sprint_delta_comparison</code> (duckdb_store.py)
<details><summary>Sprint F150H: Compare current sprint against the average of the last</summary>
<div class="doc-comment">
<p>Sprint F150H: Compare current sprint against the average of the last</p>
<p>`lookback` sprints. Returns a delta dict with absolute values of</p>
<p>current sprint and the delta vs the rolling mean of prior sprints.</p>
<p></p>
<p>Covers: new_findings, ioc_new_this_sprint, dedup_hits, findings_per_minute,</p>
<p>uma_peak_gib, synthesis_confidence.</p>
<p></p>
<p>Use for: "how is this sprint tracking vs history" without ad-hoc SQL.</p>
<p>Fail-soft - returns empty/near-zero fields on any error.</p>
</div>
</details>
</li>
<li><code>get_scorecard_consistency_check</code> (duckdb_store.py)
<details><summary>Sprint F150I: Compare findings_per_minute from sprint_scorecard vs</summary>
<div class="doc-comment">
<p>Sprint F150I: Compare findings_per_minute from sprint_scorecard vs</p>
<p>findings_per_minute from sprint_delta for the same sprint.</p>
<p>Returns ratio and warns if divergence &gt; 2x.</p>
<p></p>
<p>Use for: detecting scorecard / delta sync issues.</p>
<p>Fail-soft - returns empty dict on any error.</p>
<p></p>
<p>NOTE: As of Sprint F192F, both tables use findings_per_minute (renamed from</p>
<p>findings_per_min in sprint_delta). The JOIN now compares two same-named columns.</p>
</div>
</details>
</li>
<li><code>_sync_get</code> (duckdb_store.py)</li>
<li><code>aclose</code> (duckdb_store.py)
<details><summary>Async idempotent shutdown - canonical async cleanup path.</summary>
<div class="doc-comment">
<p>Async idempotent shutdown - canonical async cleanup path.</p>
<p></p>
<p>Delegates to _do_sync_close(emergency=False) for shared synchronous cleanup,</p>
<p>then performs async-only operations (bg task cancellation).</p>
<p></p>
<p>Idempotent: safe to call multiple times.</p>
</div>
</details>
</li>
<li><code>_get_and_bump_retry_count</code> (duckdb_store.py)
<details><summary>Sprint 8H: Get current retry count from marker metadata and bump it.</summary>
<div class="doc-comment">
<p>Sprint 8H: Get current retry count from marker metadata and bump it.</p>
<p></p>
<p>Stores retry count in the marker value under "_retry_count" key.</p>
<p>Returns the new retry count after bump.</p>
</div>
</details>
</li>
<li><code>_predict_memory_pressure</code> (lancedb_store.py) — <span class="doc-comment-inline">Predict memory pressure using LMDB stats.</span></li>
<li><code>_run_async_safe</code> (graph_rag.py)
<details><summary>Safely run an async coroutine synchronously.</summary>
<div class="doc-comment">
<p>Safely run an async coroutine synchronously.</p>
<p></p>
<p>Delegates to run_sync_async() which uses asyncio.Runner (Python 3.11+)</p>
<p>for the no-loop case. For worker threads with a running loop,</p>
<p>run_until_complete is safe to use directly.</p>
</div>
</details>
</li>
<li><code>_deduplicate_facts</code> (graph_rag.py)
<details><summary>Remove duplicate facts based on content.</summary>
<div class="doc-comment">
<p>Remove duplicate facts based on content.</p>
<p></p>
<p>Args:</p>
<p>facts: List of facts to deduplicate</p>
<p></p>
<p>Returns:</p>
<p>Deduplicated list of facts</p>
</div>
</details>
</li>
<li><code>_calculate_clustering_coefficient</code> (graph_rag.py) — <span class="doc-comment-inline">Calculate average clustering coefficient.</span></li>
<li><code>_extract_claim</code> (graph_rag.py)
<details><summary>Extract (subject, predicate, object) claim from content.</summary>
<div class="doc-comment">
<p>Extract (subject, predicate, object) claim from content.</p>
<p></p>
<p>Args:</p>
<p>content: Text content to parse</p>
<p></p>
<p>Returns:</p>
<p>Tuple of (subject, predicate, object) or None</p>
</div>
</details>
</li>
<li><code>reset_hot_cache</code> (quality_assessment.py)
<details><summary>Sprint F259B: Clear in-memory dedup hot cache + fingerprint set per-sprint.</summary>
<div class="doc-comment">
<p>Sprint F259B: Clear in-memory dedup hot cache + fingerprint set per-sprint.</p>
<p></p>
<p>Bounded: both dicts are bounded (_DEDUP_HOT_CACHE_MAX) so clear is O(1) amortized.</p>
<p>Fail-soft: any exception is swallowed — caller is the per-sprint reset path</p>
<p>and must never crash the scheduler.</p>
</div>
</details>
</li>
<li><code>__init__</code> (dedup.py)
<details><summary>Args:</summary>
<div class="doc-comment">
<p>Args:</p>
<p>capacity: Max items per generation before rotation.</p>
<p>fp_rate: Target false positive rate.</p>
<p>lmdb_path: Ignored (kept for API compat). Persistence via mmap files.</p>
</div>
</details>
</li>
<li><code>pivot</code> (ioc_graph.py)
<details><summary>Find IOC nodes connected to the given IOC up to *depth* hops.</summary>
<div class="doc-comment">
<p>Find IOC nodes connected to the given IOC up to *depth* hops.</p>
<p></p>
<p>Kuzu: MATCH (n:IOC)-[r*1..2]-(m:IOC)</p>
<p>WHERE n.value=$v AND n.ioc_type=$t RETURN m, r</p>
<p></p>
<p>Returns list of dicts: id, ioc_type, value, confidence, first_seen, last_seen.</p>
</div>
</details>
</li>
<li><code>_graph_stats_sync</code> (ioc_graph.py) — <span class="doc-comment-inline">Synchronous stats — runs on _executor thread.</span></li>
<li><code>_get_rust_pool</code> (db.py) — <span class="doc-comment-inline">Lazy Rust StdConnectionPool for DuckDB async queries.</span></li>
<li><code>init_temporal_schema</code> (db.py) — <span class="doc-comment-inline">Initialize temporal signals table in DuckDB.</span></li>
<li><code>_iter_pyarrow_filtered</code> (duckdb_store.py) — <span class="doc-comment-inline">Pure PyArrow fallback with filter.</span></li>
<li><code>__init__</code> (duckdb_store.py)</li>
<li><code>_l2_get</code> (duckdb_store.py)</li>
<li><code>async_execute_raw_sql</code> (duckdb_store.py)
<details><summary>Execute raw SQL query asynchronously (non-blocking).</summary>
<div class="doc-comment">
<p>Execute raw SQL query asynchronously (non-blocking).</p>
<p></p>
<p>Thread-safe, non-blocking - runs on duckdb_worker via run_in_executor.</p>
<p>Use this instead of direct _conn.cursor().execute() in async contexts.</p>
<p></p>
<p>Args:</p>
<p>sql: Raw SQL query string</p>
<p></p>
<p>Returns:</p>
<p>List of row tuples from fetchall()</p>
</div>
</details>
</li>
<li><code>async_query_recent_findings_by_sprint</code> (duckdb_store.py)
<details><summary>Return the most recent accepted findings for a given sprint,</summary>
<div class="doc-comment">
<p>Return the most recent accepted findings for a given sprint,</p>
<p>ordered by ts DESC. Bounded, read-only, fail-soft.</p>
<p></p>
<p>Use for: export synthesis input, sprint retrospektivu,</p>
<p>scheduler priority scoring.</p>
</div>
</details>
</li>
<li><code>upsert_global_entities</code> (duckdb_store.py)
<details><summary>Sprint F4.1 fix: Upsert entities into ghost_global store using DuckDB.</summary>
<div class="doc-comment">
<p>Sprint F4.1 fix: Upsert entities into ghost_global store using DuckDB.</p>
<p></p>
<p>Path: ~/.hledac/ghost_global.duckdb (DuckDB with native WAL mode)</p>
<p>Engine: DuckDB with access_mode='automatic' (native file locking)</p>
<p>Schema: global_entities(entity_value TEXT PK, entity_type TEXT,</p>
<p>sprint_count INT, last_seen DOUBLE, confidence_cumulative REAL)</p>
<p>INSERT OR REPLACE with MAX(confidence) semantics.</p>
<p>Returns: int (count of upserted entities).</p>
</div>
</details>
</li>
<li><code>async_get_findings_with_envelope</code> (duckdb_store.py)
<details><summary>Sprint F202A §3: Read recent findings with deserialized envelopes.</summary>
<div class="doc-comment">
<p>Sprint F202A §3: Read recent findings with deserialized envelopes.</p>
<p></p>
<p>Returns list of dicts with envelope fields attached:</p>
<p>{finding_id, query, source_type, confidence, ts, provenance,</p>
<p>payload_text, envelope: FindingEnvelope | None}</p>
<p>Fail-soft: any finding without valid envelope has envelope=None.</p>
</div>
</details>
</li>
<li><code>_init_persistent_dedup_lmdb</code> (duckdb_store.py) — <span class="doc-comment-inline">Deprecated: initialization moved to DedupManager.initialize().</span></li>
<li><code>_cosine_sim_batch</code> (lancedb_store.py)
<details><summary>MLX-compiled cosine similarity for batch processing.</summary>
<div class="doc-comment">
<p>MLX-compiled cosine similarity for batch processing.</p>
<p></p>
<p>Args:</p>
<p>a: Query embeddings (B, D) or (D,) — auto-handles singleton query</p>
<p>b: Candidate embeddings (N, D)</p>
<p></p>
<p>Returns:</p>
<p>Similarity scores (B, N) — squeeze to (N,) if B=1</p>
</div>
</details>
</li>
<li><code>_get_flashrank_ranker</code> (lancedb_store.py)
<details><summary>Lazy load FlashRank for retrieval path.</summary>
<div class="doc-comment">
<p>Lazy load FlashRank for retrieval path.</p>
<p></p>
<p>Canonical owner: tools/reranker.py</p>
<p>This is a compatibility wrapper serving the retrieval context only.</p>
<p>Uses ms-marco-MiniLM-L-12-v2 model (same as canonical).</p>
</div>
</details>
</li>
<li><code>upsert_papers</code> (lancedb_store.py)
<details><summary>Batch upsert academic papers.</summary>
<div class="doc-comment">
<p>Batch upsert academic papers.</p>
<p></p>
<p>Args:</p>
<p>papers: List of AcademicPaper instances.</p>
</div>
</details>
</li>
<li><code>_search</code> (lancedb_store.py)</li>
<li><code>_summarize_narrative</code> (graph_rag.py) — <span class="doc-comment-inline">Generate 1-3 sentence summary of narrative.</span></li>
<li><code>__init__</code> (analyst_workbench.py)
<details><summary>Initialize AnalystWorkbench with optional store references.</summary>
<div class="doc-comment">
<p>Initialize AnalystWorkbench with optional store references.</p>
<p></p>
<p>All stores are optional — workbench operates with whatever is available.</p>
<p>If a store is None, its queries return empty results (fail-soft).</p>
<p></p>
<p>Args:</p>
<p>duckdb_store: DuckDBShadowStore instance for findings</p>
<p>graph_service: DuckPGQGraph-backed service for entity history</p>
<p>vector_store: LanceDB VectorStore for text ANN</p>
<p>semantic_store: FastEmbed SemanticStore for keyword search</p>
</div>
</details>
</li>
<li><code>_make_decision</code> (quality_assessment.py)</li>
<li><code>upsert_ioc</code> (ioc_graph.py)
<details><summary>Idempotent upsert of an IOC node.</summary>
<div class="doc-comment">
<p>Idempotent upsert of an IOC node.</p>
<p></p>
<p>Uses MATCH→CREATE/SET pattern (Kuzu has no MERGE).</p>
<p>Returns the IOC id or None on failure.</p>
</div>
</details>
</li>
<li><code>graph_supports_buffered_writes</code> (graph_attachment.py)
<details><summary>NON-AUTHORITATIVE COMPAT CHECK: does attached graph support ACTIVE-phase</summary>
<div class="doc-comment">
<p>NON-AUTHORITATIVE COMPAT CHECK: does attached graph support ACTIVE-phase</p>
<p>buffered writes?</p>
<p></p>
<p>Returns True only if attached graph has both:</p>
<p>- buffer_ioc()</p>
<p>- flush_buffers()</p>
<p></p>
<p>IOCGraph (Kuzu): True — has full buffered write capability.</p>
<p>DuckPGQGraph (DuckDB): False — has checkpoint() and add_ioc() only.</p>
<p></p>
<p>Always check this before triggering background graph ingest,</p>
<p>do not assume all injected graphs support buffered writes.</p>
</div>
</details>
</li>
<li><code>inject_truth_write_graph</code> (graph_attachment.py)
<details><summary>Sprint 8WA: Inject dedicated truth-write graph for ACTIVE buffered writes.</summary>
<div class="doc-comment">
<p>Sprint 8WA: Inject dedicated truth-write graph for ACTIVE buffered writes.</p>
<p></p>
<p>F320 UPDATE: Both DuckPGQGraph and IOCGraph are now accepted.</p>
<p>DuckPGQGraph has native buffer_ioc/flush_buffers since F272.</p>
<p></p>
<p>This slot is INDEPENDENT of:</p>
<p>- _ioc_graph (analytics/donor graph)</p>
<p>- _stix_graph (STIX synthesis graph)</p>
<p></p>
<p>_truth_write_graph is used exclusively for ACTIVE-phase buffered IOC ingest</p>
<p>via _graph_ingest_findings().</p>
<p></p>
<p>Args:</p>
<p>graph: DuckPGQGraph or IOCGraph instance, or None to clear.</p>
</div>
</details>
</li>
<li><code>__init__</code> (wal.py)
<details><summary>Args:</summary>
<div class="doc-comment">
<p>Args:</p>
<p>wal_path: Absolute path to the WAL LMDB directory.</p>
<p>map_size: LMDB map size in bytes (unused when unified_store provided).</p>
<p>unified_store: Optional UnifiedLMDBStore for consolidated storage.</p>
</div>
</details>
</li>
<li><code>wal_write_deadletter_marker</code> (wal.py)
<details><summary>Write a marker to the dead-letter namespace after max retries exceeded.</summary>
<div class="doc-comment">
<p>Write a marker to the dead-letter namespace after max retries exceeded.</p>
<p></p>
<p>Dead-letter key:  deadletter_ingest:{id}</p>
<p>Value:            id, query, source_type, confidence, ts, error, retry_count</p>
</div>
</details>
</li>
<li><code>_ioc_dedup_at_exit_close</code> (ioc_dedup_adapter.py)
<details><summary>Called by weakref.finalize at interpreter exit if explicit close() was not called.</summary>
<div class="doc-comment">
<p>Called by weakref.finalize at interpreter exit if explicit close() was not called.</p>
<p></p>
<p>LMDB environment handles need explicit close() on process exit to avoid</p>
<p>map file corruption and ensure all pending writes are flushed.</p>
<p>weakref.finalize + atexit ensures this even if close() was never called.</p>
</div>
</details>
</li>
<li><code>_get_chain_for_finding</code> (evidence_chain.py)
<details><summary>Retrieve the chain containing the given finding_id.</summary>
<div class="doc-comment">
<p>Retrieve the chain containing the given finding_id.</p>
<p></p>
<p>Searches all chains in the global builder for one where the finding_id</p>
<p>appears as root_finding_id or as any step's output_id.</p>
</div>
</details>
</li>
<li><code>_json_loads_flexible</code> (duckdb_store.py)
<details><summary>Sprint F26X: Single-shot JSON decode that handles str | bytes | None | empty.</summary>
<div class="doc-comment">
<p>Sprint F26X: Single-shot JSON decode that handles str | bytes | None | empty.</p>
<p></p>
<p>Replaces the 6 hand-rolled ``orjson.loads(r[N]) if r[N] else {}`` patterns</p>
<p>that previously littered this module (lines 3038-3041, 4452-4455).</p>
</div>
</details>
</li>
<li><code>_filter_batch_source_types</code> (duckdb_store.py) — <span class="doc-comment-inline">Filter batch by source_type in-memory (post row-group filter).</span></li>
<li><code>_l2_set</code> (duckdb_store.py)</li>
<li><code>upsert_target_profile</code> (duckdb_store.py) — <span class="doc-comment-inline">Upsert target profile. Silently returns on failure.</span></li>
<li><code>wait_until_ready</code> (duckdb_store.py)
<details><summary>Event-driven readiness wait — wakes via asyncio.Event, no polling.</summary>
<div class="doc-comment">
<p>Event-driven readiness wait — wakes via asyncio.Event, no polling.</p>
<p></p>
<p>ISSUE-006 fix: replaces the 40×50ms polling loop (2s worst-case)</p>
<p>with a single event-driven wait on _startup_ready.</p>
<p></p>
<p>Returns True if store became ready within timeout, False otherwise.</p>
</div>
</details>
</li>
<li><code>async_query_top_entities_by_sprint</code> (duckdb_store.py)
<details><summary>Return entity-like pivot candidates extracted from finding queries</summary>
<div class="doc-comment">
<p>Return entity-like pivot candidates extracted from finding queries</p>
<p>and provenance for the given sprint. Looks for domain/IP/url-like</p>
<p>tokens in query text. Bounded, read-only, fail-soft.</p>
<p></p>
<p>Use for: synthesis pivot hints, entity correlation candidates,</p>
<p>export enrichment. Does NOT require global_entities table.</p>
</div>
</details>
</li>
<li><code>async_query_sprint_ioc_summary</code> (duckdb_store.py)
<details><summary>Return a lightweight IOC summary for a sprint:</summary>
<div class="doc-comment">
<p>Return a lightweight IOC summary for a sprint:</p>
<p>total findings, unique source_types, avg confidence,</p>
<p>time span (first-&gt;last ts). Bounded, read-only, fail-soft.</p>
<p></p>
<p>Use for: scheduler decision support, synthesis quality signals,</p>
<p>sprint retrospektivu.</p>
</div>
</details>
</li>
<li><code>upsert_target_memory</code> (duckdb_store.py)
<details><summary>Sprint F204D: Upsert a TargetMemory record into DuckDB.</summary>
<div class="doc-comment">
<p>Sprint F204D: Upsert a TargetMemory record into DuckDB.</p>
<p></p>
<p>Serializes facets as JSON TEXT columns. Uses INSERT OR REPLACE.</p>
<p>GHOST_INVARIANT: runs on duckdb executor via run_in_executor.</p>
</div>
</details>
</li>
<li><code>_sync_replay_single_marker</code> (duckdb_store.py)
<details><summary>Sprint 8H: Synchronous single-marker replay - MUST be called on the worker thread.</summary>
<div class="doc-comment">
<p>Sprint 8H: Synchronous single-marker replay - MUST be called on the worker thread.</p>
<p></p>
<p>Uses the same _sync_insert_finding path as normal activation.</p>
<p>Returns True if DuckDB write succeeded.</p>
</div>
</details>
</li>
<li><code>_initialize_embedder</code> (lancedb_store.py) — <span class="doc-comment-inline">Initialize embedder: MLX/GPU → CoreML/ANE → Numpy fallback.</span></li>
<li><code>_init_embedder</code> (lancedb_store.py)
<details><summary>Initialize embedder via MLX-first cascade.</summary>
<div class="doc-comment">
<p>Initialize embedder via MLX-first cascade.</p>
<p></p>
<p>Invariant: random vector fallback is FORBIDDEN — silent ANN corruption.</p>
<p>Raises RuntimeError on no backend (no np.random.randn fallback).</p>
<p>MLX path is tried first (M1 ANE/GPU, zero-copy UMA).</p>
<p>``self._embedder_backend`` is set in every success path.</p>
</div>
</details>
</li>
<li><code>batch_search</code> (rag_engine.py)
<details><summary>Batch search for multiple query vectors.</summary>
<div class="doc-comment">
<p>Batch search for multiple query vectors.</p>
<p></p>
<p>Args:</p>
<p>query_vectors: Array of shape (n_queries, dim)</p>
<p>k: Number of results per query</p>
<p>filter_ids: Optional list of ids to filter results</p>
<p></p>
<p>Returns:</p>
<p>List of (ids, distances) tuples for each query</p>
</div>
</details>
</li>
<li><code>_decode_neighbors</code> (hot_edges_cache.py)
<details><summary>Decode msgspec blob → list[(dst_id, count)].</summary>
<div class="doc-comment">
<p>Decode msgspec blob → list[(dst_id, count)].</p>
<p></p>
<p>Handles both raw msgspec and lz4/zstd wire-format blobs.</p>
</div>
</details>
</li>
<li><code>has_hot_edges</code> (hot_edges_cache.py)
<details><summary>O(1) check if hot edges exist for src_id (no decoding).</summary>
<div class="doc-comment">
<p>O(1) check if hot edges exist for src_id (no decoding).</p>
<p></p>
<p>Useful for "should I even try the cache?" gate before falling back</p>
<p>to DuckPGQ. Returns False on cache miss / LMDB error.</p>
</div>
</details>
</li>
<li><code>get_node_id_by_value</code> (hot_edges_cache.py)
<details><summary>Resolve IOC value → node_id from DuckPGQGraph.</summary>
<div class="doc-comment">
<p>Resolve IOC value → node_id from DuckPGQGraph.</p>
<p></p>
<p>Hot edges are keyed by node_id (int64), not by value string. The</p>
<p>caller needs the int id to query the cache. This is a thin wrapper</p>
<p>around DuckPGQGraph's internal stable hash function.</p>
<p></p>
<p>Returns None if graph unavailable or value not in ioc_nodes.</p>
</div>
</details>
</li>
<li><code>_dedup_manager_atexit_close</code> (dedup.py)
<details><summary>F267: Called at interpreter exit via atexit.register().</summary>
<div class="doc-comment">
<p>F267: Called at interpreter exit via atexit.register().</p>
<p></p>
<p>Fires AFTER all module-level __del__ (including Rust Drop impls).</p>
<p>By this point all Python-level cleanup has run, so we only need</p>
<p>to call close() on any surviving DedupManager instances to ensure</p>
<p>their mmap-backed IOC dedup store is properly persisted.</p>
<p></p>
<p>Exceptions are silenced because we're already in interpreter shutdown —</p>
<p>logging may be unavailable and we must not raise.</p>
</div>
</details>
</li>
<li><code>contains</code> (dedup.py)
<details><summary>Check both active and previous filters.</summary>
<div class="doc-comment">
<p>Check both active and previous filters.</p>
<p></p>
<p>Args:</p>
<p>item: URL or fingerprint string to check.</p>
<p></p>
<p>Returns:</p>
<p>True if item was previously added (possible duplicate).</p>
</div>
</details>
</li>
<li><code>_mlx_cosine_similarity_batch</code> (ann_index.py)
<details><summary>MLX-compiled batch cosine similarity for exact re-ranking.</summary>
<div class="doc-comment">
<p>MLX-compiled batch cosine similarity for exact re-ranking.</p>
<p></p>
<p>Args:</p>
<p>query_emb: (D,) query vector</p>
<p>candidates: (N, D) candidate vectors (normalized)</p>
<p></p>
<p>Returns:</p>
<p>(N,) cosine similarities</p>
</div>
</details>
</li>
<li><code>_extract_entities_gliner</code> (entity_linker.py)
<details><summary>Extract entities using GLiNER.</summary>
<div class="doc-comment">
<p>Extract entities using GLiNER.</p>
<p></p>
<p>Returns:</p>
<p>List of (entity_text, start, end, entity_type) tuples</p>
</div>
</details>
</li>
<li><code>batch_link</code> (entity_linker.py)
<details><summary>Link entities in multiple texts (batch processing).</summary>
<div class="doc-comment">
<p>Link entities in multiple texts (batch processing).</p>
<p></p>
<p>Args:</p>
<p>texts: List of texts to process</p>
<p>contexts: Optional list of contexts (one per text)</p>
<p></p>
<p>Returns:</p>
<p>List of LinkedEntity lists (one per input text)</p>
</div>
</details>
</li>
<li><code>should_tune</code> (lancedb_auto_tuner.py)
<details><summary>Decide whether the cooldown + insert-threshold gate is satisfied.</summary>
<div class="doc-comment">
<p>Decide whether the cooldown + insert-threshold gate is satisfied.</p>
<p></p>
<p>Returns True only if both:</p>
<p>- ``inserts_since_tune &gt;= self._insert_threshold``</p>
<p>- ``(now - state.last_tune_at) &gt;= self._cooldown_seconds``</p>
<p></p>
<p>Pure function — does NOT mutate state. Caller persists changes.</p>
</div>
</details>
</li>
<li><code>_pivot_sync</code> (ioc_graph.py) — <span class="doc-comment-inline">Synchronous pivot — runs on _executor thread.</span></li>
<li><code>rust_query</code> (db.py)
<details><summary>Execute query via Rust StdConnectionPool (O(1) connection access).</summary>
<div class="doc-comment">
<p>Execute query via Rust StdConnectionPool (O(1) connection access).</p>
<p></p>
<p>Returns:</p>
<p>List of rows, each row is a list of strings.</p>
</div>
</details>
</li>
<li><code>inject_graph</code> (graph_attachment.py)
<details><summary>Inject a graph instance for IOC ingest on canonical findings.</summary>
<div class="doc-comment">
<p>Inject a graph instance for IOC ingest on canonical findings.</p>
<p></p>
<p>STORE IS NOT GRAPH TRUTH OWNER — the injected graph may be:</p>
<p>- IOCGraph (Kuzu): truth backend, full capability</p>
<p>- DuckPGQGraph (DuckDB): donor/alternate backend, limited capability</p>
<p></p>
<p>Capability requirements for buffered writes (ACTIVE phase):</p>
<p>- Requires: buffer_ioc(), buffer_observation(), flush_buffers()</p>
<p>- IOCGraph has these. DuckPGQGraph does NOT.</p>
<p></p>
<p>After inject, use get_graph_attachment_kind() to determine</p>
<p>which backend was attached and check capabilities explicitly.</p>
</div>
</details>
</li>
<li><code>inject_stix_graph</code> (graph_attachment.py)
<details><summary>Sprint 8VQ: Inject truth-store STIX graph for synthesis consumption.</summary>
<div class="doc-comment">
<p>Sprint 8VQ: Inject truth-store STIX graph for synthesis consumption.</p>
<p></p>
<p>TRUTH-STORE ONLY: only IOCGraph (Kuzu) has export_stix_bundle().</p>
<p>DuckPGQGraph must NEVER be injected here — it lacks STIX capability.</p>
<p></p>
<p>This slot is INDEPENDENT of _ioc_graph (analytics/donor graph).</p>
<p>_stix_graph is used exclusively by synthesis runners for STIX context.</p>
<p></p>
<p>F320 UPDATE: Both DuckPGQGraph and IOCGraph are now accepted.</p>
<p>DuckPGQGraph has export_stix_bundle() since F271.</p>
<p></p>
<p>Args:</p>
<p>graph: DuckPGQGraph or IOCGraph instance, or None to clear.</p>
</div>
</details>
</li>
<li><code>add</code> (ioc_dedup_adapter.py) — <span class="doc-comment-inline">Add IOC — returns True if NEW, False if duplicate.</span></li>
<li><code>_validate_duckdb_threads</code> (duckdb_store.py)
<details><summary>Validate threads is a safe positive integer in DuckDB-supported range.</summary>
<div class="doc-comment">
<p>Validate threads is a safe positive integer in DuckDB-supported range.</p>
<p></p>
<p>P1-3: Replaces f-string interpolation in PRAGMA threads=...</p>
<p>DuckDB PRAGMA does not support ? prepared params, so we validate</p>
<p>the integer at write time rather than use parameterized syntax.</p>
</div>
</details>
</li>
<li><code>close</code> (duckdb_store.py)
<details><summary>Synchronous close — full cleanup without any event loop manipulation.</summary>
<div class="doc-comment">
<p>Synchronous close — full cleanup without any event loop manipulation.</p>
<p></p>
<p>F300S-FIX: close() now performs the FULL cleanup inline synchronously.</p>
<p>No run_until_complete() on a running loop — which fails on Python 3.10+</p>
<p>with RuntimeError: "cannot close event loop while running".</p>
<p>close() IS the synchronous cleanup path — no event loop manipulation needed.</p>
<p></p>
<p>Idempotent: safe to call multiple times.</p>
</div>
</details>
</li>
<li><code>async_query_top_sources_by_sprint</code> (duckdb_store.py)
<details><summary>Return source_type breakdown (findings count, avg confidence)</summary>
<div class="doc-comment">
<p>Return source_type breakdown (findings count, avg confidence)</p>
<p>for a given sprint. Bounded, read-only, fail-soft.</p>
<p></p>
<p>Use for: sprint retrospektivu, source yield analysis,</p>
<p>scheduler source weighting decisions.</p>
</div>
</details>
</li>
<li><code>upsert_scorecard</code> (duckdb_store.py)
<details><summary>Sprint 8TA B.3: Insert or replace a sprint_scorecard record.</summary>
<div class="doc-comment">
<p>Sprint 8TA B.3: Insert or replace a sprint_scorecard record.</p>
<p></p>
<p>data contains: sprint_id, ts, findings_per_minute, ioc_density,</p>
<p>semantic_novelty, source_yield_json (orjson), phase_timings_json (orjson),</p>
<p>outlines_used, accepted_findings, ioc_nodes</p>
</div>
</details>
</li>
<li><code>get_sprint_trend</code> (duckdb_store.py)
<details><summary>DEPRECATED (Sprint F183D) - use async_query_sprint_trend() instead.</summary>
<div class="doc-comment">
<p>DEPRECATED (Sprint F183D) - use async_query_sprint_trend() instead.</p>
<p></p>
<p>Convenience sync wrapper - returns last N sprints ordered by ts DESC.</p>
<p>For use in sync contexts (e.g., report printing).</p>
<p></p>
<p>REMOVAL CONDITION: all callers migrated to async read seams.</p>
</div>
</details>
</li>
<li><code>get_source_leaderboard</code> (duckdb_store.py)
<details><summary>DEPRECATED (Sprint F183D) - use async_query_source_leaderboard() instead.</summary>
<div class="doc-comment">
<p>DEPRECATED (Sprint F183D) - use async_query_source_leaderboard() instead.</p>
<p></p>
<p>Convenience sync wrapper - returns top sources by hit rate.</p>
<p>For use in sync contexts (e.g., report printing).</p>
<p></p>
<p>REMOVAL CONDITION: all callers migrated to async read seams.</p>
</div>
</details>
</li>
<li><code>get_sprint_scorecard_trend</code> (duckdb_store.py)
<details><summary>Sprint F150H: Convenience sync wrapper - returns last N scorecards</summary>
<div class="doc-comment">
<p>Sprint F150H: Convenience sync wrapper - returns last N scorecards</p>
<p>ordered by ts DESC. Covers ioc_density, semantic_novelty, accepted_findings,</p>
<p>findings_per_minute, and outlines_used. Fail-soft, bounded.</p>
<p></p>
<p>Use for: yield trend reporting, retrospektiva, sprint-to-sprint</p>
<p>quality comparison without ad-hoc SQL.</p>
</div>
</details>
</li>
<li><code>get_source_mix_trend</code> (duckdb_store.py)
<details><summary>Sprint F150H: Convenience sync wrapper - returns source_type distribution</summary>
<div class="doc-comment">
<p>Sprint F150H: Convenience sync wrapper - returns source_type distribution</p>
<p>broken down by sprint for the last `days`. Each row contains</p>
<p>source_type, sprint_id, total_findings, and hit_rate.</p>
<p></p>
<p>Use for: source mix reporting - is web growing vs feed vs document,</p>
<p>and is each source getting more productive over time.</p>
</div>
</details>
</li>
<li><code>get_yield_trend</code> (duckdb_store.py)
<details><summary>Sprint F150H: Derived yield metrics per sprint - new_findings / duration_s,</summary>
<div class="doc-comment">
<p>Sprint F150H: Derived yield metrics per sprint - new_findings / duration_s,</p>
<p>dedup_hits ratio (dedup_hits / new_findings), and ioc_rate</p>
<p>(ioc_new_this_sprint / new_findings). Returns last N sprints.</p>
<p></p>
<p>Use for: "are we getting better at extracting unique findings from sources"</p>
<p>- track yield improvement or degradation across sprints.</p>
</div>
</details>
</li>
<li><code>get_high_value_sprint_ranking</code> (duckdb_store.py)
<details><summary>Sprint F150I: Rank last N sprints by a composite value score.</summary>
<div class="doc-comment">
<p>Sprint F150I: Rank last N sprints by a composite value score.</p>
<p>Composite = accepted_findings * semantic_novelty / max(duration_s, 1).</p>
<p>Higher is better. Returns sprint_id, composite_score, and component fields.</p>
<p></p>
<p>Use for: "which sprints delivered the most value per second".</p>
<p>Fail-soft, bounded.</p>
</div>
</details>
</li>
<li><code>async_vacuum_if_needed</code> (duckdb_store.py)
<details><summary>Conditionally vacuum if the DB file exceeds threshold_bytes.</summary>
<div class="doc-comment">
<p>Conditionally vacuum if the DB file exceeds threshold_bytes.</p>
<p></p>
<p>Args:</p>
<p>threshold_bytes: size above which vacuum is triggered (default 2GB)</p>
<p></p>
<p>Returns True if vacuum was triggered and succeeded, False otherwise.</p>
</div>
</details>
</li>
<li><code>_get_rrf_reranker</code> (lancedb_store.py) — <span class="doc-comment-inline">Lazy-init LanceDB RRF reranker. Returns None if rerankers unavailable.</span></li>
<li><code>health_check</code> (lancedb_store.py) — <span class="doc-comment-inline">Check embedding store health.</span></li>
<li><code>_usearch_search</code> (lancedb_store.py) — <span class="doc-comment-inline">Search using usearch (if available).</span></li>
<li><code>_extract_community_characteristics</code> (graph_rag.py) — <span class="doc-comment-inline">Extract key characteristics of a community.</span></li>
<li><code>__init__</code> (rag_engine.py)</li>
<li><code>_generate_llm_answer</code> (analyst_workbench.py)
<details><summary>Generate LLM answer using brain/model_lifecycle.py.</summary>
<div class="doc-comment">
<p>Generate LLM answer using brain/model_lifecycle.py.</p>
<p></p>
<p>Load/unload only through canonical model_lifecycle interface.</p>
<p>Returns None on any failure (fail-soft).</p>
</div>
</details>
</li>
<li><code>_extract_entities_from_question</code> (analyst_workbench.py)
<details><summary>Extract potential IOC entities from question using regex patterns.</summary>
<div class="doc-comment">
<p>Extract potential IOC entities from question using regex patterns.</p>
<p></p>
<p>Returns list of entity values (domains, IPs, emails, hashes).</p>
</div>
</details>
</li>
<li><code>_build_evidence_pointers</code> (analyst_workbench.py)
<details><summary>Build evidence pointers from findings.</summary>
<div class="doc-comment">
<p>Build evidence pointers from findings.</p>
<p></p>
<p>Caps at MAX_EVIDENCE_PTRS, ordered by confidence descending.</p>
</div>
</details>
</li>
<li><code>_get_graph</code> (graph_service.py)
<details><summary>Lazy singleton getter for DuckPGQGraph.</summary>
<div class="doc-comment">
<p>Lazy singleton getter for DuckPGQGraph.</p>
<p></p>
<p>Defined at module level so tests can patch it and affect all callers</p>
<p>(both module-level functions and GraphService instance methods).</p>
</div>
</details>
</li>
<li><code>_try_rust_rotating</code> (dedup.py)
<details><summary>Try Rust RotatingMmapBloomFilter (F288+: race-free rotation in Rust).</summary>
<div class="doc-comment">
<p>Try Rust RotatingMmapBloomFilter (F288+: race-free rotation in Rust).</p>
<p></p>
<p>Single import block — no redundant re-imports.</p>
</div>
</details>
</li>
<li><code>advance_ioc_sprint</code> (dedup.py)
<details><summary>Advance IOC dedup store to new sprint (updates first_seen/last_seen metadata).</summary>
<div class="doc-comment">
<p>Advance IOC dedup store to new sprint (updates first_seen/last_seen metadata).</p>
<p></p>
<p>Called by SprintScheduler on sprint boundary.</p>
</div>
</details>
</li>
<li><code>add_to_hot_cache</code> (dedup.py)
<details><summary>Add entry to bounded hot cache with FIFO eviction.</summary>
<div class="doc-comment">
<p>Add entry to bounded hot cache with FIFO eviction.</p>
<p></p>
<p>Hard cap: _DEDUP_HOT_CACHE_MAX entries.</p>
<p>O(1) operations using OrderedDict: move_to_end() for MRU, popitem(last=False) for FIFO.</p>
</div>
</details>
</li>
<li><code>close</code> (ann_index.py) — <span class="doc-comment-inline">Close database connection.</span></li>
<li><code>get_ann_index</code> (ann_index.py)
<details><summary>Get the singleton ANN index instance (sync, thread-safe).</summary>
<div class="doc-comment">
<p>Get the singleton ANN index instance (sync, thread-safe).</p>
<p></p>
<p>Lazy-init on first call. Thread-safe via threading.Lock double-checked locking.</p>
</div>
</details>
</li>
<li><code>get_ann_index_async</code> (ann_index.py)
<details><summary>Get the singleton ANN index instance (async-safe).</summary>
<div class="doc-comment">
<p>Get the singleton ANN index instance (async-safe).</p>
<p></p>
<p>Lazy-init on first call. Async-safe via asyncio.Lock double-checked locking.</p>
</div>
</details>
</li>
<li><code>_get_row_group_stats</code> (duckdb_store.py) — <span class="doc-comment-inline">Get row-group statistics for filter pushdown.</span></li>
<li><code>ensure_target_profiles_schema</code> (duckdb_store.py)
<details><summary>Sprint F202K: Ensure target_profiles table exists in DuckDB.</summary>
<div class="doc-comment">
<p>Sprint F202K: Ensure target_profiles table exists in DuckDB.</p>
<p>Safe to call multiple times - uses CREATE TABLE IF NOT EXISTS.</p>
<p>Must be called after _init_connection (connection must exist).</p>
</div>
</details>
</li>
<li><code>ensure_target_memory_schema</code> (duckdb_store.py)
<details><summary>Sprint F204D: Ensure target_memory table exists in DuckDB.</summary>
<div class="doc-comment">
<p>Sprint F204D: Ensure target_memory table exists in DuckDB.</p>
<p>Safe to call multiple times - uses CREATE TABLE IF NOT EXISTS.</p>
<p>Must be called after _init_connection (connection must exist).</p>
</div>
</details>
</li>
<li><code>insert_run</code> (duckdb_store.py)</li>
<li><code>get_target_profile</code> (duckdb_store.py) — <span class="doc-comment-inline">Get target profile. Returns row tuple or None.</span></li>
<li><code>_sync_get_target_profile</code> (duckdb_store.py) — <span class="doc-comment-inline">Sync get - MUST be called on the worker thread. Returns None if not found.</span></li>
<li><code>_flush_pending_findings_sync</code> (duckdb_store.py)
<details><summary>Sync flush: persists pending findings from _pending_accepted_findings on close.</summary>
<div class="doc-comment">
<p>Sync flush: persists pending findings from _pending_accepted_findings on close.</p>
<p></p>
<p>Called from _do_sync_close via executor.submit to avoid blocking.</p>
<p>Writes via Arrow batch pipeline (same as async_ingest_findings_batch).</p>
</div>
</details>
</li>
<li><code>async_healthcheck</code> (duckdb_store.py)
<details><summary>Quick health check - attempts a zero-cost query.</summary>
<div class="doc-comment">
<p>Quick health check - attempts a zero-cost query.</p>
<p></p>
<p>Returns True if the store is healthy and responsive.</p>
</div>
</details>
</li>
<li><code>_sync_query_findings_by_keywords</code> (duckdb_store.py) — <span class="doc-comment-inline">Sync - MUST be called on worker thread.</span></li>
<li><code>_sync_query_top_sources_by_sprint</code> (duckdb_store.py) — <span class="doc-comment-inline">Sync - MUST be called on worker thread.</span></li>
<li><code>async_query_source_leaderboard</code> (duckdb_store.py)
<details><summary>Return top sources by hit rate for the last N days.</summary>
<div class="doc-comment">
<p>Return top sources by hit rate for the last N days.</p>
<p>Thread-safe, non-blocking.</p>
</div>
</details>
</li>
<li><code>async_upsert_target_profile</code> (duckdb_store.py)
<details><summary>Sprint F202K: Insert or update a target profile.</summary>
<div class="doc-comment">
<p>Sprint F202K: Insert or update a target profile.</p>
<p></p>
<p>Thread-safe, non-blocking - runs on duckdb_worker via run_in_executor.</p>
<p>Silently fails if store is closed or uninitialized.</p>
</div>
</details>
</li>
<li><code>async_get_target_profile</code> (duckdb_store.py)
<details><summary>Sprint F202K: Get a target profile by target_id.</summary>
<div class="doc-comment">
<p>Sprint F202K: Get a target profile by target_id.</p>
<p></p>
<p>Thread-safe, non-blocking - runs on duckdb_worker via run_in_executor.</p>
<p>Returns None if not found or on error.</p>
</div>
</details>
</li>
<li><code>_sync_graph_update</code> (duckdb_store.py)</li>
<li><code>_init_cache</code> (lancedb_store.py) — <span class="doc-comment-inline">Initialize LMDB cache for embeddings with float16 quantization.</span></li>
<li><code>_log_table_opened</code> (lancedb_store.py)
<details><summary>Sprint F264D: Log 'lancedb.table_opened' event with size_mb.</summary>
<div class="doc-comment">
<p>Sprint F264D: Log 'lancedb.table_opened' event with size_mb.</p>
<p></p>
<p>M1 observability — measures table footprint for IVF-PQ benefit verification.</p>
<p>Estimated: rows × embedding_dim × 4 bytes (float32) + PyArrow overhead.</p>
</div>
</details>
</li>
<li><code>_analyze_contradiction</code> (graph_rag.py) — <span class="doc-comment-inline">Analyze if two nodes contradict each other.</span></li>
<li><code>_detect_contradictions_with_narratives</code> (graph_rag.py)
<details><summary>Detect contradictions and generate competing narratives with confidence.</summary>
<div class="doc-comment">
<p>Detect contradictions and generate competing narratives with confidence.</p>
<p></p>
<p>Args:</p>
<p>facts: Facts to analyze</p>
<p></p>
<p>Returns:</p>
<p>Tuple of (contested, primary_paths, counter_paths, narratives)</p>
</div>
</details>
</li>
<li><code>_summarize_cluster</code> (rag_engine.py) — <span class="doc-comment-inline">Summarize cluster text via Hermes3 generate_structured(). Truncates on failure.</span></li>
<li><code>_extract_tokens</code> (analyst_workbench.py)</li>
<li><code>pagerank</code> (graph_service.py)
<details><summary>ISSUE #14: PageRank via DuckPGQGraph.pagerank().</summary>
<div class="doc-comment">
<p>ISSUE #14: PageRank via DuckPGQGraph.pagerank().</p>
<p></p>
<p>Returns:</p>
<p>Dict mapping IOC value → PageRank score. Empty dict if graph unavailable.</p>
</div>
</details>
</li>
<li><code>shortest_path</code> (graph_service.py)
<details><summary>ISSUE #14: Shortest path via DuckPGQGraph.shortest_path().</summary>
<div class="doc-comment">
<p>ISSUE #14: Shortest path via DuckPGQGraph.shortest_path().</p>
<p></p>
<p>Returns:</p>
<p>List of IOC values forming the path, or None if no path exists.</p>
</div>
</details>
</li>
<li><code>community_detection</code> (graph_service.py)
<details><summary>ISSUE #14: Community detection via DuckPGQGraph.community_detection().</summary>
<div class="doc-comment">
<p>ISSUE #14: Community detection via DuckPGQGraph.community_detection().</p>
<p></p>
<p>Returns:</p>
<p>Dict mapping community_id → list of IOC values in that community.</p>
</div>
</details>
</li>
<li><code>_estimate_community_count</code> (graph_service.py)
<details><summary>Estimate community count via DuckPGQGraph.community_detection().</summary>
<div class="doc-comment">
<p>Estimate community count via DuckPGQGraph.community_detection().</p>
<p></p>
<p>ISSUE #14: Delegates to the proper community_detection() method</p>
<p>which uses iterative label propagation in DuckDB SQL.</p>
<p>Returns 0 on error.</p>
</div>
</details>
</li>
<li><code>get</code> (entity_linker.py) — <span class="doc-comment-inline">Get cached value if not expired.</span></li>
<li><code>resolve_entity</code> (entity_linker.py)
<details><summary>Resolve single entity to Wikidata (convenience function).</summary>
<div class="doc-comment">
<p>Resolve single entity to Wikidata (convenience function).</p>
<p></p>
<p>Args:</p>
<p>entity_text: Entity text to resolve</p>
<p></p>
<p>Returns:</p>
<p>Best matching EntityCandidate or None</p>
</div>
</details>
</li>
<li><code>export_stix_bundle</code> (ioc_graph.py)
<details><summary>Export all IOC nodes as STIX 2.1 objects.</summary>
<div class="doc-comment">
<p>Export all IOC nodes as STIX 2.1 objects.</p>
<p></p>
<p>Validates the bundle via stix2.parse() — returns empty list on failure.</p>
</div>
</details>
</li>
<li><code>init_forensics_schema</code> (db.py) — <span class="doc-comment-inline">Initialize forensics metadata table in DuckDB.</span></li>
<li><code>get_stats</code> (neuromorphic.py) — <span class="doc-comment-inline">Get neuromorphic memory statistics.</span></li>
<li><code>close</code> (ioc_dedup_adapter.py)
<details><summary>Graceful shutdown — persist state and close LMDB.</summary>
<div class="doc-comment">
<p>Graceful shutdown — persist state and close LMDB.</p>
<p></p>
<p>F289: Detaches finalizer on explicit call to prevent double-cleanup</p>
<p>at interpreter exit. After detach(), atexit no longer triggers</p>
<p>_ioc_dedup_at_exit_close.</p>
</div>
</details>
</li>
<li><code>_get_TargetProfileSummary</code> (duckdb_store.py) — <span class="doc-comment-inline">Lazy loader for TargetProfileSummary with inline fallback.</span></li>
<li><code>_filter_row_groups</code> (duckdb_store.py) — <span class="doc-comment-inline">Apply filters to get list of row-groups to read.</span></li>
<li><code>iter_batches</code> (duckdb_store.py)
<details><summary>Iterate over filtered row-groups as Arrow RecordBatch objects.</summary>
<div class="doc-comment">
<p>Iterate over filtered row-groups as Arrow RecordBatch objects.</p>
<p></p>
<p>Yields:</p>
<p>pyarrow.RecordBatch — zero-copy view of one row-group.</p>
<p>Caller converts to Polars via pl.from_arrow(batch) for zero-copy.</p>
</div>
</details>
</li>
<li><code>_validate_path_setting</code> (duckdb_store.py)
<details><summary>Validate Path setting for DuckDB SET commands.</summary>
<div class="doc-comment">
<p>Validate Path setting for DuckDB SET commands.</p>
<p></p>
<p>P1-3: Ensures path is absolute and contains no shell metacharacters.</p>
</div>
</details>
</li>
<li><code>_with_transaction</code> (duckdb_store.py)
<details><summary>Run fn(conn) inside an explicit transaction.</summary>
<div class="doc-comment">
<p>Run fn(conn) inside an explicit transaction.</p>
<p>Commits on success, rolls back on any exception.</p>
<p>Returns fn's return value.</p>
</div>
</details>
</li>
<li><code>_get_read_conn</code> (duckdb_store.py)
<details><summary>ISSUE-008 P1: Return next read connection from round-robin pool.</summary>
<div class="doc-comment">
<p>ISSUE-008 P1: Return next read connection from round-robin pool.</p>
<p></p>
<p>Read pool allows parallel analytical queries without contention</p>
<p>with the write connection. Falls back to _file_conn if pool is empty.</p>
<p></p>
<p>Thread-safe: uses atomic idx increment.</p>
</div>
</details>
</li>
<li><code>_sync_execute_raw_sql</code> (duckdb_store.py)
<details><summary>Execute raw SQL and return all rows.</summary>
<div class="doc-comment">
<p>Execute raw SQL and return all rows.</p>
<p></p>
<p>MUST be called on duckdb worker thread (inside run_in_executor).</p>
<p>Thread-safe: uses _file_conn/_persistent_conn.</p>
</div>
</details>
</li>
<li><code>async_record_sprint_delta</code> (duckdb_store.py)
<details><summary>Insert a sprint_delta record.</summary>
<div class="doc-comment">
<p>Insert a sprint_delta record.</p>
<p></p>
<p>Thread-safe, non-blocking.</p>
</div>
</details>
</li>
<li><code>_sync_query_recent_findings_by_sprint</code> (duckdb_store.py) — <span class="doc-comment-inline">Sync - MUST be called on worker thread.</span></li>
<li><code>_sync</code> (duckdb_store.py)</li>
<li><code>async_query_sprint_source_stats</code> (duckdb_store.py)
<details><summary>Return per-source-type avg_hit_rate over the last 5 days.</summary>
<div class="doc-comment">
<p>Return per-source-type avg_hit_rate over the last 5 days.</p>
<p>Used by SprintScheduler.load_source_weights().</p>
<p>Thread-safe, non-blocking.</p>
</div>
</details>
</li>
<li><code>async_get_entity_observations_by_entity</code> (duckdb_store.py) — <span class="doc-comment-inline">Sprint F350M: Get entity observations by entity value.</span></li>
<li><code>_envelope_to_payload</code> (duckdb_store.py)
<details><summary>Sprint F202A §2: Serialize FindingEnvelope to payload_text string.</summary>
<div class="doc-comment">
<p>Sprint F202A §2: Serialize FindingEnvelope to payload_text string.</p>
<p></p>
<p>Fail-soft: returns None if serialization fails or size exceeds limit.</p>
<p>Caller degrades to plain finding when None is returned.</p>
</div>
</details>
</li>
<li><code>_sync</code> (lancedb_store.py)</li>
<li><code>_warm_cache</code> (lancedb_store.py) — <span class="doc-comment-inline">Pre-load frequently accessed embeddings.</span></li>
<li><code>_search</code> (lancedb_store.py)</li>
<li><code>_ensure_store</code> (lancedb_store.py) — <span class="doc-comment-inline">Lazily initialize SqliteVecStore.</span></li>
<li><code>_detect_query_type</code> (lancedb_store.py)
<details><summary>AREA H+: Decide whether to use FTS, hybrid, or pure vector search.</summary>
<div class="doc-comment">
<p>AREA H+: Decide whether to use FTS, hybrid, or pure vector search.</p>
<p>Same heuristic as LanceDBIdentityStore for consistency.</p>
</div>
</details>
</li>
<li><code>fetch_node_with_semaphore</code> (graph_rag.py) — <span class="doc-comment-inline">Fetch single node: returns (node_id, embedding, confidence).</span></li>
<li><code>get_bucket_key</code> (graph_rag.py) — <span class="doc-comment-inline">Get time bucket key for fact.</span></li>
<li><code>_init_index</code> (rag_engine.py) — <span class="doc-comment-inline">Initialize the usearch index.</span></li>
<li><code>_dense_retrieval</code> (rag_engine.py) — <span class="doc-comment-inline">Dense retrieval using cosine similarity.</span></li>
<li><code>_log_table_opened</code> (ann_index.py) — <span class="doc-comment-inline">Log 'lancedb.table_opened' event with size_mb.</span></li>
<li><code>_maybe_evict</code> (ann_index.py) — <span class="doc-comment-inline">Evict oldest entries if table exceeds MAX_ENTRIES.</span></li>
<li><code>_build_sparql_query</code> (entity_linker.py)
<details><summary>Build SPARQL query for entity search.</summary>
<div class="doc-comment">
<p>Build SPARQL query for entity search.</p>
<p></p>
<p>Args:</p>
<p>entity_text: Text to search for</p>
<p>limit: Maximum results</p>
<p></p>
<p>Returns:</p>
<p>SPARQL query string</p>
</div>
</details>
</li>
<li><code>record_observation</code> (ioc_graph.py)
<details><summary>Record an OBSERVED edge between two IOC nodes.</summary>
<div class="doc-comment">
<p>Record an OBSERVED edge between two IOC nodes.</p>
<p></p>
<p>Idempotent: if the edge already exists, updates last_seen on the edge.</p>
</div>
</details>
</li>
<li><code>truth_write_graph_supports_buffered_writes</code> (graph_attachment.py)
<details><summary>Sprint 8WA: Does _truth_write_graph support ACTIVE-phase buffered writes?</summary>
<div class="doc-comment">
<p>Sprint 8WA: Does _truth_write_graph support ACTIVE-phase buffered writes?</p>
<p></p>
<p>Returns True only if _truth_write_graph is IOCGraph (Kuzu) with both:</p>
<p>- buffer_ioc()</p>
<p>- flush_buffers()</p>
<p></p>
<p>This is a dedicated check for the truth-write slot, independent of</p>
<p>the analytics _ioc_graph slot.</p>
</div>
</details>
</li>
<li><code>wal_clear_pending_sync_marker</code> (wal.py)
<details><summary>Clear a pending-sync marker after successful recovery.</summary>
<div class="doc-comment">
<p>Clear a pending-sync marker after successful recovery.</p>
<p></p>
<p>Called by a future recovery sprint after the DuckDB write succeeds.</p>
</div>
</details>
</li>
<li><code>_json_dumps_str</code> (duckdb_store.py)
<details><summary>P1-11: Single canonical encode for DuckDB VARCHAR parameters.</summary>
<div class="doc-comment">
<p>P1-11: Single canonical encode for DuckDB VARCHAR parameters.</p>
<p></p>
<p>DuckDB requires ``str`` for VARCHAR columns. Uses ``encode()`` (pool-backed</p>
<p>``msgspec``) then ``.decode()`` — single allocation, no per-call Encoder</p>
<p>instantiation on the hot path. Fallback to ``orjson`` for msgspec-incompatible</p>
<p>types (sets, custom objects).</p>
<p></p>
<p>Used at: DHT metadata INSERT (L1950-1951).</p>
</div>
</details>
</li>
<li><code>set_uma_state</code> (duckdb_store.py)
<details><summary>Set or update UMA memory pressure state at runtime.</summary>
<div class="doc-comment">
<p>Set or update UMA memory pressure state at runtime.</p>
<p></p>
<p>Can be called while the store is open to adjust DuckDB settings.</p>
<p>Resolves new settings and applies to all connections immediately.</p>
<p></p>
<p>Args:</p>
<p>uma_state: "WARN", "CRITICAL", "EMERGENCY", or None for normal.</p>
<p>swap_detected: True if system-level swap is active.</p>
</div>
</details>
</li>
<li><code>_record_query_latency</code> (duckdb_store.py) — <span class="doc-comment-inline">Record a DuckDB query latency to MetricsRegistry (fail-safe).</span></li>
<li><code>_prewarm_file_conn</code> (duckdb_store.py)
<details><summary>Sprint 7H: Amortize cold connect by issuing a no-op query.</summary>
<div class="doc-comment">
<p>Sprint 7H: Amortize cold connect by issuing a no-op query.</p>
<p>Called on first write to warm up _file_conn.</p>
<p>Returns True if prewarm succeeded.</p>
</div>
</details>
</li>
<li><code>async_query_sprint_trend</code> (duckdb_store.py)
<details><summary>Return trend data for the last N sprints, ordered by ts DESC.</summary>
<div class="doc-comment">
<p>Return trend data for the last N sprints, ordered by ts DESC.</p>
<p>Thread-safe, non-blocking.</p>
</div>
</details>
</li>
<li><code>_sync_query_findings_by_text</code> (duckdb_store.py) — <span class="doc-comment-inline">Sync - MUST be called on worker thread.</span></li>
<li><code>get_recent_worst_sprints</code> (duckdb_store.py)
<details><summary>Sprint F150I: Return the bottom N sprints by yield (new_findings / duration_s).</summary>
<div class="doc-comment">
<p>Sprint F150I: Return the bottom N sprints by yield (new_findings / duration_s).</p>
<p>Only sprints with new_findings &gt; 0 are included (exclude zero-yield noise).</p>
<p>Reads from sprint_delta. Fail-soft, bounded.</p>
</div>
</details>
</li>
<li><code>_extract_url_from_provenance</code> (duckdb_store.py)
<details><summary>Sprint 8AK: Extract the first HTTP(S) URL from a provenance tuple.</summary>
<div class="doc-comment">
<p>Sprint 8AK: Extract the first HTTP(S) URL from a provenance tuple.</p>
<p></p>
<p>Source-agnostic: scans all positions regardless of source type.</p>
<p>Returns empty string if no URL is found.</p>
</div>
</details>
</li>
<li><code>_wal_clear_pending_sync_marker</code> (duckdb_store.py)
<details><summary>Sprint 8F: Clear a pending-sync marker after successful recovery.</summary>
<div class="doc-comment">
<p>Sprint 8F: Clear a pending-sync marker after successful recovery.</p>
<p></p>
<p>Called by a future recovery sprint after the DuckDB write succeeds.</p>
</div>
</details>
</li>
<li><code>_wal_get_pending_marker</code> (duckdb_store.py)
<details><summary>Sprint 8H: Get a single pending marker value by finding_id.</summary>
<div class="doc-comment">
<p>Sprint 8H: Get a single pending marker value by finding_id.</p>
<p></p>
<p>Returns the marker dict or None if not found.</p>
</div>
</details>
</li>
<li><code>_add_to_hot_cache</code> (duckdb_store.py)
<details><summary>Add entry to bounded hot cache with FIFO eviction.</summary>
<div class="doc-comment">
<p>Add entry to bounded hot cache with FIFO eviction.</p>
<p></p>
<p>DEPRECATED (Sprint F222): Delegates to DedupManager.add_to_hot_cache().</p>
<p>Kept for backward compat during migration - remove when all callers migrated.</p>
</div>
</details>
</li>
<li><code>_lmdb_put_multi</code> (lancedb_store.py)
<details><summary>Synchronous batch LMDB put - single transaction for multiple items.</summary>
<div class="doc-comment">
<p>Synchronous batch LMDB put - single transaction for multiple items.</p>
<p></p>
<p>S3: Reduces 100 individual txn.begin() calls to 1.</p>
</div>
</details>
</li>
<li><code>get_cache_telemetry</code> (lancedb_store.py) — <span class="doc-comment-inline">F214OPT-C: Telemetry accessor for LanceDB cache bounds and stats.</span></li>
<li><code>_get_colbert_reranker</code> (lancedb_store.py) — <span class="doc-comment-inline">Lazy load ColBERT.</span></li>
<li><code>_embed_texts</code> (lancedb_store.py)
<details><summary>Embed texts via the initialized embedder backend.</summary>
<div class="doc-comment">
<p>Embed texts via the initialized embedder backend.</p>
<p></p>
<p>``self._embedder`` is guaranteed non-None by ``_init_embedder``,</p>
<p>which raises ``RuntimeError`` on no backend (no silent fallback).</p>
<p>``MLXEmbeddingManager`` exposes ``.encode(texts)``.</p>
</div>
</details>
</li>
<li><code>upsert_paper</code> (lancedb_store.py)
<details><summary>Upsert a single academic paper.</summary>
<div class="doc-comment">
<p>Upsert a single academic paper.</p>
<p></p>
<p>Args:</p>
<p>paper: AcademicPaper instance to store.</p>
</div>
</details>
</li>
<li><code>__init__</code> (graph_rag.py)
<details><summary>Initialize GraphRAG orchestrator.</summary>
<div class="doc-comment">
<p>Initialize GraphRAG orchestrator.</p>
<p></p>
<p>Args:</p>
<p>knowledge_layer: PersistentKnowledgeLayer instance</p>
</div>
</details>
</li>
<li><code>_calculate_community_cohesion</code> (graph_rag.py) — <span class="doc-comment-inline">Calculate cohesion score for a community.</span></li>
<li><code>__init__</code> (rag_engine.py)</li>
<li><code>_estimate_memory_usage</code> (rag_engine.py) — <span class="doc-comment-inline">Estimate memory usage in MB.</span></li>
<li><code>_cluster_key</code> (analyst_workbench.py)</li>
<li><code>_compute_entropy_batch</code> (quality_assessment.py)
<details><summary>Sprint F320: Batch entropy — Rust path uses NEON SIMD rayon, ~10-30× faster.</summary>
<div class="doc-comment">
<p>Sprint F320: Batch entropy — Rust path uses NEON SIMD rayon, ~10-30× faster.</p>
<p></p>
<p>Fallback: serial list comprehension calling _compute_entropy per item.</p>
<p>Output is bit-identical to single-call _compute_entropy per text.</p>
</div>
</details>
</li>
<li><code>graph_stats</code> (graph_service.py) — <span class="doc-comment-inline">Return graph node/edge statistics. Returns empty dict on error.</span></li>
<li><code>link_entities</code> (entity_linker.py)
<details><summary>Link entities in text (convenience function).</summary>
<div class="doc-comment">
<p>Link entities in text (convenience function).</p>
<p></p>
<p>Args:</p>
<p>text: Input text</p>
<p>context: Optional context</p>
<p></p>
<p>Returns:</p>
<p>List of LinkedEntity objects</p>
</div>
</details>
</li>
<li><code>_load_state</code> (lancedb_auto_tuner.py) — <span class="doc-comment-inline">Load state from JSON. Returns TuneState() on any error (fail-soft).</span></li>
<li><code>__init__</code> (ioc_graph.py)</li>
<li><code>buffer_ioc</code> (ioc_graph.py)
<details><summary>Add IOC to in-memory buffer — ZERO Kuzu I/O in ACTIVE phase.</summary>
<div class="doc-comment">
<p>Add IOC to in-memory buffer — ZERO Kuzu I/O in ACTIVE phase.</p>
<p>Flush automatically when buffer reaches _BUFFER_FLUSH_SIZE.</p>
<p></p>
<p>After close() the buffer is closed: new writes are silently dropped</p>
<p>so no buffered data can be lost or observed in an inconsistent state.</p>
</div>
</details>
</li>
<li><code>__init__</code> (semantic_store.py)</li>
<li><code>aclose</code> (wal.py)
<details><summary>Async idempotent shutdown — canonical async cleanup path.</summary>
<div class="doc-comment">
<p>Async idempotent shutdown — canonical async cleanup path.</p>
<p></p>
<p>Uses asyncio.to_thread() to avoid blocking the event loop.</p>
<p>Idempotent: safe to call multiple times.</p>
</div>
</details>
</li>
<li><code>_get_scipy_sparse</code> (neuromorphic.py) — <span class="doc-comment-inline">Lazy scipy.sparse loader — defers ~227ms import cost until first use.</span></li>
<li><code>_get_np</code> (neuromorphic.py) — <span class="doc-comment-inline">Return numpy module. Defined at module level for type compatibility.</span></li>
<li><code>add_batch</code> (ioc_dedup_adapter.py)
<details><summary>Batch add IOCs. Returns list of bool (True = new).</summary>
<div class="doc-comment">
<p>Batch add IOCs. Returns list of bool (True = new).</p>
<p></p>
<p>Args:</p>
<p>items: List of (value, ioc_type, confidence) tuples</p>
</div>
</details>
</li>
<li><code>advance_sprint</code> (ioc_dedup_adapter.py)
<details><summary>Advance to new sprint. Persists current state to LMDB before advancing.</summary>
<div class="doc-comment">
<p>Advance to new sprint. Persists current state to LMDB before advancing.</p>
<p></p>
<p>Called by sprint_scheduler at sprint boundary.</p>
</div>
</details>
</li>
<li><code>get_entries_by_type</code> (ioc_dedup_adapter.py)
<details><summary>Get entries with full metadata for a given IOC type.</summary>
<div class="doc-comment">
<p>Get entries with full metadata for a given IOC type.</p>
<p></p>
<p>Returns:</p>
<p>List of (normalized_value, first_sprint, last_sprint, occurrence_count, confidence_max)</p>
</div>
</details>
</li>
<li><code>_provenance_to_arrow_native</code> (duckdb_store.py)
<details><summary>P1-11: Single canonical encode_for_arrow call — no triple import, no fallback loop.</summary>
<div class="doc-comment">
<p>P1-11: Single canonical encode_for_arrow call — no triple import, no fallback loop.</p>
<p></p>
<p>Arrow ``pa.array(bytes, type=pa.string())`` ingests bytes directly — zero-copy.</p>
<p>``msgspec`` encodes ``tuple`` natively, no ``list()`` conversion needed.</p>
<p></p>
<p>Returns:</p>
<p>- bytes: canonical encode_for_arrow() result (Arrow-compatible, zero-copy)</p>
<p>- None: for empty/None provenance (SQL NULL / Arrow null)</p>
</div>
</details>
</li>
<li><code>__init__</code> (duckdb_store.py)</li>
<li><code>iter_batches_async</code> (duckdb_store.py)
<details><summary>Async iterator for use in async contexts.</summary>
<div class="doc-comment">
<p>Async iterator for use in async contexts.</p>
<p></p>
<p>Yields batches on a thread pool to avoid blocking event loop.</p>
</div>
</details>
</li>
<li><code>_l1_get</code> (duckdb_store.py)</li>
<li><code>_validate_duckdb_setting</code> (duckdb_store.py)
<details><summary>Validate DuckDB setting value to prevent SQL injection.</summary>
<div class="doc-comment">
<p>Validate DuckDB setting value to prevent SQL injection.</p>
<p></p>
<p>P1-3: Replaces f-string interpolation in SET commands.</p>
<p>Only allows alphanumeric, GB/MB/KB/TB/MiB/GiB/KiB suffixes, and basic punctuation.</p>
</div>
</details>
</li>
<li><code>_graph_store</code> (duckdb_store.py) — <span class="doc-comment-inline">Lazy-init GraphAttachmentStore.</span></li>
<li><code>_invalidate_insert_stmt</code> (duckdb_store.py)
<details><summary>Sprint F264: Drop cached prepared statement. Call on close / reconnect.</summary>
<div class="doc-comment">
<p>Sprint F264: Drop cached prepared statement. Call on close / reconnect.</p>
<p></p>
<p>Safe to call from any thread; sets the cache to None so the next</p>
<p>`_get_insert_stmt(conn)` re-prepares on the (possibly new) conn.</p>
</div>
</details>
</li>
<li><code>_sync_iter_next_batch</code> (duckdb_store.py) — <span class="doc-comment-inline">Pull multiple batches from iterator in single executor call.</span></li>
<li><code>_sync_query_sprint_ioc_summary</code> (duckdb_store.py) — <span class="doc-comment-inline">Sync - MUST be called on worker thread.</span></li>
<li><code>get_recent_best_sprints</code> (duckdb_store.py)
<details><summary>Sprint F150I: Return the top N sprints by yield (new_findings / duration_s).</summary>
<div class="doc-comment">
<p>Sprint F150I: Return the top N sprints by yield (new_findings / duration_s).</p>
<p>Reads from sprint_delta. Fail-soft, bounded.</p>
</div>
</details>
</li>
<li><code>async_record_entity_observations_bulk</code> (duckdb_store.py) — <span class="doc-comment-inline">Sprint F350M: Bulk record entity observations.</span></li>
<li><code>async_get_research_sessions_by_sprint</code> (duckdb_store.py) — <span class="doc-comment-inline">Sprint F350M: Get research sessions by sprint_id.</span></li>
<li><code>async_get_recent_research_sessions</code> (duckdb_store.py) — <span class="doc-comment-inline">Sprint F350M: Get recent research sessions.</span></li>
<li><code>advance_ioc_sprint</code> (duckdb_store.py)
<details><summary>Advance IOC dedup store to new sprint boundary.</summary>
<div class="doc-comment">
<p>Advance IOC dedup store to new sprint boundary.</p>
<p></p>
<p>Issue #14: Delegates to SprintBoundaryCoordinator to keep</p>
<p>_DuckDBQueryCache pure-cache (no dedup knowledge) and</p>
<p>DedupManager pure-dedup (no cache knowledge).</p>
</div>
</details>
</li>
<li><code>reset_ingest_reason_counters</code> (duckdb_store.py)
<details><summary>Sprint 8AV: Reset all ingest outcome counters to zero.</summary>
<div class="doc-comment">
<p>Sprint 8AV: Reset all ingest outcome counters to zero.</p>
<p></p>
<p>Side-effect free, test-safe, can be called any time.</p>
<p>Resets all counters on QualityAssessmentState.</p>
</div>
</details>
</li>
<li><code>_warm_embedding_cache</code> (lancedb_store.py) — <span class="doc-comment-inline">Pre-load embeddings for frequently used queries.</span></li>
<li><code>_init_secure_enclave</code> (rag_engine.py) — <span class="doc-comment-inline">Inicializovat Secure Enclave</span></li>
<li><code>ask_sync</code> (analyst_workbench.py)
<details><summary>Synchronous wrapper around ask().</summary>
<div class="doc-comment">
<p>Synchronous wrapper around ask().</p>
<p></p>
<p>For use in sync contexts. Prefer ask() in async contexts.</p>
</div>
</details>
</li>
<li><code>get_evidence_chain</code> (analyst_workbench.py)
<details><summary>F203D: Retrieve the evidence chain for a given finding_id.</summary>
<div class="doc-comment">
<p>F203D: Retrieve the evidence chain for a given finding_id.</p>
<p></p>
<p>Chains are accumulated during sprint teardown by the EvidenceChainBuilder</p>
<p>(evidence_chain.py) and stored as a sprint artifact. This function looks up</p>
<p>the chain from the module-level registry.</p>
<p></p>
<p>Returns the EvidenceChain if found, None otherwise.</p>
</div>
</details>
</li>
<li><code>clear_all</code> (hot_edges_cache.py) — <span class="doc-comment-inline">Drop ALL hot edges (testing only). Returns True on success.</span></li>
<li><code>__init__</code> (entity_linker.py)
<details><summary>Initialize cache.</summary>
<div class="doc-comment">
<p>Initialize cache.</p>
<p></p>
<p>Args:</p>
<p>max_size: Maximum number of entries</p>
<p>ttl_seconds: Time-to-live in seconds</p>
</div>
</details>
</li>
<li><code>_save_state</code> (lancedb_auto_tuner.py) — <span class="doc-comment-inline">Persist state atomically. Fail-soft — never raises.</span></li>
<li><code>_rss_under_guard</code> (lancedb_auto_tuner.py)
<details><summary>True iff process RSS is below M1 8GB safety threshold.</summary>
<div class="doc-comment">
<p>True iff process RSS is below M1 8GB safety threshold.</p>
<p></p>
<p>Fail-soft: if psutil is missing or measurement fails, returns True</p>
<p>(allow tuning) — the existing per-table row guards still bound work.</p>
</div>
</details>
</li>
<li><code>_init_schema_sync</code> (ioc_graph.py) — <span class="doc-comment-inline">Synchronous schema init — runs on _executor thread.</span></li>
<li><code>record_observation_batch</code> (ioc_graph.py)
<details><summary>Batch record of OBSERVED edges between IOC nodes.</summary>
<div class="doc-comment">
<p>Batch record of OBSERVED edges between IOC nodes.</p>
<p></p>
<p>Args:</p>
<p>observations: List of (ioc_id_a, ioc_id_b, finding_id, ts, source_type).</p>
<p>Idempotent: duplicate edges update last_seen only.</p>
</div>
</details>
</li>
<li><code>wal_delete_deadletter_marker</code> (wal.py) — <span class="doc-comment-inline">Delete a dead-letter marker (used when replay succeeds later).</span></li>
<li><code>__del__</code> (wal.py)
<details><summary>Fallback destructor -- weakref.finalize is primary, __del__ is last resort.</summary>
<div class="doc-comment">
<p>Fallback destructor -- weakref.finalize is primary, __del__ is last resort.</p>
<p></p>
<p>In Python 3.14+ __del__ is not guaranteed to run, so _ensure_cleanup()</p>
<p>(via weakref.finalize) is the canonical cleanup path.</p>
</div>
</details>
</li>
<li><code>_ensure_lmdb</code> (ioc_dedup_adapter.py) — <span class="doc-comment-inline">Ensure LMDB environment is open. Returns True if successful.</span></li>
<li><code>get</code> (duckdb_store.py)</li>
<li><code>invalidate</code> (duckdb_store.py) — <span class="doc-comment-inline">Clear L1 and L2. Called after schema migration.</span></li>
<li><code>get_arrow_metrics</code> (duckdb_store.py)
<details><summary>Sprint F265C: Expose Arrow ingest metrics for sprint telemetry.</summary>
<div class="doc-comment">
<p>Sprint F265C: Expose Arrow ingest metrics for sprint telemetry.</p>
<p>Sprint F265B Variant B: DEPRECATED — _ARROW_METRICS moved to instance-level</p>
<p>DuckDBShadowStore._arrow_metrics (per-sprint reset, prevents cross-sprint growth).</p>
<p></p>
<p>Returns empty dict — instance metrics are accessed via store._arrow_metrics</p>
<p>in __main__.py L2664 (preferred path) or sprint_scheduler L8949.</p>
<p>Kept for backward compat with external callers that import this directly.</p>
</div>
</details>
</li>
<li><code>_sync_insert_finding</code> (duckdb_store.py)</li>
<li><code>insert_shadow_run</code> (duckdb_store.py)</li>
<li><code>_submit_findings_bg</code> (duckdb_store.py) — <span class="doc-comment-inline">Background task — runs submit_findings() logic without blocking the caller.</span></li>
<li><code>_duckdb_arrow_sync</code> (duckdb_store.py)
<details><summary>Sprint P1-2: DuckDB Arrow-only sync helper - DuckDB Single-Writer Variant 2.</summary>
<div class="doc-comment">
<p>Sprint P1-2: DuckDB Arrow-only sync helper - DuckDB Single-Writer Variant 2.</p>
<p></p>
<p>Runs on _duckdb_arrow_executor. Caller is responsible for WAL step</p>
<p>(separate executor, sequential WAL-first invariant).</p>
<p></p>
<p>Returns (inserted_count, error_type) - same shape as</p>
<p>_sync_record_canonical_findings_batch_arrow.</p>
</div>
</details>
</li>
<li><code>_wal_delete_deadletter_marker</code> (duckdb_store.py) — <span class="doc-comment-inline">Sprint 8H: Delete a dead-letter marker (used when replay succeeds later).</span></li>
<li><code>_compute_binary_signatures_batch</code> (lancedb_store.py) — <span class="doc-comment-inline">MLX version for batched calculations.</span></li>
<li><code>__init__</code> (lancedb_store.py)</li>
<li><code>_check_ram_for_igraph</code> (graph_rag.py) — <span class="doc-comment-inline">M1 8GB: skip igraph if RAM headroom &lt; 500MB.</span></li>
<li><code>_calculate_average_path_length</code> (graph_rag.py) — <span class="doc-comment-inline">Calculate average shortest path length.</span></li>
<li><code>resize_index</code> (rag_engine.py)
<details><summary>Resize the index to accommodate more elements.</summary>
<div class="doc-comment">
<p>Resize the index to accommodate more elements.</p>
<p></p>
<p>Args:</p>
<p>new_max_elements: New maximum number of elements</p>
</div>
</details>
</li>
<li><code>initialize</code> (rag_engine.py) — <span class="doc-comment-inline">Inicializovat RAG engine</span></li>
<li><code>_truncate_to_bytes</code> (analyst_workbench.py)
<details><summary>Truncate text to max_bytes UTF-8.</summary>
<div class="doc-comment">
<p>Truncate text to max_bytes UTF-8.</p>
<p></p>
<p>Returns (truncated_text, actual_bytes).</p>
</div>
</details>
</li>
<li><code>_keyword_score</code> (analyst_workbench.py)
<details><summary>Score text by keyword overlap.</summary>
<div class="doc-comment">
<p>Score text by keyword overlap.</p>
<p></p>
<p>Returns score in [0.0, 1.0] based on keyword match ratio.</p>
</div>
</details>
</li>
<li><code>_inc_node_count</code> (hot_edges_cache.py) — <span class="doc-comment-inline">Atomically increment node count. Returns new count. Fails silently.</span></li>
<li><code>_dec_node_count</code> (hot_edges_cache.py) — <span class="doc-comment-inline">Atomically decrement node count. Returns new count. Fails silently.</span></li>
<li><code>rejection_rate</code> (quality_assessment.py)
<details><summary>Sprint F216G: Compute rejection rate across all quality gate decisions.</summary>
<div class="doc-comment">
<p>Sprint F216G: Compute rejection rate across all quality gate decisions.</p>
<p></p>
<p>Returns fraction of rejected findings [0.0, 1.0].</p>
<p>Returns 0.0 if no decisions have been recorded yet.</p>
</div>
</details>
</li>
<li><code>add_to_hot_cache</code> (quality_assessment.py) — <span class="doc-comment-inline">Add fingerprint → finding_id to hot cache with FIFO eviction.</span></li>
<li><code>reset_session</code> (graph_service.py)
<details><summary>Clear session-level idempotency trackers and graph singleton.</summary>
<div class="doc-comment">
<p>Clear session-level idempotency trackers and graph singleton.</p>
<p></p>
<p>Call at sprint start to prevent cross-sprint state leakage.</p>
<p>Resets only this instance's state — does NOT affect other instances.</p>
</div>
</details>
</li>
<li><code>set</code> (entity_linker.py) — <span class="doc-comment-inline">Cache value with timestamp.</span></li>
<li><code>process_entity</code> (entity_linker.py)</li>
<li><code>__init__</code> (lancedb_auto_tuner.py)</li>
<li><code>make_default_tuner</code> (lancedb_auto_tuner.py)
<details><summary>Construct an ``IVFPQAutoTuner`` with default settings.</summary>
<div class="doc-comment">
<p>Construct an ``IVFPQAutoTuner`` with default settings.</p>
<p></p>
<p>State path is ``&lt;state_dir&gt;/lancedb_autotune_&lt;table_name&gt;.json``. Pass</p>
<p>``state_dir=None`` to disable persistence (in-memory state only).</p>
</div>
</details>
</li>
<li><code>graph_stats</code> (ioc_graph.py) — <span class="doc-comment-inline">Return total node and edge counts.</span></li>
<li><code>init_ct_cache_schema</code> (db.py) — <span class="doc-comment-inline">Initialize CT log cache table in DuckDB.</span></li>
<li><code>get_graph_attachment_kind</code> (graph_attachment.py)
<details><summary>NON-AUTHORITATIVE DIAGNOSTIC: returns the class name of the attached graph.</summary>
<div class="doc-comment">
<p>NON-AUTHORITATIVE DIAGNOSTIC: returns the class name of the attached graph.</p>
<p></p>
<p>Returns None if no graph attached.</p>
<p>Use this to determine which backend is attached, then call</p>
<p>hasattr/hasattr for specific capability checks before use.</p>
<p></p>
<p>This is a COMPAT SEAM, not a canonical graph API.</p>
</div>
</details>
</li>
<li><code>initialize</code> (wal.py) — <span class="doc-comment-inline">Lazily initialize the WAL LMDB store.</span></li>
<li><code>_atexit_cleanup</code> (wal.py)
<details><summary>Emergency sync cleanup for atexit.register().</summary>
<div class="doc-comment">
<p>Emergency sync cleanup for atexit.register().</p>
<p></p>
<p>Called at interpreter shutdown as last resort for lock file release.</p>
<p>Uses sync close() since event loop is not available at atexit time.</p>
</div>
</details>
</li>
<li><code>get_stats</code> (ioc_dedup_adapter.py) — <span class="doc-comment-inline">Get current dedup statistics.</span></li>
<li><code>dynamic_schema</code> (duckdb_store.py)
<details><summary>Issue 4.3: Dynamic schema via msgspec.json.schema().</summary>
<div class="doc-comment">
<p>Issue 4.3: Dynamic schema via msgspec.json.schema().</p>
<p></p>
<p>Replaces SCHEMA_VERSION constants. At startup, validates that in-memory</p>
<p>CanonicalFinding shape matches the persisted DuckDB table schema.</p>
<p></p>
<p>Returns JSON schema dict for runtime validation.</p>
</div>
</details>
</li>
<li><code>_payload_to_envelope</code> (duckdb_store.py)
<details><summary>Sprint F202A §2: Deserialize FindingEnvelope from payload_text string.</summary>
<div class="doc-comment">
<p>Sprint F202A §2: Deserialize FindingEnvelope from payload_text string.</p>
<p></p>
<p>Fail-soft: returns None if payload_text is None/empty, parsing fails,</p>
<p>or required audit_reason field is missing.</p>
</div>
</details>
</li>
<li><code>_vacuum_sync</code> (duckdb_store.py) — <span class="doc-comment-inline">Execute VACUUM ANALYZE synchronously on worker thread.</span></li>
<li><code>_lookup_persistent_dedup</code> (duckdb_store.py)
<details><summary>Lookup a fingerprint in the persistent dedup LMDB.</summary>
<div class="doc-comment">
<p>Lookup a fingerprint in the persistent dedup LMDB.</p>
<p></p>
<p>DEPRECATED (Sprint F222): Delegates to DedupManager.lookup_persistent_dedup().</p>
<p>Kept for backward compat during migration - remove when all callers migrated.</p>
</div>
</details>
</li>
<li><code>_hot_cache_lookup</code> (duckdb_store.py)
<details><summary>Bounded hot cache lookup.</summary>
<div class="doc-comment">
<p>Bounded hot cache lookup.</p>
<p></p>
<p>DEPRECATED (Sprint F222): Delegates to DedupManager.hot_cache_lookup().</p>
<p>Kept for backward compat during migration - remove when all callers migrated.</p>
</div>
</details>
</li>
<li><code>_cosine_sim_batch</code> (lancedb_store.py) — <span class="doc-comment-inline">Numpy fallback for cosine similarity.</span></li>
<li><code>_detect_query_type</code> (lancedb_store.py) — <span class="doc-comment-inline">Decide whether to use FTS, hybrid, or pure vector search.</span></li>
<li><code>_cache_maintenance_loop</code> (lancedb_store.py) — <span class="doc-comment-inline">Background cache maintenance task.</span></li>
<li><code>_get_score_semaphore</code> (graph_rag.py) — <span class="doc-comment-inline">Lazy-init semaphore for bounded parallel scoring (M1 8GB safe).</span></li>
<li><code>get_statistics</code> (graph_rag.py)
<details><summary>Get GraphRAG orchestrator statistics.</summary>
<div class="doc-comment">
<p>Get GraphRAG orchestrator statistics.</p>
<p></p>
<p>Returns:</p>
<p>Dictionary with statistics</p>
</div>
</details>
</li>
<li><code>calculate_score</code> (graph_rag.py)</li>
<li><code>get_timestamp</code> (graph_rag.py) — <span class="doc-comment-inline">Extract timestamp from fact metadata.</span></li>
<li><code>get_timestamp</code> (graph_rag.py) — <span class="doc-comment-inline">Extract timestamp from fact metadata.</span></li>
<li><code>_compress_one</code> (rag_engine.py)</li>
<li><code>get_hnsw_stats</code> (rag_engine.py)
<details><summary>Get HNSW index statistics.</summary>
<div class="doc-comment">
<p>Get HNSW index statistics.</p>
<p></p>
<p>Returns:</p>
<p>Dictionary with index statistics, or None if index not built</p>
</div>
</details>
</li>
<li><code>_get_random_chunks</code> (rag_engine.py) — <span class="doc-comment-inline">Return up to n random text chunks from documents.</span></li>
<li><code>_get_node_count</code> (hot_edges_cache.py) — <span class="doc-comment-inline">Return current unique src_id count. Returns 0 on error/miss.</span></li>
<li><code>_encode_neighbors</code> (hot_edges_cache.py) — <span class="doc-comment-inline">Encode list[(dst_id, count)] → msgspec blob, optionally lz4/zstd compressed.</span></li>
<li><code>get_runtime_status</code> (dedup.py)
<details><summary>Return typed/cheap status surface for dedup subsystem.</summary>
<div class="doc-comment">
<p>Return typed/cheap status surface for dedup subsystem.</p>
<p></p>
<p>Args:</p>
<p>quality_state: QualityAssessmentState instance with _quality_duplicate_count,</p>
<p>_persistent_duplicate_count, _accepted_count, _quality_rejected_count,</p>
<p>_quality_fail_open_count.</p>
</div>
</details>
</li>
<li><code>reset_ann_index</code> (ann_index.py) — <span class="doc-comment-inline">Reset ANN index singleton (called on sprint teardown).</span></li>
<li><code>_load_gliner</code> (entity_linker.py) — <span class="doc-comment-inline">Lazy load GLiNER model.</span></li>
<li><code>close</code> (entity_linker.py) — <span class="doc-comment-inline">Close HTTP session and cleanup resources.</span></li>
<li><code>test</code> (entity_linker.py)</li>
<li><code>_upsert_ioc_sync</code> (ioc_graph.py) — <span class="doc-comment-inline">Synchronous upsert — runs on _executor thread.</span></li>
<li><code>get_truth_write_graph</code> (graph_attachment.py)
<details><summary>Sprint 8WA: Get injected truth-write graph for ACTIVE-phase consumers.</summary>
<div class="doc-comment">
<p>Sprint 8WA: Get injected truth-write graph for ACTIVE-phase consumers.</p>
<p></p>
<p>F320 UPDATE: Both DuckPGQGraph and IOCGraph are returned</p>
<p>(both have buffer_ioc/flush_buffers).</p>
<p></p>
<p>This is a CONSUMER-SPECIFIC seam for ACTIVE-phase buffered writes only.</p>
</div>
</details>
</li>
<li><code>wal_get_finding</code> (wal.py) — <span class="doc-comment-inline">Get a WAL truth record by finding_id.</span></li>
<li><code>wal_get_pending_marker</code> (wal.py) — <span class="doc-comment-inline">Get a single pending marker value by finding_id.</span></li>
<li><code>wal_put</code> (wal.py) — <span class="doc-comment-inline">Put a raw WAL entry.</span></li>
<li><code>wal_put_many</code> (wal.py) — <span class="doc-comment-inline">Put multiple raw WAL entries. Returns per-item success list.</span></li>
<li><code>_ensure_atexit</code> (wal.py)
<details><summary>Legacy: Register atexit cleanup if not already registered.</summary>
<div class="doc-comment">
<p>Legacy: Register atexit cleanup if not already registered.</p>
<p></p>
<p>Deprecated: Use _ensure_cleanup() instead (weakref.finalize).</p>
<p>Kept for backward compat.</p>
</div>
</details>
</li>
<li><code>_cleanup_on_shutdown</code> (wal.py)
<details><summary>E4: Cleanup callback for weakref.finalize -- called at interpreter shutdown.</summary>
<div class="doc-comment">
<p>E4: Cleanup callback for weakref.finalize -- called at interpreter shutdown.</p>
<p></p>
<p>Idempotent: safe even if close() was already called.</p>
</div>
</details>
</li>
<li><code>_key</code> (duckdb_store.py) — <span class="doc-comment-inline">Stable cache key: sha256(sql + "|" + json(params)).</span></li>
<li><code>inject_semantic_store</code> (duckdb_store.py)
<details><summary>Sprint 8SB: Inject SemanticStore instance for semantic buffering of findings.</summary>
<div class="doc-comment">
<p>Sprint 8SB: Inject SemanticStore instance for semantic buffering of findings.</p>
<p></p>
<p>The store is used to buffer findings for FastEmbed embedding + LanceDB</p>
<p>indexing during WINDUP flush.</p>
</div>
</details>
</li>
<li><code>_semantic_buffer_findings</code> (duckdb_store.py)
<details><summary>Sprint 8SB: Buffer findings into SemanticStore for batch embedding.</summary>
<div class="doc-comment">
<p>Sprint 8SB: Buffer findings into SemanticStore for batch embedding.</p>
<p></p>
<p>Runs as a background task (not awaited). Fail-open: any exception</p>
<p>is caught and logged - semantic buffering failure never blocks storage.</p>
<p>Delegated to SemanticStoreBuffer.</p>
</div>
</details>
</li>
<li><code>insert_shadow_finding</code> (duckdb_store.py) — <span class="doc-comment-inline">Sync insert - backward compat. For async use async_record_shadow_finding().</span></li>
<li><code>query_recent_findings</code> (duckdb_store.py) — <span class="doc-comment-inline">Sync query - backward compat. For async use async_query_recent_findings().</span></li>
<li><code>pending_marker_count</code> (duckdb_store.py)
<details><summary>Sprint 8L: Return the number of pending_duckdb_sync:* markers in WAL LMDB.</summary>
<div class="doc-comment">
<p>Sprint 8L: Return the number of pending_duckdb_sync:* markers in WAL LMDB.</p>
<p></p>
<p>Cheap O(n) prefix scan - bounded by REPLAY_CHUNK_SIZE scan.</p>
<p>Used for observability and benchmarking.</p>
</div>
</details>
</li>
<li><code>_batch_put</code> (lancedb_store.py)</li>
<li><code>_build_adjacency_list</code> (graph_rag.py) — <span class="doc-comment-inline">Build adjacency list for graph analysis.</span></li>
<li><code>get_stats</code> (rag_engine.py)
<details><summary>Get index statistics.</summary>
<div class="doc-comment">
<p>Get index statistics.</p>
<p></p>
<p>Returns:</p>
<p>Dictionary with index statistics</p>
</div>
</details>
</li>
<li><code>enable_hnsw</code> (rag_engine.py)
<details><summary>Enable or disable HNSW search.</summary>
<div class="doc-comment">
<p>Enable or disable HNSW search.</p>
<p></p>
<p>Args:</p>
<p>enable: True to enable HNSW, False to use brute-force</p>
</div>
</details>
</li>
<li><code>close</code> (rag_engine.py) — <span class="doc-comment-inline">Zavřít engine</span></li>
<li><code>__init__</code> (graph_service.py)</li>
<li><code>checkpoint</code> (graph_service.py) — <span class="doc-comment-inline">Flush WAL to disk. No-op on error.</span></li>
<li><code>_load_rust_bloom</code> (dedup.py) — <span class="doc-comment-inline">Lazy-load Rust MmapBloomFilter to avoid early import crash on M1.</span></li>
<li><code>_use_rust_rotate</code> (dedup.py) — <span class="doc-comment-inline">True if using Rust RotatingMmapBloomFilter (race-free).</span></li>
<li><code>_get_oldest_timestamp</code> (ann_index.py) — <span class="doc-comment-inline">Get timestamp of oldest entry.</span></li>
<li><code>buffer_observation</code> (ioc_graph.py)
<details><summary>Add observation to in-memory buffer — ZERO Kuzu I/O in ACTIVE phase.</summary>
<div class="doc-comment">
<p>Add observation to in-memory buffer — ZERO Kuzu I/O in ACTIVE phase.</p>
<p></p>
<p>After close() the buffer is closed: new writes are silently dropped.</p>
</div>
</details>
</li>
<li><code>initialize</code> (ioc_graph.py) — <span class="doc-comment-inline">Create schema if not exists (try/except for already-exists).</span></li>
<li><code>_record_observation_sync</code> (ioc_graph.py) — <span class="doc-comment-inline">Synchronous observation record — runs on _executor thread.</span></li>
<li><code>__init__</code> (db.py)</li>
<li><code>lmdb_put</code> (db.py) — <span class="doc-comment-inline">Put value into LMDB cache.</span></li>
<li><code>lmdb_delete</code> (db.py) — <span class="doc-comment-inline">Delete key from LMDB cache.</span></li>
<li><code>get_stix_graph</code> (graph_attachment.py)
<details><summary>Sprint 8VQ: Get injected STIX graph for synthesis consumers.</summary>
<div class="doc-comment">
<p>Sprint 8VQ: Get injected STIX graph for synthesis consumers.</p>
<p></p>
<p>F320 UPDATE: Both DuckPGQGraph and IOCGraph are returned (both have export_stix_bundle).</p>
<p></p>
<p>This is a CONSUMER-SPECIFIC seam, not a generic graph accessor.</p>
</div>
</details>
</li>
<li><code>_ensure_cleanup</code> (wal.py)
<details><summary>E4: Register weakref.finalize for guaranteed cleanup on interpreter shutdown.</summary>
<div class="doc-comment">
<p>E4: Register weakref.finalize for guaranteed cleanup on interpreter shutdown.</p>
<p></p>
<p>Replaces atexit.register() as primary safety net (Python 3.14+ refcounting</p>
<p>changes make __del__ non-deterministic). weakref.finalize is guaranteed to run.</p>
</div>
</details>
</li>
<li><code>__repr__</code> (duckdb_store.py)</li>
<li><code>_l1_set</code> (duckdb_store.py)</li>
<li><code>close</code> (duckdb_store.py)</li>
<li><code>_get_duckdb</code> (duckdb_store.py) — <span class="doc-comment-inline">Lazy import of duckdb - only loaded when sidecar is actually used.</span></li>
<li><code>get_quality_rejection_ledger</code> (duckdb_store.py)
<details><summary>Sprint F216G: Expose the quality rejection ledger to callers (e.g. scheduler).</summary>
<div class="doc-comment">
<p>Sprint F216G: Expose the quality rejection ledger to callers (e.g. scheduler).</p>
<p></p>
<p>Returns a tuple (immutable view) of all recorded rejection records.</p>
<p>Delegates to QualityAssessmentState for backward compat.</p>
</div>
</details>
</li>
<li><code>size_bytes</code> (duckdb_store.py) — <span class="doc-comment-inline">Return the database file size in bytes, or None for :memory: mode.</span></li>
<li><code>_init_semantic_dedup_cache</code> (duckdb_store.py)
<details><summary>Initialize semantic dedup cache.</summary>
<div class="doc-comment">
<p>Initialize semantic dedup cache.</p>
<p></p>
<p>DEPRECATED (Sprint F222): Semantic dedup is now initialized by DedupManager.initialize().</p>
<p>This stub exists only for backward compat - calls are no longer emitted.</p>
</div>
</details>
</li>
<li><code>_get_write_queue</code> (lancedb_store.py) — <span class="doc-comment-inline">Get or create the global write queue (singleton, async-safe).</span></li>
<li><code>_ensure_write_worker</code> (lancedb_store.py) — <span class="doc-comment-inline">Start the write worker if not already running (idempotent, thread-safe).</span></li>
<li><code>_embed_single</code> (lancedb_store.py) — <span class="doc-comment-inline">Embed text via MLX.</span></li>
<li><code>add_entity</code> (lancedb_store.py) — <span class="doc-comment-inline">Add entity to identity store. API-compatible with LanceDBIdentityStore.</span></li>
<li><code>get_academic_store</code> (lancedb_store.py) — <span class="doc-comment-inline">Get or create the singleton academic store (async-safe).</span></li>
<li><code>calculate_score</code> (graph_rag.py)</li>
<li><code>shutdown</code> (graph_rag.py)
<details><summary>Gracefully shutdown the orchestrator and release resources.</summary>
<div class="doc-comment">
<p>Gracefully shutdown the orchestrator and release resources.</p>
<p></p>
<p>R4.1: Thread pool no longer owned by this class — Rust rayon pools</p>
<p>(io_pool, cpu_pool) are process-level singletons managed by Rust.</p>
<p>No explicit shutdown needed from Python side.</p>
</div>
</details>
</li>
<li><code>update_ef_search</code> (rag_engine.py)
<details><summary>Update ef_search parameter for search quality/speed tradeoff.</summary>
<div class="doc-comment">
<p>Update ef_search parameter for search quality/speed tradeoff.</p>
<p></p>
<p>Args:</p>
<p>ef_search: New ef_search value (higher = better recall, slower)</p>
</div>
</details>
</li>
<li><code>_init_ultra_context</code> (rag_engine.py) — <span class="doc-comment-inline">Inicializovat InfiniteContextEngine</span></li>
<li><code>_init_spr_compressor</code> (rag_engine.py) — <span class="doc-comment-inline">Inicializovat SPR Compressor</span></li>
<li><code>upsert_relation</code> (graph_service.py)</li>
<li><code>initialize</code> (dedup.py)
<details><summary>Eager initialize — kept for backward compat, marks initialized.</summary>
<div class="doc-comment">
<p>Eager initialize — kept for backward compat, marks initialized.</p>
<p>All sub-systems are now lazy-initialized on first actual use.</p>
</div>
</details>
</li>
<li><code>_check_memory_guard</code> (ann_index.py) — <span class="doc-comment-inline">Return True if ANN init is safe (RSS below threshold).</span></li>
<li><code>_get_lmdb_env</code> (db.py) — <span class="doc-comment-inline">Lazy LMDB environment — single canonical LMDB env for cache/dedup.</span></li>
<li><code>lmdb_get</code> (db.py) — <span class="doc-comment-inline">Get value from LMDB cache.</span></li>
<li><code>apply_decay</code> (neuromorphic.py) — <span class="doc-comment-inline">Apply decay to all memory strengths.</span></li>
<li><code>contains</code> (ioc_dedup_adapter.py) — <span class="doc-comment-inline">Check if IOC exists in store (without affecting counters).</span></li>
<li><code>get_by_type</code> (ioc_dedup_adapter.py) — <span class="doc-comment-inline">Get all IOC values of specified type.</span></li>
<li><code>annotate_findings_with_graph_context</code> (duckdb_store.py)</li>
<li><code>_sync_insert_findings_bulk</code> (duckdb_store.py)
<details><summary>Sprint 7H: True bulk insert using executemany in explicit transaction.</summary>
<div class="doc-comment">
<p>Sprint 7H: True bulk insert using executemany in explicit transaction.</p>
<p>MUST be called on the worker thread.</p>
<p>Returns number of successfully inserted records.</p>
</div>
</details>
</li>
<li><code>_record_quality_rejection</code> (duckdb_store.py)
<details><summary>Sprint F216G: Record a quality gate rejection to the bounded ledger.</summary>
<div class="doc-comment">
<p>Sprint F216G: Record a quality gate rejection to the bounded ledger.</p>
<p></p>
<p>Delegates to QualityAssessmentState.record_rejection().</p>
</div>
</details>
</li>
<li><code>_sync_insert_findings_bulk_as_tuples</code> (duckdb_store.py)
<details><summary>Sprint 8R: Bulk insert using list[tuple] with 6 columns (id, query, source_type, confidence, ts, provenance_json).  # noqa: E501</summary>
<div class="doc-comment">
<p>Sprint 8R: Bulk insert using list[tuple] with 6 columns (id, query, source_type, confidence, ts, provenance_json).  # noqa: E501</p>
<p>MUST be called on the worker thread.</p>
<p>Returns number of successfully inserted records.</p>
</div>
</details>
</li>
<li><code>_lmdb_put</code> (lancedb_store.py) — <span class="doc-comment-inline">Synchronous LMDB put operation - zero-copy via orjson.</span></li>
<li><code>_delete_cached_embedding</code> (lancedb_store.py) — <span class="doc-comment-inline">Delete embedding from cache.</span></li>
<li><code>close</code> (lancedb_store.py) — <span class="doc-comment-inline">Close database connection.</span></li>
<li><code>_make_key</code> (hot_edges_cache.py)
<details><summary>Encode src node id as fixed-width LMDB key (16 hex chars = 8 bytes).</summary>
<div class="doc-comment">
<p>Encode src node id as fixed-width LMDB key (16 hex chars = 8 bytes).</p>
<p></p>
<p>Fixed-width keys are LMDB-friendly: lexical sort = numeric sort for</p>
<p>positive 64-bit integers. Enables cursor iteration in id order.</p>
</div>
</details>
</li>
<li><code>get_rejection_history</code> (quality_assessment.py)
<details><summary>Sprint F216G: Expose the quality rejection ledger to callers (e.g. scheduler).</summary>
<div class="doc-comment">
<p>Sprint F216G: Expose the quality rejection ledger to callers (e.g. scheduler).</p>
<p></p>
<p>Returns a tuple (immutable view) of all recorded rejection records.</p>
</div>
</details>
</li>
<li><code>record_rejection</code> (quality_assessment.py)</li>
<li><code>reset_counters</code> (quality_assessment.py) — <span class="doc-comment-inline">Reset all counters. Called on store reset.</span></li>
<li><code>upsert_ioc</code> (graph_service.py)</li>
<li><code>upsert_identity_edge</code> (graph_service.py)</li>
<li><code>persist</code> (dedup.py) — <span class="doc-comment-inline">Sync active filter to disk (msync handled by Rust).</span></li>
<li><code>_init_sync</code> (dedup.py) — <span class="doc-comment-inline">Synchronous init — runs in thread pool to avoid event-loop blocking.</span></li>
<li><code>resolve_one</code> (entity_linker.py)</li>
<li><code>_ema_recall</code> (lancedb_auto_tuner.py)
<details><summary>Exponential moving average of recall for trend detection.</summary>
<div class="doc-comment">
<p>Exponential moving average of recall for trend detection.</p>
<p></p>
<p>P0-2: Closed-loop PID — smooths noise in recall measurements so the</p>
<p>controller reacts to direction, not single noisy samples.</p>
</div>
</details>
</li>
<li><code>_close_sync</code> (ioc_graph.py)</li>
<li><code>_get_duckdb_store</code> (db.py) — <span class="doc-comment-inline">Lazy DuckDBShadowStore singleton — canonical store for all DuckDB data.</span></li>
<li><code>wal_delete</code> (wal.py) — <span class="doc-comment-inline">Delete a WAL entry by key.</span></li>
<li><code>wal_get</code> (wal.py) — <span class="doc-comment-inline">Get a raw WAL entry.</span></li>
<li><code>contains</code> (ioc_dedup_adapter.py) — <span class="doc-comment-inline">Check if IOC exists.</span></li>
<li><code>normalize_value</code> (ioc_dedup_adapter.py)
<details><summary>Normalize IOC value according to type rules (mirrors Rust normalize_ioc).</summary>
<div class="doc-comment">
<p>Normalize IOC value according to type rules (mirrors Rust normalize_ioc).</p>
<p></p>
<p>Useful for callers that need the normalized form without adding to store.</p>
</div>
</details>
</li>
<li><code>filter_time_range</code> (duckdb_store.py) — <span class="doc-comment-inline">Set time filter for row-group pruning. Returns self for chaining.</span></li>
<li><code>total_rows</code> (duckdb_store.py) — <span class="doc-comment-inline">Return total row count across all row-groups.</span></li>
<li><code>put</code> (duckdb_store.py)</li>
<li><code>_strip_comments</code> (duckdb_store.py) — <span class="doc-comment-inline">Remove -- and # line comments, then trailing triple-quote residue.</span></li>
<li><code>__aexit__</code> (duckdb_store.py)
<details><summary>Async context manager exit - cleans up the store.</summary>
<div class="doc-comment">
<p>Async context manager exit - cleans up the store.</p>
<p>Idempotent: safe to call even if already closed.</p>
</div>
</details>
</li>
<li><code>_get_uma_budget</code> (lancedb_store.py)</li>
<li><code>_get_embedding_manager</code> (lancedb_store.py) — <span class="doc-comment-inline">Lazily get MLX embedding manager.</span></li>
<li><code>_matches_filters</code> (rag_engine.py) — <span class="doc-comment-inline">Check if document matches filters.</span></li>
<li><code>_item_key</code> (rag_engine.py)</li>
<li><code>_is_rust_hot_edges_available</code> (hot_edges_cache.py) — <span class="doc-comment-inline">Check if Rust hot_edges is available at runtime.</span></li>
<li><code>_is_ioc_dedup_available</code> (graph_service.py) — <span class="doc-comment-inline">Check if Rust IOC dedup is available at runtime.</span></li>
<li><code>get_linker</code> (entity_linker.py) — <span class="doc-comment-inline">Get singleton EntityLinker instance.</span></li>
<li><code>_extract_one</code> (ioc_graph.py)</li>
<li><code>get_db</code> (db.py) — <span class="doc-comment-inline">Get the unified database facade singleton.</span></li>
<li><code>stats_dict</code> (ioc_dedup_adapter.py) — <span class="doc-comment-inline">SoA-style stats dict matching Rust stats_dict().</span></li>
<li><code>get_global_builder</code> (evidence_chain.py) — <span class="doc-comment-inline">Get or create the global EvidenceChainBuilder singleton.</span></li>
<li><code>get_all_chains</code> (evidence_chain.py) — <span class="doc-comment-inline">Return all chains from the global builder.</span></li>
<li><code>_rollback</code> (duckdb_store.py)</li>
<li><code>_do</code> (duckdb_store.py)</li>
<li><code>_do</code> (duckdb_store.py)</li>
<li><code>_do</code> (duckdb_store.py)</li>
<li><code>_qe</code> (duckdb_store.py) — <span class="doc-comment-inline">Lazy executor - created on first _sync_* access, shared for instance lifetime.</span></li>
<li><code>_sync_insert_run</code> (duckdb_store.py)</li>
<li><code>_finding_id_of</code> (duckdb_store.py) — <span class="doc-comment-inline">Extract finding_id from CanonicalFinding or dict, safely.</span></li>
<li><code>_ensure_replay_lock</code> (duckdb_store.py) — <span class="doc-comment-inline">Lazily initialize the replay lock on the current event loop.</span></li>
<li><code>_compute_binary_signature</code> (lancedb_store.py) — <span class="doc-comment-inline">64-bit binary signature - numpy packbits (faster for 64 elements).</span></li>
<li><code>__init__</code> (lancedb_store.py)</li>
<li><code>_close_instance</code> (dedup.py)</li>
<li><code>rotate</code> (dedup.py) — <span class="doc-comment-inline">Rotate: active becomes previous (read-only), new empty active.</span></li>
<li><code>close</code> (dedup.py) — <span class="doc-comment-inline">Close mmap filters and sync to disk.</span></li>
<li><code>_ensure_utc_aware</code> (entity_linker.py) — <span class="doc-comment-inline">Normalize datetime to UTC-aware (required for TTL comparisons in Python 3.14+).</span></li>
<li><code>_get_session</code> (entity_linker.py) — <span class="doc-comment-inline">Get or create httpx.AsyncClient session (F4XX).</span></li>
<li><code>__new__</code> (db.py)</li>
<li><code>duckdb</code> (db.py) — <span class="doc-comment-inline">DuckDBShadowStore singleton — canonical store for structured data.</span></li>
<li><code>lmdb</code> (db.py) — <span class="doc-comment-inline">LMDB environment for cache/dedup/KV operations.</span></li>
<li><code>__init__</code> (graph_attachment.py)</li>
<li><code>__init__</code> (ioc_dedup_adapter.py)</li>
<li><code>filter_source_types</code> (duckdb_store.py) — <span class="doc-comment-inline">Set source_type filter. None = no filter. Returns self for chaining.</span></li>
<li><code>_aiter</code> (duckdb_store.py)</li>
<li><code>__init__</code> (duckdb_store.py)</li>
<li><code>_get_node_content</code> (graph_rag.py) — <span class="doc-comment-inline">Get node content by ID.</span></li>
<li><code>_calculate_betweenness</code> (graph_rag.py) — <span class="doc-comment-inline">Calculate betweenness centrality via igraph C-core (50-100x faster).</span></li>
<li><code>_calculate_closeness</code> (graph_rag.py) — <span class="doc-comment-inline">Calculate closeness centrality via igraph C-core.</span></li>
<li><code>_calculate_eigenvector</code> (graph_rag.py) — <span class="doc-comment-inline">Calculate eigenvector centrality via igraph C-core.</span></li>
<li><code>_calculate_pagerank</code> (graph_rag.py) — <span class="doc-comment-inline">Calculate PageRank via igraph C-core.</span></li>
<li><code>_get_node_type</code> (graph_rag.py) — <span class="doc-comment-inline">Get node type for a node ID.</span></li>
<li><code>_is_complex_query</code> (rag_engine.py) — <span class="doc-comment-inline">Detekovat komplexní dotaz pro Tree of Thoughts</span></li>
<li><code>reset_session</code> (graph_service.py)</li>
<li><code>__init__</code> (dedup.py)</li>
<li><code>clear</code> (entity_linker.py) — <span class="doc-comment-inline">Clear all cached entries.</span></li>
<li><code>clear_cache</code> (entity_linker.py) — <span class="doc-comment-inline">Clear the query cache.</span></li>
<li><code>__init__</code> (neuromorphic.py)</li>
<li><code>decay</code> (neuromorphic.py) — <span class="doc-comment-inline">Apply exponential decay to memory strength.</span></li>
<li><code>cleanup</code> (neuromorphic.py) — <span class="doc-comment-inline">Aggressive cleanup for M1 memory constraints.</span></li>
<li><code>get_by_type</code> (ioc_dedup_adapter.py) — <span class="doc-comment-inline">Get all IOC values of specified type.</span></li>
<li><code>get_entries_by_type</code> (ioc_dedup_adapter.py) — <span class="doc-comment-inline">Get entries with full metadata.</span></li>
<li><code>to_bytes</code> (ioc_dedup_adapter.py) — <span class="doc-comment-inline">Serialize state to bytes (compatible with Rust get_state_bytes).</span></li>
<li><code>clear</code> (ioc_dedup_adapter.py)</li>
<li><code>add_step</code> (evidence_chain.py) — <span class="doc-comment-inline">Add a step to the chain. Silently drops if MAX_CHAIN_DEPTH reached.</span></li>
<li><code>set_global_builder</code> (evidence_chain.py) — <span class="doc-comment-inline">Set the global EvidenceChainBuilder (called at sprint teardown).</span></li>
<li><code>reset_global_builder</code> (evidence_chain.py) — <span class="doc-comment-inline">Reset the global builder (called at sprint start).</span></li>
<li><code>_dict_to_chain</code> (evidence_chain.py)</li>
<li><code>_get_TargetMemory</code> (duckdb_store.py) — <span class="doc-comment-inline">Lazy loader for TargetMemory.</span></li>
<li><code>_get_TargetMemoryUpdate</code> (duckdb_store.py) — <span class="doc-comment-inline">Lazy loader for TargetMemoryUpdate.</span></li>
<li><code>_is_quality_gate_available</code> (duckdb_store.py) — <span class="doc-comment-inline">Check if Rust quality gate is available at runtime.</span></li>
<li><code>_get_rust_build_arrow_batch</code> (duckdb_store.py) — <span class="doc-comment-inline">Lazy getter for Rust Arrow batch builder.</span></li>
<li><code>get_uma_state</code> (duckdb_store.py) — <span class="doc-comment-inline">Return currently configured UMA state.</span></li>
<li><code>inject_graph</code> (duckdb_store.py) — <span class="doc-comment-inline">DEPRECATED (Sprint F222): Delegates to GraphAttachmentStore.inject_graph().</span></li>
<li><code>get_graph_attachment_kind</code> (duckdb_store.py) — <span class="doc-comment-inline">DEPRECATED (Sprint F222): Delegates to GraphAttachmentStore.get_graph_attachment_kind().</span></li>
<li><code>graph_supports_buffered_writes</code> (duckdb_store.py) — <span class="doc-comment-inline">DEPRECATED (Sprint F222): Delegates to GraphAttachmentStore.graph_supports_buffered_writes().</span></li>
<li><code>inject_stix_graph</code> (duckdb_store.py) — <span class="doc-comment-inline">DEPRECATED (Sprint F222): Delegates to GraphAttachmentStore.inject_stix_graph().</span></li>
<li><code>get_stix_graph</code> (duckdb_store.py) — <span class="doc-comment-inline">DEPRECATED (Sprint F222): Delegates to GraphAttachmentStore.get_stix_graph().</span></li>
<li><code>inject_truth_write_graph</code> (duckdb_store.py) — <span class="doc-comment-inline">DEPRECATED (Sprint F222): Delegates to GraphAttachmentStore.inject_truth_write_graph().</span></li>
<li><code>get_truth_write_graph</code> (duckdb_store.py) — <span class="doc-comment-inline">DEPRECATED (Sprint F222): Delegates to GraphAttachmentStore.get_truth_write_graph().</span></li>
<li><code>truth_write_graph_supports_buffered_writes</code> (duckdb_store.py) — <span class="doc-comment-inline">DEPRECATED (Sprint F222): Delegates to GraphAttachmentStore.truth_write_graph_supports_buffered_writes().</span></li>
<li><code>get_top_seed_nodes</code> (duckdb_store.py) — <span class="doc-comment-inline">DEPRECATED (Sprint F222): Delegates to GraphAttachmentStore.get_top_seed_nodes().</span></li>
<li><code>get_graph_stats</code> (duckdb_store.py) — <span class="doc-comment-inline">DEPRECATED (Sprint F222): Delegates to GraphAttachmentStore.get_graph_stats().</span></li>
<li><code>get_connected_iocs</code> (duckdb_store.py) — <span class="doc-comment-inline">DEPRECATED (Sprint F222): Delegates to GraphAttachmentStore.get_connected_iocs().</span></li>
<li><code>get_connected_iocs_batch</code> (duckdb_store.py) — <span class="doc-comment-inline">DEPRECATED (Sprint F222): Delegates to GraphAttachmentStore.get_connected_iocs_batch().</span></li>
<li><code>get_analytics_graph_for_synthesis</code> (duckdb_store.py) — <span class="doc-comment-inline">DEPRECATED (Sprint F222): Delegates to GraphAttachmentStore.get_analytics_graph_for_synthesis().</span></li>
<li><code>get_top_entities_for_ghost_global</code> (duckdb_store.py) — <span class="doc-comment-inline">DEPRECATED (Sprint F222): Delegates to GraphAttachmentStore.get_top_entities_for_ghost_global().</span></li>
<li><code>_sync_query_findings</code> (duckdb_store.py) — <span class="doc-comment-inline">Sync query - MUST be called on the worker thread.</span></li>
<li><code>_sync_upsert_target_profile</code> (duckdb_store.py) — <span class="doc-comment-inline">Sync upsert - MUST be called on the worker thread.</span></li>
<li><code>_buffer_chunk</code> (duckdb_store.py)</li>
<li><code>is_initialized</code> (duckdb_store.py) — <span class="doc-comment-inline">Return True if sidecar was successfully initialized.</span></li>
<li><code>is_closed</code> (duckdb_store.py) — <span class="doc-comment-inline">Return True if sidecar has been shut down.</span></li>
<li><code>db_path</code> (duckdb_store.py) — <span class="doc-comment-inline">Return the database path (None for :memory: mode).</span></li>
<li><code>temp_dir</code> (duckdb_store.py) — <span class="doc-comment-inline">Return the temp directory path (None if not using RAMDISK).</span></li>
<li><code>memory_limit</code> (duckdb_store.py) — <span class="doc-comment-inline">Return the configured memory limit string.</span></li>
<li><code>max_temp</code> (duckdb_store.py) — <span class="doc-comment-inline">Return the configured max temp size string.</span></li>
<li><code>is_ramdisk_mode</code> (duckdb_store.py) — <span class="doc-comment-inline">Return True if running in RAMDISK-active mode.</span></li>
<li><code>executor</code> (duckdb_store.py) — <span class="doc-comment-inline">Return the internal executor (for test introspection).</span></li>
<li><code>startup_ready</code> (duckdb_store.py) — <span class="doc-comment-inline">Sprint 8L: True if boot barrier has been lifted (store accepts writes).</span></li>
<li><code>startup_replay_done</code> (duckdb_store.py) — <span class="doc-comment-inline">Sprint 8L: True if startup replay has run (regardless of outcome).</span></li>
<li><code>invariant_memory_limit</code> (duckdb_store.py) — <span class="doc-comment-inline">Return configured memory_limit string.</span></li>
<li><code>invariant_max_temp</code> (duckdb_store.py) — <span class="doc-comment-inline">Return configured max_temp_directory_size string.</span></li>
<li><code>invariant_temp_dir</code> (duckdb_store.py) — <span class="doc-comment-inline">Return configured temp_directory path (None if :memory: mode).</span></li>
<li><code>_dedup_key_from_fingerprint</code> (duckdb_store.py) — <span class="doc-comment-inline">Build dedup namespace key from BLAKE2b fingerprint.</span></li>
<li><code>_dedup_lmdb_key_to_fingerprint</code> (duckdb_store.py) — <span class="doc-comment-inline">Extract fingerprint from dedup namespace key.</span></li>
<li><code>initialize</code> (lancedb_store.py) — <span class="doc-comment-inline">Explicit init (optional). Stores are lazy inited on first use.</span></li>
<li><code>to_dict</code> (lancedb_store.py) — <span class="doc-comment-inline">Convert to dict for LanceDB storage.</span></li>
<li><code>_get_numpy</code> (graph_rag.py) — <span class="doc-comment-inline">Lazy getter for numpy with availability check.</span></li>
<li><code>score_with_semaphore</code> (graph_rag.py)</li>
<li><code>_get_all_node_ids</code> (graph_rag.py) — <span class="doc-comment-inline">Get all node IDs from knowledge layer.</span></li>
<li><code>_tokenize</code> (rag_engine.py) — <span class="doc-comment-inline">Simple tokenization</span></li>
<li><code>_bm25_build</code> (rag_engine.py)</li>
<li><code>_bm25_build</code> (rag_engine.py)</li>
<li><code>_build_evidence_pointer</code> (analyst_workbench.py) — <span class="doc-comment-inline">Build EvidencePointer from a finding dict.</span></li>
<li><code>_get_rust_backend</code> (hot_edges_cache.py) — <span class="doc-comment-inline">Lazy getter for Rust backend.</span></li>
<li><code>hot_cache_lookup</code> (quality_assessment.py) — <span class="doc-comment-inline">Look up fingerprint in hot cache. Returns finding_id or None.</span></li>
<li><code>get_rejection_history</code> (quality_assessment.py) — <span class="doc-comment-inline">Delegate to QualityAssessmentState.get_rejection_history().</span></li>
<li><code>increment_accepted</code> (quality_assessment.py) — <span class="doc-comment-inline">Increment accepted count when finding passes quality gate.</span></li>
<li><code>increment_fail_open</code> (quality_assessment.py) — <span class="doc-comment-inline">Increment fail-open counter when quality check raises.</span></li>
<li><code>_get_rust_backend</code> (graph_service.py) — <span class="doc-comment-inline">Lazy getter for Rust backend.</span></li>
<li><code>register_relationship_callback</code> (graph_service.py) — <span class="doc-comment-inline">Register callback for relationship events (src, dst, rel_type, weight).</span></li>
<li><code>add</code> (dedup.py)</li>
<li><code>__contains__</code> (dedup.py)</li>
<li><code>__len__</code> (dedup.py)</li>
<li><code>sync</code> (dedup.py) — <span class="doc-comment-inline">No-op for in-memory filter.</span></li>
<li><code>_dedup_key_from_fingerprint</code> (dedup.py) — <span class="doc-comment-inline">Build dedup namespace key from BLAKE2b fingerprint.</span></li>
<li><code>_dedup_lmdb_key_to_fingerprint</code> (dedup.py) — <span class="doc-comment-inline">Extract fingerprint from dedup namespace key.</span></li>
<li><code>_hot_cache_max</code> (dedup.py) — <span class="doc-comment-inline">Hot cache max size from config.</span></li>
<li><code>hot_cache_lookup</code> (dedup.py) — <span class="doc-comment-inline">Bounded hot cache lookup.</span></li>
<li><code>semantic_dedup_cache</code> (dedup.py) — <span class="doc-comment-inline">Return the semantic dedup cache instance.</span></li>
<li><code>to_dict</code> (entity_linker.py) — <span class="doc-comment-inline">Convert to dictionary for serialization.</span></li>
<li><code>from_dict</code> (entity_linker.py) — <span class="doc-comment-inline">Create from dictionary.</span></li>
<li><code>to_dict</code> (entity_linker.py) — <span class="doc-comment-inline">Convert to dictionary for serialization.</span></li>
<li><code>from_dict</code> (entity_linker.py) — <span class="doc-comment-inline">Create from dictionary.</span></li>
<li><code>_generate_key</code> (entity_linker.py) — <span class="doc-comment-inline">Generate cache key from query.</span></li>
<li><code>get_stats</code> (entity_linker.py) — <span class="doc-comment-inline">Get cache statistics.</span></li>
<li><code>_init_ner_patterns</code> (entity_linker.py) — <span class="doc-comment-inline">Initialize regex patterns for fallback NER.</span></li>
<li><code>get_cache_stats</code> (entity_linker.py) — <span class="doc-comment-inline">Get cache statistics.</span></li>
<li><code>__aenter__</code> (entity_linker.py) — <span class="doc-comment-inline">Async context manager entry.</span></li>
<li><code>__aexit__</code> (entity_linker.py) — <span class="doc-comment-inline">Async context manager exit.</span></li>
<li><code>changed</code> (lancedb_auto_tuner.py) — <span class="doc-comment-inline">True iff the partition count was actually modified.</span></li>
<li><code>table_name</code> (lancedb_auto_tuner.py) — <span class="doc-comment-inline">Public accessor for the table name (read-only).</span></li>
<li><code>vector_column</code> (lancedb_auto_tuner.py) — <span class="doc-comment-inline">Public accessor for the vector column name (read-only).</span></li>
<li><code>key_column</code> (lancedb_auto_tuner.py) — <span class="doc-comment-inline">Public accessor for the key column name (read-only).</span></li>
<li><code>enabled</code> (lancedb_auto_tuner.py) — <span class="doc-comment-inline">Auto-tune gate. Independent of F264D ``HLEDAC_LANCEDB_QUANTIZE``.</span></li>
<li><code>state</code> (lancedb_auto_tuner.py) — <span class="doc-comment-inline">Current persistent state (read-only snapshot).</span></li>
<li><code>num_sub_vectors</code> (lancedb_auto_tuner.py) — <span class="doc-comment-inline">Configured sub-vector count (immutable for tuner lifetime).</span></li>
<li><code>_make_ioc_id</code> (ioc_graph.py) — <span class="doc-comment-inline">Generate a deterministic 64-bit hex ID for an IOC.</span></li>
<li><code>_record_observation_batch_sync_async</code> (ioc_graph.py) — <span class="doc-comment-inline">Async wrapper — runs sync impl on background thread via asyncio.to_thread.</span></li>
<li><code>rust_pool_ready</code> (db.py) — <span class="doc-comment-inline">Check if Rust connection pool is available.</span></li>
<li><code>lancedb_available</code> (db.py) — <span class="doc-comment-inline">LanceDB is deprecated — returns False.</span></li>
<li><code>sqlite3_available</code> (db.py) — <span class="doc-comment-inline">SQLite3 for caching is deprecated — use DuckDB or LMDB.</span></li>
<li><code>duckdb_store</code> (db.py) — <span class="doc-comment-inline">Get DuckDB store singleton.</span></li>
<li><code>lmdb_env</code> (db.py) — <span class="doc-comment-inline">Get LMDB environment singleton.</span></li>
<li><code>lmdb</code> (wal.py) — <span class="doc-comment-inline">Return the WAL LMDB store (may be None if using unified store).</span></li>
<li><code>unified_store</code> (wal.py) — <span class="doc-comment-inline">Return the unified store if using unified mode.</span></li>
<li><code>_key_finding</code> (wal.py) — <span class="doc-comment-inline">Build finding key.</span></li>
<li><code>_key_pending_sync</code> (wal.py) — <span class="doc-comment-inline">Build pending sync marker key.</span></li>
<li><code>_key_deadletter</code> (wal.py) — <span class="doc-comment-inline">Build deadletter key.</span></li>
<li><code>reinforce</code> (neuromorphic.py) — <span class="doc-comment-inline">Reinforce memory strength (capped at 1.0).</span></li>
<li><code>add_batch</code> (ioc_dedup_adapter.py) — <span class="doc-comment-inline">Batch add — returns list of bool (True = new).</span></li>
<li><code>advance_sprint</code> (ioc_dedup_adapter.py) — <span class="doc-comment-inline">Advance to next sprint.</span></li>
<li><code>stats</code> (ioc_dedup_adapter.py) — <span class="doc-comment-inline">Returns (total_seen, total_deduped, unique_count).</span></li>
<li><code>current_sprint</code> (ioc_dedup_adapter.py) — <span class="doc-comment-inline">Get current sprint ID.</span></li>
<li><code>flush</code> (ioc_dedup_adapter.py) — <span class="doc-comment-inline">Explicitly flush state to LMDB (called during sprint winddown).</span></li>
<li><code>depth</code> (evidence_chain.py) — <span class="doc-comment-inline">Number of steps in the chain.</span></li>
<li><code>is_empty</code> (evidence_chain.py) — <span class="doc-comment-inline">True if chain has no steps.</span></li>
<li><code>__init__</code> (evidence_chain.py)</li>
<li><code>record_ingest</code> (evidence_chain.py) — <span class="doc-comment-inline">Convenience: record the ingest step for a root finding.</span></li>
<li><code>record_identity</code> (evidence_chain.py) — <span class="doc-comment-inline">Convenience: record an identity stitching step.</span></li>
<li><code>record_attribution</code> (evidence_chain.py) — <span class="doc-comment-inline">Convenience: record an attribution scoring step.</span></li>
<li><code>record_exposure</code> (evidence_chain.py) — <span class="doc-comment-inline">Convenience: record an exposure correlation step.</span></li>
<li><code>record_leak</code> (evidence_chain.py) — <span class="doc-comment-inline">Convenience: record a leak sentinel step.</span></li>
<li><code>record_temporal</code> (evidence_chain.py) — <span class="doc-comment-inline">Convenience: record a temporal archaeology step.</span></li>
<li><code>record_diff</code> (evidence_chain.py) — <span class="doc-comment-inline">Convenience: record a sprint diff step.</span></li>
<li><code>record_killchain</code> (evidence_chain.py) — <span class="doc-comment-inline">Convenience: record a kill chain tagging step.</span></li>
<li><code>record_evidence_triage</code> (evidence_chain.py) — <span class="doc-comment-inline">Convenience: record an evidence triage step.</span></li>
<li><code>record_pivot</code> (evidence_chain.py) — <span class="doc-comment-inline">Convenience: record a pivot planning step.</span></li>
<li><code>build</code> (evidence_chain.py) — <span class="doc-comment-inline">Return the chain for root_finding_id, or None if not tracked.</span></li>
<li><code>build_all</code> (evidence_chain.py) — <span class="doc-comment-inline">Return all chains, newest-first by root_finding_id sort.</span></li>
<li><code>get_chain_count</code> (evidence_chain.py) — <span class="doc-comment-inline">Number of chains currently tracked.</span></li>
<li><code>get_total_steps</code> (evidence_chain.py) — <span class="doc-comment-inline">Total steps recorded across all chains.</span></li>
<li><code>_ORJSON_DECODER</code> (duckdb_store.py)</li>
<li><code>_get_rust_assess_quality_batch</code> (duckdb_store.py)</li>
<li><code>_get_rust_batch_ioc_extract</code> (duckdb_store.py)</li>
<li><code>_get_rust_batch_ioc_extract_python</code> (duckdb_store.py)</li>
<li><code>_get_parquet_get_metadata</code> (duckdb_store.py)</li>
<li><code>_get_parquet_row_group_stats</code> (duckdb_store.py)</li>
<li><code>_get_parquet_read_row_group_ipc</code> (duckdb_store.py)</li>
<li><code>_get_parquet_iter_all_row_groups</code> (duckdb_store.py)</li>
<li><code>_get_parquet_read_table</code> (duckdb_store.py)</li>
<li><code>__len__</code> (duckdb_store.py)</li>
<li><code>_accepted_count</code> (duckdb_store.py)</li>
<li><code>_quality_duplicate_count</code> (duckdb_store.py)</li>
<li><code>_quality_rejected_count</code> (duckdb_store.py)</li>
<li><code>_persistent_duplicate_count</code> (duckdb_store.py)</li>
<li><code>_begin</code> (duckdb_store.py)</li>
<li><code>_commit</code> (duckdb_store.py)</li>
<li><code>_sync_iter_wrapper</code> (duckdb_store.py)</li>
<li><code>_graph_update_coro</code> (duckdb_store.py)</li>
<li><code>_train</code> (lancedb_store.py)</li>
<li><code>get_entity_id</code> (graph_rag.py)</li>
<li><code>__hash__</code> (rag_engine.py)</li>
<li><code>to_dict</code> (rag_engine.py)</li>
<li><code>_cluster_sort</code> (analyst_workbench.py)</li>
<li><code>_url_engine_available</code> (quality_assessment.py)</li>
<li><code>_quality_gate_rust_available</code> (quality_assessment.py)</li>
<li><code>_quality_gate_batch_available</code> (quality_assessment.py)</li>
<li><code>upsert_ioc_batch</code> (graph_service.py)</li>
<li><code>find_entity_history</code> (graph_service.py)</li>
<li><code>find_connected_batch</code> (graph_service.py)</li>
<li><code>graph_stats</code> (graph_service.py)</li>
<li><code>checkpoint</code> (graph_service.py)</li>
<li><code>graph_analytics_summary</code> (graph_service.py)</li>
<li><code>metric</code> (lancedb_auto_tuner.py)</li>
<li><code>limit</code> (lancedb_auto_tuner.py)</li>
<li><code>to_list</code> (lancedb_auto_tuner.py)</li>
<li><code>to_pandas</code> (lancedb_auto_tuner.py)</li>
<li><code>count_rows</code> (lancedb_auto_tuner.py)</li>
<li><code>search</code> (lancedb_auto_tuner.py)</li>
<li><code>create_index</code> (lancedb_auto_tuner.py)</li>
<li><code>to_polars</code> (lancedb_auto_tuner.py)</li>
<li><code>batch_encode</code> (semantic_store.py)</li>
<li><code>single_encode</code> (semantic_store.py)</li>
<li><code>__len__</code> (ioc_dedup_adapter.py)</li>
<li><code>is_empty</code> (ioc_dedup_adapter.py)</li>
<li><code>get_sprint</code> (ioc_dedup_adapter.py)</li>
<li><code>len</code> (ioc_dedup_adapter.py)</li>
<li><code>is_empty</code> (ioc_dedup_adapter.py)</li>
<li><code>_chain_to_dict</code> (evidence_chain.py)</li>
</ul>
</details>

<details><summary><strong>Class</strong> (67)</summary>
<ul>
<li><code>DuckDBShadowStore</code> (duckdb_store.py)</li>
<li><code>GraphRAGOrchestrator</code> (graph_rag.py)
<details><summary>GraphRAG orchestrator for multi-hop reasoning.</summary>
<div class="doc-comment">
<p>GraphRAG orchestrator for multi-hop reasoning.</p>
<p></p>
<p>ROLE: Consumer/Orchestrator (NOT backend owner)</p>
<p>================================================</p>
<p>- multi-hop graph traversal (consumer přes knowledge_layer)</p>
<p>- NENÍ owner backend storage → persistent_layer (deprecated!)</p>
<p>- NENÍ owner embedding → MLXEmbeddingManager singleton přes _get_embedder()</p>
<p>- NENÍ owner primary retrieval → rag_engine</p>
<p></p>
<p>Performs multi-hop search over knowledge graph to find</p>
<p>relationships that aren't visible in single documents.</p>
</div>
</details>
</li>
<li><code>LanceDBIdentityStore</code> (lancedb_store.py)
<details><summary>Identity store using LanceDB for entity resolution.</summary>
<div class="doc-comment">
<p>Identity store using LanceDB for entity resolution.</p>
<p></p>
<p>ROLE: Identity/Entity Store (NOT grounding authority)</p>
<p>====================================================</p>
<p>- entity identity storage (add_entity, search_similar)</p>
<p>- NENÍ owner context grounding → rag_engine</p>
<p>- NENÍ owner document retrieval → rag_engine HNSWVectorIndex</p>
<p>- Embedding policy: MLXEmbeddingManager singleton přes _mlx_embed_manager</p>
<p>- Thermal awareness coupling: volá self._orch._memory_mgr (optional, debt)</p>
<p></p>
<p>Features:</p>
<p>- Hybrid search (vector + FTS)</p>
<p>- Bounded storage</p>
<p>- MLX acceleration for similarity computation</p>
<p>- Fail-safe degradation</p>
<p>- Sprint 76: LMDB embedding cache with float16 quantization (50% RAM savings)</p>
<p>- Sprint 76: Binary embeddings for fast pre-filter (32x compression)</p>
<p>- Sprint 76: MMR diversity filtering</p>
<p>- Sprint 76: Adaptive reranking (ColBERT/FlashRank/MLX)</p>
<p>- Sprint 76: usearch index support (lazy)</p>
</div>
</details>
</li>
<li><code>AnalystWorkbench</code> (analyst_workbench.py)
<details><summary>Read-side analyst facade over local findings, graph, and vectors.</summary>
<div class="doc-comment">
<p>Read-side analyst facade over local findings, graph, and vectors.</p>
<p></p>
<p>Bounds (fixed, not configurable):</p>
<p>- MAX_CONTEXT_BYTES = 8192</p>
<p>- MAX_TOP_K = 20</p>
<p>- MAX_GRAPH_HOPS = 2</p>
<p>- MAX_EVIDENCE_PTRS = 5</p>
<p>- MAX_RELATED_ENTITIES = 10</p>
<p></p>
<p>Thread-safe: all async methods delegate to duckdb_worker via run_in_executor.</p>
<p></p>
<p>NO external network calls.</p>
<p>NO LLM required (extractive fallback always available).</p>
<p>Model lifecycle via brain.model_lifecycle only.</p>
</div>
</details>
</li>
<li><code>RAGEngine</code> (rag_engine.py)
<details><summary>RAG engine s Ultra Context a SPR kompresí.</summary>
<div class="doc-comment">
<p>RAG engine s Ultra Context a SPR kompresí.</p>
<p></p>
<p>ROLE: Grounding Authority (NOT identity/entity store)</p>
<p>=====================================================</p>
<p>- context grounding (hybrid_retrieve, HNSWVectorIndex, RAPTOR)</p>
<p>- NENÍ owner identity/entity resolution → lancedb_store</p>
<p>- NENÍ owner embedding cache → MLXEmbeddingManager singleton</p>
<p>- Embedding policy: _fastembed_embedder (cached per-instance), fallback → MLXEmbeddingManager</p>
<p></p>
<p>Features:</p>
<p>- 6-stupňový pipeline: Query → Retrieval → Rerank → Compress → Generate → Validate</p>
<p>- Ultra Context pro 50+ chunků</p>
<p>- SPR Compression (50% redukce)</p>
<p>- Secure Enclave pro citlivá data</p>
<p>- Automatic ToT detection</p>
<p>- HNSW Vector Search for fast approximate nearest neighbor search</p>
</div>
</details>
</li>
<li><code>GraphService</code> (graph_service.py)
<details><summary>Instance-isolated graph service with DuckPGQGraph backing.</summary>
<div class="doc-comment">
<p>Instance-isolated graph service with DuckPGQGraph backing.</p>
<p></p>
<p>Instance state:</p>
<p>- _seen_iocs: idempotency set for IOCs (owned by instance)</p>
<p>- _seen_rels: idempotency set for relations (owned by instance)</p>
<p></p>
<p>The DuckPGQGraph backend is NOT stored on the instance — instance methods and</p>
<p>module-level functions alike call module-level _get_graph() for the shared</p>
<p>module-level singleton. This means patching graph_service._get_graph affects</p>
<p>all callers uniformly, which is the intended test isolation mechanism.</p>
<p></p>
<p>Use this class directly for test isolation or cross-sprint tenant isolation.</p>
</div>
</details>
</li>
<li><code>_ANNIndex</code> (ann_index.py)
<details><summary>Hybrid ANN index: USEARCH (primary, Metal SIMD) + LanceDB (persistence).</summary>
<div class="doc-comment">
<p>Hybrid ANN index: USEARCH (primary, Metal SIMD) + LanceDB (persistence).</p>
<p></p>
<p>FAIL-SOFT: init errors stored in _boot_error, ann_search() returns []</p>
<p>when unavailable. Safe to call from any thread.</p>
<p></p>
<p>Architecture:</p>
<p>- USEARCH: in-memory ANN with M1 Metal acceleration (primary search path)</p>
<p>- LanceDB: persistent storage with IVF-PQ compression (cross-session)</p>
<p>- MLX: exact cosine re-ranking on GPU after ANN candidate retrieval</p>
</div>
</details>
</li>
<li><code>SemanticStore</code> (semantic_store.py)
<details><summary>FastEmbed + LanceDB pro sémantické vyhledávání findings.</summary>
<div class="doc-comment">
<p>FastEmbed + LanceDB pro sémantické vyhledávání findings.</p>
<p></p>
<p>ANE path (F228B): CoreMLEmbedder.embed() → CoreML → ANE (preferred)</p>
<p>CPU fallback: self._model.embed() — FastEmbed TextEmbedding</p>
<p>Hash fallback: always works.</p>
<p></p>
<p>Lifecycle:</p>
<p>await store.initialize()  # BOOT — load model + open LanceDB</p>
<p>store.add_text(...)        # Buffer (sync, no I/O)</p>
<p>await store.flush()        # Batch embed + LanceDB upsert</p>
<p>await store.semantic_pivot(...)  # ANN search</p>
<p>await store.close()        # TEARDOWN</p>
</div>
</details>
</li>
<li><code>DedupManager</code> (dedup.py)
<details><summary>Owns dedup storage lifecycle for DuckDBShadowStore.</summary>
<div class="doc-comment">
<p>Owns dedup storage lifecycle for DuckDBShadowStore.</p>
<p></p>
<p>Responsible for:</p>
<p>- Persistent LMDB dedup at LMDB_ROOT/dedup.lmdb (cross-source dedup)</p>
<p>- Bounded hot cache (in-process fingerprint → finding_id)</p>
<p>- Semantic dedup cache (embedding-based near-duplicate, optional)</p>
</div>
</details>
</li>
<li><code>QualityAssessor</code> (quality_assessment.py)
<details><summary>Sprint 8W + 8AG + 8AK + F216G: Quality gate delegate.</summary>
<div class="doc-comment">
<p>Sprint 8W + 8AG + 8AK + F216G: Quality gate delegate.</p>
<p></p>
<p>Encapsulates quality decision logic (entropy check, dedup, URL-first fingerprint).</p>
<p>Delegates to DuckDBShadowStore for LMDB persistence and semantic dedup cache.</p>
<p></p>
<p>DuckDBShadowStore holds this as an attribute and calls it from</p>
<p>async_ingest_findings_batch() to keep canonical write path clean.</p>
</div>
</details>
</li>
<li><code>GraphAttachmentStore</code> (graph_attachment.py)
<details><summary>Owns graph injection lifecycle and graph-read seams for DuckDBShadowStore.</summary>
<div class="doc-comment">
<p>Owns graph injection lifecycle and graph-read seams for DuckDBShadowStore.</p>
<p></p>
<p>Provides 3 independent slots (each may be None independently):</p>
<p>- _ioc_graph: analytics/donor graph (DuckPGQGraph or IOCGraph)</p>
<p>- _stix_graph: STIX synthesis graph (DuckPGQGraph or IOCGraph)</p>
<p>- _truth_write_graph: ACTIVE-phase buffered write graph (DuckPGQGraph or IOCGraph)</p>
<p></p>
<p>All read seams are fail-open: errors return empty collections, not exceptions.</p>
</div>
</details>
</li>
<li><code>WALManager</code> (wal.py)
<details><summary>Owns LMDB WAL lifecycle for DuckDBShadowStore.</summary>
<div class="doc-comment">
<p>Owns LMDB WAL lifecycle for DuckDBShadowStore.</p>
<p></p>
<p>Responsible for:</p>
<p>- WAL truth records (finding:{id})</p>
<p>- Pending-sync recovery markers (pending_duckdb_sync:{id})</p>
<p>- Dead-letter namespace (deadletter_ingest:{id})</p>
<p>- Eviction of oldest pending markers (bounded by MAX_PENDING_SYNC_MARKERS)</p>
<p></p>
<p>F272: Supports UnifiedLMDBStore via HLEDAC_WAL_UNIFIED=1 (default).</p>
<p>Uses separate LMDB file when HLEDAC_WAL_UNIFIED=0.</p>
</div>
</details>
</li>
<li><code>IOCGraph</code> (ioc_graph.py)
<details><summary>Kuzu-backed IOC entity graph with async-safe operations.</summary>
<div class="doc-comment">
<p>Kuzu-backed IOC entity graph with async-safe operations.</p>
<p></p>
<p>GRAPH TRUTH STORE — owns authoritative IOC entity storage.</p>
<p>- buffer_ioc(), flush_buffers(), upsert_ioc_batch(), export_stix_bundle(), pivot()</p>
<p>- NOT analytics backend — DuckPGQGraph serves that role.</p>
</div>
</details>
</li>
<li><code>IVFPQAutoTuner</code> (lancedb_auto_tuner.py)
<details><summary>Adaptive IVF-PQ index tuner.</summary>
<div class="doc-comment">
<p>Adaptive IVF-PQ index tuner.</p>
<p></p>
<p>Lifecycle::</p>
<p></p>
<p>tuner = IVFPQAutoTuner(table_name="entities", state_path=Path("..."))</p>
<p># after each insert batch in the consumer:</p>
<p>result = tuner.tune_if_due(table, current_partitions=N, inserts_delta=1)</p>
<p>if result.changed():</p>
<p>consumer._ivfpq_num_partitions = result.new_partitions</p>
</div>
</details>
</li>
<li><code>_DuckDBQueryExecutor</code> (duckdb_store.py)
<details><summary>Private SQL construction and execution engine for DuckDBShadowStore.</summary>
<div class="doc-comment">
<p>Private SQL construction and execution engine for DuckDBShadowStore.</p>
<p></p>
<p>NOT part of the public API - exists solely to concentrate SQL string</p>
<p>templates and transaction patterns that were previously copy-pasted</p>
<p>across 38 _sync_* methods.</p>
<p></p>
<p>Design:</p>
<p>- All SQL templates are class-level string constants</p>
<p>- Transaction framing (_begin/_commit/_rollback) is shared</p>
<p>- Connection routing (MODE A file conn vs MODE B persistent conn) is shared</p>
<p>- Arrow-&gt;dict conversion helpers are shared</p>
</div>
</details>
</li>
<li><code>EntityLinker</code> (entity_linker.py)
<details><summary>Wikidata-based entity linker with context-aware disambiguation.</summary>
<div class="doc-comment">
<p>Wikidata-based entity linker with context-aware disambiguation.</p>
<p></p>
<p>Optimized for M1 8GB RAM:</p>
<p>- Async HTTP requests (non-blocking)</p>
<p>- Response caching (reduces API calls)</p>
<p>- Batch processing support</p>
<p>- No heavy ML models (uses lightweight similarity)</p>
<p></p>
<p>Usage:</p>
<p>linker = EntityLinker()</p>
<p>entities = await linker.link_entities("Apple was founded by Steve Jobs")</p>
</div>
</details>
</li>
<li><code>NeuromorphicMemoryManager</code> (neuromorphic.py)
<details><summary>STDP-based neuromorphic memory with zone transitions.</summary>
<div class="doc-comment">
<p>STDP-based neuromorphic memory with zone transitions.</p>
<p></p>
<p>Features:</p>
<p>- Spike-timing-dependent plasticity (STDP) for synaptic weight updates</p>
<p>- Three-zone system: working, long-term, episodic</p>
<p>- Optional sparse synaptic weight matrix (scipy.sparse)</p>
<p>- Lazy numpy / scipy.sparse imports (only when actually initialized)</p>
<p></p>
<p>Thread-safe: uses a threading.Lock for all public methods.</p>
<p></p>
<p>Memory budget (M1 8GB):</p>
<p>- 512 neurons × 512 neurons × 0.03 connectivity × 8 bytes = ~60 KB</p>
<p>- Plus pattern storage (bounded at MAX_PATTERNS=2000)</p>
</div>
</details>
</li>
<li><code>HNSWVectorIndex</code> (rag_engine.py)
<details><summary>USearch-based Vector Index for fast approximate nearest neighbor search.</summary>
<div class="doc-comment">
<p>USearch-based Vector Index for fast approximate nearest neighbor search.</p>
<p></p>
<p>Uses usearch for C++ optimized HNSW with Metal SIMD (M1 accelerated):</p>
<p>- &lt;1ms search latency for 100K vectors</p>
<p>- ~100MB memory per 100K 768-dim vectors</p>
<p>- Dynamic index updates</p>
<p>- Persistent storage support</p>
<p></p>
<p>M1 8GB Optimized:</p>
<p>- Configurable max_elements to control memory usage</p>
<p>- Efficient C++ backend with Metal SIMD</p>
<p>- Brute-force fallback when index unavailable</p>
</div>
</details>
</li>
<li><code>UnifiedDatabaseFacade</code> (db.py)
<details><summary>Single entry point for all database operations.</summary>
<div class="doc-comment">
<p>Single entry point for all database operations.</p>
<p></p>
<p>DESIGN PRINCIPLES:</p>
<p>1. DuckDB for structured analytics, canonical facts, FTS, vectors</p>
<p>2. LMDB for cache, dedup, ephemeral KV</p>
<p>3. Rust connection pool for async queries (when available)</p>
<p>4. Arrow IPC for bulk zero-copy operations</p>
<p>5. Fail-soft throughout — errors never crash the pipeline</p>
<p></p>
<p>MIGRATION PHASES:</p>
<p>- Phase 1: This facade + LanceDB deprecation</p>
<p>- Phase 2: SQLite3 → DuckDB migration</p>
<p>- Phase 3: Centralized import consolidation</p>
</div>
</details>
</li>
<li><code>ParquetHistoryReader</code> (duckdb_store.py)
<details><summary>Lazy paginated parquet reader for IOC history — enables 100 GB+ reads without OOM.</summary>
<div class="doc-comment">
<p>Lazy paginated parquet reader for IOC history — enables 100 GB+ reads without OOM.</p>
<p></p>
<p>M1 8GB safe: reads one row-group at a time (max 100_000 rows per batch).</p>
<p>Zero-copy: Arrow IPC bytes → pa.ipc.open_record_batch() → Polars zero-copy.</p>
<p></p>
<p>F320+: Filter pushdown via row-group statistics (ts min/max per RG).</p>
<p>F320+: Polars LazyFrame integration for efficient filtering.</p>
<p></p>
<p>Usage:</p>
<p>reader = ParquetHistoryReader("/path/to/history.parquet")</p>
<p></p>
<p># Filter pushdown by time range (skips irrelevant row-groups)</p>
<p>reader.filter_time_range(min_ts=1700000000.0, max_ts=1701000000.0)</p>
<p>reader.filter_source_types(["dark_web", "leak"])</p>
<p></p>
<p># Streaming iteration</p>
<p>for batch in reader.iter_batches(batch_size=50_000):</p>
<p>df = pl.from_arrow(batch)  # zero-copy</p>
<p>process(df)</p>
<p></p>
<p># Or as Polars LazyFrame (full filter pipeline)</p>
<p>lf = reader.to_polars_lazy()</p>
<p>filtered = lf.filter(pl.col("source_type") == "dark_web").collect()</p>
<p></p>
<p>Fallback: if Rust parquet_reader unavailable, falls back to pure PyArrow.</p>
</div>
</details>
</li>
<li><code>IocDedupAdapter</code> (ioc_dedup_adapter.py)
<details><summary>Cross-sprint IOC deduplication with type-aware normalization.</summary>
<div class="doc-comment">
<p>Cross-sprint IOC deduplication with type-aware normalization.</p>
<p></p>
<p>Wraps Rust IocDedupStore when available; falls back to pure Python.</p>
<p></p>
<p>Integration point: called in async_ingest_findings_batch() after</p>
<p>IOC extraction (extract_iocs_from_texts) and BEFORE quality gate</p>
<p>BLAKE2b dedup. This ensures IOC values are normalized consistently.</p>
<p></p>
<p>M1 8GB: Rust AHashMap cap=50k ≈ 5-8 MB; Python fallback uses</p>
<p>dict with same cap, slightly more memory but bounded.</p>
<p></p>
<p>PERSISTENCE: State persisted to LMDB on every advance_sprint() call.</p>
<p>Load happens lazily on first add() after init or after process restart.</p>
</div>
</details>
</li>
<li><code>LanceDBAcademicStore</code> (lancedb_store.py)
<details><summary>Semantic search over academic papers discovered during research.</summary>
<div class="doc-comment">
<p>Semantic search over academic papers discovered during research.</p>
<p></p>
<p>Sprint F259: Canonical storage for academic papers from all adapters.</p>
<p>Uses FastEmbed BAAI/bge-small-en-v1.5 (384d, 33MB) for M1 memory efficiency.</p>
<p></p>
<p>Schema:</p>
<p>- paper_id: unique identifier</p>
<p>- title: paper title</p>
<p>- abstract: paper abstract</p>
<p>- authors: list of author names</p>
<p>- year: publication year</p>
<p>- source: adapter source (arxiv/s2orc/openalex/core/unpaywall)</p>
<p>- doi: DOI string</p>
<p>- url: paper URL</p>
<p>- citation_count: number of citations</p>
<p>- embedding: 384d FastEmbed vector</p>
</div>
</details>
</li>
<li><code>RotatingBloomFilter</code> (dedup.py)
<details><summary>Cross-run URL dedup pre-check. Sprint F222F, F266-U1, F288+, P1-10.</summary>
<div class="doc-comment">
<p>Cross-run URL dedup pre-check. Sprint F222F, F266-U1, F288+, P1-10.</p>
<p></p>
<p>Two-generation bloom filter using Rust RotatingMmapBloomFilter:</p>
<p>- active: current generation, being written to</p>
<p>- previous: previous generation, read-only for lookups</p>
<p></p>
<p>When active reaches capacity, rotate: active becomes previous, new active created.</p>
<p>This prevents unbounded memory growth while maintaining dedup across many runs.</p>
<p></p>
<p>Uses Rust RotatingMmapBloomFilter via PyO3 FFI — xxHash3-64 hashing (NEON-SIMD</p>
<p>on M1, 3-5× faster than prior blake2b), mmap-backed file persistence (no LMDB</p>
<p>overhead), cross-restart persistence with zero warm-up cost.</p>
<p></p>
<p>P1-10 invariants:</p>
<p>- Always-on: no feature flag, no env var toggle</p>
<p>- Bounded: capacity hard-capped, rotation prevents unbounded growth</p>
<p>- Fail-safe: any error returns default (allow), never crashes sprint</p>
<p>- M1 8GB safe: mmap working set bounded by access pattern</p>
<p>- Race-free init: fcntl.flock prevents concurrent init race on mmap files</p>
<p>- Lazy init: filter created on first add/contains, not in __init__</p>
<p>- Single rust import: one try/except block, no redundant imports</p>
<p>- __slots__: memory-efficient, no __dict__ per instance</p>
</div>
</details>
</li>
<li><code>_DuckDBQueryCache</code> (duckdb_store.py)
<details><summary>Two-tier bounded query cache for DuckDB read queries.</summary>
<div class="doc-comment">
<p>Two-tier bounded query cache for DuckDB read queries.</p>
<p></p>
<p>L1 — in-memory LRU (500 entries, TTL 300s): sub-millisecond hit.</p>
<p>L2 — LMDB (5000 entries, TTL 300s, 16 MB map): persistent across</p>
<p>process restarts but still bounded by TTL eviction.</p>
<p></p>
<p>Cache invalidation on schema migration:</p>
<p>_invalidate_on_migration() is called by DuckDBShadowStore._apply_schema_migrations()</p>
<p>so that cached results from old schemas are never served after ALTER.</p>
<p></p>
<p>Opt-in via HLEDAC_DUCKDB_QUERY_CACHE=1 (default OFF).</p>
<p>Always-on, bounded, fail-safe invariants:</p>
<p>- Any error on hit path returns None (cache miss, no exception)</p>
<p>- Any error on write path is silently swallowed</p>
<p>- LMDB write failures do not propagate</p>
<p>- :memory: DuckDB mode bypasses L2 (no persistence path available)</p>
<p>- TTL-based LRU eviction keeps memory bounded</p>
</div>
</details>
</li>
<li><code>QualityAssessmentState</code> (quality_assessment.py)
<details><summary>Sprint F216G: Quality counters and rejection ledger state.</summary>
<div class="doc-comment">
<p>Sprint F216G: Quality counters and rejection ledger state.</p>
<p></p>
<p>Kept separate from DuckDBShadowStore so quality state is independently</p>
<p>testable and can be inspected without accessing the full store.</p>
</div>
</details>
</li>
<li><code>SqliteVecIdentityStore</code> (lancedb_store.py)
<details><summary>M1-native entity identity store using sqlite-vec.</summary>
<div class="doc-comment">
<p>M1-native entity identity store using sqlite-vec.</p>
<p></p>
<p>ROLE: Identity/Entity Store — replaces LanceDBIdentityStore on M1 8GB.</p>
<p>Provides add_entity() + search_similar() for entity stitching</p>
<p>and entity-aware RAG fallback.</p>
<p></p>
<p>Key differences from LanceDBIdentityStore:</p>
<p>- Zero-process: no LanceDB subprocess (~200MB RAM saved)</p>
<p>- In-process ANN via sqlite-vec SQLite extension (~5MB overhead)</p>
<p>- Shared db path: uses same sprint_{id}.db as DuckDBShadowStore</p>
<p>- No IVF-PQ auto-tune (not available in sqlite-vec)</p>
<p>- No binary signature pre-filter (sqlite-vec limitation)</p>
<p></p>
<p>API contract matches LanceDBIdentityStore:</p>
<p>- add_entity(entity_id, embedding, aliases) -&gt; bool</p>
<p>- search_similar(embedding, text_hint, threshold, limit, query_type) -&gt; list[dict]</p>
<p>- search_similar_adaptive(query_text, query_emb, top_k) -&gt; list[dict]</p>
<p>- _embed_single(text) -&gt; list[float] (internal)</p>
</div>
</details>
</li>
<li><code>EvidenceChainBuilder</code> (evidence_chain.py)
<details><summary>Accumulates chain steps from sidecar runs into EvidenceChain objects.</summary>
<div class="doc-comment">
<p>Accumulates chain steps from sidecar runs into EvidenceChain objects.</p>
<p></p>
<p>Usage:</p>
<p>builder = EvidenceChainBuilder()</p>
<p>builder.record_step(root_finding_id, STEP_TYPE_IDENTITY, ["f1", "f2"], "f3-id", 0.85, "linked via email+username")  # noqa: E501</p>
<p>chain = builder.build(root_finding_id)</p>
</div>
</details>
</li>
<li><code>IocDedupStorePythonFallback</code> (ioc_dedup_adapter.py)
<details><summary>Pure-Python IocDedupStore fallback.</summary>
<div class="doc-comment">
<p>Pure-Python IocDedupStore fallback.</p>
<p>Mirrors Rust IocDedupStore API for environments without compiled extension.</p>
</div>
</details>
</li>
<li><code>BM25Index</code> (rag_engine.py) — <span class="doc-comment-inline">Simple BM25 implementation for sparse retrieval</span></li>
<li><code>SimpleCache</code> (entity_linker.py)
<details><summary>Simple in-memory cache with TTL for Wikidata responses.</summary>
<div class="doc-comment">
<p>Simple in-memory cache with TTL for Wikidata responses.</p>
<p>M1 8GB optimized - limited size with LRU eviction.</p>
</div>
</details>
</li>
<li><code>CanonicalFinding</code> (duckdb_store.py)
<details><summary>Sprint 8P: Canonical internal finding DTO.</summary>
<div class="doc-comment">
<p>Sprint 8P: Canonical internal finding DTO.</p>
<p></p>
<p>Minimální povinná pole:</p>
<p>- finding_id: str       - unique identifier</p>
<p>- query: str             - research query text</p>
<p>- source_type: str       - source type (e.g., "web", "document", "synthetic")</p>
<p>- confidence: float       - confidence score [0.0, 1.0]</p>
<p>- ts: float              - Unix timestamp</p>
<p>- provenance: tuple[str, ...] - tvrdý invariant, nesmí být None, default = ()</p>
<p></p>
<p>Volitelná pole:</p>
<p>- payload_text: str | None - supplementary text payload</p>
<p></p>
<p>DTO invariants:</p>
<p>- frozen=True  - immutabilní instance</p>
<p>-      - zakázán garbage collector tracking (výkon)</p>
<p>- msgspec.Struct - zero-copy decode/encode</p>
<p></p>
<p>NOTE 8Q/8R: CanonicalFinding je používán napříč celým projektem jako univerzální</p>
<p>typ pro všechny findingy. Přesun do sdíleného DTO modulu by vyžadoval</p>
<p>extra import cyklus break (storage → DTO → callers). Aktuálně jeadržován</p>
<p>in-process přes async_ingest_findings_batch(), což je dostatečné.</p>
</div>
</details>
</li>
<li><code>AnalystBrief</code> (analyst_workbench.py)
<details><summary>Sprint F204E: Analyst brief produced at sprint teardown.</summary>
<div class="doc-comment">
<p>Sprint F204E: Analyst brief produced at sprint teardown.</p>
<p>F225B: Added source_family_summary, evidence_gaps, risk_hypotheses,</p>
<p>feed_cluster_summary, pivot_recommendations fields.</p>
<p>F225C: Added corroboration_summary field.</p>
<p>F226E: Added target_memory_feedback field.</p>
<p></p>
<p>A model-free summary of sprint results: what changed, strongest evidence,</p>
<p>next best pivots, and open questions.</p>
<p></p>
<p>Fields:</p>
<p>sprint_id: Sprint identifier</p>
<p>target_id: Research target (query or target_id)</p>
<p>headline: One-line sprint summary</p>
<p>key_findings: Tuple of key finding strings (max MAX_BRIEF_FINDINGS)</p>
<p>evidence_chain_ids: Tuple of evidence chain IDs (max MAX_BRIEF_CHAINS)</p>
<p>next_actions: Tuple of suggested next action strings (max MAX_BRIEF_NEXT_ACTIONS)</p>
<p>open_questions: Tuple of open question strings</p>
<p>confidence: Confidence score [0.0, 1.0]</p>
<p>generated_ts: Unix timestamp of generation</p>
<p>corroboration_summary: F225C cross-source corroboration strings</p>
<p>source_family_summary: F225B source family presence summary</p>
<p>evidence_gaps: F225B evidence gap strings</p>
<p>risk_hypotheses: F225B bounded risk hypotheses (max 5)</p>
<p>feed_cluster_summary: F225B feed/public/CT cluster presence</p>
<p>pivot_recommendations: F225B pivot recommendations (max 5)</p>
</div>
</details>
</li>
<li><code>EntityCandidate</code> (entity_linker.py)
<details><summary>Represents a candidate entity from Wikidata.</summary>
<div class="doc-comment">
<p>Represents a candidate entity from Wikidata.</p>
<p></p>
<p>Attributes:</p>
<p>entity_text: The original text that was matched</p>
<p>wikidata_id: Wikidata Q-ID (e.g., "Q312" for Apple Inc.)</p>
<p>label: Canonical label from Wikidata</p>
<p>description: Entity description from Wikidata</p>
<p>types: List of entity types (P31 instance of)</p>
<p>context_score: Semantic similarity to context (0-1)</p>
<p>popularity_score: Popularity based on sitelinks (0-1)</p>
<p>final_score: Combined ranking score (0-1)</p>
</div>
</details>
</li>
<li><code>LinkedEntity</code> (entity_linker.py)
<details><summary>Represents a successfully linked entity.</summary>
<div class="doc-comment">
<p>Represents a successfully linked entity.</p>
<p></p>
<p>Attributes:</p>
<p>original_text: Text as it appeared in the input</p>
<p>start_pos: Start position in the original text</p>
<p>end_pos: End position in the original text</p>
<p>canonical_id: Wikidata Q-ID</p>
<p>canonical_label: Canonical label from Wikidata</p>
<p>entity_type: Entity type/category</p>
<p>confidence: Linking confidence score (0-1)</p>
<p>candidates_considered: Number of candidates evaluated</p>
</div>
</details>
</li>
<li><code>_InMemFilter</code> (dedup.py) — <span class="doc-comment-inline">In-memory two-generation bloom filter (Python fallback).</span></li>
<li><code>EvidenceChain</code> (evidence_chain.py)
<details><summary>Complete reasoning chain for a root finding.</summary>
<div class="doc-comment">
<p>Complete reasoning chain for a root finding.</p>
<p></p>
<p>Fields:</p>
<p>root_finding_id:  The original raw finding that started this chain.</p>
<p>steps:            Ordered list of ChainStep from root to conclusion.</p>
<p>steps[0].output_id == root_finding_id.</p>
<p>conclusion:       Optional human-readable summary of the chain's conclusion,</p>
<p>or None if chain ends at a derived finding with no conclusion.</p>
</div>
</details>
</li>
<li><code>MemoryPattern</code> (neuromorphic.py)
<details><summary>A memory pattern stored in neuromorphic memory.</summary>
<div class="doc-comment">
<p>A memory pattern stored in neuromorphic memory.</p>
<p></p>
<p>Attributes:</p>
<p>pattern_id: Unique identifier for the pattern</p>
<p>neuron_activations: Sparse array of neuron activation values</p>
<p>timestamp: Creation time</p>
<p>strength: Memory strength (0.0 to 1.0)</p>
<p>metadata: Additional pattern metadata</p>
</div>
</details>
</li>
<li><code>ReplayResult</code> (duckdb_store.py)
<details><summary>Sprint F300: msgspec.Struct for pending-sync replay operations.</summary>
<div class="doc-comment">
<p>Sprint F300: msgspec.Struct for pending-sync replay operations.</p>
<p></p>
<p>Fields:</p>
<p>finding_id:           Unique identifier of the finding</p>
<p>marker_found:         True if pending marker existed before replay attempt</p>
<p>wal_truth_found:      True if finding:{id} WAL truth was found in LMDB</p>
<p>duckdb_written:       True if DuckDB write succeeded during replay</p>
<p>marker_cleared:       True if pending marker was cleared after success</p>
<p>read_back_verified:   True if fresh read-back confirmed the DuckDB record</p>
<p>deadlettered:         True if marker was moved to dead-letter namespace</p>
<p>retry_count:          Number of retry attempts made</p>
<p>error:                Error message if there was an exception, None otherwise</p>
</div>
</details>
</li>
<li><code>AnalystAnswer</code> (analyst_workbench.py)
<details><summary>Complete analyst answer with evidence.</summary>
<div class="doc-comment">
<p>Complete analyst answer with evidence.</p>
<p></p>
<p>Fields:</p>
<p>question: The original analyst question</p>
<p>extractive_answer: Deterministic extractive text answer (no model required)</p>
<p>llm_answer: Optional LLM-generated answer (None if no model used)</p>
<p>evidence_pointers: List of EvidencePointer (max MAX_EVIDENCE_PTRS)</p>
<p>related_entities: List of RelatedEntity (max MAX_RELATED_ENTITIES)</p>
<p>context_bytes: Actual bytes used for extractive answer</p>
<p>model_used: True if LLM was used for this answer</p>
<p>sources_used: List of source types consulted</p>
<p>timing_ms: Total time in milliseconds</p>
</div>
</details>
</li>
<li><code>ActivationResult</code> (duckdb_store.py)
<details><summary>Sprint F300: msgspec.Struct for activation record operations.</summary>
<div class="doc-comment">
<p>Sprint F300: msgspec.Struct for activation record operations.</p>
<p></p>
<p>Fields:</p>
<p>finding_id:     Unique identifier of the finding</p>
<p>lmdb_success:   True if LMDB WAL write succeeded</p>
<p>duckdb_success: True if DuckDB write succeeded, False if it failed,</p>
<p>None if not yet attempted</p>
<p>lmdb_key:       "finding:{id}" - LMDB key used</p>
<p>desync:         True if LMDB OK but DuckDB FAIL (WAL-DuckDB desync)</p>
<p>error:          Error message if there was an exception, None otherwise</p>
<p>accepted:       True when finding passed quality gate and was stored</p>
</div>
</details>
</li>
<li><code>RAGConfig</code> (rag_engine.py) — <span class="doc-comment-inline">Konfigurace pro RAG — Sprint F330: env var defaults consistent with knowledge/ pattern.</span></li>
<li><code>EvidencePointer</code> (analyst_workbench.py)
<details><summary>Evidence pointer for an analyst answer.</summary>
<div class="doc-comment">
<p>Evidence pointer for an analyst answer.</p>
<p></p>
<p>Fields:</p>
<p>finding_id: Unique identifier of the source finding</p>
<p>source_type: Source type (e.g., "ct_log", "document", "deep_probe")</p>
<p>query: Research query that produced this finding</p>
<p>confidence: Confidence score [0.0, 1.0]</p>
<p>ts: Unix timestamp of the finding</p>
<p>provenance: Provenance chain tuple</p>
<p>envelope_available: True if finding has evidence envelope</p>
<p>snippet: Text snippet extracted from payload_text (None if no envelope)</p>
</div>
</details>
</li>
<li><code>AcademicPaper</code> (lancedb_store.py) — <span class="doc-comment-inline">Academic paper with metadata for LanceDB storage.</span></li>
<li><code>QualityRejectionRecord</code> (quality_assessment.py)
<details><summary>Sprint F216G: Bounded per-finding quality gate rejection record.</summary>
<div class="doc-comment">
<p>Sprint F216G: Bounded per-finding quality gate rejection record.</p>
<p></p>
<p>Records individual quality gate rejections for CanonicalFinding ingest,</p>
<p>grouped by source_family and reason. Used to diagnose accepted=0</p>
<p>without changing quality/dedup/storage behavior.</p>
<p></p>
<p>Fields:</p>
<p>source_family: source_type of the finding (e.g., "ct", "public", "wayback")</p>
<p>reason:         FindingQualityDecision.reason (e.g., "low_entropy_rejected",</p>
<p>"persistent_duplicate", "semantic_duplicate")</p>
<p>finding_id:     Bounded sample: first 40 chars of finding_id</p>
<p>url_sample:      Bounded sample: provenance URL if available, else query (max 200 chars)</p>
</div>
</details>
</li>
<li><code>ChainStep</code> (evidence_chain.py)
<details><summary>Single step in an evidence chain.</summary>
<div class="doc-comment">
<p>Single step in an evidence chain.</p>
<p></p>
<p>Fields:</p>
<p>step_type:   Semantic label for the processing step that produced this step.</p>
<p>Values: finding_ingest | identity_stitching | exposure_correlation |</p>
<p>leak_sentinel | temporal_archaeology | sprint_diff |</p>
<p>kill_chain_tagging | evidence_triage | attribution_scoring |</p>
<p>pivot_planning</p>
<p>input_ids:   List of finding_id strings that fed into this step.</p>
<p>output_id:   Single finding_id produced by this step.</p>
<p>confidence:  Confidence score [0.0, 1.0] for this step's output.</p>
<p>reason:      Human-readable explanation of WHY this step produced its output.</p>
</div>
</details>
</li>
<li><code>_TableLike</code> (lancedb_auto_tuner.py)
<details><summary>Structural type for the LanceDB table interface used by the tuner.</summary>
<div class="doc-comment">
<p>Structural type for the LanceDB table interface used by the tuner.</p>
<p></p>
<p>Both ``lancedb.table.Table`` and the test-mock objects satisfy this via</p>
<p>duck-typing. Only the methods the tuner actually calls are listed.</p>
</div>
</details>
</li>
<li><code>FindingQualityDecision</code> (duckdb_store.py)
<details><summary>Sprint 8W: Quality decision contract for CanonicalFinding ingest.</summary>
<div class="doc-comment">
<p>Sprint 8W: Quality decision contract for CanonicalFinding ingest.</p>
<p></p>
<p>Fields:</p>
<p>accepted:        True if finding passed quality gate</p>
<p>reason:          Human-readable reason for reject/accept, or None</p>
<p>entropy:         Computed entropy in bits per character</p>
<p>normalized_hash: BLAKE2b fingerprint of normalized text (hex, 32 chars)</p>
<p>duplicate:       True if exact-content duplicate detected</p>
</div>
</details>
</li>
<li><code>TuneResult</code> (lancedb_auto_tuner.py) — <span class="doc-comment-inline">Outcome of a single auto-tune attempt (immutable, log-friendly).</span></li>
<li><code>_QueryBuilder</code> (lancedb_auto_tuner.py)
<details><summary>Structural type for the LanceDB query builder returned by ``Table.search()``.</summary>
<div class="doc-comment">
<p>Structural type for the LanceDB query builder returned by ``Table.search()``.</p>
<p></p>
<p>Supports method chaining: ``table.search(q).metric("cosine").limit(K).to_list()``.</p>
</div>
</details>
</li>
<li><code>RelatedEntity</code> (analyst_workbench.py)
<details><summary>Related entity from graph traversal.</summary>
<div class="doc-comment">
<p>Related entity from graph traversal.</p>
<p></p>
<p>Fields:</p>
<p>entity_value: The entity IOC value (e.g., domain, IP, email)</p>
<p>entity_type: IOC type (e.g., "domain", "ipv4", "email")</p>
<p>confidence: Entity confidence score [0.0, 1.0]</p>
<p>hops: Distance in hops from the source entity</p>
<p>relation_types: Set of relation types connecting to this entity</p>
</div>
</details>
</li>
<li><code>RaptorNode</code> (rag_engine.py) — <span class="doc-comment-inline">Single node in RAPTOR summarization tree.</span></li>
<li><code>TuneState</code> (lancedb_auto_tuner.py) — <span class="doc-comment-inline">Persistent state — JSON-serialized to ``state_path`` for cross-session.</span></li>
<li><code>CentralityScores</code> (graph_rag.py) — <span class="doc-comment-inline">Centrality analysis results for a node.</span></li>
<li><code>GraphContradiction</code> (graph_rag.py) — <span class="doc-comment-inline">Contradiction detected in the graph.</span></li>
<li><code>Document</code> (rag_engine.py) — <span class="doc-comment-inline">Document for retrieval</span></li>
<li><code>STDPParameters</code> (neuromorphic.py) — <span class="doc-comment-inline">STDP (Spike-Timing-Dependent Plasticity) parameters.</span></li>
<li><code>_IocEntryPython</code> (ioc_dedup_adapter.py) — <span class="doc-comment-inline">Python fallback entry matching ioc_dedup.rs::IocEntry.</span></li>
<li><code>_DuckDBQueryExecutor</code> (duckdb_store.py) — <span class="doc-comment-inline">Stubs for dynamic attributes set via object.__setattr__ in _DuckDBQueryExecutor.</span></li>
<li><code>Community</code> (graph_rag.py) — <span class="doc-comment-inline">Detected community in the graph.</span></li>
<li><code>RetrievedChunk</code> (rag_engine.py) — <span class="doc-comment-inline">Retrieved document chunk with scores</span></li>
<li><code>IocDedupStats</code> (ioc_dedup_adapter.py) — <span class="doc-comment-inline">Stats snapshot from IocDedupAdapter.</span></li>
<li><code>TargetProfileSummary</code> (duckdb_store.py)</li>
<li><code>QueryResult</code> (db.py) — <span class="doc-comment-inline">Generic query result.</span></li>
<li><code>NeuromorphicMemoryZone</code> (neuromorphic.py) — <span class="doc-comment-inline">Memory zones for neuromorphic memory with STDP transitions.</span></li>
<li><code>_DuckDBShadowStore</code> (duckdb_store.py) — <span class="doc-comment-inline">Stubs for dynamic attributes set via object.__setattr__ in DuckDBShadowStore.</span></li>
<li><code>DBCoordinates</code> (db.py) — <span class="doc-comment-inline">Coordinates for a database operation.</span></li>
<li><code>GraphBackendUnavailableError</code> (ioc_graph.py) — <span class="doc-comment-inline">Raised when a required graph backend (kuzu) is not installed.</span></li>
</ul>
</details>

<details><summary><strong>Method</strong> (779)</summary>
<ul>
<li><code>async_ingest_findings_batch</code> (duckdb_store.py)</li>
<li><code>_assess_finding_quality_batch</code> (duckdb_store.py)
<details><summary>Sprint P1-2: Batch quality gate — rayon-parallel via Rust batch_* APIs.</summary>
<div class="doc-comment">
<p>Sprint P1-2: Batch quality gate — rayon-parallel via Rust batch_* APIs.</p>
<p></p>
<p>P1-07: Added IOC-level dedup — extracted IOCs are checked against</p>
<p>Rust MmapIocDedupStore before the finding is accepted.</p>
<p></p>
<p>ISSUE-022: Tries assess_findings_quality_batch() Rust fast path first —</p>
<p>pure-compute decisions (URL fp, normalize, entropy, dedup fp) in a single</p>
<p>rayon pass. Stateful checks (hot_cache, LMDB, semantic dedup) run in Python</p>
<p>after Rust returns.</p>
<p></p>
<p>Falls back to the full per-finding loop if Rust is unavailable or fails.</p>
<p></p>
<p>Bounded: caller should chunk at 4096 max (Rust BATCH_HARD_CAP).</p>
<p>Returns list[FindingQualityDecision] in same order as findings.</p>
<p>Fail-soft: any exception propagates to caller for per-row fallback.</p>
</div>
</details>
</li>
<li><code>_apply_stateful_quality_checks</code> (duckdb_store.py)</li>
<li><code>assess_batch</code> (quality_assessment.py)</li>
<li><code>assess</code> (quality_assessment.py)
<details><summary>Sprint 8W + 8AG + 8AK: Assess a single finding's quality via entropy + dedup.</summary>
<div class="doc-comment">
<p>Sprint 8W + 8AG + 8AK: Assess a single finding's quality via entropy + dedup.</p>
<p></p>
<p>Sprint 8AK: URL-first fingerprint — if a canonical URL is present in</p>
<p>provenance, use it (normalized) as the primary dedup signal, independent</p>
<p>of source_type or payload position. Falls back to payload_text.</p>
<p></p>
<p>Sprint 8AG §6.17: Persistent dedup via LMDB with hot-cache read-through.</p>
<p>Lookup order: hot cache → persistent LMDB → store if miss.</p>
<p>LMDB is the authority; hot cache is a bounded read-through cache.</p>
<p></p>
<p>Returns FindingQualityDecision (frozen, immutable).</p>
<p>Fail-open: any exception → accept with reason="quality_check_error".</p>
<p></p>
<p>Text mapping: URL (if present) or payload_text (if exists and non-empty), else query.</p>
<p>If both are empty, falls back to query (may accept trivially).</p>
</div>
</details>
</li>
<li><code>_assess_finding_quality</code> (duckdb_store.py)
<details><summary>Sprint 8W + 8AG + 8AK: Assess a single finding's quality via entropy + dedup.</summary>
<div class="doc-comment">
<p>Sprint 8W + 8AG + 8AK: Assess a single finding's quality via entropy + dedup.</p>
<p></p>
<p>Sprint 8AK: URL-first fingerprint - if a canonical URL is present in</p>
<p>provenance, use it (normalized) as the primary dedup signal, independent</p>
<p>of source_type or payload position. Falls back to payload_text.</p>
<p></p>
<p>Sprint 8AG §6.17: Persistent dedup via LMDB with hot-cache read-through.</p>
<p>Lookup order: hot cache -&gt; persistent LMDB -&gt; store if miss.</p>
<p>LMDB is the authority; hot cache is a bounded read-through cache.</p>
<p></p>
<p>Returns FindingQualityDecision (frozen, immutable).</p>
<p>Fail-open: any exception -&gt; accept with reason="quality_check_error".</p>
<p></p>
<p>Text mapping: URL (if present) or payload_text (if exists and non-empty), else query.</p>
<p>If both are empty, falls back to query (may accept trivially).</p>
</div>
</details>
</li>
<li><code>build_sprint_brief</code> (analyst_workbench.py)
<details><summary>F204E: Build a model-free analyst brief at sprint teardown.</summary>
<div class="doc-comment">
<p>F204E: Build a model-free analyst brief at sprint teardown.</p>
<p></p>
<p>Generates a summary of sprint results: what changed, strongest evidence,</p>
<p>next best pivots, and open questions. Uses extractive analysis only --</p>
<p>no model loading required.</p>
<p></p>
<p>RAM guard: if governor is critical/emergency, generates minimal brief</p>
<p>from counts only (no graph queries).</p>
<p></p>
<p>F205J: If duckdb_store is available, reads cross-sprint target memory</p>
<p>via get_target_memory_summary(target_id) and incorporates it into</p>
<p>headline, key_findings, and open_questions.</p>
<p></p>
<p>F223F: store_findings_count, when provided, distinguishes runtime findings</p>
<p>(from the current sprint) from store findings (canonical total accepted).</p>
<p>The headline uses runtime findings as "sprint findings"; store findings</p>
<p>are surfaced separately in key_findings when they differ from runtime.</p>
<p></p>
<p>Bounds:</p>
<p>- MAX_BRIEF_FINDINGS = 20</p>
<p>- MAX_BRIEF_CHAINS = 5</p>
<p>- MAX_BRIEF_NEXT_ACTIONS = 10</p>
<p>- MAX_CONTEXT_BYTES = 8192</p>
<p></p>
<p>Args:</p>
<p>sprint_id: Sprint identifier</p>
<p>target_id: Research target (query or canonical target_id)</p>
<p>findings: List of findings from the current sprint run (runtime findings)</p>
<p>graph_signal: Graph signal dict from _get_graph_signal()</p>
<p>governor: Optional M1ResourceGovernor for RAM check</p>
<p>duckdb_store: Optional DuckDBShadowStore for target memory read</p>
<p>store_findings_count: Optional canonical store count of total accepted</p>
<p>findings for this target/sprint. When provided and different from</p>
<p>len(findings), the headline uses runtime findings and store findings</p>
<p>are noted in key_findings when they differ.</p>
</div>
</details>
</li>
<li><code>async_record_canonical_findings_batch_arrow</code> (duckdb_store.py)</li>
<li><code>flush</code> (semantic_store.py)
<details><summary>Batch embed + LanceDB upsert.</summary>
<div class="doc-comment">
<p>Batch embed + LanceDB upsert.</p>
<p></p>
<p>ANE path: CoreMLEmbedder.embed() → CoreML → ANE (F228B, preferred)</p>
<p>CPU fallback: self._model.embed() → FastEmbed onnxruntime</p>
</div>
</details>
</li>
<li><code>_init_connection</code> (duckdb_store.py)
<details><summary>Initialize the DuckDB connection. Must be called from the worker thread.</summary>
<div class="doc-comment">
<p>Initialize the DuckDB connection. Must be called from the worker thread.</p>
<p>Sets up file or :memory: mode, applies PRAGMAs and schema.</p>
<p>For file mode, creates persistent _file_conn (Sprint 7H).</p>
<p></p>
<p>F231: Uses _resolve_duckdb_runtime_settings() for UMA-aware configuration.</p>
<p>DRY: All PRAGMA/SET configuration consolidated in _configure_connection().</p>
</div>
</details>
</li>
<li><code>find_connected_with_lancedb_rerank</code> (graph_service.py)</li>
<li><code>ann_search</code> (ann_index.py)</li>
<li><code>summarize_feed_clusters</code> (analyst_workbench.py)
<details><summary>F225E: Deterministic feed cluster summary from findings.</summary>
<div class="doc-comment">
<p>F225E: Deterministic feed cluster summary from findings.</p>
<p></p>
<p>Clusters findings by shared IOC/entity tokens or by source_type+domain</p>
<p>fallback. Feed-heavy runs show compact clusters instead of raw volume.</p>
<p></p>
<p>Bounds:</p>
<p>- max_clusters: max number of clusters (default MAX_FEED_CLUSTERS=20)</p>
<p>- max sample IDs per cluster: MAX_SAMPLE_IDS_PER_CLUSTER=5</p>
<p>- max text per cluster line: MAX_TEXT_PER_CLUSTER=200 chars</p>
<p></p>
<p>No model, no embeddings, no network calls.</p>
<p>Fail-soft: returns ("Feed clustering unavailable",) on any error.</p>
</div>
</details>
</li>
<li><code>__init__</code> (duckdb_store.py)</li>
<li><code>async_bulk_insert_findings</code> (duckdb_store.py)
<details><summary>Sprint F800A: Controller-facing async adapter for bulk findings insert.</summary>
<div class="doc-comment">
<p>Sprint F800A: Controller-facing async adapter for bulk findings insert.</p>
<p></p>
<p>Accepts CanonicalFinding instances OR plain dicts (controller dict format).</p>
<p>Dicts are converted to CanonicalFinding before delegating to the existing</p>
<p>async_record_canonical_findings_batch truth path.</p>
<p></p>
<p>Thread-safe, non-blocking - delegates to async_record_canonical_findings_batch</p>
<p>which uses the single-worker executor.</p>
<p></p>
<p>Args:</p>
<p>findings: List of CanonicalFinding or dict with keys:</p>
<p>finding_id, query, source_type, confidence, ts, provenance.</p>
<p></p>
<p>Returns:</p>
<p>list[ActivationResult] - 1:1 mapping, len(results) == len(findings).</p>
<p>Empty list if input is empty or store is closed.</p>
</div>
</details>
</li>
<li><code>_do_sync_close</code> (duckdb_store.py)
<details><summary>Synchronous full cleanup — called by both close() and aclose().</summary>
<div class="doc-comment">
<p>Synchronous full cleanup — called by both close() and aclose().</p>
<p></p>
<p>Args:</p>
<p>emergency: If True (close() path), skips async graph/semantic closes</p>
<p>since no event loop is guaranteed to be running.</p>
<p>Async cleanup is handled by _do_async_close() in aclose() path.</p>
</div>
</details>
</li>
<li><code>async_replay_single_pending_marker</code> (duckdb_store.py)
<details><summary>Sprint 8H: Replay a single pending marker by finding_id.</summary>
<div class="doc-comment">
<p>Sprint 8H: Replay a single pending marker by finding_id.</p>
<p></p>
<p>Recovery semantics per marker:</p>
<p>1. Marker exists? -&gt; marker_found</p>
<p>2. WAL finding:{id} truth exists? -&gt; wal_truth_found</p>
<p>3. If truth missing -&gt; failure (can't recover)</p>
<p>4. DuckDB write via same safe path as activation</p>
<p>5. Fresh read-back from new connection confirms durability</p>
<p>6. Success -&gt; clear pending marker</p>
<p>7. Failure -&gt; bump retry count; if &gt;= MAX_RETRY_COUNT -&gt; dead-letter</p>
<p></p>
<p>Idempotency: if DuckDB already has the record, consider it a success.</p>
<p></p>
<p>Args:</p>
<p>finding_id: The finding identifier to replay.</p>
<p></p>
<p>Returns:</p>
<p>ReplayResult with all fields populated.</p>
</div>
</details>
</li>
<li><code>initialize</code> (semantic_store.py) — <span class="doc-comment-inline">BOOT — load FastEmbed model + open LanceDB conn.</span></li>
<li><code>async_query_arrow_batches</code> (duckdb_store.py)</li>
<li><code>_canonical_findings_batch_to_activation_results</code> (duckdb_store.py)
<details><summary>Sync batch: CanonicalFinding list -&gt; list[dict] (not ActivationResult, avoid circular import).</summary>
<div class="doc-comment">
<p>Sync batch: CanonicalFinding list -&gt; list[dict] (not ActivationResult, avoid circular import).</p>
<p></p>
<p>Returns one dict per finding in input order.</p>
<p>LMDB WAL uses msgspec.json.encode for provenance serialization.</p>
<p>DuckDB insert uses tuple rows (list of lists).</p>
</div>
</details>
</li>
<li><code>multi_hop_search</code> (graph_rag.py)
<details><summary>Perform multi-hop search over the knowledge graph with path evidence.</summary>
<div class="doc-comment">
<p>Perform multi-hop search over the knowledge graph with path evidence.</p>
<p></p>
<p>Hop 0: Find starting nodes via semantic search</p>
<p>Hop 1..N: Traverse graph to find related nodes</p>
<p>Synthesis: Return paths with novelty filtering</p>
<p></p>
<p>Args:</p>
<p>query: Search query</p>
<p>hops: Number of hops to traverse (default: 2)</p>
<p>max_nodes: Maximum nodes to return (default: 20)</p>
<p>timeline: Enable timeline mode (default: False)</p>
<p>time_min: ISO date/time filter (inclusive)</p>
<p>time_max: ISO date/time filter (inclusive)</p>
<p>prefer_recent: Prefer newer evidence in ranking</p>
<p>bucket: Time bucketing for timeline ("month" or "year")</p>
<p>max_timeline_points: Max timeline points to return (default: 12, max: 12)</p>
<p></p>
<p>Returns:</p>
<p>Dict with:</p>
<p>- insights: List of relevant facts with path evidence</p>
<p>- paths: List of graph paths with nodes, relations, evidence</p>
<p>- summary_text: Human-readable summary</p>
<p>- novelty_stats: Stats about novelty filtering</p>
<p>- contested: Whether contradictions were found</p>
<p>- counter_paths: Alternative paths (if contested)</p>
<p>- timeline_points: Temporal analysis (if timeline=True)</p>
<p>- drift_events: Detected drift events (if timeline=True)</p>
<p>- narratives: Competing narratives (if contested)</p>
</div>
</details>
</li>
<li><code>_detect_contradictions</code> (graph_rag.py)
<details><summary>Detect contradictions in facts using lightweight heuristics.</summary>
<div class="doc-comment">
<p>Detect contradictions in facts using lightweight heuristics.</p>
<p></p>
<p>Identifies contradictions when:</p>
<p>1. Same (subject, predicate) with different objects</p>
<p>2. Explicit negations in predicates (e.g., "is" vs "is_not")</p>
<p></p>
<p>Args:</p>
<p>facts: List of facts to analyze</p>
<p></p>
<p>Returns:</p>
<p>Tuple of (contested: bool, primary_paths: list, counter_paths: list)</p>
</div>
</details>
</li>
<li><code>async_record_canonical_findings_batch</code> (duckdb_store.py)
<details><summary>Sprint 8P: Batch typed ingest API for CanonicalFinding DTO list.</summary>
<div class="doc-comment">
<p>Sprint 8P: Batch typed ingest API for CanonicalFinding DTO list.</p>
<p></p>
<p>Adapts DTO list -&gt; existing WAL-first batch activation path.</p>
<p>Používá stejný single-thread write executor jako stávající API.</p>
<p></p>
<p>Returns list[ActivationResult] - 1:1 mapping, len(results) == len(findings).</p>
<p>Partial failure: pokud nějaký finding selže, ostatní jsou still processed.</p>
<p>Celý batch neshodí kvůli jednomu vadnému findingu.</p>
</div>
</details>
</li>
<li><code>arrow_fetch_batch</code> (duckdb_store.py)</li>
<li><code>async_record_activation_batch</code> (duckdb_store.py)
<details><summary>Record multiple findings with WAL-first semantics.</summary>
<div class="doc-comment">
<p>Record multiple findings with WAL-first semantics.</p>
<p></p>
<p>Order: LMDB WAL first (via put_many) -&gt; DuckDB second (chunked batch).</p>
<p>Returns one ActivationResult per finding in input order.</p>
<p>Partial failure: if LMDB OK but DuckDB fails for some/all,</p>
<p>those entries get desync=True.</p>
<p></p>
<p>Args:</p>
<p>findings: List of dicts, each must contain:</p>
<p>id, query, source_type, confidence</p>
<p></p>
<p>Returns:</p>
<p>list[ActivationResult] - one per finding</p>
</div>
</details>
</li>
<li><code>async_record_canonical_finding</code> (duckdb_store.py)
<details><summary>Sprint 8P: Typed ingest API for CanonicalFinding DTO.</summary>
<div class="doc-comment">
<p>Sprint 8P: Typed ingest API for CanonicalFinding DTO.</p>
<p></p>
<p>Adapts DTO -&gt; existing WAL-first activation path.</p>
<p>Používá stejný single-thread write executor jako stávající API.</p>
<p></p>
<p>DTO -&gt; storage contract mapping:</p>
<p>finding.finding_id  -&gt; id</p>
<p>finding.query       -&gt; query</p>
<p>finding.source_type -&gt; source_type</p>
<p>finding.confidence  -&gt; confidence</p>
<p>finding.ts          -&gt; ts (in WAL only)</p>
<p>finding.provenance  -&gt; LMDB WAL payload (DuckDB nemá provenance sloupec)</p>
<p>finding.payload_text -&gt; LMDB WAL payload (DuckDB nemá payload_text sloupec)</p>
<p></p>
<p>Returns ActivationResult with same contract as async_record_activation.</p>
<p></p>
<p>Provenance: tvrdý invariant - stored in LMDB WAL payload only</p>
<p>(DuckDB schema nemá provenance_sloupec; backward-compatible,</p>
<p>probe_8l/probe_8h/probe_8f/probe_8b zůstávají kompatibilní)</p>
</div>
</details>
</li>
<li><code>_record_fail_open_batch</code> (duckdb_store.py)</li>
<li><code>multi_hop_search_sync</code> (graph_rag.py)
<details><summary>Synchronous version of multi-hop search with path evidence.</summary>
<div class="doc-comment">
<p>Synchronous version of multi-hop search with path evidence.</p>
<p></p>
<p>Uses search_sync() for synchronous contexts.</p>
<p></p>
<p>Args:</p>
<p>query: Search query</p>
<p>hops: Number of hops to traverse (default: 2)</p>
<p>max_nodes: Maximum nodes to return (default: 20)</p>
<p>timeline: Enable timeline mode (default: False)</p>
<p>time_min: ISO date/time filter (inclusive)</p>
<p>time_max: ISO date/time filter (inclusive)</p>
<p>prefer_recent: Prefer newer evidence in ranking</p>
<p>bucket: Time bucketing for timeline ("month" or "year")</p>
<p>max_timeline_points: Max timeline points to return (default: 12)</p>
<p></p>
<p>Returns:</p>
<p>Dict with insights, paths, summary_text, novelty_stats, contested, counter_paths,</p>
<p>timeline_points (if timeline=True), drift_events (if timeline=True), narratives (if contested)</p>
</div>
</details>
</li>
<li><code>graph_analytics_summary</code> (graph_service.py)</li>
<li><code>_load_embeddings_to_mlx</code> (lancedb_store.py)
<details><summary>Load embeddings to MLX using chunked streaming for M1 8GB safety.</summary>
<div class="doc-comment">
<p>Load embeddings to MLX using chunked streaming for M1 8GB safety.</p>
<p></p>
<p>P6-fix: original loaded ALL embeddings at once (~400MB+ for 100k rows).</p>
<p>Now streams in chunks of _mlx_load_chunk_size rows, building index incrementally.</p>
<p>Memory budget: 10k rows × 256 dims × 4 bytes ≈ 10MB per chunk.</p>
<p>F265FIX: Added RAM guard before loading — skip MLX path when available memory &lt; 3GB.</p>
</div>
</details>
</li>
<li><code>async_initialize</code> (duckdb_store.py)
<details><summary>Async initialize - creates connection on the worker thread.</summary>
<div class="doc-comment">
<p>Async initialize - creates connection on the worker thread.</p>
<p></p>
<p>Optional bounded startup replay runs after connection init, before the store</p>
<p>accepts new activation writes. This integrates the Sprint 8H recovery API</p>
<p>into the real init/startup path.</p>
<p></p>
<p>Args:</p>
<p>replay_pending_limit: Max number of pending markers to replay at startup.</p>
<p>None or 0 = no startup replay.</p>
<p>replay_timeout_s:    Wall-time budget for startup replay in seconds.</p>
<p>If exceeded, replay is stopped and remaining</p>
<p>markers are left for a future recovery run.</p>
<p></p>
<p>Returns:</p>
<p>True if initialization succeeded, False otherwise.</p>
<p>Sidecar is safe to use even if this returns False.</p>
<p></p>
<p>Boot barrier semantics (Sprint 8L):</p>
<p>While startup replay is running, _startup_ready is NOT set.</p>
<p>All async activation write methods check this and refuse to proceed</p>
<p>until the barrier is lifted (or the store is closed).</p>
<p>After bounded replay completes (success, limit, or timeout),</p>
<p>_startup_ready is set and writes are accepted.</p>
<p></p>
<p>NOTE: after aclose(), _closed is True and _initialized is False.</p>
<p>We allow re-initialization by clearing _closed here.</p>
</div>
</details>
</li>
<li><code>score_path</code> (graph_rag.py)
<details><summary>Score a path in the knowledge graph based on:</summary>
<div class="doc-comment">
<p>Score a path in the knowledge graph based on:</p>
<p>- Path length (shorter is better)</p>
<p>- Node relevance to hypothesis (via embeddings)</p>
<p>- Average node credibility</p>
<p></p>
<p>Args:</p>
<p>path: List of node IDs forming the path</p>
<p>hypothesis: The hypothesis to score against</p>
<p>hypothesis_emb: Pre-computed hypothesis embedding (optional)</p>
<p>max_nodes: Maximum nodes to score (budget)</p>
<p></p>
<p>Returns:</p>
<p>Score between 0 and 1</p>
</div>
</details>
</li>
<li><code>upsert_relation</code> (graph_service.py)</li>
<li><code>_graph_ingest_findings</code> (duckdb_store.py)
<details><summary>Background task: ingest findings into IOC graph.</summary>
<div class="doc-comment">
<p>Background task: ingest findings into IOC graph.</p>
<p></p>
<p>Called via _bg_tasks tracking after async_ingest_findings_batch succeeds.</p>
<p>Fail-open: any exception is caught and logged.</p>
<p></p>
<p>Architecture (P0 Batch IOC):</p>
<p>1. Batch extract IOCs from all findings in parallel (4-thread pool)</p>
<p>2. Collect all (ioc_type, value) tuples → batch buffer_ioc calls</p>
<p>3. Collect all observations → batch buffer_observation calls</p>
<p>4. O(n) per-finding extraction → O(1) batched graph writes</p>
</div>
</details>
</li>
<li><code>_sync_record_canonical_findings_batch_arrow</code> (duckdb_store.py)
<details><summary>Sprint P0-4: Arrow zero-copy bulk insert for CanonicalFinding list.</summary>
<div class="doc-comment">
<p>Sprint P0-4: Arrow zero-copy bulk insert for CanonicalFinding list.</p>
<p></p>
<p>MUST be called on the worker thread (thread-affine connection).</p>
<p>Returns (inserted_count, error_type):</p>
<p>- (n, None) on success where n = number of rows in input table</p>
<p>- (0, error_type) on any failure, where error_type is one of:</p>
<p>"pyarrow_not_installed" - pyarrow import failed</p>
<p>"table_build_failed"    - pa.Table.from_arrays failed</p>
<p>"duckdb_insert_failed" - QueryExecutor.insert_findings_bulk_arrow failed</p>
<p></p>
<p>Distinct from the legacy `_canonical_findings_batch_to_activation_results`</p>
<p>in three ways:</p>
<p>1. Builds a single pyarrow.Table with columnar zero-copy arrays.</p>
<p>2. Calls QueryExecutor.insert_findings_bulk_arrow (register + INSERT...SELECT).</p>
<p>3. Does NOT touch LMDB WAL - caller is responsible for that half (or falls</p>
<p>back to the legacy path which does both). This split keeps the Arrow</p>
<p>path optional and side-effect-free at the WAL layer.</p>
<p></p>
<p>Fail-soft: any error returns (0, error_type) and the async wrapper falls back</p>
<p>to legacy. The error_type is used for typed telemetry.</p>
</div>
</details>
</li>
<li><code>_evict_oldest_pending_markers</code> (wal.py)
<details><summary>Evict oldest pending sync markers to enforce MAX_PENDING_SYNC_MARKERS bound.</summary>
<div class="doc-comment">
<p>Evict oldest pending sync markers to enforce MAX_PENDING_SYNC_MARKERS bound.</p>
<p></p>
<p>Removes (total_count - keep_count) oldest markers by timestamp.</p>
<p>Returns number of markers evicted.</p>
<p></p>
<p>M1-safe: uses bounded heap instead of full sort, single write transaction</p>
<p>for all deletions, and processes in chunks to limit memory pressure.</p>
</div>
</details>
</li>
<li><code>_hybrid_retrieve_hnsw</code> (rag_engine.py)
<details><summary>Internal hybrid retrieval using HNSW for dense search.</summary>
<div class="doc-comment">
<p>Internal hybrid retrieval using HNSW for dense search.</p>
<p></p>
<p>ISSUE-021: Paralelní — embed(query) + BM25.build běží concurrent.</p>
<p>ANN HNSW search (Rust, GIL-free) běží sequential po embed.</p>
</div>
</details>
</li>
<li><code>measure_recall</code> (lancedb_auto_tuner.py)
<details><summary>Measure recall@K on a bounded random sample.</summary>
<div class="doc-comment">
<p>Measure recall@K on a bounded random sample.</p>
<p></p>
<p>Returns ``(recall_at_k, avg_search_ms)``. ``recall_at_k`` is in</p>
<p>``[0.0, 1.0]`` (1.0 = perfect overlap with brute-force top-K excluding</p>
<p>the query itself). Returns ``(0.0, 0.0)`` on any failure.</p>
<p></p>
<p>Algorithm:</p>
<p>1. Extract up to ``MAX_BRUTE_FORCE_ROWS`` vectors via ``to_polars()``.</p>
<p>2. Sample ``sample_size`` query vectors (deterministic seed).</p>
<p>3. For each query: compute brute top-(K+1) via numpy matmul, exclude</p>
<p>self, compare with ANN top-K from ``table.search(...).limit(K)``.</p>
<p>4. ``recall = mean(|ANN ∩ brute| / K)``</p>
</div>
</details>
</li>
<li><code>hybrid_retrieve</code> (rag_engine.py)
<details><summary>Retrieve relevant documents using hybrid search (dense + sparse).</summary>
<div class="doc-comment">
<p>Retrieve relevant documents using hybrid search (dense + sparse).</p>
<p></p>
<p>ISSUE-021: Parallel retrieval — embed + BM25 paralelně přes asyncio.gather.</p>
<p>embed(query + docs) a BM25.index_build běží concurrent:</p>
<p>- MLX GPU embed: [query] + [doc_contents] v jednom batch call</p>
<p>- CPU: BM25 add_documents v thread pool</p>
<p>- Po embed dokončení: dense_retrieval + sparse BM25.search → fusion</p>
<p></p>
<p>Args:</p>
<p>query: Search query</p>
<p>documents: List of documents to search</p>
<p>top_k: Number of results to return</p>
<p>filters: Optional metadata filters</p>
<p></p>
<p>Returns:</p>
<p>List of retrieved chunks with scores</p>
</div>
</details>
</li>
<li><code>_canonical_finding_to_activation_result</code> (duckdb_store.py)
<details><summary>Sync wrapper: CanonicalFinding DTO -&gt; ActivationResult dict.</summary>
<div class="doc-comment">
<p>Sync wrapper: CanonicalFinding DTO -&gt; ActivationResult dict.</p>
<p></p>
<p>Sprint 8R: DTO -&gt; storage contract mapping:</p>
<p>finding.finding_id  -&gt; id</p>
<p>finding.query       -&gt; query</p>
<p>finding.source_type -&gt; source_type</p>
<p>finding.confidence  -&gt; confidence</p>
<p>finding.ts          -&gt; ts (DOUBLE in DuckDB)</p>
<p>finding.provenance  -&gt; provenance_json (JSON TEXT in DuckDB via msgspec)</p>
<p>finding.payload_text -&gt; LMDB WAL payload only</p>
<p></p>
<p>LMDB WAL uses msgspec.json.encode for consistent serialization.</p>
<p>DuckDB insert uses tuple row (efficient, not dict list).</p>
</div>
</details>
</li>
<li><code>_sync_record_canonical_findings_batch_arrow_standalone</code> (duckdb_store.py)
<details><summary>Arrow zero-copy fallback for legacy batch path (async_record_canonical_findings_batch).</summary>
<div class="doc-comment">
<p>Arrow zero-copy fallback for legacy batch path (async_record_canonical_findings_batch).</p>
<p></p>
<p>Combines WAL + DuckDB Arrow into a single sync helper so the legacy fallback</p>
<p>path also benefits from zero-copy Arrow INSERT. Replaces the tuple-based</p>
<p>_canonical_findings_batch_to_activation_results path entirely.</p>
<p></p>
<p>MUST be called on the worker thread.</p>
<p>Returns list[dict] with 1:1 mapping.</p>
</div>
</details>
</li>
<li><code>upsert</code> (ann_index.py)
<details><summary>Upsert into both USEARCH (primary) and LanceDB (persistence).</summary>
<div class="doc-comment">
<p>Upsert into both USEARCH (primary) and LanceDB (persistence).</p>
<p></p>
<p>Returns True on success, False on error (fail-open).</p>
<p>Thread-safe via lock.</p>
</div>
</details>
</li>
<li><code>_activation_record_findings_batch</code> (duckdb_store.py)
<details><summary>Sprint 8A: Batch activation - LMDB WAL first, DuckDB second.</summary>
<div class="doc-comment">
<p>Sprint 8A: Batch activation - LMDB WAL first, DuckDB second.</p>
<p></p>
<p>Each finding dict must contain: id, query, source_type, confidence</p>
<p>(id is generated by caller if not present)</p>
<p></p>
<p>Returns dict with keys: lmdb_success, duckdb_success, count,</p>
<p>failed_ids (list of ids that failed)</p>
</div>
</details>
</li>
<li><code>async_record_activation</code> (duckdb_store.py)</li>
<li><code>find_entity_history</code> (graph_service.py)</li>
<li><code>semantic_pivot</code> (semantic_store.py)</li>
<li><code>_get_mlx_chunk_size</code> (lancedb_store.py)
<details><summary>Sprint #15: Adaptive chunk sizing based on current memory pressure.</summary>
<div class="doc-comment">
<p>Sprint #15: Adaptive chunk sizing based on current memory pressure.</p>
<p></p>
<p>Memoizes the result within a loading session — callers get a consistent</p>
<p>chunk size without re-sampling on every chunk iteration.</p>
<p></p>
<p>Returns:</p>
<p>1_000 if state == "emergency" (minimal, fail-safe)</p>
<p>3_000 if state == "critical" (reduced)</p>
<p>5_000 if state == "warn" (moderate)</p>
<p>1_000 if swap_detected (abort mid-load signal — use minimal)</p>
<p>10_000 if state == "ok" / "soft_warn" / error (default, safe)</p>
</div>
</details>
</li>
<li><code>upsert_ioc</code> (graph_service.py)</li>
<li><code>annotate_findings_with_graph_context</code> (graph_attachment.py)
<details><summary>Sprint F193A §1: Read-only enrichment pass — attaches graph context to findings.</summary>
<div class="doc-comment">
<p>Sprint F193A §1: Read-only enrichment pass — attaches graph context to findings.</p>
<p></p>
<p>PURPOSE</p>
<p>-------</p>
<p>Minimal annotation layer that reads persisted findings, queries connected IOCs</p>
<p>from the graph donor backend, and attaches lightweight annotations for</p>
<p>export/report use. Does NOT make DuckDBShadowStore a graph authority.</p>
<p></p>
<p>READ-ONLY SEAM — STORE IS NOT GRAPH TRUTH OWNER</p>
<p>-------------------------------------------------</p>
<p>This method is a thin pass-through to graph donor backend seams:</p>
<p>- get_connected_iocs() for IOC linkage</p>
<p>- get_top_seed_nodes() for degree context</p>
<p>It never writes to the graph. The graph (DuckPGQGraph) remains the analytics</p>
<p>donor backend, not the truth owner.</p>
<p></p>
<p>BEHAVIOR</p>
<p>--------</p>
<p>- Iterates through findings and extracts IOC values</p>
<p>- For each unique IOC, queries get_connected_iocs() from donor graph</p>
<p>- Attaches annotations as lightweight dict (no heavy objects)</p>
<p>- Fail-open: returns original findings unchanged on any error</p>
<p>- Bounded: max_annotations limits work to prevent unbounded work</p>
<p></p>
<p>Args:</p>
<p>findings: List of finding dicts (must have 'id' field).</p>
<p>max_hops: Max traversal depth for find_connected (default 2).</p>
<p>max_annotations: Max number of findings to annotate (default 50).</p>
<p></p>
<p>Returns:</p>
<p>list[dict]: Findings with optional 'graph_annotation' key attached.</p>
<p>Unannotated fields are returned unchanged on failure.</p>
</div>
</details>
</li>
<li><code>async_ingest_dht_metadata</code> (duckdb_store.py)
<details><summary>Sprint F224A: Ingest DHT metadata from torrent discovery.</summary>
<div class="doc-comment">
<p>Sprint F224A: Ingest DHT metadata from torrent discovery.</p>
<p></p>
<p>Args:</p>
<p>metadata: List of DHT metadata dicts with keys:</p>
<p>- infohash: str (required, primary key)</p>
<p>- name: str (optional)</p>
<p>- files: list[str] (optional, stored as JSON)</p>
<p>- size_bytes: int (optional)</p>
<p>- first_seen: float (optional, defaults to now)</p>
<p>- last_seen: float (optional, defaults to now)</p>
<p>- peer_count: int (optional)</p>
<p>- sources: list[str] (optional, stored as JSON)</p>
<p></p>
<p>Returns:</p>
<p>Number of records ingested</p>
</div>
</details>
</li>
<li><code>_traverse_hop_with_paths</code> (graph_rag.py)
<details><summary>Traverse one hop with full path tracking.</summary>
<div class="doc-comment">
<p>Traverse one hop with full path tracking.</p>
<p></p>
<p>Args:</p>
<p>visited: Set of already visited node IDs</p>
<p>hop: Current hop number</p>
<p>max_nodes: Maximum nodes to collect</p>
<p>seed_entities: Entities from seed documents</p>
<p>seed_doc_entities: Entities from the top seed document only</p>
<p>max_edges: Maximum edges to traverse</p>
<p></p>
<p>Returns:</p>
<p>Tuple of (new_facts, new_paths)</p>
</div>
</details>
</li>
<li><code>get_top_entities_for_ghost_global</code> (graph_attachment.py)
<details><summary>Sprint 8TF §2: Bounded read-only seam for ghost_global cross-sprint entity accumulation.</summary>
<div class="doc-comment">
<p>Sprint 8TF §2: Bounded read-only seam for ghost_global cross-sprint entity accumulation.</p>
<p></p>
<p>PURPOSE</p>
<p>-------</p>
<p>Provides a store-facing surface for the ghost_global upsert use case.</p>
<p>__main__.py previously spelunked graph attachment internals directly:</p>
<p>graph.get_nodes()[:100]  ← method does not exist on any graph backend</p>
<p>This method wraps the correct capability query so __main__.py never accesses</p>
<p>_ioc_graph internals for this use case.</p>
<p></p>
<p>STORE IS NOT GRAPH TRUTH OWNER</p>
<p>--------------------------------</p>
<p>The injected graph is the authoritative store (IOCGraph=Kuzu or DuckPGQGraph=DuckDB).</p>
<p>This seam is a thin, fail-open adapter for one specific consumer: ghost_global upsert.</p>
<p>It does NOT make DuckDBShadowStore a graph authority.</p>
<p></p>
<p>PAYLOAD SHAPE</p>
<p>-------------</p>
<p>Returns list[tuple[str, str, float]] — exactly the shape required by</p>
<p>upsert_global_entities(entities: list[tuple[str, str, float]]).</p>
<p>Each tuple: (entity_value, entity_type, confidence_cumulative)</p>
<p></p>
<p>FUTURE OWNER / REMOVAL CONDITION</p>
<p>---------------------------------</p>
<p>- Future graph truth owner: IOCGraph (Kuzu) — should expose this directly</p>
<p>- Removal condition: IOCGraph.get_top_entities_for_ghost_global(n=100)</p>
<p>covers this use case with no remaining __main__.py consumer</p>
<p></p>
<p>CAPABILITY REQUIREMENTS</p>
<p>------------------------</p>
<p>Requires the attached graph to implement get_top_nodes_by_degree(n).</p>
<p>DuckPGQGraph (DuckDB): has this method, returns dicts with value/ioc_type/confidence.</p>
<p>IOCGraph (Kuzu): does NOT have this method — returns [] (fail-open).</p>
<p>Fail-open: returns [] if graph is None or method is absent.</p>
<p></p>
<p>Args:</p>
<p>n: Number of top entities to return (default 100).</p>
<p></p>
<p>Returns:</p>
<p>list[tuple[str, str, float]]: Bounded entity payload for ghost_global upsert.</p>
<p>Returns [] if no graph attached or call fails.</p>
</div>
</details>
</li>
<li><code>reembed_all</code> (lancedb_store.py)
<details><summary>One-shot re-embed admin operation. NOT a per-sprint hot path.</summary>
<div class="doc-comment">
<p>One-shot re-embed admin operation. NOT a per-sprint hot path.</p>
<p></p>
<p>F265X: migrated to polars native path. Uses self._table.to_polars()</p>
<p>to skip the intermediate Arrow allocation that pl.from_arrow(.to_arrow())</p>
<p>required. Falls back to .to_pandas() on polars ImportError or if</p>
<p>.to_polars() itself fails. Polars 1.x + LanceDB ≥0.9.</p>
</div>
</details>
</li>
<li><code>insert_findings_bulk_arrow</code> (duckdb_store.py)
<details><summary>Sprint P0-4: Zero-copy Arrow bulk insert via DuckDB register() + INSERT...SELECT.</summary>
<div class="doc-comment">
<p>Sprint P0-4: Zero-copy Arrow bulk insert via DuckDB register() + INSERT...SELECT.</p>
<p></p>
<p>MUST be called on the worker thread (thread-affine connection).</p>
<p>Returns (row_count, error_type) on success: (n_rows, None).</p>
<p>On any failure returns (0, error_type) where error_type is one of:</p>
<p>"table_none"    - table is None</p>
<p>"num_rows_err"  - failed to read num_rows</p>
<p>"zero_rows"     - table has 0 rows</p>
<p>"no_conn"       - could not acquire connection</p>
<p>"pyarrow_build" - pa.Table.from_arrays failed (inside DuckDB register)</p>
<p>"duckdb_error"  - DuckDB register/execute/unregister failed</p>
<p></p>
<p>Why: executemany with N prepared stmt.execute() Python calls has ~3-5x the</p>
<p>per-row Python overhead of one Arrow register() + one INSERT...SELECT.</p>
<p>Provenance is already serialized in `table` (caller builds pa.array of JSON strs),</p>
<p>so this method does no Python-level encoding.</p>
<p></p>
<p>ON CONFLICT (id) DO NOTHING handles primary-key collisions silently.</p>
<p>The secondary UNIQUE(query, source_type) constraint is NOT protected here;</p>
<p>caller is expected to pre-dedupe or accept the failure (logged + return 0).</p>
</div>
</details>
</li>
<li><code>_derive_target_memory_feedback</code> (analyst_workbench.py)
<details><summary>F226E: Derive next-run advice from target memory history.</summary>
<div class="doc-comment">
<p>F226E: Derive next-run advice from target memory history.</p>
<p></p>
<p>Computed from existing surfaces only (target_memory + findings).</p>
<p>NO new DB API, NO network, NO model.</p>
<p></p>
<p>Returns:</p>
<p>dict with keys:</p>
<p>- repeated_feed_dominance: bool</p>
<p>- prior_nonfeed_weakness: bool</p>
<p>- prior_public_accepted_count: int</p>
<p>- prior_ct_accepted_count: int</p>
<p>- suggested_next_profile: str</p>
<p>- suggested_feed_cap_reason: str</p>
<p>- suggested_nonfeed_lanes: str</p>
</div>
</details>
</li>
<li><code>aiter_recent_findings</code> (duckdb_store.py)</li>
<li><code>search_similar</code> (lancedb_store.py)
<details><summary>Search for similar entities.</summary>
<div class="doc-comment">
<p>Search for similar entities.</p>
<p></p>
<p>Args:</p>
<p>embedding: Query embedding.</p>
<p>text_hint: Optional text query for FTS.</p>
<p>threshold: Similarity threshold (0-1). Applied only for pure vector</p>
<p>results; bypassed for RRF reranked hybrid (RRF is the final ranking).</p>
<p>limit: Maximum results to return.</p>
<p>query_type: Search mode — "auto" delegates to _detect_query_type(),</p>
<p>or explicit "vector"/"fts"/"hybrid". Default "auto".</p>
<p>AREA H+: 2026 cutting-edge — when "hybrid" + FTS available, applies</p>
<p>native RRFReranker (LanceDB 0.8+) for 15-30% better OSINT recall.</p>
<p></p>
<p>Returns:</p>
<p>List of matching entities with similarity scores.</p>
</div>
</details>
</li>
<li><code>_build_raptor_tree</code> (rag_engine.py) — <span class="doc-comment-inline">Build RAPTOR summarization tree. Returns node_id -&gt; RaptorNode dict.</span></li>
<li><code>ask</code> (analyst_workbench.py)
<details><summary>Answer an analyst question using local data sources.</summary>
<div class="doc-comment">
<p>Answer an analyst question using local data sources.</p>
<p></p>
<p>PIPELINE:</p>
<p>1. query_findings() — keyword search over recent findings</p>
<p>2. query_graph() — entity history for key entities in question</p>
<p>3. _extract_answer() — deterministic extractive answer from chunks</p>
<p>4. get_evidence_pointers() — build EvidencePointer list</p>
<p>5. get_related_entities() — build RelatedEntity list</p>
<p>6. (Optional) LLM answer via model_lifecycle.load_model()</p>
<p></p>
<p>Args:</p>
<p>question: Natural language analyst question</p>
<p>use_model: If True, generate LLM answer after extractive</p>
<p>model_name: Model to load (required if use_model=True)</p>
<p></p>
<p>Returns:</p>
<p>AnalystAnswer with extractive_answer always populated.</p>
<p>llm_answer is None unless use_model=True and model loads successfully.</p>
</div>
</details>
</li>
<li><code>async_get_previous_findings_for_target</code> (duckdb_store.py)</li>
<li><code>tune_if_due</code> (lancedb_auto_tuner.py)
<details><summary>Decide-and-execute a tune cycle (synchronous core).</summary>
<div class="doc-comment">
<p>Decide-and-execute a tune cycle (synchronous core).</p>
<p></p>
<p>P0-1 + P0-2 Enhancement: Tunes BOTH num_partitions AND num_sub_vectors.</p>
<p></p>
<p>Steps:</p>
<p>1. Update inserts_since_tune counter (in-memory only).</p>
<p>2. If not enabled OR cooldown not satisfied → return early</p>
<p>with ``triggered=False``.</p>
<p>3. Else: measure recall, compute optimal partitions + sub_vectors,</p>
<p>retrain if either changed, persist new state.</p>
<p></p>
<p>Always returns a ``TuneResult``. Never raises. The caller should</p>
<p>apply ``result.new_partitions`` and ``result.new_num_sub_vectors``</p>
<p>to its own state if ``result.changed()``.</p>
</div>
</details>
</li>
<li><code>_schedule_graph_update</code> (duckdb_store.py)
<details><summary>Fire graph update as non-blocking asyncio task (Python 3.10+ safe).</summary>
<div class="doc-comment">
<p>Fire graph update as non-blocking asyncio task (Python 3.10+ safe).</p>
<p></p>
<p>Sprint F241: Writes accepted findings to DuckPGQGraph for cross-sprint</p>
<p>entity accumulation. Graph is ADVISORY ONLY - failures are silently</p>
<p>swallowed.</p>
<p></p>
<p>Sprint F-CLEAN fix: replaced `asyncio.coroutine(_graph_update_task)()`</p>
<p>(removed in Python 3.11) with the modern `async def` +</p>
<p>`loop.run_in_executor()` pattern. M1 EIGHTGB safe - DuckDB sync ops run</p>
<p>in the default ThreadPoolExecutor, not a separate process. Bounded</p>
<p>by `_MAX_INFLIGHT_GRAPH_UPDATES` via the existing `self._bg_tasks`</p>
<p>set (Sprint 8QA), auto-drained on completion.</p>
<p></p>
<p>Sync context (no running event loop - tests / sync CLI / F8H worker</p>
<p>threads) is a no-op; the graph update is advisory and not required</p>
<p>for correctness.</p>
<p></p>
<p>LAZY IMPORT: graph_store accessed here to avoid circular deps</p>
<p>with duckdb_store.</p>
</div>
</details>
</li>
<li><code>search_similar_adaptive</code> (lancedb_store.py)
<details><summary>Hybrid search with adaptive reranking and MMR (Sprint 76).</summary>
<div class="doc-comment">
<p>Hybrid search with adaptive reranking and MMR (Sprint 76).</p>
<p></p>
<p>Args:</p>
<p>query_text: Original query text for reranking.</p>
<p>query_emb: Query embedding vector.</p>
<p>top_k: Number of results to return.</p>
<p></p>
<p>Returns:</p>
<p>List of ranked documents.</p>
</div>
</details>
</li>
<li><code>search_similar</code> (lancedb_store.py)
<details><summary>Semantic search for similar papers.</summary>
<div class="doc-comment">
<p>Semantic search for similar papers.</p>
<p></p>
<p>Args:</p>
<p>query: Search query text.</p>
<p>top_k: Number of results to return.</p>
<p>filters: Optional filters (e.g., {"source": "arxiv"}).</p>
<p>query_type: Search mode — "auto" (default, uses _detect_query_type),</p>
<p>or explicit "vector"/"fts"/"hybrid". AREA H+: "hybrid" applies</p>
<p>native RRFReranker for 15-30% better recall on academic text.</p>
<p></p>
<p>Returns:</p>
<p>List of AcademicPaper instances.</p>
</div>
</details>
</li>
<li><code>_wal_delete_mode</code> (duckdb_store.py)
<details><summary>F275-2: Context manager — WAL→DELETE journal mode switch for bulk inserts.</summary>
<div class="doc-comment">
<p>F275-2: Context manager — WAL→DELETE journal mode switch for bulk inserts.</p>
<p></p>
<p>For bulk inserts (≥CHUNK_SIZE=2048), temporarily switch from WAL to DELETE</p>
<p>journal mode. WAL mode costs 2× fsync per write (WAL write + DB write);</p>
<p>DELETE costs 1× fsync. M1 SSD is safe for DELETE — single write is sufficient.</p>
<p></p>
<p>The LMDB WAL layer is unaffected (separate journal).</p>
<p></p>
<p>Restores WAL on exit regardless of success/failure.</p>
<p>Fail-soft: any error is logged and swallowed — caller continues.</p>
<p></p>
<p>P2-22 FIX: Cache original_mode on the connection object so subsequent</p>
<p>calls within the same session skip the PRAGMA query (2 round-trips saved</p>
<p>per chunk). The cache is stored on the QueryExecutor instance, which is</p>
<p>a process-wide singleton per DuckDBShadowStore instance.</p>
</div>
</details>
</li>
<li><code>_acquire_process_lock</code> (duckdb_store.py)
<details><summary>F269: Process-level lock using GraphLockManager (consolidated from F266-U5).</summary>
<div class="doc-comment">
<p>F269: Process-level lock using GraphLockManager (consolidated from F266-U5).</p>
<p></p>
<p>Uses GraphLockManager singleton per db_path — same fcntl.flock-based locking</p>
<p>as DuckPGQGraph. This unifies the 3 independent locking strategies into one.</p>
<p></p>
<p>Three-tier locking strategy:</p>
<p>1. 'excl' — we are the exclusive writer (lock acquired)</p>
<p>2. 'ro'  — another process holds the lock, open READ-ONLY</p>
<p>3. None  — lock unavailable, fall back to :memory:</p>
<p></p>
<p>Returns:</p>
<p>tuple: (lock_mode: str, message: str)</p>
</div>
</details>
</li>
<li><code>_get_cached_embedding</code> (lancedb_store.py) — <span class="doc-comment-inline">Get embedding from LMDB cache with writeback buffer.</span></li>
<li><code>_generate_timeline</code> (graph_rag.py)
<details><summary>Generate timeline points from facts.</summary>
<div class="doc-comment">
<p>Generate timeline points from facts.</p>
<p></p>
<p>Args:</p>
<p>facts: Facts with timestamps</p>
<p>bucket: Time bucketing ("month" or "year")</p>
<p>max_points: Maximum timeline points (hard limit: 12)</p>
<p></p>
<p>Returns:</p>
<p>List of timeline points</p>
</div>
</details>
</li>
<li><code>init</code> (ann_index.py)
<details><summary>Initialize LanceDB connection and table.</summary>
<div class="doc-comment">
<p>Initialize LanceDB connection and table.</p>
<p></p>
<p>Returns True on success, False on any error.</p>
<p>Stores error string in _boot_error on failure.</p>
</div>
</details>
</li>
<li><code>get_top_seed_nodes</code> (graph_attachment.py)
<details><summary>Sprint 8TF §1: Export-facing read-only seam for top seed nodes.</summary>
<div class="doc-comment">
<p>Sprint 8TF §1: Export-facing read-only seam for top seed nodes.</p>
<p></p>
<p>PURPOSE</p>
<p>-------</p>
<p>Provides a store-facing surface for the export handoff's seed-node use case.</p>
<p>export_sprint() currently falls back to store._ioc_graph.get_top_nodes_by_degree(n=5)</p>
<p>directly; this method wraps that call so export consumers don't need to spelunk</p>
<p>_ioc_graph internals.</p>
<p></p>
<p>STORE IS NOT GRAPH TRUTH OWNER</p>
<p>--------------------------------</p>
<p>The injected graph may be IOCGraph (Kuzu, truth) or DuckPGQGraph (donor/alternate).</p>
<p>This seam does NOT make DuckDBShadowStore a graph authority.</p>
<p>It is a thin, fail-open adapter for one specific export-facing read-only operation.</p>
<p></p>
<p>FUTURE OWNER / REMOVAL CONDITION</p>
<p>---------------------------------</p>
<p>- Future graph truth owner: IOCGraph (Kuzu) or its successor</p>
<p>- Removal condition: export_sprint() replaces its store._ioc_graph fallback</p>
<p>entirely with this method, AND no other consumer accesses _ioc_graph directly</p>
<p>for seed node queries</p>
<p></p>
<p>CAPABILITY REQUIREMENTS</p>
<p>-----------------------</p>
<p>Requires the attached graph to implement get_top_nodes_by_degree(n).</p>
<p>IOCGraph (Kuzu): has this method.</p>
<p>DuckPGQGraph (DuckDB): has this method.</p>
<p>If the method is absent or call fails, returns [] (fail-open).</p>
<p></p>
<p>Args:</p>
<p>n: Number of top nodes to return (default 5).</p>
<p></p>
<p>Returns:</p>
<p>list[dict]: Each dict has at least "value" and "ioc_type" keys.</p>
<p>Returns [] if no graph attached or call fails.</p>
</div>
</details>
</li>
<li><code>_configure_connection</code> (duckdb_store.py)
<details><summary>Apply all PRAGMAs/SETs to a DuckDB connection. DRY — called once per connection</summary>
<div class="doc-comment">
<p>Apply all PRAGMAs/SETs to a DuckDB connection. DRY — called once per connection</p>
<p>in _init_connection.</p>
<p></p>
<p>F231: UMA-aware configuration via _resolve_duckdb_runtime_settings().</p>
<p>F265B: WAL pragmas (file-backed DB only; N/A for :memory:).</p>
<p>F273F: madvise/F_NOCACHE for zero-copy mmap reads (file-backed only).</p>
<p>Idempotent — safe to call on any freshly-connected DuckDB connection.</p>
</div>
</details>
</li>
<li><code>async_get_recent_findings</code> (duckdb_store.py)
<details><summary>Sprint F800A: Controller-facing async adapter for recent findings.</summary>
<div class="doc-comment">
<p>Sprint F800A: Controller-facing async adapter for recent findings.</p>
<p></p>
<p>Thin wrapper around async_query_recent_findings - converts raw dict rows</p>
<p>to CanonicalFinding instances so callers receive typed DTOs.</p>
<p></p>
<p>Thread-safe, non-blocking - runs on duckdb_worker via run_in_executor.</p>
<p>Returns empty list if store is closed or uninitialized.</p>
<p></p>
<p>Args:</p>
<p>limit: Maximum number of findings to return (ordered by ts DESC).</p>
<p></p>
<p>Returns:</p>
<p>list[CanonicalFinding] - ordered by ts descending, most recent first.</p>
</div>
</details>
</li>
<li><code>invariant_validate</code> (duckdb_store.py)
<details><summary>Validate hardening invariants.</summary>
<div class="doc-comment">
<p>Validate hardening invariants.</p>
<p></p>
<p>Returns dict with keys:</p>
<p>- has_no_gpu_pragma: bool</p>
<p>- memory_limit_ok: bool (1GB or less)</p>
<p>- temp_size_ok: bool (1GB or 0GB for :memory:)</p>
<p>- temp_dir_on_ramdisk: bool (temp_dir under RAMDISK_ROOT if set)</p>
</div>
</details>
</li>
<li><code>_mlx_rerank</code> (ann_index.py)</li>
<li><code>_sync_insert_sprint_delta</code> (duckdb_store.py)
<details><summary>Sync insert - MUST be called on the worker thread.</summary>
<div class="doc-comment">
<p>Sync insert - MUST be called on the worker thread.</p>
<p></p>
<p>Sprint F192F §2: uses findings_per_minute (matches sprint_scorecard naming).</p>
</div>
</details>
</li>
<li><code>_binary_prefilter</code> (lancedb_store.py)
<details><summary>Fast pre-filter using binary embeddings (Hamming distance).</summary>
<div class="doc-comment">
<p>Fast pre-filter using binary embeddings (Hamming distance).</p>
<p></p>
<p>Tier 0: Rust SIMD hamming (batch_hamming_scores) — correct bit-level popcount</p>
<p>Tier 1: MLX fallback with popcount lookup table — correct bit-level popcount</p>
<p></p>
<p>BUG FIX (vs dead-code path):</p>
<p>- Old: checked _binary_embeddings (always None) → early return</p>
<p>- Old: mx.sum(xor_res, axis=1) summed bytes, not bits — WRONG</p>
<p>- Now: operates on _mlx_embeddings (always populated when binary path runs)</p>
<p>- Now: uses popcount (Rust or MLX lookup) for correct bit-level Hamming</p>
</div>
</details>
</li>
<li><code>_calculate_centrality_igraph</code> (graph_rag.py)
<details><summary>Calculate all centrality metrics via igraph C-core.</summary>
<div class="doc-comment">
<p>Calculate all centrality metrics via igraph C-core.</p>
<p></p>
<p>Returns {node_id: {degree, betweenness, closeness, eigenvector, pagerank}}.</p>
<p>Falls back to empty dict on error.</p>
</div>
</details>
</li>
<li><code>query_duckdb</code> (db.py)</li>
<li><code>_do_async_close</code> (duckdb_store.py)
<details><summary>Async graph/semantic store close — properly awaits coroutines.</summary>
<div class="doc-comment">
<p>Async graph/semantic store close — properly awaits coroutines.</p>
<p></p>
<p>Called only from aclose() path where an event loop is guaranteed to exist.</p>
<p>Extracts and awaits all async close() calls that _do_sync_close skips</p>
<p>when emergency=True.</p>
</div>
</details>
</li>
<li><code>_wal_put_many_sync</code> (duckdb_store.py)
<details><summary>Sprint P1-2: WAL-only sync helper - DuckDB Single-Writer Variant 2.</summary>
<div class="doc-comment">
<p>Sprint P1-2: WAL-only sync helper - DuckDB Single-Writer Variant 2.</p>
<p></p>
<p>Runs on _wal_executor. LMDB WAL is pure I/O so executor occupancy is brief.</p>
<p>Caller is responsible for DuckDB step (separate executor, sequential invariant).</p>
<p></p>
<p>Returns True if WAL succeeded for all findings.</p>
</div>
</details>
</li>
<li><code>rrf_rank_findings</code> (duckdb_store.py)
<details><summary>Sprint 8TC B.1: Reciprocal Rank Fusion přes 4 signály.</summary>
<div class="doc-comment">
<p>Sprint 8TC B.1: Reciprocal Rank Fusion přes 4 signály.</p>
<p></p>
<p>Signály:</p>
<p>1. semantic_score  - z LanceDB ANN (pokud dostupný)</p>
<p>2. pattern_count   - počet pattern matche</p>
<p>3. ioc_degree      - počet navázaných IOC uzlů</p>
<p>4. recency_score   - inverzní age (novější = vyšší)</p>
<p></p>
<p>SQL RRF: SUM(1.0 / (k + rank_i)) přes všechny signály.</p>
<p>Chybějící sloupce se přidávají dynamicky přes ALTER TABLE.</p>
<p></p>
<p>Args:</p>
<p>query: Search query string to filter canonical_findings</p>
<p>k: RRF constant (default 30 - snižuje vliv nízkých ranků)</p>
<p></p>
<p>Returns:</p>
<p>list[dict] s keys: finding_id, content, rrf_score, semantic_score,</p>
<p>pattern_count, ioc_degree, ts</p>
</div>
</details>
</li>
<li><code>_wal_evict_oldest_pending_markers</code> (duckdb_store.py)
<details><summary>P0-9 fix: Evict oldest pending sync markers to enforce MAX_PENDING_SYNC_MARKERS bound.</summary>
<div class="doc-comment">
<p>P0-9 fix: Evict oldest pending sync markers to enforce MAX_PENDING_SYNC_MARKERS bound.</p>
<p></p>
<p>Removes (total_count - keep_count) oldest markers by timestamp.</p>
<p>Returns number of markers evicted.</p>
</div>
</details>
</li>
<li><code>__init__</code> (neuromorphic.py)</li>
<li><code>_sync_query_top_entities_by_sprint</code> (duckdb_store.py) — <span class="doc-comment-inline">Sync - MUST be called on worker thread.</span></li>
<li><code>_bounded_startup_replay</code> (duckdb_store.py)
<details><summary>Sprint 8L: Time-boxed startup replay integrated into async_initialize.</summary>
<div class="doc-comment">
<p>Sprint 8L: Time-boxed startup replay integrated into async_initialize.</p>
<p></p>
<p>Scans pending_duckdb_sync:* markers, replays up to replay_pending_limit</p>
<p>of them, and respects replay_timeout_s wall-time budget.</p>
<p></p>
<p>Boot barrier: _startup_ready is NOT set during replay, so activation</p>
<p>writes are held off until replay completes or times out.</p>
<p></p>
<p>Kooperativní yield: asyncio.sleep(0) between chunks to avoid</p>
<p>starving the event loop during long replay runs.</p>
<p></p>
<p>Args:</p>
<p>replay_pending_limit: Maximum markers to replay</p>
<p>replay_timeout_s:    Wall-time budget in seconds</p>
</div>
</details>
</li>
<li><code>_init_mmap_ioc_dedup_store</code> (dedup.py)
<details><summary>Initialize Rust MmapIocDedupStore for persistent IOC dedup.</summary>
<div class="doc-comment">
<p>Initialize Rust MmapIocDedupStore for persistent IOC dedup.</p>
<p></p>
<p>F267: Mmap-backed IOC dedup replaces LMDB-based IOC dedup.</p>
<p>Persists across process restarts with zero warm-up cost.</p>
<p>M1 8GB safe: demand-paged, HashSet rebuilt on load.</p>
<p></p>
<p>G-9 FIX (2026-07-06): Clarified that signature drift reported in</p>
<p>G-9 was a false alarm. Rust MmapIocDedupStore.add() and Python-side</p>
<p>_PythonMmapIocDedupStore.add() both accept</p>
<p>(value: str, ioc_type_str: str, confidence: float) — NO drift.</p>
<p>The G-9 comment referred to the fallback PATH, not signature mismatch.</p>
<p></p>
<p>Fails softly: falls back to pure-Python _PythonMmapIocDedupStore</p>
<p>if Rust unavailable. Any exception stored in _ioc_dedup_store_error.</p>
</div>
</details>
</li>
<li><code>_detect_drift</code> (graph_rag.py)
<details><summary>Detect drift events - when claims about same (subject, predicate) change over time.</summary>
<div class="doc-comment">
<p>Detect drift events - when claims about same (subject, predicate) change over time.</p>
<p></p>
<p>Args:</p>
<p>facts: Facts to analyze</p>
<p>bucket: Time bucketing for detecting change points</p>
<p></p>
<p>Returns:</p>
<p>List of drift events (max 10)</p>
</div>
</details>
</li>
<li><code>__init__</code> (ann_index.py)</li>
<li><code>_build_usearch_index</code> (ann_index.py) — <span class="doc-comment-inline">Build USEARCH index from LanceDB data (M1 Metal SIMD accelerated).</span></li>
<li><code>async_replay_all_pending_duckdb_sync</code> (duckdb_store.py)
<details><summary>Sprint 8H: Replay all pending markers with chunking and event-loop yields.</summary>
<div class="doc-comment">
<p>Sprint 8H: Replay all pending markers with chunking and event-loop yields.</p>
<p></p>
<p>Uses per-instance replay lock to prevent concurrent replay of same markers.</p>
<p>Processes markers in chunks of REPLAY_CHUNK_SIZE, yielding to event loop</p>
<p>between chunks to avoid starving live operations.</p>
<p></p>
<p>Idempotency: markers that already exist in DuckDB are treated as success.</p>
<p></p>
<p>Args:</p>
<p>limit: Optional maximum number of markers to replay. None = all.</p>
<p></p>
<p>Returns:</p>
<p>list[ReplayResult], one per processed marker.</p>
</div>
</details>
</li>
<li><code>load_index</code> (rag_engine.py)
<details><summary>Load index from disk.</summary>
<div class="doc-comment">
<p>Load index from disk.</p>
<p></p>
<p>Args:</p>
<p>path: Path to load index from. Uses index_path from init if not provided.</p>
</div>
</details>
</li>
<li><code>upsert_ioc_batch</code> (graph_service.py)
<details><summary>Batch upsert IOCs — single DuckDB round-trip for N rows.</summary>
<div class="doc-comment">
<p>Batch upsert IOCs — single DuckDB round-trip for N rows.</p>
<p></p>
<p>Idempotency is enforced via _seen_iocs (in-memory dedup set) so duplicate</p>
<p>values within a sprint are filtered before the batch is sent to DuckDB.</p>
<p></p>
<p>Args:</p>
<p>rows: List of (value, ioc_type, confidence, source) tuples.</p>
<p>Returns:</p>
<p>Number of rows passed to DuckDB (not number actually inserted).</p>
</div>
</details>
</li>
<li><code>lookup_persistent_dedup</code> (dedup.py)
<details><summary>Lookup a fingerprint in the persistent dedup LMDB.</summary>
<div class="doc-comment">
<p>Lookup a fingerprint in the persistent dedup LMDB.</p>
<p></p>
<p>P1-4: Bloom filter pre-check — O(1) negative dedup, skip LMDB if Bloom says "not seen".</p>
<p>LMDB remains authoritative for positive matches.</p>
<p></p>
<p>F272: Lazy init — each sub-system initializes on first actual use, not at sprint start.</p>
<p>Saves ~2s from sprint boot when dedup LMDB mmap files are cold.</p>
<p></p>
<p>Args:</p>
<p>fp: 32-char BLAKE2b fingerprint hex string</p>
<p></p>
<p>Returns:</p>
<p>finding_id string if found, None otherwise (miss or LMDB unavailable)</p>
</div>
</details>
</li>
<li><code>_upsert_ioc_batch_sync</code> (ioc_graph.py)
<details><summary>Synchronous batch upsert — runs on _executor thread.</summary>
<div class="doc-comment">
<p>Synchronous batch upsert — runs on _executor thread.</p>
<p></p>
<p>N+1 elimination via UNWIND batch queries:</p>
<p>Phase 1: 1 query — UNWIND batch existence check</p>
<p>Phase 3: 1 query — UNWIND batch CREATE for new nodes</p>
<p>Phase 4: 1 query — UNWIND batch SET last_seen for existing nodes</p>
<p>Total: 3 queries regardless of batch size (was 2N+1).</p>
</div>
</details>
</li>
<li><code>embed_query</code> (semantic_store.py)
<details><summary>Embed a single query string — uses MLX path if available.</summary>
<div class="doc-comment">
<p>Embed a single query string — uses MLX path if available.</p>
<p></p>
<p>Returns:</p>
<p>ndarray dtype=float32, shape=(384,)</p>
</div>
</details>
</li>
<li><code>_generate_path_summary</code> (graph_rag.py)
<details><summary>Generate human-readable summary of graph paths.</summary>
<div class="doc-comment">
<p>Generate human-readable summary of graph paths.</p>
<p></p>
<p>Args:</p>
<p>facts: List of facts to summarize</p>
<p>query: Original query</p>
<p>contested: Whether results contain contradictions</p>
<p>counter_paths: Alternative paths showing contradictions</p>
<p></p>
<p>Returns:</p>
<p>Summary text (Hermes-friendly)</p>
</div>
</details>
</li>
<li><code>build_hnsw_index</code> (rag_engine.py)
<details><summary>Build HNSW index from documents.</summary>
<div class="doc-comment">
<p>Build HNSW index from documents.</p>
<p></p>
<p>Args:</p>
<p>documents: List of documents to index</p>
<p>embeddings: Optional pre-computed embeddings {doc_id: embedding}</p>
<p>If not provided, embeddings will be generated</p>
</div>
</details>
</li>
<li><code>_build_pivot_recommendations</code> (analyst_workbench.py)
<details><summary>F225B: Build bounded pivot recommendations from findings and graph signal.</summary>
<div class="doc-comment">
<p>F225B: Build bounded pivot recommendations from findings and graph signal.</p>
<p></p>
<p>Max 5 recommendations. Uses findings IOC values/types and graph entity data.</p>
<p>No new planner — summarizes existing pivots if present.</p>
</div>
</details>
</li>
<li><code>_sync_query_delta_comparison</code> (duckdb_store.py)
<details><summary>Sync - MUST be called on the worker thread.</summary>
<div class="doc-comment">
<p>Sync - MUST be called on the worker thread.</p>
<p></p>
<p>Sprint F192F §2: uses findings_per_minute (matches sprint_scorecard naming).</p>
</div>
</details>
</li>
<li><code>async_get_hypothesis_feedback</code> (duckdb_store.py)
<details><summary>Sprint F203G: Fetch aggregated hypothesis_feedback records.</summary>
<div class="doc-comment">
<p>Sprint F203G: Fetch aggregated hypothesis_feedback records.</p>
<p></p>
<p>Thread-safe, non-blocking - runs on duckdb_worker via run_in_executor.</p>
<p></p>
<p>Args:</p>
<p>target_id: If provided, filter by target_id. If None, returns all.</p>
<p>limit: Maximum records to return (default 1000).</p>
<p></p>
<p>Returns:</p>
<p>List of HypothesisFeedbackRecord instances ordered by ts DESC.</p>
<p>Returns empty list if store is closed or uninitialized.</p>
</div>
</details>
</li>
<li><code>_evict_if_needed</code> (lancedb_store.py) — <span class="doc-comment-inline">F214OPT-C: Pre-emptive eviction when LMDB map is near full.</span></li>
<li><code>_record_observation_batch_sync</code> (ioc_graph.py)
<details><summary>Synchronous batch observation — runs on _executor thread.</summary>
<div class="doc-comment">
<p>Synchronous batch observation — runs on _executor thread.</p>
<p></p>
<p>N+1 elimination via UNWIND batch queries:</p>
<p>Phase 1: 1 query — UNWIND batch existence check for all edges</p>
<p>Phase 3: 1 query — UNWIND batch CREATE for missing edges</p>
<p>Phase 4: 1 query — UNWIND batch SET last_seen for existing edges</p>
<p>Total: 3 queries regardless of batch size (was 2N+1).</p>
</div>
</details>
</li>
<li><code>get_connected_iocs</code> (graph_attachment.py)
<details><summary>Sprint 8VY: Read-only seam for analytics graph find_connected() (DuckPGQGraph).</summary>
<div class="doc-comment">
<p>Sprint 8VY: Read-only seam for analytics graph find_connected() (DuckPGQGraph).</p>
<p></p>
<p>PURPOSE</p>
<p>-------</p>
<p>Replaces direct shell access to store._ioc_graph.find_connected() in</p>
<p>__main__._run_sprint_mode(). Diagnostic use case: log connected nodes for top IOC.</p>
<p>DuckDBShadowStore is NOT a graph authority — thin fail-open adapter.</p>
<p></p>
<p>CONSUMER</p>
<p>--------</p>
<p>__main__._run_sprint_mode(): logging {first_ioc} → {len(connected)} connected nodes.</p>
<p></p>
<p>STORE IS NOT GRAPH TRUTH OWNER</p>
<p>-------------------------------</p>
<p>The analytics _ioc_graph (DuckPGQGraph) is the donor backend.</p>
<p>Returns [] (fail-open) if no graph attached or call fails.</p>
<p></p>
<p>CAPABILITY REQUIREMENTS</p>
<p>-----------------------</p>
<p>Requires attached graph to implement find_connected(value, max_hops) → list.</p>
<p>DuckPGQGraph: has this method.</p>
<p>IOCGraph: does NOT have this method → returns [] (fail-open).</p>
<p></p>
<p>Args:</p>
<p>ioc_value: The IOC value to find connections for.</p>
<p>max_hops: Maximum traversal depth (default 2).</p>
<p></p>
<p>Returns:</p>
<p>list: Connected IOC nodes or [] if unavailable.</p>
</div>
</details>
</li>
<li><code>calculate_centrality</code> (graph_rag.py)
<details><summary>Calculate centrality measures for nodes in the graph.</summary>
<div class="doc-comment">
<p>Calculate centrality measures for nodes in the graph.</p>
<p></p>
<p>Uses igraph C-core when available (50-100x faster than pure-Python).</p>
<p>Falls back to simplified pure-Python on igraph unavailable / RAM constraint.</p>
<p></p>
<p>Args:</p>
<p>node_ids: Specific nodes to analyze (None = all)</p>
<p>top_k: Return top K most central nodes</p>
<p></p>
<p>Returns:</p>
<p>List of CentralityScores sorted by overall influence</p>
</div>
</details>
</li>
<li><code>_export_stix_bundle_sync</code> (ioc_graph.py) — <span class="doc-comment-inline">Synchronous STIX 2.1 export — runs on _executor thread.</span></li>
<li><code>async_ingest_findings_with_envelope</code> (duckdb_store.py)</li>
<li><code>get_dedup_runtime_status</code> (duckdb_store.py)
<details><summary>Sprint 8AG §6.17 + 8AK + 8AV + F222: Typed/cheap status surface for dedup subsystem.</summary>
<div class="doc-comment">
<p>Sprint 8AG §6.17 + 8AK + 8AV + F222: Typed/cheap status surface for dedup subsystem.</p>
<p></p>
<p>Sprint F222: Now delegates to DedupManager.get_runtime_status() for dedup-specific</p>
<p>fields. QualityAssessmentState fields still pulled from _quality_state.</p>
</div>
</details>
</li>
<li><code>_traverse_hop</code> (graph_rag.py)
<details><summary>Traverse one hop in the graph with RAM-efficient frontier management.</summary>
<div class="doc-comment">
<p>Traverse one hop in the graph with RAM-efficient frontier management.</p>
<p></p>
<p>Args:</p>
<p>visited: Set of already visited node IDs</p>
<p>hop: Current hop number</p>
<p>max_nodes: Maximum nodes to collect</p>
<p>max_edges: Maximum edges to traverse (default: 500)</p>
<p></p>
<p>Returns:</p>
<p>List of new facts discovered in this hop</p>
</div>
</details>
</li>
<li><code>get_graph_stats</code> (graph_attachment.py)
<details><summary>Sprint 8VY: Read-only seam for analytics graph stats (DuckPGQGraph.stats()).</summary>
<div class="doc-comment">
<p>Sprint 8VY: Read-only seam for analytics graph stats (DuckPGQGraph.stats()).</p>
<p></p>
<p>PURPOSE</p>
<p>-------</p>
<p>Replaces direct shell access to store._ioc_graph.stats() in __main__._run_sprint_mode().</p>
<p>DuckDBShadowStore is NOT a graph authority — this is a thin fail-open adapter</p>
<p>for the diagnostics use case only.</p>
<p></p>
<p>CONSUMER</p>
<p>--------</p>
<p>__main__._run_sprint_mode(): logging [GRAPH] nodes/edges/pgq stats.</p>
<p></p>
<p>STORE IS NOT GRAPH TRUTH OWNER</p>
<p>-------------------------------</p>
<p>The analytics _ioc_graph (DuckPGQGraph) is the donor backend.</p>
<p>Returns {} (fail-open) if no graph attached or call fails.</p>
<p></p>
<p>CAPABILITY REQUIREMENTS</p>
<p>-----------------------</p>
<p>Requires attached graph to implement stats() → {nodes, edges, pgq_active}.</p>
<p>DuckPGQGraph: has this method.</p>
<p>IOCGraph: has this method.</p>
<p></p>
<p>Returns:</p>
<p>dict: {nodes, edges, pgq_active} or {} if unavailable.</p>
</div>
</details>
</li>
<li><code>store_pattern</code> (neuromorphic.py)</li>
<li><code>_apply_schema_migrations</code> (duckdb_store.py)
<details><summary>ALTER TABLE ADD COLUMN for any sprint_delta columns missing from old DBs.</summary>
<div class="doc-comment">
<p>ALTER TABLE ADD COLUMN for any sprint_delta columns missing from old DBs.</p>
<p>DuckDB does not have IF NOT EXISTS for ALTER, so we catch and ignore errors.</p>
<p></p>
<p>Sprint F192F §2: findings_per_min -&gt; findings_per_minute rename.</p>
<p>Migration order matters - add new column first, then handle legacy column:</p>
<p>1. Add findings_per_minute (new canonical name, matches sprint_scorecard)</p>
<p>2. Add top_source_type (may already exist on very old DBs)</p>
<p>3. Add synthesis_confidence (may already exist on very old DBs)</p>
<p>Legacy findings_per_min column is retained but not written to (inserts use</p>
<p>findings_per_minute). Queries read findings_per_minute which is populated</p>
<p>by current insert logic.</p>
</div>
</details>
</li>
<li><code>_sync_get_hypothesis_feedback</code> (duckdb_store.py)
<details><summary>Sprint F203G: Fetch hypothesis_feedback records ordered by ts DESC.</summary>
<div class="doc-comment">
<p>Sprint F203G: Fetch hypothesis_feedback records ordered by ts DESC.</p>
<p></p>
<p>Thread-safe: MUST be called on the duckdb_worker thread.</p>
<p></p>
<p>Args:</p>
<p>target_id: If provided, filter by target_id. If None, returns all.</p>
<p>limit: Maximum number of records to return.</p>
<p></p>
<p>Returns:</p>
<p>List of dicts with keys: id, target_id, pivot_type, ioc_type,</p>
<p>produced_count, accepted_count, signal_value, ts.</p>
</div>
</details>
</li>
<li><code>read_target_memory</code> (duckdb_store.py)
<details><summary>Sprint F204D: Read a TargetMemory record by target_id.</summary>
<div class="doc-comment">
<p>Sprint F204D: Read a TargetMemory record by target_id.</p>
<p>Returns None if not found. Deserializes JSON TEXT columns.</p>
</div>
</details>
</li>
<li><code>add_entity</code> (lancedb_store.py)
<details><summary>Add entity to identity store.</summary>
<div class="doc-comment">
<p>Add entity to identity store.</p>
<p></p>
<p>Args:</p>
<p>entity_id: Unique entity identifier.</p>
<p>embedding: Vector embedding for semantic similarity.</p>
<p>aliases: List of aliases/alternate names.</p>
<p></p>
<p>Returns:</p>
<p>True if added successfully, False otherwise.</p>
</div>
</details>
</li>
<li><code>search</code> (rag_engine.py)
<details><summary>Search for k nearest neighbors.</summary>
<div class="doc-comment">
<p>Search for k nearest neighbors.</p>
<p></p>
<p>Args:</p>
<p>query_vector: Query vector of shape (dim,)</p>
<p>k: Number of results to return</p>
<p>filter_ids: Optional list of ids to filter results</p>
<p></p>
<p>Returns:</p>
<p>Tuple of (list of ids, list of distances/scores)</p>
</div>
</details>
</li>
<li><code>_derive_open_questions</code> (analyst_workbench.py)
<details><summary>Derive open questions from gaps in findings and graph.</summary>
<div class="doc-comment">
<p>Derive open questions from gaps in findings and graph.</p>
<p></p>
<p>Checks for common gaps: low finding count, no high-confidence findings,</p>
<p>sparse graph, missing IOC types.</p>
</div>
</details>
</li>
<li><code>_parse_sparql_results</code> (entity_linker.py)
<details><summary>Parse SPARQL results into EntityCandidate objects.</summary>
<div class="doc-comment">
<p>Parse SPARQL results into EntityCandidate objects.</p>
<p></p>
<p>Args:</p>
<p>entity_text: Original entity text</p>
<p>data: SPARQL JSON response</p>
<p></p>
<p>Returns:</p>
<p>List of EntityCandidate objects</p>
</div>
</details>
</li>
<li><code>_do_close</code> (duckdb_store.py)
<details><summary>Synchronous close helper - idempotent.</summary>
<div class="doc-comment">
<p>Synchronous close helper - idempotent.</p>
<p></p>
<p>Note: _closed guard removed - close() and _do_close() are always called</p>
<p>together in the same call chain; close() sets _closed=True first and</p>
<p>guards against re-entry. _do_close() always runs its cleanup.</p>
</div>
</details>
</li>
<li><code>_try_python_fallback</code> (dedup.py)
<details><summary>Last-resort Python fallback — in-memory only, no file race possible.</summary>
<div class="doc-comment">
<p>Last-resort Python fallback — in-memory only, no file race possible.</p>
<p></p>
<p>P1-10: Uses set-based in-memory filter. No mmap persistence</p>
<p>(cross-run state is lost on crash, but dedup is best-effort anyway).</p>
<p>No os.path.exists race because there are no files to race on.</p>
</div>
</details>
</li>
<li><code>_init_bloom_filter_precheck</code> (dedup.py)
<details><summary>Initialize Rust MmapBloomFilter pre-check for fast negative dedup.</summary>
<div class="doc-comment">
<p>Initialize Rust MmapBloomFilter pre-check for fast negative dedup.</p>
<p></p>
<p>P1-4: Bloom filter sits in front of LMDB for O(1) negative dedup —</p>
<p>if Bloom says "not seen", skip LMDB entirely. If Bloom says "seen",</p>
<p>verify against LMDB (authoritative).</p>
<p></p>
<p>Fails softly: any exception stored in _bloom_filter_error.</p>
</div>
</details>
</li>
<li><code>_sync_get_previous_findings_for_target</code> (duckdb_store.py)</li>
<li><code>duckdb_fetch_polars</code> (duckdb_store.py)
<details><summary>F320-431: Zero-copy DuckDB → Polars via Arrow C Data Interface.</summary>
<div class="doc-comment">
<p>F320-431: Zero-copy DuckDB → Polars via Arrow C Data Interface.</p>
<p></p>
<p>Uses `conn.execute(sql).pl()` (DuckDB 1.5+) which reads Arrow buffers</p>
<p>directly via DuckDB's C Data Interface — no Python copies, no IPC</p>
<p>serialization round-trip. Single GIL acquire/release for the entire</p>
<p>result set.</p>
<p></p>
<p>MUST be called on the DuckDB worker thread (thread-affine connection).</p>
<p>Caller is responsible for thread safety.</p>
<p></p>
<p>Args:</p>
<p>conn: DuckDB connection (thread-affine, from _qe()._conn()).</p>
<p>sql: SQL query.</p>
<p>params: Optional query parameters.</p>
<p></p>
<p>Returns:</p>
<p>pl.DataFrame or None on error. DataFrame column order matches</p>
<p>SQL projection order.</p>
<p></p>
<p>Zero-copy guarantees:</p>
<p>- DuckDB Arrow buffers live in DuckDB's heap</p>
<p>- Polars adopts buffers via C Data Interface (zero-copy)</p>
<p>- No IPC bytes serialization (unlike Rust arrow_batch_builder path)</p>
<p>- Single GIL acquire/release vs N× for row-by-row iteration</p>
</div>
</details>
</li>
<li><code>async_get_target_memory</code> (duckdb_store.py)
<details><summary>Sprint F204D: Get target memory by target_id.</summary>
<div class="doc-comment">
<p>Sprint F204D: Get target memory by target_id.</p>
<p></p>
<p>Thread-safe, non-blocking - runs on duckdb_worker via run_in_executor.</p>
<p>Returns None if not found or on error.</p>
</div>
</details>
</li>
<li><code>async_record_research_session</code> (duckdb_store.py)</li>
<li><code>_ensure_ivf_pq_index_async</code> (lancedb_store.py)
<details><summary>Sprint F264D: Lazy IVF-PQ training (M1 8GB friendly, fail-soft).</summary>
<div class="doc-comment">
<p>Sprint F264D: Lazy IVF-PQ training (M1 8GB friendly, fail-soft).</p>
<p></p>
<p>Called from add_entity/search_similar on first invocation. Gated by</p>
<p>HLEDAC_LANCEDB_QUANTIZE=1. Skipped if table has &lt; 256 rows (insufficient</p>
<p>training data — IVF-PQ on small data degrades recall). Errors are logged</p>
<p>+ ignored → falls back to brute-force cosine. Double-checked locking</p>
<p>prevents concurrent training on first parallel query burst.</p>
<p></p>
<p>NOTE: Uses ``getattr`` for flags so the helper is safe under ``__new__``</p>
<p>test-mock paths that bypass ``__init__``.</p>
</div>
</details>
</li>
<li><code>search_with_mmr</code> (lancedb_store.py)
<details><summary>Diversity-aware search using Maximal Marginal Relevance from context_optimization.</summary>
<div class="doc-comment">
<p>Diversity-aware search using Maximal Marginal Relevance from context_optimization.</p>
<p></p>
<p>Args:</p>
<p>query_text: Original query text for reranking.</p>
<p>query_emb: Query embedding vector.</p>
<p>top_k: Number of results to return.</p>
<p>lambda_mult: Balance relevance (1.0) vs diversity (0.0). Default 0.5.</p>
<p>fetch_k: Number of candidates to fetch before reranking.</p>
<p></p>
<p>Returns:</p>
<p>List of diverse, relevant documents.</p>
</div>
</details>
</li>
<li><code>_traversal_worker</code> (graph_rag.py)
<details><summary>Worker that performs graph traversal and pushes discovered nodes to queue.</summary>
<div class="doc-comment">
<p>Worker that performs graph traversal and pushes discovered nodes to queue.</p>
<p></p>
<p>Args:</p>
<p>query: Search query</p>
<p>hops: Number of hops to traverse</p>
<p>max_nodes: Maximum nodes to discover</p>
<p>queue: Queue to push discovered nodes to</p>
</div>
</details>
</li>
<li><code>add_vectors</code> (rag_engine.py)
<details><summary>Add vectors to the index.</summary>
<div class="doc-comment">
<p>Add vectors to the index.</p>
<p></p>
<p>Args:</p>
<p>vectors: Array of shape (n_vectors, dim) or (dim,) for single vector</p>
<p>ids: List of unique string identifiers for each vector</p>
</div>
</details>
</li>
<li><code>_hnsw_retrieval</code> (rag_engine.py)
<details><summary>Retrieve documents using HNSW index.</summary>
<div class="doc-comment">
<p>Retrieve documents using HNSW index.</p>
<p></p>
<p>Args:</p>
<p>query_embedding: Query embedding vector</p>
<p>top_k: Number of results to return</p>
<p>filters: Optional metadata filters</p>
<p></p>
<p>Returns:</p>
<p>List of retrieved chunks with scores</p>
</div>
</details>
</li>
<li><code>link_entities</code> (entity_linker.py)
<details><summary>Link entities in text to Wikidata.</summary>
<div class="doc-comment">
<p>Link entities in text to Wikidata.</p>
<p></p>
<p>Args:</p>
<p>text: Input text to extract and link entities from</p>
<p>context: Optional context for disambiguation</p>
<p></p>
<p>Returns:</p>
<p>List of LinkedEntity objects</p>
</div>
</details>
</li>
<li><code>compute_optimal_partitions</code> (lancedb_auto_tuner.py)
<details><summary>Decide next ``num_partitions`` based on observed recall and search latency.</summary>
<div class="doc-comment">
<p>Decide next ``num_partitions`` based on observed recall and search latency.</p>
<p></p>
<p>P0-2 Enhancement: Trend-aware PID controller.</p>
<p></p>
<p>Instead of reacting to a single noisy recall sample, we compute an EMA</p>
<p>(exponential moving average) of recall and use its *direction* to guide</p>
<p>the adjustment. This provides closed-loop stability — the controller</p>
<p>damps oscillations that plague open-loop threshold-only approaches.</p>
<p></p>
<p>Branches:</p>
<p>- **recall_ema &lt; RECALL_TOO_LOW (0.85)** → grow by 50% (clamp upper).</p>
<p>IVF-PQ with too few partitions is hitting quantization error.</p>
<p>- **recall_ema ≥ RECALL_EXCELLENT (0.97) AND avg_search_ms &gt; 50** → shrink</p>
<p>by 25% (clamp lower). Index is over-partitioned for current data.</p>
<p>- **EMA trend is falling significantly** (3 consecutive drops) → early grow</p>
<p>signal before hitting hard threshold. Detects degradation trajectory.</p>
<p>- otherwise → no change. Index is well-tuned.</p>
<p></p>
<p>Heuristic floor: never grow above 1 partition per ~16 rows. Clamped to</p>
<p>``MAX_NUM_PARTITIONS=256`` to keep M1 RSS bounded.</p>
</div>
</details>
</li>
<li><code>add_text</code> (semantic_store.py)</li>
<li><code>wal_scan_pending_sync_markers</code> (wal.py)
<details><summary>Efficient prefix scan for all pending_duckdb_sync markers.</summary>
<div class="doc-comment">
<p>Efficient prefix scan for all pending_duckdb_sync markers.</p>
<p></p>
<p>Returns list of marker values (dicts with id, query, source_type, confidence, ts).</p>
<p>Uses LMDB cursor with prefix iteration — O(n) where n = number of pending markers.</p>
</div>
</details>
</li>
<li><code>_adjust_executor_pool</code> (duckdb_store.py)
<details><summary>Adjust _shared_executor worker count based on M1 UMA memory pressure.</summary>
<div class="doc-comment">
<p>Adjust _shared_executor worker count based on M1 UMA memory pressure.</p>
<p></p>
<p>F300S: Reduced defaults for M1 8GB UMA:</p>
<p>CRITICAL/EMERGENCY: 1 worker (~50 MB saved vs 2 workers baseline)</p>
<p>SOFT_WARN: 1 worker (conservative, leaves headroom for MLX)</p>
<p>OK: 2 workers (baseline, set at __init__)</p>
<p></p>
<p>F285-U1: Unified executor — all 4 former pools are now _shared_executor aliases.</p>
<p>This method adjusts the single shared pool's max_workers.</p>
<p></p>
<p>This is a best-effort advisory — executor is NOT restarted, only the</p>
<p>reference to max_workers is capped for future task submissions.</p>
<p>Thread count change takes effect on the NEXT submit() call.</p>
<p></p>
<p>Lazy import of resource_governor to avoid circular deps and cold-start cost.</p>
</div>
</details>
</li>
<li><code>_sync_record_research_session</code> (duckdb_store.py)</li>
<li><code>_wal_write_pending_sync_marker</code> (duckdb_store.py)
<details><summary>Sprint 8F: Write a pending-sync recovery marker to LMDB.</summary>
<div class="doc-comment">
<p>Sprint 8F: Write a pending-sync recovery marker to LMDB.</p>
<p>P0-9 fix: Enforces MAX_PENDING_SYNC_MARKERS bound via oldest eviction.</p>
<p></p>
<p>Marker key:  pending_duckdb_sync:{id}</p>
<p>Value:       same structure as WAL finding (id, query, source_type, confidence, ts)</p>
<p></p>
<p>This marker is written ONLY when LMDB succeeded but DuckDB failed.</p>
<p>A future recovery sprint can find it via prefix scan and retry the DuckDB write.</p>
</div>
</details>
</li>
<li><code>_filter_by_time</code> (graph_rag.py)
<details><summary>Filter facts by time range.</summary>
<div class="doc-comment">
<p>Filter facts by time range.</p>
<p></p>
<p>Args:</p>
<p>facts: List of facts to filter</p>
<p>time_min: ISO datetime minimum (inclusive)</p>
<p>time_max: ISO datetime maximum (inclusive)</p>
<p></p>
<p>Returns:</p>
<p>Filtered list of facts</p>
</div>
</details>
</li>
<li><code>_build_narratives</code> (graph_rag.py)
<details><summary>Build competing narratives from contradictory evidence.</summary>
<div class="doc-comment">
<p>Build competing narratives from contradictory evidence.</p>
<p></p>
<p>Args:</p>
<p>primary_paths: Primary evidence paths</p>
<p>counter_paths: Counter evidence paths</p>
<p></p>
<p>Returns:</p>
<p>List of narrative objects (max 3)</p>
</div>
</details>
</li>
<li><code>_derive_next_actions</code> (analyst_workbench.py)
<details><summary>Derive next actions from high-confidence findings.</summary>
<div class="doc-comment">
<p>Derive next actions from high-confidence findings.</p>
<p></p>
<p>Uses source_type and ioc_type patterns to suggest follow-ups.</p>
<p>No model required.</p>
</div>
</details>
</li>
<li><code>store_persistent_dedup_batch</code> (dedup.py)
<details><summary>Store multiple fingerprint → finding_id mappings in persistent dedup LMDB.</summary>
<div class="doc-comment">
<p>Store multiple fingerprint → finding_id mappings in persistent dedup LMDB.</p>
<p></p>
<p>S3: Single transaction for batch insert, reduces N txn.begin() to 1.</p>
<p></p>
<p>Args:</p>
<p>items: List of (fp, finding_id) tuples</p>
</div>
</details>
</li>
<li><code>_load_lmdb</code> (ioc_dedup_adapter.py)
<details><summary>Load persisted state from LMDB.</summary>
<div class="doc-comment">
<p>Load persisted state from LMDB.</p>
<p>Called lazily on first add() after init or after advance_sprint().</p>
</div>
</details>
</li>
<li><code>_activation_record_finding</code> (duckdb_store.py)
<details><summary>Sprint 8A: Record a structured finding - LMDB WAL first, DuckDB second.</summary>
<div class="doc-comment">
<p>Sprint 8A: Record a structured finding - LMDB WAL first, DuckDB second.</p>
<p></p>
<p>Mapping:</p>
<p>result.id or uuid4() -&gt; id</p>
<p>context.query or "" -&gt; query</p>
<p>source_type from schema/type name -&gt; source_type</p>
<p>result.confidence or 1.0 -&gt; confidence</p>
<p>time.time() -&gt; ts</p>
<p></p>
<p>Partial failure semantics:</p>
<p>- LMDB OK + DuckDB FAIL -&gt; LMDB remains truth, log desync, return duckdb_success=False</p>
<p>- LMDB FAIL + DuckDB SKIP -&gt; return lmdb_success=False, duckdb_success=None</p>
<p></p>
<p>Returns dict with keys: lmdb_success, duckdb_success, finding_id, query</p>
</div>
</details>
</li>
<li><code>_embed_batch</code> (lancedb_store.py) — <span class="doc-comment-inline">Generate embeddings in batches - thread-safe (uses embed_document for indexing).</span></li>
<li><code>detect_communities</code> (graph_rag.py)
<details><summary>Detect communities in the knowledge graph.</summary>
<div class="doc-comment">
<p>Detect communities in the knowledge graph.</p>
<p></p>
<p>Uses igraph C-core label propagation when available (5-10x faster than pure-Python).</p>
<p>Falls back to pure-Python label propagation on igraph unavailable / RAM constraint.</p>
<p></p>
<p>Args:</p>
<p>num_communities: Target number of communities</p>
<p></p>
<p>Returns:</p>
<p>List of detected communities</p>
</div>
</details>
</li>
<li><code>query_findings</code> (analyst_workbench.py)
<details><summary>Query recent findings using keyword/BM25 search.</summary>
<div class="doc-comment">
<p>Query recent findings using keyword/BM25 search.</p>
<p></p>
<p>Args:</p>
<p>query: Search query string</p>
<p>limit: Max results (capped to MAX_TOP_K)</p>
<p>source_type: Optional filter by source_type</p>
<p></p>
<p>Returns:</p>
<p>List of finding dicts ordered by relevance (keyword match).</p>
<p>Each dict has: id, query, source_type, confidence, ts, provenance,</p>
<p>payload_text (if available).</p>
</div>
</details>
</li>
<li><code>query_graph</code> (analyst_workbench.py)
<details><summary>Query entity history from DuckPGQGraph.</summary>
<div class="doc-comment">
<p>Query entity history from DuckPGQGraph.</p>
<p></p>
<p>Args:</p>
<p>entity_value: IOC value to traverse from (e.g., domain, IP)</p>
<p>max_hops: Max traversal depth (capped to MAX_GRAPH_HOPS)</p>
<p></p>
<p>Returns:</p>
<p>List of RelatedEntity ordered by hops then confidence.</p>
</div>
</details>
</li>
<li><code>async_record_hypothesis_tracking</code> (duckdb_store.py)</li>
<li><code>_wal_scan_pending_sync_markers</code> (duckdb_store.py)
<details><summary>Sprint 8F: Efficient prefix scan for all pending_duckdb_sync markers.</summary>
<div class="doc-comment">
<p>Sprint 8F: Efficient prefix scan for all pending_duckdb_sync markers.</p>
<p></p>
<p>Returns list of marker values (dicts with id, query, source_type, confidence, ts).</p>
<p>Uses LMDB cursor with prefix iteration - O(n) where n = number of pending markers,</p>
<p>NOT O(N) full database scan.</p>
</div>
</details>
</li>
<li><code>_extract_vectors_and_keys</code> (lancedb_auto_tuner.py)
<details><summary>Extract the vector column and a key column from the table as numpy.</summary>
<div class="doc-comment">
<p>Extract the vector column and a key column from the table as numpy.</p>
<p></p>
<p>Returns ``(vectors_normalized, key_list)``. Vectors are L2-normalized</p>
<p>for cosine-similarity. If the table is too large (&gt;MAX_BRUTE_FORCE_ROWS)</p>
<p>a deterministic random sample is taken for the brute-force baseline.</p>
<p></p>
<p>Fail-soft: any error returns empty arrays.</p>
</div>
</details>
</li>
<li><code>retrain</code> (lancedb_auto_tuner.py)
<details><summary>Re-train IVF-PQ with new ``num_partitions`` and optionally ``num_sub_vectors``.</summary>
<div class="doc-comment">
<p>Re-train IVF-PQ with new ``num_partitions`` and optionally ``num_sub_vectors``.</p>
<p></p>
<p>P1-2 Enhancement: Both IVF-PQ knobs are now tuned together.</p>
<p></p>
<p>Uses the canonical ``Table.create_index(..., replace=True)`` API</p>
<p>(LanceDB 0.4+). ``Table.optimize(retrain=True)`` is DEPRECATED and</p>
<p>does NOT re-train IVF-PQ centroids — it only compacts files. This</p>
<p>method is the only correct way to re-train with new params.</p>
<p></p>
<p>P1-1 Enhancement: LanceDB 0.4x API compatibility — passes</p>
<p>``max_iterations`` only when confirmed supported by the table, with</p>
<p>graceful fallback to signature-based detection.</p>
<p></p>
<p>Returns True on success, False on any error (fail-soft).</p>
</div>
</details>
</li>
<li><code>_apply_recency_boost</code> (graph_rag.py)
<details><summary>Boost scores of more recent facts.</summary>
<div class="doc-comment">
<p>Boost scores of more recent facts.</p>
<p></p>
<p>Args:</p>
<p>facts: List of facts to boost</p>
<p></p>
<p>Returns:</p>
<p>Facts with boosted scores</p>
</div>
</details>
</li>
<li><code>_build_risk_hypotheses</code> (analyst_workbench.py)
<details><summary>F225B: Build bounded deterministic risk hypotheses based on findings.</summary>
<div class="doc-comment">
<p>F225B: Build bounded deterministic risk hypotheses based on findings.</p>
<p></p>
<p>Max 5 hypotheses based on: source diversity, IOC density,</p>
<p>non-feed absence, CT/public presence.</p>
</div>
</details>
</li>
<li><code>is_duplicate_ioc_batch</code> (dedup.py)
<details><summary>Batch IOC dedup check via Rust MmapIocDedupStore.</summary>
<div class="doc-comment">
<p>Batch IOC dedup check via Rust MmapIocDedupStore.</p>
<p></p>
<p>Args:</p>
<p>items: List of (ioc_value, ioc_type) tuples.</p>
<p></p>
<p>Returns:</p>
<p>List[bool] — True = duplicate (already seen).</p>
<p></p>
<p>P1-07 invariants:</p>
<p>- Always-on: no feature flag, no env var toggle</p>
<p>- Bounded: Rust store has internal capacity limits</p>
<p>- Fail-safe: any error returns [False, ...] (allow all)</p>
<p>- Thread-safe: parking_lot::RwLock in Rust store</p>
</div>
</details>
</li>
<li><code>query_wikidata</code> (entity_linker.py)
<details><summary>Query Wikidata for entity candidates.</summary>
<div class="doc-comment">
<p>Query Wikidata for entity candidates.</p>
<p></p>
<p>Args:</p>
<p>entity_text: Entity text to search</p>
<p></p>
<p>Returns:</p>
<p>List of EntityCandidate objects</p>
</div>
</details>
</li>
<li><code>shutdown</code> (lancedb_store.py) — <span class="doc-comment-inline">Cleanup resources.</span></li>
<li><code>find_contradictions</code> (graph_rag.py)
<details><summary>Find contradictions between nodes in the graph.</summary>
<div class="doc-comment">
<p>Find contradictions between nodes in the graph.</p>
<p></p>
<p>From evidence_network_analyzer.py comments:</p>
<p>"Step 5: Identify contradictions"</p>
<p>"Find contradiction edges"</p>
<p>"Assess severity"</p>
<p></p>
<p>Args:</p>
<p>confidence_threshold: Minimum confidence to report</p>
<p></p>
<p>Returns:</p>
<p>List of detected contradictions</p>
</div>
</details>
</li>
<li><code>__init__</code> (rag_engine.py)
<details><summary>Initialize USearch Vector Index.</summary>
<div class="doc-comment">
<p>Initialize USearch Vector Index.</p>
<p></p>
<p>Args:</p>
<p>dim: Vector dimension (default 768 for typical embeddings)</p>
<p>max_elements: Maximum number of vectors in index</p>
<p>M: Number of bi-directional links for each node (higher = better recall, more memory)</p>
<p>ef_construction: Size of dynamic candidate list for construction (higher = better quality)</p>
<p>ef_search: Size of dynamic candidate list for search (higher = better recall)</p>
<p>space: Distance metric - "cosine", "l2", or "ip" (inner product)</p>
<p>index_path: Optional path for persistent index storage</p>
</div>
</details>
</li>
<li><code>_compress_chunks</code> (rag_engine.py)
<details><summary>Komprimovat chunky pomocí SPR — paralelně přes bounded TaskGroup.</summary>
<div class="doc-comment">
<p>Komprimovat chunky pomocí SPR — paralelně přes bounded TaskGroup.</p>
<p></p>
<p>M1 8GB: GRAPH_RAG limit (3,2,1,1) z ConcurrencyBudgetRegistry zamezuje</p>
<p>Metal alloc pressure. 50 chunků × 10 ms serial → ~170 ms parallel při limit=3.</p>
<p></p>
<p>Dynamic concurrency: adapts to memory pressure (lower = fewer concurrent).</p>
<p>Per-chunk timeout: prevents one stuck chunk from blocking the entire batch.</p>
</div>
</details>
</li>
<li><code>_secure_process</code> (rag_engine.py)
<details><summary>Process chunks through Secure Enclave for batch attestation.</summary>
<div class="doc-comment">
<p>Process chunks through Secure Enclave for batch attestation.</p>
<p></p>
<p>IMPORTANT: This does NOT mutate chunk text. The enclave is used for</p>
<p>hardware-backed attestation of chunk batch existence via signed digest.</p>
<p></p>
<p>Architecture:</p>
<p>- Build canonical BatchManifest (chunk_count, per-chunk SHA-256, batch_digest)</p>
<p>- Request one signature for the batch digest (NOT per-chunk)</p>
<p>- Store signature in enclave status for telemetry</p>
<p>- Return chunks unchanged</p>
</div>
</details>
</li>
<li><code>_store_embedding</code> (lancedb_store.py) — <span class="doc-comment-inline">Store embedding with float16 quantization (50% memory savings) and writeback buffer.</span></li>
<li><code>score_paths_parallel</code> (graph_rag.py)
<details><summary>Score multiple paths in parallel with bounded concurrency.</summary>
<div class="doc-comment">
<p>Score multiple paths in parallel with bounded concurrency.</p>
<p></p>
<p>M1 8GB: Uses Semaphore(4) to limit concurrent scoring operations.</p>
<p>Each scoring operation fetches embeddings via MLX (I/O bound).</p>
<p></p>
<p>Args:</p>
<p>paths: List of paths (each path is a list of node IDs)</p>
<p>hypothesis: The hypothesis to score against</p>
<p>max_nodes: Maximum nodes to score per path (budget)</p>
<p></p>
<p>Returns:</p>
<p>List of scores (one per path), in same order as input</p>
</div>
</details>
</li>
<li><code>_extract_key_findings</code> (analyst_workbench.py)
<details><summary>Extract key findings as strings from the findings list.</summary>
<div class="doc-comment">
<p>Extract key findings as strings from the findings list.</p>
<p></p>
<p>Uses extractive pattern: sorts by confidence and takes top items.</p>
<p>No model required.</p>
</div>
</details>
</li>
<li><code>_build_source_family_summary</code> (analyst_workbench.py)
<details><summary>F225B: Count source families from findings and summarize presence.</summary>
<div class="doc-comment">
<p>F225B: Count source families from findings and summarize presence.</p>
<p></p>
<p>Counts source_type/provenance families, identifies feed-only gap,</p>
<p>non-feed evidence, and CT/PUBLIC/PASSIVE_DNS support.</p>
<p></p>
<p>No model required.</p>
</div>
</details>
</li>
<li><code>close</code> (dedup.py) — <span class="doc-comment-inline">Close all LMDB stores and Bloom filter.</span></li>
<li><code>_ensure_ivf_pq_index</code> (ann_index.py) — <span class="doc-comment-inline">Lazy IVF-PQ training (M1 8GB friendly, fail-soft, sync).</span></li>
<li><code>to_polars_lazy</code> (duckdb_store.py)
<details><summary>Convert parquet file to Polars LazyFrame with filter pushdown.</summary>
<div class="doc-comment">
<p>Convert parquet file to Polars LazyFrame with filter pushdown.</p>
<p></p>
<p>This enables full Polars query optimization including:</p>
<p>- Column pruning</p>
<p>- Predicate pushdown</p>
<p>- Parallel execution</p>
<p></p>
<p>Returns:</p>
<p>polars.LazyFrame — collect() when ready to execute.</p>
</div>
</details>
</li>
<li><code>get_top_findings</code> (duckdb_store.py)
<details><summary>Sprint 8VE B.4: Return top findings by confidence for IOC graph display.</summary>
<div class="doc-comment">
<p>Sprint 8VE B.4: Return top findings by confidence for IOC graph display.</p>
<p></p>
<p>Queries canonical_findings ordered by confidence DESC, returns dicts</p>
<p>with ioc, source_type, query, and confidence fields.</p>
</div>
</details>
</li>
<li><code>_get_insert_stmt</code> (duckdb_store.py)
<details><summary>Sprint F264: Lazy-init prepared INSERT statement for canonical_findings.</summary>
<div class="doc-comment">
<p>Sprint F264: Lazy-init prepared INSERT statement for canonical_findings.</p>
<p></p>
<p>Returns the cached prepared statement for `_SQL_INSERT_SHADOW_FINDING`</p>
<p>if the underlying connection is unchanged. On reconnect the conn</p>
<p>identity differs and the statement is transparently re-prepared.</p>
<p></p>
<p>Fail-safe: if conn.prepare() raises, returns None and emits a</p>
<p>one-shot warning. The caller MUST fall back to</p>
<p>`conn.execute(self._SQL_INSERT_SHADOW_FINDING, params)` on None</p>
<p>so the canonical write path stays alive (CLAUDE.md invariant #5).</p>
<p></p>
<p>MUST be called on the worker thread (DuckDB conn is thread-affine).</p>
</div>
</details>
</li>
<li><code>classify_ingest_outcome</code> (duckdb_store.py)
<details><summary>Sprint 8AV: Classify the canonical reason string for an ingest outcome.</summary>
<div class="doc-comment">
<p>Sprint 8AV: Classify the canonical reason string for an ingest outcome.</p>
<p></p>
<p>Internal use - maps internal FindingQualityDecision or ActivationResult</p>
<p>to a human-readable reason string.</p>
<p></p>
<p>Returns one of:</p>
<p>- "accepted"                          - finding passed quality gate</p>
<p>- "low_information_rejected"         - entropy below threshold</p>
<p>- "in_memory_duplicate_rejected"     - hot-cache duplicate</p>
<p>- "persistent_duplicate_rejected"   - LMDB cross-source duplicate</p>
<p>- "other_rejected"                   - fail-open or unknown</p>
<p>- "error_rejected"                   - store/LMDB error</p>
</div>
</details>
</li>
<li><code>_rrf_fusion</code> (lancedb_store.py) — <span class="doc-comment-inline">Reciprocal Rank Fusion with robust keying — NumPy vectorized.</span></li>
<li><code>_embed_text</code> (rag_engine.py) — <span class="doc-comment-inline">Embed text using CoreML if available, fallback to MLX.</span></li>
<li><code>find_connected_batch</code> (graph_service.py)
<details><summary>P1-1: Batch version of find_entity_history — single DuckDB round-trip.</summary>
<div class="doc-comment">
<p>P1-1: Batch version of find_entity_history — single DuckDB round-trip.</p>
<p></p>
<p>Args:</p>
<p>values: List of IOC values to query.</p>
<p>max_hops: Maximum traversal depth (default 2).</p>
<p></p>
<p>Returns:</p>
<p>Dict mapping each input value to its list of connected node dicts.</p>
<p>Falls back to individual find_entity_history calls on error.</p>
</div>
</details>
</li>
<li><code>add_ioc_batch</code> (dedup.py)
<details><summary>Batch add IOCs to Rust MmapIocDedupStore.</summary>
<div class="doc-comment">
<p>Batch add IOCs to Rust MmapIocDedupStore.</p>
<p></p>
<p>Args:</p>
<p>items: List of (ioc_value, ioc_type, confidence) tuples.</p>
<p></p>
<p>Returns:</p>
<p>List[bool] — True = new (added), False = duplicate (updated stats).</p>
<p></p>
<p>P1-07 invariants:</p>
<p>- Always-on, bounded, fail-safe (same as is_duplicate_ioc_batch)</p>
</div>
</details>
</li>
<li><code>disambiguate</code> (entity_linker.py)
<details><summary>Disambiguate entity candidates using context.</summary>
<div class="doc-comment">
<p>Disambiguate entity candidates using context.</p>
<p></p>
<p>Args:</p>
<p>entity_text: Original entity text</p>
<p>candidates: List of candidate entities</p>
<p>context: Context for disambiguation</p>
<p></p>
<p>Returns:</p>
<p>Best matching candidate or None</p>
</div>
</details>
</li>
<li><code>compute_optimal_sub_vectors</code> (lancedb_auto_tuner.py)
<details><summary>Decide next ``num_sub_vectors`` based on recall and embedding dimension.</summary>
<div class="doc-comment">
<p>Decide next ``num_sub_vectors`` based on recall and embedding dimension.</p>
<p></p>
<p>P1-2 Enhancement: Adaptive compression ratio for IVF-PQ.</p>
<p></p>
<p>num_sub_vectors controls the compression ratio:</p>
<p>- More sub_vectors = smaller storage, faster search, lower accuracy</p>
<p>- Fewer sub_vectors = larger storage, slower search, higher accuracy</p>
<p></p>
<p>For 256d embeddings: 12 sub_vectors = ~21 bytes/vector (256/12 ≈ 21)</p>
<p>For 384d embeddings: 16 sub_vectors = ~24 bytes/vector (384/16 = 24)</p>
<p></p>
<p>Heuristic (mirrors partition logic — only act when there's a problem):</p>
<p>- **recall &lt; 0.80** → grow sub_vectors (reduce compression, improve recall)</p>
<p>- **recall ≥ 0.95 AND avg_search_ms &gt; SEARCH_MS_EXCESSIVE (50ms)</p>
<p>AND current &gt; MIN** → shrink (save memory, still accurate)</p>
<p>- otherwise → no change</p>
<p></p>
<p>Clamped to [MIN_NUM_SUB_VECTORS, MAX_NUM_SUB_VECTORS] and also bounded</p>
<p>by embedding_dim (can't have more sub_vectors than dimensions).</p>
</div>
</details>
</li>
<li><code>bulk_insert_arrow</code> (db.py)</li>
<li><code>insert_findings_bulk</code> (duckdb_store.py)
<details><summary>Bulk insert shadow findings. Returns number of successfully inserted records.</summary>
<div class="doc-comment">
<p>Bulk insert shadow findings. Returns number of successfully inserted records.</p>
<p>MUST be called on the worker thread.</p>
</div>
</details>
</li>
<li><code>search_similar_adaptive</code> (lancedb_store.py)
<details><summary>Hybrid search with adaptive reranking. API-compatible with LanceDBIdentityStore.</summary>
<div class="doc-comment">
<p>Hybrid search with adaptive reranking. API-compatible with LanceDBIdentityStore.</p>
<p></p>
<p>sqlite-vec limitation: no native FTS, no FlashRank/ColBERT reranker.</p>
<p>Falls back to pure ANN search with MLX cosine similarity fallback.</p>
<p></p>
<p>Args:</p>
<p>query_text: Query text (used for reranking context if available).</p>
<p>query_emb: Query embedding vector.</p>
<p>top_k: Number of results.</p>
<p></p>
<p>Returns:</p>
<p>List of ranked entity dicts.</p>
</div>
</details>
</li>
<li><code>_brute_force_search</code> (rag_engine.py) — <span class="doc-comment-inline">Brute-force search fallback.</span></li>
<li><code>_init_coreml_embedder</code> (rag_engine.py)
<details><summary>Initialize CoreML embedder via lazy import (compat seam).</summary>
<div class="doc-comment">
<p>Initialize CoreML embedder via lazy import (compat seam).</p>
<p></p>
<p>RAGEngine is grounding authority, NOT model owner.</p>
<p>CoreML model lifecycle stays in brain/model_manager.py.</p>
<p>This method is the ONLY entry point for model-plane coupling.</p>
</div>
</details>
</li>
<li><code>_build_feed_cluster_summary</code> (analyst_workbench.py) — <span class="doc-comment-inline">F225B: Summarize feed/public/CT cluster distribution from findings.</span></li>
<li><code>get_stats</code> (duckdb_store.py)
<details><summary>Sprint P2-B: Return store statistics for sprint report.</summary>
<div class="doc-comment">
<p>Sprint P2-B: Return store statistics for sprint report.</p>
<p></p>
<p>Returns duckdb_stats section: findings count, graph stats,UMA state.</p>
</div>
</details>
</li>
<li><code>_sync_record_hypothesis_feedback</code> (duckdb_store.py)
<details><summary>Sprint F203G: Insert a single hypothesis_feedback record.</summary>
<div class="doc-comment">
<p>Sprint F203G: Insert a single hypothesis_feedback record.</p>
<p></p>
<p>Thread-safe: MUST be called on the duckdb_worker thread.</p>
<p>Silently fails if store is closed or uninitialized.</p>
<p></p>
<p>Returns True if inserted, False otherwise.</p>
</div>
</details>
</li>
<li><code>_sync_query_consistency_check</code> (duckdb_store.py)
<details><summary>Sync - MUST be called on the worker thread. Uses read pool for parallelism.</summary>
<div class="doc-comment">
<p>Sync - MUST be called on the worker thread. Uses read pool for parallelism.</p>
<p></p>
<p>Sprint F192F §2: both sprint_scorecard and sprint_delta now use findings_per_minute.</p>
</div>
</details>
</li>
<li><code>_sync_record_hypothesis_tracking</code> (duckdb_store.py)</li>
<li><code>async_ingest_finding</code> (duckdb_store.py)
<details><summary>Sprint 8W: Quality-gated single-finding ingest.</summary>
<div class="doc-comment">
<p>Sprint 8W: Quality-gated single-finding ingest.</p>
<p></p>
<p>Layer ABOVE async_record_canonical_finding - applies quality gate first,</p>
<p>then delegates to legacy storage path on accept.</p>
<p></p>
<p>Quality gate is CPU-only, deterministic, and cheap.</p>
<p>Fail-open: if quality helpers raise, the finding is stored via legacy path.</p>
<p></p>
<p>Returns FindingQualityDecision when rejected/duplicate.</p>
<p>Returns ActivationResult on accept or fail-open.</p>
</div>
</details>
</li>
<li><code>deadletter_marker_count</code> (duckdb_store.py)
<details><summary>Sprint 8L: Return the number of deadletter_duckdb_sync:* markers in WAL LMDB.</summary>
<div class="doc-comment">
<p>Sprint 8L: Return the number of deadletter_duckdb_sync:* markers in WAL LMDB.</p>
<p></p>
<p>Cheap O(n) prefix scan.</p>
<p>Used for observability and monitoring.</p>
</div>
</details>
</li>
<li><code>_cleanup_orphaned_locks</code> (duckdb_store.py)
<details><summary>F11C-2: Remove orphaned DuckDB and GraphLockManager lock files at startup.</summary>
<div class="doc-comment">
<p>F11C-2: Remove orphaned DuckDB and GraphLockManager lock files at startup.</p>
<p></p>
<p>Called from async_initialize() before connecting. Uses the same stale</p>
<p>detection as GraphLockManager to avoid removing locks held by live processes.</p>
<p></p>
<p>DuckDB WAL lock path is: str(db_path) + ".lock"</p>
<p>GraphLockManager lock path is: db_path.with_suffix(".lock") — same as DuckDB!</p>
</div>
</details>
</li>
<li><code>_maybe_compact_blocking</code> (lancedb_store.py)
<details><summary>Run lancedb optimize/compact_files in calling thread. Fail-soft.</summary>
<div class="doc-comment">
<p>Run lancedb optimize/compact_files in calling thread. Fail-soft.</p>
<p></p>
<p>LanceDB &gt;= 0.4 API: Table.optimize() returns OptimizeResult.</p>
<p>Older API used compact_files(). Try optimize() first, then</p>
<p>compact_files(), else no-op. Never raises.</p>
</div>
</details>
</li>
<li><code>add_document</code> (rag_engine.py) — <span class="doc-comment-inline">Add document to index. Silently drops if MAX_BM25_DOCUMENTS reached.</span></li>
<li><code>_ensure_coreml_model</code> (rag_engine.py)
<details><summary>Convert ModernBERT to CoreML if not already done.</summary>
<div class="doc-comment">
<p>Convert ModernBERT to CoreML if not already done.</p>
<p>Returns True if conversion succeeded or already exists.</p>
</div>
</details>
</li>
<li><code>_ensure_filter</code> (dedup.py)
<details><summary>Lazy-init filter under fcntl.flock — race-free across processes.</summary>
<div class="doc-comment">
<p>Lazy-init filter under fcntl.flock — race-free across processes.</p>
<p></p>
<p>P1-10: Single import block, fcntl.flock prevents concurrent init race.</p>
<p>Fallback Python in-memory filter has no file race (no persistence).</p>
</div>
</details>
</li>
<li><code>__init__</code> (dedup.py)
<details><summary>Args:</summary>
<div class="doc-comment">
<p>Args:</p>
<p>dedup_lmdb_path: Path to dedup LMDB. If None, resolved from HLEDAC_DEDUP_LMDB_PATH env</p>
<p>or LMDB_ROOT/dedup.lmdb fallback.</p>
<p>semantic_lmdb_path: Path to semantic dedup LMDB. If None, uses default.</p>
<p>map_size: LMDB map size in bytes for dedup store.</p>
<p>max_keys: Max keys in dedup LMDB.</p>
</div>
</details>
</li>
<li><code>flush_buffers</code> (ioc_graph.py)
<details><summary>Bulk flush both buffers to Kuzu — call in WINDUP or at buffer limit.</summary>
<div class="doc-comment">
<p>Bulk flush both buffers to Kuzu — call in WINDUP or at buffer limit.</p>
<p></p>
<p>Returns:</p>
<p>ioc_created: count of IOC nodes NEWLY CREATED in this flush.</p>
<p>IOCs that already existed are updated (last_seen bump)</p>
<p>but NOT counted here. Call graph_stats() for total count.</p>
<p>obs_flushed: count of observation edges written to the graph.</p>
</div>
</details>
</li>
<li><code>_sync_upsert_target_memory</code> (duckdb_store.py) — <span class="doc-comment-inline">Sync upsert target memory - MUST be called on worker thread.</span></li>
<li><code>_sync_query_yield_trend</code> (duckdb_store.py) — <span class="doc-comment-inline">Sync - MUST be called on the worker thread. Uses read pool for parallelism.</span></li>
<li><code>vacuum_async</code> (duckdb_store.py)
<details><summary>Execute VACUUM ANALYZE on the DuckDB file to reclaim space after deletions.</summary>
<div class="doc-comment">
<p>Execute VACUUM ANALYZE on the DuckDB file to reclaim space after deletions.</p>
<p></p>
<p>Only available for file mode (_db_path is not None). Returns True on success.</p>
<p>Fail-safe: any error is logged and False is returned.</p>
</div>
</details>
</li>
<li><code>_ensure_usearch_index</code> (lancedb_store.py) — <span class="doc-comment-inline">Lazy load usearch index (experimental).</span></li>
<li><code>find_connections</code> (graph_rag.py)
<details><summary>Find connection paths between two entities (async, parallel node fetch).</summary>
<div class="doc-comment">
<p>Find connection paths between two entities (async, parallel node fetch).</p>
<p></p>
<p>M1 8GB: Runs BFS in Rust rayon io_pool (2 threads) to avoid blocking event loop.</p>
<p>Previously used asyncio.to_thread (default executor) → now uses run_in_io_pool.</p>
<p></p>
<p>Args:</p>
<p>entity1: First entity name</p>
<p>entity2: Second entity name</p>
<p>max_hops: Maximum hops to search</p>
<p></p>
<p>Returns:</p>
<p>List of connection paths</p>
</div>
</details>
</li>
<li><code>_extract_entities_from_node</code> (graph_rag.py)
<details><summary>Extract entity mentions from a node for novelty detection.</summary>
<div class="doc-comment">
<p>Extract entity mentions from a node for novelty detection.</p>
<p></p>
<p>Simple entity extraction based on capitalization patterns</p>
<p>and known entity markers.</p>
<p></p>
<p>Args:</p>
<p>node: Knowledge node to extract entities from</p>
<p></p>
<p>Returns:</p>
<p>Set of extracted entity strings</p>
</div>
</details>
</li>
<li><code>_calculate_narrative_confidence</code> (graph_rag.py)
<details><summary>Calculate narrative confidence score (0-1).</summary>
<div class="doc-comment">
<p>Calculate narrative confidence score (0-1).</p>
<p></p>
<p>Factors:</p>
<p>- Number of unique evidence sources</p>
<p>- Domain diversity</p>
<p>- Recency</p>
<p>- Echo penalty</p>
</div>
</details>
</li>
<li><code>multi_hop_search_streaming</code> (graph_rag.py)
<details><summary>Streaming version of multi-hop search that yields nodes as they are discovered.</summary>
<div class="doc-comment">
<p>Streaming version of multi-hop search that yields nodes as they are discovered.</p>
<p></p>
<p>Enables early processing of results before full traversal completes.</p>
<p>Uses asyncio.Queue for backpressure control.</p>
<p></p>
<p>Args:</p>
<p>query: Search query</p>
<p>hops: Number of hops to traverse (default: 2)</p>
<p>max_nodes: Maximum nodes to return (default: 20)</p>
<p></p>
<p>Yields:</p>
<p>Dict representing a discovered node with its metadata</p>
</div>
</details>
</li>
<li><code>_build_evidence_gaps</code> (analyst_workbench.py)
<details><summary>F225B: Identify evidence gaps from findings and source family summary.</summary>
<div class="doc-comment">
<p>F225B: Identify evidence gaps from findings and source family summary.</p>
<p></p>
<p>Checks for: feed-only (no public/CT corroboration), no high-confidence,</p>
<p>no multi-IOC type, missing graph connectivity.</p>
</div>
</details>
</li>
<li><code>store_persistent_dedup</code> (dedup.py)
<details><summary>Store a fingerprint → finding_id mapping in persistent dedup LMDB.</summary>
<div class="doc-comment">
<p>Store a fingerprint → finding_id mapping in persistent dedup LMDB.</p>
<p></p>
<p>P1-4: Also update Bloom filter for fast negative dedup.</p>
<p>F272: Lazy init on first use.</p>
<p></p>
<p>Args:</p>
<p>fp: 32-char BLAKE2b fingerprint hex string</p>
<p>finding_id: canonical finding ID</p>
</div>
</details>
</li>
<li><code>_maybe_compact_blocking</code> (ann_index.py) — <span class="doc-comment-inline">LanceDB compaction trigger (sync, fail-soft).</span></li>
<li><code>forget_weak_memories</code> (neuromorphic.py)
<details><summary>Remove weak memories below threshold strength.</summary>
<div class="doc-comment">
<p>Remove weak memories below threshold strength.</p>
<p></p>
<p>Args:</p>
<p>threshold: Minimum strength to keep</p>
<p></p>
<p>Returns:</p>
<p>Number of patterns forgotten</p>
</div>
</details>
</li>
<li><code>insert_findings_bulk_as_tuples</code> (duckdb_store.py)
<details><summary>Bulk insert shadow findings from pre-built tuple rows.</summary>
<div class="doc-comment">
<p>Bulk insert shadow findings from pre-built tuple rows.</p>
<p>MUST be called on the worker thread.</p>
<p>Returns number of successfully inserted records.</p>
</div>
</details>
</li>
<li><code>_sync_record_entity_observations_bulk</code> (duckdb_store.py) — <span class="doc-comment-inline">Sprint F350M: Bulk insert entity_observations.</span></li>
<li><code>initialize</code> (duckdb_store.py)
<details><summary>Initialize DuckDB connection synchronously (backward compat wrapper).</summary>
<div class="doc-comment">
<p>Initialize DuckDB connection synchronously (backward compat wrapper).</p>
<p></p>
<p>For async code prefer async_initialize().</p>
</div>
</details>
</li>
<li><code>_mlx_rerank</code> (lancedb_store.py)
<details><summary>Rerank candidates using MLX cosine similarity.</summary>
<div class="doc-comment">
<p>Rerank candidates using MLX cosine similarity.</p>
<p></p>
<p>P4.2: Uses module-level _cosine_sim_batch (compiled once at import).</p>
<p>Supports (B, D) × (N, D) → (B, N) for flexible batching.</p>
</div>
</details>
</li>
<li><code>close</code> (lancedb_store.py) — <span class="doc-comment-inline">Close database connection and cache.</span></li>
<li><code>calculate_network_metrics</code> (graph_rag.py)
<details><summary>Calculate comprehensive network metrics.</summary>
<div class="doc-comment">
<p>Calculate comprehensive network metrics.</p>
<p></p>
<p>From evidence_network_analyzer.py comments:</p>
<p>"Step 7: Calculate network metrics"</p>
<p>"Basic metrics"</p>
<p>"Clustering metrics"</p>
<p>"Path metrics"</p>
<p>"Evidence-specific metrics"</p>
<p></p>
<p>Returns:</p>
<p>Dictionary of network metrics</p>
</div>
</details>
</li>
<li><code>_build_ig_graph</code> (graph_rag.py) — <span class="doc-comment-inline">Build an igraph from adjacency list. M1-optimized, C-core.</span></li>
<li><code>_upsert_lancedb_entity_async</code> (graph_service.py)</li>
<li><code>resolve_aliases</code> (entity_linker.py)
<details><summary>Resolve entity aliases to canonical Wikidata labels.</summary>
<div class="doc-comment">
<p>Resolve entity aliases to canonical Wikidata labels.</p>
<p></p>
<p>Args:</p>
<p>entities: List of entity texts to resolve</p>
<p></p>
<p>Returns:</p>
<p>Dictionary mapping original text to canonical label</p>
</div>
</details>
</li>
<li><code>get_analytics_graph_for_synthesis</code> (graph_attachment.py)
<details><summary>Sprint 8VY: Read-only seam replacing store._ioc_graph fallback in _windup_synthesis().</summary>
<div class="doc-comment">
<p>Sprint 8VY: Read-only seam replacing store._ioc_graph fallback in _windup_synthesis().</p>
<p></p>
<p>PURPOSE</p>
<p>-------</p>
<p>Replaces the elif hasattr(store, "_ioc_graph") and store._ioc_graph fallback in</p>
<p>_windup_synthesis(). This is the Priority 2 / analytics-donor path for synthesis.</p>
<p></p>
<p>CONSUMER</p>
<p>--------</p>
<p>_windup_synthesis(): runner.inject_graph(store.get_analytics_graph_for_synthesis())</p>
<p></p>
<p>STORE IS NOT GRAPH TRUTH OWNER</p>
<p>-------------------------------</p>
<p>DuckDBShadowStore is NOT graph authority. This seam explicitly labels the</p>
<p>analytics donor backend. Callers must handle None.</p>
<p></p>
<p>CAPABILITY REQUIREMENTS</p>
<p>-----------------------</p>
<p>DuckPGQGraph (analytics donor) has: stats, get_top_nodes_by_degree, export_edge_list.</p>
<p>DuckPGQGraph does NOT have: export_stix_bundle, buffer_ioc, flush_buffers.</p>
<p>For STIX, use store.get_stix_graph() (Priority 1).</p>
<p></p>
<p>Returns:</p>
<p>Any: The attached analytics graph (DuckPGQGraph) or None.</p>
</div>
</details>
</li>
<li><code>insert_finding</code> (duckdb_store.py)</li>
<li><code>_sync_close_on_worker</code> (duckdb_store.py) — <span class="doc-comment-inline">Close all connections - MUST be called on the worker thread.</span></li>
<li><code>_sync_query_best_sprints</code> (duckdb_store.py)
<details><summary>Sync - MUST be called on the worker thread. Uses read pool for parallelism.</summary>
<div class="doc-comment">
<p>Sync - MUST be called on the worker thread. Uses read pool for parallelism.</p>
<p></p>
<p>Sprint F192F §2: uses findings_per_minute (matches sprint_scorecard naming).</p>
</div>
</details>
</li>
<li><code>_sync_query_worst_sprints</code> (duckdb_store.py)
<details><summary>Sync - MUST be called on the worker thread. Uses read pool for parallelism.</summary>
<div class="doc-comment">
<p>Sync - MUST be called on the worker thread. Uses read pool for parallelism.</p>
<p></p>
<p>Sprint F192F §2: uses findings_per_minute (matches sprint_scorecard naming).</p>
</div>
</details>
</li>
<li><code>_wal_write_deadletter_marker</code> (duckdb_store.py)</li>
<li><code>_extract_answer</code> (analyst_workbench.py)
<details><summary>Deterministic extractive answer from context chunks.</summary>
<div class="doc-comment">
<p>Deterministic extractive answer from context chunks.</p>
<p></p>
<p>Returns the longest contiguous text span that contains</p>
<p>the most question keywords. No model required.</p>
<p></p>
<p>Fail-soft: returns "No relevant information found." on any error.</p>
</div>
</details>
</li>
<li><code>_build_corroboration_summary</code> (analyst_workbench.py)
<details><summary>F225C: Build corroboration summary from findings source families.</summary>
<div class="doc-comment">
<p>F225C: Build corroboration summary from findings source families.</p>
<p></p>
<p>Uses summarize_chain_support if chains are available via the evidence_chain</p>
<p>module global registry, otherwise falls back to findings source_type.</p>
<p></p>
<p>Bounds: max MAX_CORROBORATION_SUMMARY lines.</p>
<p>Fail-soft: returns ("Corroboration unavailable",) on any error.</p>
</div>
</details>
</li>
<li><code>_init_semantic_dedup_cache</code> (dedup.py)
<details><summary>Initialize semantic dedup cache (Sprint F195).</summary>
<div class="doc-comment">
<p>Initialize semantic dedup cache (Sprint F195).</p>
<p></p>
<p>Memory-aware: skips init if RSS &gt; 6GB threshold.</p>
<p>Fail-soft: any exception stored in _semantic_dedup_boot_error.</p>
</div>
</details>
</li>
<li><code>wal_write_pending_sync_marker</code> (wal.py)
<details><summary>Write a pending-sync recovery marker to LMDB.</summary>
<div class="doc-comment">
<p>Write a pending-sync recovery marker to LMDB.</p>
<p></p>
<p>Marker key:  pending_duckdb_sync:{id}</p>
<p>Value:       same structure as WAL finding (id, query, source_type, confidence, ts)</p>
<p></p>
<p>Written ONLY when LMDB succeeded but DuckDB failed.</p>
<p>A future recovery sprint can find it via prefix scan and retry the DuckDB write.</p>
<p></p>
<p>Evicts oldest markers if at or above MAX_PENDING_SYNC_MARKERS bound.</p>
</div>
</details>
</li>
<li><code>compact</code> (wal.py)
<details><summary>Compact the WAL LMDB if interval OR write count threshold reached.</summary>
<div class="doc-comment">
<p>Compact the WAL LMDB if interval OR write count threshold reached.</p>
<p></p>
<p>Compaction is triggered when EITHER:</p>
<p>- Time since last compaction &gt;= _compact_interval_s (default: 1h)</p>
<p>- Writes since last compaction &gt;= _compact_write_threshold (default: 5000)</p>
<p>- WAL LMDB is available (not using unified store)</p>
<p></p>
<p>Returns compaction stats dict or None if skipped / unavailable.</p>
</div>
</details>
</li>
<li><code>_sync_query_sprint_trend</code> (duckdb_store.py) — <span class="doc-comment-inline">Sync query - MUST be called on the worker thread. Uses persistent _file_conn.</span></li>
<li><code>upsert_episode</code> (duckdb_store.py) — <span class="doc-comment-inline">Sprint 8UC B.2: Zapsat sprint epizodu pro budoucí recall.</span></li>
<li><code>_sync_upsert_global_entities</code> (duckdb_store.py)
<details><summary>Sync upsert global entities - MUST be called on worker thread.</summary>
<div class="doc-comment">
<p>Sync upsert global entities - MUST be called on worker thread.</p>
<p></p>
<p>Uses DuckDB's built-in file locking via access_mode='automatic'.</p>
<p>DuckDB handles crash-safety internally - no external lock file needed.</p>
</div>
</details>
</li>
<li><code>_store_envelope_payload</code> (duckdb_store.py)
<details><summary>Sprint F202A §2: Update LMDB WAL entry with envelope payload_text.</summary>
<div class="doc-comment">
<p>Sprint F202A §2: Update LMDB WAL entry with envelope payload_text.</p>
<p></p>
<p>Called after initial ingest when envelope is attached post-hoc.</p>
<p>Returns True if LMDB update succeeded.</p>
</div>
</details>
</li>
<li><code>_checkpoint_loop</code> (duckdb_store.py)
<details><summary>Background checkpoint task for DuckDB native WAL.</summary>
<div class="doc-comment">
<p>Background checkpoint task for DuckDB native WAL.</p>
<p></p>
<p>Runs every 300s (O3) to flush WAL to main database file, bounding WAL growth.</p>
<p>duckdb_autocheckpoint=262144 (256MB) provides a secondary safety valve between</p>
<p>runs. Fail-safe: any error is silently caught and logged.</p>
<p>Only active for file mode; _checkpoint_task is None for :memory: mode.</p>
</div>
</details>
</li>
<li><code>_initialize</code> (lancedb_store.py) — <span class="doc-comment-inline">Initialize database and table.</span></li>
<li><code>compute_similarity</code> (lancedb_store.py)
<details><summary>Compute cosine similarity between two embeddings.</summary>
<div class="doc-comment">
<p>Compute cosine similarity between two embeddings.</p>
<p></p>
<p>Args:</p>
<p>emb1: First embedding.</p>
<p>emb2: Second embedding.</p>
<p></p>
<p>Returns:</p>
<p>Cosine similarity score (0-1).</p>
</div>
</details>
</li>
<li><code>_label_propagation_igraph</code> (graph_rag.py)
<details><summary>Community detection via igraph C-core label propagation (5-10x faster).</summary>
<div class="doc-comment">
<p>Community detection via igraph C-core label propagation (5-10x faster).</p>
<p></p>
<p>Returns None on igraph unavailable / RAM constraint.</p>
</div>
</details>
</li>
<li><code>init_audit_schema</code> (db.py) — <span class="doc-comment-inline">Initialize audit events table in DuckDB.</span></li>
<li><code>get_connected_iocs_batch</code> (graph_attachment.py)
<details><summary>P1-1 fix: Batch version of get_connected_iocs for N+1 query optimization.</summary>
<div class="doc-comment">
<p>P1-1 fix: Batch version of get_connected_iocs for N+1 query optimization.</p>
<p>Returns dict mapping each value to its connected IOC list.</p>
<p></p>
<p>CAPABILITY REQUIREMENTS</p>
<p>-----------------------</p>
<p>Requires attached graph to implement find_connected_batch(values, max_hops) → dict.</p>
<p>DuckPGQGraph: has this method (P1-1 fix).</p>
<p>IOCGraph: does NOT have this method → returns {} (fail-open).</p>
</div>
</details>
</li>
<li><code>_init_synaptic_weights</code> (neuromorphic.py) — <span class="doc-comment-inline">Initialize sparse synaptic weight matrix.</span></li>
<li><code>_encode_pattern</code> (neuromorphic.py) — <span class="doc-comment-inline">Encode arbitrary data into a neuron activation vector.</span></li>
<li><code>query_findings</code> (duckdb_store.py) — <span class="doc-comment-inline">Select recent shadow findings. Returns list of dicts.</span></li>
<li><code>ensure_connected</code> (duckdb_store.py)
<details><summary>Lazy connection init — called on first actual query.</summary>
<div class="doc-comment">
<p>Lazy connection init — called on first actual query.</p>
<p></p>
<p>When lazy=True (default): defers actual DuckDB connection to this method.</p>
<p>When lazy=False: no-op (already connected via async_initialize).</p>
<p></p>
<p>This is the on-demand bootstrap that enables ~0s sprint boot with no findings.</p>
<p>All async write methods call ensure_connected() before their run_in_executor.</p>
<p></p>
<p>Barrier semantics (Sprint DuckDB Lazy Init F265X):</p>
<p>In lazy mode, _startup_ready is cleared here BEFORE connecting, then</p>
<p>set again AFTER connecting. This ensures writes always wait for the</p>
<p>connection to be ready (no spurious proceeds before connection exists).</p>
</div>
</details>
</li>
<li><code>async_record_shadow_findings_batch</code> (duckdb_store.py)</li>
<li><code>async_record_source_hit</code> (duckdb_store.py)</li>
<li><code>async_ingest_cooccurrence_batch</code> (duckdb_store.py)
<details><summary>Batch upsert IOC co-occurrence pairs into DuckDB.</summary>
<div class="doc-comment">
<p>Batch upsert IOC co-occurrence pairs into DuckDB.</p>
<p></p>
<p>Replaces raw sqlite3 DELETE+INSERT in IOCooccurrenceMiner.persist().</p>
<p>Uses DELETE + per-item INSERT (support &gt;= 2 filter applied by caller).</p>
<p></p>
<p>Args:</p>
<p>pairs: List of dicts with keys:</p>
<p>ioc_a, ioc_b, ioc_type_a, ioc_type_b,</p>
<p>support, confidence, score, last_seen</p>
<p></p>
<p>Returns:</p>
<p>True on success, False on failure.</p>
</div>
</details>
</li>
<li><code>_sync_ingest_cooccurrence_batch</code> (duckdb_store.py) — <span class="doc-comment-inline">Synchronous batch upsert for IOC co-occurrence pairs.</span></li>
<li><code>_sync_load_cooccurrence</code> (duckdb_store.py) — <span class="doc-comment-inline">Synchronous load of IOC co-occurrence pairs from DuckDB.</span></li>
<li><code>async_query_findings_by_keywords</code> (duckdb_store.py)
<details><summary>P1-2: Read canonical_findings rows matching ANY of the given keywords.</summary>
<div class="doc-comment">
<p>P1-2: Read canonical_findings rows matching ANY of the given keywords.</p>
<p>Uses OR across keywords so "ransomware breach" matches findings</p>
<p>containing either "ransomware" OR "breach".</p>
<p>Used by run_runtime_pivot_prelude() for cross-sprint seed extraction</p>
<p>when the full query string has no direct match.</p>
<p></p>
<p>Args:</p>
<p>keywords: List of keywords to search in query/title/payload_text.</p>
<p>limit: Max rows to return (default 1000).</p>
<p></p>
<p>Returns:</p>
<p>list[dict] with keys: id, query, source_type, title, payload_text, ts.</p>
<p>Fail-soft: returns [] on any error.</p>
</div>
</details>
</li>
<li><code>_sync_query_findings_by_keywords_impl</code> (duckdb_store.py) — <span class="doc-comment-inline">Sync - MUST be called on worker thread. Internal: actual query without cache.</span></li>
<li><code>_embed_single</code> (lancedb_store.py) — <span class="doc-comment-inline">Embed single text via current embedder (for indexing - uses embed_document).</span></li>
<li><code>_maybe_compact_async</code> (lancedb_store.py) — <span class="doc-comment-inline">Non-blocking compaction trigger; actual work in executor.</span></li>
<li><code>get_citation_context</code> (lancedb_store.py)
<details><summary>Get papers that cite or are cited by the given paper.</summary>
<div class="doc-comment">
<p>Get papers that cite or are cited by the given paper.</p>
<p></p>
<p>Args:</p>
<p>paper_id: Paper ID to find citation context for.</p>
<p>max_papers: Max papers to return.</p>
<p></p>
<p>Returns:</p>
<p>List of related AcademicPaper instances.</p>
</div>
</details>
</li>
<li><code>ask_with_reasoning</code> (graph_rag.py)
<details><summary>Ask a question with multi-hop reasoning.</summary>
<div class="doc-comment">
<p>Ask a question with multi-hop reasoning.</p>
<p></p>
<p>Returns both the facts and the reasoning paths.</p>
<p></p>
<p>Args:</p>
<p>question: Question to ask</p>
<p>hops: Number of hops to traverse</p>
<p>max_nodes: Maximum nodes to return</p>
<p></p>
<p>Returns:</p>
<p>Dictionary with facts and reasoning paths</p>
</div>
</details>
</li>
<li><code>analyze_key_paths</code> (graph_rag.py)
<details><summary>Analyze key paths between two nodes (async).</summary>
<div class="doc-comment">
<p>Analyze key paths between two nodes (async).</p>
<p></p>
<p>From evidence_network_analyzer.py comments:</p>
<p>"Step 6: Analyze key paths in the network"</p>
<p>"Find shortest paths between central nodes"</p>
<p>"Look for paths that might be important reasoning chains"</p>
<p>"Calculate path confidence"</p>
<p></p>
<p>Args:</p>
<p>start_node_id: Starting node</p>
<p>target_node_id: Target node</p>
<p>max_hops: Maximum path length</p>
<p></p>
<p>Returns:</p>
<p>List of paths with confidence scores</p>
</div>
</details>
</li>
<li><code>save_index</code> (rag_engine.py)
<details><summary>Save index to disk.</summary>
<div class="doc-comment">
<p>Save index to disk.</p>
<p></p>
<p>Args:</p>
<p>path: Path to save index. Uses index_path from init if not provided.</p>
</div>
</details>
</li>
<li><code>__init__</code> (quality_assessment.py)</li>
<li><code>record_rejection</code> (quality_assessment.py)</li>
<li><code>compute_context_similarity</code> (entity_linker.py)
<details><summary>Compute semantic similarity between entity description and context.</summary>
<div class="doc-comment">
<p>Compute semantic similarity between entity description and context.</p>
<p></p>
<p>Uses rapidfuzz for fuzzy matching (lightweight, no ML models).</p>
<p></p>
<p>Args:</p>
<p>entity_desc: Entity description</p>
<p>context: Context text</p>
<p></p>
<p>Returns:</p>
<p>Similarity score (0-1)</p>
</div>
</details>
</li>
<li><code>close</code> (semantic_store.py) — <span class="doc-comment-inline">TEARDOWN — final flush + close connections.</span></li>
<li><code>__init__</code> (ioc_dedup_adapter.py)</li>
<li><code>_persist_lmdb</code> (ioc_dedup_adapter.py)
<details><summary>Persist current state to LMDB.</summary>
<div class="doc-comment">
<p>Persist current state to LMDB.</p>
<p>Called on advance_sprint() and during graceful shutdown.</p>
</div>
</details>
</li>
<li><code>_sync_get_research_sessions_by_sprint</code> (duckdb_store.py) — <span class="doc-comment-inline">Sprint F350M: Fetch research_sessions by sprint_id.</span></li>
<li><code>_sync_get_recent_research_sessions</code> (duckdb_store.py) — <span class="doc-comment-inline">Sprint F350M: Fetch recent research_sessions.</span></li>
<li><code>_sync_query_findings_by_text_impl</code> (duckdb_store.py) — <span class="doc-comment-inline">Sync - MUST be called on worker thread. Internal: actual query without cache.</span></li>
<li><code>_sync_upsert_scorecard</code> (duckdb_store.py) — <span class="doc-comment-inline">Sync upsert scorecard - MUST be called on worker thread.</span></li>
<li><code>_sync_query_high_value_ranking</code> (duckdb_store.py) — <span class="doc-comment-inline">Sync - MUST be called on the worker thread. Uses read pool for parallelism.</span></li>
<li><code>_sync_verify_duckdb_record</code> (duckdb_store.py)
<details><summary>Sprint 8H: Fresh read-back verification from a NEW DuckDB connection.</summary>
<div class="doc-comment">
<p>Sprint 8H: Fresh read-back verification from a NEW DuckDB connection.</p>
<p></p>
<p>Called after write commit to confirm the record is durable.</p>
<p>Uses a non-read-only fresh connection so the WAL is flushed.</p>
<p>MUST be called on the worker thread.</p>
</div>
</details>
</li>
<li><code>_store_persistent_dedup</code> (duckdb_store.py)
<details><summary>Store a fingerprint -&gt; finding_id mapping in persistent dedup LMDB.</summary>
<div class="doc-comment">
<p>Store a fingerprint -&gt; finding_id mapping in persistent dedup LMDB.</p>
<p></p>
<p>DEPRECATED (Sprint F222): Delegates to DedupManager.store_persistent_dedup().</p>
<p>Kept for backward compat during migration - remove when all callers migrated.</p>
<p></p>
<p>P1-4: Also update Bloom filter in DedupManager when available.</p>
<p>Falls back to store._dedup_lmdb directly for backward compat with tests</p>
<p>that mock store._dedup_lmdb without going through DedupManager.</p>
</div>
</details>
</li>
<li><code>_find_paths_bfs</code> (graph_rag.py)
<details><summary>BFS to find paths between nodes (runs in thread pool).</summary>
<div class="doc-comment">
<p>BFS to find paths between nodes (runs in thread pool).</p>
<p></p>
<p>Returns:</p>
<p>List of connection paths</p>
</div>
</details>
</li>
<li><code>save_hnsw_index</code> (rag_engine.py)
<details><summary>Save HNSW index to disk.</summary>
<div class="doc-comment">
<p>Save HNSW index to disk.</p>
<p></p>
<p>Args:</p>
<p>path: Path to save index. Uses config.hnsw_index_path if not provided.</p>
</div>
</details>
</li>
<li><code>load_hnsw_index</code> (rag_engine.py)
<details><summary>Load HNSW index from disk.</summary>
<div class="doc-comment">
<p>Load HNSW index from disk.</p>
<p></p>
<p>Args:</p>
<p>path: Path to load index from. Uses config.hnsw_index_path if not provided.</p>
</div>
</details>
</li>
<li><code>_compute_rg_stats_fallback</code> (duckdb_store.py) — <span class="doc-comment-inline">Compute row-group stats using PyArrow (fallback).</span></li>
<li><code>_sync_query_source_leaderboard</code> (duckdb_store.py) — <span class="doc-comment-inline">Sync query - MUST be called on the worker thread. Uses persistent _file_conn.</span></li>
<li><code>_resolve_path</code> (duckdb_store.py)
<details><summary>Resolve _db_path and _temp_dir based on RAMDISK availability.</summary>
<div class="doc-comment">
<p>Resolve _db_path and _temp_dir based on RAMDISK availability.</p>
<p></p>
<p>RAMDISK_ACTIVE=True:  DUCKDB_STORE_ROOT / "shadow_analytics.duckdb", temp = RAMDISK_ROOT / "duckdb_tmp"</p>
<p>RAMDISK_ACTIVE=False: DUCKDB_STORE_ROOT / "analytics.duckdb",     temp = None (no spill to SSD)</p>
<p></p>
<p>Sprint F265B: All hot DuckDB data now uses DUCKDB_STORE_ROOT (co-located with LMDB_STORE_ROOT</p>
<p>for atomic WAL operations). DUCKDB_STORE_ROOT defaults to SPRINT_STORE_ROOT.parent / "duckdb_store"</p>
<p>which is ~/.hledac/duckdb_store — or RAMDISK-backed when HLEDAC_RAMDISK/HLEDAC_DUCKDB_STORE is set.</p>
</div>
</details>
</li>
<li><code>async_initialize_schema</code> (duckdb_store.py)
<details><summary>F275: Explicit schema initialization - creates/touches the DB file and</summary>
<div class="doc-comment">
<p>F275: Explicit schema initialization - creates/touches the DB file and</p>
<p>runs CREATE TABLE IF NOT EXISTS for all canonical tables.</p>
<p></p>
<p>Safe to call multiple times (idempotent). Does NOT run full</p>
<p>async_initialize() - no WAL replay, no DedupManager init.</p>
<p>This is the minimal init path for the zero-findings case.</p>
<p></p>
<p>Returns True if schema is ready, False on error.</p>
</div>
</details>
</li>
<li><code>_sync_query_sprint_ioc_summary_impl</code> (duckdb_store.py) — <span class="doc-comment-inline">Sync - MUST be called on worker thread. Internal: actual query without cache.</span></li>
<li><code>async_record_hypothesis_feedback</code> (duckdb_store.py)
<details><summary>Sprint F203G: Record a single hypothesis_feedback entry.</summary>
<div class="doc-comment">
<p>Sprint F203G: Record a single hypothesis_feedback entry.</p>
<p></p>
<p>Thread-safe, non-blocking - runs on duckdb_worker via run_in_executor.</p>
<p>Silently fails if store is closed or uninitialized.</p>
<p></p>
<p>Args:</p>
<p>record: HypothesisFeedbackRecord (frozen dataclass) with fields:</p>
<p>id, target_id, pivot_type, ioc_type, produced_count,</p>
<p>accepted_count, signal_value, ts.</p>
<p></p>
<p>Returns:</p>
<p>True if recorded, False otherwise.</p>
</div>
</details>
</li>
<li><code>_sync_read_envelope</code> (duckdb_store.py)
<details><summary>Sprint F202A §3: Read and deserialize envelope from LMDB WAL entry.</summary>
<div class="doc-comment">
<p>Sprint F202A §3: Read and deserialize envelope from LMDB WAL entry.</p>
<p></p>
<p>Returns None if finding doesn't exist or has no valid envelope.</p>
<p>Fail-soft: does not raise.</p>
</div>
</details>
</li>
<li><code>_flush_writeback</code> (lancedb_store.py) — <span class="doc-comment-inline">Flush writeback buffer to LMDB — single batch transaction.</span></li>
<li><code>_rank_facts_with_novelty</code> (graph_rag.py)
<details><summary>Rank facts considering novelty score.</summary>
<div class="doc-comment">
<p>Rank facts considering novelty score.</p>
<p></p>
<p>Args:</p>
<p>facts: List of facts to rank</p>
<p></p>
<p>Returns:</p>
<p>Ranked list with novelty bonus</p>
</div>
</details>
</li>
<li><code>query</code> (rag_engine.py)
<details><summary>Procesovat RAG query.</summary>
<div class="doc-comment">
<p>Procesovat RAG query.</p>
<p></p>
<p>Args:</p>
<p>query: Uživatelský dotaz</p>
<p>context_chunks: Kontextové chunky</p>
<p>use_compression: Použít kompresi (auto-detect pokud None)</p>
<p>secure: Použít secure enclave</p>
<p></p>
<p>Returns:</p>
<p>Výsledek RAG query</p>
</div>
</details>
</li>
<li><code>hybrid_retrieve_with_hnsw</code> (rag_engine.py)
<details><summary>Retrieve relevant documents using hybrid search (dense + sparse) with optional HNSW.</summary>
<div class="doc-comment">
<p>Retrieve relevant documents using hybrid search (dense + sparse) with optional HNSW.</p>
<p></p>
<p>This is an enhanced version of hybrid_retrieve that uses HNSW for fast</p>
<p>dense retrieval when available.</p>
<p></p>
<p>Args:</p>
<p>query: Search query</p>
<p>documents: List of documents to search (only needed if HNSW not built)</p>
<p>top_k: Number of results to return</p>
<p>filters: Optional metadata filters</p>
<p>use_hnsw: Override HNSW usage (None = use config setting)</p>
<p></p>
<p>Returns:</p>
<p>List of retrieved chunks with scores</p>
</div>
</details>
</li>
<li><code>query_vectors</code> (analyst_workbench.py)
<details><summary>Query LanceDB text index for ANN similar vectors.</summary>
<div class="doc-comment">
<p>Query LanceDB text index for ANN similar vectors.</p>
<p></p>
<p>Args:</p>
<p>query_embedding: 256d numpy array (MRL dimension for text)</p>
<p>k: Number of results (capped to MAX_TOP_K)</p>
<p></p>
<p>Returns:</p>
<p>List of (finding_id, similarity_score) tuples ordered by similarity.</p>
</div>
</details>
</li>
<li><code>upsert_identity_edge</code> (graph_service.py)</li>
<li><code>__init__</code> (entity_linker.py)
<details><summary>Initialize EntityLinker.</summary>
<div class="doc-comment">
<p>Initialize EntityLinker.</p>
<p></p>
<p>Args:</p>
<p>wikidata_endpoint: SPARQL endpoint URL</p>
<p>cache_size: Maximum cache entries</p>
<p>cache_ttl: Cache TTL in seconds</p>
<p>max_candidates: Maximum candidates to fetch per entity</p>
<p>confidence_threshold: Minimum confidence for linking</p>
<p>request_timeout: HTTP request timeout in seconds</p>
<p>use_gliner: Whether to use GLiNER for NER if available</p>
</div>
</details>
</li>
<li><code>canonicalize_entity</code> (entity_linker.py)
<details><summary>Canonicalize entity text to a standard form.</summary>
<div class="doc-comment">
<p>Canonicalize entity text to a standard form.</p>
<p></p>
<p>Args:</p>
<p>entity_text: Original entity text</p>
<p>entity_type: Entity type</p>
<p></p>
<p>Returns:</p>
<p>Canonicalized entity text</p>
</div>
</details>
</li>
<li><code>tune_if_due_async</code> (lancedb_auto_tuner.py)
<details><summary>Async-safe wrapper — runs the synchronous ``tune_if_due`` in executor.</summary>
<div class="doc-comment">
<p>Async-safe wrapper — runs the synchronous ``tune_if_due`` in executor.</p>
<p></p>
<p>P1-2 Enhancement: Passes current_num_sub_vectors through to the</p>
<p>synchronous core so both IVF-PQ knobs are tuned.</p>
<p></p>
<p>Use this from async code paths (e.g. ``LanceDBIdentityStore.add_entity``).</p>
<p>Off-loads the blocking ``to_polars``, ``search``, ``create_index`` calls</p>
<p>to the default executor so the event loop stays responsive.</p>
</div>
</details>
</li>
<li><code>close</code> (ioc_graph.py)
<details><summary>Gracefully close the Kuzu connection.</summary>
<div class="doc-comment">
<p>Gracefully close the Kuzu connection.</p>
<p></p>
<p>Flushes any pending IOC and observation buffers before shutdown</p>
<p>to prevent silent data loss when close() is called without</p>
<p>an intervening WINDUP phase.</p>
<p></p>
<p>close() is idempotent and data-safe: pending buffered writes are</p>
<p>flushed BEFORE _closed is set to True, so no buffered IOC or</p>
<p>observation data is lost on normal shutdown.</p>
</div>
</details>
</li>
<li><code>wal_write_finding</code> (wal.py)
<details><summary>Write a finding to the WAL LMDB (sync, no await).</summary>
<div class="doc-comment">
<p>Write a finding to the WAL LMDB (sync, no await).</p>
<p></p>
<p>LMDB key:   finding:{id}</p>
<p>Value:      serialized dict with id, query, source_type, confidence, ts</p>
<p></p>
<p>Returns True if LMDB write succeeded.</p>
</div>
</details>
</li>
<li><code>num_row_groups</code> (duckdb_store.py) — <span class="doc-comment-inline">Return number of row-groups (metadata only, no data read).</span></li>
<li><code>_iter_rust_filtered</code> (duckdb_store.py) — <span class="doc-comment-inline">Rust-accelerated row-group iteration via IPC bytes with filter.</span></li>
<li><code>for_testing</code> (duckdb_store.py)
<details><summary>Create a DuckDB store for test isolation.</summary>
<div class="doc-comment">
<p>Create a DuckDB store for test isolation.</p>
<p></p>
<p>Not for production use - provides a predictable temp path that is</p>
<p>cleaned up by the caller after the test.</p>
<p></p>
<p>Args:</p>
<p>name:  Identifier used in the temp path (default "test").</p>
<p>Pass unique names per test to avoid collisions.</p>
<p>temp_dir:  Optional temp directory. If None, a temp dir is created</p>
<p>via tempfile.mkdtemp and the caller is responsible for</p>
<p>cleaning it up.</p>
</div>
</details>
</li>
<li><code>_conn</code> (duckdb_store.py)
<details><summary>Return the active write connection (MODE A file or MODE B persistent).</summary>
<div class="doc-comment">
<p>Return the active write connection (MODE A file or MODE B persistent).</p>
<p></p>
<p>F265X-LAZY-FIX: triggers ensure_connected() if connection is not yet</p>
<p>established in lazy mode. In lazy mode, __aenter__ sets _initialized=True</p>
<p>but leaves _file_conn=None and _persistent_conn=None. First actual use</p>
<p>via this property establishes the connection on-demand.</p>
<p></p>
<p>P2-22 FIX: Removed redundant _prewarm_file_conn() call from hot path.</p>
<p>The prewarm SELECT 1 was being issued on EVERY _conn() call in the</p>
<p>hot ingest loop (~millions of times), adding ~0.1-0.3ms per call</p>
<p>overhead for no benefit after the first prewarm. Prewarm is now</p>
<p>called exactly once after initial connection in _init_connection().</p>
</div>
</details>
</li>
<li><code>async_load_cooccurrence</code> (duckdb_store.py)
<details><summary>Load IOC co-occurrence pairs from DuckDB.</summary>
<div class="doc-comment">
<p>Load IOC co-occurrence pairs from DuckDB.</p>
<p></p>
<p>Replaces raw sqlite3 SELECT in IOCooccurrenceMiner._load_sync().</p>
<p></p>
<p>Args:</p>
<p>limit: Max pairs to load (default 100_000).</p>
<p></p>
<p>Returns:</p>
<p>List of dicts with keys:</p>
<p>ioc_a, ioc_b, ioc_type_a, ioc_type_b,</p>
<p>support, confidence, score, last_seen</p>
</div>
</details>
</li>
<li><code>async_query_findings_by_text</code> (duckdb_store.py)
<details><summary>F251A: Read canonical_findings rows matching a text/keyword pattern.</summary>
<div class="doc-comment">
<p>F251A: Read canonical_findings rows matching a text/keyword pattern.</p>
<p>Used by run_runtime_pivot_prelude() for offline memory seed extraction</p>
<p>when a text query has no direct IOC seeds.</p>
<p></p>
<p>Args:</p>
<p>like_pattern: Keyword to search in query/title/payload_text.</p>
<p>limit: Max rows to return (default 1000).</p>
<p></p>
<p>Returns:</p>
<p>list[dict] with keys: id, query, source_type, title, payload_text, ts.</p>
<p>Fail-soft: returns [] on any error.</p>
</div>
</details>
</li>
<li><code>async_upsert_target_memory</code> (duckdb_store.py)
<details><summary>Sprint F204D: Insert or update target memory from a TargetMemory.</summary>
<div class="doc-comment">
<p>Sprint F204D: Insert or update target memory from a TargetMemory.</p>
<p></p>
<p>Thread-safe, non-blocking - runs on duckdb_worker via run_in_executor.</p>
<p>Silently fails if store is closed or uninitialized.</p>
<p></p>
<p>F206H FIX: Previously accepted TargetMemoryUpdate and silently failed</p>
<p>(type mismatch with _sync_upsert_target_memory which expects TargetMemory).</p>
<p>Now accepts TargetMemory directly - caller (SprintScheduler) passes</p>
<p>the already-merged memory from TargetMemoryService.merge_update().</p>
</div>
</details>
</li>
<li><code>_mmr</code> (lancedb_store.py) — <span class="doc-comment-inline">Maximal Marginal Relevance - reduce duplicates in results.</span></li>
<li><code>search_similar</code> (lancedb_store.py)
<details><summary>ANN search for similar entities. API-compatible with LanceDBIdentityStore.</summary>
<div class="doc-comment">
<p>ANN search for similar entities. API-compatible with LanceDBIdentityStore.</p>
<p></p>
<p>Args:</p>
<p>embedding: Query embedding vector.</p>
<p>text_hint: Optional text for FTS (ignored — sqlite-vec has no FTS).</p>
<p>threshold: Similarity threshold (0-1). Applied as 1 - vec0_distance.</p>
<p>limit: Maximum results.</p>
<p>query_type: "auto"/"vector"/"fts"/"hybrid" (fts/hybrid fall back to vector).</p>
<p></p>
<p>Returns:</p>
<p>List of matching entities with id, aliases, similarity, first_seen, last_seen.</p>
</div>
</details>
</li>
<li><code>search</code> (rag_engine.py) — <span class="doc-comment-inline">Search documents using BM25</span></li>
<li><code>_generate_embeddings</code> (rag_engine.py)
<details><summary>Generate embeddings using UnifiedEmbeddingManager (MLX primary).</summary>
<div class="doc-comment">
<p>Generate embeddings using UnifiedEmbeddingManager (MLX primary).</p>
<p></p>
<p>Priority: MLXEmbeddingManager (ModernBERT) → SHA256 hash fallback.</p>
<p>FastEmbed removed — unified MLX is faster on M1 8GB.</p>
<p></p>
<p>M1 8GB: MLXEmbeddingManager runs on GPU via unified memory, no CPU transfer.</p>
</div>
</details>
</li>
<li><code>query_semantic</code> (analyst_workbench.py)
<details><summary>Query SemanticStore (FastEmbed) for finding_ids by keyword.</summary>
<div class="doc-comment">
<p>Query SemanticStore (FastEmbed) for finding_ids by keyword.</p>
<p></p>
<p>Args:</p>
<p>query: Search query</p>
<p>limit: Max results (capped to MAX_TOP_K)</p>
<p></p>
<p>Returns:</p>
<p>List of finding_ids ordered by semantic relevance.</p>
</div>
</details>
</li>
<li><code>get_evidence_chain</code> (analyst_workbench.py)
<details><summary>F203D: Retrieve the evidence chain for a given finding_id.</summary>
<div class="doc-comment">
<p>F203D: Retrieve the evidence chain for a given finding_id.</p>
<p></p>
<p>Chains are accumulated by the EvidenceChainBuilder during sprint teardown</p>
<p>and stored as a sprint artifact. This method queries the module-level</p>
<p>registry for the chain.</p>
<p></p>
<p>Args:</p>
<p>finding_id: The finding ID to look up.</p>
<p></p>
<p>Returns:</p>
<p>EvidenceChain if found, None otherwise.</p>
<p>Returns None if no sprint has been run yet or if the finding_id</p>
<p>is not part of any tracked chain.</p>
</div>
</details>
</li>
<li><code>_pattern_completion</code> (neuromorphic.py) — <span class="doc-comment-inline">Auto-associative pattern completion using synaptic weights.</span></li>
<li><code>consolidate_memories</code> (neuromorphic.py)
<details><summary>Consolidate strong working memories to long-term memory.</summary>
<div class="doc-comment">
<p>Consolidate strong working memories to long-term memory.</p>
<p></p>
<p>Args:</p>
<p>strength_threshold: Minimum strength to consolidate</p>
<p></p>
<p>Returns:</p>
<p>Number of patterns consolidated</p>
</div>
</details>
</li>
<li><code>_memory_replay</code> (neuromorphic.py)
<details><summary>Strengthen memories through replay (sleep-like consolidation).</summary>
<div class="doc-comment">
<p>Strengthen memories through replay (sleep-like consolidation).</p>
<p></p>
<p>Args:</p>
<p>n_replays: Number of memory replays</p>
</div>
</details>
</li>
<li><code>record_step</code> (evidence_chain.py)
<details><summary>Record a processing step into the chain for root_finding_id.</summary>
<div class="doc-comment">
<p>Record a processing step into the chain for root_finding_id.</p>
<p></p>
<p>If no chain exists for root_finding_id, one is created with the root</p>
<p>as the first (ingest) step. Subsequent calls add derivative steps.</p>
<p></p>
<p>Silently drops steps once MAX_CHAIN_DEPTH or MAX_CHAINS_PER_SPRINT is reached.</p>
</div>
</details>
</li>
<li><code>_sync_get_entity_observations_by_entity</code> (duckdb_store.py) — <span class="doc-comment-inline">Sprint F350M: Fetch entity_observations by entity_value.</span></li>
<li><code>__aenter__</code> (duckdb_store.py)
<details><summary>Async context manager entry - initializes the store.</summary>
<div class="doc-comment">
<p>Async context manager entry - initializes the store.</p>
<p></p>
<p>Usage:</p>
<p>async with DuckDBShadowStore() as store:</p>
<p>await store.async_insert_finding(...)</p>
<p># aclose() called automatically on exit</p>
<p></p>
<p>Sprint DuckDB Lazy Init (F265X): when lazy=True (default), this returns</p>
<p>immediately without connecting. Connection is deferred to the first actual</p>
<p>query via ensure_connected(). This saves ~1-2s from sprint boot.</p>
</div>
</details>
</li>
<li><code>submit_findings</code> (duckdb_store.py)
<details><summary>Fire-and-forget async write — delegates directly to async_ingest_findings_batch().</summary>
<div class="doc-comment">
<p>Fire-and-forget async write — delegates directly to async_ingest_findings_batch().</p>
<p></p>
<p>async_ingest_findings_batch() has its own built-in Arrow pipeline batching</p>
<p>(1024-item chunks, 4-slot pipeline queue, concurrent WAL+DuckDB via asyncio.gather),</p>
<p>so no separate coalescer layer is needed.</p>
<p></p>
<p>NOTE: findings list must not be mutated after this call returns.</p>
<p>Caller is responsible for ensuring this.</p>
<p></p>
<p>Returns: None (fire-and-forget async write).</p>
</div>
</details>
</li>
<li><code>_sync_query_source_mix_trend</code> (duckdb_store.py) — <span class="doc-comment-inline">Sync - MUST be called on the worker thread. Uses read pool for parallelism.</span></li>
<li><code>_wal_write_finding</code> (duckdb_store.py)
<details><summary>Sprint 8A: Write a single finding to LMDB WAL (sync, no await).</summary>
<div class="doc-comment">
<p>Sprint 8A: Write a single finding to LMDB WAL (sync, no await).</p>
<p></p>
<p>LMDB key format:  finding:{id}</p>
<p>Value: serialized dict with id, query, source_type, confidence, ts</p>
<p></p>
<p>Returns True if LMDB write succeeded.</p>
<p></p>
<p>Delegation: Sprint F233A micro-cleanup - routes through WALManager</p>
<p>to eliminate the residual direct LMDB WAL path.</p>
</div>
</details>
</li>
<li><code>__init__</code> (lancedb_store.py)
<details><summary>Initialize LanceDB identity store.</summary>
<div class="doc-comment">
<p>Initialize LanceDB identity store.</p>
<p></p>
<p>Args:</p>
<p>uri: Path to LanceDB database.</p>
<p>orchestrator: Optional orchestrator reference for memory context.</p>
</div>
</details>
</li>
<li><code>ensure_index</code> (lancedb_store.py) — <span class="doc-comment-inline">Create index with respect to available RAM and thermal state.</span></li>
<li><code>_pack_query_to_binary</code> (lancedb_store.py)
<details><summary>Pack float32 query vector to packed binary bytes.</summary>
<div class="doc-comment">
<p>Pack float32 query vector to packed binary bytes.</p>
<p></p>
<p>Matches the big-endian packing used in _load_embeddings_to_mlx:</p>
<p>signs = (emb &gt; 0).astype(uint8)   -- 1 if &gt;= 0, 0 if &lt; 0</p>
<p>packed[i] = bits[i*8]&lt;&lt;7 | bits[i*8+1]&lt;&lt;6 | ... | bits[i*8+7]&lt;&lt;0</p>
<p></p>
<p>Returns:</p>
<p>Packed bytes (num_bytes = (dim + 7) // 8).</p>
</div>
</details>
</li>
<li><code>initialize</code> (lancedb_store.py) — <span class="doc-comment-inline">Initialize table and embedder.</span></li>
<li><code>_get_embedder</code> (graph_rag.py)
<details><summary>Get shared MLXEmbeddingManager singleton (memory-convergent).</summary>
<div class="doc-comment">
<p>Get shared MLXEmbeddingManager singleton (memory-convergent).</p>
<p></p>
<p>M1 8GB: graph_rag NENÍ embedder owner. Používá sdílený</p>
<p>MLXEmbeddingManager singleton z core/mlx_embeddings.py.</p>
<p>Žádné duplikátní RAGEngine() vytváření.</p>
</div>
</details>
</li>
<li><code>_rank_facts</code> (graph_rag.py)
<details><summary>Rank facts by relevance (similarity, hop distance, type).</summary>
<div class="doc-comment">
<p>Rank facts by relevance (similarity, hop distance, type).</p>
<p></p>
<p>Args:</p>
<p>facts: List of facts to rank</p>
<p></p>
<p>Returns:</p>
<p>Ranked list of facts</p>
</div>
</details>
</li>
<li><code>_raptor_retrieve</code> (rag_engine.py) — <span class="doc-comment-inline">Retrieve top-K nodes from all RAPTOR levels by cosine similarity.</span></li>
<li><code>_rrf_merge</code> (rag_engine.py) — <span class="doc-comment-inline">Merge two ranked lists via Reciprocal Rank Fusion. Stable key = hash of content.</span></li>
<li><code>_extract_url_from_provenance</code> (quality_assessment.py)
<details><summary>Extract the first HTTP(S) URL from a provenance tuple.</summary>
<div class="doc-comment">
<p>Extract the first HTTP(S) URL from a provenance tuple.</p>
<p></p>
<p>Handles two formats:</p>
<p>- Raw URL: "https://example.com"</p>
<p>- Tagged URL: "url:https://example.com" (PUBLIC lane format from _build_public_finding)</p>
</div>
</details>
</li>
<li><code>__init__</code> (quality_assessment.py)</li>
<li><code>add</code> (dedup.py)
<details><summary>Add item hash to active filter. Rotate if active is full.</summary>
<div class="doc-comment">
<p>Add item hash to active filter. Rotate if active is full.</p>
<p></p>
<p>Args:</p>
<p>item: URL or fingerprint string to add.</p>
</div>
</details>
</li>
<li><code>prewarm</code> (ann_index.py)
<details><summary>Pre-warm the ANN index for faster first-query latency.</summary>
<div class="doc-comment">
<p>Pre-warm the ANN index for faster first-query latency.</p>
<p></p>
<p>Ensures USEARCH index is loaded and pre-warms Metal memory.</p>
</div>
</details>
</li>
<li><code>upsert_ioc_batch</code> (ioc_graph.py)
<details><summary>Batch upsert of IOC nodes.</summary>
<div class="doc-comment">
<p>Batch upsert of IOC nodes.</p>
<p></p>
<p>Args:</p>
<p>iocs: list of (ioc_type, value, confidence) tuples.</p>
<p>Returns:</p>
<p>List of node IDs newly created in this batch.</p>
<p>Duplicate calls with the same inputs return [] on subsequent calls.</p>
</div>
</details>
</li>
<li><code>close</code> (wal.py) — <span class="doc-comment-inline">Close the WAL LMDB and release the lock file.</span></li>
<li><code>_stdp_update</code> (neuromorphic.py)
<details><summary>Apply STDP update to synaptic weight.</summary>
<div class="doc-comment">
<p>Apply STDP update to synaptic weight.</p>
<p></p>
<p>Args:</p>
<p>pre_idx: Pre-synaptic neuron index (unused in simplified model)</p>
<p>post_idx: Post-synaptic neuron index (unused in simplified model)</p>
<p>delta_t: Time difference (pre - post)</p>
<p></p>
<p>Returns:</p>
<p>Weight change value</p>
</div>
</details>
</li>
<li><code>_update_weights_from_pattern</code> (neuromorphic.py) — <span class="doc-comment-inline">Update synaptic weights based on neuron activations.</span></li>
<li><code>recall_pattern</code> (neuromorphic.py)
<details><summary>Recall a pattern from memory.</summary>
<div class="doc-comment">
<p>Recall a pattern from memory.</p>
<p></p>
<p>Args:</p>
<p>pattern_id: Pattern to recall</p>
<p>completion: Whether to perform pattern completion</p>
<p></p>
<p>Returns:</p>
<p>Recalled pattern or None</p>
</div>
</details>
</li>
<li><code>add</code> (ioc_dedup_adapter.py)
<details><summary>Add IOC to dedup store. Returns True if NEW (not duplicate), False if duplicate.</summary>
<div class="doc-comment">
<p>Add IOC to dedup store. Returns True if NEW (not duplicate), False if duplicate.</p>
<p></p>
<p>Args:</p>
<p>value: IOC value (domain, URL, IP, hash, CVE, email)</p>
<p>ioc_type: IOC type string (domain, url, ip, md5, sha1, sha256, cve, email, etc.)</p>
<p>confidence: Confidence score [0.0, 1.0], used for confidence_max tracking</p>
<p></p>
<p>Returns:</p>
<p>True if this is a NEW IOC (accepted), False if duplicate</p>
</div>
</details>
</li>
<li><code>_sync_query_sprint_source_stats</code> (duckdb_store.py)
<details><summary>Sprint 8RC: Query source_type hit-rate stats for weight loading.</summary>
<div class="doc-comment">
<p>Sprint 8RC: Query source_type hit-rate stats for weight loading.</p>
<p>Returns avg_hit_rate per source_type over the last 5 days.</p>
<p>MUST be called on the worker thread.</p>
</div>
</details>
</li>
<li><code>async_query_recent_findings</code> (duckdb_store.py)
<details><summary>Query recent findings ordered by timestamp descending.</summary>
<div class="doc-comment">
<p>Query recent findings ordered by timestamp descending.</p>
<p></p>
<p>Thread-safe, non-blocking - runs on duckdb_worker via run_in_executor.</p>
</div>
</details>
</li>
<li><code>drain_and_get_accepted</code> (duckdb_store.py)
<details><summary>Direct ingest — calls async_ingest_findings_batch() and returns results.</summary>
<div class="doc-comment">
<p>Direct ingest — calls async_ingest_findings_batch() and returns results.</p>
<p></p>
<p>This is the canonical write path for call sites that need the</p>
<p>accepted/stored counts from async_ingest_findings_batch().</p>
<p></p>
<p>Args:</p>
<p>findings: findings to ingest.</p>
<p></p>
<p>Returns:</p>
<p>List of FindingQualityDecision/ActivationResult objects,</p>
<p>one per finding submitted. Empty list on failure.</p>
</div>
</details>
</li>
<li><code>recall_episodes</code> (duckdb_store.py) — <span class="doc-comment-inline">Sprint 8UC B.2: Načíst posledních `limit` epizod (recency-based).</span></li>
<li><code>__init__</code> (lancedb_store.py)
<details><summary>Args:</summary>
<div class="doc-comment">
<p>Args:</p>
<p>db_path: Path to LanceDB database. If None, uses default.</p>
<p>dim: Embedding dimension (default 384 for FastEmbed BAAI).</p>
</div>
</details>
</li>
<li><code>_label_propagation</code> (graph_rag.py) — <span class="doc-comment-inline">Simple label propagation for community detection.</span></li>
<li><code>get_target_memory_summary</code> (analyst_workbench.py)
<details><summary>F204D: Get target memory summary for a target.</summary>
<div class="doc-comment">
<p>F204D: Get target memory summary for a target.</p>
<p></p>
<p>Returns dict with keys: target_id, sprint_count, cumulative_finding_count,</p>
<p>entity_facets, exposure_facets, pivot_facets, confidence_drift,</p>
<p>updated_by_sprint_id or None if not found.</p>
<p></p>
<p>Thread-safe: runs on duckdb_worker via run_in_executor.</p>
<p>Fail-soft: returns None on any error.</p>
</div>
</details>
</li>
<li><code>ainitialize</code> (dedup.py)
<details><summary>Async version of initialize() — runs all sync I/O in thread pool.</summary>
<div class="doc-comment">
<p>Async version of initialize() — runs all sync I/O in thread pool.</p>
<p></p>
<p>F268: Prevents event-loop blocking during DedupManager init.</p>
<p>All 4 init methods do file I/O (LMDB open, mmap files).</p>
<p>Running them in thread pool keeps event loop responsive.</p>
</div>
</details>
</li>
<li><code>_init_persistent_dedup_lmdb</code> (dedup.py)
<details><summary>Initialize persistent dedup LMDB.</summary>
<div class="doc-comment">
<p>Initialize persistent dedup LMDB.</p>
<p></p>
<p>Fails softly: any exception is caught and stored in _dedup_lmdb_boot_error.</p>
</div>
</details>
</li>
<li><code>_extract_entities_fallback</code> (entity_linker.py)
<details><summary>Extract entities using regex patterns (fallback when GLiNER unavailable).</summary>
<div class="doc-comment">
<p>Extract entities using regex patterns (fallback when GLiNER unavailable).</p>
<p></p>
<p>Returns:</p>
<p>List of (entity_text, start, end, entity_type) tuples</p>
</div>
</details>
</li>
<li><code>read_table</code> (duckdb_store.py)
<details><summary>Read entire parquet file as a single Arrow Table.</summary>
<div class="doc-comment">
<p>Read entire parquet file as a single Arrow Table.</p>
<p>WARNING: may OOM for 100GB+ files — prefer iter_batches().</p>
<p></p>
<p>Returns:</p>
<p>pyarrow.Table or None on error.</p>
</div>
</details>
</li>
<li><code>_sync_insert_source_hit</code> (duckdb_store.py)</li>
<li><code>async_record_shadow_run</code> (duckdb_store.py)</li>
<li><code>async_record_shadow_finding</code> (duckdb_store.py)</li>
<li><code>_execute_in_thread_sync</code> (duckdb_store.py)
<details><summary>Execute synchronous function on the duckdb executor and return its result.</summary>
<div class="doc-comment">
<p>Execute synchronous function on the duckdb executor and return its result.</p>
<p></p>
<p>MUST be called from the main thread. The callable fn runs on the</p>
<p>single-worker ThreadPoolExecutor and blocks until complete.</p>
<p></p>
<p>Returns:</p>
<p>The return value of fn(), or None if the executor raised an exception.</p>
<p></p>
<p>NOTE: This is a synchronous helper. Async callers MUST await the result:</p>
<p>result = await loop.run_in_executor(self._executor, self._execute_in_thread_sync, fn)</p>
<p>For direct async wrappers, prefer loop.run_in_executor() directly.</p>
</div>
</details>
</li>
<li><code>_sync_query_scorecard_trend</code> (duckdb_store.py)
<details><summary>Sync - MUST be called on the worker thread.</summary>
<div class="doc-comment">
<p>Sync - MUST be called on the worker thread.</p>
<p></p>
<p>F320-6.6: Polars LazyFrame for analytics queries.</p>
<p>Uses duckdb_fetch_polars() zero-copy path (DuckDB 1.5+ Arrow C Data Interface).</p>
<p>Streaming collection for bounded memory on large result sets.</p>
</div>
</details>
</li>
<li><code>get_sprint_delta_comparison</code> (duckdb_store.py)
<details><summary>Sprint F150H: Compare current sprint against the average of the last</summary>
<div class="doc-comment">
<p>Sprint F150H: Compare current sprint against the average of the last</p>
<p>`lookback` sprints. Returns a delta dict with absolute values of</p>
<p>current sprint and the delta vs the rolling mean of prior sprints.</p>
<p></p>
<p>Covers: new_findings, ioc_new_this_sprint, dedup_hits, findings_per_minute,</p>
<p>uma_peak_gib, synthesis_confidence.</p>
<p></p>
<p>Use for: "how is this sprint tracking vs history" without ad-hoc SQL.</p>
<p>Fail-soft - returns empty/near-zero fields on any error.</p>
</div>
</details>
</li>
<li><code>get_scorecard_consistency_check</code> (duckdb_store.py)
<details><summary>Sprint F150I: Compare findings_per_minute from sprint_scorecard vs</summary>
<div class="doc-comment">
<p>Sprint F150I: Compare findings_per_minute from sprint_scorecard vs</p>
<p>findings_per_minute from sprint_delta for the same sprint.</p>
<p>Returns ratio and warns if divergence &gt; 2x.</p>
<p></p>
<p>Use for: detecting scorecard / delta sync issues.</p>
<p>Fail-soft - returns empty dict on any error.</p>
<p></p>
<p>NOTE: As of Sprint F192F, both tables use findings_per_minute (renamed from</p>
<p>findings_per_min in sprint_delta). The JOIN now compares two same-named columns.</p>
</div>
</details>
</li>
<li><code>aclose</code> (duckdb_store.py)
<details><summary>Async idempotent shutdown - canonical async cleanup path.</summary>
<div class="doc-comment">
<p>Async idempotent shutdown - canonical async cleanup path.</p>
<p></p>
<p>Delegates to _do_sync_close(emergency=False) for shared synchronous cleanup,</p>
<p>then performs async-only operations (bg task cancellation).</p>
<p></p>
<p>Idempotent: safe to call multiple times.</p>
</div>
</details>
</li>
<li><code>_get_and_bump_retry_count</code> (duckdb_store.py)
<details><summary>Sprint 8H: Get current retry count from marker metadata and bump it.</summary>
<div class="doc-comment">
<p>Sprint 8H: Get current retry count from marker metadata and bump it.</p>
<p></p>
<p>Stores retry count in the marker value under "_retry_count" key.</p>
<p>Returns the new retry count after bump.</p>
</div>
</details>
</li>
<li><code>_predict_memory_pressure</code> (lancedb_store.py) — <span class="doc-comment-inline">Predict memory pressure using LMDB stats.</span></li>
<li><code>_run_async_safe</code> (graph_rag.py)
<details><summary>Safely run an async coroutine synchronously.</summary>
<div class="doc-comment">
<p>Safely run an async coroutine synchronously.</p>
<p></p>
<p>Delegates to run_sync_async() which uses asyncio.Runner (Python 3.11+)</p>
<p>for the no-loop case. For worker threads with a running loop,</p>
<p>run_until_complete is safe to use directly.</p>
</div>
</details>
</li>
<li><code>_deduplicate_facts</code> (graph_rag.py)
<details><summary>Remove duplicate facts based on content.</summary>
<div class="doc-comment">
<p>Remove duplicate facts based on content.</p>
<p></p>
<p>Args:</p>
<p>facts: List of facts to deduplicate</p>
<p></p>
<p>Returns:</p>
<p>Deduplicated list of facts</p>
</div>
</details>
</li>
<li><code>_calculate_clustering_coefficient</code> (graph_rag.py) — <span class="doc-comment-inline">Calculate average clustering coefficient.</span></li>
<li><code>_extract_claim</code> (graph_rag.py)
<details><summary>Extract (subject, predicate, object) claim from content.</summary>
<div class="doc-comment">
<p>Extract (subject, predicate, object) claim from content.</p>
<p></p>
<p>Args:</p>
<p>content: Text content to parse</p>
<p></p>
<p>Returns:</p>
<p>Tuple of (subject, predicate, object) or None</p>
</div>
</details>
</li>
<li><code>reset_hot_cache</code> (quality_assessment.py)
<details><summary>Sprint F259B: Clear in-memory dedup hot cache + fingerprint set per-sprint.</summary>
<div class="doc-comment">
<p>Sprint F259B: Clear in-memory dedup hot cache + fingerprint set per-sprint.</p>
<p></p>
<p>Bounded: both dicts are bounded (_DEDUP_HOT_CACHE_MAX) so clear is O(1) amortized.</p>
<p>Fail-soft: any exception is swallowed — caller is the per-sprint reset path</p>
<p>and must never crash the scheduler.</p>
</div>
</details>
</li>
<li><code>__init__</code> (dedup.py)
<details><summary>Args:</summary>
<div class="doc-comment">
<p>Args:</p>
<p>capacity: Max items per generation before rotation.</p>
<p>fp_rate: Target false positive rate.</p>
<p>lmdb_path: Ignored (kept for API compat). Persistence via mmap files.</p>
</div>
</details>
</li>
<li><code>pivot</code> (ioc_graph.py)
<details><summary>Find IOC nodes connected to the given IOC up to *depth* hops.</summary>
<div class="doc-comment">
<p>Find IOC nodes connected to the given IOC up to *depth* hops.</p>
<p></p>
<p>Kuzu: MATCH (n:IOC)-[r*1..2]-(m:IOC)</p>
<p>WHERE n.value=$v AND n.ioc_type=$t RETURN m, r</p>
<p></p>
<p>Returns list of dicts: id, ioc_type, value, confidence, first_seen, last_seen.</p>
</div>
</details>
</li>
<li><code>_graph_stats_sync</code> (ioc_graph.py) — <span class="doc-comment-inline">Synchronous stats — runs on _executor thread.</span></li>
<li><code>init_temporal_schema</code> (db.py) — <span class="doc-comment-inline">Initialize temporal signals table in DuckDB.</span></li>
<li><code>_iter_pyarrow_filtered</code> (duckdb_store.py) — <span class="doc-comment-inline">Pure PyArrow fallback with filter.</span></li>
<li><code>__init__</code> (duckdb_store.py)</li>
<li><code>_l2_get</code> (duckdb_store.py)</li>
<li><code>async_execute_raw_sql</code> (duckdb_store.py)
<details><summary>Execute raw SQL query asynchronously (non-blocking).</summary>
<div class="doc-comment">
<p>Execute raw SQL query asynchronously (non-blocking).</p>
<p></p>
<p>Thread-safe, non-blocking - runs on duckdb_worker via run_in_executor.</p>
<p>Use this instead of direct _conn.cursor().execute() in async contexts.</p>
<p></p>
<p>Args:</p>
<p>sql: Raw SQL query string</p>
<p></p>
<p>Returns:</p>
<p>List of row tuples from fetchall()</p>
</div>
</details>
</li>
<li><code>async_query_recent_findings_by_sprint</code> (duckdb_store.py)
<details><summary>Return the most recent accepted findings for a given sprint,</summary>
<div class="doc-comment">
<p>Return the most recent accepted findings for a given sprint,</p>
<p>ordered by ts DESC. Bounded, read-only, fail-soft.</p>
<p></p>
<p>Use for: export synthesis input, sprint retrospektivu,</p>
<p>scheduler priority scoring.</p>
</div>
</details>
</li>
<li><code>upsert_global_entities</code> (duckdb_store.py)
<details><summary>Sprint F4.1 fix: Upsert entities into ghost_global store using DuckDB.</summary>
<div class="doc-comment">
<p>Sprint F4.1 fix: Upsert entities into ghost_global store using DuckDB.</p>
<p></p>
<p>Path: ~/.hledac/ghost_global.duckdb (DuckDB with native WAL mode)</p>
<p>Engine: DuckDB with access_mode='automatic' (native file locking)</p>
<p>Schema: global_entities(entity_value TEXT PK, entity_type TEXT,</p>
<p>sprint_count INT, last_seen DOUBLE, confidence_cumulative REAL)</p>
<p>INSERT OR REPLACE with MAX(confidence) semantics.</p>
<p>Returns: int (count of upserted entities).</p>
</div>
</details>
</li>
<li><code>async_get_findings_with_envelope</code> (duckdb_store.py)
<details><summary>Sprint F202A §3: Read recent findings with deserialized envelopes.</summary>
<div class="doc-comment">
<p>Sprint F202A §3: Read recent findings with deserialized envelopes.</p>
<p></p>
<p>Returns list of dicts with envelope fields attached:</p>
<p>{finding_id, query, source_type, confidence, ts, provenance,</p>
<p>payload_text, envelope: FindingEnvelope | None}</p>
<p>Fail-soft: any finding without valid envelope has envelope=None.</p>
</div>
</details>
</li>
<li><code>_init_persistent_dedup_lmdb</code> (duckdb_store.py) — <span class="doc-comment-inline">Deprecated: initialization moved to DedupManager.initialize().</span></li>
<li><code>_get_flashrank_ranker</code> (lancedb_store.py)
<details><summary>Lazy load FlashRank for retrieval path.</summary>
<div class="doc-comment">
<p>Lazy load FlashRank for retrieval path.</p>
<p></p>
<p>Canonical owner: tools/reranker.py</p>
<p>This is a compatibility wrapper serving the retrieval context only.</p>
<p>Uses ms-marco-MiniLM-L-12-v2 model (same as canonical).</p>
</div>
</details>
</li>
<li><code>upsert_papers</code> (lancedb_store.py)
<details><summary>Batch upsert academic papers.</summary>
<div class="doc-comment">
<p>Batch upsert academic papers.</p>
<p></p>
<p>Args:</p>
<p>papers: List of AcademicPaper instances.</p>
</div>
</details>
</li>
<li><code>_summarize_narrative</code> (graph_rag.py) — <span class="doc-comment-inline">Generate 1-3 sentence summary of narrative.</span></li>
<li><code>__init__</code> (analyst_workbench.py)
<details><summary>Initialize AnalystWorkbench with optional store references.</summary>
<div class="doc-comment">
<p>Initialize AnalystWorkbench with optional store references.</p>
<p></p>
<p>All stores are optional — workbench operates with whatever is available.</p>
<p>If a store is None, its queries return empty results (fail-soft).</p>
<p></p>
<p>Args:</p>
<p>duckdb_store: DuckDBShadowStore instance for findings</p>
<p>graph_service: DuckPGQGraph-backed service for entity history</p>
<p>vector_store: LanceDB VectorStore for text ANN</p>
<p>semantic_store: FastEmbed SemanticStore for keyword search</p>
</div>
</details>
</li>
<li><code>_make_decision</code> (quality_assessment.py)</li>
<li><code>upsert_ioc</code> (ioc_graph.py)
<details><summary>Idempotent upsert of an IOC node.</summary>
<div class="doc-comment">
<p>Idempotent upsert of an IOC node.</p>
<p></p>
<p>Uses MATCH→CREATE/SET pattern (Kuzu has no MERGE).</p>
<p>Returns the IOC id or None on failure.</p>
</div>
</details>
</li>
<li><code>graph_supports_buffered_writes</code> (graph_attachment.py)
<details><summary>NON-AUTHORITATIVE COMPAT CHECK: does attached graph support ACTIVE-phase</summary>
<div class="doc-comment">
<p>NON-AUTHORITATIVE COMPAT CHECK: does attached graph support ACTIVE-phase</p>
<p>buffered writes?</p>
<p></p>
<p>Returns True only if attached graph has both:</p>
<p>- buffer_ioc()</p>
<p>- flush_buffers()</p>
<p></p>
<p>IOCGraph (Kuzu): True — has full buffered write capability.</p>
<p>DuckPGQGraph (DuckDB): False — has checkpoint() and add_ioc() only.</p>
<p></p>
<p>Always check this before triggering background graph ingest,</p>
<p>do not assume all injected graphs support buffered writes.</p>
</div>
</details>
</li>
<li><code>inject_truth_write_graph</code> (graph_attachment.py)
<details><summary>Sprint 8WA: Inject dedicated truth-write graph for ACTIVE buffered writes.</summary>
<div class="doc-comment">
<p>Sprint 8WA: Inject dedicated truth-write graph for ACTIVE buffered writes.</p>
<p></p>
<p>F320 UPDATE: Both DuckPGQGraph and IOCGraph are now accepted.</p>
<p>DuckPGQGraph has native buffer_ioc/flush_buffers since F272.</p>
<p></p>
<p>This slot is INDEPENDENT of:</p>
<p>- _ioc_graph (analytics/donor graph)</p>
<p>- _stix_graph (STIX synthesis graph)</p>
<p></p>
<p>_truth_write_graph is used exclusively for ACTIVE-phase buffered IOC ingest</p>
<p>via _graph_ingest_findings().</p>
<p></p>
<p>Args:</p>
<p>graph: DuckPGQGraph or IOCGraph instance, or None to clear.</p>
</div>
</details>
</li>
<li><code>__init__</code> (wal.py)
<details><summary>Args:</summary>
<div class="doc-comment">
<p>Args:</p>
<p>wal_path: Absolute path to the WAL LMDB directory.</p>
<p>map_size: LMDB map size in bytes (unused when unified_store provided).</p>
<p>unified_store: Optional UnifiedLMDBStore for consolidated storage.</p>
</div>
</details>
</li>
<li><code>wal_write_deadletter_marker</code> (wal.py)
<details><summary>Write a marker to the dead-letter namespace after max retries exceeded.</summary>
<div class="doc-comment">
<p>Write a marker to the dead-letter namespace after max retries exceeded.</p>
<p></p>
<p>Dead-letter key:  deadletter_ingest:{id}</p>
<p>Value:            id, query, source_type, confidence, ts, error, retry_count</p>
</div>
</details>
</li>
<li><code>_filter_batch_source_types</code> (duckdb_store.py) — <span class="doc-comment-inline">Filter batch by source_type in-memory (post row-group filter).</span></li>
<li><code>_l2_set</code> (duckdb_store.py)</li>
<li><code>upsert_target_profile</code> (duckdb_store.py) — <span class="doc-comment-inline">Upsert target profile. Silently returns on failure.</span></li>
<li><code>wait_until_ready</code> (duckdb_store.py)
<details><summary>Event-driven readiness wait — wakes via asyncio.Event, no polling.</summary>
<div class="doc-comment">
<p>Event-driven readiness wait — wakes via asyncio.Event, no polling.</p>
<p></p>
<p>ISSUE-006 fix: replaces the 40×50ms polling loop (2s worst-case)</p>
<p>with a single event-driven wait on _startup_ready.</p>
<p></p>
<p>Returns True if store became ready within timeout, False otherwise.</p>
</div>
</details>
</li>
<li><code>async_query_top_entities_by_sprint</code> (duckdb_store.py)
<details><summary>Return entity-like pivot candidates extracted from finding queries</summary>
<div class="doc-comment">
<p>Return entity-like pivot candidates extracted from finding queries</p>
<p>and provenance for the given sprint. Looks for domain/IP/url-like</p>
<p>tokens in query text. Bounded, read-only, fail-soft.</p>
<p></p>
<p>Use for: synthesis pivot hints, entity correlation candidates,</p>
<p>export enrichment. Does NOT require global_entities table.</p>
</div>
</details>
</li>
<li><code>async_query_sprint_ioc_summary</code> (duckdb_store.py)
<details><summary>Return a lightweight IOC summary for a sprint:</summary>
<div class="doc-comment">
<p>Return a lightweight IOC summary for a sprint:</p>
<p>total findings, unique source_types, avg confidence,</p>
<p>time span (first-&gt;last ts). Bounded, read-only, fail-soft.</p>
<p></p>
<p>Use for: scheduler decision support, synthesis quality signals,</p>
<p>sprint retrospektivu.</p>
</div>
</details>
</li>
<li><code>upsert_target_memory</code> (duckdb_store.py)
<details><summary>Sprint F204D: Upsert a TargetMemory record into DuckDB.</summary>
<div class="doc-comment">
<p>Sprint F204D: Upsert a TargetMemory record into DuckDB.</p>
<p></p>
<p>Serializes facets as JSON TEXT columns. Uses INSERT OR REPLACE.</p>
<p>GHOST_INVARIANT: runs on duckdb executor via run_in_executor.</p>
</div>
</details>
</li>
<li><code>_sync_replay_single_marker</code> (duckdb_store.py)
<details><summary>Sprint 8H: Synchronous single-marker replay - MUST be called on the worker thread.</summary>
<div class="doc-comment">
<p>Sprint 8H: Synchronous single-marker replay - MUST be called on the worker thread.</p>
<p></p>
<p>Uses the same _sync_insert_finding path as normal activation.</p>
<p>Returns True if DuckDB write succeeded.</p>
</div>
</details>
</li>
<li><code>_initialize_embedder</code> (lancedb_store.py) — <span class="doc-comment-inline">Initialize embedder: MLX/GPU → CoreML/ANE → Numpy fallback.</span></li>
<li><code>_init_embedder</code> (lancedb_store.py)
<details><summary>Initialize embedder via MLX-first cascade.</summary>
<div class="doc-comment">
<p>Initialize embedder via MLX-first cascade.</p>
<p></p>
<p>Invariant: random vector fallback is FORBIDDEN — silent ANN corruption.</p>
<p>Raises RuntimeError on no backend (no np.random.randn fallback).</p>
<p>MLX path is tried first (M1 ANE/GPU, zero-copy UMA).</p>
<p>``self._embedder_backend`` is set in every success path.</p>
</div>
</details>
</li>
<li><code>batch_search</code> (rag_engine.py)
<details><summary>Batch search for multiple query vectors.</summary>
<div class="doc-comment">
<p>Batch search for multiple query vectors.</p>
<p></p>
<p>Args:</p>
<p>query_vectors: Array of shape (n_queries, dim)</p>
<p>k: Number of results per query</p>
<p>filter_ids: Optional list of ids to filter results</p>
<p></p>
<p>Returns:</p>
<p>List of (ids, distances) tuples for each query</p>
</div>
</details>
</li>
<li><code>contains</code> (dedup.py)
<details><summary>Check both active and previous filters.</summary>
<div class="doc-comment">
<p>Check both active and previous filters.</p>
<p></p>
<p>Args:</p>
<p>item: URL or fingerprint string to check.</p>
<p></p>
<p>Returns:</p>
<p>True if item was previously added (possible duplicate).</p>
</div>
</details>
</li>
<li><code>_extract_entities_gliner</code> (entity_linker.py)
<details><summary>Extract entities using GLiNER.</summary>
<div class="doc-comment">
<p>Extract entities using GLiNER.</p>
<p></p>
<p>Returns:</p>
<p>List of (entity_text, start, end, entity_type) tuples</p>
</div>
</details>
</li>
<li><code>batch_link</code> (entity_linker.py)
<details><summary>Link entities in multiple texts (batch processing).</summary>
<div class="doc-comment">
<p>Link entities in multiple texts (batch processing).</p>
<p></p>
<p>Args:</p>
<p>texts: List of texts to process</p>
<p>contexts: Optional list of contexts (one per text)</p>
<p></p>
<p>Returns:</p>
<p>List of LinkedEntity lists (one per input text)</p>
</div>
</details>
</li>
<li><code>should_tune</code> (lancedb_auto_tuner.py)
<details><summary>Decide whether the cooldown + insert-threshold gate is satisfied.</summary>
<div class="doc-comment">
<p>Decide whether the cooldown + insert-threshold gate is satisfied.</p>
<p></p>
<p>Returns True only if both:</p>
<p>- ``inserts_since_tune &gt;= self._insert_threshold``</p>
<p>- ``(now - state.last_tune_at) &gt;= self._cooldown_seconds``</p>
<p></p>
<p>Pure function — does NOT mutate state. Caller persists changes.</p>
</div>
</details>
</li>
<li><code>_pivot_sync</code> (ioc_graph.py) — <span class="doc-comment-inline">Synchronous pivot — runs on _executor thread.</span></li>
<li><code>rust_query</code> (db.py)
<details><summary>Execute query via Rust StdConnectionPool (O(1) connection access).</summary>
<div class="doc-comment">
<p>Execute query via Rust StdConnectionPool (O(1) connection access).</p>
<p></p>
<p>Returns:</p>
<p>List of rows, each row is a list of strings.</p>
</div>
</details>
</li>
<li><code>inject_graph</code> (graph_attachment.py)
<details><summary>Inject a graph instance for IOC ingest on canonical findings.</summary>
<div class="doc-comment">
<p>Inject a graph instance for IOC ingest on canonical findings.</p>
<p></p>
<p>STORE IS NOT GRAPH TRUTH OWNER — the injected graph may be:</p>
<p>- IOCGraph (Kuzu): truth backend, full capability</p>
<p>- DuckPGQGraph (DuckDB): donor/alternate backend, limited capability</p>
<p></p>
<p>Capability requirements for buffered writes (ACTIVE phase):</p>
<p>- Requires: buffer_ioc(), buffer_observation(), flush_buffers()</p>
<p>- IOCGraph has these. DuckPGQGraph does NOT.</p>
<p></p>
<p>After inject, use get_graph_attachment_kind() to determine</p>
<p>which backend was attached and check capabilities explicitly.</p>
</div>
</details>
</li>
<li><code>inject_stix_graph</code> (graph_attachment.py)
<details><summary>Sprint 8VQ: Inject truth-store STIX graph for synthesis consumption.</summary>
<div class="doc-comment">
<p>Sprint 8VQ: Inject truth-store STIX graph for synthesis consumption.</p>
<p></p>
<p>TRUTH-STORE ONLY: only IOCGraph (Kuzu) has export_stix_bundle().</p>
<p>DuckPGQGraph must NEVER be injected here — it lacks STIX capability.</p>
<p></p>
<p>This slot is INDEPENDENT of _ioc_graph (analytics/donor graph).</p>
<p>_stix_graph is used exclusively by synthesis runners for STIX context.</p>
<p></p>
<p>F320 UPDATE: Both DuckPGQGraph and IOCGraph are now accepted.</p>
<p>DuckPGQGraph has export_stix_bundle() since F271.</p>
<p></p>
<p>Args:</p>
<p>graph: DuckPGQGraph or IOCGraph instance, or None to clear.</p>
</div>
</details>
</li>
<li><code>add</code> (ioc_dedup_adapter.py) — <span class="doc-comment-inline">Add IOC — returns True if NEW, False if duplicate.</span></li>
<li><code>close</code> (duckdb_store.py)
<details><summary>Synchronous close — full cleanup without any event loop manipulation.</summary>
<div class="doc-comment">
<p>Synchronous close — full cleanup without any event loop manipulation.</p>
<p></p>
<p>F300S-FIX: close() now performs the FULL cleanup inline synchronously.</p>
<p>No run_until_complete() on a running loop — which fails on Python 3.10+</p>
<p>with RuntimeError: "cannot close event loop while running".</p>
<p>close() IS the synchronous cleanup path — no event loop manipulation needed.</p>
<p></p>
<p>Idempotent: safe to call multiple times.</p>
</div>
</details>
</li>
<li><code>async_query_top_sources_by_sprint</code> (duckdb_store.py)
<details><summary>Return source_type breakdown (findings count, avg confidence)</summary>
<div class="doc-comment">
<p>Return source_type breakdown (findings count, avg confidence)</p>
<p>for a given sprint. Bounded, read-only, fail-soft.</p>
<p></p>
<p>Use for: sprint retrospektivu, source yield analysis,</p>
<p>scheduler source weighting decisions.</p>
</div>
</details>
</li>
<li><code>upsert_scorecard</code> (duckdb_store.py)
<details><summary>Sprint 8TA B.3: Insert or replace a sprint_scorecard record.</summary>
<div class="doc-comment">
<p>Sprint 8TA B.3: Insert or replace a sprint_scorecard record.</p>
<p></p>
<p>data contains: sprint_id, ts, findings_per_minute, ioc_density,</p>
<p>semantic_novelty, source_yield_json (orjson), phase_timings_json (orjson),</p>
<p>outlines_used, accepted_findings, ioc_nodes</p>
</div>
</details>
</li>
<li><code>get_sprint_trend</code> (duckdb_store.py)
<details><summary>DEPRECATED (Sprint F183D) - use async_query_sprint_trend() instead.</summary>
<div class="doc-comment">
<p>DEPRECATED (Sprint F183D) - use async_query_sprint_trend() instead.</p>
<p></p>
<p>Convenience sync wrapper - returns last N sprints ordered by ts DESC.</p>
<p>For use in sync contexts (e.g., report printing).</p>
<p></p>
<p>REMOVAL CONDITION: all callers migrated to async read seams.</p>
</div>
</details>
</li>
<li><code>get_source_leaderboard</code> (duckdb_store.py)
<details><summary>DEPRECATED (Sprint F183D) - use async_query_source_leaderboard() instead.</summary>
<div class="doc-comment">
<p>DEPRECATED (Sprint F183D) - use async_query_source_leaderboard() instead.</p>
<p></p>
<p>Convenience sync wrapper - returns top sources by hit rate.</p>
<p>For use in sync contexts (e.g., report printing).</p>
<p></p>
<p>REMOVAL CONDITION: all callers migrated to async read seams.</p>
</div>
</details>
</li>
<li><code>get_sprint_scorecard_trend</code> (duckdb_store.py)
<details><summary>Sprint F150H: Convenience sync wrapper - returns last N scorecards</summary>
<div class="doc-comment">
<p>Sprint F150H: Convenience sync wrapper - returns last N scorecards</p>
<p>ordered by ts DESC. Covers ioc_density, semantic_novelty, accepted_findings,</p>
<p>findings_per_minute, and outlines_used. Fail-soft, bounded.</p>
<p></p>
<p>Use for: yield trend reporting, retrospektiva, sprint-to-sprint</p>
<p>quality comparison without ad-hoc SQL.</p>
</div>
</details>
</li>
<li><code>get_source_mix_trend</code> (duckdb_store.py)
<details><summary>Sprint F150H: Convenience sync wrapper - returns source_type distribution</summary>
<div class="doc-comment">
<p>Sprint F150H: Convenience sync wrapper - returns source_type distribution</p>
<p>broken down by sprint for the last `days`. Each row contains</p>
<p>source_type, sprint_id, total_findings, and hit_rate.</p>
<p></p>
<p>Use for: source mix reporting - is web growing vs feed vs document,</p>
<p>and is each source getting more productive over time.</p>
</div>
</details>
</li>
<li><code>get_yield_trend</code> (duckdb_store.py)
<details><summary>Sprint F150H: Derived yield metrics per sprint - new_findings / duration_s,</summary>
<div class="doc-comment">
<p>Sprint F150H: Derived yield metrics per sprint - new_findings / duration_s,</p>
<p>dedup_hits ratio (dedup_hits / new_findings), and ioc_rate</p>
<p>(ioc_new_this_sprint / new_findings). Returns last N sprints.</p>
<p></p>
<p>Use for: "are we getting better at extracting unique findings from sources"</p>
<p>- track yield improvement or degradation across sprints.</p>
</div>
</details>
</li>
<li><code>get_high_value_sprint_ranking</code> (duckdb_store.py)
<details><summary>Sprint F150I: Rank last N sprints by a composite value score.</summary>
<div class="doc-comment">
<p>Sprint F150I: Rank last N sprints by a composite value score.</p>
<p>Composite = accepted_findings * semantic_novelty / max(duration_s, 1).</p>
<p>Higher is better. Returns sprint_id, composite_score, and component fields.</p>
<p></p>
<p>Use for: "which sprints delivered the most value per second".</p>
<p>Fail-soft, bounded.</p>
</div>
</details>
</li>
<li><code>async_vacuum_if_needed</code> (duckdb_store.py)
<details><summary>Conditionally vacuum if the DB file exceeds threshold_bytes.</summary>
<div class="doc-comment">
<p>Conditionally vacuum if the DB file exceeds threshold_bytes.</p>
<p></p>
<p>Args:</p>
<p>threshold_bytes: size above which vacuum is triggered (default 2GB)</p>
<p></p>
<p>Returns True if vacuum was triggered and succeeded, False otherwise.</p>
</div>
</details>
</li>
<li><code>health_check</code> (lancedb_store.py) — <span class="doc-comment-inline">Check embedding store health.</span></li>
<li><code>_usearch_search</code> (lancedb_store.py) — <span class="doc-comment-inline">Search using usearch (if available).</span></li>
<li><code>_extract_community_characteristics</code> (graph_rag.py) — <span class="doc-comment-inline">Extract key characteristics of a community.</span></li>
<li><code>__init__</code> (rag_engine.py)</li>
<li><code>_generate_llm_answer</code> (analyst_workbench.py)
<details><summary>Generate LLM answer using brain/model_lifecycle.py.</summary>
<div class="doc-comment">
<p>Generate LLM answer using brain/model_lifecycle.py.</p>
<p></p>
<p>Load/unload only through canonical model_lifecycle interface.</p>
<p>Returns None on any failure (fail-soft).</p>
</div>
</details>
</li>
<li><code>_extract_entities_from_question</code> (analyst_workbench.py)
<details><summary>Extract potential IOC entities from question using regex patterns.</summary>
<div class="doc-comment">
<p>Extract potential IOC entities from question using regex patterns.</p>
<p></p>
<p>Returns list of entity values (domains, IPs, emails, hashes).</p>
</div>
</details>
</li>
<li><code>_build_evidence_pointers</code> (analyst_workbench.py)
<details><summary>Build evidence pointers from findings.</summary>
<div class="doc-comment">
<p>Build evidence pointers from findings.</p>
<p></p>
<p>Caps at MAX_EVIDENCE_PTRS, ordered by confidence descending.</p>
</div>
</details>
</li>
<li><code>_try_rust_rotating</code> (dedup.py)
<details><summary>Try Rust RotatingMmapBloomFilter (F288+: race-free rotation in Rust).</summary>
<div class="doc-comment">
<p>Try Rust RotatingMmapBloomFilter (F288+: race-free rotation in Rust).</p>
<p></p>
<p>Single import block — no redundant re-imports.</p>
</div>
</details>
</li>
<li><code>advance_ioc_sprint</code> (dedup.py)
<details><summary>Advance IOC dedup store to new sprint (updates first_seen/last_seen metadata).</summary>
<div class="doc-comment">
<p>Advance IOC dedup store to new sprint (updates first_seen/last_seen metadata).</p>
<p></p>
<p>Called by SprintScheduler on sprint boundary.</p>
</div>
</details>
</li>
<li><code>add_to_hot_cache</code> (dedup.py)
<details><summary>Add entry to bounded hot cache with FIFO eviction.</summary>
<div class="doc-comment">
<p>Add entry to bounded hot cache with FIFO eviction.</p>
<p></p>
<p>Hard cap: _DEDUP_HOT_CACHE_MAX entries.</p>
<p>O(1) operations using OrderedDict: move_to_end() for MRU, popitem(last=False) for FIFO.</p>
</div>
</details>
</li>
<li><code>close</code> (ann_index.py) — <span class="doc-comment-inline">Close database connection.</span></li>
<li><code>_get_row_group_stats</code> (duckdb_store.py) — <span class="doc-comment-inline">Get row-group statistics for filter pushdown.</span></li>
<li><code>ensure_target_profiles_schema</code> (duckdb_store.py)
<details><summary>Sprint F202K: Ensure target_profiles table exists in DuckDB.</summary>
<div class="doc-comment">
<p>Sprint F202K: Ensure target_profiles table exists in DuckDB.</p>
<p>Safe to call multiple times - uses CREATE TABLE IF NOT EXISTS.</p>
<p>Must be called after _init_connection (connection must exist).</p>
</div>
</details>
</li>
<li><code>ensure_target_memory_schema</code> (duckdb_store.py)
<details><summary>Sprint F204D: Ensure target_memory table exists in DuckDB.</summary>
<div class="doc-comment">
<p>Sprint F204D: Ensure target_memory table exists in DuckDB.</p>
<p>Safe to call multiple times - uses CREATE TABLE IF NOT EXISTS.</p>
<p>Must be called after _init_connection (connection must exist).</p>
</div>
</details>
</li>
<li><code>insert_run</code> (duckdb_store.py)</li>
<li><code>get_target_profile</code> (duckdb_store.py) — <span class="doc-comment-inline">Get target profile. Returns row tuple or None.</span></li>
<li><code>_sync_get_target_profile</code> (duckdb_store.py) — <span class="doc-comment-inline">Sync get - MUST be called on the worker thread. Returns None if not found.</span></li>
<li><code>_flush_pending_findings_sync</code> (duckdb_store.py)
<details><summary>Sync flush: persists pending findings from _pending_accepted_findings on close.</summary>
<div class="doc-comment">
<p>Sync flush: persists pending findings from _pending_accepted_findings on close.</p>
<p></p>
<p>Called from _do_sync_close via executor.submit to avoid blocking.</p>
<p>Writes via Arrow batch pipeline (same as async_ingest_findings_batch).</p>
</div>
</details>
</li>
<li><code>async_healthcheck</code> (duckdb_store.py)
<details><summary>Quick health check - attempts a zero-cost query.</summary>
<div class="doc-comment">
<p>Quick health check - attempts a zero-cost query.</p>
<p></p>
<p>Returns True if the store is healthy and responsive.</p>
</div>
</details>
</li>
<li><code>_sync_query_findings_by_keywords</code> (duckdb_store.py) — <span class="doc-comment-inline">Sync - MUST be called on worker thread.</span></li>
<li><code>_sync_query_top_sources_by_sprint</code> (duckdb_store.py) — <span class="doc-comment-inline">Sync - MUST be called on worker thread.</span></li>
<li><code>async_query_source_leaderboard</code> (duckdb_store.py)
<details><summary>Return top sources by hit rate for the last N days.</summary>
<div class="doc-comment">
<p>Return top sources by hit rate for the last N days.</p>
<p>Thread-safe, non-blocking.</p>
</div>
</details>
</li>
<li><code>async_upsert_target_profile</code> (duckdb_store.py)
<details><summary>Sprint F202K: Insert or update a target profile.</summary>
<div class="doc-comment">
<p>Sprint F202K: Insert or update a target profile.</p>
<p></p>
<p>Thread-safe, non-blocking - runs on duckdb_worker via run_in_executor.</p>
<p>Silently fails if store is closed or uninitialized.</p>
</div>
</details>
</li>
<li><code>async_get_target_profile</code> (duckdb_store.py)
<details><summary>Sprint F202K: Get a target profile by target_id.</summary>
<div class="doc-comment">
<p>Sprint F202K: Get a target profile by target_id.</p>
<p></p>
<p>Thread-safe, non-blocking - runs on duckdb_worker via run_in_executor.</p>
<p>Returns None if not found or on error.</p>
</div>
</details>
</li>
<li><code>_init_cache</code> (lancedb_store.py) — <span class="doc-comment-inline">Initialize LMDB cache for embeddings with float16 quantization.</span></li>
<li><code>_log_table_opened</code> (lancedb_store.py)
<details><summary>Sprint F264D: Log 'lancedb.table_opened' event with size_mb.</summary>
<div class="doc-comment">
<p>Sprint F264D: Log 'lancedb.table_opened' event with size_mb.</p>
<p></p>
<p>M1 observability — measures table footprint for IVF-PQ benefit verification.</p>
<p>Estimated: rows × embedding_dim × 4 bytes (float32) + PyArrow overhead.</p>
</div>
</details>
</li>
<li><code>_analyze_contradiction</code> (graph_rag.py) — <span class="doc-comment-inline">Analyze if two nodes contradict each other.</span></li>
<li><code>_detect_contradictions_with_narratives</code> (graph_rag.py)
<details><summary>Detect contradictions and generate competing narratives with confidence.</summary>
<div class="doc-comment">
<p>Detect contradictions and generate competing narratives with confidence.</p>
<p></p>
<p>Args:</p>
<p>facts: Facts to analyze</p>
<p></p>
<p>Returns:</p>
<p>Tuple of (contested, primary_paths, counter_paths, narratives)</p>
</div>
</details>
</li>
<li><code>_summarize_cluster</code> (rag_engine.py) — <span class="doc-comment-inline">Summarize cluster text via Hermes3 generate_structured(). Truncates on failure.</span></li>
<li><code>pagerank</code> (graph_service.py)
<details><summary>ISSUE #14: PageRank via DuckPGQGraph.pagerank().</summary>
<div class="doc-comment">
<p>ISSUE #14: PageRank via DuckPGQGraph.pagerank().</p>
<p></p>
<p>Returns:</p>
<p>Dict mapping IOC value → PageRank score. Empty dict if graph unavailable.</p>
</div>
</details>
</li>
<li><code>shortest_path</code> (graph_service.py)
<details><summary>ISSUE #14: Shortest path via DuckPGQGraph.shortest_path().</summary>
<div class="doc-comment">
<p>ISSUE #14: Shortest path via DuckPGQGraph.shortest_path().</p>
<p></p>
<p>Returns:</p>
<p>List of IOC values forming the path, or None if no path exists.</p>
</div>
</details>
</li>
<li><code>community_detection</code> (graph_service.py)
<details><summary>ISSUE #14: Community detection via DuckPGQGraph.community_detection().</summary>
<div class="doc-comment">
<p>ISSUE #14: Community detection via DuckPGQGraph.community_detection().</p>
<p></p>
<p>Returns:</p>
<p>Dict mapping community_id → list of IOC values in that community.</p>
</div>
</details>
</li>
<li><code>get</code> (entity_linker.py) — <span class="doc-comment-inline">Get cached value if not expired.</span></li>
<li><code>export_stix_bundle</code> (ioc_graph.py)
<details><summary>Export all IOC nodes as STIX 2.1 objects.</summary>
<div class="doc-comment">
<p>Export all IOC nodes as STIX 2.1 objects.</p>
<p></p>
<p>Validates the bundle via stix2.parse() — returns empty list on failure.</p>
</div>
</details>
</li>
<li><code>init_forensics_schema</code> (db.py) — <span class="doc-comment-inline">Initialize forensics metadata table in DuckDB.</span></li>
<li><code>get_stats</code> (neuromorphic.py) — <span class="doc-comment-inline">Get neuromorphic memory statistics.</span></li>
<li><code>close</code> (ioc_dedup_adapter.py)
<details><summary>Graceful shutdown — persist state and close LMDB.</summary>
<div class="doc-comment">
<p>Graceful shutdown — persist state and close LMDB.</p>
<p></p>
<p>F289: Detaches finalizer on explicit call to prevent double-cleanup</p>
<p>at interpreter exit. After detach(), atexit no longer triggers</p>
<p>_ioc_dedup_at_exit_close.</p>
</div>
</details>
</li>
<li><code>_filter_row_groups</code> (duckdb_store.py) — <span class="doc-comment-inline">Apply filters to get list of row-groups to read.</span></li>
<li><code>iter_batches</code> (duckdb_store.py)
<details><summary>Iterate over filtered row-groups as Arrow RecordBatch objects.</summary>
<div class="doc-comment">
<p>Iterate over filtered row-groups as Arrow RecordBatch objects.</p>
<p></p>
<p>Yields:</p>
<p>pyarrow.RecordBatch — zero-copy view of one row-group.</p>
<p>Caller converts to Polars via pl.from_arrow(batch) for zero-copy.</p>
</div>
</details>
</li>
<li><code>_with_transaction</code> (duckdb_store.py)
<details><summary>Run fn(conn) inside an explicit transaction.</summary>
<div class="doc-comment">
<p>Run fn(conn) inside an explicit transaction.</p>
<p>Commits on success, rolls back on any exception.</p>
<p>Returns fn's return value.</p>
</div>
</details>
</li>
<li><code>_get_read_conn</code> (duckdb_store.py)
<details><summary>ISSUE-008 P1: Return next read connection from round-robin pool.</summary>
<div class="doc-comment">
<p>ISSUE-008 P1: Return next read connection from round-robin pool.</p>
<p></p>
<p>Read pool allows parallel analytical queries without contention</p>
<p>with the write connection. Falls back to _file_conn if pool is empty.</p>
<p></p>
<p>Thread-safe: uses atomic idx increment.</p>
</div>
</details>
</li>
<li><code>_sync_execute_raw_sql</code> (duckdb_store.py)
<details><summary>Execute raw SQL and return all rows.</summary>
<div class="doc-comment">
<p>Execute raw SQL and return all rows.</p>
<p></p>
<p>MUST be called on duckdb worker thread (inside run_in_executor).</p>
<p>Thread-safe: uses _file_conn/_persistent_conn.</p>
</div>
</details>
</li>
<li><code>async_record_sprint_delta</code> (duckdb_store.py)
<details><summary>Insert a sprint_delta record.</summary>
<div class="doc-comment">
<p>Insert a sprint_delta record.</p>
<p></p>
<p>Thread-safe, non-blocking.</p>
</div>
</details>
</li>
<li><code>_sync_query_recent_findings_by_sprint</code> (duckdb_store.py) — <span class="doc-comment-inline">Sync - MUST be called on worker thread.</span></li>
<li><code>async_query_sprint_source_stats</code> (duckdb_store.py)
<details><summary>Return per-source-type avg_hit_rate over the last 5 days.</summary>
<div class="doc-comment">
<p>Return per-source-type avg_hit_rate over the last 5 days.</p>
<p>Used by SprintScheduler.load_source_weights().</p>
<p>Thread-safe, non-blocking.</p>
</div>
</details>
</li>
<li><code>async_get_entity_observations_by_entity</code> (duckdb_store.py) — <span class="doc-comment-inline">Sprint F350M: Get entity observations by entity value.</span></li>
<li><code>_envelope_to_payload</code> (duckdb_store.py)
<details><summary>Sprint F202A §2: Serialize FindingEnvelope to payload_text string.</summary>
<div class="doc-comment">
<p>Sprint F202A §2: Serialize FindingEnvelope to payload_text string.</p>
<p></p>
<p>Fail-soft: returns None if serialization fails or size exceeds limit.</p>
<p>Caller degrades to plain finding when None is returned.</p>
</div>
</details>
</li>
<li><code>_warm_cache</code> (lancedb_store.py) — <span class="doc-comment-inline">Pre-load frequently accessed embeddings.</span></li>
<li><code>_ensure_store</code> (lancedb_store.py) — <span class="doc-comment-inline">Lazily initialize SqliteVecStore.</span></li>
<li><code>_detect_query_type</code> (lancedb_store.py)
<details><summary>AREA H+: Decide whether to use FTS, hybrid, or pure vector search.</summary>
<div class="doc-comment">
<p>AREA H+: Decide whether to use FTS, hybrid, or pure vector search.</p>
<p>Same heuristic as LanceDBIdentityStore for consistency.</p>
</div>
</details>
</li>
<li><code>_init_index</code> (rag_engine.py) — <span class="doc-comment-inline">Initialize the usearch index.</span></li>
<li><code>_dense_retrieval</code> (rag_engine.py) — <span class="doc-comment-inline">Dense retrieval using cosine similarity.</span></li>
<li><code>_log_table_opened</code> (ann_index.py) — <span class="doc-comment-inline">Log 'lancedb.table_opened' event with size_mb.</span></li>
<li><code>_maybe_evict</code> (ann_index.py) — <span class="doc-comment-inline">Evict oldest entries if table exceeds MAX_ENTRIES.</span></li>
<li><code>_build_sparql_query</code> (entity_linker.py)
<details><summary>Build SPARQL query for entity search.</summary>
<div class="doc-comment">
<p>Build SPARQL query for entity search.</p>
<p></p>
<p>Args:</p>
<p>entity_text: Text to search for</p>
<p>limit: Maximum results</p>
<p></p>
<p>Returns:</p>
<p>SPARQL query string</p>
</div>
</details>
</li>
<li><code>record_observation</code> (ioc_graph.py)
<details><summary>Record an OBSERVED edge between two IOC nodes.</summary>
<div class="doc-comment">
<p>Record an OBSERVED edge between two IOC nodes.</p>
<p></p>
<p>Idempotent: if the edge already exists, updates last_seen on the edge.</p>
</div>
</details>
</li>
<li><code>truth_write_graph_supports_buffered_writes</code> (graph_attachment.py)
<details><summary>Sprint 8WA: Does _truth_write_graph support ACTIVE-phase buffered writes?</summary>
<div class="doc-comment">
<p>Sprint 8WA: Does _truth_write_graph support ACTIVE-phase buffered writes?</p>
<p></p>
<p>Returns True only if _truth_write_graph is IOCGraph (Kuzu) with both:</p>
<p>- buffer_ioc()</p>
<p>- flush_buffers()</p>
<p></p>
<p>This is a dedicated check for the truth-write slot, independent of</p>
<p>the analytics _ioc_graph slot.</p>
</div>
</details>
</li>
<li><code>wal_clear_pending_sync_marker</code> (wal.py)
<details><summary>Clear a pending-sync marker after successful recovery.</summary>
<div class="doc-comment">
<p>Clear a pending-sync marker after successful recovery.</p>
<p></p>
<p>Called by a future recovery sprint after the DuckDB write succeeds.</p>
</div>
</details>
</li>
<li><code>set_uma_state</code> (duckdb_store.py)
<details><summary>Set or update UMA memory pressure state at runtime.</summary>
<div class="doc-comment">
<p>Set or update UMA memory pressure state at runtime.</p>
<p></p>
<p>Can be called while the store is open to adjust DuckDB settings.</p>
<p>Resolves new settings and applies to all connections immediately.</p>
<p></p>
<p>Args:</p>
<p>uma_state: "WARN", "CRITICAL", "EMERGENCY", or None for normal.</p>
<p>swap_detected: True if system-level swap is active.</p>
</div>
</details>
</li>
<li><code>_record_query_latency</code> (duckdb_store.py) — <span class="doc-comment-inline">Record a DuckDB query latency to MetricsRegistry (fail-safe).</span></li>
<li><code>_prewarm_file_conn</code> (duckdb_store.py)
<details><summary>Sprint 7H: Amortize cold connect by issuing a no-op query.</summary>
<div class="doc-comment">
<p>Sprint 7H: Amortize cold connect by issuing a no-op query.</p>
<p>Called on first write to warm up _file_conn.</p>
<p>Returns True if prewarm succeeded.</p>
</div>
</details>
</li>
<li><code>async_query_sprint_trend</code> (duckdb_store.py)
<details><summary>Return trend data for the last N sprints, ordered by ts DESC.</summary>
<div class="doc-comment">
<p>Return trend data for the last N sprints, ordered by ts DESC.</p>
<p>Thread-safe, non-blocking.</p>
</div>
</details>
</li>
<li><code>_sync_query_findings_by_text</code> (duckdb_store.py) — <span class="doc-comment-inline">Sync - MUST be called on worker thread.</span></li>
<li><code>get_recent_worst_sprints</code> (duckdb_store.py)
<details><summary>Sprint F150I: Return the bottom N sprints by yield (new_findings / duration_s).</summary>
<div class="doc-comment">
<p>Sprint F150I: Return the bottom N sprints by yield (new_findings / duration_s).</p>
<p>Only sprints with new_findings &gt; 0 are included (exclude zero-yield noise).</p>
<p>Reads from sprint_delta. Fail-soft, bounded.</p>
</div>
</details>
</li>
<li><code>_extract_url_from_provenance</code> (duckdb_store.py)
<details><summary>Sprint 8AK: Extract the first HTTP(S) URL from a provenance tuple.</summary>
<div class="doc-comment">
<p>Sprint 8AK: Extract the first HTTP(S) URL from a provenance tuple.</p>
<p></p>
<p>Source-agnostic: scans all positions regardless of source type.</p>
<p>Returns empty string if no URL is found.</p>
</div>
</details>
</li>
<li><code>_wal_clear_pending_sync_marker</code> (duckdb_store.py)
<details><summary>Sprint 8F: Clear a pending-sync marker after successful recovery.</summary>
<div class="doc-comment">
<p>Sprint 8F: Clear a pending-sync marker after successful recovery.</p>
<p></p>
<p>Called by a future recovery sprint after the DuckDB write succeeds.</p>
</div>
</details>
</li>
<li><code>_wal_get_pending_marker</code> (duckdb_store.py)
<details><summary>Sprint 8H: Get a single pending marker value by finding_id.</summary>
<div class="doc-comment">
<p>Sprint 8H: Get a single pending marker value by finding_id.</p>
<p></p>
<p>Returns the marker dict or None if not found.</p>
</div>
</details>
</li>
<li><code>_add_to_hot_cache</code> (duckdb_store.py)
<details><summary>Add entry to bounded hot cache with FIFO eviction.</summary>
<div class="doc-comment">
<p>Add entry to bounded hot cache with FIFO eviction.</p>
<p></p>
<p>DEPRECATED (Sprint F222): Delegates to DedupManager.add_to_hot_cache().</p>
<p>Kept for backward compat during migration - remove when all callers migrated.</p>
</div>
</details>
</li>
<li><code>_lmdb_put_multi</code> (lancedb_store.py)
<details><summary>Synchronous batch LMDB put - single transaction for multiple items.</summary>
<div class="doc-comment">
<p>Synchronous batch LMDB put - single transaction for multiple items.</p>
<p></p>
<p>S3: Reduces 100 individual txn.begin() calls to 1.</p>
</div>
</details>
</li>
<li><code>get_cache_telemetry</code> (lancedb_store.py) — <span class="doc-comment-inline">F214OPT-C: Telemetry accessor for LanceDB cache bounds and stats.</span></li>
<li><code>_get_colbert_reranker</code> (lancedb_store.py) — <span class="doc-comment-inline">Lazy load ColBERT.</span></li>
<li><code>_embed_texts</code> (lancedb_store.py)
<details><summary>Embed texts via the initialized embedder backend.</summary>
<div class="doc-comment">
<p>Embed texts via the initialized embedder backend.</p>
<p></p>
<p>``self._embedder`` is guaranteed non-None by ``_init_embedder``,</p>
<p>which raises ``RuntimeError`` on no backend (no silent fallback).</p>
<p>``MLXEmbeddingManager`` exposes ``.encode(texts)``.</p>
</div>
</details>
</li>
<li><code>upsert_paper</code> (lancedb_store.py)
<details><summary>Upsert a single academic paper.</summary>
<div class="doc-comment">
<p>Upsert a single academic paper.</p>
<p></p>
<p>Args:</p>
<p>paper: AcademicPaper instance to store.</p>
</div>
</details>
</li>
<li><code>__init__</code> (graph_rag.py)
<details><summary>Initialize GraphRAG orchestrator.</summary>
<div class="doc-comment">
<p>Initialize GraphRAG orchestrator.</p>
<p></p>
<p>Args:</p>
<p>knowledge_layer: PersistentKnowledgeLayer instance</p>
</div>
</details>
</li>
<li><code>_calculate_community_cohesion</code> (graph_rag.py) — <span class="doc-comment-inline">Calculate cohesion score for a community.</span></li>
<li><code>__init__</code> (rag_engine.py)</li>
<li><code>_estimate_memory_usage</code> (rag_engine.py) — <span class="doc-comment-inline">Estimate memory usage in MB.</span></li>
<li><code>graph_stats</code> (graph_service.py) — <span class="doc-comment-inline">Return graph node/edge statistics. Returns empty dict on error.</span></li>
<li><code>_load_state</code> (lancedb_auto_tuner.py) — <span class="doc-comment-inline">Load state from JSON. Returns TuneState() on any error (fail-soft).</span></li>
<li><code>__init__</code> (ioc_graph.py)</li>
<li><code>buffer_ioc</code> (ioc_graph.py)
<details><summary>Add IOC to in-memory buffer — ZERO Kuzu I/O in ACTIVE phase.</summary>
<div class="doc-comment">
<p>Add IOC to in-memory buffer — ZERO Kuzu I/O in ACTIVE phase.</p>
<p>Flush automatically when buffer reaches _BUFFER_FLUSH_SIZE.</p>
<p></p>
<p>After close() the buffer is closed: new writes are silently dropped</p>
<p>so no buffered data can be lost or observed in an inconsistent state.</p>
</div>
</details>
</li>
<li><code>__init__</code> (semantic_store.py)</li>
<li><code>aclose</code> (wal.py)
<details><summary>Async idempotent shutdown — canonical async cleanup path.</summary>
<div class="doc-comment">
<p>Async idempotent shutdown — canonical async cleanup path.</p>
<p></p>
<p>Uses asyncio.to_thread() to avoid blocking the event loop.</p>
<p>Idempotent: safe to call multiple times.</p>
</div>
</details>
</li>
<li><code>add_batch</code> (ioc_dedup_adapter.py)
<details><summary>Batch add IOCs. Returns list of bool (True = new).</summary>
<div class="doc-comment">
<p>Batch add IOCs. Returns list of bool (True = new).</p>
<p></p>
<p>Args:</p>
<p>items: List of (value, ioc_type, confidence) tuples</p>
</div>
</details>
</li>
<li><code>advance_sprint</code> (ioc_dedup_adapter.py)
<details><summary>Advance to new sprint. Persists current state to LMDB before advancing.</summary>
<div class="doc-comment">
<p>Advance to new sprint. Persists current state to LMDB before advancing.</p>
<p></p>
<p>Called by sprint_scheduler at sprint boundary.</p>
</div>
</details>
</li>
<li><code>get_entries_by_type</code> (ioc_dedup_adapter.py)
<details><summary>Get entries with full metadata for a given IOC type.</summary>
<div class="doc-comment">
<p>Get entries with full metadata for a given IOC type.</p>
<p></p>
<p>Returns:</p>
<p>List of (normalized_value, first_sprint, last_sprint, occurrence_count, confidence_max)</p>
</div>
</details>
</li>
<li><code>__init__</code> (duckdb_store.py)</li>
<li><code>iter_batches_async</code> (duckdb_store.py)
<details><summary>Async iterator for use in async contexts.</summary>
<div class="doc-comment">
<p>Async iterator for use in async contexts.</p>
<p></p>
<p>Yields batches on a thread pool to avoid blocking event loop.</p>
</div>
</details>
</li>
<li><code>_l1_get</code> (duckdb_store.py)</li>
<li><code>_graph_store</code> (duckdb_store.py) — <span class="doc-comment-inline">Lazy-init GraphAttachmentStore.</span></li>
<li><code>_invalidate_insert_stmt</code> (duckdb_store.py)
<details><summary>Sprint F264: Drop cached prepared statement. Call on close / reconnect.</summary>
<div class="doc-comment">
<p>Sprint F264: Drop cached prepared statement. Call on close / reconnect.</p>
<p></p>
<p>Safe to call from any thread; sets the cache to None so the next</p>
<p>`_get_insert_stmt(conn)` re-prepares on the (possibly new) conn.</p>
</div>
</details>
</li>
<li><code>_sync_query_sprint_ioc_summary</code> (duckdb_store.py) — <span class="doc-comment-inline">Sync - MUST be called on worker thread.</span></li>
<li><code>get_recent_best_sprints</code> (duckdb_store.py)
<details><summary>Sprint F150I: Return the top N sprints by yield (new_findings / duration_s).</summary>
<div class="doc-comment">
<p>Sprint F150I: Return the top N sprints by yield (new_findings / duration_s).</p>
<p>Reads from sprint_delta. Fail-soft, bounded.</p>
</div>
</details>
</li>
<li><code>async_record_entity_observations_bulk</code> (duckdb_store.py) — <span class="doc-comment-inline">Sprint F350M: Bulk record entity observations.</span></li>
<li><code>async_get_research_sessions_by_sprint</code> (duckdb_store.py) — <span class="doc-comment-inline">Sprint F350M: Get research sessions by sprint_id.</span></li>
<li><code>async_get_recent_research_sessions</code> (duckdb_store.py) — <span class="doc-comment-inline">Sprint F350M: Get recent research sessions.</span></li>
<li><code>advance_ioc_sprint</code> (duckdb_store.py)
<details><summary>Advance IOC dedup store to new sprint boundary.</summary>
<div class="doc-comment">
<p>Advance IOC dedup store to new sprint boundary.</p>
<p></p>
<p>Issue #14: Delegates to SprintBoundaryCoordinator to keep</p>
<p>_DuckDBQueryCache pure-cache (no dedup knowledge) and</p>
<p>DedupManager pure-dedup (no cache knowledge).</p>
</div>
</details>
</li>
<li><code>reset_ingest_reason_counters</code> (duckdb_store.py)
<details><summary>Sprint 8AV: Reset all ingest outcome counters to zero.</summary>
<div class="doc-comment">
<p>Sprint 8AV: Reset all ingest outcome counters to zero.</p>
<p></p>
<p>Side-effect free, test-safe, can be called any time.</p>
<p>Resets all counters on QualityAssessmentState.</p>
</div>
</details>
</li>
<li><code>_warm_embedding_cache</code> (lancedb_store.py) — <span class="doc-comment-inline">Pre-load embeddings for frequently used queries.</span></li>
<li><code>_init_secure_enclave</code> (rag_engine.py) — <span class="doc-comment-inline">Inicializovat Secure Enclave</span></li>
<li><code>ask_sync</code> (analyst_workbench.py)
<details><summary>Synchronous wrapper around ask().</summary>
<div class="doc-comment">
<p>Synchronous wrapper around ask().</p>
<p></p>
<p>For use in sync contexts. Prefer ask() in async contexts.</p>
</div>
</details>
</li>
<li><code>__init__</code> (entity_linker.py)
<details><summary>Initialize cache.</summary>
<div class="doc-comment">
<p>Initialize cache.</p>
<p></p>
<p>Args:</p>
<p>max_size: Maximum number of entries</p>
<p>ttl_seconds: Time-to-live in seconds</p>
</div>
</details>
</li>
<li><code>_save_state</code> (lancedb_auto_tuner.py) — <span class="doc-comment-inline">Persist state atomically. Fail-soft — never raises.</span></li>
<li><code>_rss_under_guard</code> (lancedb_auto_tuner.py)
<details><summary>True iff process RSS is below M1 8GB safety threshold.</summary>
<div class="doc-comment">
<p>True iff process RSS is below M1 8GB safety threshold.</p>
<p></p>
<p>Fail-soft: if psutil is missing or measurement fails, returns True</p>
<p>(allow tuning) — the existing per-table row guards still bound work.</p>
</div>
</details>
</li>
<li><code>_init_schema_sync</code> (ioc_graph.py) — <span class="doc-comment-inline">Synchronous schema init — runs on _executor thread.</span></li>
<li><code>record_observation_batch</code> (ioc_graph.py)
<details><summary>Batch record of OBSERVED edges between IOC nodes.</summary>
<div class="doc-comment">
<p>Batch record of OBSERVED edges between IOC nodes.</p>
<p></p>
<p>Args:</p>
<p>observations: List of (ioc_id_a, ioc_id_b, finding_id, ts, source_type).</p>
<p>Idempotent: duplicate edges update last_seen only.</p>
</div>
</details>
</li>
<li><code>wal_delete_deadletter_marker</code> (wal.py) — <span class="doc-comment-inline">Delete a dead-letter marker (used when replay succeeds later).</span></li>
<li><code>__del__</code> (wal.py)
<details><summary>Fallback destructor -- weakref.finalize is primary, __del__ is last resort.</summary>
<div class="doc-comment">
<p>Fallback destructor -- weakref.finalize is primary, __del__ is last resort.</p>
<p></p>
<p>In Python 3.14+ __del__ is not guaranteed to run, so _ensure_cleanup()</p>
<p>(via weakref.finalize) is the canonical cleanup path.</p>
</div>
</details>
</li>
<li><code>_ensure_lmdb</code> (ioc_dedup_adapter.py) — <span class="doc-comment-inline">Ensure LMDB environment is open. Returns True if successful.</span></li>
<li><code>get</code> (duckdb_store.py)</li>
<li><code>invalidate</code> (duckdb_store.py) — <span class="doc-comment-inline">Clear L1 and L2. Called after schema migration.</span></li>
<li><code>_sync_insert_finding</code> (duckdb_store.py)</li>
<li><code>insert_shadow_run</code> (duckdb_store.py)</li>
<li><code>_submit_findings_bg</code> (duckdb_store.py) — <span class="doc-comment-inline">Background task — runs submit_findings() logic without blocking the caller.</span></li>
<li><code>_duckdb_arrow_sync</code> (duckdb_store.py)
<details><summary>Sprint P1-2: DuckDB Arrow-only sync helper - DuckDB Single-Writer Variant 2.</summary>
<div class="doc-comment">
<p>Sprint P1-2: DuckDB Arrow-only sync helper - DuckDB Single-Writer Variant 2.</p>
<p></p>
<p>Runs on _duckdb_arrow_executor. Caller is responsible for WAL step</p>
<p>(separate executor, sequential WAL-first invariant).</p>
<p></p>
<p>Returns (inserted_count, error_type) - same shape as</p>
<p>_sync_record_canonical_findings_batch_arrow.</p>
</div>
</details>
</li>
<li><code>_wal_delete_deadletter_marker</code> (duckdb_store.py) — <span class="doc-comment-inline">Sprint 8H: Delete a dead-letter marker (used when replay succeeds later).</span></li>
<li><code>_compute_binary_signatures_batch</code> (lancedb_store.py) — <span class="doc-comment-inline">MLX version for batched calculations.</span></li>
<li><code>__init__</code> (lancedb_store.py)</li>
<li><code>_calculate_average_path_length</code> (graph_rag.py) — <span class="doc-comment-inline">Calculate average shortest path length.</span></li>
<li><code>resize_index</code> (rag_engine.py)
<details><summary>Resize the index to accommodate more elements.</summary>
<div class="doc-comment">
<p>Resize the index to accommodate more elements.</p>
<p></p>
<p>Args:</p>
<p>new_max_elements: New maximum number of elements</p>
</div>
</details>
</li>
<li><code>initialize</code> (rag_engine.py) — <span class="doc-comment-inline">Inicializovat RAG engine</span></li>
<li><code>rejection_rate</code> (quality_assessment.py)
<details><summary>Sprint F216G: Compute rejection rate across all quality gate decisions.</summary>
<div class="doc-comment">
<p>Sprint F216G: Compute rejection rate across all quality gate decisions.</p>
<p></p>
<p>Returns fraction of rejected findings [0.0, 1.0].</p>
<p>Returns 0.0 if no decisions have been recorded yet.</p>
</div>
</details>
</li>
<li><code>add_to_hot_cache</code> (quality_assessment.py) — <span class="doc-comment-inline">Add fingerprint → finding_id to hot cache with FIFO eviction.</span></li>
<li><code>reset_session</code> (graph_service.py)
<details><summary>Clear session-level idempotency trackers and graph singleton.</summary>
<div class="doc-comment">
<p>Clear session-level idempotency trackers and graph singleton.</p>
<p></p>
<p>Call at sprint start to prevent cross-sprint state leakage.</p>
<p>Resets only this instance's state — does NOT affect other instances.</p>
</div>
</details>
</li>
<li><code>set</code> (entity_linker.py) — <span class="doc-comment-inline">Cache value with timestamp.</span></li>
<li><code>__init__</code> (lancedb_auto_tuner.py)</li>
<li><code>graph_stats</code> (ioc_graph.py) — <span class="doc-comment-inline">Return total node and edge counts.</span></li>
<li><code>init_ct_cache_schema</code> (db.py) — <span class="doc-comment-inline">Initialize CT log cache table in DuckDB.</span></li>
<li><code>get_graph_attachment_kind</code> (graph_attachment.py)
<details><summary>NON-AUTHORITATIVE DIAGNOSTIC: returns the class name of the attached graph.</summary>
<div class="doc-comment">
<p>NON-AUTHORITATIVE DIAGNOSTIC: returns the class name of the attached graph.</p>
<p></p>
<p>Returns None if no graph attached.</p>
<p>Use this to determine which backend is attached, then call</p>
<p>hasattr/hasattr for specific capability checks before use.</p>
<p></p>
<p>This is a COMPAT SEAM, not a canonical graph API.</p>
</div>
</details>
</li>
<li><code>initialize</code> (wal.py) — <span class="doc-comment-inline">Lazily initialize the WAL LMDB store.</span></li>
<li><code>_atexit_cleanup</code> (wal.py)
<details><summary>Emergency sync cleanup for atexit.register().</summary>
<div class="doc-comment">
<p>Emergency sync cleanup for atexit.register().</p>
<p></p>
<p>Called at interpreter shutdown as last resort for lock file release.</p>
<p>Uses sync close() since event loop is not available at atexit time.</p>
</div>
</details>
</li>
<li><code>get_stats</code> (ioc_dedup_adapter.py) — <span class="doc-comment-inline">Get current dedup statistics.</span></li>
<li><code>dynamic_schema</code> (duckdb_store.py)
<details><summary>Issue 4.3: Dynamic schema via msgspec.json.schema().</summary>
<div class="doc-comment">
<p>Issue 4.3: Dynamic schema via msgspec.json.schema().</p>
<p></p>
<p>Replaces SCHEMA_VERSION constants. At startup, validates that in-memory</p>
<p>CanonicalFinding shape matches the persisted DuckDB table schema.</p>
<p></p>
<p>Returns JSON schema dict for runtime validation.</p>
</div>
</details>
</li>
<li><code>_payload_to_envelope</code> (duckdb_store.py)
<details><summary>Sprint F202A §2: Deserialize FindingEnvelope from payload_text string.</summary>
<div class="doc-comment">
<p>Sprint F202A §2: Deserialize FindingEnvelope from payload_text string.</p>
<p></p>
<p>Fail-soft: returns None if payload_text is None/empty, parsing fails,</p>
<p>or required audit_reason field is missing.</p>
</div>
</details>
</li>
<li><code>_vacuum_sync</code> (duckdb_store.py) — <span class="doc-comment-inline">Execute VACUUM ANALYZE synchronously on worker thread.</span></li>
<li><code>_lookup_persistent_dedup</code> (duckdb_store.py)
<details><summary>Lookup a fingerprint in the persistent dedup LMDB.</summary>
<div class="doc-comment">
<p>Lookup a fingerprint in the persistent dedup LMDB.</p>
<p></p>
<p>DEPRECATED (Sprint F222): Delegates to DedupManager.lookup_persistent_dedup().</p>
<p>Kept for backward compat during migration - remove when all callers migrated.</p>
</div>
</details>
</li>
<li><code>_hot_cache_lookup</code> (duckdb_store.py)
<details><summary>Bounded hot cache lookup.</summary>
<div class="doc-comment">
<p>Bounded hot cache lookup.</p>
<p></p>
<p>DEPRECATED (Sprint F222): Delegates to DedupManager.hot_cache_lookup().</p>
<p>Kept for backward compat during migration - remove when all callers migrated.</p>
</div>
</details>
</li>
<li><code>_detect_query_type</code> (lancedb_store.py) — <span class="doc-comment-inline">Decide whether to use FTS, hybrid, or pure vector search.</span></li>
<li><code>_cache_maintenance_loop</code> (lancedb_store.py) — <span class="doc-comment-inline">Background cache maintenance task.</span></li>
<li><code>_get_score_semaphore</code> (graph_rag.py) — <span class="doc-comment-inline">Lazy-init semaphore for bounded parallel scoring (M1 8GB safe).</span></li>
<li><code>get_statistics</code> (graph_rag.py)
<details><summary>Get GraphRAG orchestrator statistics.</summary>
<div class="doc-comment">
<p>Get GraphRAG orchestrator statistics.</p>
<p></p>
<p>Returns:</p>
<p>Dictionary with statistics</p>
</div>
</details>
</li>
<li><code>get_hnsw_stats</code> (rag_engine.py)
<details><summary>Get HNSW index statistics.</summary>
<div class="doc-comment">
<p>Get HNSW index statistics.</p>
<p></p>
<p>Returns:</p>
<p>Dictionary with index statistics, or None if index not built</p>
</div>
</details>
</li>
<li><code>_get_random_chunks</code> (rag_engine.py) — <span class="doc-comment-inline">Return up to n random text chunks from documents.</span></li>
<li><code>get_runtime_status</code> (dedup.py)
<details><summary>Return typed/cheap status surface for dedup subsystem.</summary>
<div class="doc-comment">
<p>Return typed/cheap status surface for dedup subsystem.</p>
<p></p>
<p>Args:</p>
<p>quality_state: QualityAssessmentState instance with _quality_duplicate_count,</p>
<p>_persistent_duplicate_count, _accepted_count, _quality_rejected_count,</p>
<p>_quality_fail_open_count.</p>
</div>
</details>
</li>
<li><code>_load_gliner</code> (entity_linker.py) — <span class="doc-comment-inline">Lazy load GLiNER model.</span></li>
<li><code>close</code> (entity_linker.py) — <span class="doc-comment-inline">Close HTTP session and cleanup resources.</span></li>
<li><code>_upsert_ioc_sync</code> (ioc_graph.py) — <span class="doc-comment-inline">Synchronous upsert — runs on _executor thread.</span></li>
<li><code>get_truth_write_graph</code> (graph_attachment.py)
<details><summary>Sprint 8WA: Get injected truth-write graph for ACTIVE-phase consumers.</summary>
<div class="doc-comment">
<p>Sprint 8WA: Get injected truth-write graph for ACTIVE-phase consumers.</p>
<p></p>
<p>F320 UPDATE: Both DuckPGQGraph and IOCGraph are returned</p>
<p>(both have buffer_ioc/flush_buffers).</p>
<p></p>
<p>This is a CONSUMER-SPECIFIC seam for ACTIVE-phase buffered writes only.</p>
</div>
</details>
</li>
<li><code>wal_get_finding</code> (wal.py) — <span class="doc-comment-inline">Get a WAL truth record by finding_id.</span></li>
<li><code>wal_get_pending_marker</code> (wal.py) — <span class="doc-comment-inline">Get a single pending marker value by finding_id.</span></li>
<li><code>wal_put</code> (wal.py) — <span class="doc-comment-inline">Put a raw WAL entry.</span></li>
<li><code>wal_put_many</code> (wal.py) — <span class="doc-comment-inline">Put multiple raw WAL entries. Returns per-item success list.</span></li>
<li><code>_ensure_atexit</code> (wal.py)
<details><summary>Legacy: Register atexit cleanup if not already registered.</summary>
<div class="doc-comment">
<p>Legacy: Register atexit cleanup if not already registered.</p>
<p></p>
<p>Deprecated: Use _ensure_cleanup() instead (weakref.finalize).</p>
<p>Kept for backward compat.</p>
</div>
</details>
</li>
<li><code>_cleanup_on_shutdown</code> (wal.py)
<details><summary>E4: Cleanup callback for weakref.finalize -- called at interpreter shutdown.</summary>
<div class="doc-comment">
<p>E4: Cleanup callback for weakref.finalize -- called at interpreter shutdown.</p>
<p></p>
<p>Idempotent: safe even if close() was already called.</p>
</div>
</details>
</li>
<li><code>_key</code> (duckdb_store.py) — <span class="doc-comment-inline">Stable cache key: sha256(sql + "|" + json(params)).</span></li>
<li><code>inject_semantic_store</code> (duckdb_store.py)
<details><summary>Sprint 8SB: Inject SemanticStore instance for semantic buffering of findings.</summary>
<div class="doc-comment">
<p>Sprint 8SB: Inject SemanticStore instance for semantic buffering of findings.</p>
<p></p>
<p>The store is used to buffer findings for FastEmbed embedding + LanceDB</p>
<p>indexing during WINDUP flush.</p>
</div>
</details>
</li>
<li><code>_semantic_buffer_findings</code> (duckdb_store.py)
<details><summary>Sprint 8SB: Buffer findings into SemanticStore for batch embedding.</summary>
<div class="doc-comment">
<p>Sprint 8SB: Buffer findings into SemanticStore for batch embedding.</p>
<p></p>
<p>Runs as a background task (not awaited). Fail-open: any exception</p>
<p>is caught and logged - semantic buffering failure never blocks storage.</p>
<p>Delegated to SemanticStoreBuffer.</p>
</div>
</details>
</li>
<li><code>insert_shadow_finding</code> (duckdb_store.py) — <span class="doc-comment-inline">Sync insert - backward compat. For async use async_record_shadow_finding().</span></li>
<li><code>query_recent_findings</code> (duckdb_store.py) — <span class="doc-comment-inline">Sync query - backward compat. For async use async_query_recent_findings().</span></li>
<li><code>pending_marker_count</code> (duckdb_store.py)
<details><summary>Sprint 8L: Return the number of pending_duckdb_sync:* markers in WAL LMDB.</summary>
<div class="doc-comment">
<p>Sprint 8L: Return the number of pending_duckdb_sync:* markers in WAL LMDB.</p>
<p></p>
<p>Cheap O(n) prefix scan - bounded by REPLAY_CHUNK_SIZE scan.</p>
<p>Used for observability and benchmarking.</p>
</div>
</details>
</li>
<li><code>_build_adjacency_list</code> (graph_rag.py) — <span class="doc-comment-inline">Build adjacency list for graph analysis.</span></li>
<li><code>get_stats</code> (rag_engine.py)
<details><summary>Get index statistics.</summary>
<div class="doc-comment">
<p>Get index statistics.</p>
<p></p>
<p>Returns:</p>
<p>Dictionary with index statistics</p>
</div>
</details>
</li>
<li><code>enable_hnsw</code> (rag_engine.py)
<details><summary>Enable or disable HNSW search.</summary>
<div class="doc-comment">
<p>Enable or disable HNSW search.</p>
<p></p>
<p>Args:</p>
<p>enable: True to enable HNSW, False to use brute-force</p>
</div>
</details>
</li>
<li><code>close</code> (rag_engine.py) — <span class="doc-comment-inline">Zavřít engine</span></li>
<li><code>__init__</code> (graph_service.py)</li>
<li><code>checkpoint</code> (graph_service.py) — <span class="doc-comment-inline">Flush WAL to disk. No-op on error.</span></li>
<li><code>_use_rust_rotate</code> (dedup.py) — <span class="doc-comment-inline">True if using Rust RotatingMmapBloomFilter (race-free).</span></li>
<li><code>_get_oldest_timestamp</code> (ann_index.py) — <span class="doc-comment-inline">Get timestamp of oldest entry.</span></li>
<li><code>buffer_observation</code> (ioc_graph.py)
<details><summary>Add observation to in-memory buffer — ZERO Kuzu I/O in ACTIVE phase.</summary>
<div class="doc-comment">
<p>Add observation to in-memory buffer — ZERO Kuzu I/O in ACTIVE phase.</p>
<p></p>
<p>After close() the buffer is closed: new writes are silently dropped.</p>
</div>
</details>
</li>
<li><code>initialize</code> (ioc_graph.py) — <span class="doc-comment-inline">Create schema if not exists (try/except for already-exists).</span></li>
<li><code>_record_observation_sync</code> (ioc_graph.py) — <span class="doc-comment-inline">Synchronous observation record — runs on _executor thread.</span></li>
<li><code>__init__</code> (db.py)</li>
<li><code>lmdb_put</code> (db.py) — <span class="doc-comment-inline">Put value into LMDB cache.</span></li>
<li><code>lmdb_delete</code> (db.py) — <span class="doc-comment-inline">Delete key from LMDB cache.</span></li>
<li><code>get_stix_graph</code> (graph_attachment.py)
<details><summary>Sprint 8VQ: Get injected STIX graph for synthesis consumers.</summary>
<div class="doc-comment">
<p>Sprint 8VQ: Get injected STIX graph for synthesis consumers.</p>
<p></p>
<p>F320 UPDATE: Both DuckPGQGraph and IOCGraph are returned (both have export_stix_bundle).</p>
<p></p>
<p>This is a CONSUMER-SPECIFIC seam, not a generic graph accessor.</p>
</div>
</details>
</li>
<li><code>_ensure_cleanup</code> (wal.py)
<details><summary>E4: Register weakref.finalize for guaranteed cleanup on interpreter shutdown.</summary>
<div class="doc-comment">
<p>E4: Register weakref.finalize for guaranteed cleanup on interpreter shutdown.</p>
<p></p>
<p>Replaces atexit.register() as primary safety net (Python 3.14+ refcounting</p>
<p>changes make __del__ non-deterministic). weakref.finalize is guaranteed to run.</p>
</div>
</details>
</li>
<li><code>__repr__</code> (duckdb_store.py)</li>
<li><code>_l1_set</code> (duckdb_store.py)</li>
<li><code>close</code> (duckdb_store.py)</li>
<li><code>get_quality_rejection_ledger</code> (duckdb_store.py)
<details><summary>Sprint F216G: Expose the quality rejection ledger to callers (e.g. scheduler).</summary>
<div class="doc-comment">
<p>Sprint F216G: Expose the quality rejection ledger to callers (e.g. scheduler).</p>
<p></p>
<p>Returns a tuple (immutable view) of all recorded rejection records.</p>
<p>Delegates to QualityAssessmentState for backward compat.</p>
</div>
</details>
</li>
<li><code>size_bytes</code> (duckdb_store.py) — <span class="doc-comment-inline">Return the database file size in bytes, or None for :memory: mode.</span></li>
<li><code>_init_semantic_dedup_cache</code> (duckdb_store.py)
<details><summary>Initialize semantic dedup cache.</summary>
<div class="doc-comment">
<p>Initialize semantic dedup cache.</p>
<p></p>
<p>DEPRECATED (Sprint F222): Semantic dedup is now initialized by DedupManager.initialize().</p>
<p>This stub exists only for backward compat - calls are no longer emitted.</p>
</div>
</details>
</li>
<li><code>_embed_single</code> (lancedb_store.py) — <span class="doc-comment-inline">Embed text via MLX.</span></li>
<li><code>add_entity</code> (lancedb_store.py) — <span class="doc-comment-inline">Add entity to identity store. API-compatible with LanceDBIdentityStore.</span></li>
<li><code>shutdown</code> (graph_rag.py)
<details><summary>Gracefully shutdown the orchestrator and release resources.</summary>
<div class="doc-comment">
<p>Gracefully shutdown the orchestrator and release resources.</p>
<p></p>
<p>R4.1: Thread pool no longer owned by this class — Rust rayon pools</p>
<p>(io_pool, cpu_pool) are process-level singletons managed by Rust.</p>
<p>No explicit shutdown needed from Python side.</p>
</div>
</details>
</li>
<li><code>update_ef_search</code> (rag_engine.py)
<details><summary>Update ef_search parameter for search quality/speed tradeoff.</summary>
<div class="doc-comment">
<p>Update ef_search parameter for search quality/speed tradeoff.</p>
<p></p>
<p>Args:</p>
<p>ef_search: New ef_search value (higher = better recall, slower)</p>
</div>
</details>
</li>
<li><code>_init_ultra_context</code> (rag_engine.py) — <span class="doc-comment-inline">Inicializovat InfiniteContextEngine</span></li>
<li><code>_init_spr_compressor</code> (rag_engine.py) — <span class="doc-comment-inline">Inicializovat SPR Compressor</span></li>
<li><code>initialize</code> (dedup.py)
<details><summary>Eager initialize — kept for backward compat, marks initialized.</summary>
<div class="doc-comment">
<p>Eager initialize — kept for backward compat, marks initialized.</p>
<p>All sub-systems are now lazy-initialized on first actual use.</p>
</div>
</details>
</li>
<li><code>_check_memory_guard</code> (ann_index.py) — <span class="doc-comment-inline">Return True if ANN init is safe (RSS below threshold).</span></li>
<li><code>lmdb_get</code> (db.py) — <span class="doc-comment-inline">Get value from LMDB cache.</span></li>
<li><code>apply_decay</code> (neuromorphic.py) — <span class="doc-comment-inline">Apply decay to all memory strengths.</span></li>
<li><code>contains</code> (ioc_dedup_adapter.py) — <span class="doc-comment-inline">Check if IOC exists in store (without affecting counters).</span></li>
<li><code>get_by_type</code> (ioc_dedup_adapter.py) — <span class="doc-comment-inline">Get all IOC values of specified type.</span></li>
<li><code>annotate_findings_with_graph_context</code> (duckdb_store.py)</li>
<li><code>_sync_insert_findings_bulk</code> (duckdb_store.py)
<details><summary>Sprint 7H: True bulk insert using executemany in explicit transaction.</summary>
<div class="doc-comment">
<p>Sprint 7H: True bulk insert using executemany in explicit transaction.</p>
<p>MUST be called on the worker thread.</p>
<p>Returns number of successfully inserted records.</p>
</div>
</details>
</li>
<li><code>_record_quality_rejection</code> (duckdb_store.py)
<details><summary>Sprint F216G: Record a quality gate rejection to the bounded ledger.</summary>
<div class="doc-comment">
<p>Sprint F216G: Record a quality gate rejection to the bounded ledger.</p>
<p></p>
<p>Delegates to QualityAssessmentState.record_rejection().</p>
</div>
</details>
</li>
<li><code>_sync_insert_findings_bulk_as_tuples</code> (duckdb_store.py)
<details><summary>Sprint 8R: Bulk insert using list[tuple] with 6 columns (id, query, source_type, confidence, ts, provenance_json).  # noqa: E501</summary>
<div class="doc-comment">
<p>Sprint 8R: Bulk insert using list[tuple] with 6 columns (id, query, source_type, confidence, ts, provenance_json).  # noqa: E501</p>
<p>MUST be called on the worker thread.</p>
<p>Returns number of successfully inserted records.</p>
</div>
</details>
</li>
<li><code>_lmdb_put</code> (lancedb_store.py) — <span class="doc-comment-inline">Synchronous LMDB put operation - zero-copy via orjson.</span></li>
<li><code>_delete_cached_embedding</code> (lancedb_store.py) — <span class="doc-comment-inline">Delete embedding from cache.</span></li>
<li><code>close</code> (lancedb_store.py) — <span class="doc-comment-inline">Close database connection.</span></li>
<li><code>get_rejection_history</code> (quality_assessment.py)
<details><summary>Sprint F216G: Expose the quality rejection ledger to callers (e.g. scheduler).</summary>
<div class="doc-comment">
<p>Sprint F216G: Expose the quality rejection ledger to callers (e.g. scheduler).</p>
<p></p>
<p>Returns a tuple (immutable view) of all recorded rejection records.</p>
</div>
</details>
</li>
<li><code>record_rejection</code> (quality_assessment.py)</li>
<li><code>reset_counters</code> (quality_assessment.py) — <span class="doc-comment-inline">Reset all counters. Called on store reset.</span></li>
<li><code>persist</code> (dedup.py) — <span class="doc-comment-inline">Sync active filter to disk (msync handled by Rust).</span></li>
<li><code>_ema_recall</code> (lancedb_auto_tuner.py)
<details><summary>Exponential moving average of recall for trend detection.</summary>
<div class="doc-comment">
<p>Exponential moving average of recall for trend detection.</p>
<p></p>
<p>P0-2: Closed-loop PID — smooths noise in recall measurements so the</p>
<p>controller reacts to direction, not single noisy samples.</p>
</div>
</details>
</li>
<li><code>_close_sync</code> (ioc_graph.py)</li>
<li><code>wal_delete</code> (wal.py) — <span class="doc-comment-inline">Delete a WAL entry by key.</span></li>
<li><code>wal_get</code> (wal.py) — <span class="doc-comment-inline">Get a raw WAL entry.</span></li>
<li><code>contains</code> (ioc_dedup_adapter.py) — <span class="doc-comment-inline">Check if IOC exists.</span></li>
<li><code>normalize_value</code> (ioc_dedup_adapter.py)
<details><summary>Normalize IOC value according to type rules (mirrors Rust normalize_ioc).</summary>
<div class="doc-comment">
<p>Normalize IOC value according to type rules (mirrors Rust normalize_ioc).</p>
<p></p>
<p>Useful for callers that need the normalized form without adding to store.</p>
</div>
</details>
</li>
<li><code>filter_time_range</code> (duckdb_store.py) — <span class="doc-comment-inline">Set time filter for row-group pruning. Returns self for chaining.</span></li>
<li><code>total_rows</code> (duckdb_store.py) — <span class="doc-comment-inline">Return total row count across all row-groups.</span></li>
<li><code>put</code> (duckdb_store.py)</li>
<li><code>__aexit__</code> (duckdb_store.py)
<details><summary>Async context manager exit - cleans up the store.</summary>
<div class="doc-comment">
<p>Async context manager exit - cleans up the store.</p>
<p>Idempotent: safe to call even if already closed.</p>
</div>
</details>
</li>
<li><code>_get_embedding_manager</code> (lancedb_store.py) — <span class="doc-comment-inline">Lazily get MLX embedding manager.</span></li>
<li><code>_matches_filters</code> (rag_engine.py) — <span class="doc-comment-inline">Check if document matches filters.</span></li>
<li><code>stats_dict</code> (ioc_dedup_adapter.py) — <span class="doc-comment-inline">SoA-style stats dict matching Rust stats_dict().</span></li>
<li><code>_rollback</code> (duckdb_store.py)</li>
<li><code>_qe</code> (duckdb_store.py) — <span class="doc-comment-inline">Lazy executor - created on first _sync_* access, shared for instance lifetime.</span></li>
<li><code>_sync_insert_run</code> (duckdb_store.py)</li>
<li><code>_finding_id_of</code> (duckdb_store.py) — <span class="doc-comment-inline">Extract finding_id from CanonicalFinding or dict, safely.</span></li>
<li><code>_ensure_replay_lock</code> (duckdb_store.py) — <span class="doc-comment-inline">Lazily initialize the replay lock on the current event loop.</span></li>
<li><code>_compute_binary_signature</code> (lancedb_store.py) — <span class="doc-comment-inline">64-bit binary signature - numpy packbits (faster for 64 elements).</span></li>
<li><code>__init__</code> (lancedb_store.py)</li>
<li><code>rotate</code> (dedup.py) — <span class="doc-comment-inline">Rotate: active becomes previous (read-only), new empty active.</span></li>
<li><code>close</code> (dedup.py) — <span class="doc-comment-inline">Close mmap filters and sync to disk.</span></li>
<li><code>_get_session</code> (entity_linker.py) — <span class="doc-comment-inline">Get or create httpx.AsyncClient session (F4XX).</span></li>
<li><code>__new__</code> (db.py)</li>
<li><code>duckdb</code> (db.py) — <span class="doc-comment-inline">DuckDBShadowStore singleton — canonical store for structured data.</span></li>
<li><code>lmdb</code> (db.py) — <span class="doc-comment-inline">LMDB environment for cache/dedup/KV operations.</span></li>
<li><code>__init__</code> (graph_attachment.py)</li>
<li><code>__init__</code> (ioc_dedup_adapter.py)</li>
<li><code>filter_source_types</code> (duckdb_store.py) — <span class="doc-comment-inline">Set source_type filter. None = no filter. Returns self for chaining.</span></li>
<li><code>__init__</code> (duckdb_store.py)</li>
<li><code>_get_node_content</code> (graph_rag.py) — <span class="doc-comment-inline">Get node content by ID.</span></li>
<li><code>_calculate_betweenness</code> (graph_rag.py) — <span class="doc-comment-inline">Calculate betweenness centrality via igraph C-core (50-100x faster).</span></li>
<li><code>_calculate_closeness</code> (graph_rag.py) — <span class="doc-comment-inline">Calculate closeness centrality via igraph C-core.</span></li>
<li><code>_calculate_eigenvector</code> (graph_rag.py) — <span class="doc-comment-inline">Calculate eigenvector centrality via igraph C-core.</span></li>
<li><code>_calculate_pagerank</code> (graph_rag.py) — <span class="doc-comment-inline">Calculate PageRank via igraph C-core.</span></li>
<li><code>_get_node_type</code> (graph_rag.py) — <span class="doc-comment-inline">Get node type for a node ID.</span></li>
<li><code>_is_complex_query</code> (rag_engine.py) — <span class="doc-comment-inline">Detekovat komplexní dotaz pro Tree of Thoughts</span></li>
<li><code>__init__</code> (dedup.py)</li>
<li><code>clear</code> (entity_linker.py) — <span class="doc-comment-inline">Clear all cached entries.</span></li>
<li><code>clear_cache</code> (entity_linker.py) — <span class="doc-comment-inline">Clear the query cache.</span></li>
<li><code>__init__</code> (neuromorphic.py)</li>
<li><code>decay</code> (neuromorphic.py) — <span class="doc-comment-inline">Apply exponential decay to memory strength.</span></li>
<li><code>cleanup</code> (neuromorphic.py) — <span class="doc-comment-inline">Aggressive cleanup for M1 memory constraints.</span></li>
<li><code>get_by_type</code> (ioc_dedup_adapter.py) — <span class="doc-comment-inline">Get all IOC values of specified type.</span></li>
<li><code>get_entries_by_type</code> (ioc_dedup_adapter.py) — <span class="doc-comment-inline">Get entries with full metadata.</span></li>
<li><code>to_bytes</code> (ioc_dedup_adapter.py) — <span class="doc-comment-inline">Serialize state to bytes (compatible with Rust get_state_bytes).</span></li>
<li><code>clear</code> (ioc_dedup_adapter.py)</li>
<li><code>add_step</code> (evidence_chain.py) — <span class="doc-comment-inline">Add a step to the chain. Silently drops if MAX_CHAIN_DEPTH reached.</span></li>
<li><code>get_uma_state</code> (duckdb_store.py) — <span class="doc-comment-inline">Return currently configured UMA state.</span></li>
<li><code>inject_graph</code> (duckdb_store.py) — <span class="doc-comment-inline">DEPRECATED (Sprint F222): Delegates to GraphAttachmentStore.inject_graph().</span></li>
<li><code>get_graph_attachment_kind</code> (duckdb_store.py) — <span class="doc-comment-inline">DEPRECATED (Sprint F222): Delegates to GraphAttachmentStore.get_graph_attachment_kind().</span></li>
<li><code>graph_supports_buffered_writes</code> (duckdb_store.py) — <span class="doc-comment-inline">DEPRECATED (Sprint F222): Delegates to GraphAttachmentStore.graph_supports_buffered_writes().</span></li>
<li><code>inject_stix_graph</code> (duckdb_store.py) — <span class="doc-comment-inline">DEPRECATED (Sprint F222): Delegates to GraphAttachmentStore.inject_stix_graph().</span></li>
<li><code>get_stix_graph</code> (duckdb_store.py) — <span class="doc-comment-inline">DEPRECATED (Sprint F222): Delegates to GraphAttachmentStore.get_stix_graph().</span></li>
<li><code>inject_truth_write_graph</code> (duckdb_store.py) — <span class="doc-comment-inline">DEPRECATED (Sprint F222): Delegates to GraphAttachmentStore.inject_truth_write_graph().</span></li>
<li><code>get_truth_write_graph</code> (duckdb_store.py) — <span class="doc-comment-inline">DEPRECATED (Sprint F222): Delegates to GraphAttachmentStore.get_truth_write_graph().</span></li>
<li><code>truth_write_graph_supports_buffered_writes</code> (duckdb_store.py) — <span class="doc-comment-inline">DEPRECATED (Sprint F222): Delegates to GraphAttachmentStore.truth_write_graph_supports_buffered_writes().</span></li>
<li><code>get_top_seed_nodes</code> (duckdb_store.py) — <span class="doc-comment-inline">DEPRECATED (Sprint F222): Delegates to GraphAttachmentStore.get_top_seed_nodes().</span></li>
<li><code>get_graph_stats</code> (duckdb_store.py) — <span class="doc-comment-inline">DEPRECATED (Sprint F222): Delegates to GraphAttachmentStore.get_graph_stats().</span></li>
<li><code>get_connected_iocs</code> (duckdb_store.py) — <span class="doc-comment-inline">DEPRECATED (Sprint F222): Delegates to GraphAttachmentStore.get_connected_iocs().</span></li>
<li><code>get_connected_iocs_batch</code> (duckdb_store.py) — <span class="doc-comment-inline">DEPRECATED (Sprint F222): Delegates to GraphAttachmentStore.get_connected_iocs_batch().</span></li>
<li><code>get_analytics_graph_for_synthesis</code> (duckdb_store.py) — <span class="doc-comment-inline">DEPRECATED (Sprint F222): Delegates to GraphAttachmentStore.get_analytics_graph_for_synthesis().</span></li>
<li><code>get_top_entities_for_ghost_global</code> (duckdb_store.py) — <span class="doc-comment-inline">DEPRECATED (Sprint F222): Delegates to GraphAttachmentStore.get_top_entities_for_ghost_global().</span></li>
<li><code>_sync_query_findings</code> (duckdb_store.py) — <span class="doc-comment-inline">Sync query - MUST be called on the worker thread.</span></li>
<li><code>_sync_upsert_target_profile</code> (duckdb_store.py) — <span class="doc-comment-inline">Sync upsert - MUST be called on the worker thread.</span></li>
<li><code>is_initialized</code> (duckdb_store.py) — <span class="doc-comment-inline">Return True if sidecar was successfully initialized.</span></li>
<li><code>is_closed</code> (duckdb_store.py) — <span class="doc-comment-inline">Return True if sidecar has been shut down.</span></li>
<li><code>db_path</code> (duckdb_store.py) — <span class="doc-comment-inline">Return the database path (None for :memory: mode).</span></li>
<li><code>temp_dir</code> (duckdb_store.py) — <span class="doc-comment-inline">Return the temp directory path (None if not using RAMDISK).</span></li>
<li><code>memory_limit</code> (duckdb_store.py) — <span class="doc-comment-inline">Return the configured memory limit string.</span></li>
<li><code>max_temp</code> (duckdb_store.py) — <span class="doc-comment-inline">Return the configured max temp size string.</span></li>
<li><code>is_ramdisk_mode</code> (duckdb_store.py) — <span class="doc-comment-inline">Return True if running in RAMDISK-active mode.</span></li>
<li><code>executor</code> (duckdb_store.py) — <span class="doc-comment-inline">Return the internal executor (for test introspection).</span></li>
<li><code>startup_ready</code> (duckdb_store.py) — <span class="doc-comment-inline">Sprint 8L: True if boot barrier has been lifted (store accepts writes).</span></li>
<li><code>startup_replay_done</code> (duckdb_store.py) — <span class="doc-comment-inline">Sprint 8L: True if startup replay has run (regardless of outcome).</span></li>
<li><code>invariant_memory_limit</code> (duckdb_store.py) — <span class="doc-comment-inline">Return configured memory_limit string.</span></li>
<li><code>invariant_max_temp</code> (duckdb_store.py) — <span class="doc-comment-inline">Return configured max_temp_directory_size string.</span></li>
<li><code>invariant_temp_dir</code> (duckdb_store.py) — <span class="doc-comment-inline">Return configured temp_directory path (None if :memory: mode).</span></li>
<li><code>_dedup_key_from_fingerprint</code> (duckdb_store.py) — <span class="doc-comment-inline">Build dedup namespace key from BLAKE2b fingerprint.</span></li>
<li><code>_dedup_lmdb_key_to_fingerprint</code> (duckdb_store.py) — <span class="doc-comment-inline">Extract fingerprint from dedup namespace key.</span></li>
<li><code>initialize</code> (lancedb_store.py) — <span class="doc-comment-inline">Explicit init (optional). Stores are lazy inited on first use.</span></li>
<li><code>to_dict</code> (lancedb_store.py) — <span class="doc-comment-inline">Convert to dict for LanceDB storage.</span></li>
<li><code>_get_all_node_ids</code> (graph_rag.py) — <span class="doc-comment-inline">Get all node IDs from knowledge layer.</span></li>
<li><code>_tokenize</code> (rag_engine.py) — <span class="doc-comment-inline">Simple tokenization</span></li>
<li><code>hot_cache_lookup</code> (quality_assessment.py) — <span class="doc-comment-inline">Look up fingerprint in hot cache. Returns finding_id or None.</span></li>
<li><code>get_rejection_history</code> (quality_assessment.py) — <span class="doc-comment-inline">Delegate to QualityAssessmentState.get_rejection_history().</span></li>
<li><code>increment_accepted</code> (quality_assessment.py) — <span class="doc-comment-inline">Increment accepted count when finding passes quality gate.</span></li>
<li><code>increment_fail_open</code> (quality_assessment.py) — <span class="doc-comment-inline">Increment fail-open counter when quality check raises.</span></li>
<li><code>register_relationship_callback</code> (graph_service.py) — <span class="doc-comment-inline">Register callback for relationship events (src, dst, rel_type, weight).</span></li>
<li><code>add</code> (dedup.py)</li>
<li><code>__contains__</code> (dedup.py)</li>
<li><code>__len__</code> (dedup.py)</li>
<li><code>sync</code> (dedup.py) — <span class="doc-comment-inline">No-op for in-memory filter.</span></li>
<li><code>_dedup_key_from_fingerprint</code> (dedup.py) — <span class="doc-comment-inline">Build dedup namespace key from BLAKE2b fingerprint.</span></li>
<li><code>_dedup_lmdb_key_to_fingerprint</code> (dedup.py) — <span class="doc-comment-inline">Extract fingerprint from dedup namespace key.</span></li>
<li><code>_hot_cache_max</code> (dedup.py) — <span class="doc-comment-inline">Hot cache max size from config.</span></li>
<li><code>hot_cache_lookup</code> (dedup.py) — <span class="doc-comment-inline">Bounded hot cache lookup.</span></li>
<li><code>semantic_dedup_cache</code> (dedup.py) — <span class="doc-comment-inline">Return the semantic dedup cache instance.</span></li>
<li><code>to_dict</code> (entity_linker.py) — <span class="doc-comment-inline">Convert to dictionary for serialization.</span></li>
<li><code>from_dict</code> (entity_linker.py) — <span class="doc-comment-inline">Create from dictionary.</span></li>
<li><code>to_dict</code> (entity_linker.py) — <span class="doc-comment-inline">Convert to dictionary for serialization.</span></li>
<li><code>from_dict</code> (entity_linker.py) — <span class="doc-comment-inline">Create from dictionary.</span></li>
<li><code>_generate_key</code> (entity_linker.py) — <span class="doc-comment-inline">Generate cache key from query.</span></li>
<li><code>get_stats</code> (entity_linker.py) — <span class="doc-comment-inline">Get cache statistics.</span></li>
<li><code>_init_ner_patterns</code> (entity_linker.py) — <span class="doc-comment-inline">Initialize regex patterns for fallback NER.</span></li>
<li><code>get_cache_stats</code> (entity_linker.py) — <span class="doc-comment-inline">Get cache statistics.</span></li>
<li><code>__aenter__</code> (entity_linker.py) — <span class="doc-comment-inline">Async context manager entry.</span></li>
<li><code>__aexit__</code> (entity_linker.py) — <span class="doc-comment-inline">Async context manager exit.</span></li>
<li><code>changed</code> (lancedb_auto_tuner.py) — <span class="doc-comment-inline">True iff the partition count was actually modified.</span></li>
<li><code>table_name</code> (lancedb_auto_tuner.py) — <span class="doc-comment-inline">Public accessor for the table name (read-only).</span></li>
<li><code>vector_column</code> (lancedb_auto_tuner.py) — <span class="doc-comment-inline">Public accessor for the vector column name (read-only).</span></li>
<li><code>key_column</code> (lancedb_auto_tuner.py) — <span class="doc-comment-inline">Public accessor for the key column name (read-only).</span></li>
<li><code>enabled</code> (lancedb_auto_tuner.py) — <span class="doc-comment-inline">Auto-tune gate. Independent of F264D ``HLEDAC_LANCEDB_QUANTIZE``.</span></li>
<li><code>state</code> (lancedb_auto_tuner.py) — <span class="doc-comment-inline">Current persistent state (read-only snapshot).</span></li>
<li><code>num_sub_vectors</code> (lancedb_auto_tuner.py) — <span class="doc-comment-inline">Configured sub-vector count (immutable for tuner lifetime).</span></li>
<li><code>_record_observation_batch_sync_async</code> (ioc_graph.py) — <span class="doc-comment-inline">Async wrapper — runs sync impl on background thread via asyncio.to_thread.</span></li>
<li><code>rust_pool_ready</code> (db.py) — <span class="doc-comment-inline">Check if Rust connection pool is available.</span></li>
<li><code>lancedb_available</code> (db.py) — <span class="doc-comment-inline">LanceDB is deprecated — returns False.</span></li>
<li><code>sqlite3_available</code> (db.py) — <span class="doc-comment-inline">SQLite3 for caching is deprecated — use DuckDB or LMDB.</span></li>
<li><code>lmdb</code> (wal.py) — <span class="doc-comment-inline">Return the WAL LMDB store (may be None if using unified store).</span></li>
<li><code>unified_store</code> (wal.py) — <span class="doc-comment-inline">Return the unified store if using unified mode.</span></li>
<li><code>_key_finding</code> (wal.py) — <span class="doc-comment-inline">Build finding key.</span></li>
<li><code>_key_pending_sync</code> (wal.py) — <span class="doc-comment-inline">Build pending sync marker key.</span></li>
<li><code>_key_deadletter</code> (wal.py) — <span class="doc-comment-inline">Build deadletter key.</span></li>
<li><code>reinforce</code> (neuromorphic.py) — <span class="doc-comment-inline">Reinforce memory strength (capped at 1.0).</span></li>
<li><code>add_batch</code> (ioc_dedup_adapter.py) — <span class="doc-comment-inline">Batch add — returns list of bool (True = new).</span></li>
<li><code>advance_sprint</code> (ioc_dedup_adapter.py) — <span class="doc-comment-inline">Advance to next sprint.</span></li>
<li><code>stats</code> (ioc_dedup_adapter.py) — <span class="doc-comment-inline">Returns (total_seen, total_deduped, unique_count).</span></li>
<li><code>current_sprint</code> (ioc_dedup_adapter.py) — <span class="doc-comment-inline">Get current sprint ID.</span></li>
<li><code>flush</code> (ioc_dedup_adapter.py) — <span class="doc-comment-inline">Explicitly flush state to LMDB (called during sprint winddown).</span></li>
<li><code>depth</code> (evidence_chain.py) — <span class="doc-comment-inline">Number of steps in the chain.</span></li>
<li><code>is_empty</code> (evidence_chain.py) — <span class="doc-comment-inline">True if chain has no steps.</span></li>
<li><code>__init__</code> (evidence_chain.py)</li>
<li><code>record_ingest</code> (evidence_chain.py) — <span class="doc-comment-inline">Convenience: record the ingest step for a root finding.</span></li>
<li><code>record_identity</code> (evidence_chain.py) — <span class="doc-comment-inline">Convenience: record an identity stitching step.</span></li>
<li><code>record_attribution</code> (evidence_chain.py) — <span class="doc-comment-inline">Convenience: record an attribution scoring step.</span></li>
<li><code>record_exposure</code> (evidence_chain.py) — <span class="doc-comment-inline">Convenience: record an exposure correlation step.</span></li>
<li><code>record_leak</code> (evidence_chain.py) — <span class="doc-comment-inline">Convenience: record a leak sentinel step.</span></li>
<li><code>record_temporal</code> (evidence_chain.py) — <span class="doc-comment-inline">Convenience: record a temporal archaeology step.</span></li>
<li><code>record_diff</code> (evidence_chain.py) — <span class="doc-comment-inline">Convenience: record a sprint diff step.</span></li>
<li><code>record_killchain</code> (evidence_chain.py) — <span class="doc-comment-inline">Convenience: record a kill chain tagging step.</span></li>
<li><code>record_evidence_triage</code> (evidence_chain.py) — <span class="doc-comment-inline">Convenience: record an evidence triage step.</span></li>
<li><code>record_pivot</code> (evidence_chain.py) — <span class="doc-comment-inline">Convenience: record a pivot planning step.</span></li>
<li><code>build</code> (evidence_chain.py) — <span class="doc-comment-inline">Return the chain for root_finding_id, or None if not tracked.</span></li>
<li><code>build_all</code> (evidence_chain.py) — <span class="doc-comment-inline">Return all chains, newest-first by root_finding_id sort.</span></li>
<li><code>get_chain_count</code> (evidence_chain.py) — <span class="doc-comment-inline">Number of chains currently tracked.</span></li>
<li><code>get_total_steps</code> (evidence_chain.py) — <span class="doc-comment-inline">Total steps recorded across all chains.</span></li>
<li><code>__len__</code> (duckdb_store.py)</li>
<li><code>_accepted_count</code> (duckdb_store.py)</li>
<li><code>_quality_duplicate_count</code> (duckdb_store.py)</li>
<li><code>_quality_rejected_count</code> (duckdb_store.py)</li>
<li><code>_persistent_duplicate_count</code> (duckdb_store.py)</li>
<li><code>_begin</code> (duckdb_store.py)</li>
<li><code>_commit</code> (duckdb_store.py)</li>
<li><code>__hash__</code> (rag_engine.py)</li>
<li><code>to_dict</code> (rag_engine.py)</li>
<li><code>metric</code> (lancedb_auto_tuner.py)</li>
<li><code>limit</code> (lancedb_auto_tuner.py)</li>
<li><code>to_list</code> (lancedb_auto_tuner.py)</li>
<li><code>to_pandas</code> (lancedb_auto_tuner.py)</li>
<li><code>count_rows</code> (lancedb_auto_tuner.py)</li>
<li><code>search</code> (lancedb_auto_tuner.py)</li>
<li><code>create_index</code> (lancedb_auto_tuner.py)</li>
<li><code>to_polars</code> (lancedb_auto_tuner.py)</li>
<li><code>__len__</code> (ioc_dedup_adapter.py)</li>
<li><code>is_empty</code> (ioc_dedup_adapter.py)</li>
<li><code>get_sprint</code> (ioc_dedup_adapter.py)</li>
<li><code>len</code> (ioc_dedup_adapter.py)</li>
<li><code>is_empty</code> (ioc_dedup_adapter.py)</li>
</ul>
</details>

<details><summary><strong>Constant</strong> (155)</summary>
<ul>
<li><code>_FEED_SOURCE_TYPES</code> (quality_assessment.py)</li>
<li><code>_LMDB_MAP_SIZE</code> (hot_edges_cache.py)</li>
<li><code>_QUALITY_ENTROPY_THRESHOLD</code> (quality_assessment.py)</li>
<li><code>_QUALITY_MIN_ENTROPY_LEN</code> (quality_assessment.py)</li>
<li><code>_IOC_CHUNK</code> (duckdb_store.py)</li>
<li><code>_QUALITY_GATE_BATCH_AVAILABLE</code> (duckdb_store.py)</li>
<li><code>_RUST_ASSESS_QUALITY_BATCH_AVAILABLE</code> (duckdb_store.py)</li>
<li><code>_RUST_ARROW_AVAILABLE</code> (duckdb_store.py)</li>
<li><code>_IOC_EXTRACT_BATCH_AVAILABLE</code> (duckdb_store.py)</li>
<li><code>_IOC_EXTRACT_PYTHON_ZERO_COPY_AVAILABLE</code> (duckdb_store.py)</li>
<li><code>_RUST_PARQUET_AVAILABLE</code> (duckdb_store.py)</li>
<li><code>_DUCKDB_MEMORY_LIMIT</code> (duckdb_store.py)</li>
<li><code>_DUCKDB_MAX_TEMP</code> (duckdb_store.py)</li>
<li><code>_DUCKDB_HARD_MEMORY_LIMIT</code> (duckdb_store.py)</li>
<li><code>_ARROW_INGEST_ENABLED</code> (duckdb_store.py)</li>
<li><code>_DUCKDB_RAMDISK_TEMP</code> (duckdb_store.py)</li>
<li><code>_ARROW_MIN_BATCH</code> (duckdb_store.py)</li>
<li><code>_DUCKDB_QUERY_CACHE_ENABLED</code> (duckdb_store.py)</li>
<li><code>_DUCKDB_QUERY_CACHE_L1_MAX</code> (duckdb_store.py)</li>
<li><code>_DUCKDB_QUERY_CACHE_L2_MAX</code> (duckdb_store.py)</li>
<li><code>_DUCKDB_QUERY_CACHE_TTL_S</code> (duckdb_store.py)</li>
<li><code>_DEDUP_LMDB_MAP_SIZE</code> (duckdb_store.py)</li>
<li><code>_SCHEMA_SQL</code> (duckdb_store.py)</li>
<li><code>_MAX_INFLIGHT_GRAPH_UPDATES</code> (duckdb_store.py)</li>
<li><code>_COMPILED_CACHE</code> (lancedb_store.py)</li>
<li><code>_RRF_RERANKER_CACHE</code> (lancedb_store.py)</li>
<li><code>_DEFAULT_URI</code> (lancedb_store.py)</li>
<li><code>_HLEDAC_DEFAULT_CACHE_MB</code> (lancedb_store.py)</li>
<li><code>_HLEDAC_HARD_MAX_CACHE_MB</code> (lancedb_store.py)</li>
<li><code>_HLEDAC_LARGE_OVERRIDE_VAR</code> (lancedb_store.py)</li>
<li><code>_HLEDAC_CACHE_MB_VAR</code> (lancedb_store.py)</li>
<li><code>_WRITEBACK_MAX</code> (lancedb_store.py)</li>
<li><code>_WRITEBACK_BATCH_SIZE</code> (lancedb_store.py)</li>
<li><code>_WRITE_QUEUE</code> (lancedb_store.py)</li>
<li><code>_WRITE_QUEUE_LOCK</code> (lancedb_store.py)</li>
<li><code>_WRITE_WORKER_TASK</code> (lancedb_store.py)</li>
<li><code>COREML_AVAILABLE</code> (rag_engine.py)</li>
<li><code>COREML_MODEL_PATH</code> (rag_engine.py)</li>
<li><code>MAX_CONTEXT_BYTES</code> (analyst_workbench.py)</li>
<li><code>MAX_TOP_K</code> (analyst_workbench.py)</li>
<li><code>MAX_GRAPH_HOPS</code> (analyst_workbench.py)</li>
<li><code>MAX_EVIDENCE_PTRS</code> (analyst_workbench.py)</li>
<li><code>MAX_RELATED_ENTITIES</code> (analyst_workbench.py)</li>
<li><code>MAX_ENVELOPE_SIZE</code> (analyst_workbench.py)</li>
<li><code>MAX_BRIEF_FINDINGS</code> (analyst_workbench.py)</li>
<li><code>MAX_BRIEF_CHAINS</code> (analyst_workbench.py)</li>
<li><code>MAX_BRIEF_NEXT_ACTIONS</code> (analyst_workbench.py)</li>
<li><code>MAX_GRAPH_ANALYTICS_BRIEF_FINDINGS</code> (analyst_workbench.py)</li>
<li><code>MAX_CORROBORATION_SUMMARY</code> (analyst_workbench.py)</li>
<li><code>MAX_FEED_CLUSTERS</code> (analyst_workbench.py)</li>
<li><code>MAX_SAMPLE_IDS_PER_CLUSTER</code> (analyst_workbench.py)</li>
<li><code>MAX_TEXT_PER_CLUSTER</code> (analyst_workbench.py)</li>
<li><code>MAX_RISK_HYPOTHESES</code> (analyst_workbench.py)</li>
<li><code>MAX_PIVOT_RECOMMENDATIONS</code> (analyst_workbench.py)</li>
<li><code>_RUST_COUNTERS_AVAILABLE</code> (hot_edges_cache.py)</li>
<li><code>_LMDB_PATH</code> (hot_edges_cache.py)</li>
<li><code>_KEY_PREFIX</code> (hot_edges_cache.py)</li>
<li><code>MAX_HOT_NEIGHBORS_PER_NODE</code> (hot_edges_cache.py)</li>
<li><code>MAX_HOT_NODES</code> (hot_edges_cache.py)</li>
<li><code>HOT_EDGES_ENABLED</code> (hot_edges_cache.py)</li>
<li><code>_UINT64_MAX</code> (hot_edges_cache.py)</li>
<li><code>_ENV</code> (hot_edges_cache.py)</li>
<li><code>_ENV_OPEN_FAILED</code> (hot_edges_cache.py)</li>
<li><code>_COUNTER_KEY</code> (hot_edges_cache.py)</li>
<li><code>_HOT_EDGES_COMPRESS</code> (hot_edges_cache.py)</li>
<li><code>_VERSION_DENORM</code> (hot_edges_cache.py)</li>
<li><code>_WIRE_MARKER_DENORM</code> (hot_edges_cache.py)</li>
<li><code>_DUCKDB_RO_CON</code> (hot_edges_cache.py)</li>
<li><code>_HIGH_CONF_IOC_RE</code> (quality_assessment.py)</li>
<li><code>_DEDUP_LMDB_MAP_SIZE</code> (quality_assessment.py)</li>
<li><code>_DEDUP_HOT_CACHE_MAX</code> (quality_assessment.py)</li>
<li><code>_RUST_IOC_DEDUP_AVAILABLE</code> (graph_service.py)</li>
<li><code>MAX_GRAPH_ANALYTICS_NODES</code> (graph_service.py)</li>
<li><code>MAX_GRAPH_ANALYTICS_TOP_K</code> (graph_service.py)</li>
<li><code>_DUCKPGQ_GRAPH</code> (graph_service.py)</li>
<li><code>_DEFAULT_GRAPH_SERVICE</code> (graph_service.py)</li>
<li><code>_DEDUP_LMDB_MAP_SIZE</code> (dedup.py)</li>
<li><code>_DEDUP_HOT_CACHE_MAX</code> (dedup.py)</li>
<li><code>_RUST_MMAP_IOC_DEDUP_AVAILABLE</code> (dedup.py)</li>
<li><code>_DEDUP_MANAGER_FINALIZERS</code> (dedup.py)</li>
<li><code>_SIGTERM_HANDLER_REGISTERED</code> (dedup.py)</li>
<li><code>_EMBEDDING_DIM</code> (ann_index.py)</li>
<li><code>_TABLE_NAME</code> (ann_index.py)</li>
<li><code>_MAX_ENTRIES</code> (ann_index.py)</li>
<li><code>_MIN_SCORE</code> (ann_index.py)</li>
<li><code>_MEMORY_GUARD_GB</code> (ann_index.py)</li>
<li><code>_USEARCH_CONNECTIVITY</code> (ann_index.py)</li>
<li><code>_USEARCH_EXPANSION_ADD</code> (ann_index.py)</li>
<li><code>_USEARCH_EXPANSION_SEARCH</code> (ann_index.py)</li>
<li><code>_IVF_PQ_PARTITIONS</code> (ann_index.py)</li>
<li><code>_IVF_PQ_SUB_VECTORS</code> (ann_index.py)</li>
<li><code>GLINER_AVAILABLE</code> (entity_linker.py)</li>
<li><code>DEFAULT_NUM_PARTITIONS</code> (lancedb_auto_tuner.py)</li>
<li><code>MIN_NUM_PARTITIONS</code> (lancedb_auto_tuner.py)</li>
<li><code>MAX_NUM_PARTITIONS</code> (lancedb_auto_tuner.py)</li>
<li><code>DEFAULT_NUM_SUB_VECTORS</code> (lancedb_auto_tuner.py)</li>
<li><code>MIN_NUM_SUB_VECTORS</code> (lancedb_auto_tuner.py)</li>
<li><code>MAX_NUM_SUB_VECTORS</code> (lancedb_auto_tuner.py)</li>
<li><code>M1_MAX_ITERATIONS</code> (lancedb_auto_tuner.py)</li>
<li><code>AUTO_TUNE_ENV</code> (lancedb_auto_tuner.py)</li>
<li><code>INSERT_THRESHOLD_ENV</code> (lancedb_auto_tuner.py)</li>
<li><code>DEFAULT_INSERT_THRESHOLD</code> (lancedb_auto_tuner.py)</li>
<li><code>COOLDOWN_SECONDS_ENV</code> (lancedb_auto_tuner.py)</li>
<li><code>DEFAULT_COOLDOWN_SECONDS</code> (lancedb_auto_tuner.py)</li>
<li><code>DEFAULT_RECALL_SAMPLES</code> (lancedb_auto_tuner.py)</li>
<li><code>MAX_RECALL_SAMPLES</code> (lancedb_auto_tuner.py)</li>
<li><code>RECALL_TOP_K</code> (lancedb_auto_tuner.py)</li>
<li><code>RECALL_TOO_LOW</code> (lancedb_auto_tuner.py)</li>
<li><code>RECALL_EXCELLENT</code> (lancedb_auto_tuner.py)</li>
<li><code>SEARCH_MS_EXCESSIVE</code> (lancedb_auto_tuner.py)</li>
<li><code>M1_RSS_GUARD_BYTES</code> (lancedb_auto_tuner.py)</li>
<li><code>MAX_BRUTE_FORCE_ROWS</code> (lancedb_auto_tuner.py)</li>
<li><code>_KUZU_AVAILABLE</code> (ioc_graph.py)</li>
<li><code>_KUZU_DB_ROOT</code> (ioc_graph.py)</li>
<li><code>_IOC_GRAPH_FILENAME</code> (ioc_graph.py)</li>
<li><code>IOC_TYPES</code> (ioc_graph.py)</li>
<li><code>_RE_IP_PUBLIC</code> (ioc_graph.py)</li>
<li><code>_RE_SHA256</code> (ioc_graph.py)</li>
<li><code>_RE_ONION_V3</code> (ioc_graph.py)</li>
<li><code>_RE_ONION_V2</code> (ioc_graph.py)</li>
<li><code>_IOC_EXTRACTOR</code> (ioc_graph.py)</li>
<li><code>MAX_EXTRACT_BATCH</code> (ioc_graph.py)</li>
<li><code>_EMBED_DIM</code> (semantic_store.py)</li>
<li><code>_MAX_PENDING</code> (semantic_store.py)</li>
<li><code>_MAX_TEXT_LEN</code> (semantic_store.py)</li>
<li><code>_TABLE_NAME</code> (semantic_store.py)</li>
<li><code>CPU_EXECUTOR</code> (semantic_store.py)</li>
<li><code>_DUCKDB_STORE</code> (db.py)</li>
<li><code>_DUCKDB_POOL_READY</code> (db.py)</li>
<li><code>_LMDB_ENV</code> (db.py)</li>
<li><code>_RUST_POOL</code> (db.py)</li>
<li><code>MAX_SIMILARITIES</code> (neuromorphic.py)</li>
<li><code>MAX_PATTERNS</code> (neuromorphic.py)</li>
<li><code>_IOC_DEDUP_LMDB_PATH</code> (ioc_dedup_adapter.py)</li>
<li><code>SOURCE_FAMILY_FEED</code> (evidence_chain.py)</li>
<li><code>SOURCE_FAMILY_CT</code> (evidence_chain.py)</li>
<li><code>SOURCE_FAMILY_PUBLIC</code> (evidence_chain.py)</li>
<li><code>SOURCE_FAMILY_DEEP</code> (evidence_chain.py)</li>
<li><code>SOURCE_FAMILY_DOC</code> (evidence_chain.py)</li>
<li><code>CORROBORATION_NONE</code> (evidence_chain.py)</li>
<li><code>CORROBORATION_SINGLE</code> (evidence_chain.py)</li>
<li><code>CORROBORATION_MULTI</code> (evidence_chain.py)</li>
<li><code>MAX_CHAIN_DEPTH</code> (evidence_chain.py)</li>
<li><code>MAX_CHAINS_PER_SPRINT</code> (evidence_chain.py)</li>
<li><code>MAX_CHAIN_JSON_BYTES</code> (evidence_chain.py)</li>
<li><code>STEP_TYPE_INGEST</code> (evidence_chain.py)</li>
<li><code>STEP_TYPE_IDENTITY</code> (evidence_chain.py)</li>
<li><code>STEP_TYPE_EXPOSURE</code> (evidence_chain.py)</li>
<li><code>STEP_TYPE_LEAK</code> (evidence_chain.py)</li>
<li><code>STEP_TYPE_TEMPORAL</code> (evidence_chain.py)</li>
<li><code>STEP_TYPE_DIFF</code> (evidence_chain.py)</li>
<li><code>STEP_TYPE_KILLCHAIN</code> (evidence_chain.py)</li>
<li><code>STEP_TYPE_EVIDENCE_TRIAGE</code> (evidence_chain.py)</li>
<li><code>STEP_TYPE_ATTRIBUTION</code> (evidence_chain.py)</li>
<li><code>STEP_TYPE_PIVOT</code> (evidence_chain.py)</li>
</ul>
</details>



## Metrics

| Metric | Value |
|---|---|
| Files | 63 |
| Total lines | 33861 |
| Avg lines/file | 537 |
| Languages | Python |
| Outgoing deps | 0 |
| Incoming deps | 4 |
| Tier | 1 |

