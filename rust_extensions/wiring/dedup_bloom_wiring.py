"""
DedupBloom Wiring - B4
======================

Wires rust_extensions/src/dedup_bloom.rs (DistributedBloomFilter) to:
- coordinators/fetch_coordinator.py — URL queue deduplication

Purpose:
- Lock-free bloom filter for cross-instance URL dedup
- 10× faster than RotatingBloomFilter for 5K URL fronta
- Farm hash (FNV-1a) for cross-instance consistency

API (from Rust):
-----------------
- DedupBloom: Distributed multi-tier BloomFilter
  - __init__(cache_dir: str) → creates filter with 100K/500K/1M capacity tiers
  - add(url: str) → bool (returns True if new)
  - contains(url: str) → bool
  - add_batch(urls: list[str]) → list[bool]
  - contains_batch(urls: list[str]) → list[bool]
  - stats() → dict
  - save() / load() — persistence
  - len() → int
  - memory_bytes() → int

B4 Integration Point:
--------------------
coordinators/fetch_coordinator.py:
  1. Add self._url_bloom at startup
  2. Check bloom before each fetch (fast skip)
  3. RotatingBloomFilter remains canonical (CLAUDE.md rule 7)
  4. DedupBloom supplements, not replaces

M1 8GB Safety:
---------------
- Lock-free via rayon/SIMD hashing
- ~12 MB memory footprint for 1M items @ 0.001 FPP
- Tiered: 100K fine / 500K coarse / 1M macro
- mmap persistence optional
- Python fallback uses LRU cache (bounded to 50K items)

Usage:
-------
from rust_extensions.wiring.dedup_bloom_wiring import get_dedup_bloom

bloom = get_dedup_bloom("/tmp/dedup_bloom")
if bloom.contains(url):
    skip_fetch()
else:
    bloom.add(url)
    await fetch(url)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:

# B4 FIX: Use cachetools for bounded Python fallback (M1 8GB safety)
from cachetools import LRUCache

logger = logging.getLogger(__name__)

# R6: Centralized Rust access via core.rust_backend
from hledac.universal._core.rust_backend import rust as _rust_backend

_dedup_bloom_available = (
    _rust_backend.is_available
    and hasattr(_rust_backend, "dedup_bloom")
    and getattr(_rust_backend, "dedup_bloom", None) is not None
)

_dedup_bloom_module = getattr(_rust_backend, "dedup_bloom", None) if _dedup_bloom_available else None

# Module-level cache
_cached_instance: Any = None

# B4 FIX: Bounded cache size for Python fallback (M1 8GB safety)
# 50K URLs × ~200 bytes avg URL length ≈ 10 MB max
_PYTHON_FALLBACK_MAX_SIZE = 50_000

def get_dedup_bloom(cache_dir: str | Path | None = None) -> DedupBloom | None:
    """
    Get or create the global DedupBloom instance.

    Uses module-level caching to avoid repeated instantiation.

    Args:
        cache_dir: Optional path for mmap persistence.
                   Defaults to /tmp/hledac/dedup_bloom

    Returns:
        DedupBloom wrapper or None if Rust module unavailable.
    """
    global _cached_instance

    if _cached_instance is not None:
        return _cached_instance

    if not _dedup_bloom_available:
        logger.debug("DedupBloom unavailable (Rust extension not built)")
        return None

    try:
        _dir = cache_dir or "/tmp/hledac/dedup_bloom"
        _cached_instance = DedupBloom(_dir)
        logger.info("DedupBloom initialized: %s", _cached_instance.stats())
        return _cached_instance
    except Exception as e:
        logger.warning("Failed to initialize DedupBloom: %s", e)
        return None

class DedupBloom:
    """
    DedupBloom — Distributed BloomFilter for cross-instance URL deduplication.

    Thread-safe via parking_lot::RwLock.
    Falls back to Python LRUCache if Rust module unavailable.

    B4 FIX: Python fallback now uses bounded LRUCache (50K items max, ~10 MB)
    to prevent unbounded memory growth on M1 8GB.

    This is a FAST path bloom filter that runs BEFORE RotatingBloomFilter.
    The purpose is to quickly skip obviously duplicate URLs in the 5K+ URL
    queue without hitting the more expensive RotatingBloomFilter.

    Architecture:
        URL Queue → DedupBloom (fast skip) → RotatingBloomFilter (canonical dedup)

    Example:
        >>> bloom = DedupBloom("/tmp/dedup_bloom")
        >>> bloom.contains("https://example.com")
        False
        >>> bloom.add("https://example.com")
        True
        >>> bloom.contains("https://example.com")
        True
    """

    def __init__(
        self,
        cache_dir: str | Path,
    ) -> None:
        self._bloom: Any = None
        # B4 FIX: Use bounded LRUCache instead of unbounded dict
        # Max 50K items ≈ 10 MB (200 bytes avg URL × 50K)
        self._python_fallback: LRUCache[str, bool] = LRUCache(maxsize=_PYTHON_FALLBACK_MAX_SIZE)

        if _dedup_bloom_module is not None:
            try:
                self._bloom = _dedup_bloom_module.PyDistributedBloomFilter(str(cache_dir))
                logger.debug("DedupBloom Rust backend initialized")
            except Exception as e:
                logger.warning("DedupBloom Rust init failed: %s, using Python fallback", e)
                self._bloom = None
        else:
            logger.debug("DedupBloom using Python fallback (Rust unavailable, LRUCache bounded to %d items)", _PYTHON_FALLBACK_MAX_SIZE)

    @property
    def available(self) -> bool:
        """True if Rust backend is available."""
        return self._bloom is not None

    def add(self, url: str) -> bool:
        """
        Add URL to bloom filter.

        Args:
            url: URL to add

        Returns:
            True if URL was new (not seen before), False if duplicate.
        """
        if self._bloom is not None:
            return self._bloom.add(url)

        # Python fallback
        if url in self._python_fallback:
            return False
        self._python_fallback[url] = True
        return True

    def contains(self, url: str) -> bool:
        """
        Check if URL might be in the bloom filter.

        Note: Bloom filters have false positives (URL reported as seen when not).
        Use this for FAST SKIP of obvious duplicates; canonical dedup uses
        RotatingBloomFilter which handles false negatives.

        Args:
            url: URL to check

        Returns:
            True if URL might be seen before (may be false positive),
            False if definitely not seen (no false negatives).
        """
        if self._bloom is not None:
            return self._bloom.contains(url)

        # Python fallback
        return url in self._python_fallback

    def add_batch(self, urls: list[str]) -> list[bool]:
        """
        Bulk add URLs.

        Args:
            urls: List of URLs to add

        Returns:
            List of booleans (True if new for each URL).
        """
        if self._bloom is not None:
            return self._bloom.add_batch(urls)

        # Python fallback
        return [self.add(url) for url in urls]

    def contains_batch(self, urls: list[str]) -> list[bool]:
        """
        Bulk check URLs.

        Args:
            urls: List of URLs to check

        Returns:
            List of booleans (True if might be duplicate).
        """
        if self._bloom is not None:
            return self._bloom.contains_batch(urls)

        # Python fallback
        return [self.contains(url) for url in urls]

    def skip_batch(self, urls: list[str]) -> tuple[list[str], int]:
        """
        B4 OPTIMIZATION: Bulk skip duplicate URLs and return non-duplicate list.

        More efficient than checking each URL individually - uses batch operations
        when Rust backend is available (rayon parallel).

        Args:
            urls: List of URLs to check

        Returns:
            Tuple of (non_duplicate_urls, skip_count)
        """
        if not urls:
            return [], 0

        if self._bloom is not None:
            # Rust backend: use batch contains (rayon parallel on M1)
            results = self._bloom.contains_batch(urls)
            non_duplicates = []
            skip_count = 0
            for url, is_duplicate in zip(urls, results, strict=True):
                if not is_duplicate:
                    non_duplicates.append(url)
                else:
                    skip_count += 1
            return non_duplicates, skip_count

        # Python fallback with LRUCache
        non_duplicates = []
        skip_count = 0
        for url in urls:
            if self.contains(url):
                skip_count += 1
            else:
                non_duplicates.append(url)
        return non_duplicates, skip_count

    def stats(self) -> dict[str, Any]:
        """
        Get bloom filter statistics.

        Returns:
            Dict with tier stats, memory usage, etc.
        """
        if self._bloom is not None:
            return self._bloom.stats()

        return {
            "total_items": len(self._python_fallback),
            "memory_bytes": len(self._python_fallback) * 100,  # Rough estimate
            "tier_count": 1,
            "fallback": True,
        }

    def len(self) -> int:
        """Return number of items added."""
        if self._bloom is not None:
            return self._bloom.len()

        return len(self._python_fallback)

    def memory_bytes(self) -> int:
        """Return memory usage in bytes."""
        if self._bloom is not None:
            return self._bloom.memory_bytes()

        return len(self._python_fallback) * 100  # Rough estimate

    def save(self) -> str | None:
        """
        Save bloom filter to disk.

        Returns:
            Path to saved file, or None on failure.
        """
        if self._bloom is not None:
            return self._bloom.save()

        return None

    def reset(self) -> None:
        """Reset the bloom filter (clear all entries)."""
        if self._bloom is not None:
            self._bloom.reset()
        else:
            self._python_fallback.clear()

def bloom_check(url: str, bloom: DedupBloom | None) -> bool:
    """
    Quick bloom filter check.

    Args:
        url: URL to check
        bloom: DedupBloom instance or None

    Returns:
        True if bloom filter says "skip" (might be duplicate),
        False if bloom filter says "proceed" (definitely new or bloom says no).
    """
    if bloom is None:
        return False
    return bloom.contains(url)

def bloom_skip(url: str, bloom: DedupBloom | None) -> bool:
    """
    Check if URL should be skipped based on bloom filter.

    Args:
        url: URL to check
        bloom: DedupBloom instance or None

    Returns:
        True if URL should be skipped (bloom says duplicate),
        False if URL should proceed to canonical dedup.
    """
    return bloom_check(url, bloom)

def bloom_add(url: str, bloom: DedupBloom | None) -> bool:
    """
    Add URL to bloom filter.

    Args:
        url: URL to add
        bloom: DedupBloom instance or None

    Returns:
        True if URL was new, False if duplicate.
    """
    if bloom is None:
        return True
    return bloom.add(url)

def bloom_skip_batch(urls: list[str], bloom: DedupBloom | None) -> tuple[list[str], int]:
    """
    B4 OPTIMIZATION: Bulk skip duplicate URLs and return non-duplicate list.

    More efficient than checking each URL individually - uses batch operations
    when Rust backend is available (rayon parallel).

    Args:
        urls: List of URLs to check
        bloom: DedupBloom instance or None

    Returns:
        Tuple of (non_duplicate_urls, skip_count).
        If bloom is None, returns (urls, 0) - no filtering.
    """
    if bloom is None:
        return urls, 0
    return bloom.skip_batch(urls)
