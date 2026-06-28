"""
SprintSchedulerConfig — Configuration for sprint runs.
======================================================

Extracted from runtime/sprint_scheduler.py (Phase 1 of modular decomposition).
Lines: 1528-1798 in original.

F250 + F272A + F273B + F278A + F285: Dynamic windup that scales with sprint duration.
F285: M1 Air 8GB fix — explicit windup_lead_s override support.
F289: Windup caps reduced for M1 Air 8GB optimization.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum, auto


def _env_flag(name: str, default: str = "") -> str:
    """Cached env-var lookup (duplicated from sprint_scheduler.py to avoid circular import)."""
    import os
    try:
        return (os.environ.get(name, default) or default).strip()
    except Exception:
        return default

logger = logging.getLogger(__name__)

# Tier ordering (high -> low priority)
_TIER_ORDER: list[SourceTier] = []  # Populated after SourceTier class


class SourceTier(Enum):
    """Feed source priority tier."""
    SURFACE = auto()        # high-value real-time feeds (news, alerts)
    STRUCTURED_TI = auto()  # structured threat intel feeds
    DEEP = auto()           # archive, historical, passive DNS
    ARCHIVE = auto()        # Wayback, archive.org
    OTHER = auto()          # everything else


# Initialize _TIER_ORDER after SourceTier is defined
_TIER_ORDER = [
    SourceTier.SURFACE,
    SourceTier.STRUCTURED_TI,
    SourceTier.DEEP,
    SourceTier.ARCHIVE,
    SourceTier.OTHER,
]


@dataclass(frozen=True, slots=True)
class SprintSchedulerConfig:
    """Configuration for one sprint run."""
    sprint_duration_s: float = 1800.0          # 30 min
    windup_lead_s: float = 180.0               # enter wind-down 3 min before end
    cycle_sleep_s: float = 5.0                 # sleep between cycles

    # Sprint F-A3: per-cycle hard deadline. Wraps `_run_one_cycle` in
    # `asyncio.timeout(cycle_budget_s)`. If a cycle exceeds this budget
    # (e.g. a branch wraps sync I/O via `asyncio.to_thread` and the
    # thread doesn't respect CancelledError promptly), TimeoutError
    # propagates and the cycle is counted as empty so the F228G
    # consecutive-empty-cycles guard eventually forces windup. 60s =
    # 4x the typical 15s branch timeout — generous headroom for the
    # concurrent aggressive-mode feed/public/CT trio.
    cycle_budget_s: float = 60.0                # hard per-cycle deadline

    max_cycles: int = 100                      # safety cap
    max_parallel_sources: int = 4              # concurrent source fetches
    stop_on_first_accepted: bool = False       # early exit on first accepted
    export_enabled: bool = True
    export_dir: str = ""
    max_entries_per_cycle: int = 50            # per-source cap

    # Sprint F193B: Hypothesis -> finding feedback loop caps
    max_hypothesis_depth: int = 3             # max iteration depth for hypothesis-driven pivots
    max_hypothesis_queries: int = 10          # max total hypothesis-driven pivot queries

    # Aggressive mode: fans out feed/public/CT branches concurrently per cycle
    aggressive_mode: bool = False
    aggressive_branch_timeout_s: float = 45.0  # per-branch timeout in aggressive mode

    # Sprint F195B: Per-branch timeout budget in seconds (aggressive mode uses 8.0)
    branch_timeout_budget_s: float = 0.0       # 0 = use aggressive_branch_timeout_s

    # Sprint F212-B: Branch timeout envelope caps
    _MAX_BRANCH_TIMEOUT_CAP: float = 300.0    # absolute per-branch cap

    # F273A: Dynamic branch-remaining floor. The 5.0s static floor was the root
    # cause of `terminal:remaining_too_low` killing PUBLIC + CT branches during
    # the windup transition. With 0.10 windup ratio + 60s ceiling, a 60s sprint
    # hit the 5.0s floor 50%+ of the way through. The floor now scales with
    # observed cycle time so quick cycles (< 10s) keep a 2s floor (matches the
    # F228G `cycle_sleep_s` minimum) and slow cycles (>= 30s) preserve a 9s
    # floor so public/CT don't drop into the 5s danger zone mid-fetch.
    # F273B: Adaptive formula ceiling. The primary formula is
    # `0.15 * remaining_s`, clamped [2.0, CAP]. With CAP=5.0 the floor grows
    # from 2s (early sprint) up to 5s (late windup), preserving PUBLIC+CT
    # branch headroom. F285 attempted to fix M1 windup truncation by lowering
    # CAP to 2.0, but that killed the adaptive formula entirely — restoring 5.0.
    _MIN_BRANCH_REMAINING_S_DEFAULT: float = 2.0  # base floor (no cycles seen yet)
    _MIN_BRANCH_REMAINING_S_CAP: float = 5.0     # max floor -- F273B adaptive formula ceiling

    # Sprint F273A kept the legacy constant name as an alias for back-compat
    # (some tests + sidecar adapters read this attribute directly).
    _MIN_BRANCH_REMAINING_S: float = 2.0          # back-compat alias; runtime uses _min_branch_remaining_s()

    # Partial export interval -- every N findings in aggressive mode (recovery artifact)
    partial_export_findings_interval: int = 10

    # Tier budgets in seconds -- only enforced approximately via cycle limits
    # Sources NOT listed here fall to OTHER tier
    source_tier_map: dict[str, SourceTier] = field(default_factory=dict)

    @property
    def effective_windup_lead_s(self) -> float:
        """
        F250 + F272A + F273B + F278A + F285: Dynamic windup that scales with sprint duration.

        F285: M1 Air 8GB fix — if windup_lead_s is explicitly set (not equal to
        class default 180.0), use it directly. This allows CLI --windup-lead to
        override the percentage-based calculation that was causing 600s sprints to
        spend 180s in windup (30%) instead of the intended 30s.

        Previously: 30% of sprint_duration_s always, clamped to [30, 180]
          - 600s thoro sprint -> 180s windup (ceiling) — TOO LONG for M1 Air
          - 300s deep sprint  -> 90s windup

        F285 fix: respect explicit windup_lead_s if set to non-default value
          - 600s with --windup-lead 30 -> 30s windup (leaving 570s active)
          - 300s with --windup-lead 30 -> 30s windup (leaving 270s active)

        Bounded [30, 180] to match F221-ABORT pre-flight guard.
        """
        # F285: Honor explicit windup_lead_s if set to non-default value
        # Default class value is 180.0, so explicit override will be different.
        # F289: Explicit values capped at 180s to match F221-ABORT guard.
        if self.windup_lead_s != 180.0:
            return float(min(180.0, self.windup_lead_s))
        # F289-WINDUP: 30% ratio, capped at 180s — matches F221-ABORT guard.
        # Sprint 60s:  0.30*60=18 → clamp [30, 180] → 30s active=30s OK
        # Sprint 300s: 0.30*300=90 (< 180)            → active=210s OK
        # Sprint 600s: 0.30*600=180 (at ceiling)      → active=420s OK
        ratio = 0.30
        raw = self.sprint_duration_s * ratio
        return float(min(180.0, raw))

    @property
    def final_windup_lead_s(self) -> float:
        """
        F285: M1 Air 8GB fix — if windup_lead_s is explicitly set (not equal to
        class default 180.0), use it directly. This is the windup value used at
        sprint end for synthesis and graceful shutdown.

        F289: 30% ratio capped at 180s — matches F221-ABORT guard.
        MLX: Model loads in prewarm, synthesis runs in windup phase.
        Use 30% ratio so 300s sprint gets 90s. Bounded [30, 180].

        Non-MLX: Hermes never loads, no synthesis lane needed.
        Still uses 30% ratio to match F221-ABORT guard.
        F221-ABORT guard: active window ≥ MIN_ACTIVE_WINDOW_S.
        """
        # F285: Honor explicit windup_lead_s if set to non-default value
        # F289: Capped at 180s to match F221-ABORT guard.
        if self.windup_lead_s != 180.0:
            result = float(min(180.0, self.windup_lead_s))
            logger.info("[WINDUP] final_windup=%.1fs (explicit)", result)
            return result
        _hermes_enabled = _env_flag("HLEDAC_ENABLE_HERMES_SYNTHESIS") == "1"
        if not _hermes_enabled:
            # Bez Hermes synthesis nepotřebujeme dlouhý windup.
            # 30% ratio, capped at 180s — matches F221-ABORT guard.
            result = float(min(180.0, self.sprint_duration_s * 0.30))
            logger.info("[WINDUP] lead=%.1fs hermes=%s", result, _hermes_enabled)
            return result
        # MLX: aggressive mode needs MORE windup for synthesis (30%);
        # non-aggressive gets 30% to match F221-ABORT guard.
        # F289: Both capped at 180s.
        ratio = 0.30
        raw = self.sprint_duration_s * ratio
        result = float(min(180.0, raw))
        logger.info("[WINDUP] lead=%.1fs hermes=%s", result, _hermes_enabled)
        return result

    def windup_for_cycle(self, cycle_time_ema: float) -> float:
        """
        F273B + F278A: Cycle-time-adaptive windup lead.

        The base `effective_windup_lead_s` (30% of duration, clamped [30, 180])
        is the static floor. This method returns a longer windup when observed
        cycles are slow -- so the windup phase has at least 2 cycles of headroom
        for pattern extraction, synthesis, and DuckDB ingest.

        Formula:
          base = effective_windup_lead_s  (30% ratio)
          adapt = max(0, (cycle_time_ema - 8) * 0.5)  # +0.5s per s over 8s cycle
          adapt = min(30.0, adapt)         # cap the bonus at 30s
          return clamp(base + adapt, 30, 180)

        Examples (300s sprint, base=90s):
          - cycle_time_ema=5s  -> 90s (no bonus, quick cycles)
          - cycle_time_ema=20s -> 96s (+6s bonus)
          - cycle_time_ema=60s -> 120s (+30s bonus, saturates at ceiling)

        Examples (100s sprint, base=30s):
          - cycle_time_ema=5s  -> 30s (no bonus, floor)
          - cycle_time_ema=30s -> 41s (+11s, over floor)
          - cycle_time_ema=60s -> 60s (saturates well below ceiling)

        Always-on, bounded [30, 180], fail-soft (negative cycle_time_ema -> base).
        """
        if cycle_time_ema <= 0:
            return self.effective_windup_lead_s
        base = self.effective_windup_lead_s
        adapt = max(0.0, min(30.0, (cycle_time_ema - 8.0) * 0.5))
        return float(max(30.0, min(180.0, base + adapt)))

    @property
    def effective_cycle_sleep_s(self) -> float:
        """
        F228G: Adaptive cycle sleep that scales with sprint duration.

        Short sprints (60-90s) need a much shorter inter-cycle sleep than
        long ones (1800s). For very short sprints the 5.0s default sleep
        consumes up to 50% of the active window -- making it impossible to
        run more than a handful of cycles before windup.

        Returns:
          - 60s quick (active=30s) -> 1.0s (fits ~25 cycles)
          - 300s deep  (active=210s) -> 2.0s (fits ~50 cycles)
          - 600s thoro (active=420s) -> 3.0s
          - 1800s default (active=1620s) -> 5.0s (preserves pre-F228G behavior)

        Bounded: clamp [0.5, 5.0]s to prevent both over-sleep on quick
        sprints and ultra-tight loops on long ones.

        Fail-safe: if active <= 0, returns 0.5s (minimum).
        """
        active = max(0.0, self.sprint_duration_s - self.final_windup_lead_s)
        if active <= 0:
            return 0.5
        # Scale: 0.5s for 30s active, 5.0s for 1500s+ active
        scaled = max(0.5, min(5.0, active / 300.0))
        return float(scaled)

    @property
    def hermes_budget_s(self) -> int:
        """
        F253: Adaptive Hermes synthesis budget = 35% of the active window,
        floored at 30s. Prevents short sprints from starving the synthesis
        lane while ensuring long sprints reserve enough budget.

        Uses final_windup_lead_s (which reflects MLX vs non-MLX adaptive logic).

        Examples:
          - 60s quick (active=30s) -> 30 (floor)
          - 300s deep non-MLX (active=270s) -> 94 (35%)
          - 300s deep MLX     (active=210s) -> 73 (35%)
          - 600s thoro  (active=420s) -> 147 (35%)
        """
        active = max(0, self.sprint_duration_s - self.final_windup_lead_s)
        return max(30, int(active * 0.35))

    # F223A: Explicit acquisition profile -- overrides env var / profile-name inference
    acquisition_profile: str | None = None

    # Sprint F214: Optional strict feed dominance guard -- blocks feed-only early exit
    # when True and guard is triggered (ratio > 0.95, nonfeed < 5)
    require_nonfeed_corrob_for_early_exit: bool = False

    # Sprint F250: Preferred transport for sensitive queries (auto/nym/tor/i2p/clearnet)
    sensitive_query_transport: str = "auto"

    # F233C: Optional predecessor sprint_id for next_sprint_seeds consumption
    predecessor_sprint_id: str | None = None

    # F11: Deep research advisory (post-WINDUP, fire-and-forget)
    deep_research_enabled: bool = False
    extreme_mode: bool = False

    def tier_of(self, source: str) -> SourceTier:
        return self.source_tier_map.get(source, SourceTier.OTHER)

    def sorted_tiers(self) -> list[SourceTier]:
        return _TIER_ORDER.copy()
