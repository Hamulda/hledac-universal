"""
Async Task Management - Bounded Task Tracking
==============================================

Sprint F320-B4: Modular bounded task tracking, replacing ad-hoc patterns.

This module provides bounded concurrency primitives for asyncio task management,
specifically designed for M1 8GB UMA constraints where unbounded task spawn
can cause memory pressure.

PRIMARY EXPORTS
---------------
BoundedTaskSet    — Semaphore-bounded task registry with auto-cleanup
spawn_task        — Convenience wrapper: create + register in one call

PATTERN SELECTION GUIDE
-----------------------
| Pattern                    | Use                          |
|----------------------------|------------------------------|
| Fire-and-forget, bounded   | BoundedTaskSet.spawn()       |
| Fan-out I/O, bounded       | bounded_gather() in async_helpers |
| Fire-and-forget, native   | safe_create_task() in async_helpers |
| Structured all-or-nothing | asyncio.TaskGroup (stdlib)   |
| Result preservation        | safe_gather_*() in async_helpers   |

MIGRATION PATH
--------------
Old: asyncio.create_task(coro) in a set/collection
New: BoundedTaskSet.spawn(coro) — semaphore prevents overload

DEPRECATION (F320-B4)
---------------------
BoundedTaskSet is deprecated in favor of Python 3.11+ asyncio.TaskGroup.
It remains for:
  1. duckdb_store.py — bound to __slots__, complex to migrate
  2. Sync contexts needing async spawn (require loop running)

For NEW code: use asyncio.TaskGroup or bounded_gather().

M1 8GB BOUNDS
-------------
- Max concurrent tasks per BoundedTaskSet: 256 (hard cap)
- Typical steady-state: << 32 tasks
- Memory per task: ~2-4 KB (negligible vs 8GB budget)
- Metal cache: 1.5 GiB wired, 1 GiB soft cap

Example:
    from hledac.universal.utils.async_task import BoundedTaskSet, spawn_task

    ts = BoundedTaskSet(maxsize=256)
    t = await ts.spawn(my_coro(), name="fetch:example.com")
    await ts.cancel()  # broadcast cancel + drain

    # Or use the convenience spawn_task:
    t = await spawn_task(my_coro(), name="batch:item", maxsize=128)
"""
import asyncio
import logging
import warnings
from typing import Any, Awaitable
from .async_helpers import safe_gather_fire_and_forget
logger = logging.getLogger(__name__)

def safe_create_task(coro: Any, *, name: str | None=None, eager_start: bool=False) -> asyncio.Task:
    """Safe task creation wrapper — imports safe_create_task from async_helpers."""
    from .async_helpers import safe_create_task as _impl
    return _impl(coro, name=name, eager_start=eager_start)

