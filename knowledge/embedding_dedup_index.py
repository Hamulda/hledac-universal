"""
knowledge/embedding_dedup_index.py — A7: USearch Embedding-Based Dedup for Prelude
================================================================================



Embedding-based near-duplicate detection using usearch + MLX embeddings.
Integrates with existing UnifiedEmbeddingManager (M1 ANE-accelerated).

Architecture:
- usearch.Index with cosine distance for ANN search (M1 Metal SIMD accelerated)
- UnifiedEmbeddingManager.embed_one() for MLX/ANE embeddings
- Bounded in-memory index (no LMDB to avoid blocking canonical path)
- fail-soft: any error → allow through (advisory only, canonical write never blocked)
- Thread-safe via asyncio.Lock for writes

M1 8GB: usearch is C++ HNSW with Metal SIMD — faster + smaller than hnswlib.
  - MAX_INDEX_ENTRIES = 50_000 (~50K × 512d × 4B = ~100 MB max)
  - connectivity=16, expansion_add=14, expansion_search=50 — M1 8GB balanced

GHOST_INVARIANTS:
- fail-safe: all methods return safe defaults on error
- bounded: MAX_INDEX_ENTRIES
- canonical write path NEVER blocked: dedup is advisory only
- always-on: no feature flag

Usage:
    from hledac.universal.knowledge.embedding_dedup_index import (
        EmbeddingDedupIndex,
        get_embedding_dedup_index,
    )
    idx = get_embedding_dedup_index()
    result = await idx.check_duplicate(finding_id, text)
"""


import asyncio
import logging
from dataclasses import dataclass

from typing import cast

from hledac.universal.utils.asyncx import parallel
import msgspec
from compat.msgspec_gc_compat import Struct
from hledac.universal.compat.msgspec_gc_compat import Struct
from typing import TYPE_CHECKING, Any, Final

import numpy as np
from _core import aclose

try:
    from usearch.index import Index as _UsearchIndexClass
    _USEARCH_AVAILABLE = True
except ImportError:
    _UsearchIndexClass: type | None = None  # type: ignore[assignment]
    _USEARCH_AVAILABLE = False

if TYPE_CHECKING:
    from hledac.universal.brain.unified_embedding_manager import UnifiedEmbeddingManager

__all__ = [
    "EmbeddingDedupIndex",
    "get_embedding_dedup_index",
    "DedupResult",
]

logger = logging.getLogger(__name__)

# ── Bounds ─────────────────────────────────────────────────────────────────────

MAX_INDEX_ENTRIES: Final[int] = 50_000  # max USearch entries
MAX_TEXT_EMBED_BYTES: Final[int] = 4096  # max text size for embedding
EMBEDDING_DIM: Final[int] = 512  # Must match UnifiedEmbeddingManager.DEFAULT_DIM
MIN_SIMILARITY_THRESHOLD: Final[float] = 0.92  # cosine similarity threshold
USEARCH_CONNECTIVITY: Final[int] = 16  # neighbors per node (same as hnswlib M=16)
USEARCH_EXPANSION_ADD: Final[int] = 14  # construction expansion
USEARCH_EXPANSION_SEARCH: Final[int] = 50  # search expansion (ef equivalent)
HNSW_SEARCH_K: Final[int] = 5  # number of neighbors to search
MIN_TEXT_LEN: Final[int] = 50  # minimum text length for embedding dedup


# ── Result Types ───────────────────────────────────────────────────────────────


class DedupResult(Struct, frozen=True):
    """Embedding-based dedup advisory result."""
    is_duplicate: bool
    similarity: float  # cosine similarity to nearest neighbor
    nearest_id: str | None  # ID of most similar known finding
    nearest_text: str | None  # Truncated text of nearest (for logging)
    confidence: float  # 0.0-1.0


# ── EmbeddingDedupIndex ───────────────────────────────────────────────────────


