"""Tests for scorecard _task_dedup metrics correctness."""
import pytest
from unittest.mock import MagicMock

from runtime.scorecard import ScorecardBuilder
from _core import aclose


class MockFinding:
    """Minimal CanonicalFinding-like object for testing."""
    def __init__(self, source_type: str = "public"):
        self.source_type = source_type


class MockSprintReport:
    """Minimal SprintReport-like object for testing."""
    def __init__(self, findings):
        self.findings = findings


class MockStore:
    """DuckDBShadowStore-like mock for testing get_dedup_runtime_status."""
    def __init__(self, dedup_status: dict):
        self._dedup_status = dedup_status

    def get_dedup_runtime_status(self) -> dict:
        return self._dedup_status


class TestTaskDedupMetrics:
    """Test suite for scorecard _task_dedup metrics."""

    def _make_scorecard(self, sprint_report, store):
        """Construct a ScorecardBuilder with minimal required fields."""
        return ScorecardBuilder(
            store=store,
            sprint_report=sprint_report,
            target="test_target",
            phase_timings={},
            sprint_id="test-sprint",
            analyst_brief=None,
        )

    @pytest.mark.asyncio
    async def test_task_dedup_ioc_val_incremented(self):
        """ioc_val should equal the number of findings iterated in the loop."""
        findings = [MockFinding("public"), MockFinding("passive"), MockFinding("ct")]
        sprint_report = MockSprintReport(findings=findings)
        scorecard = self._make_scorecard(sprint_report, store=None)

        await scorecard._task_dedup()

        assert scorecard._results.get("ioc_nodes") == 3, (
            f"Expected ioc_nodes=3, got {scorecard._results.get('ioc_nodes')}"
        )

    @pytest.mark.asyncio
    async def test_task_dedup_fallback_ioc_count(self):
        """Fallback branch should set both accepted and ioc from dedup dict."""
        dedup_status = {"accepted_count": 5, "ioc_count": 10}
        store = MockStore(dedup_status=dedup_status)
        # empty findings triggers fallback
        sprint_report = MockSprintReport(findings=[])
        scorecard = self._make_scorecard(sprint_report, store=store)

        await scorecard._task_dedup()

        assert scorecard._results.get("accepted") == 5, (
            f"Expected accepted=5, got {scorecard._results.get('accepted')}"
        )
        assert scorecard._results.get("ioc_nodes") == 10, (
            f"Expected ioc_nodes=10, got {scorecard._results.get('ioc_nodes')}"
        )

    @pytest.mark.asyncio
    async def test_task_dedup_accepted_in_loop(self):
        """accepted should also be incremented in the findings loop."""
        findings = [MockFinding("public"), MockFinding("ct"), MockFinding("dns")]
        sprint_report = MockSprintReport(findings=findings)
        scorecard = self._make_scorecard(sprint_report, store=None)

        await scorecard._task_dedup()

        assert scorecard._results.get("accepted") == 3, (
            f"Expected accepted=3, got {scorecard._results.get('accepted')}"
        )
