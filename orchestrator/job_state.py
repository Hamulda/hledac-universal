"""
Job State Machine for GlobalPriorityScheduler
===============================================

Tracks state transitions: PENDING → RUNNING → SUCCEEDED | FAILED | CANCELLED

- JobState dataclass: immutable snapshot of job state
- JobStore: in-memory + LMDB-persisted job registry
- Bounded storage: MAX_JOBS entries, FIFO eviction
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class JobStatus(Enum):
    """Job state transitions: PENDING → RUNNING → SUCCEEDED | FAILED | CANCELLED"""
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class JobState:
    """
    Immutable snapshot of a job's state at a point in time.

    Transitions:
    - schedule()     → PENDING
    - worker start   → RUNNING
    - worker success → SUCCEEDED
    - worker failure → FAILED
    - cancel()       → CANCELLED
    """
    job_id: str
    status: JobStatus
    task_name: str
    priority: int
    created_at: float
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    result: Optional[Any] = None
    error: Optional[str] = None
    worker_id: Optional[int] = None
    attempts: int = 0


# Sprint 54: Bounded job store (memory leak fix)
MAX_JOBS: int = 5000


class JobStore:
    """
    In-memory job registry with LMDB persistence for crash recovery.

    - O(1) lookup by job_id
    - Bounded storage: MAX_JOBS entries, FIFO eviction
    - LMDB persistence: jobs survive process restarts
    """

    def __init__(self, lmdb_path: Optional[str] = None):
        self._jobs: Dict[str, JobState] = {}
        self._lmdb_path = lmdb_path
        self._lmdb_env = None
        self._lock = asyncio.Lock()
        if lmdb_path:
            self._init_lmdb()

    def _init_lmdb(self):
        """Initialize LMDB for crash recovery persistence."""
        try:
            import lmdb
            import orjson
            self._lmdb_env = lmdb.open(self._lmdb_path, max_dbs=1, max_readers=4)
            self._orjson = orjson
            logger.info(f"JobStore LMDB persistence enabled at {self._lmdb_path}")
        except ImportError:
            logger.warning("LMDB not available — job state not persisted")
            self._lmdb_env = None

    def _put_lmdb(self, job: JobState):
        """Persist job state to LMDB."""
        if self._lmdb_env is None:
            return
        try:
            key = job.job_id.encode()
            value = self._orjson.dumps({
                "job_id": job.job_id,
                "status": job.status.value,
                "task_name": job.task_name,
                "priority": job.priority,
                "created_at": job.created_at,
                "started_at": job.started_at,
                "completed_at": job.completed_at,
                "result": job.result,
                "error": job.error,
                "worker_id": job.worker_id,
                "attempts": job.attempts,
            })
            with self._lmdb_env.begin(write=True) as txn:
                txn.put(key, value)
        except Exception as e:
            logger.warning(f"JobStore LMDB write failed: {e}")

    def _get_lmdb(self, job_id: str) -> Optional[JobState]:
        """Recover job state from LMDB."""
        if self._lmdb_env is None:
            return None
        try:
            with self._lmdb_env.begin() as txn:
                data = txn.get(job_id.encode())
            if data is None:
                return None
            d = self._orjson.loads(data)
            return JobState(
                job_id=d["job_id"],
                status=JobStatus(d["status"]),
                task_name=d["task_name"],
                priority=d["priority"],
                created_at=d["created_at"],
                started_at=d.get("started_at"),
                completed_at=d.get("completed_at"),
                result=d.get("result"),
                error=d.get("error"),
                worker_id=d.get("worker_id"),
                attempts=d.get("attempts", 0),
            )
        except Exception as e:
            logger.warning(f"JobStore LMDB read failed: {e}")
            return None

    def _evict_fifo(self):
        """FIFO eviction when max exceeded — remove oldest completed jobs."""
        # Remove completed/cancelled jobs first (already terminal)
        terminal = [jid for jid, j in self._jobs.items() if j.status in (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED)]
        if len(terminal) > MAX_JOBS // 2:
            # Remove oldest terminal jobs
            terminal.sort(key=lambda jid: self._jobs[jid].completed_at or 0)
            remove = terminal[:len(terminal) - MAX_JOBS // 2]
            for jid in remove:
                del self._jobs[jid]
        elif len(self._jobs) >= MAX_JOBS:
            # Still need space — remove oldest pending jobs
            pending = [jid for jid, j in self._jobs.items() if j.status == JobStatus.PENDING]
            if pending:
                pending.sort(key=lambda jid: self._jobs[jid].created_at)
                remove = pending[:len(pending) - MAX_JOBS // 2]
                for jid in remove:
                    del self._jobs[jid]

    async def create(self, job_id: str, task_name: str, priority: int) -> JobState:
        """Create a new PENDING job."""
        async with self._lock:
            if len(self._jobs) >= MAX_JOBS:
                self._evict_fifo()
            job = JobState(
                job_id=job_id,
                status=JobStatus.PENDING,
                task_name=task_name,
                priority=priority,
                created_at=time.time(),
            )
            self._jobs[job_id] = job
            self._put_lmdb(job)
            return job

    async def mark_running(self, job_id: str, worker_id: int) -> Optional[JobState]:
        """Transition PENDING → RUNNING."""
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            updated = JobState(
                job_id=job.job_id,
                status=JobStatus.RUNNING,
                task_name=job.task_name,
                priority=job.priority,
                created_at=job.created_at,
                started_at=time.time(),
                worker_id=worker_id,
                attempts=job.attempts,
            )
            self._jobs[job_id] = updated
            self._put_lmdb(updated)
            return updated

    async def mark_succeeded(self, job_id: str, result: Any = None) -> Optional[JobState]:
        """Transition RUNNING → SUCCEEDED."""
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            updated = JobState(
                job_id=job.job_id,
                status=JobStatus.SUCCEEDED,
                task_name=job.task_name,
                priority=job.priority,
                created_at=job.created_at,
                started_at=job.started_at,
                completed_at=time.time(),
                result=result,
                worker_id=job.worker_id,
                attempts=job.attempts + 1,
            )
            self._jobs[job_id] = updated
            self._put_lmdb(updated)
            return updated

    async def mark_failed(self, job_id: str, error: str) -> Optional[JobState]:
        """Transition RUNNING → FAILED."""
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            updated = JobState(
                job_id=job.job_id,
                status=JobStatus.FAILED,
                task_name=job.task_name,
                priority=job.priority,
                created_at=job.created_at,
                started_at=job.started_at,
                completed_at=time.time(),
                error=error,
                worker_id=job.worker_id,
                attempts=job.attempts + 1,
            )
            self._jobs[job_id] = updated
            self._put_lmdb(updated)
            return updated

    async def mark_cancelled(self, job_id: str) -> Optional[JobState]:
        """Transition PENDING → CANCELLED."""
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            updated = JobState(
                job_id=job.job_id,
                status=JobStatus.CANCELLED,
                task_name=job.task_name,
                priority=job.priority,
                created_at=job.created_at,
                completed_at=time.time(),
                attempts=job.attempts,
            )
            self._jobs[job_id] = updated
            self._put_lmdb(updated)
            return updated

    async def get(self, job_id: str) -> Optional[JobState]:
        """Get job state by ID."""
        async with self._lock:
            return self._jobs.get(job_id)

    async def list_pending(self) -> list[JobState]:
        """List all PENDING jobs (for debugging/admin)."""
        async with self._lock:
            return [j for j in self._jobs.values() if j.status == JobStatus.PENDING]

    async def list_all(self, limit: int = 100) -> list[JobState]:
        """List recent jobs (newest first)."""
        async with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
            return jobs[:limit]

    async def get_stats(self) -> dict[str, int]:
        """Get job counts by status."""
        async with self._lock:
            counts = {s.value: 0 for s in JobStatus}
            for job in self._jobs.values():
                counts[job.status.value] += 1
            return counts

    def close(self):
        """Close LMDB environment."""
        if self._lmdb_env:
            self._lmdb_env.close()
            self._lmdb_env = None
