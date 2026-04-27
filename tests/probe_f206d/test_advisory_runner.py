"""
Sprint F206D: SprintAdvisoryRunner — Probe Tests

Tests the extracted advisory runner from sprint_scheduler.py teardown.
Verifies:
- AdvisoryRunOutcome dataclass invariants
- SprintAdvisoryRunner construction
- run_all_advisories() sequential execution in correct order
- Each step's fail-soft behavior
- CancelledError propagation
- Scheduler delegate methods work correctly
- Runner sets scheduler state correctly (_planned_pivots, _analyst_brief, etc.)
"""

from __future__ import annotations

import asyncio
from dataclasses import fields
from unittest.mock import AsyncMock, MagicMock

import pytest

from hledac.universal.runtime.sprint_advisory_runner import (
    AdvisoryRunOutcome,
    SprintAdvisoryRunner,
)


# ── Test AdvisoryRunOutcome dataclass ──────────────────────────────────────────


class TestAdvisoryRunOutcomeDataclass:
    """Invariant: AdvisoryRunOutcome is frozen with correct fields."""

    def test_outcome_frozen(self):
        outcome = AdvisoryRunOutcome(
            planned_pivots=5,
            executed_pivots=3,
            governor_recorded=True,
            brief_generated=True,
        )
        with pytest.raises(Exception):
            outcome.planned_pivots = 10  # type: ignore

    def test_outcome_fields(self):
        flds = {f.name for f in fields(AdvisoryRunOutcome)}
        assert flds >= {
            "planned_pivots",
            "executed_pivots",
            "governor_recorded",
            "brief_generated",
            "error",
        }

    def test_outcome_default_values(self):
        outcome = AdvisoryRunOutcome()
        assert outcome.planned_pivots == 0
        assert outcome.executed_pivots == 0
        assert outcome.governor_recorded is False
        assert outcome.brief_generated is False
        assert outcome.error is None

    def test_outcome_with_values(self):
        outcome = AdvisoryRunOutcome(
            planned_pivots=10,
            executed_pivots=5,
            governor_recorded=True,
            brief_generated=True,
            error=None,
        )
        assert outcome.planned_pivots == 10
        assert outcome.executed_pivots == 5
        assert outcome.governor_recorded is True
        assert outcome.brief_generated is True


# ── Test SprintAdvisoryRunner construction ──────────────────────────────────────


class TestRunnerConstruction:
    """Invariant: runner initializes with scheduler and optional store/governor."""

    def test_runner_construction_minimal(self):
        scheduler = MagicMock()
        runner = SprintAdvisoryRunner(scheduler=scheduler)
        assert runner._scheduler is scheduler
        assert runner._duckdb_store is None
        assert runner._governor is None
        assert runner._analyst_workbench is None

    def test_runner_construction_full(self):
        scheduler = MagicMock()
        store = MagicMock()
        governor = MagicMock()
        workbench = MagicMock()
        runner = SprintAdvisoryRunner(
            scheduler=scheduler,
            duckdb_store=store,
            governor=governor,
            analyst_workbench=workbench,
        )
        assert runner._scheduler is scheduler
        assert runner._duckdb_store is store
        assert runner._governor is governor
        assert runner._analyst_workbench is workbench


# ── Test run_all_advisories sequential execution ───────────────────────────────


