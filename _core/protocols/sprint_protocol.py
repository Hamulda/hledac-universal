"""
Sprint Protocol — sprint lifecycle helpers for core modules.

Breaks core ↔ runtime dependency cycle by providing cancel_all_tasks
as a lazy import from core instead of importing runtime.sprint_entrypoint.

F350M-R: Dependency cycle elimination
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


async def cancel_all_tasks(loop: asyncio.AbstractEventLoop, timeout_s: float = 5.0) -> list[asyncio.Task]:
    """
    Cancel all running tasks and return list of cancelled tasks.
    
    This is the canonical task cancellation helper for graceful shutdown.
    Previously imported from runtime.sprint_entrypoint._cancel_all_tasks.
    
    Args:
        loop: The asyncio event loop
        timeout_s: Maximum seconds to wait for tasks to complete cancellation
        
    Returns:
        List of tasks that were cancelled
    """
    tasks = [t for t in asyncio.all_tasks(loop) if not t.done()]
    
    if not tasks:
        return []
    
    logger.debug("[cancel] cancelling %d tasks", len(tasks))
    
    for task in tasks:
        task.cancel()
    
    # Wait for cancellations with timeout
    try:
        await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=timeout_s
        )
    except (asyncio.TimeoutError, asyncio.CancelledError):
        pass
    
    cancelled = [t for t in tasks if t.cancelled() or t.done()]
    logger.debug("[cancel] cancelled %d/%d tasks", len(cancelled), len(tasks))
    
    return cancelled


# Lazy import for _cancel_all_tasks from runtime.sprint_entrypoint
# This allows core modules to call the runtime function without importing it directly
_async_cancel_all_tasks_impl: Any = None


def _get_cancel_all_tasks_impl():
    """Lazy load _cancel_all_tasks from runtime.sprint_entrypoint."""
    global _async_cancel_all_tasks_impl
    if _async_cancel_all_tasks_impl is None:
        from hledac.universal.runtime.sprint_entrypoint import _cancel_all_tasks as _impl
        _async_cancel_all_tasks_impl = _impl
    return _async_cancel_all_tasks_impl
