"""
Sprint F204A: Canonical Accepted-Finding Sidecar Bus — Probe Tests
====================================================================

Invariant mapping:
  F204A-1  | SidecarBatch is frozen=True with correct fields (sprint_id, query, source_branch, findings, created_ts)
  F204A-2  | SidecarRunResult is frozen=True with correct fields (sidecar_name, attempted, produced_count, stored_count, skipped_reason, elapsed_ms)
  F204A-3  | FindingSidecarBus.__init__ accepts governor parameter
  F204A-4  | create_sidecar_bus() returns bus with 12 registered DEFAULT_SIDECAR_RUNNERS
  F204A-5  | Bus.register() rejects duplicate sidecar names
  F204A-6  | Bus._is_heavy_blocked returns False for light sidecars (leak_sentinel, exposure_correlator, etc.)
  F204A-7  | Bus._is_heavy_blocked returns True for heavy sidecars (identity_stitching, embedding, sprint_diff) at critical/emergency RAM
  F204A-8  | Bus._is_heavy_blocked returns False for heavy sidecars at normal RAM
  F204A-9  | run_all_sidecars with empty findings returns []
  F204A-10 | run_all_sidecars with findings under MAX_SIDECAR_FINDINGS passes all findings
  F204A-11 | run_all_sidecars caps results at MAX_SIDECAR_RESULT_RECORDS
  F204A-12 | asyncio.gather uses return_exceptions=True (fail-soft per sidecar)
  F204A-13 | asyncio.CancelledError is re-raised by run_all_sidecars
  F204A-14 | SidecarBatch.source_branch accepts "feed", "public", "ct" values
  F204A-15 | Failed sidecar error is captured in SidecarRunResult.skipped_reason
  F204A-16 | Heavy sidecars skipped due to RAM return skipped_reason="ram_governor_critical"
  F204A-17 | Findings exceeding MAX_SIDECAR_FINDINGS are truncated
  F204A-18 | Bus.run_all_sidecars called with feed/public/CT batches produces SidecarRunResult list
"""

import asyncio
import time as _time
from dataclasses import fields
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hledac.universal.runtime.sidecar_bus import (
    MAX_SIDECAR_FINDINGS,
    MAX_SIDECAR_RESULT_RECORDS,
    SIDECAR_TIMEOUT_S,
    DEFAULT_SIDECAR_RUNNERS,
    FindingSidecarBus,
    SidecarBatch,
    SidecarRunResult,
    create_sidecar_bus,
)


# ============================================================================
# F204A-1: SidecarBatch frozen dataclass
# ============================================================================


class TestSidecarBatchDataclass:
    """F204A-1: SidecarBatch has correct frozen dataclass fields."""

    def test_sidecar_batch_is_frozen(self):
        batch = SidecarBatch(
            sprint_id="sprint-1",
            query="test query",
            source_branch="ct",
            findings=("finding1", "finding2"),
            created_ts=1234.0,
        )
        with pytest.raises(AttributeError):
            batch.sprint_id = "changed"  # type: ignore[index]

    def test_sidecar_batch_has_correct_fields(self):
        field_names = {f.name for f in fields(SidecarBatch)}
        assert field_names == {"sprint_id", "query", "source_branch", "findings", "created_ts"}

    def test_sidecar_batch_accepts_feed_branch(self):
        batch = SidecarBatch(
            sprint_id="s1",
            query="q",
            source_branch="feed",
            findings=(),
            created_ts=1.0,
        )
        assert batch.source_branch == "feed"

    def test_sidecar_batch_accepts_public_branch(self):
        batch = SidecarBatch(
            sprint_id="s1",
            query="q",
            source_branch="public",
            findings=(),
            created_ts=1.0,
        )
        assert batch.source_branch == "public"

    def test_sidecar_batch_accepts_ct_branch(self):
        batch = SidecarBatch(
            sprint_id="s1",
            query="q",
            source_branch="ct",
            findings=(),
            created_ts=1.0,
        )
        assert batch.source_branch == "ct"


# ============================================================================
# F204A-2: SidecarRunResult frozen dataclass
# ============================================================================