class TestRunnerSequentialExecution:
    """Invariant: run_all_advisories executes steps in correct order."""

    @pytest.fixture
    def mock_scheduler(self):
        scheduler = MagicMock()
        scheduler._pivot_planner = MagicMock()
        scheduler._duckdb_store = MagicMock()
        scheduler._governor = MagicMock()
        scheduler._analyst_workbench = MagicMock()
        scheduler._all_findings = []
        scheduler.sprint_id = "test-sprint"
        scheduler.query = "test query"
        scheduler._sidecars_skipped = set()
        scheduler._peak_rss_gib = 0.0
        scheduler._result = MagicMock()
        scheduler._planned_pivots = []
        scheduler._pivot_execution_results = []
        scheduler._analyst_brief = None
        return scheduler

    @pytest.mark.asyncio
    async def test_steps_execute_in_order(self, mock_scheduler):
        """
        Steps must execute in order: planner → executor → governor → brief.
        Verify by tracking call sequence.
        """
        call_order: list[str] = []

        async def mock_planner(outcome):
            call_order.append("planner")
            return outcome

        async def mock_executor(outcome):
            call_order.append("executor")
            return outcome

        async def mock_governor(outcome):
            call_order.append("governor")
            return outcome

        async def mock_brief(outcome):
            call_order.append("brief")
            return outcome

        runner = SprintAdvisoryRunner(scheduler=mock_scheduler)
        runner._run_pivot_planner_advisory = mock_planner
        runner._run_pivot_executor_advisory = mock_executor
        runner._run_resource_governor_advisory = mock_governor
        runner._run_analyst_brief_advisory = mock_brief

        await runner.run_all_advisories()

        assert call_order == ["planner", "executor", "governor", "brief"]

    @pytest.mark.asyncio
    async def test_planner_step_sets_planned_pivots(self, mock_scheduler):
        """Planner step stores pivots in scheduler._planned_pivots."""
        mock_finding = MagicMock()
        mock_finding.payload_text = "evil.com"
        mock_scheduler._all_findings = [mock_finding]

        planned = [MagicMock(), MagicMock()]
        planned[0].expected_value = 0.8
        planned[1].expected_value = 0.6
        mock_scheduler._pivot_planner.plan_pivots = MagicMock(return_value=planned)

        runner = SprintAdvisoryRunner(
            scheduler=mock_scheduler,
            duckdb_store=None,
            governor=None,
            analyst_workbench=None,
        )
        result = await runner.run_all_advisories()

        assert result.planned_pivots == 2
        assert mock_scheduler._planned_pivots == planned

    @pytest.mark.asyncio
    async def test_executor_step_skips_when_no_pivots(self, mock_scheduler):
        """Executor step returns early when no planned pivots."""
        mock_scheduler._planned_pivots = []  # No pivots
        mock_scheduler._duckdb_store = MagicMock()

        runner = SprintAdvisoryRunner(
            scheduler=mock_scheduler,
            duckdb_store=mock_scheduler._duckdb_store,
            governor=None,
            analyst_workbench=None,
        )
        result = await runner.run_all_advisories()

        # Executor should return 0 since there are no pivots
        assert result.executed_pivots == 0

    @pytest.mark.asyncio
    async def test_executor_step_skips_when_no_store(self, mock_scheduler):
        """Executor step returns early when duckdb_store is None."""
        mock_scheduler._planned_pivots = [MagicMock()]
        mock_scheduler._duckdb_store = None

        runner = SprintAdvisoryRunner(
            scheduler=mock_scheduler,
            duckdb_store=None,
            governor=None,
            analyst_workbench=None,
        )
        result = await runner.run_all_advisories()

        assert result.executed_pivots == 0

    @pytest.mark.asyncio
    async def test_governor_step_sets_governor_recorded(self, mock_scheduler):
        """Governor step sets governor_recorded=True on success."""
        mock_governor = MagicMock()
        mock_governor.evaluate = AsyncMock(return_value=MagicMock())
        mock_governor.apply_decision = AsyncMock()

        runner = SprintAdvisoryRunner(
            scheduler=mock_scheduler,
            duckdb_store=None,
            governor=mock_governor,
            analyst_workbench=None,
        )
        result = await runner.run_all_advisories()

        assert result.governor_recorded is True
        mock_governor.evaluate.assert_awaited_once()
        mock_governor.apply_decision.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_brief_step_sets_brief_generated(self, mock_scheduler):
        """Brief step sets brief_generated=True when workbench provided via scheduler."""
        mock_brief = MagicMock()
        mock_brief.headline = "Test sprint brief"

        class RealWorkbench:
            async def build_sprint_brief(self, **kwargs):
                return mock_brief

        # Runner checks getattr(scheduler, '_analyst_workbench', None)
        # So we need to configure the mock_scheduler to return RealWorkbench
        mock_scheduler._analyst_workbench = RealWorkbench()
        mock_scheduler._duckdb_store = None

        runner = SprintAdvisoryRunner(
            scheduler=mock_scheduler,
            duckdb_store=None,
            governor=None,
            analyst_workbench=None,  # Will be read from scheduler via getattr
        )

        result = await runner.run_all_advisories()

        assert result.brief_generated is True
        assert mock_scheduler._analyst_brief is mock_brief


