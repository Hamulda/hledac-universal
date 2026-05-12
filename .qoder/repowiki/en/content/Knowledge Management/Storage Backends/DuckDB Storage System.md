# DuckDB Storage System

<cite>
**Referenced Files in This Document**
- [duckdb_store.py](file://knowledge/duckdb_store.py)
- [lmdb_boot_guard.py](file://knowledge/lmdb_boot_guard.py)
</cite>

## Table of Contents
1. [Introduction](#introduction)
2. [Project Structure](#project-structure)
3. [Core Components](#core-components)
4. [Architecture Overview](#architecture-overview)
5. [Detailed Component Analysis](#detailed-component-analysis)
6. [Dependency Analysis](#dependency-analysis)
7. [Performance Considerations](#performance-considerations)
8. [Troubleshooting Guide](#troubleshooting-guide)
9. [Conclusion](#conclusion)

## Introduction
This document describes the DuckDB storage system implementation used as the canonical store for sprint-level facts and analytics. The system centers on the DuckDBShadowStore class, which provides:
- Async-safe operations using a single-threaded worker pool
- RAMDISK-first operational mode with graceful degradation
- Three-tier facts hierarchy (sprint facts, shadow findings, graph components)
- Quality gating and deduplication with persistent LMDB backing
- Integration with external graph systems and semantic stores

The implementation emphasizes thread-affinity, memory-conscious runtime settings, and robust recovery via WAL-first ingestion.

## Project Structure
The DuckDB storage system lives primarily in the knowledge module:
- DuckDBShadowStore: main storage class with async APIs, quality gating, and deduplication
- LMDB boot guard: safe LMDB initialization with stale-lock detection

```mermaid
graph TB
subgraph "Knowledge Module"
DDB["DuckDBShadowStore<br/>Async-safe DuckDB store"]
LBG["LMDB Boot Guard<br/>Safe LMDB open"]
end
subgraph "External Integrations"
LMDB["LMDB WAL Store<br/>shadow_wal.lmdb"]
DDBFile["DuckDB File Mode<br/>shadow_analytics.duckdb"]
DDBMem[":memory: Mode<br/>Persistent connection"]
Graph["Graph Backends<br/>IOCGraph / DuckPGQGraph"]
SemStore["Semantic Store<br/>FastEmbed + LanceDB"]
end
DDB --> LMDB
DDB --> DDBFile
DDB --> DDBMem
DDB --> Graph
DDB --> SemStore
LBG -.-> LMDB
```

**Diagram sources**
- [duckdb_store.py](file://knowledge/duckdb_store.py)
- [lmdb_boot_guard.py](file://knowledge/lmdb_boot_guard.py)

**Section sources**
- [duckdb_store.py](file://knowledge/duckdb_store.py)

## Core Components
- DuckDBShadowStore: Async-safe storage with thread-affine connections, batch operations, health checks, and lifecycle management
- Quality gating and deduplication: entropy checks, URL-first fingerprints, hot-cache + persistent LMDB dedup
- WAL-first activation: LMDB WAL followed by DuckDB, with recovery markers and dead-lettering
- Graph integration: capability-checked injection for truth-write, analytics, and STIX export
- Semantic buffering: background embedding and indexing for findings

Key async API surface:
- async_initialize(replay_pending_limit, replay_timeout_s)
- async_record_shadow_finding / async_record_shadow_findings_batch
- async_record_shadow_run
- async_query_recent_findings
- async_healthcheck
- aclose()

**Section sources**
- [duckdb_store.py](file://knowledge/duckdb_store.py)

## Architecture Overview
The system separates concerns across three layers:
- Facts layer (DuckDB): canonical sprint metrics and findings
- Activation layer (WAL-first): durable ingestion with recovery
- Integration layer (Graph/Semantic): optional enrichment and truth-write

```mermaid
sequenceDiagram
participant Client as "Client"
participant Store as "DuckDBShadowStore"
participant Worker as "ThreadPoolExecutor(1)"
participant WAL as "LMDB WAL"
participant DB as "DuckDB"
Client->>Store : async_record_shadow_finding(id, query, source, conf)
Store->>Worker : run_in_executor(_activation_record_finding)
Worker->>WAL : put("finding : {id}", payload)
WAL-->>Worker : ok
Worker->>DB : INSERT shadow_findings
DB-->>Worker : ok
Worker-->>Store : {lmdb_success : True, duckdb_success : True}
Store-->>Client : True
```

**Diagram sources**
- [duckdb_store.py](file://knowledge/duckdb_store.py)

## Detailed Component Analysis

### DuckDBShadowStore Class
Async-safe design with thread-affine connections:
- Single-threaded worker pool (ThreadPoolExecutor with 1 worker)
- Connections created inside the worker thread (thread-affine)
- All public async methods use run_in_executor to avoid event-loop blocking
- Two operational modes:
  - File mode: persistent file DB + RAMDISK temp directory
  - Memory mode: :memory: with a persistent connection

UMA-aware runtime settings:
- Resolved at connection init based on uma_state and swap detection
- Memory limit, threads, and safe_mode adjusted conservatively for M1 8GB UMA

Health and lifecycle:
- async_healthcheck performs a zero-cost query
- aclose() is idempotent and resets boot barriers
- async_initialize supports bounded startup replay

```mermaid
classDiagram
class DuckDBShadowStore {
-bool _initialized
-bool _closed
-Path _db_path
-Path _temp_dir
-dict _duckdb_settings
-ThreadPoolExecutor _executor
-Any _persistent_conn
-Any _file_conn
-dict _dedup_hot_cache
-Any _dedup_lmdb
+async async_initialize()
+async async_record_shadow_finding()
+async async_record_shadow_findings_batch()
+async async_record_shadow_run()
+async async_query_recent_findings()
+async async_healthcheck()
+async aclose()
+void set_uma_state()
+void inject_graph()
+void inject_stix_graph()
+void inject_truth_write_graph()
+void inject_semantic_store()
}
```

**Diagram sources**
- [duckdb_store.py](file://knowledge/duckdb_store.py)

**Section sources**
- [duckdb_store.py](file://knowledge/duckdb_store.py)

### Quality Gating and Deduplication
Quality assessment pipeline:
- URL-first fingerprinting when available; otherwise text-based BLAKE2b
- Hot-cache lookup (bounded) then persistent LMDB authority
- Short-string bypass with semantic dedup cache when available
- Entropy threshold filtering for low-information content
- Failure-isolation: quality gate failures are fail-open

Deduplication mechanisms:
- Hot cache: bounded in-memory cache keyed by fingerprint
- Persistent LMDB: namespace "dedup:{fingerprint}" → finding_id
- Quality rejection ledger: bounded record of rejections for diagnosis

```mermaid
flowchart TD
Start(["Incoming Finding"]) --> HasURL{"Has URL in provenance?"}
HasURL --> |Yes| URLFP["Compute URL fingerprint"]
HasURL --> |No| TextSel["Select text (payload or query)"]
TextSel --> Normalize["Normalize text"]
Normalize --> Entropy["Compute entropy"]
URLFP --> HotCache["Hot cache lookup"]
Entropy --> EntropyCheck{"Entropy >= threshold?"}
EntropyCheck --> |No| RejectLow["Reject: low entropy"]
EntropyCheck --> |Yes| SemCheck{"Semantic cache enabled?"}
SemCheck --> |Yes| SemDup["Semantic duplicate check"]
SemDup --> |Duplicate| RejectSem["Reject: semantic duplicate"]
SemCheck --> |No| Persist["Persist to LMDB + hot cache"]
Persist --> Accept["Accept"]
RejectLow --> RecordLedger["Record to quality ledger"]
RejectSem --> RecordLedger
HotCache --> |Hit| RejectDup["Reject: duplicate"]
HotCache --> |Miss| LMDBLookup["LMDB lookup"]
LMDBLookup --> |Hit| PopulateCache["Populate hot cache"] --> RejectDup
LMDBLookup --> |Miss| Accept
```

**Diagram sources**
- [duckdb_store.py](file://knowledge/duckdb_store.py)

**Section sources**
- [duckdb_store.py](file://knowledge/duckdb_store.py)

### WAL-First Activation and Recovery
Activation workflow:
- LMDB WAL first: writing finding payload to shadow_wal.lmdb
- DuckDB second: inserting into shadow_findings
- Partial failure: write pending-sync marker for later replay
- Dead-lettering: after max retries, move marker to dead-letter namespace

Startup replay:
- Bounded replay of pending markers during async_initialize
- Boot barrier prevents writes until replay completes or times out

```mermaid
sequenceDiagram
participant Client as "Client"
participant Store as "DuckDBShadowStore"
participant Worker as "Worker Thread"
participant WAL as "LMDB WAL"
participant DB as "DuckDB"
Client->>Store : async_record_activation(...)
Store->>Worker : run_in_executor(_activation_record_finding)
Worker->>WAL : put("finding : {id}", payload)
WAL-->>Worker : ok
Worker->>DB : INSERT shadow_findings
DB-->>Worker : ok
alt DuckDB failed
Worker->>WAL : put("pending_duckdb_sync : {id}", marker)
Worker-->>Store : {duckdb_success : False}
else Success
Worker-->>Store : {success}
end
Store-->>Client : result
```

**Diagram sources**
- [duckdb_store.py](file://knowledge/duckdb_store.py)

**Section sources**
- [duckdb_store.py](file://knowledge/duckdb_store.py)

### Graph Integration
The store supports multiple graph backends with capability checks:
- Truth-write graph: requires buffer_ioc and flush_buffers (IOCGraph)
- Analytics graph: donor backend (DuckPGQGraph) for read-only operations
- STIX graph: export_stix_bundle capability (IOCGraph)

```mermaid
classDiagram
class DuckDBShadowStore {
-Any _ioc_graph
-Any _truth_write_graph
-Any _stix_graph
-Any _semantic_store
+inject_graph(graph)
+inject_truth_write_graph(graph)
+inject_stix_graph(graph)
+inject_semantic_store(store)
+graph_supports_buffered_writes() bool
+truth_write_graph_supports_buffered_writes() bool
}
class IOCGraph {
+buffer_ioc()
+flush_buffers()
+export_stix_bundle()
}
class DuckPGQGraph {
+stats()
+get_top_nodes_by_degree()
}
DuckDBShadowStore --> IOCGraph : "truth-write"
DuckDBShadowStore --> DuckPGQGraph : "analytics donor"
```

**Diagram sources**
- [duckdb_store.py](file://knowledge/duckdb_store.py)

**Section sources**
- [duckdb_store.py](file://knowledge/duckdb_store.py)

### Three-Tier Facts Hierarchy
Tier 1 (DuckDB, durable):
- sprint_delta: per-sprint metrics (duration, findings, dedup hits, ioc nodes, synthesis metrics)
- sprint_scorecard: aggregated scores (findings_per_minute, ioc_density, semantic_novelty)
- source_hit_log: per-sprint source attribution (hit rates)

Tier 2 (DuckDB, durable):
- shadow_findings: finding-level records with provenance and payload
- shadow_runs: run-level metadata

Tier 3 (Injected):
- IOCGraph: truth graph for IOC storage
- SemanticStore: ANN semantic search

```mermaid
erDiagram
SHADOW_FINDINGS {
varchar id PK
varchar query
varchar source_type
double confidence
double ts
text provenance_json
}
SHADOW_RUNS {
varchar run_id PK
timestamp started_at
timestamp ended_at
integer total_fds
integer rss_mb
}
SPRINT_DELTA {
varchar sprint_id PK
double ts
text query
real duration_s
integer new_findings
integer dedup_hits
integer ioc_nodes
integer ioc_new_this_sprint
real uma_peak_gib
bool synthesis_success
real findings_per_minute
text top_source_type
real synthesis_confidence
}
SOURCE_HIT_LOG {
varchar sprint_id
double ts
varchar source_type
integer findings_count
integer ioc_count
real hit_rate
}
SHADOW_FINDINGS ||--o{ SOURCE_HIT_LOG : "attributed by"
SHADOW_RUNS ||--o{ SPRINT_DELTA : "coordinated with"
```

**Diagram sources**
- [duckdb_store.py](file://knowledge/duckdb_store.py)

**Section sources**
- [duckdb_store.py](file://knowledge/duckdb_store.py)

## Dependency Analysis
- DuckDBShadowStore depends on DuckDB (imported lazily) and LMDB (via LMDBKVStore)
- Uses ThreadPoolExecutor for thread-affine operations
- Integrates with external systems via capability checks (graphs, semantic store)
- Boot guard ensures safe LMDB initialization with stale-lock detection

```mermaid
graph LR
Store["DuckDBShadowStore"] --> DDB["DuckDB"]
Store --> LMDB["LMDBKVStore"]
Store --> Exec["ThreadPoolExecutor(1)"]
Store --> Graphs["Graph Backends"]
Store --> Sem["Semantic Store"]
BootGuard["LMDB Boot Guard"] --> LMDB
```

**Diagram sources**
- [duckdb_store.py](file://knowledge/duckdb_store.py)
- [lmdb_boot_guard.py](file://knowledge/lmdb_boot_guard.py)

**Section sources**
- [duckdb_store.py](file://knowledge/duckdb_store.py)
- [lmdb_boot_guard.py](file://knowledge/lmdb_boot_guard.py)

## Performance Considerations
- RAMDISK-first mode: file DB with temp directory on RAMDISK for improved I/O throughput
- Memory-conscious runtime: conservative memory_limit and threads for M1 8GB UMA
- Batch operations: chunked inserts with explicit transactions for throughput
- Streaming queries: async_query_arrow_batches for large result sets
- Background tasks: graph ingest and semantic buffering run fire-and-forget to avoid blocking

## Troubleshooting Guide
Common operational checks:
- Health: async_healthcheck returns True when queries succeed
- Pending markers: pending_marker_count indicates outstanding recovery work
- Dead-letter markers: deadletter_marker_count tracks failed recovery attempts
- Invariants: invariant_validate verifies memory limits and temp directory placement

Recovery procedures:
- Startup replay: async_initialize supports bounded replay of pending markers
- Manual replay: async_replay_all_pending_duckdb_sync processes markers in chunks
- Shutdown: aclose() gracefully closes connections, graphs, and stores

Operational safeguards:
- Boot guard: open_lmdb_with_guard prevents unsafe stale-lock scenarios
- UMA-aware settings: set_uma_state adjusts runtime parameters dynamically

**Section sources**
- [duckdb_store.py](file://knowledge/duckdb_store.py)
- [lmdb_boot_guard.py](file://knowledge/lmdb_boot_guard.py)

## Conclusion
The DuckDB storage system provides a robust, async-safe foundation for sprint analytics with:
- Thread-affine connections and a single-threaded worker pool
- RAMDISK-first operational mode with graceful degradation
- Comprehensive quality gating and persistent deduplication
- Integration hooks for graph and semantic systems
- Operational resilience via WAL-first ingestion and recovery

This design is optimized for constrained environments (e.g., M1 8GB UMA) while maintaining durability and performance for sustained research operations.