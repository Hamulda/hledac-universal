"""
URL Deduplication using RotatingBloomFilter
==========================================

Wrapper around probables.RotatingBloomFilter for URL deduplication.
Provides bounded, memory-efficient URL tracking.

Sprint 81 Fáze 3: xxhash support for faster non-crypto hashing.

Sprint F214AD: DeduplicationStrategy protocol extracted to break concrete coupling.
"""

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

from typing import Any, Optional, Protocol, runtime_checkable

# Sprint 81 Fáze 3: xxhash for faster hashing
try:
    import xxhash
    XXHASH_AVAILABLE = True
except ImportError:
    XXHASH_AVAILABLE = False
    import hashlib

# -----------------------------------------------------------------------------
# Rust extension import guard
# -----------------------------------------------------------------------------
_RUST_BLOOM_AVAILABLE = False
try:
    from hledac.rust_extensions.bloom_filter import RustRotatingBloomFilter
    _RUST_BLOOM_AVAILABLE = True
except ImportError:
    pass


@runtime_checkable
class DeduplicationStrategy(Protocol):
    """
    Protocol for URL deduplication strategies.

    Abstracts over different dedup implementations (Bloom filter, Set, LMDB, etc.)
    allowing callers to be decoupled from concrete types.

    Sprint F214AD: Extracted from FetchCoordinator seam.
    """

    def add(self, item: str) -> None:
        """Add an item to the dedup collection."""
        ...

    def __contains__(self, item: str) -> bool:
        """Check if an item is already in the dedup collection."""
        ...


class RotatingBloomFilterAdapter:
    """
    Adapter wrapping RotatingBloomFilter to satisfy DeduplicationStrategy.

    Sprint F214AD: Formerly used directly by FetchCoordinator — now encapsulated.
    """

    __slots__ = ("_filter",)

    def __init__(self, filter_instance):
        self._filter = filter_instance

    def add(self, item: str) -> None:
        self._filter.add(item)

    def __contains__(self, item: str) -> bool:
        return item in self._filter


class PersistentSetAdapter:
    """
    High-precision Set-based dedup adapter for workflows requiring zero false positives.

    Bound is explicit — callers must respect max_size to prevent unbounded growth.

    Sprint F214AD: Alternative to RotatingBloomFilter for high-precision workflows.
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

# Default parameters for URL deduplication
DEFAULT_URL_ESTIMATE = 100000
DEFAULT_FPR = 0.01  # 1% false positive rate (min value for probables)
# P1-15: Cap to prevent unbounded memory growth on M1 8GB
MAX_URL_ESTIMATE = 1_000_000


def fast_hash(text: str) -> str:
    """
    Sprint 81 Fáze 3: 64bit hash pro URL deduplikaci (nekryptografický).

    Uses xxhash if available (10x faster), falls back to blake2b.
    """
    if XXHASH_AVAILABLE:
        return xxhash.xxh3_64(text.encode()).hexdigest()
    else:
        return hashlib.blake2b(text.encode(), digest_size=8).hexdigest()


def create_rotating_bloom_filter(
    est_elements: int = DEFAULT_URL_ESTIMATE,
    false_positive_rate: float = DEFAULT_FPR
):
    """
    Create a RotatingBloomFilter for URL deduplication.

    Args:
        est_elements: Estimated number of unique URLs to track
        false_positive_rate: Target false positive rate (0.001 = 0.1%)

    Returns:
        Configured RotatingBloomFilter instance

    Raises:
        ImportError: If probables library is not installed
    """
    if not PROBABLES_AVAILABLE:
        raise ImportError("probables library required: pip install probables")
    # P1-15: Enforce upper bound to prevent unbounded memory growth
    est_elements = min(est_elements, MAX_URL_ESTIMATE)
    return RotatingBloomFilter(
        est_elements=est_elements,
        false_positive_rate=false_positive_rate
    )


_default_bloom: Optional[Any] = None


def get_default_bloom_filter():
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
