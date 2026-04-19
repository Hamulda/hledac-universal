"""
URL Deduplication using RotatingBloomFilter
==========================================

Wrapper around probables.RotatingBloomFilter for URL deduplication.
Provides bounded, memory-efficient URL tracking.

Sprint 81 Fáze 3: xxhash support for faster non-crypto hashing.
"""

try:
    from probables import RotatingBloomFilter
    PROBABLES_AVAILABLE = True
except ImportError:
    try:
        from pyprobables import RotatingBloomFilter
        PROBABLES_AVAILABLE = True
    except ImportError:
        RotatingBloomFilter = None
        PROBABLES_AVAILABLE = False

# Sprint 81 Fáze 3: xxhash for faster hashing
try:
    import xxhash
    XXHASH_AVAILABLE = True
except ImportError:
    XXHASH_AVAILABLE = False
    import hashlib

from typing import Optional

# Default parameters for URL deduplication
DEFAULT_URL_ESTIMATE = 100000
DEFAULT_FPR = 0.01  # 1% false positive rate (min value for probables)


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
) -> RotatingBloomFilter:
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
    return RotatingBloomFilter(
        est_elements=est_elements,
        false_positive_rate=false_positive_rate
    )


# Default instance for reuse
_default_bloom: Optional[RotatingBloomFilter] = None


def get_default_bloom_filter() -> RotatingBloomFilter:
    """Get or create the default RotatingBloomFilter instance."""
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
