"""R13: Lane Balancer for SprintScheduler v2.

Feed Dominance Guard + Lane Budget Pool integration for scheduler_v2.

Architecture:
    - LaneBudgetPool: Adaptive per-lane budget limiter for discovery lanes
    - FeedDominanceGuard: Prevents feed monopolization during sprint acquisition

Rust backend: _core.rust_backend.sprint_policies (compute_dominance, lane pool)
Python fallback: _core.rust_backend.sprint_policies (PythonLaneBudgetPool, PythonFeedDominanceGuard)

Integration points:
    - AcquisitionOrchestrator: Uses FeedDominanceGuard to check feed/nonfeed ratio
    - _v2_init.py: Injects lane_balancer into SprintContext
    - protocol.py: SprintContext extended with lane_balancer field

Usage:
    lb = LaneBalancer()
    lb.allocate_lane('discovery', 60.0)  # 60s budget for discovery
    lb.consume_lane('discovery', 5.5)     # 5.5s consumed
    
    # Check feed dominance before accepting findings
    dom_result = lb.check_dominance(
        total_accepted=100,
        feed_accepted=96,
        nonfeed_accepted=4,
    )
    if dom_result.guard_triggered:
        logger.warning("Feed dominance detected: %s", dom_result.reason)
"""

from __future__ import annotations

import logging
import threading
import time as _time
from dataclasses import dataclass as _dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    pass


log = logging.getLogger(__name__)


# =============================================================================
# Lane Names — Extended for sprint lanes (C11 pattern)
# =============================================================================

# Sprint lanes for discovery + original classification lanes
LaneName = Literal[
    "discovery", "ioc_validation", "enrichment",  # C11: sprint lanes
    "public", "feed", "ct", "dns", "passive", "structured", "deep", "hot", "warm", "cold"  # Original
]


# =============================================================================
# Policy Import Pattern — Reuse from Rust backend
# =============================================================================

# Rust availability flag and thread lock
_RustAvailable: bool = False
_PolicyLock: threading.RLock | None = None

# Policy classes (initialized by _init_policies)
_PythonFeedDominanceGuard: type | None = None
_PythonLaneBudgetPool: type | None = None
_RustFeedDominanceGuard: type | None = None  # Direct Rust class if available
_RustLaneBudgetPool: type | None = None

# Result class — will be set by _init_policies
FeedDominanceResult: type | None = None


# =============================================================================
# Inline Fallback Classes (only used when Rust backend module unavailable)
# =============================================================================

def _feed_dominance_ratio_class(ratio: float) -> str:
    """Classify feed dominance ratio — shared with Rust backend."""
    if ratio >= 0.99:
        return "feed_only_like"
    if ratio >= 0.80:
        return "feed_dominant"
    if ratio >= 0.50:
        return "balanced"
    return "low"


@_dataclass(slots=True)
class _InlineFeedDominanceGuardResult:
    """Result object for FeedDominanceGuard.compute() — inline fallback."""
    feed_dominance_ratio: float
    nonfeed_accepted_findings: int
    feed_dominance_class: str
    should_recommend_nonfeed_diagnostic: bool
    guard_triggered: bool
    block_early_exit: bool
    reason: str


