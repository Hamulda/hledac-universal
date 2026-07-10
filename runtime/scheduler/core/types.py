"""
Core type definitions for the sprint scheduler.

Extracted from runtime/sprint_scheduler.py (Phase 1 of modular decomposition).
All types here are independent — no circular deps with sprint_scheduler.py.

Canonical source for these types remains sprint_scheduler.py until fully migrated.
"""
from __future__ import annotations


from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Literal

import msgspec

# ── Source Tier (moved from sprint_scheduler.py L1544-L1601) ──────────────────


class SourceTier(Enum):
    """Feed source priority tier."""
    SURFACE = auto()       # high-value real-time feeds (news, alerts)
    STRUCTURED_TI = auto() # structured threat intel feeds
    DEEP = auto()          # deep/dark web, archive feeds
    ARCHIVE = auto()        # historical/wayback/archive feeds
    OTHER = auto()         # everything else -- processed only if time allows


_TIER_ORDER: list[SourceTier] = [
    SourceTier.SURFACE,
    SourceTier.STRUCTURED_TI,
    SourceTier.DEEP,
    SourceTier.ARCHIVE,
    SourceTier.OTHER,
]


# Sprint F228G: Default tier mapping for the fallback _DEFAULT_SOURCE_TYPES.
_DEFAULT_SOURCE_TIER_MAP: dict[str, SourceTier] = {
    "cisa_kev": SourceTier.STRUCTURED_TI,
    "threatfox_ioc": SourceTier.STRUCTURED_TI,
    "urlhaus_recent": SourceTier.STRUCTURED_TI,
    "feodo_ip": SourceTier.STRUCTURED_TI,
    "openphish_feed": SourceTier.STRUCTURED_TI,
}


# ── CT Bridge Loss Tracking (moved from sprint_scheduler.py L1617-L1673) ───────


class CTLossStage(Enum):
    """Enum describing where CT raw evidence is lost in the live bridge path."""

    NO_RAW = "no_raw"
    BRIDGE_NOT_INVOKED = "bridge_not_invoked"
    RAW_NOT_BRIDGED = "raw_not_bridged"
    UNSUPPORTED_RAW_SHAPE = "unsupported_raw_shape"
    ALL_REJECTED_BY_BRIDGE = "all_rejected_by_bridge"
    CANDIDATES_BUILT_NOT_ACCUMULATED = "candidates_built_not_accumulated"
    ACCUMULATED_NOT_STORED = "accumulated_not_stored"
    STORED_NOT_REPORTED = "stored_not_reported"
    NO_LOSS = "no_loss"
    UNKNOWN_LOSS = "unknown_loss"
    PROVIDER_FAILURE = "provider_failure"
    STALE_CACHE_USED = "stale_cache_used"


# ── Early Exit Classification (moved from sprint_scheduler.py L1961-L2019) ────


class EarlyExitClass:
    """Sprint F215D: Canonical early exit classification for sprint runs."""

    COMPLETED_FULL_DURATION = "completed_full_duration"
    EARLY_COMPLETE_NO_WORK_REMAINING = "early_complete_no_work_remaining"
    EARLY_COMPLETE_RETURN_GUARD_SATISFIED = "early_complete_return_guard_satisfied"
    EARLY_COMPLETE_FEED_ONLY = "early_complete_feed_only"
    EARLY_COMPLETE_PRELUDE_COMPLETE = "early_complete_prelude_complete"
    FEED_DOMINANT_NONFEED_RESCUE_ATTEMPTED = "feed_dominant_nonfeed_rescue_attempted"
    ABORTED_BY_MEMORY = "aborted_by_memory"
    ABORTED_BY_DEADLINE = "aborted_by_deadline"
    ABORTED_BY_ERROR = "aborted_by_error"


# ── Feed Dominance Guard Result (moved from sprint_scheduler.py L2039-L2056) ──


class FeedDominanceGuardResult(msgspec.Struct, frozen=True):
    """F214: Result of FeedDominanceGuard.compute()."""

    feed_dominance_ratio: float
    nonfeed_accepted_findings: int
    feed_dominance_class: str          # "balanced" | "feed_dominant" | "feed_only_like"
    should_recommend_nonfeed_diagnostic: bool
    guard_triggered: bool
    block_early_exit: bool
    reason: str


# ── Lane Budget (moved from sprint_scheduler.py L2069-L2093) ──────────────────

# Sprint F-ISSUE-155: Type-level enum for lane names — prevents string typos at type-check time.
LaneName = Literal["public", "feed", "ct", "dns", "passive", "structured", "deep", "hot", "warm", "cold"]


class LaneBudgetAllocation(msgspec.Struct):
    lane_name: LaneName
    allocated_s: float = 0.0
    consumed_s: float = 0.0
    released_s: float = 0.0
    timeout_count: int = 0


@dataclass
class LaneBudgetPool:
    """Per-lane timeout accounting pool."""
    _allocations: dict = field(default_factory=dict)
    _total_budget_s: float = 0.0

    def allocate(self, lane_name: LaneName, budget_s: float) -> None:
        if lane_name not in self._allocations:
            self._allocations[lane_name] = LaneBudgetAllocation(lane_name=lane_name)
        self._allocations[lane_name].allocated_s += budget_s
        self._total_budget_s += budget_s

    def consume(self, lane_name: LaneName, elapsed_s: float) -> None:
        if lane_name in self._allocations:
            self._allocations[lane_name].consumed_s += elapsed_s

    def release(self, lane_name: LaneName, remaining_s: float | None = None) -> float:
        if lane_name not in self._allocations:
            return 0.0
        alloc = self._allocations[lane_name]
        released = remaining_s if remaining_s is not None else alloc.allocated_s
        alloc.released_s += released
        self._total_budget_s -= released
        return released

    def total_allocated(self) -> float:
        return self._total_budget_s

    def timeout(self, lane_name: LaneName) -> None:
        if lane_name in self._allocations:
            self._allocations[lane_name].timeout_count += 1
