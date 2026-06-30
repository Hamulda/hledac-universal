"""
Sprint 8TB probe tests — pivot stats increment.
Sprint: 8TB
Area: Agentic Pivot Loop
"""

import pytest

from hledac.universal.runtime.sprint_scheduler import SprintScheduler, SprintSchedulerConfig


class TestPivotStatsIncrement:
    """enqueue_pivot increments _pivot_stats counter."""

    def test_enqueue_increments_total(self):
        """3 enqueue calls → total == 3."""
        config = SprintSchedulerConfig()
        sched = SprintScheduler(config)

        sched.enqueue_pivot("CVE-2024-1", "cve", 0.8, 1.0)
        sched.enqueue_pivot("1.2.3.4", "ipv4", 0.7, 1.0)
        sched.enqueue_pivot("evil.com", "domain", 0.9, 1.0)

        assert sched._pivot_stats["total"] == 3
        assert sched._pivot_stats["processed"] == 0
        assert sched._pivot_stats["errors"] == 0
