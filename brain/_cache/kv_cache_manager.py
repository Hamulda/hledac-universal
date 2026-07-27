"""
kv_cache_manager.py — KV Cache Manager
=====================================

PEP 698: Extracted from DeepHermes3Engine.
Manages prefix cache, session cache, and KV pool for M1 8GB UMA.

Three-tier cache hierarchy:
1. PrefixCache — System prompt caching (prefill optimization)
2. SessionCache — Per-session KV cache (formatted prompt key)
3. KVCachePool — LRU pool for KV cache tensors

M1 8GB Bounds:
- KV pool: max 4 items, 256MB memory
- Session cache: max 8 items, 128MB memory
- Prefix cache: max 64 items
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from hledac.universal.utils.lru_cache import LRUCache

if TYPE_CHECKING:
    from typing import Callable

# Type for KV cache value: (cache_tensor, size_bytes, timestamp)
_KVCacheValue = tuple[Any, int, float]


@dataclass(frozen=True)
class KVCacheStats:
    """Immutable snapshot of KV cache statistics."""
    pool_size: int = 0
    pool_maxsize: int = 0
    session_cache_size: int = 0
    session_cache_maxsize: int = 0
    prefix_cache_size: int = 0
    prefix_cache_maxsize: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    cache_prefills: int = 0


@dataclass
class KVCacheManager:
    """
    Unified KV cache management for Metal model inference.

    Extracted from DeepHermes3Engine to eliminate scattered cache logic.
    Provides single interface for:
    - Prefix caching (system prompt prefill)
    - Session caching (per-prompt KV cache)
    - KV pool management (tensor pool)

    M1 8GB UMA safe: Bounded sizes, memory tracking, pressure-aware eviction.
    """

    # KV Pool config
    kv_pool_maxsize: int = 4
    kv_pool_memory_mb: int = 256

    # Session cache config
    session_cache_maxsize: int = 8
    session_cache_memory_mb: int = 128

    # Prefix cache config
    prefix_cache_maxsize: int = 64

    # Internal state
    _kv_cache_pool: LRUCache[str, _KVCacheValue] = field(default_factory=None)
    _session_cache_pool: LRUCache[str, tuple[Any, str, float, int]] = field(default_factory=None)
    _prefix_cache: LRUCache[str, Any] = field(default_factory=None)
    _kv_cache_stats: dict[str, int] = field(default_factory=None)
    _session_cache_stats: dict[str, int] = field(default_factory=None)
    _prefix_cache_stats: dict[str, int] = field(default_factory=None)
    _key_locks: dict[str, threading.RLock] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock)

    def __post_init__(self) -> None:
        """Initialize cache pools with bounded sizes."""
        self._kv_cache_pool = LRUCache(max_size=self.kv_pool_maxsize)
        self._session_cache_pool = LRUCache(max_size=self.session_cache_maxsize)
        self._prefix_cache = LRUCache(max_size=self.prefix_cache_maxsize)

        self._kv_cache_stats = {
            'cache_uses': 0,
            'cache_prefills': 1,
            'quantized_count': 0,
        }
        self._session_cache_stats = {
            'session_cache_hits': 0,
            'session_cache_misses': 0,
        }
        self._prefix_cache_stats = {
            'prefix_cache_maxsize': self.prefix_cache_maxsize,
            'prefix_cache_hits': 0,
            'prefix_cache_misses': 0,
        }

    # ========================================================================
    # Prefix Cache Methods
    # ========================================================================

    def get_prefix_cache(self, system_prompt: str) -> Any | None:
        """
        Get cached prefix cache for system prompt.

        Args:
            system_prompt: System prompt string

        Returns:
            Cached prefix cache tensor or None
        """
        import xxhash
        key = xxhash.xxh3_64_hex(system_prompt)[:16]
        result = self._prefix_cache.get(key)
        if result is not None:
            self._prefix_cache_stats['prefix_cache_hits'] += 1
        else:
            self._prefix_cache_stats['prefix_cache_misses'] += 1
        return result

    def put_prefix_cache(self, system_prompt: str, cache: Any) -> None:
        """Store prefix cache for system prompt."""
        import xxhash
        key = xxhash.xxh3_64_hex(system_prompt)[:16]
        self._prefix_cache.put(key, cache)

    def invalidate_prefix_cache(self) -> None:
        """Clear all prefix caches."""
        self._prefix_cache.clear()

    # ========================================================================
    # Session Cache Methods
    # ========================================================================

    def get_session_cache(self, formatted_prompt: str) -> tuple[Any, str] | None:
        """
        Get cached KV cache for formatted prompt.

        Args:
            formatted_prompt: Formatted prompt string (ChatML)

        Returns:
            Tuple of (kv_cache, cache_key) or None
        """
        import xxhash
        key = xxhash.xxh3_64_hex(formatted_prompt)[:16]

        result = self._session_cache_pool.get(key)
        if result is not None:
            self._session_cache_stats['session_cache_hits'] += 1
            return result[0], result[1]  # kv_cache, cache_key
        self._session_cache_stats['session_cache_misses'] += 1
        return None

    def store_session_cache(
        self,
        formatted_prompt: str,
        kv_cache: Any,
        cache_size: int,
    ) -> None:
        """
        Store KV cache for formatted prompt.

        Args:
            formatted_prompt: Formatted prompt
            kv_cache: KV cache tensor
            cache_size: Size in bytes
        """
        import xxhash
        key = xxhash.xxh3_64_hex(formatted_prompt)[:16]
        self._session_cache_pool.put(
            key,
            (kv_cache, key, self._current_time(), cache_size)
        )

    def _current_time(self) -> float:
        """Get current timestamp."""
        import time
        return time.time()

    # ========================================================================
    # KV Pool Methods
    # ========================================================================

    def get_kv_pool_item(self, key: str) -> tuple[Any, float, int] | None:
        """
        Get item from KV pool.

        Args:
            key: Cache key

        Returns:
            Tuple of (kv_cache, timestamp, size_bytes) or None
        """
        return self._kv_cache_pool.get(key)  # type: ignore

    def put_kv_pool_item(
        self,
        key: str,
        kv_cache: Any,
        size_bytes: int,
    ) -> bool:
        """
        Put item into KV pool with memory bounds.

        Args:
            key: Cache key
            kv_cache: KV cache tensor
            size_bytes: Size in bytes

        Returns:
            True if stored successfully
        """
        # Check memory bounds
        total_size = self._estimate_pool_memory()
        if total_size + size_bytes > self.kv_pool_memory_mb * 1024 * 1024:
            # Evict oldest items
            self._evict_kv_pool_items(2)

        return self._kv_cache_pool.put(key, (kv_cache, self._current_time(), size_bytes))

    def _estimate_pool_memory(self) -> int:
        """Estimate current pool memory usage."""
        total = 0
        for item in self._kv_cache_pool._cache.values():  # type: ignore
            if isinstance(item, tuple) and len(item) >= 3:
                total += item[2]  # size_bytes
        return total

    def _evict_kv_pool_items(self, count: int) -> None:
        """Evict oldest items from KV pool."""
        for _ in range(min(count, len(self._kv_cache_pool._cache))):  # type: ignore
            oldest_key = self._kv_cache_pool._cache.keys()[-1]  # type: ignore
            self._kv_cache_pool.remove(oldest_key)

    def compress_kv_cache(self) -> bool:
        """
        Trigger KV cache compression if available.

        M1 8GB: Reduces memory footprint by quantizing KV tensors.
        """
        # Placeholder for compression logic
        # Would use kv_bits=4 quantization here
        return True

    def prune_kv_cache(self) -> bool:
        """
        Prune KV cache based on LRU policy.

        M1 8GB: Called when memory pressure detected.
        """
        # Evict oldest 50%
        current_size = len(self._kv_cache_pool._cache)  # type: ignore
        evict_count = max(1, current_size // 2)
        self._evict_kv_pool_items(evict_count)
        return True

    # ========================================================================
    # Statistics
    # ========================================================================

    def get_stats(self) -> KVCacheStats:
        """Get comprehensive cache statistics."""
        return KVCacheStats(
            pool_size=len(self._kv_cache_pool._cache),  # type: ignore
            pool_maxsize=self.kv_pool_maxsize,
            session_cache_size=len(self._session_cache_pool._cache),  # type: ignore
            session_cache_maxsize=self.session_cache_maxsize,
            prefix_cache_size=len(self._prefix_cache._cache),  # type: ignore
            prefix_cache_maxsize=self.prefix_cache_maxsize,
            cache_hits=self._session_cache_stats['session_cache_hits'] + self._prefix_cache_stats['prefix_cache_hits'],
            cache_misses=self._session_cache_stats['session_cache_misses'] + self._prefix_cache_stats['prefix_cache_misses'],
            cache_prefills=self._kv_cache_stats['cache_prefills'],
        )

    def invalidate_all_caches(self, reason: str) -> None:
        """
        Invalidate all caches with logging.

        Args:
            reason: Reason for invalidation (debugging)
        """
        self._prefix_cache.clear()
        self._session_cache_pool.clear()
        self._kv_cache_pool.clear()


# Singleton accessor
_kv_cache_manager_instance: KVCacheManager | None = None


def get_kv_cache_manager() -> KVCacheManager:
    """Get singleton KVCacheManager instance."""
    global _kv_cache_manager_instance
    if _kv_cache_manager_instance is None:
        _kv_cache_manager_instance = KVCacheManager()
    return _kv_cache_manager_instance


# Convenience class aliases
PrefixCache = KVCacheManager  # type: ignore[misc]
SessionCache = KVCacheManager  # type: ignore[misc]
