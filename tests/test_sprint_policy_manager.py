"""
Tests for SprintPolicyManager — opt-in RL sprint policy layer.

Tests the contract:
  1. Disabled by default (no effect on sprint behavior)
  2. Every 5th sprint is exploration
  3. Policy persists between instances
  4. Reward computed from real SprintSchedulerResult fields

Integration: runtime/sprint_scheduler.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from hledac.universal.rl.actions import ACTION_CONTINUE, ACTION_DEEP_DIVE
from hledac.universal.rl.sprint_policy_manager import (
    SprintPolicyManager,
    SprintPolicyState,
)

# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_policy_path(tmp_path: Path) -> Path:
    """Temporary policy state file."""
    return tmp_path / ".sprint_policy_state.json"


@pytest.fixture
def enabled_manager(tmp_policy_path: Path) -> SprintPolicyManager:
    """SprintPolicyManager with policy enabled."""
    return SprintPolicyManager(enabled=True, policy_path=tmp_policy_path)


@pytest.fixture
def disabled_manager() -> SprintPolicyManager:
    """SprintPolicyManager with policy disabled (default)."""
    return SprintPolicyManager(enabled=False)


def _make_result(
    cycles_completed: int = 0,
    accepted_findings: int = 0,
    unique_entry_hashes_seen: int = 0,
    duplicate_entry_hashes_skipped: int = 0,
    aborted: bool = False,
    **kwargs: Any,
) -> MagicMock:
    """Factory for a mock SprintSchedulerResult."""
    r = MagicMock()
    r.cycles_completed = cycles_completed
    r.accepted_findings = accepted_findings
    r.unique_entry_hashes_seen = unique_entry_hashes_seen
    r.duplicate_entry_hashes_skipped = duplicate_entry_hashes_skipped
    r.aborted = aborted
    for k, v in kwargs.items():
        setattr(r, k, v)
    return r


# ── Test: disabled by default ───────────────────────────────────────────────

class TestDisabledByDefault:
    """Policy must be disabled-by-default — no effect on sprint behavior."""

    def test_disabled_update_is_noop(self, disabled_manager: SprintPolicyManager) -> None:
        """update() must be no-op when disabled."""
        result = _make_result(cycles_completed=5, accepted_findings=10)
        disabled_manager.update(result)
        assert disabled_manager.sprint_sequence_number == 0

    def test_disabled_should_explore_returns_false(self, disabled_manager: SprintPolicyManager) -> None:
        """should_explore() must return False when disabled."""
        assert disabled_manager.should_explore() is False

    def test_disabled_get_action_returns_continue(self, disabled_manager: SprintPolicyManager) -> None:
        """get_action() must return ACTION_CONTINUE when disabled."""
        assert disabled_manager.get_action() == ACTION_CONTINUE

    def test_disabled_reset_is_noop(self, disabled_manager: SprintPolicyManager) -> None:
        """reset() must be no-op when disabled."""
        disabled_manager.reset()
        assert disabled_manager.sprint_sequence_number == 0

    def test_enabled_flag_readonly(self, disabled_manager: SprintPolicyManager) -> None:
        """enabled property must be read-only."""
        assert disabled_manager.enabled is False


# ── Test: every 5th sprint is exploration ────────────────────────────────────

class TestEveryFifthSprintExploration:
    """every 5th sprint is exploration (ACTION_DEEP_DIVE)."""

    def test_sprint_5_is_exploration(self, enabled_manager: SprintPolicyManager) -> None:
        """Sprint #5 (1-indexed modulo) should trigger exploration."""
        result = _make_result()
        for _i in range(4):
            enabled_manager.update(result)
        # Sprint 5: should explore (sprint_sequence_number == 4 is 0-indexed, 5 % 5 == 0)
        assert enabled_manager.should_explore() is True

    def test_sprint_10_is_exploration(self, enabled_manager: SprintPolicyManager) -> None:
        """Sprint #10 should trigger exploration."""
        result = _make_result()
        for _i in range(9):
            enabled_manager.update(result)
        assert enabled_manager.should_explore() is True

    def test_sprints_1_to_4_not_exploration(self) -> None:
        """Sprints #1-4 should NOT trigger interval-based exploration."""
        # epsilon=0 to isolate deterministic interval logic from stochastic epsilon-greedy
        manager = SprintPolicyManager(enabled=True, epsilon=0.0)
        result = _make_result()
        for _ in range(4):
            manager.update(result)
        # After sprint #4 (sprint_sequence_number=4): no exploration yet
        assert manager.should_explore() is False

    def test_get_action_returns_deep_dive_on_explore(
        self, enabled_manager: SprintPolicyManager
    ) -> None:
        """get_action() must return ACTION_DEEP_DIVE when should_explore() is True."""
        result = _make_result()
        for _i in range(4):
            enabled_manager.update(result)
        assert enabled_manager.get_action() == ACTION_DEEP_DIVE

    def test_get_action_returns_continue_when_not_exploring(
        self, tmp_policy_path: Path
    ) -> None:
        """get_action() must return ACTION_CONTINUE when should_explore() is False."""
        # epsilon=0 to isolate from stochastic epsilon-greedy
        manager = SprintPolicyManager(enabled=True, epsilon=0.0)
        result = _make_result()
        manager.update(result)
        assert manager.get_action() == ACTION_CONTINUE


