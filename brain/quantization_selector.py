"""
brain/quantization_selector.py — F203J: Quantization Selector & Adaptive Inference Budget

ROLE: Advisory layer that selects MLX quantization and token/latency budget
based on current UMA snapshot. Model lifecycle authority STAYS in brain modules.

Policy (always-on, fail-soft):
  Q4_K_M — default for constrained/default M1
  Q5_K_M — when free UMA >= 1.5 GiB
  Q8_0   — only when free UMA >= 2.5 GiB AND explicitly safe
  reject  — when governor denies model load

Bounds:
  No operation >1.5GB RSS except governed model load
  Fallback: Q4_K_M on any error
  No automatic model download in tests

Invariant table:
  Invariant                              | Test
  ─────────────────────────────────────────────────────────────────────
  Q4_K_M at CRITICAL/EMERGENCY         | test_q4_at_critical_emergency
  Q5_K_M at WARN with free >= 1.5GiB  | test_q5_at_warn_sufficient_free
  Q8_0 only when free >= 2.5GiB       | test_q8_only_when_explicitly_safe
  reject when governor denies           | test_reject_when_governor_denies
  fallback Q4_K_M on error             | test_fallback_q4_on_error
  select() returns InferenceBudget      | test_select_returns_inference_budget
  free_uma_hint() computed correctly    | test_free_uma_hint
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# F203J: Quantization constants
Q4_K_M = "q4_k_m"
Q5_K_M = "q5_k_m"
Q8_0 = "q8_0"
Q4_K_M_FALLBACK = "q4_k_m"  # always-available fallback

# F203J: Memory thresholds (GiB free UMA)
_FREE_UMA_FOR_Q5: float = 1.5  # minimum free for Q5_K_M
_FREE_UMA_FOR_Q8: float = 2.5  # minimum free for Q8_0
# F203J: RSS budget guard — no single op > 1.5 GiB
RSS_OP_BUDGET_GB: float = 1.5


@dataclass(frozen=True)
class InferenceBudget:
    """F203J: Inference budget for a model load decision."""
    max_tokens: int
    max_latency_ms: int
    quantization: str
    reason: str


@dataclass(frozen=True)
class QuantizationDecision:
    """F203J: Full decision record from QuantizationSelector.select()."""
    quantization: str
    max_tokens: int
    max_latency_ms: int
    reason: str
    free_uma_gib: float
    allowed: bool


def _compute_free_uma_gib(uma_snapshot) -> float:
    """
    Extract free UMA GiB from a UMAStatus-like snapshot.

    Tries system_available_gib first; falls back to computing from
    system_used_gib if total is unavailable.

    Returns 0.0 on any error (fail-open — selector will pick safe Q4_K_M).
    """
    try:
        if hasattr(uma_snapshot, "system_available_gib"):
            return float(uma_snapshot.system_available_gib)
        if hasattr(uma_snapshot, "system_used_gib") and hasattr(uma_snapshot, "rss_gib"):
            # system_total ≈ 8.0 GiB on M1, or we can infer from other fields
            # Prefer rss_gib as diagnostic; system_used_gib is threshold driver
            if hasattr(uma_snapshot, "total_gib"):
                return float(uma_snapshot.total_gib) - float(uma_snapshot.system_used_gib)
        # Last resort: return 0 (selector will use Q4_K_M)
        return 0.0
    except Exception:
        return 0.0


def _is_explicitly_safe(uma_snapshot) -> bool:
    """
    Return True only if Q8_0 is explicitly allowed by UMA state.

    Q8_0 is allowed only when:
      - uma_state == "ok"
      - io_only == False
      - No swap detected
      - NOT in aggressive memory pressure
    """
    try:
        state = getattr(uma_snapshot, "state", "ok")
        io_only = getattr(uma_snapshot, "io_only", False)
        swap_detected = getattr(uma_snapshot, "swap_detected", False)
        return (
            state == "ok"
            and not io_only
            and not swap_detected
        )
    except Exception:
        return False


class QuantizationSelector:
    """
    F203J: Selects quantization and inference budget based on UMA snapshot.

    Always-on, fail-soft. Falls back to Q4_K_M on any error.

    Usage:
        selector = QuantizationSelector()
        budget = selector.select(uma_snapshot, requested_model="hermes")
        # budget.quantization, budget.max_tokens, budget.max_latency_ms
    """

    def select(
        self,
        uma_snapshot: Any,
        requested_model: str = "hermes",
    ) -> InferenceBudget:
        """
        Select quantization and inference budget for a model load.

        Policy:
          CRITICAL/EMERGENCY → Q4_K_M (constrained, max_tokens=512, max_latency_ms=30000)
          WARN + free >= 1.5 GiB → Q5_K_M (balanced, max_tokens=1024, max_latency_ms=45000)
          OK + free >= 2.5 GiB + explicitly safe → Q8_0 (full, max_tokens=2048, max_latency_ms=60000)
          otherwise → Q4_K_M (safe fallback)

        Args:
            uma_snapshot: GovernorSnapshot or UMAStatus-like object
            requested_model: Model name (default "hermes")

        Returns:
            InferenceBudget with quantization, token/latency budget, and reason
        """
        try:
            return self._select_impl(uma_snapshot, requested_model)
        except Exception as exc:
            logger.debug("[QuantizationSelector] select() failed, using Q4_K_M fallback: %s", exc)
            return InferenceBudget(
                max_tokens=512,
                max_latency_ms=30000,
                quantization=Q4_K_M_FALLBACK,
                reason="fallback_q4_k_m_on_error",
            )

    def _select_impl(self, uma_snapshot: Any, _requested_model: str) -> InferenceBudget:
        """Internal implementation — raises on error (caller wraps in try/except)."""
        free_uma = _compute_free_uma_gib(uma_snapshot)
        state = getattr(uma_snapshot, "state", "ok")

        # Governor denies: block model load entirely
        # Note: allow_model_load=False is checked by the caller in model_manager
        # Here we just return a deny budget
        model_denied = getattr(uma_snapshot, "model_denied", False)
        if model_denied:
            return InferenceBudget(
                max_tokens=0,
                max_latency_ms=0,
                quantization=Q4_K_M_FALLBACK,
                reason="governor_denied",
            )

        # CRITICAL/EMERGENCY: Q4_K_M only
        if state in ("critical", "emergency"):
            return InferenceBudget(
                max_tokens=512,
                max_latency_ms=30000,
                quantization=Q4_K_M,
                reason=f"uma_{state}: constrained",
            )

        # WARN: Q5_K_M if free >= 1.5 GiB
        if state == "warn":
            if free_uma >= _FREE_UMA_FOR_Q5:
                return InferenceBudget(
                    max_tokens=1024,
                    max_latency_ms=45000,
                    quantization=Q5_K_M,
                    reason=f"uma_warn: free_uma={free_uma:.2f}GiB >= 1.5GiB",
                )
            return InferenceBudget(
                max_tokens=512,
                max_latency_ms=30000,
                quantization=Q4_K_M,
                reason=f"uma_warn: free_uma={free_uma:.2f}GiB < 1.5GiB",
            )

        # OK: Q8_0 only if explicitly safe and free >= 2.5 GiB
        explicitly_safe = _is_explicitly_safe(uma_snapshot)
        if explicitly_safe and free_uma >= _FREE_UMA_FOR_Q8:
            return InferenceBudget(
                max_tokens=2048,
                max_latency_ms=60000,
                quantization=Q8_0,
                reason=f"uma_ok: free_uma={free_uma:.2f}GiB >= 2.5GiB, explicitly_safe",
            )

        # OK but not enough for Q8: Q5_K_M if possible, else Q4_K_M
        if free_uma >= _FREE_UMA_FOR_Q5:
            return InferenceBudget(
                max_tokens=1024,
                max_latency_ms=45000,
                quantization=Q5_K_M,
                reason=f"uma_ok: free_uma={free_uma:.2f}GiB >= 1.5GiB",
            )

        return InferenceBudget(
            max_tokens=512,
            max_latency_ms=30000,
            quantization=Q4_K_M,
            reason=f"uma_ok: free_uma={free_uma:.2f}GiB < 1.5GiB",
        )

    def free_uma_hint(self, uma_snapshot: Any) -> float:
        """
        Return the free UMA GiB hint from a snapshot (helper for diagnostics).
        """
        return _compute_free_uma_gib(uma_snapshot)
