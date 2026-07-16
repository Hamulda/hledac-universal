"""
locks — Lazy asyncio.Lock registry for M1 8GB UMA.

Centralizes all module-level asyncio.Lock() creation to prevent the
ISSUE-014 crash on macOS: asyncio.Lock() captures the running event loop
at creation time. If called at module import (no loop yet), subsequent
async uses fail with "no running event loop".

Solution: LazyAsyncioLock stores a None sentinel at module level.
The actual asyncio.Lock() is created only inside get(), which is only
called from async code — where an event loop is guaranteed to exist.

Usage::

    from utils.locks import LazyAsyncioLock

    _my_lock = LazyAsyncioLock()

    async def do_work():
        async with _my_lock.get():
            ...

    # NOT: async with _my_lock:  # AttributeError: 'LazyAsyncioLock' has no 'acquire'

Alternative (explicit get)::

    async with (await _my_lock.get()):
        ...

Always-on, fail-safe, M1-optimized.
"""

from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass


class LazyAsyncioLock:
    """
    Lazy asyncio.Lock wrapper.

    Stores None at module/class level. The actual asyncio.Lock() is
    created on first get() call — always from async context where an
    event loop exists.

    Thread-safe via double-checked locking (threading.Lock protects
    the init block; after init, asyncio.Lock is used in async context
    which is single-threaded by design in this project).

    Attributes:
        _lock: The asyncio.Lock instance, None until first get() call.
        _thread_lock: Protects the init block from concurrent access.

    Invariant: get() must only be called from async code (where a loop
    is guaranteed to exist). DO NOT call from synchronous module-level
    code.
    """

    __slots__ = ("_lock", "_thread_lock")

    def __init__(self) -> None:
        self._lock: asyncio.Lock | None = None
        self._thread_lock: threading.Lock = threading.Lock()

    def get(self) -> asyncio.Lock:
        """
        Return the asyncio.Lock, creating it lazily on first call.

        MUST be called from an async context (where an event loop exists).
        Returns the same Lock instance on all subsequent calls.
        """
        lock = self._lock
        if lock is None:
            with self._thread_lock:
                lock = self._lock
                if lock is None:
                    lock = asyncio.Lock()
                    self._lock = lock
        return lock

    async def acquire(self) -> None:
        """Acquire the lock (for 'async with' protocol)."""
        await self.get().acquire()

    def release(self) -> None:
        """Release the lock."""
        self.get().release()

    async def __aenter__(self) -> None:
        await self.acquire()

    async def __aexit__(self, *args: Any) -> None:
        self.release()

    @property
    def locked(self) -> bool:
        return self._lock is not None and self._lock.locked()

    def __repr__(self) -> str:
        return f"LazyAsyncioLock(initialized={self._lock is not None})"
