"""
Planning package — lazy imports to avoid heavy-stack eager loading.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .cost_model import AdaptiveCostModel
    from .htn_planner import HTNPlanner
    from .search import anytime_beam_search
    from .slm_decomposer import SLMDecomposer
    from .task_cache import TaskCache

__all__ = ['HTNPlanner', 'AdaptiveCostModel', 'anytime_beam_search', 'SLMDecomposer', 'TaskCache']


def __getattr__(name: str) -> Any:
    if name == 'HTNPlanner':
        from .htn_planner import HTNPlanner as cls  # noqa: N813
        return cls
    if name == 'AdaptiveCostModel':
        from .cost_model import AdaptiveCostModel as cls  # noqa: N813
        return cls
    if name == 'anytime_beam_search':
        from .search import anytime_beam_search as fn
        return fn
    if name == 'SLMDecomposer':
        from .slm_decomposer import SLMDecomposer as cls  # noqa: N813
        return cls
    if name == 'TaskCache':
        from .task_cache import TaskCache as cls  # noqa: N813
        return cls
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
