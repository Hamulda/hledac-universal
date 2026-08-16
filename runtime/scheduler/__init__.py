"""
runtime/scheduler - Sprint Scheduler Core Components
=====================================================

Vertical slice extraction from sprint_scheduler.py (Phase 1 of modular decomposition).

Modules:
    core/           - Scheduler core (lifecycle, config, result types)
"""


# F289: Use the canonical SprintSchedulerConfig from sprint_scheduler.py
# to ensure consistent windup math across all consumers.
# The core/config.py SprintSchedulerConfig is deprecated.
# SourceTier remains in core/config.py (no windup logic).
from hledac.universal.runtime.scheduler.core.config import SourceTier
from hledac.universal.runtime.scheduler.core.lifecycle import SprintLifecycleAdapter
from hledac.universal.runtime.scheduler.core.types import (
    CTLossStage,
    EarlyExitClass,
    FeedDominanceGuardResult,
    LaneBudgetAllocation,
    LaneBudgetPool,
    LaneName,
    SourceTier,
    _TIER_ORDER,
    _DEFAULT_SOURCE_TIER_MAP,
    )
# SprintSchedulerConfig remains in sprint_scheduler.py (cross-module import)

__all__ = [
    "SprintLifecycleAdapter",
    "SourceTier",
    "CTLossStage",
    "EarlyExitClass",
    "FeedDominanceGuardResult",
    "LaneBudgetAllocation",
    "LaneBudgetPool",
    "LaneName",
    "_TIER_ORDER",
    "_DEFAULT_SOURCE_TIER_MAP",
]
