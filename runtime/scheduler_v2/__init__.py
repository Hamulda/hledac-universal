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

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

# ── Re-exported types (backward compat for v1 imports) ───────────────────────

if TYPE_CHECKING:
    from hledac.universal.runtime.scheduler_config import SprintSchedulerConfig
    from hledac.universal.runtime.scheduler_result import SprintSchedulerResult

__all__ = [
    # v1 compat re-exports
    "SprintSchedulerConfig",
    "SprintSchedulerResult",
    # v2 types
    "SprintSchedulerV2",
    "SprintContext",
    "PhaseRunner",
    "PreludePhase",
    "AcquisitionPhase",
    "WinddownPhase",
    "AcquisitionOrchestrator",
    "WinddownOrchestrator",
    "CycleResult",
    "LaneResult",
    "AcquisitionOrchestratorProtocol",
    "SchedulerProtocol",
]


# ── Lazy import registry (dict-based, single lookup) ─────────────────────────
# ponytail: no dict-to-switch optimization needed — 14 items is fine as-is
_LAZY_MAP: dict[str, str] = {
    # v1 compat
    "SprintSchedulerConfig": "hledac.universal.runtime.scheduler_config",
    "SprintSchedulerResult": "hledac.universal.runtime.scheduler_result",
    # v2 orchestrator
    "SprintSchedulerV2": "hledac.universal.runtime.scheduler_v2.scheduler",
    # v2 protocols
    "SprintContext": "hledac.universal.runtime.scheduler_v2.protocol",
    "PhaseRunner": "hledac.universal.runtime.scheduler_v2.protocol",
    "PreludePhase": "hledac.universal.runtime.scheduler_v2.protocol",
    "AcquisitionPhase": "hledac.universal.runtime.scheduler_v2.protocol",
    "WinddownPhase": "hledac.universal.runtime.scheduler_v2.protocol",
    "AcquisitionOrchestratorProtocol": "hledac.universal.runtime.scheduler_v2.protocol",
    "SchedulerProtocol": "hledac.universal.runtime.scheduler_v2.protocol",
    # v2 phase orchestrators
    "AcquisitionOrchestrator": "hledac.universal.runtime.scheduler_v2.acquisition",
    "WinddownOrchestrator": "hledac.universal.runtime.scheduler_v2.winddown",
    # v2 results
    "CycleResult": "hledac.universal.runtime.scheduler_v2.acquisition",
    "LaneResult": "hledac.universal.runtime.scheduler_v2.prelude",
}


def __getattr__(name: str):
    """Lazy import dispatch — single lookup for all 14 exports."""
    if name in _LAZY_MAP:
        module = importlib.import_module(_LAZY_MAP[name])
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return list(__all__)


# ponytail: skip __getitem__ protocol — not needed
# ponytail: skip typing_extensions imports — TYPE_CHECKING handles type hints
