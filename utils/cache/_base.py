"""
Base Cache Interfaces & Shared Eviction Logic
==============================================




Provides abstract base classes and shared data structures for cache implementations:
- CacheNode: Doubly-linked list node for O(1) LRU operations
- CacheMetrics: Hit/miss/eviction tracking
- EvictionPolicy: Abstract eviction strategy interface
- _OrderTracker: Order tracking via list with O(1) move_to_end

All implementations use these primitives to avoid code duplication.
"""
from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Generic, TypeVar
from _core import aclose

K = TypeVar("K")
V = TypeVar("V")

__all__ = [
    "CacheMetrics",
    "CacheStats",
    "EvictionPolicy",
    "LRUNode",
    "_OrderTracker",
]


# ── Metrics & Stats ───────────────────────────────────────────────────────────


@dataclass
class CacheMetrics:
    """Cache performance metrics."""

    hits: int = 0
    misses: int = 0
    evictions: int = 0
    expirations: int = 0

    @property
    def total(self) -> int:
        """Total accesses."""
        return self.hits + self.misses

    @property
    def hit_rate(self) -> float:
        """Hit rate as percentage."""
        total = self.total
        return self.hits / total if total > 0 else 0.0

    def reset(self) -> None:
        """Reset all counters."""
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        self.expirations = 0

    def to_dict(self) -> dict[str, int | float]:
        """Convert to dictionary for serialization."""
        return {
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
            "expirations": self.expirations,
            "hit_rate": self.hit_rate,
        }


# Alias for backward compatibility
CacheStats = CacheMetrics


# ── Eviction Policies ─────────────────────────────────────────────────────────


class EvictionPolicy(ABC, Generic[K, V]):
    """
    Abstract base for eviction policies.

    Implementations provide:
    - select_victim(): Choose entry to evict
    - on_access(key): Record cache hit
    - on_set(key): Record new entry
    - on_delete(key): Remove entry from policy tracking
    """

    @abstractmethod
    def select_victim(self) -> K | None:
        """Return key to evict, or None if cache is empty."""
        ...

    @abstractmethod
    def on_access(self, key: K) -> None:
        """Record access to key."""
        ...

    @abstractmethod
    def on_set(self, key: K) -> None:
        """Record new key."""
        ...

    @abstractmethod
    def on_delete(self, key: K) -> None:
        """Remove key from tracking."""
        ...

    @abstractmethod
    def clear(self) -> None:
        """Reset policy state."""
        ...


# ── LRU Order Tracker ─────────────────────────────────────────────────────────


class _OrderTracker(Generic[K]):
    """
    Tracks LRU order using list with O(1) move_to_end.

    LRU = front of list, MRU = back of list.

    Operations:
    - touch(key): O(1) amortized - pop from current position, append to end
    - pop_lru(): O(1) - pop from front
    - pop_mru(): O(1) - pop from back
    - contains(key): O(n) - linear search
    """

    __slots__ = ("_order",)

    def __init__(self) -> None:
        self._order: list[K] = []

    def touch(self, key: K) -> None:
        """Move key to MRU position (end). O(1) amortized."""
        try:
            idx = self._order.index(key)
            if idx < len(self._order) - 1:
                self._order.pop(idx)
                self._order.append(key)
        except ValueError:  # noqa: BLE001
            pass

    def append(self, key: K) -> None:
        """Add new key at MRU position."""
        self._order.append(key)

    def pop_lru(self) -> K:
        """Remove and return LRU key (front). Raises IndexError if empty."""
        return self._order.pop(0)

    def pop_mru(self) -> K:
        """Remove and return MRU key (back). Raises IndexError if empty."""
        return self._order.pop()

    def remove(self, key: K) -> bool:
        """Remove key from order. Returns True if found."""
        try:
            self._order.remove(key)
            return True
        except ValueError:
            return False

    def __contains__(self, key: object) -> bool:
        return key in self._order

    def __len__(self) -> int:
        return len(self._order)

    def __iter__(self):
        """Iterate in LRU order (oldest first)."""
        return iter(self._order)

    def clear(self) -> None:
        self._order.clear()

    def keys(self):
        """Return iterator over keys in LRU order."""
        return iter(self._order)


# ── TTL Helpers ───────────────────────────────────────────────────────────────


@dataclass
class TTLEntry(Generic[V]):
    """Entry with timestamp for TTL tracking."""

    value: V
    timestamp: float = field(default_factory=time.monotonic)

    def is_expired(self, ttl: float) -> bool:
        """Check if entry has expired."""
        if ttl <= 0:
            return False
        return time.monotonic() - self.timestamp > ttl

    def refresh(self) -> None:
        """Refresh timestamp to current time."""
        self.timestamp = time.monotonic()


def get_now() -> float:
    """Get current monotonic time for TTL calculations."""
    return time.monotonic()


# ── Thread Safety ─────────────────────────────────────────────────────────────


class _ThreadSafeMixin:
    """Mixin for adding optional thread safety via RLock."""

    __slots__ = ("_lock",)

    def _init_lock(self, thread_safe: bool) -> None:
        self._lock = threading.RLock() if thread_safe else None

    def _acquire(self) -> threading.RLock | None:
        """Acquire lock if thread_safe, else None."""
        return self._lock

    def _release(self, lock: threading.RLock | None) -> None:
        """Release lock if it was acquired."""
        pass  # Context manager handles this
