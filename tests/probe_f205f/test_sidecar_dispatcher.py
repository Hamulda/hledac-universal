"""
Sprint F205F: Sidecar Dispatcher Extraction — Probe Tests
==========================================================

Tests SidecarDispatcher directly, verifying parity with the original
SprintScheduler._dispatch_accepted_findings_sidecars helper (F205C).

Invariant mapping:
  F205F-1  | dispatch() returns early when findings is empty list
  F205F-2  | dispatch() returns early when findings is None (falsy)
  F205F-3  | dispatch() returns early when store is None
  F205F-4  | dispatch() returns early when bus is None
  F205F-5  | dispatch() calls bus.run_all_sidecars with correct SidecarBatch for "ct"
  F205F-6  | dispatch() calls bus.run_all_sidecars with correct SidecarBatch for "feed"
  F205F-7  | dispatch() calls bus.run_all_sidecars with correct SidecarBatch for "public"
  F205F-8  | dispatch() re-raises asyncio.CancelledError
  F205F-9  | dispatch() is fail-soft: non-CancelledError exceptions are swallowed
  F205F-10 | Skipped heavy sidecars (uma_/high_water/rss_exceeds) added to _sidecars_skipped
  F205F-11 | Skipped non-heavy sidecars NOT added to _sidecars_skipped
  F205F-12 | Skipped heavy sidecars returned in DispatchOutcome (canonical path)
  F205F-13 | reset() clears _sidecars_skipped
  F205F-14 | DispatchOutcome returned with correct sprint_id and source_branch
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from hledac.universal.runtime.sidecar_bus import SidecarBatch, SidecarRunResult
from hledac.universal.runtime.sidecar_dispatcher import DispatchOutcome, SidecarDispatcher


# ── Fixtures ───────────────────────────────────────────────────────────────────

class TestDispatchOutcome:
    """Sanity check on DispatchOutcome dataclass."""

    def test_dispatch_outcome_fields(self):
        outcome = DispatchOutcome(
            sprint_id="sprint-1",
            source_branch="ct",
            sidecars_skipped=("identity_stitching",),
        )
        assert outcome.sprint_id == "sprint-1"
        assert outcome.source_branch == "ct"
        assert outcome.sidecars_skipped == ("identity_stitching",)


class TestSidecarDispatcher:
    """F205F-1 through F205F-14: SidecarDispatcher.dispatch() behavior."""

    def _mock_finding(self, fid: str = "finding-1"):
        f = MagicMock()
        f.finding_id = fid
        return f

    # ── F205F-1: empty findings ─────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_dispatch_returns_early_on_empty_findings(self):
        """F205F-1: Empty list returns DispatchOutcome with empty skips."""
        bus = AsyncMock()
        dispatcher = SidecarDispatcher(bus=bus)

        outcome = await dispatcher.dispatch(
            source_branch="ct",
            findings=[],
            store=MagicMock(),
            query="test",
            sprint_id="sprint-1",
        )

        bus.run_all_sidecars.assert_not_called()
        assert outcome.sidecars_skipped == ()
        assert outcome.source_branch == "ct"

    # ── F205F-2: None findings ─────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_dispatch_returns_early_on_none_findings(self):
        """F205F-2: Falsy findings (None) returns without calling bus."""
        bus = AsyncMock()
        dispatcher = SidecarDispatcher(bus=bus)

        outcome = await dispatcher.dispatch(
            source_branch="ct",
            findings=None,
            store=MagicMock(),
            query="test",
            sprint_id="sprint-1",
        )

        bus.run_all_sidecars.assert_not_called()
        assert outcome.sidecars_skipped == ()

    # ── F205F-3: None store ───────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_dispatch_returns_early_on_none_store(self):
        """F205F-3: None store returns without calling bus."""
        bus = AsyncMock()
        dispatcher = SidecarDispatcher(bus=bus)

        outcome = await dispatcher.dispatch(
            source_branch="ct",
            findings=[MagicMock()],
            store=None,
            query="test",
            sprint_id="sprint-1",
        )

        bus.run_all_sidecars.assert_not_called()
        assert outcome.sidecars_skipped == ()

    # ── F205F-4: None bus ─────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_dispatch_returns_early_when_no_bus(self):
        """F205F-4: No bus returns without calling anything."""
        dispatcher = SidecarDispatcher(bus=None)

        outcome = await dispatcher.dispatch(
            source_branch="ct",
            findings=[MagicMock()],
            store=MagicMock(),
            query="test",
            sprint_id="sprint-1",
        )

        assert outcome.sidecars_skipped == ()

    # ── F205F-5/6/7: correct batch per branch ─────────────────────────────

    @pytest.mark.asyncio
    async def test_dispatch_calls_bus_with_correct_ct_batch(self):
        """F205F-5: CT branch creates SidecarBatch with source_branch='ct'."""
        bus = AsyncMock()
        bus.run_all_sidecars.return_value = []
        dispatcher = SidecarDispatcher(bus=bus)

        await dispatcher.dispatch(
            source_branch="ct",
            findings=[self._mock_finding("ct-finding-1")],
            store=MagicMock(),
            query="domain example.com",
            sprint_id="sprint-ct",
        )

        bus.run_all_sidecars.assert_called_once()
        batch: SidecarBatch = bus.run_all_sidecars.call_args[0][0]
        assert isinstance(batch, SidecarBatch)
        assert batch.source_branch == "ct"
        assert batch.sprint_id == "sprint-ct"
        assert batch.query == "domain example.com"
        assert len(batch.findings) == 1

    @pytest.mark.asyncio
    async def test_dispatch_calls_bus_with_correct_feed_batch(self):
        """F205F-6: Feed branch creates SidecarBatch with source_branch='feed'."""
        bus = AsyncMock()
        bus.run_all_sidecars.return_value = []
        dispatcher = SidecarDispatcher(bus=bus)

        await dispatcher.dispatch(
            source_branch="feed",
            findings=[self._mock_finding("feed-finding-1")],
            store=MagicMock(),
            query="test",
            sprint_id="sprint-feed",
        )

        bus.run_all_sidecars.assert_called_once()
        batch: SidecarBatch = bus.run_all_sidecars.call_args[0][0]
        assert batch.source_branch == "feed"

    @pytest.mark.asyncio
    async def test_dispatch_calls_bus_with_correct_public_batch(self):
        """F205F-7: Public branch creates SidecarBatch with source_branch='public'."""
        bus = AsyncMock()
        bus.run_all_sidecars.return_value = []
        dispatcher = SidecarDispatcher(bus=bus)

        await dispatcher.dispatch(
            source_branch="public",
            findings=[self._mock_finding("public-finding-1")],
            store=MagicMock(),
            query="test",
            sprint_id="sprint-public",
        )

        bus.run_all_sidecars.assert_called_once()
        batch: SidecarBatch = bus.run_all_sidecars.call_args[0][0]
        assert batch.source_branch == "public"

    # ── F205F-8: CancelledError re-raise ───────────────────────────────────

    @pytest.mark.asyncio
    async def test_dispatch_reraises_cancelled_error(self):
        """F205F-8: asyncio.CancelledError from bus is re-raised."""
        bus = AsyncMock()
        bus.run_all_sidecars.side_effect = asyncio.CancelledError
        dispatcher = SidecarDispatcher(bus=bus)

        with pytest.raises(asyncio.CancelledError):
            await dispatcher.dispatch(
                source_branch="ct",
                findings=[self._mock_finding()],
                store=MagicMock(),
                query="test",
                sprint_id="sprint-1",
            )

    # ── F205F-9: fail-soft ────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_dispatch_is_fail_soft_on_runtime_error(self):
        """F205F-9: Non-CancelledError exceptions are swallowed."""
        bus = AsyncMock()
        bus.run_all_sidecars.side_effect = RuntimeError("sidecar exploded")
        dispatcher = SidecarDispatcher(bus=bus)

        # Should not raise
        outcome = await dispatcher.dispatch(
            source_branch="ct",
            findings=[self._mock_finding()],
            store=MagicMock(),
            query="test",
            sprint_id="sprint-1",
        )

        assert outcome.sidecars_skipped == ()

    # ── F205F-10: skipped heavy sidecar tracking ─────────────────────────

    @pytest.mark.asyncio
    async def test_dispatch_tracks_skipped_heavy_sidecars(self):
        """F205F-10: uma_/high_water/rss_exceeds skipped reason → tracked."""
        bus = AsyncMock()
        bus.run_all_sidecars.return_value = [
            SidecarRunResult(
                sidecar_name="identity_stitching",
                attempted=False,
                produced_count=0,
                stored_count=0,
                skipped_reason="uma_critical",
                elapsed_ms=1.0,
            ),
            SidecarRunResult(
                sidecar_name="embedding",
                attempted=False,
                produced_count=0,
                stored_count=0,
                skipped_reason="high_water",
                elapsed_ms=2.0,
            ),
        ]
        dispatcher = SidecarDispatcher(bus=bus)

        outcome = await dispatcher.dispatch(
            source_branch="ct",
            findings=[self._mock_finding()],
            store=MagicMock(),
            query="test",
            sprint_id="sprint-1",
        )

        assert "identity_stitching" in dispatcher._sidecars_skipped
        assert "embedding" in dispatcher._sidecars_skipped
        assert "identity_stitching" in outcome.sidecars_skipped
        assert "embedding" in outcome.sidecars_skipped

    # ── F205F-11: non-heavy not tracked ────────────────────────────────────

    @pytest.mark.asyncio
    async def test_dispatch_does_not_track_non_heavy_skipped(self):
        """F205F-11: Skipped sidecar with non-RAM reason is not tracked."""
        bus = AsyncMock()
        bus.run_all_sidecars.return_value = [
            SidecarRunResult(
                sidecar_name="leak_sentinel",
                attempted=False,
                produced_count=0,
                stored_count=0,
                skipped_reason="not_registered",
                elapsed_ms=0.5,
            ),
        ]
        dispatcher = SidecarDispatcher(bus=bus)

        outcome = await dispatcher.dispatch(
            source_branch="ct",
            findings=[self._mock_finding()],
            store=MagicMock(),
            query="test",
            sprint_id="sprint-1",
        )

        assert "leak_sentinel" not in dispatcher._sidecars_skipped
        assert "leak_sentinel" not in outcome.sidecars_skipped

    # ── F205F-12: skipped sidecars only in DispatchOutcome (not result_sink) ───

    @pytest.mark.asyncio
    async def test_dispatch_returns_skipped_in_outcome(self):
        """F205F-12: Skipped heavy sidecars returned in DispatchOutcome (canonical path)."""
        bus = AsyncMock()
        bus.run_all_sidecars.return_value = [
            SidecarRunResult(
                sidecar_name="sprint_diff",
                attempted=False,
                produced_count=0,
                stored_count=0,
                skipped_reason="rss_exceeds",
                elapsed_ms=1.0,
            ),
        ]
        dispatcher = SidecarDispatcher(bus=bus)

        outcome = await dispatcher.dispatch(
            source_branch="ct",
            findings=[self._mock_finding()],
            store=MagicMock(),
            query="test",
            sprint_id="sprint-1",
        )

        assert "sprint_diff" in outcome.sidecars_skipped

    # ── F205F-13: reset clears tracking ───────────────────────────────────

    @pytest.mark.asyncio
    async def test_reset_clears_sidecars_skipped(self):
        """F205F-13: reset() clears _sidecars_skipped."""
        bus = AsyncMock()
        bus.run_all_sidecars.return_value = [
            SidecarRunResult(
                sidecar_name="embedding",
                attempted=False,
                produced_count=0,
                stored_count=0,
                skipped_reason="uma_emergency",
                elapsed_ms=1.0,
            ),
        ]
        dispatcher = SidecarDispatcher(bus=bus)

        await dispatcher.dispatch(
            source_branch="ct",
            findings=[self._mock_finding()],
            store=MagicMock(),
            query="test",
            sprint_id="sprint-1",
        )

        assert "embedding" in dispatcher._sidecars_skipped

        dispatcher.reset()

        assert len(dispatcher._sidecars_skipped) == 0

    # ── F205F-14: DispatchOutcome fields ──────────────────────────────────

    @pytest.mark.asyncio
    async def test_dispatch_returns_dispatch_outcome(self):
        """F205F-14: DispatchOutcome has correct sprint_id and source_branch."""
        bus = AsyncMock()
        bus.run_all_sidecars.return_value = []
        dispatcher = SidecarDispatcher(bus=bus)

        outcome = await dispatcher.dispatch(
            source_branch="public",
            findings=[self._mock_finding()],
            store=MagicMock(),
            query="target query",
            sprint_id="sprint-public-42",
        )

        assert isinstance(outcome, DispatchOutcome)
        assert outcome.sprint_id == "sprint-public-42"
        assert outcome.source_branch == "public"
        assert outcome.sidecars_skipped == ()

    # ── Attempted sidecars not tracked as skipped ──────────────────────────

    @pytest.mark.asyncio
    async def test_attempted_sidecar_not_tracked(self):
        """Attempted sidecars (even with error reason) are not in skipped set."""
        bus = AsyncMock()
        bus.run_all_sidecars.return_value = [
            SidecarRunResult(
                sidecar_name="leak_sentinel",
                attempted=True,
                produced_count=0,
                stored_count=0,
                skipped_reason="",
                elapsed_ms=5.0,
            ),
        ]
        dispatcher = SidecarDispatcher(bus=bus)

        outcome = await dispatcher.dispatch(
            source_branch="ct",
            findings=[self._mock_finding()],
            store=MagicMock(),
            query="test",
            sprint_id="sprint-1",
        )

        assert "leak_sentinel" not in dispatcher._sidecars_skipped
        assert outcome.sidecars_skipped == ()
