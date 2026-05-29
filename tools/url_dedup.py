"""
URL Deduplication using RotatingBloomFilter

Wrapper around probables.RotatingBloomFilter for URL deduplication.
Provides bounded, memory-efficient URL tracking.

Sprint 81 Fáze 3: xxhash support for faster non-crypto hashing.
Sprint F214AD: DeduplicationStrategy protocol extracted to break concrete coupling.
"""
from __future__ import annotations

import hashlib
from typing import Any, Protocol, runtime_checkable

# Probables library import (RotatingBloomFilter from probables)
try:
    from probables import RotatingBloomFilter

    PROBABLES_AVAILABLE = True
except ImportError:
    try:
        from pyprobables import RotatingBloomFilter

        PROBABLES_AVAILABLE = True
    except ImportError:
        RotatingBloomFilter = object  # sentinel — functions raise ImportError before use
        PROBABLES_AVAILABLE = False

# xxhash for fast non-crypto hashing (10x faster than blake2b)
try:
    import xxhash

    xxhash_available = True
except ImportError:
    xxhash_available = False

# Rust extension import guard
_RUST_BLOOM_AVAILABLE = False
try:
    import hledac_rust_extensions

    # Expose Rust BloomFilter as RustRotatingBloomFilter for API compatibility
    RustRotatingBloomFilter = hledac_rust_extensions.BloomFilter
    _RUST_BLOOM_AVAILABLE = True
except ImportError:
    pass

# Rust UrlSet — FNV-1a hash dedup (highest ROI, HOTPATH_RUST_ANALYSIS.md)
_RUST_URL_DEDUP_AVAILABLE = False
try:
    from hledac_rust_extensions import UrlSet as RustUrlSet

    _RUST_URL_DEDUP_AVAILABLE = True
except ImportError:
    RustUrlSet = None  # type: ignore[assignment,sentinel]


@runtime_checkable
class DeduplicationStrategy(Protocol):
    """Protocol for URL deduplication strategies."""

    def add(self, item: str) -> None:
        """Add an item to the deduplication set."""
        ...

    def __contains__(self, item: str) -> bool:
        """Check if an item might have been seen before."""
        ...


class RotatingBloomFilterAdapter:
    """
    Adapter wrapping RotatingBloomFilter to satisfy DeduplicationStrategy.

    Sprint F214AD: Formerly used directly by FetchCoordinator — now encapsulated.
    """

    __slots__ = ("_filter",)

    def __init__(self, filter_instance: Any) -> None:
        self._filter = filter_instance

    def add(self, item: str) -> None:
        self._filter.add(item)

    def __contains__(self, item: str) -> bool:
        return item in self._filter


class PersistentSetAdapter:
    """
    Bounded set adapter for deduplication when BloomFilter unavailable.

    Uses an OrderedDict-style eviction to maintain bounded memory.
    """

    __slots__ = ("_set", "_max_size")

    def __init__(self, max_size: int = 500_000) -> None:
        self._set: set = set()
        self._max_size = max_size

    def add(self, item: str) -> None:
        if len(self._set) >= self._max_size:
            # Evict oldest 10% when bound reached — O(1) amortized
            evict_count = max(1, self._max_size // 10)
            for _ in range(evict_count):
                try:
                    self._set.pop()
                except KeyError:
                    break
        self._set.add(item)

    def __contains__(self, item: str) -> bool:
        return item in self._set


class RustUrlSetAdapter:
    """
    Adapter wrapping Rust UrlSet (FNV-1a hash set) to satisfy DeduplicationStrategy.

    Rust implementation: url_set.rs — FNV-1a hashing, O(1) add/contains.
    Falls back to Python set if Rust unavailable (RUST_URL_DEDUP_AVAILABLE=False).
    """
    __slots__ = ("_set",)

    def __init__(self) -> None:
        if not _RUST_URL_DEDUP_AVAILABLE:
            raise ImportError("hledac_rust_extensions.UrlSet not available")
        self._set: Any = RustUrlSet()

    def add(self, item: str) -> None:
        self._set.add(item)

    def __contains__(self, item: str) -> bool:
        return self._set.contains(item)

    def __len__(self) -> int:
        return self._set.len()

    def clear(self) -> None:
        self._set.clear()


def create_rust_url_set() -> DeduplicationStrategy:
    """Create a Rust-backed URL deduplication set (FNV-1a, O(1))."""
    if not _RUST_URL_DEDUP_AVAILABLE:
        raise ImportError("Rust UrlSet not available — install hledac_rust_extensions")
    return RustUrlSetAdapter()


def fast_hash(text: str) -> str:
    """
    Fast non-crypto hash for URL fingerprinting.

    Uses xxhash (10x faster) if available, falls back to blake2b.
    xxhash is NOT cryptographically safe — use only for deduplication.
    """
    if xxhash_available:
        return xxhash.xxh64(text).hexdigest()
    # Fallback to blake2b (crypto-grade but slower)
    return hashlib.blake2b(text.encode(), digest_size=8).hexdigest()


# Configuration
DEFAULT_URL_ESTIMATE = 100_000
DEFAULT_FPR = 0.01  # 1% false positive rate
MAX_URL_ESTIMATE = 1_000_000


def create_rotating_bloom_filter(
    est_elements: int = DEFAULT_URL_ESTIMATE,
    false_positive_rate: float = DEFAULT_FPR,
) -> DeduplicationStrategy:
    """
    Create a RotatingBloomFilter for URL deduplication.

    Args:
        est_elements: Estimated number of unique URLs to track
        false_positive_rate: Target false positive rate (0.001 = 0.1%)
    Returns:
        Configured DeduplicationStrategy (Rust or probables fallback)
    Raises:
        ImportError: If neither Rust extensions nor probables library is available
    """
    # P1-15: Enforce upper bound to prevent unbounded memory growth
    est_elements = min(est_elements, MAX_URL_ESTIMATE)

    # Prefer Rust BloomFilter when available — 10-100x faster than pyprobables
    if _RUST_BLOOM_AVAILABLE:
        return RustRotatingBloomFilter(est_elements, false_positive_rate)

    if not PROBABLES_AVAILABLE:
        raise ImportError(
            "Neither Rust BloomFilter (hledac-rust-extensions) nor "
            "probables library available. Install probables: pip install probables"
        )
    return RotatingBloomFilter(
        est_elements=est_elements,
        false_positive_rate=false_positive_rate,
    )


_default_bloom: Any | None = None


def get_default_bloom_filter() -> DeduplicationStrategy:
    """Get the shared default BloomFilter instance."""
    global _default_bloom
    if _default_bloom is None:
        if not PROBABLES_AVAILABLE:
            raise ImportError("probables library required: pip install probables")
        _default_bloom = create_rotating_bloom_filter()
    return _default_bloom


def reset_default_bloom_filter() -> None:
    """Reset the default bloom filter (for testing)."""
    global _default_bloom
    _default_bloom = None
