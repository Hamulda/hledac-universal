# Issue G2-002: Knowledge Layer Refactor — duckdb_store.py 10,751 LOC

## Stav: PHASE 1+2 COMPLETE ✅

---

## Executive Summary

`duckdb_store.py` měl 10,751 řádků. Hlavní třída `DuckDBShadowStore` (řádky 1838–10669) = **8,831 řádků** kombinující 5+ storage backends a 59 slot atributů.

**Phase 1+2 dokončeno:** DuckDBVectorStore composition pattern eliminuje 209 řádků duplicitního kódu.

---

## 1. Co bylo implementováno (Phase 1+2)

### 1.1 DuckDBVectorStore Composition Slot ✅
```python
# V __slots__ přidáno:
"_vector_store",  # F360M: DuckDBVectorStore composition (eliminates duplicate RAG/vector methods)

# V __init__ přidáno:
self._vector_store: Any | None = None

# Cleanup v aclose:
_cleanup_vector_store()  # volá _safe_cleanup("vector_store.close", ...)
```

### 1.2 Lazy Composition Accessor ✅
```python
async def _ensure_vector_store(self) -> Any:
    """F360M: Lazy composition accessor for DuckDBVectorStore."""
    if self._vector_store is not None:
        return self._vector_store
    self.ensure_connected()
    if self._conn is None:
        return None
    from hledac.universal.knowledge.duckdb_vector_store import DuckDBVectorStore
    self._vector_store = DuckDBVectorStore(
        duckdb_conn=self._conn,
        executor=self._executor,
    )
    return self._vector_store
```

### 1.3 Delegated Methods (4 metody, 209 řádků odstraněno) ✅

| Metoda | Původní | Nová |
|--------|----------|-------|
| `upsert_rag_embeddings` | 57 LOC inline | 9 LOC delegace |
| `vector_search_rag` | 75 LOC inline | 10 LOC delegace |
| `upsert_entity_embeddings` | 55 LOC inline | 9 LOC delegace |
| `vector_search_entities` | 75 LOC inline | 10 LOC delegace |

### 1.4 DuckDBVectorStore.close() ✅
```python
def close(self) -> None:
    """F360M: Close DuckDBVectorStore — no-op since connection is owned by DuckDBShadowStore."""
    self._duckdb_conn = None
    self._initialized = False
    self._rag_schema_initialized = False
    self._entity_schema_initialized = False
```

### 1.5 Phase 2: Early Binding ✅
```python
async def async_initialize_schema(self) -> bool:
    # ... existing init code ...
    self._initialized = True
    self._startup_ready.set()
    # F360M Phase 2: Early binding of DuckDBVectorStore
    await self._ensure_vector_store()
    return True
```

### 1.6 Bug Fix: vector_search_rag_mmr ✅
```python
# BEFORE (redundant):
await self.async_initialize_schema()  # redundant - DuckDBVectorStore calls _ensure_schema() internally
self.ensure_connected()
k = min(k, 100)
# ...

# AFTER (clean):
k = min(k, 100)
fetch_k = min(fetch_k, 200)  # cap for M1 8GB
# Fetch candidates — DuckDBVectorStore.vector_search_rag calls _ensure_schema() internally
candidates = await self.vector_search_rag(query_vector, k=fetch_k)
```

---

## 2. Výsledky

| Metrika | Před | Po |
|---------|-------|-----|
| duckdb_store.py LOC | 10,751 | **10,539** (-212) |
| Duplicitní RAG metody | 4 | 0 |
| DuckDBVectorStore composition | Ne | Ano |
| Phase 2 early binding | Ne | Ano |
| vector_search_rag_mmr redundancy | Ano | Ne |
| DuckDBVectorStore.close() | Ne | Ano |

---

## 3. Architekturní přínos

**Před:**
```
DuckDBShadowStore
├── inline upsert_rag_embeddings (57 LOC)
├── inline vector_search_rag (75 LOC)  
├── inline upsert_entity_embeddings (55 LOC)
├── inline vector_search_entities (75 LOC)
└── DuckDBVectorStore (v separátním souboru, 461 LOC)
```

**Po:**
```
DuckDBShadowStore
├── _ensure_vector_store() → DuckDBVectorStore
├── upsert_rag_embeddings() → delegace
├── vector_search_rag() → delegace
├── upsert_entity_embeddings() → delegace
├── vector_search_entities() → delegace
└── DuckDBVectorStore (v separátním souboru, 461 LOC)
    └── close() — lifecycle cleanup
```

---

## 4. Následující kroky (Phase 3)

### 4.1 DuckDBWriteCoordinator Composition
`DuckDBWriteCoordinator` (589 LOC v `duckdb_write_coordinator.py`) by měl být composován do `DuckDBShadowStore` stejným způsobem jako `DuckDBVectorStore`.

### 4.2 DuckDBMaintenanceManager
400+ řádkové metody `_maintenance_loop`, `_graph_update_coro` by měly být extrahovány do samostatné třídy.

---

## 5. M1 8GB UMA Constraints (nezměněno)

- DuckDB zůstává in-process (žádný subprocess isolation overhead)
- LMDB pro dedup zůstává (zero-copy performance)
- Lazy initialization je zachován pro M1 8GB
- DuckDBVectorStore je vytvořen lazy na prvním použití (nebo early binding pokud explicitně voláno)

---

*Status: PHASE 1+2 COMPLETE (2026-07-31)*
