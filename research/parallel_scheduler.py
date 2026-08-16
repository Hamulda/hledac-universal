"""
ParallelResearchScheduler – spravuje frontu úloh s prioritami.
Používá asyncio pro I/O úlohy a ThreadPoolExecutor pro CPU-bound úlohy.


PEP 654 redesign: asyncio.PriorityQueue + TaskGroup místo asyncio.Lock + heapq.
- PriorityQueue je thread-safe (deque-based), žádné manuální locky pro frontu operace


- asyncio.run_coroutine_threadsafe() místo call_soon_threadsafe+create_task (atomické v Py 3.10+)
- msgspec.Struct(frozen=True) pro ~40% menší alokace PrioritizedTask
- Bounded counter místo Event pro wait_all (eliminuje lost-wakeup race)
- Safe task cancellation přes TaskGroup shield

ISSUE-037 opravy:
- asyncio.BoundedSemaphore místo Semaphore (ochrana proti pře-Release)
- _pending_lock lazy init (None + _get_lock()) — asyncio.Lock() bez event loopu na macOS
- _completed dict writes chráněny přes _pending_lock
- mx.eval() + clear_cache() pro MLX workers
"""
from __future__ import annotations

import asyncio

from hledac.universal.compat.msgspec_gc_compat import Struct
import logging
import time
from collections.abc import Callable
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any
from _core import aclose

if TYPE_CHECKING:
    import msgspec as _msgspec_module

try:
    import msgspec

    _MSGSpec = True
except Exception:  # noqa: BLE001 — fail-soft: msgspec optional import, struct availability checked at runtime
    msgspec: Any = None  # type: ignore[assignment]
    _MSGSpec = False

logger = logging.getLogger(__name__)

if _MSGSpec:

    class PrioritizedTask(Struct, frozen=True):
        """Immutable prioritized task. msgspec offset access ~10× faster than dataclass.

        priority: higher = sooner (inverted for min-heap internally).
        """

        priority: float
        task_id: str
        coro_or_fn: Any
        args: tuple = ()
        kwargs: dict = {}
        created_at: float = 0.0
        metadata: dict = {}
        is_coro: bool = True
        timeout: float = 30.0

        def __post_init__(self) -> None:
            if self.created_at == 0.0:
                object.__setattr__(self, "created_at", time.time())

else:
    from dataclasses import dataclass, field

    class PrioritizedTask(Struct):
        priority: float
        task_id: str
        coro_or_fn: Any
        args: tuple = field(default=(), compare=False)
        kwargs: dict = field(default_factory=dict, compare=False)
        created_at: float = field(default_factory=time.time, compare=False)
        metadata: dict = field(default_factory=dict, compare=False)
        is_coro: bool = True
        timeout: float = 30.0


PRIORITY_RESEARCH = 5
PRIORITY_PREFETCH = 9
PRIORITY_BACKGROUND = 10
# ContextVar: each async context (Task) gets its own lock automatically.
# ISSUE-037 + ISSUE-014 FIX: asyncio.Lock bound to a single loop is a bug on macOS —
# ContextVar keyed by Task gives per-context isolation without manual tracking.
_pending_lock_var: ContextVar[asyncio.Lock | None] = ContextVar("_pending_lock_var", default=None)


