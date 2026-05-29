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

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Boundedness constant for hash cache
MAX_HASH_CACHE_SIZE = 10_000

# Optional xxhash for faster hashing (Fix 3)
try:
    import xxhash
    XXHASH_AVAILABLE = True
except ImportError:
    xxhash = None
    XXHASH_AVAILABLE = False


@dataclass
class BloomFilterStats:
    """Statistics for Bloom Filter."""
    size: int
    hash_count: int
    max_elements: int
    error_rate: float
    element_count: int
    current_fpp: float  # Current estimated false positive probability
    fill_ratio: float
    memory_bytes: int


class BloomFilter:
    """
    Memory-efficient Bloom Filter for fast existence checking.

    Optimized for M1 MacBook with minimal memory footprint.
    Uses multiple hash functions for better false positive control.
    """

    def __init__(self, max_elements: int = 100000, error_rate: float = 0.01):
        """
        Initialize Bloom Filter with optimal parameters.

        Args:
            max_elements: Maximum number of elements expected
            error_rate: Desired false positive rate (0.01 = 1%)
        """
        self.max_elements = max_elements
        self.error_rate = error_rate

        # Calculate optimal bit array size and hash count
        self.size = self._calculate_size(max_elements, error_rate)
        self.hash_count = self._calculate_hash_count(self.size, max_elements)

        # Initialize bit array as bytearray for memory efficiency
        self._byte_array = bytearray((self.size + 7) // 8)

        # Track element count for statistics
        self.element_count = 0

        # Cache for hash positions (optimization for repeated checks)
        self._hash_cache: dict[str, list[int]] = {}

    def _calculate_size(self, n: int, p: float) -> int:
        """Calculate optimal bit array size."""
        # m = -(n * ln(p)) / (ln(2)^2)
        return int(-(n * math.log(p)) / (math.log(2) ** 2))

    def _calculate_hash_count(self, m: int, n: int) -> int:
        """Calculate optimal number of hash functions."""
        # k = (m/n) * ln(2)
        return max(1, int((m / n) * math.log(2)))

    def _get_hash_positions(self, item: str) -> list[int]:
        """Get bit positions for an item using multiple hash functions."""
        if item in self._hash_cache:
            return self._hash_cache[item]

        positions = []
        # Use xxhash for faster hashing when available (Fix 3)
        if XXHASH_AVAILABLE:
            for i in range(self.hash_count):
                h = xxhash.xxh64(item, seed=i).intdigest()
                pos = h % self.size
                positions.append(pos)
        else:
            # Fallback to md5 + sha1
            hash1 = int(hashlib.md5(item.encode()).hexdigest(), 16)
            hash2 = int(hashlib.sha1(item.encode()).hexdigest(), 16)
            for i in range(self.hash_count):
                # Double hashing: (hash1 + i * hash2) % size
                pos = (hash1 + i * hash2) % self.size
                positions.append(pos)

        # Cache positions for this item
        self._hash_cache[item] = positions
        # Enforce boundedness: FIFO eviction if over limit
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
        self._byte_array[byte_index] |= (1 << bit_index)

    def _get_bit(self, position: int) -> bool:
        """Get bit at position."""
        byte_index = position // 8
        bit_index = position % 8
        return (self._byte_array[byte_index] >> bit_index) & 1

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
        # Calculate fill ratio
        set_bits = sum(bin(byte).count('1') for byte in self._byte_array)
        fill_ratio = set_bits / self.size

        # Calculate current false positive probability
        # fpp = (1 - e^(-kn/m))^k
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
            memory_bytes=len(self._byte_array)
        )

    def save(self, filepath: Union[str, Path]) -> None:
        """Save Bloom Filter to file."""
        data = {
            'size': self.size,
            'hash_count': self.hash_count,
            'max_elements': self.max_elements,
            'error_rate': self.error_rate,
            'element_count': self.element_count,
            'byte_array': list(self._byte_array)
        }
        with open(filepath, 'w') as f:
            json.dump(data, f)

    @classmethod
    def load(cls, filepath: Union[str, Path]) -> BloomFilter:
        """Load Bloom Filter from file."""
        with open(filepath) as f:
            data = json.load(f)

        bf = cls(
            max_elements=data['max_elements'],
            error_rate=data['error_rate']
        )
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


class ScalableBloomFilter:
    """
    Scalable Bloom Filter that grows as elements are added.

    Automatically creates additional Bloom filters when the current
    one reaches capacity, maintaining a target false positive rate.
    """

    def __init__(
        self,
        initial_capacity: int = 10000,
        error_rate: float = 0.01,
        growth_factor: float = 2.0
    ):
        """
        Initialize scalable Bloom filter.

        Args:
            initial_capacity: Initial capacity of first filter
            error_rate: Target false positive rate
            growth_factor: Factor by which capacity grows for each new filter
        """
        self.initial_capacity = initial_capacity
        self.target_error_rate = error_rate
        self.growth_factor = growth_factor
        self.filters: list[BloomFilter] = []
        self._add_new_filter()

    def _add_new_filter(self) -> None:
        """Add a new Bloom filter with increased capacity."""
        if not self.filters:
            capacity = self.initial_capacity
        else:
            capacity = int(self.initial_capacity * (self.growth_factor ** len(self.filters)))

        # Allocate error rate budget among filters
        filter_error = self.target_error_rate / (2 ** len(self.filters))

        self.filters.append(BloomFilter(
            max_elements=capacity,
            error_rate=filter_error
        ))

    def add(self, item: str) -> None:
        """Add an item to the scalable Bloom filter."""
        # Check if we need to add a new filter
        current_filter = self.filters[-1]
        if current_filter.element_count >= current_filter.max_elements * 0.9:
            self._add_new_filter()
            current_filter = self.filters[-1]

        current_filter.add(item)

    def __contains__(self, item: str) -> bool:
        """Check if item may exist in any filter."""
        return any(item in bf for bf in self.filters)

    def get_stats(self) -> dict[str, Any]:
        """Get statistics for all filters."""
        total_elements = sum(bf.element_count for bf in self.filters)
        total_memory = sum(bf.get_stats().memory_bytes for bf in self.filters)

        # Combined false positive probability
        # P(fp) = 1 - product(1 - p_i) for all filters i
        combined_fpp = 1.0
        for bf in self.filters:
            stats = bf.get_stats()
            combined_fpp *= (1 - stats.current_fpp)
        combined_fpp = 1 - combined_fpp

        return {
            'filter_count': len(self.filters),
            'total_elements': total_elements,
            'total_memory_bytes': total_memory,
            'combined_fpp': combined_fpp,
            'filters': [bf.get_stats() for bf in self.filters]
        }


def create_url_deduplicator(expected_urls: int = 100000) -> BloomFilter:
    """
    Create a Bloom filter optimized for URL deduplication.

    Args:
        expected_urls: Expected number of URLs to track

    Returns:
        Configured BloomFilter for URL deduplication
    """
    return BloomFilter(
        max_elements=expected_urls,
        error_rate=0.001  # 0.1% FPP for accurate deduplication
    )


def create_content_fingerprint(expected_items: int = 50000) -> BloomFilter:
    """
    Create a Bloom filter for content fingerprinting.

    Args:
        expected_items: Expected number of content items

    Returns:
        Configured BloomFilter for content deduplication
    """
    return BloomFilter(
        max_elements=expected_items,
        error_rate=0.01  # 1% FPP acceptable for content
    )


__all__ = [
    'BloomFilter',
    'BloomFilterStats',
    'ScalableBloomFilter',
    'create_url_deduplicator',
    'create_content_fingerprint'
]