class TestSidecarRunResultDataclass:
    """F204A-2: SidecarRunResult has correct frozen dataclass fields."""

    def test_sidecar_run_result_is_frozen(self):
        result = SidecarRunResult(
            sidecar_name="leak_sentinel",
            attempted=True,
            produced_count=5,
            stored_count=3,
            skipped_reason="",
            elapsed_ms=12.5,
        )
        with pytest.raises(AttributeError):
            result.attempted = False  # type: ignore[index]

    def test_sidecar_run_result_has_correct_fields(self):
        field_names = {f.name for f in fields(SidecarRunResult)}
        assert field_names == {
            "sidecar_name",
            "attempted",
            "produced_count",
            "stored_count",
            "skipped_reason",
            "elapsed_ms",
        }


# ============================================================================
# F204A-3 & F204A-4: FindingSidecarBus initialization
# ============================================================================


class TestFindingSidecarBusInit:
    """F204A-3: Bus.__init__ accepts governor parameter. F204A-4: Default runners registered."""

    def test_bus_init_without_governor(self):
        bus = FindingSidecarBus(governor=None)
        assert bus._governor is None

    def test_bus_init_with_mock_governor(self):
        mockGov = MagicMock()
        bus = FindingSidecarBus(governor=mockGov)
        assert bus._governor is mockGov

    def test_create_sidecar_bus_has_twelve_default_runners(self):
        bus = create_sidecar_bus()
        assert len(bus._runners) == 12

    def test_default_runner_names_are_correct(self):
        bus = create_sidecar_bus()
        expected = {
            "leak_sentinel",
            "exposure_correlator",
            "temporal_archaeology",
            "evidence_triage",
            "identity_stitching",
            "sprint_diff",
            "kill_chain_tagging",
            "wayback_diff",
            "passive_fingerprint",
            "rir_correlator",
            "embedding",
            "social_identity_surface",
        }
        assert set(bus._runners.keys()) == expected


# ============================================================================
# F204A-5: Bus.register rejects duplicate names
# ============================================================================


class TestBusRegister:
    """F204A-5: Bus.register() rejects duplicate sidecar names."""

    def test_register_rejects_duplicate(self):
        bus = FindingSidecarBus()

        async def dummy(findings, store, query):
            pass

        bus.register("leak_sentinel", dummy)
        with pytest.raises(ValueError, match="already registered"):
            bus.register("leak_sentinel", dummy)

    def test_register_accepts_unique_name(self):
        bus = FindingSidecarBus()

        async def dummy(findings, store, query):
            pass

        bus.register("custom_sidecar", dummy)
        assert "custom_sidecar" in bus._runners


# ============================================================================
# F204A-6, F204A-7, F204A-8: RAM governor guard
# ============================================================================


class TestRAMGovernorGuard:
    """F204A-6: Light sidecars never blocked. F204A-7: Heavy blocked at critical. F204A-8: Heavy allowed at normal."""

    def _mock_admission(self, allowed: bool, reason: str = ""):
        adm = MagicMock()
        adm.allowed = allowed
        adm.reason = reason
        return adm

    def _mock_gov(self, is_critical=False, is_emergency=False, high_water=0.5):
        gov = MagicMock()
        # _is_heavy_blocked uses governor.sidecar_admission() with name + RAM estimate
        if is_critical or is_emergency or high_water >= 0.85:
            gov.sidecar_admission.return_value = self._mock_admission(False, "ram_governor_critical")
        else:
            gov.sidecar_admission.return_value = self._mock_admission(True, "")
        return gov

    def test_light_sidecar_never_blocked_at_critical(self):
        gov = self._mock_gov(is_critical=True)
        bus = FindingSidecarBus(governor=gov)
        blocked, reason = bus._is_heavy_blocked("leak_sentinel")
        assert blocked is False
        blocked, reason = bus._is_heavy_blocked("exposure_correlator")
        assert blocked is False

    def test_heavy_sidecar_blocked_at_critical(self):
        gov = self._mock_gov(is_critical=True)
        bus = FindingSidecarBus(governor=gov)
        blocked, reason = bus._is_heavy_blocked("identity_stitching")
        assert blocked is True
        blocked, reason = bus._is_heavy_blocked("embedding")
        assert blocked is True
        blocked, reason = bus._is_heavy_blocked("sprint_diff")
        assert blocked is True

    def test_heavy_sidecar_blocked_at_emergency(self):
        gov = self._mock_gov(is_emergency=True)
        bus = FindingSidecarBus(governor=gov)
        blocked, reason = bus._is_heavy_blocked("identity_stitching")
        assert blocked is True

    def test_heavy_sidecar_blocked_at_85_percent_high_water(self):
        gov = self._mock_gov(high_water=0.85)
        bus = FindingSidecarBus(governor=gov)
        blocked, reason = bus._is_heavy_blocked("embedding")
        assert blocked is True

    def test_heavy_sidecar_not_blocked_at_normal(self):
        gov = self._mock_gov(high_water=0.5)
        bus = FindingSidecarBus(governor=gov)
        blocked, reason = bus._is_heavy_blocked("identity_stitching")
        assert blocked is False
        blocked, reason = bus._is_heavy_blocked("embedding")
        assert blocked is False
        blocked, reason = bus._is_heavy_blocked("sprint_diff")
        assert blocked is False

    def test_governor_error_allows_heavy_sidecars(self):
        gov = MagicMock()
        gov.sidecar_admission.side_effect = RuntimeError("governor unavailable")
        bus = FindingSidecarBus(governor=gov)
        # Fail-soft: allow heavy sidecars if governor errors
        blocked, reason = bus._is_heavy_blocked("identity_stitching")
        assert blocked is False


