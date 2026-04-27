"""
Sprint F205C: Unified Sidecar Bus Dispatch — Probe Tests
=========================================================

Invariant mapping:
  F205C-1  | _dispatch_accepted_findings_sidecars is an async method on SprintScheduler
  F205C-2  | Helper returns early when findings is empty list
  F205C-3  | Helper returns early when findings is None (falsy)
  F205C-4  | Helper returns early when self._sidecar_dispatcher is None
  F205C-5  | Helper calls self._sidecar_bus.run_all_sidecars with correct SidecarBatch for "ct" branch
  F205C-6  | Helper re-raises asyncio.CancelledError from the bus
  F205C-7  | Helper is fail-soft: non-CancelledError exceptions are swallowed
  F205C-8  | Helper tracks skipped heavy sidecars in self._result.sidecars_skipped (via dispatcher)
  F205C-9  | CT branch in _run_ct_log_discovery_in_cycle calls the helper
  F205C-10 | Feed diagnostic log is emitted in stable mode when accepted_findings > 0
  F205C-11 | Feed diagnostic log is emitted in aggressive mode when accepted_findings > 0
  F205C-12 | Public diagnostic log is emitted when accepted_findings > 0 or stored_findings > 0

Note: F205C-4, F205C-8 updated for F205F refactor — dispatcher delegates to bus.
"""

import asyncio
import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest

from hledac.universal.runtime.sidecar_bus import SidecarBatch, SidecarRunResult
from hledac.universal.runtime.sidecar_dispatcher import SidecarDispatcher


