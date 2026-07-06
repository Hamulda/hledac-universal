"""STEP 1 — SprintSchedulerConfig + supporting types.

Extracted from sprint_scheduler.py (33 449 LOC → modular package).
F350M-R / Issue #P2.

All changes are backward-compatible: the original class remains in
runtime/sprint_scheduler.py and is re-imported / aliased there.
This file is the canonical home for the type definitions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from runtime.sprint_scheduler import IntCounterLayoutProto

logger = logging.getLogger(__name__)


# ── Source tier ────────────────────────────────────────────────────────────────

# Tier priority (high→low): surface → structured_ti → deep → archive → other

class SourceTier(Enum):
    SURFACE = auto()       # SERP, public DNS, direct crawls
    STRUCTURED_TI = auto()  # Certificate Transparency, Passive DNS, RDAP
    DEEP = auto()          # Wayback, CommonCrawl, dark pivots
    ARCHIVE = auto()       # academic, IPFS, DHT
    OTHER = auto()         # anything not mapped above

    def __repr__(self) -> str:
        return f"SourceTier.{self.name}"


_TIER_ORDER = [
    SourceTier.SURFACE,
    SourceTier.STRUCTURED_TI,
    SourceTier.DEEP,
    SourceTier.ARCHIVE,
    SourceTier.OTHER,
]


# ── Feed dominance guard ───────────────────────────────────────────────────────

@dataclass
class EarlyExitClass:
    """Canonical early-exit classification labels."""
    NATURAL = "natural"                        # hard deadline hit, all lanes terminal
    FEED_DOMINANT = "feed_dominant"           # feed > 95%, nonfeed exhausted
    NONFEED_ONLY = "nonfeed_only"             # feed suppressed by nonfeed budget
    PREWINDUP_TIMEOUT = "prewindup_timeout"   # pre-windup barrier timed out
    ABORT = "abort"                            # explicit abort / SIGINT
    UNKNOWN = "unknown"


@dataclass
class FeedDominanceGuardResult:
    should_suppress: bool
    reason: str
    feed_ratio: float
    nonfeed_terminal: bool


@dataclass
class LaneBudgetAllocation:
    lane_name: str
    budget_s: float
    allocated_at: float


@dataclass
class LaneBudgetPool:
    """Sliding-window budget pool for lane time accounting.

    M1 8GB: bounded dict, no growing caches.
    """
    _budgets: dict[str, float] = field(default_factory=dict)
    _consumed: dict[str, float] = field(default_factory=dict)
    _release_hook: Any = None  # callable(lane, released_s) | None

    def allocate(self, lane_name: str, budget_s: float) -> None:
        self._budgets[lane_name] = budget_s
        self._consumed[lane_name] = 0.0

    def consume(self, lane_name: str, elapsed_s: float) -> None:
        self._consumed[lane_name] = self._consumed.get(lane_name, 0.0) + elapsed_s

    def release(self, lane_name: str, remaining_s: float | None = None) -> float:
        released = 0.0
        if remaining_s is not None and lane_name in self._budgets:
            budget = self._budgets[lane_name]
            consumed = self._consumed.get(lane_name, 0.0)
            released = max(0.0, budget - consumed - remaining_s)
        self._budgets.pop(lane_name, None)
        self._consumed.pop(lane_name, None)
        if released > 0 and self._release_hook:
            try:
                self._release_hook(lane_name, released)
            except Exception:
                pass
        return released

    def get_utilization(self) -> float:
        total = sum(self._budgets.values())
        consumed = sum(self._consumed.values())
        return consumed / total if total > 0 else 0.0

    def get_lane_stats(self) -> dict[str, Any]:
        return {
            name: {"budget": self._budgets.get(name, 0), "consumed": self._consumed.get(name, 0)}
            for name in self._budgets
        }


class FeedDominanceGuard:
    """Blocks feed-only early exit when nonfeed lanes haven't reached terminal state."""

    def compute(
        self,
        total_accepted: int,
        feed_accepted: int,
        nonfeed_accepted: int,
        eligible_nonfeed_lanes_terminal: bool = False,
        nonfeed_diagnostic_timed_out: bool = False,
    ) -> FeedDominanceGuardResult:
        if total_accepted == 0:
            return FeedDominanceGuardResult(
                should_suppress=False,
                reason="no_evidence_yet",
                feed_ratio=0.0,
                nonfeed_terminal=eligible_nonfeed_lanes_terminal,
            )
        feed_ratio = feed_accepted / total_accepted
        if feed_ratio > 0.95 and nonfeed_accepted < 5 and not eligible_nonfeed_lanes_terminal:
            return FeedDominanceGuardResult(
                should_suppress=True,
                reason="feed_dominant_awaiting_nonfeed",
                feed_ratio=feed_ratio,
                nonfeed_terminal=False,
            )
        return FeedDominanceGuardResult(
            should_suppress=False,
            reason="balanced_or_nonfeed_sufficient",
            feed_ratio=feed_ratio,
            nonfeed_terminal=eligible_nonfeed_lanes_terminal,
        )


