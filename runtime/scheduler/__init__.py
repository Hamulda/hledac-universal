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
from hledac.universal.runtime.sprint_scheduler import SprintSchedulerConfig
from hledac.universal.runtime.scheduler.core.lifecycle import SprintLifecycleAdapter

__all__ = [
    "SprintLifecycleAdapter",
    "SprintSchedulerConfig",
    "SourceTier",
]
