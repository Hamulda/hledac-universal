"""
Characterization tests for SwarmTask priority ordering.
No behavior change — these tests document current behavior.
"""
import pytest  # noqa: F401

from coordinators.swarm_coordinator import SwarmTask


class TestSwarmTaskPriority:
    """SwarmTask.__lt__ enables priority queue ordering."""

    def test_lower_priority_number_is_higher_priority(self):
        task_low = SwarmTask(task_id="t1", task_type="x", payload={}, priority=1)
        task_high = SwarmTask(task_id="t2", task_type="x", payload={}, priority=5)
        assert task_low < task_high  # priority 1 comes before priority 5

    def test_equal_priority_are_equal(self):
        task_a = SwarmTask(task_id="t1", task_type="x", payload={}, priority=3)
        task_b = SwarmTask(task_id="t2", task_type="x", payload={}, priority=3)
        # __lt__ is antisymmetric: a < b and b < a cannot both be true
        assert not (task_a < task_b)
        assert not (task_b < task_a)