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

import threading
import time
from collections import OrderedDict
from typing import Any, TypeVar

# Use unified LRUCache from new package
from hledac.universal.utils.cache import LRUCache

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

    def update(self, batch: dict[K, V]) -> None:
        """
        Bulk-insert key-value pairs in one lock acquisition.

        For keys that already exist: update value + refresh TTL.
        For new keys: evict LRU entries to make room, then insert.

        Thread-safe. Silently skips on error (fail-safe).
        """
        if not batch:
            return
        try:
            with self._lock:
                now = time.monotonic()
                for key, value in batch.items():
                    # If key exists: update + refresh TTL
                    if key in self._data:
                        self._data[key] = (value, now)
                        self._data.move_to_end(key)
                    else:
                        # Evict oldest until we have space (LRU eviction)
                        while len(self._data) >= self._maxsize:
                            self._data.popitem(last=False)
                            self._evictions += 1
                        self._data[key] = (value, now)
        except Exception:  # noqa: BLE001
            pass

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
                expired: list[K] = [k for k, (_, ts) in self._data.items() if now - ts > self._ttl_s]
                for k in expired:
                    del self._data[k]
                    self._expirations += 1
                return len(expired)
        except Exception:
            return 0

    def items(self) -> list[tuple[K, V]]:
        """
        Return list of (key, value) pairs, excluding expired.

        Thread-safe. O(n) scan.
        """
        try:
            with self._lock:
                now = time.monotonic()
                result: list[tuple[K, V]] = []
                expired: list[K] = []
                for k, (v, ts) in self._data.items():
                    if now - ts > self._ttl_s:
                        expired.append(k)
                    else:
                        result.append((k, v))
                for k in expired:
                    del self._data[k]
                    self._expirations += 1
                return result
        except Exception:
            return []

    def keys(self) -> list[K]:
        """Return list of keys, excluding expired. Thread-safe."""
        return [k for k, _ in self.items()]

    def values(self) -> list[V]:
        """Return list of values, excluding expired. Thread-safe."""
        return [v for _, v in self.items()]

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
# AsyncPyCacheDict — Async-safe bounded TTL LRU cache
# ─────────────────────────────────────────────────────────────────────────────
#
# ISSUE-13: async variant of PyCacheDict for async call sites.
# Uses lazy asyncio.Lock() pattern — critical on macOS where Lock() at module
# import time fails because there is no event loop in the importing thread.
#
# Design choices:
#   • maxsize / ttl_s — identical semantics to PyCacheDict
#   • asyncio.Lock via _get_lock() lazy helper — NEVER asyncio.Lock() at import
#   • all async methods: get, set, touch, clear, purge_expired
#   • __contains__ and stats are sync (they don't need the lock for reads)
#   • fail-safe: any error returns None / False / empty list, never raises
#   • Optional WeakValueDictionary backing for numpy array / embedding values
#     (Python 3.14: values auto-GC'd when only WVD holds them)
#
# Usage:
#     cache = AsyncPyCacheDict(maxsize=4096, ttl_s=300)
#     val = await cache.get("key")        # returns None on miss/expired
#     await cache.set("key", value)        # True on success
#     await cache.touch("key")             # True if key existed
#     await cache.clear()                  # True
#     await cache.purge_expired()          # returns count purged
#     # Optional: WVD-backed for numpy / embedding values
#     cache = AsyncPyCacheDict(weak_values=True)
#
# M1 8GB: same memory bounds as PyCacheDict — values are Python objects held
# in the OrderedDict; WeakValueDictionary only affects GC timing, not allocation.

import asyncio
import weakref
from collections import OrderedDict
from core import aclose

# K and V are already defined at module level (lines 26-27) for PyCacheDict.
# AsyncPyCacheDict reuses the same TypeVars — no redefinition needed.


