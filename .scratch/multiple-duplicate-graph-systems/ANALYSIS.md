# Multiple Duplicate Graph Systems — Complete Analysis

## Executive Summary

The project has **4 graph systems** that are loosely coordinated at best, creating data fragmentation, lock contention, and architectural confusion. This is a P1 stability and correctness issue.

---

## The 4 Graph Systems

### System 1: DuckPGQGraph (`graph/quantum_pathfinder.py`, 2066 lines)

**Role**: Analytics donor — quantum-inspired path finding, PageRank, entity degree analysis.

**Storage**: DuckDB file (`graph_*.duckdb` per graph instance).

**Locking**: `GraphLockManager` singleton per `db_path` using `fcntl.flock`.

**Key capabilities**:
- `upsert_ioc`, `upsert_ioc_batch` — IOC node management
- `add_relation` — edge creation  
- `find_connected`, `find_connected_batch` — graph traversal
- `get_top_nodes_by_degree` — degree-based analytics
- `export_edge_list` — edge list for GNN
- `merge_from_parquet` — cross-sprint data loading
- `checkpoint` — fsync

**Direct usages**:
- `__main__.py:3527` — `scheduler._ioc_graph = DuckPGQGraph()` in `_run_sprint_mode()`
- `windup_engine.py:111,125,127,184,244` — direct `scheduler._ioc_graph` access
- `EvidenceNetworkAnalyzer` — optional injected DuckPGQGraph
- `brain/gnn_predictor.py:620-629` — DuckPGQGraph bridge functions

---

### System 2: GraphService (`knowledge/graph_service.py`, 876 lines)

**Role**: Module-level singleton facade providing a convenient API layer over DuckPGQGraph.

**Storage**: Delegates to DuckPGQGraph (injected or created lazily).

**Locking**: Same as DuckPGQGraph — no independent locking.

**Key capabilities**:
- `upsert_ioc`, `upsert_ioc_batch` — wrappers around DuckPGQGraph
- `upsert_relation`, `upsert_identity_edge` — edge operations
- `find_entity_history` — `DuckPGQGraph.find_connected()` wrapped
- `find_connected_batch` — batch traversal
- `graph_stats`, `checkpoint`, `reset_session`

**Module-level singletons** (anti-pattern):
- `_ModuleSeenIOCs` — tracks seen IOCs to prevent duplicates in a session
- `_ModuleSeenRels` — same for relations
- These are **process-global** state that persists across sprints within the same process

**Used by**:
- `SprintGraphAccumulator` (runtime/graph_accumulator.py) — `gs.upsert_ioc_batch(rows)` at line ~93
- `brain/research_hypothesis_engine.py:1830-1844` — optional cross-sprint retrieval

---

### System 3: IOCGraph (`knowledge/ioc_graph.py`, 1001 lines)

**Role**: STIX-compliant IOC storage and pivot operations.

**Storage**: Kuzu graph database (separate `.kuzu` files).

**Locking**: Kuzu's own internal locking (independent of GraphLockManager).

**Key capabilities**:
- `buffer_ioc`, `flush_buffers` — buffered writes for performance
- `upsert_ioc`, `upsert_ioc_batch` — IOC nodes
- `record_observation`, `record_observation_batch` — edges with finding_id provenance
- `pivot` — STIX-compliant pivot queries
- `export_stix_bundle` — STIX 2.1 bundle export

**Injection pattern**:
```python
# core/__main__.py:2955-3032
ioc_graph = IOCGraph()
await ioc_graph.initialize()
store_instance.inject_truth_write_graph(ioc_graph)  # slot 1
store_instance.inject_stix_graph(ioc_graph)          # slot 2 (same instance)
```

**Used by**:
- `__main__.py` — created and injected into DuckDBShadowStore
- `brain/gnn_predictor.py:336-367` — optional IOC graph for degree lookup

---

### System 4: DuckDBShadowStore (`knowledge/duckdb_store.py`, 9665 lines)

**Role**: Canonical store — `canonical_findings` table + `ioc_graph` table + WAL.

**Storage**: DuckDB (`shadow_store_*.duckdb`) + WAL + LMDB for dedup.

**Locking**: Independent `fcntl.flock` via `_acquire_process_lock()` at line ~9110. **Not** coordinated with GraphLockManager.

