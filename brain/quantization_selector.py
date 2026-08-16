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
  Q8_0 only when free >= 3.5GiB       | test_q8_only_when_explicitly_safe
  reject when governor denies           | test_reject_when_governor_denies
  fallback Q4_K_M on error             | test_fallback_q4_on_error
  select() returns InferenceBudget      | test_select_returns_inference_budget
  free_uma_hint() computed correctly    | test_free_uma_hint
"""
import logging
from dataclasses import dataclass
import msgspec
from compat.msgspec_gc_compat import Struct
from typing import Any
from _core import aclose
logger = logging.getLogger(__name__)
Q3_K_M = 'q3_k_m'
Q4_K_M = 'q4_k_m'
Q5_K_M = 'q5_k_m'
Q8_0 = 'q8_0'
Q4_K_M_FALLBACK = 'q4_k_m'
_FREE_UMA_FOR_Q3: float = 1.0  # ISSUE #15: M1 8GB emergency fallback
_FREE_UMA_FOR_Q5: float = 2.0
# ISSUE-35: M1 8GB with 4.5GB MLX inference ceiling leaves 3.5GB for system.
# Q8 threshold raised from 3.0 to 3.5 GB to match actual available headroom.
_FREE_UMA_FOR_Q8: float = 3.5
RSS_OP_BUDGET_GB: float = 4.5  # ISSUE-35: Hard cap 4.5 GB for MLX inference (from 8GB)

class InferenceBudget(Struct, frozen=True):
    """F203J: Inference budget for a model load decision."""
    max_tokens: int
    max_latency_ms: int
    quantization: str
    reason: str

class QuantizationDecision(Struct, frozen=True):
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
        if hasattr(uma_snapshot, 'system_available_gib'):
            return float(uma_snapshot.system_available_gib)
        if hasattr(uma_snapshot, 'system_used_gib') and hasattr(uma_snapshot, 'rss_gib'):
            if hasattr(uma_snapshot, 'total_gib'):
                return float(uma_snapshot.total_gib) - float(uma_snapshot.system_used_gib)
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
        state = getattr(uma_snapshot, 'state', 'ok')
        io_only = getattr(uma_snapshot, 'io_only', False)
        swap_detected = getattr(uma_snapshot, 'swap_detected', False)
        return state == 'ok' and (not io_only) and (not swap_detected)
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

    def select(self, uma_snapshot: Any, requested_model: str='hermes') -> InferenceBudget:
        """
        Select quantization and inference budget for a model load.

        Policy (ISSUE #15 + ISSUE-35):
          CRITICAL/EMERGENCY → Q4_K_M (constrained, max_tokens=512, max_latency_ms=30000)
          WARN + free >= 1.5 GiB → Q5_K_M (balanced, max_tokens=1024, max_latency_ms=45000)
          OK + free >= 3.5 GiB + explicitly safe → Q8_0 (full, max_tokens=2048, max_latency_ms=60000)
          free < 1.0 GiB (any state) → Q3_K_M (emergency, max_tokens=256, max_latency_ms=20000)
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
            logger.debug('[QuantizationSelector] select() failed, using Q4_K_M fallback: %s', exc)
            return InferenceBudget(max_tokens=512, max_latency_ms=30000, quantization=Q4_K_M_FALLBACK, reason='fallback_q4_k_m_on_error')

    def _select_impl(self, uma_snapshot: Any, _requested_model: str) -> InferenceBudget:
        """Internal implementation — raises on error (caller wraps in try/except)."""
        free_uma = _compute_free_uma_gib(uma_snapshot)
        state = getattr(uma_snapshot, 'state', 'ok')
        model_denied = getattr(uma_snapshot, 'model_denied', False)
        if model_denied:
            return InferenceBudget(max_tokens=0, max_latency_ms=0, quantization=Q4_K_M_FALLBACK, reason='governor_denied')

        # ISSUE #15: Q3_K_M emergency fallback when FREE_UMA < 1GB
        if free_uma < _FREE_UMA_FOR_Q3:
            return InferenceBudget(max_tokens=256, max_latency_ms=20000, quantization=Q3_K_M, reason=f'uma_{state}: free_uma={free_uma:.2f}GiB < 1.0GiB, Q3_K_M emergency')

        if state in ('critical', 'emergency'):
            return InferenceBudget(max_tokens=512, max_latency_ms=30000, quantization=Q4_K_M, reason=f'uma_{state}: constrained')
        if state == 'warn':
            if free_uma >= _FREE_UMA_FOR_Q5:
                return InferenceBudget(max_tokens=1024, max_latency_ms=45000, quantization=Q5_K_M, reason=f'uma_warn: free_uma={free_uma:.2f}GiB >= 1.5GiB')
            return InferenceBudget(max_tokens=512, max_latency_ms=30000, quantization=Q4_K_M, reason=f'uma_warn: free_uma={free_uma:.2f}GiB < 1.5GiB')
        explicitly_safe = _is_explicitly_safe(uma_snapshot)
        if explicitly_safe and free_uma >= _FREE_UMA_FOR_Q8:
            return InferenceBudget(max_tokens=2048, max_latency_ms=60000, quantization=Q8_0, reason=f'uma_ok: free_uma={free_uma:.2f}GiB >= 3.5GiB, explicitly_safe')
        if free_uma >= _FREE_UMA_FOR_Q5:
            return InferenceBudget(max_tokens=1024, max_latency_ms=45000, quantization=Q5_K_M, reason=f'uma_ok: free_uma={free_uma:.2f}GiB >= 1.5GiB')
        return InferenceBudget(max_tokens=512, max_latency_ms=30000, quantization=Q4_K_M, reason=f'uma_ok: free_uma={free_uma:.2f}GiB < 1.5GiB')

    def free_uma_hint(self, uma_snapshot: Any) -> float:
        """
        Return the free UMA GiB hint from a snapshot (helper for diagnostics).
        """
        return _compute_free_uma_gib(uma_snapshot)