"""
Hybrid Frequency Sketches for PatternStats
===========================================

MLX-accelerated streaming sketches for bounded-memory frequency estimation:
- Count-Mean-Min sketch for approximate counts
- SpaceSaving heap for exact top-K counts
- LMDB-backed cold storage for rare items

M1 8GB optimized: GPU-speed sketch ops, zero-copy LMDB I/O.
"""

from __future__ import annotations

import hashlib
import heapq
import logging
import pathlib
from collections import OrderedDict

logger = logging.getLogger(__name__)

# Optional MLX
try:
    import mlx.core as mx

    MLX_AVAILABLE = True
except ImportError:
    mx = None
    MLX_AVAILABLE = False

# Optional LMDB
try:
    import lmdb

    LMDB_AVAILABLE = True
except ImportError:
    lmdb = None
    LMDB_AVAILABLE = False


class HybridFrequencySketch:
    """
    Hybrid frequency sketch combining:
    - Count-Mean-Min sketch (MLX-accelerated) for approximate estimates
    - SpaceSaving heap for exact top-K counts
    - LMDB persistent store + LRU cache for rare items
    """

    def __init__(
        self,
        sketch_width: int = 2**16,
        sketch_depth: int = 5,
        top_k: int = 1024,
        lru_size: int = 512,
        lmdb_path: str | None = None,
    ):
        self.width = sketch_width
        self.depth = sketch_depth
        self.top_k = top_k
        self.lru_size = lru_size

        # Count-Mean-Min sketch table (MLX or Python)
        if MLX_AVAILABLE:
            self.table = mx.zeros((sketch_depth, sketch_width), dtype=mx.int32)
        else:
            self.table = [[0] * sketch_width for _ in range(sketch_depth)]

        # SpaceSaving heap (max-heap via negative counts)
        self.heap: list[tuple[int, str]] = []  # (-count, item)
        self.exact_counts: dict[str, int] = {}  # exact counts for items in heap
        self._item_set: set[str] = set()  # Track items in heap for fast lookup

        # LRU cache for recently accessed rare items
        self.lru_cache: Ordereddict[str, int] = OrderedDict()

        # LMDB environment (if available)
        self.lmdb_env = None
        if LMDB_AVAILABLE and lmdb_path:
            try:
                from hledac.universal.paths import open_lmdb
                self.lmdb_env = open_lmdb(
                    pathlib.Path(lmdb_path), map_size=100 * 1024 * 1024  # 100MB
                )
            except Exception as e:
                logger.warning(f"Failed to open LMDB: {e}")
                self.lmdb_env = None

    def _hash(self, item: str, seed: int) -> int:
        """Return sketch index for given seed."""
        h = hashlib.sha256(f"{seed}:{item}".encode()).digest()
        return int.from_bytes(h[:8], "big") % self.width

    def _update_sketch(self, item: str, count: int = 1) -> None:
        """Add count to sketch table (vectorized in MLX).

        Uses mx.arange + mx.at for true vectorization without Python loops.
        """
        if MLX_AVAILABLE:
            # Compute indices for all depths at once
            indices = [self._hash(item, d) for d in range(self.depth)]

            # True vectorized update using mx.arange + mx.at (no Python loop)
            rows = mx.arange(self.depth)  # [0, 1, 2, ...]
            cols = mx.array(indices, dtype=mx.int32)

            # Create update matrix and add in one operation
            updates = mx.zeros((self.depth, self.width), dtype=mx.int32)
            updates = updates.at[rows, cols].add(count)
            self.table = self.table + updates
        else:
            for d in range(self.depth):
                idx = self._hash(item, d)
                self.table[d][idx] += count

    def _update_spacesaving(self, item: str, count: int = 1) -> None:
        """Update exact counts via SpaceSaving algorithm."""
        if item in self.exact_counts:
            # Item already in heap - update count
            old_count = self.exact_counts[item]
            self.exact_counts[item] = old_count + count
            # Push new entry for lazy heap
            heapq.heappush(self.heap, (-(old_count + count), item))
        else:
            # New item - decide whether to insert into heap
            if len(self.heap) < self.top_k:
                # Heap not full - add directly
                self.exact_counts[item] = count
                self._item_set.add(item)
                heapq.heappush(self.heap, (-count, item))
            else:
                # Heap full - compare with smallest count in heap
                smallest_count = -self.heap[0][0]
                if count > smallest_count:
                    # Evict smallest, replace with new
                    _, evicted = heapq.heappop(self.heap)
                    if evicted in self.exact_counts:
                        evicted_count = self.exact_counts.pop(evicted)
                        self._item_set.discard(evicted)
                    else:
                        evicted_count = 0
                    # Move evicted item to cold storage
                    self._store_to_cold(evicted, evicted_count)
                    # Insert new
                    self.exact_counts[item] = count
                    self._item_set.add(item)
                    heapq.heappush(self.heap, (-count, item))
                else:
                    # Not frequent enough - store in cold storage
                    self._store_to_cold(item, count)

    def _store_to_cold(self, item: str, count: int) -> None:
        """Store a rare item in LRU cache or LMDB."""
        if len(self.lru_cache) < self.lru_size:
            if item in self.lru_cache:
                self.lru_cache[item] += count
            else:
                self.lru_cache[item] = count
            # Move to end (most recently used)
            self.lru_cache.move_to_end(item)
        elif self.lmdb_env:
            # LRU full - evict oldest to LMDB
            if self.lru_cache:
                oldest_item, oldest_count = self.lru_cache.popitem(last=False)
                self._persist_to_lmdb(oldest_item, oldest_count)
            # Add new item to cache
            if item in self.lru_cache:
                self.lru_cache[item] += count
            else:
                self.lru_cache[item] = count
            self.lru_cache.move_to_end(item)
        else:
            # No LMDB - just bounded LRU eviction
            if len(self.lru_cache) >= self.lru_size:
                # Simple LRU: remove the first (oldest) entry
                self.lru_cache.popitem(last=False)
            if item in self.lru_cache:
                self.lru_cache[item] += count
            else:
                self.lru_cache[item] = count
            self.lru_cache.move_to_end(item)

    def _persist_to_lmdb(self, item: str, count: int) -> None:
        """Persist an item to LMDB."""
        if self.lmdb_env:
            try:
                with self.lmdb_env.begin(write=True) as txn:
                    txn.put(item.encode(), str(count).encode())
            except Exception as e:
                logger.warning(f"LMDB write failed: {e}")

    def _retrieve_from_cold(self, item: str) -> int | None:
        """Retrieve count from LRU or LMDB."""
        if item in self.lru_cache:
            # Move to end (most recently used)
            self.lru_cache.move_to_end(item)
            return self.lru_cache[item]
        if self.lmdb_env:
            try:
                with self.lmdb_env.begin() as txn:
                    val = txn.get(item.encode())
                    if val:
                        # Put back into LRU cache for faster access next time
                        count = int(val)
                        self.lru_cache[item] = count
                        self.lru_cache.move_to_end(item)
                        return count
            except Exception as e:
                logger.warning(f"LMDB read failed: {e}")
        return None

    def add(self, item: str, count: int = 1) -> None:
        """Increment count for an item."""
        self._update_sketch(item, count)
        self._update_spacesaving(item, count)

    def estimate(self, item: str) -> int:
        """Estimate the count of an item using vectorized MLX operations."""
        # First check exact sources
        if item in self.exact_counts:
            return self.exact_counts[item]
        cold = self._retrieve_from_cold(item)
        if cold is not None:
            return cold

        # Otherwise, use sketch estimate
        if MLX_AVAILABLE:
            # Compute all hash indices at once
            indices = [self._hash(item, d) for d in range(self.depth)]

            # Vectorized access using mx.arange for rows
            rows = mx.arange(self.depth)
            cols = mx.array(indices, dtype=mx.int32)

            # Get values using advanced indexing (vectorized)
            sketch_vals = self.table[rows, cols]
            min_count = int(mx.min(sketch_vals))

            # Noise: adjacent cells (idx + 1) % width
            noise_cols = mx.array([(idx + 1) % self.width for idx in indices], dtype=mx.int32)
            noise_vals = self.table[rows, noise_cols]
            mean_noise = int(mx.mean(noise_vals))
        else:
            vals = [self.table[d][self._hash(item, d)] for d in range(self.depth)]
            min_count = min(vals)
            # Count-Mean-Min: subtract mean of adjacent cells as noise estimate
            noise_vals = [
                self.table[d][(self._hash(item, d) + 1) % self.width]
                for d in range(self.depth)
            ]
            mean_noise = sum(noise_vals) // len(noise_vals)

        return max(0, min_count - mean_noise)

    def get_top_k(self, k: int = 10) -> list[tuple[str, int]]:
        """Get top K items by exact count."""
        # Rebuild heap to get accurate counts (lazy deletion may leave duplicates)
        count_map: dict[str, int] = {}
        for neg_count, item in self.heap:
            count = -neg_count
            if item in count_map:
                count_map[item] = max(count_map[item], count)
            else:
                count_map[item] = count

        # Add items from cold storage that might be in top-K
        for item, count in self.lru_cache.items():
            if item in count_map:
                count_map[item] = max(count_map[item], count)
            else:
                count_map[item] = count
            # Also check LMDB for items not in cache
            if self.lmdb_env and item not in self.lru_cache:
                cold_count = self._retrieve_from_cold(item)
                if cold_count is not None:
                    count_map[item] = max(count_map.get(item, 0), cold_count)

        # Sort by count descending
        sorted_items = sorted(count_map.items(), key=lambda x: x[1], reverse=True)
        return sorted_items[:k]

    def close(self) -> None:
        """Clean up LMDB environment."""
        if self.lmdb_env:
            try:
                self.lmdb_env.close()
            except Exception as e:
                logger.warning(f"LMDB close failed: {e}")
            finally:
                self.lmdb_env = None


