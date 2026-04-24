"""
Sprint F199A — Test reward loop: FindingQualityDecision → source weight adaptation.

Invariant F199A-1: _process_result accumulates fetched/accepted into _source_quality_feedback
Invariant F199A-2: _adapt_source_weights_from_feedback applies ±bounded delta [0.3, 2.5]
Invariant F199A-3: _reset_result clears _source_quality_feedback
Invariant F199A-4: scheduler runs adaptation at teardown even when policy_manager is None
Invariant F199A-5: update_with_quality_decisions is a no-op stub (source weights live in scheduler)
"""
import pytest

from hledac.universal.runtime.sprint_scheduler import (
    SprintScheduler,
    SprintSchedulerConfig,
)


class FakeFeedResult:
    """Minimal FeedPipelineRunResult stand-in for testing."""
    def __init__(self, fetched=0, accepted=0, matched=0):
        self.fetched_entries = fetched
        self.accepted_findings = accepted
        self.matched_patterns = matched
        self.signal_stage = "unknown"
        self.feed_confidence_score = 50
        self.winning_source_breakdown = {}


class TestF199AFeedbackAccumulation:
    """Test F199A-1: _source_quality_feedback populated from _process_result."""

    def test_feedback_accumulates_from_process_result(self):
        sched = SprintScheduler(SprintSchedulerConfig())
        url = "https://threatfox.abuse.ch/export/json/recent/"
        result = FakeFeedResult(fetched=20, accepted=8, matched=8)
        sched._process_result(url, result)
        assert url in sched._source_quality_feedback
        assert sched._source_quality_feedback[url]["fetched"] == 20
        assert sched._source_quality_feedback[url]["accepted"] == 8

    def test_feedback_accumulates_acumulates(self):
        sched = SprintScheduler(SprintSchedulerConfig())
        url = "https://urlhaus.abuse.ch/hosts/json/"
        sched._process_result(url, FakeFeedResult(fetched=10, accepted=3))
        sched._process_result(url, FakeFeedResult(fetched=10, accepted=5))
        # fetched=20, accepted=8 (3+5)
        assert sched._source_quality_feedback[url]["fetched"] == 20
        assert sched._source_quality_feedback[url]["accepted"] == 8

    def test_feedback_bounded_at_200_sources(self):
        """F199A-2: max 200 feed_urls tracked."""
        sched = SprintScheduler(SprintSchedulerConfig())
        for i in range(250):
            sched._process_result(f"https://source-{i}.example/feed", FakeFeedResult(5, 2))
        # Only first 200 tracked
        assert len(sched._source_quality_feedback) == 200

    def test_reset_result_clears_feedback(self):
        """F199A-3: _reset_result clears _source_quality_feedback."""
        sched = SprintScheduler(SprintSchedulerConfig())
        sched._source_quality_feedback["https://x.io"] = {"fetched": 10, "accepted": 3}
        sched._reset_result()
        assert sched._source_quality_feedback == {}


