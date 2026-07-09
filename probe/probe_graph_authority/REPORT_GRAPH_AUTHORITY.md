# Sprint F206AI: Graph Authority and Write-Path Seal

**Date:** 2026-05-01
**Status:** COMPLETE
**Scope:** Graph ecosystem authority matrix and guard tests

---

## Executive Summary

All graph write paths have been classified. No deprecated module can silently become an active truth writer. Every write method has a declared owner with explicit deprecation risk ratings.

---

## Authority Matrix

| Module | Class | Type | Write Methods | Status |
|--------|-------|------|---------------|--------|
| `knowledge/graph_service.py` | module (facade) | **FACADE** | `upsert_ioc`, `upsert_relation`, `upsert_identity_edge`, `upsert_ioc_batch` | ACTIVE — delegates to DuckPGQGraph |
| `graph/quantum_pathfinder.py` | `DuckPGQGraph` | **ACTIVE TRUTH** | `add_ioc`, `upsert_ioc_batch`, `add_relation` | ACTIVE — DuckDB backend for scheduler accumulation |
| `knowledge/ioc_graph.py` | `IOCGraph` | **STANDALONE TRUTH** | `upsert_ioc`, `buffer_ioc`, `upsert_ioc_batch`, `add_relation`, `export_stix_bundle` | STANDBY — Kuzu backend, not active in scheduler accumulation path |
| `knowledge/graph_attachment.py` | `GraphAttachmentStore` | **CAPABILITY GATE** | `inject_graph` (capability-checked), `inject_truth_write_graph` | ACTIVE — capability gate for graph attachment |
| `knowledge/duckdb_store.py` | `DuckDBShadowStore` | **SPRINT FACTS** | `inject_graph` (DEPRECATED, delegates to GraphAttachmentStore) | ACTIVE — NOT graph authority |
| `coordinators/graph_coordinator.py` | `GraphCoordinator` | **REASONING OVERLAY** | none | READ-ONLY |
| `graph/quantum_pathfinder.py` | `QuantumInspiredPathFinder` | **REASONING OVERLAY** | none | READ-ONLY |
| `knowledge/graph_layer.py` | `KnowledgeGraphLayer` | **DEPRECATED** | `add_entry` | NO PRODUCTION CALL SITES |
| `knowledge/context_graph.py` | `ContextGraph` | **DEPRECATED** | `add_node`, `add_edge` | LEGACY ONLY (in `legacy/` dir) |
| `knowledge/graph_rag.py` | `GraphRAG` | **ACTIVE-READ** | none | READ-ONLY RAG pipeline |
| `knowledge/persistent_layer.py` | N/A (stub) | **DEPRECATED STUB** | none | Forwards to `legacy.persistent_layer` |

---

## Canonical Write Path (F226/F232 update)

```
runtime/sprint_scheduler.py
  └─ _accumulate_findings_to_graph (line ~7090)
       └─ graph_service.upsert_ioc_batch()
            └─ _get_graph() → DuckPGQGraph.add_ioc() [DuckDB ACTIVE TRUTH STORE]
```

**Active backend:** `DuckPGQGraph` (DuckDB) — all scheduler graph accumulation writes go here
via `graph_service.upsert_ioc_batch()` → `DuckPGQGraph.upsert_ioc_batch()`.

**IOCGraph (Kuzu):** Standalone truth store, not the active accumulation target for scheduler
writes. Exists as a separate module (`knowledge/ioc_graph.py`) and can serve as a future
truth-write backend if DuckDB path is deprecated.

**Key change from F206AI:** `graph_service.upsert_ioc()` (and `upsert_ioc_batch()`) delegates
to `DuckPGQGraph` via `_get_graph()`, not to IOCGraph. The Kuzu path (`IOCGraph`) is
present in module structure but is not invoked by scheduler's graph accumulation seam.

---

## Reset Session Verification

- **Exists:** `knowledge/graph_service.py:152`
- **Called at:** `runtime/sprint_scheduler.py:5832` (sprint teardown)
- **Status:** VERIFIED

---

## DuckDBShadowStore — DEPRECATED (Sprint F222)

```python
def inject_graph(self, graph: Any) -> None:
    """DEPRECATED (Sprint F222): Delegates to GraphAttachmentStore.inject_graph()."""
    self._graph_store().inject_graph(graph)
```

- **Status:** DEPRECATED — delegates entirely to GraphAttachmentStore
- **Capability gate responsibility** moved to GraphAttachmentStore.inject_graph()
- DuckDBShadowStore is explicitly labeled NOT graph authority in its class docs

---

## DuckDBShadowStore Capability Gate (GraphAttachmentStore)

```python
def inject_graph(self, graph: Any) -> None:
    """
    STORE IS NOT GRAPH TRUTH OWNER ...
    Capability must be checked, never assumed. Set by inject_graph().
    """
```

- **Status:** VERIFIED — capability/authority check present in GraphAttachmentStore.inject_graph()
- **VERDICT comment:** Present at graph_attachment.py:62-64

---

## Deprecated Modules — Cannot Silently Write

| Module | Protection Mechanism |
|--------|---------------------|
| `graph_layer.py` | `add_entry()` has ZERO production call sites |
| `context_graph.py` | Only imported in `legacy/autonomous_orchestrator.py` |
| `persistent_layer.py` | Stub file — no real code |
| `graph_rag.py` | Read-only RAG — zero write methods |

---

## Test Coverage

Created: `tests/probe_graph_authority_f206ai/test_graph_authority.py`

| Test Class | Coverage |
|------------|----------|
| `TestDeprecatedGraphModulesCannotWrite` | 4 tests |
| `TestGraphServiceResetSession` | 3 tests |
| `TestDuckDBShadowStoreCapabilityGate` | 3 tests |
| `TestGraphAnalyticsBounded` | 3 tests |
| `TestGraphWritePathOwners` | 5 tests |

**Total:** 18 probe tests

---

## Abort Conditions — All Avoided

| Condition | Status |
|-----------|--------|
| Live graph DB mutation in tests | PREVENTED — MagicMock/AsyncMock only |
| Network | Not used |
| Model load | Not used |
| Broad rewrite | Not done — VERDICT comments only |

---

## Artifacts

- `probe_graph_authority/graph_authority_matrix.json` — Machine-readable authority matrix
- `probe_graph_authority/REPORT_GRAPH_AUTHORITY.md` — This report
- `tests/probe_graph_authority_f206ai/test_graph_authority.py` — Guard tests

---

## Verification Command

```bash
pytest hledac/universal/tests/probe_graph_authority_f206ai/ -v
```