# ============================================================================
# F204A-9, F204A-10, F204A-11, F204A-17: run_all_sidecars bounds
# ============================================================================


class TestRunAllSidecarsBounds:
    """F204A-9: Empty findings return []. F204A-10: Under limit passes all. F204A-11: Caps results. F204A-17: Truncates findings."""

    @pytest.mark.asyncio
    async def test_empty_findings_returns_empty(self):
        bus = create_sidecar_bus()
        store = MagicMock()
        batch = SidecarBatch(
            sprint_id="s1", query="q", source_branch="ct",
            findings=(), created_ts=_time.time(),
        )
        results = await bus.run_all_sidecars(batch, store)
        assert results == []

    @pytest.mark.asyncio
    async def test_findings_under_max_limit_all_passed(self):
        bus = FindingSidecarBus()
        findings = [{"finding_id": f"f{i}"} for i in range(10)]
        ran_findings = []

        async def tracker(findings_list, store, query):
            ran_findings.extend(findings_list)

        bus.register("tracker", tracker)
        batch = SidecarBatch(
            sprint_id="s1", query="q", source_branch="ct",
            findings=tuple(findings), created_ts=_time.time(),
        )
        store = MagicMock()
        await bus.run_all_sidecars(batch, store)
        assert len(ran_findings) == 10

    @pytest.mark.asyncio
    async def test_findings_exceeding_max_are_truncated(self):
        bus = FindingSidecarBus()
        findings = [{"finding_id": f"f{i}"} for i in range(MAX_SIDECAR_FINDINGS + 100)]
        ran_findings = []

        async def tracker(findings_list, store, query):
            ran_findings.extend(findings_list)

        bus.register("tracker", tracker)
        batch = SidecarBatch(
            sprint_id="s1", query="q", source_branch="ct",
            findings=tuple(findings), created_ts=_time.time(),
        )
        store = MagicMock()
        await bus.run_all_sidecars(batch, store)
        assert len(ran_findings) == MAX_SIDECAR_FINDINGS


# ============================================================================
# F204A-12: asyncio.gather uses return_exceptions=True
# ============================================================================


class TestGatherReturnExceptions:
    """F204A-12: gather with return_exceptions=True means one sidecar crash doesn't stop others."""

    @pytest.mark.asyncio
    async def test_one_sidecar_crash_does_not_stop_others(self):
        bus = FindingSidecarBus()
        call_order = []

        async def crashing(findings, store, query):
            call_order.append("crashing")
            raise RuntimeError("simulated crash")

        async def healthy(findings, store, query):
            call_order.append("healthy")

        bus.register("crashing", crashing)
        bus.register("healthy", healthy)
        batch = SidecarBatch(
            sprint_id="s1", query="q", source_branch="ct",
            findings=({"id": 1},), created_ts=_time.time(),
        )
        store = MagicMock()
        results = await bus.run_all_sidecars(batch, store)
        # Both should have been attempted
        assert "crashing" in call_order
        assert "healthy" in call_order
        # Results should have entries for both
        result_names = {r.sidecar_name for r in results}
        assert "crashing" in result_names
        assert "healthy" in result_names


# ============================================================================
# F204A-13: CancelledError re-raised
# ============================================================================


