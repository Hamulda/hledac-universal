"""
tests/probe_f203j/test_quantization_selector.py

Probe tests for QuantizationSelector (F203J).

Invariant table:
  Invariant                                   | Test method
  ─────────────────────────────────────────────────────────────────────
  Q4_K_M at CRITICAL/EMERGENCY             | test_q4_at_critical_emergency
  Q5_K_M at WARN with free >= 1.5GiB     | test_q5_at_warn_sufficient_free
  Q4_K_M at WARN with free < 1.5GiB      | test_q4_at_warn_insufficient_free
  Q8_0 only when free >= 2.5GiB+safe       | test_q8_only_when_explicitly_safe
  Q5_K_M at OK with free >= 1.5GiB        | test_q5_at_ok_sufficient_free
  Q4_K_M at OK with free < 1.5GiB         | test_q4_at_ok_insufficient_free
  reject when governor denies               | test_reject_when_governor_denies
  fallback Q4_K_M on error                 | test_fallback_q4_on_error
  select() returns InferenceBudget         | test_select_returns_inference_budget
  QuantizationDecision has correct fields   | test_quantization_decision_fields
  free_uma_hint computed correctly         | test_free_uma_hint
  governor free_uma_gib in decision        | test_governor_decision_has_free_uma
  governor free_uma_gib in snapshot        | test_governor_snapshot_has_free_uma
  model_lifecycle selected quantization     | test_lifecycle_selected_quantization
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch


class TestQuantizationSelector:
    """F203J: QuantizationSelector probe tests."""

    @pytest.fixture
    def selector(self):
        """Create a fresh QuantizationSelector instance per test."""
        from hledac.universal.brain.quantization_selector import QuantizationSelector
        return QuantizationSelector()

    def _uma(self, state="ok", system_available_gib=2.0, swap_detected=False, io_only=False):
        """Helper to create a mock UMA snapshot."""
        uma = MagicMock()
        uma.state = state
        uma.system_available_gib = system_available_gib
        uma.swap_detected = swap_detected
        uma.io_only = io_only
        uma.model_denied = False
        return uma

    # ── Q4_K_M at CRITICAL/EMERGENCY ─────────────────────────────────────

    def test_q4_at_critical_emergency(self, selector):
        """F203J-1: CRITICAL state → Q4_K_M."""
        uma = self._uma(state="critical", system_available_gib=0.5)
        budget = selector.select(uma)
        assert budget.quantization == "q4_k_m", "critical → q4_k_m"
        assert budget.max_tokens == 512
        assert budget.max_latency_ms == 30000
        assert "critical" in budget.reason

    def test_q4_at_emergency(self, selector):
        """F203J-2: EMERGENCY state → Q4_K_M."""
        uma = self._uma(state="emergency", system_available_gib=0.3)
        budget = selector.select(uma)
        assert budget.quantization == "q4_k_m", "emergency → q4_k_m"
        assert budget.max_tokens == 512
        assert budget.max_latency_ms == 30000
        assert "emergency" in budget.reason

    # ── Q5_K_M at WARN with sufficient free ───────────────────────────────

    def test_q5_at_warn_sufficient_free(self, selector):
        """F203J-3: WARN + free >= 1.5 GiB → Q5_K_M."""
        uma = self._uma(state="warn", system_available_gib=1.8)
        budget = selector.select(uma)
        assert budget.quantization == "q5_k_m", "warn + free >= 1.5 → q5_k_m"
        assert budget.max_tokens == 1024
        assert budget.max_latency_ms == 45000

    def test_q4_at_warn_insufficient_free(self, selector):
        """F203J-4: WARN + free < 1.5 GiB → Q4_K_M."""
        uma = self._uma(state="warn", system_available_gib=1.0)
        budget = selector.select(uma)
        assert budget.quantization == "q4_k_m", "warn + free < 1.5 → q4_k_m"
        assert budget.max_tokens == 512

    # ── Q8_0 only when explicitly safe ───────────────────────────────────

    def test_q8_only_when_explicitly_safe(self, selector):
        """F203J-5: OK + free >= 2.5 GiB + explicitly safe → Q8_0."""
        uma = self._uma(state="ok", system_available_gib=3.0, io_only=False, swap_detected=False)
        budget = selector.select(uma)
        assert budget.quantization == "q8_0", "ok + free >= 2.5 + safe → q8_0"
        assert budget.max_tokens == 2048
        assert budget.max_latency_ms == 60000
        assert "explicitly_safe" in budget.reason

    def test_q5_at_ok_sufficient_free(self, selector):
        """F203J-6: OK + free >= 1.5 GiB but not safe → Q5_K_M."""
        uma = self._uma(state="ok", system_available_gib=2.0, io_only=False, swap_detected=False)
        budget = selector.select(uma)
        assert budget.quantization == "q5_k_m", "ok + free >= 1.5 + not_q8_safe → q5_k_m"
        assert budget.max_tokens == 1024

    def test_q4_at_ok_insufficient_free(self, selector):
        """F203J-7: OK + free < 1.5 GiB → Q4_K_M."""
        uma = self._uma(state="ok", system_available_gib=1.0)
        budget = selector.select(uma)
        assert budget.quantization == "q4_k_m", "ok + free < 1.5 → q4_k_m"

    def test_q4_when_io_only(self, selector):
        """F203J-8: io_only=True blocks Q8_0 even with free >= 2.5 GiB."""
        uma = self._uma(state="ok", system_available_gib=3.0, io_only=True, swap_detected=False)
        budget = selector.select(uma)
        assert budget.quantization in ("q4_k_m", "q5_k_m"), "io_only blocks q8_0"

    def test_q4_when_swap_detected(self, selector):
        """F203J-9: swap_detected=True blocks Q8_0 even with free >= 2.5 GiB."""
        uma = self._uma(state="ok", system_available_gib=3.0, io_only=False, swap_detected=True)
        budget = selector.select(uma)
        assert budget.quantization in ("q4_k_m", "q5_k_m"), "swap_detected blocks q8_0"

    # ── Reject when governor denies ────────────────────────────────────────

    def test_reject_when_governor_denies(self, selector):
        """F203J-10: model_denied=True → budget with max_tokens=0."""
        uma = self._uma(state="ok", system_available_gib=3.0)
        uma.model_denied = True
        budget = selector.select(uma)
        assert budget.max_tokens == 0, "governor_denied → max_tokens=0"
        assert budget.max_latency_ms == 0, "governor_denied → max_latency_ms=0"
        assert budget.reason == "governor_denied"

    # ── Fallback on error ──────────────────────────────────────────────────

    def test_fallback_q4_on_error(self, selector):
        """F203J-11: select() fails soft → Q4_K_M budget."""
        # Force _select_impl to raise by patching it directly
        original_impl = selector._select_impl
        def raising_impl(*args, **kwargs):
            raise RuntimeError("synthetic error")
        selector._select_impl = raising_impl
        try:
            budget = selector.select(MagicMock())
            assert budget.quantization == "q4_k_m", "fallback → q4_k_m"
            assert budget.max_tokens == 512
            assert "fallback" in budget.reason or "error" in budget.reason
        finally:
            selector._select_impl = original_impl

    # ── Return type ─────────────────────────────────────────────────────────

    def test_select_returns_inference_budget(self, selector):
        """F203J-12: select() returns InferenceBudget with correct fields."""
        uma = self._uma(state="ok", system_available_gib=1.0)
        budget = selector.select(uma)
        assert hasattr(budget, "max_tokens")
        assert hasattr(budget, "max_latency_ms")
        assert hasattr(budget, "quantization")
        assert hasattr(budget, "reason")
        assert isinstance(budget.max_tokens, int)
        assert isinstance(budget.max_latency_ms, int)
        assert isinstance(budget.quantization, str)
        assert isinstance(budget.reason, str)

    def test_quantization_decision_fields(self):
        """F203J-13: QuantizationDecision has correct fields."""
        from hledac.universal.brain.quantization_selector import QuantizationDecision
        decision = QuantizationDecision(
            quantization="q5_k_m",
            max_tokens=1024,
            max_latency_ms=45000,
            reason="uma_warn: free_uma=1.8GiB >= 1.5GiB",
            free_uma_gib=1.8,
            allowed=True,
        )
        assert decision.quantization == "q5_k_m"
        assert decision.max_tokens == 1024
        assert decision.max_latency_ms == 45000
        assert decision.reason == "uma_warn: free_uma=1.8GiB >= 1.5GiB"
        assert decision.free_uma_gib == 1.8
        assert decision.allowed is True

    def test_free_uma_hint(self, selector):
        """F203J-14: free_uma_hint() returns correct free UMA GiB."""
        uma = self._uma(state="ok", system_available_gib=2.5)
        hint = selector.free_uma_hint(uma)
        assert hint == 2.5, "free_uma_hint returns system_available_gib"

    def test_free_uma_hint_from_governor_snapshot(self, selector):
        """F203J-15: free_uma_hint() works with GovernorSnapshot-like object."""
        # GovernorSnapshot has free_uma_gib field
        snap = MagicMock()
        snap.system_available_gib = 1.8
        snap.state = "warn"
        snap.model_denied = False
        hint = selector.free_uma_hint(snap)
        assert hint == 1.8


class TestGovernorDecisionFreeUMA:
    """F203J: GovernorDecision and GovernorSnapshot free_uma_gib field tests."""

    def test_governor_decision_has_free_uma(self):
        """F203J-16: GovernorDecision has free_uma_gib field."""
        from hledac.universal.runtime.resource_governor import GovernorDecision
        decision = GovernorDecision(
            fetch_limit=25,
            allow_renderer=True,
            allow_model_load=True,
            branch_concurrency=4,
            reason="normal",
            uma_state="ok",
            model_loaded=False,
            free_uma_gib=2.5,
        )
        assert decision.free_uma_gib == 2.5

    @pytest.mark.asyncio
    async def test_governor_evaluate_returns_free_uma(self):
        """F203J-17: evaluate() returns free_uma_gib in GovernorDecision."""
        from hledac.universal.runtime.resource_governor import M1ResourceGovernor
        governor = M1ResourceGovernor()
        with patch("hledac.universal.runtime.resource_governor.sample_uma_status") as mock_uma:
            mock_status = MagicMock()
            mock_status.state = "ok"
            mock_status.system_available_gib = 2.5
            mock_status.system_used_gib = 5.5
            mock_status.io_only = False
            mock_status.swap_detected = False
            mock_uma.return_value = mock_status
            decision = await governor.evaluate()
            assert hasattr(decision, "free_uma_gib"), "GovernorDecision must have free_uma_gib"
            assert decision.free_uma_gib == 2.5

    def test_governor_snapshot_has_free_uma(self):
        """F203J-18: GovernorSnapshot has free_uma_gib field."""
        from hledac.universal.runtime.resource_governor import GovernorSnapshot
        snap = GovernorSnapshot(
            uma_state="ok",
            model_loaded=False,
            fetch_limit=25,
            branch_concurrency=4,
            renderer_denied_count=0,
            model_denied_count=0,
            system_used_gib=5.5,
            io_only=False,
            free_uma_gib=2.5,
        )
        assert snap.free_uma_gib == 2.5


class TestModelLifecycleQuantization:
    """F203J: model_lifecycle quantization tracking tests."""

    def test_get_selected_quantization_default(self):
        """F203J-19: get_selected_quantization() returns default q4_k_m."""
        from hledac.universal.brain.model_lifecycle import get_selected_quantization
        # Reset to default before test
        import hledac.universal.brain.model_lifecycle as ml
        ml._selected_quantization = "q4_k_m"
        assert get_selected_quantization() == "q4_k_m"

    def test_set_selected_quantization(self):
        """F203J-20: set_selected_quantization() updates the tracked value."""
        from hledac.universal.brain.model_lifecycle import (
            get_selected_quantization,
            set_selected_quantization,
        )
        import hledac.universal.brain.model_lifecycle as ml
        original = ml._selected_quantization
        try:
            set_selected_quantization("q5_k_m")
            assert get_selected_quantization() == "q5_k_m"
            set_selected_quantization("q8_0")
            assert get_selected_quantization() == "q8_0"
        finally:
            ml._selected_quantization = original
