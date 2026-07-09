"""
utils/lazy_singleton.py — Thread-safe lazy singleton factory.

Provides LazySingleton[T] for thread-safe deferred initialization.
Uses double-checked locking with threading.Lock.

Python 3.14+ note: GIL provides memory-ordering guarantees for simple
reads. threading.Lock is still required for the slow-path (initialization)
to ensure visibility across all CPUs on M1.

M1 8GB: lock object ~64 bytes, negligible overhead.
"""
from __future__ import annotations

import threading
from typing import Callable, Generic, TypeVar, cast

__all__ = ["LazySingleton"]

T = TypeVar("T")


class LazySingleton(Generic[T]):
    """Thread-safe lazy singleton.

    Initialization happens exactly once, even under concurrent access.
    Thread-safety via double-checked locking pattern:

    1. Fast path: _initialized flag is read without lock (GIL-protected).
       If True, _value is returned immediately.
    2. Slow path: lock acquired, double-check, then initialize.

    reset() is provided for test isolation.
    """

    __slots__ = ("_factory", "_value", "_lock", "_initialized")

    def __init__(self, factory: Callable[[], T]) -> None:
        self._factory = factory
        self._value: T | None = None
        self._lock = threading.Lock()
        self._initialized = False

    def __call__(self) -> T:
        # Fast path: lock-free read after initialization.
        # _initialized=True write in slow path is atomic due to GIL.
        if self._initialized:
            return cast(T, self._value)
        # Slow path: lock + double-check.
        with self._lock:
            if not self._initialized:
                self._value = self._factory()
                self._initialized = True
            # _value is guaranteed T here (factory returns T, not None)
            return self._value  # type: ignore[return-value]

    def reset(self) -> None:
        """Reset state — for test isolation."""
        with self._lock:
            self._initialized = False
            self._value = None