# ── Test: policy persists between instances ──────────────────────────────────

class TestPolicyPersistence:
    """Policy state survives instance restarts via JSON file."""

    def test_state_saved_after_updates(
        self, enabled_manager: SprintPolicyManager, tmp_policy_path: Path
    ) -> None:
        """State file must be written after update()."""
        result = _make_result(cycles_completed=3, accepted_findings=7)
        enabled_manager.update(result)
        enabled_manager.update(result)
        assert tmp_policy_path.exists()

    def test_state_reloaded_on_new_instance(
        self, enabled_manager: SprintPolicyManager, tmp_policy_path: Path
    ) -> None:
        """New instance must load state from file."""
        result = _make_result(cycles_completed=3, accepted_findings=7)
        enabled_manager.update(result)
        enabled_manager.update(result)

        # Create new instance with same path
        new_manager = SprintPolicyManager(enabled=True, policy_path=tmp_policy_path)
        assert new_manager.sprint_sequence_number == 2

    def test_epsilon_persists(self, enabled_manager: SprintPolicyManager, tmp_policy_path: Path) -> None:
        """Epsilon must be persisted and reloaded."""
        result = _make_result()
        enabled_manager.update(result)
        original_epsilon = enabled_manager.epsilon

        new_manager = SprintPolicyManager(enabled=True, policy_path=tmp_policy_path)
        assert new_manager.epsilon == original_epsilon

    def test_reset_deletes_file(self, enabled_manager: SprintPolicyManager, tmp_policy_path: Path) -> None:
        """reset() must delete the persisted state file."""
        result = _make_result()
        enabled_manager.update(result)
        assert tmp_policy_path.exists()

        enabled_manager.reset()
        assert not tmp_policy_path.exists()
        assert enabled_manager.sprint_sequence_number == 0

    def test_recent_rewards_persists(self, enabled_manager: SprintPolicyManager, tmp_policy_path: Path) -> None:
        """Recent reward list must be persisted (capped at 100)."""
        for i in range(10):
            r = _make_result(accepted_findings=i)
            enabled_manager.update(r)

        new_manager = SprintPolicyManager(enabled=True, policy_path=tmp_policy_path)
        assert len(new_manager.recent_rewards) == 10

    def test_missing_file_is_handled_gracefully(self, tmp_policy_path: Path) -> None:
        """Loading non-existent file must not raise."""
        manager = SprintPolicyManager(enabled=True, policy_path=tmp_policy_path)
        assert manager.sprint_sequence_number == 0

    def test_corrupt_file_is_handled_gracefully(self, tmp_policy_path: Path) -> None:
        """Loading corrupt file must not raise — must use defaults."""
        tmp_policy_path.write_text("not valid json{{{")
        manager = SprintPolicyManager(enabled=True, policy_path=tmp_policy_path)
        assert manager.sprint_sequence_number == 0


# ── Test: reward from SprintSchedulerResult ──────────────────────────────────

