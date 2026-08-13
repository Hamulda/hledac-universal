"""
Semantic Gravity Field — [SILICON]-05
======================================
Detects voids in the semantic embedding space of collected IOCs and suggests



fetch targets to fill those gaps. Prevents the meta-reasoning coordinator
from making blind decisions based solely on keyword matching.

Architecture:
- USearch HNSW index (M1 Metal SIMD) over all collected IOC embeddings
- Inverse nearest-neighbor void detection: for each point, max distance to NN
  reveals semantic emptiness — high NN distance = under-explored region
- FetchDirective generation: centroid of void region + nearest known entity
  provides context for downstream search-query generation

Data flow:
  Sprint pipeline accumulates findings with embeddings
    → SemanticGravityField.add_embedding() / add_embeddings_batch()
    → [every 60s] find_voids() → suggest_fetch_targets()
    → meta_reasoning_coordinator._select_strategy() uses void info
    → acquisition lanes receive FetchDirectives for final sprint minutes

M1 8GB bounds:
- Max 10,000 embeddings × 256d × 4 bytes ≈ 10 MB for vector storage
- HNSW index overhead: ~10 MB
- Void detection: O(n log n) HNSW traversal, ~5ms per scan at max capacity
- Sample cap: 2000 points per find_voids() call (sub-linear scaling)
- Refresh rate: void detection at most once per 60s

Fail-soft: every method returns safe defaults on error (empty lists, zeros).
Never raises — the reasoning coordinator must continue even without gravity.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────
_EMBEDDING_DIM: int = 256  # Must match embedding_pipeline._EMBEDDING_DIM
_MAX_EMBEDDINGS: int = 10_000  # M1 8GB hard cap — FIFO eviction beyond this
_VOID_SAMPLE_CAP: int = 2000  # Max points scanned per find_voids() call
_VOID_REFRESH_INTERVAL_S: float = 60.0  # Minimum seconds between void scans
_VOID_MIN_POINTS: int = 10  # Minimum points before void detection is meaningful

# HNSW parameters (M1 Metal SIMD optimized)
_HNSW_CONNECTIVITY: int = 16
_HNSW_EXPANSION_ADD: int = 128
_HNSW_EXPANSION_SEARCH: int = 64


# ── Data types ───────────────────────────────────────────────────────────────

@dataclass(slots=True)
class VoidRegion:
    """A semantic void — an under-explored region in embedding space.

    Detected via inverse nearest-neighbor: a point whose nearest neighbor
    is far away sits in a low-density region of the semantic space.
    """

    centroid: np.ndarray  # (256,) float32 — center of the void
    radius: float  # distance to nearest known entity
    nearest_entity_id: str  # ID of the closest known entity
    nearest_distance: float  # cosine distance to that entity
    density_estimate: float  # 0.0 = empty, 1.0 = fully dense

    def to_dict(self) -> dict[str, Any]:
        return {
            'radius': float(self.radius),
            'nearest_entity_id': self.nearest_entity_id,
            'nearest_distance': float(self.nearest_distance),
            'density_estimate': float(self.density_estimate),
        }


@dataclass(slots=True)
class FetchDirective:
    """A suggested fetch target to fill a semantic void.

    These directives flow to the acquisition lanes, which translate them
    into concrete search queries and fetch targets.
    """

    query_hint: str  # Human-readable hint for query generation
    centroid: np.ndarray  # (256,) embedding of void centroid
    void_score: float  # Higher = more important gap to fill
    nearest_entity_id: str  # Closest known entity for contextual grounding
    nearest_distance: float  # Cosine distance to nearest entity

    def to_dict(self) -> dict[str, Any]:
        return {
            'query_hint': self.query_hint,
            'void_score': float(self.void_score),
            'nearest_entity_id': self.nearest_entity_id,
            'nearest_distance': float(self.nearest_distance),
        }


# ── SemanticGravityField ─────────────────────────────────────────────────────

class SemanticGravityField:
    """HNSW-based semantic density field for void detection and fetch targeting.

    Maintains a USearch HNSW index over all collected IOC embeddings.
    ``find_voids()`` uses inverse nearest-neighbor to locate under-explored
    regions. ``suggest_fetch_targets()`` translates voids into actionable
    directives for the acquisition pipeline.

    Thread-safety: NOT thread-safe. All mutations must happen from the same
    event-loop thread. Callers are responsible for serialization.

    M1 8GB safe:
    - Hard cap at _MAX_EMBEDDINGS (10K) with FIFO eviction
    - Void scan samples at most _VOID_SAMPLE_CAP (2000) points
    - refresh interval enforced to avoid thrashing
    - Lazy usearch import — no cost if never used
    """

    __slots__ = (
        '_dim',
        '_max_embeddings',
        '_index',          # usearch.index.Index | None
        '_embeddings',     # np.ndarray (N, dim) float32 — preallocated ring buffer
        '_ids',            # list[str] — parallel to _embeddings
        '_count',          # int — current number of stored vectors
        '_write_pos',      # int — next write position in ring buffer
        '_last_void_scan', # float — monotonic timestamp of last find_voids()
        '_cached_voids',   # list[VoidRegion] | None — cached from last scan
        '_stats',          # dict — internal counters
    )

    def __init__(self, dim: int = _EMBEDDING_DIM, max_embeddings: int = _MAX_EMBEDDINGS) -> None:
        self._dim = dim
        self._max_embeddings = min(max_embeddings, _MAX_EMBEDDINGS)
        self._index = None
        self._embeddings = np.zeros((self._max_embeddings, dim), dtype=np.float32)
        self._ids: list[str] = [''] * self._max_embeddings  # preallocated ring buffer
        self._count = 0
        self._write_pos = 0
        self._last_void_scan: float = 0.0
        self._cached_voids: list[VoidRegion] | None = None
        self._stats: dict[str, int] = {
            'added': 0,
            'evicted': 0,
            'void_scans': 0,
        }

    # ── Lazy index init ──────────────────────────────────────────────────

    def _ensure_index(self) -> bool:
        """Lazy-init the USearch HNSW index. Returns True on success."""
        if self._index is not None:
            return True
        try:
            import usearch.index
            self._index = usearch.index.Index(
                ndim=self._dim,
                metric='cos',
                dtype='f32',
                connectivity=_HNSW_CONNECTIVITY,
                expansion_add=_HNSW_EXPANSION_ADD,
                expansion_search=_HNSW_EXPANSION_SEARCH,
            )
            # Re-add existing embeddings to the new index
            for i in range(self._count):
                idx = (self._write_pos - self._count + i) % self._max_embeddings
                self._index.add(i, self._embeddings[idx])
            logger.debug(
                '[gravity] USearch index initialized: dim=%d connectivity=%d max=%d',
                self._dim, _HNSW_CONNECTIVITY, self._max_embeddings,
            )
            return True
        except ImportError:
            logger.debug('[gravity] usearch not available — gravity field disabled')
            return False
        except Exception as e:
            logger.warning('[gravity] USearch init failed: %s', e)
            return False

    # ── Embedding ingestion ──────────────────────────────────────────────

    def add_embedding(self, entity_id: str, vec: np.ndarray) -> None:
        """Add a single embedding to the gravity field.

        Args:
            entity_id: Unique identifier (finding_key, IOC key, etc.)
            vec: (256,) float32 embedding vector
        """
        if vec.ndim != 1 or len(vec) != self._dim:
            logger.debug('[gravity] add_embedding: wrong dim %s', getattr(vec, 'shape', '?'))
            return

        # Normalize for cosine distance
        norm = np.linalg.norm(vec) + 1e-8
        normalized = (vec / norm).astype(np.float32)

        # Ring buffer write — physical position wraps at max_embeddings
        pos = self._write_pos % self._max_embeddings
        self._embeddings[pos] = normalized
        self._ids[pos] = entity_id

        if self._count < self._max_embeddings:
            self._count += 1
        else:
            self._stats['evicted'] += 1

        # Add to HNSW index
        if self._ensure_index():
            try:
                internal_id = self._write_pos % self._max_embeddings
                self._index.add(internal_id, normalized)
            except Exception as e:
                logger.debug('[gravity] HNSW add failed: %s', e)

        self._write_pos += 1
        self._stats['added'] += 1
        self._cached_voids = None  # invalidate cache

    def add_embeddings_batch(self, ids: list[str], vecs: np.ndarray) -> None:
        """Add a batch of embeddings to the gravity field.

        Args:
            ids: List of unique identifiers
            vecs: (N, 256) float32 embedding matrix
        """
        if vecs.ndim != 2 or vecs.shape[1] != self._dim:
            logger.debug('[gravity] add_embeddings_batch: wrong shape %s', getattr(vecs, 'shape', '?'))
            return
        if len(ids) != vecs.shape[0]:
            logger.debug('[gravity] add_embeddings_batch: id/vec count mismatch')
            return

        # Normalize
        norms = np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-8
        normalized = (vecs / norms).astype(np.float32)

        index_ready = self._ensure_index()

        for i in range(len(ids)):
            pos = self._write_pos % self._max_embeddings
            self._embeddings[pos] = normalized[i]
            self._ids[pos] = ids[i]

            if self._count < self._max_embeddings:
                self._count += 1
            else:
                self._stats['evicted'] += 1

            if index_ready and self._index is not None:
                try:
                    self._index.add(self._write_pos % self._max_embeddings, normalized[i])
                except Exception:  # noqa: BLE001
                    pass

            self._write_pos += 1

        self._stats['added'] += len(ids)
        self._cached_voids = None

    # ── Void detection ───────────────────────────────────────────────────

    def find_voids(self, k: int = 5, min_distance: float = 0.30) -> list[VoidRegion]:
        """Find the top-k semantic voids in the embedding space.

        Uses inverse nearest-neighbor: for each sampled point, computes the
        distance to its nearest neighbor. Points with large NN distance sit
        in low-density (under-explored) regions — the "semantic voids."

        Results are cached for _VOID_REFRESH_INTERVAL_S seconds.

        Args:
            k: Number of voids to return.
            min_distance: Minimum NN distance to consider a region a void
                          (cosine distance, range [0, 2]).

        Returns:
            List of VoidRegion sorted by radius descending (emptiest first).
            Empty list if not enough points or on error.
        """
        if self._count < _VOID_MIN_POINTS:
            return []

        # Check cache
        now = time.monotonic()
        if (
            self._cached_voids is not None
            and (now - self._last_void_scan) < _VOID_REFRESH_INTERVAL_S
        ):
            # Return from cache, re-filtering by min_distance
            return [v for v in self._cached_voids if v.radius >= min_distance][:k]

        if not self._ensure_index():
            return []

        try:
            # Sample points for void detection (sub-linear scan)
            sample_size = min(self._count, _VOID_SAMPLE_CAP)
            if sample_size < _VOID_MIN_POINTS:
                return []

            # Pick evenly-spaced indices across the ring buffer
            step = self._count // sample_size
            sample_indices = []
            for i in range(sample_size):
                ring_idx = (self._write_pos - self._count + i * step) % self._max_embeddings
                sample_indices.append(ring_idx)

            # For each sampled point, find its 2-NN (first hit is self)
            voids_raw: list[tuple[int, float, int, float]] = []  # (ring_idx, nn_dist, nn_ring_idx, nn_dist)
            for ring_idx in sample_indices:
                vec = self._embeddings[ring_idx]
                results = self._index.search(vec.astype(np.float32), 2)
                if len(results) >= 2:
                    # results[0] is the query point itself (distance ≈ 0)
                    nn_dist = float(getattr(results[1], 'distance', 2.0))
                    nn_key = int(getattr(results[1], 'key', 0))
                    if nn_dist >= min_distance:
                        voids_raw.append((ring_idx, nn_dist, nn_key, nn_dist))

            # Sort by radius descending — emptiest voids first
            voids_raw.sort(key=lambda x: x[1], reverse=True)
            voids_raw = voids_raw[:k]

            # Build VoidRegion objects
            voids: list[VoidRegion] = []
            for ring_idx, radius, nn_ring_idx, nn_dist in voids_raw:
                # Nearest entity: use the ring buffer position directly
                nn_pos = nn_ring_idx % self._max_embeddings
                nn_entity_id = self._ids[nn_pos] if nn_pos < len(self._ids) else ''

                # Density estimate: 1.0 - normalized radius (max cosine distance is 2.0)
                density = max(0.0, min(1.0, 1.0 - radius / 2.0))

                voids.append(VoidRegion(
                    centroid=self._embeddings[ring_idx].copy(),
                    radius=radius,
                    nearest_entity_id=nn_entity_id,
                    nearest_distance=nn_dist,
                    density_estimate=density,
                ))

            # Update cache
            self._cached_voids = voids
            self._last_void_scan = now
            self._stats['void_scans'] += 1

            logger.debug(
                '[gravity] find_voids: scanned=%d found=%d top_radius=%.3f',
                sample_size, len(voids),
                voids[0].radius if voids else 0.0,
            )
            return voids

        except Exception as e:
            logger.debug('[gravity] find_voids failed: %s', e)
            return []

    # ── Fetch directive generation ───────────────────────────────────────

    def suggest_fetch_targets(self, n: int = 3) -> list[FetchDirective]:
        """Generate actionable fetch directives from detected semantic voids.

        Each directive represents an under-explored region that the acquisition
        pipeline should target. The ``query_hint`` and ``nearest_entity_id``
        together provide enough context for downstream query generation.

        Args:
            n: Maximum number of directives to return.

        Returns:
            List of FetchDirective sorted by void_score descending.
            Empty list if no voids detected or on error.
        """
        voids = self.find_voids(k=max(n, 5))
        if not voids:
            return []

        directives: list[FetchDirective] = []
        for i, void in enumerate(voids[:n]):
            # Score: higher radius = more important, normalized to [0, 1]
            void_score = min(1.0, void.radius / 1.5)

            # Generate query hint from nearest entity
            if void.nearest_entity_id:
                hint = (
                    f'Explore region semantically distant from "{void.nearest_entity_id[:60]}" '
                    f'(gap radius: {void.radius:.2f})'
                )
            else:
                hint = f'Explore semantic void region (radius: {void.radius:.2f})'

            directives.append(FetchDirective(
                query_hint=hint,
                centroid=void.centroid.copy(),
                void_score=void_score,
                nearest_entity_id=void.nearest_entity_id,
                nearest_distance=void.nearest_distance,
            ))

        return directives

    # ── Stats & introspection ────────────────────────────────────────────

    def get_stats(self) -> dict[str, Any]:
        """Return internal statistics for monitoring."""
        return {
            **self._stats,
            'count': self._count,
            'max_embeddings': self._max_embeddings,
            'dim': self._dim,
            'index_ready': self._index is not None,
            'last_void_scan_age_s': (
                time.monotonic() - self._last_void_scan
                if self._last_void_scan > 0
                else float('inf')
            ),
            'cached_voids': len(self._cached_voids) if self._cached_voids else 0,
        }

    @property
    def count(self) -> int:
        """Number of embeddings currently stored."""
        return self._count

    @property
    def is_ready(self) -> bool:
        """Whether the field has enough data for meaningful void detection."""
        return self._count >= _VOID_MIN_POINTS and self._index is not None

    def clear(self) -> None:
        """Reset the gravity field — frees all memory."""
        self._index = None
        self._embeddings = np.zeros((self._max_embeddings, self._dim), dtype=np.float32)
        self._ids = [''] * self._max_embeddings
        self._count = 0
        self._write_pos = 0
        self._last_void_scan = 0.0
        self._cached_voids = None
        self._stats = {'added': 0, 'evicted': 0, 'void_scans': 0}