class EmbeddingDedupIndex:
    """
    USearch-based embedding near-duplicate detector.

    Uses usearch C++ HNSW with Metal SIMD for ANN search.
    Integrates with UnifiedEmbeddingManager for MLX/ANE embeddings.

    Two-phase:
    1. Embed new text via MLX
    2. Search USearch for k nearest neighbors
    3. If best cosine similarity ≥ MIN_SIMILARITY_THRESHOLD → duplicate

    Storage is in-memory only (no LMDB to avoid blocking canonical path).
    On M1 8GB, 50K × 512d × 4B ≈ 100 MB max.

    Thread-safety: writes guarded by asyncio.Lock.
    """

    __slots__ = (
        "_index", "_texts", "_finding_ids",
        "_int_to_id",  # reverse map: usearch int_id → original finding_id
        "_embedder", "_lock", "_stats",
    )

    def __init__(self) -> None:
        idx: Any = None
        if _USEARCH_AVAILABLE:
            import usearch.index

            idx = usearch.index.Index(
                ndim=EMBEDDING_DIM,
                metric="cos",
                dtype="f32",
                connectivity=USEARCH_CONNECTIVITY,
                expansion_add=USEARCH_EXPANSION_ADD,
                expansion_search=USEARCH_EXPANSION_SEARCH,
    )
        self._index = idx

        self._texts: dict[str, str] = {}  # finding_id → truncated text
        self._finding_ids: list[str] = []  # ordered list for index access
        self._int_to_id: dict[int, str] = {}  # reverse map: usearch int_id → finding_id
        self._embedder: UnifiedEmbeddingManager | None = None
        self._lock = asyncio.Lock()
        self._stats = {
            "checks": 0,
            "duplicates": 0,
            "embed_errors": 0,
            "usearch_errors": 0,
        }

    def _get_embedder(self) -> UnifiedEmbeddingManager:
        """Lazily get UnifiedEmbeddingManager (imports mlx at first use)."""
        if self._embedder is None:
            from hledac.universal.brain.unified_embedding_manager import get_unified_embedder
            self._embedder = get_unified_embedder()
        return self._embedder

    async def _embed_text(self, text: str) -> np.ndarray | None:
        """
        P2-07: Embed text via MLXDispatcher async batching fronta.

        Používá embed_batch s jedním textem — malé batche jdou přes
        AsyncEmbeddingBatcher frontu, která je batchuje s ostatními requesty.
        Výsledek: 10-20× rychlejší než původní per-item embed_one.
        """
        if not text:
            return None
        text = text[:MAX_TEXT_EMBED_BYTES]
        try:
            # P2-07: Použij MLXDispatcher s async batching frontou
            from hledac.universal.brain._mlx_dispatcher import get_mlx_dispatcher
            dispatcher = get_mlx_dispatcher()
            # embed_batch na dispatcher používá AsyncEmbeddingBatcher pro small batches
            embedding: np.ndarray = await dispatcher.embed_batch(text)
            # Normalize for cosine similarity (usearch cos expects normalized)
            norm = np.linalg.norm(embedding)
            if norm > 0:
                embedding = embedding / norm
            return embedding
        except Exception as exc:
            logger.debug("EmbeddingDedupIndex: embed error: %s", exc)
            self._stats["embed_errors"] += 1
            return None

    async def embed_texts_batch(self, texts: list[str]) -> list[np.ndarray | None]:
        """
        P2-07: Batch embed více textů přes async batching frontu.

        Args:
            texts: List textů k embeddedí (max MAX_TEXT_EMBED_BYTES na text).

        Returns:
            List[np.ndarray | None] — embedding pro každý text, nebo None při chybě.
        """
        if not texts:
            return []

        # Ořezat a filtrovat prázdné
        processed: list[tuple[int, str]] = []
        for i, text in enumerate(texts):
            if not text:
                processed.append((i, ""))
            else:
                processed.append((i, text[:MAX_TEXT_EMBED_BYTES]))

        try:
            from hledac.universal.brain._mlx_dispatcher import get_mlx_dispatcher
            dispatcher = get_mlx_dispatcher()
            # Normalizovat texty pro batch
            texts_to_encode = [t for _, t in processed]
            # embed_batch vrací np.ndarray shape (n, dim)
            embeddings: np.ndarray = await dispatcher.embed_batch(texts_to_encode)
            # Normalizovat pro cosine similarity
            norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
            norms = np.where(norms > 0, norms, 1.0)  # Avoid div by zero
            embeddings = embeddings / norms

            # Sestavit výsledky
            results: list[np.ndarray | None] = [None] * len(texts)
            for idx, (_, text) in enumerate(processed):
                if text:
                    results[idx] = embeddings[idx]
            return results
        except Exception as exc:
            logger.debug("EmbeddingDedupIndex: batch embed error: %s", exc)
            self._stats["embed_errors"] += len(processed)
            return [None] * len(texts)

    async def check_duplicate(
        self,
        finding_id: str,
        text: str,
        _metadata: str = "",
    ) -> DedupResult:
        """
        Check if text is near-duplicate of any known finding via embeddings.

        Two-phase:
        1. Embed text via MLX (normalized for cosine)
        2. Search USearch for k nearest neighbors
        3. If best cosine similarity ≥ MIN_SIMILARITY_THRESHOLD → duplicate
        """
        self._stats["checks"] += 1

        if len(text) < MIN_TEXT_LEN:
            return DedupResult(
                is_duplicate=False,
                similarity=0.0,
                nearest_id=None,
                nearest_text=None,
                confidence=0.0,
    )

        try:
            embedding = await self._embed_text(text)
            if embedding is None:
                return DedupResult(
                    is_duplicate=False,
                    similarity=0.0,
                    nearest_id=None,
                    nearest_text=None,
                    confidence=0.0,
    )

            async with self._lock:
                count = len(self._finding_ids)
                if count == 0:
                    await self._add_to_index(finding_id, text, embedding)
                    return DedupResult(
                        is_duplicate=False,
                        similarity=1.0,
                        nearest_id=None,
                        nearest_text=None,
                        confidence=1.0,
    )

                try:
                    if self._index is None:
                        raise RuntimeError("usearch index not available")
                    # USearch returns (key, distance, ) tuples
                    results = self._index.search(
                        embedding.astype(np.float32),
                        count=min(HNSW_SEARCH_K, count),
    )
                except Exception as exc:
                    logger.debug("EmbeddingDedupIndex: USearch search error: %s", exc)
                    self._stats["usearch_errors"] += 1
                    await self._add_to_index(finding_id, text, embedding)
                    return DedupResult(
                        is_duplicate=False,
                        similarity=0.0,
                        nearest_id=None,
                        nearest_text=None,
                        confidence=0.0,
    )

                if not results:
                    await self._add_to_index(finding_id, text, embedding)
                    return DedupResult(
                        is_duplicate=False,
                        similarity=0.0,
                        nearest_id=None,
                        nearest_text=None,
                        confidence=0.0,
    )

                # Best neighbor: results sorted by distance ascending
                # usearch returns Match|Matches; access via getattr for type safety
                best_match = results[0]
                best_key = int(getattr(best_match, "key", 0))
                best_dist = float(getattr(best_match, "distance", 2.0))
                # usearch cosine distance: 0 = identical, 2 = opposite
                # similarity = 1 - distance (maps [0,2] → [1,-1])
                similarity = max(0.0, 1.0 - best_dist / 2.0)

                # usearch key is our hash-based int_id, map back to original finding_id
                best_original_id = self._int_to_id.get(best_key, str(best_key))
                best_text = self._texts.get(best_original_id, "")[:100]

                if similarity >= MIN_SIMILARITY_THRESHOLD:
                    self._stats["duplicates"] += 1
                    return DedupResult(
                        is_duplicate=True,
                        similarity=similarity,
                        nearest_id=best_original_id,
                        nearest_text=best_text,
                        confidence=similarity,
    )

                await self._add_to_index(finding_id, text, embedding)
                return DedupResult(
                    is_duplicate=False,
                    similarity=similarity,
                    nearest_id=best_original_id,
                    nearest_text=best_text,
                    confidence=similarity,
    )

        except Exception as exc:
            logger.debug("EmbeddingDedupIndex: check_duplicate error: %s", exc)
            return DedupResult(
                is_duplicate=False,
                similarity=0.0,
                nearest_id=None,
                nearest_text=None,
                confidence=0.0,
    )

    async def _add_to_index(
        self,
        finding_id: str,
        text: str,
        embedding: np.ndarray,
    ) -> None:
        """Add embedding to USearch index (bounded, FIFO eviction)."""
        count = len(self._finding_ids)
        if count >= MAX_INDEX_ENTRIES:
            evict_count = MAX_INDEX_ENTRIES // 10
            for i in range(min(evict_count, len(self._finding_ids))):
                old_id = self._finding_ids[i]
                self._texts.pop(old_id, None)
                # Remove reverse mapping for evicted entry
                old_int_id = abs(hash(old_id)) % (MAX_INDEX_ENTRIES * 10)
                self._int_to_id.pop(old_int_id, None)
            self._finding_ids = self._finding_ids[evict_count:]

        try:
            if self._index is None:
                return
            int_id = abs(hash(finding_id)) % (MAX_INDEX_ENTRIES * 10)
            self._index.add(
                int_id,
                embedding.astype(np.float32),
    )
            self._texts[finding_id] = text[:MAX_TEXT_EMBED_BYTES]
            self._int_to_id[int_id] = finding_id
            self._finding_ids.append(finding_id)
        except Exception as exc:
            logger.debug("EmbeddingDedupIndex: add error: %s", exc)

    async def check_duplicate_batch(
        self,
        items: list[tuple[str, str, str]],  # (finding_id, text, metadata)
    ) -> list[DedupResult]:
        """Check multiple items for duplicates (parallel, uses async batching fronta)."""
        if not items:
            return []
        # P2-07: Parallel přes gather — lock uvnitř check_duplicate serializuje
        # writes, ale embedding requesty jdou přes async batching frontu
        # ISSUE ASYNC-001: asyncio.gather → parallel() with bounded concurrency
        # Embedding deduplication is I/O-bound (async batching fronta), bounded for M1 8GB
        _raw = await parallel(
            *[self.check_duplicate(finding_id, text, metadata) for finding_id, text, metadata in items],
            policy="log",
            concurrency=8,
    )
        results: list[DedupResult] = cast(list[DedupResult], _raw)
        return list(results)

    def get_stats(self) -> dict[str, int]:
        """Return dedup statistics."""
        return dict(self._stats)

    def reset(self) -> None:
        """Reset the index (for testing)."""
        idx: Any = None
        if _USEARCH_AVAILABLE:
            import usearch.index

            idx = usearch.index.Index(
                ndim=EMBEDDING_DIM,
                metric="cos",
                dtype="f32",
                connectivity=USEARCH_CONNECTIVITY,
                expansion_add=USEARCH_EXPANSION_ADD,
                expansion_search=USEARCH_EXPANSION_SEARCH,
    )
        self._index = idx
        self._texts.clear()
        self._finding_ids.clear()
        self._int_to_id.clear()
        self._stats = {
            "checks": 0,
            "duplicates": 0,
            "embed_errors": 0,
            "usearch_errors": 0,
        }


# ── Module-level singleton ─────────────────────────────────────────────────────

_index: EmbeddingDedupIndex | None = None


def get_embedding_dedup_index() -> EmbeddingDedupIndex:
    """Get the module-level EmbeddingDedupIndex singleton."""
    global _index
    if _index is None:
        _index = EmbeddingDedupIndex()
    return _index
