"""
P17: Q-Learning Unit Tests
==========================

Tests for QTable update logic and action selection.

Anti-patterns covered:
- QTable persistence via LMDB (not JSON)
- E-greedy action selection with deterministic tie-break
"""


import pytest
from hledac.universal.loops.research_loop import QTable, ResearchLoop, ResearchResult, ResearchState


class TestQTableUpdate:
    """Test Q-learning update rule."""

    def test_q_update_with_reward(self):
        """
        Test that Q-learning update changes Q-value correctly.

        Q(s,a) = Q(s,a) + alpha * (reward + gamma * max(Q(s',a')) - Q(s,a))

        With alpha=0.1, gamma=0.9, reward=0.5, max(Q(s',:))=0
        Expected: Q_new = 0 + 0.1 * (0.5 + 0.9 * 0 - 0) = 0.05
        """
        qtable = QTable(alpha=0.1, gamma=0.9)
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
        qtable = QTable(alpha=0.1, gamma=0.9)
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
        qtable = QTable(alpha=0.1, gamma=0.9)

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


class TestQTableDeterministicTieBreak:
    """Test that action selection is deterministic on ties."""

    def test_greedy_tie_breaks_alphabetically(self):
        """
        Test that when Q-values are equal, actions are selected alphabetically.

        Actions: ["fetch", "discovery", "evaluate"]
        All have Q=0, so "discovery" should be first alphabetically.
        """
        qtable = QTable(alpha=0.1, gamma=0.9)
        state = ("test", 0, 0, 0, False)
        actions = ["fetch", "discovery", "evaluate"]

        # All Q-values are 0 (default), so alphabetically "discovery" < "evaluate" < "fetch"
        # We need to test that the tie-break is deterministic
        results = []
        for _ in range(10):
            action = qtable.get_best_action(state, actions)
            results.append(action)

        # All results should be the same (deterministic)
        assert len(set(results)) == 1, f"Expected deterministic tie-break, got {results}"
        # And it should be the alphabetically first
        assert results[0] == "discovery", f"Expected 'discovery' (alphabetically first), got {results[0]}"

    def test_greedy_prefers_higher_q(self):
        """Test that higher Q-value is always preferred."""
        qtable = QTable(alpha=0.1, gamma=0.9)
        state = ("test", 0, 0, 0, False)
        actions = ["fetch", "discovery", "evaluate"]

        # Give "discovery" a higher Q-value
        qtable.update(state, "discovery", 1.0, ("test", 1, 0, 0, False))
        qtable.update(state, "fetch", 0.5, ("test", 1, 0, 0, False))

        action = qtable.get_best_action(state, actions)
        assert action == "discovery", f"Expected 'discovery' (highest Q), got {action}"


class TestResearchState:
    """Test ResearchState and state conversion."""

    def test_state_to_tuple_discretization(self):
        """Test that continuous values are discretized properly."""
        loop = ResearchLoop(
            hypothesis_engine=None,
            graph=None,
        )

        state = ResearchState(
            query="test query",
            cycle=5,
            findings_count=25,
            memory_budget_mb=150.0,
            tot_used=True,
        )

        state_tuple = loop._state_to_tuple(state)

        # cycle 5 -> bucket 2 (5//2)
        # findings 25 -> bucket 2 (25//10)
        # memory 150 -> bucket 3 (150//50)
        # tot_used = True
        assert state_tuple == ("test query", 2, 2, 3, True)


class TestResearchResult:
    """Test ResearchResult dataclass."""

    def test_research_result_creation(self):
        """Test that ResearchResult can be created with proper fields."""
        result = ResearchResult(
            findings=[{"type": "test", "content": "test finding"}],
            reward=0.75,
            state={"cycle": 0, "findings_count": 1},
            action="hypothesis_generation",
        )

        assert len(result.findings) == 1
        assert result.reward == 0.75
        assert result.action == "hypothesis_generation"
        assert result.state["cycle"] == 0


class TestQTableSerialization:
    """Test QTable serialization for LMDB persistence."""

    def test_to_dict(self):
        """Test QTable to_dict serialization."""
        qtable = QTable(alpha=0.1, gamma=0.9)
        state = ("test", 0, 0, 0, False)
        qtable.update(state, "discovery", 0.5, ("test", 1, 0, 0, False))

        data = qtable.to_dict()

        assert data["_alpha"] == 0.1
        assert data["_gamma"] == 0.9
        assert "_table" in data
        assert len(data["_table"]) > 0

    def test_from_dict(self):
        """Test QTable from_dict deserialization."""
        qtable = QTable(alpha=0.1, gamma=0.9)
        state = ("test", 0, 0, 0, False)
        qtable.update(state, "discovery", 0.5, ("test", 1, 0, 0, False))

        # Serialize and deserialize
        data = qtable.to_dict()
        restored = QTable.from_dict(data)

        # Check alpha/gamma restored
        assert restored._alpha == 0.1
        assert restored._gamma == 0.9

        # Check Q-value restored
        q = restored.get_q(state, "discovery")
        assert abs(q - 0.05) < 0.001


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
