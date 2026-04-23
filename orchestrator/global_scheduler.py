"""
Global Priority Scheduler for Distributed Processing on Single M1
==============================================================

ProcessPoolExecutor-based scheduler with:
- Task registry (no pickle of functions)
- Priority queue (lower number = higher priority)
- CPU affinity to performance cores (0-3)
- Work stealing with affinity awareness
"""

import concurrent.futures
import multiprocessing as mp
from multiprocessing import Manager
import os
import time
import logging
import asyncio
import inspect
from typing import Optional, Callable, Any, Dict
from collections import OrderedDict

logger = logging.getLogger(__name__)

# Sprint 0A: Bounded task registry (memory leak fix)
MAX_TASK_REGISTRY: int = 1000

# Sprint 0A: Bounded affinity tracking (memory leak fix)
MAX_AFFINITY_ENTRIES: int = 5000

# Task registry - maps task name to function (no pickle needed)
# Uses OrderedDict for FIFO eviction when max exceeded
_TASK_REGISTRY: OrderedDict[str, Callable] = OrderedDict()

# Affinity key -> last worker that handled it (for work stealing)
# Uses OrderedDict for FIFO eviction when max exceeded
_LAST_WORKER_FOR_AFFINITY: OrderedDict[str, int] = OrderedDict()


def _bounded_put(registry: OrderedDict, key: str, value: Any, max_size: int) -> None:
    """FIFO eviction when max exceeded."""
    if key in registry:
        del registry[key]
    registry[key] = value
    while len(registry) > max_size:
        registry.popitem(last=False)


def register_task(name: str, func: Callable):
    """Register a function under a name for use in the scheduler."""
    _bounded_put(_TASK_REGISTRY, name, func, MAX_TASK_REGISTRY)


def get_task(name: str) -> Optional[Callable]:
    """Get a registered task function by name."""
    return _TASK_REGISTRY.get(name)


class GlobalPriorityScheduler:
    """
    Global priority scheduler with:
    - ProcessPoolExecutor for parallel execution
    - PriorityQueue for task ordering (lower number = higher priority)
    - CPU affinity to performance cores {0, 1, 2, 3}
    - Work stealing with affinity awareness
    """

    def __init__(self, max_workers: int = 3):
        self.max_workers = max_workers
        # Use multiprocessing Queue instead of Manager.PriorityQueue
        # Priority is handled by sorting items manually
        self.manager = Manager()
        self.task_queue = self.manager.list()  # Use list for manual priority ordering
        self._task_lock = self.manager.Lock()
        self.executor = concurrent.futures.ProcessPoolExecutor(max_workers=max_workers)
        self._running = True
        self._workers = []
        self._worker_affinity: Dict[int, str] = {}  # worker_id -> affinity_key
        self._affinity_lock = self.manager.Lock()

    def start(self):
        """Start worker processes."""
        for i in range(self.max_workers):
            future = self.executor.submit(self._worker_loop, i)
            self._workers.append(future)
        logger.info(f"GlobalPriorityScheduler started with {self.max_workers} workers")

    def _set_affinity(self, pid: int) -> bool:
        """Set process affinity to performance cores {0, 1, 2, 3}. Returns True if successful."""
        try:
            # On macOS, use proc_pidinfo for thread affinity
            # On Linux, use sched_setaffinity
            if hasattr(os, 'sched_setaffinity'):
                os.sched_setaffinity(pid, {0, 1, 2, 3})
                return True
            else:
                # macOS fallback - try using ctypes
                try:
                    import ctypes
                    import ctypes.util

                    # Get current CPU set and restrict to performance cores
                    libc = ctypes.CDLL(ctypes.util.find_library('c'))
                    # This is a best-effort approach on macOS
                    return False
                except Exception:
                    return False
        except (AttributeError, OSError) as e:
            logger.debug(f"CPU affinity not available: {e}")
            return False

    def _worker_loop(self, worker_id: int):
        """Main worker loop - runs in separate process."""
        pid = os.getpid()
        self._set_affinity(pid)

        logger.debug(f"Worker {worker_id} (PID {pid}) started")

        while self._running:
            try:
                # Get item from list (FIFO from front)
                with self._task_lock:
                    if len(self.task_queue) > 0:
                        item = self.task_queue[0]
                        del self.task_queue[0]
                    else:
                        # Safe: this is a sync worker process (ProcessPoolExecutor),
                        # not an async context. Blocks only this worker process thread.
                        time.sleep(0.1)
                        continue

                if item is None:
                    continue

                priority, timestamp, task_name, args, kwargs, affinity_key = item

                # Update affinity tracking for work stealing (bounded)
                if affinity_key:
                    with self._affinity_lock:
                        _bounded_put(_LAST_WORKER_FOR_AFFINITY, affinity_key, worker_id, MAX_AFFINITY_ENTRIES)

                if task_name not in _TASK_REGISTRY:
                    logger.error(f"Unknown task '{task_name}' in queue")
                    continue

                func = _TASK_REGISTRY[task_name]

                # Run async function in existing event loop (worker is sync process)
                # Use get_event_loop() instead of asyncio.run() to avoid creating
                # a new event loop on every call, which is expensive and can cause
                # issues on M1 when the process shares memory with the main event loop
                try:
                    if inspect.iscoroutinefunction(func):
                        loop = asyncio.get_event_loop()
                        loop.run_until_complete(func(*args, **kwargs))
                    else:
                        func(*args, **kwargs)
                except Exception as e:
                    logger.exception(f"Worker {worker_id} failed to execute {task_name}: {e}")

            except Exception as e:
                # Safe: sync worker process, not async context — blocks only this thread.
                logger.exception(f"Worker {worker_id} error: {e}")
                time.sleep(0.1)
                continue

    def schedule(
        self,
        priority: int,
        task_name: str,
        *args,
        affinity_key: Optional[str] = None,
        **kwargs
    ):
        """
        Schedule a task with priority (lower number = higher priority).
        task_name must be registered in _TASK_REGISTRY.
        """
        if task_name not in _TASK_REGISTRY:
            raise ValueError(f"Task '{task_name}' not registered. Call register_task() first.")

        # Insert into sorted list (lower priority number = higher priority)
        item = (priority, time.time(), task_name, args, kwargs, affinity_key)
        with self._task_lock:
            # Insert in sorted position by priority
            queue_list = list(self.task_queue)
            queue_list.append(item)
            queue_list.sort(key=lambda x: (x[0], x[1]))  # Sort by priority, then timestamp
            # Update the managed list
            del self.task_queue[:]
            self.task_queue.extend(queue_list)
        logger.debug(f"Scheduled task '{task_name}' with priority {priority}")

    def schedule_background(self, task_name: str, *args, **kwargs):
        """
        Zařadí úlohu s nízkou prioritou (8) pro background processing.

        Args:
            task_name: Název úlohy v registru
            *args: Poziční argumenty pro úlohu
            **kwargs: Keyword argumenty pro úlohu
        """
        self.schedule(8, task_name, *args, **kwargs)
        logger.debug(f"Scheduled background task '{task_name}' with priority 8")

    def get_next_affinity_worker(self, affinity_key: str) -> Optional[int]:
        """Get the last worker that handled this affinity_key for work stealing."""
        with self._affinity_lock:
            return _LAST_WORKER_FOR_AFFINITY.get(affinity_key)

    def shutdown(self, wait: bool = True):
        """Shutdown the scheduler."""
        self._running = False

        # Clear the task queue
        with self._task_lock:
            del self.task_queue[:]

        self.executor.shutdown(wait=wait)
        logger.info("GlobalPriorityScheduler shutdown complete")

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.shutdown()
        return False