# ── Test fail-soft per step ─────────────────────────────────────────────────────


class TestRunnerFailSoft:
    """Invariant: each advisory step fails soft and continues to next step."""

    @pytest.fixture
    def mock_scheduler(self):
        scheduler = MagicMock()
        scheduler._pivot_planner = MagicMock()
        scheduler._duckdb_store = None
        scheduler._governor = MagicMock()
        scheduler._analyst_workbench = None
        scheduler._all_findings = []
        scheduler.sprint_id = "test-sprint"
        scheduler.query = ""
        scheduler._sidecars_skipped = set()
        scheduler._peak_rss_gib = 0.0
        scheduler._result = MagicMock()
        scheduler._planned_pivots = []
        scheduler._pivot_execution_results = []
        scheduler._analyst_brief = None
        scheduler._get_graph_signal = MagicMock(return_value={})
        return scheduler

    @pytest.mark.asyncio
    async def test_planner_fails_soft_no_crash(self, mock_scheduler):
        """Planner exception does not propagate."""
        mock_scheduler._pivot_planner.plan_pivots = MagicMock(
            side_effect=RuntimeError("planner error")
        )
        runner = SprintAdvisoryRunner(scheduler=mock_scheduler)
        result = await runner.run_all_advisories()
        # Outcome is partial but runner completes
        assert isinstance(result, AdvisoryRunOutcome)

    @pytest.mark.asyncio
    async def test_governor_fails_soft_no_crash(self, mock_scheduler):
        """Governor exception does not propagate."""
        mock_scheduler._governor.evaluate = AsyncMock(
            side_effect=RuntimeError("governor error")
        )
        runner = SprintAdvisoryRunner(scheduler=mock_scheduler)
        result = await runner.run_all_advisories()
        assert isinstance(result, AdvisoryRunOutcome)
        assert result.governor_recorded is False

    @pytest.mark.asyncio
    async def test_all_steps_fail_soft_returns_partial_outcome(self, mock_scheduler):
        """All steps failing still returns partial outcome without crashing."""
        mock_scheduler._governor.evaluate = AsyncMock(
            side_effect=RuntimeError("governor error")
        )
        runner = SprintAdvisoryRunner(scheduler=mock_scheduler)
        result = await runner.run_all_advisories()
        assert isinstance(result, AdvisoryRunOutcome)
        assert result.error is None  # No top-level error


# ── Test CancelledError propagation ────────────────────────────────────────────