class AsyncPyCacheDict[K, V]:
    """
    Async-safe bounded OrderedDict cache with per-entry TTL.

    Eviction: O(1) LRU via move_to_end() + popitem(last=False).
    Lock: lazy asyncio.Lock() — NEVER instantiate at module import time.

    Invariants:
        - maxsize enforced on write: oldest evicted when full
        - ttl enforced on read: expired entries return None (lazy purge)
        - async-safe: asyncio.Lock protects _data mutations
        - fail-safe: any error returns None / False / empty, never raises

    Python 3.14 note: pass weak_values=True to use
    WeakValueDictionary backing for auto-GC of values (numpy arrays,
    embeddings) when they are only held by the cache. The WVD is a
    secondary GC reference; the primary (key, (value, ts)) entries
    always live in the OrderedDict.
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
        "_weak_values",
        "_wvd_ref",
    )

    def __init__(
        self,
        maxsize: int = 4096,
        ttl_s: float = 300.0,
        *,
        weak_values: bool = False,
    ) -> None:
        self._maxsize: int = max(1, maxsize)
        self._ttl_s: float = max(0.0, ttl_s)
        self._weak_values: bool = weak_values
        # _data is ALWAYS OrderedDict — needed for LRU move_to_end/popitem
        self._data: OrderedDict[K, tuple[V, float]] = OrderedDict()
        # _wvd_ref: secondary weak reference for GC assist (numpy / embeddings)
        # Always a WVD instance when weak_values=True, otherwise None
        self._wvd_ref: weakref.WeakValueDictionary[K, V] | None = (
            weakref.WeakValueDictionary() if weak_values else None
        )
        # Lazy lock — NEVER asyncio.Lock() at import time (macOS crash vector)
        self._lock: asyncio.Lock | None = None
        # Stats
        self._hits: int = 0
        self._misses: int = 0
        self._evictions: int = 0
        self._expirations: int = 0

    # ── lock helper (lazy — critical on macOS) ─────────────────────────────

    async def _get_lock(self) -> asyncio.Lock:
        """
        Lazy lock acquisition — creates Lock on first await inside an event loop.

        This is the CORRECT pattern for asyncio.Lock in async classes.
        NEVER use self._lock = asyncio.Lock() at __init__ / module level.
        """
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    # ── internal helpers (WVD ops guarded by _weak_values check) ───────────

    def _wvd_delete(self, key: K) -> None:
        """Remove key from secondary WVD if active."""
        wvd = self._wvd_ref
        if wvd is not None and key in wvd:  # type: ignore[operator]
            del wvd[key]  # type: ignore[index]

    def _wvd_set(self, key: K, value: V) -> None:
        """Add value to secondary WVD if active."""
        wvd = self._wvd_ref
        if wvd is not None:
            wvd[key] = value  # type: ignore[index]

    # ── async read ────────────────────────────────────────────────────────

    async def get(self, key: K) -> V | None:
        """
        Get value by key. Returns None on miss or if entry is expired.

        Async-safe. Refreshes TTL on hit (move to end).
        """
        try:
            lock = await self._get_lock()
            async with lock:
                entry = self._data.get(key)
                if entry is None:
                    self._misses += 1
                    return None
                value, timestamp = entry
                if time.monotonic() - timestamp > self._ttl_s:
                    del self._data[key]
                    self._wvd_delete(key)
                    self._expirations += 1
                    self._misses += 1
                    return None
                self._data.move_to_end(key)
                self._hits += 1
                return value
        except Exception:
            return None

    async def set(self, key: K, value: V) -> bool:
        """
        Set key-value pair. Evicts oldest entry if at capacity.

        Async-safe. Returns True on success, False on error.
        """
        try:
            lock = await self._get_lock()
            async with lock:
                now = time.monotonic()
                if key in self._data:
                    self._data[key] = (value, now)
                    self._data.move_to_end(key)
                    self._wvd_set(key, value)
                    return True
                while len(self._data) >= self._maxsize:
                    evicted_key, _ = self._data.popitem(last=False)
                    self._wvd_delete(evicted_key)
                    self._evictions += 1
                self._data[key] = (value, now)
                self._wvd_set(key, value)
                return True
        except Exception:
            return False

    async def update(self, batch: dict[K, V]) -> None:
        """
        Bulk-insert key-value pairs in one lock acquisition.

        For keys that already exist: update value + refresh TTL.
        For new keys: evict LRU entries to make room, then insert.

        Async-safe. Silently skips on error (fail-safe).
        """
        if not batch:
            return
        try:
            lock = await self._get_lock()
            async with lock:
                now = time.monotonic()
                for key, value in batch.items():
                    if key in self._data:
                        self._data[key] = (value, now)
                        self._data.move_to_end(key)
                        self._wvd_set(key, value)
                    else:
                        while len(self._data) >= self._maxsize:
                            evicted_key, _ = self._data.popitem(last=False)
                            self._wvd_delete(evicted_key)
                            self._evictions += 1
                        self._data[key] = (value, now)
                        self._wvd_set(key, value)
        except Exception:  # noqa: BLE001
            pass

    async def touch(self, key: K) -> bool:
        """Refresh TTL for an existing key. Async-safe."""
        try:
            lock = await self._get_lock()
            async with lock:
                entry = self._data.get(key)
                if entry is None:
                    return False
                _, timestamp = entry
                if time.monotonic() - timestamp > self._ttl_s:
                    del self._data[key]
                    self._wvd_delete(key)
                    self._expirations += 1
                    return False
                self._data[key] = (entry[0], time.monotonic())
                self._data.move_to_end(key)
                self._wvd_set(key, entry[0])
                return True
        except Exception:
            return False

    # ── async maintenance ────────────────────────────────────────────────

    async def clear(self) -> bool:
        """Clear all entries. Async-safe."""
        try:
            lock = await self._get_lock()
            async with lock:
                self._data.clear()
                if self._wvd_ref is not None:
                    self._wvd_ref.clear()
                self._hits = 0
                self._misses = 0
                self._evictions = 0
                self._expirations = 0
                return True
        except Exception:
            return False

    async def purge_expired(self) -> int:
        """Remove all expired entries. Async-safe. Returns purge count."""
        try:
            lock = await self._get_lock()
            async with lock:
                now = time.monotonic()
                expired: list[K] = [
                    k for k, (_, ts) in self._data.items() if now - ts > self._ttl_s
                ]
                for k in expired:
                    del self._data[k]
                    self._wvd_delete(k)
                    self._expirations += 1
                return len(expired)
        except Exception:
            return 0

    # ── sync reads (no lock needed for stats / capacity) ─────────────────

    def __contains__(self, key: K) -> bool:
        """Check key exists and is not expired. O(1)."""
        try:
            entry = self._data.get(key)
            if entry is None:
                return False
            _, timestamp = entry
            if time.monotonic() - timestamp > self._ttl_s:
                return False
            return True
        except Exception:
            return False

    @property
    def size(self) -> int:
        """Current number of entries (including potentially expired)."""
        try:
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
        """Hit/miss/eviction/expiration stats."""
        try:
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
            return (
                f"AsyncPyCacheDict(maxsize={self._maxsize}, ttl_s={self._ttl_s}, "
                f"size={len(self._data)}, hits={self._hits}, misses={self._misses}, "
                f"evictions={self._evictions}, expirations={self._expirations}, "
                f"weak_values={self._weak_values})"
            )
        except Exception:
            return f"AsyncPyCacheDict(maxsize={self._maxsize}, ttl_s={self._ttl_s})"


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

    Enforces maxsize with O(1) LRU eviction via LRUCache.
    Thread-safe (LRUCache internal lock). Fail-safe: any error returns None/False.

    Invariants:
        - maxsize enforced on every put(): oldest entry evicted if at capacity
        - get() refreshes LRU order (move_to_end)
        - clear() removes all entries
        - Memory: maxsize × ~200 MB ≈ 400 MB absolute ceiling on M1 8GB
    """

    __slots__ = (
        "_cache",
        "_maxsize",
        "_hits",
        "_misses",
        "_evictions",
    )

    def __init__(self, maxsize: int = 2) -> None:
        self._maxsize: int = max(1, maxsize)
        self._cache: LRUCache[str, tuple[Any, Any]] = LRUCache(max_size=maxsize, thread_safe=True)
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
            entry = self._cache.get(key)
            if entry is None:
                self._misses += 1
                return None
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
            # If key exists, update value
            if key in self._cache:
                self._cache[key] = value
                return True
            # Evict oldest until we have space
            while len(self._cache) >= self._maxsize:
                try:
                    self._cache.popitem(last=False)
                    self._evictions += 1
                except KeyError:
                    break
            self._cache[key] = value
            return True
        except Exception:
            return False

    def contains(self, key: str) -> bool:
        """Check key exists. Thread-safe. O(1)."""
        try:
            return key in self._cache
        except Exception:
            return False

    def evict_oldest(self) -> tuple[str, tuple[Any, Any]] | None:
        """
        Evict and return the oldest (LRU) entry, or None if cache is empty.

        Thread-safe.
        """
        try:
            key, raw = self._cache.popitem(last=False)
            self._evictions += 1
            return key, raw
        except KeyError:
            return None
        except Exception:
            return None

    def clear(self) -> bool:
        """Clear all entries. Thread-safe. Returns True."""
        try:
            self._cache.clear()
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
            return len(self._cache)
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
            return {
                "hits": self._hits,
                "misses": self._misses,
                "evictions": self._evictions,
                "size": len(self._cache),
                "maxsize": self._maxsize,
            }
        except Exception:
            return {"hits": 0, "misses": 0, "evictions": 0, "size": 0, "maxsize": self._maxsize}

    def __repr__(self) -> str:
        try:
            return (
                f"BoundedLoRACache(maxsize={self._maxsize}, "
                f"size={len(self._cache)}, hits={self._hits}, "
                f"misses={self._misses}, evictions={self._evictions})"
            )
        except Exception:
            return f"BoundedLoRACache(maxsize={self._maxsize})"


