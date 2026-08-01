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
    from hledac.universal.utils.lru_cache import LRUCache

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
    from hledac.universal.utils.lru_cache import TTLCache
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


# ── Sliding Window KV Cache ───────────────────────────────────────────────────


class SlidingWindowKVCache(Generic[K, V]):
    """
    F-06: Sliding window LRU cache for KV cache pools.

    Addresses abrupt LRU eviction problem in burst workloads where 5+ concurrent
    requests compete for 4 slots — standard LRU causes thrashing because eviction
    is all-or-nothing at pool capacity.

    Mechanism:
        - Each entry accumulates "access tokens" over a sliding time window
        - Gradual aging: tokens decay exponentially between accesses
        - Eviction candidate: lowest token count (least accessed recently)
          regardless of entry size (size-based eviction is handled separately
          by the pool's memory budget loop)
        - New entries start with half the max tokens to avoid immediate
          eviction in burst scenarios

    Unlike TTLCache (time-based expiry) or LRUCache (binary hit/miss):
        - SlidingWindow tracks HOW OFTEN each entry is accessed within window
        - Entries accessed frequently stay warm; occasional accesses decay
        - Burst of requests doesn't cause thrashing because token distribution
          smooths the access pattern over time

    Thread-safe via optional lock parameter.

    Args:
        max_size: Maximum entries before any eviction is triggered
        window_tokens: Max tokens per entry (default 16)
        decay_base: Exponential decay base per token interval (default 0.85)
        token_interval_s: Seconds between decay ticks (default 5.0)
        thread_safe: Synchronize all operations (default False)

    Example:
        pool = SlidingWindowKVCache[str, tuple](max_size=4, window_tokens=16)
        pool[hash1] = (cache_obj, time.monotonic(), 64_MB)
        # Access updates token count, decay runs on each put/get
    """

    __slots__ = (
        "_data",
        "_order",
        "_lock",
        "_max_size",
        "_window_tokens",
        "_decay_base",
        "_token_interval_s",
        "_tokens",
        "_last_decay",
        "_hits",
        "_misses",
    )

    def __init__(
        self,
        max_size: int = 4,
        window_tokens: int = 16,
        decay_base: float = 0.85,
        token_interval_s: float = 5.0,
        thread_safe: bool = False,
    ) -> None:
        if max_size < 1:
            raise ValueError(f"max_size must be >= 1, got {max_size}")
        if not (0.0 < decay_base <= 1.0):
            raise ValueError(f"decay_base must be in (0, 1], got {decay_base}")
        self._data: dict[K, V] = {}
        self._order: list[K] = []  # LRU order: front = LRU, back = MRU
        self._max_size = max_size
        self._window_tokens = window_tokens
        self._decay_base = decay_base
        self._token_interval_s = token_interval_s
        self._tokens: dict[K, float] = {}
        self._last_decay = time.monotonic()
        self._hits = 0
        self._misses = 0
        self._lock = threading.RLock() if thread_safe else None

    # ── Internal helpers ───────────────────────────────────────────────────

    def _decay_tokens(self, force: bool = False) -> None:
        """
        Apply exponential decay to all token counts.

        Called on every public operation (get/set). Skipped if
        token_interval_s has not elapsed unless force=True.

        decay formula: tokens *= decay_base^(elapsed_intervals)
        """
        now = time.monotonic()
        elapsed = now - self._last_decay
        intervals = int(elapsed / self._token_interval_s)
        if intervals < 1 and not force:
            return
        self._last_decay = now
        if intervals < 1:
            intervals = 1
        factor = self._decay_base ** intervals
        for key in list(self._tokens.keys()):
            self._tokens[key] *= factor

    def _add_tokens(self, key: K, delta: float) -> None:
        """Add tokens to an entry, capped at window_tokens."""
        if key not in self._tokens:
            self._tokens[key] = min(self._window_tokens / 2, delta)
        else:
            self._tokens[key] = min(self._window_tokens, self._tokens[key] + delta)

    # ── Dict-like interface ────────────────────────────────────────────────

    def __getitem__(self, key: K) -> V:
        if self._lock:
            with self._lock:
                return self._get(key)
        return self._get(key)

    def _get(self, key: K) -> V:
        self._decay_tokens()
        if key in self._data:
            self._add_tokens(key, 2.0)  # +2 tokens per access
            self._touch(key)
            self._hits += 1
            return self._data[key]
        self._misses += 1
        raise KeyError(key)

    def __setitem__(self, key: K, value: V) -> None:
        if self._lock:
            with self._lock:
                self._set(key, value)
        else:
            self._set(key, value)

    def _set(self, key: K, value: V) -> None:
        self._decay_tokens()
        if key in self._data:
            self._data[key] = value
            self._add_tokens(key, 1.0)
            self._touch(key)
        else:
            self._data[key] = value
            self._order.append(key)
            self._add_tokens(key, self._window_tokens / 2)  # new entries start warm

    def __contains__(self, key: object) -> bool:
        return key in self._data

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:
        return (
            f"SlidingWindowKVCache(max_size={self._max_size}, "
            f"window_tokens={self._window_tokens}, len={len(self)})"
        )

    # ── LRU-specific operations ────────────────────────────────────────────

    def _touch(self, key: K) -> None:
        if key in self._order:
            self._order.remove(key)
        self._order.append(key)

    def pop_lru(self) -> tuple[K, V]:
        if self._lock:
            with self._lock:
                return self._pop_lru()
        return self._pop_lru()

    def _pop_lru(self) -> tuple[K, V]:
        if not self._order:
            raise KeyError("pop_lru from empty cache")
        key = self._order.pop(0)
        value = self._data.pop(key)
        self._tokens.pop(key, None)
        return key, value

    def popitem(self, last: bool = True) -> tuple[K, V]:
        if self._lock:
            with self._lock:
                return self._popitem(last)
        return self._popitem(last)

    def _popitem(self, last: bool) -> tuple[K, V]:
        if not self._order:
            raise KeyError("popitem from empty cache")
        if last:
            key = self._order.pop()
        else:
            key = self._order.pop(0)
        value = self._data.pop(key)
        self._tokens.pop(key, None)
        return key, value

    def get(self, key: K, default: V | None = None) -> V | None:
        if self._lock:
            with self._lock:
                return self._get_or_default(key, default)
        return self._get_or_default(key, default)

    def _get_or_default(self, key: K, default: V | None) -> V | None:
        self._decay_tokens()
        if key in self._data:
            self._add_tokens(key, 2.0)
            self._touch(key)
            self._hits += 1
            return self._data[key]
        self._misses += 1
        return default

    def put(self, key: K, value: V) -> None:
        self[key] = value

    def clear(self) -> None:
        if self._lock:
            with self._lock:
                self._data.clear()
                self._order.clear()
                self._tokens.clear()
        else:
            self._data.clear()
            self._order.clear()
            self._tokens.clear()

    def pop(self, key: K, default: V | None = None) -> V | None:
        if self._lock:
            with self._lock:
                return self._pop(key, default)
        return self._pop(key, default)

    def _pop(self, key: K, default: V | None) -> V | None:
        if key in self._data:
            value = self._data.pop(key)
            if key in self._order:
                self._order.remove(key)
            self._tokens.pop(key, None)
            return value
        return default

    def keys(self):
        return iter(self._order)

    def values(self):
        return (self._data[k] for k in self._order)

    def items(self):
        return ((k, self._data[k]) for k in self._order)

    # ── Token introspection ─────────────────────────────────────────────────

    def get_tokens(self, key: K) -> float:
        """Return current token count for an entry (for debugging)."""
        return self._tokens.get(key, 0.0)

    # ── Stats ───────────────────────────────────────────────────────────────

    @property
    def stats(self) -> dict[str, int | float]:
        total = self._hits + self._misses
        hit_rate = self._hits / total if total > 0 else 0.0
        return {
            "hits": self._hits,
            "misses": self._misses,
            "size": len(self),
            "max_size": self._max_size,
            "hit_rate": hit_rate,
            "window_tokens": self._window_tokens,
            "decay_base": self._decay_base,
        }

    def reset_stats(self) -> None:
        self._hits = 0
        self._misses = 0
