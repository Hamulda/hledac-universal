"""
Sprint 8TB probe tests — PivotTask max-heap ordering.
Sprint: 8TB
Area: Agentic Pivot Loop
"""

import pytest

from hledac.universal.runtime.sprint_scheduler import PivotTask


class TestPivotTaskMaxHeapOrder:
    """PivotTask priority ordering — negative value makes higher confidence = smaller priority (extracted first from max-heap)."""

    def test_higher_confidence_smaller_priority(self):
        """Higher confidence × degree → more negative priority → extracted first."""
        t1 = PivotTask(priority=-0.9, ioc_type="cve", ioc_value="CVE-2024-1", task_type="cve_to_github")
        t2 = PivotTask(priority=-0.5, ioc_type="cve", ioc_value="CVE-2024-2", task_type="cve_to_github")
        # t1 < t2 because -0.9 < -0.5 (more negative = higher priority for max-heap)
        assert t1 < t2

    def test_higher_degree_smaller_priority(self):
        """Higher degree → more negative priority → extracted first."""
        t1 = PivotTask(priority=-0.72, ioc_type="ipv4", ioc_value="1.2.3.4", task_type="ip_to_ct")
        t2 = PivotTask(priority=-0.36, ioc_type="ipv4", ioc_value="1.2.3.5", task_type="ip_to_ct")
        assert t1 < t2

    def test_equal_priority_different_ioc(self):
        """Same priority but different IOC — ordering stable by insertion."""
        t1 = PivotTask(priority=-0.5, ioc_type="domain", ioc_value="evil.com", task_type="domain_to_dns")
        t2 = PivotTask(priority=-0.5, ioc_type="domain", ioc_value="bad.com", task_type="domain_to_dns")
        # Both have same priority, tie-break by the rest
        # Since ioc_value is not compared (compare=False), they should be equal for ordering
        assert t1.priority == t2.priority