# =============================================================================
# Sprint 30: CommVQ 2-bit KV Cache Quantization (MLX-native K-means)
# =============================================================================

def commvq_quantize(cache, bits: int = 2):
    """
    CommVQ 2-bit KV cache quantization (87.5% savings, MLX-native).
    Uses group-wise k-means with 10 iterations (fast on M1 GPU).
    """
    if not MLX_AVAILABLE:
        logger.warning("CommVQ requires MLX, skipping quantization")
        return cache

    try:
        import mlx.core as mx

        # Check if cache is valid bfloat16/float16
        try:
            mx.eval(cache)
            # Handle list of tuples (MLX KV cache format)
            if isinstance(cache, list):
                # Check first element's dtype
                first_elem = cache[0] if cache else None
                if isinstance(first_elem, tuple):
                    first_tensor = first_elem[0]  # k or v
                    dtype = first_tensor.dtype if hasattr(first_tensor, 'dtype') else None
                elif hasattr(first_elem, 'dtype'):
                    dtype = first_elem.dtype
                else:
                    dtype = None

                if dtype is None or dtype not in (mx.bfloat16, mx.float16, mx.float32):
                    logger.warning(f"CommVQ requires bfloat16/float16 cache, got {dtype}")
                    return cache
            elif not hasattr(cache, 'dtype') or cache.dtype not in (mx.bfloat16, mx.float16, mx.float32):
                logger.warning(f"CommVQ requires bfloat16/float16 cache, got {getattr(cache, 'dtype', 'unknown')}")
                return cache
        except Exception as e:
            logger.warning(f"Cannot evaluate cache: {e}")
            return cache

        # Get original shape from first tensor
        if isinstance(cache, list) and cache:
            first_elem = cache[0]
            if isinstance(first_elem, tuple):
                first_tensor = first_elem[0]
                orig_shape = first_tensor.shape if hasattr(first_tensor, 'shape') else None
            elif hasattr(first_elem, 'shape'):
                orig_shape = first_elem.shape
            else:
                orig_shape = None
        else:
            orig_shape = cache.shape if hasattr(cache, 'shape') else None

        if orig_shape is None:
            return cache

        # Flatten: (..., seq_len, hidden) -> (groups, group_size, hidden)
        if isinstance(cache, list):
            # Flatten list of (k, v) tuples
            all_tensors = []
            for item in cache:
                if isinstance(item, tuple):
                    all_tensors.append(item[0])  # k
                    all_tensors.append(item[1])  # v
                else:
                    all_tensors.append(item)
            flat = mx.concatenate([t.reshape(-1) for t in all_tensors])
            flat = flat.reshape(-1, orig_shape[-1])
        else:
            flat = cache.reshape(-1, cache.shape[-1])
        group_size = 1024  # M1 optimal
        n_groups = (flat.shape[0] + group_size - 1) // group_size

        compressed_groups = []
        for i in range(n_groups):
            start_idx = i * group_size
            end_idx = min((i + 1) * group_size, flat.shape[0])
            group = flat[start_idx:end_idx]

            if group.size == 0:
                continue

            n_clusters = 1 << bits  # 4 for 2-bit
            # Initialize centroids randomly from the group
            indices = mx.random.randint(0, group.shape[0], (n_clusters,))
            centroids = group[indices]

            # 10 iterations k-means (MLX-native, runs on GPU)
            for _ in range(10):
                # Compute squared distances: (group_size, n_clusters)
                distances = mx.sum((group[:, None] - centroids[None, :]) ** 2, axis=2)
                assignments = mx.argmin(distances, axis=1)

                # Update centroids
                new_centroids = mx.zeros_like(centroids)
                counts = mx.zeros(n_clusters)
                for k in range(n_clusters):
                    mask = (assignments == k)
                    cnt = mx.sum(mask)
                    counts[k] = cnt
                    if cnt > 0:
                        new_centroids[k] = mx.sum(group * mask[:, None], axis=0) / cnt
                centroids = new_centroids

            # Final assignments
            final_distances = mx.sum((group[:, None] - centroids[None, :]) ** 2, axis=2)
            indices = mx.argmin(final_distances, axis=1)
            compressed_groups.append((centroids, indices))

        logger.info(f"[CommVQ] Compressed {n_groups} groups, 87.5% theoretical savings")
        # Return as tuple for reconstruction during eval
        return ('commvq_compressed', compressed_groups, orig_shape)

    except Exception as e:
        logger.warning(f"CommVQ failed: {e}")
        return cache  # fail-safe


class ExactCounterFallback:
    """Fallback exact counter when MLX and hybrid are unavailable."""

    def __init__(self):
        self._counts: dict[str, int] = {}

    def add(self, item: str, count: int = 1) -> None:
        self._counts[item] = self._counts.get(item, 0) + count

    def estimate(self, item: str) -> int:
        return self._counts.get(item, 0)

    def get_top_k(self, k: int = 10) -> list[tuple[str, int]]:
        sorted_items = sorted(
            self._counts.items(), key=lambda x: x[1], reverse=True
        )
        return sorted_items[:k]

    def close(self) -> None:
        pass
