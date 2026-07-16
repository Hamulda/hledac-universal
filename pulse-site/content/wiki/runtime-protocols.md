+++
title = "runtime/protocols/"
weight = 18
description = "<think> Let me analyze the structural context for the `runtime/protocols` module:  1. **Purpose**: This module defines Protocol classes (Python typing constructs) that establish interfaces for various..."

[extra]
tier = 2
file_count = 16
total_lines = 1027
languages = "Python"
parent_path = "runtime"
+++

<think>
Let me analyze the structural context for the `runtime/protocols` module:

1. **Purpose**: This module defines Protocol classes (Python typing constructs) that establish interfaces for various subsystems in the system. It acts as a contracts/interfaces layer.

2. **Key characteristics**:
 - 16 Python files
 - 1027 total lines
 - No outgoing or incoming dependencies detected
 - Tier 2 module
 - 16 Protocol classes defining interfaces for different capabilities

3. **Major protocols defined**:
 - Graph Protocol: Entity graph operations with two capability tiers (analytics/STIX)
 - Storage Protocol: DuckDB + LMDB storage operations
 - Async Cleanable: Base protocol for async resource cleanup
 - Brain Protocol: LLM inference and synthesis
 - Transport Protocol: Anonymous transport (Tor, I2P, DHT)
 - Fetch Protocol: HTTP fetch coordination with backpressure
 - Pivot Protocol: Pivot planning and queue
 - Score Protocol: IOC scoring and source weighting
 - Intel Protocol: Threat intelligence (CT logs, OPSEC policy)
 - Metrics Protocol: Metrics collection
 - Prefetch Protocol: Prefetch and temporal prediction
 - Lane Protocol: Feed/pipeline lane execution
 - Enrichment Protocol: Enrichment services
 - Lifecycle Protocol: Sprint lifecycle management
 - Layers Protocol: Security layers (privacy, stealth)

4. **Architecture role**: This is a foundational module - it defines interfaces without implementations. It has no dependencies and no dependents (currently). It defines the "contract" that other modules implement.

5. **Complexity**: 
 - Average lines per file: 64 (relatively small)
 - Total 1027 lines across 16 files
 - High symbol density with 16 classes and 58 functions
 - No dependencies mean it sits at the bottom of the dependency hierarchy

6. **Key insight**: This module is a leaf in the dependency graph (no dependents detected), but it's foundational because it provides the interfaces that everything else implements. 

Without concrete usage patterns, I can only describe what the module represents conceptually and structurally.
</think>

This module defines Protocol interfaces that establish contracts between core subsystems without implementing them—each Protocol describes how a service behaves and what operations it exposes, allowing implementations to vary while consumers remain decoupled. The module covers a remarkably broad surface area: sixteen Protocol classes govern everything from DuckDB + LMDB persistence and anonymous transport routing (Tor, I2P, DHT) to LLM inference orchestration, pivot queue management, IOC scoring, threat intelligence lookups, and security layer enforcement. A particularly critical interface is `AsyncCleanable`, which standardizes graceful shutdown across all Sprint Scheduler resources by requiring `aclose()` to be idempotent, fail-safe, and bounded by timeout. The graph protocol distinguishes two capability tiers—TIER_A for analytics workloads against DuckPGQGraph and TIER_S for STIX/truth-write operations against IOCGraph—enabling heterogeneous graph backends. With zero outgoing dependencies and no detected dependents, this module sits at the foundation of the architecture as a pure interface layer; any change to these contracts would ripple through every implementation. At roughly 1,000 lines across 16 files, the module exhibits high symbol density with 16 classes and 58 functions, indicating a design that favors granular interface decomposition over monolithic abstractions.

## Structure

| Language | Files |
|---|---|
| Python | 16 |

### Largest Files

- `cleanup_protocol.py` (206 lines)
- `graph_protocol.py` (137 lines)
- `__init__.py` (90 lines)
- `storage_protocol.py` (57 lines)
- `pivot_protocol.py` (52 lines)
- `brain_protocol.py` (49 lines)
- `transport_protocol.py` (48 lines)
- `lane_protocol.py` (48 lines)
- `score_protocol.py` (47 lines)
- `intel_protocol.py` (44 lines)

<details><summary><strong>Show 6 more files</strong></summary>

