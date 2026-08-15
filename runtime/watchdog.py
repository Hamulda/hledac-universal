"""
StuckTaskDetector — asyncio task wall-clock timeout watchdog.
============================================================


Detects tasks that have been running longer than `timeout_s` without yielding.
Designed to catch C-extension I/O hangs (DNS resolver, TLS handshake, curl_cffi)
that appear healthy in system-wide CPU/memory metrics but are blocked in
non-cancellable syscalls.

Usage
-----
    detector = StuckTaskDetector(timeout_s=60.0)
    detector.track(some_task)
    stuck_ids = await detector.run()   # task ids running > timeout_s
    detector.forget(some_task)

Wired into SprintLifecycleManager: after cancel_all(), wait 5s, then call
detector.run() — any still-running task after cancellation attempt is a
confirmed C-extension hang.

Invariants
----------
- timeout_s >= 1.0 (minimum bound)
- track() is idempotent — re-tracking same task updates its start time
- forget() is safe on unknown task ids (no-op)
- run() is async-safe and non-blocking
- Always-on, bounded, fail-safe
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING
from _core import aclose

if TYPE_CHECKING:
    pass


__all__ = ['StuckTaskDetector']


class StuckTaskDetector:
    """
    Detects asyncio tasks running longer than `timeout_s` without yielding.

    Tracks wall-clock start time per task. run() returns task ids whose
    elapsed time exceeds the configured timeout.

    M1 8GB note: the internal dict is bounded by the number of active
    tasks in the sprint — never grows beyond the lane concurrency limit
    (typically 8–16 fire-and-forget tasks). Memory footprint is negligible.
    """

    __slots__ = ('_running', '_task_started', '_timeout_s')

    def __init__(self, timeout_s: float = 60.0) -> None:
        if timeout_s < 1.0:
            timeout_s = 1.0
        self._timeout_s: float = timeout_s
        # task id (int) -> monotonic start time (float)
        self._task_started: dict[int, float] = {}
        self._running: bool = False

    def track(self, task: asyncio.Task) -> None:
        """Record task start time (idempotent — updates if already tracked)."""
        self._task_started[id(task)] = time.monotonic()

    def forget(self, task: asyncio.Task) -> None:
        """Remove task from tracking (safe if not present)."""
        self._task_started.pop(id(task), None)

    async def run(self) -> list[int]:
        """
        Return stuck task ids (elapsed > timeout_s).

        Runs in O(n) over tracked tasks. Non-blocking — just a timedelta
        comparison and dict iteration.
        """
        now = time.monotonic()
        timeout = self._timeout_s
        return [tid for tid, started_at in list(self._task_started.items())
                if now - started_at > timeout]

    async def get_stuck_with_tasks(self) -> list[tuple[int, float]]:
        """
        Return (task_id, elapsed_seconds) for all stuck tasks.
        Used by callers that need the actual elapsed time, not just the id.
        """
        now = time.monotonic()
        timeout = self._timeout_s
        return [(tid, now - started_at)
                for tid, started_at in list(self._task_started.items())
                if now - started_at > timeout]

    @property
    def timeout_s(self) -> float:
        """Configured timeout in seconds."""
        return self._timeout_s

    @property
    def tracked_count(self) -> int:
        """Number of currently tracked tasks."""
        return len(self._task_started)

    def clear(self) -> None:
        """Clear all tracked task ids. Use on sprint reset."""
        self._task_started.clear()
