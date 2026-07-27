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

Example:
    >>> bf = BloomFilter(max_elements=10000, error_rate=0.01)
    >>> bf.add("https://example.com/page1")
    >>> "https://example.com/page1" in bf
    True
    >>> "https://example.com/page2" in bf
    False
"""
import hashlib
import json
import logging
import math
from dataclasses import dataclass
import msgspec
from pathlib import Path
from typing import Any, cast
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
    import hledac_rust_extensions as _rust
    _RustBloomFilter = getattr(_rust, 'BloomFilter', None)
    _RUST_BLOOM_AVAILABLE = _RustBloomFilter is not None
except ImportError:
    _RustBloomFilter = None
    _RUST_BLOOM_AVAILABLE = False
logger.debug('bloom_filter_backend', extra={'backend': 'rust' if _RUST_BLOOM_AVAILABLE else 'python'})

class BloomFilterStats(msgspec.Struct, gc=False):
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
    __slots__ = tuple(('_byte_array', '_hash_cache', 'element_count', 'error_rate', 'hash_count', 'max_elements', 'size'))

    def __init__(self, max_elements: int=100000, error_rate: float=0.01):
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
        self._hash_cache: dict[str, list[int]] = {}

    def _calculate_size(self, n: int, p: float) -> int:
        """Calculate optimal bit array size."""
        return int(-(n * math.log(p)) / math.log(2) ** 2)

    def _calculate_hash_count(self, m: int, n: int) -> int:
        """Calculate optimal number of hash functions."""
        return max(1, int(m / n * math.log(2)))

    def _get_hash_positions(self, item: str) -> list[int]:
        """Get bit positions for an item using multiple hash functions."""
        if item in self._hash_cache:
            return self._hash_cache[item]
        positions = []
        if XXHASH_AVAILABLE:
            for i in range(self.hash_count):
                h = xxhash.xxh64(item, seed=i).intdigest()
                pos = h % self.size
                positions.append(pos)
        else:
            hash1 = int(hashlib.md5(item.encode()).hexdigest(), 16)
            hash2 = int(hashlib.sha256(item.encode()).hexdigest(), 16)
            for i in range(self.hash_count):
                pos = (hash1 + i * hash2) % self.size
                positions.append(pos)
        self._hash_cache[item] = positions
        if len(self._hash_cache) > MAX_HASH_CACHE_SIZE:
            try:
                oldest = next(iter(self._hash_cache))
                self._hash_cache.pop(oldest, None)
            except Exception:
                pass
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
        return all((self._get_bit(pos) for pos in positions))

    def contains(self, item: str) -> bool:
        """Explicit check method (same as 'in' operator)."""
        return item in self

    def get_stats(self) -> BloomFilterStats:
        """Get current statistics."""
        set_bits = sum((bin(byte).count('1') for byte in self._byte_array))
        fill_ratio = set_bits / self.size
        if self.element_count > 0:
            current_fpp = (1 - math.exp(-self.hash_count * self.element_count / self.size)) ** self.hash_count
        else:
            current_fpp = 0.0
        return BloomFilterStats(size=self.size, hash_count=self.hash_count, max_elements=self.max_elements, error_rate=self.error_rate, element_count=self.element_count, current_fpp=current_fpp, fill_ratio=fill_ratio, memory_bytes=len(self._byte_array))

    def save(self, filepath: str | Path) -> None:
        """Save Bloom Filter to file."""
        data = {'size': self.size, 'hash_count': self.hash_count, 'max_elements': self.max_elements, 'error_rate': self.error_rate, 'element_count': self.element_count, 'byte_array': list(self._byte_array)}
        with open(filepath, 'w') as f:
            json.dump(data, f)

    @classmethod
    def load(cls, filepath: str | Path) -> BloomFilter:
        """Load Bloom Filter from file."""
        with open(filepath) as f:
            data = json.load(f)
        bf = cls(max_elements=data['max_elements'], error_rate=data['error_rate'])
        bf.size = data['size']
        bf.hash_count = data['hash_count']
        bf.element_count = data['element_count']
        bf._byte_array = bytearray(data['byte_array'])
        return bf

    def clear(self) -> None:
        """Clear all elements from Bloom Filter."""
        self._byte_array = bytearray((self.size + 7) // 8)
        self.element_count = 0
        self._hash_cache.clear()

class RotatingBloomFilter:
    """
    Rotating Bloom Filter with Rust acceleration + Python fallback.

    When hledac_rust_extensions is importable, delegates to the native
    FNV-1a BloomFilter (10x faster on M1, see HOTPATH_RUST_ANALYSIS.md).
    Otherwise falls back to the pure-Python BloomFilter above.

    API-compatible with pyprobables.RotatingBloomFilter (add, contains, check).
    The rotating (multi-tier) layout is not yet implemented — the
    single-tier Rust filter is already correct for the current dedup use
    case (URL dedup uses one filter per host, not per process).
    """
    __slots__ = ('_impl', '_is_rust', 'max_elements', 'error_rate', 'element_count')

    def __init__(self, max_elements: int=100000, error_rate: float=0.01) -> None:
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
            reset = getattr(self._impl, 'reset', None)
            if callable(reset):
                reset()
            else:
                clear = getattr(self._impl, 'clear', None)
                if callable(clear):
                    clear()
        else:
            clear = getattr(self._impl, 'clear', None)
            if callable(clear):
                clear()
        self.element_count = 0

    def __len__(self) -> int:
        if self._is_rust:
            rust_len = getattr(self._impl, '__len__', None)
            if callable(rust_len):
                return int(cast(Any, rust_len)())
        py_count = getattr(self._impl, 'element_count', 0)
        return int(py_count)

def create_url_deduplicator(expected_urls: int=100000) -> BloomFilter:
    """
    Create a Bloom filter optimized for URL deduplication.

    Args:
        expected_urls: Expected number of URLs to track

    Returns:
        Configured BloomFilter for URL deduplication
    """
    return BloomFilter(max_elements=expected_urls, error_rate=0.001)

def create_content_fingerprint(expected_items: int=50000) -> BloomFilter:
    """
    Create a Bloom filter for content fingerprinting.

    Args:
        expected_items: Expected number of content items

    Returns:
        Configured BloomFilter for content deduplication
    """
    return BloomFilter(max_elements=expected_items, error_rate=0.01)
__all__ = ['BloomFilter', 'BloomFilterStats', 'RotatingBloomFilter', 'create_url_deduplicator', 'create_content_fingerprint']