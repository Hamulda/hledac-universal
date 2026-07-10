"""STEP 4 — SprintScheduler v2: greenfield rewrite of runtime/sprint_scheduler.py.

F350M-R / Issue #P2.

Package structure:
    protocol.py   — SprintContext + Phase protocols
    prelude.py    — PreludeOrchestrator
    acquisition.py — AcquisitionOrchestrator
    winddown.py   — WinddownOrchestrator
    scheduler.py  — SprintSchedulerV2 (thin orchestrator)

All phase orchestrators are imported lazily to avoid M1 Metal initialization
at import time (PEP 810 lazy imports).
"""


from typing import TYPE_CHECKING

# ── Re-exported types (backward compat for v1 imports) ───────────────────────

if TYPE_CHECKING:
    from runtime.scheduler_config import SprintSchedulerConfig
    from runtime.scheduler_result import SprintSchedulerResult

__all__ = [
    # v1 compat re-exports
    "SprintSchedulerConfig",
    "SprintSchedulerResult",
    # v2 types (lazy — imported from protocol.py below)
]


def __getattr__(name: str):
    if name == "SprintSchedulerConfig":
        from runtime.scheduler_config import SprintSchedulerConfig

        return SprintSchedulerConfig
    if name == "SprintSchedulerResult":
        from runtime.scheduler_result import SprintSchedulerResult

        return SprintSchedulerResult
    if name == "SprintSchedulerV2":
        from runtime.scheduler_v2.scheduler import SprintSchedulerV2

        return SprintSchedulerV2
    if name == "SprintContext":
        from runtime.scheduler_v2.protocol import SprintContext

        return SprintContext
    if name == "PhaseRunner":
        from runtime.scheduler_v2.protocol import PhaseRunner

        return PhaseRunner
    if name == "PreludePhase":
        from runtime.scheduler_v2.protocol import PreludePhase

        return PreludePhase
    if name == "AcquisitionPhase":
        from runtime.scheduler_v2.protocol import AcquisitionPhase

        return AcquisitionPhase
    if name == "WinddownPhase":
        from runtime.scheduler_v2.protocol import WinddownPhase

        return WinddownPhase
    if name == "AcquisitionOrchestrator":
        from runtime.scheduler_v2.acquisition import AcquisitionOrchestrator

        return AcquisitionOrchestrator
    if name == "WinddownOrchestrator":
        from runtime.scheduler_v2.winddown import WinddownOrchestrator

        return WinddownOrchestrator
    if name == "CycleResult":
        from runtime.scheduler_v2.acquisition import CycleResult

        return CycleResult
    if name == "LaneResult":
        from runtime.scheduler_v2.prelude import LaneResult

        return LaneResult
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
