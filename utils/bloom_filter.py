"""
Bloom Filter - Memory-Efficient Existence Checking
===================================================

Integrated from hledac/utils/bloom_filter.py

A lightweight, memory-efficient Bloom Filter implementation for
deduplication and fast existence checking without external dependencies.

Features:
- O(1) existence checking
- Configurable false positive rate
- Memory-efficient bit array
- Serialization support
- M1-optimized for 8GB RAM
- O(1) hash cache eviction (Python 3.7+ ordered dict + popitem)

Example:
    >>> bf = BloomFilter(max_elements=10000, error_rate=0.01)
    >>> bf.add("https://example.com/page1")
    >>> "https://example.com/page1" in bf
    True
    >>> "https://example.com/page2" in bf
    False
"""

import logging
import math
import time
import weakref
from collections import OrderedDict
from pathlib import Path
from typing import Any, cast

from compat.msgspec_gc_compat import Struct

# orjson fallback — 5-10× faster than stdlib json, M1 optimized
try:
    import orjson

    def _json_loads(data: str | bytes) -> Any:
        return orjson.loads(data)

    def _json_dumps(data: Any) -> str:
        return orjson.dumps(data).decode("utf-8")

except ImportError:
    import json as _stdlib_json

    def _json_loads(data: str | bytes) -> Any:
        return _stdlib_json.loads(data)

    def _json_dumps(data: Any) -> str:
        return _stdlib_json.dumps(data)


logger = logging.getLogger(__name__)
MAX_HASH_CACHE_SIZE = 10000
try:
    import xxhash

    XXHASH_AVAILABLE = True
except ImportError:
    XXHASH_AVAILABLE = False
    xxhash: Any = None
_RustBloomFilter: type | None = None
_RUST_BLOOM_AVAILABLE = False
try:
    # R6: Centralized Rust access via core.rust_backend
    from hledac.universal._core.rust_backend import rust

    _rust = rust.raw.module
    _RustBloomFilter = getattr(_rust, "BloomFilter", None)
    _RUST_BLOOM_AVAILABLE = _RustBloomFilter is not None
except ImportError:
    _RustBloomFilter = None
    _RUST_BLOOM_AVAILABLE = False
logger.debug("bloom_filter_backend", extra={"backend": "rust" if _RUST_BLOOM_AVAILABLE else "python"})


class BloomFilterStats(Struct):
    """Statistics for Bloom Filter."""

    size: int
    hash_count: int
    max_elements: int
    error_rate: float
    element_count: int
    current_fpp: float
    fill_ratio: float
    memory_bytes: int