# ── SprintSchedulerConfig ─────────────────────────────────────────────────────

# Sentinel for "unset" on optional fields that also have non-None defaults
_UNSET: Any = object()


@dataclass
class SprintSchedulerConfig:
    """Configuration for one sprint run.

    STEP 1 extracted from sprint_scheduler.py (33 449 LOC → modular package).
    F350M-R / Issue #P2.
    """

    sprint_duration_s: float = 1800.0          # 30 min

    windup_lead_s: float = 180.0              # enter wind-down 3 min before end

    cycle_sleep_s: float = 5.0                 # sleep between cycles

    # Sprint F-A3: per-cycle hard deadline. Wraps `_run_one_cycle` in
    # `asyncio.timeout(cycle_budget_s)`. If a cycle exceeds this budget,
    # TimeoutError propagates and the cycle is counted as empty so the F228G
    # consecutive-empty-cycles guard eventually forces windup.
    cycle_budget_s: float = 60.0                # hard per-cycle deadline

    max_cycles: int = 100                      # safety cap

    max_parallel_sources: int = 4              # concurrent source fetches

    stop_on_first_accepted: bool = False       # early exit on first accepted

    export_enabled: bool = True

    export_dir: str = ""

    max_entries_per_cycle: int = 50             # per-source cap

    # Sprint F193B: Hypothesis -> finding feedback loop caps
    max_hypothesis_depth: int = 3              # max iteration depth for hypothesis-driven pivots
    max_hypothesis_queries: int = 10           # max total hypothesis-driven pivot queries

    # Aggressive mode: fans out feed/public/CT branches concurrently per cycle
    aggressive_mode: bool = False              # if True, run branches in parallel
    aggressive_branch_timeout_s: float = 45.0  # per-branch timeout in aggressive mode

    # Sprint F195B: Per-branch timeout budget in seconds (aggressive mode uses 8.0)
    branch_timeout_budget_s: float = 0.0       # 0 = use aggressive_branch_timeout_s

    _MAX_BRANCH_TIMEOUT_CAP: float = 300.0    # absolute per-branch cap

    # F273A: Dynamic branch-remaining floor
    _MIN_BRANCH_REMAINING_S_DEFAULT: float = 2.0
    _MIN_BRANCH_REMAINING_S_CAP: float = 5.0
    _MIN_BRANCH_REMAINING_S: float = 2.0       # back-compat alias

    partial_export_findings_interval: int = 10

    # Tier budgets in seconds — Sources NOT listed here fall to OTHER tier
    source_tier_map: dict[str, SourceTier] = field(default_factory=dict)

    # F223A: Explicit acquisition profile -- overrides env var / profile-name inference
    acquisition_profile: str | None = None

    # Sprint F214: Optional strict feed dominance guard
    require_nonfeed_corrob_for_early_exit: bool = False

    # Sprint F250: Preferred transport for sensitive queries
    sensitive_query_transport: str = "auto"

    # F233C: Optional predecessor sprint_id for next_sprint_seeds consumption
    predecessor_sprint_id: str | None = None

    # F11: Deep research advisory (post-WINDUP, fire-and-forget)
    deep_research_enabled: bool = False
    extreme_mode: bool = False

    # ── Computed properties ────────────────────────────────────────────────────

    @property
    def effective_windup_lead_s(self) -> float:
        """Adaptive windup that scales with sprint duration.

        F290: Short sprints get smaller windup overhead.
        F285: Explicit windup_lead_s (non-default 180.0) passes through directly.
        F273B + F288: Aggressive mode → 15% ratio.
        """
        if self.windup_lead_s != 180.0:
            return float(max(30.0, min(180.0, float(self.windup_lead_s))))
        if self.aggressive_mode:
            ratio = 0.15
        elif self.sprint_duration_s <= 120.0:
            ratio = 0.20
        elif self.sprint_duration_s <= 300.0:
            ratio = 0.25
        else:
            ratio = 0.30
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
            ratio = 0.20
        elif self.sprint_duration_s <= 300.0:
            ratio = 0.25
        else:
            ratio = 0.30
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