class _InlineFeedDominanceGuard:
    """Inline Python FeedDominanceGuard fallback — used only when Rust backend module unavailable."""
    __slots__ = ("_threshold", "_min_nonfeed", "_strict")

    def __init__(self, dominance_ratio_threshold: float = 0.95, min_nonfeed_findings: int = 5, strict: bool = False) -> None:
        self._threshold = dominance_ratio_threshold
        self._min_nonfeed = min_nonfeed_findings
        self._strict = strict

    def compute(
        self,
        total_accepted: int,
        feed_accepted: int,
        nonfeed_accepted: int,
        **kwargs: Any,
    ) -> _InlineFeedDominanceGuardResult:
        if total_accepted == 0:
            return _InlineFeedDominanceGuardResult(
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

        return _InlineFeedDominanceGuardResult(
            feed_dominance_ratio=ratio,
            nonfeed_accepted_findings=nonfeed_accepted,
            feed_dominance_class=cls,
            should_recommend_nonfeed_diagnostic=guard_triggered,
            guard_triggered=guard_triggered,
            block_early_exit=block_early_exit,
            reason=reason,
        )

    def compute_simple(self, total_accepted: int, feed_accepted: int, nonfeed_accepted: int) -> _InlineFeedDominanceGuardResult:
        return self.compute(total_accepted, feed_accepted, nonfeed_accepted)


# Inline lane budget allocation (used by _InlineLaneBudgetPool)
class _InlineLaneBudgetAllocation:
    """Per-lane budget slot — inline fallback."""
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


class _InlineLaneBudgetPool:
    """Inline Python LaneBudgetPool fallback."""
    __slots__ = ("_allocations",)

    def __init__(self) -> None:
        self._allocations: dict[LaneName, _InlineLaneBudgetAllocation] = {}

    def allocate(self, lane_name: LaneName, budget_s: float) -> None:
        if lane_name in self._allocations:
            self._allocations[lane_name].allocated_s = budget_s
        else:
            self._allocations[lane_name] = _InlineLaneBudgetAllocation(lane_name, budget_s)

    def consume(self, lane_name: LaneName, elapsed_s: float) -> None:
        if lane_name not in self._allocations:
            return
        self._allocations[lane_name].consumed_s += elapsed_s

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


def _init_policies() -> None:
    """Initialize policy classes from Rust backend with Python fallback."""
    global _PythonFeedDominanceGuard, _PythonLaneBudgetPool, _RustAvailable, _PolicyLock
    global _RustFeedDominanceGuard, _RustLaneBudgetPool, FeedDominanceResult
    
    if _PolicyLock is None:
        _PolicyLock = threading.RLock()
    
    with _PolicyLock:
        if _PythonFeedDominanceGuard is not None:
            return  # Already initialized
        
        # Try Rust backend first
        try:
            from _core.rust_backend import rust as _rust
            
            if _rust.is_available:
                _RustAvailable = True
                # Get Rust domain wrappers
                _RustFeedDominanceGuard = _rust.sprint_policies.FeedDominanceGuard
                _RustLaneBudgetPool = _rust.sprint_policies.LaneBudgetPool
                log.debug("[LaneBalancer] Rust sprint_policies loaded (RustAvailable=True)")
            else:
                log.debug("[LaneBalancer] Rust not available, using Python fallbacks")
        except Exception as _e:
            log.debug("[LaneBalancer] Rust sprint_policies unavailable: %s", _e)
            _RustFeedDominanceGuard = None
            _RustLaneBudgetPool = None
        
        # Always import Python fallbacks from Rust backend module
        try:
            from _core.rust_backend.sprint_policies import (
                PythonFeedDominanceGuard as _PFG,
                PythonLaneBudgetPool as _PLBP,
                PythonFeedDominanceGuardResult as _FeedResult,
            )
            _PythonFeedDominanceGuard = _PFG
            _PythonLaneBudgetPool = _PLBP
            FeedDominanceResult = _FeedResult
            log.debug("[LaneBalancer] Python fallbacks loaded from Rust backend module")
        except ImportError:
            # Ultimate fallback — use inline implementations
            _PythonFeedDominanceGuard = None
            _PythonLaneBudgetPool = None
            FeedDominanceResult = _InlineFeedDominanceGuardResult
            log.warning("[LaneBalancer] Using inline fallbacks")


# =============================================================================
# Lane Balancer — Main Class
# =============================================================================

class LaneBalancer:
    """R13: Adaptive lane balancer for SprintScheduler v2.

    Combines:
    - FeedDominanceGuard: Prevents feed monopolization
    - LaneBudgetPool: Per-lane budget accounting for adaptive limiting

    Thread-safe via RLock on all public methods.

    Usage:
        lb = LaneBalancer()
        
        # Per-cycle dominance check
        result = lb.check_dominance(
            total_accepted=100,
            feed_accepted=96,
            nonfeed_accepted=4,
        )
        
        # Per-lane budget management
        lb.allocate_lane('discovery', 60.0)
        lb.consume_lane('discovery', 5.5)
        
        # Check if lane is at risk
        if lb.is_lane_at_risk('discovery'):
            logger.warning("Discovery lane at risk: %s", lb.get_lane_stats()['discovery'])
    """

    __slots__ = (
        "_fg",           # FeedDominanceGuard instance
        "_pool",         # LaneBudgetPool instance
        "_lock",         # Thread safety
        "_last_check",   # Last dominance check time
        "_dominance_threshold",  # Configurable threshold
        "_min_nonfeed_findings", # Configurable min nonfeed
        "_strict",       # Strict mode blocks early exit
        "_feed_count",   # Feed findings count for dominance tracking
        "_nonfeed_count", # Nonfeed findings count for dominance tracking
    )

    # Default lane budgets (seconds) — C11 pattern
    DEFAULT_LANE_BUDGETS: dict[str, float] = {
        "discovery": 120.0,      # Discovery phase budget
        "ioc_validation": 60.0,  # IOC validation budget
        "enrichment": 90.0,     # Enrichment budget
        "public": 30.0,         # Public source budget
        "feed": 20.0,           # Feed source budget
        "ct": 15.0,             # Certificate transparency budget
        "dns": 10.0,            # DNS budget
        "passive": 10.0,        # Passive DNS budget
        "structured": 25.0,     # Structured data budget
        "deep": 40.0,           # Deep/dark web budget
        "hot": 5.0,             # Hot/recent budget
        "warm": 15.0,           # Warm budget
        "cold": 45.0,           # Cold/archived budget
    }

    def __init__(
        self,
        dominance_ratio_threshold: float = 0.95,
        min_nonfeed_findings: int = 5,
        strict: bool = False,
        lane_budgets: dict[str, float] | None = None,
    ) -> None:
        """Initialize LaneBalancer with configurable parameters.

        Args:
            dominance_ratio_threshold: Feed dominance ratio threshold (0.0-1.0)
            min_nonfeed_findings: Minimum nonfeed findings before guard triggers
            strict: If True, guard blocks early exit when triggered
            lane_budgets: Optional dict of lane budgets (seconds). Defaults to DEFAULT_LANE_BUDGETS.
        """
        # Initialize policies lazily
        _init_policies()

        # Create FeedDominanceGuard
        if _RustFeedDominanceGuard is not None:
            self._fg = _RustFeedDominanceGuard(dominance_ratio_threshold, min_nonfeed_findings, strict)
        elif _PythonFeedDominanceGuard is not None:
            self._fg = _PythonFeedDominanceGuard(dominance_ratio_threshold, min_nonfeed_findings, strict)
        else:
            self._fg = _InlineFeedDominanceGuard(dominance_ratio_threshold, min_nonfeed_findings, strict)

        # Create LaneBudgetPool
        if _RustLaneBudgetPool is not None:
            self._pool = _RustLaneBudgetPool()
        elif _PythonLaneBudgetPool is not None:
            self._pool = _PythonLaneBudgetPool()
        else:
            self._pool = _InlineLaneBudgetPool()

        # Thread safety
        self._lock = threading.RLock()

        # State
        self._last_check: float = 0.0
        self._dominance_threshold = dominance_ratio_threshold
        self._min_nonfeed_findings = min_nonfeed_findings
        self._strict = strict
        
        # Per-cycle dominance tracking
        self._feed_count: int = 0
        self._nonfeed_count: int = 0

        # Initialize default lane budgets
        budgets = lane_budgets or self.DEFAULT_LANE_BUDGETS
        for lane, budget in budgets.items():
            try:
                self._pool.allocate(lane, budget)
            except Exception:
                pass  # Fail soft — don't crash on unknown lanes

        log.debug(
            "[LaneBalancer] Initialized (threshold=%.2f, min_nonfeed=%d, strict=%s, lanes=%d)",
            dominance_ratio_threshold,
            min_nonfeed_findings,
            strict,
            self._pool.lane_count(),
        )

    # ── Feed Dominance Guard API ────────────────────────────────────────────

    def check_dominance(
        self,
        total_accepted: int | None = None,
        feed_accepted: int | None = None,
        nonfeed_accepted: int | None = None,
    ) -> Any:
        """Check feed dominance ratio.

        Can be called with explicit counts OR using internal tracking:
          - check_dominance(100, 96, 4) - explicit counts
          - check_dominance() - uses internal _feed_count/_nonfeed_count

        Args:
            total_accepted: Total accepted findings (optional if using internal tracking)
            feed_accepted: Feed-sourced accepted findings (optional)
            nonfeed_accepted: Nonfeed (public, CT, DNS, etc.) accepted findings (optional)

        Returns:
            FeedDominanceResult with dominance analysis
        """
        with self._lock:
            self._last_check = _time.monotonic()
            
            # Use internal tracking if counts not provided
            if total_accepted is None:
                total_accepted = self._feed_count + self._nonfeed_count
                feed_accepted = self._feed_count
                nonfeed_accepted = self._nonfeed_count

            # Use compute_simple for hot path (consistent API)
            if hasattr(self._fg, 'compute_simple'):
                result = self._fg.compute_simple(total_accepted, feed_accepted or 0, nonfeed_accepted or 0)
            elif hasattr(self._fg, 'compute'):
                result = self._fg.compute(total_accepted, feed_accepted or 0, nonfeed_accepted or 0)
            else:
                # Fallback for other implementations
                result = self._fg.check(total_accepted, feed_accepted or 0, nonfeed_accepted or 0)

            log.log(
                logging.DEBUG - 1 if log.isEnabledFor(logging.DEBUG - 1) else logging.DEBUG,
                "[LaneBalancer] Dominance check: ratio=%.3f, class=%s, triggered=%s",
                result.feed_dominance_ratio,
                result.feed_dominance_class,
                result.guard_triggered,
            )

            return result

    def record_findings(self, feed_count: int = 0, nonfeed_count: int = 0) -> None:
        """Record finding counts for dominance tracking.
        
        Args:
            feed_count: Number of feed-sourced findings
            nonfeed_count: Number of nonfeed findings
        """
        with self._lock:
            self._feed_count += feed_count
            self._nonfeed_count += nonfeed_count

    def reset_cycle_counts(self) -> None:
        """Reset per-cycle finding counts."""
        with self._lock:
            self._feed_count = 0
            self._nonfeed_count = 0

    @property
    def feed_count(self) -> int:
        """Current feed finding count."""
        with self._lock:
            return self._feed_count

    @property
    def nonfeed_count(self) -> int:
        """Current nonfeed finding count."""
        with self._lock:
            return self._nonfeed_count

    @property
    def last_dominance_check(self) -> float:
        """Monotonic timestamp of last dominance check."""
        return self._last_check

    @property
    def dominance_threshold(self) -> float:
        """Configured dominance ratio threshold."""
        return self._dominance_threshold

    @property
    def min_nonfeed_findings(self) -> int:
        """Configured minimum nonfeed findings."""
        return self._min_nonfeed_findings

    # ── Lane Budget Pool API ───────────────────────────────────────────────

    def allocate_lane(self, lane_name: LaneName, budget_s: float) -> None:
        """Allocate budget for a lane.

        Args:
            lane_name: Lane identifier
            budget_s: Budget in seconds
        """
        with self._lock:
            self._pool.allocate(lane_name, budget_s)
            log.debug("[LaneBalancer] Allocated %.1fs for lane '%s'", budget_s, lane_name)

    def consume_lane(self, lane_name: LaneName, elapsed_s: float) -> None:
        """Consume budget from a lane.

        Args:
            lane_name: Lane identifier
            elapsed_s: Elapsed seconds to consume
        """
        with self._lock:
            self._pool.consume(lane_name, elapsed_s)

    def release_lane(self, lane_name: LaneName, remaining_s: float | None = None) -> float:
        """Release unused budget from a lane.

        Args:
            lane_name: Lane identifier
            remaining_s: Remaining budget (or None to release all)

        Returns:
            Released seconds
        """
        with self._lock:
            return self._pool.release(lane_name, remaining_s)

    def get_lane_utilization(self) -> float:
        """Get overall lane pool utilization (0.0-1.0)."""
        with self._lock:
            return self._pool.get_utilization()

    def get_lane_stats(self) -> dict[str, Any]:
        """Get per-lane statistics."""
        with self._lock:
            return self._pool.get_lane_stats()

    def lane_utilization(self, lane_name: LaneName) -> float:
        """Get utilization for a specific lane (0.0-1.0)."""
        with self._lock:
            return self._pool.lane_utilization(lane_name)

    def lane_remaining_s(self, lane_name: LaneName) -> float:
        """Get remaining budget for a lane in seconds."""
        with self._lock:
            return self._pool.lane_remaining_s(lane_name)

    def is_lane_at_risk(self, lane_name: LaneName, threshold: float = 0.80) -> bool:
        """Check if lane utilization exceeds threshold.

        Args:
            lane_name: Lane identifier
            threshold: Utilization threshold (default 0.80 = 80%)

        Returns:
            True if lane is at risk (utilization >= threshold)
        """
        util = self.lane_utilization(lane_name)
        return util >= threshold

    def get_lanes_needing_attention(self, threshold: float = 0.80) -> list[str]:
        """Get list of lanes with utilization >= threshold.

        Args:
            threshold: Utilization threshold (default 0.80 = 80%)

        Returns:
            List of lane names needing attention
        """
        stats = self.get_lane_stats()
        return [
            name for name, stat in stats.items()
            if stat["utilization"] >= threshold
        ]

    def record_lane_timeout(self, lane_name: LaneName) -> None:
        """Record a timeout for a lane.

        Args:
            lane_name: Lane identifier
        """
        with self._lock:
            if hasattr(self._pool, 'timeout'):
                self._pool.timeout(lane_name)
            elif hasattr(self._pool, '_allocations') and lane_name in self._pool._allocations:
                self._pool._allocations[lane_name].timeout_count += 1

    def reset_lane(self, lane_name: LaneName) -> None:
        """Reset a lane's budget to default.

        Args:
            lane_name: Lane identifier
        """
        with self._lock:
            default_budget = self.DEFAULT_LANE_BUDGETS.get(lane_name, 30.0)
            self._pool.allocate(lane_name, default_budget)
            log.debug("[LaneBalancer] Reset lane '%s' to %.1fs", lane_name, default_budget)

    def clear(self) -> None:
        """Clear all lane budgets."""
        with self._lock:
            if hasattr(self._pool, 'clear'):
                self._pool.clear()
            log.debug("[LaneBalancer] Cleared all lane budgets")

    @property
    def lane_count(self) -> int:
        """Number of active lanes."""
        with self._lock:
            return self._pool.lane_count()

    @property
    def total_allocated_s(self) -> float:
        """Total allocated budget in seconds."""
        with self._lock:
            if hasattr(self._pool, 'total_allocated_s'):
                return self._pool.total_allocated_s()
            return 0.0

    # ── Adaptive Balancing API ─────────────────────────────────────────────

    def adaptive_allocate(
        self,
        lane_name: LaneName,
        base_budget_s: float,
        multiplier: float = 1.0,
    ) -> float:
        """Adaptively allocate budget based on lane performance.

        Args:
            lane_name: Lane identifier
            base_budget_s: Base budget in seconds
            multiplier: Budget multiplier (0.5-2.0 range recommended)

        Returns:
            Actual allocated budget
        """
        with self._lock:
            util = self.lane_utilization(lane_name)
            
            # If lane is under-utilized, give more budget
            if util < 0.5:
                adjusted = base_budget_s * min(multiplier, 2.0)
            # If lane is over-utilized, reduce budget
            elif util > 0.8:
                adjusted = base_budget_s * max(multiplier * 0.5, 0.25)
            else:
                adjusted = base_budget_s * multiplier

            self._pool.allocate(lane_name, adjusted)
            return adjusted

    def check_balance(self) -> dict[str, Any]:
        """Periodic balance check with structured output.

        Returns:
            Dict with balance analysis including at_risk and needs_attention flags
        """
        with self._lock:
            stats = self.get_lane_stats()
            
            at_risk = []
            needs_attention = []
            
            for name, stat in stats.items():
                util = stat["utilization"]
                if util >= 0.90:
                    at_risk.append(name)
                elif util >= 0.80:
                    needs_attention.append(name)

            return {
                "available": all(u < 0.90 for u in [s["utilization"] for s in stats.values()]),
                "utilization": self.get_lane_utilization(),
                "lanes": stats,
                "at_risk": at_risk,
                "needs_attention": needs_attention,
            }

    def __repr__(self) -> str:
        return (
            f"LaneBalancer("
            f"lanes={self.lane_count}, "
            f"utilization={self.get_lane_utilization():.1%}, "
            f"at_risk={len(self.get_lanes_needing_attention(0.90))})"
        )


# =============================================================================
# Convenience Factory
# =============================================================================

_LANE_BALANCER_GLOBAL: LaneBalancer | None = None
_LB_GLOBAL_LOCK: threading.Lock = threading.Lock()


def get_lane_balancer() -> LaneBalancer:
    """Get or create the global LaneBalancer instance.

    Thread-safe singleton pattern.
    """
    global _LANE_BALANCER_GLOBAL
    
    if _LANE_BALANCER_GLOBAL is not None:
        return _LANE_BALANCER_GLOBAL
    
    with _LB_GLOBAL_LOCK:
        if _LANE_BALANCER_GLOBAL is None:
            _LANE_BALANCER_GLOBAL = LaneBalancer()
        return _LANE_BALANCER_GLOBAL


def reset_lane_balancer() -> None:
    """Reset the global LaneBalancer (for testing)."""
    global _LANE_BALANCER_GLOBAL
    with _LB_GLOBAL_LOCK:
        _LANE_BALANCER_GLOBAL = None