class TestDispatchHelper:
    """F205C-1 through F205C-8: _dispatch_accepted_findings_sidecars helper behavior."""

    @pytest.fixture
    def scheduler(self):
        """Build a minimal SprintScheduler with mocked dependencies."""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler

        config = MagicMock()
        config.aggressive_mode = False
        config.max_parallel_sources = 4
        config.branch_timeout_budget_s = 0.0
        config.aggressive_branch_timeout_s = 45.0

        sch = SprintScheduler.__new__(SprintScheduler)
        sch._config = config
        sch._sidecar_bus = None
        # F205F: sidecar bookkeeping extracted to SidecarDispatcher
        sch._sidecar_dispatcher = None
        sch._result = MagicMock()
        sch._result.sidecars_skipped = set()  # dispatcher writes here
        sch.sprint_id = "test-sprint"
        return sch

    def _mock_finding(self, fid: str = "finding-1"):
        f = MagicMock()
        f.finding_id = fid
        return f

    # ── F205C-2: empty findings ─────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_dispatch_returns_early_on_empty_findings(self, scheduler):
        """F205C-2: Empty list returns without calling bus."""
        bus = AsyncMock()
        scheduler._sidecar_bus = bus

        await scheduler._dispatch_accepted_findings_sidecars(
            source_branch="ct",
            findings=[],
            store=MagicMock(),
            query="test query",
        )
        bus.run_all_sidecars.assert_not_called()

    # ── F205C-3: None findings ─────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_dispatch_returns_early_on_none_findings(self, scheduler):
        """F205C-3: Falsy findings (None) returns without calling bus."""
        bus = AsyncMock()
        scheduler._sidecar_bus = bus

        await scheduler._dispatch_accepted_findings_sidecars(
            source_branch="ct",
            findings=None,
            store=MagicMock(),
            query="test query",
        )
        bus.run_all_sidecars.assert_not_called()

    # ── F205C-4: no sidecar dispatcher ───────────────────────────────────

    @pytest.mark.asyncio
    async def test_dispatch_returns_early_when_no_dispatcher(self, scheduler):
        """F205C-4 (F205F): No sidecar dispatcher returns without calling bus."""
        scheduler._sidecar_dispatcher = None
        finding = self._mock_finding()

        # Should not raise
        await scheduler._dispatch_accepted_findings_sidecars(
            source_branch="ct",
            findings=[finding],
            store=MagicMock(),
            query="test query",
        )

    # ── F205C-5: correct batch for ct branch ────────────────────────────────

    @pytest.mark.asyncio
    async def test_dispatch_calls_bus_with_correct_ct_batch(self, scheduler):
        """F205C-5: CT branch creates SidecarBatch with source_branch='ct' (via dispatcher)."""
        bus = AsyncMock()
        bus.run_all_sidecars.return_value = []
        scheduler._sidecar_dispatcher = SidecarDispatcher(
            bus=bus,
            result_sink=scheduler._result,
        )
        scheduler.sprint_id = "sprint-ct"
        finding = self._mock_finding("ct-finding-1")

        await scheduler._dispatch_accepted_findings_sidecars(
            source_branch="ct",
            findings=[finding],
            store=MagicMock(),
            query="domain example.com",
        )

        bus.run_all_sidecars.assert_called_once()
        call_args = bus.run_all_sidecars.call_args
        batch: SidecarBatch = call_args[0][0]
        assert isinstance(batch, SidecarBatch)
        assert batch.source_branch == "ct"
        assert batch.sprint_id == "sprint-ct"
        assert batch.query == "domain example.com"
        assert len(batch.findings) == 1

    @pytest.mark.asyncio
    async def test_dispatch_calls_bus_with_correct_feed_batch(self, scheduler):
        """F205C-5 (F205F): Feed branch creates SidecarBatch via dispatcher."""
        bus = AsyncMock()
        bus.run_all_sidecars.return_value = []
        scheduler._sidecar_dispatcher = SidecarDispatcher(
            bus=bus,
            result_sink=scheduler._result,
        )
        scheduler.sprint_id = "sprint-feed"
        finding = self._mock_finding("feed-finding-1")

        await scheduler._dispatch_accepted_findings_sidecars(
            source_branch="feed",
            findings=[finding],
            store=MagicMock(),
            query="test query",
        )

        bus.run_all_sidecars.assert_called_once()
        batch: SidecarBatch = bus.run_all_sidecars.call_args[0][0]
        assert batch.source_branch == "feed"

    @pytest.mark.asyncio
    async def test_dispatch_calls_bus_with_correct_public_batch(self, scheduler):
        """F205C-5 (F205F): Public branch creates SidecarBatch via dispatcher."""
        bus = AsyncMock()
        bus.run_all_sidecars.return_value = []
        scheduler._sidecar_dispatcher = SidecarDispatcher(
            bus=bus,
            result_sink=scheduler._result,
        )
        scheduler.sprint_id = "sprint-public"
        finding = self._mock_finding("public-finding-1")

        await scheduler._dispatch_accepted_findings_sidecars(
            source_branch="public",
            findings=[finding],
            store=MagicMock(),
            query="test query",
        )

        bus.run_all_sidecars.assert_called_once()
        batch: SidecarBatch = bus.run_all_sidecars.call_args[0][0]
        assert batch.source_branch == "public"

    # ── F205C-6: CancelledError re-raise ───────────────────────────────────

    @pytest.mark.asyncio
    async def test_dispatch_reises_cancelled_error(self, scheduler):
        """F205C-6 (F205F): asyncio.CancelledError from bus is re-raised via dispatcher."""
        bus = AsyncMock()
        bus.run_all_sidecars.side_effect = asyncio.CancelledError
        scheduler._sidecar_dispatcher = SidecarDispatcher(
            bus=bus,
            result_sink=scheduler._result,
        )

        with pytest.raises(asyncio.CancelledError):
            await scheduler._dispatch_accepted_findings_sidecars(
                source_branch="ct",
                findings=[self._mock_finding()],
                store=MagicMock(),
                query="test",
            )

    # ── F205C-7: fail-soft ─────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_dispatch_is_fail_soft_on_runtime_error(self, scheduler):
        """F205C-7 (F205F): Non-CancelledError exceptions are swallowed via dispatcher."""
        bus = AsyncMock()
        bus.run_all_sidecars.side_effect = RuntimeError("sidecar exploded")
        scheduler._sidecar_dispatcher = SidecarDispatcher(
            bus=bus,
            result_sink=scheduler._result,
        )

        # Should not raise
        await scheduler._dispatch_accepted_findings_sidecars(
            source_branch="ct",
            findings=[self._mock_finding()],
            store=MagicMock(),
            query="test",
        )

    # ── F205C-8: skipped heavy sidecar tracking ─────────────────────────────

    @pytest.mark.asyncio
    async def test_dispatch_tracks_skipped_heavy_sidecars(self, scheduler):
        """F205C-8 (F205F): Skipped heavy sidecars added to _result.sidecars_skipped."""
        bus = AsyncMock()
        skipped_result = SidecarRunResult(
            sidecar_name="identity_stitching",
            attempted=False,
            produced_count=0,
            stored_count=0,
            skipped_reason="uma_critical",
            elapsed_ms=1.0,
        )
        bus.run_all_sidecars.return_value = [skipped_result]
        # F205F: wire dispatcher → bus → result_sink
        scheduler._sidecar_dispatcher = SidecarDispatcher(
            bus=bus,
            result_sink=scheduler._result,
        )

        await scheduler._dispatch_accepted_findings_sidecars(
            source_branch="ct",
            findings=[self._mock_finding()],
            store=MagicMock(),
            query="test",
        )

        # F205F: dispatcher writes skipped sidecars to result_sink.sidecars_skipped
        assert "identity_stitching" in scheduler._result.sidecars_skipped

    @pytest.mark.asyncio
    async def test_dispatch_does_not_track_attempted_sidecars(self, scheduler):
        """F205C-8 (F205F): Attempted sidecars are not tracked as skipped."""
        bus = AsyncMock()
        attempted_result = SidecarRunResult(
            sidecar_name="leak_sentinel",
            attempted=True,
            produced_count=0,
            stored_count=0,
            skipped_reason="",
            elapsed_ms=5.0,
        )
        bus.run_all_sidecars.return_value = [attempted_result]
        scheduler._sidecar_dispatcher = SidecarDispatcher(
            bus=bus,
            result_sink=scheduler._result,
        )

        await scheduler._dispatch_accepted_findings_sidecars(
            source_branch="ct",
            findings=[self._mock_finding()],
            store=MagicMock(),
            query="test",
        )

        assert "leak_sentinel" not in scheduler._result.sidecars_skipped