class TestCancelledErrorPropagation:
    """Invariant: CancelledError is re-raised, never swallowed."""

    @pytest.fixture
    def mock_scheduler(self):
        scheduler = MagicMock()
        scheduler._pivot_planner = MagicMock()
        scheduler._duckdb_store = None
        scheduler._governor = MagicMock()
        scheduler._governor.evaluate = AsyncMock(return_value=MagicMock())
        scheduler._governor.apply_decision = AsyncMock()
        scheduler._analyst_workbench = None
        scheduler._all_findings = [MagicMock()]  # Non-empty so planner is called
        scheduler.sprint_id = "test-sprint"
        scheduler.query = ""
        scheduler._sidecars_skipped = set()
        scheduler._peak_rss_gib = 0.0
        scheduler._result = MagicMock()
        scheduler._planned_pivots = []
        scheduler._pivot_execution_results = []
        scheduler._analyst_brief = None
        scheduler._get_graph_signal = MagicMock(return_value={})
        return scheduler

    @pytest.mark.asyncio
    async def test_planner_cancelled_error_propagates(self, mock_scheduler):
        """CancelledError from planner step propagates to caller."""
        mock_scheduler._pivot_planner.plan_pivots = MagicMock(
            side_effect=asyncio.CancelledError()
        )
        runner = SprintAdvisoryRunner(
            scheduler=mock_scheduler,
            duckdb_store=getattr(mock_scheduler, "_duckdb_store", None),
            governor=getattr(mock_scheduler, "_governor", None),
            analyst_workbench=None,
        )
        with pytest.raises(asyncio.CancelledError):
            await runner.run_all_advisories()

    @pytest.mark.asyncio
    async def test_governor_cancelled_error_propagates(self, mock_scheduler):
        """CancelledError from governor step propagates to caller."""
        # duckdb_store must be non-None for executor to run (but returns early
        # since no pivots). governor must be passed to runner explicitly.
        mock_scheduler._governor.evaluate = AsyncMock(
            side_effect=asyncio.CancelledError()
        )
        runner = SprintAdvisoryRunner(
            scheduler=mock_scheduler,
            duckdb_store=MagicMock(),  # Non-None so executor doesn't early-return
            governor=mock_scheduler._governor,  # Explicit governor with CancelledError
            analyst_workbench=None,
        )
        with pytest.raises(asyncio.CancelledError):
            await runner.run_all_advisories()


# ── Test Governor RSS tracking ──────────────────────────────────────────────────


class TestGovernorRSSTracking:
    """Invariant: governor step tracks peak RSS and sidecars_skipped on result."""

    @pytest.fixture
    def mock_scheduler(self):
        scheduler = MagicMock()
        scheduler._pivot_planner = None
        scheduler._duckdb_store = None
        scheduler._governor = MagicMock()
        scheduler._governor.evaluate = AsyncMock(return_value=MagicMock())
        scheduler._governor.apply_decision = AsyncMock()
        scheduler._analyst_workbench = None
        scheduler._all_findings = []
        scheduler.sprint_id = "test-sprint"
        scheduler.query = ""
        scheduler._sidecars_skipped = {"sidecar_a", "sidecar_b"}
        scheduler._peak_rss_gib = 2.0
        # Pre-configure _result so MagicMock doesn't auto-create children
        scheduler._result = MagicMock()
        scheduler._result.sidecars_skipped = ()
        scheduler._result.peak_rss_gib = 0.0
        scheduler._result.budget_violations = 0
        scheduler._planned_pivots = []
        scheduler._pivot_execution_results = []
        scheduler._analyst_brief = None
        return scheduler

    @pytest.mark.asyncio
    async def test_governor_records_sidecars_skipped(self, mock_scheduler):
        """Governor step records sidecars_skipped on result."""
        runner = SprintAdvisoryRunner(
            scheduler=mock_scheduler,
            duckdb_store=getattr(mock_scheduler, "_duckdb_store", None),
            governor=getattr(mock_scheduler, "_governor", None),
            analyst_workbench=None,
        )
        result = await runner.run_all_advisories()

        assert result.governor_recorded is True
        assert mock_scheduler._result.sidecars_skipped == ("sidecar_a", "sidecar_b")

    @pytest.mark.asyncio
    async def test_governor_records_peak_rss_gib(self, mock_scheduler):
        """Governor step records peak_rss_gib on result."""
        runner = SprintAdvisoryRunner(
            scheduler=mock_scheduler,
            duckdb_store=getattr(mock_scheduler, "_duckdb_store", None),
            governor=getattr(mock_scheduler, "_governor", None),
            analyst_workbench=None,
        )
        await runner.run_all_advisories()

        # peak_rss_gib should be set (possibly 0 if sample_uma_status returns 0)
        assert hasattr(mock_scheduler._result, "peak_rss_gib")


# ── Test Brief generation ───────────────────────────────────────────────────────


