"""
Hybrid Frequency Sketches for PatternStats
===========================================



MLX-accelerated streaming sketches for bounded-memory frequency estimation:
- Count-Mean-Min sketch for approximate counts
- SpaceSaving heap for exact top-K counts
- LMDB-backed cold storage for rare items

M1 8GB optimized: GPU-speed sketch ops, zero-copy LMDB I/O.
"""
import hashlib
import heapq
import logging
import pathlib
from collections import OrderedDict
from typing import Any
logger = logging.getLogger(__name__)

# C1-X FIX: Import MLX_AVAILABLE from SSOT (zero-import detection)
from hledac.universal.utils.mlx_memory import MLX_AVAILABLE
from _core import aclose

try:
    import mlx.core as mx
except ImportError:
    mx = None

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
    __slots__ = tuple(('_item_set', 'depth', 'exact_counts', 'heap', 'lmdb_env', 'lru_cache', 'lru_size', 'table', 'top_k', 'width'))

    def __init__(self, sketch_width: int=2 ** 16, sketch_depth: int=5, top_k: int=1024, lru_size: int=512, lmdb_path: str | None=None):
        self.width = sketch_width
        self.depth = sketch_depth
        self.top_k = top_k
        self.lru_size = lru_size
        if MLX_AVAILABLE:
            self.table = mx.zeros((sketch_depth, sketch_width), dtype=mx.int32)
        else:
            self.table = [[0] * sketch_width for _ in range(sketch_depth)]
        self.heap: list[tuple[int, str]] = []
        self.exact_counts: dict[str, int] = {}
        self._item_set: set[str] = set()
        self.lru_cache: OrderedDict[str, int] = OrderedDict()
        self.lmdb_env = None
        if LMDB_AVAILABLE and lmdb_path:
            try:
                from hledac.universal.paths import open_lmdb
                self.lmdb_env = open_lmdb(pathlib.Path(lmdb_path), map_size=100 * 1024 * 1024)
            except Exception as e:
                logger.warning(f'Failed to open LMDB: {e}')
                self.lmdb_env = None

    def _hash(self, item: str, seed: int) -> int:
        """Return sketch index for given seed."""
        h = hashlib.sha256(f'{seed}:{item}'.encode()).digest()
        return int.from_bytes(h[:8], 'big') % self.width

    def _update_sketch(self, item: str, count: int=1) -> None:
        """Add count to sketch table (vectorized in MLX).

        Uses mx.arange + mx.at for true vectorization without Python loops.
        """
        if MLX_AVAILABLE:
            indices = [self._hash(item, d) for d in range(self.depth)]
            rows = mx.arange(self.depth)
            cols = mx.array(indices, dtype=mx.int32)
            updates = mx.zeros((self.depth, self.width), dtype=mx.int32)
            updates = updates.at[rows, cols].add(count)
            self.table = self.table + updates
        else:
            for d in range(self.depth):
                idx = self._hash(item, d)
                self.table[d][idx] += count

    def _update_spacesaving(self, item: str, count: int=1) -> None:
        """Update exact counts via SpaceSaving algorithm."""
        if item in self.exact_counts:
            old_count = self.exact_counts[item]
            self.exact_counts[item] = old_count + count
            heapq.heappush(self.heap, (-(old_count + count), item))
        elif len(self.heap) < self.top_k:
            self.exact_counts[item] = count
            self._item_set.add(item)
            heapq.heappush(self.heap, (-count, item))
        else:
            smallest_count = -self.heap[0][0]
            if count > smallest_count:
                _, evicted = heapq.heappop(self.heap)
                if evicted in self.exact_counts:
                    evicted_count = self.exact_counts.pop(evicted)
                    self._item_set.discard(evicted)
                else:
                    evicted_count = 0
                self._store_to_cold(evicted, evicted_count)
                self.exact_counts[item] = count
                self._item_set.add(item)
                heapq.heappush(self.heap, (-count, item))
            else:
                self._store_to_cold(item, count)

    def _store_to_cold(self, item: str, count: int) -> None:
        """Store a rare item in LRU cache or LMDB."""
        if len(self.lru_cache) < self.lru_size:
            if item in self.lru_cache:
                self.lru_cache[item] += count
            else:
                self.lru_cache[item] = count
            self.lru_cache.move_to_end(item)
        elif self.lmdb_env:
            if self.lru_cache:
                oldest_item, oldest_count = self.lru_cache.popitem(last=False)
                self._persist_to_lmdb(oldest_item, oldest_count)
            if item in self.lru_cache:
                self.lru_cache[item] += count
            else:
                self.lru_cache[item] = count
            self.lru_cache.move_to_end(item)
        else:
            if len(self.lru_cache) >= self.lru_size:
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
                logger.warning(f'LMDB write failed: {e}')

    def _retrieve_from_cold(self, item: str) -> int | None:
        """Retrieve count from LRU or LMDB."""
        if item in self.lru_cache:
            self.lru_cache.move_to_end(item)
            return self.lru_cache[item]
        if self.lmdb_env:
            try:
                with self.lmdb_env.begin() as txn:
                    val = txn.get(item.encode())
                    if val:
                        count = int(val)
                        self.lru_cache[item] = count
                        self.lru_cache.move_to_end(item)
                        return count
            except Exception as e:
                logger.warning(f'LMDB read failed: {e}')
        return None

    def add(self, item: str, count: int=1) -> None:
        """Increment count for an item."""
        self._update_sketch(item, count)
        self._update_spacesaving(item, count)

    def estimate(self, item: str) -> int:
        """Estimate the count of an item using vectorized MLX operations."""
        if item in self.exact_counts:
            return self.exact_counts[item]
        cold = self._retrieve_from_cold(item)
        if cold is not None:
            return cold
        if MLX_AVAILABLE:
            indices = [self._hash(item, d) for d in range(self.depth)]
            rows = mx.arange(self.depth)
            cols = mx.array(indices, dtype=mx.int32)
            sketch_vals: Any = self.table[rows, cols]
            min_count = int(mx.min(sketch_vals))
            noise_cols = mx.array([(idx + 1) % self.width for idx in indices], dtype=mx.int32)
            noise_vals: Any = self.table[rows, noise_cols]
            mean_noise = int(mx.mean(noise_vals))
        else:
            vals = [self.table[d][self._hash(item, d)] for d in range(self.depth)]
            min_count = min(vals)
            noise_vals = [self.table[d][(self._hash(item, d) + 1) % self.width] for d in range(self.depth)]
            mean_noise = sum(noise_vals) // len(noise_vals)
        return max(0, int(min_count) - int(mean_noise))

    def get_top_k(self, k: int=10) -> list[tuple[str, int]]:
        """Get top K items by exact count."""
        count_map: dict[str, int] = {}
        for neg_count, item in self.heap:
            count = -neg_count
            if item in count_map:
                count_map[item] = max(count_map[item], count)
            else:
                count_map[item] = count
        for item, count in self.lru_cache.items():
            if item in count_map:
                count_map[item] = max(count_map[item], count)
            else:
                count_map[item] = count
            if self.lmdb_env and item not in self.lru_cache:
                cold_count = self._retrieve_from_cold(item)
                if cold_count is not None:
                    count_map[item] = max(count_map.get(item, 0), cold_count)
        sorted_items = sorted(count_map.items(), key=lambda x: x[1], reverse=True)
        return sorted_items[:k]

    def close(self) -> None:
        """Clean up LMDB environment."""
        if self.lmdb_env:
            try:
                self.lmdb_env.close()
            except Exception as e:
                logger.warning(f'LMDB close failed: {e}')
            finally:
                self.lmdb_env = None