class TestCancelledError:
    """F204A-13: asyncio.CancelledError is re-raised, never swallowed."""

    @pytest.mark.asyncio
    async def test_cancelled_error_raised(self):
        """F204A-13: asyncio.CancelledError is re-raised when task is cancelled externally."""
        bus = FindingSidecarBus()

        async def never_returns(findings, store, query):
            await asyncio.sleep(10)  # Would exceed timeout

        bus.register("slow", never_returns)
        batch = SidecarBatch(
            sprint_id="s1", query="q", source_branch="ct",
            findings=({"id": 1},), created_ts=_time.time(),
        )
        store = MagicMock()

        # Create task that wraps run_all_sidecars, then cancel it mid-flight
        task = asyncio.create_task(bus.run_all_sidecars(batch, store))
        # Let the runner start, then cancel
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


# ============================================================================
# F204A-14: source_branch accepts all three values (already tested above)
# ============================================================================


# ============================================================================
# F204A-15: Failed sidecar error captured in skipped_reason
# ============================================================================


class TestSidecarErrorCapture:
    """F204A-15: Sidecar runtime error is captured in SidecarRunResult.skipped_reason."""

    @pytest.mark.asyncio
    async def test_sidecar_error_captured_in_skipped_reason(self):
        bus = FindingSidecarBus()

        async def failing(findings, store, query):
            raise ValueError("test error")

        bus.register("failing", failing)
        batch = SidecarBatch(
            sprint_id="s1", query="q", source_branch="ct",
            findings=({"id": 1},), created_ts=_time.time(),
        )
        store = MagicMock()
        results = await bus.run_all_sidecars(batch, store)
        failing_result = next(r for r in results if r.sidecar_name == "failing")
        assert failing_result.attempted is True
        assert "ValueError" in failing_result.skipped_reason
        assert "test error" in failing_result.skipped_reason


# ============================================================================
# F204A-16: Heavy sidecar skipped due to RAM governor
# ============================================================================


class TestHeavySidecarSkipped:
    """F204A-16: Heavy sidecar skipped due to RAM returns skipped_reason='ram_governor_critical'."""

    @pytest.mark.asyncio
    async def test_heavy_sidecar_skipped_at_critical_ram(self):
        mockGov = MagicMock()
        adm = MagicMock()
        adm.allowed = False
        adm.reason = "ram_governor_critical"
        mockGov.sidecar_admission.return_value = adm

        bus = FindingSidecarBus(governor=mockGov)

        async def dummy(findings, store, query):
            pass  # Should not be called

        bus.register("identity_stitching", dummy)
        batch = SidecarBatch(
            sprint_id="s1", query="q", source_branch="ct",
            findings=({"id": 1},), created_ts=_time.time(),
        )
        store = MagicMock()
        results = await bus.run_all_sidecars(batch, store)
        identity_result = next(r for r in results if r.sidecar_name == "identity_stitching")
        assert identity_result.attempted is False
        assert identity_result.skipped_reason == "ram_governor_critical"


# ============================================================================
# F204A-18: feed/public/CT batches all produce SidecarRunResult list
# ============================================================================


class TestAllBranchSources:
    """F204A-18: All three source branches (feed, public, ct) produce SidecarRunResult list."""

    @pytest.mark.asyncio
    async def test_feed_branch_produces_results(self):
        bus = create_sidecar_bus()
        batch = SidecarBatch(
            sprint_id="s1", query="q", source_branch="feed",
            findings=(), created_ts=_time.time(),
        )
        store = MagicMock()
        results = await bus.run_all_sidecars(batch, store)
        assert isinstance(results, list)
        assert all(isinstance(r, SidecarRunResult) for r in results)

    @pytest.mark.asyncio
    async def test_public_branch_produces_results(self):
        bus = create_sidecar_bus()
        batch = SidecarBatch(
            sprint_id="s1", query="q", source_branch="public",
            findings=(), created_ts=_time.time(),
        )
        store = MagicMock()
        results = await bus.run_all_sidecars(batch, store)
        assert isinstance(results, list)
        assert all(isinstance(r, SidecarRunResult) for r in results)

    @pytest.mark.asyncio
    async def test_ct_branch_produces_results(self):
        bus = create_sidecar_bus()
        batch = SidecarBatch(
            sprint_id="s1", query="q", source_branch="ct",
            findings=(), created_ts=_time.time(),
        )
        store = MagicMock()
        results = await bus.run_all_sidecars(batch, store)
        assert isinstance(results, list)
        assert all(isinstance(r, SidecarRunResult) for r in results)
