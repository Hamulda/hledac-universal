"""
Planning package — lazy imports to avoid heavy-stack eager loading.

ISSUE-003 FIX: Module-level locks registered via @auto_register decorator.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

from _core.lock_registry import LockCategory, auto_register

if TYPE_CHECKING:
    from .cost_model import AdaptiveCostModel
    from .htn_planner import HTNPlanner
    from .search import anytime_beam_search
    from .slm_decomposer import SLMDecomposer
    from .step_reward_model import (
        CumulativePRMScorer,
        PRMFeatureExtractor,
        PRMInference,
        PRMInferenceContext,
        create_default_prm_scorer,
    )
    from .task_cache import TaskCache

__all__ = [
    "HTNPlanner",
    "AdaptiveCostModel",
    "anytime_beam_search",
    "SLMDecomposer",
    "TaskCache",
    "get_slm_decomposer",
    # PRM-1: Step-Level Process Reward Model
    "PRMFeatureExtractor",
    "PRMInference",
    "PRMInferenceContext",
    "CumulativePRMScorer",
    "create_default_prm_scorer",
]

# ISSUE-2.4 FIX: Singleton factory — model loaded once, shared across sprints.
# Prevents ~400MB-1GB per-sprint re-load + Metal active memory leak.
_slm_decomposer_instance: SLMDecomposer | None = None


@auto_register(LockCategory.MPC)
def _slm_lock():
    """Module-level lock for SLMDecomposer singleton factory."""
    return threading.Lock()


def get_slm_decomposer(
    governor, cache, model_name: str = "mlx-community/Qwen2.5-0.5B-4bit", max_parallel: int = 2
) -> SLMDecomposer:
    """
    Singleton factory for SLMDecomposer — model loaded once, reused across sprints.

    ISSUE-2.4 FIX: Replaces per-sprint SLMDecomposer() instantiation which caused:
    - 10-15s re-load per sprint (Qwen2.5-0.5B-4bit ~400MB Metal allocation)
    - Peak 800MB-2GB Metal memory overlap during model swap
    - mx.core Metal active memory not released without explicit unload()

    Thread-safe singleton with lazy initialization.
    """
    global _slm_decomposer_instance
    with _slm_lock():
        if _slm_decomposer_instance is None:
            from .slm_decomposer import SLMDecomposer as cls

            _slm_decomposer_instance = cls(
                governor=governor, cache=cache, model_name=model_name, max_parallel=max_parallel
            )
        return _slm_decomposer_instance


# REFACTORED: Extracted to dispatch table to fix duplicated_branches (10 instances).
# Original pattern: 10 repeated if-blocks with identical structure.
_LAZY_IMPORT_DISPATCH: dict[str, tuple[str, str] | None] = {
    # Class imports
    "HTNPlanner": (".htn_planner", "HTNPlanner"),
    "AdaptiveCostModel": (".cost_model", "AdaptiveCostModel"),
    "SLMDecomposer": (".slm_decomposer", "SLMDecomposer"),
    "TaskCache": (".task_cache", "TaskCache"),
    # PRM-1: Step-Level Process Reward Model
    "PRMFeatureExtractor": (".step_reward_model", "PRMFeatureExtractor"),
    "PRMInference": (".step_reward_model", "PRMInference"),
    "PRMInferenceContext": (".step_reward_model", "PRMInferenceContext"),
    "CumulativePRMScorer": (".step_reward_model", "CumulativePRMScorer"),
    # Function imports
    "create_default_prm_scorer": (".step_reward_model", "create_default_prm_scorer"),
    "anytime_beam_search": (".search", "anytime_beam_search"),
}


def __getattr__(name: str) -> Any:
    # Fast path for singleton factory
    if name == "get_slm_decomposer":
        return get_slm_decomposer
    if name in _LAZY_IMPORT_DISPATCH:
        module_path, symbol_name = _LAZY_IMPORT_DISPATCH[name]
        import importlib

        module = importlib.import_module(module_path, __package__)
        return getattr(module, symbol_name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
