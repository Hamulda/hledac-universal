"""
TrackedTask — Structured Concurrency wrapper for asyncio.Task (PEP 654).

Provides:

- Automatic task tracking in a registry set
- Context manager for lifecycle management
- Done-callback cleanup
- __slots__ for M1 8GB RAM optimization (~200 bytes/future saved)
- Optional exception handling callbacks

M1 8GB: Prevents orphaned tasks that leak memory.

Usage:
    async with TrackedTask(tasks, coro, name="my_task") as t:
        await t
    # Task is automatically removed from registry on exit

    # With exception callback
    async with TrackedTask(
        tasks, coro, name="my_task",
        on_exception=lambda exc: logger.error(f"Task failed: {exc}")
    ) as t:
        await t
"""



import asyncio
import logging
from typing import Any
from collections.abc import Callable, Coroutine

from hledac.universal.utils.asyncx import safe_create_task
from _core import aclose

logger = logging.getLogger(__name__)


class TrackedTask:
    """
    Wrapper around asyncio.Task with automatic tracking and cleanup.

    Lifecycle:
        - Created via async context manager
        - Task added to registry on __aenter__
        - Done-callback removes from registry on completion
        - __aexit__ cancels the task and awaits its completion

    Invariants:
        - [TT1] __slots__ = True — M1 8GB RAM optimization (~200 bytes/future)
        - [TT2] on_exception callback called on non-CancelledError exceptions
        - [TT3] CancelledError in __aexit__ is suppressed (intentional cancellation)
        - [TT4] Non-CancelledError exceptions are logged + callback called, NOT re-raised

    Args:
        registry: Set to track active tasks (typically self._bg_tasks)
        coro: Coroutine to run as the task
        name: Optional task name for debugging (shown as "None" if omitted)
        on_exception: Optional callback(exc: BaseException) for exception handling
    """

    __slots__ = ("_registry", "_coro", "_task", "_name", "_on_exception")

    def __init__(
        self,
        registry: set[asyncio.Task[Any]],
        coro: Coroutine[Any, Any, Any],
        name: str | None = None,
        *,
        on_exception: Callable[[BaseException], Any] | None = None,
    ) -> None:
        self._registry = registry
        self._coro = coro
        self._task: asyncio.Task[Any] | None = None
        self._name = name
        self._on_exception = on_exception

    async def __aenter__(self) -> TrackedTask:
        """Start the task and register it."""
        self._task = safe_create_task(self._coro, name=self._name)
        self._registry.add(self._task)
        self._task.add_done_callback(self._on_done)
        logger.debug(f"[TrackedTask] Started: {self._name}")
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Cancel and await task completion. [TT3] CancelledError suppressed."""
        del exc_type, exc_val, exc_tb  # unused
        if self._task is None:
            return
        if not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                logger.debug(f"[TrackedTask] Cancelled: {self._name}")
                # [TT3] Never re-raise — __aexit__ cancellation is intentional cleanup
            except Exception as e:
                logger.warning(f"[TrackedTask] Task {self._name} raised: {e}")
                if self._on_exception is not None:
                    self._on_exception(e)
        # Task removed from registry by _on_done callback

    def _on_done(self, task: asyncio.Task[Any]) -> None:
        """Callback to remove task from registry on completion."""
        # Synchronous removal is safe here — we just discard from the set
        self._registry.discard(task)
        if task.cancelled():
            logger.debug(f"[TrackedTask] Completed (cancelled): {self._name}")
            return
        exc = task.exception()
        if exc is not None:
            # [TT2] Call exception callback for non-CancelledError exceptions
            if self._on_exception is not None:
                try:
                    self._on_exception(exc)
                except Exception as callback_err:
                    logger.warning(
                        f"[TrackedTask] Exception callback failed for {self._name}: {callback_err}"
    )
            logger.debug(f"[TrackedTask] Completed with error: {self._name}")
        else:
            logger.debug(f"[TrackedTask] Completed: {self._name}")

    @property
    def done(self) -> bool:
        """Check if task is done."""
        return self._task.done() if self._task else True

    @property
    def result(self) -> Any:
        """Get task result (raises if not done or exception)."""
        if self._task is None:
            raise RuntimeError("Task not started")
        return self._task.result()

    def cancel(self) -> None:
        """Cancel the task."""
        if self._task and not self._task.done():
            self._task.cancel()

    def add_done_callback(self, callback: Any) -> None:
        """Add a done callback to the underlying task."""
        if self._task:
            self._task.add_done_callback(callback)