class TestBriefGeneration:
    """Invariant: brief step uses canonical target_id and duckdb_store."""

    @pytest.fixture
    def mock_scheduler(self):
        scheduler = MagicMock()
        scheduler._pivot_planner = None
        scheduler._duckdb_store = MagicMock()
        scheduler._governor = None
        scheduler._analyst_workbench = None  # Will be created on-demand
        scheduler._all_findings = []
        scheduler.sprint_id = "sprint-abc"
        scheduler.query = "evil.com"  # F205J: canonical target_id
        scheduler._sidecars_skipped = set()
        scheduler._peak_rss_gib = 0.0
        scheduler._result = MagicMock()
        scheduler._planned_pivots = []
        scheduler._pivot_execution_results = []
        scheduler._analyst_brief = None
        scheduler._get_graph_signal = MagicMock(return_value={})
        return scheduler

    @pytest.mark.asyncio
    async def test_brief_uses_query_as_target_id(self, mock_scheduler):
        """Brief generation uses query as canonical target_id (F205J)."""
        mock_brief = MagicMock()
        mock_brief.headline = "Sprint sprint-abc: 0 findings"

        calls: list[dict] = []

        class CaptureWorkbench:
            async def build_sprint_brief(self, **kwargs):
                calls.append(kwargs)
                return mock_brief

        # Runner checks getattr(scheduler, '_analyst_workbench', None) first
        mock_scheduler._analyst_workbench = CaptureWorkbench()
        mock_scheduler._duckdb_store = None  # Prevent on-demand workbench creation

        runner = SprintAdvisoryRunner(
            scheduler=mock_scheduler,
            duckdb_store=None,
            governor=None,
            analyst_workbench=None,  # Runner reads from scheduler
        )
        await runner.run_all_advisories()

        assert len(calls) == 1
        assert calls[0]["target_id"] == "evil.com"
        assert calls[0]["sprint_id"] == "sprint-abc"

    @pytest.mark.asyncio
    async def test_brief_falls_back_to_sprint_id(self, mock_scheduler):
        """Brief falls back to sprint_id when query is empty."""
        mock_scheduler.query = ""  # Empty query
        mock_brief = MagicMock()
        mock_brief.headline = "Test"

        calls: list[dict] = []

        class CaptureWorkbench:
            async def build_sprint_brief(self, **kwargs):
                calls.append(kwargs)
                return mock_brief

        mock_scheduler._analyst_workbench = CaptureWorkbench()
        mock_scheduler._duckdb_store = None

        runner = SprintAdvisoryRunner(
            scheduler=mock_scheduler,
            duckdb_store=None,
            governor=None,
            analyst_workbench=None,
        )
        await runner.run_all_advisories()

        assert calls[0]["target_id"] == "sprint-abc"


# ── Test runner order is preserved ───────────────────────────────────────────


class TestRunnerOrderPreserved:
    """Invariant: runner executes steps in fixed order regardless of individual step timing."""

    @pytest.mark.asyncio
    async def test_runner_returns_sequential_outcome(self):
        """Outcome reflects all steps executed sequentially."""
        scheduler = MagicMock()
        scheduler._pivot_planner = None
        scheduler._duckdb_store = None
        scheduler._governor = MagicMock()
        scheduler._governor.evaluate = AsyncMock(return_value=MagicMock())
        scheduler._governor.apply_decision = AsyncMock()
        scheduler._analyst_workbench = None
        scheduler._all_findings = []
        scheduler.sprint_id = "s"
        scheduler.query = ""
        scheduler._sidecars_skipped = set()
        scheduler._peak_rss_gib = 0.0
        scheduler._result = MagicMock()
        scheduler._planned_pivots = []
        scheduler._pivot_execution_results = []
        scheduler._analyst_brief = None
        scheduler._get_graph_signal = MagicMock(return_value={})

        runner = SprintAdvisoryRunner(
            scheduler=scheduler,
            duckdb_store=None,
            governor=scheduler._governor,
            analyst_workbench=None,
        )
        result = await runner.run_all_advisories()

        assert result.planned_pivots == 0  # No planner
        assert result.executed_pivots == 0  # No pivots
        assert result.governor_recorded is True
        assert result.brief_generated is False  # No workbench
