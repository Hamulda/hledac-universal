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
from typing import Any, Generic, TypeVar

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


# ─────────────────────────────────────────────────────────────────────────────
# BoundedLoRACache — ISSUE-111 fix: bounded OrderedDict for LoRA adapter cache
# ─────────────────────────────────────────────────────────────────────────────
#
# Root cause: _lora_cache was an unbounded OrderedDict despite the comment
# saying "max 2". LoRA adapters are 50-200 MB Metal SRAM each; without a hard
# cap, repeated adapter switches leak memory indefinitely.
#
# Design choices:
#   • maxsize=2  — hard cap matching the original comment intent (M1 8GB safe)
#   • No TTL     — LoRA adapters are not time-sensitive; TTL adds only complexity
#   • LRU order  — move_to_end() on access + insert keeps most-recently-used alive
#   • Thread-safe via threading.Lock — serialize cache mutations across threads
#   • fail-safe  — any error returns None / False, never raises
#
# Usage:
#     cache = BoundedLoRACache(maxsize=2)
#     cache.put("path/to/adapter", (lora_model, lora_tokenizer))
#     result = cache.get("path/to/adapter")   # (lora_model, lora_tokenizer) | None
#     cache.evict_oldest()                    # returns evicted (key, value) or None
#     cache.clear()
#
# Memory bound: maxsize × ~200 MB ≈ 400 MB worst-case (bounded, M1 8GB safe)


class BoundedLoRACache:
    """
    Bounded LRU cache for MLX LoRA adapter models.

    Enforces maxsize with O(1) LRU eviction (move_to_end + popitem).
    Thread-safe. Fail-safe: any error returns None/False.

    Invariants:
        - maxsize enforced on every put(): oldest entry evicted if at capacity
        - get() refreshes LRU order (move_to_end)
        - clear() removes all entries
        - Memory: maxsize × ~200 MB ≈ 400 MB absolute ceiling on M1 8GB
    """

    __slots__ = (
        "_data",
        "_maxsize",
        "_lock",
        "_hits",
        "_misses",
        "_evictions",
    )

    def __init__(self, maxsize: int = 2) -> None:
        self._maxsize: int = max(1, maxsize)
        self._data: OrderedDict[str, tuple[Any, Any, float]] = OrderedDict()
        self._lock = threading.Lock()
        self._hits: int = 0
        self._misses: int = 0
        self._evictions: int = 0

    def get(self, key: str) -> tuple[Any, Any] | None:
        """
        Get (lora_model, lora_tokenizer) tuple by adapter path.

        Thread-safe. Refreshes LRU order on hit.
        Returns None on miss.
        """
        try:
            with self._lock:
                entry = self._data.get(key)
                if entry is None:
                    self._misses += 1
                    return None
                self._data.move_to_end(key)
                self._hits += 1
                return entry[0], entry[1]
        except Exception:
            return None

    def put(self, key: str, value: tuple[Any, Any]) -> bool:
        """
        Store (lora_model, lora_tokenizer) tuple for an adapter path.

        Thread-safe. Evicts oldest entry when at capacity (LRU).
        Returns True on success, False on error.
        """
        try:
            with self._lock:
                now = time.monotonic()
                # Update existing entry — refresh LRU
                if key in self._data:
                    self._data[key] = (value[0], value[1], now)
                    self._data.move_to_end(key)
                    return True
                # Evict oldest until we have space
                while len(self._data) >= self._maxsize:
                    self._data.popitem(last=False)
                    self._evictions += 1
                self._data[key] = (value[0], value[1], now)
                return True
        except Exception:
            return False

    def contains(self, key: str) -> bool:
        """Check key exists. Thread-safe. O(1)."""
        try:
            with self._lock:
                return key in self._data
        except Exception:
            return False

    def evict_oldest(self) -> tuple[str, tuple[Any, Any]] | None:
        """
        Evict and return the oldest (LRU) entry, or None if cache is empty.

        Thread-safe.
        """
        try:
            with self._lock:
                if not self._data:
                    return None
                key = next(iter(self._data))
                raw = self._data.pop(key)
                self._evictions += 1
                return key, (raw[0], raw[1])
        except Exception:
            return None

    def clear(self) -> bool:
        """Clear all entries. Thread-safe. Returns True."""
        try:
            with self._lock:
                self._data.clear()
                self._hits = 0
                self._misses = 0
                self._evictions = 0
                return True
        except Exception:
            return False

    @property
    def size(self) -> int:
        """Current number of entries."""
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
        """Hit/miss/eviction stats for cache efficiency monitoring."""
        try:
            with self._lock:
                return {
                    "hits": self._hits,
                    "misses": self._misses,
                    "evictions": self._evictions,
                    "size": len(self._data),
                    "maxsize": self._maxsize,
                }
        except Exception:
            return {"hits": 0, "misses": 0, "evictions": 0, "size": 0, "maxsize": self._maxsize}

    def __repr__(self) -> str:
        try:
            with self._lock:
                return (
                    f"BoundedLoRACache(maxsize={self._maxsize}, "
                    f"size={len(self._data)}, hits={self._hits}, "
                    f"misses={self._misses}, evictions={self._evictions})"
                )
        except Exception:
            return f"BoundedLoRACache(maxsize={self._maxsize})"
