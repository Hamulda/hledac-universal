# Sprint F232A: Pivot builder stubs — TEMPORARY until component is restored
from typing import Any


def _derive_branch_seeds(branch: Any) -> list:
    return []


def _derive_focus_expand(branch: Any) -> tuple:
    return ([], [])


def _derive_trend_seeds(trend: Any) -> list:
    return []


def _get_correlation_from_handoff(handoff: Any) -> float:
    return 0.0


def _get_runtime_truth(eh: Any) -> dict[str, Any]:
    """
    Sprint F232A: Extract runtime_truth from ExportHandoff.

    Truth order (fail-soft):
      1. eh.runtime_truth — canonical surface
      2. eh.scorecard["runtime_truth"] — fallback
      3. {} — empty dict when not present
    """
    if hasattr(eh, "runtime_truth") and eh.runtime_truth:
        return eh.runtime_truth if isinstance(eh.runtime_truth, dict) else {}
    if hasattr(eh, "scorecard") and eh.scorecard:
        _sc_rt = eh.scorecard.get("runtime_truth") if isinstance(eh.scorecard, dict) else None
        if isinstance(_sc_rt, dict):
            return _sc_rt
    return {}
