"""
Async-Safe Cache Implementations
================================


Async-aware caches with per-key single-flight locks for preventing
duplicate concurrent computation.

Classes
-------
AsyncLRUCache[T, U]:  Bounded LRU cache with per-key asyncio.Lock
async_cached:         Decorator for async function memoization
cached_awaitable:      Utility for explicit get-or-compute patterns

Features
--------
- Per-key asyncio.Lock prevents thundering-herd on cache miss
- Single-flight pattern: only ONE task computes a given key
- Lazy lock creation (ISSUE-014 pattern: no locks at module import)
- Bounded memory (maxsize parameter)
- Dict/list args raise AsyncCacheError immediately (fail-fast)

M1 8GB bounds:
- Per-key lock dict capped at 512 entries max (~512KB)
- Cache: user-specified maxsize

Usage
-----
    from hledac.universal.utils.cache import async_cached, AsyncLRUCache

    # Decorator (per-key locks, bounded)
    @async_cached(maxsize=256)
    async def expensive_fetch(key: str) -> dict:
        return await _do_fetch(key)

    # Direct cache instance
    cache = AsyncLRUCache(maxsize=512)
    async with cache.acquire(key) as result:
        if result is None:
            result = await compute(key)
        return result

NOTE
----
Why not cachetools/aiocache?
- cachetools: No async support, no per-key locking
- aiocache: Redis/Memcached backends required, not in deps
This module provides pure stdlib asyncio + functools.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, TypeVar

from ._sync import LRUCache

if TYPE_CHECKING:
    pass

T = TypeVar("T")
U = TypeVar("U")

__all__ = ["AsyncLRUCache", "AsyncCacheError", "async_cached", "cached_awaitable"]


class AsyncCacheError(Exception):
    """Raised on async cache misuse (e.g., dict argument to async_cached)."""


class AsyncLRUCache[T, U]:
    """
    Bounded LRU cache with per-key asyncio locks for async single-flight.

    M1 8GB: per-key lock dict capped at 512 entries to avoid memory bloat.

    Unlike functools.lru_cache:
    - IS async-aware (awaitable values)
    - Per-key locking prevents thundering-herd on cache miss
    - Bounded memory (maxsize parameter)
    - Dict args raise AsyncCacheError immediately (fail-fast)
    """

    __slots__ = (
        "_maxsize",
        "_cache",
        "_locks",
        "_lock_order",
        "_max_locks",
        "_on_evict",
    )

    def __init__(
        self,
        maxsize: int,
        *,
        max_locks: int = 512,
        on_evict: Callable[[T, U], None] | None = None,
    ) -> None:
        if maxsize <= 0:
            raise ValueError(f"maxsize must be positive, got {maxsize}")
        self._maxsize = maxsize
        self._cache: LRUCache[T, U] = LRUCache(max_size=maxsize)
        # Per-key locks: lazy (None at init, created on first use)
        self._locks: dict[T, asyncio.Lock | None] = {}
        # LRU order tracker for lock dict pruning
        self._lock_order: list[T] = []
        # Cap on lock dict size to bound M1 RAM
        self._max_locks = max_locks
        # Optional callback on evicted items
        self._on_evict = on_evict

    async def get(self, key: T) -> U | None:
        """Get value from cache. Returns None if not found."""
        # Fail-fast on unhashable keys
        self._check_hashable(key)
        lock = await self._lock_for(key)
        async with lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
            return None

    async def set(self, key: T, value: U) -> None:
        """Set value in cache, evicting LRU entry if at capacity."""
        self._check_hashable(key)
        lock = await self._lock_for(key)
        async with lock:
            await self._set_unchecked(key, value)

    async def _set_unchecked(self, key: T, value: U) -> None:
        """Set value in cache WITHOUT acquiring lock. Caller must hold the lock."""
        if key in self._cache:
            self._cache.move_to_end(key)
            self._cache[key] = value
            return
        if len(self._cache) >= self._maxsize:
            evicted_key, evicted_value = self._cache.pop_lru()
            if self._on_evict is not None:
                try:
                    self._on_evict(evicted_key, evicted_value)
                except Exception:  # noqa: BLE001
                    pass
        self._cache[key] = value
        self._cache.move_to_end(key)
        # Track lock order for LRU lock eviction
        if key not in self._lock_order:
            self._lock_order.append(key)

    async def acquire(self, key: T, *, compute: Callable[[], Awaitable[U]] | None = None) -> U:
        """
        Get-or-compute with single-flight guarantee.

        If key in cache → return cached value
        Else if compute provided → await compute(), cache result, return
        Else → raise KeyError

        Multiple coroutines calling acquire(key) simultaneously will all
        wait for the SAME compute() call (single-flight pattern).
        """
        self._check_hashable(key)

        # Fast path: check cache WITHOUT lock
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]

        lock = await self._lock_for(key)
        async with lock:
            # Double-check after acquiring lock (another task may have cached it)
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]

            if compute is None:
                raise KeyError(key)

            try:
                result = await compute()
            except Exception as e:
                # Don't cache errors — let caller retry
                raise AsyncCacheError(f"compute() raised {type(e).__name__}: {e}") from e

            await self._set_unchecked(key, result)
            return result

    def clear(self) -> None:
        """Clear all entries and locks."""
        self._cache.clear()
        self._locks.clear()
        self._lock_order.clear()

    def __len__(self) -> int:
        return len(self._cache)

    def __contains__(self, key: object) -> bool:
        return key in self._cache

    def _check_hashable(self, key: T) -> None:
        """Fail-fast on unhashable keys (e.g., dict) instead of silent TypeError."""
        try:
            hash(key)
        except TypeError as e:
            raise AsyncCacheError(
                f"Cache key must be hashable, got {type(key).__name__}. Use a tuple or frozenset instead of dict/list."
            ) from e

    async def _lock_for(self, key: T) -> asyncio.Lock:
        """Get or create a lock for a specific key."""
        # Fast path: already have a non-None lock
        existing = self._locks.get(key)
        if existing is not None:
            return existing
        return await self._get_or_create_lock(key)

    async def _get_or_create_lock(self, key: T) -> asyncio.Lock:
        """Lazily create a per-key lock (ISSUE-014 pattern: no locks at module import)."""
        # Quick check without lock
        existing: asyncio.Lock | None = self._locks.get(key)
        if existing is not None:
            return existing

        # Verify running event loop (ISSUE-014 pattern)
        asyncio.get_running_loop()

        # Create lock (no loop param needed in Python 3.10+)
        new_lock: asyncio.Lock = asyncio.Lock()

        # Serialize lock creation (check again in case another task created it)
        if key not in self._locks:
            self._locks[key] = new_lock
            self._lock_order.append(key)
        elif self._locks[key] is None:
            self._locks[key] = new_lock
            self._lock_order.append(key)
        else:
            # Another task created the lock first — discard ours and return theirs
            existing = self._locks[key]
            return existing if existing is not None else new_lock

        # Prune lock dict if over max_locks (evict oldest)
        while len(self._locks) > self._max_locks:
            oldest = self._lock_order.pop(0)
            self._locks.pop(oldest, None)

        return new_lock

    def __repr__(self) -> str:
        return f"AsyncLRUCache(maxsize={self._maxsize}, len={len(self._cache)}, locks={len(self._locks)})"


# Global registry of decorator instances (for cache_clear)
_decorator_caches: list[AsyncLRUCache[Any, Any]] = []


def async_cached(
    maxsize: int = 256,
    *,
    cache: AsyncLRUCache[Any, Any] | None = None,
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """
    Async-safe memoization decorator with per-key single-flight locks.

    Decorator usage (recommended):
        @async_cached(maxsize=256)
        async def expensive_fetch(url: str) -> dict:
            return await _do_fetch(url)

    Instance reuse:
        _fetch_cache = AsyncLRUCache(maxsize=512)

        @async_cached(cache=_fetch_cache)
        async def fetch(url: str) -> dict:
            return await _do_fetch(url)

    Differences from functools.lru_cache:
    - Works ONLY with async def functions (raises TypeError on sync def)
    - Per-key asyncio.Lock prevents duplicate concurrent computation
    - Bounded cache size (LRU eviction)
    - Dict/list arguments raise AsyncCacheError immediately (not TypeError later)
    - Cache is per-decorator-instance (shared across all calls)

    M1 8GB bounds:
    - maxsize default 256 (configurable)
    - Per-key locks capped at 512
    """
    if cache is None:
        _cache: AsyncLRUCache[Any, Any] = AsyncLRUCache(maxsize=maxsize)
    else:
        _cache = cache
    _decorator_caches.append(_cache)

    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        func_name = getattr(func, "__qualname__", getattr(func, "__name__", "<unknown>"))
        if not inspect.iscoroutinefunction(func):
            raise AsyncCacheError(
                f"async_cached can only decorate async def functions. "
                f"Got sync function: {func_name}. "
                f"Use functools.lru_cache for sync functions."
            )

        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            # Build cache key from args (same policy as lru_cache)
            try:
                key = (args, tuple(sorted(kwargs.items()))) if kwargs else args
                hash(key)  # Fail-fast check
            except TypeError as e:
                raise AsyncCacheError(
                    f"async_cached argument must be hashable. "
                    f"Got args={args!r}, kwargs={kwargs!r}. "
                    f"Use frozenset or tuple instead of dict/list."
                ) from e

            async def _call() -> T:
                return await func(*args, **kwargs)

            return await _cache.acquire(key, compute=_call)

        # Provide cache_clear for test isolation (only used in tests)
        # These are dynamically added; ty can't see them through functools.wraps
        wrapper.cache_clear = _cache.clear  # type: ignore[arg-type]
        wrapper.__cache__ = _cache  # type: ignore[arg-type]

        return wrapper

    return decorator


async def cached_awaitable[U](
    cache: AsyncLRUCache[Any, U],
    key: Any,
    compute: Callable[[], Awaitable[U]],
) -> U:
    """
    Get-or-compute via AsyncLRUCache.acquire().
    Convenience wrapper for explicit cache + key + compute patterns.
    """
    return await cache.acquire(key, compute=compute)
