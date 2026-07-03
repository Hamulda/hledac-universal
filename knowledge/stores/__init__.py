"""knowledge/stores — Triple Storage SSOT Architecture (F320)

PEP 544 Protocol-based storage abstractions:
- FindingStore: canonical write path (DuckDB durable)
- HotCacheStore: LMDB read-through cache
- VectorStore: LanceDB ANN embeddings

Composites:
- CompositeFindingStore: delegates to specializovane implementace

M1 8GB optimalizace:
- DuckDBPool: max 2 connections (M1 P-core ceiling)
- asyncio.to_thread pro zero-GIL blocking I/O
- Arrow IPC zero-copy pres duckdb_subprocess_writer
"""
from __future__ import annotations


from knowledge.stores.protocols import (
    FindingStore,
    HotCacheStore,
    VectorStore,
    FindingFilter,
)

# Lazy imports pro experimental/stub implementace
__all__ = [
    "FindingStore",
    "HotCacheStore",
    "VectorStore",
    "FindingFilter",
    "DuckDBPool",
    "DuckDBFindingStore",
    "CompositeFindingStore",
    "LMDBHotCacheStore",
    "LanceDBVectorStore",
]


def __getattr__(name: str):
    """Lazy loading — avoid import-time cost for unused stores."""
    if name == "DuckDBPool":
        from knowledge.stores.duckdb_pool import DuckDBPool

        return DuckDBPool
    if name == "DuckDBFindingStore":
        from knowledge.stores.duckdb_finding_store import DuckDBFindingStore

        return DuckDBFindingStore
    if name == "CompositeFindingStore":
        from knowledge.stores.composite_store import CompositeFindingStore

        return CompositeFindingStore
    if name == "LMDBHotCacheStore":
        from knowledge.stores.lmdb_hot_cache import LMDBHotCacheStore

        return LMDBHotCacheStore
    if name == "LanceDBVectorStore":
        from knowledge.stores.lancedb_vector_store import LanceDBVectorStore

        return LanceDBVectorStore
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
