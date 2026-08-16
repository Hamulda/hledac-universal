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

Architecture (Sprint Split-Brain):
- KVCacheManager: Facade with thin delegation
- KVCacheEvictor: Pure eviction logic + MemoryPressureListener
- KVCacheStats: Immutable statistics snapshot
"""
from __future__ import annotations
import logging
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from hledac.universal.utils.lru_cache import LRUCache
from _core import aclose
if TYPE_CHECKING:
    from collections.abc import Callable
logger = logging.getLogger(__name__)
_KVCacheValue = tuple[Any, int, float]

@dataclass(frozen=True, slots=True)
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

class KVCacheEvictor:
    """
    Pure eviction logic + MemoryPressureListener for KV caches.

    Sprint Split-Brain: Extracted from KVCacheManager to isolate
    eviction policy from cache storage. Enables independent testing
    and future eviction algorithm swaps (LRU → LFU, ARC, etc.).

    MemoryPressureListener protocol (R8):
      - listener_priority = 1 (HIGH — large memory consumer)
      - listener_name = "kv_cache_evictor"
      - on_soft_warn: prune 50% of KV pool
      - on_warn: prune 75% of KV pool + clear session cache
      - on_critical: invalidate all caches
    """
    __slots__ = ('_kv_pool', '_session_pool', '_prefix_cache', '_kv_stats', '_session_stats', '_prefix_stats', '_key_locks', '_lock')

    def __init__(self, kv_pool: LRUCache, session_pool: LRUCache, prefix_cache: LRUCache) -> None:
        self._kv_pool = kv_pool
        self._session_pool = session_pool
        self._prefix_cache = prefix_cache
        self._kv_stats: dict[str, int] = {'cache_uses': 0, 'cache_prefills': 1, 'quantized_count': 0}
        self._session_stats: dict[str, int] = {'session_cache_hits': 0, 'session_cache_misses': 0}
        self._prefix_stats: dict[str, int] = {'prefix_cache_hits': 0, 'prefix_cache_misses': 0}
        self._key_locks: dict[str, threading.RLock] = {}
        self._lock = threading.RLock()

    def _evict_kv_pool_items(self, count: int) -> None:
        """Evict oldest `count` items from KV pool."""
        for _ in range(min(count, len(self._kv_pool))):
            try:
                self._kv_pool.pop((oldest := True))
            except KeyError:
                break

    def prune_kv_cache(self) -> bool:
        """Prune KV cache based on LRU policy (M1 8GB: called on memory pressure)."""
        evict_count = max(1, len(self._kv_pool) // 2)
        self._evict_kv_pool_items(evict_count)
        return True

    def invalidate_all(self, reason: str) -> None:
        """Invalidate all caches with logging."""
        self._prefix_cache.clear()
        self._session_pool.clear()
        self._kv_pool.clear()

    @property
    def listener_priority(self) -> int:
        return 1

    @property
    def listener_name(self) -> str:
        return 'kv_cache_evictor'

    def on_soft_warn(self) -> None:
        """R8: ELEVATED pressure — prune KV pool 50%, clear prefix cache."""
        pool_size = len(self._kv_pool)
        evict_count = max(1, pool_size // 2)
        self._evict_kv_pool_items(evict_count)
        self._prefix_cache.clear()
        logger.info('[KVCacheEvictor] on_soft_warn: pruned %d/%d KV pool', evict_count, pool_size)

    def on_warn(self) -> None:
        """R8: HIGH pressure — prune KV pool 75%, clear session + prefix caches."""
        pool_size = len(self._kv_pool)
        evict_count = max(1, pool_size * 3 // 4)
        self._evict_kv_pool_items(evict_count)
        self._session_pool.clear()
        self._prefix_cache.clear()
        logger.warning('[KVCacheEvictor] on_warn: pruned %d/%d KV pool', evict_count, pool_size)

    def on_critical(self) -> None:
        """R8: CRITICAL pressure — invalidate everything."""
        self.invalidate_all('critical_pressure')
        logger.critical('[KVCacheEvictor] on_critical: all caches invalidated')

    def on_normal(self) -> None:
        """R8: NORMAL pressure — no action needed."""
        pass

    def get_stats(self) -> KVCacheStats:
        """Get comprehensive cache statistics."""
        return KVCacheStats(pool_size=len(self._kv_pool), pool_maxsize=self._kv_pool.max_size, session_cache_size=len(self._session_pool), session_cache_maxsize=self._session_pool.max_size, prefix_cache_size=len(self._prefix_cache), prefix_cache_maxsize=self._prefix_cache.max_size, cache_hits=self._session_stats['session_cache_hits'] + self._prefix_stats['prefix_cache_hits'], cache_misses=self._session_stats['session_cache_misses'] + self._prefix_stats['prefix_cache_misses'], cache_prefills=self._kv_stats['cache_prefills'])

@dataclass(slots=True)
class KVCacheManager:
    """
    Unified KV cache management for Metal model inference.

    Facade delegating to specialized components:
    - KVCacheEvictor: eviction + memory pressure
    - LRUCache pools: storage (prefix, session, KV)

    M1 8GB UMA safe: Bounded sizes, memory tracking, pressure-aware eviction.
    """
    kv_pool_maxsize: int = 4
    kv_pool_memory_mb: int = 256
    session_cache_maxsize: int = 8
    session_cache_memory_mb: int = 128
    prefix_cache_maxsize: int = 64
    _kv_cache_pool: LRUCache[str, _KVCacheValue] = field(default=None)
    _session_cache_pool: LRUCache[str, tuple[Any, str, float, int]] = field(default=None)
    _prefix_cache: LRUCache[str, Any] = field(default=None)
    _evictor: KVCacheEvictor = field(default=None)

    def __post_init__(self, _kv_cache_pool: LRUCache | None=None, _session_cache_pool: LRUCache | None=None, _prefix_cache: LRUCache | None=None) -> None:
        """Initialize cache pools with bounded sizes."""
        self._kv_cache_pool = _kv_cache_pool or LRUCache(max_size=self.kv_pool_maxsize)
        self._session_cache_pool = _session_cache_pool or LRUCache(max_size=self.session_cache_maxsize)
        self._prefix_cache = _prefix_cache or LRUCache(max_size=self.prefix_cache_maxsize)
        self._evictor = KVCacheEvictor(self._kv_cache_pool, self._session_cache_pool, self._prefix_cache)
        self._register_with_broadcaster()

    def get_prefix_cache(self, system_prompt: str) -> Any | None:
        """Get cached prefix cache for system prompt."""
        import xxhash
        key = xxhash.xxh3_64_hexdigest(system_prompt)
        with self._evictor._lock:
            result = self._prefix_cache.get(key)
            if result is not None:
                self._evictor._prefix_stats['prefix_cache_hits'] += 1
            else:
                self._evictor._prefix_stats['prefix_cache_misses'] += 1
            return result

    def put_prefix_cache(self, system_prompt: str, cache_data: Any) -> None:
        """Store prefix cache for system prompt."""
        import xxhash
        key = xxhash.xxh3_64_hexdigest(system_prompt)
        with self._evictor._lock:
            self._prefix_cache.put(key, cache_data)

    def get_session_cache(self, session_key: str) -> tuple[Any, str, float, int] | None:
        """Get cached session KV cache."""
        with self._evictor._lock:
            result = self._session_cache_pool.get(session_key)
            if result is not None:
                self._evictor._session_stats['session_cache_hits'] += 1
            else:
                self._evictor._session_stats['session_cache_misses'] += 1
            return result

    def put_session_cache(self, session_key: str, cache_data: Any, prompt: str, timestamp: float, size_bytes: int) -> None:
        """Store session KV cache."""
        with self._evictor._lock:
            self._session_cache_pool.put(session_key, (cache_data, prompt, timestamp, size_bytes))

    def get_kv_cache(self, key: str) -> Any | None:
        """Get KV cache tensor from pool."""
        with self._evictor._lock:
            entry = self._kv_cache_pool.get(key)
            if entry is not None:
                cache_tensor, size_bytes, timestamp = entry
                self._evictor._kv_stats['cache_uses'] += 1
                return cache_tensor
            return None

    def put_kv_cache(self, key: str, cache_tensor: Any, size_bytes: int) -> bool:
        """Store KV cache tensor in pool."""
        with self._evictor._lock:
            if len(self._kv_cache_pool) >= self.kv_pool_maxsize:
                oldest = True
                try:
                    self._evictor._evict_kv_pool_items(1)
                except Exception:
                    pass
            self._kv_cache_pool.put(key, (cache_tensor, size_bytes, __import__('time').time()))
            self._evictor._kv_stats['cache_prefills'] += 1
            return True

    def prune_kv_cache(self) -> bool:
        """Prune KV cache based on LRU policy."""
        return self._evictor.prune_kv_cache()

    def get_stats(self) -> KVCacheStats:
        """Get comprehensive cache statistics."""
        return self._evictor.get_stats()

    def invalidate_all_caches(self, reason: str) -> None:
        """Invalidate all caches with logging."""
        self._evictor.invalidate_all(reason)

    @property
    def listener_priority(self) -> int:
        return self._evictor.listener_priority

    @property
    def listener_name(self) -> str:
        return self._evictor.listener_name

    def on_soft_warn(self) -> None:
        self._evictor.on_soft_warn()

    def on_warn(self) -> None:
        self._evictor.on_warn()

    def on_critical(self) -> None:
        self._evictor.on_critical()

    def on_normal(self) -> None:
        self._evictor.on_normal()

    def _register_with_broadcaster(self) -> None:
        """R8: Register with MemoryPressureBroadcaster. Fail-open."""
        try:
            from hledac.universal._core.memory_pressure import MemoryPressureBroadcaster
            broadcaster = MemoryPressureBroadcaster.get_instance()
            broadcaster.register(self)
        except Exception:
            pass
_kv_cache_manager_instance: KVCacheManager | None = None

def get_kv_cache_manager() -> KVCacheManager:
    """Get singleton KVCacheManager instance."""
    global _kv_cache_manager_instance
    if _kv_cache_manager_instance is None:
        _kv_cache_manager_instance = KVCacheManager()
    return _kv_cache_manager_instance
PrefixCache = KVCacheManager
SessionCache = KVCacheManager