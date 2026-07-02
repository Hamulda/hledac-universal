"""
knowledge/embedding_dedup_index.py — A7: HNSW Embedding-Based Dedup for Prelude
================================================================================

Embedding-based near-duplicate detection using hnswlib + MLX embeddings.
Integrates with existing UnifiedEmbeddingManager (M1 ANE-accelerated).

Architecture:
- _hnswlib.Index with cosine distance for ANN search
- UnifiedEmbeddingManager.embed_one() for MLX/ANE embeddings
- Bounded in-memory index (no LMDB to avoid blocking canonical path)
- fail-soft: any error → allow through (advisory only, canonical write never blocked)
- Thread-safe via asyncio.Lock for writes

M1 8GB: hnswlib is C++ HNSW — ~10× faster than datasketch pure-Python HNSW.
  - MAX_INDEX_ENTRIES = 50_000 (~50K × 512d × 4B = ~100 MB max)
  - ef=100, M=16 balanced for M1 8GB

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

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import numpy as np

import hnswlib as _hnswlib  # type: ignore[attr-defined]
_HNSWLIB_AVAILABLE = True

if TYPE_CHECKING:
    from brain.unified_embedding_manager import UnifiedEmbeddingManager

__all__ = [
    "EmbeddingDedupIndex",
    "get_embedding_dedup_index",
    "DedupResult",
]

logger = logging.getLogger(__name__)

# ── Bounds ─────────────────────────────────────────────────────────────────────

MAX_INDEX_ENTRIES: Final[int] = 50_000  # max HNSW entries
MAX_TEXT_EMBED_BYTES: Final[int] = 4096  # max text size for embedding
EMBEDDING_DIM: Final[int] = 512  # Must match UnifiedEmbeddingManager.DEFAULT_DIM
MIN_SIMILARITY_THRESHOLD: Final[float] = 0.92  # cosine similarity threshold
HNSW_M: Final[int] = 16  # neighbors per node (C++ HNSW m=16 is standard)
HNSW_EF_CONSTRUCTION: Final[int] = 100  # construction quality/speed
HNSW_SEARCH_K: Final[int] = 5  # number of neighbors to search
HNSW_SEARCH_EF: Final[int] = 64  # search-time ef (speed/accuracy tradeoff)
MIN_TEXT_LEN: Final[int] = 50  # minimum text length for embedding dedup


# ── Result Types ───────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DedupResult:
    """Embedding-based dedup advisory result."""
    is_duplicate: bool
    similarity: float  # cosine similarity to nearest neighbor
    nearest_id: str | None  # ID of most similar known finding
    nearest_text: str | None  # Truncated text of nearest (for logging)
    confidence: float  # 0.0-1.0


# ── EmbeddingDedupIndex ───────────────────────────────────────────────────────


class EmbeddingDedupIndex:
    """
    HNSW-based embedding near-duplicate detector.

    Uses hnswlib C++ HNSW with cosine distance for ANN search.
    Integrates with UnifiedEmbeddingManager for MLX/ANE embeddings.

    Two-phase:
    1. Embed new text via MLX
    2. Search HNSW for k nearest neighbors
    3. If best cosine similarity ≥ MIN_SIMILARITY_THRESHOLD → duplicate

    Storage is in-memory only (no LMDB to avoid blocking canonical path).
    On M1 8GB, 50K × 512d × 4B ≈ 100 MB max.

    Thread-safety: writes guarded by asyncio.Lock.
    """

    __slots__ = (
        "_index", "_texts", "_finding_ids",
        "_embedder", "_lock", "_stats",
    )

    def __init__(self) -> None:
        self._index: "_hnswlib.Index" = _hnswlib.Index(  # type: ignore[attr-defined]
            space="cosine",
            dim=EMBEDDING_DIM,
        )
        self._index.init_index(
            max_elements=MAX_INDEX_ENTRIES,
            ef_construction=HNSW_EF_CONSTRUCTION,
            M=HNSW_M,
        )
        self._index.set_ef(HNSW_SEARCH_EF)
        self._index.set_num_threads(2)  # C++ thread pool, M1 friendly

        self._texts: dict[str, str] = {}  # finding_id → truncated text
        self._finding_ids: list[str] = []  # ordered list for index access
        self._embedder: UnifiedEmbeddingManager | None = None
        self._lock = asyncio.Lock()
        self._stats = {
            "checks": 0,
            "duplicates": 0,
            "embed_errors": 0,
            "hnsw_errors": 0,
        }

    def _get_embedder(self) -> UnifiedEmbeddingManager:
        """Lazily get UnifiedEmbeddingManager (imports mlx at first use)."""
        if self._embedder is None:
            from brain.unified_embedding_manager import get_unified_embedder
            self._embedder = get_unified_embedder()
        return self._embedder

    async def _embed_text(self, text: str) -> np.ndarray | None:
        """Embed text via MLX (blocking, runs in executor)."""
        if not text:
            return None
        # Truncate to max embed bytes
        text = text[:MAX_TEXT_EMBED_BYTES]
        try:
            embedder = self._get_embedder()
            loop = asyncio.get_running_loop()
            # embed_one returns list[float]; convert to numpy array
            embedding: list[float] = await loop.run_in_executor(
                None,  # use default thread pool
                lambda: embedder.embed_one(text),
            )
            arr = np.array(embedding, dtype=np.float32)
            # Normalize for cosine similarity (hnswlib cosine expects normalized)
            norm = np.linalg.norm(arr)
            if norm > 0:
                arr = arr / norm
            return arr
        except Exception as exc:
            logger.debug("EmbeddingDedupIndex: embed error: %s", exc)
            self._stats["embed_errors"] += 1
            return None

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
        2. Search HNSW for k nearest neighbors
        3. If best cosine similarity ≥ MIN_SIMILARITY_THRESHOLD → duplicate

        Args:
            finding_id: Unique ID of the new finding
            text: Primary text content
            _metadata: Ignored (SimHash/MinHash handle secondary text)

        Returns:
            DedupResult with is_duplicate, similarity, nearest_id
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
            # Phase 1: embed
            embedding = await self._embed_text(text)
            if embedding is None:
                return DedupResult(
                    is_duplicate=False,
                    similarity=0.0,
                    nearest_id=None,
                    nearest_text=None,
                    confidence=0.0,
                )

            # Phase 2: HNSW search
            async with self._lock:
                count = self._index.element_count()
                if count == 0:
                    # First entry — not a duplicate
                    await self._add_to_index(finding_id, text, embedding)
                    return DedupResult(
                        is_duplicate=False,
                        similarity=1.0,
                        nearest_id=None,
                        nearest_text=None,
                        confidence=1.0,
                    )

                try:
                    # Search HNSW for k nearest
                    labels_arr, distances_arr = self._index.knn_query(
                        embedding.reshape(1, -1),
                        k=min(HNSW_SEARCH_K, count),
                    )
                except Exception as exc:
                    logger.debug("EmbeddingDedupIndex: HNSW search error: %s", exc)
                    self._stats["hnsw_errors"] += 1
                    await self._add_to_index(finding_id, text, embedding)
                    return DedupResult(
                        is_duplicate=False,
                        similarity=0.0,
                        nearest_id=None,
                        nearest_text=None,
                        confidence=0.0,
                    )

                if labels_arr.size == 0:
                    await self._add_to_index(finding_id, text, embedding)
                    return DedupResult(
                        is_duplicate=False,
                        similarity=0.0,
                        nearest_id=None,
                        nearest_text=None,
                        confidence=0.0,
                    )

                # Best neighbor
                best_id_str = str(labels_arr[0][0])
                best_dist = float(distances_arr[0][0])
                # hnswlib cosine distance: 0 = identical, 2 = opposite
                # Convert to similarity: 1 - distance/2 maps [0,2] → [1,-1], normalize to [1,0]
                similarity = max(0.0, 1.0 - best_dist)

                best_text = self._texts.get(best_id_str, "")[:100]

                if similarity >= MIN_SIMILARITY_THRESHOLD:
                    self._stats["duplicates"] += 1
                    return DedupResult(
                        is_duplicate=True,
                        similarity=similarity,
                        nearest_id=best_id_str,
                        nearest_text=best_text,
                        confidence=similarity,
                    )

                # Not a duplicate: add to index
                await self._add_to_index(finding_id, text, embedding)
                return DedupResult(
                    is_duplicate=False,
                    similarity=similarity,
                    nearest_id=best_id_str,
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
        """Add embedding to HNSW index (bounded)."""
        count = self._index.element_count()
        # Evict oldest when at capacity
        if count >= MAX_INDEX_ENTRIES:
            evict_count = MAX_INDEX_ENTRIES // 10
            for i in range(min(evict_count, len(self._finding_ids))):
                old_id = self._finding_ids[i]
                try:
                    self._index.mark_deleted(old_id)
                except Exception:
                    pass
                self._texts.pop(old_id, None)
            self._finding_ids = self._finding_ids[evict_count:]

        try:
            int_id = abs(hash(finding_id)) % (MAX_INDEX_ENTRIES * 10)
            self._index.add_items(
                embedding.reshape(1, -1),
                ids=np.array([int_id], dtype=np.int64),
            )
            self._texts[finding_id] = text[:MAX_TEXT_EMBED_BYTES]
            self._finding_ids.append(finding_id)
        except Exception as exc:
            logger.debug("EmbeddingDedupIndex: add error: %s", exc)

    async def check_duplicate_batch(
        self,
        items: list[tuple[str, str, str]],  # (finding_id, text, metadata)
    ) -> list[DedupResult]:
        """
        Check multiple items for duplicates.

        Args:
            items: List of (finding_id, text, metadata)

        Returns:
            List of DedupResult, one per item.
        """
        results: list[DedupResult] = []
        for finding_id, text, metadata in items:
            result = await self.check_duplicate(finding_id, text, metadata)
            results.append(result)
        return results

    def get_stats(self) -> dict[str, int]:
        """Return dedup statistics."""
        return dict(self._stats)

    def reset(self) -> None:
        """Reset the index (for testing)."""
        self._index = _hnswlib.Index(space="cosine", dim=EMBEDDING_DIM)
        self._index.init_index(
            max_elements=MAX_INDEX_ENTRIES,
            ef_construction=HNSW_EF_CONSTRUCTION,
            M=HNSW_M,
        )
        self._index.set_ef(HNSW_SEARCH_EF)
        self._index.set_num_threads(2)
        self._texts.clear()
        self._finding_ids.clear()
        self._stats = {
            "checks": 0,
            "duplicates": 0,
            "embed_errors": 0,
            "hnsw_errors": 0,
        }


# ── Module-level singleton ─────────────────────────────────────────────────────

_index: EmbeddingDedupIndex | None = None


def get_embedding_dedup_index() -> EmbeddingDedupIndex:
    """Get the module-level EmbeddingDedupIndex singleton."""
    global _index
    if _index is None:
        _index = EmbeddingDedupIndex()
    return _index