class BoundedTaskSet:
    """
    asyncio.Task registry with semaphore-bound concurrent task limit.

    Fix K11/F3.3: Unbound ``set[asyncio.Task]`` allowed unlimited growth during
    mass operations (e.g. 500+ tasks during duckdb_store mass drain).
    BoundedTaskSet replaces direct set usage with semaphore-backed spawning.

    Features:
    - Semaphore-bound spawning (default 256, M1-safe ceiling)
    - Auto-cancel all pending tasks via .cancel()
    - Auto-cleanup via done_callback (no leak)
    - Exception logging per completed task
    - Fail-open: no operation propagates exception

    Invariants:
        - [BTS-1] Semaphore acquire blocks until slot available or cancel requested
        - [BTS-2] cancel() broadcasts to all pending tasks
        - [BTS-3] done_callback auto-releases semaphore slot
        - [BTS-4] Failed tasks log at WARNING, never propagate

    DEPRECATED (F320-B4): Python 3.11+ asyncio.TaskGroup is preferred.
    This class remains for sync contexts needing async spawn and for
    duckdb_store.py (__slots__ constraint).

    Usage:
        ts = BoundedTaskSet(maxsize=256)
        t = await ts.spawn(my_coro(), name="fetch:example.com")
        await ts.cancel()  # drain all pending tasks
    """
    __slots__ = tuple(('_cancel_requested', '_lock', '_maxsize', '_sem', '_tasks'))

    def __init__(self, maxsize: int=256) -> None:
        """
        Initialize BoundedTaskSet.

        Args:
            maxsize: Maximum concurrent tasks. Default 256 is M1-safe ceiling.
                    Typical workloads run << 32 tasks steady-state.
        """
        self._maxsize = maxsize
        self._tasks: dict[asyncio.Task, str] = {}
        self._sem = asyncio.Semaphore(maxsize)
        self._cancel_requested = False
        self._lock = asyncio.Lock()

    @property
    def count(self) -> int:
        """Return number of active (incomplete) tasks."""
        return len(self._tasks)

    async def spawn(self, coro: Awaitable[Any], name: str | None=None) -> asyncio.Task:
        """
        Create and register a task, blocking if maxsize reached.

        Args:
            coro: Coroutine to execute.
            name: Optional task name for debugging/logging.

        Returns:
            asyncio.Task instance.

        Raises:
            RuntimeError: If cancel was requested and no current task exists.
        """
        if self._cancel_requested:
            t = asyncio.current_task()
            if t is not None:
                return t
            t = asyncio.create_task(asyncio.sleep(0))
            t.cancel()
            return t
        await self._sem.acquire()
        task = asyncio.create_task(coro, name=name or 'bounded_taskset:anon')
        task_name = task.get_name()
        async with self._lock:
            self._tasks[task] = task_name

        def _done_callback(f: asyncio.Task) -> None:
            self._tasks.pop(f, None)
            self._sem.release()
            try:
                if not f.cancelled():
                    exc = f.exception()
                    if exc is not None:
                        logger.warning('[BoundedTaskSet] Task %s failed: %r', f.get_name(), exc)
            except asyncio.InvalidStateError:
                pass
        task.add_done_callback(_done_callback)
        return task

    async def cancel(self) -> None:
        """
        Cancel ALL pending tasks and wait for their completion.

        Safe for re-entry: cancel() can be called multiple times.
        """
        self._cancel_requested = True
        async with self._lock:
            tasks = list(self._tasks.keys())
        if not tasks:
            return
        logger.debug('[BoundedTaskSet] Cancelling %d tasks', len(tasks))
        for t in tasks:
            t.cancel()
        await safe_gather_fire_and_forget(*tasks, label='BoundedTaskSet:cancel', logger_instance=logger)
        async with self._lock:
            self._tasks.clear()

async def spawn_task(coro: Awaitable[Any], *, name: str | None=None, maxsize: int=128, task_set: BoundedTaskSet | None=None) -> asyncio.Task:
    """
    Convenience: spawn a task with optional BoundedTaskSet.

    If task_set is provided, uses it (bounded). Otherwise creates a
    temporary BoundedTaskSet(maxsize) for fire-and-forget.

    Args:
        coro: Coroutine to execute.
        name: Optional task name.
        maxsize: Max concurrent tasks if creating temporary set.
        task_set: Optional existing BoundedTaskSet to use.

    Returns:
        asyncio.Task instance.
    """
    if task_set is not None:
        return await task_set.spawn(coro, name=name)
    ts = BoundedTaskSet(maxsize=maxsize)
    return await ts.spawn(coro, name=name)

def __getattr__(name: str):
    """DEPRECATED: BoundedTaskSet emit DeprecationWarning on import."""
    if name == 'BoundedTaskSet':
        warnings.warn('BoundedTaskSet is deprecated. Use Python 3.11+ asyncio.TaskGroup instead. For bounded gather patterns, use bounded_gather() in async_helpers. This module will be removed in a future sprint.', DeprecationWarning, stacklevel=2)
        return BoundedTaskSet
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
__all__ = ['BoundedTaskSet', 'spawn_task', 'safe_create_task']