**Key capabilities**:
- `async_ingest_findings_batch` — canonical write path (Sprint pipeline)
- `_graph_ingest_findings` — background task ingesting findings into IOC table
- Graph slots via `GraphAttachmentStore` (3 independent slots)

**Graph slots** (via GraphAttachmentStore, Sprint F222):
- `_ioc_graph` — analytics donor (DuckPGQGraph or IOCGraph)
- `_stix_graph` — STIX export (IOCGraph)  
- `_truth_write_graph` — authoritative write (IOCGraph)

**The `ioc_graph` table**: DuckDBShadowStore has its OWN `ioc_graph` DuckDB table. This is DIFFERENT from the DuckPGQGraph's IOC data.

---

## Root Cause Analysis

### Problem 1: No Unified Graph Abstraction

There is no `GraphProtocol` that unifies the 4 systems. Each has different:
- Storage backends (DuckDB file, Kuzu, DuckDB table)
- Locking strategies (GraphLockManager, Kuzu internal, independent fcntl)
- API surfaces (STIX vs analytics vs canonical store)

### Problem 2: Data Fragmentation

```
IOC Data lives in 3 places:
1. DuckDBShadowStore.ioc_graph table  (canonical)
2. DuckPGQGraph DuckDB file           (analytics)  
3. IOCGraph Kuzu database             (STIX/pivot)
```

These are **not synchronized**. An IOC written to one may not appear in others.

### Problem 3: Lock Fragmentation

```
4 independent locking systems:
1. GraphLockManager (fcntl.flock) — per db_path singleton
2. DuckDBShadowStore._acquire_process_lock() — fcntl.flock, different path
3. Kuzu internal locks — IOCGraph
4. No locks — GraphService (delegates to DuckPGQGraph)
```

`GraphLockManager` is a singleton registry per `db_path`, but:
- DuckDBShadowStore doesn't use it — has its own lock
- IOCGraph doesn't use it — Kuzu handles locking
- The same db_path might be opened by multiple systems without coordination

### Problem 4: SprintScheduler._ioc_graph Direct Access

`windup_engine.py` accesses `scheduler._ioc_graph` directly at 5+ locations:
```python
# windup_engine.py:110-185
if hasattr(scheduler, "_ioc_graph") and scheduler._ioc_graph is not None:
    edge_list = scheduler._ioc_graph.export_edge_list()
    ioc_graph_stats = scheduler._ioc_graph.stats()
    top_nodes = scheduler._ioc_graph.get_top_nodes_by_degree(n=10)
    runner.inject_graph(scheduler._ioc_graph)
    scheduler._ioc_graph.checkpoint()
```

This is **direct DuckPGQGraph** access bypassing GraphService and GraphAttachmentStore seams.

### Problem 5: GraphService Module-Level Singletons

`_ModuleSeenIOCs` and `_ModuleSeenRels` in `graph_service.py` are **process-global** sets. They persist across sprints within the same process, which is:
- A memory leak for long-running processes
- Incorrect behavior if multiple sprints should have isolated graphs

### Problem 6: SprintGraphAccumulator DuckPGQGraph Isolation (F265C Fixed)

Previously `SprintGraphAccumulator` created its own private `DuckPGQGraph()` instance instead of using the scheduler's shared instance. F265C fixed this to use `GraphService.upsert_relation`. But GraphService itself may create a new DuckPGQGraph if none is injected.

---

## The Confirmed Issues

| # | Issue | Severity | Location |
|---|-------|----------|----------|
| 1 | `scheduler._ioc_graph` direct access bypasses GraphAttachmentStore seams | HIGH | windup_engine.py:110-244 |
| 2 | 3 separate IOC storages not synchronized | HIGH | duckdb_store.py ioc_graph table + DuckPGQGraph + IOCGraph |
| 3 | GraphLockManager singleton not used consistently | HIGH | lock_manager.py vs duckdb_store.py:9110 |
| 4 | GraphService module singletons leak across sprints | MEDIUM | graph_service.py:712-755 |
| 5 | DuckDBShadowStore has own DuckDB file + ioc_graph table | MEDIUM | duckdb_store.py:ioc_graph table |
| 6 | No GraphProtocol unifying the 4 systems | MEDIUM | protocols/graph_protocol.py exists but not used consistently |

---

## Solution: Unified Graph Layer Architecture

### Core Principle

**One graph, one storage, unified protocol**. Consolidate to a single graph abstraction that:

1. Uses DuckDB as the single storage backend (not Kuzu, not separate files)
2. Implements a `GraphProtocol` for all graph operations
3. Provides STIX export via a read-only adapter
4. Uses a single locking strategy (GraphLockManager)
5. Is injected once, shared across all consumers

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     GraphFacade                            │
│  (single entry point, owns DuckDB connection)              │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │IOCOperations│  │AnalyticsOps  │  │STIXExportAdapter │  │
│  │(upsert_ioc)│  │(pathfind,    │  │(read-only,       │  │
│  │            │  │ PageRank)    │  │ export_stix)     │  │
│  └──────┬──────┘  └──────┬───────┘  └────────┬─────────┘  │
│         │                │                     │            │
│         └────────────────┼────────────────────┘            │
│                          │                                 │
│                   ┌──────▼──────┐                          │
│                   │ DuckDB     │  (single .duckdb file)   │
│                   │ (ioc_graph │                          │
│                   │  tables)   │                          │
│                   └────────────┘                          │
├─────────────────────────────────────────────────────────────┤
│ GraphLockManager (fcntl.flock, singleton per db_path)      │
└─────────────────────────────────────────────────────────────┘
```

### Implementation Steps

#### Phase 1: Protocol Unification

1. Extend `protocols/graph_protocol.py` to cover all needed operations
2. Make `DuckPGQGraph`, `IOCGraph` both implement `GraphProtocol`
3. Create `DuckDBGraphAdapter` wrapping DuckDB table operations
4. Deprecate direct `scheduler._ioc_graph` access in favor of injected graph

#### Phase 2: Storage Consolidation

1. Move IOCGraph data from Kuzu to DuckDB (STIX-compatible schema)
2. Deprecate `DuckDBShadowStore.ioc_graph` table in favor of unified graph
3. All graph operations go through `GraphFacade.upsert_ioc()` etc.
4. Remove `_graph_ingest_findings` background task — synchronous write path

#### Phase 3: Lock Consolidation

1. Make `DuckDBShadowStore._acquire_process_lock()` use `GraphLockManager`
2. Remove independent fcntl locking in duckdb_store.py
3. All DuckDB graph files use the same lock registry

#### Phase 4: Service Cleanup

1. Remove `_ModuleSeenIOCs` / `_ModuleSeenRels` (replace with DuckDB-level dedup)
2. Make `GraphService` a thin facade over injected `GraphProtocol`
3. Remove `SprintGraphAccumulator` duplicate graph operations
4. Single `inject_graph()` slot instead of 3 separate slots

#### Phase 5: SprintScheduler Integration

1. Replace `scheduler._ioc_graph = DuckPGQGraph()` with `scheduler.inject_graph(GraphFacade())`
2. Windup engine uses `scheduler.get_graph()` seam instead of direct attribute access
3. Export uses `GraphFacade.export_edge_list()` or STIX adapter

### Key Files to Modify

| File | Change |
|------|--------|
| `protocols/graph_protocol.py` | Extend to cover all operations |
| `graph/quantum_pathfinder.py` | Implement GraphProtocol, use shared connection |
| `knowledge/ioc_graph.py` | Adapt to DuckDB backend instead of Kuzu |
| `knowledge/graph_service.py` | Thin facade, remove module singletons |
| `knowledge/duckdb_store.py` | Remove ioc_graph table, use GraphFacade |
| `knowledge/graph_attachment.py` | Simplify to single injection slot |
| `runtime/graph_accumulator.py` | Use injected graph, remove duplicate writes |
| `runtime/windup_engine.py` | Use graph seams instead of direct access |
| `runtime/sprint_scheduler.py` | Own graph lifecycle via GraphFacade |
| `core/__main__.py` | Single graph injection instead of 3 |
| `graph/lock_manager.py` | Ensure consistent singleton usage |

### M1 8GB Considerations

For the consolidated DuckDB graph:
- Single DuckDB file avoids multiple open connections
- WAL mode for crash safety without sync overhead
- Bounded cache: `PRAGMA cache_size=512MB` 
- `PRAGMA threads=1` for M1 friendly concurrency
- GraphLockManager remains process-safe for multi-threaded access

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Data loss during migration | LOW | CRITICAL | Read-only migration first, validate before cutover |
| Lock regression | MEDIUM | HIGH | Test all concurrent access patterns |
| Performance regression | MEDIUM | MEDIUM | Benchmark before/after on M1 8GB |
| Breaking existing consumers | HIGH | MEDIUM | Maintain GraphProtocol adapters, deprecate gradually |

---

## Recommendation Priority

### ✅ DONE (Sprint F269 Phase 1): Fix windup_engine.py direct _ioc_graph access
- Added `SprintScheduler.get_graph()` seam (runtime/sprint_scheduler.py:28248)
- Replaced 4 direct `scheduler._ioc_graph` accesses in `windup_engine.py`:
  1. Line 110-118: GNN edge_list export → `getattr(scheduler, "get_graph")()...export_edge_list()`
  2. Line 125-134: DuckPGQ stats + top nodes → same pattern
  3. Line 186-187: SynthesisRunner.inject_graph → same pattern
  4. Line 246-251: DuckPGQ checkpoint → same pattern
- Fixed `generate_sprint_hypotheses` call to use `await` (async method)

### ✅ DONE (Sprint F269 Phase 2): Consolidate locking (use GraphLockManager everywhere)
- `DuckDBShadowStore._acquire_process_lock()` refaktorována na GraphLockManager
- GraphLockManager singleton per db_path — sdíleno mezi DuckDBShadowStore a DuckPGQGraph
- fcntl.flock jako jediný authoritative lock detector
- READ-ONLY a :memory: fallbacky zachovány
- 139 tests pass (test_sprint_scheduler.py, test_sprint_dashboard.py, test_sprint_f260.py)
### ✅ DONE (Sprint F270): Extend GraphProtocol, create GraphFacade
- `runtime/protocols/graph_protocol.py` v2 — TIER_A (analytics) + TIER_S (STIX) unify
- `DuckPGQGraphAdapter` — aktualizován, plná TIER_A + DuckDB-native TIER_S
- `IOCGraphAdapter` — Kuzu backend, plná TIER_S + fail-open TIER_A
- `GraphFacade` — unified entry point přes 3slot (analytics + stix + truth-write)
- `runtime/adapters/__init__.py` — exporty aktualizovány
### ✅ DONE (Sprint F271): DuckDB-native STIX for DuckPGQGraph
- DuckPGQGraph.graph_stats() — DuckDB-native node/edge counts
- DuckPGQGraph.export_stix_bundle() — STIX 2.1 export (ip, domain, sha256, cve, onion)
- DuckPGQGraph.pivot() — DuckDB recursive CTE pivot
- `__main__.py` — DuckPGQGraph first, Kuzu fallback (F271 Phase 2)
- 139 tests pass
### ✅ DONE (Sprint F272): Remove duplicate IOC storage, single write path
- DuckPGQGraph buffer methods: `buffer_ioc()`, `buffer_observation()`, `flush_buffers()`
- Mirrors IOCGraph interface so DuckPGQGraph can serve as `truth_write_graph`
- `DuckPGQGraphAdapter` TIER_S delegation updated
- `_graph_ingest_findings` now writes to DuckPGQGraph (DuckDB) instead of silent drops
### ✅ DONE (Sprint F273): Remove GraphService module singletons
- Removed `_ModuleSeenIOCs` + `_ModuleSeenRels` wrapper classes (58 lines)
- Removed module-level `_SEEN_IOCS` + `_SEEN_RELS` instances
- `reset_session()` zůstává jako jediný cleanup mechanismus
- `cross_sprint_memory.py` comment updated
- `test_graph_service_f226.py` — odstraněny 2 testy pro odstraněné API
- Instance-level `_seen_iocs`/`_seen_rels` zachovány (správně izolované per-instance)

### ✅ DONE (Audit 2026-06-29): 3 issues found and fixed
- **Issue #1 [HIGH]**: `duckdb_store.py:9490` — logic error v list comprehension (`and` místo správného patternu) → opraveno na for-loop
- **Issue #2 [MEDIUM]**: `duckdb_store.py:9484` — orphaned `_sync_graph_update` používal starý `graph_service._get_graph()` místo `truth_write_graph` → přesměrováno na `self._graph_store().get_truth_write_graph()`
- **Issue #3 [LOW]**: `__main__.py:3535` — přímý přístup k `scheduler._ioc_graph` je INTENTNÍ (bootstrap-only) → přidán comment vysvětlující proč je to správně

The architectural fix is substantial but tractable — the systems already have similar interfaces and the protocol-based DI pattern (`protocols/graph_protocol.py`) is already established in the codebase.