# ─────────────────────────────────────────────────────────────────────────────
# GenerationalCache — 3-generation dict LRU with age-based eviction
# ─────────────────────────────────────────────────────────────────────────────
#
# Memory management upgrade (ISSUE-ZOOMOUT 2026-07-16):
#   • 3 generations: gen0 (youngest) → gen1 → gen2 (oldest)
#   • Each generation is a regular dict — values are held strongly.
#     Use refcount_check=True to detect orphaned entries (refcount≤baseline)
#     and evict them before age-based eviction kicks in.
#   • New entries land in gen0; when gen0 fills its maxsize, the oldest
#     25% is promoted to gen1; gen1 → gen2 follows the same rule
#   • Eviction policy: gen2 (oldest) evicted first, then gen1, then gen0
#   • Optional refcount threshold: entries with refcount ≤ baseline can be
#     force-evicted before age-based eviction kicks in
#
# M1 8GB: refcount_check=True detects numpy array / embedding values that
# are only held by the cache (refcount=1) and evicts them before memory
# pressure events, preventing the allocator from hitting OOM.
#
# Usage:
#     cache = GenerationalCache(maxsize_per_gen=1024, refcount_check=True)
#     cache.set("key", heavy_numpy_array)
#     val = cache.get("key")           # returns None on miss
#     cache.promote("key")             # move to next older generation
#     cache.evict_low_refcount()       # force-evict orphaned entries


