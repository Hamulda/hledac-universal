"""
tests/probe_f202j/test_m1_resource_governor.py

Probe tests for M1ResourceGovernor advisory safety layer.

Invariant table:
  Invariant                                                  | Test method
  ───────────────────────────────────────────────────────────────────────
  model_loaded path → fetch_limit=3                         | test_governor_sets_fetch_limit_3_when_model_loaded
  model_unloaded path → fetch_limit=25                        | test_governor_restores_fetch_limit_25_when_model_unloaded
  no_model_plus_renderer_concurrently                       | test_no_renderer_when_model_loaded
  advisory_only_fails_soft                                   | test_advisory_fails_soft
  GovernorDecision has correct fields                        | test_governor_decision_fields
  snapshot() returns GovernorSnapshot                        | test_snapshot_returns_governor_snapshot
  evaluate() is async and returns GovernorDecision           | test_evaluate_is_async
  get_governor() returns singleton                           | test_get_governor_singleton
"""


import pytest
from unittest.mock import AsyncMock, patch, MagicMock


class TestM1ResourceGovernor:
    """F202J: M1ResourceGovernor probe tests."""

    @pytest.fixture
    def governor(self):
        """Create a fresh governor instance per test."""
        from hledac.universal.runtime.resource_governor import M1ResourceGovernor
        return M1ResourceGovernor()

    # ── Invariant: model_loaded path → fetch_limit=3 ──────────────────────

    @pytest.mark.asyncio
    async def test_governor_sets_fetch_limit_3_when_model_loaded(self, governor):
        """
        F202J-1: When model is loaded, fetch_limit must be 3.

        Evidence: model_lifecycle.get_model_lifecycle_status() returns loaded=True
        → governor.evaluate() returns fetch_limit=3.
        """
        with patch.object(governor, "_get_model_status", return_value={"loaded": True, "current_model": "hermes", "initialized": True, "last_error": None}):
            with patch("hledac.universal.runtime.resource_governor.sample_uma_status") as mock_uma:
                mock_uma.return_value = MagicMock(state="ok", system_used_gib=5.0, io_only=False)
                decision = await governor.evaluate()
                assert decision.fetch_limit == 3, "model_loaded → fetch_limit must be 3"
                assert decision.model_loaded is True

    # ── Invariant: model_unloaded path → fetch_limit=25 ───────────────────

    @pytest.mark.asyncio
    async def test_governor_restores_fetch_limit_25_when_model_unloaded(self, governor):
        """
        F202J-2: When model is unloaded, fetch_limit must be 25 (default).

        Evidence: model_lifecycle.get_model_lifecycle_status() returns loaded=False
        → governor.evaluate() returns fetch_limit=25.
        """
        with patch.object(governor, "_get_model_status", return_value={"loaded": False, "current_model": None, "initialized": False, "last_error": None}):
            with patch("hledac.universal.runtime.resource_governor.sample_uma_status") as mock_uma:
                mock_uma.return_value = MagicMock(state="ok", system_used_gib=5.0, io_only=False)
                decision = await governor.evaluate()
                assert decision.fetch_limit == 25, "model_unloaded → fetch_limit must be 25"

    # ── Invariant: no model + JS renderer concurrently ─────────────────────

    @pytest.mark.asyncio
    async def test_no_renderer_when_model_loaded(self, governor):
        """
        F202J-3: Model loaded → renderer must be denied (allow_renderer=False).

        This is the core M1 constraint: model + JS renderer never concurrently.
        """
        with patch.object(governor, "_get_model_status", return_value={"loaded": True, "current_model": "hermes", "initialized": True, "last_error": None}):
            with patch("hledac.universal.runtime.resource_governor.sample_uma_status") as mock_uma:
                mock_uma.return_value = MagicMock(state="ok", system_used_gib=5.0, io_only=False)
                decision = await governor.evaluate()
                assert decision.allow_renderer is False, "model_loaded → renderer denied"

    # ── Invariant: advisory only, fails soft ────────────────────────────────

    @pytest.mark.asyncio
    async def test_advisory_fails_soft(self, governor):
        """
        F202J-4: Governor fails soft — no exceptions propagate from evaluate().

        If model_lifecycle or sample_uma_status throws, evaluate() completes
        without raising. Returns a GovernorDecision (never None).
        """
        # Fail on model status lookup → should NOT raise
        with patch.object(governor, "_get_model_status", side_effect=RuntimeError("model synthetic")):
            with patch("hledac.universal.runtime.resource_governor.sample_uma_status") as mock_uma:
                mock_uma.return_value = MagicMock(state="ok", system_used_gib=5.0, io_only=False)
                decision = await governor.evaluate()
                assert decision is not None
                assert isinstance(decision.fetch_limit, int)
                assert decision.branch_concurrency >= 1
        # Fail on uma status → should NOT raise
        with patch("hledac.universal.runtime.resource_governor.sample_uma_status", side_effect=RuntimeError("uma synthetic")):
            decision = await governor.evaluate()
            assert decision is not None
            assert isinstance(decision.fetch_limit, int)
            assert decision.branch_concurrency >= 1

    # ── GovernorDecision fields ───────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_governor_decision_fields(self, governor):
        """
        F202J-5: GovernorDecision has all required fields.
        """
        with patch.object(governor, "_get_model_status", return_value={"loaded": False, "current_model": None, "initialized": False, "last_error": None}):
            with patch("hledac.universal.runtime.resource_governor.sample_uma_status") as mock_uma:
                mock_uma.return_value = MagicMock(state="ok", system_used_gib=5.0, io_only=False)
                decision = await governor.evaluate()
                assert hasattr(decision, "fetch_limit")
                assert hasattr(decision, "allow_renderer")
                assert hasattr(decision, "allow_model_load")
                assert hasattr(decision, "branch_concurrency")
                assert hasattr(decision, "reason")
                assert hasattr(decision, "uma_state")
                assert hasattr(decision, "model_loaded")
                assert hasattr(decision, "renderer_denied_count")
                assert hasattr(decision, "model_denied_count")

    # ── snapshot() ────────────────────────────────────────────────────────

    def test_snapshot_returns_governor_snapshot(self, governor):
        """
        F202J-6: snapshot() returns GovernorSnapshot dataclass.
        """
        snap = governor.snapshot()
        assert hasattr(snap, "uma_state")
        assert hasattr(snap, "model_loaded")
        assert hasattr(snap, "fetch_limit")
        assert hasattr(snap, "branch_concurrency")
        assert hasattr(snap, "renderer_denied_count")
        assert hasattr(snap, "model_denied_count")

    # ── evaluate() is async ────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_evaluate_is_async(self, governor):
        """
        F202J-7: evaluate() is async and returns GovernorDecision.
        """
        with patch.object(governor, "_get_model_status", return_value={"loaded": False, "current_model": None, "initialized": False, "last_error": None}):
            with patch("hledac.universal.runtime.resource_governor.sample_uma_status") as mock_uma:
                mock_uma.return_value = MagicMock(state="ok", system_used_gib=5.0, io_only=False)
                decision = await governor.evaluate()
                from hledac.universal.runtime.resource_governor import GovernorDecision
                assert isinstance(decision, GovernorDecision)

    # ── singleton ─────────────────────────────────────────────────────────

    def test_get_governor_singleton(self):
        """
        F202J-8: get_governor() returns the same instance.
        """
        from hledac.universal.runtime.resource_governor import get_governor
        g1 = get_governor()
        g2 = get_governor()
        assert g1 is g2

    # ── CRITICAL memory state ───────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_critical_memory_forces_safe_mode(self, governor):
        """
        F202J-9: CRITICAL UMA state forces safe low-concurrency mode.
        F265H: CRITICAL fetch_limit=6 (not 3) — at 6.7 GiB the system has
        1.3 GiB headroom before EMERGENCY; 6 workers is safe and not too aggressive.
        F265H-EXT: Graduated branch concurrency — 6.7 GiB mild → 3, 6.85 GiB severe → 2.
        """
        with patch.object(governor, "_get_model_status", return_value={"loaded": False, "current_model": None, "initialized": False, "last_error": None}):
            with patch("hledac.universal.runtime.resource_governor.sample_uma_status") as mock_uma:
                mock_uma.return_value = MagicMock(state="critical", system_used_gib=6.7, io_only=False)
                decision = await governor.evaluate()
                assert decision.fetch_limit == 6  # F265H: was 3, raised to 6 for proactive offload
                assert decision.allow_renderer is False
                # F265H-EXT: 6.7 GiB = mild CRITICAL → 3 branches (not 1)
                assert decision.branch_concurrency == 3

    @pytest.mark.asyncio
    async def test_critical_near_emergency_memory(self, governor):
        """
        F265H-EXT: Graduated branch concurrency at 6.85 GiB (near EMERGENCY).
        """
        with patch.object(governor, "_get_model_status", return_value={"loaded": False, "current_model": None, "initialized": False, "last_error": None}):
            with patch("hledac.universal.runtime.resource_governor.sample_uma_status") as mock_uma:
                mock_uma.return_value = MagicMock(state="critical", system_used_gib=6.85, io_only=False)
                decision = await governor.evaluate()
                assert decision.fetch_limit == 6
                assert decision.allow_renderer is False
                # 6.85 GiB = near EMERGENCY → 2 branches
                assert decision.branch_concurrency == 2

    @pytest.mark.asyncio
    async def test_emergency_memory_maximal_reduction(self, governor):
        """
        F265H-EXT: EMERGENCY state (>= 7.0 GiB) forces minimal concurrency (1 branch).
        """
        with patch.object(governor, "_get_model_status", return_value={"loaded": False, "current_model": None, "initialized": False, "last_error": None}):
            with patch("hledac.universal.runtime.resource_governor.sample_uma_status") as mock_uma:
                mock_uma.return_value = MagicMock(state="emergency", system_used_gib=7.0, io_only=False)
                decision = await governor.evaluate()
                assert decision.fetch_limit == 6
                assert decision.allow_renderer is False
                assert decision.branch_concurrency == 1  # EMERGENCY = 1 branch

    # ── apply_decision() ───────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_apply_decision_calls_adjust_fetch_workers(self, governor):
        """
        F202J-10: apply_decision() calls adjust_fetch_workers with the decision's fetch_limit.
        """
        with patch.object(governor, "_get_model_status", return_value={"loaded": False}):
            with patch("hledac.universal.runtime.resource_governor.sample_uma_status") as mock_uma:
                mock_uma.return_value = MagicMock(state="ok", system_used_gib=5.0, io_only=False)
                decision = await governor.evaluate()
                with patch("hledac.universal.utils.concurrency.adjust_fetch_workers", new_callable=AsyncMock) as mock_adjust:
                    await governor.apply_decision(decision)
                    mock_adjust.assert_called_once_with(decision.fetch_limit)

    # ── F2-2: EMA timeout tracking ─────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_evaluate_adaptive_10_timeouts_force_branch_1(self, governor):
        """
        F2-2: 10 consecutive branch timeouts → EMA → branch_concurrency == 1.
        EMA formula: ema = alpha * 1.0 + (1-alpha) * ema, alpha=0.3.
        After 10 timeouts: ema ≈ 1.0, which exceeds 0.7 threshold → branch=1.
        """
        # Record 10 timeouts
        for _ in range(10):
            governor.record_branch_timeout()

        assert governor.ema_branch_pressure > 0.7

        # Adaptive evaluation should cap branch_concurrency at 1
        with patch.object(governor, "_get_model_status", return_value={"loaded": False}):
            with patch("hledac.universal.runtime.resource_governor.sample_uma_status") as mock_uma:
                mock_uma.return_value = MagicMock(state="ok", system_used_gib=5.0, io_only=False)
                decision = await governor.evaluate_adaptive()
                assert decision.branch_concurrency == 1, (
                    f"Expected branch_concurrency=1 after sustained timeouts, got {decision.branch_concurrency}"
                )

    @pytest.mark.asyncio
    async def test_evaluate_adaptive_low_ema_preserves_base(self, governor):
        """
        F2-2: Low EMA (no timeouts) → evaluate_adaptive returns base decision unchanged.
        """
        # Fresh governor: ema = 0.0
        assert governor.ema_branch_pressure == 0.0

        with patch.object(governor, "_get_model_status", return_value={"loaded": False}):
            with patch("hledac.universal.runtime.resource_governor.sample_uma_status") as mock_uma:
                mock_uma.return_value = MagicMock(state="ok", system_used_gib=5.0, io_only=False)
                base = await governor.evaluate()
                adaptive = await governor.evaluate_adaptive()
                # Branch concurrency should be unchanged from base (4 in OK state)
                assert adaptive.branch_concurrency == base.branch_concurrency
                assert adaptive.fetch_limit == base.fetch_limit

    @pytest.mark.asyncio
    async def test_record_branch_success_decays_ema(self, governor):
        """
        F2-2: Successful branch completion decays EMA toward 0.
        """
        # Record several timeouts
        for _ in range(5):
            governor.record_branch_timeout()
        ema_after_timeouts = governor.ema_branch_pressure
        assert ema_after_timeouts > 0

        # Record success — EMA should decay
        governor.record_branch_success()
        assert governor.ema_branch_pressure < ema_after_timeouts

    @pytest.mark.asyncio
    async def test_evaluate_adaptive_medium_ema_caps_at_2(self, governor):
        """
        F2-2: Medium timeout pressure (ema 0.4-0.7) → branch_concurrency = min(base, 2).
        """
        # Inject specific EMA: after ~5 timeouts with some decays
        governor._ema_branch_timeouts = 0.5  # Direct injection

        with patch.object(governor, "_get_model_status", return_value={"loaded": False}):
            with patch("hledac.universal.runtime.resource_governor.sample_uma_status") as mock_uma:
                mock_uma.return_value = MagicMock(state="ok", system_used_gib=5.0, io_only=False)
                decision = await governor.evaluate_adaptive()
                # OK state base = 4, medium EMA should cap at 2
                assert decision.branch_concurrency == 2, (
                    f"Expected branch_concurrency=2 for medium EMA, got {decision.branch_concurrency}"
                )

    @pytest.mark.asyncio
    async def test_ema_branch_pressure_in_snapshot(self, governor):
        """
        F2-2: GovernorSnapshot includes ema_branch_pressure field.
        """
        governor.record_branch_timeout()
        governor.record_branch_timeout()
        snap = governor.snapshot()
        assert hasattr(snap, "ema_branch_pressure")
        assert snap.ema_branch_pressure > 0
