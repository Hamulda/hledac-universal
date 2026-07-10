"""
P17: FederatedQTable Q-Learning Unit Tests

Tests for FederatedQTable (M1-safe replacement for loops.research_loop.QTable).
Loops QTable had M1 crash vectors:
- asyncio.get_event_loop().run_until_complete() inside existing event loop
- Orphaned event loops without close()

FederatedQTable is bounded (MAX_QTABLE_ENTRIES=1024), fail-soft,
no eval, no event loop blocking.
"""

import pytest
from hledac.universal.federated.qtable import FederatedQTable, MAX_QTABLE_ENTRIES


class TestFederatedQTableUpdate:
    """Test Q-learning update rule."""

    def test_q_update_with_reward(self):
        """
        Test that Q-learning update changes Q-value correctly.

        Q(s,a) = Q(s,a) + alpha * (reward + gamma * max(Q(s',a')) - Q(s,a))

        With alpha=0.1, gamma=0.9, reward=0.5, max(Q(s',:))=0
        Expected: Q_new = 0 + 0.1 * (0.5 + 0.9 * 0 - 0) = 0.05
        """
        qtable = FederatedQTable(alpha=0.1, gamma=0.9)
        state = ("test", 0, 0, 0, False)
        action = "hypothesis_generation"
        reward = 0.5
        next_state = ("test", 1, 1, 0, False)

        initial_q = qtable.get_q(state, action)
        assert initial_q == 0.0, "Initial Q-value should be 0"

        qtable.update(state, action, reward, next_state)

        # Q(s,a) = 0 + 0.1 * (0.5 + 0.9 * 0 - 0) = 0.05
        expected_q = 0.1 * (0.5 + 0.9 * 0 - 0)
        actual_q = qtable.get_q(state, action)
        assert abs(actual_q - expected_q) < 0.001, f"Expected {expected_q}, got {actual_q}"

    def test_q_update_accumulates(self):
        """Test that repeated updates accumulate Q-values."""
        qtable = FederatedQTable(alpha=0.1, gamma=0.9)
        state = ("test", 0, 0, 0, False)
        action = "discovery"
        reward = 0.5
        next_state = ("test", 1, 0, 0, False)

        # First update
        qtable.update(state, action, reward, next_state)
        q1 = qtable.get_q(state, action)

        # Second update with same reward
        qtable.update(state, action, reward, next_state)
        q2 = qtable.get_q(state, action)

        assert q2 > q1, "Q-value should accumulate with repeated updates"

    def test_q_update_with_next_state_values(self):
        """
        Test that max(Q(s',a')) influences update.

        When next state has known Q-values, they should affect the update.
        """
        qtable = FederatedQTable(alpha=0.1, gamma=0.9)

        # State A -> action -> State B
        state_a = ("test", 0, 0, 0, False)
        state_b = ("test", 1, 1, 0, False)
        action = "fetch"

        # Pre-populate Q(s', a') for state_b
        qtable.update(state_b, "hypothesis_generation", 0.8, ("test", 2, 2, 0, False))

        reward = 0.5
        qtable.update(state_a, action, reward, state_b)

        # Q(s,a) should be higher because of positive max(Q(s',a'))
        final_q = qtable.get_q(state_a, action)
        assert final_q > 0, "Q-value should be positive due to next state value"


class TestFederatedQTableActionSelection:
    """Test that action selection is deterministic on ties."""

    def test_greedy_prefers_higher_q(self):
        """Test that higher Q-value is always preferred."""
        qtable = FederatedQTable(alpha=0.1, gamma=0.9)
        state = ("test", 0, 0, 0, False)
        actions = ["fetch", "discovery", "evaluate"]

        # Give "discovery" a higher Q-value
        qtable.update(state, "discovery", 1.0, ("test", 1, 0, 0, False))
        qtable.update(state, "fetch", 0.5, ("test", 1, 0, 0, False))

        action = qtable.get_best_action(state, actions)
        assert action == "discovery", f"Expected 'discovery' (highest Q), got {action}"

    def test_get_best_action_returns_first_on_empty(self):
        """Empty actions list returns empty string, not raises."""
        qtable = FederatedQTable(alpha=0.1, gamma=0.9)
        state = ("test", 0)
        action = qtable.get_best_action(state, [])
        assert action == ""

    def test_get_best_action_returns_first_on_tie(self):
        """On equal Q-values, returns first action in list."""
        qtable = FederatedQTable(alpha=0.1, gamma=0.9)
        state = ("test", 0, 0, 0, False)
        actions = ["fetch", "discovery", "evaluate"]

        # No Q-values set — all default to 0, returns first
        action = qtable.get_best_action(state, actions)
        assert action == "fetch", f"Expected 'fetch' (first action), got {action}"


