"""
ParallelResearchScheduler – spravuje frontu úloh s prioritami.
Používá asyncio pro I/O úlohy a ThreadPoolExecutor pro CPU-bound úlohy.
Implementuje work stealing mezi worker vlákny (experimentální).
"""

import asyncio
import concurrent.futures
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from heapq import heappop, heappush
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(order=True)
class PrioritizedTask:
    """Úloha s prioritou pro frontu."""
    priority: float  # vyšší = dřívější (v heapu je -priority, protože heappush je min-heap)
    task_id: str = field(compare=False)
    coro_or_fn: Any = field(compare=False)  # async function nebo sync funkce
    args: tuple = field(default=(), compare=False)
    kwargs: dict = field(default_factory=dict, compare=False)
    created_at: float = field(default_factory=time.time, compare=False)
    metadata: dict[str, Any] = field(default_factory=dict, compare=False)
    is_coro: bool = True
    timeout: float = 30.0


class ParallelResearchScheduler:
    """
    Asynchronní plánovač s prioritní frontou pro výzkumné úlohy.
    Podporuje oddělené I/O a CPU fronty s adaptivní concurrency.
    """

    def __init__(self, resource_allocator=None,
                 max_concurrent_io: int = 10,
                 max_concurrent_cpu: int = 4):
        self.resource_allocator = resource_allocator
        self.max_concurrent_io = max_concurrent_io
        self.max_concurrent_cpu = max_concurrent_cpu
        self.io_queue: list[PrioritizedTask] = []
        self.cpu_queue: list[PrioritizedTask] = []
        self.running_io: dict[str, asyncio.Task] = {}
        self.running_cpu: dict[str, concurrent.futures.Future] = {}
        self.completed: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        # Event-based wait — no busy-polling in wait_all()
        # Initially set (no work yet)
        self._work_done = asyncio.Event()
        self._work_done.set()

        # ThreadPoolExecutor pro CPU úlohy
        self._cpu_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=max_concurrent_cpu, thread_name_prefix="parallel_cpu"
        )

    async def get_recommended_concurrency(self, task_type: str) -> int:
        """Vrátí doporučenou concurrency podle typu úlohy a aktuálních zdrojů."""
        if self.resource_allocator and hasattr(self.resource_allocator, 'get_recommended_concurrency'):
            return await self.resource_allocator.get_recommended_concurrency(task_type)

        # Default hodnoty
        if task_type == 'io':
            return self.max_concurrent_io
        else:  # cpu
            return self.max_concurrent_cpu

    async def submit(self, task_id: str, coro_or_fn: Callable,
                     priority: float = 1.0,
                     metadata: dict | None = None,
                     is_coro: bool = True,
                     timeout: float | None = None,
                     *args, **kwargs):
        """Přidá úlohu do příslušné fronty."""
        async with self._lock:
            # New work arriving — clear the all-done event
            self._work_done.clear()
            current_max_io = await self.get_recommended_concurrency('io')
            current_max_cpu = await self.get_recommended_concurrency('cpu')

            task = PrioritizedTask(
                -priority,  # negative for min-heap
                task_id,
                coro_or_fn,
                args,
                kwargs,
                metadata=metadata or {},
                is_coro=is_coro,
                timeout=timeout or (30.0 if is_coro else 10.0)
            )

            if is_coro:
                if len(self.running_io) < current_max_io:
                    await self._start_io_task(task)
                else:
                    heappush(self.io_queue, task)
            else:
                if len(self.running_cpu) < current_max_cpu:
                    self._start_cpu_task(task)
                else:
                    heappush(self.cpu_queue, task)

    async def _start_io_task(self, task: PrioritizedTask):
        """Spustí I/O úlohu."""
        t = asyncio.create_task(self._run_io_task(task), name=f"parallel_scheduler:io_task:{task.task_id}")
        self.running_io[task.task_id] = t

    def _start_cpu_task(self, task: PrioritizedTask):
        """Spustí CPU úlohu v thread poolu."""
        future = self._cpu_executor.submit(self._run_cpu_task_sync, task)
        self.running_cpu[task.task_id] = future

        # Správné předání do event loop z thread poolu
        try:
            loop = asyncio.get_running_loop()
            future.add_done_callback(
                lambda f: loop.call_soon_threadsafe(
                    lambda: asyncio.create_task(self._on_cpu_done(task.task_id, f), name=f"parallel_scheduler:cpu_done:{task.task_id}")
                )
            )
        except RuntimeError:
            # Event loop není dostupný, zpracujeme synchronně
            pass

    async def _run_io_task(self, task: PrioritizedTask):
        """Spustí I/O úlohu s timeoutem."""
        try:
            result = await asyncio.wait_for(
                task.coro_or_fn(*task.args, **task.kwargs),
                timeout=task.timeout
            )
            self.completed[task.task_id] = result
        except TimeoutError:
            self.completed[task.task_id] = TimeoutError(f"Task {task.task_id} timed out")
            logger.warning(f"Task {task.task_id} timed out after {task.timeout}s")
        except Exception as e:
            self.completed[task.task_id] = e
            logger.error(f"Task {task.task_id} failed: {e}")
        finally:
            await self._task_done(task.task_id, is_coro=True)

    def _run_cpu_task_sync(self, task: PrioritizedTask):
        """Spustí CPU úlohu synchronně."""
        try:
            return task.coro_or_fn(*task.args, **task.kwargs)
        except Exception as e:
            return e

    async def _on_cpu_done(self, task_id: str, future: concurrent.futures.Future):
        """Zpracuje dokončení CPU úlohy."""
        try:
            result = future.result()
            self.completed[task_id] = result
        except Exception as e:
            self.completed[task_id] = e
            logger.error(f"CPU task {task_id} failed: {e}")
        await self._task_done(task_id, is_coro=False)

    async def _task_done(self, task_id: str, is_coro: bool):
        """Zpracuje dokončení úlohy a spustí další z fronty."""
        async with self._lock:
            if is_coro:
                self.running_io.pop(task_id, None)
                if self.io_queue:
                    next_task = heappop(self.io_queue)
                    await self._start_io_task(next_task)
            else:
                self.running_cpu.pop(task_id, None)
                if self.cpu_queue:
                    next_task = heappop(self.cpu_queue)
                    self._start_cpu_task(next_task)

            # Signal wait_all when all work is done
            if not self.running_io and not self.running_cpu:
                self._work_done.set()

    async def steal_work(self, worker_type: str):
        """
        Work stealing – experimentální.
        Zatím neimplementováno, placeholder pro budoucí rozšíření.
        """
        pass

    async def get_status(self) -> dict[str, Any]:
        """Vrátí aktuální stav plánovače."""
        async with self._lock:
            return {
                'running_io': len(self.running_io),
                'running_cpu': len(self.running_cpu),
                'queued_io': len(self.io_queue),
                'queued_cpu': len(self.cpu_queue),
                'completed': len(self.completed)
            }

    def shutdown(self, wait: bool = True):
        """Ukončí plánovač a uvolní zdroje."""
        self._cpu_executor.shutdown(wait=wait)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.shutdown()
        return False

    async def wait_all(self, timeout: float | None = None):
        """
        Počká na dokončení všech úloh — event-based, no polling.

        Uses asyncio.Event for efficient wait instead of busy-sleep polling.
        """
        try:
            await asyncio.wait_for(self._work_done.wait(), timeout=timeout)
        except TimeoutError:
            pass  # timeout exceeded, return anyway

    # Priority constants

    # Priority constants
    PRIORITY_RESEARCH = 5
    PRIORITY_PREFETCH = 9
    PRIORITY_BACKGROUND = 10

    async def schedule_prefetch(self, task_id: str, coro_or_fn, priority: int,
                                is_coro: bool, url: str, deadline: float,
                                estimated_bytes: int, metadata: dict):
        """Naplánuje prefetch úlohu."""
        await self.submit(
            task_id=task_id,
            coro_or_fn=coro_or_fn,
            priority=priority,
            is_coro=is_coro,
            timeout=deadline - time.time() if deadline > time.time() else 1.0,
            url=url,
            deadline=deadline,
            estimated_bytes=estimated_bytes,
            metadata=metadata
        )
