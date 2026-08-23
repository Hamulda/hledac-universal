# DuckDB Shadow Store

## Metadata

- **Entry Path:** modules/duckdb-shadow-store
- **Status:** current
- **Source:** knowledge/db.py
- **Evidence Level:** source
- **Last Verified:** 2026-08-20
- **Category:** module

## Summary

Canonical DuckDB store for structured findings with RAG vector support and graph analytics.

## Source Paths

- `knowledge/db.py`
- `knowledge/duckdb_vector_store.py`
- `knowledge/duckdb_graph_attachment.py`

## Storage Trinity

| Layer | Technology | Purpose |
|-------|-----------|---------|
| DuckDB | SQL | Canonical findings |
| LMDB | Key-value | Entity/claim metadata, whisper cache |
| LanceDB | ANN | RAG embeddings |

## Key Methods

| Method | Purpose |
|--------|---------|
| `async_ingest_findings_batch()` | **Canonical write path** |
| `arrow_fetch_batch()` | Zero-copy Arrow C Data |
| `_ensure_vector_store()` | Lazy RAG vector store |
| `_ensure_graph_attachment()` | Lazy graph analytics |

## Singleton Pattern

```python
from knowledge.db import UnifiedDatabaseFacade

db = UnifiedDatabaseFacade()  # Singleton
duckdb_store = db.duckdb  # DuckDBShadowStore
```

## Arrow Zero-Copy

M1 8GB optimization: `fetch_arrow_table()` uses Arrow C Data Interface — no Python intermediary.

## Related Entries

- modules/duckdb-pool
- features/rag-search
- modules/hypothesis-graph
