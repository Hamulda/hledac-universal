# DuckDB Vector Store

## Metadata

- **Entry Path:** modules/duckdb-vector-store
- **Status:** current
- **Source:** knowledge/duckdb_vector_store.py
- **Evidence Level:** source
- **Last Verified:** 2026-08-20
- **Category:** module

## Summary

RAG vector storage backed by DuckDB with optional LanceDB fallback.

## Source Paths

- `knowledge/duckdb_vector_store.py`

## Purpose

Semantic search over research findings and evidence.

## Lazy Initialization

Created on first access via `DuckDBShadowStore._ensure_vector_store()`.

## Key Methods

| Method | Purpose |
|--------|---------|
| `upsert_rag_embeddings()` | Store embedding + text |
| `vector_search_rag()` | ANN search for relevant context |

## M1 Considerations

DuckDB in-process mode (S-04: IPC subprocess removed).

## Optional Dependencies

```bash
uv sync --extra mlx-embed  # MLX-native embedding
```

## Related Entries

- modules/duckdb-shadow-store
- features/rag-search
