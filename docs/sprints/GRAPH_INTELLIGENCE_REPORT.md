# GRAPH_INTELLIGENCE_REPORT.md — Sprint F214Q Audit & Wiring (Updated 2026-05-23)

## 1. QuantumPathfinder Analysis

### Algorithm
**Quantum-inspired pathfinding** (NOT pure quantum):
- Sparse matrix (scipy COO) representation of graph adjacency
- Grover/Hadamard quantum coin operators for state evolution
- Amplitude amplification (Grover-style) to amplify target probability
- MLX acceleration for Metal GPU (Apple Silicon)
- Fallback: NumPy + SciPy sparse matrices

**NOT**: Yen's k-shortest, A*, Dijkstra — those are classical algorithms.

### Graph Representation
- **DuckPGQGraph**: DuckDB with `ioc_nodes` + `ioc_edges` tables. NOT NetworkX.
- **QuantumInspiredPathFinder**: scipy sparse COO matrix. NOT NetworkX.
- **networkx only**: Used in `graph_manager.py` for visualization (pyvis HTML export).

### Dependencies (All Clean — No Broken Imports)
- `duckdb` (REQUIRED)
- `numpy` (REQUIRED)
- `scipy` (optional, lazy ImportError guard)
- `mlx` (optional, lazy ImportError guard)
- `networkx` (optional, graph_manager only)

---

## 2. Audit Results: graph/ Directory

| File | Lines | Primary Class/Fn | Dependencies | Status |
|------|-------|-----------------|--------------|--------|
| `__init__.py` | 42 | Lazy import factory | None | ✅ OK |
| `graph_manager.py` | 255 | `GraphManager` | networkx, pyvis (lazy) | ✅ OK — visualization only |
| `quantum_pathfinder.py` | 1459 | `DuckPGQGraph`, `QuantumInspiredPathFinder` | duckdb, numpy, scipy/mlx (optional) | ✅ OK |

---

## 3. Sprint Scheduler Wiring (Already Done)

| Method | Location | Status |
|--------|----------|--------|
| `DuckPGQGraph()` instantiation | Line ~12428, `reset_session()` | ✅ Wired |
| `_ioc_graph.add_relation()` | Line ~12401, `_accumulate_findings_to_graph()` | ✅ Wired |
| `_ioc_graph.find_connected()` | Line ~8433, `_run_quantum_path_analysis()` | ✅ Wired |
| `DuckPGQGraph.stats()` | Line ~12951 | ✅ Wired |
| `quantum_path_seeds` field | Line 1370, `SprintSchedulerResult` | ✅ Wired |

---

## 4. NOT Yet Wired — Implementation Required

### 4.1 `find_paths_between_iocs()` — DuckPGQGraph
Does NOT exist. `find_best_path()` exists (line 1397) but is never called — takes NetworkX graph input, sprint uses DuckDB-backed DuckPGQGraph.

### 4.2 HLEDAC_ENABLE_GRAPH_ANALYSIS Gate
Does NOT exist. Quantum path analysis runs unconditionally (fail-soft only).

### 4.3 graph_path_discovery CanonicalFinding
`quantum_path_seeds` stored in `SprintSchedulerResult` but NOT as CanonicalFinding.

---

## 5. Implementation Plan

### P1: Add `find_paths_between_iocs()` to DuckPGQGraph
- Build adjacency list from DuckDB edges
- Initialize QuantumInspiredPathFinder with adjacency list
- Run quantum walk to find paths
- Return list of path lists

### P2: Add HLEDAC_ENABLE_GRAPH_ANALYSIS gate
```python
import os
if os.environ.get("HLEDAC_ENABLE_GRAPH_ANALYSIS", "0") != "1":
    return []
```

### P3: Wire graph path discovery findings as CanonicalFinding
```python
source_type = "graph_path_discovery"
```

### P4: M1 Safety (Already In Place)
- `max_nodes = 5000` in DuckPGQGraph
- `Semaphore(1)` on graph ops
- `cleanup()` called in finally block

---

## 6. Summary Table
**Quantum-Inspired Random Walk** (NOT true quantum):
- Simulates quantum walk dynamics: Hadamard-like coin operator + amplitude amplification
- Grover-like search: periodic target amplification every 5 steps (`if step % 5 == 0`)
- Classical simulation on NumPy sparse matrices, optional MLX acceleration
- M1 fallback: NumPy only (MLX requires additional import)

### DuckPGQGraph Backend
- `DuckPGQGraph` owns DuckDB connection + two tables (`ioc_nodes`, `ioc_edges`) persisted at `IOC_DB_PATH`
- `_ensure_duckpgq(con)` installs `duckpgq` extension lazily (once per process)
- DuckDB WAL checkpoint on every `checkpoint()` call
- **Key**: DuckPGQGraph is a donor backend - it provides graph analytics (paths, centrality) for data owned by the canonical DuckDB store

