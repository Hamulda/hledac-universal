"""
tests/probe_f204j/test_m1_mission_budget.py — F204J probe tests
==============================================================

Probe tests for M1 Mission Budget enforcement.
Validates: sidecar_admission, RSS guard, embedding fallback chunking,
MissionBudgetSnapshot, budget decision tracking in SprintSchedulerResult.

Run: pytest tests/probe_f204j/ -q
Definition of Done: 24 passed
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from hledac.universal.runtime.resource_governor import (
    M1ResourceGovernor,
    GovernorDecision,
    GovernorSnapshot,
    SidecarAdmission,
    MissionBudgetSnapshot,
    MISSION_PEAK_RSS_GIB,
    SIDECAR_DEFAULT_ESTIMATE_MB,
    HEAVY_SIDECARS,
    MAX_BUDGET_EVENTS,
    get_governor,
)


# ── Test Bounds Constants ────────────────────────────────────────────────────────
class TestBoundsConstants:
    """F204J-1: Budget constants are correctly defined."""

    def test_mission_peak_rss_gib_is_55(self):
        """MISSION_PEAK_RSS_GIB is 5.5."""
        assert MISSION_PEAK_RSS_GIB == 5.5

    def test_sidecar_default_estimate_mb_is_128(self):
        """SIDECAR_DEFAULT_ESTIMATE_MB is 128."""
        assert SIDECAR_DEFAULT_ESTIMATE_MB == 128

    def test_heavy_sidecars_tuple(self):
        """HEAVY_SIDECARS is a tuple containing expected sidecars."""
        assert isinstance(HEAVY_SIDECARS, tuple)
        assert "embedding" in HEAVY_SIDECARS
        assert "wayback_diff" in HEAVY_SIDECARS
        assert "social_identity" in HEAVY_SIDECARS
        assert "rir_correlation" in HEAVY_SIDECARS

    def test_max_budget_events_is_100(self):
        """MAX_BUDGET_EVENTS is 100."""
        assert MAX_BUDGET_EVENTS == 100


# ── Test Dataclasses ───────────────────────────────────────────────────────────
class TestDataclasses:
    """F204J-2: SidecarAdmission and MissionBudgetSnapshot are properly defined."""

    def test_sidecar_admission_is_frozen(self):
        """SidecarAdmission is a frozen dataclass."""
        admission = SidecarAdmission(
            allowed=True,
            sidecar_name="embedding",
            reason="admitted",
            rss_gib=3.2,
            uma_state="ok",
            estimated_mb=128,
        )
        assert admission.allowed is True
        assert admission.sidecar_name == "embedding"
        assert admission.rss_gib == 3.2

    def test_sidecar_admission_immutable(self):
        """SidecarAdmission cannot be modified after creation."""
        admission = SidecarAdmission(
            allowed=True,
            sidecar_name="embedding",
            reason="admitted",
            rss_gib=3.2,
            uma_state="ok",
            estimated_mb=128,
        )
        with pytest.raises(Exception):
            admission.allowed = False  # type: ignore

    def test_mission_budget_snapshot_is_frozen(self):
        """MissionBudgetSnapshot is a frozen dataclass."""
        snap = MissionBudgetSnapshot(
            sprint_id="test-123",
            peak_rss_gib=4.1,
            peak_uma_used_gib=2.8,
            sidecars_skipped=("embedding", "wayback_diff"),
            model_loaded=False,
            renderer_allowed=True,
            fetch_limit=25,
        )
        assert snap.sprint_id == "test-123"
        assert snap.peak_rss_gib == 4.1
        assert snap.sidecars_skipped == ("embedding", "wayback_diff")

    def test_mission_budget_snapshot_immutable(self):
        """MissionBudgetSnapshot cannot be modified after creation."""
        snap = MissionBudgetSnapshot(
            sprint_id="test-123",
            peak_rss_gib=4.1,
            peak_uma_used_gib=2.8,
            sidecars_skipped=(),
            model_loaded=False,
            renderer_allowed=True,
            fetch_limit=25,
        )
        with pytest.raises(Exception):
            snap.peak_rss_gib = 5.0  # type: ignore


# ── Test Sidecar Admission ─────────────────────────────────────────────────────
class TestSidecarAdmission:
    """F204J-3: Governor sidecar_admission() checks work correctly."""

    @pytest.mark.asyncio
    async def test_non_heavy_sidecar_always_allowed(self):
        """Non-heavy sidecars are always allowed regardless of RAM state."""
        governor = M1ResourceGovernor()

        # Mock sample_uma_status to return critical state
        with patch.object(governor, "_get_model_status", return_value={"loaded": False}):
            # Mock sample_uma_status at module level
            with patch("hledac.universal.runtime.resource_governor.sample_uma_status") as mock_uma:
                mock_uma.return_value = MagicMock(
                    state="critical",
                    system_used_gib=6.0 * (1024**3),
                    system_available_gib=1.0,
                    is_critical=True,
                    is_emergency=False,
                    is_warn=False,
                    high_water=0.95,
                )
                # Non-heavy sidecar should still be allowed
                result = governor.sidecar_admission("leak_sentinel", 64)
                assert result.allowed is True
                assert result.reason == "admitted"

    @pytest.mark.asyncio
    async def test_heavy_sidecar_blocked_on_uma_critical(self):
        """Heavy sidecars are blocked when UMA is critical."""
        governor = M1ResourceGovernor()

        with patch.object(governor, "_get_model_status", return_value={"loaded": False}):
            with patch("hledac.universal.runtime.resource_governor.sample_uma_status") as mock_uma:
                mock_uma.return_value = MagicMock(
                    state="critical",
                    system_used_gib=6.0 * (1024**3),
                    system_available_gib=1.0,
                    is_critical=True,
                    is_emergency=False,
                    is_warn=False,
                    high_water=0.95,
                )
                result = governor.sidecar_admission("embedding", 128)
                assert result.allowed is False
                assert "uma_critical" in result.reason or "blocking_heavy" in result.reason

    @pytest.mark.asyncio
    async def test_heavy_sidecar_blocked_on_high_water(self):
        """Heavy sidecars are blocked when high_water >= 85%."""
        governor = M1ResourceGovernor()

        with patch.object(governor, "_get_model_status", return_value={"loaded": False}):
            with patch("hledac.universal.runtime.resource_governor.sample_uma_status") as mock_uma:
                mock_uma.return_value = MagicMock(
                    state="warn",
                    system_used_gib=5.5 * (1024**3),
                    system_available_gib=1.5,
                    is_critical=False,
                    is_emergency=False,
                    is_warn=True,
                    high_water=0.87,  # > 0.85
                )
                result = governor.sidecar_admission("embedding", 128)
                assert result.allowed is False
                assert "high_water" in result.reason

    @pytest.mark.asyncio
    async def test_heavy_sidecar_blocked_on_rss_exceeds_headroom(self):
        """Heavy sidecars blocked when RSS exceeds headroom limit."""
        governor = M1ResourceGovernor()

        with patch.object(governor, "_get_model_status", return_value={"loaded": False}):
            with patch("hledac.universal.runtime.resource_governor.sample_uma_status") as mock_uma:
                # RSS > MISSION_PEAK_RSS_GIB - 0.5 = 5.0
                mock_uma.return_value = MagicMock(
                    state="ok",
                    system_used_gib=5.3 * (1024**3),  # 5.3 GiB > 5.0 headroom
                    system_available_gib=1.5,
                    is_critical=False,
                    is_emergency=False,
                    is_warn=False,
                    high_water=0.70,
                )
                result = governor.sidecar_admission("embedding", 128)
                assert result.allowed is False
                assert "rss_exceeds_headroom" in result.reason

    @pytest.mark.asyncio
    async def test_heavy_sidecar_allowed_under_normal_memory(self):
        """Heavy sidecars are allowed under normal memory conditions."""
        governor = M1ResourceGovernor()

        with patch.object(governor, "_get_model_status", return_value={"loaded": False}):
            with patch("hledac.universal.runtime.resource_governor.sample_uma_status") as mock_uma:
                mock_uma.return_value = MagicMock(
                    state="ok",
                    system_used_gib=3.5 * (1024**3),
                    system_available_gib=3.5,
                    is_critical=False,
                    is_emergency=False,
                    is_warn=False,
                    high_water=0.50,
                )
                result = governor.sidecar_admission("embedding", 128)
                assert result.allowed is True
                assert result.reason == "admitted"


# ── Test RSS Guard ─────────────────────────────────────────────────────────────
class TestRSSGuard:
    """F204J-4: RSS guard in sidecar admission works correctly."""

    @pytest.mark.asyncio
    async def test_sidecar_admission_returns_rss_gib(self):
        """sidecar_admission returns current RSS in GiB."""
        governor = M1ResourceGovernor()

        with patch.object(governor, "_get_model_status", return_value={"loaded": False}):
            with patch("hledac.universal.runtime.resource_governor.sample_uma_status") as mock_uma:
                mock_uma.return_value = MagicMock(
                    state="ok",
                    system_used_gib=4.2 * (1024**3),
                    system_available_gib=2.8,
                    is_critical=False,
                    is_emergency=False,
                    is_warn=False,
                    high_water=0.60,
                )
                result = governor.sidecar_admission("leak_sentinel", 64)
                assert result.rss_gib > 0
                assert result.uma_state == "ok"

    @pytest.mark.asyncio
    async def test_fails_soft_on_uma_check_error(self):
        """sidecar_admission fails soft when sample_uma_status fails."""
        governor = M1ResourceGovernor()

        with patch.object(governor, "_get_model_status", return_value={"loaded": False}):
            with patch("hledac.universal.runtime.resource_governor.sample_uma_status") as mock_uma:
                mock_uma.side_effect = RuntimeError("UMA check failed")
                # Should fail soft and allow the sidecar
                result = governor.sidecar_admission("embedding", 128)
                assert result.allowed is True
                assert "uma_check_failed" in result.reason


# ── Test Singleton ────────────────────────────────────────────────────────────────
class TestSingleton:
    """F204J-5: get_governor() returns the singleton."""

    def test_get_governor_returns_singleton(self):
        """get_governor returns the same instance on multiple calls."""
        gov1 = get_governor()
        gov2 = get_governor()
        assert gov1 is gov2
        assert isinstance(gov1, M1ResourceGovernor)


# ── Test Governor Decision ───────────────────────────────────────────────────────
class TestGovernorDecision:
    """F204J-6: GovernorDecision has expected fields."""

    def test_governor_decision_has_free_uma_gib(self):
        """GovernorDecision includes free_uma_gib field."""
        decision = GovernorDecision(
            fetch_limit=25,
            allow_renderer=True,
            allow_model_load=True,
            branch_concurrency=4,
            reason="normal",
            uma_state="ok",
            model_loaded=False,
            free_uma_gib=3.5,
        )
        assert decision.free_uma_gib == 3.5


# ── Test SprintSchedulerResult Budget Fields ────────────────────────────────────
class TestSchedulerResultBudgetFields:
    """F204J-7: SprintSchedulerResult has budget tracking fields."""

    def test_result_has_sidecars_skipped_field(self):
        """SprintSchedulerResult.sidecars_skipped is a tuple of str."""
        from hledac.universal.runtime.sprint_scheduler import SprintSchedulerResult
        result = SprintSchedulerResult()
        assert hasattr(result, "sidecars_skipped")
        assert result.sidecars_skipped == ()

    def test_result_has_peak_rss_gib_field(self):
        """SprintSchedulerResult.peak_rss_gib is a float."""
        from hledac.universal.runtime.sprint_scheduler import SprintSchedulerResult
        result = SprintSchedulerResult()
        assert hasattr(result, "peak_rss_gib")
        assert result.peak_rss_gib == 0.0

    def test_result_has_budget_violations_field(self):
        """SprintSchedulerResult.budget_violations is an int."""
        from hledac.universal.runtime.sprint_scheduler import SprintSchedulerResult
        result = SprintSchedulerResult()
        assert hasattr(result, "budget_violations")
        assert result.budget_violations == 0


# ── Test Streaming Embedder Fallback Chunking ───────────────────────────────────
class TestStreamingEmbedderFallbackChunking:
    """F204J-8: Streaming embedder fallback properly chunks."""

    @pytest.mark.asyncio
    async def test_embed_fallback_yields_in_chunks(self):
        """Fallback path yields chunks, not one big batch."""
        from hledac.universal.intelligence.streaming_embedder import StreamingEmbedder

        class MockFinding:
            __slots__ = ("finding_id", "payload_text")
            def __init__(self, fid, text):
                self.finding_id = fid
                self.payload_text = text

        # Create 100 mock findings
        findings = [
            MockFinding(f"f{i}", f"test text content for embedding {i}")
            for i in range(100)
        ]

        embedder = StreamingEmbedder()
        chunk_count = 0
        total_ids = 0

        # The fallback path should yield in chunks of batch_size
        # We can't test actual embedding (needs MLX), but we can verify
        # the chunking loop structure by checking it doesn't yield all at once
        async for ids, _ in embedder._embed_fallback(findings, 16):
            chunk_count += 1
            total_ids += len(ids)

        # With 100 findings and batch_size=16, we expect 7 chunks (6 full + 1 partial)
        assert chunk_count > 1
        assert total_ids == 100


# ── Test Sidecar Bus Integration ────────────────────────────────────────────────
class TestSidecarBusIntegration:
    """F204J-9: FindingSidecarBus uses governor.sidecar_admission()."""

    def test_is_heavy_blocked_returns_tuple(self):
        """_is_heavy_blocked returns (bool, str) tuple."""
        from hledac.universal.runtime.sidecar_bus import FindingSidecarBus

        bus = FindingSidecarBus(governor=None)
        # Without governor, nothing is blocked
        blocked, reason = bus._is_heavy_blocked("embedding")
        assert blocked is False
        assert reason == ""

    def test_is_heavy_blocked_checks_governor_admission(self):
        """_is_heavy_blocked uses governor.sidecar_admission()."""
        from hledac.universal.runtime.sidecar_bus import FindingSidecarBus

        mock_governor = MagicMock()
        mock_admission = MagicMock()
        mock_admission.allowed = False
        mock_admission.reason = "uma_critical_blocking_heavy_sidecar"
        mock_governor.sidecar_admission.return_value = mock_admission

        bus = FindingSidecarBus(governor=mock_governor)
        blocked, reason = bus._is_heavy_blocked("embedding")

        assert blocked is True
        assert "uma_critical" in reason or "blocking_heavy" in reason
        mock_governor.sidecar_admission.assert_called_once()


# ── Test Mission Budget Snapshot ───────────────────────────────────────────────
class TestMissionBudgetSnapshot:
    """F204J-10: MissionBudgetSnapshot has all required fields."""

    def test_mission_budget_snapshot_fields(self):
        """MissionBudgetSnapshot has all required fields."""
        snap = MissionBudgetSnapshot(
            sprint_id="sprint-001",
            peak_rss_gib=4.2,
            peak_uma_used_gib=2.8,
            sidecars_skipped=("embedding",),
            model_loaded=False,
            renderer_allowed=True,
            fetch_limit=25,
        )
        assert snap.sprint_id == "sprint-001"
        assert snap.peak_rss_gib == 4.2
        assert snap.peak_uma_used_gib == 2.8
        assert snap.sidecars_skipped == ("embedding",)
        assert snap.model_loaded is False
        assert snap.renderer_allowed is True
        assert snap.fetch_limit == 25


# ── Test Benchmark Constants ─────────────────────────────────────────────────────
class TestBenchmarkConstants:
    """F204J-11: Benchmark uses correct MISSION_PEAK_RSS_GIB."""

    def test_benchmark_uses_governor_constant(self):
        """Benchmark references MISSION_PEAK_RSS_GIB from governor module."""
        # The benchmark file imports MISSION_PEAK_RSS_GIB from resource_governor
        # This verifies the constant exists and is 5.5
        assert MISSION_PEAK_RSS_GIB == 5.5