_COMMvQ_VALID_DTYPES = frozenset({'bfloat16', 'float16', 'float32'})


def _commvq_get_dtype(cache) -> Any | None:
    """Extract dtype from cache tensor or first element of list."""
    import mlx.core as mx
    if isinstance(cache, list):
        first_elem = cache[0] if cache else None
        if isinstance(first_elem, tuple):
            first_tensor = first_elem[0]
            return getattr(first_tensor, 'dtype', None)
        return getattr(first_elem, 'dtype', None)
    return getattr(cache, 'dtype', None)


def _commvq_validate_dtype(cache) -> bool:
    """Validate that cache has supported MLX dtype."""
    import mlx.core as mx
    try:
        mx.eval(cache)
    except Exception as e:
        logger.warning(f'Cannot evaluate cache: {e}')
        return False

    dtype = _commvq_get_dtype(cache)
    if dtype is None:
        logger.warning('CommVQ: Cannot determine cache dtype')
        return False
    if str(dtype) not in _COMMvQ_VALID_DTYPES:
        logger.warning(f'CommVQ requires bfloat16/float16/float32 cache, got {dtype}')
        return False
    return True


def _commvq_extract_shape(cache) -> Any | None:
    """Extract original tensor shape from cache."""
    if isinstance(cache, list) and cache:
        first_elem = cache[0]
        if isinstance(first_elem, tuple):
            first_tensor = first_elem[0]
            return getattr(first_tensor, 'shape', None)
        return getattr(first_elem, 'shape', None)
    return getattr(cache, 'shape', None)