### Input / Output
| Method | Input | Output |
|--------|-------|--------|
| `find_paths(start_nodes, target_nodes, max_steps)` | Two node lists | `List[List[str]]` — all paths found |
| `find_best_path(graph, start, end)` | graph obj + start + end str | `list[str]` — best path |
| `find_connected(val, max_hops)` | IOC value str | `list[dict]` — connected nodes |
| `add_relation(src, dst, rel_type)` | str, str, str | side-effect: writes to DuckDB |
| `stats()` | none | node/edge counts, pgq_available |

**Constraints**: `max_nodes=5000` (DuckPGQGraph init), M1 RAM bound of 1000 nodes per traversal.

### Wiring Status
- DuckPGQGraph instantiated in `sprint_scheduler` at line ~11432 (`self._ioc_graph = DuckPGQGraph()`)
- `_ioc_graph.add_relation()` called in hypothesis confirmation loop (~line 11406)
- `DuckPGQGraph.find_paths` / `find_connected` — **NOT called** anywhere in sprint_scheduler
- `find_best_path` — standalone function, no production caller
- **NOT wired** to post-sprint analysis phase. No path queries run on sprint results.

---

## 2. GraphService / DuckPGQ Backend

### Schema (DuckDB, persisted to `IOC_DB_PATH`)

```sql
CREATE TABLE IF NOT EXISTS ioc_nodes (
    id         BIGINT PRIMARY KEY,    -- SHA1(val)[0:8] & 0x7FFFFFFFFFFFFFFF
    val        VARCHAR NOT NULL UNIQUE,
    ioc_type   VARCHAR,
    confidence FLOAT,
    src        VARCHAR,
    first_seen TIMESTAMP DEFAULT now()
)

CREATE TABLE IF NOT EXISTS ioc_edges (
    src_id   BIGINT REFERENCES ioc_nodes(id),
    dst_id   BIGINT REFERENCES ioc_nodes(id),
    rel_type VARCHAR,
    weight   FLOAT DEFAULT 1.0,
    evidence VARCHAR
)
```

### IOC Taxonomy Coverage
`upsert_ioc(val, ioc_type, confidence, src)` accepts **any string** for `ioc_type` — no enforcement of the 20-type taxonomy from `patterns/pattern_matcher.py`. Any type string can be written; no validation against the pattern taxonomy exists. This is a gap: the graph will store arbitrary `ioc_type` strings that may not match the canonical taxonomy.

### Wiring
- `GraphService.upsert_ioc()` → delegates to `DuckPGQGraph.upsert_ioc()` via `_get_graph()`
- `GraphService.upsert_ioc_batch()` → deduplicates at call site then delegates to graph
- Module-level facade functions call `_DEFAULT_GRAPH_SERVICE` singleton
- Used by `_accumulate_findings_to_graph()` (sprint_scheduler ~line 7625) for CT findings

---

## 3. Wiring: quantum_pathfinder → Post-Sprint Analysis

### Current State: NOT WIRED

Post-sprint analysis phase does NOT call `find_paths` or `find_best_path`. The `DuckPGQGraph` is only used as a write path (hypothesis → `add_relation`) and stats collector (`stats()`).

### Required Wiring (what's missing)
After `DuckDBShadowStore.async_ingest_findings_batch` completes for a sprint:
1. Extract all accepted IOC values from `sprint_result.findings`
2. Call `DuckPGQGraph.find_connected(value, max_hops=3)` for each new IOC
3. Cluster the connected nodes (undiscovered related IOCs)
4. Store clusters in sprint export output as `{sprint_id}_next_seeds.json` (already implemented via `_generate_next_sprint_seeds`, but uses `get_top_nodes_by_degree` — not path-based discovery)
5. Feed these clusters back as next-sprint bootstrap seeds via the acquisition planner

**Correction**: `sprint_seeds.lmdb` does NOT exist. Next-sprint seeds are produced by `_generate_next_sprint_seeds()` in `export/sprint_exporter.py` (JSON file output). That function calls `get_top_nodes_by_degree(n)` — a degree-centrality approach, NOT the quantum pathfinder's `find_paths`/`find_connected`. Path-based seed discovery is still unwired.

**Sprint Scheduler seam**: `_accumulate_findings_to_graph()` is the correct injection point — runs after `async_ingest_findings_batch`, already upserts IOCs. Add a second phase there: `await _run_quantum_path_analysis(sprint_result)`.

---