- `fetch_protocol.py` (42 lines)
- `metrics_protocol.py` (42 lines)
- `prefetch_protocol.py` (42 lines)
- `enrichment_protocol.py` (41 lines)
- `lifecycle_protocol.py` (41 lines)
- `layers_protocol.py` (41 lines)

</details>


## Dependencies

No outgoing dependencies detected.

## Dependents

No incoming dependencies detected.

## Key Symbols

<p><strong>Key definitions:</strong></p>
<ul>
<li>
<p><code>StorageProtocol</code> (Class) in storage_protocol.py — referenced in 4 files</p>
<details><summary>Storage operations protocol for DuckDB + LMDB.</summary>
<div class="doc-comment">
<p>Storage operations protocol for DuckDB + LMDB.</p>
<p></p>
<p>Implementations:</p>
<p>- DuckDBStoreAdapter: wraps DuckDBShadowStore</p>
<p>- InMemoryStorageAdapter: for testing</p>
<p></p>
<p>Key methods:</p>
<p>- async_ingest_findings: canonical write path</p>
<p>- open_lmdb: zero-copy LMDB access</p>
<p>- query_sprint_results: SQL queries</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py, duckdb_adapter.py</li></ul>
</li>
<li>
<p><code>FetchProtocol</code> (Class) in fetch_protocol.py — referenced in 3 files</p>
<details><summary>HTTP fetch coordination protocol.</summary>
<div class="doc-comment">
<p>HTTP fetch coordination protocol.</p>
<p></p>
<p>Implementations:</p>
<p>- FetchCoordinatorAdapter: wraps FetchCoordinator</p>
<p></p>
<p>Key methods:</p>
<p>- fetch: execute HTTP fetch with backpressure</p>
<p>- get_semaphore: concurrency control</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py, fetch_adapter.py</li></ul>
</li>
<li>
<p><code>GraphProtocol</code> (Class) in graph_protocol.py — referenced in 3 files</p>
<details><summary>Unified entity graph operations protocol.</summary>
<div class="doc-comment">
<p>Unified entity graph operations protocol.</p>
<p></p>
<p>Implementations:</p>
<p>- DuckPGQGraphAdapter: DuckPGQGraph (DuckDB, analytics donor)</p>
<p>- IOCGraphAdapter: IOCGraph (Kuzu, STIX/truth-write)</p>
<p></p>
<p>Two capability tiers:</p>
<p>TIER_A — Analytics (DuckPGQGraph):</p>
<p>upsert_ioc, upsert_ioc_batch, upsert_relation,</p>
<p>find_connected, find_connected_batch,</p>
<p>get_top_nodes_by_degree, export_edge_list, stats, checkpoint</p>
<p></p>
<p>TIER_S — STIX/TruthWrite (IOCGraph):</p>
<p>buffer_ioc, flush_buffers (buffered writes),</p>
<p>record_observation, pivot, graph_stats, export_stix_bundle</p>
<p></p>
<p>Consumers check capability via hasattr() before calling tier-specific methods.</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py, graph_adapter.py</li></ul>
</li>
<li>
<p><code>AsyncCleanable</code> (Class) in cleanup_protocol.py — referenced in 2 files</p>
<details><summary>Base Protocol for all async-cleanable resources.</summary>
<div class="doc-comment">
<p>Base Protocol for all async-cleanable resources.</p>
<p></p>
<p>All SprintScheduler resources (DuckDB store, scheduler, coalescers,</p>
<p>inference engines, transport sessions) must implement this Protocol.</p>
<p></p>
<p>Rationale:</p>
<p>Having a single Protocol means:</p>
<p>- Static type checkers can verify all resources are aclose-able</p>
<p>- manage_cleanup() accepts heterogeneous resource lists</p>
<p>- No more inconsistent naming (aclose vs shutdown vs close)</p>
<p></p>
<p>Implementations:</p>
<p>- coordinators/memory_coordinator: UniversalMemoryCoordinator</p>
<p>- knowledge/duckdb_store: DuckDBShadowStore</p>
<p>- runtime/sprint_scheduler: SprintScheduler</p>
<p></p>
<p>Invariant:</p>
<p>aclose() must be:</p>
<p>- Idempotent: safe to call multiple times</p>
<p>- Fail-safe: never raises, catches all exceptions</p>
<p>- Bounded: must complete within timeout_s or force-cancel</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py</li></ul>
</li>
<li>
<p><code>BrainProtocol</code> (Class) in brain_protocol.py — referenced in 2 files</p>
<details><summary>LLM inference and synthesis protocol.</summary>
<div class="doc-comment">
<p>LLM inference and synthesis protocol.</p>
<p></p>
<p>Implementations:</p>
<p>- Hermes3EngineAdapter: wraps Hermes3Engine</p>
<p>- SynthesisRunnerAdapter: wraps SynthesisRunner</p>
<p></p>
<p>Key methods:</p>
<p>- generate: LLM inference</p>
<p>- synthesize: multi-finding synthesis</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py</li></ul>
</li>
</ul>

