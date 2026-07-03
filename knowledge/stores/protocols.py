"""knowledge/stores/protocols.py — PEP 544 Storage Abstractions (F320)

Triple Storage SSOT Architecture:
- FindingStore: canonical write path (DuckDB durable)
- HotCacheStore: LMDB read-through cache
- VectorStore: LanceDB ANN embeddings

Cutting-edge design principles:
- Protocol-based DI (PEP 544) — no inheritance coupling
- Bounded resources — M1 8GB ceiling per store
- Async-first — asyncio.to_thread for blocking I/O
- Zero-copy Arrow IPC — subprocess writer pattern

M1 8GB bounds:
- DuckDB: max 2 connections (M1 P-core ceiling, F265-U5)
- LMDB hot cache: 16 MB map, 5000 entry limit (F265B pattern)
- LanceDB: optional, RAM-gated (advanced_rag fallback)
"""
from __future__ import annotations


from typing import (
    TYPE_CHECKING,
    Protocol,
    runtime_checkable,
    Any,
)
from collections.abc import Iterator, AsyncIterator

if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding, ActivationResult


# -----------------------------------------------------------------------------
# FindingFilter — query DSL for FindingStore
# -----------------------------------------------------------------------------

class FindingFilter:
    """Query filter for FindingStore.query() / query_async()."""

    __slots__ = (
        "sprint_id",
        "source_type",
        "min_confidence",
        "limit",
        "offset",
        "keywords",
    )

    def __init__(
        self,
        sprint_id: str | None = None,
        source_type: str | None = None,
        min_confidence: float = 0.0,
        limit: int = 100,
        offset: int = 0,
        keywords: list[str] | None = None,
    ):
        self.sprint_id = sprint_id
        self.source_type = source_type
        self.min_confidence = min_confidence
        self.limit = limit
        self.offset = offset
        self.keywords = keywords


# -----------------------------------------------------------------------------
# FindingStore — canonical write path (DuckDB durable)
# -----------------------------------------------------------------------------

@runtime_checkable
class FindingStore(Protocol):
    """
    PEP 544 Protocol — canonical finding storage.

    Implementation: DuckDBFindingStore (knowledge/stores/duckdb_finding_store.py)

    M1 8GB: bounded by DuckDB file-backed mmap (automatic out-of-core).
    Thread safety: asyncio.to_thread + DuckDBPool (max 2 connections).
    """

    async def append(self, finding: CanonicalFinding) -> None:
        """Append single finding to canonical store."""
        ...

    async def append_batch(
        self, findings: list[CanonicalFinding]
    ) -> list[ActivationResult]:
        """
        Batch append with quality gating.

        Returns ActivationResult per finding (accepted/rejected/duplicate).
        M1 8GB: chunks of 2048 rows (Arrow batch optimal size).
        """
        ...

    async def query(self, filter: FindingFilter) -> Iterator[dict[str, Any]]:
        """
        Synchronous query iterator.

        Yields finding dicts matching filter criteria.
        """
        ...

    async def query_async(
        self, filter: FindingFilter
    ) -> AsyncIterator[dict[str, Any]]:
        """
        Async query iterator — for streaming large result sets.

        Uses asyncio.to_thread to avoid blocking the event loop.
        """
        ...


# -----------------------------------------------------------------------------
# HotCacheStore — LMDB read-through cache
# -----------------------------------------------------------------------------

@runtime_checkable
class HotCacheStore(Protocol):
    """
    PEP 544 Protocol — LMDB read-through cache for hot findings.

    Implementation: LMDBHotCacheStore (knowledge/stores/lmdb_hot_cache.py)

    M1 8GB bounds:
    - 16 MB map size (matches F265B conditional_cache)
    - 5000 entry limit (FIFO eviction)
    - zstd compression (matches F265B pattern)

    Read path: fingerprint → LMDB → finding_id (zero-copy buffer)
    Write path: fingerprint → LMDB + Bloom filter (DedupManager integration)
    """

    def lookup(self, fingerprint: str) -> str | None:
        """
        Lookup fingerprint in hot cache.

        Returns finding_id if found, None otherwise.
        Zero-copy: returns LMDB buffer directly (no bytes() conversion).
        """
        ...

    def store(self, fingerprint: str, finding_id: str) -> None:
        """
        Store fingerprint → finding_id mapping.

        Non-blocking: best-effort with error suppression.
        Updates Bloom filter in DedupManager when available.
        """
        ...

    def get_stats(self) -> dict[str, Any]:
        """
        Return cache statistics.

        Includes: hits, misses, hit_rate, entry_count, memory_bytes.
        """
        ...


# -----------------------------------------------------------------------------
# VectorStore — LanceDB ANN embeddings
# -----------------------------------------------------------------------------

@runtime_checkable
class VectorStore(Protocol):
    """
    PEP 544 Protocol — ANN vector store for semantic RAG.

    Implementation: LanceDBVectorStore (knowledge/stores/lancedb_vector_store.py)

    M1 8GB: RAM-gated, falls back to sqlitevec when RAM > 5GB.
    IVF-PQ quantization: HLEDAC_LANCEDB_IVFPQ_NUM_PARTITIONS=64,
                         HLEDAC_LANCEDB_IVFPQ_NUM_SUB_VECTORS=12

    Index: LanceDB ANN with cosine similarity.
    """

    async def upsert_embeddings(
        self, embeddings: list[tuple[str, dict[str, Any]]]
    ) -> None:
        """
        Upsert entity embeddings.

        Args:
            embeddings: list of (entity_id, embedding_vector) tuples
        M1 8GB: batch size 512 (memory-adaptive)
        """
        ...

    async def search_similar(
        self,
        query_embedding: list[float],
        k: int = 10,
        filter: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Search k most similar embeddings.

        Returns list of {entity_id, score, distance} dicts.
        M1 8GB: IVF-PQ quantized (opt-in via HLEDAC_LANCEDB_QUANTIZE=1)
        """
        ...


# -----------------------------------------------------------------------------
# DedupManager Protocol — unified dedup interface
# -----------------------------------------------------------------------------

@runtime_checkable
class DedupManager(Protocol):
    """
    PEP 544 Protocol — unified deduplication interface.

    Integrates HotCacheStore (LMDB) + BloomFilter (RotatingBloomFilter).
    Single interface for persistent + hot dedup lookups.
    """

    def lookup(self, fingerprint: str) -> str | None:
        """Check hot cache first, then persistent LMDB."""
        ...

    def store(self, fingerprint: str, finding_id: str) -> None:
        """Store in hot cache + persistent LMDB + Bloom filter."""
        ...

    def get_stats(self) -> dict[str, Any]:
        """Combined stats from all dedup layers."""
        ...