## 4. RelationshipDiscoveryEngine — NOT Wired

### Architecture
- Pure **NetworkX in-memory** graph — no DuckDB connection
- `RelationshipDiscoveryEngine.__init__` builds `self._nx_graph` from input edge list
- Persistence: `save_graph(path)` / `load_graph(path)` via pickle (NetworkX pickle format)
- No DuckPGQ queries; no cross-sprint accumulation

### Why betweenness = 0.0032?
The 0.0032 betweenness centrality score is computed from **NetworkX in-memory graph built from session inputs** (e.g., CT finding relationships, pasted IOCs). It is NOT from the DuckPGQ graph. The engine measures centrality of entities within a single session's relationship graph, not the accumulated cross-sprint graph.

### Wiring Status
- **NOT called after `GraphService.upsert_ioc()`**
- Called from: `prefetch_oracle.py` (RELATIONSHIP_DISCOVERY_AVAILABLE path only)
- No automatic invocation after graph upsert

### If Wired
To wire correctly: call `RelationshipDiscoveryEngine.discover_relationships(edges)` with the same edges passed to `GraphService.upsert_relation()`, then feed the discovered latent relationships back as `suggested_pivots` in the evidence envelope. However, the current architecture keeps NetworkX and DuckPGQ completely separate.

---

## 5. Cross-Sprint Graph Persistence — VERIFIED ACCUMULATIVE

### DuckDB file persists
- DuckDB file path: `IOC_DB_PATH` (default `~/.hledac/ioc.db` or similar via `paths.get_ioc_db_path()`)
- `DuckPGQGraph.__init__` calls `duckdb.connect(db_path)` — **opens existing file, does NOT create new**
- WAL checkpointed on `checkpoint()` but **no `DROP TABLE`** anywhere in reset path

### reset_session() does NOT wipe data
```python
def reset_session(self) -> None:
    # Clears: _seen_iocs, _seen_rels (session idempotency sets)
    # Clears: _DUCKPGQ_GRAPH singleton reference
    # Does NOT: DROP TABLE, DELETE FROM, vacuum, or recreate ioc_nodes/ioc_edges
    _DUCKPGQ_GRAPH = None   # singleton cleared but DB file persists
    self._seen_iocs.clear()
    self._seen_rels.clear()
```

On next sprint, `_get_graph()` re-initializes `DuckPGQGraph` pointing to the **same DuckDB file** — all previously upserted IOCs and edges remain.

### Evidence
- `DuckDB.connect(db_path)` opens existing file (no overwrite flag)
- No `CREATE OR REPLACE TABLE` in `DuckPGQGraph._init_duckdb`
- `reset_session()` in `graph_service.py` line 251 — only clears in-memory sets + singleton
- `sprint_scheduler.reset_session()` at line 12844 calls `graph_service.reset_session()` — only clears session state

**Cross-sprint accumulation: CONFIRMED. The graph grows across all sprints.**

---

## Summary Table

| Component | Status | Notes |
|-----------|--------|-------|
| DuckPGQGraph (sprint_scheduler._ioc_graph) | ✅ Wired | Write path only (add_relation, upsert_ioc) |
| DuckPGQGraph.find_connected | ✅ Wired | Called in _run_quantum_path_analysis (line 8433) |
| DuckPGQGraph.find_paths_between_iocs | ✅ IMPLEMENTED P1 | BFS paths, max 10 paths, fail-soft (line 1360) |
| DuckPGQGraph.stats | ✅ Wired | Called at line 12951 |
| HLEDAC_ENABLE_GRAPH_ANALYSIS gate | ✅ IMPLEMENTED P2 | Line 8440 in sprint_scheduler |
| DuckPGQGraph.upsert_ioc_batch | ❌ Unused | Single add_ioc used instead |
| DuckPGQGraph.export_edge_list | ❌ Unused | Future telemetry use |
| graph_path_discovery CanonicalFinding | ❌ Pending | quantum_path_seeds stored in SprintSchedulerResult |

---

## Audit Completed 2026-05-23

### Changes Made
1. **P1**: `DuckPGQGraph.find_paths_between_iocs()` added (line 1360, quantum_pathfinder.py)
   - BFS implementation, bounded max 10 paths, max_hops=4 default
   - Fail-soft: returns `[]` on any exception
   - M1-safe: LIMIT 5000 on edge query

2. **P2**: `HLEDAC_ENABLE_GRAPH_ANALYSIS` gate added (line 8440, sprint_scheduler.py)
   - Returns `[]` if env var not set to `"1"`
   - Fail-soft so sprints continue if disabled

### To Enable Graph Analysis
```bash
export HLEDAC_ENABLE_GRAPH_ANALYSIS=1
```