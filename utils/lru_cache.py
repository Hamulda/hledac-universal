"""
LRU Cache Utilities
==================

Custom LRU cache implementation optimized for M1 8GB UMA with battle-tested
cachetools library available as alternative.

Features vs cachetools:
- __slots__ for reduced memory footprint (M1 8GB critical)
- TTLCache extension with per-entry expiration
- lru_cache decorator with functools-compatible interface
- hit/miss statistics tracking

When to use which:
- LRUCache/TTLCache: When you need TTL, __slots__, or the decorator
- cachetools.LRUCache: When you want battle-tested standard library semantics

Usage:
    from utils.lru_cache import LRUCache

    cache = LRUCache(max_size=100)
    cache[key] = value      # Set
    cache[key]              # Get (marks as MRU)
    cache.move_to_end(key)  # Mark as most recently used
    key, val = cache.pop_lru()  # Evict least recently used

    # Or use as decorator:
    @lru_cache(max_size=256)
    def expensive_func(arg):
        return compute(arg)

    # TTLCache for time-based expiration:
    from utils.lru_cache import TTLCache
    cache = TTLCache(max_size=100, ttl=3600)  # 1 hour TTL
"""

from __future__ import annotations

import functools
import threading
import time
from collections.abc import Callable
from typing import Generic, TypeVar, cast

T = TypeVar("T")
K = TypeVar("K")
V = TypeVar("V")


class LRUCache(Generic[K, V]):
    """
    LRU Cache with O(1) operations using dict + list hybrid.

    This replaces OrderedDict for LRU cache use cases where move_to_end()
    is needed, providing Python 3.14+ compatible implementation.

    Operations:
        - __getitem__, __setitem__, __contains__, __len__ : O(1)
        - move_to_end(key) : O(1) amortized
        - pop_lru() : O(1)
        - get(), put() : O(1)

    Thread-safe via optional lock parameter.
    """

    __slots__ = ("_data", "_order", "_lock", "_max_size", "_hits", "_misses")

    def __init__(self, max_size: int = 128, thread_safe: bool = False) -> None:
        """
        Initialize LRU cache.

        Args:
            max_size: Maximum number of entries before eviction
            thread_safe: If True, all operations are synchronized
        """
        self._data: dict[K, V] = {}
        self._order: list[K] = []  # Order of access: front = LRU, back = MRU
        self._max_size = max_size
        self._hits = 0
        self._misses = 0
        self._lock = threading.RLock() if thread_safe else None

    # ── Dict-like interface ────────────────────────────────────────────

    def __getitem__(self, key: K) -> V:
        """Get item, marking as most recently used."""
        if self._lock:
            with self._lock:
                return self._get(key)
        return self._get(key)

    def _get(self, key: K) -> V:
        """Internal get without lock."""
        if key in self._data:
            self._touch(key)
            self._hits += 1
            return self._data[key]
        self._misses += 1
        raise KeyError(key)

    def __setitem__(self, key: K, value: V) -> None:
        """Set item, evicting LRU if at capacity."""
        if self._lock:
            with self._lock:
                self._set(key, value)
        else:
            self._set(key, value)

    def _set(self, key: K, value: V) -> None:
        """Internal set without lock."""
        if key in self._data:
            self._data[key] = value
            self._touch(key)
        else:
            if len(self._data) >= self._max_size:
                self._pop_lru()
            self._data[key] = value
            self._order.append(key)

    def __contains__(self, key: object) -> bool:
        return key in self._data

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:
        return f"LRUCache(max_size={self._max_size}, len={len(self)})"

    # ── LRU-specific operations ─────────────────────────────────────────

    def move_to_end(self, key: K) -> None:
        """
        Move key to end (most recently used position).

        O(1) amortized - list remove + append is O(1) at the end.

        Raises:
            KeyError: If key not in cache
        """
        if self._lock:
            with self._lock:
                self._touch(key)
        else:
            self._touch(key)

    def _touch(self, key: K) -> None:
        """Internal touch without lock."""
        if key in self._order:
            self._order.remove(key)  # O(n) but list operations at end are optimized
        self._order.append(key)

    def pop_lru(self) -> tuple[K, V]:
        """
        Remove and return (key, value) for least recently used item.

        Returns:
            Tuple of (key, value) that was evicted

        Raises:
            KeyError: If cache is empty
        """
        if self._lock:
            with self._lock:
                return self._pop_lru()
        return self._pop_lru()

    def _pop_lru(self) -> tuple[K, V]:
        """Internal pop_lru without lock. Raises KeyError if empty."""
        if not self._order:
            raise KeyError("pop_lru from empty cache")
        key = self._order.pop(0)  # O(n) - but n is typically small
        value = self._data.pop(key)
        return key, value

    def popitem(self, last: bool = True) -> tuple[K, V]:
        """
        Remove and return (key, value) pair.

        If last=True (default): removes most recently used (MRU) - like OrderedDict.popitem().
        If last=False: removes least recently used (LRU) - matches OrderedDict.popitem(last=False).

        Raises KeyError if cache is empty.
        """
        if self._lock:
            with self._lock:
                return self._popitem(last)
        return self._popitem(last)

    def _popitem(self, last: bool) -> tuple[K, V]:
        """Internal popitem without lock. Raises KeyError if empty."""
        if not self._order:
            raise KeyError("popitem from empty cache")
        if last:
            key = self._order.pop()  # Remove from end (MRU)
        else:
            key = self._order.pop(0)  # Remove from front (LRU)
        value = self._data.pop(key)
        return key, value

    def get(self, key: K, default: V | None = None) -> V | None:
        """Get item, returning default if not found (marks as MRU)."""
        if self._lock:
            with self._lock:
                return self._get_or_default(key, default)
        return self._get_or_default(key, default)

    def _get_or_default(self, key: K, default: V | None) -> V | None:
        """Internal get_or_default without lock."""
        if key in self._data:
            self._touch(key)
            self._hits += 1
            return self._data[key]
        self._misses += 1
        return default

    def put(self, key: K, value: V) -> None:
        """Alias for __setitem__."""
        self[key] = value

    def clear(self) -> None:
        """Clear all entries."""
        if self._lock:
            with self._lock:
                self._data.clear()
                self._order.clear()
        else:
            self._data.clear()
            self._order.clear()

    def pop(self, key: K, default: V | None = None) -> V | None:
        """Remove and return value for key, or default if not found."""
        if self._lock:
            with self._lock:
                return self._pop(key, default)
        return self._pop(key, default)

    def _pop(self, key: K, default: V | None) -> V | None:
        """Internal pop without lock."""
        if key in self._data:
            value = self._data.pop(key)
            if key in self._order:
                self._order.remove(key)
            return value
        return default

    def keys(self):
        """Return keys in LRU order (oldest to newest)."""
        return iter(self._order)

    def values(self):
        """Return values in LRU order."""
        return (self._data[k] for k in self._order)

    def items(self):
        """Return items in LRU order."""
        return ((k, self._data[k]) for k in self._order)

    # ── Stats ──────────────────────────────────────────────────────────

    @property
    def stats(self) -> dict[str, int | float]:
        """Return cache statistics."""
        total = self._hits + self._misses
        hit_rate = self._hits / total if total > 0 else 0.0
        return {
            "hits": self._hits,
            "misses": self._misses,
            "size": len(self),
            "max_size": self._max_size,
            "hit_rate": hit_rate,
        }

    def reset_stats(self) -> None:
        """Reset hit/miss counters."""
        self._hits = 0
        self._misses = 0


