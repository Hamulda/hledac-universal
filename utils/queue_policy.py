"""
Queue Policy Utilities — Sprint F207N-D Wave 1
Bounded queue policies for M1 8GB memory safety.
No imports with heavy side effects.
"""
import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Queue size constants
DEFAULT_LOW_PRIORITY_QUEUE_MAXSIZE = 64
DEFAULT_CONTROL_QUEUE_MAXSIZE = 256


def put_drop_oldest(queue: asyncio.Queue, item: Any) -> None:
    """
    Put item onto queue, dropping the oldest item if at capacity.
    Non-blocking. Logs dropped item.
    """
    try:
        if queue.full():
            try:
                oldest = queue.get_nowait()
                logger.debug(f"Queue overflow, dropped oldest: {type(oldest).__name__}")
            except asyncio.QueueEmpty:
                pass
        queue.put_nowait(item)
    except asyncio.QueueFull:
        # Should not happen after drop_oldest, but guard anyway
        logger.warning("put_drop_oldest: queue full after drop attempt")


def put_fail_fast(queue: asyncio.Queue, item: Any) -> bool:
    """
    Attempt to put item on queue. Return True if successful, False if full.
    Non-blocking. Does not raise on full.
    """
    try:
        queue.put_nowait(item)
        return True
    except asyncio.QueueFull:
        logger.debug(f"Queue full, fail-fast: {type(item).__name__}")
        return False