class GenerationalCache[K, V]:
    """
    3-Generation dict cache with age-based eviction.

    Eviction order: gen2 (oldest) → gen1 → gen0 (youngest).
    Each generation is a regular dict — values are held strongly.
    Use refcount_check=True to detect orphaned entries (refcount≤baseline)
    and evict them before age-based eviction.

    Invariants:
        - maxsize enforced per generation
        - age-based eviction: oldest generation evicted first
        - optional refcount threshold: force-evict entries with refcount≤baseline
        - fail-safe: any error returns None/False, never raises
    """

    __slots__ = (
        "_evictions",
        "_gen0",
        "_gen1",
        "_gen2",
        "_hits",
        "_lock",
        "_maxsize",
        "_misses",
        "_promotions",
        "_refcount_check",
        "_refcount_baseline",
    )

    def __init__(
        self,
        maxsize_per_gen: int = 1024,
        *,
        refcount_check: bool = True,
        refcount_baseline: int = 2,
    ) -> None:
        """
        Initialize GenerationalCache.

        Args:
            maxsize_per_gen: Maximum entries per generation (gen0/gen1/gen2).
            refcount_check: If True, check refcount during eviction.
            refcount_baseline: refcount ≤ this → entry is "orphaned" and
                evicted before age-based eviction triggers.
        """
        self._maxsize: int = max(1, maxsize_per_gen)
        self._refcount_check: bool = refcount_check
        self._refcount_baseline: int = refcount_baseline
        # gen0 = youngest, gen2 = oldest
        self._gen0: dict[K, V] = {}
        self._gen1: dict[K, V] = {}
        self._gen2: dict[K, V] = {}
        self._lock = threading.RLock()
        self._hits: int = 0
        self._misses: int = 0
        self._evictions: int = 0
        self._promotions: int = 0

    # ── internal helpers ────────────────────────────────────────────────────

    def _refcount(self, key: K, gen: dict[K, V]) -> int:
        """Return sys.getrefcount for an entry in the given generation."""
        try:
            import sys
            val = gen.get(key)
            if val is None:
                return 0
            # -1 because getrefcount includes this call's temporary reference
            return sys.getrefcount(val) - 1
        except Exception:
            return 0

    def _is_orphaned(self, key: K, gen: dict[K, V]) -> bool:
        """Return True if entry's refcount suggests it's only held by the cache."""
        if not self._refcount_check:
            return False
        return self._refcount(key, gen) <= self._refcount_baseline

    def _gens(self) -> list[tuple[int, dict[K, V]]]:
        """Return generations in eviction order (oldest first)."""
        return [(2, self._gen2), (1, self._gen1), (0, self._gen0)]

    def _evict_from_gen(self, gen: dict[K, V], count: int) -> int:
        """Evict up to `count` entries from a generation. Returns count evicted."""
        evicted = 0
        try:
            # Collect orphaned entries first, then LRU
            orphaned: list[K] = []
            lru_order: list[K] = list(gen.keys())
            for key in lru_order:
                if self._is_orphaned(key, gen):
                    orphaned.append(key)
            for key in orphaned[:count]:
                if len(gen) == 0:
                    break
                try:
                    del gen[key]
                    self._evictions += 1
                    evicted += 1
                except KeyError:  # noqa: BLE001
                    pass
            remaining = count - evicted
            for _ in range(remaining):
                if len(gen) == 0:
                    break
                # Pop oldest (first inserted = first in dict order)
                try:
                    key = next(iter(gen))
                    del gen[key]
                    self._evictions += 1
                    evicted += 1
                except (StopIteration, KeyError):
                    break
        except Exception:  # noqa: BLE001
            pass
        return evicted

    def _promote_gen0_to_gen1(self) -> int:
        """Promote oldest 25% of gen0 to gen1. Returns count promoted."""
        promoted = 0
        try:
            count = max(1, len(self._gen0) // 4)
            keys = list(self._gen0.keys())[:count]
            for key in keys:
                val = self._gen0.get(key)
                if val is None:
                    continue
                if len(self._gen1) >= self._maxsize:
                    self._evict_from_gen(self._gen1, 1)
                try:
                    del self._gen0[key]
                    self._gen1[key] = val
                    self._promotions += 1
                    promoted += 1
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass
        return promoted

    def _promote_gen1_to_gen2(self) -> int:
        """Promote oldest 25% of gen1 to gen2. Returns count promoted."""
        promoted = 0
        try:
            count = max(1, len(self._gen1) // 4)
            keys = list(self._gen1.keys())[:count]
            for key in keys:
                val = self._gen1.get(key)
                if val is None:
                    continue
                if len(self._gen2) >= self._maxsize:
                    self._evict_from_gen(self._gen2, 1)
                try:
                    del self._gen1[key]
                    self._gen2[key] = val
                    self._promotions += 1
                    promoted += 1
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass
        return promoted

    # ── public API ──────────────────────────────────────────────────────────

    def get(self, key: K) -> V | None:
        """
        Get value by key. Checks gen0 → gen1 → gen2 (youngest first).

        Thread-safe. Returns None on miss.
        """
        try:
            with self._lock:
                for gen in (self._gen0, self._gen1, self._gen2):
                    if key in gen:
                        val = gen[key]
                        self._hits += 1
                        # Note: no promotion on read (preserves generational age)
                        return val
                self._misses += 1
                return None
        except Exception:
            return None

    def set(self, key: K, value: V) -> bool:
        """
        Set key-value pair. Lands in gen0 (youngest).

        If gen0 is at capacity, promotes oldest 25% to gen1.
        Thread-safe. Returns True on success, False on error.
        """
        try:
            with self._lock:
                if key in self._gen0 or key in self._gen1 or key in self._gen2:
                    # Update existing — remove from all gens first
                    for gen in (self._gen0, self._gen1, self._gen2):
                        if key in gen:
                            del gen[key]
                # Evict gen2 if needed, then gen1, then make room in gen0
                if len(self._gen2) >= self._maxsize:
                    self._evict_from_gen(self._gen2, max(1, self._maxsize // 4))
                if len(self._gen1) >= self._maxsize:
                    self._promote_gen1_to_gen2()
                if len(self._gen0) >= self._maxsize:
                    self._promote_gen0_to_gen1()
                self._gen0[key] = value
                return True
        except Exception:
            return False

    def update(self, batch: dict[K, V]) -> None:
        """
        Bulk-insert key-value pairs.

        For keys that already exist: remove from all gens first, then re-insert.
        For new keys: standard generational insertion (lands in gen0).

        Thread-safe. Silently skips on error (fail-safe).
        """
        if not batch:
            return
        try:
            with self._lock:
                for key, value in batch.items():
                    # Remove existing from any gen
                    for gen in (self._gen0, self._gen1, self._gen2):
                        if key in gen:
                            del gen[key]
                    # Evict to make room
                    if len(self._gen2) >= self._maxsize:
                        self._evict_from_gen(self._gen2, max(1, self._maxsize // 4))
                    if len(self._gen1) >= self._maxsize:
                        self._promote_gen1_to_gen2()
                    if len(self._gen0) >= self._maxsize:
                        self._promote_gen0_to_gen1()
                    self._gen0[key] = value
        except Exception:  # noqa: BLE001
            pass

    def promote(self, key: K) -> bool:
        """
        Explicitly promote an entry one generation older (gen0 → gen1 → gen2).

        Thread-safe. Returns True if entry was found and promoted.
        """
        try:
            with self._lock:
                for src_gen, dst_gen, next_gen in [
                    (self._gen0, self._gen1, self._gen2),
                    (self._gen1, self._gen2, None),
                ]:
                    if key in src_gen:
                        val = src_gen.get(key)
                        if val is None:
                            return False
                        del src_gen[key]
                        if len(dst_gen) >= self._maxsize and next_gen is not None:
                            self._evict_from_gen(next_gen, max(1, self._maxsize // 4))
                        dst_gen[key] = val
                        self._promotions += 1
                        return True
                return False
        except Exception:
            return False

    def evict_low_refcount(self, max_evict: int = 64) -> int:
        """
        Force-evict entries with refcount ≤ baseline across all generations.

        Use during memory pressure events to aggressively reclaim orphaned entries.

        Args:
            max_evict: Maximum entries to evict in this call.

        Returns:
            Number of entries evicted.
        """
        evicted = 0
        try:
            with self._lock:
                for _gen_num, gen in self._gens():
                    if evicted >= max_evict:
                        break
                    orphaned = [k for k in gen.keys() if self._is_orphaned(k, gen)]
                    for key in orphaned[:max_evict - evicted]:
                        if key in gen:
                            try:
                                del gen[key]
                                self._evictions += 1
                                evicted += 1
                            except KeyError:  # noqa: BLE001
                                pass
        except Exception:  # noqa: BLE001
            pass
        return evicted

    def clear(self) -> bool:
        """Clear all generations. Thread-safe."""
        try:
            with self._lock:
                self._gen0.clear()
                self._gen1.clear()
                self._gen2.clear()
                self._hits = 0
                self._misses = 0
                self._evictions = 0
                self._promotions = 0
                return True
        except Exception:
            return False

    @property
    def size(self) -> int:
        """Total entries across all generations."""
        try:
            with self._lock:
                return len(self._gen0) + len(self._gen1) + len(self._gen2)
        except Exception:
            return 0

    def __len__(self) -> int:
        return self.size

    @property
    def stats(self) -> dict[str, int]:
        """Hit/miss/eviction/promotion stats."""
        try:
            with self._lock:
                return {
                    "hits": self._hits,
                    "misses": self._misses,
                    "evictions": self._evictions,
                    "promotions": self._promotions,
                    "gen0_size": len(self._gen0),
                    "gen1_size": len(self._gen1),
                    "gen2_size": len(self._gen2),
                    "total": len(self),
                }
        except Exception:
            return {"hits": 0, "misses": 0, "evictions": 0, "promotions": 0,
                    "gen0_size": 0, "gen1_size": 0, "gen2_size": 0, "total": 0}

    def __repr__(self) -> str:
        try:
            with self._lock:
                return (
                    f"GenerationalCache(maxsize={self._maxsize}, "
                    f"gen0={len(self._gen0)}, gen1={len(self._gen1)}, gen2={len(self._gen2)}, "
                    f"hits={self._hits}, misses={self._misses}, "
                    f"evictions={self._evictions}, promotions={self._promotions})"
                )
        except Exception:
            return f"GenerationalCache(maxsize={self._maxsize})"


# ─────────────────────────────────────────────────────────────────────────────
# RefcountEvictionCache — sys.getrefcount-based eviction for embedder sessions
# ─────────────────────────────────────────────────────────────────────────────
#
# Memory management upgrade (ISSUE-ZOOMOUT 2026-07-16):
#   Embedder sessions (MLXEmbeddingManager) hold Metal buffers and large numpy
#   arrays. The standard LRU eviction doesn't account for entries that have
#   been abandoned by callers but still occupy cache slots.
#
#   sys.getrefcount(obj) returns the number of references to an object.
#   An entry only held by the cache has refcount ≈ 1 (the cache dict ref).
#   An entry with external references (still in use by async tasks, etc.)
#   has refcount ≥ 2.
#
#   This cache uses refcount as the PRIMARY eviction signal:
#     1. Scan all entries
#     2. Entries with refcount ≤ baseline are "orphaned" → evicted first
#     3. Then apply LRU eviction to remaining entries
#
#   Secondary signal: generational age. Entries that survive N cycles in
#   gen0 are promoted to gen1, then gen2. This provides age-based
#   eviction as a fallback when refcount signals are noisy.
#
# Usage:
#     sessions = RefcountEvictionCache(maxsize=16, name="embedder_sessions")
#     sessions.set("session_id", embedder_session_object)
#     # On memory pressure:
#     sessions.evict_orphaned()   # kick out abandoned sessions
#     sessions.evict_gen2()       # then age-based eviction
#     val = sessions.get("session_id")
#
# M1 8GB: embedder sessions are 50-200MB each (Metal buffers).
# A 16-slot cache = 800MB-3.2GB. Refcount eviction prevents these
# from lingering after their callers have dropped references.


class RefcountEvictionCache[K, V]:
    """
    Embedder-session cache with sys.getrefcount-based eviction.

    Primary eviction signal: refcount ≤ baseline (orphaned entries).
    Secondary signal: generational age (gen0 → gen1 → gen2).

    Thread-safe via threading.RLock. Fail-safe: any error returns safely.

    Invariants:
        - maxsize enforced on write
        - orphaned entries evicted before LRU when refcount_check=True
        - generational promotion every N set() calls
        - fail-safe: never raises, returns safely on error
    """

    __slots__ = (
        "_evictions",
        "_evict_orphaned_total",
        "_gen0",
        "_gen1",
        "_gen2",
        "_hits",
        "_lock",
        "_maxsize",
        "_misses",
        "_name",
        "_orphaned_total",
        "_refcount_baseline",
        "_refcount_check",
        "_set_counter",
    )

    def __init__(
        self,
        maxsize: int = 16,
        *,
        name: str = "refcount_cache",
        refcount_check: bool = True,
        refcount_baseline: int = 2,
    ) -> None:
        """
        Initialize RefcountEvictionCache.

        Args:
            maxsize: Maximum entries in the cache.
            name: Human-readable name for telemetry/logging.
            refcount_check: Enable refcount-based orphaned eviction.
            refcount_baseline: refcount ≤ this → orphaned (evict first).
        """
        self._maxsize: int = max(1, maxsize)
        self._name: str = name
        self._refcount_check: bool = refcount_check
        self._refcount_baseline: int = refcount_baseline
        self._gen0: dict[K, V] = {}
        self._gen1: dict[K, V] = {}
        self._gen2: dict[K, V] = {}
        self._lock = threading.RLock()
        self._hits: int = 0
        self._misses: int = 0
        self._evictions: int = 0
        self._orphaned_total: int = 0
        self._evict_orphaned_total: int = 0
        self._set_counter: int = 0

    # ── refcount helpers ────────────────────────────────────────────────────

    def _get_refcount(self, key: K, gen: dict[K, V]) -> int:
        """Get refcount for an entry. Returns 0 if not found."""
        try:
            import sys
            val = gen.get(key)
            if val is None:
                return 0
            # -1 because getrefcount includes this call's temporary reference
            return sys.getrefcount(val) - 1
        except Exception:
            return 0

    def _is_orphaned(self, key: K, gen: dict[K, V]) -> bool:
        """Return True if entry's refcount suggests it's only held by cache."""
        if not self._refcount_check:
            return False
        return self._get_refcount(key, gen) <= self._refcount_baseline

    def _scan_refcounts(self) -> dict[K, tuple[int, dict[K, V]]]:
        """Scan all generations and return {key: (refcount, gen)} for all entries."""
        result: dict[K, tuple[int, dict[K, V]]] = {}
        for gen in (self._gen0, self._gen1, self._gen2):
            for key in gen:
                result[key] = (self._get_refcount(key, gen), gen)
        return result

    # ── internal helpers ────────────────────────────────────────────────────

    def _evict_lru_from(self, gen: dict[K, V], count: int) -> int:
        """Evict `count` oldest entries (first in dict order) from gen. Returns evicted."""
        evicted = 0
        try:
            for _ in range(count):
                if not gen:
                    break
                key = next(iter(gen))
                del gen[key]
                self._evictions += 1
                evicted += 1
        except Exception:  # noqa: BLE001
            pass
        return evicted

    def _make_room(self) -> None:
        """Ensure space in gen0. If full, promote gen0→gen1→gen2, then evict gen2."""
        while len(self._gen0) >= self._maxsize:
            if len(self._gen2) > 0:
                self._evict_lru_from(self._gen2, max(1, self._maxsize // 4))
            if len(self._gen1) >= self._maxsize:
                # Promote oldest 25% of gen1 to gen2
                keys = list(self._gen1.keys())[:max(1, len(self._gen1) // 4)]
                for k in keys:
                    v = self._gen1.pop(k, None)
                    if v is not None and len(self._gen2) < self._maxsize:
                        self._gen2[k] = v
            if len(self._gen0) >= self._maxsize and len(self._gen1) > 0:
                # Promote oldest 25% of gen0 to gen1
                keys = list(self._gen0.keys())[:max(1, len(self._gen0) // 4)]
                for k in keys:
                    v = self._gen0.pop(k, None)
                    if v is not None and len(self._gen1) < self._maxsize:
                        self._gen1[k] = v
            if len(self._gen0) >= self._maxsize:
                # Last resort: evict oldest from gen0
                self._evict_lru_from(self._gen0, max(1, self._maxsize // 4))

    # ── public API ──────────────────────────────────────────────────────────

    def get(self, key: K) -> V | None:
        """
        Get value by key. Returns None on miss.

        Thread-safe.
        """
        try:
            with self._lock:
                for gen in (self._gen0, self._gen1, self._gen2):
                    if key in gen:
                        val = gen[key]
                        self._hits += 1
                        return val
                self._misses += 1
                return None
        except Exception:
            return None

    def set(self, key: K, value: V) -> bool:
        """
        Set key-value pair. Lands in gen0.

        Thread-safe. Returns True on success.
        """
        try:
            with self._lock:
                # Remove from all gens if existing
                for gen in (self._gen0, self._gen1, self._gen2):
                    if key in gen:
                        del gen[key]
                self._make_room()
                self._gen0[key] = value
                self._set_counter += 1
                # Periodic generational promotion every 8 sets
                if self._set_counter % 8 == 0:
                    self._promote_generations()
                return True
        except Exception:
            return False

    def update(self, batch: dict[K, V]) -> None:
        """
        Bulk-insert key-value pairs.

        For keys that already exist: remove from all gens first, then re-insert.
        For new keys: standard insertion (lands in gen0).

        Thread-safe. Silently skips on error (fail-safe).
        """
        if not batch:
            return
        try:
            with self._lock:
                for key, value in batch.items():
                    # Remove existing from any gen
                    for gen in (self._gen0, self._gen1, self._gen2):
                        if key in gen:
                            del gen[key]
                    self._make_room()
                    self._gen0[key] = value
                self._set_counter += len(batch)
                # Periodic generational promotion every 8 sets
                if self._set_counter % 8 == 0:
                    self._promote_generations()
        except Exception:  # noqa: BLE001
            pass

    def _promote_generations(self) -> None:
        """Promote oldest 25% of each generation to the next older generation."""
        try:
            for src_gen, dst_gen in [(self._gen0, self._gen1), (self._gen1, self._gen2)]:
                count = max(1, len(src_gen) // 4)
                keys = list(src_gen.keys())[:count]
                for k in keys:
                    v = src_gen.pop(k, None)
                    if v is not None and len(dst_gen) < self._maxsize:
                        dst_gen[k] = v
        except Exception:  # noqa: BLE001
            pass

    def get_refcount(self, key: K) -> int:
        """
        Get current refcount for an entry. Useful for telemetry.

        Returns 0 if key not found.
        """
        try:
            with self._lock:
                for gen in (self._gen0, self._gen1, self._gen2):
                    if key in gen:
                        return self._get_refcount(key, gen)
                return 0
        except Exception:
            return 0

    def get_refcounts(self) -> dict[K, int]:
        """
        Get refcounts for all entries. For telemetry/debugging.

        Returns {key: refcount} for all entries.
        """
        try:
            with self._lock:
                return {k: rc for k, (rc, _) in self._scan_refcounts().items()}
        except Exception:
            return {}

    def evict_orphaned(self, max_evict: int = 8) -> int:
        """
        Evict entries with refcount ≤ baseline (orphaned).

        Call this during memory pressure events to reclaim abandoned sessions.

        Args:
            max_evict: Maximum entries to evict in this call.

        Returns:
            Number of entries evicted.
        """
        evicted = 0
        try:
            with self._lock:
                all_entries = self._scan_refcounts()
                # Sort by refcount ascending (most orphaned first), then by generation
                orphaned = [(k, rc, gen) for k, (rc, gen) in all_entries.items()
                            if self._is_orphaned(k, gen)]
                orphaned.sort(key=lambda x: (x[1], 0 if x[2] is self._gen0 else 1 if x[2] is self._gen1 else 2))
                for key, _, gen in orphaned[:max_evict]:
                    if key in gen:
                        try:
                            del gen[key]
                            self._evictions += 1
                            self._evict_orphaned_total += 1
                            evicted += 1
                        except KeyError:  # noqa: BLE001
                            pass
        except Exception:  # noqa: BLE001
            pass
        return evicted

    def evict_gen2(self, max_evict: int = 4) -> int:
        """
        Evict oldest generation (gen2) entries by LRU.

        Call after evict_orphaned() to clear aged entries.

        Returns:
            Number of entries evicted.
        """
        try:
            with self._lock:
                return self._evict_lru_from(self._gen2, max_evict)
        except Exception:
            return 0

    def clear(self) -> bool:
        """Clear all generations. Thread-safe."""
        try:
            with self._lock:
                self._gen0.clear()
                self._gen1.clear()
                self._gen2.clear()
                self._hits = 0
                self._misses = 0
                self._evictions = 0
                self._orphaned_total = 0
                self._evict_orphaned_total = 0
                self._set_counter = 0
                return True
        except Exception:
            return False

    @property
    def size(self) -> int:
        """Total entries across all generations."""
        try:
            with self._lock:
                return len(self._gen0) + len(self._gen1) + len(self._gen2)
        except Exception:
            return 0

    def __len__(self) -> int:
        return self.size

    @property
    def stats(self) -> dict[str, int | str]:
        """Full stats including refcount telemetry."""
        try:
            with self._lock:
                orphaned_count = sum(
                    1 for k, (_rc, _) in self._scan_refcounts().items()
                    if self._is_orphaned(k, self._gen0 if k in self._gen0 else self._gen1 if k in self._gen1 else self._gen2)
                )
                return {
                    "name": self._name,
                    "hits": self._hits,
                    "misses": self._misses,
                    "evictions": self._evictions,
                    "evict_orphaned_total": self._evict_orphaned_total,
                    "gen0_size": len(self._gen0),
                    "gen1_size": len(self._gen1),
                    "gen2_size": len(self._gen2),
                    "total": len(self),
                    "orphaned_current": orphaned_count,
                    "refcount_baseline": self._refcount_baseline,
                }
        except Exception:
            return {"name": self._name, "hits": 0, "misses": 0, "evictions": 0,
                    "evict_orphaned_total": 0, "gen0_size": 0, "gen1_size": 0,
                    "gen2_size": 0, "total": 0, "orphaned_current": 0,
                    "refcount_baseline": self._refcount_baseline}

    def __repr__(self) -> str:
        try:
            with self._lock:
                return (
                    f"RefcountEvictionCache(name={self._name!r}, "
                    f"maxsize={self._maxsize}, "
                    f"gen0={len(self._gen0)}, gen1={len(self._gen1)}, gen2={len(self._gen2)}, "
                    f"hits={self._hits}, miss={self._misses}, "
                    f"evict_orphaned={self._evict_orphaned_total})"
                )
        except Exception:
            return f"RefcountEvictionCache(name={self._name!r}, maxsize={self._maxsize})"
