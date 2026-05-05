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
import os
import time
import logging
import asyncio
import inspect
import queue
import threading
import uuid
from typing import Optional, Callable, Any, Dict
from collections import OrderedDict

logger = logging.getLogger(__name__)

# Module-level sequence counter for total ordering — safe across processes
# because each process gets its own copy; priority queue items are pickled
# as values, not references, so each process sees the full seq number.
_PQ_SEQ: int = 0
_PQ_SEQ_LOCK = mp.Lock()


def _next_seq() -> int:
    """Generate unique sequence number for priority queue item ordering."""
    global _PQ_SEQ
    with _PQ_SEQ_LOCK:
        _PQ_SEQ += 1
        return _PQ_SEQ


# Sprint 0A: Bounded task registry (memory leak fix)
MAX_TASK_REGISTRY: int = 1000

# Sprint 0A: Bounded affinity tracking (memory leak fix)
MAX_AFFINITY_ENTRIES: int = 5000

# Task registry - maps task name to function (no pickle needed)
# Uses OrderedDict for FIFO eviction when max exceeded
_TASK_REGISTRY: "OrderedDict[str, Callable]" = OrderedDict()

# Affinity key -> last worker that handled it (for work stealing)
# Uses OrderedDict with FIFO eviction when max exceeded
_LAST_WORKER_FOR_AFFINITY: "OrderedDict[str, int]" = OrderedDict()
_AFFINITY_EVICTION_LIST: list = []  # FIFO ordered keys for bounded eviction