class TestFederatedQTableBounds:
    """Test bounded table size (MAX_QTABLE_ENTRIES)."""

    def test_max_entries_eviction(self):
        """When over MAX_QTABLE_ENTRIES, lowest Q entries are evicted."""
        qtable = FederatedQTable(alpha=0.1, gamma=0.9, max_entries=10)
        # Add many entries
        for i in range(20):
            state = (f"state_{i}", i, 0, 0, False)
            qtable.update(state, "action", float(i), ("next", i + 1, 0, 0, False))

        # Should be capped at max_entries
        assert len(qtable) <= 10, f"Expected <= 10, got {len(qtable)}"

    def test_len_returns_entry_count(self):
        """len() returns the number of entries."""
        qtable = FederatedQTable(alpha=0.1, gamma=0.9)
        assert len(qtable) == 0
        qtable.update(("s1",), "a1", 0.5, ("s2",))
        assert len(qtable) == 1
        qtable.update(("s1",), "a2", 0.3, ("s2",))  # same state, different action
        assert len(qtable) == 2


class TestFederatedQTableSerialization:
    """Test FederatedQTable serialization for LMDB persistence."""

    def test_to_dict(self):
        """Test FederatedQTable to_dict serialization."""
        qtable = FederatedQTable(alpha=0.1, gamma=0.9)
        qtable.update(("test", 0, 0, 0, False), "discovery", 0.5, ("test", 1, 0, 0, False))

        data = qtable.to_dict()

        assert len(data) > 0
        # Keys are "state|action" format
        assert any("|" in k for k in data.keys())

    def test_from_dict_roundtrip(self):
        """Test FederatedQTable from_dict deserialization."""
        qtable = FederatedQTable(alpha=0.1, gamma=0.9)
        qtable.update(("test", 0, 0, 0, False), "discovery", 0.5, ("test", 1, 0, 0, False))

        # Serialize and deserialize
        data = qtable.to_dict()
        restored = FederatedQTable.from_dict(data)

        # Check it restored some entries
        assert len(restored) > 0

    def test_from_dict_empty_data(self):
        """from_dict with empty/None data returns fresh qtable."""
        qt = FederatedQTable.from_dict({})
        assert len(qt) == 0
        assert qt._alpha == 0.1
        assert qt._gamma == 0.9

        qt2 = FederatedQTable.from_dict(None, alpha=0.2, gamma=0.8)
        assert qt2._alpha == 0.2
        assert qt2._gamma == 0.8


class TestFederatedQTableFailSoft:
    """Test that FederatedQTable never raises."""

    def test_get_q_on_invalid_state(self):
        """get_q returns 0.0 on invalid input, never raises."""
        qtable = FederatedQTable(alpha=0.1, gamma=0.9)
        # None as state
        result = qtable.get_q(None, "action")
        assert result == 0.0
        # Invalid tuple
        result = qtable.get_q(123, "action")  # type: ignore
        assert result == 0.0

    def test_update_on_invalid_state(self):
        """update silently handles invalid input."""
        qtable = FederatedQTable(alpha=0.1, gamma=0.9)
        # Should not raise
        qtable.update(None, "action", 0.5, None)  # type: ignore
        qtable.update(123, "action", 0.5, 456)  # type: ignore

    def test_get_best_action_on_empty_actions(self):
        """Empty actions returns empty string."""
        qtable = FederatedQTable(alpha=0.1, gamma=0.9)
        result = qtable.get_best_action(("test",), [])
        assert result == ""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
