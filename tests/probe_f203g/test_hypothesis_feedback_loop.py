"""
Sprint F203G: Hypothesis Feedback Loop & Dead-End Pruning Tests

Tests cover:
- HypothesisFeedbackRecord and HypothesisFeedbackSummary dataclasses
- HypothesisFeedbackAdapter.async_record() and async_get_summary()
- DuckDBShadowStore.async_record_hypothesis_feedback() and async_get_hypothesis_feedback()
- PivotPlanner.plan_pivots() with feedback_summary applying penalties
- SprintScheduler.record_hypothesis_feedback() async method
- MAX_FEEDBACK_RECORDS=10000, MAX_PRUNED_TYPES=20 bounds
- No hard ban: penalty only after >= 3 consecutive zero-yield

F203G Invariants:
| Test | Invariant |
|------|-----------|
| test_feedback_record_dataclass | HypothesisFeedbackRecord frozen dataclass |
| test_feedback_summary_dataclass | HypothesisFeedbackSummary frozen dataclass |
| test_adapter_in_memory_mode | Adapter works without duckdb_store |
| test_adapter_record_writes_to_store | async_record calls duckdb method |
| test_adapter_get_summary_aggregates | Aggregation by (pivot_type, ioc_type) |
| test_penalty_multiplier_no_penalty | avg_signal >= 0.3 → multiplier=1.0 |
| test_penalty_multiplier_consecutive_zero | >= 3 zeros → penalty applied |
| test_penalty_multiplier_minimum | Minimum penalty is 0.1 |
| test_duckdb_schema_exists | hypothesis_feedback table defined |
| test_duckdb_async_record_hypothesis_feedback | async_record_hypothesis_feedback method exists |
| test_duckdb_async_get_hypothesis_feedback | async_get_hypothesis_feedback method exists |
| test_pivot_planner_accepts_feedback_summary | plan_pivots(feedback_summary=...) works |
| test_pivot_planner_penalizes_low_yield | Low-yield type gets reduced expected_value |
| test_pivot_planner_no_penalty_unknown_type | Unknown pivot type gets multiplier=1.0 |
| test_record_hypothesis_feedback_silent_fail | record_hypothesis_feedback fails silently |
| test_feedback_summary_penalty_roundtrip | Penalty survives record→get→penalty cycle |
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

import pytest


class TestHypothesisFeedbackDataclasses:
    """Test F203G feedback dataclasses."""

    def test_feedback_record_dataclass(self):
        """F203G: HypothesisFeedbackRecord is a frozen dataclass."""
        from hledac.universal.runtime.hypothesis_feedback import HypothesisFeedbackRecord

        record = HypothesisFeedbackRecord(
            id="test-id",
            target_id="target_001",
            pivot_type="domain",
            ioc_type="domain",
            produced_count=5,
            accepted_count=3,
            signal_value=0.75,
            ts=1234567890.0,
        )
        assert record.id == "test-id"
        assert record.pivot_type == "domain"
        assert record.produced_count == 5
        assert record.accepted_count == 3
        assert record.signal_value == 0.75

        # Frozen — cannot modify
        with pytest.raises(Exception):  # dataclasses.FrozenInstanceError
            record.id = "modified"  # type: ignore

    def test_feedback_summary_dataclass(self):
        """F203G: HypothesisFeedbackSummary is a frozen dataclass."""
        from hledac.universal.runtime.hypothesis_feedback import HypothesisFeedbackSummary

        summary = HypothesisFeedbackSummary(
            pivot_type="domain",
            ioc_type="domain",
            total_records=10,
            total_produced=50,
            total_accepted=30,
            avg_signal=0.6,
            consecutive_zero_yield=0,
            penalty_multiplier=1.0,
        )
        assert summary.pivot_type == "domain"
        assert summary.total_records == 10
        assert summary.avg_signal == 0.6
        assert summary.penalty_multiplier == 1.0

        # Frozen
        with pytest.raises(Exception):
            summary.penalty_multiplier = 0.5  # type: ignore


class TestHypothesisFeedbackAdapter:
    """Test HypothesisFeedbackAdapter."""

    def test_adapter_in_memory_mode(self):
        """F203G: Adapter works without duckdb_store (no-op)."""
        from hledac.universal.runtime.hypothesis_feedback import HypothesisFeedbackAdapter

        adapter = HypothesisFeedbackAdapter(duckdb_store=None, target_id="test")
        # async_record should return False when no store
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            adapter.async_record(
                pivot_type="domain",
                ioc_type="domain",
                produced_count=5,
                accepted_count=3,
                signal_value=0.75,
            )
        )
        assert result is False

    def test_adapter_get_summary_empty_store(self):
        """F203G: async_get_summary returns empty dict when no store."""
        from hledac.universal.runtime.hypothesis_feedback import HypothesisFeedbackAdapter

        adapter = HypothesisFeedbackAdapter(duckdb_store=None, target_id="test")
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            adapter.async_get_summary()
        )
        assert result == {}

    def test_penalty_multiplier_no_penalty(self):
        """F203G: avg_signal >= 0.3 → multiplier=1.0 (no penalty)."""
        from hledac.universal.runtime.hypothesis_feedback import HypothesisFeedbackAdapter
        from hledac.universal.runtime.hypothesis_feedback import HypothesisFeedbackSummary

        adapter = HypothesisFeedbackAdapter(target_id="test")
        summary = HypothesisFeedbackSummary(
            pivot_type="domain",
            ioc_type="domain",
            total_records=5,
            total_produced=10,
            total_accepted=8,
            avg_signal=0.5,  # >= 0.3
            consecutive_zero_yield=0,
            penalty_multiplier=1.0,
        )
        multiplier = adapter._compute_penalty(
            avg_signal=0.5,
            consecutive_zero_yield=0,
            _total_records=5,
        )
        assert multiplier == 1.0

    def test_penalty_multiplier_consecutive_zero(self):
        """F203G: consecutive_zero_yield >= 3 → penalty applied."""
        from hledac.universal.runtime.hypothesis_feedback import HypothesisFeedbackAdapter

        adapter = HypothesisFeedbackAdapter(target_id="test")
        # 3 consecutive zeros with low signal
        multiplier = adapter._compute_penalty(
            avg_signal=0.1,
            consecutive_zero_yield=3,
            _total_records=5,
        )
        assert multiplier == 0.5  # PENALTY_FACTOR = 0.5

    def test_penalty_multiplier_minimum(self):
        """F203G: Minimum penalty is 0.1 (never 0.0)."""
        from hledac.universal.runtime.hypothesis_feedback import HypothesisFeedbackAdapter

        adapter = HypothesisFeedbackAdapter(target_id="test")
        # Many consecutive zeros
        multiplier = adapter._compute_penalty(
            avg_signal=0.0,
            consecutive_zero_yield=10,
            _total_records=10,
        )
        assert multiplier == 0.1  # minimum

    def test_penalty_multiplier_mild_low_signal(self):
        """F203G: Low signal but no consecutive zeros → mild penalty (0.7)."""
        from hledac.universal.runtime.hypothesis_feedback import HypothesisFeedbackAdapter

        adapter = HypothesisFeedbackAdapter(target_id="test")
        multiplier = adapter._compute_penalty(
            avg_signal=0.05,  # < 0.1
            consecutive_zero_yield=0,
            _total_records=2,
        )
        assert multiplier == 0.7

    def test_get_penalty_multiplier_unknown_type(self):
        """F203G: Unknown pivot type → multiplier=1.0 (no penalty)."""
        from hledac.universal.runtime.hypothesis_feedback import HypothesisFeedbackAdapter
        from hledac.universal.runtime.hypothesis_feedback import HypothesisFeedbackSummary

        adapter = HypothesisFeedbackAdapter(target_id="test")
        summaries = {
            ("domain", "domain"): HypothesisFeedbackSummary(
                pivot_type="domain", ioc_type="domain",
                total_records=1, total_produced=0, total_accepted=0,
                avg_signal=0.0, consecutive_zero_yield=3, penalty_multiplier=0.5,
            )
        }
        # identity type not in summaries → should get 1.0
        multiplier = adapter.get_penalty_multiplier("identity", "email", summaries)
        assert multiplier == 1.0

    def test_adapter_aggregate(self):
        """F203G: _aggregate correctly groups by (pivot_type, ioc_type)."""
        from hledac.universal.runtime.hypothesis_feedback import (
            HypothesisFeedbackAdapter,
            HypothesisFeedbackRecord,
        )

        adapter = HypothesisFeedbackAdapter(target_id="test")
        records = [
            HypothesisFeedbackRecord(
                id=f"id_{i}",
                target_id="test",
                pivot_type="domain",
                ioc_type="domain",
                produced_count=5,
                accepted_count=3,
                signal_value=0.6,
                ts=1000.0 + i,
            )
            for i in range(3)
        ]
        summary = adapter._aggregate(records)
        assert ("domain", "domain") in summary
        s = summary[("domain", "domain")]
        assert s.total_records == 3
        assert s.total_produced == 15
        assert s.total_accepted == 9


class TestDuckDBMethods:
    """Test duckdb_store.py F203G methods exist and have correct signatures."""

    def test_duckdb_schema_has_hypothesis_feedback(self):
        """F203G: hypothesis_feedback table defined in duckdb_store schema."""
        from hledac.universal.knowledge.duckdb_store import _SCHEMA_SQL

        assert "hypothesis_feedback" in _SCHEMA_SQL
        assert "pivot_type" in _SCHEMA_SQL
        assert "ioc_type" in _SCHEMA_SQL
        assert "produced_count" in _SCHEMA_SQL
        assert "signal_value" in _SCHEMA_SQL

    def test_duckdb_async_record_method_exists(self):
        """F203G: async_record_hypothesis_feedback method exists on DuckDBShadowStore."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore.__new__(DuckDBShadowStore)
        store._initialized = False
        store._closed = False

        # Method should exist
        assert hasattr(store, "async_record_hypothesis_feedback")
        assert callable(store.async_record_hypothesis_feedback)

    def test_duckdb_async_get_method_exists(self):
        """F203G: async_get_hypothesis_feedback method exists on DuckDBShadowStore."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore.__new__(DuckDBShadowStore)
        store._initialized = False
        store._closed = False

        assert hasattr(store, "async_get_hypothesis_feedback")
        assert callable(store.async_get_hypothesis_feedback)


class TestPivotPlannerFeedbackIntegration:
    """Test PivotPlanner with feedback_summary parameter."""

    def test_plan_pivots_accepts_feedback_summary(self):
        """F203G: plan_pivots accepts feedback_summary parameter."""
        from hledac.universal.runtime.pivot_planner import PivotPlanner
        from hledac.universal.runtime.hypothesis_feedback import HypothesisFeedbackSummary

        # Create a finding with a domain
        class MockFinding:
            def __init__(self):
                self.finding_id = "fid_001"
                self.source_type = "ct_log"
                self.confidence = 0.8
                self.payload_text = "https://evil.example.com/path"

        planner = PivotPlanner()
        summaries = {
            ("domain", "domain"): HypothesisFeedbackSummary(
                pivot_type="domain", ioc_type="domain",
                total_records=5, total_produced=10, total_accepted=8,
                avg_signal=0.6, consecutive_zero_yield=0, penalty_multiplier=1.0,
            )
        }
        # Should not raise
        pivots = planner.plan_pivots(
            [MockFinding()],
            feedback_summary=summaries,
        )
        assert isinstance(pivots, list)

    def test_plan_pivots_penalizes_low_yield(self):
        """F203G: Low-yield pivot type gets reduced expected_value via penalty."""
        from hledac.universal.runtime.pivot_planner import PivotPlanner, PivotType
        from hledac.universal.runtime.hypothesis_feedback import HypothesisFeedbackSummary

        class MockFinding:
            def __init__(self):
                self.finding_id = "fid_email"
                self.source_type = "ct_log"
                self.confidence = 0.8
                self.payload_text = "test@example.com"

        planner = PivotPlanner()

        # No feedback → no penalty
        pivots_no_fb = planner.plan_pivots([MockFinding()], feedback_summary=None)
        leak_pivots = [p for p in pivots_no_fb if p.pivot_type == PivotType.LEAK]
        assert len(leak_pivots) > 0
        base_leak_score = leak_pivots[0].expected_value

        # With feedback → penalized
        summaries = {
            ("leak", "email"): HypothesisFeedbackSummary(
                pivot_type="leak", ioc_type="email",
                total_records=5, total_produced=0, total_accepted=0,
                avg_signal=0.0, consecutive_zero_yield=5, penalty_multiplier=0.1,
            )
        }
        pivots_penalized = planner.plan_pivots([MockFinding()], feedback_summary=summaries)
        leak_penalized = [p for p in pivots_penalized if p.pivot_type == PivotType.LEAK]
        assert len(leak_penalized) > 0
        # Penalized score should be lower
        assert leak_penalized[0].expected_value < base_leak_score

    def test_plan_pivots_no_penalty_unknown_type(self):
        """F203G: Unknown pivot type gets multiplier=1.0 (no reduction)."""
        from hledac.universal.runtime.pivot_planner import PivotPlanner, PivotType
        from hledac.universal.runtime.hypothesis_feedback import HypothesisFeedbackSummary

        class MockFinding:
            def __init__(self):
                self.finding_id = "fid_001"
                self.source_type = "ct_log"
                self.confidence = 0.8
                self.payload_text = "https://example.com"

        planner = PivotPlanner()
        summaries = {
            # Only domain feedback exists, not archive
            ("domain", "domain"): HypothesisFeedbackSummary(
                pivot_type="domain", ioc_type="domain",
                total_records=5, total_produced=10, total_accepted=8,
                avg_signal=0.6, consecutive_zero_yield=0, penalty_multiplier=1.0,
            )
        }
        pivots = planner.plan_pivots([MockFinding()], feedback_summary=summaries)
        # Archive pivot should not be penalized (unknown type → multiplier 1.0)
        archive_pivots = [p for p in pivots if p.pivot_type == PivotType.ARCHIVE]
        assert len(archive_pivots) > 0
        # Score should be > 0 (not zeroed out)
        assert archive_pivots[0].expected_value > 0


class TestSprintSchedulerIntegration:
    """Test SprintScheduler F203G integration."""

    def test_record_hypothesis_feedback_silent_fail_no_store(self):
        """F203G: record_hypothesis_feedback fails silently when no store."""
        import asyncio
        from unittest.mock import MagicMock

        # Minimal mock scheduler
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler

        scheduler = SprintScheduler.__new__(SprintScheduler)
        scheduler._duckdb_store = None
        scheduler.sprint_id = "test_sprint"

        # Should not raise
        async def run_test():
            await scheduler.record_hypothesis_feedback(
                pivot_type="domain",
                ioc_type="domain",
                produced_count=5,
                accepted_count=3,
                signal_value=0.75,
            )

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_record_hypothesis_feedback_calls_duckdb(self):
        """F203G: record_hypothesis_feedback calls duckdb async method."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        from hledac.universal.runtime.sprint_scheduler import SprintScheduler

        scheduler = SprintScheduler.__new__(SprintScheduler)
        mock_store = MagicMock()
        mock_store.async_record_hypothesis_feedback = AsyncMock(return_value=True)
        scheduler._duckdb_store = mock_store
        scheduler.sprint_id = "test_sprint"

        async def run_test():
            await scheduler.record_hypothesis_feedback(
                pivot_type="domain",
                ioc_type="domain",
                produced_count=5,
                accepted_count=3,
                signal_value=0.75,
            )
            mock_store.async_record_hypothesis_feedback.assert_called_once()
            call_args = mock_store.async_record_hypothesis_feedback.call_args
            record = call_args[0][0]
            assert record.pivot_type == "domain"
            assert record.ioc_type == "domain"
            assert record.produced_count == 5

        asyncio.get_event_loop().run_until_complete(run_test())