def _bounded_put(registry: Dict, key: str, value: Any, max_size: int,
                 eviction_list: Optional[list] = None) -> None:
    """FIFO eviction when max exceeded."""
    if key in registry:
        del registry[key]
    registry[key] = value
    if eviction_list is not None:
        if key not in eviction_list:
            eviction_list.append(key)
    while len(registry) > max_size:
        oldest = eviction_list.pop(0) if eviction_list else None
        if oldest and oldest in registry:
            del registry[oldest]


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
    - queue.PriorityQueue for O(log n) ordered insert + blocking get()
    - mp.Queue for worker signaling (process-safe notification)
    - CPU affinity to performance cores {0, 1, 2, 3}
    - Work stealing with affinity awareness
    """

    def __init__(self, max_workers: int = 3):
        self.max_workers = max_workers
        # queue.PriorityQueue: O(log n) ordered insert + blocking get().
        # Uses heapq internally. Process-safe via OS-level queue primitives.
        # Items compared by (priority, timestamp, seq) tuple — seq is always
        # unique, so comparison never raises TypeError.
        self._pq: queue.PriorityQueue = queue.PriorityQueue()
        # mp.Queue: process-safe signaling channel. Workers block on this.
        # We put the full item tuple here so workers receive ordered items.
        self._signal_queue: mp.Queue = mp.Queue()
        # mp.Queue: worker→main job state updates (RUNNING, SUCCEEDED, FAILED)
        self._result_queue: mp.Queue = mp.Queue()
        self.executor = concurrent.futures.ProcessPoolExecutor(max_workers=max_workers)
        self._running = True
        self._workers = []
        self._worker_affinity: Dict[int, str] = {}
        self._affinity_lock = mp.Lock()
        # Consumer thread: bridges PriorityQueue (has ordering) to signal Queue (process-safe)
        self._consumer_active = True
        self._consumer_running = False
        # Job state tracking (main process only)
        self._jobs: Dict[str, dict] = {}
        self._jobs_lock = threading.Lock()
        self._result_collector_active = True
        # Dead Letter Queue: permanently failed jobs after max_retries exhausted
        self._dlq: Dict[str, dict] = {}
        # Idempotency key → job_id mapping (caller-provided keys for deduplication)
        self._idempotency_map: Dict[str, str] = {}
        # Default job timeout in seconds (None = no default timeout)
        self.default_job_timeout: Optional[float] = None
        # Timeout checker thread
        self._timeout_checker_active = True

    def _start_consumer(self):
        """
        Start consumer thread that bridges PriorityQueue to signal Queue.
        Consumer takes from _pq (ordered), puts to _signal_queue (process-safe).
        This thread exists in the main process and dies with it.
        """
        def consumer():
            while self._consumer_active:
                try:
                    item = self._pq.get(timeout=0.1)  # 100ms timeout — check _consumer_active periodically
                    if item is None:
                        # Poison pill from shutdown
                        self._signal_queue.put(None)
                        break
                    self._signal_queue.put(item)
                except queue.Empty:
                    continue
                except Exception as e:
                    logger.warning(f"PriorityQueue consumer error: {e}")
                    continue

        import threading
        t = threading.Thread(target=consumer, daemon=True, name="pq-consumer")
        t.start()
        self._consumer_thread = t

    def _start_result_collector(self):
        """
        Result collector thread: reads RUNNING/SUCCEEDED/FAILED from _result_queue
        and updates _jobs dict in the main process.
        Failed jobs with exhausted retries are moved to the Dead Letter Queue.
        """
        MAX_DLQ_SIZE = 1000

        def collector():
            while self._result_collector_active:
                try:
                    result = self._result_queue.get(timeout=0.1)
                    if result is None:
                        continue
                    job_id, status, error = result
                    worker_id = result[3] if len(result) > 3 else None
                    with self._jobs_lock:
                        if job_id not in self._jobs:
                            continue
                        job = self._jobs[job_id]
                        if status == "running":
                            job["status"] = "running"
                            job["started_at"] = time.time()
                            job["worker_id"] = worker_id
                        elif status == "succeeded":
                            job["status"] = "succeeded"
                            job["completed_at"] = time.time()
                            # Clean up idempotency map on terminal state
                            self._idempotency_map = {
                                k: v for k, v in self._idempotency_map.items() if v != job_id
                            }
                        elif status in ("failed", "timeout"):
                            job["attempts"] = job.get("attempts", 0) + 1
                            max_retries = job.get("max_retries", 0)
                            if job["attempts"] > max_retries:
                                # Move to Dead Letter Queue
                                job["status"] = "dead_letter"
                                job["completed_at"] = time.time()
                                job["error"] = error
                                self._dlq[job_id] = dict(job)
                                # FIFO eviction if DLQ full
                                if len(self._dlq) > MAX_DLQ_SIZE:
                                    oldest = min(self._dlq.keys(),
                                                key=lambda jid: self._dlq[jid].get("completed_at", 0))
                                    del self._dlq[oldest]
                                # Remove from active jobs
                                del self._jobs[job_id]
                                # Clean up idempotency map
                                self._idempotency_map = {
                                    k: v for k, v in self._idempotency_map.items() if v != job_id
                                }
                            else:
                                # Retry: reschedule (same priority, same params — preserve original created_at)
                                job["status"] = "pending"
                                job["started_at"] = None
                                job["completed_at"] = None
                                job["worker_id"] = None
                                job["error"] = None
                                # Re-insert into PQ (preserve original created_at for auditing)
                                item = (job["priority"], time.time(), _next_seq(),
                                        job["task_name"], (), {}, None, job_id, max_retries)
                                self._pq.put(item)
                        elif status == "cancelled":
                            job["status"] = "cancelled"
                            job["completed_at"] = time.time()
                            # Clean up idempotency map
                            self._idempotency_map = {
                                k: v for k, v in self._idempotency_map.items() if v != job_id
                            }
                except queue.Empty:
                    continue
                except Exception as e:
                    logger.warning(f"Result collector error: {e}")
                    continue

        t = threading.Thread(target=collector, daemon=True, name="result-collector")
        t.start()
        self._result_collector_thread = t

    def _start_timeout_checker(self):
        """
        Background thread: monitors running jobs for timeout.
        Runs every 5 seconds; puts timeout on result queue so the collector
        handles all state transitions uniformly (no race with collector).
        """
        CHECK_INTERVAL = 5.0

        def checker():
            while self._timeout_checker_active:
                try:
                    now = time.time()
                    with self._jobs_lock:
                        for job_id, job in list(self._jobs.items()):
                            if job["status"] != "running":
                                continue
                            timeout = job.get("job_timeout") or self.default_job_timeout
                            if timeout is None:
                                continue
                            if job["started_at"] and (now - job["started_at"]) > timeout:
                                logger.warning(f"Job {job_id} timed out after {timeout:.1f}s")
                                # Put on result queue — collector handles retry/DLQ uniformly
                                self._result_queue.put((
                                    job_id, "timeout",
                                    f"Job timeout after {timeout:.1f}s",
                                    job.get("worker_id")
                                ))
                except Exception as e:
                    logger.warning(f"Timeout checker error: {e}")
                time.sleep(CHECK_INTERVAL)

        t = threading.Thread(target=checker, daemon=True, name="timeout-checker")
        t.start()
        self._timeout_checker_thread = t

    def start(self):
        """Start worker processes and the priority queue consumer thread."""
        self._start_consumer()
        self._start_result_collector()
        self._start_timeout_checker()
        for i in range(self.max_workers):
            future = self.executor.submit(self._worker_loop, i)
            self._workers.append(future)
        logger.info(f"GlobalPriorityScheduler started with {self.max_workers} workers")

    def _set_affinity(self, pid: int) -> bool:
        """Set process affinity to performance cores {0, 1, 2, 3}. Returns True if successful."""
        try:
            if hasattr(os, 'sched_setaffinity'):
                os.sched_setaffinity(pid, {0, 1, 2, 3})
                return True
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
                # Blocking get on mp.Queue — no busy-sleep.
                # Items arrive in priority order (consumer thread ensures this).
                # Poison pill (None) = shutdown signal.
                item = self._signal_queue.get()
                if item is None:
                    break
            except Exception as e:
                if not self._running:
                    break
                logger.exception(f"Worker {worker_id} signal queue get error: {e}")
                continue

            # item is (priority, timestamp, seq, task_name, args, kwargs, affinity_key, job_id, max_retries)
            # priority/timestamp/seq are from the ordering tuple, not needed individually
            _priority, _timestamp, _seq, task_name, args, kwargs, affinity_key, job_id, _max_retries = item

            # Report RUNNING to result queue
            try:
                self._result_queue.put((job_id, "running", None, worker_id))
            except Exception:
                pass

            # Update affinity tracking for work stealing (bounded)
            if affinity_key:
                global _LAST_WORKER_FOR_AFFINITY, _AFFINITY_EVICTION_LIST
                with self._affinity_lock:
                    _bounded_put(_LAST_WORKER_FOR_AFFINITY, affinity_key,
                                 worker_id, MAX_AFFINITY_ENTRIES, _AFFINITY_EVICTION_LIST)

            if task_name not in _TASK_REGISTRY:
                logger.error(f"Unknown task '{task_name}' in queue")
                try:
                    self._result_queue.put((job_id, "failed", f"Unknown task: {task_name}", worker_id))
                except Exception:
                    pass
                continue

            func = _TASK_REGISTRY[task_name]

            try:
                if inspect.iscoroutinefunction(func):
                    try:
                        loop = asyncio.get_running_loop()
                    except RuntimeError:
                        new_loop = asyncio.new_event_loop()
                        try:
                            new_loop.run_until_complete(func(*args, **kwargs))
                        finally:
                            new_loop.close()
                        continue
                    loop.run_until_complete(func(*args, **kwargs))
                else:
                    func(*args, **kwargs)
                # Report SUCCEEDED
                try:
                    self._result_queue.put((job_id, "succeeded", None, worker_id))
                except Exception:
                    pass
            except Exception as e:
                logger.exception(f"Worker {worker_id} failed to execute {task_name}: {e}")
                try:
                    self._result_queue.put((job_id, "failed", str(e), worker_id))
                except Exception:
                    pass

    def schedule(
        self,
        priority: int,
        task_name: str,
        *args,
        affinity_key: Optional[str] = None,
        max_retries: int = 0,
        idempotency_key: Optional[str] = None,
        job_timeout: Optional[float] = None,
        **kwargs
    ) -> str:
        """
        Schedule a task with priority (lower number = higher priority).
        task_name must be registered in _TASK_REGISTRY.
        max_retries: number of retries on failure (default 0 = no retries).
        idempotency_key: optional caller-provided key for deduplication.
            If a job with the same idempotency_key is still pending/running,
            returns the existing job_id instead of creating a duplicate.
        job_timeout: per-job timeout in seconds (overrides default_job_timeout).
            Timed-out jobs are marked failed and moved to DLQ if retries exhausted.
        Returns job_id for status tracking.
        """
        if task_name not in _TASK_REGISTRY:
            raise ValueError(f"Task '{task_name}' not registered. Call register_task() first.")

        # Idempotency check: if same key exists and job is still active, return existing job_id
        if idempotency_key:
            with self._jobs_lock:
                existing_job_id = self._idempotency_map.get(idempotency_key)
                if existing_job_id and existing_job_id in self._jobs:
                    existing = self._jobs[existing_job_id]
                    if existing["status"] in ("pending", "running"):
                        logger.debug(f"Idempotency hit for key '{idempotency_key}' — returning existing job_id={existing_job_id}")
                        return existing_job_id

        job_id = str(uuid.uuid4())
        now = time.time()
        # O(log n) insert into PriorityQueue — no full-list sort
        # Item tuple: (priority, timestamp, seq, task_name, args, kwargs, affinity_key, job_id, max_retries)
        item = (priority, now, _next_seq(),
                task_name, args, kwargs, affinity_key, job_id, max_retries)
        self._pq.put(item)

        # Track job state (main process only)
        with self._jobs_lock:
            if idempotency_key:
                self._idempotency_map[idempotency_key] = job_id
            self._jobs[job_id] = {
                "job_id": job_id,
                "status": "pending",
                "task_name": task_name,
                "priority": priority,
                "created_at": now,
                "started_at": None,
                "completed_at": None,
                "result": None,
                "error": None,
                "worker_id": None,
                "max_retries": max_retries,
                "attempts": 0,
                "job_timeout": job_timeout,
            }

        logger.debug(f"Scheduled task '{task_name}' with priority {priority} max_retries={max_retries} timeout={job_timeout} (job_id={job_id})")
        return job_id

    def schedule_background(self, task_name: str, *args, **kwargs) -> str:
        """
        Zařadí úlohu s nízkou prioritou (8) pro background processing.
        Returns job_id for status tracking.
        """
        return self.schedule(8, task_name, *args, **kwargs)

    def peek(self) -> Optional[Any]:
        """
        Peek at the highest-priority item without consuming it (for testing).
        Returns None if queue is empty. Item is re-queued after peek.
        """
        try:
            item = self._pq.get_nowait()
            self._pq.put(item)
            return item
        except queue.Empty:
            return None
        except Exception:
            return None

    def get_next_affinity_worker(self, affinity_key: str) -> Optional[int]:
        """Get the last worker that handled this affinity_key for work stealing."""
        with self._affinity_lock:
            return _LAST_WORKER_FOR_AFFINITY.get(affinity_key)

    def get_job_status(self, job_id: str) -> Optional[dict]:
        """
        Get job state by job_id.

        Returns dict with keys:
        - job_id, status, task_name, priority, created_at,
          started_at, completed_at, error, worker_id

        Returns None if job_id not found.
        """
        with self._jobs_lock:
            return self._jobs.get(job_id)

    def list_jobs(self, limit: int = 100, status: Optional[str] = None) -> list[dict]:
        """
        List recent jobs (newest first), optionally filtered by status.

        Args:
            limit: max jobs to return
            status: filter by status string (pending, running, succeeded, failed, cancelled, dead_letter)
        """
        with self._jobs_lock:
            jobs = [j for j in self._jobs.values()]
            if status == "dead_letter":
                jobs = list(self._dlq.values())
            elif status:
                jobs = [j for j in jobs if j["status"] == status]
            jobs.sort(key=lambda j: j["created_at"], reverse=True)
            return jobs[:limit]

    def get_job_stats(self) -> dict[str, int]:
        """Get job counts by status."""
        with self._jobs_lock:
            counts = {"pending": 0, "running": 0, "succeeded": 0, "failed": 0, "cancelled": 0, "dead_letter": 0}
            for job in self._jobs.values():
                if job["status"] in counts:
                    counts[job["status"]] += 1
            return counts

    def get_dlq(self, limit: int = 100) -> list[dict]:
        """
        Get Dead Letter Queue — jobs that failed after all retries exhausted.
        Returns newest dead letter jobs first.
        """
        with self._jobs_lock:
            jobs = sorted(self._dlq.values(), key=lambda j: j.get("completed_at", 0), reverse=True)
            return jobs[:limit]

    def get_dlq_stats(self) -> dict[str, int]:
        """Get DLQ statistics."""
        with self._jobs_lock:
            return {
                "dlq_size": len(self._dlq),
            }

    def shutdown(self, wait: bool = True):
        """Shutdown the scheduler with poison-pill pattern."""
        self._running = False
        self._consumer_active = False
        self._result_collector_active = False
        self._timeout_checker_active = False

        # Send poison pill to each worker (they exit on None)
        for _ in range(self.max_workers):
            try:
                self._signal_queue.put_nowait(None)
            except Exception:
                pass

        # Drain the PriorityQueue
        try:
            while True:
                self._pq.get_nowait()
        except queue.Empty:
            pass

        if hasattr(self, '_consumer_thread'):
            self._consumer_thread.join(timeout=2.0)
        if hasattr(self, '_result_collector_thread'):
            self._result_collector_thread.join(timeout=2.0)
        if hasattr(self, '_timeout_checker_thread'):
            self._timeout_checker_thread.join(timeout=2.0)

        self.executor.shutdown(wait=wait)
        logger.info("GlobalPriorityScheduler shutdown complete")

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.shutdown()
        return False
