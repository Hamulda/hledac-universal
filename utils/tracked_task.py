"""
TrackedTask — Structured Concurrency wrapper for asyncio.Task.

Provides:
- Automatic task tracking in a registry set
- Context manager for lifecycle management
- Done-callback cleanup

M1 8GB: Prevents orphaned tasks that leak memory.

Usage:
    async with TrackedTask(tasks, coro, name="my_task") as t:
        await t
    # Task is automatically removed from registry on exit
"""


import asyncio
import logging
from typing import Any, Coroutine, Optional

logger = logging.getLogger(__name__)


class TrackedTask:
    """
    Wrapper around asyncio.Task with automatic tracking and cleanup.

    Lifecycle:
        - Created via async context manager
        - Task added to registry on __aenter__
        - Done-callback removes from registry on completion
        - __aexit__ cancels the task and awaits its completion

    Args:
        registry: Set to track active tasks (typically self._bg_tasks)
        coro: Coroutine to run as the task
        name: Optional task name for debugging
    """

    def __init__(
        self,
        registry: set[asyncio.Task[Any]],
        coro: Coroutine[Any, Any, Any],
        name: Optional[str] = None,
    ) -> None:
        self._registry = registry
        self._coro = coro
        self._task: Optional[asyncio.Task[Any]] = None
        self._name = name

    async def __aenter__(self) -> TrackedTask:
        """Start the task and register it."""
        self._task = asyncio.create_task(self._coro, name=self._name)
        self._registry.add(self._task)
        self._task.add_done_callback(self._on_done)
        logger.debug(f"[TrackedTask] Started: {self._name}")
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Cancel and await task completion."""
        del exc_type, exc_val, exc_tb  # unused
        if self._task is None:
            return
        if not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                logger.debug(f"[TrackedTask] Cancelled: {self._name}")
            except Exception as e:
                logger.warning(f"[TrackedTask] Task {self._name} raised: {e}")
        # Task removed from registry by _on_done callback

    def _on_done(self, task: asyncio.Task[Any]) -> None:
        """Callback to remove task from registry on completion."""
        asyncio.create_task(self._remove_from_registry(task))

    async def _remove_from_registry(self, task: asyncio.Task[Any]) -> None:
        """Remove task from registry."""
        self._registry.discard(task)
        if task.cancelled():
            logger.debug(f"[TrackedTask] Completed (cancelled): {self._name}")
        elif task.exception() is not None:
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
