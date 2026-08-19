"""
Micro Sprint Service — UNIFIED-004 Entropy Feedback Integration
=============================================================

Provides micro-sprint scheduling for rapid fetch operations.

Features:
- Micro sprint queue with entropy-based prioritization
- Sprint execution with timeboxing
- Integration with entropy feedback for adaptive scheduling
- Lightweight async task management

M1 8GB: Uses __slots__ for memory efficiency.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable, TypeVar

from hledac.universal.compat.msgspec_gc_compat import Struct

logger = logging.getLogger(__name__)

T = TypeVar('T')


# =============================================================================
# Configuration
# =============================================================================

class MicroSprintConfig(Struct, frozen=True):
    """Micro sprint configuration. M1 8GB: msgspec.Struct for fast init."""
    sprint_duration_s: float = 5.0
    max_tasks_per_sprint: int = 50
    cooldown_s: float = 1.0
    enable_entropy_feedback: bool = True
    entropy_threshold: float = 7.5
    adaptive_duration: bool = True


# =============================================================================
# Sprint Task
# =============================================================================

@dataclass(slots=True, order=True)
class SprintTask:
    """Task scheduled for micro-sprint execution."""
    priority: int = field(compare=True)
    task_id: str = field(compare=False)
    url: str = field(compare=False)
    callback: Callable[..., Awaitable[T]] = field(compare=False, repr=False)
    args: tuple[Any, ...] = field(compare=False, default_factory=tuple)
    kwargs: dict[str, Any] = field(compare=False, default_factory=dict)
    created_at: float = field(compare=False, default_factory=time.monotonic)
    entropy_score: float = field(compare=False, default=0.0)
    retry_count: int = field(compare=False, default=0)

    def __post_init__(self) -> None:
        if not isinstance(self.priority, int):
            self.priority = 0


@dataclass(slots=True)
class SprintResult:
    """Result of sprint task execution."""
    task_id: str
    success: bool
    result: Any = None
    error: str | None = None
    duration_s: float = 0.0
    entropy_score: float = 0.0


# =============================================================================
# Micro Sprint Service
# =============================================================================

@dataclass(slots=True)
class MicroSprintService:
    """
    Micro sprint service for rapid fetch operations.

    Implements UNIFIED-004 micro sprint protocol:
    - Time-boxed sprint execution (default 5s)
    - Priority-based task scheduling
    - Entropy feedback integration for adaptive sprinting
    - Lightweight task management

    M1 8GB: Uses __slots__ for memory efficiency.

    ISSUE-OPT-1: Uses asyncio.Event for _running flag instead of bool.
    STRESS-25 pattern: asyncio.Event provides immediate cancellation response
    (no polling delay when stop() is called).
    """
    config: MicroSprintConfig = field(default_factory=MicroSprintConfig)

    _task_queue: asyncio.PriorityQueue[SprintTask] = field(
        default_factory=lambda: asyncio.PriorityQueue()
    )
    _results: dict[str, SprintResult] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _running: asyncio.Event = field(default=None, init=False)  # ISSUE-OPT-1: Event-driven
    _sprint_task: asyncio.Task[None] | None = field(default=None, init=False)
    _entropy_feedback: Any = field(default=None, init=False)
    _stats: dict[str, Any] = field(default_factory=lambda: {
        'tasks_scheduled': 0,
        'tasks_completed': 0,
        'tasks_failed': 0,
        'sprints_executed': 0,
        'total_duration_s': 0.0,
    })

    def __post_init__(self) -> None:
        # ISSUE-OPT-1: Initialize asyncio.Event lazily
        if self._running is None:
            object.__setattr__(self, '_running', asyncio.Event())
            self._running.set()  # Start in running state

    def set_entropy_feedback(self, entropy_service: Any) -> None:
        """Set entropy feedback service for adaptive scheduling."""
        self._entropy_feedback = entropy_service

    async def schedule_task(
        self,
        task_id: str,
        url: str,
        callback: Callable[..., Awaitable[T]],
        *args: Any,
        priority: int = 0,
        entropy_score: float = 0.0,
        **kwargs: Any
    ) -> None:
        """
        Schedule a task for sprint execution.

        Args:
            task_id: Unique task identifier
            url: URL associated with task
            callback: Async function to execute
            *args: Positional arguments for callback
            priority: Task priority (higher = more important)
            entropy_score: Entropy score for adaptive scheduling
            **kwargs: Keyword arguments for callback
        """
        # Adjust priority based on entropy
        adjusted_priority = priority
        if self.config.enable_entropy_feedback and entropy_score > self.config.entropy_threshold:
            # High entropy = boost priority
            adjusted_priority = priority + 10

        task = SprintTask(
            priority=-adjusted_priority,  # Negative for max-heap
            task_id=task_id,
            url=url,
            callback=callback,
            args=args,
            kwargs=kwargs,
            entropy_score=entropy_score,
        )

        await self._task_queue.put(task)

        async with self._lock:
            self._stats['tasks_scheduled'] += 1

    async def execute_sprint(self) -> list[SprintResult]:
        """
        Execute a micro sprint.

        Runs tasks until duration expires or queue is empty.
        Returns list of sprint results.
        """
        sprint_start = time.monotonic()
        sprint_duration = self.config.sprint_duration_s
        results: list[SprintResult] = []

        # Check for adaptive duration
        if self.config.adaptive_duration and self._entropy_feedback:
            try:
                # Get recent entropy average
                stats = self._entropy_feedback.get_stats()
                avg_entropy = stats.get('avg_entropy', 0.0)
                if avg_entropy > self.config.entropy_threshold:
                    # High entropy = shorter sprint
                    sprint_duration = self.config.sprint_duration_s * 0.5
            except Exception:  # noqa: BLE001
                pass

        tasks_executed = 0

        while time.monotonic() - sprint_start < sprint_duration:
            if tasks_executed >= self.config.max_tasks_per_sprint:
                break

            try:
                task = self._task_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            result = await self._execute_task(task)
            results.append(result)

            async with self._lock:
                self._stats['tasks_completed'] += 1 if result.success else 0
                self._stats['tasks_failed'] += 1 if not result.success else 0

            tasks_executed += 1

        async with self._lock:
            self._stats['sprints_executed'] += 1
            self._stats['total_duration_s'] += time.monotonic() - sprint_start

        return results

    async def _execute_task(self, task: SprintTask) -> SprintResult:
        """Execute a single sprint task."""
        start_time = time.monotonic()
        result = SprintResult(
            task_id=task.task_id,
            success=False,
            entropy_score=task.entropy_score,
        )

        try:
            task_result = await asyncio.wait_for(
                task.callback(*task.args, **task.kwargs),
                timeout=30.0
            )
            result.result = task_result
            result.success = True
        except asyncio.TimeoutError:
            result.error = "timeout"
        except Exception as e:  # noqa: BLE001
            result.error = str(e)
        finally:
            result.duration_s = time.monotonic() - start_time

        self._results[task.task_id] = result
        return result

    async def start_sprint_loop(self) -> None:
        """Start continuous sprint execution loop."""
        if self._running.is_set():
            return

        self._running.set()  # ISSUE-OPT-1: Set running state
        self._sprint_task = asyncio.create_task(self._sprint_loop())
        logger.info("Micro sprint loop started")

    async def stop_sprint_loop(self) -> None:
        """Stop sprint execution loop. ISSUE-OPT-1: Uses asyncio.Event for immediate response."""
        self._running.clear()  # ISSUE-OPT-1: Immediately wakes up wait()
        if self._sprint_task:
            self._sprint_task.cancel()
            try:
                await self._sprint_task
            except asyncio.CancelledError:
                pass
        logger.info("Micro sprint loop stopped")

    async def _sprint_loop(self) -> None:
        """Main sprint loop. ISSUE-OPT-1: Uses asyncio.Event for event-driven cancellation."""
        while not self._running.is_set():
            try:
                await self.execute_sprint()

                # Cooldown between sprints - use Event.wait() for immediate cancellation
                try:
                    await asyncio.wait_for(
                        self._running.wait(),
                        timeout=self.config.cooldown_s
                    )
                    break  # Event was set, exit loop
                except asyncio.TimeoutError:
                    pass  # Timeout reached, continue

            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001
                logger.error(f"Sprint loop error: {e}")
                try:
                    await asyncio.wait_for(
                        self._running.wait(),
                        timeout=self.config.cooldown_s
                    )
                    break
                except asyncio.TimeoutError:
                    pass

    def get_queue_size(self) -> int:
        """Get pending task count."""
        return self._task_queue.qsize()

    def get_result(self, task_id: str) -> SprintResult | None:
        """Get result for completed task."""
        return self._results.get(task_id)

    def get_stats(self) -> dict[str, Any]:
        """Get sprint statistics."""
        avg_duration = (
            self._stats['total_duration_s'] / self._stats['sprints_executed']
            if self._stats['sprints_executed'] > 0 else 0.0
        )

        return {
            **self._stats,
            'queue_size': self._task_queue.qsize(),
            'results_cached': len(self._results),
            'avg_sprint_duration_s': avg_duration,
            'running': self._running,
        }

    def cleanup_results(self, max_age_s: float = 300.0) -> int:
        """Clean up old results."""
        now = time.monotonic()
        to_remove = [
            task_id for task_id, result in self._results.items()
            if now - (result.duration_s or 0) > max_age_s
        ]
        for task_id in to_remove:
            del self._results[task_id]
        return len(to_remove)

    async def aclose(self) -> None:
        """Close micro sprint service and release resources."""
        await self.stop_sprint_loop()
        async with self._lock:
            self._results.clear()
            # Drain task queue
            while not self._task_queue.empty():
                try:
                    self._task_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
        self._entropy_feedback = None
        logger.debug("MicroSprintService closed")


__all__ = [
    'MicroSprintConfig',
    'SprintTask',
    'SprintResult',
    'MicroSprintService',
]
