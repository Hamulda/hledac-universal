"""
Query Cache - TTL-aware Graph Traversal Cache
============================================


CACHE LAYER (B3): TTL-aware caching for graph traversal queries.

Architecture:
- Tier 0: Rust LRU cache (graph_cache.rs) via graph_cache_wiring.py
- TTL Layer: 5-minute TTL with intelligent invalidation
- IOC Invalidation: Cache cleared when new IOCs are added

Cache Keys:
-----------
- find_entity_history: f"history:{seed_value}:{max_hops}"
- find_connected_batch: f"batch:{hash(sorted(values))}:{max_hops}"

TTL:
----
- Default TTL: 5 minutes (300 seconds)
- Intelligent invalidation on IOC add

M1 8GB Safety:
---------------
- Uses shared Rust cache (50k entries, 50MB max)
- TTL cleanup runs lazily on next access
- No additional memory overhead

Usage:
-------
from knowledge.graph.query_cache import QueryCache, get_query_cache

cache = get_query_cache()
# Cache lookup
result = cache.get_history("1.2.3.4", max_hops=2)
# On IOC add - invalidate all history caches
cache.invalidate_on_ioc_add()
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# =============================================================================
# TTL Constants
# =============================================================================

# Default TTL: 5 minutes (matches requirement)
DEFAULT_TTL_SECONDS: int = 300

# Max TTL: 1 hour (safety cap)
MAX_TTL_SECONDS: int = 3600

# Cache entry with TTL metadata
_TTL_ENTRY_VERSION: int = 1


class TTLEntry:
    """
    TTL-aware cache entry.

    Stores value with expiration timestamp.
    """

    __slots__ = ("value", "expires_at")

    def __init__(self, value: bytes, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self.value = value
        self.expires_at: float = time.monotonic() + ttl_seconds

    def is_expired(self) -> bool:
        """Check if entry has expired."""
        return time.monotonic() > self.expires_at


# =============================================================================
# Query Cache
# =============================================================================


class QueryCache:
    """
    TTL-aware cache for graph traversal queries.

    Wraps Rust LRU cache with:
    - TTL enforcement (5-minute default)
    - IOC invalidation tracking
    - Query key generation

    Cache Keys:
    - find_entity_history: f"history:{seed_value}:{max_hops}"
    - find_connected_batch: f"batch:{hash(values)}:{max_hops}"

    Args:
        ttl_seconds: Default TTL for cache entries (default 300 = 5 minutes)
        max_entries: Max entries in Rust cache (default 50,000)
        max_bytes: Max bytes in Rust cache (default 50 MB)

    Example:
        >>> cache = QueryCache()
        >>> # Lookup with cache
        >>> result = cache.get_history("1.2.3.4", max_hops=2)
        >>> # Cache miss - fetch from DuckDB and cache
        >>> if result is None:
        ...     result = await graph_service.find_entity_history("1.2.3.4", 2)
        ...     cache.put_history("1.2.3.4", 2, serialize(result))
        >>> # On IOC add - invalidate
        >>> cache.invalidate_on_ioc_add()
    """

    __slots__ = (
        "_rust_cache",
        "_ttl_map",
        "_ttl_seconds",
        "_last_invalidation",
        "_hits",
        "_misses",
    )

    def __init__(
        self,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        max_entries: int = 50_000,
        max_bytes: int = 50 * 1024 * 1024,
    ) -> None:
        # Import Rust cache
        try:
            from hledac.universal.rust_extensions.wiring.graph_cache_wiring import GraphCache

            self._rust_cache = GraphCache(max_entries=max_entries, max_bytes=max_bytes)
        except ImportError:
            logger.warning("[QueryCache] graph_cache_wiring unavailable, cache disabled")
            self._rust_cache = None

        # TTL tracking: key -> expiration time (monotonic)
        self._ttl_map: dict[str, float] = {}

        # TTL duration
        self._ttl_seconds = min(ttl_seconds, MAX_TTL_SECONDS)

        # Track last invalidation for IOC changes
        self._last_invalidation: float = time.monotonic()

        # Statistics
        self._hits: int = 0
        self._misses: int = 0

    # ── Key Generation ──────────────────────────────────────────────────────

    @staticmethod
    def _make_history_key(seed_value: str, max_hops: int) -> str:
        """Generate cache key for find_entity_history."""
        return f"history:{seed_value}:{max_hops}"

    @staticmethod
    def _make_batch_key(values: list[str], max_hops: int) -> str:
        """Generate cache key for find_connected_batch."""
        # Sort for deterministic hash
        sorted_values = sorted(values)
        # Use MD5 for faster hashing (16 bytes = no truncation needed)
        # MD5 is fine here since we're only using it for cache key uniqueness
        values_hash = hashlib.md5(",".join(sorted_values).encode()).hexdigest()
        return f"batch:{values_hash}:{max_hops}"

    # ── TTL Enforcement ─────────────────────────────────────────────────────

    def _is_expired(self, key: str) -> bool:
        """Check if cached entry has expired."""
        if key not in self._ttl_map:
            return True
        return time.monotonic() > self._ttl_map[key]

    def _cleanup_expired(self) -> int:
        """Remove expired entries from TTL map. Returns count of cleaned entries."""
        now = time.monotonic()
        expired_keys = [k for k, exp in self._ttl_map.items() if now > exp]
        if not expired_keys:
            return 0
        for key in expired_keys:
            del self._ttl_map[key]
            # Also remove from Rust cache if exists
            if self._rust_cache is not None:
                self._rust_cache.remove(key)
        return len(expired_keys)

    def _set_ttl(self, key: str) -> None:
        """Set TTL for a cache key."""
        self._ttl_map[key] = time.monotonic() + self._ttl_seconds

    # ── Public API ──────────────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        """Check if Rust cache is available."""
        return self._rust_cache is not None and self._rust_cache.available

    @property
    def last_invalidation_age(self) -> float | None:
        """
        Get the age of the last invalidation in seconds.
        
        Returns:
            Seconds since last invalidation, or None if never invalidated.
        """
        if self._last_invalidation <= 0:
            return None
        return time.monotonic() - self._last_invalidation

    @property
    def stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        rust_stats = {}
        if self._rust_cache is not None:
            rust_stats = self._rust_cache.stats()

        return {
            "rust_available": self.available,
            "ttl_seconds": self._ttl_seconds,
            "ttl_entries": len(self._ttl_map),
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": self._hits / max(1, self._hits + self._misses),
            **rust_stats,
        }

    def get_history(self, seed_value: str, max_hops: int) -> bytes | None:
        """
        Get cached graph traversal result for find_entity_history.

        Args:
            seed_value: IOC value to query
            max_hops: Maximum traversal depth

        Returns:
            Cached result as bytes, or None if not found / expired
        """
        key = self._make_history_key(seed_value, max_hops)
        return self._get(key)

    def put_history(
        self, seed_value: str, max_hops: int, result: bytes
    ) -> bool:
        """
        Cache graph traversal result for find_entity_history.

        Args:
            seed_value: IOC value queried
            max_hops: Traversal depth
            result: Serialized result (bytes)

        Returns:
            True if cached successfully
        """
        key = self._make_history_key(seed_value, max_hops)
        return self._put(key, result)

    def get_batch(self, values: list[str], max_hops: int) -> bytes | None:
        """
        Get cached results for find_connected_batch.

        Args:
            values: List of IOC values queried
            max_hops: Maximum traversal depth

        Returns:
            Cached result as bytes, or None if not found / expired
        """
        key = self._make_batch_key(values, max_hops)
        return self._get(key)

    def put_batch(self, values: list[str], max_hops: int, result: bytes) -> bool:
        """
        Cache results for find_connected_batch.

        Args:
            values: List of IOC values queried
            max_hops: Traversal depth
            result: Serialized result (bytes)

        Returns:
            True if cached successfully
        """
        key = self._make_batch_key(values, max_hops)
        return self._put(key, result)

    def _get(self, key: str) -> bytes | None:
        """Internal cache get with TTL enforcement."""
        if not self.available:
            self._misses += 1
            return None

        # Lazy cleanup: check a few expired entries on each access
        # This keeps cleanup O(1) amortized
        if len(self._ttl_map) > 100:
            # Random cleanup: check up to 10 entries
            self._cleanup_expired()

        # Check TTL
        if self._is_expired(key):
            self._misses += 1
            return None

        # Get from Rust cache
        result = self._rust_cache.get(key)
        if result is None:
            self._misses += 1
            return None

        self._hits += 1
        return result

    def _put(self, key: str, value: bytes) -> bool:
        """Internal cache put with TTL tracking."""
        if not self.available:
            return False

        # Put in Rust cache
        success = self._rust_cache.put(key, value)
        if success:
            self._set_ttl(key)
        return success

    def invalidate_on_ioc_add(self) -> int:
        """
        Invalidate cache when new IOC is added.

        Conservative approach: clears all graph traversal caches.
        This ensures consistency when graph structure changes.

        Returns:
            Number of entries cleared
        """
        if self._rust_cache is None:
            return 0

        # Track invalidation time
        self._last_invalidation = time.monotonic()

        # Clear TTL map
        count = len(self._ttl_map)
        self._ttl_map.clear()

        # Clear Rust cache (only history/batch entries)
        # Note: We clear everything for simplicity - graph structure changed
        if self._rust_cache.available:
            self._rust_cache.clear()

        logger.debug(f"[QueryCache] Invalidated {count} entries on IOC add")
        return count

    def invalidate_pattern(self, pattern: str) -> int:
        """
        Invalidate cache entries matching a pattern.

        Args:
            pattern: Key pattern to match (prefix match)

        Returns:
            Number of entries invalidated
        """
        if not self.available:
            return 0

        # Find matching keys in TTL map
        matching_keys = [k for k in self._ttl_map if k.startswith(pattern)]
        for key in matching_keys:
            del self._ttl_map[key]
            self._rust_cache.remove(key)

        return len(matching_keys)

    def clear(self) -> None:
        """Clear all cached entries."""
        self._ttl_map.clear()
        if self._rust_cache is not None:
            self._rust_cache.clear()
        self._hits = 0
        self._misses = 0

    def reset_stats(self) -> None:
        """Reset hit/miss statistics."""
        self._hits = 0
        self._misses = 0


# =============================================================================
# Singleton Instance
# =============================================================================

_query_cache: QueryCache | None = None


def get_query_cache() -> QueryCache:
    """
    Get the singleton QueryCache instance.

    Returns:
        Global QueryCache instance
    """
    global _query_cache
    if _query_cache is None:
        _query_cache = QueryCache()
    return _query_cache


def reset_query_cache() -> None:
    """Reset the global cache instance (for testing)."""
    global _query_cache
    if _query_cache is not None:
        _query_cache.clear()
    _query_cache = None
