"""
Planning package — lazy imports to avoid heavy-stack eager loading.
"""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .cost_model import AdaptiveCostModel
    from .htn_planner import HTNPlanner
    from .search import anytime_beam_search
    from .slm_decomposer import SLMDecomposer
    from .task_cache import TaskCache
    from .step_reward_model import (
        PRMFeatureExtractor,
        PRMInference,
        PRMInferenceContext,
        CumulativePRMScorer,
        create_default_prm_scorer,
    )

__all__ = [
    'HTNPlanner',
    'AdaptiveCostModel',
    'anytime_beam_search',
    'SLMDecomposer',
    'TaskCache',
    'get_slm_decomposer',
    # PRM-1: Step-Level Process Reward Model
    'PRMFeatureExtractor',
    'PRMInference',
    'PRMInferenceContext',
    'CumulativePRMScorer',
    'create_default_prm_scorer',
]

# ISSUE-2.4 FIX: Singleton factory — model loaded once, shared across sprints.
# Prevents ~400MB-1GB per-sprint re-load + Metal active memory leak.
_slm_decomposer_instance: "SLMDecomposer | None" = None
_slm_lock: threading.Lock = threading.Lock()


def get_slm_decomposer(governor, cache, model_name: str = "mlx-community/Qwen2.5-0.5B-4bit", max_parallel: int = 2) -> "SLMDecomposer":
    """
    Singleton factory for SLMDecomposer — model loaded once, reused across sprints.

    ISSUE-2.4 FIX: Replaces per-sprint SLMDecomposer() instantiation which caused:
    - 10-15s re-load per sprint (Qwen2.5-0.5B-4bit ~400MB Metal allocation)
    - Peak 800MB-2GB Metal memory overlap during model swap
    - mx.core Metal active memory not released without explicit unload()

    Thread-safe singleton with lazy initialization.
    """
    global _slm_decomposer_instance
    with _slm_lock:
        if _slm_decomposer_instance is None:
            from .slm_decomposer import SLMDecomposer as cls
            _slm_decomposer_instance = cls(governor=governor, cache=cache, model_name=model_name, max_parallel=max_parallel)
        return _slm_decomposer_instance


def __getattr__(name: str) -> Any:
    if name == 'HTNPlanner':
        from .htn_planner import HTNPlanner as cls  # noqa: N813
        return cls
    if name == 'AdaptiveCostModel':
        from .cost_model import AdaptiveCostModel as cls  # noqa: N813
        return cls
    # PRM-1: Step-Level Process Reward Model lazy imports
    if name == 'PRMFeatureExtractor':
        from .step_reward_model import PRMFeatureExtractor as cls  # noqa: N813
        return cls
    if name == 'PRMInference':
        from .step_reward_model import PRMInference as cls  # noqa: N813
        return cls
    if name == 'PRMInferenceContext':
        from .step_reward_model import PRMInferenceContext as cls  # noqa: N813
        return cls
    if name == 'CumulativePRMScorer':
        from .step_reward_model import CumulativePRMScorer as cls  # noqa: N813
        return cls
    if name == 'create_default_prm_scorer':
        from .step_reward_model import create_default_prm_scorer as fn  # noqa: N813
        return fn
    if name == 'anytime_beam_search':
        from .search import anytime_beam_search as fn
        return fn
    if name == 'SLMDecomposer':
        from .slm_decomposer import SLMDecomposer as cls  # noqa: N813
        return cls
    if name == 'TaskCache':
        from .task_cache import TaskCache as cls  # noqa: N813
        return cls
    if name == 'get_slm_decomposer':
        return get_slm_decomposer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
