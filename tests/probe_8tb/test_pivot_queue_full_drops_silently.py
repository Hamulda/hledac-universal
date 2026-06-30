"""
Sprint 8TB probe tests — pivot queue full drops silently.
Sprint: 8TB
Area: Agentic Pivot Loop
"""

import asyncio

import pytest

from hledac.universal.runtime.sprint_scheduler import PivotTask, SprintScheduler
from hledac.universal.runtime.sprint_scheduler import SprintSchedulerConfig


class TestPivotQueueFullDropsSilently:
    """When pivot queue is full, enqueue_pivot drops silently (M1 8GB constraint)."""

    def test_queue_full_drops_silently(self):
        """Enqueue when full does not raise — silently drops."""
        config = SprintSchedulerConfig()
        sched = SprintScheduler(config)

        # Fill the queue
        for i in range(200):
            sched.enqueue_pivot(f"value{i}", "cve", 0.8, 1.0)

        # Queue should be full now
        assert sched._pivot_queue.full()

        # This should NOT raise — silently dropped
        sched.enqueue_pivot("overflow_value", "cve", 0.9, 1.0)

        # Stats should still show only 200 (not 201)
        assert sched._pivot_stats["total"] == 200
