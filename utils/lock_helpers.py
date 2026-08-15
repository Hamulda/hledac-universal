"""
lock_helpers — Shared async-lock helpers for M1 8GB UMA.

Provides:

    make_async_lock_dclp(): Double-checked locking pattern for asyncio.Lock.

Protocol:
    asyncio.Lock() není thread-safe při init z více vláken současně.
    Používáme threading.Lock (reentrant, OS-provided) k ochraně init bloku.
    Po init už asyncio.Lock běží čistě v event loop — žádné cross-thread race.

Always-on, fail-safe, M1-optimized.
"""

import asyncio
import threading
from collections.abc import Callable
from typing import Any
from _core import aclose


# Type alias for the lock factory
_AsyncLockFactory = Callable[[], asyncio.Lock]


def make_async_lock_dclp() -> tuple[_AsyncLockFactory, threading.Lock]:
    """
    Create a double-checked locking async Lock pair.

    Returns a tuple of (get_lock_factory, thread_lock).
    The factory is used as a method decorator or standalone function.

    Usage (standalone)::
        _thread_lock = threading.Lock()
        _lock: asyncio.Lock | None = None

        def get_lock() -> asyncio.Lock:
            nonlocal _lock
            if _lock is None:
                with _thread_lock:
                    if _lock is None:
                        _lock = asyncio.Lock()
            return _lock
    """
    thread_lock = threading.Lock()
    lock_ref: asyncio.Lock | None = None

    def get_lock() -> asyncio.Lock:
        nonlocal lock_ref
        if lock_ref is None:
            with thread_lock:
                if lock_ref is None:
                    lock_ref = asyncio.Lock()
        return lock_ref

    return get_lock, thread_lock


class AsyncLockDCLP:
    """
    Double-checked locking asyncio.Lock wrapper.

    Thread-safe lazy init: threading.Lock protects the init block.
    After init, asyncio.Lock is used for async context coordination.

    Usage::
        class MyClass:
            _async_lock = AsyncLockDCLP()

            async def do_something(self):
                async with self._async_lock:
                    ...
    """

    __slots__ = ("_thread_lock", "_lock")

    def __init__(self) -> None:
        self._thread_lock: threading.Lock = threading.Lock()
        self._lock: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        """Thread-safe lazy init pro asyncio.Lock — DCLP protected by threading.Lock."""
        lock = self._lock
        if lock is None:
            with self._thread_lock:
                lock = self._lock
                if lock is None:
                    lock = asyncio.Lock()
                    self._lock = lock
        return lock

    async def __aenter__(self) -> None:
        await self._get_lock().acquire()

    async def __aexit__(self, *args: Any) -> None:
        self._get_lock().release()

    @property
    def locked(self) -> bool:
        return self._lock is not None and self._lock.locked()
