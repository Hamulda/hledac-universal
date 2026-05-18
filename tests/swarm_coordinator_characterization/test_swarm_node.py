"""
Characterization tests for SwarmNode behavior methods.
No behavior change — these tests document current behavior.
"""
import time
import pytest

from coordinators.swarm_coordinator import SwarmNode


class TestSwarmNodeUpdateReputation:
    """SwarmNode.update_reputation — no external deps, pure unit."""

    def test_update_reputation_success_increases(self):
        node = SwarmNode(node_id="n1", endpoint="ws://x", reputation=1.0)
        node.update_reputation(success=True, task_complexity=1.0)
        assert node.reputation == pytest.approx(1.1)
        assert node.tasks_completed == 1
        assert node.tasks_failed == 0

    def test_update_reputation_success_caps_at_5(self):
        node = SwarmNode(node_id="n1", endpoint="ws://x", reputation=5.0)
        node.update_reputation(success=True, task_complexity=1.0)
        assert node.reputation == 5.0
        assert node.tasks_completed == 1

    def test_update_reputation_failure_decreases(self):
        node = SwarmNode(node_id="n1", endpoint="ws://x", reputation=1.0)
        node.update_reputation(success=False, task_complexity=1.0)
        assert node.reputation == pytest.approx(0.8)
        assert node.tasks_failed == 1
        assert node.tasks_completed == 0

    def test_update_reputation_failure_floors_at_01(self):
        node = SwarmNode(node_id="n1", endpoint="ws://x", reputation=0.1)
        node.update_reputation(success=False, task_complexity=1.0)
        assert node.reputation == 0.1

    def test_update_reputation_complexity_scales_effect(self):
        node = SwarmNode(node_id="n1", endpoint="ws://x", reputation=1.0)
        node.update_reputation(success=True, task_complexity=2.0)  # 0.1 * 2 = 0.2
        assert node.reputation == pytest.approx(1.2)
        node.update_reputation(success=False, task_complexity=2.0)  # 0.2 * 2 = 0.4
        assert node.reputation == pytest.approx(0.8)


class TestSwarmNodeHeartbeat:
    """SwarmNode.heartbeat — pure unit, no deps."""

    def test_heartbeat_sets_online_true(self):
        node = SwarmNode(node_id="n1", endpoint="ws://x", is_online=False)
        node.heartbeat()
        assert node.is_online is True
        assert node.last_heartbeat == pytest.approx(time.time(), abs=1.0)


class TestSwarmNodeCheckHealth:
    """SwarmNode.check_health — no network, pure time comparison."""

    def test_check_health_within_timeout_returns_true(self):
        node = SwarmNode(node_id="n1", endpoint="ws://x", last_heartbeat=time.time())
        result = node.check_health(timeout=30.0)
        assert result is True
        assert node.is_online is True

    def test_check_health_past_timeout_returns_false(self):
        node = SwarmNode(node_id="n1", endpoint="ws://x", last_heartbeat=time.time() - 60.0)
        result = node.check_health(timeout=30.0)
        assert result is False
        assert node.is_online is False

    def test_check_health_edge_case_exactly_at_timeout(self):
        """At exact timeout boundary: (now - past) = 30.0, but check is < 30 → unhealthy."""
        past = time.time() - 30.0
        node = SwarmNode(node_id="n1", endpoint="ws://x", last_heartbeat=past)
        result = node.check_health(timeout=30.0)
        # Code uses (elapsed < timeout), so exactly at boundary is unhealthy
        assert result is False
        assert node.is_online is False

    def test_check_health_zero_timeout(self):
        """Zero timeout means node must have just heartbeaten."""
        node = SwarmNode(node_id="n1", endpoint="ws://x", last_heartbeat=time.time() - 0.1)
        result = node.check_health(timeout=0.0)
        assert result is False