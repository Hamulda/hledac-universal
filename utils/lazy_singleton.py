"""
utils/lazy_singleton.py — Thread-safe lazy singleton factory.

Provides:


- LazySingleton[T]: thread-safe deferred initialization with DCLP
- AsyncLazySingleton[T]: per-task instances via ContextVar

Python 3.14+ note: GIL provides memory-ordering guarantees for simple
reads. threading.Lock is still required for the slow-path (initialization)
to ensure visibility across all CPUs on M1.

M1 8GB: lock object ~64 bytes, negligible overhead.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from contextvars import ContextVar
from typing import TypeVar

__all__ = ["LazySingleton", "AsyncLazySingleton"]

T = TypeVar("T")


class LazySingleton:
    """Thread-safe lazy singleton.

    Initialization happens exactly once, even under concurrent access.
    Thread-safety via double-checked locking pattern.

    reset() is provided for test isolation.
    """

    __slots__ = ("_factory", "_value", "_lock", "_initialized")

    def __init__(self, factory: Callable[[], T]) -> None:
        self._factory = factory
        self._value: T | None = None
        self._lock = threading.Lock()
        self._initialized = False

    def __call__(self) -> T:
        if self._initialized:
            return self._value  # type: ignore[return-value]
        with self._lock:
            if not self._initialized:
                self._value = self._factory()
                self._initialized = True
            return self._value  # type: ignore[return-value]

    def reset(self) -> None:
        """Reset state — for test isolation."""
        with self._lock:
            self._initialized = False
            self._value = None


class AsyncLazySingleton:
    """Per-task lazy singleton via ContextVar — different async tasks get different instances.

    Solves the nested asyncio.run() isolation problem: separate event loops
    create separate ContextVar copies, so each gets its own instance.

    Usage:
        lock_singleton = AsyncLazySingleton(lambda: asyncio.Lock())

        async def task_a():
            lock = lock_singleton()  # task-a's own lock
            ...

        asyncio.gather(task_a(), task_b())  # task_b gets its own lock
    """

    __slots__ = ("_factory", "_ctx_var")

    def __init__(self, factory: Callable[[], T]) -> None:
        self._factory = factory
        self._ctx_var: ContextVar[dict[int, T] | None] = ContextVar(
            f"_AsyncLazySingleton_{id(self)}", default=None
        )

    def __call__(self) -> T:
        instances = self._ctx_var.get()
        if instances is None:
            instances = {}
            self._ctx_var.set(instances)
        task_id = id(instances)
        if task_id not in instances:
            instances[task_id] = self._factory()
        return instances[task_id]

    def reset(self) -> None:
        """Clear the ContextVar — next call re-creates the instance for the current task."""
        self._ctx_var.set(None)