<details><summary><strong>Function</strong> (58)</summary>
<ul>
<li><code>manage_cleanup</code> (cleanup_protocol.py)</li>
<li><code>_aclose_one</code> (cleanup_protocol.py)
<details><summary>Call aclose() on a single resource, collecting all exceptions.</summary>
<div class="doc-comment">
<p>Call aclose() on a single resource, collecting all exceptions.</p>
<p></p>
<p>Returns list of exceptions encountered (empty = clean).</p>
<p>CancelledError is re-raised (does NOT appear in returned list).</p>
</div>
</details>
</li>
<li><code>aclose</code> (cleanup_protocol.py)
<details><summary>Graceful shutdown with bounded timeout.</summary>
<div class="doc-comment">
<p>Graceful shutdown with bounded timeout.</p>
<p></p>
<p>Args:</p>
<p>timeout_s: Maximum seconds to wait for cleanup.</p>
<p>Default: 10.0s.</p>
<p>CancelledError propagates after timeout on best-effort basis.</p>
<p></p>
<p>Post-condition:</p>
<p>Resource is fully cleaned up (WAL flushed, connections closed,</p>
<p>tasks cancelled) or cancellation was attempted.</p>
</div>
</details>
</li>
<li><code>upsert_relation</code> (graph_protocol.py)</li>
<li><code>record_observation</code> (graph_protocol.py)</li>
<li><code>upsert_ioc</code> (graph_protocol.py)</li>
<li><code>enqueue_pivot</code> (pivot_protocol.py)</li>
<li><code>compute_score</code> (score_protocol.py)</li>
<li><code>buffer_ioc</code> (graph_protocol.py)</li>
<li><code>pivot</code> (graph_protocol.py)</li>
<li><code>record_feedback</code> (pivot_protocol.py)</li>
<li><code>generate</code> (brain_protocol.py)</li>
<li><code>run_lane</code> (lane_protocol.py)</li>
<li><code>record</code> (metrics_protocol.py)</li>
<li><code>find_connected</code> (graph_protocol.py)</li>
<li><code>upsert_ioc_batch</code> (graph_protocol.py)</li>
<li><code>find_connected_batch</code> (graph_protocol.py)</li>
<li><code>async_ingest_findings</code> (storage_protocol.py)</li>
<li><code>synthesize</code> (brain_protocol.py)</li>
<li><code>record_lane_outcome</code> (lane_protocol.py)</li>
<li><code>query_ct_logs</code> (intel_protocol.py)</li>
<li><code>get_prefetch_candidates</code> (prefetch_protocol.py)</li>
<li><code>predict_temporal</code> (prefetch_protocol.py)</li>
<li><code>enrich_finding</code> (enrichment_protocol.py)</li>
<li><code>log_evidence</code> (enrichment_protocol.py)</li>
<li><code>__init__</code> (cleanup_protocol.py)</li>
<li><code>get_top_nodes_by_degree</code> (graph_protocol.py) — <span class="doc-comment-inline">Return top N nodes by degree. Returns [] on error.</span></li>
<li><code>export_edge_list</code> (graph_protocol.py) — <span class="doc-comment-inline">Export all edges as (src, dst, rel_type, weight) tuples. Returns [] on error.</span></li>
<li><code>stats</code> (graph_protocol.py) — <span class="doc-comment-inline">Return {nodes, edges, ...}. Returns {} on error.</span></li>
<li><code>checkpoint</code> (graph_protocol.py) — <span class="doc-comment-inline">Flush WAL to disk. Fail-safe (no-op on error).</span></li>
<li><code>flush_buffers</code> (graph_protocol.py) — <span class="doc-comment-inline">Flush buffered IOCs. Returns {}. No-op on non-buffered backends.</span></li>
<li><code>graph_stats</code> (graph_protocol.py) — <span class="doc-comment-inline">STIX graph stats. Returns {} on non-STIX backends.</span></li>
<li><code>export_stix_bundle</code> (graph_protocol.py) — <span class="doc-comment-inline">Export as STIX 2.1 bundle. Returns [] on non-STIX backends.</span></li>
<li><code>async_flush_arrow</code> (storage_protocol.py) — <span class="doc-comment-inline">Flush any pending Arrow batches.</span></li>
<li><code>open_lmdb</code> (storage_protocol.py) — <span class="doc-comment-inline">Open LMDB environment for zero-copy metadata access.</span></li>
<li><code>query_sprint_results</code> (storage_protocol.py) — <span class="doc-comment-inline">Execute SQL query against DuckDB store.</span></li>
<li><code>async_initialize</code> (storage_protocol.py) — <span class="doc-comment-inline">Initialize storage (open DuckDB, LMDB).</span></li>
<li><code>drain_pivot_queue</code> (pivot_protocol.py) — <span class="doc-comment-inline">Drain pivot queue, return number processed.</span></li>
<li><code>score_ioc</code> (brain_protocol.py) — <span class="doc-comment-inline">Return IOC novelty score (0.0-1.0).</span></li>
<li><code>fetch_via_tor</code> (transport_protocol.py) — <span class="doc-comment-inline">Fetch URL via Tor transport.</span></li>
<li><code>fetch_via_i2p</code> (transport_protocol.py) — <span class="doc-comment-inline">Fetch URL via I2P transport.</span></li>
<li><code>dht_get</code> (transport_protocol.py) — <span class="doc-comment-inline">DHT key lookup.</span></li>
<li><code>dht_put</code> (transport_protocol.py) — <span class="doc-comment-inline">DHT key store.</span></li>
<li><code>get_lane_budget</code> (lane_protocol.py) — <span class="doc-comment-inline">Get remaining time budget for lane.</span></li>
<li><code>get_source_weight</code> (score_protocol.py) — <span class="doc-comment-inline">Get source reliability weight (0.0-1.0).</span></li>
<li><code>get_november_bonus</code> (score_protocol.py) — <span class="doc-comment-inline">Get novelty bonus for IOC type.</span></li>
<li><code>check_policy</code> (intel_protocol.py) — <span class="doc-comment-inline">Check if operation is allowed by OPSEC policy.</span></li>
<li><code>get_analyst_brief</code> (intel_protocol.py) — <span class="doc-comment-inline">Get analyst brief for query.</span></li>
<li><code>fetch</code> (fetch_protocol.py) — <span class="doc-comment-inline">Execute fetch work item, return (url, result) or None.</span></li>
<li><code>get_semaphore</code> (fetch_protocol.py) — <span class="doc-comment-inline">Return concurrency semaphore.</span></li>
<li><code>get_backpressure</code> (fetch_protocol.py) — <span class="doc-comment-inline">Return current backpressure ratio (0.0-1.0).</span></li>
<li><code>get_summary</code> (metrics_protocol.py) — <span class="doc-comment-inline">Get metrics summary.</span></li>
<li><code>transition</code> (lifecycle_protocol.py) — <span class="doc-comment-inline">Transition lifecycle to next state.</span></li>
<li><code>get_state</code> (lifecycle_protocol.py) — <span class="doc-comment-inline">Get current lifecycle state.</span></li>
<li><code>winddown</code> (lifecycle_protocol.py) — <span class="doc-comment-inline">Execute winddown procedures.</span></li>
<li><code>apply_privacy</code> (layers_protocol.py) — <span class="doc-comment-inline">Apply privacy policy to data.</span></li>
<li><code>check_stealth</code> (layers_protocol.py) — <span class="doc-comment-inline">Check if URL passes stealth mode.</span></li>
<li><code>enforce_layers</code> (layers_protocol.py) — <span class="doc-comment-inline">Enforce all active security layers.</span></li>
</ul>
</details>

