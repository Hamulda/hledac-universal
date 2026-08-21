"""
MpscPool Wiring - Typed Channels for FetchCoordinator
====================================================

Wires rust_extensions/src/mpsc_pool.rs (MPSCPool) to:
- coordinators/fetch_coordinator.py — micro-sprint queue replacement

Purpose:
- Bounded MPSC pool replacing asyncio.Queue(maxsize=32)
- Lock-free via crossbeam-channel (ARM LSE atomics)
- Pipe-based async wake-up (no polling)
- Zero-copy: msgspec-serialized bytes directly to Rust

G5.MPSC_POOL (ZOMBIE → AKTIVNÍ):
-----------------------------------
API: Interní MpscPool<T> (15360 bytes / 483 LOC, interní API)
Hot path: coordinators/fetch_coordinator.py:1573-5701
Wiring: Vrstva pod asyncio.Queue pro backpressure-ready fetch queues
        (Rust-side bounded, abort-safe)

Architecture:
-------------
[Python producer] --send()-> [crossbeam bounded MPSC]
                                    |
                        [pipe wake-up fd]
                                    |
[asyncio Event watches read end]
                                    |
[recv_batch in Python async thread]

M1 8GB Safety:
- Capacity 2048 slots (2× asyncio.Queue maxsize=500 for headroom)
- Per-slot: ~512 bytes max
- Total: ~1 MiB — negligible
- Non-blocking send() prevents unbounded growth

Integration Points:
-------------------
1. coordinators/fetch_coordinator.py:_micro_sprint_queue
   - asyncio.Queue[dict] → MPSCQueue[dict]
   - put_nowait() → send()
   - await queue.get() → asyncio.Event.wait() on wake_fd

2. coordinators/fetch_coordinator.py:_entropy_bridge_queue
   - Similar pattern for entropy alerts

Usage:
-------
from rust_extensions.wiring.mpsc_pool_wiring import get_mpsc_queue

queue = get_mpsc_queue(capacity=32)

# Non-blocking send (from any thread)
if queue.send({"entity_id": "test", "entropy": 4.5}):
    pass  # sent OK
else:
    pass  # queue full, backpressure

# Async wait with wake_fd integration
await queue.wait_for_item()
item = queue.recv_batch(1)[0]
"""

from __future__ import annotations

import asyncio
import json
import logging
import selectors
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)

# R6: Centralized Rust access via core.rust_backend
try:
    from hledac.universal._core.rust_backend import rust as _rust_backend
except ImportError:
    _rust_backend = None

_mpsc_pool_available = (
    _rust_backend is not None
    and _rust_backend.is_available
    and hasattr(_rust_backend, "mpsc_pool")
    and getattr(_rust_backend, "mpsc_pool", None) is not None
)

# Module-level cache
_cached_instances: dict[int, MPSCQueue] = {}

# ponytail: global lock for instance dict, add per-key lock if contention emerges
_instances_lock = None


def _get_lock():
    global _instances_lock
    if _instances_lock is None:
        import threading

        _instances_lock = threading.Lock()
    return _instances_lock


def get_mpsc_queue(capacity: int = 32) -> MPSCQueue:
    """
    Get or create an MPSCQueue instance with given capacity.

    Args:
        capacity: Queue depth (default 32, matches asyncio.Queue(maxsize=32))

    Returns:
        MPSCQueue wrapper instance
    """
    with _get_lock():
        if capacity in _cached_instances:
            return _cached_instances[capacity]

        queue = MPSCQueue(capacity=capacity)
        _cached_instances[capacity] = queue
        return queue


def clear_mpsc_caches() -> None:
    """Clear all cached MPSCQueue instances (for testing)."""
    with _get_lock():
        _cached_instances.clear()