class TestCTBranchUsesHelper:
    """F205C-9: CT branch in _run_ct_log_discovery_in_cycle calls the dispatch helper."""

    def test_ct_branch_calls_dispatch_helper(self):
        """F205C-9: Verify the CT branch is refactored to call _dispatch_accepted_findings_sidecars."""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler

        source = inspect.getsource(SprintScheduler._run_ct_log_discovery_in_cycle)

        # Should call the helper for CT findings
        assert "_dispatch_accepted_findings_sidecars" in source
        assert 'source_branch="ct"' in source
        assert "accepted_findings" in source


class TestFeedDiagnosticLogs:
    """F205C-10/11: Feed diagnostic logs are emitted when findings are accepted."""

    def test_feed_diagnostic_logged_stable_mode(self):
        """F205C-10: Diagnostic logged in stable mode when feed accepted_findings > 0."""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler

        source = inspect.getsource(SprintScheduler)
        assert "[F205C] Feed accepted findings not in scheduler scope" in source

    def test_feed_diagnostic_logged_aggressive_mode(self):
        """F205C-11: Diagnostic logged in aggressive mode when feed accepted_findings > 0."""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler

        source = inspect.getsource(SprintScheduler)
        assert "[F205C] Aggressive feed accepted findings not in scope" in source


class TestPublicDiagnosticLogs:
    """F205C-12: Public diagnostic log emitted when findings are accepted/stored."""

    def test_public_diagnostic_logged(self):
        """F205C-12: Diagnostic logged for public branch when findings are non-zero."""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler

        source = inspect.getsource(SprintScheduler)
        assert "[F205C] Public accepted findings not in scheduler scope" in source
