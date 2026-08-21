"""
Graph Cache Wiring - ISSUE-007
=============================

Wires rust_extensions/src/graph_cache.rs to its Python integration points.

Rust Module: rust_extensions/src/graph_cache.rs
Feature: Shared LRU cache s TinyLFU admission policy
Purpose: Cross-sprint persistence malých graph result setů

Integration Points:
------------------
1. knowledge/graph_service.py - Graph traversal query caching
2. knowledge/graph/query_cache.py - TTL-aware cache layer

API (from Rust):
-----------------
- PyGraphLRUCache: Shared LRU cache with TinyLFU admission
  - PyGraphLRUCache(max_entries, max_bytes) - constructor
  - get(key: str) -> Option<Vec<u8>> - cache lookup
  - put(key: str, value: Vec<u8>) -> bool - cache insert (returns false if rejected)
  - remove(key: str) -> Option<Vec<u8>> - explicit removal
  - clear() - clear all entries
  - contains_key(key: str) -> bool - check if key exists
  - len() -> usize - number of entries
  - is_empty() -> bool - check if empty
  - stats() -> HashMap - cache statistics

Architecture:
--------------
- TinyLFU admission policy: Only admits items with higher frequency than current minimum
- Count-Min Sketch for frequency estimation (4 rows × 8192 buckets = 32KB)
- LRU eviction when at capacity
- Thread-safe via Arc<Mutex<>> (M1 8GB safe)

M1 8GB bounds:
  MAX_ENTRIES = 50,000
  MAX_BYTES = 50 * 1024 * 1024 (50 MB)
  LOAD_FACTOR = 0.7

Usage:
-------
from rust_extensions.wiring.graph_cache_wiring import GraphCache

cache = GraphCache(max_entries=50_000, max_bytes=50 * 1024 * 1024)
cache.put("key", b"value")
if result := cache.get("key"):
    print(result)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

from hledac.universal._core.rust_backend import rust as _rust_backend


def _rust_available() -> bool:
    """Check if Rust backend is available."""
    return _rust_backend is not None and _rust_backend.is_available


class GraphCache:
    """
    Shared LRU cache with TinyLFU admission policy.

    Thread-safe via Arc<Mutex<>>. Falls back to no-op when Rust unavailable.

    M1 8GB bounds:
      - max_entries: 50,000 default
      - max_bytes: 50 MB default
      - TinyLFU: 32KB Count-Min Sketch

    Args:
        max_entries: Maximum number of cached entries (default 50,000)
        max_bytes: Maximum cache size in bytes (default 50 MB)

    Example:
        >>> cache = GraphCache()
        >>> cache.put("my_key", b"my_value")
        True
        >>> cache.get("my_key")
        b'my_value'
        >>> cache.contains("my_key")
        True
    """

    __slots__ = ("_cache", "_available")

    def __init__(
        self,
        max_entries: int = 50_000,
        max_bytes: int = 50 * 1024 * 1024,
    ) -> None:
        self._cache = None
        self._available = _rust_available()

        if self._available:
            try:
                self._cache = _rust_backend.graph_cache.PyGraphLRUCache(max_entries, max_bytes)
                logger.debug(f"[GraphCache] Rust cache initialized: max_entries={max_entries}, max_bytes={max_bytes}")
            except Exception as e:
                logger.warning(f"[GraphCache] Failed to create Rust cache: {e}")
                self._available = False
                self._cache = None

    @property
    def available(self) -> bool:
        """Check if Rust cache is available."""
        return self._available and self._cache is not None

    def get(self, key: str) -> bytes | None:
        """
        Get value from cache.

        Args:
            key: Cache key

        Returns:
            Cached value as bytes, or None if not found / unavailable
        """
        if not self.available:
            return None

        try:
            result = self._cache.get(key)
            if result is not None:
                return bytes(result)
            return None
        except Exception as e:
            logger.debug(f"[GraphCache] get failed for key={key!r}: {e}")
            return None

    def put(self, key: str, value: bytes | str) -> bool:
        """
        Put value into cache.

        Args:
            key: Cache key
            value: Value to cache (bytes or str)

        Returns:
            True if cached, False if rejected (TinyLFU policy) / unavailable
        """
        if not self.available:
            return False

        try:
            if isinstance(value, str):
                value = value.encode("utf-8")
            elif not isinstance(value, bytes):
                value = bytes(value)

            return self._cache.put(key, list(value))
        except Exception as e:
            logger.debug(f"[GraphCache] put failed for key={key!r}: {e}")
            return False

    def remove(self, key: str) -> bytes | None:
        """
        Remove key from cache.

        Args:
            key: Cache key to remove

        Returns:
            Previous value if existed, or None
        """
        if not self.available:
            return None

        try:
            result = self._cache.remove(key)
            if result is not None:
                return bytes(result)
            return None
        except Exception as e:
            logger.debug(f"[GraphCache] remove failed for key={key!r}: {e}")
            return None

    def contains(self, key: str) -> bool:
        """
        Check if key exists in cache.

        Args:
            key: Cache key

        Returns:
            True if key exists
        """
        if not self.available:
            return False

        try:
            return self._cache.contains_key(key)
        except Exception as e:
            logger.debug(f"[GraphCache] contains failed for key={key!r}: {e}")
            return False

    def clear(self) -> None:
        """Clear all cache entries."""
        if not self.available:
            return

        try:
            self._cache.clear()
        except Exception as e:
            logger.debug(f"[GraphCache] clear failed: {e}")

    def len(self) -> int:
        """
        Get number of cached entries.

        Returns:
            Number of entries in cache
        """
        if not self.available:
            return 0

        try:
            return self._cache.len()
        except Exception:
            return 0

    def is_empty(self) -> bool:
        """
        Check if cache is empty.

        Returns:
            True if cache has no entries
        """
        if not self.available:
            return True

        try:
            return self._cache.is_empty()
        except Exception:
            return True

    def stats(self) -> dict[str, int]:
        """
        Get cache statistics.

        Returns:
            Dict with entries, bytes, max_entries, max_bytes
        """
        if not self.available:
            return {
                "entries": 0,
                "bytes": 0,
                "max_entries": 0,
                "max_bytes": 0,
            }

        try:
            return dict(self._cache.stats())
        except Exception as e:
            logger.debug(f"[GraphCache] stats failed: {e}")
            return {
                "entries": 0,
                "bytes": 0,
                "max_entries": 0,
                "max_bytes": 0,
            }


# Global cache instance - lazily initialized
_graph_cache: GraphCache | None = None


def get_graph_cache() -> GraphCache:
    """
    Get the singleton GraphCache instance.

    Returns:
        Global GraphCache instance
    """
    global _graph_cache
    if _graph_cache is None:
        _graph_cache = GraphCache()
    return _graph_cache


def reset_graph_cache() -> None:
    """Reset the global cache instance (for testing)."""
    global _graph_cache
    if _graph_cache is not None:
        _graph_cache.clear()
    _graph_cache = None


if _rust_available():
    try:
        _ = _rust_backend.graph_cache
        logger.info("[GraphCache] Rust graph_cache.rs integration: ENABLED")
    except AttributeError:
        logger.info("[GraphCache] Rust graph_cache.rs integration: DISABLED (module not in rust_backend)")
else:
    logger.info("[GraphCache] Rust graph_cache.rs integration: DISABLED (backend unavailable)")
