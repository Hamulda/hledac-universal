"""knowledge/stores/composite_store.py — Composite Finding Store (F320)

PEP 544 CompositeFindingStore implementation.

Delegates to specialized stores:
- FindingStore (DuckDBFindingStore) — durable canonical writes
- HotCacheStore (LMDBHotCacheStore) — read-through cache
- VectorStore (LanceDBVectorStore) — ANN embeddings (optional)

M1 8GB design:
- Each store has bounded resource budget
- asyncio.to_thread for blocking I/O
- Arrow IPC zero-copy via duckdb_subprocess_writer
"""
from __future__ import annotations


import asyncio
import logging
from dataclasses import dataclass, field
import msgspec
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding
    from knowledge.stores.protocols import (
        HotCacheStore,
        VectorStore,
        FindingFilter,
    )
    # DuckDBShadowStore is the actual implementation during transition
    from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

logger = logging.getLogger(__name__)


@dataclass
class CompositeFindingStore:
    """
    Kompozitni store delegujici na specializovane implementace.

    M1 8GB: kazdy store ma vlastni bounded resource budget.
    Delegation order:
        WRITE: hot_cache (check) → duckdb_store (durable) → vector_store

    M1 8GB invariants:
    - DuckDB: max 2 threads (F265-U5)
    - LMDB: 16 MB map, 5000 entries (F265B)
    - LanceDB: RAM-gated, falls back > 5GB
    """

    # During F320 transition: DuckDBShadowStore directly
    # TODO: Replace with FindingStore protocol once all stores implement it
    duckdb_store: DuckDBShadowStore
    hot_cache: HotCacheStore
    vector_store: VectorStore | None = None

    # Stats
    _stats: dict[str, int] = field(
        default_factory=lambda: {
            "cache_hits": 0,
            "cache_misses": 0,
            "duckdb_writes": 0,
            "vector_upserts": 0,
            "errors": 0,
        },
        repr=False,
    )

    async def append(self, finding: CanonicalFinding) -> None:
        """
        Append single finding via delegation chain.

        Chain: hot_cache (check first) → duckdb_store (durable) →
               vector_store (async embeddings)
        """
        try:
            # 1. Check hot cache first (sync lookup)
            fp = getattr(finding, "fingerprint", None) or str(hash(finding.finding_id))
            cached_id = self.hot_cache.lookup(fp)

            if cached_id is not None:
                self._stats["cache_hits"] += 1
                logger.debug("[Composite] duplicate skipped: %s", fp)
                return

            self._stats["cache_misses"] += 1

            # 2. Write to DuckDB (durable canonical)
            # DuckDBShadowStore uses async_ingest_finding
            await self.duckdb_store.async_ingest_finding(finding)
            self._stats["duckdb_writes"] += 1

            # 3. Update hot cache (sync, non-blocking)
            self.hot_cache.store(fp, finding.finding_id)

            # 4. Upsert embeddings to vector store (fire-and-forget)
            if self.vector_store is not None:
                embedding = getattr(finding, "embedding", None)
                if embedding is not None:
                    asyncio.create_task(
                        self.vector_store.upsert_embeddings([
                            (finding.finding_id, embedding)
                        ])
                    )
                    self._stats["vector_upserts"] += 1

        except Exception as e:
            logger.warning("[Composite] append failed: %s", e)
            self._stats["errors"] += 1

    async def append_batch(
        self, findings: list[CanonicalFinding]
    ) -> list[Any]:
        """
        Batch append with quality gating.

        Returns ActivationResult per finding.
        M1 8GB: chunks of 2048 rows (Arrow batch optimal size).
        """
        # DuckDBShadowStore uses async_ingest_findings_batch
        return await self.duckdb_store.async_ingest_findings_batch(findings)

    async def query_async(self, filter: FindingFilter) -> list[dict[str, Any]]:
        """
        Async query — delegates to DuckDBShadowStore.async_query_recent_findings.

        DuckDBShadowStore exposes async_query_recent_findings(limit) publicly.
        Hot cache already checked at write time.
        """
        return await self.duckdb_store.async_query_recent_findings(
            limit=filter.limit
        )

    def get_stats(self) -> dict[str, Any]:
        """Return composite store statistics."""
        return {
            **self._stats,
            "hot_cache": self.hot_cache.get_stats(),
        }
