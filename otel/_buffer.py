"""
Bounded LRU ring buffer for spans/trace records.

M1 8GB safe: O(1) put/get, FIFO eviction, pure Python (no GIL pressure spike),

thread-safe via single re-entrant lock.

Generic over key/value types. Used by RingBufferExporter for test inspection
and by on-demand span snapshots (e.g. last 100 errors).

This module provides the canonical FIFO ring buffer implementation.
TelemetryRingBuffer (core/ffi_circuit_breaker.py) extends this with
telemetry-specific filtering methods.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Generic, TypeVar
from _core import aclose

K = TypeVar("K", default=object)
V = TypeVar("V", default=object)


class BoundedRing[K, V]:
    """
    FIFO-evicting map. Thread-safe.

    Extends OrderedDict with bounded capacity and LRU-style eviction.
    When capacity is reached, oldest entry is evicted (FIFO).

    Supports generic key/value types and exposes hit/miss/eviction statistics.

    TelemetryRingBuffer extends this with module-specific filtering.
    """

    __slots__ = (
        "_capacity",
        "_data",
        "_lock",
        "_hits",
        "_misses",
        "_evictions",
    )

    def __init__(self, capacity: int = 4096) -> None:
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        if capacity > 1_000_000:
            raise ValueError("capacity must be <= 1_000_000 (M1 8GB cap)")
        self._capacity = capacity
        self._data: OrderedDict[K, V] = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    @property
    def capacity(self) -> int:
        return self._capacity

    def put(self, key: K, value: V) -> None:
        with self._lock:
            if key in self._data:
                self._data[key] = value
                self._data.move_to_end(key)
                return
            if len(self._data) >= self._capacity:
                self._data.popitem(last=False)
                self._evictions += 1
            self._data[key] = value

    def get(self, key: K) -> V | None:
        with self._lock:
            if key not in self._data:
                self._misses += 1
                return None
            self._data.move_to_end(key)
            self._hits += 1
            return self._data[key]

    def __contains__(self, key: K) -> bool:
        with self._lock:
            return key in self._data

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def values(self) -> list[V]:
        with self._lock:
            return list(self._data.values())

    def items(self) -> list[tuple[K, V]]:
        with self._lock:
            return list(self._data.items())

    def keys(self) -> list[K]:
        with self._lock:
            return list(self._data.keys())

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "size": len(self._data),
                "capacity": self._capacity,
                "hits": self._hits,
                "misses": self._misses,
                "evictions": self._evictions,
            }

    # ── Telemetry extension methods ───────────────────────────────────────

    def get_recent(self, n: int = 100) -> list[V]:
        """
        Get N most recent values (LIFO order).

        Override in subclass for typed filtering (see TelemetryRingBuffer).
        Default: returns last N values from the OrderedDict.
        """
        with self._lock:
            if self._size == 0:
                return []
            count = min(n, self._size)
            # OrderedDict is LRU-order: most recent at end
            values = list(self._data.values())
            return values[-count:]

    @property
    def _size(self) -> int:  # type: ignore[unused-ignore]
        """Internal: current size (for subclasses)."""
        return len(self._data)
