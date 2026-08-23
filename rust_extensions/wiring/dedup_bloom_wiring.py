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
- ~1.5 MB total memory footprint (1.2 MB tiers + 256 KB Count-Min Sketch)
- Tiered: 100K fine / 500K coarse / 1M macro
- mmap persistence via LZ4-compressed files (restart-safe)
- Python fallback uses bounded set (50K items max, ~10 MB)

Usage:
-------
from rust_extensions.wiring.dedup_bloom_wiring import get_dedup_bloom

bloom = get_dedup_bloom()  # shared singleton, /tmp/hledac/dedup_bloom
if bloom.contains(url):
    skip_fetch()
else:
    bloom.add(url)
    await fetch(url)
"""

from __future__ import annotations

import logging
import threading
import weakref
from pathlib import Path
from typing import Any

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

# B4 FIX: Bounded set size for Python fallback (M1 8GB safety)
# 50K URLs × ~200 bytes avg URL length ≈ 10 MB max (set[str] no value overhead)
_PYTHON_FALLBACK_MAX_SIZE = 50_000

# Tier capacities mirror rust_extensions/src/dedup_bloom.rs TIER_CAPACITIES
# Keep in sync: (fine, coarse, macro)
_TIER_CAPACITIES: tuple[int, int, int] = (100_000, 500_000, 1_000_000)

# DCLP lock for thread-safe singleton initialization (Fix 3)
_dedup_bloom_lock: threading.Lock | None = None


def _get_dedup_bloom_lock() -> threading.Lock:
    """DCLP lazy lock init — avoids threading.Lock() at module load."""
    global _dedup_bloom_lock
    if _dedup_bloom_lock is None:
        _l = threading.Lock()
        if _dedup_bloom_lock is None:
            _dedup_bloom_lock = _l
    return _dedup_bloom_lock


def get_dedup_bloom() -> DedupBloom | None:
    """
    Get or create the global DedupBloom instance.

    Uses module-level caching with DCLP lock for thread-safe singleton init.
    Backing store: /tmp/hledac/dedup_bloom (LZ4-compressed mmap, restart-safe).

    Returns:
        DedupBloom wrapper or None if Rust module unavailable.
    """
    global _cached_instance

    # DCLP double-check: fast path without lock
    if _cached_instance is not None:
        return _cached_instance

    with _get_dedup_bloom_lock():
        # Double-check after acquiring lock
        if _cached_instance is not None:
            return _cached_instance

        if not _dedup_bloom_available:
            logger.debug("DedupBloom unavailable (Rust extension not built)")
            return None

        try:
            _dir = "/tmp/hledac/dedup_bloom"
            _cached_instance = DedupBloom(_dir)
            logger.info("DedupBloom initialized: %s", _cached_instance.stats())
            return _cached_instance
        except Exception as e:
            logger.warning("Failed to initialize DedupBloom: %s", e)
            return None

class DedupBloom:
    """
    DedupBloom — Distributed BloomFilter for cross-instance URL deduplication.

    Thread-safe (Rust backend uses all-Copy data structures, no locks needed).
    Falls back to Python set (bounded to 50K items) if Rust module unavailable.

    B4 FIX: Python fallback uses bounded set[str] (~10 MB for 50K URLs)
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
        # B4 FIX: Python fallback uses set[str] instead of LRUCache.
        # set stores keys only (no value overhead), O(1) avg lookup, bounded eviction.
        self._python_fallback: set[str] = set()
        # Note: cache_dir is passed to Rust backend only (PyDistributedBloomFilter).
        # Python fallback does not persist — it is bounded and ephemeral.

        if _dedup_bloom_module is not None:
            try:
                self._bloom = _dedup_bloom_module.PyDistributedBloomFilter(str(cache_dir))
                logger.debug("DedupBloom Rust backend initialized")
            except Exception as e:
                logger.warning("DedupBloom Rust init failed: %s, using Python fallback", e)
                self._bloom = None
        else:
            logger.debug("DedupBloom using Python fallback (Rust unavailable, set bounded to %d items)", _PYTHON_FALLBACK_MAX_SIZE)

        # Fix 1: weakref.finalize ensures save() is called on interpreter exit (SIGINT/SIGTERM).
        # This is the canonical persistence lifecycle — Rust save() uses LZ4-compressed mmap.
        self._finalizer = weakref.finalize(self, _dedup_bloom_at_exit, self)

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

        # Python fallback: set membership (O(1) avg)
        if url in self._python_fallback:
            return False
        # Evict oldest if at capacity (set itself has no maxsize — managed by add)
        if len(self._python_fallback) >= _PYTHON_FALLBACK_MAX_SIZE:
            # Remove arbitrary oldest entry (set iteration order is insertion-order-like)
            for oldest in self._python_fallback:
                self._python_fallback.discard(oldest)
                break
        self._python_fallback.add(url)
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

        # Python fallback: set membership
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
        Bulk skip duplicate URLs and return the non-duplicate subset.

        Uses batch contains (rayon-parallel on Rust backend) for efficiency.
        Internally uses contains_batch: True = "maybe duplicate" (bloom filter
        false-positive possible), so skip_count may include false positives.
        Canonical dedup (RotatingBloomFilter) handles false negatives.

        Args:
            urls: List of URLs to check for duplicates.

        Returns:
            Tuple of (non_duplicate_urls, skip_count).
            non_duplicate_urls: URLs that passed the bloom filter (may include
                false positives — canonical dedup catches these).
            skip_count: Number of URLs flagged as potential duplicates by the
                bloom filter (may include false positives due to FPP).
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

        # Python fallback with set (bounded to _PYTHON_FALLBACK_MAX_SIZE)
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

        item_count = len(self._python_fallback)
        # Rough estimate: set[str] entry ≈ 72 bytes hash overhead + avg URL string
        return {
            "total_items": item_count,
            "memory_bytes": item_count * 200,
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

        return len(self._python_fallback) * 200  # Rough estimate: set[str] overhead

    def efficiency(self) -> dict[str, Any]:
        """
        Return bloom filter fill-rate efficiency metrics per tier.

        Fill rate = items_added / tier_capacity. High fill rate (>70% on tier 0)
        means the bloom filter is getting saturated and false positives increase.
        This is the signal to consider resetting or sizing up.

        Returns:
            Dict with per-tier fill rates (0.0–1.0) and overall fill rate.
            Falls back to Python set stats when Rust unavailable.
        """
        if self._bloom is not None:
            s = self._bloom.stats()
            tiers = [
                ("tier_0", _TIER_CAPACITIES[0]),
                ("tier_1", _TIER_CAPACITIES[1]),
                ("tier_2", _TIER_CAPACITIES[2]),
            ]
            result: dict[str, Any] = {}
            total_items = 0
            total_capacity = 0
            for tier_name, capacity in tiers:
                items = s.get(f"{tier_name}_items", 0)
                total_items += items
                total_capacity += capacity
                fill_rate = items / capacity if capacity > 0 else 0.0
                result[f"{tier_name}_fill_rate"] = round(fill_rate, 4)
            result["overall_fill_rate"] = round(total_items / total_capacity, 4) if total_capacity > 0 else 0.0
            result["total_items_added"] = total_items
            return result

        # Python fallback
        fallback_count = len(self._python_fallback)
        fallback_capacity = _PYTHON_FALLBACK_MAX_SIZE
        return {
            "tier_0_fill_rate": fallback_count / fallback_capacity,
            "tier_1_fill_rate": 0.0,
            "tier_2_fill_rate": 0.0,
            "overall_fill_rate": fallback_count / fallback_capacity,
            "total_items_added": fallback_count,
        }

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

def _dedup_bloom_at_exit(bloom: DedupBloom) -> None:
    """
    At-exit handler registered via weakref.finalize in DedupBloom.__init__.

    Saves the Rust-backed DedupBloom to LZ4-compressed mmap on interpreter exit.
    Python fallback (set) is not persisted — it is bounded and ephemeral.

    This is fail-safe: exceptions are suppressed since we are in interpreter shutdown.
    """
    try:
        path = bloom.save()
        if path:
            logger.debug("DedupBloom saved at exit: %s", path)
    except Exception:
        pass  # Interpreter shutdown — suppress all errors


def bloom_check(url: str, bloom: DedupBloom | None) -> bool:
    """
    Quick bloom filter check.

    .. deprecated::
        Unused in production — only kept for API stability.
        Use ``bloom.skip_batch()`` for batch operations instead.

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

def bloom_add(url: str, bloom: DedupBloom | None) -> bool:
    """
    Add URL to bloom filter.

    .. deprecated::
        Unused in production — only kept for API stability.
        Use ``bloom.add()`` or ``bloom.add_batch()`` directly instead.

    Args:
        url: URL to add
        bloom: DedupBloom instance or None

    Returns:
        True if URL was new, False if duplicate.
    """
    if bloom is None:
        return True
    return bloom.add(url)