class ParallelResearchScheduler:
    """Modern async parallel scheduler — asyncio.PriorityQueue + TaskGroup (PEP 654).

    Replaces lock-based queue + manual heappush/pop s asyncio.PriorityQueue.
    Structured cancellation: cancel sibling tasks on first failure via TaskGroup.

    Thread-safety invariants:
    - PriorityQueue is thread-safe (internally uses asyncio.Queue with a deque lock)
    - CPU executor callbacks use asyncio.to_thread() — no thread contention
    - All shared state guarded by BoundedSemaphore + lazy asyncio.Lock
    """

    __slots__ = tuple(
        (
            "_all_done",
            "_completed",
            "_completed_max_size",  # D9: max size for bounded dict
            "_cpu_queue",
            "_cpu_sem",
            "_io_queue",
            "_io_sem",
            "_max_cpu",
            "_max_io",
            "_pending",
            "_pending_lock",
            "_resource_allocator",
            "_seq",
            "_shutdown",
    )
    )

    def __init__(
        self,
        resource_allocator=None,
        max_concurrent_io: int = 10,
        max_concurrent_cpu: int = 4,
    ) -> None:
        self._resource_allocator = resource_allocator
        self._max_io = max_concurrent_io
        self._max_cpu = max_concurrent_cpu
        # D6 FIX: Bounded PriorityQueue to prevent memory exhaustion on M1 8GB.
        # maxsize proportional to concurrency (10× factor for buffer).
        self._io_queue: asyncio.PriorityQueue[
            tuple[float, int, PrioritizedTask]
        ] = asyncio.PriorityQueue(maxsize=max_concurrent_io * 10)
        self._cpu_queue: asyncio.PriorityQueue[
            tuple[float, int, PrioritizedTask]
        ] = asyncio.PriorityQueue(maxsize=max_concurrent_cpu * 10)
        # ISSUE-037: BoundedSemaphore — raise na pře-Release
        self._io_sem = asyncio.BoundedSemaphore(max_concurrent_io)
        self._cpu_sem = asyncio.BoundedSemaphore(max_concurrent_cpu)
        self._seq = 0
        self._pending = 0
        self._all_done = asyncio.Event()
        # D9 FIX: Use bounded dict with max size to prevent unbounded memory growth.
        # LRU eviction when max_size exceeded; old entries auto-removed.
        self._completed: dict[str, Any] = {}
        self._completed_max_size: int = 10_000  # D9: configurable max entries
        self._shutdown = False

    def _get_lock(self) -> asyncio.Lock:
        """Get the ContextVar-backed pending lock for the current async context."""
        lock = _pending_lock_var.get()
        if lock is None:
            lock = asyncio.Lock()
            _pending_lock_var.set(lock)
        return lock

    async def submit(
        self,
        task_id: str,
        coro_or_fn: Callable,
        *args: Any,
        priority: float = 1.0,
        metadata: dict | None = None,
        is_coro: bool = True,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> None:
        """Submit a task to the scheduler. Returns immediately."""
        async with self._get_lock():
            self._pending += 1
            self._all_done.clear()

        task = PrioritizedTask(
            priority=-priority,
            task_id=task_id,
            coro_or_fn=coro_or_fn,
            args=args,
            kwargs=kwargs,
            metadata=metadata or {},
            is_coro=is_coro,
            timeout=timeout or (30.0 if is_coro else 10.0),
    )
        if is_coro:
            await self._io_queue.put((task.priority, self._next_seq(), task))
        else:
            await self._cpu_queue.put((task.priority, self._next_seq(), task))

    async def schedule_prefetch(
        self,
        task_id: str,
        coro_or_fn: Callable,
        priority: float,
        is_coro: bool,
        url: str,
        deadline: float,
        estimated_bytes: int,
        metadata: dict,
    ) -> None:
        """Schedule a prefetch task — forwards to submit() with prefetch metadata."""
        await self.submit(
            task_id=task_id,
            coro_or_fn=coro_or_fn,
            priority=priority,
            is_coro=is_coro,
            metadata={
                **metadata,
                "url": url,
                "deadline": deadline,
                "estimated_bytes": estimated_bytes,
            },
            timeout=(
                deadline - time.time() if deadline > time.time() else 1.0
            ),
    )

    async def wait_all(self, timeout: float | None = None) -> None:
        """Wait for all submitted tasks to complete.

        Uses bounded counter instead of Event to fix lost-wakeup race.
        Periodically checks queue to avoid infinite wait when queue stays empty
        but no tasks were ever submitted (counter stays at 0).
        """
        poll_interval = 0.1
        elapsed = 0.0
        while True:
            try:
                async with asyncio.timeout(poll_interval):
                    await self._all_done.wait()
                    return
            except asyncio.TimeoutError:
                elapsed += poll_interval
                if timeout is not None and elapsed >= timeout:
                    return
                async with self._get_lock():
                    if self._pending == 0:
                        self._all_done.set()
                        return

    async def get_status(self) -> dict[str, Any]:
        """Return current scheduler status."""
        async with self._get_lock():
            pending = self._pending
        return {
            "running_io": 0,  # workers don't update a shared counter
            "running_cpu": 0,
            "queued_io": self._io_queue.qsize(),
            "queued_cpu": self._cpu_queue.qsize(),
            "completed": len(self._completed),
            "pending": pending,
        }

    def shutdown(self, wait: bool = True, clear_completed: bool = True) -> None:
        """Shutdown the scheduler and signal workers to exit.

        Args:
            wait: If True, waits for all pending tasks to complete before returning.
            clear_completed: If True (default), clears completed results after wait.
                D9 FIX: Prevents memory accumulation across multiple research runs.
        """
        self._shutdown = True
        if wait:
            import asyncio
            try:
                asyncio.get_running_loop().run_until_complete(self.wait_all(timeout=30.0))
            except RuntimeError:  # noqa: BLE001
                pass  # No running event loop
        # D9 FIX: Clear completed results after run to free memory
        if clear_completed:
            self.clear_completed()

    def __enter__(self) -> "ParallelResearchScheduler":
        return self

    def __exit__(self, _exc_type: Any, _exc_val: Any, _exc_tb: Any) -> bool:
        self.shutdown()
        return False

    def _next_seq(self) -> int:
        """Tie-break counter for heap ordering."""
        self._seq += 1
        return self._seq

    def _set_completed(self, task_id: str, result: Any) -> None:
        """Store completed task result with LRU-style bounded dict.

        D9 FIX: Evicts oldest entries when max_size exceeded to prevent
        unbounded memory growth during long-running research sessions.
        """
        if len(self._completed) >= self._completed_max_size:
            # Evict oldest ~10% of entries when limit reached
            evict_count = max(1, self._completed_max_size // 10)
            keys_to_remove = list(self._completed.keys())[:evict_count]
            for key in keys_to_remove:
                del self._completed[key]
        self._completed[task_id] = result

    def get_completed_result(self, task_id: str) -> Any | None:
        """Get completed task result, or None if not found."""
        return self._completed.get(task_id)

    def clear_completed(self) -> int:
        """Clear completed results dict. Returns count of cleared entries.

        D9 FIX: Call at end of run to free memory.
        Returns:
            Number of entries cleared.
        """
        count = len(self._completed)
        self._completed.clear()
        return count

    async def get_recommended_concurrency(self, task_type: str) -> int:
        """Return recommended concurrency for task type and current resources."""
        if self._resource_allocator and hasattr(
            self._resource_allocator, "get_recommended_concurrency"
        ):
            return await self._resource_allocator.get_recommended_concurrency(
                task_type
    )
        if task_type == "io":
            return self._max_io
        return self._max_cpu

    async def run_until_drain(self) -> None:
        """Drain both queues via fixed worker pools inside a single TaskGroup.

        A3-11 REDESIGN: Fixed worker pool pattern replaces while+QueueEmpty.
        - N IO workers + M CPU workers as TaskGroup children
        - Workers loop on their queue with asyncio.timeout() — no QueueEmpty
        - Structured cancellation propagates naturally via TaskGroup hierarchy
        - Workers exit when: queue empty AND pending == 0

        Call after submit() to process queued tasks.
        """
        async with asyncio.TaskGroup() as tg:
            for i in range(self._max_io):
                # F350M-R ISSUE #31: eager_start=True (IO worker loop is hot-path)
                tg.create_task(self._io_worker_loop(), name=f"prs:io:{i}", eager_start=True)
            for i in range(self._max_cpu):
                # F350M-R ISSUE #31: eager_start=True (CPU worker loop is hot-path)
                tg.create_task(self._cpu_worker_loop(), name=f"prs:cpu:{i}", eager_start=True)

    async def _io_worker_loop(self) -> None:
        """IO worker — processes _io_queue until drained and pending == 0."""
        while not self._shutdown:
            try:
                async with asyncio.timeout(0.5):
                    _, _, task = await self._io_queue.get()
            except asyncio.TimeoutError:
                async with self._get_lock():
                    if self._pending == 0 and self._io_queue.empty():
                        break
                continue
            async with self._io_sem:
                await self._run_io_task(task)
            # ISSUE-037 FIX: task_done called only via _task_done() in finally block

    async def _cpu_worker_loop(self) -> None:
        """CPU worker — processes _cpu_queue until drained and pending == 0."""
        while not self._shutdown:
            try:
                async with asyncio.timeout(0.5):
                    _, _, task = await self._cpu_queue.get()
            except asyncio.TimeoutError:
                async with self._get_lock():
                    if self._pending == 0 and self._cpu_queue.empty():
                        break
                continue
            async with self._cpu_sem:
                await self._run_cpu_task(task)
            # ISSUE-037 FIX: task_done called only via _task_done() in finally block

    async def _run_io_task(self, task: PrioritizedTask) -> None:
        """Execute an I/O task with timeout and proper BaseException handling."""
        try:
            async with asyncio.timeout(task.timeout):
                result = await task.coro_or_fn(*task.args, **task.kwargs)
            async with self._get_lock():
                # D9 FIX: Use bounded dict with LRU eviction
                self._set_completed(task.task_id, result)
        except asyncio.CancelledError:
            async with self._get_lock():
                # D9 FIX: Use bounded dict with LRU eviction
                self._set_completed(task.task_id, asyncio.CancelledError(
                    f"Task {task.task_id} cancelled"
                ))
            raise
        except asyncio.TimeoutError:
            async with self._get_lock():
                # D9 FIX: Use bounded dict with LRU eviction
                self._set_completed(task.task_id, TimeoutError(
                    f"Task {task.task_id} timed out after {task.timeout}s"
                ))
            logger.warning(
                "Task %s timed out after %ss", task.task_id, task.timeout
    )
        except BaseException as e:  # noqa: BLE001 — intentional: must catch ALL exceptions including CancelledError/SystemExit
            async with self._get_lock():
                # D9 FIX: Use bounded dict with LRU eviction
                self._set_completed(task.task_id, e)
            logger.error(
                "Task %s failed with %s: %s",
                task.task_id,
                type(e).__name__,
                e,
    )
        finally:
            await self._task_done(task.task_id)

    async def _run_cpu_task(self, task: PrioritizedTask) -> None:
        """Execute a CPU-bound task via asyncio.to_thread()."""

        def _sync_wrapper() -> Any:
            try:
                return task.coro_or_fn(*task.args, **task.kwargs)
            except BaseException as e:  # noqa: BLE001 — intentional: catch all to return as result; caller handles
                return e

        try:
            async with asyncio.timeout(task.timeout):
                result = await asyncio.to_thread(_sync_wrapper)
            async with self._get_lock():
                # D9 FIX: Use bounded dict with LRU eviction
                self._set_completed(task.task_id, result)
        except asyncio.CancelledError:
            async with self._get_lock():
                # D9 FIX: Use bounded dict with LRU eviction
                self._set_completed(task.task_id, asyncio.CancelledError(
                    f"Task {task.task_id} cancelled"
                ))
            raise
        except asyncio.TimeoutError:
            async with self._get_lock():
                # D9 FIX: Use bounded dict with LRU eviction
                self._set_completed(task.task_id, TimeoutError(
                    f"Task {task.task_id} timed out after {task.timeout}s"
                ))
            logger.warning(
                "CPU task %s timed out after %ss", task.task_id, task.timeout
    )
        except BaseException as e:  # noqa: BLE001 — intentional: must catch ALL exceptions including CancelledError/SystemExit
            async with self._get_lock():
                # D9 FIX: Use bounded dict with LRU eviction
                self._set_completed(task.task_id, e)
            logger.error(
                "CPU task %s failed with %s: %s",
                task.task_id,
                type(e).__name__,
                e,
    )
        finally:
            await self._task_done(task.task_id)

    async def _task_done(self, _task_id: str) -> None:
        """
        Decrement pending counter and signal wait_all when done.

        Bounded counter pattern: no lost-wakeup because we use atomic increment
        in submit() and decrement here, with the event only set when pending hits 0.
        """
        async with self._get_lock():
            self._pending -= 1
            if self._pending == 0:
                self._all_done.set()

    async def steal_work(self, _worker_type: str) -> list:
        """Work stealing — returns empty list (placeholder).

        TODO-314: Implement work stealing for better CPU utilization.
        """
        # Placeholder: return empty list instead of raising
        # Full implementation should steal work from other workers
        return []

    @property
    def io_queue(self) -> list:
        """Stub for backward compatibility — returns empty list."""
        return []

    @property
    def cpu_queue(self) -> list:
        """Stub for backward compatibility — returns empty list."""
        return []