<details><summary><strong>Class</strong> (16)</summary>
<ul>
<li><code>GraphProtocol</code> (graph_protocol.py)
<details><summary>Unified entity graph operations protocol.</summary>
<div class="doc-comment">
<p>Unified entity graph operations protocol.</p>
<p></p>
<p>Implementations:</p>
<p>- DuckPGQGraphAdapter: DuckPGQGraph (DuckDB, analytics donor)</p>
<p>- IOCGraphAdapter: IOCGraph (Kuzu, STIX/truth-write)</p>
<p></p>
<p>Two capability tiers:</p>
<p>TIER_A — Analytics (DuckPGQGraph):</p>
<p>upsert_ioc, upsert_ioc_batch, upsert_relation,</p>
<p>find_connected, find_connected_batch,</p>
<p>get_top_nodes_by_degree, export_edge_list, stats, checkpoint</p>
<p></p>
<p>TIER_S — STIX/TruthWrite (IOCGraph):</p>
<p>buffer_ioc, flush_buffers (buffered writes),</p>
<p>record_observation, pivot, graph_stats, export_stix_bundle</p>
<p></p>
<p>Consumers check capability via hasattr() before calling tier-specific methods.</p>
</div>
</details>
</li>
<li><code>AsyncCleanable</code> (cleanup_protocol.py)
<details><summary>Base Protocol for all async-cleanable resources.</summary>
<div class="doc-comment">
<p>Base Protocol for all async-cleanable resources.</p>
<p></p>
<p>All SprintScheduler resources (DuckDB store, scheduler, coalescers,</p>
<p>inference engines, transport sessions) must implement this Protocol.</p>
<p></p>
<p>Rationale:</p>
<p>Having a single Protocol means:</p>
<p>- Static type checkers can verify all resources are aclose-able</p>
<p>- manage_cleanup() accepts heterogeneous resource lists</p>
<p>- No more inconsistent naming (aclose vs shutdown vs close)</p>
<p></p>
<p>Implementations:</p>
<p>- coordinators/memory_coordinator: UniversalMemoryCoordinator</p>
<p>- knowledge/duckdb_store: DuckDBShadowStore</p>
<p>- runtime/sprint_scheduler: SprintScheduler</p>
<p></p>
<p>Invariant:</p>
<p>aclose() must be:</p>
<p>- Idempotent: safe to call multiple times</p>
<p>- Fail-safe: never raises, catches all exceptions</p>
<p>- Bounded: must complete within timeout_s or force-cancel</p>
</div>
</details>
</li>
<li><code>StorageProtocol</code> (storage_protocol.py)
<details><summary>Storage operations protocol for DuckDB + LMDB.</summary>
<div class="doc-comment">
<p>Storage operations protocol for DuckDB + LMDB.</p>
<p></p>
<p>Implementations:</p>
<p>- DuckDBStoreAdapter: wraps DuckDBShadowStore</p>
<p>- InMemoryStorageAdapter: for testing</p>
<p></p>
<p>Key methods:</p>
<p>- async_ingest_findings: canonical write path</p>
<p>- open_lmdb: zero-copy LMDB access</p>
<p>- query_sprint_results: SQL queries</p>
</div>
</details>
</li>
<li><code>PivotProtocol</code> (pivot_protocol.py)
<details><summary>Pivot planning and queue protocol.</summary>
<div class="doc-comment">
<p>Pivot planning and queue protocol.</p>
<p></p>
<p>Implementations:</p>
<p>- PivotQueueAdapter: wraps pivot queue logic</p>
<p></p>
<p>Key methods:</p>
<p>- enqueue_pivot: add IOC pivot to queue</p>
<p>- drain_pivot_queue: process pending pivots</p>
</div>
</details>
</li>
<li><code>BrainProtocol</code> (brain_protocol.py)
<details><summary>LLM inference and synthesis protocol.</summary>
<div class="doc-comment">
<p>LLM inference and synthesis protocol.</p>
<p></p>
<p>Implementations:</p>
<p>- Hermes3EngineAdapter: wraps Hermes3Engine</p>
<p>- SynthesisRunnerAdapter: wraps SynthesisRunner</p>
<p></p>
<p>Key methods:</p>
<p>- generate: LLM inference</p>
<p>- synthesize: multi-finding synthesis</p>
</div>
</details>
</li>
<li><code>TransportProtocol</code> (transport_protocol.py)
<details><summary>Anonymous transport protocol.</summary>
<div class="doc-comment">
<p>Anonymous transport protocol.</p>
<p></p>
<p>Implementations:</p>
<p>- TorTransportAdapter</p>
<p>- I2PTransportAdapter</p>
<p>- NymTransportAdapter</p>
<p>- DHTNodeAdapter</p>
<p></p>
<p>Key methods:</p>
<p>- fetch_via_tor: Tor-anonymized fetch</p>
<p>- fetch_via_i2p: I2P-anonymized fetch</p>
</div>
</details>
</li>
<li><code>LaneProtocol</code> (lane_protocol.py)
<details><summary>Feed/pipeline lane execution protocol.</summary>
<div class="doc-comment">
<p>Feed/pipeline lane execution protocol.</p>
<p></p>
<p>Implementations:</p>
<p>- LaneExecutorAdapter: runs acquisition lanes</p>
<p></p>
<p>Key methods:</p>
<p>- run_lane: execute single acquisition lane</p>
<p>- get_lane_budget: remaining budget for lane</p>
</div>
</details>
</li>
<li><code>ScoreProtocol</code> (score_protocol.py)
<details><summary>IOC scoring and source weighting protocol.</summary>
<div class="doc-comment">
<p>IOC scoring and source weighting protocol.</p>
<p></p>
<p>Implementations:</p>
<p>- SourceWeightAdapter: manages source weights</p>
<p></p>
<p>Key methods:</p>
<p>- compute_score: IOC novelty/quality score</p>
<p>- get_source_weight: source reliability weight</p>
</div>
</details>
</li>
<li><code>IntelProtocol</code> (intel_protocol.py)
<details><summary>Threat intelligence protocol.</summary>
<div class="doc-comment">
<p>Threat intelligence protocol.</p>
<p></p>
<p>Implementations:</p>
<p>- CTLogClientAdapter: CT log lookups</p>
<p>- PolicyManagerAdapter: OPSEC policy enforcement</p>
<p></p>
<p>Key methods:</p>
<p>- query_ct_logs: Certificate Transparency lookups</p>
<p>- check_policy: OPSEC policy validation</p>
</div>
</details>
</li>
<li><code>MetricsProtocol</code> (metrics_protocol.py)
<details><summary>Metrics collection protocol.</summary>
<div class="doc-comment">
<p>Metrics collection protocol.</p>
<p></p>
<p>Implementations:</p>
<p>- MetricsRegistryAdapter: wraps MetricsRegistry</p>
<p></p>
<p>Key methods:</p>
<p>- record: record a metric value</p>
<p>- get_summary: get metrics summary</p>
</div>
</details>
</li>
<li><code>PrefetchProtocol</code> (prefetch_protocol.py)
<details><summary>Prefetch and prediction protocol.</summary>
<div class="doc-comment">
<p>Prefetch and prediction protocol.</p>
<p></p>
<p>Implementations:</p>
<p>- PrefetchOracleAdapter: speculative prefetch</p>
<p>- TemporalPredictorAdapter: time-series prediction</p>
<p></p>
<p>Key methods:</p>
<p>- get_prefetch_candidates: speculative URLs to prefetch</p>
<p>- predict_temporal: predict query timing</p>
</div>
</details>
</li>
<li><code>FetchProtocol</code> (fetch_protocol.py)
<details><summary>HTTP fetch coordination protocol.</summary>
<div class="doc-comment">
<p>HTTP fetch coordination protocol.</p>
<p></p>
<p>Implementations:</p>
<p>- FetchCoordinatorAdapter: wraps FetchCoordinator</p>
<p></p>
<p>Key methods:</p>
<p>- fetch: execute HTTP fetch with backpressure</p>
<p>- get_semaphore: concurrency control</p>
</div>
</details>
</li>
<li><code>EnrichmentProtocol</code> (enrichment_protocol.py)
<details><summary>Enrichment services protocol.</summary>
<div class="doc-comment">
<p>Enrichment services protocol.</p>
<p></p>
<p>Implementations:</p>
<p>- EnrichmentServicesAdapter: wraps enrichment services</p>
<p></p>
<p>Key methods:</p>
<p>- enrich_finding: add external context to finding</p>
<p>- log_evidence: record evidence for audit</p>
</div>
</details>
</li>
<li><code>LifecycleProtocol</code> (lifecycle_protocol.py)
<details><summary>Sprint lifecycle management protocol.</summary>
<div class="doc-comment">
<p>Sprint lifecycle management protocol.</p>
<p></p>
<p>Implementations:</p>
<p>- LifecycleAdapter: wraps LifecycleManager</p>
<p></p>
<p>Key methods:</p>
<p>- transition: advance lifecycle state</p>
<p>- get_state: current lifecycle state</p>
</div>
</details>
</li>
<li><code>LayersProtocol</code> (layers_protocol.py)
<details><summary>Security layers protocol.</summary>
<div class="doc-comment">
<p>Security layers protocol.</p>
<p></p>
<p>Implementations:</p>
<p>- LayerManagerAdapter: wraps LayerManager</p>
<p></p>
<p>Key methods:</p>
<p>- apply_privacy: privacy policy enforcement</p>
<p>- check_stealth: stealth mode validation</p>
</div>
</details>
</li>
<li><code>_ManagedResource</code> (cleanup_protocol.py) — <span class="doc-comment-inline">Wrapper that holds a resource and its cleanup timeout.</span></li>
</ul>
</details>