class BloomFilter:
    """
    Memory-efficient Bloom Filter for fast existence checking.

    Optimized for M1 MacBook with minimal memory footprint.
    Uses multiple hash functions for better false positive control.
    """

    __slots__ = ("_byte_array", "_hash_cache", "element_count", "error_rate", "hash_count", "max_elements", "size")

    def __init__(self, max_elements: int = 100000, error_rate: float = 0.01) -> None:
        """
        Initialize Bloom Filter with optimal parameters.

        Args:
            max_elements: Maximum number of elements expected
            error_rate: Desired false positive rate (0.01 = 1%)
        """
        self.max_elements = max_elements
        self.error_rate = error_rate
        self.size = self._calculate_size(max_elements, error_rate)
        self.hash_count = self._calculate_hash_count(self.size, max_elements)
        self._byte_array = bytearray((self.size + 7) // 8)
        self.element_count = 0
        self._hash_cache: OrderedDict[str, list[int]] = OrderedDict()

    def _calculate_size(self, n: int, p: float) -> int:
        """Calculate optimal bit array size."""
        return int(-(n * math.log(p)) / math.log(2) ** 2)

    def _calculate_hash_count(self, m: int, n: int) -> int:
        """Calculate optimal number of hash functions."""
        return max(1, int(m / n * math.log(2)))

    def _get_hash_positions(self, item: str) -> list[int]:
        """
        Get bit positions for an item using multiple hash functions.

        Uses xxhash.xxh64() with seeds for double-hashing (SIMD on M1).
        Falls back to xxhash.xxh64() with two different seeds if xxhash unavailable.
        O(1) FIFO eviction via OrderedDict.popitem(last=False).
        """
        if item in self._hash_cache:
            # Move to end to mark as recently used
            self._hash_cache.move_to_end(item)
            return self._hash_cache[item]

        positions = []
        # Ensure item is bytes for xxhash (v4.0+ requires encoding)
        item_bytes = item.encode("utf-8") if isinstance(item, str) else item
        if XXHASH_AVAILABLE:
            # Primary path: xxhash with seeds for double-hashing (SIMD on M1)
            for i in range(self.hash_count):
                h = xxhash.xxh64(item_bytes, seed=i).intdigest()
                pos = h % self.size
                positions.append(pos)
        else:
            # xxhash is a required dependency - this should never trigger
            raise ImportError("xxhash is required for BloomFilter. Install with: pip install xxhash")

        self._hash_cache[item] = positions

        # O(1) FIFO eviction: OrderedDict.popitem(last=False) removes oldest entry
        if len(self._hash_cache) > MAX_HASH_CACHE_SIZE:
            self._hash_cache.popitem(last=False)

        return positions

    def _set_bit(self, position: int) -> None:
        """Set bit at position."""
        byte_index = position // 8
        bit_index = position % 8
        self._byte_array[byte_index] |= 1 << bit_index

    def _get_bit(self, position: int) -> bool:
        """Get bit at position."""
        byte_index = position // 8
        bit_index = position % 8
        return bool(self._byte_array[byte_index] >> bit_index & 1)

    def add(self, item: str) -> None:
        """
        Add item to Bloom Filter.

        Args:
            item: String item to add
        """
        positions = self._get_hash_positions(item)
        for pos in positions:
            self._set_bit(pos)
        self.element_count += 1

    def __contains__(self, item: str) -> bool:
        """
        Check if item might be in the set.

        Args:
            item: String item to check

        Returns:
            True if item might be in set, False if definitely not
        """
        positions = self._get_hash_positions(item)
        return all(self._get_bit(pos) for pos in positions)

    def contains(self, item: str) -> bool:
        """Explicit check method (same as 'in' operator)."""
        return item in self

    def get_stats(self) -> BloomFilterStats:
        """Get current statistics."""
        set_bits = sum(bin(byte).count("1") for byte in self._byte_array)
        fill_ratio = set_bits / self.size
        if self.element_count > 0:
            current_fpp = (1 - math.exp(-self.hash_count * self.element_count / self.size)) ** self.hash_count
        else:
            current_fpp = 0.0
        return BloomFilterStats(
            size=self.size,
            hash_count=self.hash_count,
            max_elements=self.max_elements,
            error_rate=self.error_rate,
            element_count=self.element_count,
            current_fpp=current_fpp,
            fill_ratio=fill_ratio,
            memory_bytes=len(self._byte_array),
        )

    def save(self, filepath: str | Path) -> None:
        """Save Bloom Filter to file."""
        data = {
            "size": self.size,
            "hash_count": self.hash_count,
            "max_elements": self.max_elements,
            "error_rate": self.error_rate,
            "element_count": self.element_count,
            "byte_array": list(self._byte_array),
        }
        with open(filepath, "w") as f:
            f.write(_json_dumps(data))

    @classmethod
    def load(cls, filepath: str | Path) -> BloomFilter:
        """Load Bloom Filter from file."""
        with open(filepath) as f:
            data = _json_loads(f.read())
        bf = cls(max_elements=data["max_elements"], error_rate=data["error_rate"])
        bf.size = data["size"]
        bf.hash_count = data["hash_count"]
        bf.element_count = data["element_count"]
        bf._byte_array = bytearray(data["byte_array"])
        return bf

    def clear(self) -> None:
        """Clear all elements from Bloom Filter."""
        self._byte_array = bytearray((self.size + 7) // 8)
        self.element_count = 0
        self._hash_cache.clear()


class RotatingBloomFilter:
    """
    Single-tier Rotating Bloom Filter with Rust acceleration + Python fallback.

    When hledac_rust_extensions is importable, delegates to the native
    FNV-1a BloomFilter (10x faster on M1, see HOTPATH_RUST_ANALYSIS.md).
    Otherwise falls back to the pure-Python BloomFilter above.

    API-compatible with pyprobables.RotatingBloomFilter (add, contains, check).

    For multi-tier rotating (per-host tiers + global bloom), use:
        MultiTierRotatingBloomFilter instead.

    This single-tier filter is suitable for:
    - Per-host URL dedup (one filter per host, not per process)
    - Simple deduplication scenarios
    - Batch dedup with Rust acceleration
    """

    __slots__ = ("_impl", "_is_rust", "max_elements", "error_rate", "element_count")

    def __init__(self, max_elements: int = 100000, error_rate: float = 0.01) -> None:
        self.max_elements = int(max_elements)
        self.error_rate = float(error_rate)
        if _RUST_BLOOM_AVAILABLE and _RustBloomFilter is not None:
            self._impl = _RustBloomFilter(capacity=self.max_elements, fp_rate=self.error_rate)
            self._is_rust = True
        else:
            self._impl = BloomFilter(max_elements=self.max_elements, error_rate=self.error_rate)
            self._is_rust = False
        self.element_count = 0

    @property
    def is_rust(self) -> bool:
        """True when the Rust backend is active."""
        return self._is_rust

    def add(self, item: str) -> bool:
        """
        Add an item. Returns True if new, False if already present.
        Falls back to a Python-side membership check for the Python backend,
        which lacks the native new-entry return value.
        """
        if self._is_rust:
            was_new: bool = bool(self._impl.add(item))
            if was_new:
                self.element_count += 1
            return was_new
        was_present = item in self._impl
        self._impl.add(item)
        if not was_present:
            self.element_count += 1
        return not was_present

    def __contains__(self, item: str) -> bool:
        return bool(self._impl.__contains__(item))

    def put_many(self, items: list[str]) -> list[bool]:
        """
        Bulk add items to the filter.

        Args:
            items: List of URL/fingerprint strings to add

        Returns:
            List[bool] — True for each new item, False for duplicates.

        Uses Rust add_batch when available (3-5x faster than per-item).
        Falls back to sequential add() for Python backend.
        """
        if not items:
            return []
        if self._is_rust:
            return list(self._impl.add_batch(items))
        return [self.add(item) for item in items]

    def contains(self, item: str) -> bool:
        return bool(self._impl.contains(item))

    def check(self, item: str) -> bool:
        if self._is_rust:
            check_fn = cast(Any, self._impl).check
            return bool(check_fn(item))
        return item in self._impl

    def contains_batch(self, items: list[str]) -> list[bool]:
        """
        Bulk contains check — delegates to Rust contains_batch when available.

        Args:
            items: List of strings to check

        Returns:
            List[bool] — True if item might be in filter, False if definitely not.
            ~10-50× faster than sequential contains() calls due to rayon parallelism.
        """
        if not items:
            return []
        if self._is_rust:
            batch_fn = cast(Any, self._impl).contains_batch
            return list(batch_fn(items))
        return [self.contains(item) for item in items]

    def clear(self) -> None:
        """Reset filter to empty state."""
        if self._is_rust:
            reset = getattr(self._impl, "reset", None)
            if callable(reset):
                reset()
            else:
                clear = getattr(self._impl, "clear", None)
                if callable(clear):
                    clear()
        else:
            clear = getattr(self._impl, "clear", None)
            if callable(clear):
                clear()
        self.element_count = 0

    def __len__(self) -> int:
        if self._is_rust:
            rust_len = getattr(self._impl, "__len__", None)
            if callable(rust_len):
                return int(cast(Any, rust_len)())
        py_count = getattr(self._impl, "element_count", 0)
        return int(py_count)


def create_url_deduplicator(expected_urls: int = 100000) -> BloomFilter:
    """
    Create a Bloom filter optimized for URL deduplication.

    Args:
        expected_urls: Expected number of URLs to track

    Returns:
        Configured BloomFilter for URL deduplication
    """
    return BloomFilter(max_elements=expected_urls, error_rate=0.001)


def create_content_fingerprint(expected_items: int = 50000) -> BloomFilter:
    """
    Create a Bloom filter for content fingerprinting.

    Args:
        expected_items: Expected number of content items

    Returns:
        Configured BloomFilter for content deduplication
    """
    return BloomFilter(max_elements=expected_items, error_rate=0.01)


class MultiTierRotatingBloomFilter:
    """
    Multi-tier rotating Bloom Filter with per-host tiers + global bloom.

    Architecture:
      - Per-host tiers: One BloomFilter per host (domain), rotates independently
      - Global bloom: Catches cross-host duplicates, persistent via mmap
      - Tier rotation: When a host tier exceeds max_fill_ratio, it rotates to a new filter

    Benefits:
      - Per-host tiers prevent one noisy host from polluting the entire filter
      - Global bloom catches duplicates across hosts
      - Automatic rotation keeps memory bounded per host
      - Memory-mapped persistence survives process restarts

    M1 8GB optimizations:
      - Tier LRU eviction when exceeding max_tiers
      - Fill ratio-based rotation (not count-based) for accurate sizing
      - Optional mmap for global bloom persistence

    Example:
        >>> filter = MultiTierRotatingBloomFilter(
        ...     per_host_capacity=10000,
        ...     global_capacity=100000,
        ...     max_tiers=100,
        ...     max_fill_ratio=0.7
        ... )
        >>> filter.add("https://example.com/page1", host="example.com")
        True
        >>> filter.contains("https://example.com/page1", host="example.com")
        True
    """

    __slots__ = (
        "_per_host_capacity",
        "_global_capacity",
        "_max_tiers",
        "_max_fill_ratio",
        "_global_filter",
        "_host_tiers",
        "_tier_stats",
        "_lru_order",
        "_lock",
        "_is_rust",
        "_mmap_path",
        "_mmap_enabled",
    )

    def __init__(
        self,
        per_host_capacity: int = 10000,
        global_capacity: int = 100000,
        max_tiers: int = 100,
        max_fill_ratio: float = 0.7,
        mmap_path: str | None = None,
    ) -> None:
        """
        Initialize multi-tier rotating Bloom filter.

        Args:
            per_host_capacity: Max elements per host tier before rotation
            global_capacity: Max elements in global bloom filter
            max_tiers: Maximum number of host tiers before LRU eviction
            max_fill_ratio: Fill ratio threshold to trigger tier rotation
            mmap_path: Optional path for mmap-backed global filter persistence
        """
        self._per_host_capacity = per_host_capacity
        self._global_capacity = global_capacity
        self._max_tiers = max_tiers
        self._max_fill_ratio = max_fill_ratio
        self._mmap_path = mmap_path
        self._mmap_enabled = mmap_path is not None

        # Global bloom filter
        if mmap_path is not None:
            self._global_filter = MmapBloomFilter(
                path=mmap_path,
                capacity=global_capacity,
                fp_rate=0.01,
            )
        else:
            self._global_filter = BloomFilter(
                max_elements=global_capacity,
                error_rate=0.01,
            )

        # Per-host tiers: {host: (tier, last_access_time)}
        self._host_tiers: dict[str, BloomFilter] = {}
        self._tier_stats: dict[str, dict[str, Any]] = {}  # host -> stats
        # O(1) LRU tracking via OrderedDict - move_to_end on access, popitem(last=False) to evict
        self._lru_order: OrderedDict[str, None] = OrderedDict()  # LRU ordering for eviction

        # Thread safety
        from threading import Lock

        self._lock = Lock()

        # Backend detection
        self._is_rust = _RUST_BLOOM_AVAILABLE and _RustBloomFilter is not None

    def _get_or_create_host_tier(self, host: str) -> BloomFilter:
        """Get or create a tier for the given host."""
        if host in self._host_tiers:
            # Update LRU: O(1) via move_to_end
            self._lru_order.move_to_end(host)
            return self._host_tiers[host]

        # Evict LRU host if at capacity
        if len(self._host_tiers) >= self._max_tiers:
            self._evict_lru_tier()

        tier = BloomFilter(
            max_elements=self._per_host_capacity,
            error_rate=0.01,
        )
        self._host_tiers[host] = tier
        self._tier_stats[host] = {
            "created_at": time.time(),
            "element_count": 0,
            "rotations": 0,
        }
        self._lru_order[host] = None  # O(1) LRU append

        return tier

    def _evict_lru_tier(self) -> None:
        """Evict the least recently used host tier. O(1) via OrderedDict.popitem(last=False)."""
        if not self._lru_order:
            return

        # O(1) FIFO eviction - removes first (oldest) entry
        lru_host, _ = self._lru_order.popitem(last=False)
        if lru_host in self._host_tiers:
            del self._host_tiers[lru_host]
        if lru_host in self._tier_stats:
            del self._tier_stats[lru_host]

    def _should_rotate_tier(self, tier: BloomFilter) -> bool:
        """Check if a tier should be rotated based on fill ratio."""
        stats = tier.get_stats()
        return stats.fill_ratio >= self._max_fill_ratio

    def _rotate_tier(self, host: str) -> BloomFilter:
        """Rotate a host tier, creating a new filter."""
        # Archive current tier stats
        if host in self._tier_stats:
            self._tier_stats[host]["rotations"] += 1

        new_tier = BloomFilter(
            max_elements=self._per_host_capacity,
            error_rate=0.01,
        )
        self._host_tiers[host] = new_tier

        return new_tier

    def add(self, item: str, host: str) -> bool:
        """
        Add an item to the filter.

        Args:
            item: URL or fingerprint to add
            host: Host/domain to attribute this item to

        Returns:
            True if item is new (not in any tier), False if duplicate
        """
        with self._lock:
            if item in self._global_filter:
                return False

            tier = self._get_or_create_host_tier(host)

            # Check if tier should rotate
            if self._should_rotate_tier(tier):
                tier = self._rotate_tier(host)

            # Add to host tier
            was_new = item not in tier
            tier.add(item)

            if was_new:
                # Add to global bloom
                self._global_filter.add(item)

                if host in self._tier_stats:
                    self._tier_stats[host]["element_count"] += 1

            return was_new

    def add_batch(self, items: list[str], hosts: list[str] | None = None) -> list[bool]:
        """
        Bulk add items.

        Args:
            items: List of URLs/fingerprints
            hosts: Optional list of hosts (same length as items).
                   If None, extracts host from each item URL.

        Returns:
            List of bools indicating new (True) vs duplicate (False) per item
        """
        if not items:
            return []

        # Normalize hosts
        if hosts is None:
            hosts = [self._extract_host(item) for item in items]
        elif len(hosts) != len(items):
            raise ValueError("items and hosts must have same length")

        results = []
        for item, host in zip(items, hosts, strict=False):
            results.append(self.add(item, host))
        return results

    def contains(self, item: str, host: str | None = None) -> bool:
        """
        Check if item is in the filter.

        Args:
            item: URL or fingerprint to check
            host: Optional host to narrow the search

        Returns:
            True if item might be in filter, False if definitely not
        """
        with self._lock:
            # Quick global check
            if item not in self._global_filter:
                return False

            # If host provided, check specific tier
            if host is not None and host in self._host_tiers:
                return item in self._host_tiers[host]

            # Otherwise check all tiers
            return any(item in tier for tier in self._host_tiers.values())

    def contains_batch(self, items: list[str]) -> list[bool]:
        """
        Bulk contains check.

        Args:
            items: List of items to check

        Returns:
            List of bools
        """
        return [self.contains(item) for item in items]

    def _extract_host(self, url: str) -> str:
        """Extract host from URL."""
        try:
            from urllib.parse import urlparse

            parsed = urlparse(url)
            return parsed.netloc or "unknown"
        except Exception:  # noqa: BLE001
            return "unknown"

    def get_stats(self) -> dict[str, Any]:
        """Get comprehensive statistics."""
        return {
            "global": _stats_to_dict(self._global_filter.get_stats()),
            "host_tiers": {
                "count": len(self._host_tiers),
                "max_allowed": self._max_tiers,
                "details": {
                    host: {
                        "stats": _stats_to_dict(tier.get_stats()),
                    }
                    for host, tier in self._host_tiers.items()
                },
            },
            "config": {
                "per_host_capacity": self._per_host_capacity,
                "global_capacity": self._global_capacity,
                "max_tiers": self._max_tiers,
                "max_fill_ratio": self._max_fill_ratio,
                "mmap_enabled": self._mmap_enabled,
            },
        }

    def clear(self) -> None:
        """Clear all tiers and global filter."""
        with self._lock:
            self._global_filter.clear()
            self._host_tiers.clear()
            self._tier_stats.clear()
            self._lru_order.clear()

    def persist(self) -> None:
        """Persist global filter to mmap file."""
        if self._mmap_enabled and hasattr(self._global_filter, "msync"):
            self._global_filter.msync(0)

    def __len__(self) -> int:
        """Return total element count across all tiers + global."""
        with self._lock:
            total = self._global_filter.element_count
            for tier in self._host_tiers.values():
                total += tier.element_count
            return total

    def tier_count(self) -> int:
        """Return the number of active host tiers."""
        with self._lock:
            return len(self._host_tiers)

    def evict_idle_tiers(self, max_idle_seconds: float = 3600.0) -> int:
        """
        Evict tiers that haven't been accessed recently.

        Args:
            max_idle_seconds: Maximum idle time before eviction.

        Returns:
            Number of tiers evicted.
        """
        import time

        evicted = 0
        current_time = time.time()

        with self._lock:
            to_evict = [
                host
                for host, stats in self._tier_stats.items()
                if current_time - stats.get("created_at", 0) > max_idle_seconds
            ]

            for host in to_evict:
                if host in self._host_tiers:
                    del self._host_tiers[host]
                if host in self._tier_stats:
                    del self._tier_stats[host]
                if host in self._lru_order:
                    del self._lru_order[host]  # O(1) dict deletion
                evicted += 1

        return evicted

    async def add_async(self, item: str, host: str) -> bool:
        """
        Async add - runs synchronous add in thread pool to avoid blocking event loop.

        Args:
            item: URL or fingerprint to add
            host: Host/domain to attribute this item to

        Returns:
            True if item is new (not in any tier), False if duplicate
        """
        import asyncio

        return await asyncio.to_thread(self.add, item, host)

    async def contains_async(self, item: str, host: str | None = None) -> bool:
        """
        Async contains check - runs synchronous contains in thread pool.

        Args:
            item: URL or fingerprint to check
            host: Optional host to narrow the search

        Returns:
            True if item might be in filter, False if definitely not
        """
        import asyncio

        return await asyncio.to_thread(self.contains, item, host)

    async def add_batch_async(
        self,
        items: list[str],
        hosts: list[str] | None = None,
    ) -> list[bool]:
        """
        Async batch add - runs synchronous batch add in thread pool.

        Args:
            items: List of URLs/fingerprints
            hosts: Optional hosts list (same length as items)

        Returns:
            List of bools (True = new, False = duplicate)
        """
        import asyncio

        return await asyncio.to_thread(self.add_batch, items, hosts)

    async def contains_batch_async(self, items: list[str]) -> list[bool]:
        """
        Async batch contains - runs synchronous batch contains in thread pool.

        Args:
            items: List of items to check

        Returns:
            List of bools
        """
        import asyncio

        return await asyncio.to_thread(self.contains_batch, items)


class MmapBloomFilter(BloomFilter):
    """
    Memory-mapped Bloom Filter for persistence across restarts.

    Uses mmap for efficient memory-mapped file I/O on M1.
    Falls back to regular BloomFilter if mmap unavailable.
    """

    __slots__ = ("_path", "_mmap_obj", "_mmap_mode", "_finalizer")

    def __init__(
        self,
        path: str,
        capacity: int = 100000,
        fp_rate: float = 0.01,
    ) -> None:
        import mmap
        import os
        from pathlib import Path

        path = str(Path(path).expanduser().resolve())
        self._path = path
        self._mmap_obj: mmap.mmap | None = None
        self._mmap_mode = False

        super().__init__(max_elements=capacity, error_rate=fp_rate)

        # Calculate size for mmap
        byte_size = (self.size + 7) // 8

        # Try mmap first - gracefully fallback if unavailable
        try:
            # Create directory if needed
            dir_path = os.path.dirname(path)
            if dir_path and not os.path.exists(dir_path):
                os.makedirs(dir_path, exist_ok=True)

            # Initialize file if needed
            if not os.path.exists(path):
                with open(path, "wb") as f:
                    f.write(b"\x00" * byte_size)

            # Memory-map the file
            with open(path, "r+b") as f:
                self._mmap_obj = mmap.mmap(f.fileno(), byte_size)
                self._byte_array = bytearray(self._mmap_obj)  # Share memory view
                self._mmap_mode = True

        except OSError, PermissionError, ValueError:
            # Fallback: use regular bytearray (non-persistent)
            logger.debug("MmapBloomFilter falling back to non-mmap mode: %s", path)
            self._mmap_obj = None
            self._mmap_mode = False

        # F264: weakref.finalize for deterministic cleanup (Python 3.14+ compatible)
        self._finalizer = weakref.finalize(self, _mmap_bloom_filter_cleanup, self._mmap_obj)

    @property
    def is_mmap(self) -> bool:
        """True if using memory-mapped storage."""
        return self._mmap_mode

    def msync(self, flags: int = 0) -> None:
        """Sync mmap to disk."""
        if self._mmap_obj is not None:
            self._mmap_obj.flush(flags)

    def __del__(self) -> None:
        """
        F264: Fallback cleanup — weakref.finalize is primary, __del__ is last resort.

        Called only if:
        - Finalizer wasn't triggered (interpreter shutdown order)
        - Object was resurrected and then deleted
        """
        if hasattr(self, "_finalizer") and self._finalizer.detach():
            self._cleanup_mmap()

    def _cleanup_mmap(self) -> None:
        """Cleanup method for weakref.finalize."""
        if self._mmap_obj is not None:
            try:
                self._mmap_obj.close()
            except Exception:  # noqa: BLE001
                pass


def _mmap_bloom_filter_cleanup(mmap_obj: Any) -> None:
    """
    Module-level cleanup function for weakref.finalize.

    F264: Close mmap when MmapBloomFilter is garbage collected.
    Called automatically by weakref.finalize when the object is GC'd.
    """
    try:
        if mmap_obj is not None:
            mmap_obj.close()
    except Exception:  # noqa: BLE001
        pass


def _stats_to_dict(stats: BloomFilterStats) -> dict[str, Any]:
    """Convert BloomFilterStats (msgspec.Struct) to dict."""
    return {
        "size": stats.size,
        "hash_count": stats.hash_count,
        "max_elements": stats.max_elements,
        "error_rate": stats.error_rate,
        "element_count": stats.element_count,
        "current_fpp": stats.current_fpp,
        "fill_ratio": stats.fill_ratio,
        "memory_bytes": stats.memory_bytes,
    }


__all__ = [
    "BloomFilter",
    "BloomFilterStats",
    "RotatingBloomFilter",
    "MultiTierRotatingBloomFilter",
    "MmapBloomFilter",
    "create_url_deduplicator",
    "create_content_fingerprint",
    "_RUST_BLOOM_AVAILABLE",
]