class TestBounds:
    """Test F203G bounds invariants."""

    def test_max_feedback_records_bound(self):
        """F203G: MAX_FEEDBACK_RECORDS=10000."""
        from hledac.universal.runtime.hypothesis_feedback import MAX_FEEDBACK_RECORDS

        assert MAX_FEEDBACK_RECORDS == 10000

    def test_max_pruned_types_bound(self):
        """F203G: MAX_PRUNED_TYPES=20."""
        from hledac.universal.runtime.hypothesis_feedback import MAX_PRUNED_TYPES

        assert MAX_PRUNED_TYPES == 20


class TestFeedbackRoundtrip:
    """Test F203G: penalty survives record → get → penalty cycle."""

    def test_feedback_summary_penalty_roundtrip(self):
        """F203G: Penalty from duckdb record roundtrips through adapter to planner."""
        from hledac.universal.runtime.hypothesis_feedback import (
            HypothesisFeedbackAdapter,
            HypothesisFeedbackRecord,
        )

        adapter = HypothesisFeedbackAdapter(target_id="roundtrip_test")

        # Simulate 3 zero-yield records for a pivot type
        records = [
            HypothesisFeedbackRecord(
                id=f"zero_{i}",
                target_id="roundtrip_test",
                pivot_type="leak",
                ioc_type="email",
                produced_count=0,
                accepted_count=0,
                signal_value=0.0,
                ts=1000.0 + i,
            )
            for i in range(3)
        ]

        summary = adapter._aggregate(records)
        assert ("leak", "email") in summary

        s = summary[("leak", "email")]
        assert s.consecutive_zero_yield == 3
        assert s.penalty_multiplier == 0.5  # 3 zeros → 0.5

        # Planner would apply this penalty
        multiplier = adapter.get_penalty_multiplier("leak", "email", summary)
        assert multiplier == 0.5
