"""
Crawler URL Deduplication Factory — Issue #6 Fix
===============================================

Provides a unified factory for bounded URL/crawl dedup.

Strategy:
  - If Rust bloom backend is available (mlx-embed / hledac_rust_extensions):
      RotatingBloomFilter — O(1) add/contains, bounded memory, mmap-persisted
  - Else:
      BoundedCappedSet — OrderedDict with maxlen, O(1) LRU eviction

Usage:
    seen = make_url_dedup(capacity=100_000)
    if "https://example.com" not in seen:
        seen.add("https://example.com")

Anti-pattern (unbounded growth):
    seen = set()              # NEVER in crawlers
    seen.add(url)             # Memory leak on M1 8GB

Canonical import:
    from hledac.universal.utils.crawler_dedup import make_url_dedup
"""

from __future__ import annotations

import os
from collections import OrderedDict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import TypeAlias

__all__ = ["make_url_dedup", "BoundedCappedSet"]

# ---------------------------------------------------------------------
# Rust-backed RotatingBloomFilter (preferred)
# ---------------------------------------------------------------------

_RUST_BLOOM_AVAILABLE: bool = False
_RustBloomFilter: type | None = None

try:
    from hledac.universal.utils.bloom_filter import RotatingBloomFilter as _RBF

    _RUST_BLOOM_AVAILABLE = True
    _RustBloomFilter = _RBF
except ImportError:
    RotatingBloomFilter = None  # type: ignore[misc,assignment]


# ---------------------------------------------------------------------
# Python fallback: OrderedDict with maxlen LRU eviction
# ---------------------------------------------------------------------


class BoundedCappedSet:
    """
    Bounded set[str] with O(1) LRU eviction when capacity is reached.

    Uses a list of keys + dict for O(1) contains/add via dict lookup,
    with manual FIFO eviction when capacity is reached.

    Memory budget: ~72 bytes/entry → 100K URLs ≈ 7.2 MB (acceptable).

    Usage:
    s = BoundedCappedSet(maxlen=100_000)
    s.add("https://example.com")
    "https://example.com" in s  # True

    Eviction policy: FIFO (oldest entry evicted when full).
    """

    __slots__ = ("_maxlen", "_data", "_order")

    def __init__(self, maxlen: int = 100_000) -> None:
        object.__setattr__(self, "_maxlen", int(maxlen))
        # _data: key → True mapping for O(1) contains
        object.__setattr__(self, "_data", {})
        # _order: ordered list of keys (FIFO eviction order)
        object.__setattr__(self, "_order", [])

    def _check_evict(self) -> None:
        """Evict oldest entry if at capacity."""
        data = object.__getattribute__(self, "_data")
        order = object.__getattribute__(self, "_order")
        maxlen = object.__getattribute__(self, "_maxlen")
        while len(data) >= maxlen and order:
            oldest = order.pop(0)
            data.pop(oldest, None)

    def add(self, item: str) -> bool:
        """Add item. Returns True if new, False if already present."""
        data = object.__getattribute__(self, "_data")
        order = object.__getattribute__(self, "_order")
        if item in data:
            # Move to end (most-recently-seen position) on re-visit
            order.remove(item)
            order.append(item)
            return False
        self._check_evict()
        data[item] = True
        order.append(item)
        return True

    def __contains__(self, item: str) -> bool:
        return item in object.__getattribute__(self, "_data")

    def __len__(self) -> int:
        return len(object.__getattribute__(self, "_data"))

    def clear(self) -> None:
        object.__getattribute__(self, "_data").clear()
        object.__getattribute__(self, "_order").clear()

    def discard(self, item: str) -> None:
        """Remove an item without raising KeyError."""
        data = object.__getattribute__(self, "_data")
        order = object.__getattribute__(self, "_order")
        if item in data:
            data.pop(item, None)
            try:
                order.remove(item)
            except ValueError:
                pass

    def __repr__(self) -> str:
        return f"BoundedCappedSet(maxlen={self._maxlen}, size={len(self)})"


# ---------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------

# Default capacity — large enough for most crawls, small enough for M1 8GB
_DEFAULT_CAPACITY: int = int(os.environ.get("HLEDAC_CRAWLER_DEDUP_CAPACITY", "100_000"))


def make_url_dedup(
    capacity: int | None = None,
    error_rate: float = 0.005,
) -> BoundedCappedSet | RotatingBloomFilter:
    """
    Create a bounded URL deduplication container.

    Args:
        capacity: Maximum number of URLs to track (default: 100_000).
                  Controls memory budget. On M1 8GB, 100K URLs ≈ 7-10 MB.
        error_rate: False-positive rate for Bloom filter (ignored for BoundedCappedSet).
                    Lower rate = more memory. Default 0.5% (acceptable for dedup).

    Returns:
        RotatingBloomFilter when Rust backend is available (preferred).
        BoundedCappedSet as pure-Python fallback.

    Example:
        seen = make_url_dedup(capacity=50_000)
        for url in urls:
            if url not in seen:
                seen.add(url)
                yield url
    """
    cap = capacity if capacity is not None else _DEFAULT_CAPACITY

    if _RUST_BLOOM_AVAILABLE and _RustBloomFilter is not None:
        return _RustBloomFilter(max_elements=cap, error_rate=error_rate)

    return BoundedCappedSet(maxlen=cap)


# ---------------------------------------------------------------------
# Alias for type annotations
# ---------------------------------------------------------------------

UrlDedup: TypeAlias = BoundedCappedSet | RotatingBloomFilter