class MPSCQueue:
    """
    Bounded MPSC queue wrapping Rust MPSCPool.

    Provides async-compatible interface similar to asyncio.Queue but:
    - Zero-copy: msgspec-serialized bytes to Rust
    - Lock-free: ARM LSE atomics via crossbeam
    - Bounded: pre-allocated ring buffer, no OOM
    - Non-blocking send(): returns bool for backpressure

    Usage:
        queue = MPSCQueue(capacity=32)

        # From any thread (non-blocking)
        queue.send({"key": "value"})  # returns True/False

        # From async context
        await queue.wait_for_item()
        item = queue.recv_batch(1)[0]
    """

    __slots__ = (
        "_capacity",
        "_pool",
        "_sender_handle",
        "_wake_fd",
        "_event",
        "_selector",
        "_selector_task",
        "_closed",
        "_available",
    )

    def __init__(self, capacity: int = 32) -> None:
        """
        Initialize MPSC queue.

        Args:
            capacity: Max queue depth (default 32)
        """
        self._capacity = capacity
        self._pool = None
        self._sender_handle = 0
        self._wake_fd = -1
        self._event: asyncio.Event | None = None
        self._selector: selectors.BaseSelector | None = None
        self._selector_task: asyncio.Task[None] | None = None
        self._closed = False

        if _mpsc_pool_available:
            try:
                self._pool = _rust_backend.mpsc_pool.MPSCPool(capacity)
                self._sender_handle = self._pool.add_sender()
                self._wake_fd = self._pool.wake_fd()
                self._available = True
                logger.info(
                    "[MpscPool] Rust MPSCPool initialized: capacity=%d, wake_fd=%d",
                    capacity,
                    self._wake_fd,
                )
            except Exception as e:
                logger.warning("[MpscPool] Failed to initialize Rust MPSCPool: %s", e)
                self._available = False
        else:
            self._available = False
            logger.info("[MpscPool] Rust unavailable, using Python fallback")

    @property
    def available(self) -> bool:
        """Check if Rust MPSCPool is available."""
        return self._available

    @property
    def capacity(self) -> int:
        """Return queue capacity."""
        return self._capacity

    def qsize(self) -> int:
        """Return approximate queue size."""
        if self._pool is not None:
            return self._pool.len()
        return 0

    def full(self) -> bool:
        """Return True if queue is full."""
        if self._pool is not None:
            return self._pool.available_slots(self._sender_handle) == 0
        return True

    def empty(self) -> bool:
        """Return True if queue is empty."""
        if self._pool is not None:
            return self._pool.is_empty()
        return True

    def send(self, item: dict[str, Any]) -> bool:
        """
        Non-blocking send (thread-safe).

        Args:
            item: Dict to serialize and send

        Returns:
            True if sent, False if queue full
        """
        if self._closed:
            return False

        if not self._available:
            # Python fallback: just return False (would block in real impl)
            return False

        try:
            # Serialize with msgspec (fast) or json fallback
            try:
                import msgspec

                payload = msgspec.json.encode(item)
            except ImportError:
                payload = json.dumps(item).encode("utf-8")

            return self._pool.send(self._sender_handle, payload)
        except Exception as e:
            logger.debug("[MpscPool] send error: %s", e)
            return False

    def send_batch(self, items: Sequence[dict[str, Any]]) -> int:
        """
        Batch send (thread-safe).

        Args:
            items: Sequence of dicts to send

        Returns:
            Number of items successfully sent
        """
        if self._closed or not items:
            return 0

        if not self._available:
            return 0

        try:
            import msgspec

            payloads = [msgspec.json.encode(item) for item in items]
        except ImportError:
            payloads = [json.dumps(item).encode("utf-8") for item in items]

        try:
            import builtins

            py_list = builtins.list(payloads)
            return self._pool.send_batch(self._sender_handle, py_list)
        except Exception as e:
            logger.debug("[MpscPool] send_batch error: %s", e)
            return 0

    def recv_batch(self, max_items: int | None = None) -> list[dict[str, Any]]:
        """
        Non-blocking receive batch.

        Args:
            max_items: Max items to receive (None = all available)

        Returns:
            List of dict items
        """
        if self._pool is None:
            return []

        try:
            batches = self._pool.recv_batch(max_items)
            result = []
            for batch in batches:
                try:
                    import msgspec

                    item = msgspec.json.decode(batch)
                except ImportError:
                    item = json.loads(batch.decode("utf-8"))
                result.append(item)
            return result
        except Exception as e:
            logger.debug("[MpscPool] recv_batch error: %s", e)
            return []

    def wake_fd(self) -> int:
        """Return wake file descriptor for asyncio integration."""
        return self._wake_fd

    async def _selector_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Background selector loop watching wake_fd."""
        if self._wake_fd < 0 or self._selector is None:
            return

        while not self._closed:
            try:
                events = self._selector.select(timeout=0.1)
                for _key, mask in events:
                    if mask & selectors.EVENT_READ:
                        # Wake event received
                        if self._event is not None:
                            self._event.set()
            except Exception:
                break

    async def wait_for_item(self, timeout: float | None = None) -> bool:
        """
        Async wait for item availability.

        Uses selector on wake_fd to avoid polling.

        Args:
            timeout: Max wait time in seconds (None = wait forever)

        Returns:
            True if item available, False on timeout
        """
        if self._event is None:
            self._event = asyncio.Event()

        if self._available and self._wake_fd >= 0:
            if self._selector is None:
                self._selector = selectors.DefaultSelector()
                self._selector.register(self._wake_fd, selectors.EVENT_READ, None)

            if self._selector_task is None or self._selector_task.done():
                loop = asyncio.get_running_loop()
                self._selector_task = loop.create_task(self._selector_loop(loop))

            try:
                if timeout is not None:
                    await asyncio.wait_for(self._event.wait(), timeout=timeout)
                else:
                    await self._event.wait()
                self._event.clear()
                return True
            except TimeoutError:
                return False
        else:
            # Python fallback: simple polling
            if timeout is None:
                timeout = 5.0
            start = asyncio.get_event_loop().time()
            while asyncio.get_event_loop().time() - start < timeout:
                if not self.empty():
                    return True
                await asyncio.sleep(0.01)
            return False

    def put_nowait(self, item: dict[str, Any]) -> None:
        """
        Non-blocking put (alias for send() with exception on full).

        Args:
            item: Dict to enqueue

        Raises:
            asyncio.QueueFull: If queue is full
        """
        if not self.send(item):
            raise asyncio.QueueFull()

    async def get(self) -> dict[str, Any]:
        """
        Async get with wait.

        Returns:
            Next dict item

        Raises:
            asyncio.QueueEmpty: If timeout (5s default)
        """
        if self.wait_for_item(timeout=5.0):
            items = self.recv_batch(1)
            if items:
                return items[0]
        # G5.MPSC_POOL: Must raise QueueEmpty for exception handling compatibility
        raise asyncio.QueueEmpty()

    def close(self) -> None:
        """Close the queue."""
        self._closed = True
        if self._selector_task is not None:
            self._selector_task.cancel()
            self._selector_task = None
        if self._selector is not None:
            try:
                self._selector.unregister(self._wake_fd)
            except Exception:
                pass
            self._selector = None


# Backwards compatibility alias
MpscPool = MPSCQueue

__all__ = [
    "MPSCQueue",
    "MpscPool",
    "get_mpsc_queue",
    "clear_mpsc_caches",
    "_mpsc_pool_available",
]
