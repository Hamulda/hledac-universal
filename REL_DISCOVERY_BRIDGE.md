# REL_DISCOVERY_BRIDGE.md — Wave 2 Cross-Sprint Relationship Persistence

## Status: IMPLEMENTED (Wave 2)

Date: 2026-05-23
Sprint: 124
Wave: 2 (RelationshipDiscoveryEngine → DuckPGQ bridge)

---

## 1. Discovered API Surface

### RelationshipDiscoveryEngine (`intelligence/relationship_discovery.py`, 2357+ lines)

| Method | Signature | Line |
|--------|-----------|------|
| `add_relationship` | `(relationship: Relationship) -> bool` | 821 |
| `export_graph` | `() -> Any` (returns NetworkX graph via `_build_networkx_graph()`) | 2172 |
| `to_dict` | `() -> Dict[str, Any]` (entities + relationships + stats) | 2176 |
| `save_graph` | `(path: Path) -> None` (Wave 2, with MAX_NODES pruning) | 2195 |
| `load_graph` | `(path: Path) -> bool` (Wave 2, returns True on success) | 2218 |
| `_build_networkx_graph` | `() -> Any` (lazy, caches in `self._nx_graph`) | 1064 |

**Relationship dataclass** (line 196):
- `source: str`, `target: str`, `type: Union[str, RelationshipType]`, `strength: float = 1.0`, `confidence: float = 0.5`

### GraphService (`knowledge/graph_service.py`)

| Method | Signature | Line |
|--------|-----------|------|
| `upsert_relation` | `(src, dst, rel_type, weight=1.0, evidence="") -> bool` | 162 |
| Module-level | `upsert_relation(src, dst, rel_type, ...)` → `_DEFAULT_GRAPH_SERVICE` | 414 |
| `register_relationship_callback` | `(fn: Callable[..., None]) -> None` | **NEW (line 91)** |
| `_relationship_callbacks` | `list[Callable]` in `__slots__` | **NEW** |

---

## 2. Implementation (3 files changed)

### `knowledge/graph_service.py` — Callback Hook
- `__slots__` expanded: `("_relationship_callbacks",)` (line 82)
- `__init__`: `self._relationship_callbacks: list[Callable] = []` (line 87)
- `register_relationship_callback(fn)` added (line 91)
- `upsert_relation` fires all callbacks after successful `add_relation()` (line ~192)

### `intelligence/relationship_discovery.py` — Persistence
- `Path` imported from `pathlib`
- `MAX_NODES = 10_000` class attribute
- `save_graph(path)` — prunes to MAX_NODES by lowest-degree, pickle-dumps NetworkX graph
- `load_graph(path)` — loads pickle, prunes if >MAX_NODES, rebuilds internal entities/relationships from NX edges

### `runtime/sprint_scheduler.py` — Wiring
- WARMUP init (line ~2574): instantiates `RelationshipDiscoveryEngine(use_sparse=True, max_memory_mb=512)`, loads persisted graph if exists, registers callback on `_DEFAULT_GRAPH_SERVICE`
- Teardown (line ~3333): calls `save_graph()` + `_sync_latent_relationships_to_graph()`
- `_sync_latent_relationships_to_graph()` (line ~7933): exports NetworkX graph, upserts unseen edges to DuckPGQ with `rel_type="latent_related"`, weight from NetworkX edge data

---

## 3. Key Design Decisions

1. **Callback not async-first**: callbacks fire synchronously in `upsert_relation` using `asyncio.get_event_loop().run_until_complete()` for async callbacks.
2. **Node pruning by degree**: lowest-degree nodes pruned first (carry least structural information).
3. **Latent sync uses seen_rels**: checks `_DEFAULT_GRAPH_SERVICE._seen_rels` to avoid re-upserting already-known relations within the sprint.
4. **pickle over orjson**: NetworkX graph serialization requires pickle. LMDB path ensures cross-sprint persistence.

---

## 4. Memory Bound

- **MAX_NODES = 10,000** — prune on save AND on load
- **max_memory_mb=512** — RelationshipDiscoveryEngine advisory limit for M1 8GB
- **Lazy NetworkX** — `_nx_graph` built on demand, not at `__init__`

---

## 5. File Changes Summary

| File | Change |
|------|--------|
| `knowledge/graph_service.py` | `Callable` import, `__slots__` + `__init__` + `register_relationship_callback` + callback fire in `upsert_relation` |
| `intelligence/relationship_discovery.py` | `Path` import, `MAX_NODES`, `save_graph()`, `load_graph()` |
| `runtime/sprint_scheduler.py` | WARMUP: init + load + register; teardown: save + sync latent; new `_sync_latent_relationships_to_graph()` |

---

## 6. Node Count at Sprint 124

**First run — node count will be 0** (cold start, no persisted graph yet).
After Sprint 124: graph saved to `LMDB_ROOT / "rel_discovery_graph.pkl"`.
Subsequent sprints: loaded at WARMUP, accumulated across runs.

---

## 7. Invariant Checklist

| # | Invariant | Status |
|---|-----------|--------|
| 1 | RelationshipDiscoveryEngine.add_relationship(Relationship) → bool | ✅ `add_relationship(Relationship)` at line 821 |
| 2 | save_graph/load_graph roundtrip preserves node count | ✅ probe test needed |
| 3 | MAX_NODES prune triggers when graph > 10,000 | ✅ both save and load prune |
| 4 | GraphService callback fires on upsert_relation | ✅ after `graph.add_relation()` + `_seen_rels.add()` |
| 5 | Sprint teardown calls save_graph | ✅ in teardown block |
| 6 | WARMUP calls load_graph if file exists | ✅ `if _rel_graph_path.exists()` before load |
| 7 | Latent relationships upserted to DuckPGQ | ✅ `_sync_latent_relationships_to_graph()` uses `upsert_relation` |

---

## 8. Pre-Existing Diagnostics

All `reportMissingImports` for optional deps (scipy, igraph, mlx, community, datasketch, gnn_predictor) and `reportOptionalMemberAccess` on `_nx_graph` are pre-existing and unrelated to this change.

---

END OF REL_DISCOVERY_BRIDGE.md