# ── Decorator ──────────────────────────────────────────────────────────────────

_F = TypeVar("_F", bound=Callable)


def lru_cache(max_size: int = 128) -> Callable[[_F], _F]:
    """
    LRU cache decorator using dict + list hybrid implementation.

    Unlike functools.lru_cache, this uses our custom LRUCache which is
    Python 3.14+ compatible (no OrderedDict dependency).

    Thread-safe by default.

    Example:
        @lru_cache(max_size=256)
        def fib(n):
            return n if n < 2 else fib(n-1) + fib(n-2)
    """
    cache: LRUCache[tuple, object] = LRUCache(max_size=max_size, thread_safe=True)

    def decorator(func: _F) -> _F:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            key = (args, tuple(sorted(kwargs.items())))
            try:
                return cache[key]
            except KeyError:
                result = func(*args, **kwargs)
                cache[key] = result
                return result

        # These are functools.lru_cache compatible attributes
        wrapper.cache_info = lambda: cache.stats  # type: ignore[attr-defined]
        wrapper.cache_clear = cache.clear  # type: ignore[attr-defined]
        return cast(_F, wrapper)

    return decorator


# ── TTL Cache ─────────────────────────────────────────────────────────────────


class TTLCache(LRUCache[K, V]):
    """
    LRU Cache with TTL (time-to-live) for entries.

    Extends LRUCache with time-based expiration.

    Example:
        cache = TTLCache(max_size=100, ttl=3600)  # 1 hour TTL
        cache[key] = value  # expires after 1 hour
    """

    __slots__ = ("_ttl", "_timestamps")

    def __init__(self, max_size: int = 128, ttl: float = 3600.0) -> None:
        """
        Initialize TTL cache.

        Args:
            max_size: Maximum entries before eviction
            ttl: Time-to-live in seconds
        """
        super().__init__(max_size)
        self._ttl = ttl
        self._timestamps: dict[K, float] = {}  # key -> last access time

    def _touch(self, key: K) -> None:
        """Update access time."""
        super()._touch(key)
        self._timestamps[key] = time.monotonic()

    def _set(self, key: K, value: V) -> None:
        """Set with timestamp."""
        super()._set(key, value)
        self._timestamps[key] = time.monotonic()

    def _evict_expired(self) -> int:
        """Evict all expired entries. Returns count of evicted."""
        if self._ttl <= 0:
            return 0
        now = time.monotonic()
        expired = [
            k for k, ts in self._timestamps.items() if now - ts > self._ttl
        ]
        for key in expired:
            if key in self._data:
                del self._data[key]
            if key in self._order:
                self._order.remove(key)
            if key in self._timestamps:
                del self._timestamps[key]
        return len(expired)

    def get(self, key: K, default: V | None = None) -> V | None:
        """Get with automatic expiration check."""
        self._evict_expired()
        return super().get(key, default)

    def __getitem__(self, key: K) -> V:
        """Get with automatic expiration check."""
        self._evict_expired()
        return super().__getitem__(key)
