# sprint_policies.py — Sprint Policies domain
"""
Feed dominance guard and lane budget pool for sprint scheduling.
Implements feed dominance detection and per-lane budget allocation.








"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal
from _core._util import aclose

if TYPE_CHECKING:
    from hledac_rust_extensions import hledac_rust_extensions


# Sprint F-ISSUE-155: Type-level enum for lane names.
LaneName = Literal["public", "feed", "ct", "dns", "passive", "structured", "deep", "hot", "warm", "cold"]


# =============================================================================
# Shared helpers
# =============================================================================


def _feed_dominance_ratio_class(ratio: float) -> str:
    """Shared ratio classification — used by both Rust and Python FeedDominanceGuard."""
    if ratio >= 0.99:
        return "feed_only_like"
    if ratio >= 0.80:
        return "feed_dominant"
    if ratio >= 0.50:
        return "balanced"
    return "low"


# =============================================================================
# Feed Dominance Result
# =============================================================================


@dataclass(slots=True)
class _FeedDominanceResult:
    """Result object for FeedDominanceGuard.compute()."""

    feed_dominance_ratio: float
    nonfeed_accepted_findings: int
    feed_dominance_class: str
    should_recommend_nonfeed_diagnostic: bool
    guard_triggered: bool
    block_early_exit: bool
    reason: str


# =============================================================================
# Rust Sprint Policies Domain
# =============================================================================


class _RustSprintPoliciesDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: hledac_rust_extensions) -> None:
        self._ext = ext

    def FeedDominanceGuard(
        self,
        dominance_ratio_threshold: float = 0.95,
        min_nonfeed_findings: int = 5,
        strict: bool = False,
    ) -> _RustFeedDominanceGuard:
        return _RustFeedDominanceGuard(dominance_ratio_threshold, min_nonfeed_findings, strict, self._ext)

    def LaneBudgetPool(self) -> _RustLaneBudgetPool:
        return _RustLaneBudgetPool(self._ext)

    def compute_dominance(
        self,
        total_accepted: int,
        feed_accepted: int,
        nonfeed_accepted: int,
    ) -> dict[str, Any]:
        """Convenience method — wraps Rust compute_feed_dominance."""
        return self._ext.compute_feed_dominance(total_accepted, feed_accepted, nonfeed_accepted, 0.95, 5)


class _RustFeedDominanceGuard:
    """Wrapper that makes Rust compute_feed_dominance look like a FeedDominanceGuard class."""

    __slots__ = ("_threshold", "_min_nonfeed", "_strict", "_ext")

    def __init__(self, dominance_ratio_threshold: float, min_nonfeed_findings: int, strict: bool, ext: hledac_rust_extensions) -> None:
        self._threshold = dominance_ratio_threshold
        self._min_nonfeed = min_nonfeed_findings
        self._strict = strict
        self._ext = ext

    def compute(
        self,
        total_accepted: int,
        feed_accepted: int,
        nonfeed_accepted: int,
        **kwargs: Any,
    ) -> _FeedDominanceResult:
        d = self._ext.compute_feed_dominance(
            total_accepted, feed_accepted, nonfeed_accepted,
            self._threshold, self._min_nonfeed,
        )
        ratio = d["feed_dominance_ratio"]
        guard_triggered = ratio >= self._threshold and nonfeed_accepted < self._min_nonfeed
        block_early_exit = self._strict and guard_triggered
        return _FeedDominanceResult(
            feed_dominance_ratio=ratio,
            nonfeed_accepted_findings=nonfeed_accepted,
            feed_dominance_class=d["feed_dominance_class"],
            should_recommend_nonfeed_diagnostic=guard_triggered,
            guard_triggered=guard_triggered,
            block_early_exit=block_early_exit,
            reason=d["reason"],
        )

    def compute_simple(self, total_accepted: int, feed_accepted: int, nonfeed_accepted: int) -> _FeedDominanceResult:
        return self.compute(total_accepted, feed_accepted, nonfeed_accepted)

    @staticmethod
    def ratio_class(ratio: float) -> str:
        return _feed_dominance_ratio_class(ratio)


class _RustLaneBudgetPool:
    """Lane budget pool — uses Python fallback for state since Rust standalone functions are incompatible."""

    __slots__ = ("_pool",)

    def __init__(self, ext: hledac_rust_extensions) -> None:
        # Rust pool functions use standalone API with dict state that's incompatible
        # with the class-based API expected by callers. Delegate to Python fallback.
        self._pool: PythonLaneBudgetPool = PythonLaneBudgetPool()

    def allocate(self, lane_name: LaneName, budget_s: float) -> None:
        self._pool.allocate(lane_name, budget_s)

    def consume(self, lane_name: LaneName, elapsed_s: float) -> None:
        self._pool.consume(lane_name, elapsed_s)

    def release(self, lane_name: LaneName, remaining_s: float | None = None) -> float:
        return self._pool.release(lane_name, remaining_s)

    def get_utilization(self) -> float:
        return self._pool.get_utilization()

    def get_lane_stats(self) -> dict[str, Any]:
        return self._pool.get_lane_stats()

    def lane_count(self) -> int:
        return self._pool.lane_count()


# =============================================================================
# Python Sprint Policies Domain
# =============================================================================


class _PythonSprintPoliciesDomain:
    __slots__ = ()

    def FeedDominanceGuard(
        self,
        dominance_ratio_threshold: float = 0.95,
        min_nonfeed_findings: int = 5,
        strict: bool = False,
    ) -> PythonFeedDominanceGuard:
        return PythonFeedDominanceGuard(dominance_ratio_threshold, min_nonfeed_findings, strict)

    def LaneBudgetPool(self) -> PythonLaneBudgetPool:
        return PythonLaneBudgetPool()

    @staticmethod
    def compute_dominance(
        total_accepted: int,
        feed_accepted: int,
        nonfeed_accepted: int,
    ) -> dict[str, Any]:
        """Convenience method — pure-Python fallback for compute_feed_dominance."""
        if total_accepted == 0:
            return {"feed_dominance_ratio": 0.0, "guard_triggered": False}
        ratio = feed_accepted / total_accepted
        guard_triggered = ratio >= 0.95 and nonfeed_accepted < 5
        return {"feed_dominance_ratio": ratio, "guard_triggered": guard_triggered}


# =============================================================================
# Python Feed Dominance Guard
# =============================================================================


@dataclass(slots=True)
class PythonFeedDominanceGuardResult:
    """Result object for PythonFeedDominanceGuard.compute()."""

    feed_dominance_ratio: float
    nonfeed_accepted_findings: int
    feed_dominance_class: str
    should_recommend_nonfeed_diagnostic: bool
    guard_triggered: bool
    block_early_exit: bool
    reason: str


class PythonFeedDominanceGuard:
    __slots__ = ("_threshold", "_min_nonfeed", "_strict")

    def __init__(self, dominance_ratio_threshold: float, min_nonfeed_findings: int, strict: bool) -> None:
        self._threshold = dominance_ratio_threshold
        self._min_nonfeed = min_nonfeed_findings
        self._strict = strict

    def compute(
        self,
        total_accepted: int,
        feed_accepted: int,
        nonfeed_accepted: int,
        **kwargs: Any,
    ) -> PythonFeedDominanceGuardResult:
        # Zero findings case - return balanced without computing ratio
        if total_accepted == 0:
            return PythonFeedDominanceGuardResult(
                feed_dominance_ratio=0.0,
                nonfeed_accepted_findings=0,
                feed_dominance_class="balanced",
                should_recommend_nonfeed_diagnostic=False,
                guard_triggered=False,
                block_early_exit=False,
                reason="zero findings",
            )

        ratio = feed_accepted / total_accepted
        cls = _feed_dominance_ratio_class(ratio)
        guard_triggered = ratio >= self._threshold and nonfeed_accepted < self._min_nonfeed
        block_early_exit = self._strict and guard_triggered

        if guard_triggered:
            reason = f"Feed dominance ratio {ratio:.2%} >= {self._threshold:.0%} and nonfeed findings {nonfeed_accepted} < {self._min_nonfeed}"
        else:
            reason = f"Feed dominance ratio {ratio:.2%} within acceptable bounds"

        return PythonFeedDominanceGuardResult(
            feed_dominance_ratio=ratio,
            nonfeed_accepted_findings=nonfeed_accepted,
            feed_dominance_class=cls,
            should_recommend_nonfeed_diagnostic=guard_triggered,
            guard_triggered=guard_triggered,
            block_early_exit=block_early_exit,
            reason=reason,
        )

    def compute_simple(self, total_accepted: int, feed_accepted: int, nonfeed_accepted: int) -> PythonFeedDominanceGuardResult:
        return self.compute(total_accepted, feed_accepted, nonfeed_accepted)

    @staticmethod
    def ratio_class(ratio: float) -> str:
        return _feed_dominance_ratio_class(ratio)


# =============================================================================
# Python Lane Budget Pool
# =============================================================================


class PythonLaneBudgetAllocation:
    """Per-lane budget slot — pure Python fallback."""

    __slots__ = ("lane_name", "allocated_s", "consumed_s", "released_s", "timeout_count")

    def __init__(self, lane_name: LaneName, budget_s: float = 0.0) -> None:
        self.lane_name = lane_name
        self.allocated_s = budget_s
        self.consumed_s = 0.0
        self.released_s = 0.0
        self.timeout_count = 0

    def utilization(self) -> float:
        if self.allocated_s <= 0.0:
            return 0.0
        return min(self.consumed_s / self.allocated_s, 1.0)

    def remaining_s(self) -> float:
        return max(self.allocated_s - self.consumed_s - self.released_s, 0.0)


class PythonLaneBudgetPool:
    """Per-lane timeout accounting pool — pure Python fallback."""

    __slots__ = ("_allocations",)

    def __init__(self) -> None:
        self._allocations: dict[LaneName, PythonLaneBudgetAllocation] = {}

    def allocate(self, lane_name: LaneName, budget_s: float) -> None:
        if lane_name in self._allocations:
            # Update existing allocation
            alloc = self._allocations[lane_name]
            alloc.allocated_s = budget_s
        else:
            self._allocations[lane_name] = PythonLaneBudgetAllocation(lane_name, budget_s)

    def consume(self, lane_name: LaneName, elapsed_s: float) -> None:
        if lane_name not in self._allocations:
            return
        alloc = self._allocations[lane_name]
        alloc.consumed_s += elapsed_s

    def release(self, lane_name: LaneName, remaining_s: float | None = None) -> float:
        if lane_name not in self._allocations:
            return 0.0
        alloc = self._allocations[lane_name]
        if remaining_s is not None:
            alloc.released_s = remaining_s
        return alloc.remaining_s()

    def get_utilization(self) -> float:
        if not self._allocations:
            return 0.0
        total_allocated = sum(a.allocated_s for a in self._allocations.values())
        total_consumed = sum(a.consumed_s for a in self._allocations.values())
        if total_allocated <= 0:
            return 0.0
        return min(total_consumed / total_allocated, 1.0)

    def get_lane_stats(self) -> dict[str, Any]:
        return {
            name: {
                "allocated_s": alloc.allocated_s,
                "consumed_s": alloc.consumed_s,
                "remaining_s": alloc.remaining_s(),
                "utilization": alloc.utilization(),
            }
            for name, alloc in self._allocations.items()
        }

    def lane_count(self) -> int:
        return len(self._allocations)

    def total_allocated_s(self) -> float:
        return sum(a.allocated_s for a in self._allocations.values())

    def lane_utilization(self, lane_name: LaneName) -> float:
        if lane_name not in self._allocations:
            return 0.0
        return self._allocations[lane_name].utilization()

    def lane_remaining_s(self, lane_name: LaneName) -> float:
        if lane_name not in self._allocations:
            return 0.0
        return self._allocations[lane_name].remaining_s()

    def clear(self) -> None:
        self._allocations.clear()

    def __repr__(self) -> str:
        return f"PythonLaneBudgetPool(lanes={list(self._allocations.keys())})"


def get_sprint_policies_domain(ext: object | None) -> _RustSprintPoliciesDomain | _PythonSprintPoliciesDomain:
    """Factory: return Rust or Python SprintPoliciesDomain based on ext availability."""
    if ext is not None:
        try:
            return _RustSprintPoliciesDomain(ext)
        except Exception:  # noqa: BLE001
            pass
    return _PythonSprintPoliciesDomain()