<details><summary><strong>Method</strong> (56)</summary>
<ul>
<li><code>aclose</code> (cleanup_protocol.py)
<details><summary>Graceful shutdown with bounded timeout.</summary>
<div class="doc-comment">
<p>Graceful shutdown with bounded timeout.</p>
<p></p>
<p>Args:</p>
<p>timeout_s: Maximum seconds to wait for cleanup.</p>
<p>Default: 10.0s.</p>
<p>CancelledError propagates after timeout on best-effort basis.</p>
<p></p>
<p>Post-condition:</p>
<p>Resource is fully cleaned up (WAL flushed, connections closed,</p>
<p>tasks cancelled) or cancellation was attempted.</p>
</div>
</details>
</li>
<li><code>upsert_relation</code> (graph_protocol.py)</li>
<li><code>record_observation</code> (graph_protocol.py)</li>
<li><code>upsert_ioc</code> (graph_protocol.py)</li>
<li><code>enqueue_pivot</code> (pivot_protocol.py)</li>
<li><code>compute_score</code> (score_protocol.py)</li>
<li><code>buffer_ioc</code> (graph_protocol.py)</li>
<li><code>pivot</code> (graph_protocol.py)</li>
<li><code>record_feedback</code> (pivot_protocol.py)</li>
<li><code>generate</code> (brain_protocol.py)</li>
<li><code>run_lane</code> (lane_protocol.py)</li>
<li><code>record</code> (metrics_protocol.py)</li>
<li><code>find_connected</code> (graph_protocol.py)</li>
<li><code>upsert_ioc_batch</code> (graph_protocol.py)</li>
<li><code>find_connected_batch</code> (graph_protocol.py)</li>
<li><code>async_ingest_findings</code> (storage_protocol.py)</li>
<li><code>synthesize</code> (brain_protocol.py)</li>
<li><code>record_lane_outcome</code> (lane_protocol.py)</li>
<li><code>query_ct_logs</code> (intel_protocol.py)</li>
<li><code>get_prefetch_candidates</code> (prefetch_protocol.py)</li>
<li><code>predict_temporal</code> (prefetch_protocol.py)</li>
<li><code>enrich_finding</code> (enrichment_protocol.py)</li>
<li><code>log_evidence</code> (enrichment_protocol.py)</li>
<li><code>__init__</code> (cleanup_protocol.py)</li>
<li><code>get_top_nodes_by_degree</code> (graph_protocol.py) — <span class="doc-comment-inline">Return top N nodes by degree. Returns [] on error.</span></li>
<li><code>export_edge_list</code> (graph_protocol.py) — <span class="doc-comment-inline">Export all edges as (src, dst, rel_type, weight) tuples. Returns [] on error.</span></li>
<li><code>stats</code> (graph_protocol.py) — <span class="doc-comment-inline">Return {nodes, edges, ...}. Returns {} on error.</span></li>
<li><code>checkpoint</code> (graph_protocol.py) — <span class="doc-comment-inline">Flush WAL to disk. Fail-safe (no-op on error).</span></li>
<li><code>flush_buffers</code> (graph_protocol.py) — <span class="doc-comment-inline">Flush buffered IOCs. Returns {}. No-op on non-buffered backends.</span></li>
<li><code>graph_stats</code> (graph_protocol.py) — <span class="doc-comment-inline">STIX graph stats. Returns {} on non-STIX backends.</span></li>
<li><code>export_stix_bundle</code> (graph_protocol.py) — <span class="doc-comment-inline">Export as STIX 2.1 bundle. Returns [] on non-STIX backends.</span></li>
<li><code>async_flush_arrow</code> (storage_protocol.py) — <span class="doc-comment-inline">Flush any pending Arrow batches.</span></li>
<li><code>open_lmdb</code> (storage_protocol.py) — <span class="doc-comment-inline">Open LMDB environment for zero-copy metadata access.</span></li>
<li><code>query_sprint_results</code> (storage_protocol.py) — <span class="doc-comment-inline">Execute SQL query against DuckDB store.</span></li>
<li><code>async_initialize</code> (storage_protocol.py) — <span class="doc-comment-inline">Initialize storage (open DuckDB, LMDB).</span></li>
<li><code>drain_pivot_queue</code> (pivot_protocol.py) — <span class="doc-comment-inline">Drain pivot queue, return number processed.</span></li>
<li><code>score_ioc</code> (brain_protocol.py) — <span class="doc-comment-inline">Return IOC novelty score (0.0-1.0).</span></li>
<li><code>fetch_via_tor</code> (transport_protocol.py) — <span class="doc-comment-inline">Fetch URL via Tor transport.</span></li>
<li><code>fetch_via_i2p</code> (transport_protocol.py) — <span class="doc-comment-inline">Fetch URL via I2P transport.</span></li>
<li><code>dht_get</code> (transport_protocol.py) — <span class="doc-comment-inline">DHT key lookup.</span></li>
<li><code>dht_put</code> (transport_protocol.py) — <span class="doc-comment-inline">DHT key store.</span></li>
<li><code>get_lane_budget</code> (lane_protocol.py) — <span class="doc-comment-inline">Get remaining time budget for lane.</span></li>
<li><code>get_source_weight</code> (score_protocol.py) — <span class="doc-comment-inline">Get source reliability weight (0.0-1.0).</span></li>
<li><code>get_november_bonus</code> (score_protocol.py) — <span class="doc-comment-inline">Get novelty bonus for IOC type.</span></li>
<li><code>check_policy</code> (intel_protocol.py) — <span class="doc-comment-inline">Check if operation is allowed by OPSEC policy.</span></li>
<li><code>get_analyst_brief</code> (intel_protocol.py) — <span class="doc-comment-inline">Get analyst brief for query.</span></li>
<li><code>fetch</code> (fetch_protocol.py) — <span class="doc-comment-inline">Execute fetch work item, return (url, result) or None.</span></li>
<li><code>get_semaphore</code> (fetch_protocol.py) — <span class="doc-comment-inline">Return concurrency semaphore.</span></li>
<li><code>get_backpressure</code> (fetch_protocol.py) — <span class="doc-comment-inline">Return current backpressure ratio (0.0-1.0).</span></li>
<li><code>get_summary</code> (metrics_protocol.py) — <span class="doc-comment-inline">Get metrics summary.</span></li>
<li><code>transition</code> (lifecycle_protocol.py) — <span class="doc-comment-inline">Transition lifecycle to next state.</span></li>
<li><code>get_state</code> (lifecycle_protocol.py) — <span class="doc-comment-inline">Get current lifecycle state.</span></li>
<li><code>winddown</code> (lifecycle_protocol.py) — <span class="doc-comment-inline">Execute winddown procedures.</span></li>
<li><code>apply_privacy</code> (layers_protocol.py) — <span class="doc-comment-inline">Apply privacy policy to data.</span></li>
<li><code>check_stealth</code> (layers_protocol.py) — <span class="doc-comment-inline">Check if URL passes stealth mode.</span></li>
<li><code>enforce_layers</code> (layers_protocol.py) — <span class="doc-comment-inline">Enforce all active security layers.</span></li>
</ul>
</details>

<details><summary><strong>Constant</strong> (1)</summary>
<ul>
<li><code>DEFAULT_ACLOSE_TIMEOUT_S</code> (cleanup_protocol.py)</li>
</ul>
</details>



## Metrics

| Metric | Value |
|---|---|
| Files | 16 |
| Total lines | 1027 |
| Avg lines/file | 64 |
| Languages | Python |
| Outgoing deps | 0 |
| Incoming deps | 0 |
| Tier | 2 |