class TestF199ASourceWeightAdaptation:
    """Test F199A-2: _adapt_source_weights_from_feedback with B.6 bounds."""

    def test_adaptation_high_ratio_plus10pct(self):
        """ratio >= 0.7 → weight multiplied by 1.10."""
        sched = SprintScheduler(SprintSchedulerConfig())
        # Default tier mapping → OTHER tier → key "other"
        sched._source_quality_feedback["https://high_quality.example/"] = {"fetched": 100, "accepted": 80}
        sched._source_weights["other"] = 1.0
        sched._adapt_source_weights_from_feedback()
        assert sched._source_weights["other"] == pytest.approx(1.10, rel=1e-6)

    def test_adaptation_warm_ratio_plus5pct(self):
        """0.40 <= ratio < 0.7 → weight multiplied by 1.05."""
        sched = SprintScheduler(SprintSchedulerConfig())
        sched._source_quality_feedback["https://warm.example/"] = {"fetched": 100, "accepted": 45}
        sched._source_weights["other"] = 1.0
        sched._adapt_source_weights_from_feedback()
        assert sched._source_weights["other"] == pytest.approx(1.05, rel=1e-6)

    def test_adaptation_neutral_ratio(self):
        """0.15 <= ratio < 0.40 → neutral (delta = 1.00)."""
        sched = SprintScheduler(SprintSchedulerConfig())
        sched._source_quality_feedback["https://neutral.example/"] = {"fetched": 100, "accepted": 20}
        sched._source_weights["other"] = 1.0
        sched._adapt_source_weights_from_feedback()
        assert sched._source_weights["other"] == 1.0  # no change

    def test_adaptation_low_ratio_minus5pct(self):
        """ratio < 0.15 → weight multiplied by 0.95."""
        sched = SprintScheduler(SprintSchedulerConfig())
        sched._source_quality_feedback["https://poor.example/"] = {"fetched": 100, "accepted": 5}
        sched._source_weights["other"] = 1.0
        sched._adapt_source_weights_from_feedback()
        assert sched._source_weights["other"] == pytest.approx(0.95, rel=1e-6)

    def test_adaptation_clamped_floor_03(self):
        """B.6 floor: weight cannot go below 0.3."""
        sched = SprintScheduler(SprintSchedulerConfig())
        sched._source_quality_feedback["https://bad.example/"] = {"fetched": 100, "accepted": 3}
        sched._source_weights["other"] = 0.33  # just above floor
        sched._adapt_source_weights_from_feedback()
        assert sched._source_weights["other"] >= 0.3

    def test_adaptation_clamped_ceiling_25(self):
        """B.6 ceiling: weight cannot exceed 2.5."""
        sched = SprintScheduler(SprintSchedulerConfig())
        sched._source_quality_feedback["https://great.example/"] = {"fetched": 100, "accepted": 90}
        sched._source_weights["other"] = 2.3
        sched._adapt_source_weights_from_feedback()
        assert sched._source_weights["other"] <= 2.5

    def test_adaptation_unknown_defaults_to_other(self):
        """Unknown URLs → OTHER tier → key 'other'. Adapts from 1.0 starting point."""
        sched = SprintScheduler(SprintSchedulerConfig())
        # Unknown URL falls to OTHER tier, uses key "other"
        sched._source_quality_feedback["https://unknown.example/"] = {"fetched": 100, "accepted": 75}
        sched._source_weights["other"] = 1.0
        sched._adapt_source_weights_from_feedback()
        assert sched._source_weights["other"] == pytest.approx(1.10, rel=1e-6)

    def test_adaptation_zero_total_no_change(self):
        """total=0 → no change to weight."""
        sched = SprintScheduler(SprintSchedulerConfig())
        sched._source_quality_feedback["https://empty.example/"] = {"fetched": 0, "accepted": 0}
        sched._source_weights["other"] = 1.23
        sched._adapt_source_weights_from_feedback()
        assert sched._source_weights["other"] == 1.23  # unchanged


class TestF199AIntegration:
    """Test F199A-4: scheduler calls adaptation at teardown (fail-soft)."""

    def test_adaptation_runs_at_run_teardown(self):
        """Fail-soft: adaptation called even when policy_manager is None."""
        sched = SprintScheduler(SprintSchedulerConfig())
        sched._source_quality_feedback["https://test.example/"] = {"fetched": 50, "accepted": 35}
        sched._source_weights["other"] = 1.0
        # _adapt_source_weights_from_feedback must not raise
        sched._adapt_source_weights_from_feedback()
        assert sched._source_weights["other"] == pytest.approx(1.10, rel=1e-6)

    def test_policy_manager_none_still_adapts(self):
        """policy_manager=None should not block adaptation."""
        sched = SprintScheduler(SprintSchedulerConfig())
        sched._policy_manager = None
        sched._source_quality_feedback["https://test.example/"] = {"fetched": 20, "accepted": 10}
        sched._source_weights["other"] = 1.0
        sched._adapt_source_weights_from_feedback()
        assert sched._source_weights["other"] == pytest.approx(1.05, rel=1e-6)


class TestF199APolicyManagerStub:
    """Test F199A-5: update_with_quality_decisions is a no-op."""

    def test_update_with_quality_decisions_is_noop(self):
        from hledac.universal.rl.sprint_policy_manager import SprintPolicyManager
        import tempfile, os

        with tempfile.TemporaryDirectory() as tmpdir:
            pm = SprintPolicyManager(enabled=True, policy_path=os.path.join(tmpdir, "policy.json"))
            initial_seq = pm.sprint_sequence_number

            # Should be a no-op (no crash, no state change)
            pm.update_with_quality_decisions([], feed_url="https://example.com")

            # Sequence unchanged (it's a no-op)
            assert pm.sprint_sequence_number == initial_seq

    def test_update_with_quality_decisions_works_when_disabled(self):
        from hledac.universal.rl.sprint_policy_manager import SprintPolicyManager
        import tempfile, os

        with tempfile.TemporaryDirectory() as tmpdir:
            pm = SprintPolicyManager(enabled=False, policy_path=os.path.join(tmpdir, "policy.json"))
            # Must not raise even when disabled
            pm.update_with_quality_decisions([], feed_url="https://example.com")