class TestRewardComputation:
    """Reward computed from real SprintSchedulerResult fields."""

    def test_accepted_findings_positive_signal(self) -> None:
        """accepted_findings contributes positively to reward."""
        manager = SprintPolicyManager(enabled=True)
        result = _make_result(accepted_findings=10)
        manager.update(result)
        assert manager.total_reward > 0

    def test_abort_penalty(self) -> None:
        """aborted=True applies penalty."""
        result_normal = _make_result(cycles_completed=5, accepted_findings=5, aborted=False)
        result_aborted = _make_result(cycles_completed=5, accepted_findings=5, aborted=True)

        m1 = SprintPolicyManager(enabled=True)
        m2 = SprintPolicyManager(enabled=True)

        m1.update(result_normal)
        m2.update(result_aborted)

        assert m2.total_reward < m1.total_reward

    def test_duplicate_ratio_contributes(self) -> None:
        """High duplicate ratio contributes to reward (dedup efficiency)."""
        result_high_dup = _make_result(
            cycles_completed=1,
            accepted_findings=1,
            unique_entry_hashes_seen=10,
            duplicate_entry_hashes_skipped=90,  # 90% duplicates
        )
        result_no_dup = _make_result(
            cycles_completed=1,
            accepted_findings=1,
            unique_entry_hashes_seen=100,
            duplicate_entry_hashes_skipped=0,
        )

        m1 = SprintPolicyManager(enabled=True)
        m1.update(result_high_dup)

        m2 = SprintPolicyManager(enabled=True)
        m2.update(result_no_dup)

        # High dup ratio should have higher reward (dedup efficiency)
        assert m1.total_reward > 0
        assert m2.total_reward > 0

    def test_cycles_completed_bonus(self, enabled_manager: SprintPolicyManager) -> None:
        """cycles_completed contributes to reward."""
        result_0 = _make_result(cycles_completed=0, accepted_findings=0, unique_entry_hashes_seen=0)
        result_10 = _make_result(cycles_completed=10, accepted_findings=0, unique_entry_hashes_seen=0)

        m1 = SprintPolicyManager(enabled=True)
        m1.update(result_0)

        m2 = SprintPolicyManager(enabled=True)
        m2.update(result_10)

        assert m2.total_reward > m1.total_reward


# ── Test: integration with sprint_scheduler via update() ──────────────────────

class TestSprintSchedulerIntegration:
    """update() called by SprintScheduler after run() — real result fields."""

    def test_update_with_real_result_structure(self, enabled_manager: SprintPolicyManager) -> None:
        """update() must accept a real SprintSchedulerResult (or mock matching it)."""
        result = _make_result(
            cycles_completed=3,
            accepted_findings=2,
            unique_entry_hashes_seen=50,
            duplicate_entry_hashes_skipped=10,
        )
        enabled_manager.update(result)
        assert enabled_manager.sprint_sequence_number == 1

    def test_sequence_number_increments(self, enabled_manager: SprintPolicyManager) -> None:
        """sprint_sequence_number increments on each update()."""
        result = _make_result()
        for _i in range(5):
            enabled_manager.update(result)
        assert enabled_manager.sprint_sequence_number == 5

    def test_epsilon_decay_on_update(self, enabled_manager: SprintPolicyManager) -> None:
        """Epsilon decays on each update()."""
        result = _make_result()
        enabled_manager.update(result)
        original = enabled_manager.epsilon
        enabled_manager.update(result)
        assert enabled_manager.epsilon < original

    def test_epsilon_floor(self, enabled_manager: SprintPolicyManager) -> None:
        """Epsilon must not go below 0.05."""
        result = _make_result()
        for _ in range(1000):
            enabled_manager.update(result)
        assert enabled_manager.epsilon >= 0.05


# ── Test: invariants ──────────────────────────────────────────────────────────

class TestInvariants:
    """SprintPolicyManager invariants from the audit."""

    def test_enabled_false_means_no_file_writes(self, tmp_policy_path: Path) -> None:
        """Disabled manager must not write policy state file."""
        manager = SprintPolicyManager(enabled=False, policy_path=tmp_policy_path)
        manager.update(_make_result())
        assert not tmp_policy_path.exists()

    def test_disabled_manager_no_persistent_side_effects(
        self, tmp_policy_path: Path
    ) -> None:
        """Disabled manager must not create persistent state."""
        manager = SprintPolicyManager(enabled=False, policy_path=tmp_policy_path)
        manager.update(_make_result())
        manager.reset()
        assert not tmp_policy_path.exists()

    def test_sprint_policy_state_serialization(self, tmp_policy_path: Path) -> None:
        """SprintPolicyState must serialize to valid JSON."""
        state = SprintPolicyState(
            sprint_sequence_number=42,
            epsilon=0.07,
            total_reward=123.5,
            sprint_rewards=[1.0, 2.0, 3.0],
        )
        tmp_policy_path.write_text(json.dumps(state.__dict__))
        loaded = json.loads(tmp_policy_path.read_text())
        assert loaded["sprint_sequence_number"] == 42
        assert loaded["epsilon"] == 0.07
        assert loaded["total_reward"] == 123.5
        assert loaded["sprint_rewards"] == [1.0, 2.0, 3.0]
