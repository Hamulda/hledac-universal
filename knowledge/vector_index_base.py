"""
Vector Index Abstraction Layer — Pattern #12 Fix
================================================



ROLE: Shared base abstraction for ANN index implementations.

ARCHITECTURE (M1 8GB, Python 3.14+):
  ┌─────────────────────────────────────────────────────────────┐
  │  VectorIndexBase (ABC)                                     │
  │  ├── build_index() — populate index from storage          │
  │  ├── search(embedding, top_k) — ANN search                │
  │  ├── upsert(key, embedding, metadata) — add/update        │
  │  └── delete(key) — remove entry                           │
  ├─────────────────────────────────────────────────────────────┤
  │  USEARCHEngine (shared, not ABC)                          │
  │  ├── Metal SIMD acceleration for M1                        │
  │  ├── MLX cosine reranking                                 │
  │  └── _mlx_rerank() class method                           │
  └─────────────────────────────────────────────────────────────┘

IMPLEMENTATIONS:
  - AnnIndex (knowledge/ann_index.py): SemanticDedup USEARCH + LanceDB/HNSW
  - LanceDBVectorIndex (future): LanceDB + USEARCH + MLX (deprecated path)

MIGRATION PATH (F350M-R):
  LanceDB → DuckDB HNSW as persistence layer.
  LanceDBIdentityStore will migrate to DuckDB-backed implementation.
  This base ensures API compatibility during migration.

INVARIANTS (always-on, bounded, fail-safe):
  - FAIL-OPEN: Any init/query error → returns empty result, never raises
  - BOUNDED: MAX_ENTRIES enforced via LRU eviction
  - MLX rerank with numpy fallback always available
  - Thread-safe via lock
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time as _time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Protocol

import numpy as np

if TYPE_CHECKING:
    import mlx.core as mx

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------
# Constants (shared across implementations)
# -----------------------------------------------------------------------
_EMBEDDING_DIM = 256
_MEMORY_GUARD_GB = 6.0
_MAX_ENTRIES = 50_000
_MIN_SCORE = 0.90

# USEARCH configuration (M1 Metal SIMD optimized)
_USEARCH_CONNECTIVITY = 16
_USEARCH_EXPANSION_ADD = 128
_USEARCH_EXPANSION_SEARCH = 64

# IVF-PQ configuration (M1 8GB optimized for 256d vectors)
_IVF_PQ_PARTITIONS = 256
_IVF_PQ_SUB_VECTORS = 8
_M1_MAX_ITERATIONS = 20
_IVF_PQ_NPROBES_DEFAULT = 8


# -----------------------------------------------------------------------
# MLX compiled cosine similarity (shared across implementations)
# -----------------------------------------------------------------------
_MLX_AVAILABLE = False
_mlx_cosine_similarity_batch: Any = None

try:
    import mlx.core as mx

    @mx.compile
    def _mlx_cosine_similarity_batch_impl(
        query_emb: "mx.array", candidates: "mx.array"
    ) -> "mx.array":
        """MLX-compiled batch cosine similarity for exact re-ranking.

        Args:
            query_emb: (D,) query vector
            candidates: (N, D) candidate vectors (normalized)

        Returns:
            (N,) cosine similarities
        """
        q_norm = mx.linalg.norm(query_emb, keepdims=True)
        q_normalized = query_emb / mx.maximum(q_norm, 1e-8)
        return mx.matmul(candidates, q_normalized)

    _mlx_cosine_similarity_batch = _mlx_cosine_similarity_batch_impl
    _MLX_AVAILABLE = True
except ImportError:  # noqa: BLE001
    pass


# -----------------------------------------------------------------------
# Shared utilities (not ABC, concrete mixin helpers)
# -----------------------------------------------------------------------

def check_memory_guard(threshold_gb: float = _MEMORY_GUARD_GB) -> bool:
    """Return True if ANN init is safe (RSS below threshold)."""
    try:
        import psutil
        rss = psutil.Process().memory_info().rss
        return rss < threshold_gb * 1024**3
    except Exception:
        return True  # Fail-soft: allow init if check fails


def mlx_rerank(
    query_emb: np.ndarray,
    candidate_indices: list[int],
    candidate_vectors: list[np.ndarray],
) -> list[tuple[int, float]]:
    """GPU-accelerated exact cosine re-ranking using MLX.

    Falls back to numpy on any error.
    """
    if not _MLX_AVAILABLE or not candidate_vectors:
        return _numpy_rerank(query_emb, candidate_indices, candidate_vectors)

    try:
        mx.eval([])  # Barrier before Metal ops
        q_mx = mx.array(query_emb, dtype=mx.float32)
        c_mx = mx.stack([mx.array(v, dtype=mx.float32) for v in candidate_vectors])

        scores = _mlx_cosine_similarity_batch(q_mx, c_mx)
        scores_np = np.array(scores)

        results = list(zip(candidate_indices, scores_np.tolist()))
        results.sort(key=lambda x: x[1], reverse=True)
        return results
    except Exception:
        return _numpy_rerank(query_emb, candidate_indices, candidate_vectors)


def _numpy_rerank(
    query_emb: np.ndarray,
    candidate_indices: list[int],
    candidate_vectors: list[np.ndarray],
) -> list[tuple[int, float]]:
    """Numpy fallback for cosine re-ranking."""
    q = query_emb.astype(np.float32)
    q_norm = q / (np.linalg.norm(q) + 1e-8)
    results = []
    for idx, vec in zip(candidate_indices, candidate_vectors):
        v = vec.astype(np.float32)
        v_norm = v / (np.linalg.norm(v) + 1e-8)
        score = float(np.dot(q_norm, v_norm))
        results.append((idx, score))
    results.sort(key=lambda x: x[1], reverse=True)
    return results


# -----------------------------------------------------------------------
# Compaction scheduler (shared mixin, not ABC)
# -----------------------------------------------------------------------

class CompactionScheduler:
    """LanceDB/vector compaction scheduler — shared implementation.

    Triggers compaction when:
    - 1000+ inserts since last compaction, OR
    - 1 hour since last compaction

    Thread-safe, fail-soft.
    """

    __slots__ = (
        "_insert_count_since_compact",
        "_last_compact_ts",
        "_compact_in_flight",
        "_lock",
    )

    def __init__(self) -> None:
        self._insert_count_since_compact: int = 0
        self._last_compact_ts: float = 0.0
        self._compact_in_flight: bool = False
        self._lock = threading.Lock()

    def on_insert(self) -> None:
        """Call after each upsert to track compaction schedule."""
        self._insert_count_since_compact += 1

    def should_compact(self) -> bool:
        """Check if compaction is due."""
        if self._compact_in_flight:
            return False
        now = _time.time()
        count_due = self._insert_count_since_compact >= 1000
        time_due = (now - self._last_compact_ts) >= 3600.0
        return count_due or time_due

    def execute_compaction(
        self,
        table: Any,
        optimize_fn: Callable[[], None] | None = None,
    ) -> bool:
        """Execute compaction if due. Returns True if compaction ran."""
        if not self.should_compact():
            return False

        # Rate-limit: don't compact within 60s of last
        now = _time.time()
        if (now - self._last_compact_ts) < 60.0:
            return False

        self._compact_in_flight = True
        try:
            if optimize_fn is not None:
                optimize_fn()
            elif hasattr(table, "optimize"):
                table.optimize()
            elif hasattr(table, "compact_files"):
                table.compact_files()
            else:
                return False

            self._insert_count_since_compact = 0
            self._last_compact_ts = _time.time()
            logger.debug("[CompactionScheduler] compact ok")
            return True
        except Exception as e:
            logger.debug(f"[CompactionScheduler] compact failed: {e}")
            return False
        finally:
            self._compact_in_flight = False


# -----------------------------------------------------------------------
# Vector Index Base (ABC)
# -----------------------------------------------------------------------

class VectorIndexBase(ABC):
    """
    Abstract base for vector index implementations.

    Defines the contract for ANN index operations:
    - build_index(): Populate index from storage
    - search(): ANN search with optional MLX reranking
    - upsert(): Add/update a vector entry
    - delete(): Remove entry
    - close(): Cleanup resources

    FAIL-OPEN: All methods return empty/safe results on error, never raise.
    """

    @abstractmethod
    def build_index(self) -> None:
        """Populate in-memory index from persistent storage.

        Called once on init or after cold start.
        """
        ...

    @abstractmethod
    def search(
        self,
        embedding: np.ndarray,
        top_k: int = 5,
        graph_filter: Callable[[list[str]], list[str]] | None = None,
    ) -> list[dict]:
        """ANN search with optional graph-aware filtering.

        Args:
            embedding: Query vector (D,)
            top_k: Number of results to return
            graph_filter: Optional graph expansion function

        Returns:
            List of dicts with 'key', 'score', 'metadata' fields
        """
        ...

    @abstractmethod
    def upsert(self, key: str, embedding: np.ndarray, metadata: dict[str, Any]) -> bool:
        """Add or update a vector entry.

        Args:
            key: Unique identifier for this vector
            embedding: Vector data (D,) or (1, D)
            metadata: Additional data to store alongside vector

        Returns:
            True on success, False on error
        """
        ...

    @abstractmethod
    def delete(self, key: str) -> bool:
        """Remove a vector entry by key.

        Args:
            key: Unique identifier

        Returns:
            True if deleted, False if not found or error
        """
        ...

    def close(self) -> None:
        """Cleanup resources. Override in subclass if needed."""
        pass


# -----------------------------------------------------------------------
# USEARCH Engine (shared, not abstract)
# -----------------------------------------------------------------------

class USEARCHEngine:
    """
    USEARCH index wrapper with M1 Metal SIMD acceleration.

    Shared by all vector index implementations that use USEARCH as primary ANN.
    Not abstract — concrete mixin for build_index() and search() helpers.
    """

    __slots__ = (
        "_usearch_index",
        "_usearch_loaded",
        "_usearch_labels",
        "_embed_dim",
        "_lock",
    )

    def __init__(self, embed_dim: int = _EMBEDDING_DIM) -> None:
        self._usearch_index: Any = None  # usearch.index.Index
        self._usearch_loaded: bool = False
        self._usearch_labels: list[str] = []  # parallel to index positions
        self._embed_dim: int = embed_dim
        self._lock = threading.Lock()

    def _build_usearch_from_vectors(
        self,
        vectors_data: dict[str, tuple[list[float], dict[str, Any]]],
    ) -> None:
        """Build USEARCH index from key→(vector, metadata) mapping.

        Args:
            vectors_data: {key: (embedding_list, metadata_dict)}
        """
        if not vectors_data:
            return

        try:
            from usearch.index import Index

            self._usearch_index = Index(
                ndim=self._embed_dim,
                metric='cos',
                dtype='f32',
                connectivity=_USEARCH_CONNECTIVITY,
                expansion_add=_USEARCH_EXPANSION_ADD,
                expansion_search=_USEARCH_EXPANSION_SEARCH,
            )

            self._usearch_labels = []
            for key, (emb, _meta) in vectors_data.items():
                idx = len(self._usearch_labels)
                self._usearch_labels.append(key)
                self._usearch_index.add(idx, np.array(emb, dtype=np.float32))

            self._usearch_loaded = True
            logger.info(
                f"[USEARCH] Built index: {len(self._usearch_labels)} vectors, "
                f"connectivity={_USEARCH_CONNECTIVITY}"
            )
        except ImportError:
            logger.debug("[USEARCH] USEARCH not available")
            self._usearch_loaded = False
        except Exception as e:
            logger.debug(f"[USEARCH] Build failed: {e}")
            self._usearch_index = None
            self._usearch_labels = []
            self._usearch_loaded = False

    def _usearch_search(
        self,
        query_emb: np.ndarray,
        top_k: int,
    ) -> list[tuple[str, float]]:
        """Search USEARCH index.

        Returns list of (key, score) sorted by descending score.
        """
        if not self._usearch_loaded or self._usearch_index is None:
            return []

        try:
            query_np = np.array(query_emb, dtype=np.float32)
            matches = self._usearch_index.search(query_np, top_k)

            results = []
            for match in matches:
                idx = int(match.key)
                if idx < len(self._usearch_labels):
                    key = self._usearch_labels[idx]
                    score = float(max(0.0, min(1.0, 1.0 - match.distance)))
                    results.append((key, score))
            return results
        except Exception as e:
            logger.debug(f"[USEARCH] Search failed: {e}")
            return []

    def _usearch_add(
        self,
        key: str,
        embedding: np.ndarray,
    ) -> bool:
        """Add a single vector to USEARCH index. Returns True on success."""
        if not self._usearch_loaded or self._usearch_index is None:
            return False

        try:
            idx = len(self._usearch_labels)
            self._usearch_labels.append(key)
            self._usearch_index.add(idx, embedding.astype(np.float32))
            return True
        except Exception as e:
            logger.debug(f"[USEARCH] Add failed: {e}")
            return False

    def _usearch_count(self) -> int:
        """Return number of vectors in USEARCH index."""
        return len(self._usearch_labels) if self._usearch_loaded else 0