def _commvq_validate_and_extract_shape(cache) -> tuple[Any, Any] | None:
    """Validate MLX cache dtype and extract original shape."""
    if not _commvq_validate_dtype(cache):
        return None
    orig_shape = _commvq_extract_shape(cache)
    if orig_shape is None:
        return None
    return cache, orig_shape


def _commvq_flatten_cache(cache, orig_shape) -> Any:
    """Flatten cache tensor(s) into (N, D) matrix for quantization."""
    import mlx.core as mx
    if isinstance(cache, list):
        all_tensors = []
        for item in cache:
            if isinstance(item, tuple):
                all_tensors.append(item[0])
                all_tensors.append(item[1])
            else:
                all_tensors.append(item)
        flat = mx.concatenate([t.reshape(-1) for t in all_tensors])
        flat = flat.reshape(-1, orig_shape[-1])
    else:
        flat = cache.reshape(-1, cache.shape[-1])
    return flat


def _commvq_quantize_group(group, bits: int) -> tuple[Any, Any] | None:
    """Run k-means quantization on a single group. Returns (centroids, indices)."""
    import mlx.core as mx
    if group.size == 0:
        return None
    n_clusters = 1 << bits
    indices = mx.random.randint(0, group.shape[0], (n_clusters,))
    centroids = group[indices]
    for _ in range(10):
        distances = mx.sum((group[:, None] - centroids[None, :]) ** 2, axis=2)
        assignments = mx.argmin(distances, axis=1)
        new_centroids = mx.zeros_like(centroids)
        for k in range(n_clusters):
            mask: Any = assignments == k
            cnt = mx.sum(mask)
            if cnt > 0:
                new_centroids[k] = mx.sum(group * mask[:, None], axis=0) / cnt
        centroids = new_centroids
    final_distances = mx.sum((group[:, None] - centroids[None, :]) ** 2, axis=2)
    final_indices = mx.argmin(final_distances, axis=1)
    return centroids, final_indices


def commvq_quantize(cache, bits: int=2):
    """
    CommVQ 2-bit KV cache quantization (87.5% savings, MLX-native).
    Uses group-wise k-means with 10 iterations (fast on M1 GPU).
    """
    if not MLX_AVAILABLE:
        logger.warning('CommVQ requires MLX, skipping quantization')
        return cache
    try:
        result = _commvq_validate_and_extract_shape(cache)
        if result is None:
            return cache
        cache, orig_shape = result
        flat = _commvq_flatten_cache(cache, orig_shape)

        group_size = 1024
        n_groups = (flat.shape[0] + group_size - 1) // group_size
        compressed_groups = []
        for i in range(n_groups):
            start_idx = i * group_size
            end_idx = min((i + 1) * group_size, flat.shape[0])
            group = flat[start_idx:end_idx]
            quantized = _commvq_quantize_group(group, bits)
            if quantized is not None:
                compressed_groups.append(quantized)

        logger.info(f'[CommVQ] Compressed {n_groups} groups, 87.5% theoretical savings')
        return ('commvq_compressed', compressed_groups, orig_shape)
    except Exception as e:
        logger.warning(f'CommVQ failed: {e}')
        return cache

class ExactCounterFallback:
    """Fallback exact counter when MLX and hybrid are unavailable."""
    __slots__ = tuple(('_counts',))

    def __init__(self):
        self._counts: dict[str, int] = {}

    def add(self, item: str, count: int=1) -> None:
        self._counts[item] = self._counts.get(item, 0) + count

    def estimate(self, item: str) -> int:
        return self._counts.get(item, 0)

    def get_top_k(self, k: int=10) -> list[tuple[str, int]]:
        sorted_items = sorted(self._counts.items(), key=lambda x: x[1], reverse=True)
        return sorted_items[:k]

    def close(self) -> None:
        pass