"""
Bounded Collections — Always-on, fail-safe, M1 8GB safe.

collections.deque with explicit maxlen for unbounded-list prevention.

Every list-typed field that grows without limit is a memory leak vector
on M1 8GB UMA. This module provides zero-overhead wrappers that make
the bounded semantics explicit in the type signature.

Usage:
    from hledac.universal.core.bounded_collections import BoundedList

    class MyClass:
        __slots__ = ('_items',)
        def __init__(self):
            self._items = BoundedList[str](maxlen=256)
"""
from __future__ import annotations

from collections import deque
from typing import Generic, TypeVar
from collections.abc import Iterator, Iterable
from core._util import aclose

T = TypeVar("T")


class BoundedList(Generic[T]):  # noqa: N801
    """
    Generic bounded list backed by collections.deque(maxlen=N).

    Inherent properties:
    - O(1) append with automatic FIFO eviction (no growth beyond maxlen)
    - iterable (for len(), for x in self, etc.)
    - zero additional memory over plain deque
    - thread-safe for single-writer patterns (same as deque)

    NOT provided: random access by index (use list(self) if needed),
    slicing, or any mutating operation other than append.
    """

    __slots__ = ("_d",)

    def __init__(self, maxlen: int) -> None:
        self._d: deque[T] = deque(maxlen=maxlen)

    def append(self, x: T) -> None:
        self._d.append(x)

    def extend(self, iterable: Iterable[T]) -> None:
        """Extend with an iterable. Items beyond maxlen are silently dropped (FIFO)."""
        self._d.extend(iterable)

    def clear(self) -> None:
        self._d.clear()

    def __iter__(self) -> Iterator[T]:
        return iter(self._d)

    def __len__(self) -> int:
        return len(self._d)

    def __bool__(self) -> bool:
        return bool(self._d)

    def __contains__(self, x: object) -> bool:
        return x in self._d

    def __repr__(self) -> str:
        return f"BoundedList(maxlen={self._d.maxlen}, len={len(self._d)})"

    @property
    def maxlen(self) -> int | None:
        """Returns the maxlen bound (the N in BoundedList[N])."""
        return self._d.maxlen

    def to_list(self) -> list[T]:
        """Convert to plain list (copy). Use sparingly — defeats the bounded property."""
        return list(self._d)


class SlottedBoundedList(BoundedList[T]):  # noqa: N801
    """
    BoundedList subclass compatible with __slots__ host classes.

    Use this when the bounded list must live on a class that uses
    __slots__. The subclass does NOT declare its own __slots__;
    it relies on BoundedList's __slots__ = ("_d",) inherited via C3 MRO.
    object.__setattr__ bypasses the slots mechanism in __init__.

    Example:
        class MyService:
            __slots__ = ('_history',)
            def __init__(self):
                self._history = SlottedBoundedList[Event](maxlen=512)
    """

    def __init__(self, maxlen: int) -> None:
        object.__setattr__(self, "_d", deque(maxlen=maxlen))
