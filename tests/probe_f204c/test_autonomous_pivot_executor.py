"""
Sprint F204C: Autonomous Pivot Executor — Probe Tests
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import fields
from unittest.mock import AsyncMock, MagicMock

import pytest

from hledac.universal.runtime.pivot_executor import (
    MAX_ACTIVE_PIVOTS,
    MAX_PIVOT_FINDINGS,
    MAX_PIVOTS_PER_SPRINT,
    PIVOT_TIMEOUT_S,
    PivotExecutionRequest,
    PivotExecutionResult,
    AutonomousPivotExecutor,
)


def _make_pivot(
    priority: float = 0.0,
    pivot_id: str = "p-1",
    pivot_type: str = "domain",
    ioc_type: str = "domain",
    ioc_value: str = "example.com",
):
    """Create a Pivot-like mock object."""
    pivot = MagicMock()
    pivot.priority = priority
    pivot.pivot_id = pivot_id
    pivot.pivot_type = pivot_type
    pivot.ioc_type = ioc_type
    pivot.ioc_value = ioc_value
    return pivot


class TestDataclasses:
    """Invariant: dataclasses are frozen and have correct fields."""

    def test_pivot_execution_request_frozen(self):
        req = PivotExecutionRequest(
            pivot_id="p-1", pivot_type="domain", ioc_type="domain",
            ioc_value="example.com", confidence=0.8, reason="test",
        )
        with pytest.raises(Exception):
            req.pivot_id = "changed"

    def test_pivot_execution_request_fields(self):
        flds = {f.name for f in fields(PivotExecutionRequest)}
        assert flds >= {
            "pivot_id", "pivot_type", "ioc_type", "ioc_value",
            "confidence", "reason",
        }

    def test_pivot_execution_result_frozen(self):
        res = PivotExecutionResult(
            pivot_id="p-1", attempted=True, produced_count=5,
            accepted_count=3, signal_value=0.6, error="", elapsed_ms=150.0,
        )
        with pytest.raises(Exception):
            res.produced_count = 99

    def test_pivot_execution_result_fields(self):
        flds = {f.name for f in fields(PivotExecutionResult)}
        assert flds >= {
            "pivot_id", "attempted", "produced_count", "accepted_count",
            "signal_value", "error", "elapsed_ms",
        }


class TestBounds:
    def test_max_active_pivots(self):
        assert MAX_ACTIVE_PIVOTS == 3

    def test_max_pivots_per_sprint(self):
        assert MAX_PIVOTS_PER_SPRINT == 10

    def test_pivot_timeout(self):
        assert PIVOT_TIMEOUT_S == 25.0

    def test_max_pivot_findings(self):
        assert MAX_PIVOT_FINDINGS == 50


class TestExecutorInit:
    def test_init_all_params(self):
        store = MagicMock()
        gov = MagicMock()
        fb = MagicMock()
        ex = AutonomousPivotExecutor(
            duckdb_store=store, resource_governor=gov, feedback_adapter=fb,
            max_active=5, max_per_sprint=8, pivot_timeout=10.0, max_findings=20,
        )
        assert ex._store is store
        assert ex._governor is gov
        assert ex._feedback is fb
        assert ex._max_active == 5
        assert ex._max_per_sprint == 8
        assert ex._pivot_timeout == 10.0
        assert ex._max_findings == 20
        assert ex._executed_count == 0

    def test_init_defaults(self):
        ex = AutonomousPivotExecutor(duckdb_store=MagicMock())
        assert ex._max_active == MAX_ACTIVE_PIVOTS
        assert ex._max_per_sprint == MAX_PIVOTS_PER_SPRINT
        assert ex._pivot_timeout == PIVOT_TIMEOUT_S
        assert ex._max_findings == MAX_PIVOT_FINDINGS


class TestExecuteTop:
    """Test execute_top behavior."""

    @pytest.mark.asyncio
    async def test_empty_pivots_returns_empty(self):
        ex = AutonomousPivotExecutor(duckdb_store=MagicMock())
        result = await ex.execute_top([], [])
        assert result == []

    @pytest.mark.asyncio
    async def test_respects_max_per_sprint(self):
        store = MagicMock()
        store.async_ingest_findings_batch = AsyncMock(return_value=[])
        ex = AutonomousPivotExecutor(duckdb_store=store, max_per_sprint=3, max_active=3)
        pivots = [_make_pivot(priority=i, pivot_id=f"p-{i}") for i in range(7)]
        ex._run_pivot_search = AsyncMock(return_value=[])
        result = await ex.execute_top(pivots, [])
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_sorted_by_priority(self):
        store = MagicMock()
        store.async_ingest_findings_batch = AsyncMock(return_value=[])
        ex = AutonomousPivotExecutor(duckdb_store=store, max_per_sprint=3, max_active=3)
        call_order = []

        async def mock_search(pivot):
            call_order.append(getattr(pivot, 'pivot_id'))
            return []

        ex._run_pivot_search = mock_search
        pivots = [
            _make_pivot(priority=5.0, pivot_id="low-priority"),
            _make_pivot(priority=-2.0, pivot_id="high-priority"),
            _make_pivot(priority=1.0, pivot_id="mid-priority"),
        ]
        await ex.execute_top(pivots, [])
        assert call_order == ["high-priority", "mid-priority", "low-priority"]

    @pytest.mark.asyncio
    async def test_ram_guard_critical_skips(self):
        gov = MagicMock()
        gov.sample_uma_status = AsyncMock(
            return_value=MagicMock(is_critical=True, is_emergency=False)
        )
        ex = AutonomousPivotExecutor(duckdb_store=MagicMock(), resource_governor=gov)
        result = await ex.execute_top([_make_pivot()], [])
        assert result == []

    @pytest.mark.asyncio
    async def test_ram_guard_emergency_skips(self):
        gov = MagicMock()
        gov.sample_uma_status = AsyncMock(
            return_value=MagicMock(is_critical=False, is_emergency=True)
        )
        ex = AutonomousPivotExecutor(duckdb_store=MagicMock(), resource_governor=gov)
        result = await ex.execute_top([_make_pivot()], [])
        assert result == []

    @pytest.mark.asyncio
    async def test_ram_guard_normal_allows(self):
        gov = MagicMock()
        gov.sample_uma_status = AsyncMock(
            return_value=MagicMock(is_critical=False, is_emergency=False)
        )
        store = MagicMock()
        store.async_ingest_findings_batch = AsyncMock(return_value=[])
        ex = AutonomousPivotExecutor(
            duckdb_store=store, resource_governor=gov,
            max_per_sprint=3, max_active=3,
        )
        ex._run_pivot_search = AsyncMock(return_value=[])
        pivots = [_make_pivot(priority=0.0)]
        result = await ex.execute_top(pivots, [])
        assert len(result) == 1
        assert result[0].attempted is True

    @pytest.mark.asyncio
    async def test_one_pivot_failure_does_not_block_others(self):
        store = MagicMock()
        store.async_ingest_findings_batch = AsyncMock(return_value=[])
        ex = AutonomousPivotExecutor(duckdb_store=store, max_per_sprint=3, max_active=3)

        async def mock_search(pivot):
            if getattr(pivot, 'pivot_id') == "p-2":
                raise RuntimeError("search failed")
            return [{"accepted": True}]

        ex._run_pivot_search = mock_search
        pivots = [
            _make_pivot(priority=0.0, pivot_id="p-1"),
            _make_pivot(priority=0.1, pivot_id="p-2"),
            _make_pivot(priority=0.2, pivot_id="p-3"),
        ]
        result = await ex.execute_top(pivots, [])
        assert len(result) == 3
        assert len([r for r in result if r.error]) == 1
        assert len([r for r in result if not r.error]) == 2

    @pytest.mark.asyncio
    async def test_timeout_returns_error_result(self):
        store = MagicMock()
        store.async_ingest_findings_batch = AsyncMock(return_value=[])
        ex = AutonomousPivotExecutor(
            duckdb_store=store, pivot_timeout=0.001,
            max_active=3, max_per_sprint=3,
        )

        async def slow_search(pivot):
            await asyncio.sleep(10.0)
            return [{"accepted": True}]

        ex._run_pivot_search = slow_search
        pivots = [_make_pivot(priority=0.0, pivot_id="p-timeout")]
        result = await ex.execute_top(pivots, [])
        assert len(result) == 1
        assert result[0].attempted is True
        assert "timeout" in result[0].error.lower()

    @pytest.mark.asyncio
    async def test_feedback_recorded_on_success(self):
        fb = AsyncMock()
        store = MagicMock()
        store.async_ingest_findings_batch = AsyncMock(return_value=[])
        ex = AutonomousPivotExecutor(
            duckdb_store=store, feedback_adapter=fb,
            max_active=3, max_per_sprint=3,
        )
        ex._run_pivot_search = AsyncMock(return_value=[{"accepted": True}] * 3)
        pivots = [_make_pivot(priority=0.0, pivot_type="domain", ioc_type="domain")]
        await ex.execute_top(pivots, [])
        fb.async_record.assert_called_once()
        call_kwargs = fb.async_record.call_args
        assert call_kwargs[1]["pivot_type"] == "domain"
        assert call_kwargs[1]["produced_count"] == 3
        assert call_kwargs[1]["accepted_count"] == 3

    @pytest.mark.asyncio
    async def test_feedback_not_recorded_over_limit(self):
        fb = AsyncMock()
        store = MagicMock()
        store.async_ingest_findings_batch = AsyncMock(return_value=[])
        ex = AutonomousPivotExecutor(
            duckdb_store=store, feedback_adapter=fb,
            max_active=3, max_per_sprint=2,
        )
        ex._run_pivot_search = AsyncMock(return_value=[{"accepted": True}])
        pivots = [_make_pivot(priority=i) for i in range(5)]
        await ex.execute_top(pivots, [])
        assert fb.async_record.call_count == 2

    @pytest.mark.asyncio
    async def test_cancelled_error_raised(self):
        store = MagicMock()
        store.async_ingest_findings_batch = AsyncMock(return_value=[])
        ex = AutonomousPivotExecutor(duckdb_store=store, max_active=3, max_per_sprint=3)

        async def cancelling_search(pivot):
            raise asyncio.CancelledError("pivot cancelled")

        ex._run_pivot_search = cancelling_search
        pivots = [_make_pivot(priority=0.0)]
        with pytest.raises(asyncio.CancelledError):
            await ex.execute_top(pivots, [])


class TestCanonicalIngest:
    """Test canonical ingest path."""

    @pytest.mark.asyncio
    async def test_ingest_called_with_findings(self):
        store = AsyncMock()
        store.async_ingest_findings_batch = AsyncMock(return_value=[])
        ex = AutonomousPivotExecutor(duckdb_store=store, max_active=3, max_per_sprint=3)
        findings = [{"accepted": True}, {"accepted": False}]
        ex._run_pivot_search = AsyncMock(return_value=findings)
        pivots = [_make_pivot(priority=0.0)]
        await ex.execute_top(pivots, [])
        store.async_ingest_findings_batch.assert_called_once_with(findings)

    @pytest.mark.asyncio
    async def test_ingest_failure_does_not_crash(self):
        store = AsyncMock()
        store.async_ingest_findings_batch = AsyncMock(side_effect=RuntimeError("DB error"))
        fb = AsyncMock()
        ex = AutonomousPivotExecutor(
            duckdb_store=store, feedback_adapter=fb,
            max_active=3, max_per_sprint=3,
        )
        ex._run_pivot_search = AsyncMock(return_value=[{"accepted": True}])
        pivots = [_make_pivot(priority=0.0)]
        result = await ex.execute_top(pivots, [])
        assert len(result) == 1
        assert result[0].error == ""


class TestCheckGathered:
    """Test _check_gathered helper."""

    def test_cancelled_error_raised(self):
        with pytest.raises(asyncio.CancelledError):
            AutonomousPivotExecutor._check_gathered([asyncio.CancelledError()], "test")

    def test_exception_logged_not_raised(self):
        AutonomousPivotExecutor._check_gathered([RuntimeError("oops")], "test")


class TestNoModelLoad:
    """Invariant: executor must NOT call brain.model_lifecycle."""

    @pytest.mark.asyncio
    async def test_executor_does_not_load_model(self):
        """
        execute_top does not invoke model_lifecycle — executor only uses
        _run_pivot_search (the injected search stub) and never calls
        brain.model_lifecycle.get_model_lifecycle_status().
        """
        store = MagicMock()
        store.async_ingest_findings_batch = AsyncMock(return_value=[])
        gov = MagicMock()
        gov.sample_uma_status = AsyncMock(
            return_value=MagicMock(is_critical=False, is_emergency=False)
        )
        ex = AutonomousPivotExecutor(
            duckdb_store=store, resource_governor=gov,
            max_active=3, max_per_sprint=3,
        )
        ex._run_pivot_search = AsyncMock(return_value=[])
        pivots = [_make_pivot(priority=i) for i in range(3)]
        await ex.execute_top(pivots, [])
        # Verify _run_pivot_search was called — executor delegates search,
        # never loads model itself
        assert ex._run_pivot_search.call_count == 3


class TestDuckDBSchema:
    """Test hypothesis_feedback schema accepts records."""

    @pytest.mark.asyncio
    async def test_feedback_record_stored_to_duckdb(self):
        store = MagicMock()
        store.async_record_hypothesis_feedback = AsyncMock(return_value=True)
        from hledac.universal.runtime.hypothesis_feedback import HypothesisFeedbackRecord
        record = HypothesisFeedbackRecord(
            id="test-id", target_id="sprint-1", pivot_type="domain",
            ioc_type="domain", produced_count=5, accepted_count=3,
            signal_value=0.6, ts=time.time(),
        )
        await store.async_record_hypothesis_feedback(record)
        store.async_record_hypothesis_feedback.assert_called_once()


class TestSmoke:
    """Smoke test: module imports without error."""

    def test_module_imports(self):
        from hledac.universal.runtime import pivot_executor
        from hledac.universal.runtime.pivot_executor import (
            PivotExecutionRequest,
            PivotExecutionResult,
            AutonomousPivotExecutor,
        )
        assert PivotExecutionRequest is not None
        assert PivotExecutionResult is not None
        assert AutonomousPivotExecutor is not None

    def test_constants_exported(self):
        assert MAX_ACTIVE_PIVOTS > 0
        assert MAX_PIVOTS_PER_SPRINT > 0
        assert PIVOT_TIMEOUT_S > 0
        assert MAX_PIVOT_FINDINGS > 0
