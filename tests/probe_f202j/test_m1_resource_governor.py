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

from __future__ import annotations

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
        """
        with patch.object(governor, "_get_model_status", return_value={"loaded": False, "current_model": None, "initialized": False, "last_error": None}):
            with patch("hledac.universal.runtime.resource_governor.sample_uma_status") as mock_uma:
                mock_uma.return_value = MagicMock(state="critical", system_used_gib=6.5, io_only=False)
                decision = await governor.evaluate()
                assert decision.fetch_limit == 3
                assert decision.allow_renderer is False
                assert decision.branch_concurrency == 1

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
