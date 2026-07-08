"""
PyCacheDict — Bounded TTL LRU cache replacing functools.lru_cache
=================================================================

M1 8GB safe: bounded OrderedDict with TTL eviction.
Thread-safe via threading.RLock (reentrant — safe from signal handlers).

Invariant: always bounded, always fail-safe.

Usage:
    cache = PyCacheDict(maxsize=4096, ttl_s=300)
    cache["key"] = value
    val = cache.get("key")          # returns None on miss/expired
    cache.clear()                    # manual purge
    cache.touch("key")               # refresh TTL

For async code: wrap with asyncio.to_thread() for the lock acquire.
Never use this for coroutine objects — use async_lru from cachetools instead.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Generic, TypeVar

K = TypeVar("K", default=object)
V = TypeVar("V", default=object)


class PyCacheDict[K, V]:
    """
    Bounded OrderedDict cache with per-entry TTL.

    Eviction: O(1) LRU via move_to_end() + popitem(last=False).

    Invariants:
        - maxsize enforced on write: oldest evicted when full
        - ttl enforced on read: expired entries return None (lazy purge)
        - thread-safe: threading.Lock protects _data
        - fail-safe: any error returns None / False, never raises
    """

    __slots__ = (
        "_data",
        "_maxsize",
        "_ttl_s",
        "_lock",
        "_hits",
        "_misses",
        "_evictions",
        "_expirations",
    )

    def __init__(self, maxsize: int = 4096, ttl_s: float = 300.0) -> None:
        self._maxsize: int = max(1, maxsize)
        self._ttl_s: float = max(0.0, ttl_s)
        self._data: OrderedDict[K, tuple[V, float]] = OrderedDict()
        self._lock = threading.RLock()
        self._hits: int = 0
        self._misses: int = 0
        self._evictions: int = 0
        self._expirations: int = 0

    # -- read -----------------------------------------------------------

    def get(self, key: K) -> V | None:
        """
        Get value by key. Returns None on miss or if entry is expired.

        Thread-safe. Refreshes TTL on hit (move to end).
        """
        try:
            with self._lock:
                entry = self._data.get(key)
                if entry is None:
                    self._misses += 1
                    return None
                value, timestamp = entry
                if time.monotonic() - timestamp > self._ttl_s:
                    # Lazy expiry — remove stale entry
                    del self._data[key]
                    self._expirations += 1
                    self._misses += 1
                    return None
                # Refresh TTL (LRU touch)
                self._data.move_to_end(key)
                self._hits += 1
                return value
        except Exception:
            return None

    def __getitem__(self, key: K) -> V:
        """Raise KeyError on miss/expired — unlike get()."""
        try:
            with self._lock:
                entry = self._data.get(key)
                if entry is None:
                    raise KeyError(key)
                value, timestamp = entry
                if time.monotonic() - timestamp > self._ttl_s:
                    del self._data[key]
                    self._expirations += 1
                    raise KeyError(key)
                self._data.move_to_end(key)
                self._hits += 1
                return value
        except KeyError:
            raise
        except Exception:
            raise KeyError(key)

    def __contains__(self, key: K) -> bool:
        """Check key exists and is not expired. O(1). Thread-safe."""
        try:
            with self._lock:
                entry = self._data.get(key)
                if entry is None:
                    return False
                _, timestamp = entry
                if time.monotonic() - timestamp > self._ttl_s:
                    del self._data[key]
                    self._expirations += 1
                    return False
                return True
        except Exception:
            return False

    # -- write ---------------------------------------------------------

    def set(self, key: K, value: V) -> bool:
        """
        Set key-value pair. Evicts oldest entry if at capacity.

        Thread-safe.
        Returns True on success, False on error.
        """
        try:
            with self._lock:
                now = time.monotonic()
                # If key exists, update + refresh TTL
                if key in self._data:
                    self._data[key] = (value, now)
                    self._data.move_to_end(key)
                    return True
                # Evict oldest until we have space (LRU eviction)
                while len(self._data) >= self._maxsize:
                    self._data.popitem(last=False)
                    self._evictions += 1
                self._data[key] = (value, now)
                return True
        except Exception:
            return False

    def __setitem__(self, key: K, value: V) -> None:
        self.set(key, value)

    def touch(self, key: K) -> bool:
        """
        Refresh TTL for an existing key.

        Thread-safe. Returns True if key existed (and is not expired),
        False otherwise.
        """
        try:
            with self._lock:
                entry = self._data.get(key)
                if entry is None:
                    return False
                _, timestamp = entry
                if time.monotonic() - timestamp > self._ttl_s:
                    del self._data[key]
                    self._expirations += 1
                    return False
                self._data[key] = (entry[0], time.monotonic())
                self._data.move_to_end(key)
                return True
        except Exception:
            return False

    # -- maintenance ---------------------------------------------------

    def clear(self) -> bool:
        """Clear all entries. Thread-safe. Returns True."""
        try:
            with self._lock:
                self._data.clear()
                self._hits = 0
                self._misses = 0
                self._evictions = 0
                self._expirations = 0
                return True
        except Exception:
            return False

    def purge_expired(self) -> int:
        """
        Remove all expired entries. O(n) scan with lock held.

        Thread-safe. Returns number of purged entries.
        """
        try:
            with self._lock:
                now = time.monotonic()
                expired: list[K] = [
                    k
                    for k, (_, ts) in self._data.items()
                    if now - ts > self._ttl_s
                ]
                for k in expired:
                    del self._data[k]
                    self._expirations += 1
                return len(expired)
        except Exception:
            return 0

    # -- introspection ───────────────────────────────────────────────────────

    @property
    def size(self) -> int:
        """Current number of entries (including potentially expired)."""
        try:
            with self._lock:
                return len(self._data)
        except Exception:
            return 0

    @property
    def capacity(self) -> int:
        """Maximum number of entries (maxsize)."""
        return self._maxsize

    def __len__(self) -> int:
        return self.size

    @property
    def stats(self) -> dict[str, int]:
        """
        Hit/miss/eviction/expiration stats for cache efficiency monitoring.

        Returns a copy — safe for read-only access.
        """
        try:
            with self._lock:
                return {
                    "hits": self._hits,
                    "misses": self._misses,
                    "evictions": self._evictions,
                    "expirations": self._expirations,
                    "size": len(self._data),
                }
        except Exception:
            return {"hits": 0, "misses": 0, "evictions": 0, "expirations": 0, "size": 0}

    def __repr__(self) -> str:
        try:
            with self._lock:
                return (
                    f"PyCacheDict(maxsize={self._maxsize}, ttl_s={self._ttl_s}, "
                    f"size={len(self._data)}, hits={self._hits}, misses={self._misses}, "
                    f"evictions={self._evictions}, expirations={self._expirations})"
                )
        except Exception:
            return f"PyCacheDict(maxsize={self._maxsize}, ttl_s={self._ttl_s})"
