"""STEP 1 — SprintSchedulerConfig + supporting types.

Extracted from sprint_scheduler.py (33 449 LOC → modular package).
F350M-R / Issue #P2.


All changes are backward-compatible: the original class remains in
runtime/sprint_scheduler.py and is re-imported / aliased there.
This file is the canonical home for the type definitions.
"""

import logging
from dataclasses import field
from enum import Enum, auto
from typing import Any, Protocol

from compat.msgspec_gc_compat import Struct

logger = logging.getLogger(__name__)


class IntCounterLayoutProto(Protocol):
    """Minimal duck-typed interface for IntCounterLayout (used in hot-path properties)."""

    def get(self, key: str) -> int: ...

    def set(self, key: str, value: int) -> None: ...

    def bump(self, name: str, n: int) -> int: ...


class SourceTier(Enum):
    SURFACE = auto()
    STRUCTURED_TI = auto()
    DEEP = auto()
    ARCHIVE = auto()
    OTHER = auto()

    def __repr__(self) -> str:
        return f"SourceTier.{self.name}"


_TIER_ORDER = [SourceTier.SURFACE, SourceTier.STRUCTURED_TI, SourceTier.DEEP, SourceTier.ARCHIVE, SourceTier.OTHER]


class EarlyExitClass(Struct):
    """Canonical early-exit classification labels."""

    NATURAL = "natural"
    FEED_DOMINANT = "feed_dominant"
    NONFEED_ONLY = "nonfeed_only"
    PREWINDUP_TIMEOUT = "prewindup_timeout"
    ABORT = "abort"
    UNKNOWN = "unknown"


_UNSET: Any = object()


class SprintSchedulerConfig(Struct):
    """Configuration for one sprint run.

    STEP 1 extracted from sprint_scheduler.py (33 449 LOC → modular package).
    F350M-R / Issue #P2.
    """

    sprint_duration_s: float = 1800.0
    windup_lead_s: float = 180.0
    cycle_sleep_s: float = 5.0
    cycle_budget_s: float = 60.0
    max_cycles: int = 100
    max_parallel_sources: int = 4
    stop_on_first_accepted: bool = False
    export_enabled: bool = True
    export_dir: str = ""
    max_entries_per_cycle: int = 50
    max_hypothesis_depth: int = 3
    max_hypothesis_queries: int = 10
    aggressive_mode: bool = True
    aggressive_branch_timeout_s: float = 45.0
    branch_timeout_budget_s: float = 0.0
    _MAX_BRANCH_TIMEOUT_CAP: float = 300.0
    _MIN_BRANCH_REMAINING_S_DEFAULT: float = 2.0
    _MIN_BRANCH_REMAINING_S_CAP: float = 5.0
    _MIN_BRANCH_REMAINING_S: float = 2.0
    partial_export_findings_interval: int = 10
    source_tier_map: dict[str, SourceTier] = field(default_factory=dict)
    acquisition_profile: str | None = None
    require_nonfeed_corrob_for_early_exit: bool = False
    sensitive_query_transport: str = "auto"
    predecessor_sprint_id: str | None = None
    deep_research_enabled: bool = False
    extreme_mode: bool = False

    @property
    def effective_windup_lead_s(self) -> float:
        """Adaptive windup that scales with sprint duration.

        F290: Short sprints get smaller windup overhead.
        F285: Explicit windup_lead_s (non-default 180.0) passes through directly.
        F273B + F288: Aggressive mode → 15% ratio.
        """
        if self.windup_lead_s is not None and self.windup_lead_s != 180.0:
            return float(max(30.0, min(180.0, float(self.windup_lead_s))))
        if self.aggressive_mode:
            ratio = 0.15
        elif self.sprint_duration_s <= 120.0:
            ratio = 0.2
        elif self.sprint_duration_s <= 300.0:
            ratio = 0.25
        else:
            ratio = 0.3
        raw = self.sprint_duration_s * ratio
        return float(max(15.0, min(180.0, raw)))

    @property
    def final_windup_lead_s(self) -> float:
        """Adaptive windup for sprint-end synthesis and graceful shutdown.

        Matches effective_windup_lead_s ratio tiers but with [30, 180] floor.
        """
        if self.windup_lead_s != 180.0:
            result = float(min(45.0, self.windup_lead_s))
            logger.info("[WINDUP] final_windup=%.1fs (explicit)", result)
            return result
        if self.aggressive_mode:
            ratio = 0.15
        elif self.sprint_duration_s <= 120.0:
            ratio = 0.2
        elif self.sprint_duration_s <= 300.0:
            ratio = 0.25
        else:
            ratio = 0.3
        raw = self.sprint_duration_s * ratio
        result = float(max(30.0, min(180.0, raw)))
        logger.info("[WINDUP] lead=%.1fs", result)
        return result

    def windup_for_cycle(self, cycle_time_ema: float) -> float:
        """Cycle-time-adaptive windup lead (F273B + F278A + F290).

        Returns longer windup when observed cycles are slow so the windup
        phase has at least 2 cycles of headroom.
        """
        if cycle_time_ema <= 0:
            return self.effective_windup_lead_s
        base = self.effective_windup_lead_s
        adapt = max(0.0, min(30.0, (cycle_time_ema - 8.0) * 0.5))
        return float(max(30.0, min(180.0, base + adapt)))

    @property
    def effective_cycle_sleep_s(self) -> float:
        """Adaptive cycle sleep that scales with sprint duration (F228G).

        Short sprints need much shorter inter-cycle sleep than long ones.
        """
        active = max(0.0, self.sprint_duration_s - self.final_windup_lead_s)
        if active <= 0:
            return 0.5
        scaled = max(0.5, min(5.0, active / 300.0))
        return float(scaled)

    @property
    def hermes_budget_s(self) -> int:
        """Adaptive Hermes synthesis budget = 35% of the active window, floored at 30s."""
        active = max(0, self.sprint_duration_s - self.final_windup_lead_s)
        return max(30, int(active * 0.35))

    def tier_of(self, source: str) -> SourceTier:
        return self.source_tier_map.get(source, SourceTier.OTHER)

    def sorted_tiers(self) -> list[SourceTier]:
        return _TIER_ORDER.copy()
