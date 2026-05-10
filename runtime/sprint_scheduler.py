"""
Sprint 8BK — Tier-Aware Feed Sprint Scheduler V1.

Sidecar over SprintLifecycleManager (8BI). Operational backbone for
30-minute bounded sprint runs.

================================================================
F177D ROLE VERDICT
================================================================
ROLE: runtime worker / operational executor
STATUS: NOT a sprint owner — scheduler executes work dispatched by sprint owner

The scheduler is a RUNTIME WORKER. It receives a SprintLifecycleManager
and sources from the sprint owner (core.__main__.run_sprint) and executes
the sprint cycle. All report truth flows through the owner, not the scheduler.

Tier ordering (high → low priority):
  surface → structured_ti → deep → archive → other

Key invariants:
- Wind-down respected: no new work after lifecycle says WINDUP
- In-sprint dedup: same entry_hash never processed twice in one sprint
- Lifecycle is authority for time and phase transitions
- Export always runs on teardown (zero-signal too)
- No background threads; TaskGroup for owned concurrency
"""

from __future__ import annotations

import asyncio
import gc
import logging
import os
import struct
import time as _time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, Callable, Final, Optional, Sequence

from hledac.universal.patterns.pattern_matcher import match_text
from hledac.universal.runtime.sprint_lifecycle import SprintLifecycleManager, SprintPhase
from hledac.universal.utils.async_helpers import _check_gathered
from hledac.universal.transport.circuit_breaker import (
    get_all_breaker_snapshots,
    get_all_breaker_states,
    MAX_TRACKED_DOMAINS,
)

# Sprint F218D: Lane rejection bound
MAX_LANE_REJECTIONS: int = 1000

# E4: gc.callbacks for sprint-level GC telemetry — module-level state
_gc_sprint_callback_handle: Optional[Callable] = None  # stores registered callback function
MAX_GC_STATS: int = 1000  # Sprint F219M: bound GC telemetry

_gc_sprint_stats: list[dict] = []


def _gc_sprint_callback(phase: str, info: dict) -> None:
    """E4: GC per-collection callback — records generation and collection counts."""
    if len(_gc_sprint_stats) < MAX_GC_STATS:
        _gc_sprint_stats.append({
            'gen': info.get('generation', -1),
            'collected': info.get('collected', -1),
        })


from hledac.universal.runtime.shadow_inputs import (
    collect_lifecycle_snapshot,
    collect_graph_summary,
    collect_model_control_facts,
    collect_provider_runtime_facts,
)
from hledac.universal.runtime.sidecar_bus import (
    SidecarBatch,
    SidecarRunResult,
    create_sidecar_bus,
)
from hledac.universal.runtime.sidecar_dispatcher import (
    SidecarDispatcher,
)
from hledac.universal.runtime.sprint_advisory_runner import (
    AdvisoryRunOutcome,
    SprintAdvisoryRunner,
)
from hledac.universal.runtime.sprint_lifecycle_runner import SprintLifecycleRunner
from hledac.universal.runtime.shadow_parity import run_shadow_parity
# Sprint F204D: Target memory integration
from hledac.universal.knowledge.target_memory import (
    TargetMemoryService,
    TargetMemoryUpdate,
    MAX_MEMORY_ENTITIES,
    MAX_MEMORY_EXPOSURES,
    MAX_MEMORY_PIVOTS,
)
from hledac.universal.runtime.shadow_pre_decision import compose_pre_decision
# Sprint F206BG: Canonical acquisition strategy layer
from hledac.universal.runtime.acquisition_strategy import (
    AcquisitionLane,
    AcquisitionLaneOutcome,
    MandatoryLaneTerminality,
    NonfeedPlanDebug,
    NonfeedMissionController,
    FeedDominanceBudget,
    normalize_source_family_outcome,
    build_acquisition_plan,
    is_lane_enabled,
    lane_skip_reason,
    get_lane_plan,
    build_lane_query,
    run_enabled_acquisition_lanes,
    required_terminal_lanes,
    terminality_report,
    lane_is_terminal,
    _get_ct_adapter,
    _load_feed_budget_from_env,
)
# F216F: Pivot candidate generation from query
# F217E: Nonfeed candidate evidence ledger
from hledac.universal.runtime.nonfeed_candidate_ledger import (
    NonfeedCandidateLedger,
    STAGE_ACCEPTED,
    STAGE_DISCOVERED,
    STAGE_FETCHED,
    STAGE_PARSED,
    STAGE_PROVIDER_FAILED,
    STAGE_QUARANTINED,
    STAGE_REJECTED,
    STAGE_STORED,
    FAMILY_CT,
    FAMILY_PUBLIC,
    FAMILY_PIVOT,
)
from hledac.universal.runtime.pivot_planner import generate_pivot_candidates_from_query
from hledac.universal.runtime.source_finding_bridge import (
    ct_results_to_findings,
    wayback_results_to_findings,
    passive_dns_results_to_findings,
    REJECTION_UNSUPPORTED_SHAPE,
)

if TYPE_CHECKING:
    pass

import lmdb
import xxhash

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sprint F216C: PUBLIC stage machine helper
# ---------------------------------------------------------------------------
# Explicit PUBLIC stage machine for zero-evidence diagnosis.
# PUBLIC=0 can occur at any of: discovery, fetch, parse, quality, storage.
# Maps _public_outcome dict (and optionally PipelineRunResult) to precise stage.
# ---------------------------------------------------------------------------

# PUBLIC stage names
class _PublicStage:
    NOT_SCHEDULED = "NOT_SCHEDULED"
    SCHEDULED = "SCHEDULED"
    # Sprint F217C: Deterministic bootstrap stages
    BOOTSTRAP_ATTEMPTED = "BOOTSTRAP_ATTEMPTED"
    BOOTSTRAP_ZERO_SUCCESS = "BOOTSTRAP_ZERO_SUCCESS"
    BOOTSTRAP_ACCEPTED = "BOOTSTRAP_ACCEPTED"
    # Sprint F221C: Bootstrap timeout stages — distinguish timeout during bootstrap from zero success
    BOOTSTRAP_ATTEMPTED_TIMEOUT = "BOOTSTRAP_ATTEMPTED_TIMEOUT"
    BOOTSTRAP_ZERO_CANDIDATES_TIMEOUT = "BOOTSTRAP_ZERO_CANDIDATES_TIMEOUT"
    # Normal discovery stages
    DISCOVERY_ATTEMPTED = "DISCOVERY_ATTEMPTED"
    DISCOVERY_ZERO_RESULTS = "DISCOVERY_ZERO_RESULTS"
    DISCOVERY_TIMEOUT = "DISCOVERY_TIMEOUT"
    DISCOVERY_ERROR = "DISCOVERY_ERROR"
    FETCH_ATTEMPTED = "FETCH_ATTEMPTED"
    FETCH_ZERO_SUCCESS = "FETCH_ZERO_SUCCESS"
    FETCH_TIMEOUT = "FETCH_TIMEOUT"
    FETCH_ERROR = "FETCH_ERROR"
    PARSE_ATTEMPTED = "PARSE_ATTEMPTED"
    PARSE_ZERO_TEXT = "PARSE_ZERO_TEXT"
    QUALITY_REJECTED = "QUALITY_REJECTED"
    STORAGE_REJECTED = "STORAGE_REJECTED"
    ACCEPTED = "ACCEPTED"
    TERMINAL = "TERMINAL"


def _compute_public_stage(outcome: dict | None, public_result: Any | None = None) -> tuple[str, dict]:
    """
    Compute public_terminal_stage and public_stage_counters from _public_outcome.

    The stage machine traces the full discovery→fetch→parse→quality→storage
    pipeline to explain why PUBLIC=0.

    Returns (terminal_stage: str, stage_counters: dict).
    """
    # NOT_SCHEDULED: _public_outcome never set (branch never ran)
    if outcome is None:
        return _PublicStage.NOT_SCHEDULED, {
            "discovered_urls": 0,
            "fetch_attempted": 0,
            "fetch_success": 0,
            "fetch_timeout": 0,
            "fetch_error": 0,
            "parse_attempted": 0,
            "parse_success": 0,
            "quality_rejected": 0,
            "storage_rejected": 0,
            "accepted_findings": 0,
        }

    error = outcome.get("error", "") or ""
    timeout = outcome.get("timeout", False)
    skip_reason = outcome.get("skip_reason", "") or ""
    raw_count = outcome.get("raw_count", 0) or 0
    built_count = outcome.get("built_count", 0) or 0
    accepted_count = outcome.get("accepted_count", 0) or 0
    attempted = outcome.get("attempted", False)
    skipped = outcome.get("skipped", False)

    # Build stage_counters from PipelineRunResult if available
    # Sprint F223C: Use public_result.discovered as discovered_urls — this is the
    # canonical discovery count and is correct even when public_discovery_raw_count
    # is 0 (e.g., bootstrap-only path, discovery_empty path with bootstrap candidates).
    # public_discovery_raw_count is set to 0 on discovery_empty/uma_emergency failure paths
    # but public_result.discovered correctly reflects what was discovered.
    if public_result is not None:
        pr = public_result
        # Sprint F223C: Canonical discovered count from the pipeline result
        _discovered_count = getattr(pr, 'discovered', 0) or 0
        # Sprint F223C: Determine raw_count_source for diagnosability
        # _pub_discovery_raw > 0 → discovery (public discovery hit count > 0)
        # _bootstrap_cand > 0 and _discovered_count == _bootstrap_cand → bootstrap only
        #   (all discovered URLs came from bootstrap, no discovery contribution)
        # _bootstrap_cand > 0 and _discovered_count > _bootstrap_cand → mixed
        #   (bootstrap candidates plus additional discovery hits, some may overlap)
        # otherwise → unknown
        _bootstrap_cand = getattr(pr, 'public_bootstrap_candidates_count', 0) or 0
        _pub_discovery_raw = getattr(pr, 'public_discovery_raw_count', 0) or 0
        if _pub_discovery_raw > 0:
            _raw_count_source = "discovery"
        elif _bootstrap_cand > 0 and _discovered_count == _bootstrap_cand:
            _raw_count_source = "bootstrap"
        elif _bootstrap_cand > 0 and _discovered_count > _bootstrap_cand:
            _raw_count_source = "mixed"
        else:
            _raw_count_source = "unknown"
        stage_counters = {
            "discovered_urls": _discovered_count,
            "raw_count_source": _raw_count_source,
            "fetch_attempted": getattr(pr, 'public_fetch_attempted', 0) or 0,
            "fetch_success": getattr(pr, 'public_fetch_success', 0) or 0,
            "fetch_timeout": getattr(pr, 'public_skipped_timeout', 0) or 0,
            "fetch_error": getattr(pr, 'public_skipped_fetch_error', 0) or 0,
            "parse_attempted": getattr(pr, 'public_fetch_success', 0) or 0,
            "parse_success": getattr(pr, 'public_fetch_success', 0) or 0,
            "quality_rejected": getattr(pr, 'public_acceptance_rejected', 0) or 0,
            "storage_rejected": getattr(pr, 'public_rejected_storage_rejected', 0) or 0,
            "accepted_findings": getattr(pr, 'public_findings_accepted', 0) or 0,
        }
        # Bounded samples (max 5 each) — already capped in PipelineRunResult
        stage_counters["rejection_reasons"] = list(
            (getattr(pr, 'public_acceptance_reject_reasons', None) or {}).items()
        )[:5]
        stage_counters["error_samples"] = list(
            (getattr(pr, 'public_skipped_url_sample', None) or ())[:5]
        )
        stage_counters["rejected_url_samples"] = list(
            (getattr(pr, 'public_rejected_url_samples', None) or ())[:5]
        )
        # Sprint F217C: Deterministic bootstrap stage counters
        stage_counters["bootstrap_candidates"] = getattr(pr, 'public_bootstrap_candidates_count', 0) or 0
        stage_counters["bootstrap_fetch_attempted"] = getattr(pr, 'public_bootstrap_fetch_attempted', 0) or 0
        stage_counters["bootstrap_fetch_success"] = getattr(pr, 'public_bootstrap_fetch_success', 0) or 0
        stage_counters["bootstrap_accepted_findings"] = getattr(pr, 'public_bootstrap_accepted_findings', 0) or 0
        stage_counters["bootstrap_errors"] = getattr(pr, 'public_bootstrap_errors', 0) or 0
        # Bootstrap-enabled flag for stage derivation
        bootstrap_enabled = getattr(pr, 'public_bootstrap_enabled', False)
        # public_stage_failure from PipelineRunResult if available
        psf = getattr(pr, 'public_stage_failure', None)
        # Sprint F221C: Bootstrap-aware stage derivation.
        # BOOTSTRAP_ATTEMPTED_TIMEOUT: bootstrap was enabled, produced candidates (raw_count>0),
        #   but the branch timed out before discovery could complete.
        # BOOTSTRAP_ZERO_CANDIDATES_TIMEOUT: bootstrap was enabled, produced NO candidates,
        #   AND the branch timed out (bootstrap itself failed + timeout).
        # BOOTSTRAP_ZERO_SUCCESS: bootstrap produced candidates but none yielded accepted findings
        #   (discovery completed normally, no timeout).
        # BOOTSTRAP_ACCEPTED: bootstrap produced candidates and at least one was accepted.
        if bootstrap_enabled and raw_count > 0:
            # raw_count > 0 means bootstrap produced URL candidates
            if accepted_count > 0:
                terminal_stage = _PublicStage.BOOTSTRAP_ACCEPTED
            elif timeout:
                # Branch timed out with bootstrap candidates pending — distinct from zero-success
                terminal_stage = _PublicStage.BOOTSTRAP_ATTEMPTED_TIMEOUT
            else:
                terminal_stage = _PublicStage.BOOTSTRAP_ZERO_SUCCESS
        elif bootstrap_enabled and raw_count == 0 and timeout:
            # Bootstrap enabled but produced no candidates AND timed out
            terminal_stage = _PublicStage.BOOTSTRAP_ZERO_CANDIDATES_TIMEOUT
        elif psf == "fetch_zero" and accepted_count == 0:
            terminal_stage = _PublicStage.FETCH_ZERO_SUCCESS
        else:
            terminal_stage = _derive_terminal_stage(
                error=error,
                timeout=timeout,
                skip_reason=skip_reason,
                raw_count=raw_count,
                built_count=built_count,
                accepted_count=accepted_count,
                attempted=attempted,
                skipped=skipped,
                public_stage_failure=psf,
            )
    else:
        # No PipelineRunResult — use only _public_outcome dict fields
        stage_counters = {
            "discovered_urls": raw_count,
            "fetch_attempted": 0,
            "fetch_success": 0,
            "fetch_timeout": 0,
            "fetch_error": 0,
            "parse_attempted": built_count,
            "parse_success": built_count,
            "quality_rejected": 0,
            "storage_rejected": 0,
            "accepted_findings": accepted_count,
        }
        terminal_stage = _derive_terminal_stage(
            error=error,
            timeout=timeout,
            skip_reason=skip_reason,
            raw_count=raw_count,
            built_count=built_count,
            accepted_count=accepted_count,
            attempted=attempted,
            skipped=skipped,
            public_stage_failure=None,
        )

    return terminal_stage, stage_counters


def _derive_terminal_stage(
    error: str,
    timeout: bool,
    skip_reason: str,
    raw_count: int,
    built_count: int,
    accepted_count: int,
    attempted: bool,
    skipped: bool,
    public_stage_failure: str | None,
) -> str:
    """Derive terminal stage from outcome fields."""
    # NOT_SCHEDULED: never attempted
    if not attempted:
        return _PublicStage.NOT_SCHEDULED

    # SCHEDULED: attempted but skipped before discovery
    if skipped and skip_reason:
        if "remaining_too_low" in skip_reason:
            return _PublicStage.DISCOVERY_TIMEOUT
        return _PublicStage.DISCOVERY_ERROR

    # Discovery failure classification
    if raw_count == 0:
        if timeout or "timeout" in error.lower() or "timeout" in skip_reason.lower():
            return _PublicStage.DISCOVERY_TIMEOUT
        if error and error not in ("null", ""):
            return _PublicStage.DISCOVERY_ERROR
        return _PublicStage.DISCOVERY_ZERO_RESULTS

    # Discovery succeeded but fetch never produced anything
    if built_count == 0:
        if timeout or "timeout" in error.lower():
            return _PublicStage.FETCH_TIMEOUT
        if error and error not in ("null", ""):
            return _PublicStage.FETCH_ERROR
        return _PublicStage.FETCH_ZERO_SUCCESS

    # Discovery and fetch succeeded but no findings accepted
    if accepted_count == 0:
        # Use PipelineRunResult public_stage_failure if available
        if public_stage_failure == "fetch_zero":
            return _PublicStage.FETCH_ZERO_SUCCESS
        if public_stage_failure == "discovery_empty":
            return _PublicStage.DISCOVERY_ZERO_RESULTS
        # Infer from built_count vs accepted_count gap
        if built_count > 0:
            # Fetch produced pages but no findings accepted
            return _PublicStage.QUALITY_REJECTED
        return _PublicStage.PARSE_ZERO_TEXT

    # Some findings accepted
    if accepted_count > 0:
        return _PublicStage.ACCEPTED

    # Fallback
    return _PublicStage.TERMINAL


# ---------------------------------------------------------------------------
# Lifecycle Adapter — bridges utils/ vs runtime/ sprint_lifecycle API
# ---------------------------------------------------------------------------
# Runtime version (hledac.universal.runtime.sprint_lifecycle):
#   start(), tick(), remaining_time(), is_terminal(),
#   should_enter_windup(), _current_phase, recommended_tool_mode(),
#   request_abort(), _abort_requested, _abort_reason
#
# Old utils version (hledac.universal.utils.sprint_lifecycle):
#   begin_sprint(), is_active, remaining_time, state, is_windup_phase()
#
# Scheduler always calls the runtime API. Adapter is a no-op shim for
# any caller that passes the old utils-style object.
# ---------------------------------------------------------------------------

class _LifecycleAdapter:
    """
    Normalizes lifecycle API differences between runtime/ and utils/ versions.

    runtime/sprint_lifecycle: start(), tick(), remaining_time(),
        is_terminal(), should_enter_windup(), _current_phase,
        recommended_tool_mode(), request_abort(), _abort_requested

    Adapter ensures begin_sprint() on any lifecycle object maps to start()
    for runtime objects, and bridges property vs method access patterns.
    """

    __slots__ = ("_lc",)

    def __init__(self, lifecycle: Any) -> None:
        self._lc = lifecycle

    # ── start / begin_sprint ───────────────────────────────────────────────

    def start(self) -> None:
        """runtime: start() — transitions BOOT→WARMUP."""
        lc = self._lc
        if hasattr(lc, "start"):
            lc.start()
        elif hasattr(lc, "begin_sprint"):
            lc.begin_sprint()

    # ── tick ──────────────────────────────────────────────────────────────

    def tick(self, now_monotonic: Optional[float] = None):
        """runtime: tick() returns SprintPhase. Fallback: 'UNKNOWN' phase string."""
        lc = self._lc
        if hasattr(lc, "tick"):
            return lc.tick(now_monotonic)
        # Fallback: return phase-like 'UNKNOWN' string, not float.
        # Callers (line 530) compare phase != _current_phase — requires str.
        return "UNKNOWN"

    # ── remaining_time ───────────────────────────────────────────────────

    def remaining_time(self, now_monotonic: Optional[float] = None) -> float:
        """runtime: remaining_time(). utils: remaining_time property."""
        lc = self._lc
        if hasattr(lc, "remaining_time"):
            val = lc.remaining_time
            return float(val() if callable(val) else val)
        return 0.0

    # ── is_terminal ──────────────────────────────────────────────────────

    def is_terminal(self) -> bool:
        """runtime: is_terminal(). Returns True when phase is TEARDOWN."""
        lc = self._lc
        if hasattr(lc, "is_terminal"):
            val = lc.is_terminal
            return bool(val() if callable(val) else val)
        # Fallback: check phase name
        phase = self._current_phase
        return phase == "TEARDOWN"

    # ── should_enter_windup ──────────────────────────────────────────────

    def should_enter_windup(self, now_monotonic: Optional[float] = None) -> bool:
        """runtime: should_enter_windup(). utils: is_windup_phase()."""
        lc = self._lc
        if hasattr(lc, "should_enter_windup"):
            val = lc.should_enter_windup
            return bool(val(now_monotonic) if callable(val) else val)
        if hasattr(lc, "is_windup_phase"):
            val = lc.is_windup_phase
            return bool(val() if callable(val) else val)
        return False

    # ── _current_phase ───────────────────────────────────────────────────

    @property
    def _current_phase(self) -> str:
        """runtime: _current_phase (SprintPhase enum). utils: state (SprintLifecycleState)."""
        lc = self._lc
        for attr in ("_current_phase", "phase", "state", "current_phase"):
            if hasattr(lc, attr):
                val = getattr(lc, attr)
                v = val() if callable(val) else val
                return str(v.name if hasattr(v, "name") else v)
        return "UNKNOWN"

    # ── mark_warmup_done ─────────────────────────────────────────────────
    # F184A: Canonical public API for WARMUP→ACTIVE transition.
    # F184A: Replaces direct adapter._lc.mark_warmup_done() bypass in run().

    def mark_warmup_done(self) -> None:
        """runtime: mark_warmup_done() — transitions WARMUP→ACTIVE."""
        lc = self._lc
        if hasattr(lc, "mark_warmup_done"):
            lc.mark_warmup_done()
        elif hasattr(lc, "transition_to"):
            from hledac.universal.runtime.sprint_lifecycle import SprintPhase
            lc.transition_to(SprintPhase.ACTIVE)

    # ── recommended_tool_mode ────────────────────────────────────────────

    def recommended_tool_mode(self, now_monotonic: Optional[float] = None) -> str:
        """runtime: recommended_tool_mode(). Returns 'normal'/'prune'/'panic'."""
        lc = self._lc
        if hasattr(lc, "recommended_tool_mode"):
            val = lc.recommended_tool_mode
            return str(val(now_monotonic) if callable(val) else val)
        return "normal"

    # ── request_abort ────────────────────────────────────────────────────

    def request_abort(self, reason: str = "") -> None:
        """runtime: request_abort(reason)."""
        lc = self._lc
        if hasattr(lc, "request_abort"):
            lc.request_abort(reason)
        elif hasattr(lc, "_abort_requested"):
            lc._abort_requested = True
            if hasattr(lc, "_abort_reason"):
                lc._abort_reason = reason

    # ── _abort_requested ─────────────────────────────────────────────────

    @property
    def _abort_requested(self) -> bool:
        lc = self._lc
        if hasattr(lc, "_abort_requested"):
            val = lc._abort_requested
            return bool(val() if callable(val) else val)
        return False

    @property
    def _abort_reason(self) -> str:
        lc = self._lc
        if hasattr(lc, "_abort_reason"):
            val = lc._abort_reason
            return str(val() if callable(val) else val)
        return ""


# ---------------------------------------------------------------------------
# Source tier
# ---------------------------------------------------------------------------

class SourceTier(Enum):
    """Feed source priority tier."""
    SURFACE = auto()       # high-value real-time feeds (news, alerts)
    STRUCTURED_TI = auto() # structured threat intel feeds
    DEEP = auto()          # deep/dark web, archive feeds
    ARCHIVE = auto()        # historical/wayback/archive feeds
    OTHER = auto()         # everything else — processed only if time allows


_TIER_ORDER = [
    SourceTier.SURFACE,
    SourceTier.STRUCTURED_TI,
    SourceTier.DEEP,
    SourceTier.ARCHIVE,
    SourceTier.OTHER,
]


# ---------------------------------------------------------------------------
# CT Bridge Loss Tracking (F214D)
# ---------------------------------------------------------------------------


class CTLossStage(Enum):
    """Enum describing where CT raw evidence is lost in the live bridge path.

    Canonical live path: crtsh_adapter → ct_results_to_findings → candidates →
    duckdb async_ingest → lane_ct_accepted_findings → benchmark report.

    Any deviation from this path constitutes a loss stage.
    """

    NO_RAW = "no_raw"  # CT adapter returned 0 hits
    BRIDGE_NOT_INVOKED = "bridge_not_invoked"  # CT adapter returned 0 raw and bridge was never called
    RAW_NOT_BRIDGED = "raw_not_bridged"  # F228E: raw > 0 but bridge was never called (_needs_ct=False)
    UNSUPPORTED_RAW_SHAPE = "unsupported_raw_shape"  # batch_result has no .hits attribute
    ALL_REJECTED_BY_BRIDGE = "all_rejected_by_bridge"  # hits existed but all candidates rejected
    CANDIDATES_BUILT_NOT_ACCUMULATED = "candidates_built_not_accumulated"  # bridge returned candidates but scheduler dropped them
    ACCUMULATED_NOT_STORED = "accumulated_not_stored"  # candidates accumulated but storage rejected
    # NOTE: STORED_NOT_REPORTED is unreachable in the current single-store architecture.
    # It would require a gap between async_ingest_findings_batch (which sets accepted)
    # and the benchmark report query. In the current design, accepted IS the canonical
    # count — there is no separate report query that could return a different value.
    # Kept for completeness and future multi-store architectures.
    STORED_NOT_REPORTED = "stored_not_reported"  # stored in DB but not visible in benchmark
    NO_LOSS = "no_loss"  # full path succeeded: raw → candidates → stored → reported
    # F215C: Unknown loss — raw > 0 but telemetry insufficient to determine loss stage
    UNKNOWN_LOSS = "unknown_loss"  # raw > 0 but cannot determine which loss stage
    # F217D: Provider failure — live provider failed and no stale cache available
    PROVIDER_FAILURE = "provider_failure"
    # F230C: Stale cache used — provider failed but stale cache was served as fallback
    STALE_CACHE_USED = "stale_cache_used"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SprintSchedulerConfig:
    """Configuration for one sprint run."""
    sprint_duration_s: float = 1800.0          # 30 min
    windup_lead_s: float = 180.0              # enter wind-down 3 min before end
    cycle_sleep_s: float = 5.0                 # sleep between cycles
    max_cycles: int = 100                      # safety cap
    max_parallel_sources: int = 4              # concurrent source fetches
    stop_on_first_accepted: bool = False       # early exit on first accepted
    export_enabled: bool = True
    export_dir: str = ""
    max_entries_per_cycle: int = 50             # per-source cap
    # Sprint F193B: Hypothesis → finding feedback loop caps
    max_hypothesis_depth: int = 3              # max iteration depth for hypothesis-driven pivots
    max_hypothesis_queries: int = 10           # max total hypothesis-driven pivot queries
    # Aggressive mode: fans out feed/public/CT branches concurrently per cycle
    aggressive_mode: bool = False              # if True, run branches in parallel
    aggressive_branch_timeout_s: float = 45.0  # per-branch timeout in aggressive mode
    # Sprint F195B: Per-branch timeout budget in seconds (aggressive mode uses 8.0)
    branch_timeout_budget_s: float = 0.0       # 0 = use aggressive_branch_timeout_s
    # Sprint F212-B: Branch timeout envelope caps
    _MAX_BRANCH_TIMEOUT_CAP: float = 300.0    # absolute per-branch cap
    _MIN_BRANCH_REMAINING_S: float = 5.0       # safety floor — skip branch below this
    # Partial export interval — every N findings in aggressive mode (recovery artifact)
    partial_export_findings_interval: int = 10
    # Tier budgets in seconds — only enforced approximately via cycle limits
    # Sources NOT listed here fall to OTHER tier
    source_tier_map: dict[str, SourceTier] = field(default_factory=dict)
    # F223A: Explicit acquisition profile — overrides env var / profile-name inference
    acquisition_profile: str | None = None

    def tier_of(self, source: str) -> SourceTier:
        return self.source_tier_map.get(source, SourceTier.OTHER)

    def sorted_tiers(self) -> list[SourceTier]:
        return _TIER_ORDER.copy()


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

class EarlyExitClass:
    """
    Sprint F215D: Canonical early exit classification for sprint runs.

    Enforces that active300/600 runs that complete in < 90% of planned
    duration are NOT reported ambiguously as completed — they must have an
    explicit early exit class.

    Values:
        completed_full_duration              -- ran to or past planned duration
        early_complete_no_work_remaining    -- work loop exited because no feed work remained
        early_complete_return_guard_satisfied -- return_guard passed, windup entered legitimately early
        early_complete_feed_only            -- feed-only run with zero nonfeed accepted findings
        aborted_by_memory                   -- aborted due to memory pressure / governor emergency
        aborted_by_deadline                 -- hard deadline exceeded before completion
        aborted_by_error                    -- exception in run() loop caused abort
    """

    COMPLETED_FULL_DURATION = "completed_full_duration"
    EARLY_COMPLETE_NO_WORK_REMAINING = "early_complete_no_work_remaining"
    EARLY_COMPLETE_RETURN_GUARD_SATISFIED = "early_complete_return_guard_satisfied"
    EARLY_COMPLETE_FEED_ONLY = "early_complete_feed_only"
    ABORTED_BY_MEMORY = "aborted_by_memory"
    ABORTED_BY_DEADLINE = "aborted_by_deadline"
    ABORTED_BY_ERROR = "aborted_by_error"


@dataclass
class SprintSchedulerResult:
    """
    Outcome of one sprint run.

    Attributes:
        cycles_started: Number of fetch cycles initiated.
        cycles_completed: Number of fetch cycles that completed all phases.
        unique_entry_hashes_seen: Count of deduplicated entries processed.
        duplicate_entry_hashes_skipped: Count of duplicate entries filtered.
        total_pattern_hits: Sum of pattern matches across all sources.
        accepted_findings: Findings that passed quality gate.
        entries_per_source: Breakdown of entries by source (source_name -> count).
        hits_per_source: Pattern hits per source (source_name -> count).
        final_phase: Last phase reached (BOOT, GATHER, JUDGMENT, EXPORT, TEARDOWN).
        export_paths: List of paths where sprint results were exported.
        aborted: True if sprint was aborted early.
        abort_reason: Human-readable reason for abortion.
        stop_requested: True when stop_on_first_accepted triggered acceptance.
        public_discovered: Public pipeline discoveries (F8XE).
        public_fetched: Public pipeline successful fetches.
        public_matched_patterns: Public pipeline pattern matches.
        public_accepted_findings: Public pipeline accepted findings.
        public_stored_findings: Public pipeline stored findings.
        public_error: Public pipeline error message.
        ct_log_discovered: CT log discoveries (F193A).
        ct_log_stored: CT log stored findings.
        ct_log_accepted_findings: CT log accepted findings (F194A).
        ct_log_error: CT log error message.
        entered_active_at_monotonic: Timestamp when ACTIVE phase first entered.
        pre_loop_elapsed_s: Wall-clock seconds from run() to loop guard entry.
        first_cycle_started_at_monotonic: Timestamp of first cycles_started increment.
        pre_active_starved: True when gap between entered_active and first_cycle_started > 30s.
    """
    cycles_started: int = 0
    cycles_completed: int = 0
    unique_entry_hashes_seen: int = 0
    duplicate_entry_hashes_skipped: int = 0
    total_pattern_hits: int = 0
    accepted_findings: int = 0
    entries_per_source: dict[str, int] = field(default_factory=dict)
    hits_per_source: dict[str, int] = field(default_factory=dict)
    final_phase: str = "BOOT"
    export_paths: list[str] = field(default_factory=list)
    aborted: bool = False
    abort_reason: str = ""
    stop_requested: bool = False  # True when stop_on_first_accepted triggered
    # Sprint 8XE: Public discovery pipeline results (canonical path parity)
    public_discovered: int = 0
    public_fetched: int = 0
    public_matched_patterns: int = 0
    public_accepted_findings: int = 0
    public_stored_findings: int = 0
    public_error: str = ""
    # Sprint F193A: CT log canonical discovery pipeline results
    ct_log_discovered: int = 0
    ct_log_stored: int = 0
    # Sprint F194A: CT log accepted findings — canonical truth accounting
    # additive to feed/public accepted_findings in canonical sprint truth surfaces
    ct_log_accepted_findings: int = 0
    ct_log_error: str = ""
    # Sprint F214D: CT Bridge Loss Audit — tracks where raw CT evidence is lost
    ct_loss_stage: str = "no_loss"
    ct_bridge_invoked: bool = False
    ct_raw_sample_keys: tuple[str, ...] = ()
    ct_raw_sample_count: int = 0
    ct_raw_count: int = 0  # F215B: total raw entries from CT adapter
    ct_candidates_built: int = 0
    ct_bridge_rejections_count: int = 0
    ct_bridge_rejection_reasons: tuple[str, ...] = ()
    ct_candidates_accumulated: int = 0
    ct_candidates_stored: int = 0
    ct_storage_rejected: int = 0
    # Sprint F226C: CT bridge acceptance diagnostics
    ct_candidate_count: int = 0  # domains that passed structural checks
    ct_valid_domain_count: int = 0  # domains that passed private/reserved filtering
    ct_bridge_build_success_count: int = 0  # CanonicalFinding candidates successfully built
    ct_bridge_quality_rejected_count: int = 0  # rejected at storage quality gate
    # Sprint F231B: CT expansion clue summary — domain expansion evidence visible even when accepted=0
    ct_raw_domains_seen: int = 0
    ct_unique_domains_seen: int = 0
    ct_valid_public_domains: int = 0
    ct_wildcard_domains: int = 0
    ct_private_reserved_domains: int = 0
    ct_duplicate_candidates: int = 0
    ct_expansion_clues_count: int = 0
    ct_candidate_examples: tuple[str, ...] = ()  # JSON-serialized quarantine entries
    # Sprint F216G: Quality Rejection Ledger — canonical ingest quality gate rejections
    # Bounded ledger: max 200 entries (source_family, reason, finding_id, url_sample)
    quality_rejection_ledger: tuple = ()  # tuple[QualityRejectionRecord, ...]
    # Sprint F216G: Pre-computed summaries by rejection category
    quality_rejection_summary_by_family: dict = field(default_factory=dict)
    duplicate_rejection_summary_by_family: dict = field(default_factory=dict)
    low_information_by_family: dict = field(default_factory=dict)
    # Sprint F216D: CT quarantine evidence for raw hits rejected by bridge criteria
    ct_quarantine_count: int = 0
    ct_quarantine_samples: tuple[str, ...] = ()  # bounded samples as JSON strings
    # Sprint F217D: CT provider resilience — explicit provider status and stale cache
    ct_provider_status: str = ""  # CTProviderStatus.value or ""
    ct_cache_used: bool = False  # True when stale cache was used
    ct_cache_stale: bool = False  # True when cache was already stale when used
    ct_cache_age_s: float = 0.0  # Seconds since cache was written (0 if not cached)
    # Sprint F166B: Pre-loop starvation tracking
    # Set when ACTIVE phase is first observed (loop guard entry)
    entered_active_at_monotonic: float | None = None
    # Wall-clock seconds from run() entry to loop guard entry
    pre_loop_elapsed_s: float | None = None
    # Set at first cycles_started += 1
    first_cycle_started_at_monotonic: float | None = None
    # True when gap between entered_active and first_cycle_started > 30s (M1 warmup budget)
    pre_active_starved: bool = False
    # First identified pre-loop cost center (additive, never overwritten)
    pre_loop_blocker_reason: str = ""
    # Dedup preload telemetry
    dedup_preload_count: int | None = None
    dedup_preload_elapsed_s: float | None = None
    # Sprint F169E: Feed branch blocker aggregation (additive, fail-soft)
    # Set to True when corresponding signal_stage/zero_signal_reason appears in any feed cycle
    feed_zero_yield_detected: bool = False        # zero_signal_reason was set
    feed_inaccessible_detected: bool = False      # empty_fetch in any feed source
    feed_content_empty_detected: bool = False     # content_empty in any feed source
    feed_no_pattern_with_content: bool = False   # no_pattern_hits_with_content in any feed
    findings_build_loss_detected: bool = False   # findings_build_loss in any feed
    feed_no_signal_sources: list[str] = field(default_factory=list)  # source URLs with zero_signal_reason
    # Sprint F228A: Policy quality feedback telemetry — populated by update_with_quality_decisions calls
    policy_quality_feedback_calls: int = 0          # total calls to update_with_quality_decisions
    policy_quality_feedback_decisions: int = 0      # total decisions processed across all calls
    policy_quality_feedback_sources: int = 0        # unique sources fed to policy
    policy_quality_feedback_errors: int = 0         # exceptions caught during policy calls
    # Sprint F169E: Public branch blocker aggregation
    public_backend_degraded: bool = False         # backend_degraded was True in any public cycle
    # Sprint F169E: Dominant blocker summary (first non-empty wins per category)
    dominant_public_blocker: str = ""            # "backend_degraded" | "public_error:{msg}"
    dominant_feed_blocker: str = ""              # one of feed blocker type names above
    dominant_branch_blocker: str = ""            # "public" or "feed" — whichever first had non-empty blocker
    branch_degradation_summary: str = ""         # e.g. "public_degraded_feed_zero"
    # Sprint F195B: Branch timeout tracking for aggressive mode
    # Incremented each time a branch (public/CT) is cancelled due to timeout
    branch_timeout_count: int = 0
    # Branch-level degradation flags — set when corresponding branch times out
    public_branch_timed_out: bool = False
    ct_branch_timed_out: bool = False
    # Sprint F195C: Forensics enrichment — CT findings enriched before storage
    forensics_enriched_ct_findings: int = 0
    # Sprint F195C: Multimodal enrichment — PDF/image findings enriched before storage
    multimodal_enriched_findings: int = 0
    # Sprint F202B: Identity stitching sidecar
    identity_candidates_found: int = 0
    identity_findings_produced: int = 0
    # Sprint F202C: Asset exposure correlator sidecar
    exposure_findings_produced: int = 0
    correlated_assets_count: int = 0
    # Sprint F202D: Leak sentinel sidecar
    leak_findings_produced: int = 0
    # Sprint F202E: Temporal archaeology sidecar
    timeline_findings_produced: int = 0
    # Sprint F202I: Evidence triage sidecar
    evidence_triage_findings_count: int = 0
    # Sprint F203A: Sprint diff sidecar
    sprint_diff_findings_produced: int = 0
    # Sprint F203C: Kill chain tagging sidecar
    kill_chain_tags_produced: int = 0
    # Sprint F203F: Wayback diff sidecar
    wayback_diff_findings_produced: int = 0
    # Sprint F203D: Evidence chain tracker
    chain_steps_recorded: int = 0
    # Sprint F204H: RIR/ASN correlator sidecar
    rir_correlation_produced: int = 0
    # Sprint F204J: Mission budget tracking
    sidecars_skipped: tuple[str, ...] = ()  # heavy sidecars skipped due to RAM pressure
    # Sprint F206BK: Acquisition strategy enforcement — optional heavy lanes skipped via acquisition plan gate
    acquisition_lanes_skipped: int = 0
    peak_rss_gib: float = 0.0  # peak RSS observed during sprint
    budget_violations: int = 0  # number of times RSS exceeded MISSION_PEAK_RSS_GIB
    # Sprint F193B: CommonCrawl + academic discovery additive truth
    cc_archive_injected: int = 0
    academic_findings_count: int = 0
    # Sprint F207A: Multi-source acquisition lane outcomes (CT/WAYBACK/PASSIVE_DNS/BLOCKCHAIN)
    acquisition_lane_outcomes: tuple = ()  # tuple[AcquisitionLaneOutcome, ...]
    # Sprint F207J-A: Lane verdict accumulators for signal_path and branch_mix truth
    lane_ct_accepted_findings: int = 0
    lane_wayback_accepted_findings: int = 0
    lane_pdns_accepted_findings: int = 0
    lane_blockchain_accepted_findings: int = 0
    # R10: IPFS CID-only lane telemetry
    lane_ipfs_accepted_findings: int = 0
    # Sprint F229: Wayback/PassiveDNS raw and candidate telemetry
    wayback_attempted: bool = False
    wayback_raw_count: int = 0
    wayback_candidates_built: int = 0
    wayback_accepted_count: int = 0
    passive_dns_attempted: bool = False
    passive_dns_raw_count: int = 0
    passive_dns_candidates_built: int = 0
    passive_dns_accepted_count: int = 0
    # Sprint F231C: Wayback advisory evidence surface
    wayback_advisory_clues_count: int = 0
    wayback_changed_url_count: int = 0
    wayback_added_url_count: int = 0
    wayback_digest_changed_count: int = 0
    wayback_unchanged_rejected: int = 0
    # Sprint F231C: PassiveDNS advisory evidence surface
    passive_dns_advisory_clues_count: int = 0
    passive_dns_private_ip_rejected: int = 0
    passive_dns_empty_ip_rejected: int = 0
    # Sprint F207M-A: Nonfeed pre-dispatch telemetry
    nonfeed_predispatch_attempted: bool = False
    nonfeed_predispatch_skipped: dict[str, str] = field(default_factory=dict)
    nonfeed_predispatch_lanes: tuple[str, ...] = ()
    nonfeed_predispatch_duration_s: float = 0.0
    windup_blocked_until_nonfeed_attempted: bool = False
    # Sprint F207L: Nonfeed lane planning debug snapshot for live KPI diagnosis
    nonfeed_plan_debug: "NonfeedPlanDebug | None" = None
    # Sprint F207Q-A: Pre-windup barrier telemetry
    prewindup_barrier_checked: bool = False
    prewindup_barrier_required_lanes: tuple[str, ...] = ()
    prewindup_barrier_satisfied: bool = False
    prewindup_barrier_attempted_lanes: tuple[str, ...] = ()
    prewindup_barrier_skipped_lanes: dict[str, str] = field(default_factory=dict)
    prewindup_barrier_errors: dict[str, str] = field(default_factory=dict)
    prewindup_barrier_duration_s: float = 0.0
    windup_delayed_for_nonfeed: bool = False
    # Sprint F207S-B: Scheduler-owned prewindup barrier — delayed cycle once
    prewindup_barrier_delayed_cycle: bool = False
    # Sprint F207S-A: Windup guard callsite telemetry — accumulated across the run
    windup_guard_call_count: int = 0
    windup_guard_callback_supplied_count: int = 0
    windup_guard_callback_executed_count: int = 0
    windup_guard_last_reason: str = ""
    windup_guard_last_phase: str = ""
    windup_guard_last_allowed: bool | None = None
    windup_guard_last_callback_not_executed_reason: str = ""
    # Sprint F223-D: prewindup barrier async bridge telemetry
    prewindup_guard_async_bridge_used: bool = False  # True when running loop detected
    prewindup_guard_async_error: str = ""            # last async/bridge error
    prewindup_guard_fail_closed: bool = False         # True when windup blocked by error
    # Sprint F207T-A: Return guard for mandatory nonfeed terminal state
    # Scheduler cannot return a valid result for domain query until PUBLIC and CT
    # have terminal state (attempted | skipped | error | timeout)
    return_guard_checked: bool = False
    return_guard_required_lanes: tuple[str, ...] = ()
    return_guard_satisfied: bool = False
    return_guard_delayed_for_nonfeed: bool = False
    return_guard_block_reason: str = ""
    return_guard_attempted_lanes: tuple[str, ...] = ()
    return_guard_skipped_lanes: dict[str, str] = field(default_factory=dict)
    return_guard_errors: dict[str, str] = field(default_factory=dict)
    # Sprint F207V-A: Scheduler exit path tracer
    # Identifies exact break/return branch used in live execution
    # to diagnose why return_guard fields are absent in live runs
    scheduler_exit_path: str | None = None
    scheduler_exit_reason: str | None = None
    scheduler_exit_phase: str | None = None
    scheduler_exit_cycle: int | None = None
    scheduler_exit_elapsed_s: float | None = None
    scheduler_exit_guard_checked: bool = False
    scheduler_exit_guard_required: tuple[str, ...] = ()
    scheduler_exit_guard_satisfied: bool | None = None
    # Sprint F212A: Hard wallclock deadline authority
    # Derived from config.sprint_duration_s at run() start; enforced before every
    # cycle and before every expensive branch dispatch.
    hard_deadline_monotonic: float | None = None
    hard_deadline_checked_count: int = 0
    hard_deadline_exceeded: bool = False
    hard_deadline_exceeded_at_cycle: int | None = None
    hard_deadline_remaining_s_at_exit: float | None = None
    # Sprint F208B: Acquisition terminality consumer — scheduler enforces terminality
    # from AcquisitionStrategy rather than owning hardcoded PUBLIC/CT policy
    acquisition_terminality_checked: bool = False
    acquisition_terminality_satisfied: bool = False
    acquisition_terminality_missing_lanes: tuple[str, ...] = ()
    acquisition_terminality_report: dict = field(default_factory=dict)
    # Sprint F208M-A: Nonfeed predispatch before final terminality
    # Before finalizing terminality, run bounded PUBLIC/CT predispatch so that
    # terminality is computed AFTER lanes have terminal state, not before.
    # This fixes the race where terminality computed before CT/PUBLIC predispatch
    # results arrived, marking CT as missing even though predispatch was attempted.
    nonfeed_predispatch_checked: bool = False
    nonfeed_predispatch_ran: bool = False
    nonfeed_predispatch_reason: str | None = None
    nonfeed_predispatch_outcomes_count: int = 0
    # Sprint F209A: Mandatory Acquisition Prelude
    # Runs before main feed cycle loop to establish early terminal state for PUBLIC/CT.
    # Canonical for domain queries: PUBLIC and CT must get one bounded attempt
    # before feed cycles dominate runtime (~120s windup lead).
    acquisition_prelude_checked: bool = False
    acquisition_prelude_ran: bool = False
    acquisition_prelude_required_lanes: tuple[str, ...] = ()
    acquisition_prelude_terminal_lanes: tuple[str, ...] = ()
    acquisition_prelude_missing_lanes: tuple[str, ...] = ()
    acquisition_prelude_skipped_lanes: dict[str, str] = field(default_factory=dict)
    acquisition_prelude_errors: dict[str, str] = field(default_factory=dict)
    acquisition_prelude_duration_s: float = 0.0
    acquisition_prelude_reason: str = ""
    acquisition_prelude_domain_detected: bool = False
    acquisition_prelude_plan_present: bool = False
    acquisition_prelude_plan_built_for_prelude: bool = False
    acquisition_prelude_domain_detection_error: str = ""
    # Sprint F216E: Feed dominance budget telemetry
    # Populated by _check_feed_dominance_budget() called per-cycle and at teardown
    feed_budget_active: bool = False                    # True when budget policy is configured
    feed_budget_reason: str = ""                       # Human-readable cap activation reason
    feed_accepted_before_cap: int = 0                 # Feed accepted count when cap first triggered
    feed_suppressed_by_budget: int = 0                 # Feed findings suppressed due to cap
    feed_budget_per_source: dict[str, int] = field(default_factory=dict)  # per-source counts at cap trigger
    top_feed_source_counts: tuple[tuple[str, int], ...] = ()  # top-N (source, count) pairs at cap trigger
    max_per_source_applied: str = ""                   # Source URL that hit per-source cap first
    # Sprint F230D: Nonfeed budget telemetry — for nonfeed_diagnostic profile
    nonfeed_budget_active: bool = False                # True when nonfeed budget policy is active
    nonfeed_budget_expected_lanes: tuple[str, ...] = ()  # Expected nonfeed lanes from nonfeed_plan_debug
    nonfeed_budget_terminal_lanes: tuple[str, ...] = ()  # Nonfeed lanes that reached terminal state
    nonfeed_budget_unresolved_lanes: tuple[str, ...] = ()  # Nonfeed lanes still unresolved
    feed_suppressed_by_nonfeed_budget: int = 0         # Feed findings suppressed by nonfeed budget
    feed_suppression_count: int = 0                    # Number of feed sources suppressed by nonfeed budget
    feed_suppression_reason: str = ""                  # Reason for nonfeed budget suppression
    # Sprint F214OPT-D: Arrow batch hard cap telemetry
    arrow_batch_hard_cap: int = 0
    arrow_batch_dropped_after_flush_failure: int = 0
    arrow_last_flush_error: str = ""
    # Sprint F215D: Early exit semantics — canonical classification of WHY run ended early
    # Populated by _compute_early_exit_class() called in _finalize_result_truth()
    requested_duration_s: float = 0.0      # planned sprint duration at run() entry
    actual_duration_s: float = 0.0        # wall-clock seconds from run() entry to return
    elapsed_pct: float = 0.0              # actual_duration_s / requested_duration_s (0.0 if requested=0)
    active_window_budget_s: float = 0.0    # sprint_duration_s minus windup lead
    active_window_elapsed_s: float = 0.0  # wall-clock spent in ACTIVE phase
    early_exit_class: str = ""             # EarlyExitClass value or "" if not yet computed
    early_exit_reason: str = ""            # Human-readable reason for early_exit_class
    # Sprint F216A: Minimal event-sourced lane truth seed
    # Bounded list of source-family lifecycle events for diagnostics
    # Event schema: {family, event, count, reason, terminal_state, ts_monotonic}
    # Max 200 events, oldest dropped when cap reached
    source_family_events: list[dict] = field(default_factory=list)
    MAX_SOURCE_FAMILY_EVENTS: int = 200   # class-level cap constant
    # Sprint F216C: PUBLIC stage machine — explicit terminal stage + bounded counters
    # terminal_stage: one of NOT_SCHEDULED | DISCOVERY_ZERO_RESULTS | DISCOVERY_TIMEOUT |
    #                 DISCOVERY_ERROR | FETCH_ZERO_SUCCESS | FETCH_TIMEOUT | FETCH_ERROR |
    #                 PARSE_ZERO_TEXT | QUALITY_REJECTED | STORAGE_REJECTED | ACCEPTED | TERMINAL
    public_terminal_stage: str = ""
    # stage_counters: bounded diagnostics for PUBLIC=0 diagnosis
    # Keys: discovered_urls, fetch_attempted, fetch_success, fetch_timeout, fetch_error,
    #       parse_attempted, parse_success, quality_rejected, storage_rejected, accepted_findings,
    #       rejection_reasons (top 5), error_samples (top 5), rejected_url_samples (top 5)
    public_stage_counters: dict = field(default_factory=dict)
    # Sprint F217B: Nonfeed Mission Controller telemetry
    # Populated by NonfeedMissionController.build_snapshot() called at teardown
    # and optionally per-cycle when nonfeed_diagnostic profile is active
    nonfeed_mission_active: bool = False
    nonfeed_required_families: tuple[str, ...] = ()
    nonfeed_optional_families: tuple[str, ...] = ()
    nonfeed_family_status: dict[str, str] = field(default_factory=dict)
    nonfeed_all_required_terminal: bool = False
    nonfeed_any_accepted: bool = False
    nonfeed_provider_failures: tuple[str, ...] = ()
    nonfeed_memory_skips: tuple[str, ...] = ()
    nonfeed_mission_exit_reason: str = ""
    # Sprint F217E: Nonfeed candidate evidence ledger summary (records kept runtime only)
    nonfeed_candidate_ledger_summary: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Sprint F207Q-A: Pre-windup barrier result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PreWindupBarrierResult:
    """
    Result of a pre-windup barrier check.

    Returned by _ensure_pre_windup_lane_terminal_states() to inform
    the windup decision whether required lanes are satisfied.
    """
    required_lanes: tuple[str, ...] = ()
    satisfied: bool = False
    attempted_lanes: tuple[str, ...] = ()
    skipped_lanes: tuple[str, ...] = ()
    error_lanes: tuple[str, ...] = ()
    duration_s: float = 0.0


# ---------------------------------------------------------------------------
# Sprint F160C: Source Economics — per-sprint bounded local economics layer
# ---------------------------------------------------------------------------

@dataclass
class SourceEconomics:
    """
    Per-source economics state for one sprint.

    All fields are in-memory only. Reset happens in _reset_result().
    No cross-sprint persistence. No background tasks.

    Bounded:
    - silent_streak: int (unbounded within sprint, capped by sprint length)
    - cooldown_until_cycle: int | None (None = not in cooldown)
    - recent_health_posture: str (one of hot/warm/lukewarm/marginal/cold)
    """
    source: str
    silent_streak: int = 0                          # consecutive cold/lukewarm cycles
    last_signal_cycle: int = -1                      # last cycle with hot/warm signal
    cooldown_until_cycle: int | None = None          # None = no cooldown active
    recent_health_posture: str = "unknown"           # hot | warm | lukewarm | marginal | cold | unknown


# ---------------------------------------------------------------------------
# Source work item
# ---------------------------------------------------------------------------

@dataclass
class SourceWork:
    """A single source fetch unit."""
    feed_url: str
    source: str  # tier key
    tier: SourceTier
    max_entries: int = 50


# ---------------------------------------------------------------------------
# Live-feed pipeline seam (lazy import to avoid heavy cold-import cost)
# ---------------------------------------------------------------------------

def _import_live_feed_pipeline():
    from hledac.universal.pipeline.live_feed_pipeline import (
        async_run_live_feed_pipeline,
        FeedPipelineRunResult,
    )
    return async_run_live_feed_pipeline, FeedPipelineRunResult


# ---------------------------------------------------------------------------
# Live-public pipeline seam (lazy import — Sprint 8XE canonical parity)
# ---------------------------------------------------------------------------

def _import_live_public_pipeline():
    from hledac.universal.pipeline.live_public_pipeline import (
        async_run_live_public_pipeline,
        PipelineRunResult,
    )
    return async_run_live_public_pipeline, PipelineRunResult


# ---------------------------------------------------------------------------
# Exporter seam (lazy import)
# ---------------------------------------------------------------------------

def _import_exporters():
    from hledac.universal.export import (
        render_diagnostic_markdown_to_path,
        render_jsonld_to_path,
        render_stix_bundle_to_path,
        render_cti_stix_bundle_to_path,
    )
    from hledac.universal.export.stix_exporter import collect_cti_export_inputs
    return (
        render_diagnostic_markdown_to_path,
        render_jsonld_to_path,
        render_stix_bundle_to_path,
        render_cti_stix_bundle_to_path,
        collect_cti_export_inputs,
    )


# ---------------------------------------------------------------------------
# Sprint 8VN: Correlation seam (lazy import)
# ---------------------------------------------------------------------------

def _import_correlate_findings():
    from hledac.universal.intelligence.workflow_orchestrator import correlate_findings
    return correlate_findings


# ---------------------------------------------------------------------------
# Sprint 8VN: Hypothesis pack seam (lazy import)
# ---------------------------------------------------------------------------

def _import_hypothesis_engine():
    from hledac.universal.brain.hypothesis_engine import HypothesisEngine
    return HypothesisEngine


# ---------------------------------------------------------------------------
# Sprint Scheduler
# ---------------------------------------------------------------------------

# Sprint 8TB: Agentic Pivot Loop
@dataclass(order=True)
class PivotTask:
    """Pivot task pro agentic pivot loop — prioritizován podle confidence × degree."""
    priority: float                    # negace → max-heap: -(confidence × degree)
    ioc_type: str = field(compare=False)
    ioc_value: str = field(compare=False)
    task_type: str = field(compare=False)  # "cve_to_github" | "ip_to_ct" | "domain_to_dns" | "hash_to_mb"

# Sprint 8RA: Persistent cross-sprint dedup via LMDB
_DEDUP_LMDB_NAME = "sprint_dedup.lmdb"
_FORENSICS_LMDB_NAME = "forensics_enrichment.lmdb"
_MULTIMODAL_LMDB_NAME = "multimodal_enrichment.lmdb"

def _get_dedup_lmdb_path() -> Path:
    from hledac.universal.paths import LMDB_ROOT
    return LMDB_ROOT / _DEDUP_LMDB_NAME


def _get_forensics_lmdb_path() -> Path:
    from hledac.universal.paths import LMDB_ROOT
    return LMDB_ROOT / _FORENSICS_LMDB_NAME


def _get_multimodal_lmdb_path() -> Path:
    from hledac.universal.paths import LMDB_ROOT
    return LMDB_ROOT / _MULTIMODAL_LMDB_NAME


class SprintScheduler:
    """
    Tier-aware sprint scheduler sidecar.

    Runs bounded feed-fetch cycles under a SprintLifecycleManager.
    Does NOT own the lifecycle — lifecycle is passed in and owned by caller.

    Authority boundaries (Sprint F350M §H5):
    - Does NOT execute tools via execute_with_limits()
    - Does NOT activate providers via acquire() or load_model()
    - Does NOT create new persistent state beyond in-sprint accumulators
    - Does NOT own lifecycle phase transitions
    - Does NOT dispatch work based on shadow pre-decision output

    Runtime mode semantics (Sprint F350M §H1-H2):
    - legacy_runtime (default): normal scheduler path — full execution
    - scheduler_shadow: read-only diagnostic path — consume_shadow_pre_decision() only
    - scheduler_active: NOT supported — any implied readiness is FALSE.
      Fallback: diagnostic-only containement. Activation requires separate verified sprint.

    Advisory gate: computed at WINDUP entry, DIAGNOSTIC ONLY.
    Shadow pre-decision: read-only parity/composition, DIAGNOSTIC ONLY.

    Dependency injection: see inject_* methods for authoritative documentation.
    """

    def __init__(self, config: SprintSchedulerConfig, ct_log_client: Any = None) -> None:
        self._config = config
        # In-sprint dedup: entry_hash → True
        self._seen_hashes: dict[str, bool] = {}
        # Per-source counters
        self._entries_per_source: dict[str, int] = {}
        self._hits_per_source: dict[str, int] = {}
        # Result accumulators
        self._result = SprintSchedulerResult()
        # Cancellation flag
        self._stop_requested = False
        # Sprint F217E: Nonfeed candidate evidence ledger (runtime only, not persisted)
        self._nonfeed_ledger: NonfeedCandidateLedger = NonfeedCandidateLedger()
        # Sprint 8RA: Store lifecycle reference for UMA callbacks
        self._lifecycle = None
        # Sprint F210A: Query stored for terminality recompute at export time
        self._query: str = ""
        # Sprint 8SA: Lifecycle adapter — normalizes runtime/ vs utils/ API
        self._lc_adapter: Optional[_LifecycleAdapter] = None
        # Sprint 8RA: Persistent cross-sprint dedup
        self._dedup_env: Optional[lmdb.Environment] = None
        self._dedup_seen: set[str] = set()  # in-memory cache for fast lookup
        self._dedup_dirty: bool = False  # True if _dedup_seen has un-flushed entries
        # Sprint 8RC: IOC-aware scoring state
        self._source_weights: dict[str, float] = {}  # source_type → hit_rate multiplier
        self._novelty_bonuses: dict[str, float] = {}  # source_type → novelty multiplier
        # Sprint F199A: Per-source quality feedback for reward-driven weight adaptation
        # Bounded accumulation: feed_url → {fetched, accepted} — reset per sprint via _reset_result
        self._source_quality_feedback: dict[str, dict[str, int]] = {}
        # Sprint F216E: Feed dominance budget per-source tracking
        # feed_url → accepted count — used to enforce per-source cap
        self._feed_accepted_per_source: dict[str, int] = {}
        # True once budget cap has been triggered (latch — once suppressed, stays suppressed)
        self._feed_budget_triggered: bool = False
        # Sprint 8TB: Agentic Pivot Loop state
        self._pivot_queue: asyncio.PriorityQueue[PivotTask] = asyncio.PriorityQueue(maxsize=200)
        # Sprint 8XE: Last sources list for public discovery query hint
        self._last_sources: list[str] = []
        self._pivot_stats: dict[str, int] = {"total": 0, "processed": 0, "errors": 0}
        self._pivot_ioc_graph: Any = None  # IOCGraph reference injected via inject_ioc_graph
        # Sprint 8UC B.4: Speculative prefetch
        self._bg_tasks: set[asyncio.Task] = set()
        self._speculative_results: dict[str, object] = {}
        self._last_speculative: float = 0.0
        # Sprint 8UC B.5: OODA loop
        self._ooda_interval: float = 60.0
        self._last_ooda: float = 0.0
        # Sprint F207M-A: Nonfeed pre-dispatch guard — set True after first predispatch runs
        self._nonfeed_predispatch_done: bool = False
        # Sprint F207S-B: Scheduler-owned prewindup barrier — set True after one delayed cycle
        self._prewindup_barrier_delayed: bool = False
        # Sprint 8VB: Adaptive timeout EMA
        # F196C: Bounded to prevent unbounded growth across sprints
        self._fetch_latency_ema: dict[str, float] = {}
        self._fetch_latency_ema_order: deque[str] = deque(maxlen=1000)  # O(1) LRU with auto-eviction
        self._MAX_FETCH_LATENCY_EMA: int = 1000  # max domains to track
        _EMA_ALPHA: float = 0.3
        _TIMEOUT_MIN: float = 5.0
        _TIMEOUT_MAX: float = 30.0
        _TIMEOUT_MULT: float = 3.0
        # Sprint 8VD §B: Arrow columnar buffer
        self._arrow_batch: list[dict] = []
        self._arrow_last_flush: float = 0.0
        self._duckdb_read_con: Optional[Any] = None
        # Sprint 8BK: Wall-clock start for duration budget guard
        self._wall_clock_start: float = 0.0
        # Sprint F212A: Hard deadline — derived from config.sprint_duration_s at run() start
        self._hard_deadline_monotonic: float | None = None
        self._hard_deadline_checked_count: int = 0
        self._ARROW_FLUSH_N: int = 1000
        # P12: Bounded Hermes lifecycle — loaded at sprint start, released at teardown
        # M1 8GB invariant: only one large model at a time (Hermes ~2GB)
        self._hermes_engine: Any = None
        self._memory_manager: Any = None
        self._ARROW_FLUSH_S: float = 60.0
        # Sprint F214OPT-D: Arrow batch hard cap — prevents unbounded growth after flush failure
        # Default: max(2 * _ARROW_FLUSH_N, 2000) = 2000
        # Env override via HLEDAC_ARROW_BATCH_HARD_CAP
        self._ARROW_BATCH_HARD_CAP: int = self._resolve_arrow_batch_hard_cap()
        # Sprint F214OPT-D: Arrow flush failure telemetry
        self._arrow_batch_dropped_after_flush_failure: int = 0
        self._arrow_last_flush_error: Optional[str] = None
        self._fetch_semaphore: asyncio.Semaphore = asyncio.Semaphore(20)
        self.sprint_id: str = ""
        # Sprint F202J: M1 resource governor advisory (lazy init)
        self._governor = None
        # Sprint 8VD §F: Scorecard tracking
        self._finding_count: int = 0
        # Partial export tracking — reset per sprint
        self._last_partial_finding_count: int = 0
        self._synthesis_engine: str = "unknown"
        # Sprint 8VI §B: RL adaptive pivot — task_type → reward history
        self._pivot_rewards: dict[str, list[float]] = {}
        # Sprint 8VI §C: Recent IOC ring buffer for hypothesis feedback
        self._recent_iocs: list[dict] = []
        # Sprint 8VI §D: IOCScorer reference (set during WARMUP)
        self._ioc_scorer: Any = None
        # Sprint F195: DuckDB store for canonical finding persistence
        self._duckdb_store: Any = None
        # Sprint F195C: Forensics enrichment layer
        self._forensics_enricher: Any = None
        self._forensics_lmdb_env: Any = None
        # Sprint F195C: Multimodal enrichment layer
        self._multimodal_enricher: Any = None
        self._multimodal_lmdb_env: Any = None
        # Sprint 8VI §D: DuckPGQGraph reference (set during WARMUP)
        self._ioc_graph: Any = None
        # Sprint F193B: Hypothesis → finding feedback loop tracking
        # Bounded iteration depth and query count to prevent runaway recursion
        self._hypothesis_depth: int = 0        # current depth of hypothesis-driven pivot chain
        self._hypothesis_query_count: int = 0  # total hypothesis-driven queries enqueued
        # Sprint 8VI §C: All findings collected during sprint
        self._all_findings: list[dict] = []
        # Sprint F200A: Prefetch oracle integration (advisory only)
        self._prefetch_oracle: Any = None
        # Sprint 8VM: Shadow pre-decision consumer — read-only, no mutable state
        self._shadow_pd_summary: Any = None
        # Sprint 8VQ: Advisory gate snapshot — ephemeral, computed at WINDUP entry, diagnostic only
        self._advisory_gate_snapshot: Any = None
        # Sprint 8VN: Correlation + hypothesis seams accumulators
        # Bounded: max 500 findings to prevent OOM on M1 8GB
        self._correlation_cache: Optional[dict] = None
        self._hypothesis_pack_cache: Optional[dict] = None
        self._branch_value_summary: Optional[dict] = None
        # Sprint F206BG: Acquisition strategy plan — built at sprint start, diagnostic only
        self._acquisition_plan: Any = None
        # Sprint F207A: Multi-source acquisition lane outcomes (CT/WAYBACK/PASSIVE_DNS/BLOCKCHAIN)
        self._lane_outcomes: tuple = ()
        # Sprint F207K-A: Rejection tracking for non-feed bridge outcomes
        self._lane_rejections: list[dict] = []
        # Sprint F218D: Lane rejection counters
        self._lane_rejections_total_seen: int = 0
        self._lane_rejections_dropped: int = 0
        # Sprint F207J-A: Lane verdict accumulators for compute_sprint_intelligence
        # (tag, signal, fallback_use, fallback_waste, quality)
        self._lane_verdicts: list[tuple[str, int, int, int, int]] = []
        # Sprint 8VN §C: Feed + public branch verdict accumulators (additive, fail-soft)
        # Capped at 10 entries to stay M1 8GB safe
        self._feed_verdicts: list[tuple[str, int, int, int, int]] = []  # (verdict_tag, s, f, w, q)
        self._public_verdicts: list[dict] = []  # public_branch_verdict dicts
        # Sprint F207H: Public pipeline outcome for source_family_outcomes consumption
        self._public_outcome: dict | None = None  # normalized public outcome dict
        # Sprint F216C: PipelineRunResult reference for stage machine computation
        self._public_pipeline_result: Any | None = None  # PipelineRunResult from _run_public_discovery_in_cycle
        # Sprint F160C: Per-sprint source economics — bounded local economics layer
        # In-memory only, reset per sprint, no cross-sprint state
        self._source_economics: dict[str, SourceEconomics] = {}
        # Sprint F193A: CT log canonical discovery client
        self._ct_log_client: Any = ct_log_client
        # Sprint F195C: Sprint policy manager (opt-in RL layer)
        self._policy_manager: Any = None
        # Sprint F202B: Identity stitching sidecar adapter
        self._identity_adapter: Any = None
        # Sprint F202C: Asset exposure correlator adapter
        self._exposure_adapter: Any = None
        # Sprint F202D: Leak sentinel adapter
        self._leak_sentinel_adapter: Any = None
        # Sprint F202I: Evidence triage adapter
        self._evidence_triage_adapter: Any = None
        # Sprint F203A: Sprint diff engine (lazy — imported inside sidecar method)
        self._diff_engine: Any = None
        # Sprint F202G: Pivot planner (advisory, advisory ordering input only)
        self._pivot_planner: Any = None
        self._planned_pivots: list = []  # Last planned pivots for diagnostics
        # Sprint F204A: Canonical sidecar bus for all accepted-finding sidecars
        self._sidecar_bus: Any = None
        # Sprint F204D: Target memory service for cross-sprint target state
        self._target_memory_service: Optional[TargetMemoryService] = None
        # Sprint F204E: Analyst workbench for sprint brief generation
        self._analyst_brief: Any = None
        # Sprint F204J: Mission budget tracking
        self._sidecars_skipped: set[str] = set()
        self._peak_rss_gib: float = 0.0
        # Sprint F205H: Metrics registry for sprint reporting (fail-soft)
        self._metrics_registry: Any = None
        self._metrics_initialized: bool = False
        # Sprint F215D: Override for _finalize_result_truth exit path — set in except block
        self._run_exit_path_override: str | None = None

    # ── Sprint F160C: Source Economics ─────────────────────────────────

    def _update_source_economics(
        self,
        feed_url: str,
        result: Any,
        current_cycle: int,
    ) -> None:
        """
        Update per-source economics from pipeline result signals.

        Uses only existing surfaces from FeedPipelineRunResult:
        - signal_stage: cold/hot diagnosis
        - feed_confidence_score: 0-100 adapter-informed confidence
        - winning_source_breakdown: signal origin analysis

        Economics state is in-memory only for the current sprint.
        Reset happens in _reset_result().
        """
        econ = self._source_economics.setdefault(
            feed_url,
            SourceEconomics(source=feed_url),
        )

        # ── Derive health posture from signal_stage ─────────────────────
        signal_stage = getattr(result, "signal_stage", "unknown") or "unknown"
        feed_conf = getattr(result, "feed_confidence_score", 0) or 0
        winning = getattr(result, "winning_source_breakdown", {}) or {}

        # Cold verdict: signal_stage indicating no usable signal
        cold_stages = {"empty_registry", "no_pattern_hits", "content_empty"}
        is_cold = signal_stage in cold_stages or feed_conf == 0

        # Sprint posture derived from pipeline signals
        match ():
            case _ if signal_stage == "prestore_findings_present":
                econ.recent_health_posture = "hot"
                econ.last_signal_cycle = current_cycle
                econ.silent_streak = 0
                econ.cooldown_until_cycle = None
            case _ if feed_conf >= 60:
                econ.recent_health_posture = "warm"
                econ.last_signal_cycle = current_cycle
                econ.silent_streak = 0
                econ.cooldown_until_cycle = None
            case _ if feed_conf >= 20:
                econ.recent_health_posture = "lukewarm"
                econ.silent_streak = (econ.silent_streak + 1) if econ.silent_streak > 0 else 1
                if econ.cooldown_until_cycle is None and econ.silent_streak >= 2:
                    econ.cooldown_until_cycle = current_cycle + 3
            case _ if is_cold:
                econ.recent_health_posture = "cold"
                if econ.cooldown_until_cycle is None:
                    econ.silent_streak += 1
                    if econ.silent_streak >= 2:
                        econ.cooldown_until_cycle = current_cycle + 3
            case _:
                econ.recent_health_posture = "marginal"
                econ.silent_streak = (econ.silent_streak + 1) if econ.silent_streak > 0 else 1

        # Winning source analysis — if feed_native dominates, source is self-sufficient
        feed_native_hits = winning.get("feed_native", 0)
        fallback_hits = winning.get("fallback", 0)
        if feed_native_hits > fallback_hits * 2 and feed_native_hits > 0:
            econ.recent_health_posture = "hot"  # feed-native signal is strong

    def _get_source_economics(self, feed_url: str) -> SourceEconomics | None:
        """Return economics state for a source, or None if never seen."""
        return self._source_economics.get(feed_url)

    def _is_source_in_cooldown(self, feed_url: str, current_cycle: int) -> bool:
        """True if source is in bounded cooldown and cycle hasn't exceeded it."""
        econ = self._source_economics.get(feed_url)
        if econ is None:
            return False
        if econ.cooldown_until_cycle is None:
            return False
        return current_cycle < econ.cooldown_until_cycle

    def _should_deprioritize_source(self, feed_url: str, current_cycle: int) -> bool:
        """
        Return True if source should be deprioritized this cycle.

        Deprioritization conditions (all bounded, all in-memory):
        1. Source is in cooldown — pushed to end of work list
        2. Silent streak >= 4 cycles — deprioritized but NOT excluded
        """
        econ = self._source_economics.get(feed_url)
        if econ is None:
            return False
        if self._is_source_in_cooldown(feed_url, current_cycle):
            return True
        if econ.silent_streak >= 4:
            return True
        return False

    def _sort_work_items_by_economics(
        self,
        items: list[SourceWork],
        current_cycle: int,
    ) -> list[SourceWork]:
        """
        Re-sort work items by source economics.

        Order:
        1. Sources NOT in cooldown first (natural priority)
        2. Sources with hot/warm posture boosted
        3. Cold/in-cooldown sources at the end
        4. Tier ordering still applies as secondary sort key
        5. F200A: Advisory prefetch oracle score multiplies the sort key

        F200A: oracle is ADVISORY ONLY — scheduler retains authority.
        If oracle is None or suggest_scores fails → falls back to default ordering.
        """
        def economics_sort_key(item: SourceWork) -> tuple:
            econ = self._source_economics.get(item.feed_url)
            # Tier primary sort (from config)
            tier_order = _TIER_ORDER.index(item.tier)

            if econ is None:
                # Never-seen sources: neutral (0)
                return (0, tier_order, 0, item.feed_url)

            in_cooldown = self._is_source_in_cooldown(item.feed_url, current_cycle)
            streak = econ.silent_streak
            posture_score = {
                "hot": 0,
                "warm": 1,
                "lukewarm": 2,
                "marginal": 3,
                "cold": 4,
            }.get(econ.recent_health_posture, 5)

            if in_cooldown:
                # In cooldown: pushed to end of its tier band (tier primary, cooldown posture=5)
                return (tier_order, 5, streak, item.feed_url)
            return (tier_order, posture_score, streak, item.feed_url)

        # F200A: Get advisory oracle scores (fail-soft)
        oracle_scores: dict[str, float] = {}
        if self._prefetch_oracle is not None:
            try:
                oracle_scores = self._prefetch_oracle.suggest_scores(items, current_cycle)
            except Exception:
                # Advisory only — fall back to default ordering
                oracle_scores = {}

        # Sort with oracle advisory (oracle score affects effective priority)
        def oracle_sort_key(item: SourceWork) -> tuple:
            base_key = economics_sort_key(item)
            # Oracle score multiplier: higher score → lower tuple value → higher priority
            # neutral oracle score = 1.0 → no change
            oracle_mult = oracle_scores.get(item.feed_url, 1.0)
            # Scale oracle multiplier into sort key (oracle range [0.1, 3.0])
            # This shifts items up/down within their tier/posture band
            oracle_shift = (oracle_mult - 1.0) * 10
            return (base_key[0], base_key[1], base_key[2] - oracle_shift, base_key[3])

        return sorted(items, key=oracle_sort_key)

    # ── Sprint 8VI §B: RL Adaptive Pivot ────────────────────────────────

    def record_pivot_outcome(
        self, task_type: str, found_count: int, elapsed_s: float
    ) -> None:
        """
        Zaznamenej výsledek pivot tasku jako reward signal pro RL.
        reward = findings per second (FPS) — normalizovaný na [0, 1].
        """
        import math
        if elapsed_s <= 0:
            return
        fps = found_count / elapsed_s
        # log1p pro sub-lineární scaling, max 1.0
        reward = min(1.0, math.log1p(fps) / math.log1p(10))
        history = self._pivot_rewards.setdefault(task_type, [])
        history.append(reward)
        # Udržuj pouze posledních 20 epizod
        if len(history) > 20:
            self._pivot_rewards[task_type] = history[-20:]

    # ── Sprint F203G: Hypothesis Feedback ─────────────────────────────────

    async def record_hypothesis_feedback(
        self,
        pivot_type: str,
        ioc_type: str,
        produced_count: int,
        accepted_count: int,
        signal_value: float,
    ) -> None:
        """
        F203G: Record hypothesis feedback to DuckDB for future pivot planning.

        Persists a HypothesisFeedbackRecord to the duckdb_store for aggregation
        and use by PivotPlanner to penalize low-yield pivot types.

        Silently fails if duckdb_store is unavailable.

        Args:
            pivot_type: domain/identity/leak/archive/graph
            ioc_type: The IOC type operated on
            produced_count: Number of findings produced by this pivot
            accepted_count: Number of findings accepted (stored)
            signal_value: reward signal [0.0, 1.0]
        """
        store = getattr(self, "_duckdb_store", None)
        if store is None:
            return
        try:
            from hledac.universal.runtime.hypothesis_feedback import (
                HypothesisFeedbackRecord,
            )
            import uuid
            import time as _time

            record = HypothesisFeedbackRecord(
                id=str(uuid.uuid4()),
                target_id=getattr(self, "sprint_id", "") or "default",
                pivot_type=pivot_type,
                ioc_type=ioc_type,
                produced_count=produced_count,
                accepted_count=accepted_count,
                signal_value=signal_value,
                ts=_time.time(),
            )
            await store.async_record_hypothesis_feedback(record)
        except Exception:
            pass  # Fail-safe: feedback recording must never crash sprint

    # ── Sprint F212A: Hard deadline authority ───────────────────────────────

    def _check_hard_deadline(self) -> bool:
        """
        Check if the hard monotonic deadline has been exceeded.

        Returns:
            True if deadline is NOT exceeded (new work may proceed).
            False if deadline IS exceeded (no new branch dispatch).

        This method is idempotent — it can be called multiple times per cycle
        without changing state. Deadline-exceeded state is tracked once in
        the result and never reset.
        """
        if self._hard_deadline_monotonic is None:
            return True  # No deadline set — always allowed

        self._result.hard_deadline_checked_count += 1

        if self._result.hard_deadline_exceeded:
            return False  # Already exceeded — stay stopped

        elapsed = _time.monotonic() - self._hard_deadline_monotonic

        if elapsed >= 0.0:
            # Deadline exceeded — record state once
            self._result.hard_deadline_exceeded = True
            self._result.hard_deadline_exceeded_at_cycle = self._result.cycles_started
            self._result.hard_deadline_remaining_s_at_exit = 0.0
            log.warning(
                f"[F212A] Hard deadline exceeded at cycle {self._result.cycles_started}. "
                f"Elapsed={elapsed:.1f}s. Stopping new work."
            )
            return False

        return True

    def _get_adaptive_priority(
        self, task_type: str, base_priority: float = 0.5
    ) -> float:
        """
        Vrátí EMA reward jako priority modifikátor.
        Task types s vyšší historickou yield dostávají vyšší prioritu.
        """
        history = self._pivot_rewards.get(task_type, [])
        if not history:
            return base_priority
        # EMA with alpha=0.3 (recent weighted)
        ema = history[0]
        for r in history[1:]:
            ema = 0.3 * r + 0.7 * ema
        # Mix: 70% EMA reward + 30% base priority
        return round(0.7 * ema + 0.3 * base_priority, 4)

    # ── Public API ─────────────────────────────────────────────────────────

    async def run(
        self,
        lifecycle: Any,
        sources: Sequence[str],
        now_monotonic: Optional[float] = None,
        query: str = "",
        duckdb_store: Any = None,
        ct_log_client: Any = None,
        policy_manager: Any = None,
        progress_callback: Optional[Any] = None,
    ) -> SprintSchedulerResult:
        """
        Run the sprint to completion.

        Args:
            lifecycle: SprintLifecycleManager instance (owned by caller)
            sources: ordered list of feed URLs to process
            now_monotonic: optional fake clock for testing

        Returns:
            SprintSchedulerResult with final statistics
        """
        # Sprint 8SA: Lifecycle adapter — bridges runtime/ vs utils/ API
        adapter = _LifecycleAdapter(lifecycle)
        # Sprint F210A: Store query for terminality recompute at export time
        self._query = query
        # Sprint F206C: Lifecycle runner — encapsulates lifecycle orchestration
        self._runner = SprintLifecycleRunner(lifecycle, adapter)
        # Start lifecycle via runner (BOOT→WARMUP)
        self._runner.setup()
        self._reset_result()
        # Sprint F207V-D: Initialize wall-clock anchor for scheduler_exit_elapsed_s
        self._run_started_at: float = _time.monotonic()

        # Sprint F202J: Initialize M1 resource governor (lazy, advisory only)
        try:
            from hledac.universal.runtime.resource_governor import get_governor
            self._governor = get_governor()
        except Exception:
            self._governor = None

        # Sprint F204A: Initialize canonical sidecar bus (bounded orchestrator for all accepted-finding sidecars)
        self._sidecar_bus = create_sidecar_bus(governor=self._governor)

        # Sprint F205F: Extracted sidecar dispatch bookkeeping
        self._sidecar_dispatcher = SidecarDispatcher(
            bus=self._sidecar_bus,
            governor=self._governor,
            result_sink=self._result,
        )

        # Sprint F193A: CT log client can be injected at run() call time
        if ct_log_client is not None:
            self._ct_log_client = ct_log_client

        # Sprint 8VD: Set sprint_id from lifecycle if available
        try:
            self.sprint_id = getattr(lifecycle, "sprint_id", "") or ""
        except Exception:
            self.sprint_id = ""

        # Sprint 8RA: Store lifecycle ref for callbacks
        self._lifecycle = lifecycle
        # Sprint 8SA: Store adapter for all lifecycle access in this run
        self._lc_adapter = adapter

        # Sprint F195C: Inject opt-in RL policy manager
        if policy_manager is not None:
            self.inject_policy_manager(policy_manager)

        # Sprint 8BK: Record wall-clock start for duration budget guard
        self._wall_clock_start = _time.monotonic()
        # Sprint F212A: Derive hard deadline from sprint_duration_s
        self._hard_deadline_monotonic = self._wall_clock_start + self._config.sprint_duration_s
        self._result.hard_deadline_monotonic = self._hard_deadline_monotonic
        # Sprint F214OPT-D: Wire Arrow batch hard cap to result
        self._result.arrow_batch_hard_cap = self._ARROW_BATCH_HARD_CAP
        # Sprint F195: Store duckdb_store on self for task handler access
        self._duckdb_store = duckdb_store

        # Sprint F195C: Initialize forensics enricher and LMDB
        await self._init_forensics()
        # Sprint F195C: Initialize multimodal enricher and LMDB
        await self._init_multimodal()
        # Sprint F205H: Initialize metrics registry (fail-soft, run_dir from config or default)
        await self._init_metrics_registry()
        # F205H: Capture baseline RSS at sprint start (not just at cycle end)
        self._tick_metrics_on_cycle_end()

        # E4: Register GC sprint callbacks (remove stale handle first to prevent doubles)
        global _gc_sprint_callback_handle
        if _gc_sprint_callback_handle is not None:
            gc.callbacks.remove(_gc_sprint_callback_handle)
        _gc_sprint_stats.clear()
        _gc_sprint_callback_handle = _gc_sprint_callback
        gc.callbacks.append(_gc_sprint_callback)

        # E2: Opt-in tracemalloc snapshot diff via HLEDAC_TRACEMALLOC env var
        # Sprint F219M: init before try so finally always has consistent reference
        _trace_snap_before: Any = None
        _trace_enabled = os.environ.get("HLEDAC_TRACEMALLOC")
        if _trace_enabled:
            try:
                import tracemalloc
                tracemalloc.start(10)  # 10-frame depth limit
                _trace_snap_before = tracemalloc.take_snapshot()
            except Exception as _e:
                log.warning(f"[E2] tracemalloc start failed: {_e}")
                _trace_enabled = False  # prevent finally from attempting stop/compare

        # Initial tick to enter ACTIVE
        phase = self._runner.tick(now_monotonic)

        # Sprint 8UA: Fix lifecycle WARMUP→ACTIVE transition
        # Sprint F206C: Delegated to runner.ensure_active()
        self._runner.ensure_active(now_monotonic)

        # Sprint 8RA: Load persistent dedup at BOOT
        _dedup_t0 = _time.monotonic()
        await self._load_dedup()
        _dedup_elapsed = _time.monotonic() - _dedup_t0
        self._result.dedup_preload_elapsed_s = _dedup_elapsed
        self._result.dedup_preload_count = len(self._dedup_seen) if hasattr(self, '_dedup_seen') and self._dedup_seen is not None else 0

        # Sprint F203D: Initialize evidence chain builder at sprint start
        try:
            from hledac.universal.knowledge.evidence_chain import EvidenceChainBuilder, set_global_builder
            set_global_builder(EvidenceChainBuilder())
        except Exception:
            pass  # Fail-soft: chain tracking is optional advisory

        # Sprint F166B: Identify pre-loop cost center (additive — first reason only)
        if _dedup_elapsed > 1.0 and not self._result.pre_loop_blocker_reason:
            self._result.pre_loop_blocker_reason = "dedup_preload"

        # P12: Hermes prewarm — explicit policy by mode (bounded M1 8GB lifecycle)
        # Aggressive mode: prewarm before fan-out, unless RSS > 4GB (skip fail-soft)
        # Stable mode: current safe behavior via ModelManager memory guards
        # Hermes ~2GB: loaded once at sprint start, released at teardown
        self._hermes_engine = None
        self._memory_manager = None
        try:
            await self._prewarm_hermes_for_sprint()
        except Exception as e:
            log.debug(f"[P12] Hermes prewarm failed, ToT will be skipped: {e}")
            self._hermes_engine = None

        # Sprint 8SA: Source scoring — order sources by priority at start of ACTIVE
        _DEFAULT_SOURCE_TYPES = [
            "cisa_kev", "threatfox_ioc", "urlhaus_recent",
            "feodo_ip", "openphish_feed",
        ]
        _graph_stats: dict[str, int] = {"nodes": 0, "edges": 0}
        ordered_sources = self.prioritize_sources(
            list(sources) if sources else _DEFAULT_SOURCE_TYPES, _graph_stats
        )

        # Sprint F166B: Capture pre-loop surfaces before entering while loop
        _pre_loop_elapsed = _time.monotonic() - self._wall_clock_start
        self._result.pre_loop_elapsed_s = _pre_loop_elapsed
        # entered_active_at_monotonic: first observation of ACTIVE (loop guard)
        self._result.entered_active_at_monotonic = _pre_loop_elapsed

        # Sprint F206BG: Build acquisition strategy plan at sprint start
        try:
            from hledac.universal.core.resource_governor import sample_uma_status
            uma = sample_uma_status()
            _uma_state = "ok"
            if uma.is_emergency:
                _uma_state = "emergency"
            elif uma.is_critical:
                _uma_state = "critical"
            elif uma.is_warn:
                _uma_state = "warn"
            self._acquisition_plan = build_acquisition_plan(
                query=query,
                duration_s=self._config.sprint_duration_s,
                aggressive_mode=self._config.aggressive_mode,
                uma_state=_uma_state,
                swap_detected=uma.swap_detected,
                accepted_findings_so_far=self._result.accepted_findings,
                branch_timeout_count=self._result.branch_timeout_count,
                # F223A: Explicit acquisition profile override from config
                acquisition_profile=self._config.acquisition_profile,
            )
            # [F207L] Capture nonfeed_plan_debug from acquisition plan for KPI telemetry
            self._result.nonfeed_plan_debug = getattr(self._acquisition_plan, 'nonfeed_plan_debug', None)
            # F216F: Generate pivot candidates from query and fill telemetry
            # F226A: Pass mission_intent for mission-aware scoring
            if self._result.nonfeed_plan_debug is not None:
                try:
                    _nd = self._result.nonfeed_plan_debug
                    _pivot_candidates = generate_pivot_candidates_from_query(
                        query, mission_intent=_nd.mission_intent
                    )
                    _nd.pivot_candidates_count = len(_pivot_candidates)
                    _nd.pivot_candidate_types = tuple(set(p.pivot_type for p in _pivot_candidates))
                    _nd.pivot_scheduled_lanes = ()
                    _nd.pivot_skip_reason = None
                    _nd.pivot_errors = ()
                except Exception:
                    pass  # Fail-soft
        except Exception:
            self._acquisition_plan = None

        # Sprint F209A: Run Mandatory Acquisition Prelude BEFORE main feed cycle loop
        # This establishes early terminal state for PUBLIC/CT before feed cycles dominate runtime
        await self._run_mandatory_acquisition_prelude(
            self._result, query, duckdb_store, self._ct_log_client
        )
        # Capture initial terminality after prelude
        await self._finalize_result_truth("prelude_complete", "acquisition prelude finished", "BOOT", query)

        try:
            # Sprint 8VD §C: Start memory pressure monitoring loop
            _t = asyncio.create_task(self._memory_pressure_loop(), name="sprint:memory_pressure_loop")
            self._bg_tasks.add(_t)
            _t.add_done_callback(self._bg_tasks.discard)

            while not self._runner.is_terminal():
                # Sprint F212A: Hard deadline check FIRST — before any lifecycle
                # transitions or guards that could break early. Ensures deadline
                # is always enforced regardless of windup_guard or 8BK guard state.
                if not self._check_hard_deadline():
                    # Deadline exceeded — stop starting new work
                    await self._ensure_nonfeed_predispatch_before_finalization(
                        query, "hard_deadline_exceeded"
                    )
                    self._capture_timing_fields()
                    await self._finalize_result_truth(
                        "hard_deadline_exceeded",
                        f"hard deadline exceeded at cycle {self._result.cycles_started}",
                        "GATHER",
                        query,
                    )
                    break
                if self._stop_requested:
                    # Sprint F207T-A: Return guard — ensure mandatory nonfeed terminal state
                    if await self._ensure_mandatory_nonfeed_before_return(
                        query, duckdb_store, "stop_requested"
                    ):
                        await self._ensure_nonfeed_predispatch_before_finalization(
                            query, "stop_requested_break"
                        )
                        self._capture_timing_fields()
                        await self._finalize_result_truth("stop_requested_break", "stop_requested guard passed", "GATHER", query)
                        break
                    # Guard blocked: continue loop to satisfy nonfeed lanes
                    continue
                # Detect abort requested via lifecycle flag
                if self._runner.abort_requested:
                    self._result.aborted = True
                    self._result.abort_reason = self._runner.abort_reason or "lifecycle_abort"
                    # Sprint F195B: write partial on abort so latest state survives
                    await self._maybe_export_partial(lifecycle)
                    # Sprint F207T-A: Return guard — ensure mandatory nonfeed before abort return
                    # Abort is terminal; attempt guard but do not block abort
                    await self._ensure_mandatory_nonfeed_before_return(
                        query, duckdb_store, "lifecycle_abort"
                    )
                    await self._ensure_nonfeed_predispatch_before_finalization(
                        query, "lifecycle_abort_break"
                    )
                    self._capture_timing_fields()
                    await self._finalize_result_truth("lifecycle_abort_break", "abort_requested from lifecycle", "GATHER", query)
                    break

                # Periodic tick
                phase = self._runner.tick(now_monotonic)

                # ── Sprint F207M-A: Nonfeed pre-dispatch checkpoint ───────────
                # Run BEFORE windup guard so CT is attempted before early windup can fire.
                # Called once per sprint; subsequent calls are no-ops.
                await self._maybe_dispatch_nonfeed_probe_lanes(query, duckdb_store)

                # ── Sprint F207S-B: Scheduler-owned prewindup barrier ───────
                # Primary windup gate: scheduler ensures barrier terminal state BEFORE
                # it asks the lifecycle runner whether windup is allowed.
                # Even if the lifecycle runner's callback is missed, the scheduler
                # cannot enter windup until required lanes are terminal.
                #
                # Sprint F206C: windup_guard delegated to runner
                # Sprint F207M-A: nonfeed pre-dispatch guard
                # Sprint F207R-A: windup_guard accepts pre_windup_barrier callback
                # Sprint F207S-A: telemetry for callback observation
                # Sprint F207S-B: scheduler barrier is the primary gate; callback is secondary
                self._result.windup_guard_call_count += 1

                _barrier_result = await self._ensure_pre_windup_lane_terminal_states(
                    query, self._acquisition_plan, "ok"
                )
                _barrier_satisfied = getattr(_barrier_result, "satisfied", False)
                _barrier_required = getattr(_barrier_result, "required_lanes", ())
                _barrier_delayed = self._prewindup_barrier_delayed

                if _barrier_required and not _barrier_satisfied and not _barrier_delayed:
                    # Not satisfied and first delay — mark delayed, yield one cycle
                    self._prewindup_barrier_delayed = True
                    self._result.prewindup_barrier_delayed_cycle = True
                    log.debug(
                        "[F207S-B] Prewindup barrier not satisfied (required=%s) — delaying cycle once",
                        _barrier_required,
                    )
                    # Continue the active loop once instead of entering windup
                    continue

                # Barrier satisfied or already delayed once — delegate to runner
                _guard_result = self._runner.windup_guard(
                    now_monotonic,
                    pre_windup_barrier=lambda: self._check_prewindup_barrier_sync(
                        query, duckdb_store
                    ),
                )
                _obs = self._runner.last_guard_observation
                if _obs:
                    # F208M-B: Only increment supplied when callback was actually reached
                    if _obs.get("callback_supplied"):
                        self._result.windup_guard_callback_supplied_count += 1
                    self._result.windup_guard_callback_executed_count += 1 if _obs.get("callback_executed") else 0
                    self._result.windup_guard_last_reason = _obs.get("reason", "")
                    self._result.windup_guard_last_phase = _obs.get("phase", "")
                    self._result.windup_guard_last_allowed = _obs.get("allowed")
                    self._result.windup_guard_last_callback_not_executed_reason = _obs.get("callback_not_executed_reason", "")
                if _guard_result:
                    # If nonfeed pre-dispatch hasn't run yet, yield to it first
                    if not self._nonfeed_predispatch_done:
                        log.debug("[F207M-A] Windup signalled but pre-dispatch not done — yielding")
                        # Give pre-dispatch a chance before entering windup
                        await self._maybe_dispatch_nonfeed_probe_lanes(query, duckdb_store)

                    # Phase already advanced via tick(); let scheduler handle pre-windup ops
                    # Sprint 8RA: Flush dedup at WINDUP entry
                    await self._flush_dedup()
                    # Sprint F195C: Flush forensics at WINDUP entry
                    await self._flush_forensics()
                    # Sprint 8VQ: Evaluate advisory gate at WINDUP entry (diagnostic only)
                    self.evaluate_advisory_gate()
                    # Sprint F195B: write partial on early windup so latest state survives
                    await self._maybe_export_partial(lifecycle)
                    # Sprint F207V-D: Return guard — windup barrier is terminal; ensure
                    # mandatory nonfeed lanes before breaking out of work loop
                    if await self._ensure_mandatory_nonfeed_before_return(
                        query, duckdb_store, "windup_barrier"
                    ):
                        await self._ensure_nonfeed_predispatch_before_finalization(
                            query, "windup_barrier_passed"
                        )
                        self._capture_timing_fields()
                        await self._finalize_result_truth("windup_barrier_passed", "pre-windup barrier satisfied, entered windup", "WINDUP", query)
                        break  # exit work loop → teardown
                    # Guard blocked — force one bounded terminalization pass then break
                    await self._ensure_mandatory_nonfeed_before_return(
                        query, duckdb_store, "windup_barrier_forced"
                    )
                    await self._ensure_nonfeed_predispatch_before_finalization(
                        query, "windup_barrier_break"
                    )
                    self._capture_timing_fields()
                    await self._finalize_result_truth("windup_barrier_break", "pre-windup barrier unsatisfied, forced terminalization", "WINDUP", query)
                    break  # exit work loop → teardown

                # ── Sprint 8SA: Source scoring re-ordering ───────────────────
                # Re-prioritize at the start of each ACTIVE cycle using latest graph stats
                current_phase_str = self._runner.current_phase
                if current_phase_str == "ACTIVE":
                    ordered_sources = self.prioritize_sources(
                        ordered_sources, _graph_stats
                    )

                # ── Run one cycle ───────────────────────────────────────────
                # Enforce max_cycles BEFORE starting new work
                if self._result.cycles_started >= self._config.max_cycles:
                    # Sprint F207T-A: Return guard — max cycles is terminal, force one final
                    # barrier dispatch to satisfy mandatory lanes, then break
                    await self._ensure_mandatory_nonfeed_before_return(
                        query, duckdb_store, "max_cycles"
                    )
                    await self._ensure_nonfeed_predispatch_before_finalization(
                        query, "max_cycles_break"
                    )
                    self._capture_timing_fields()
                    await self._finalize_result_truth("max_cycles_break", "cycles >= max_cycles reached", "GATHER", query)
                    break

                self._result.cycles_started += 1
                # Sprint F166B: Capture first_cycle_started at cycles_started += 1
                if self._result.first_cycle_started_at_monotonic is None:
                    self._result.first_cycle_started_at_monotonic = _time.monotonic() - self._wall_clock_start
                    # Sprint F166B: Check starvation — gap > 30s = pre-active starvation
                    gap = self._result.first_cycle_started_at_monotonic - self._result.entered_active_at_monotonic
                    if gap > 30.0:
                        self._result.pre_active_starved = True
                        if not self._result.pre_loop_blocker_reason:
                            self._result.pre_loop_blocker_reason = "pre_loop_slow"
                # Sprint 8BK: Wall-clock duration guard — catches cases where lifecycle
                # remaining_time() does not decrease between cycles (e.g. async tick gap).
                # Force-enter-windup if wall-clock exceeds sprint_duration_s + grace.
                # Grace = one cycle budget; prevents false trigger on exact boundary.
                elapsed_wall = _time.monotonic() - self._wall_clock_start
                if elapsed_wall > self._config.sprint_duration_s + self._config.cycle_sleep_s:
                    log.warning(
                        f"[8BK] Duration budget exceeded: {elapsed_wall:.1f}s "
                        f"> {self._config.sprint_duration_s + self._config.cycle_sleep_s:.1f}s "
                        f"(grace={self._config.cycle_sleep_s:.1f}s). Forcing windup."
                    )
                    # Sprint F207T-A: Return guard — duration budget is terminal urgency,
                    # force one final barrier dispatch, then proceed to windup
                    await self._ensure_mandatory_nonfeed_before_return(
                        query, duckdb_store, "duration_budget"
                    )
                    await self._ensure_nonfeed_predispatch_before_finalization(
                        query, "duration_budget_break"
                    )
                    self._capture_timing_fields()
                    await self._finalize_result_truth("duration_budget_break", "duration_budget exhausted", "GATHER", query)
                    break
                # Sprint 8XE: Store sources for public discovery query hint
                self._last_sources = list(ordered_sources)
                cycle_ok = await self._run_one_cycle(
                    lifecycle, ordered_sources, now_monotonic, query, duckdb_store
                )
                self._result.cycles_completed += 1

                # Sprint F205H: Tick metrics at cycle completion (bounded, fail-soft)
                self._tick_metrics_on_cycle_end()

                # Sprint F195C: Progress callback for dashboard / observability
                if progress_callback is not None:
                    elapsed_s = _time.monotonic() - self._wall_clock_start
                    try:
                        progress_callback(self._result, current_phase_str, elapsed_s)
                    except Exception:
                        pass  # fail-safe: dashboard must never affect sprint

                # Sprint F195B: Partial export every N findings in aggressive mode
                await self._maybe_export_partial(lifecycle)

                # Sprint 8TB: Drain pivot queue after each ACTIVE cycle
                if current_phase_str == "ACTIVE":
                    pivot_n = await self._drain_pivot_queue()
                    if pivot_n:
                        log.debug(f"Pivot queue drained: {pivot_n} tasks, stats={self._pivot_stats}")

                if not cycle_ok:
                    # Sprint F207T-A: Return guard — cycle failed, check nonfeed terminal
                    if await self._ensure_mandatory_nonfeed_before_return(
                        query, duckdb_store, "cycle_ok_false"
                    ):
                        await self._ensure_nonfeed_predispatch_before_finalization(
                            query, "cycle_ok_false_break"
                        )
                        self._capture_timing_fields()
                        await self._finalize_result_truth("cycle_ok_false_break", "cycle returned False, guard passed", "GATHER", query)
                        break
                    # Guard blocked: continue loop to satisfy nonfeed lanes
                    continue

                # Early exit check
                if (
                    self._config.stop_on_first_accepted
                    and self._result.accepted_findings > 0
                ):
                    self._result.stop_requested = True
                    # Sprint F207T-A: Return guard — ensure mandatory nonfeed terminal
                    if await self._ensure_mandatory_nonfeed_before_return(
                        query, duckdb_store, "stop_on_first_accepted"
                    ):
                        await self._ensure_nonfeed_predispatch_before_finalization(
                            query, "stop_on_first_accepted_break"
                        )
                        self._capture_timing_fields()
                        await self._finalize_result_truth("stop_on_first_accepted_break", "first accepted finding, stop", "GATHER", query)
                        break
                    # Guard blocked: continue loop to satisfy nonfeed lanes
                    continue

                # Sleep between cycles (short interval, not one long sleep)
                # Sprint F206C: Delegated to runner.sleep_or_abort()
                await self._runner.sleep_or_abort(self._config.cycle_sleep_s)

                # ── Post-sleep windup gate ──────────────────────────────────
                # Sprint F206C: Delegated to runner.post_sleep_gate()
                if self._runner.post_sleep_gate(now_monotonic):
                    # Sprint F195B: write partial on windup so latest state survives
                    await self._maybe_export_partial(lifecycle)
                    # Sprint F207U-C: Return guard — check return value; if guard
                    # blocked, continue loop once more to satisfy nonfeed lanes before
                    # allowing windup break. This prevents post_sleep_gate from
                    # bypassing the mandatory PUBLIC/CT terminal-state requirement.
                    if await self._ensure_mandatory_nonfeed_before_return(
                        query, duckdb_store, "post_sleep_windup"
                    ):
                        await self._ensure_nonfeed_predispatch_before_finalization(
                            query, "post_sleep_windup_break"
                        )
                        self._capture_timing_fields()
                        await self._finalize_result_truth("post_sleep_windup_break", "post_sleep gate windup, guard passed", "WINDUP", query)
                        break
                    # Guard blocked — continue loop once to satisfy nonfeed lanes
                    continue

                # Sprint 8UC B.4: Speculative prefetch every 15s
                now_mono = _time.monotonic()
                if (now_mono - self._last_speculative) >= 15.0:
                    _t = asyncio.create_task(self._speculative_prefetch(n=3), name="sprint:speculative_prefetch")
                    self._bg_tasks.add(_t)
                    _t.add_done_callback(self._bg_tasks.discard)
                    self._last_speculative = now_mono

                # Sprint 8UC B.5: OODA cycle every 60s
                if (now_mono - self._last_ooda) >= self._ooda_interval:
                    _t = asyncio.create_task(self._run_ooda_cycle(self._pivot_ioc_graph), name="sprint:ooda_cycle")
                    self._bg_tasks.add(_t)
                    _t.add_done_callback(self._bg_tasks.discard)
                    self._last_ooda = now_mono

        except Exception as exc:
            self._runner.abort(f"scheduler_exception:{type(exc).__name__}")
            self._result.aborted = True
            self._result.abort_reason = f"{type(exc).__name__}"
            # Sprint F215D: Mark exception path so _finalize_result_truth knows this is an error abort
            self._run_exit_path_override = "aborted_by_error"

        finally:
            # E4: Remove GC callbacks and log stats
            if _gc_sprint_callback_handle is not None:
                gc.callbacks.remove(_gc_sprint_callback_handle)
                _gc_sprint_callback_handle = None
                if _gc_sprint_stats:
                    log.debug(f"[E4] GC sprint stats: {len(_gc_sprint_stats)} collections")

            # E2: Opt-in tracemalloc snapshot diff
            if _trace_snap_before is not None and _trace_enabled:
                try:
                    import tracemalloc
                    tracemalloc.stop()
                    snap_after = tracemalloc.take_snapshot()
                    diff = snap_after.compare_to(_trace_snap_before, 'lineno')
                    for stat in diff[:10]:
                        log.info(f"[E2] Alloc delta: {stat}")
                except Exception as _e:
                    log.warning(f"[E2] tracemalloc compare failed: {_e}")
                finally:
                    # Sprint F219M: delete snapshot refs so they cannot survive beyond sprint
                    # Use try/except since snap_after/diff may not be defined if tracemalloc.stop() raised
                    try:
                        del snap_after
                    except NameError:
                        pass
                    try:
                        del diff
                    except NameError:
                        pass

        # ── Teardown / Export ───────────────────────────────────────────────
        # Sprint F206C: Delegated to runner.teardown()
        self._runner.teardown()
        if self._config.export_enabled:
            await self._run_export(lifecycle)

        self._result.final_phase = self._runner.current_phase

        # Sprint 8RA: Close persistent dedup at TEARDOWN
        await self._close_dedup()
        # Sprint F195C: Close forensics enricher and LMDB at TEARDOWN
        await self._close_forensics()
        # Sprint F195C: Close multimodal enricher and LMDB at TEARDOWN
        await self._close_multimodal()

        # Sprint F206D: Run all advisory steps via extracted runner (fail-soft, non-blocking)
        await self._run_advisory_runner()

        # P12: Release Hermes engine at teardown via ModelManager (bounded M1 8GB lifecycle)
        await self._unload_hermes_at_teardown()

        # Sprint 8UC B.4: Cancel all background speculative tasks
        for t in list(self._bg_tasks):
            t.cancel()
        if self._bg_tasks:
            await asyncio.gather(*self._bg_tasks, return_exceptions=True)
        self._bg_tasks.clear()

        # Sprint F205H: Close metrics registry at teardown (flush + non-tail-loss)
        await self._close_metrics_registry()

        # Sprint F215G: Close aiohttp sessions for Tor/I2P (session leak fix)
        # F215G: These are local singletons in public_fetcher, not shared with session_runtime
        try:
            from hledac.universal.fetching import public_fetcher
            await public_fetcher._close_tor_session()
            await public_fetcher._close_i2p_session()
        except Exception:
            pass  # fail-safe: session close must not crash teardown

        # Sprint F169E: Compute dominant branch blocker summary (additive, first-non-empty wins)
        _r = self._result
        # dominant_public_blocker: backend_degraded takes priority, else error string
        match ():
            case _ if _r.public_backend_degraded:
                _r.dominant_public_blocker = "backend_degraded"
            case _ if _r.public_error and _r.public_error not in ("", "null"):
                _r.dominant_public_blocker = _r.public_error[:80]
            case _:
                pass
        # dominant_feed_blocker: first non-empty feed blocker type
        match ():
            case _ if _r.feed_inaccessible_detected:
                _r.dominant_feed_blocker = "feed_inaccessible"
            case _ if _r.feed_content_empty_detected:
                _r.dominant_feed_blocker = "feed_content_empty"
            case _ if _r.feed_no_pattern_with_content:
                _r.dominant_feed_blocker = "feed_no_pattern_with_content"
            case _ if _r.findings_build_loss_detected:
                _r.dominant_feed_blocker = "findings_build_loss"
            case _ if _r.feed_zero_yield_detected:
                _r.dominant_feed_blocker = "feed_zero_yield"
            case _:
                pass
        # dominant_branch_blocker: whichever branch had a non-empty blocker first
        match ():
            case _ if _r.dominant_public_blocker and not _r.dominant_feed_blocker:
                _r.dominant_branch_blocker = "public"
            case _ if _r.dominant_feed_blocker and not _r.dominant_public_blocker:
                _r.dominant_branch_blocker = "feed"
            case _ if _r.dominant_public_blocker and _r.dominant_feed_blocker:
                _r.dominant_branch_blocker = "both"
            case _:
                pass
        # branch_degradation_summary: descriptive tag combining all detected conditions
        _tags: list[str] = []
        if _r.public_backend_degraded:
            _tags.append("public_degraded")
        if _r.feed_inaccessible_detected:
            _tags.append("feed_inaccessible")
        if _r.feed_content_empty_detected:
            _tags.append("feed_content_empty")
        if _r.feed_no_pattern_with_content:
            _tags.append("feed_no_pattern")
        if _r.findings_build_loss_detected:
            _tags.append("findings_build_loss")
        if _r.feed_zero_yield_detected:
            _tags.append("feed_zero_yield")
        if _tags:
            _r.branch_degradation_summary = "_".join(_tags)

        # Sprint F195C: Update RL policy with sprint result (opt-in, fail-safe)
        if self._policy_manager is not None:
            try:
                self._policy_manager.update(self._result)
            except Exception as e:
                log.debug(f"[SprintPolicyManager] update() failed: {e}")

        # Sprint F199A: Adapt source weights from per-source quality feedback (fail-soft)
        try:
            self._adapt_source_weights_from_feedback()
        except Exception as e:
            log.debug(f"[F199A] _adapt_source_weights_from_feedback() failed: {e}")

        # Sprint F228A: Call policy_manager.update_with_quality_decisions if enabled and decisions exist.
        # Collect decisions from the store's batch results accumulated during the sprint.
        # Fail-soft: any exception is caught and logged; CancelledError propagates.
        # Telemetry: policy_quality_feedback_calls, _decisions, _sources, _errors.
        if self._policy_manager is not None and self._policy_manager.enabled:
            _decisions: list = []
            _feed_url = "feed"
            # Feed ingest decisions: reconstructed from _source_quality_feedback per feed_url.
            # Each decision dict has accepted (bool) and source_family (str) keys.
            try:
                # Reconstruct FindingQualityDecision-style dicts from accumulated feedback
                for _feed, _fb in self._source_quality_feedback.items():
                    _total = _fb.get("fetched", 0)
                    _accepted = _fb.get("accepted", 0)
                    if _total == 0:
                        continue
                    # One decision per feed_url: accepted is True if ratio >= 0.15 (same threshold as _adapt)
                    _ratio = _accepted / _total if _total > 0 else 0.0
                    _decisions.append({
                        "accepted": _ratio >= 0.15,
                        "source_family": _feed,
                    })
                if _decisions:
                    try:
                        self._policy_manager.update_with_quality_decisions(_decisions, feed_url=_feed_url)
                        self._result.policy_quality_feedback_calls += 1
                        self._result.policy_quality_feedback_decisions += len(_decisions)
                        self._result.policy_quality_feedback_sources += len(set(d.get("source_family") for d in _decisions))
                    except asyncio.CancelledError:
                        raise  # CancelledError must propagate — do not catch here
                    except Exception as _e:
                        self._result.policy_quality_feedback_errors += 1
                        log.debug(f"[F228A] policy_quality_feedback update failed: {_e}")
            except asyncio.CancelledError:
                raise
            except Exception as _e:
                self._result.policy_quality_feedback_errors += 1
                log.debug(f"[F228A] policy_quality_feedback decision collection failed: {_e}")

        # Sprint F208I-B: Finalize result ONCE before returning.
        # _finalize_result_truth computes terminality + calls _record_scheduler_exit.
        # Sprint F215D: Skip if early exit already finalized (break paths already called
        # _finalize_result_truth with correct exit_path before setting timing fields here).
        await self._ensure_nonfeed_predispatch_before_finalization(
            query, "run_complete"
        )
        # Sprint F215D: Capture timing fields — needed for all paths (early + normal).
        self._capture_timing_fields()
        # Sprint F215D: Only finalize if early_exit_class is not yet set.
        # Early exit paths (break statements) call _finalize_result_truth BEFORE reaching
        # here with the correct exit_path — we must NOT overwrite with "run_complete".
        if not self._result.early_exit_class:
            await self._finalize_result_truth(
                exit_path="run_complete",
                exit_reason="run() finished normally",
                exit_phase="TEARDOWN",
                query=query,
            )
        return self._result

    async def _record_scheduler_exit(
        self,
        path: str,
        reason: str,
        phase: str | None = None,
    ) -> None:
        """
        Sprint F207V-A: Record the exact exit path taken by the scheduler.

        Side-effect light — only updates in-memory telemetry fields.
        No network, no DB write, no graph write.
        """
        self._result.scheduler_exit_path = path
        self._result.scheduler_exit_reason = reason
        self._result.scheduler_exit_phase = phase
        self._result.scheduler_exit_elapsed_s = _time.monotonic() - self._run_started_at
        self._result.scheduler_exit_cycle = self._result.cycles_started
        # Capture guard state at exit — P4 fix: if return_guard_checked is False
        # but we have a valid exit_path, run the guard one final time so the
        # capture reflects reality rather than a stale False default.
        if not getattr(self._result, "return_guard_checked", False):
            # Attempting the guard for capture is fail-safe (sets True or leaves False)
            try:
                await self._ensure_mandatory_nonfeed_before_return(
                    getattr(self, "_query", "") or "",
                    None,
                    f"scheduler_exit_capture({path})",
                )
            except Exception:
                pass
        self._result.scheduler_exit_guard_checked = self._result.return_guard_checked
        self._result.scheduler_exit_guard_required = self._result.return_guard_required_lanes
        self._result.scheduler_exit_guard_satisfied = self._result.return_guard_satisfied

    def _capture_timing_fields(self) -> None:
        """
        Sprint F215D: Capture wall-clock timing fields for early-exit classification.

        Called before _finalize_result_truth in early-exit break paths so that
        _compute_early_exit_class has correct elapsed_pct (not 0.0).
        Timing is also captured at the normal-completion path (lines 1843-1859).
        """
        self._result.requested_duration_s = self._config.sprint_duration_s
        self._result.actual_duration_s = _time.monotonic() - self._wall_clock_start
        self._result.elapsed_pct = (
            self._result.actual_duration_s / self._result.requested_duration_s
            if self._result.requested_duration_s > 0
            else 0.0
        )
        self._result.active_window_budget_s = self._config.sprint_duration_s
        self._result.active_window_elapsed_s = self._result.actual_duration_s

    def _compute_early_exit_class(
        self,
        exit_path: str,
    ) -> tuple[str, str]:
        """
        Sprint F215D: Compute canonical early exit classification.

        Called in _finalize_result_truth after timing fields are populated.
        Returns (early_exit_class, early_exit_reason).

        Invariants (GHOST_INVARIANTS):
          - No network I/O, no model load, no browser launch
          - Fail-safe: returns (COMPLETED_FULL_DURATION, "") on any error
        """
        try:
            r = self._result
            if self._run_exit_path_override:
                exit_path = self._run_exit_path_override

            if r.hard_deadline_exceeded or exit_path == "hard_deadline_exceeded":
                return (
                    EarlyExitClass.ABORTED_BY_DEADLINE,
                    "hard deadline exceeded before planned duration",
                )

            if r.aborted or exit_path == "aborted_by_error":
                return (
                    EarlyExitClass.ABORTED_BY_ERROR,
                    r.abort_reason or "scheduler exception",
                )

            if hasattr(r, "peak_rss_gib") and r.peak_rss_gib > 0:
                if r.elapsed_pct < 0.5 and r.budget_violations > 0:
                    return (
                        EarlyExitClass.ABORTED_BY_MEMORY,
                        f"memory pressure ({r.budget_violations} budget violations)",
                    )

            if r.elapsed_pct >= 0.9:
                return (
                    EarlyExitClass.COMPLETED_FULL_DURATION,
                    f"ran to planned duration ({r.actual_duration_s:.1f}s / {r.requested_duration_s:.1f}s)",
                )

            nonfeed_accepted = (
                r.lane_ct_accepted_findings
                + r.lane_wayback_accepted_findings
                + r.lane_pdns_accepted_findings
                + r.lane_blockchain_accepted_findings
                + r.lane_ipfs_accepted_findings
            )
            if r.accepted_findings > 0 and nonfeed_accepted == 0:
                return (
                    EarlyExitClass.EARLY_COMPLETE_FEED_ONLY,
                    f"feed-only early exit ({r.accepted_findings} accepted findings, no nonfeed)",
                )

            if r.return_guard_satisfied and exit_path in (
                "windup_barrier_passed",
                "windup_barrier_break",
                "post_sleep_windup_break",
            ):
                return (
                    EarlyExitClass.EARLY_COMPLETE_RETURN_GUARD_SATISFIED,
                    f"return guard satisfied, early windup ({exit_path})",
                )

            if exit_path in (
                "stop_requested_break",
                "stop_on_first_accepted_break",
                "cycles_limit_reached",
                "max_cycles_break",
            ):
                return (
                    EarlyExitClass.EARLY_COMPLETE_NO_WORK_REMAINING,
                    f"no work remaining ({exit_path})",
                )

            pct = r.elapsed_pct * 100
            return (
                EarlyExitClass.COMPLETED_FULL_DURATION,
                f"early exit ({pct:.0f}% of planned duration, exit_path={exit_path})",
            )
        except Exception:
            return (EarlyExitClass.COMPLETED_FULL_DURATION, "")

    # ── Sprint F208I-B: Result finalization ──────────────────────────────────

    async def _finalize_result_truth(
        self,
        exit_path: str,
        exit_reason: str,
        exit_phase: str,
        query: str = "",
    ) -> None:
        """
        Sprint F208I-B: Finalize SprintSchedulerResult before run() returns.

        Computes terminality from acquisition strategy and records scheduler exit
        path. Called once before every return from run() — both normal completion
        and all early exit paths (stop_requested, abort, windup_barrier, etc.).

        Invariants (GHOST_INVARIANTS):
          - No network I/O
          - No model/MLX load
          - No browser launch
          - No blocking ops
          - Fail-safe: terminality errors don't prevent return
        """
        # Compute acquisition terminality first
        try:
            uma_state = "ok"
            swap_detected = False
            if self._governor is not None:
                try:
                    _snap = await self._governor.evaluate()
                    uma_state = getattr(_snap, "uma_state", "ok")
                    swap_detected = getattr(_snap, "swap_detected", False)
                except Exception:
                    pass

            _mlt_required = required_terminal_lanes(
                snapshot=self._acquisition_plan,
                query=query,
                uma_state=uma_state,
                swap_detected=swap_detected,
            )
            # Build observed outcomes from live scheduler state
            _observed_outcomes: list[dict] = []
            _seen_outcome_lanes: set[str] = set()
            if self._public_outcome is not None:
                _observed_outcomes.append(self._public_outcome)
                _seen_outcome_lanes.add("PUBLIC")
            else:
                # [F222A] PUBLIC was required but _public_outcome is None — emit explicit
                # skipped outcome so PUBLIC always appears in source_family_outcomes as
                # a terminal state. This handles the case where PUBLIC branch was skipped
                # due to remaining_time_too_low (terminal:remaining_too_low) but the
                # skipped outcome wasn't captured in _public_outcome.
                for _mlt in _mlt_required:
                    if _mlt.lane == AcquisitionLane.PUBLIC and _mlt.required:
                        _skip_reason = "public_branch_not_run"
                        if self._acquisition_plan is not None:
                            for _plan in (self._acquisition_plan.plans or ()):
                                if hasattr(_plan, "lane") and _plan.lane == AcquisitionLane.PUBLIC:
                                    _reason = getattr(_plan, "reason", "") or ""
                                    if _reason:
                                        _skip_reason = _reason
                                    break
                        _observed_outcomes.append({
                            "lane": "PUBLIC",
                            "family": "PUBLIC",
                            "attempted": False,
                            "skipped": True,
                            "terminal_state": "skipped",
                            "skip_reason": _skip_reason,
                            "raw_count": 0,
                            "built_count": 0,
                            "accepted_count": 0,
                            "error": None,
                            "timeout": False,
                            "duration_s": None,
                        })
                        _seen_outcome_lanes.add("PUBLIC")
                        break
            # [F208K-A] CT terminal if:
            #  - ct_log_discovered > 0 or lane_ct_accepted_findings > 0 (findings produced), OR
            #  - any acquisition_lane_outcomes has lane="CT" with attempted=True
            #    (CT was attempted but produced zero accepted findings — terminal=success_empty)
            # CT is MISSING only when no CT outcome with attempted=True exists at all.
            _ct_outcome = self._collect_ct_terminal_outcome()
            if _ct_outcome is not None:
                _observed_outcomes.append(_ct_outcome)
                _seen_outcome_lanes.add("CT")
            else:
                # [F221B] CT was required but not attempted — emit explicit skipped outcome
                # so CT always appears in source_family_outcomes as a terminal state.
                # This handles the case where CT was disabled (hardware_critical, swap) or
                # skipped silently; it must NOT disappear from terminal outcomes.
                for _mlt in _mlt_required:
                    if _mlt.lane == AcquisitionLane.CT and _mlt.required:
                        # Derive skip reason from acquisition plan
                        _skip_reason = "hardware_critical"
                        if self._acquisition_plan is not None:
                            for _plan in (self._acquisition_plan.plans or ()):
                                if hasattr(_plan, "lane") and _plan.lane == AcquisitionLane.CT:
                                    _reason = getattr(_plan, "reason", "") or ""
                                    if "swap" in _reason or "hardware_critical" in _reason:
                                        _skip_reason = _reason
                                    elif not getattr(_plan, "enabled", True):
                                        _skip_reason = _reason or "lane_disabled"
                                    else:
                                        _skip_reason = "ct_required_not_attempted"
                                    break
                        _observed_outcomes.append({
                            "lane": "CT",
                            "family": "CT",
                            "attempted": False,
                            "skipped": True,
                            "terminal_state": "skipped",
                            "skip_reason": _skip_reason,
                            "raw_count": 0,
                            "accepted_count": 0,
                            "error": None,
                            "timeout": False,
                        })
                        _seen_outcome_lanes.add("CT")
                        break
            # Also include lane outcomes from acquisition_lane_outcomes, skipping
            # lanes already represented via _public_outcome or CT check above
            for _o in self._result.acquisition_lane_outcomes or ():
                _lane_name: str | None = getattr(_o, "lane", None)
                if _lane_name is None and isinstance(_o, dict):
                    _lane_name = _o.get("lane")
                if _lane_name and _lane_name not in _seen_outcome_lanes:
                    _observed_outcomes.append(
                        _o.to_dict() if hasattr(_o, "to_dict") else dict(_o)
                    )
                    _seen_outcome_lanes.add(_lane_name)

            _term_report = terminality_report(
                required_lanes=_mlt_required,
                observed_outcomes=tuple(_observed_outcomes),
            )
            self._result.acquisition_terminality_checked = True
            self._result.acquisition_terminality_satisfied = (
                len(_term_report.get("missing_lanes", [])) == 0
            )
            self._result.acquisition_terminality_missing_lanes = tuple(
                _term_report.get("missing_lanes", [])
            )
            self._result.acquisition_terminality_report = _term_report
        except Exception as exc:
            # Fail-soft: terminality errors don't block return
            log.debug("[F208I-B] acquisition_terminality check failed: %s", exc)
            self._result.acquisition_terminality_checked = True
            self._result.acquisition_terminality_satisfied = False
            self._result.acquisition_terminality_missing_lanes = ()
            self._result.acquisition_terminality_report = {"error": str(exc)}

        # Sprint F215D: Compute and record early exit classification
        _exit_class, _exit_reason = self._compute_early_exit_class(exit_path)
        self._result.early_exit_class = _exit_class
        self._result.early_exit_reason = _exit_reason

        # Record scheduler exit path
        await self._record_scheduler_exit(exit_path, exit_reason, exit_phase)

        # Sprint F216C: Compute PUBLIC stage machine — must happen after all _public_outcome
        # assignments are complete (including in _run_public_discovery_in_cycle and
        # _attempt_public_prewindup_barrier and all error/timeout branches)
        _pub_stage, _pub_counters = _compute_public_stage(
            self._public_outcome, self._public_pipeline_result
        )
        self._result.public_terminal_stage = _pub_stage
        self._result.public_stage_counters = _pub_counters

        # Sprint F217B: Compute nonfeed mission telemetry
        # NonfeedMissionController.build_snapshot() reads acquisition_lane_outcomes
        # and _public_outcome to produce mission status without benchmark-owned logic.
        _acq_profile = getattr(self._config, "acquisition_profile", "default")
        if NonfeedMissionController.is_mission_profile(_acq_profile):
            try:
                _mission_snapshot = NonfeedMissionController.build_snapshot(
                    acquisition_profile=_acq_profile,
                    acquisition_lane_outcomes=self._result.acquisition_lane_outcomes or (),
                    public_outcome=self._public_outcome,
                    ct_quarantine_count=self._result.ct_quarantine_count,
                    quality_rejection_ledger=self._result.quality_rejection_ledger or (),
                )
                self._result.nonfeed_mission_active = _mission_snapshot.mission_active
                self._result.nonfeed_required_families = _mission_snapshot.required_families
                self._result.nonfeed_optional_families = _mission_snapshot.optional_families
                self._result.nonfeed_family_status = _mission_snapshot.family_status
                self._result.nonfeed_all_required_terminal = _mission_snapshot.all_required_terminal
                self._result.nonfeed_any_accepted = _mission_snapshot.any_accepted
                self._result.nonfeed_provider_failures = _mission_snapshot.provider_failures
                self._result.nonfeed_memory_skips = _mission_snapshot.memory_skips
                self._result.nonfeed_mission_exit_reason = _mission_snapshot.mission_exit_reason
                # Sprint F217E: Record ledger summary at teardown
                if hasattr(self, "_nonfeed_ledger") and self._nonfeed_ledger is not None:
                    self._result.nonfeed_candidate_ledger_summary = self._nonfeed_ledger.summary()
            except Exception as _exc:
                log.debug("[F217B] NonfeedMissionController snapshot failed: %s", _exc)
                # Fail-soft: telemetry errors don't block return

    # ── Sprint F208M-A: Nonfeed predispatch before final terminality ──────────

    async def _ensure_nonfeed_predispatch_before_finalization(
        self,
        query: str,
        reason: str,
    ) -> None:
        """
        Sprint F208M-A: Ensure nonfeed predispatch has run before final terminality.

        This helper is called before every _finalize_result_truth() to guarantee
        that bounded CT/PUBLIC predispatch has had a chance to populate
        acquisition_lane_outcomes / _lane_outcomes BEFORE terminality is computed.

        Without this, terminality computed in _finalize_result_truth sees
        acquisition_lane_outcomes empty (no CT attempted yet), marking CT as
        missing even though _maybe_dispatch_nonfeed_probe_lanes() was called.

        Runs only once per sprint — subsequent calls are no-ops.
        Records explicit telemetry so failure is never silent.

        Args:
            query: Sprint query for lane shaping.
            reason: Human-readable reason for this finalization call.

        Raises:
            CancelledError: propagated if predispatch is cancelled.
        """
        if self._result.nonfeed_predispatch_checked:
            return

        self._result.nonfeed_predispatch_checked = True
        self._result.nonfeed_predispatch_reason = reason

        try:
            # Run bounded nonfeed predispatch using the existing probe lane helper.
            # This uses the same seams as _maybe_dispatch_nonfeed_probe_lanes:
            #   - CT via _run_ct_predispatch (max 5 results, 15s timeout)
            #   - PUBLIC via _attempt_public_prewindup_barrier (max 3 results, 10s timeout)
            #   - No stealth, no browser, no MLX load
            await self._maybe_dispatch_nonfeed_probe_lanes(query, self._duckdb_store)
            self._result.nonfeed_predispatch_ran = True
            # Count outcomes accumulated in acquisition_lane_outcomes
            _count = len(self._result.acquisition_lane_outcomes or ())
            self._result.nonfeed_predispatch_outcomes_count = _count
        except asyncio.CancelledError:
            # Propagate cancellation — do not catch
            raise
        except Exception as exc:
            # Record terminal error outcome — terminality will be computed with
            # whatever state is available; fail-safe prevents crash
            log.debug("[F208M-A] Nonfeed predispatch failed before finalization: %s", exc)
            self._result.nonfeed_predispatch_ran = False
            self._result.nonfeed_predispatch_outcomes_count = 0

    # ── Sprint F208L-A: CT outcome SSOT ────────────────────────────────────────

    def _collect_ct_terminal_outcome(self) -> dict | None:
        """
        Sprint F208L-A: Collect canonical CT terminal outcome from all CT surfaces.

        This is the ONE source of truth for CT terminality in _finalize_result_truth.
        It inspects all canonical CT surfaces and returns a complete outcome dict
        with lane, family, attempted, terminal_state, raw_count, accepted_count,
        error, timeout, skipped fields.

        Returns None when CT was never attempted (not even attempted=True with zero
        raw results) — allowing terminality_report to mark CT as missing.

        Terminal state rules:
          - error not None  → terminal_state="error"
          - timeout=True    → terminal_state="timeout"
          - skipped=True    → terminal_state="skipped"
          - raw_count > 0 and accepted_count == 0 → terminal_state="success_empty"
          - raw_count == 0 and attempted=True and no error → terminal_state="empty"
          - attempted=True (default terminal) → terminal_state="success"
        """
        _ct_outcome: dict | None = None

        # Surface 1: acquisition_lane_outcomes (canonical lane runner result)
        for _o in (self._result.acquisition_lane_outcomes or ()):
            _lane_name = getattr(_o, "lane", None)
            if _lane_name is None and isinstance(_o, dict):
                _lane_name = _o.get("lane")
            if _lane_name == "CT":
                _d = _o.to_dict() if hasattr(_o, "to_dict") else dict(_o)
                _attempted = _d.get("attempted", False)
                if not _attempted:
                    continue  # skip non-attempted CT entries
                _raw = _d.get("raw_count", 0) or _d.get("ct_results_raw", 0) or 0
                _accepted = _d.get("accepted_count", 0) or _d.get("accepted_findings", 0) or 0
                _error = _d.get("error")
                _timeout = _d.get("timeout", False)
                _skipped = _d.get("skipped", False)

                # Derive terminal_state
                if _error is not None:
                    _ts = "error"
                elif _timeout:
                    _ts = "timeout"
                elif _skipped:
                    _ts = "skipped"
                elif _raw > 0 and _accepted == 0:
                    _ts = "success_empty"
                elif _raw == 0 and _attempted and _error is None:
                    _ts = "empty"
                else:
                    _ts = "success"

                _ct_outcome = {
                    "lane": "CT",
                    "family": "CT",
                    "attempted": True,
                    "terminal_state": _ts,
                    "raw_count": _raw,
                    "accepted_count": _accepted,
                    "error": _error,
                    "timeout": _timeout,
                    "skipped": _skipped,
                }
                break

        # Surface 2: _lane_outcomes (in-memory lane outcomes, may overlap with surface 1)
        if _ct_outcome is None:
            for _o in getattr(self, "_lane_outcomes", None) or ():
                _lane_name = getattr(_o, "lane", None)
                if _lane_name is None and isinstance(_o, dict):
                    _lane_name = _o.get("lane")
                if _lane_name == "CT":
                    _d = _o.to_dict() if hasattr(_o, "to_dict") else dict(_o)
                    _attempted = _d.get("attempted", False)
                    if not _attempted:
                        continue
                    _raw = _d.get("raw_count", 0) or _d.get("ct_results_raw", 0) or 0
                    _accepted = _d.get("accepted_count", 0) or _d.get("accepted_findings", 0) or 0
                    _error = _d.get("error")
                    _timeout = _d.get("timeout", False)
                    _skipped = _d.get("skipped", False)

                    if _error is not None:
                        _ts = "error"
                    elif _timeout:
                        _ts = "timeout"
                    elif _skipped:
                        _ts = "skipped"
                    elif _raw > 0 and _accepted == 0:
                        _ts = "success_empty"
                    elif _raw == 0 and _attempted and _error is None:
                        _ts = "empty"
                    else:
                        _ts = "success"

                    _ct_outcome = {
                        "lane": "CT",
                        "family": "CT",
                        "attempted": True,
                        "terminal_state": _ts,
                        "raw_count": _raw,
                        "accepted_count": _accepted,
                        "error": _error,
                        "timeout": _timeout,
                        "skipped": _skipped,
                    }
                    break

        # Surface 3: ct_log_discovered / lane_ct_accepted_findings counters
        # (set by source_finding_bridge even when lane outcome was not recorded)
        if _ct_outcome is None:
            _disc = getattr(self._result, "ct_log_discovered", 0) or 0
            _acc = getattr(self._result, "lane_ct_accepted_findings", 0) or 0
            _ct_adapter_called = getattr(self._ct_log_client, "_called", False)
            if _disc > 0 or _acc > 0 or _ct_adapter_called:
                _raw = _disc
                if _raw > 0 and _acc == 0:
                    _ts = "success_empty"
                elif _raw == 0 and _ct_adapter_called and _acc == 0:
                    _ts = "empty"
                else:
                    _ts = "success"
                _ct_outcome = {
                    "lane": "CT",
                    "family": "CT",
                    "attempted": True,
                    "terminal_state": _ts,
                    "raw_count": _raw,
                    "accepted_count": _acc,
                    "error": None,
                    "timeout": False,
                    "skipped": False,
                }

        return _ct_outcome

    # ── Sprint F210A: Source family outcomes terminality SSOT ────────────────

    def _final_source_family_outcomes_for_terminality(self) -> tuple[dict, ...]:
        """
        Sprint F210A: Canonical source family outcomes for terminality SSOT.

        This mirrors the EXACT same logic used in _build_diagnostic_report to build
        source_family_outcomes (lines ~6219-6244), ensuring terminality_report is
        ALWAYS computed from the same canonical outcomes that go into the report.

        This fixes the stale terminality bug where:
          - _finalize_result_truth() is called before all nonfeed lanes complete
          - terminality was computed from a snapshot with CT/PUBLIC not yet attempted
          - source_family_outcomes reflected final state but terminality was stale

        Returns:
            Tuple of outcome dicts for terminality computation — same format as
            observed_outcomes passed to terminality_report().
        """
        _outcomes: list[dict] = []
        _seen: set[str] = set()

        # PUBLIC: mirrors _build_diagnostic_report lines 6233-6235
        # _public_outcome is already a normalized dict (see line 3371)
        if self._public_outcome is not None:
            _outcomes.append(self._public_outcome)
            _seen.add("PUBLIC")

        # CT: use _collect_ct_terminal_outcome for canonical CT terminal outcome
        # (mirrors _finalize_result_truth lines 1762-1765)
        _ct_outcome = self._collect_ct_terminal_outcome()
        if _ct_outcome is not None:
            _outcomes.append(_ct_outcome)
            _seen.add("CT")

        # Other lanes: mirror the EXACT same family/lane loop as _build_diagnostic_report
        # lines 6222-6243 (normalize_source_family_outcome for each family)
        for _fam, _lane in [
            ("ct", AcquisitionLane.CT),
            ("wayback", AcquisitionLane.WAYBACK),
            ("passive_dns", AcquisitionLane.PASSIVE_DNS),
            ("blockchain", AcquisitionLane.BLOCKCHAIN),
            ("feed", "FEED"),
            ("public", AcquisitionLane.PUBLIC),
        ]:
            # Skip families already covered above
            if _lane == "PUBLIC":
                continue  # handled above via _public_outcome
            if _lane == AcquisitionLane.CT:
                continue  # handled above via _collect_ct_terminal_outcome

            _raw: dict | None = None
            if _lane == "FEED":
                _raw = getattr(self, "_feed_verdicts", []) or None
                # F221A: FEED accepted_count must come from result-level counts, not _feed_verdicts
                # (verdict tuples carry signal/quality, not accepted_count).
                # Net FEED = total - public - CT_log
                if _raw is not None:
                    _feed_accepted = (
                        (self._result.accepted_findings or 0)
                        - (self._result.public_accepted_findings or 0)
                        - (self._result.ct_log_accepted_findings or 0)
                    )
                    _feed_accepted = max(0, _feed_accepted)
                    if isinstance(_raw, list) and len(_raw) > 0:
                        _first = _raw[0]
                        if isinstance(_first, tuple):
                            _raw = {
                                "verdict": _first,
                                "accepted_count": _feed_accepted,
                                "raw_count": _first[1] if len(_first) > 1 else 0,
                                "attempted": True,
                            }
            elif self._lane_outcomes:
                for _o in self._lane_outcomes:
                    if hasattr(_o, "lane") and _o.lane == _lane:
                        _raw = _o
                        break

            _normalized = normalize_source_family_outcome(_fam, _raw)
            _lane_name = _normalized.get("lane") or _fam.upper()
            _normalized["lane"] = _lane_name
            if _lane_name not in _seen:
                _outcomes.append(_normalized)
                _seen.add(_lane_name)

        return tuple(_outcomes)

    # ── Sprint F207M-A: Nonfeed Pre-dispatch ────────────────────────────────

    async def _maybe_dispatch_nonfeed_probe_lanes(
        self,
        query: str,
        duckdb_store: Any,
    ) -> None:
        """
        Sprint F207M-A: Bounded nonfeed pre-dispatch checkpoint.

        Fires before the first active cycle's aggressive branch fan-out can trigger
        early windup, ensuring CT (and optionally WAYBACK/PASSIVE_DNS) are attempted
        at least once for domain queries before the sprint winds down.

        Invariants (strict):
          - No stealth, no graph writes, no unbounded network
          - max_items <= 5, timeout_s <= 15
          - Fail-soft: errors/skips are telemetry only, never crash sprint
          - CT only by default for domain queries
          - WAYBACK/PASSIVE_DNS only when memory is ok/warn

        Windup blocking:
          If domain query + CT enabled but not yet attempted, set
          windup_blocked_until_nonfeed_attempted = True so the windup gate
          delays entry until pre-dispatch completes.
        """
        import time as _time

        if self._nonfeed_predispatch_done:
            return

        if self._acquisition_plan is None:
            return

        # Check if CT is enabled in the acquisition plan
        ct_plan = get_lane_plan(self._acquisition_plan, AcquisitionLane.CT)
        if ct_plan is None or not ct_plan.enabled:
            self._nonfeed_predispatch_done = True
            return

        # Check if this is a domain query (CT pre-dispatch is only meaningful for domains)
        if not is_lane_enabled(self._acquisition_plan, AcquisitionLane.CT):
            # CT not enabled — nothing to pre-dispatch
            self._nonfeed_predispatch_done = True
            return

        # Sprint F207M-A: Check whether CT was already attempted via aggressive branch
        _ct_already_run = (
            self._result.ct_log_discovered > 0
            or self._result.lane_ct_accepted_findings > 0
            or getattr(self._ct_log_client, "_called", False)
        )

        # Windup blocking: if domain query + CT enabled + not yet run, block windup
        if _ct_already_run:
            self._nonfeed_predispatch_done = True
            return

        # Windup will be blocked until pre-dispatch runs
        self._result.windup_blocked_until_nonfeed_attempted = True
        log.debug("[F207M-A] Nonfeed pre-dispatch: blocking windup until CT attempted")

        # Determine memory state for optional lane gating
        _uma = "ok"
        if self._governor is not None:
            try:
                _snap = await self._governor.evaluate()
                _uma = getattr(_snap, "uma_state", "ok")
            except Exception:
                pass

        _memory_ok = _uma in ("ok", "warn")

        # Build a minimal CT plan with tiny bounds
        from hledac.universal.runtime.acquisition_strategy import (
            AcquisitionLanePlan,
            AcquisitionLaneOutcome,
            RiskLevel,
        )

        _t0 = _time.monotonic()
        _skipped: dict[str, str] = {}
        _attempted_lanes: list[str] = []
        _outcome: AcquisitionLaneOutcome | None = None

        async def _run_ct_predispatch() -> AcquisitionLaneOutcome:
            """CT pre-dispatch with max_items=5, timeout=15s."""
            _start = _time.monotonic()
            _candidate_findings: tuple = ()
            _rejection_reasons: tuple = ()
            _rejected_count = 0
            _sample_rejections: tuple = ()
            _ct_error: str | None = None
            _ct_results_raw = 0
            _candidates: tuple = ()  # initialized; set by ct_results_to_findings on success
            try:
                async with asyncio.timeout(15.0):
                    _ct_call = _get_ct_adapter()
                    # Build lane query for CT
                    from hledac.universal.runtime.acquisition_strategy import build_lane_query
                    _shaped = build_lane_query(query, AcquisitionLane.CT)
                    if isinstance(_shaped, dict) or not _shaped:
                        raise ValueError("empty_ct_query")
                    result, ct_outcome = await _ct_call(
                        query=_shaped,
                        max_results=5,
                        timeout_s=15.0,
                    )
                    _ct_results_raw = ct_outcome.raw_count

                    # Bridge conversion
                    candidates, rejections, _ct_telemetry = ct_results_to_findings(
                        result, ct_outcome, query, sprint_id=f"predispatch-{int(_time.time())}"
                    )
                    _candidate_findings = tuple(candidates)
                    _rejection_reasons = tuple(rejections)
                    _rejected_count = len(rejections)
                    _sample_rejections = tuple(rejections[:3])

                    accepted = 0
                    if _candidate_findings and duckdb_store is not None:
                        if hasattr(duckdb_store, "async_ingest_findings_batch"):
                            try:
                                ingest_results = await duckdb_store.async_ingest_findings_batch(
                                    list(_candidate_findings)
                                )
                                accepted = sum(
                                    1 for r in ingest_results
                                    if isinstance(r, dict) and r.get("accepted")
                                )
                            except Exception:
                                pass
                            # Sprint F216G: Record quality gate rejections from this ingest
                            self._record_quality_rejections_from_store(duckdb_store)
                            # Sprint F217E: Mirror quality rejections in ledger
                            for _rec in self._result.quality_rejection_ledger or ():
                                try:
                                    self._nonfeed_ledger.add_quality_rejection(
                                        source_family=_rec.source_family or "unknown",
                                        reason=_rec.reason or "unknown",
                                        sample_url=getattr(_rec, "url_sample", "") or "",
                                        sample_value=getattr(_rec, "finding_id", "")[:16],
                                    )
                                except Exception:
                                    pass  # fail-soft
                    if ct_outcome.error:
                        _ct_error = ct_outcome.error

                    # ── Sprint F214D: CT Bridge Loss Audit ───────────────────────────
                    # Derive ct_loss_stage and store telemetry on SprintSchedulerResult.
                    # Bridge telemetry: _ct_telemetry (dict with ct_raw_entries,
                    # ct_candidate_domains, ct_accepted_candidates, etc.)
                    _bridge_raw = _ct_telemetry.get("ct_raw_entries", 0) if isinstance(_ct_telemetry, dict) else 0
                    _bridge_candidates = len(candidates) if candidates else 0

                    # Sanitized sample keys from raw hits (capped at 3 — no raw data exposed)
                    _raw_keys: tuple[str, ...] = ()
                    if _bridge_raw > 0 and hasattr(result, "hits") and result.hits:
                        _sample_hits = list(result.hits)[:3]
                        _raw_keys = tuple(sorted(set(
                            k for hit in _sample_hits
                            for k in (getattr(hit, "url", None), getattr(hit, "ct_name_value", None))
                            if k
                        )))

                    # Derive ct_loss_stage — each branch is mutually exclusive
                    # Track whether storage was actually attempted (vs candidates built but not accumulated)
                    _storage_attempted = (
                        bool(_candidate_findings)
                        and duckdb_store is not None
                        and hasattr(duckdb_store, "async_ingest_findings_batch")
                    )
                    # F217D: Check for stale cache before provider-failure derivation
                    # F230C: Distinguish PROVIDER_FAILURE (no cache, raw=0) from
                    # STALE_CACHE_USED (cache was used as fallback, raw=0 or raw>0)
                    _ct_cache_used = getattr(ct_outcome, "ct_cache_used", False)
                    if _ct_cache_used:
                        # F230C: Cache was used as provider fallback — stale cache is the
                        # terminal outcome regardless of raw count
                        _ct_loss_stage = CTLossStage.STALE_CACHE_USED.value
                        self._result.ct_bridge_invoked = False
                    elif _ct_results_raw == 0:
                        # F217D: raw=0 and no stale cache — explicit provider failure
                        _ct_loss_stage = CTLossStage.PROVIDER_FAILURE.value
                        self._result.ct_bridge_invoked = False
                    elif (
                        _bridge_raw == 0
                        and _ct_results_raw > 0
                        and _bridge_candidates == 0
                        and REJECTION_UNSUPPORTED_SHAPE in _rejection_reasons
                    ):
                        _ct_loss_stage = CTLossStage.UNSUPPORTED_RAW_SHAPE.value
                        self._result.ct_bridge_invoked = True
                    # F215C: ALL_REJECTED_BY_BRIDGE when bridge was called, produced zero candidates,
                    # and telemetry recorded raw entries (hits were processed by bridge).
                    elif _bridge_candidates == 0 and _bridge_raw > 0:
                        _ct_loss_stage = CTLossStage.ALL_REJECTED_BY_BRIDGE.value
                        self._result.ct_bridge_invoked = True
                    elif _bridge_candidates > 0 and not _storage_attempted:
                        _ct_loss_stage = CTLossStage.CANDIDATES_BUILT_NOT_ACCUMULATED.value
                        self._result.ct_bridge_invoked = True
                    elif _bridge_candidates > 0 and accepted == 0:
                        _ct_loss_stage = CTLossStage.ACCUMULATED_NOT_STORED.value
                        self._result.ct_bridge_invoked = True
                    elif _bridge_candidates > 0 and accepted > 0:
                        # F215C: no_loss requires ALL: raw>0, candidates>0, stored>0, accepted>0
                        _ct_loss_stage = CTLossStage.NO_LOSS.value
                        self._result.ct_bridge_invoked = True
                    elif _ct_results_raw > 0 and _bridge_candidates == 0 and _bridge_raw == 0:
                        # F215C: bridge was called but returned no telemetry entries (early-return path).
                        # Hits were presented but all were rejected at the raw-entry level.
                        _ct_loss_stage = CTLossStage.UNKNOWN_LOSS.value
                        self._result.ct_bridge_invoked = True
                    else:
                        # F228E: Bridge was NOT called despite raw entries existing (e.g., _needs_ct=False).
                        # This is RAW_NOT_BRIDGED — distinct from BRIDGE_NOT_INVOKED (no raw at all).
                        _ct_loss_stage = CTLossStage.RAW_NOT_BRIDGED.value
                        self._result.ct_bridge_invoked = False

                    # Persist CT bridge loss telemetry onto the canonical result
                    self._result.ct_loss_stage = _ct_loss_stage
                    # Sprint F216A: Emit CT raw_received and CT terminal events
                    _ct_raw = _ct_results_raw
                    if _ct_raw > 0:
                        self._emit_source_family_event(
                            family="CT",
                            event="raw_received",
                            count=_ct_raw,
                        )
                    self._emit_source_family_event(
                        family="CT",
                        event="terminal",
                        reason="bridge_invoked",
                        terminal_state="success",
                    )
                    self._result.ct_raw_sample_keys = _raw_keys
                    self._result.ct_raw_sample_count = _bridge_raw
                    self._result.ct_raw_count = _ct_results_raw
                    self._result.ct_candidates_built = _bridge_candidates
                    self._result.ct_bridge_rejections_count = _rejected_count
                    self._result.ct_bridge_rejection_reasons = tuple(str(r) for r in _sample_rejections)
                    self._result.ct_candidates_accumulated = _bridge_candidates
                    self._result.ct_candidates_stored = accepted
                    self._result.ct_storage_rejected = max(0, _bridge_candidates - accepted)
                    # Sprint F226C: CT bridge acceptance diagnostics
                    if isinstance(_ct_telemetry, dict):
                        self._result.ct_candidate_count = _ct_telemetry.get("ct_bridge_candidate_count", 0)
                        self._result.ct_valid_domain_count = _ct_telemetry.get("ct_bridge_valid_domain_count", 0)
                        self._result.ct_bridge_build_success_count = _ct_telemetry.get("ct_bridge_build_success_count", 0)
                        self._result.ct_bridge_quality_rejected_count = _ct_telemetry.get("ct_bridge_quality_rejected_count", 0)
                        # F231B: CT expansion clue summary — domain expansion evidence visible even when accepted=0
                        self._result.ct_raw_domains_seen = _ct_telemetry.get("ct_raw_domains_seen", 0)
                        self._result.ct_unique_domains_seen = _ct_telemetry.get("ct_unique_domains_seen", 0)
                        self._result.ct_valid_public_domains = _ct_telemetry.get("ct_valid_public_domains", 0)
                        self._result.ct_wildcard_domains = _ct_telemetry.get("ct_wildcard_domains", 0)
                        self._result.ct_private_reserved_domains = _ct_telemetry.get("ct_private_reserved_domains", 0)
                        self._result.ct_duplicate_candidates = _ct_telemetry.get("ct_duplicate_candidates", 0)
                        self._result.ct_expansion_clues_count = _ct_telemetry.get("ct_expansion_clues_count", 0)
                        # Bounded examples: serialize up to MAX_EXPANSION_CLUE_EXAMPLES for report
                        _ct_examples = _ct_telemetry.get("ct_candidate_examples", []) or []
                        if _ct_examples:
                            import json as _json
                            _ex_samples: list[str] = []
                            for _ex in _ct_examples[:5]:
                                try:
                                    _ex_samples.append(_json.dumps(_ex, ensure_ascii=True))
                                except Exception:
                                    pass
                            self._result.ct_candidate_examples = tuple(_ex_samples)
                    # Sprint F216D: CT quarantine evidence for raw hits rejected by bridge
                    _ct_quarantine_count = _ct_telemetry.get("ct_quarantine_count", 0) if isinstance(_ct_telemetry, dict) else 0
                    _ct_quarantine_entries = _ct_telemetry.get("ct_quarantine_entries", []) if isinstance(_ct_telemetry, dict) else []
                    self._result.ct_quarantine_count = _ct_quarantine_count
                    # Bounded samples: cap at 10, store as JSON strings for report persistence
                    if _ct_quarantine_entries:
                        import json
                        _samples: list[str] = []
                        for _entry in _ct_quarantine_entries[:10]:
                            try:
                                _samples.append(json.dumps(_entry, ensure_ascii=True))
                            except Exception:
                                pass
                        self._result.ct_quarantine_samples = tuple(_samples)
                    # ── End F216D ────────────────────────────────────────────────
                    # Sprint F217E: Record CT quarantine events in ledger
                    for _entry in _ct_quarantine_entries:
                        try:
                            self._nonfeed_ledger.add_ct_quarantine(
                                domain=_entry.get("raw_value", ""),
                                reject_reason=_entry.get("reject_reason", "unknown"),
                                source_url=_entry.get("source_url", ""),
                                query=_entry.get("normalized_query", ""),
                            )
                        except Exception:
                            pass  # fail-soft: ledger must never block sprint
                    # Sprint F217D: CT provider resilience telemetry
                    self._result.ct_provider_status = str(getattr(ct_outcome, "provider_status", "") or "")
                    self._result.ct_cache_used = getattr(ct_outcome, "ct_cache_used", False)
                    self._result.ct_cache_stale = getattr(ct_outcome, "ct_cache_stale", False)
                    self._result.ct_cache_age_s = getattr(ct_outcome, "ct_cache_age_s", 0.0)
                    # ── End F217D ────────────────────────────────────────────────

                    return AcquisitionLaneOutcome(
                        lane=AcquisitionLane.CT,
                        enabled=True,
                        attempted=True,
                        accepted_findings=accepted,
                        produced_items=_ct_results_raw,
                        duration_s=_time.monotonic() - _start,
                        source_family="ct",
                        ct_query=_shaped,
                        ct_results_raw=_ct_results_raw,
                        error=_ct_error,
                        candidate_findings=_candidate_findings,
                        rejection_reasons=_rejection_reasons,
                        rejected_count=_rejected_count,
                        sample_rejections=_sample_rejections,
                    )
            except asyncio.TimeoutError:
                return AcquisitionLaneOutcome(
                    lane=AcquisitionLane.CT,
                    enabled=True,
                    attempted=True,
                    timeout=True,
                    duration_s=_time.monotonic() - _start,
                    error="predispatch_timeout",
                    source_family="ct",
                    ct_query=str(_shaped) if '_shaped' in dir() else "",
                    ct_results_raw=_ct_results_raw,
                    candidate_findings=_candidate_findings,
                    rejection_reasons=_rejection_reasons,
                    rejected_count=_rejected_count,
                    sample_rejections=_sample_rejections,
                )
            except Exception as exc:
                return AcquisitionLaneOutcome(
                    lane=AcquisitionLane.CT,
                    enabled=True,
                    attempted=True,
                    error=f"predispatch_error:{type(exc).__name__}:{exc}",
                    duration_s=_time.monotonic() - _start,
                    source_family="ct",
                    ct_query=str(_shaped) if '_shaped' in dir() else "",
                    ct_results_raw=_ct_results_raw,
                    candidate_findings=_candidate_findings,
                    rejection_reasons=_rejection_reasons,
                    rejected_count=_rejected_count,
                    sample_rejections=_sample_rejections,
                )

        # Actually run CT predispatch
        try:
            _outcome = await _run_ct_predispatch()
            _attempted_lanes.append("ct")
        except Exception as exc:
            log.debug("[F207M-A] CT pre-dispatch failed: %s", exc)
            _skipped["ct"] = f"predispatch_exception:{type(exc).__name__}"

        # Optional: WAYBACK only if memory ok and domain present
        if _memory_ok and _outcome and not _outcome.timeout:
            from hledac.universal.runtime.acquisition_strategy import build_lane_query
            _wayback_shaped = build_lane_query(query, AcquisitionLane.WAYBACK)
            if _wayback_shaped and not isinstance(_wayback_shaped, dict):
                try:
                    from hledac.universal.intelligence.wayback_diff_miner import WaybackDiffMiner
                    miner = WaybackDiffMiner()
                    try:
                        result = await miner.mine([str(_wayback_shaped)])
                    finally:
                        await miner.close()
                    candidates, rejections, wayback_telemetry = wayback_results_to_findings(
                        result, str(_wayback_shaped), query,
                        sprint_id=f"predispatch-wb-{int(_time.time())}"
                    )
                    # Sprint F231C: Populate wayback advisory fields from bridge telemetry
                    if wayback_telemetry:
                        self._result.wayback_advisory_clues_count += wayback_telemetry.get("wayback_changed_count", 0)
                        self._result.wayback_changed_url_count += wayback_telemetry.get("wayback_changed_url_count", 0)
                        self._result.wayback_added_url_count += wayback_telemetry.get("wayback_added_count", 0)
                        self._result.wayback_digest_changed_count += wayback_telemetry.get("wayback_digest_changed_count", 0)
                        self._result.wayback_unchanged_rejected += wayback_telemetry.get("wayback_unchanged_rejected", 0)
                    _attempted_lanes.append("wayback")
                except Exception as exc:
                    _skipped["wayback"] = f"{type(exc).__name__}:{exc}"
            else:
                _skipped["wayback"] = "empty_query_or_disabled"
        else:
            _skip_reason = "memory_critical" if not _memory_ok else ("ct_timeout" if _outcome and _outcome.timeout else "ct_failed")
            _skipped["wayback"] = _skip_reason

        # Optional: PASSIVE_DNS only if memory ok
        if _memory_ok:
            from hledac.universal.runtime.acquisition_strategy import build_lane_query
            _pdns_shaped = build_lane_query(query, AcquisitionLane.PASSIVE_DNS)
            if _pdns_shaped and not isinstance(_pdns_shaped, dict):
                try:
                    from hledac.universal.security.passive_dns import call_lookup_passive_dns
                    ips, pdns_outcome = await call_lookup_passive_dns(str(_pdns_shaped))
                    # Sprint F231C: Capture PassiveDNS advisory telemetry from bridge
                    pdns_findings, pdns_rejections, pdns_telemetry = passive_dns_results_to_findings(
                        ips, pdns_outcome, query, sprint_id=f"predispatch-pdns-{int(_time.time())}"
                    )
                    if pdns_telemetry:
                        self._result.passive_dns_advisory_clues_count += pdns_telemetry.get("pdns_public_accepted", 0)
                        self._result.passive_dns_private_ip_rejected += pdns_telemetry.get("pdns_private_rejected", 0)
                        self._result.passive_dns_empty_ip_rejected += pdns_telemetry.get("pdns_empty_rejected", 0)
                    _attempted_lanes.append("passive_dns")
                except Exception as exc:
                    _skipped["passive_dns"] = f"{type(exc).__name__}:{exc}"
            else:
                _skipped["passive_dns"] = "empty_query_or_disabled"
        else:
            _skipped["passive_dns"] = "memory_critical"

        _duration = _time.monotonic() - _t0

        # Record telemetry
        self._result.nonfeed_predispatch_attempted = True
        self._result.nonfeed_predispatch_lanes = tuple(_attempted_lanes)
        self._result.nonfeed_predispatch_skipped = dict(_skipped)
        self._result.nonfeed_predispatch_duration_s = _duration

        # Accumulate CT outcome into scheduler truth if we got one
        if _outcome is not None:
            _outcomes = (_outcome,)
            self._lane_outcomes = _outcomes
            self._result.acquisition_lane_outcomes = _outcomes
            self._accumulate_lane_findings(_outcomes, query)

        self._nonfeed_predispatch_done = True
        log.debug(
            "[F207M-A] Nonfeed pre-dispatch done: lanes=%s, skipped=%s, dur=%.2fs",
            _attempted_lanes, _skipped, _duration,
        )

    # ── Sprint F207Q-A: Pre-windup barrier helpers ─────────────────────────────

    async def _required_pre_windup_lanes(
        self,
        query: str,
        acquisition_plan: Any,
        memory_state: str,
    ) -> tuple[str, ...]:
        """
        Sprint F208B: Determine required lanes before windup.

        Delegates to required_terminal_lanes() from acquisition_strategy,
        which owns the canonical terminality policy (not the scheduler).

        Returns tuple of required lane names (lowercase).
        """
        if acquisition_plan is None:
            return ()

        # Derive uma_state and swap_detected from governor if available
        uma_state = memory_state
        swap_detected = False
        if self._governor is not None:
            try:
                _snap = await self._governor.evaluate()
                uma_state = getattr(_snap, "uma_state", memory_state)
                swap_detected = getattr(_snap, "swap_detected", False)
            except Exception:
                pass

        # Delegate to acquisition strategy — it owns the terminality policy
        mlt_tuples = required_terminal_lanes(
            snapshot=acquisition_plan,
            query=query,
            uma_state=uma_state,
            swap_detected=swap_detected,
        )
        return tuple(mlt.lane.lower() for mlt in mlt_tuples)

    async def _ensure_pre_windup_lane_terminal_states(
        self,
        query: str,
        acquisition_plan: Any,
        memory_state: str,
    ) -> PreWindupBarrierResult:
        """
        Sprint F207Q-A: Ensure required lanes have terminal state before windup.

        This is the hard pre-windup barrier — it attempts required cheap lanes
        (PUBLIC, CT) if they have not yet reached terminal state.

        Invariants:
          - Never calls stealth lane
          - Never directly writes DB or graph
          - Uses existing lane runner / adapter indirection
          - Bounded timeout per lane (max 15s per lane)
          - Fail-soft: adapter error becomes terminal error, not crash
          - Records all telemetry on self._result

        Args:
            query: Sprint query
            acquisition_plan: Acquisition plan from build_acquisition_plan
            memory_state: "ok" | "warn" | "critical" | "emergency"

        Returns:
            PreWindupBarrierResult describing what happened
        """
        import time as _time

        required = await self._required_pre_windup_lanes(query, acquisition_plan, memory_state)
        # F214WINDUP: ALWAYS record barrier checked=True, even when no lanes required.
        # The _get_prewindup_barrier_report() reads prewindup_barrier_checked to decide
        # whether to return the barrier dict. Without this, active300 runs show
        # fix_prewindup_barrier_not_called because required=() early-return skips the
        # self._result.prewindup_barrier_checked=True assignment at line 2791.
        self._result.prewindup_barrier_checked = True
        self._result.prewindup_barrier_required_lanes = required
        if not required:
            self._result.prewindup_barrier_satisfied = True
            self._result.prewindup_barrier_attempted_lanes = ()
            self._result.prewindup_barrier_skipped_lanes = {}
            self._result.prewindup_barrier_errors = {}
            self._result.prewindup_barrier_duration_s = 0.0
            return PreWindupBarrierResult(satisfied=True, required_lanes=())

        t0 = _time.monotonic()
        attempted: list[str] = []
        skipped: dict[str, str] = {}
        errors: dict[str, str] = {}

        # Check if lanes already have terminal state
        # PUBLIC terminal = _public_outcome is not None
        # CT terminal = ct_log_discovered > 0 or lane_ct_accepted_findings > 0
        _ct_done = (
            self._result.ct_log_discovered > 0
            or self._result.lane_ct_accepted_findings > 0
        )
        _public_done = self._public_outcome is not None

        for lane in required:
            if lane == "public" and _public_done:
                skipped["public"] = "already_terminal"
                continue
            if lane == "ct" and _ct_done:
                skipped["ct"] = "already_terminal"
                continue

            # Attempt the lane
            if lane == "public":
                outcome = await self._attempt_public_prewindup_barrier(query)
                if outcome is None:
                    skipped["public"] = "adapter_error"
                    errors["public"] = "prewindup_barrier_public_error"
                elif outcome.get("error"):
                    errors["public"] = outcome["error"]
                    attempted.append("public")
                else:
                    attempted.append("public")
            elif lane == "ct":
                outcome = await self._attempt_ct_prewindup_barrier(query)
                if outcome is None:
                    skipped["ct"] = "adapter_error"
                    errors["ct"] = "prewindup_barrier_ct_error"
                elif outcome.get("timeout"):
                    skipped["ct"] = "timeout"
                    attempted.append("ct")
                elif outcome.get("error"):
                    errors["ct"] = outcome["error"]
                    attempted.append("ct")
                else:
                    attempted.append("ct")

        duration = _time.monotonic() - t0
        satisfied = len(attempted) >= len(required) or all(
            r in skipped or r in attempted for r in required
        )

        # Record telemetry
        self._result.prewindup_barrier_checked = True
        self._result.prewindup_barrier_required_lanes = required
        self._result.prewindup_barrier_satisfied = satisfied
        self._result.prewindup_barrier_attempted_lanes = tuple(attempted)
        self._result.prewindup_barrier_skipped_lanes = skipped
        self._result.prewindup_barrier_errors = errors
        self._result.prewindup_barrier_duration_s = duration

        return PreWindupBarrierResult(
            required_lanes=required,
            satisfied=satisfied,
            attempted_lanes=tuple(attempted),
            skipped_lanes=tuple(skipped.keys()),
            error_lanes=tuple(errors.keys()),
            duration_s=duration,
        )

    async def _attempt_public_prewindup_barrier(self, query: str) -> dict | None:
        """
        Sprint F207Q-A: Attempt PUBLIC lane as part of pre-windup barrier.

        Args:
            query: Sprint query for lane query shaping.

        Returns dict with keys: attempted, error, timeout, or None on exception.
        Uses tiny bounds (max 3 results, 10s timeout).
        """
        try:
            from hledac.universal.runtime.acquisition_strategy import (
                build_lane_query,
                AcquisitionLane,
            )
            from hledac.universal.pipeline.live_public_pipeline import (
                async_run_live_public_pipeline,
            )

            shaped = build_lane_query(query, AcquisitionLane.PUBLIC)
            if isinstance(shaped, dict) or not shaped:
                return {"error": "empty_public_query"}

            # Run with tiny bounds - no store, no engine (barrier is read-only)
            try:
                async with asyncio.timeout(10.0):
                    result = await async_run_live_public_pipeline(
                        query=shaped,
                        store=None,  # barrier doesn't write to DB
                        max_results=3,
                        fetch_timeout_s=10.0,
                        fetch_concurrency=2,
                        hermes_engine=None,
                        memory_manager=None,
                        enqueue_hypothesis_pivot=None,
                    )
                return {
                    "attempted": True,
                    "accepted": getattr(result, "accepted_findings", 0),
                }
            except asyncio.TimeoutError:
                return {"attempted": True, "timeout": True}
        except Exception as exc:
            return {"attempted": False, "error": f"{type(exc).__name__}:{exc}"}

    async def _attempt_ct_prewindup_barrier(self, query: str) -> dict | None:
        """
        Sprint F207Q-A: Attempt CT lane as part of pre-windup barrier.

        Args:
            query: Sprint query for lane query shaping.

        Returns dict with keys: attempted, error, timeout, or None on exception.
        Uses tiny bounds (max 5 results, 15s timeout).
        """
        try:
            from hledac.universal.runtime.acquisition_strategy import (
                build_lane_query,
                AcquisitionLane,
            )
            from hledac.universal.runtime.source_finding_bridge import (
                ct_results_to_findings,
            )

            shaped = build_lane_query(query, AcquisitionLane.CT)
            if isinstance(shaped, dict) or not shaped:
                return {"error": "empty_ct_query"}

            _ct_call = _get_ct_adapter()
            try:
                async with asyncio.timeout(15.0):
                    ct_result, ct_outcome = await _ct_call(
                        query=shaped,
                        max_results=5,
                        timeout_s=15.0,
                    )
                return {
                    "attempted": True,
                    "raw_count": getattr(ct_outcome, "raw_count", 0),
                }
            except asyncio.TimeoutError:
                return {"attempted": True, "timeout": True}
        except Exception as exc:
            return {"attempted": False, "error": f"{type(exc).__name__}:{exc}"}

    # ── Sprint F209A: Mandatory Acquisition Prelude ───────────────────────────

    async def _run_mandatory_acquisition_prelude(
        self,
        result: SprintSchedulerResult,
        query: str,
        duckdb_store: Any,
        ct_log_client: Any,
    ) -> None:
        """
        Sprint F209A: Mandatory Acquisition Prelude.

        Runs BEFORE the main feed cycle loop to establish early terminal state for
        PUBLIC and CT lanes on domain queries.

        This fixes the active300 window issue where windup_lead_s=180s means the
        feed loop dominates before PUBLIC/CT can get a reliable execution slot.

        Rules:
          - PUBLIC and CT required for domain queries (per required_terminal_lanes)
          - Each lane gets ONE bounded attempt before cycles_started is incremented
          - PUBLIC: max 3 results, 10s timeout via live_public_pipeline
          - CT: max 5 results, 15s timeout via crtsh adapter
          - CancelledError is re-raised (prelude is mandatory for domain queries)
          - Skipped/error outcomes are recorded as terminal state
          - No stealth, no browser, no MLX load

        Integration:
          - _public_outcome dict (same shape as _attempt_public_prewindup_barrier)
          - _lane_outcomes tuple with AcquisitionLaneOutcome (same as predispatch)
          - _result.acquisition_lane_outcomes (SSOT for terminality_report)
          - After prelude, _finalize_result_truth captures initial terminality
        """
        import time as _time

        _t0 = _time.monotonic()
        self._result.acquisition_prelude_checked = True

        # Determine memory state
        _uma = "ok"
        if self._governor is not None:
            try:
                _snap = await self._governor.evaluate()
                _uma = getattr(_snap, "uma_state", "ok")
            except Exception:
                pass

        _has_domain = False
        _domain_error = ""
        try:
            from hledac.universal.runtime.acquisition_strategy import _has_domain_or_ip
            _has_domain = _has_domain_or_ip(query)
        except Exception as _exc:
            _domain_error = f"{type(_exc).__name__}:{_exc}"
            _has_domain = False

        self._result.acquisition_prelude_domain_detected = _has_domain
        self._result.acquisition_prelude_plan_present = self._acquisition_plan is not None
        self._result.acquisition_prelude_domain_detection_error = _domain_error

        # If not a domain query, skip prelude (CT not required)
        if not _has_domain:
            self._result.acquisition_prelude_ran = False
            if _domain_error:
                self._result.acquisition_prelude_reason = f"domain_detection_error:{_domain_error}"
            else:
                self._result.acquisition_prelude_reason = "non_domain_query"
            self._result.acquisition_prelude_required_lanes = ()
            self._result.acquisition_prelude_duration_s = _time.monotonic() - _t0
            return

        # Derive required lanes
        _required: tuple[str, ...] = ()
        _mlt_tuples: tuple = ()
        _plan_built = False
        _rtl = None
        if self._acquisition_plan is not None:
            try:
                from hledac.universal.runtime.acquisition_strategy import (
                    required_terminal_lanes as _rtl,
                    AcquisitionLane,
                )
                _mlt_tuples = _rtl(
                    snapshot=self._acquisition_plan,
                    query=query,
                    uma_state=_uma,
                    swap_detected=False,
                )
                _required = tuple(
                    mlt.lane.value if hasattr(mlt.lane, 'value')
                    else str(mlt.lane).upper()
                    for mlt in _mlt_tuples
                    if getattr(mlt, 'required', False)
                )
            except Exception:
                _required = ("PUBLIC", "CT")
        else:
            # Sprint F228B: no acquisition plan but domain query detected
            # Build minimal plan so required_terminal_lanes can derive correct lanes
            try:
                from hledac.universal.runtime.acquisition_strategy import (
                    build_acquisition_plan,
                    required_terminal_lanes as _rtl,
                )
                _minimal = build_acquisition_plan(
                    query=query,
                    duration_s=60.0,
                    uma_state=_uma,
                    swap_detected=False,
                    memory_budget_mb=512,
                )
                if _minimal is not None:
                    self._acquisition_plan = _minimal
                    _plan_built = True
                    _mlt_tuples = _rtl(
                        snapshot=_minimal,
                        query=query,
                        uma_state=_uma,
                        swap_detected=False,
                    )
                    _required = tuple(
                        mlt.lane.value if hasattr(mlt.lane, 'value')
                        else str(mlt.lane).upper()
                        for mlt in _mlt_tuples
                        if getattr(mlt, 'required', False)
                    )
                else:
                    _required = ("PUBLIC", "CT")
            except Exception:
                _required = ("PUBLIC", "CT")

        self._result.acquisition_prelude_plan_built_for_prelude = _plan_built
        _needs_public = "PUBLIC" in _required or "public" in [r.lower() for r in _required]
        _needs_ct = "CT" in _required or "ct" in [r.lower() for r in _required]

        self._result.acquisition_prelude_required_lanes = _required
        self._result.acquisition_prelude_reason = f"domain_query_requires_prelude"

        _attempted_lanes: list[str] = []
        _terminal_lanes: list[str] = []
        _skipped: dict[str, str] = {}
        _errors: dict[str, str] = {}
        _prelude_outcomes: list = []

        # ── PUBLIC prelude ────────────────────────────────────────────────────
        if _needs_public:
            _public_result: dict | None = None
            try:
                from hledac.universal.runtime.acquisition_strategy import (
                    build_lane_query,
                    AcquisitionLane,
                )
                from hledac.universal.pipeline.live_public_pipeline import (
                    async_run_live_public_pipeline,
                )

                _shaped = build_lane_query(query, AcquisitionLane.PUBLIC)
                if isinstance(_shaped, dict) or not _shaped:
                    _skipped["PUBLIC"] = "empty_public_query"
                else:
                    try:
                        async with asyncio.timeout(10.0):
                            _pipeline_result = await async_run_live_public_pipeline(
                                query=_shaped,
                                store=None,
                                max_results=3,
                                fetch_timeout_s=10.0,
                                fetch_concurrency=2,
                                hermes_engine=None,
                                memory_manager=None,
                                enqueue_hypothesis_pivot=None,
                            )
                        # Sprint F216C: Store PipelineRunResult for stage machine
                        self._public_pipeline_result = _pipeline_result
                        _public_result = {
                            "lane": "PUBLIC",
                            "attempted": True,
                            "skipped": False,
                            "skip_reason": None,
                            "raw_count": getattr(_pipeline_result, 'discovered', 0) or 0,
                            "built_count": getattr(_pipeline_result, 'fetched', 0) or 0,
                            "accepted_count": getattr(_pipeline_result, 'accepted_findings', 0) or 0,
                            "error": getattr(_pipeline_result, 'error', None),
                            "timeout": getattr(_pipeline_result, 'timed_out', False),
                            "duration_s": getattr(_pipeline_result, 'elapsed_s', None),
                        }
                        _attempted_lanes.append("PUBLIC")
                        _terminal_lanes.append("PUBLIC")
                    except asyncio.TimeoutError:
                        _public_result = {
                            "lane": "PUBLIC",
                            "attempted": True,
                            "skipped": False,
                            "timeout": True,
                            "error": None,
                        }
                        _attempted_lanes.append("PUBLIC")
                        _terminal_lanes.append("PUBLIC")
            except asyncio.CancelledError:
                raise  # Re-raise CancelledError
            except Exception as exc:
                _errors["PUBLIC"] = f"{type(exc).__name__}:{exc}"
                _skipped["PUBLIC"] = f"prelude_error:{type(exc).__name__}"

            # Record _public_outcome
            if _public_result is not None:
                self._public_outcome = _public_result

        # ── CT prelude ───────────────────────────────────────────────────────
        if _needs_ct:
            _ct_outcome_prelude: Any = None
            # F214D: Initialize CT prelude variables to avoid NameError in finally block
            # These are set by ct_results_to_findings on success inside the inner try
            _ct_result: Any = None
            _candidates_prelude: tuple = ()
            _rejections_prelude: tuple = ()
            try:
                from hledac.universal.runtime.acquisition_strategy import (
                    build_lane_query,
                    AcquisitionLane,
                    AcquisitionLaneOutcome,
                )
                from hledac.universal.runtime.source_finding_bridge import (
                    ct_results_to_findings,
                )

                _shaped = build_lane_query(query, AcquisitionLane.CT)
                if isinstance(_shaped, dict) or not _shaped:
                    _skipped["CT"] = "empty_ct_query"
                else:
                    _ct_call = _get_ct_adapter()
                    try:
                        async with asyncio.timeout(15.0):
                            _ct_result, _ct_outcome_obj = await _ct_call(
                                query=_shaped,
                                max_results=5,
                                timeout_s=15.0,
                            )
                        _ct_results_raw = getattr(_ct_outcome_obj, 'raw_count', 0) or 0
                        _ct_error = getattr(_ct_outcome_obj, 'error', None)

                        # Convert to CanonicalFinding candidates (not stored in DB during prelude)
                        _candidates_prelude, _rejections_prelude, _ct_telemetry_prelude = ct_results_to_findings(
                            _ct_result, _ct_outcome_obj, query, sprint_id=f"prelude-{int(_time.time())}"
                        )
                        _accepted = 0

                        _ct_outcome_prelude = AcquisitionLaneOutcome(
                            lane=AcquisitionLane.CT,
                            enabled=True,
                            attempted=True,
                            accepted_findings=_accepted,
                            produced_items=_ct_results_raw,
                            duration_s=0.0,
                            source_family="ct",
                            ct_query=str(_shaped),
                            ct_results_raw=_ct_results_raw,
                            error=_ct_error,
                            candidate_findings=tuple(_candidates_prelude),
                            rejection_reasons=tuple(_rejections_prelude),
                            rejected_count=len(_rejections_prelude),
                            sample_rejections=tuple(_rejections_prelude[:3]),
                        )
                        _attempted_lanes.append("CT")
                        # raw>0 and accepted=0 is success_empty terminal
                        if _ct_results_raw > 0 and _accepted == 0:
                            _terminal_lanes.append("CT")
                        elif _ct_results_raw == 0 and _ct_error is None:
                            _terminal_lanes.append("CT")
                    except asyncio.TimeoutError:
                        _ct_outcome_prelude = AcquisitionLaneOutcome(
                            lane=AcquisitionLane.CT,
                            enabled=True,
                            attempted=True,
                            timeout=True,
                            duration_s=15.0,
                            error="prelude_timeout",
                            source_family="ct",
                            ct_query=str(_shaped),
                            ct_results_raw=0,
                        )
                        _attempted_lanes.append("CT")
                        _terminal_lanes.append("CT")
            except asyncio.CancelledError:
                raise  # Re-raise CancelledError
            except Exception as exc:
                _errors["CT"] = f"{type(exc).__name__}:{exc}"
                _skipped["CT"] = f"prelude_error:{type(exc).__name__}"

            # Record CT outcome
            if _ct_outcome_prelude is not None:
                _prelude_outcomes.append(_ct_outcome_prelude)
                # Also update counters
                self._result.ct_log_discovered = getattr(_ct_outcome_prelude, 'ct_results_raw', 0) or 0
                # ── Sprint F214D: CT Bridge Loss Audit (prelude path) ────────────
                # Prelude does NOT accumulate into DuckDB, so accepted_findings=0 always.
                # Any candidates built by the bridge are lost if not accumulated.
                # _candidates is initialized to () above; set by ct_results_to_findings on success.
                _prelude_candidates = len(_candidates_prelude) if _candidates_prelude else 0
                if _ct_results_raw == 0:
                    _prelude_loss_stage = CTLossStage.NO_RAW.value
                    _prelude_bridge_invoked = False
                elif (
                    _prelude_candidates == 0
                    and _ct_results_raw > 0
                    and REJECTION_UNSUPPORTED_SHAPE in _rejections_prelude
                ):
                    _prelude_loss_stage = CTLossStage.UNSUPPORTED_RAW_SHAPE.value
                    _prelude_bridge_invoked = True
                # F215E: Fallback for raw>0 but bridge produced no candidates without explicit rejection.
                # Mirrors the main path fix for the same gap condition.
                elif _prelude_candidates == 0 and _ct_results_raw > 0:
                    _prelude_loss_stage = CTLossStage.ALL_REJECTED_BY_BRIDGE.value
                    _prelude_bridge_invoked = True
                else:
                    _prelude_loss_stage = CTLossStage.CANDIDATES_BUILT_NOT_ACCUMULATED.value
                    _prelude_bridge_invoked = True
                # Sample keys from prelude hits
                # _ct_result is set by the CT adapter on success; guarded by _ct_results_raw > 0
                _prelude_keys: tuple[str, ...] = ()
                if _ct_results_raw > 0 and hasattr(_ct_result, 'hits') and _ct_result.hits:
                    _prelude_keys = tuple(sorted(set(
                        k for hit in list(_ct_result.hits)[:3]
                        for k in (getattr(hit, "url", None), getattr(hit, "ct_name_value", None))
                        if k
                    )))
                self._result.ct_loss_stage = _prelude_loss_stage
                self._result.ct_bridge_invoked = _prelude_bridge_invoked
                self._result.ct_raw_sample_keys = _prelude_keys
                self._result.ct_raw_sample_count = _ct_results_raw
                self._result.ct_raw_count = _ct_results_raw
                self._result.ct_candidates_built = _prelude_candidates
                self._result.ct_bridge_rejections_count = len(_rejections_prelude) if _rejections_prelude else 0
                self._result.ct_bridge_rejection_reasons = tuple(str(r) for r in (_rejections_prelude[:3] if _rejections_prelude else []))
                self._result.ct_candidates_accumulated = 0  # prelude does not accumulate
                self._result.ct_candidates_stored = 0  # prelude does not store
                self._result.ct_storage_rejected = 0
                # Sprint F226C: CT bridge acceptance diagnostics for prelude
                if isinstance(_ct_telemetry_prelude, dict):
                    self._result.ct_candidate_count = _ct_telemetry_prelude.get("ct_bridge_candidate_count", 0)
                    self._result.ct_valid_domain_count = _ct_telemetry_prelude.get("ct_bridge_valid_domain_count", 0)
                    self._result.ct_bridge_build_success_count = _ct_telemetry_prelude.get("ct_bridge_build_success_count", 0)
                    self._result.ct_bridge_quality_rejected_count = _ct_telemetry_prelude.get("ct_bridge_quality_rejected_count", 0)
                # Sprint F216D: CT quarantine evidence for raw hits rejected by bridge
                _ct_quarantine_count = _ct_telemetry_prelude.get("ct_quarantine_count", 0) if isinstance(_ct_telemetry_prelude, dict) else 0
                _ct_quarantine_entries = _ct_telemetry_prelude.get("ct_quarantine_entries", []) if isinstance(_ct_telemetry_prelude, dict) else []
                self._result.ct_quarantine_count = _ct_quarantine_count
                # Bounded samples: cap at 10, store as JSON strings for report persistence
                if _ct_quarantine_entries:
                    import json
                    _samples: list[str] = []
                    for _entry in _ct_quarantine_entries[:10]:
                        try:
                            _samples.append(json.dumps(_entry, ensure_ascii=True))
                        except Exception:
                            pass
                    self._result.ct_quarantine_samples = tuple(_samples)
                # ── End F216D ─────────────────────────────────────────────────
                # Sprint F217E: Record CT quarantine events in ledger (prelude)
                for _entry in _ct_quarantine_entries:
                    try:
                        self._nonfeed_ledger.add_ct_quarantine(
                            domain=_entry.get("raw_value", ""),
                            reject_reason=_entry.get("reject_reason", "unknown"),
                            source_url=_entry.get("source_url", ""),
                            query=_entry.get("normalized_query", ""),
                        )
                    except Exception:
                        pass  # fail-soft: ledger must never block sprint
                # ── End F214D ─────────────────────────────────────────────────

        # Accumulate outcomes
        if _prelude_outcomes:
            self._lane_outcomes = tuple(_prelude_outcomes)
            self._result.acquisition_lane_outcomes = tuple(_prelude_outcomes)

        # Record telemetry
        self._result.acquisition_prelude_ran = True
        self._result.acquisition_prelude_terminal_lanes = tuple(_terminal_lanes)
        self._result.acquisition_prelude_skipped_lanes = dict(_skipped)
        self._result.acquisition_prelude_errors = dict(_errors)
        self._result.acquisition_prelude_duration_s = _time.monotonic() - _t0

        # Missing lanes = required but not in terminal_lanes
        _missing = [r for r in _required if r not in _terminal_lanes]
        self._result.acquisition_prelude_missing_lanes = tuple(_missing)

        log.debug(
            "[F209A] Acquisition prelude done: required=%s, terminal=%s, missing=%s, "
            "skipped=%s, errors=%s, dur=%.2fs",
            _required, _terminal_lanes, _missing, _skipped, _errors,
            self._result.acquisition_prelude_duration_s,
        )

    # ── Sprint F207T-A: Return Guard for Mandatory Nonfeed Terminal State ────
    # ---------------------------------------------------------------------------

    async def _ensure_mandatory_nonfeed_before_return(
        self,
        query: str,
        duckdb_store: Any,
        reason: str,
    ) -> bool:
        """
        Sprint F207T-A: Ensure mandatory nonfeed lanes have terminal state before
        the scheduler can return a meaningful result for a domain query.

        This is the return-path analog of the pre-windup barrier — it prevents
        the scheduler from returning ACTIVE-phase results when PUBLIC/CT have
        not yet been attempted (even if the windup guard was never reached).

        Rules:
          - domain query + ok/warn memory: both PUBLIC and CT must have terminal state
          - domain query + critical/emergency: may skip with explicit reason recorded
          - non-domain: only PUBLIC required (CT skips with no_domain)
          - Feed-only result: may return if domain query but PUBLIC+CT already terminal

        Semantics:
          - Returns True if the scheduler MAY return (all required lanes terminal)
          - Returns False if return must be DELAYED (required lanes not terminal)
          - On False: sets return_guard telemetry and continues loop if possible

        Args:
            query: Sprint query
            duckdb_store: DuckDB store (may be None)
            reason: Human-readable reason for the return check (e.g. "stop_requested",
                    "max_cycles", "stop_on_first_accepted", "post_sleep_windup")

        Returns:
            True if return is allowed, False if blocked
        """
        import time as _time

        # Record that we checked the return guard
        self._result.return_guard_checked = True

        # Determine memory state for skipping rules
        _uma = "ok"
        if self._governor is not None:
            try:
                _snap = await self._governor.evaluate()
                _uma = getattr(_snap, "uma_state", "ok")
            except Exception:
                pass

        _memory_state = _uma if _uma in ("ok", "warn", "critical", "emergency") else "ok"
        _memory_critical = _memory_state in ("critical", "emergency")

        # Check if this is a domain query
        _is_domain = False
        if self._acquisition_plan is not None:
            _debug = getattr(self._acquisition_plan, "nonfeed_plan_debug", None)
            if _debug is not None:
                _is_domain = getattr(_debug, "domain_detected", False)

        if not _is_domain:
            # Non-domain query: return allowed without blocking
            # (PUBLIC may still be checked but CT is not required)
            self._result.return_guard_satisfied = True
            self._result.return_guard_block_reason = ""
            return True

        # Domain query: check required lanes via acquisition strategy terminality contract
        # Sprint F208B: Scheduler no longer owns hardcoded PUBLIC/CT policy —
        # it delegates to required_terminal_lanes() from acquisition_strategy
        uma_state = _memory_state
        swap_detected = False
        if self._governor is not None:
            try:
                _snap = await self._governor.evaluate()
                uma_state = getattr(_snap, "uma_state", _memory_state)
                swap_detected = getattr(_snap, "swap_detected", False)
            except Exception:
                pass

        _mlt_required = required_terminal_lanes(
            snapshot=self._acquisition_plan,
            query=query,
            uma_state=uma_state,
            swap_detected=swap_detected,
        )
        _required = [mlt.lane.lower() for mlt in _mlt_required if mlt.required]

        self._result.return_guard_required_lanes = tuple(_required)

        # Check terminal state for each required lane using lane_is_terminal()
        # PUBLIC terminal: _public_outcome is not None
        # CT terminal: ct_log_discovered > 0 or lane_ct_accepted_findings > 0
        _public_done = self._public_outcome is not None
        _ct_done = (
            self._result.ct_log_discovered > 0
            or self._result.lane_ct_accepted_findings > 0
        )

        # Build observed outcomes dict for terminality_report
        _observed: dict[str, dict] = {}
        if _public_done and self._public_outcome is not None:
            _observed["PUBLIC"] = self._public_outcome
        if _ct_done:
            _observed["CT"] = {
                "attempted": True,
                "skipped": False,
                "error": None,
                "timeout": False,
                "lane": "CT",
            }

        _unsatisfied: list[str] = []
        for lane in _required:
            if lane == "public" and not _public_done:
                _unsatisfied.append("public")
            elif lane == "ct" and not _ct_done:
                _unsatisfied.append("ct")

        if not _unsatisfied:
            # All required lanes have terminal state
            self._result.return_guard_satisfied = True
            self._result.return_guard_block_reason = ""
            return True

        # Not all lanes are terminal — try to satisfy them
        _attempted: list[str] = []
        _skipped: dict[str, str] = {}
        _errors: dict[str, str] = {}

        # Try PUBLIC
        if "public" in _unsatisfied:
            try:
                outcome = await self._attempt_public_prewindup_barrier(query)
                if outcome is None:
                    _skipped["public"] = "adapter_error"
                    _errors["public"] = "return_guard_public_adapter_error"
                elif outcome.get("error"):
                    _errors["public"] = outcome["error"]
                    _attempted.append("public")
                elif outcome.get("timeout"):
                    _skipped["public"] = "timeout"
                    _attempted.append("public")
                else:
                    _attempted.append("public")
            except Exception as exc:
                _skipped["public"] = f"exception:{type(exc).__name__}"
                _errors["public"] = f"{type(exc).__name__}:{exc}"

        # Try CT
        if "ct" in _unsatisfied:
            try:
                outcome = await self._attempt_ct_prewindup_barrier(query)
                if outcome is None:
                    _skipped["ct"] = "adapter_error"
                    _errors["ct"] = "return_guard_ct_adapter_error"
                elif outcome.get("timeout"):
                    _skipped["ct"] = "timeout"
                    _attempted.append("ct")
                elif outcome.get("error"):
                    _errors["ct"] = outcome["error"]
                    _attempted.append("ct")
                else:
                    _attempted.append("ct")
                    # F228E: Pre-windup barrier got raw CT but did not call bridge.
                    # Set full telemetry so raw>0 + accepted=0 is truthfully explained.
                    # Matches prelude path field completeness for RAW_NOT_BRIDGED exit.
                    if outcome.get("raw_count", 0) > 0:
                        self._result.ct_bridge_invoked = False
                        self._result.ct_loss_stage = CTLossStage.RAW_NOT_BRIDGED.value
                        self._result.ct_raw_count = outcome.get("raw_count", 0)
                        self._result.ct_raw_sample_count = 0
                        self._result.ct_candidates_built = 0
                        self._result.ct_candidates_accumulated = 0
                        self._result.ct_candidates_stored = 0
                        self._result.ct_storage_rejected = 0
                        self._result.ct_quarantine_count = 0
                        self._result.ct_bridge_rejections_count = 0
                        self._result.ct_bridge_rejection_reasons = ()
            except Exception as exc:
                _skipped["ct"] = f"exception:{type(exc).__name__}"
                _errors["ct"] = f"{type(exc).__name__}:{exc}"

        # Re-check terminal state after attempted barrier
        _public_done = self._public_outcome is not None
        _ct_done = (
            self._result.ct_log_discovered > 0
            or self._result.lane_ct_accepted_findings > 0
        )

        _still_unsatisfied: list[str] = []
        for lane in _required:
            if lane == "public" and not _public_done:
                _still_unsatisfied.append("public")
            elif lane == "ct" and not _ct_done:
                _still_unsatisfied.append("ct")

        if _still_unsatisfied:
            # Cannot return — set block telemetry and return False
            self._result.return_guard_delayed_for_nonfeed = True
            self._result.return_guard_block_reason = (
                f"nonfeed_not_terminal:{','.join(_still_unsatisfied)}"
            )
            self._result.return_guard_attempted_lanes = tuple(_attempted)
            self._result.return_guard_skipped_lanes = dict(_skipped)
            self._result.return_guard_errors = dict(_errors)
            return False

        # All required lanes now terminal
        self._result.return_guard_satisfied = True
        self._result.return_guard_block_reason = ""
        self._result.return_guard_attempted_lanes = tuple(_attempted)
        self._result.return_guard_skipped_lanes = dict(_skipped)
        self._result.return_guard_errors = dict(_errors)
        return True

    # ── Cycle logic ────────────────────────────────────────────────────────

    # ---------------------------------------------------------------------------
    # Sprint F207R-A: Sync barrier check callable for lifecycle runner windup_guard
    # ---------------------------------------------------------------------------

    def _check_prewindup_barrier_sync(
        self,
        query: str,
        duckdb_store: Any,
    ) -> bool:
        """
        Sprint F207R-A: Synchronous pre-windup barrier check.

        Called by the windup_guard() callback from the lifecycle runner.
        Returns True if windup is allowed (barrier satisfied or not required).
        Returns False if windup must be blocked (required lanes not terminal).

        Safe sync bridge — uses run_until_complete on a new event loop when a loop
        is already running (never uses asyncio.run() with a live loop — M1 crash vector).
        Falls back to running-loop await when available.
        Fail-closed: on loop/access error, blocks windup with explicit telemetry.

        Telemetry:
          - prewindup_guard_async_bridge_used: running-loop was detected
          - prewindup_guard_async_error: exception during await
          - prewindup_guard_fail_closed: windup blocked due to async error

        Raises:
            CancelledError: propagated — do not silently swallow cancellation.
            bool: always returned — fail-soft (True) or fail-closed (False).
        """
        # F223-D: Safe sync bridge — no asyncio.run() when loop is running.
        # asyncio.run() in a thread with live loop = M1 crash vector.
        # asyncio.run() with exception before await = RuntimeWarning: never awaited.
        _loop = None
        try:
            _loop = asyncio.get_running_loop()
        except RuntimeError:
            pass  # No running loop — asyncio.run() is safe here

        if _loop is not None:
            # Running loop detected — cannot use asyncio.run() (M1 crash vector).
            # Use run_until_complete on a new loop created for this thread context.
            self._result.prewindup_guard_async_bridge_used = True
            try:
                # run_until_complete on a fresh loop is safe for sync callback
                # and avoids M1 crash vector of asyncio.run() inside running loop
                _bg_loop = asyncio.new_event_loop()
                try:
                    result = _bg_loop.run_until_complete(
                        asyncio.wait_for(
                            self._ensure_pre_windup_lane_terminal_states(
                                query, self._acquisition_plan, "ok"
                            ),
                            timeout=30.0,
                        ),
                    )
                finally:
                    _bg_loop.close()
            except asyncio.CancelledError:
                # F223-D: CancelledError is re-raised — propagate, don't silently allow
                raise
            except Exception as exc:
                self._result.prewindup_guard_async_error = str(exc)
                self._result.prewindup_guard_fail_closed = True
                log.warning(
                    "[F223-D] prewindup barrier async bridge error (blocking windup): %s",
                    exc,
                )
                return False
        else:
            # No running loop — asyncio.run() is safe (creates its own)
            try:
                result = asyncio.run(
                    self._ensure_pre_windup_lane_terminal_states(
                        query, self._acquisition_plan, "ok"
                    ),
                )
            except Exception as exc:
                # F223-D: Catch CancelledError and other exceptions — fail-closed
                self._result.prewindup_guard_async_error = str(exc)
                if isinstance(exc, asyncio.CancelledError):
                    # Re-raise CancelledError — propagate cancellation, don't silently allow
                    raise
                # On other exceptions, fail-closed: block windup with explicit reason
                self._result.prewindup_guard_fail_closed = True
                log.warning(
                    "[F223-D] prewindup barrier error (blocking windup): %s",
                    exc,
                )
                return False

        if result is None:
            return True  # fail-soft: allow windup
        satisfied = getattr(result, "satisfied", False)
        required_lanes = getattr(result, "required_lanes", ())
        if required_lanes and not satisfied:
            self._result.windup_delayed_for_nonfeed = True
            log.debug(
                "[F207R-A] Windup blocked by barrier: required lanes not terminal %s",
                required_lanes,
            )
            return False
        return True

    async def _run_one_cycle(
        self,
        lifecycle,
        sources: Sequence[str],
        now_monotonic: Optional[float] = None,
        query: str = "",
        duckdb_store: Any = None,
    ) -> bool:
        """
        Run one bounded fetch cycle across all sources, tier-ordered.
        In aggressive mode, feed/public/CT branches run concurrently with per-branch timeouts.
        Returns False when lifecycle says stop; True otherwise.
        """
        # Build tiered work list
        work_items = self._build_work_items(sources)

        # Sprint F160C: Apply source economics re-sorting within the current cycle
        # Uses signal_stage, feed_confidence_score, winning_source_breakdown from prior cycles
        # In-cooldown and silent_streak>=4 sources are pushed to end of their tier band
        current_cycle = self._result.cycles_started
        work_items = self._sort_work_items_by_economics(work_items, current_cycle)

        # Filter: skip lower tiers if lifecycle is pruning
        mode = lifecycle.recommended_tool_mode(now_monotonic)
        match mode:
            case "prune":
                work_items = self._prune_work_items(work_items)
            case "panic":
                work_items = [w for w in work_items if w.tier == SourceTier.SURFACE]

        if not work_items:
            return True  # nothing to do this cycle

        if self._config.aggressive_mode:
            return await self._run_one_cycle_aggressive(
                lifecycle, work_items, query, duckdb_store
            )
        else:
            return await self._run_one_cycle_stable(
                lifecycle, work_items, query, duckdb_store
            )

    async def _run_one_cycle_stable(
        self,
        lifecycle,
        work_items: list,
        query: str,
        duckdb_store: Any,
    ) -> bool:
        """
        Stable mode: feed sources run first, then public discovery runs after.
        CT discovery runs once after the main cycle loop (in __main__.py).

        F212-B: Public discovery runs under remaining-time-aware asyncio.timeout.
        Branch is skipped if remaining time is at or below the safety floor.
        """
        async_run_live_feed, FeedPipelineRunResult = _import_live_feed_pipeline()

        remaining_s = lifecycle.remaining_time()

        # F216E: Determine nonfeed terminality before feed cycle
        # If nonfeed is terminal, feed budget does not apply
        _nonfeed_terminal = bool(
            self._result.lane_ct_accepted_findings > 0
            or self._result.lane_wayback_accepted_findings > 0
            or self._result.lane_pdns_accepted_findings > 0
            or self._result.lane_blockchain_accepted_findings > 0
            or self._result.lane_ipfs_accepted_findings > 0
        )

        # Run sources under TaskGroup (bounded concurrency)
        semaphore = asyncio.Semaphore(self._config.max_parallel_sources)

        async def fetch_one(work) -> tuple[str, FeedPipelineRunResult]:
            async with semaphore:
                # F216E: Feed dominance budget check before calling async_run_live_feed
                should_fetch, budget_reason = self._feed_dominance_should_fetch(work, _nonfeed_terminal)
                if not should_fetch:
                    log.debug(
                        "[F216E] Feed source skipped by budget cap: url=%s reason=%s",
                        work.feed_url, budget_reason,
                    )
                    return work.feed_url, FeedPipelineRunResult(
                        feed_url=work.feed_url,
                        fetched_entries=0,
                        accepted_findings=0,
                        stored_findings=0,
                        patterns_configured=0,
                        matched_patterns=0,
                        pages=(),
                        error="feed_budget_cap_suppressed",
                    )
                try:
                    result = await asyncio.wait_for(
                        async_run_live_feed(
                            feed_url=work.feed_url,
                            max_entries=work.max_entries,
                        ),
                        timeout=30.0,
                    )
                    return work.feed_url, result
                except asyncio.TimeoutError:
                    return work.feed_url, FeedPipelineRunResult(
                        feed_url=work.feed_url,
                        fetched_entries=0,
                        accepted_findings=0,
                        stored_findings=0,
                        patterns_configured=0,
                        matched_patterns=0,
                        pages=(),
                        error="timeout",
                    )
                except Exception as exc:
                    return work.feed_url, FeedPipelineRunResult(
                        feed_url=work.feed_url,
                        fetched_entries=0,
                        accepted_findings=0,
                        stored_findings=0,
                        patterns_configured=0,
                        matched_patterns=0,
                        pages=(),
                        error=f"exception:{type(exc).__name__}:{exc}",
                    )

        # Execute all source fetches concurrently
        tasks = [fetch_one(w) for w in work_items]
        results: list[tuple[str, FeedPipelineRunResult]] = await asyncio.gather(*tasks, return_exceptions=True)

        # ── Sprint F212A: Check deadline after feed gather ───────────────────
        # F212-A: After gather, check if deadline exceeded before processing results
        if not self._check_hard_deadline():
            log.debug("[F212A] Deadline exceeded after feed gather in stable cycle")
            return True  # cycle_ok=True, loop will exit via deadline check at next iteration

        # Process results
        for feed_url, result in results:
            self._process_result(feed_url, result)

        # F205C: Feed findings are scoped inside async_run_live_feed_pipeline and not
        # exposed to the scheduler for sidecar dispatch. Log observable diagnostic.
        _accepted = sum(getattr(r, "accepted_findings", 0) or 0 for _, r in results)
        if _accepted:
            log.debug(
                "[F205C] Feed accepted findings not in scheduler scope for sidecar dispatch "
                "(pipeline-internal storage, store=None). accepted=%d", _accepted
            )

        # Sprint 8XE: Run public discovery pipeline in same cycle (canonical parity)
        # F212-B: Wrap with remaining-time-aware asyncio.timeout
        public_timeout = self._branch_timeout_s("PUBLIC", remaining_s)
        if public_timeout <= 0:
            log.debug(
                "[F212-B] PUBLIC branch skipped in stable: remaining=%.1fs <= floor=%.1fs",
                remaining_s, self._config._MIN_BRANCH_REMAINING_S,
            )
            self._result.public_error = "terminal:remaining_too_low"
            # F214-E: Always emit PUBLIC outcome so Lane Execution Truth never loses PUBLIC
            self._public_outcome = {
                "lane": "PUBLIC",
                "attempted": True,
                "skipped": True,
                "skip_reason": "terminal:remaining_too_low",
                "raw_count": 0,
                "built_count": 0,
                "accepted_count": 0,
                "error": "terminal:remaining_too_low",
                "timeout": False,
                "duration_s": None,
            }
        else:
            try:
                async with asyncio.timeout(public_timeout):
                    await self._run_public_discovery_in_cycle(
                        query=query,
                        duckdb_store=duckdb_store,
                        hermes_engine=self._hermes_engine,
                        memory_manager=self._memory_manager,
                    )
            except asyncio.TimeoutError:
                log.debug("[stable] PUBLIC branch timed out after %ss", public_timeout)
                self._result.public_branch_timed_out = True
                self._result.public_error = "terminal:timeout"
                # Sprint F216A: Emit PUBLIC timeout event
                self._emit_source_family_event(
                    family="PUBLIC",
                    event="timeout",
                    reason="terminal:timeout",
                    terminal_state="terminal",
                )
                # F214-E: Always emit PUBLIC outcome so Lane Execution Truth never loses PUBLIC
                self._public_outcome = {
                    "lane": "PUBLIC",
                    "attempted": True,
                    "skipped": False,
                    "skip_reason": None,
                    "raw_count": self._result.public_discovered,
                    "built_count": 0,
                    "accepted_count": self._result.public_accepted_findings,
                    "error": "terminal:timeout",
                    "timeout": True,
                    "duration_s": public_timeout,
                }
            except asyncio.CancelledError:
                log.debug("[stable] PUBLIC branch cancelled")
                raise  # [I6] propagate CancelledError

        # Sprint F207A: Run enabled multi-source acquisition lanes (CT/WAYBACK/PASSIVE_DNS/BLOCKCHAIN)
        # STEALTH excluded — never auto-runs; FEED and PUBLIC already handled above
        # F212-B: Skip if remaining time is below safety floor
        lanes_timeout = self._branch_timeout_s("ADVISORY", remaining_s)
        _uma = getattr(self._governor, "_uma_state", "ok") if self._governor else "ok"
        if lanes_timeout > 0:
            try:
                async with asyncio.timeout(lanes_timeout):
                    _outcomes = await run_enabled_acquisition_lanes(
                        snapshot=self._acquisition_plan,
                        query=query,
                        store=duckdb_store,
                        uma_state=_uma,
                    )
                    if _outcomes:
                        self._lane_outcomes = _outcomes
                        self._result.acquisition_lane_outcomes = _outcomes
                        self._accumulate_lane_findings(_outcomes, query)
                        # Sprint R1B: Ingest CT lane CanonicalFinding candidates via DuckDBShadowStore
                        await self._ingest_ct_lane_candidates(_outcomes, duckdb_store)
                        # Sprint F216G: Read quality rejection ledger from duckdb_store
                        self._record_quality_rejections_from_store(duckdb_store)
                        # Sprint R8: Run CT→PassiveDNS active-cycle pivot after lane ingestion
                        ct_findings: list = []
                        for _oc in _outcomes:
                            if getattr(_oc, "source_family", None) == "ct":
                                _cands = getattr(_oc, "candidate_findings", ()) or ()
                                if _cands:
                                    ct_findings.extend(_cands)
                        if ct_findings:
                            await self._run_ct_to_passivedns_active_pivot(
                                ct_findings=ct_findings,
                                duckdb_store=duckdb_store,
                                remaining_s=remaining_s,
                            )
                        # Sprint F217E: Mirror quality rejections in ledger (stable advisory)
                        for _rec in self._result.quality_rejection_ledger or ():
                            try:
                                self._nonfeed_ledger.add_quality_rejection(
                                    source_family=_rec.source_family or "unknown",
                                    reason=_rec.reason or "unknown",
                                    sample_url=getattr(_rec, "url_sample", "") or "",
                                    sample_value=getattr(_rec, "finding_id", "")[:16],
                                )
                            except Exception:
                                pass  # fail-soft
            except asyncio.TimeoutError:
                log.debug("[stable] ADVISORY lanes timed out after %ss", lanes_timeout)
            except asyncio.CancelledError:
                raise  # [I6] propagate CancelledError
            except Exception:
                pass  # fail-soft: lane runner must never crash sprint
        else:
            log.debug("[F212-B] ADVISORY lanes skipped: remaining=%.1fs", remaining_s)

        return True

    # ── F212-B: Branch Timeout Envelope ───────────────────────────────────

    def _branch_timeout_s(self, branch_name: str, remaining_s: float) -> float:
        """
        F212-B: Compute remaining-time-aware timeout for a named branch.

        Formula: min(config_timeout, remaining_s * 0.5, MAX_BRANCH_TIMEOUT_CAP)

        - Prevents a branch from consuming more than 50% of remaining cycle time
        - Capped at MAX_BRANCH_TIMEOUT_CAP to bound absolute worst case
        - Returns 0 when remaining_s <= MIN_BRANCH_REMAINING_S (safety floor)
        """
        if remaining_s <= self._config._MIN_BRANCH_REMAINING_S:
            return 0.0
        base = (
            self._config.branch_timeout_budget_s
            if self._config.branch_timeout_budget_s > 0
            else self._config.aggressive_branch_timeout_s
        )
        bounded = min(base, remaining_s * 0.5, self._config._MAX_BRANCH_TIMEOUT_CAP)
        log.debug(
            "[F212-B] %s branch timeout: base=%.1fs remaining=%.1fs capped=%.1fs",
            branch_name, base, remaining_s, bounded,
        )
        return bounded

    # ── Aggressive Cycle ──────────────────────────────────────────────────

    async def _run_one_cycle_aggressive(
        self,
        lifecycle,
        work_items: list,
        query: str,
        duckdb_store: Any,
    ) -> bool:
        """
        Aggressive mode: feed, public discovery, and CT branches fire concurrently.
        Each branch has its own timeout budget; slow branches are cancelled without
        affecting other branches.

        F212-B: All branch timeouts are remaining-time-aware and capped at
        min(config_timeout, remaining * 0.5, MAX_CAP). Branches are skipped with
        terminal outcome when remaining time is below the safety floor.
        """
        import asyncio as _asyncio

        remaining_s = lifecycle.remaining_time()

        # F212-B: Safety floor — skip entire aggressive cycle if time too low
        if remaining_s <= self._config._MIN_BRANCH_REMAINING_S:
            log.debug(
                "[F212-B] Aggressive cycle skipped: remaining=%.1fs <= floor=%.1fs",
                remaining_s, self._config._MIN_BRANCH_REMAINING_S,
            )
            self._result.public_branch_timed_out = True
            self._result.ct_branch_timed_out = True
            self._result.branch_timeout_count += 2
            self._result.public_error = "terminal:remaining_too_low"
            self._result.ct_log_error = "terminal:remaining_too_low"
            # Sprint F216A: Emit PUBLIC and CT timeout events
            self._emit_source_family_event(
                family="PUBLIC",
                event="timeout",
                reason="terminal:remaining_too_low",
                terminal_state="terminal",
            )
            self._emit_source_family_event(
                family="CT",
                event="timeout",
                reason="terminal:remaining_too_low",
                terminal_state="terminal",
            )
            return True

        # F216E: nonfeed terminality for aggressive mode budget gate
        _nonfeed_terminal = bool(
            self._result.lane_ct_accepted_findings > 0
            or self._result.lane_wayback_accepted_findings > 0
            or self._result.lane_pdns_accepted_findings > 0
            or self._result.lane_blockchain_accepted_findings > 0
            or self._result.lane_ipfs_accepted_findings > 0
        )

        async def _run_feed_branch() -> None:
            """Feed branch: fetches all sources concurrently."""
            async_run_live_feed, FeedPipelineRunResult = _import_live_feed_pipeline()
            branch_concurrency = 4
            if self._governor is not None:
                try:
                    decision = await self._governor.evaluate()
                    branch_concurrency = decision.branch_concurrency
                except Exception:
                    pass
            semaphore = _asyncio.Semaphore(min(branch_concurrency, self._config.max_parallel_sources))

            async def fetch_one(work) -> tuple[str, FeedPipelineRunResult]:
                # F216E: budget gate before fetching
                should_fetch, budget_reason = self._feed_dominance_should_fetch(work, _nonfeed_terminal)
                if not should_fetch:
                    return work.feed_url, FeedPipelineRunResult(
                        feed_url=work.feed_url,
                        fetched_entries=0,
                        accepted_findings=0,
                        stored_findings=0,
                        patterns_configured=0,
                        matched_patterns=0,
                        pages=(),
                        error="feed_budget_cap_suppressed",
                    )
                async with semaphore:
                    try:
                        result = await _asyncio.wait_for(
                            async_run_live_feed(
                                feed_url=work.feed_url,
                                max_entries=work.max_entries,
                            ),
                            timeout=30.0,
                        )
                        return work.feed_url, result
                    except _asyncio.TimeoutError:
                        return work.feed_url, FeedPipelineRunResult(
                            feed_url=work.feed_url,
                            fetched_entries=0,
                            accepted_findings=0,
                            stored_findings=0,
                            patterns_configured=0,
                            matched_patterns=0,
                            pages=(),
                            error="timeout",
                        )
                    except Exception as exc:
                        return work.feed_url, FeedPipelineRunResult(
                            feed_url=work.feed_url,
                            fetched_entries=0,
                            accepted_findings=0,
                            stored_findings=0,
                            patterns_configured=0,
                            matched_patterns=0,
                            pages=(),
                            error=f"exception:{type(exc).__name__}:{exc}",
                        )

            tasks = [fetch_one(w) for w in work_items]
            results: list[tuple[str, FeedPipelineRunResult]] = await _asyncio.gather(*tasks, return_exceptions=True)
            for feed_url, result in results:
                self._process_result(feed_url, result)
            # F205C: Feed findings accepted but not in scheduler scope for sidecar dispatch.
            _accepted = sum(getattr(r, "accepted_findings", 0) or 0 for _, r in results)
            if _accepted:
                _log = logging.getLogger(__name__)
                _log.debug(
                    "[F205C] Aggressive feed accepted findings not in scope for sidecar dispatch. accepted=%d",
                    _accepted,
                )

        async def _run_public_branch() -> None:
            """Public discovery branch with remaining-time-aware asyncio.timeout."""
            branch_timeout = self._branch_timeout_s("PUBLIC", remaining_s)
            if branch_timeout <= 0:
                log.debug("[F212-B] PUBLIC branch skipped: remaining=%.1fs", remaining_s)
                self._result.public_error = "terminal:remaining_too_low"
                # F214-E: Always emit PUBLIC outcome so Lane Execution Truth never loses PUBLIC
                self._public_outcome = {
                    "lane": "PUBLIC",
                    "attempted": True,
                    "skipped": True,
                    "skip_reason": "terminal:remaining_too_low",
                    "raw_count": 0,
                    "built_count": 0,
                    "accepted_count": 0,
                    "error": "terminal:remaining_too_low",
                    "timeout": False,
                    "duration_s": None,
                }
                return
            try:
                async with _asyncio.timeout(branch_timeout):
                    # Sprint F217C: Enable deterministic bootstrap for nonfeed_diagnostic profile
                    # Bootstrap provides fallback when search discovery fails or returns zero.
                    _bootstrap_enabled = (
                        getattr(self._config, "acquisition_profile", "") == "nonfeed_diagnostic"
                    )
                    # Sprint F221C: Store bootstrap state before timeout context so exception handler
                    # can access it to derive precise terminal stage (BOOTSTRAP_ATTEMPTED_TIMEOUT vs generic timeout).
                    self._public_bootstrap_enabled_at_timeout = _bootstrap_enabled
                    await self._run_public_discovery_in_cycle(
                        query=query,
                        duckdb_store=duckdb_store,
                        hermes_engine=self._hermes_engine,
                        memory_manager=self._memory_manager,
                        public_bootstrap_enabled=_bootstrap_enabled,
                    )
            except _asyncio.TimeoutError:
                log.debug("[aggressive] Public branch timed out after %ss", branch_timeout)
                self._result.public_branch_timed_out = True
                # Sprint F221C: Derive precise terminal stage from bootstrap state
                # _public_outcome may already be partially set by the pipeline running up to the timeout point.
                # Capture bootstrap state at timeout for precise stage classification.
                _existing_outcome = getattr(self, '_public_outcome', None) or {}
                _existing_raw = _existing_outcome.get("raw_count", 0) or 0
                _existing_accepted = _existing_outcome.get("accepted_count", 0) or 0
                _bootstrap_was_enabled = getattr(self, '_public_bootstrap_enabled_at_timeout', _bootstrap_enabled)
                # Derive precise stage for the error field (used by delta/sanity tools)
                if _bootstrap_was_enabled and _existing_raw > 0:
                    _precise_stage = _PublicStage.BOOTSTRAP_ATTEMPTED_TIMEOUT
                elif _bootstrap_was_enabled and _existing_raw == 0:
                    _precise_stage = _PublicStage.BOOTSTRAP_ZERO_CANDIDATES_TIMEOUT
                else:
                    _precise_stage = "terminal:timeout"
                self._result.public_error = _precise_stage
                # Sprint F216A: Emit PUBLIC timeout event
                self._emit_source_family_event(
                    family="PUBLIC",
                    event="timeout",
                    reason=_precise_stage,
                    terminal_state="terminal",
                )
                # F214-E: Always emit PUBLIC outcome so Lane Execution Truth never loses PUBLIC
                self._public_outcome = {
                    "lane": "PUBLIC",
                    "attempted": True,
                    "skipped": False,
                    "skip_reason": None,
                    "raw_count": _existing_raw,
                    "built_count": 0,
                    "accepted_count": _existing_accepted,
                    "error": _precise_stage,
                    "timeout": True,
                    "duration_s": branch_timeout,
                }
            except _asyncio.CancelledError:
                log.debug("[aggressive] Public branch cancelled")
                raise  # [I6] propagate CancelledError
            except Exception as exc:
                log.debug("[aggressive] Public branch error: %s", exc)
                self._result.public_error = f"{type(exc).__name__}:{exc}"
                # F214-E: Always emit PUBLIC outcome so Lane Execution Truth never loses PUBLIC
                self._public_outcome = {
                    "lane": "PUBLIC",
                    "attempted": True,
                    "skipped": False,
                    "skip_reason": None,
                    "raw_count": self._result.public_discovered,
                    "built_count": 0,
                    "accepted_count": self._result.public_accepted_findings,
                    "error": f"{type(exc).__name__}:{exc}",
                    "timeout": False,
                    "duration_s": branch_timeout,
                }

        async def _run_ct_branch() -> None:
            """CT log discovery branch with remaining-time-aware asyncio.timeout."""
            if self._ct_log_client is None or duckdb_store is None:
                return
            branch_timeout = self._branch_timeout_s("CT", remaining_s)
            if branch_timeout <= 0:
                log.debug("[F212-B] CT branch skipped: remaining=%.1fs", remaining_s)
                self._result.ct_log_error = "terminal:remaining_too_low"
                return
            try:
                async with _asyncio.timeout(branch_timeout):
                    await self._run_ct_log_discovery_in_cycle(query=query, store=duckdb_store)
            except _asyncio.TimeoutError:
                log.debug("[aggressive] CT branch timed out after %ss", branch_timeout)
                self._result.ct_branch_timed_out = True
                self._result.ct_log_error = "terminal:timeout"
            except _asyncio.CancelledError:
                log.debug("[aggressive] CT branch cancelled")
                raise  # [I6] propagate CancelledError
            except Exception as exc:
                log.debug("[aggressive] CT branch error: %s", exc)
                self._result.ct_log_error = f"{type(exc).__name__}:{exc}"

        # Launch all branches concurrently
        feed_branch = _asyncio.create_task(_run_feed_branch(), name="sprint:feed_branch")
        public_branch = _asyncio.create_task(_run_public_branch(), name="sprint:public_branch")
        ct_branch = _asyncio.create_task(_run_ct_branch(), name="sprint:ct_branch")

        # F212-B: Wait for all branches with remaining-time-aware envelope timeout.
        # Uses asyncio.timeout so cancellation of one branch propagates correctly.
        outer_timeout = self._branch_timeout_s("cycle_envelope", remaining_s)
        results = []
        if outer_timeout > 0:
            try:
                async with _asyncio.timeout(outer_timeout):
                    results = await _asyncio.gather(
                        feed_branch, public_branch, ct_branch,
                        return_exceptions=True,
                    )
            except _asyncio.TimeoutError:
                log.debug("[aggressive] Branch(es) did not complete within %ss", outer_timeout)
                self._result.public_branch_timed_out = True
                self._result.ct_branch_timed_out = True
                self._result.branch_timeout_count += 2
                self._result.public_error = "terminal:envelope_timeout"
                self._result.ct_log_error = "terminal:envelope_timeout"
                # Sprint F216A: Emit PUBLIC and CT timeout events
                self._emit_source_family_event(
                    family="PUBLIC",
                    event="timeout",
                    reason="terminal:envelope_timeout",
                    terminal_state="terminal",
                )
                self._emit_source_family_event(
                    family="CT",
                    event="timeout",
                    reason="terminal:envelope_timeout",
                    terminal_state="terminal",
                )
                results = []
                # F214-E: Always emit PUBLIC outcome so Lane Execution Truth never loses PUBLIC
                self._public_outcome = {
                    "lane": "PUBLIC",
                    "attempted": True,
                    "skipped": False,
                    "skip_reason": None,
                    "raw_count": self._result.public_discovered,
                    "built_count": 0,
                    "accepted_count": self._result.public_accepted_findings,
                    "error": "terminal:envelope_timeout",
                    "timeout": True,
                    "duration_s": outer_timeout,
                }
            except _asyncio.CancelledError:
                log.debug("[aggressive] Aggressive cycle cancelled")
                raise  # [I6] propagate CancelledError
        else:
            log.debug(
                "[F212-B] Aggressive gather skipped: outer_timeout=%.1fs (remaining=%.1fs)",
                outer_timeout, remaining_s,
            )
            # Record skipped terminals without double-counting
            self._result.public_branch_timed_out = True
            self._result.ct_branch_timed_out = True
            self._result.branch_timeout_count += 2
            self._result.public_error = "terminal:remaining_too_low"
            self._result.ct_log_error = "terminal:remaining_too_low"
            # Sprint F216A: Emit PUBLIC and CT timeout events
            self._emit_source_family_event(
                family="PUBLIC",
                event="timeout",
                reason="terminal:remaining_too_low",
                terminal_state="terminal",
            )
            self._emit_source_family_event(
                family="CT",
                event="timeout",
                reason="terminal:remaining_too_low",
                terminal_state="terminal",
            )
            # F214-E: Always emit PUBLIC outcome so Lane Execution Truth never loses PUBLIC
            self._public_outcome = {
                "lane": "PUBLIC",
                "attempted": True,
                "skipped": True,
                "skip_reason": "terminal:remaining_too_low",
                "raw_count": 0,
                "built_count": 0,
                "accepted_count": 0,
                "error": "terminal:remaining_too_low",
                "timeout": False,
                "duration_s": None,
            }

        # Sprint F207A: Run enabled multi-source acquisition lanes (CT/WAYBACK/PASSIVE_DNS/BLOCKCHAIN)
        # STEALTH excluded — never auto-runs; FEED, PUBLIC, CT already handled as branches above
        # F212-B: Skip if remaining time is below safety floor
        lanes_timeout = self._branch_timeout_s("ADVISORY", remaining_s)
        _uma = getattr(self._governor, "_uma_state", "ok") if self._governor else "ok"
        if lanes_timeout > 0:
            try:
                async with _asyncio.timeout(lanes_timeout):
                    _outcomes = await run_enabled_acquisition_lanes(
                        snapshot=self._acquisition_plan,
                        query=query,
                        store=duckdb_store,
                        uma_state=_uma,
                    )
                    if _outcomes:
                        self._lane_outcomes = _outcomes
                        self._result.acquisition_lane_outcomes = _outcomes
                        self._accumulate_lane_findings(_outcomes, query)
                        # Sprint R1B: Ingest CT lane CanonicalFinding candidates via DuckDBShadowStore
                        await self._ingest_ct_lane_candidates(_outcomes, duckdb_store)
                        # Sprint F216G: Read quality rejection ledger from duckdb_store
                        self._record_quality_rejections_from_store(duckdb_store)
                        # Sprint R8: Run CT→PassiveDNS active-cycle pivot after lane ingestion (aggressive)
                        ct_findings: list = []
                        for _oc in _outcomes:
                            if getattr(_oc, "source_family", None) == "ct":
                                _cands = getattr(_oc, "candidate_findings", ()) or ()
                                if _cands:
                                    ct_findings.extend(_cands)
                        if ct_findings:
                            await self._run_ct_to_passivedns_active_pivot(
                                ct_findings=ct_findings,
                                duckdb_store=duckdb_store,
                                remaining_s=remaining_s,
                            )
                        # Sprint F217E: Mirror quality rejections in ledger (aggressive advisory)
                        for _rec in self._result.quality_rejection_ledger or ():
                            try:
                                self._nonfeed_ledger.add_quality_rejection(
                                    source_family=_rec.source_family or "unknown",
                                    reason=_rec.reason or "unknown",
                                    sample_url=getattr(_rec, "url_sample", "") or "",
                                    sample_value=getattr(_rec, "finding_id", "")[:16],
                                )
                            except Exception:
                                pass  # fail-soft
            except _asyncio.TimeoutError:
                log.debug("[aggressive] ADVISORY lanes timed out after %ss", lanes_timeout)
            except _asyncio.CancelledError:
                raise  # [I6] propagate CancelledError
            except Exception:
                pass  # fail-soft: lane runner must never crash sprint
        else:
            log.debug("[F212-B] ADVISORY lanes skipped in aggressive: remaining=%.1fs", remaining_s)

        return True

    async def _run_public_discovery_in_cycle(
        self,
        query: str = "",
        duckdb_store: Any = None,
        hermes_engine: Any | None = None,
        memory_manager: Any | None = None,
        public_bootstrap_enabled: bool = False,  # Sprint F217C: deterministic bootstrap
    ) -> None:
        """
        Sprint 8XE: Run public discovery pipeline in the current cycle.
        P12: Also runs post-storage ToT hypothesis layer when hermes_engine is available.

        Uses asyncio.TaskGroup for bounded concurrency with the feed pipeline.
        Fail-soft: errors are accumulated but never raise or abort the sprint.

        query: real sprint query context from __main__.py (not a weak source hint).
        duckdb_store: DuckDBShadowStore instance for storing findings.
        hermes_engine: Hermes3Engine instance for P12 post-storage ToT (optional, M1 8GB safe).
        memory_manager: MemoryManager instance for session history (optional).
        UMA check is handled inside the pipeline itself.
        """
        try:
            async_run_public, PipelineRunResult = _import_live_public_pipeline()
        except Exception as exc:
            log.debug(f"[8XE] Public pipeline import failed: {exc}")
            self._result.public_error = f"import:{type(exc).__name__}"
            # F214-E: Always emit PUBLIC outcome so Lane Execution Truth never loses PUBLIC
            self._public_outcome = {
                "lane": "PUBLIC",
                "attempted": True,
                "skipped": False,
                "skip_reason": None,
                "raw_count": self._result.public_discovered,
                "built_count": 0,
                "accepted_count": self._result.public_accepted_findings,
                "error": f"import:{type(exc).__name__}",
                "timeout": False,
                "duration_s": None,
            }
            # Sprint F216C: Clear stale PipelineRunResult on early exit
            self._public_pipeline_result = None
            return

        # Build query hint: real sprint query from __main__.py takes priority
        query_hint = query or "OSINT passive discovery"

        try:
            async with asyncio.TaskGroup() as tg:
                public_task = tg.create_task(
                    async_run_public(
                        query=query_hint,
                        store=duckdb_store,  # Sprint 8XE: real store for finding persistence
                        max_results=5,
                        fetch_timeout_s=35.0,
                        fetch_concurrency=3,
                        hermes_engine=hermes_engine,  # P12: post-storage ToT hypothesis layer
                        memory_manager=memory_manager,  # P11: session history for RAG context
                        enqueue_hypothesis_pivot=self.enqueue_hypothesis_pivot,  # Sprint F193B: bounded feedback seam
                        public_bootstrap_enabled=public_bootstrap_enabled,  # Sprint F217C: deterministic bootstrap
                    ),
                    name="sprint:public",
                )
        except ExceptionGroup as eg:
            # F196A: TaskGroup ExceptionGroup handler for Python 3.11+.
            # TaskGroup __exit__ raises ExceptionGroup when a task fails.
            # Handle CancelledError propagation, log others.
            for e in eg.exceptions:
                if isinstance(e, asyncio.CancelledError):
                    raise e  # [I6] propagate CancelledError
                log.error(f"Public pipeline task failed: {e}")
            self._result.public_error = f"TaskGroup: {type(eg).__name__}"
            # F214-E: Always emit PUBLIC outcome so Lane Execution Truth never loses PUBLIC
            self._public_outcome = {
                "lane": "PUBLIC",
                "attempted": True,
                "skipped": False,
                "skip_reason": None,
                "raw_count": self._result.public_discovered,
                "built_count": 0,
                "accepted_count": self._result.public_accepted_findings,
                "error": f"TaskGroup: {type(eg).__name__}",
                "timeout": False,
                "duration_s": None,
            }
            # Sprint F216C: Clear stale PipelineRunResult on early exit
            self._public_pipeline_result = None
            return
        except Exception as exc:
            log.debug(f"[8XE] Public pipeline error: {exc}")
            self._result.public_error = f"{type(exc).__name__}:{exc}"
            # F214-E: Always emit PUBLIC outcome so Lane Execution Truth never loses PUBLIC
            self._public_outcome = {
                "lane": "PUBLIC",
                "attempted": True,
                "skipped": False,
                "skip_reason": None,
                "raw_count": self._result.public_discovered,
                "built_count": 0,
                "accepted_count": self._result.public_accepted_findings,
                "error": f"{type(exc).__name__}:{exc}",
                "timeout": False,
                "duration_s": None,
            }
            # Sprint F216C: Clear stale PipelineRunResult on early exit
            self._public_pipeline_result = None
            return

        # Task succeeded - accumulate results
        public_result = public_task.result()

        # Sprint F216C: Store PipelineRunResult for stage machine computation
        self._public_pipeline_result = public_result

        # Accumulate into result — fail-soft aggregation
        self._result.public_discovered += public_result.discovered
        self._result.public_fetched += public_result.fetched
        self._result.public_matched_patterns += public_result.matched_patterns
        self._result.public_accepted_findings += public_result.accepted_findings
        self._result.public_stored_findings += public_result.stored_findings
        if public_result.error:
            self._result.public_error = public_result.error

        # Sprint 8VD §F: Track public findings in scorecard count
        self._finding_count += public_result.accepted_findings
        # Sprint 8VN §C: Accumulate public branch verdict (additive, fail-soft)
        pbv = getattr(public_result, 'public_branch_verdict', None)
        if pbv and isinstance(pbv, dict) and len(self._public_verdicts) < 10:
            self._public_verdicts.append(pbv)
        # Sprint F169E: Public branch blocker aggregation — fail-soft
        if getattr(public_result, 'backend_degraded', False):
            self._result.public_backend_degraded = True

        # Sprint F207H: Populate _public_outcome for source_family_outcomes consumption
        # Maps PipelineRunResult fields to the shape normalize_source_family_outcome expects
        # Sprint F221C: Include public_bootstrap_enabled so timeout exception handler can determine
        # precise bootstrap terminal stage at the point of timeout (not at exception time).
        _pub_bootstrap_en = getattr(public_result, 'public_bootstrap_enabled', False)
        _pub_bootstrap_ord = getattr(public_result, 'public_bootstrap_order', 'disabled')
        _pub_bootstrap_prevented = getattr(public_result, 'public_bootstrap_prevented_discovery_timeout', False)
        _pub_bootstrap_fetch_att = getattr(public_result, 'public_bootstrap_first_fetch_attempted', False)
        # F208G-A: Collect all public_* telemetry into _public_outcome for propagation
        _pub_discovered = getattr(public_result, 'public_discovered', 0) or 0
        _pub_fetch_candidate = getattr(public_result, 'public_fetch_candidate_count', 0) or 0
        _pub_fetch_attempted = getattr(public_result, 'public_fetch_attempted', 0) or 0
        _pub_fetch_success = getattr(public_result, 'public_fetch_success', 0) or 0
        _pub_fetch_failed = getattr(public_result, 'public_fetch_failed', 0) or 0
        _pub_acceptance_attempted = getattr(public_result, 'public_acceptance_attempted', 0) or 0
        _pub_acceptance_accepted = getattr(public_result, 'public_acceptance_accepted', 0) or 0
        _pub_acceptance_rejected = getattr(public_result, 'public_acceptance_rejected', 0) or 0
        _pub_acceptance_reject_reasons = getattr(public_result, 'public_acceptance_reject_reasons', {}) or {}
        _pub_terminal_classified = getattr(public_result, 'public_terminal_classified_count', 0) or 0
        _pub_unclassified = getattr(public_result, 'public_unclassified_count', 0) or 0
        _pub_terminal_reason_counts = getattr(public_result, 'public_terminal_reason_counts', {}) or {}
        _pub_skipped_duplicate = getattr(public_result, 'public_skipped_duplicate', 0) or 0
        _pub_skipped_scheme = getattr(public_result, 'public_skipped_unsupported_scheme', 0) or 0
        _pub_skipped_mem = getattr(public_result, 'public_skipped_memory_gate', 0) or 0
        _pub_skipped_quality = getattr(public_result, 'public_skipped_quality_gate', 0) or 0
        _pub_skipped_browser = getattr(public_result, 'public_skipped_browser_unavailable', 0) or 0
        _pub_skipped_xml = getattr(public_result, 'public_skipped_xml_or_feed', 0) or 0
        _pub_skipped_timeout = getattr(public_result, 'public_skipped_timeout', 0) or 0
        _pub_skipped_fetch_err = getattr(public_result, 'public_skipped_fetch_error', 0) or 0
        _pub_skipped_url_sample = getattr(public_result, 'public_skipped_url_sample', ()) or ()
        _pub_rejected_no_pattern = getattr(public_result, 'public_rejected_no_pattern_match', 0) or 0
        _pub_rejected_low_info = getattr(public_result, 'public_rejected_low_information', 0) or 0
        _pub_rejected_duplicate = getattr(public_result, 'public_rejected_duplicate', 0) or 0
        _pub_rejected_storage = getattr(public_result, 'public_rejected_storage_rejected', 0) or 0
        _pub_rejected_url_sample = getattr(public_result, 'public_rejected_url_samples', ()) or ()
        _pub_accepted_url_sample = getattr(public_result, 'public_accepted_url_sample', ()) or ()
        self._public_outcome = {
            "lane": "PUBLIC",
            "attempted": True,
            "skipped": False,
            "skip_reason": None,
            "raw_count": getattr(public_result, 'discovered', 0) or 0,
            "built_count": getattr(public_result, 'fetched', 0) or 0,
            "accepted_count": getattr(public_result, 'accepted_findings', 0) or 0,
            "error": getattr(public_result, 'error', None),
            "timeout": getattr(public_result, 'timed_out', False),
            "duration_s": getattr(public_result, 'elapsed_s', None),
            "public_bootstrap_enabled": _pub_bootstrap_en,
            # Sprint F229A: Bootstrap ordering telemetry
            "public_bootstrap_order": _pub_bootstrap_ord,
            "public_bootstrap_prevented_discovery_timeout": _pub_bootstrap_prevented,
            "public_bootstrap_first_fetch_attempted": _pub_bootstrap_fetch_att,
            # F208G-A: PUBLIC Yield Taxonomy — full telemetry propagation
            "public_discovered": _pub_discovered,
            "public_fetch_candidate_count": _pub_fetch_candidate,
            "public_fetch_attempted": _pub_fetch_attempted,
            "public_fetch_success": _pub_fetch_success,
            "public_fetch_failed": _pub_fetch_failed,
            "public_acceptance_attempted": _pub_acceptance_attempted,
            "public_acceptance_accepted": _pub_acceptance_accepted,
            "public_acceptance_rejected": _pub_acceptance_rejected,
            "public_acceptance_reject_reasons": _pub_acceptance_reject_reasons,
            "public_terminal_classified_count": _pub_terminal_classified,
            "public_unclassified_count": _pub_unclassified,
            "public_terminal_reason_counts": _pub_terminal_reason_counts,
            "public_skipped_duplicate": _pub_skipped_duplicate,
            "public_skipped_unsupported_scheme": _pub_skipped_scheme,
            "public_skipped_memory_gate": _pub_skipped_mem,
            "public_skipped_quality_gate": _pub_skipped_quality,
            "public_skipped_browser_unavailable": _pub_skipped_browser,
            "public_skipped_xml_or_feed": _pub_skipped_xml,
            "public_skipped_timeout": _pub_skipped_timeout,
            "public_skipped_fetch_error": _pub_skipped_fetch_err,
            "public_skipped_url_sample": _pub_skipped_url_sample,
            "public_rejected_no_pattern_match": _pub_rejected_no_pattern,
            "public_rejected_low_information": _pub_rejected_low_info,
            "public_rejected_duplicate": _pub_rejected_duplicate,
            "public_rejected_storage_rejected": _pub_rejected_storage,
            "public_rejected_url_samples": _pub_rejected_url_sample,
            "public_accepted_url_sample": _pub_accepted_url_sample,
        }

        log.debug(
            f"[8XE] Public discovery: discovered={public_result.discovered} "
            f"matched={public_result.matched_patterns} "
            f"accepted={public_result.accepted_findings}"
        )

        # F205C: Public findings are scoped inside async_run_live_public_pipeline and not
        # exposed to the scheduler. Fall back to diagnostic log.
        if public_result.accepted_findings > 0 or public_result.stored_findings > 0:
            log.debug(
                "[F205C] Public accepted findings not in scheduler scope for sidecar dispatch "
                "(pipeline-internal storage). accepted=%d stored=%d",
                public_result.accepted_findings,
                public_result.stored_findings,
            )

    # ── F205C/F205F: Unified Sidecar Dispatch ────────────────────────────────

    async def _dispatch_accepted_findings_sidecars(
        self,
        source_branch: str,
        findings: list,
        store: Any,
        query: str,
    ) -> None:
        """
        F205C/F205F: Route accepted findings from any branch through FindingSidecarBus.

        Delegates to SidecarDispatcher (F205F extracted bookkeeping). All
        batch construction, empty guards, skipped heavy sidecar tracking,
        CancelledError propagation, and fail-soft handling live in the
        dispatcher.

        Args:
            source_branch: "feed" | "public" | "ct"
            findings: List of accepted CanonicalFinding objects
            store: DuckDBShadowStore instance
            query: Original sprint query
        """
        if self._sidecar_dispatcher is None:
            return
        # CancelledError re-raised by dispatcher; all other exceptions fail-soft
        await self._sidecar_dispatcher.dispatch(
            source_branch=source_branch,
            findings=findings,
            store=store,
            query=query,
            sprint_id=self.sprint_id or "",
        )

    # ── F193A: CT Log Discovery ─────────────────────────────────────────────

    async def _run_ct_log_discovery_in_cycle(
        self,
        query: str,
        store: Any,
    ) -> None:
        """
        Sprint F193A: Run CT log canonical discovery in the current cycle.

        Extracts domain from query, pivots via CTLogClient, converts results
        to CanonicalFinding and ingests into DuckDB store.

        Fail-soft: errors are accumulated but never raise or abort the sprint.
        """
        if self._ct_log_client is None or store is None:
            return

        import re

        matches = re.findall(
            r'[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z]{2,})+',
            query,
        )
        domain = matches[0].lstrip("www.") if matches else query.strip()
        if not domain:
            return

        session = None
        try:
            import aiohttp
            session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False))
            ct_result = await self._ct_log_client.pivot_domain(domain, session)
            findings = self._ct_log_client.to_canonical_findings(ct_result, query)
            self._result.ct_log_discovered = len(findings)
            if findings:
                # Sprint F195C: Enrich findings before storage (fail-safe — never crashes)
                await self._enrich_ct_findings_forensics(findings)
                # Sprint F195C: Multimodal enrichment for PDF/image findings
                await self._enrich_findings_multimodal(findings)
                # Sprint F198A: Accumulate findings to cross-sprint graph (fail-soft)
                self._accumulate_findings_to_graph(findings, sprint_id=self.sprint_id or "")
                results = await store.async_ingest_findings_batch(findings)
                stored = sum(1 for r in results if isinstance(r, dict) and r.get("accepted"))
                self._result.ct_log_stored = stored
                # Sprint F194A: ct_log_accepted_findings tracks accepted CT findings
                # for canonical truth accounting (additive to feed/public accepted_findings)
                self._result.ct_log_accepted_findings = stored
                # Sprint F204A: Route all accepted CT findings through FindingSidecarBus
                accepted_findings = [f for f, r in zip(findings, results)
                                     if isinstance(r, dict) and r.get("accepted")]
                await self._dispatch_accepted_findings_sidecars(
                    source_branch="ct",
                    findings=accepted_findings,
                    store=store,
                    query=query,
                )

                # F204D: Update cross-sprint target memory after findings are accepted
                if accepted_findings:
                    await self._run_target_memory_update(accepted_findings, store, query)
        except Exception as exc:
            self._result.ct_log_error = str(exc)[:200]
            logging.getLogger(__name__).warning("CT log discovery failed: %s", exc)
        finally:
            if session is not None:
                await session.close()

    # ── F198A: Cross-Sprint Graph Accumulation ───────────────────────────────

    def _accumulate_findings_to_graph(
        self,
        findings: list,
        sprint_id: str = "",
    ) -> int:
        """
        F198A: Extract IOCs from accepted findings and upsert to graph_service.

        Each finding is represented as an IOC node:
          - ioc_type  = source_type (e.g. "ct_log", "public", "feed")
          - ioc_value = finding_id (stable cross-sprint identifier)
          - confidence = finding.confidence
          - source    = sprint_id

        Fail-soft: graph errors must NOT prevent sprint continuation.

        Returns:
            Number of findings successfully upserted to graph.
        """
        if not findings:
            return 0
        count = 0
        try:
            from hledac.universal.knowledge import graph_service
            for finding in findings:
                fid = getattr(finding, "finding_id", None)
                if not fid:
                    continue
                src_type = getattr(finding, "source_type", "unknown") or "unknown"
                confidence = getattr(finding, "confidence", 0.5) or 0.5
                if graph_service.upsert_ioc(
                    value=fid,
                    ioc_type=src_type,
                    confidence=confidence,
                    source=sprint_id or "",
                ):
                    count += 1
        except Exception:
            pass  # Fail-soft: graph must never block sprint
        return count

    # ── Sprint F216G: Quality Rejection Ledger recording ─────────────────────────

    def _record_quality_rejections_from_store(self, store: Any | None) -> None:
        """
        Sprint F216G: Read quality rejection ledger from duckdb_store and
        compute summary dictionaries.

        Called after run_enabled_acquisition_lanes() completes (both advisory
        and aggressive cycles) so that all lane ingest quality gate rejections
        are captured in SprintSchedulerResult.

        Also called from _maybe_dispatch_nonfeed_probe_lanes after its
        direct async_ingest_findings_batch call.

        Invariants (strict):
          - No threshold changes
          - No dedup behavior changes
          - No destructive DB schema migration
          - No benchmark-owned scoring change
        """
        if store is None:
            return
        try:
            if not hasattr(store, "get_quality_rejection_ledger"):
                return
            ledger = store.get_quality_rejection_ledger()
            if not ledger:
                return
            # Store raw ledger (bounded tuple)
            self._result.quality_rejection_ledger = ledger

            # Compute summaries by family and reason
            quality_sum: dict[str, dict[str, int]] = {}
            dup_sum: dict[str, dict[str, int]] = {}
            low_info_sum: dict[str, dict[str, int]] = {}

            for rec in ledger:
                fam = rec.source_family or "unknown"
                reason = rec.reason or "unknown"
                if reason in ("low_entropy_rejected",):
                    d = low_info_sum.setdefault(fam, {})
                    d[reason] = d.get(reason, 0) + 1
                elif reason in ("persistent_duplicate", "duplicate_detected", "semantic_duplicate"):
                    d = dup_sum.setdefault(fam, {})
                    d[reason] = d.get(reason, 0) + 1
                else:
                    d = quality_sum.setdefault(fam, {})
                    d[reason] = d.get(reason, 0) + 1

            self._result.quality_rejection_summary_by_family = quality_sum
            self._result.duplicate_rejection_summary_by_family = dup_sum
            self._result.low_information_by_family = low_info_sum
        except Exception:
            pass  # Fail-soft: ledger recording must never block sprint


    # ── Sprint R8: CT → PassiveDNS Active-Cycle Pivot ───────────────────────
    async def _run_ct_to_passivedns_active_pivot(
        self,
        ct_findings: list,
        duckdb_store: Any | None,
        remaining_s: float,
    ) -> None:
        """
        Sprint R8: CT → PassiveDNS active-cycle bounded pivot.

        One-hop pivot from CT lane accepted findings to PassiveDNS lookup.
        Runs after CT lane candidates are ingested in ACTIVE cycle.

        Bounds: default 3 max 5 pivots, depth=1, no recursive, no CT→CT, no PDNS→CT.
        Skips if UMA critical/emergency, remaining time too low, or already ran.

        Flow:
          1. Guard: UMA, time, already-done flag
          2. Select deduplicated domains (max 5)
          3. Run PassiveDNS per domain (monkeypatched in tests)
          4. Convert results via passive_dns_results_to_findings
          5. Ingest via store.async_ingest_findings_batch
          6. Record FAMILY_PIVOT in NonfeedCandidateLedger
          7. Record source_family_outcomes pivot_source=ct, pivot_phase=active

        GHOST_INVARIANTS:
          - gather(return_exceptions=True), CancelledError re-raised
          - Fail-soft: adapter/storage errors never crash sprint
          - No asyncio.run() in async context
        """
        # Guard: skip if already done in this cycle
        if getattr(self, "_ct_pdns_active_done", False):
            log.debug("[R8] CT→PDNS active pivot skipped: already ran this cycle")
            return

        # Guard: skip under UMA critical/emergency
        try:
            governor = getattr(self, "_governor", None)
            if governor is not None:
                snap = await governor.evaluate()
                uma_state = getattr(snap, "uma_state", "ok") or "ok"
                if uma_state in ("critical", "emergency"):
                    log.debug(f"[R8] CT→PDNS active pivot skipped: uma_state={uma_state}")
                    return
        except Exception:
            pass

        # Guard: skip if remaining time below safety floor
        min_remaining = self._config._MIN_BRANCH_REMAINING_S if self._config else 3.0
        if remaining_s <= min_remaining:
            log.debug(f"[R8] CT→PDNS active pivot skipped: remaining={remaining_s:.1f}s <= floor={min_remaining:.1f}s")
            return

        if not ct_findings:
            log.debug("[R8] CT→PDNS active pivot: no CT findings to pivot")
            return

        from hledac.universal.runtime.acquisition_strategy import (
            select_ct_domains_for_passivedns_pivot,
        )
        pivot_domains = select_ct_domains_for_passivedns_pivot(ct_findings, max_pivots=5)
        if not pivot_domains:
            log.debug("[R8] CT→PDNS active pivot: no domains extracted from CT findings")
            return

        log.debug(f"[R8] CT→PDNS active pivot: {len(pivot_domains)} domains: {pivot_domains[:3]}...")

        self._ct_pdns_active_done = True

        store = getattr(self, "_duckdb_store", None) or duckdb_store
        pdns_results: list = []
        errors: list = []

        from hledac.universal.security.passive_dns import (
            call_lookup_passive_dns as _pdns_lookup,
            PassiveDNSOutcome,
        )
        from hledac.universal.runtime.source_finding_bridge import (
            passive_dns_results_to_findings,
        )

        async def _run_pdns_for_domain(domain: str) -> tuple[str, list[str], PassiveDNSOutcome | None]:
            try:
                ips, outcome = await _pdns_lookup(domain)
                return domain, ips, outcome
            except asyncio.CancelledError:
                raise
            except Exception:
                return domain, [], None

        try:
            gather_results = await asyncio.gather(
                *[_run_pdns_for_domain(d) for d in pivot_domains],
                return_exceptions=True,
            )
            for result in gather_results:
                if isinstance(result, BaseException):
                    if isinstance(result, asyncio.CancelledError):
                        raise result
                    errors.append(str(result))
                    continue
                domain, ips, outcome = result
                if outcome is not None:
                    pdns_results.append({"domain": domain, "ips": ips, "outcome": outcome})
                else:
                    errors.append(f"domain_{domain}_none")
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

        # Convert and ingest
        if pdns_results and store is not None:
            for res in pdns_results:
                outcome = res["outcome"]
                ips = res["ips"] or []
                domain = res["domain"]
                if not ips or outcome is None:
                    continue
                pdns_findings, pdns_rejections, pdns_telemetry = passive_dns_results_to_findings(
                    ips, outcome, domain, sprint_id=self.sprint_id or ""
                )
                if pdns_findings:
                    try:
                        ingest_results = await store.async_ingest_findings_batch(pdns_findings)
                        stored = sum(1 for r in ingest_results if isinstance(r, dict) and r.get("accepted"))
                        log.debug(f"[R8] PDNS pivot stored: domain={domain} stored={stored}")
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        log.debug(f"[R8] PDNS ingest failed for {domain}: {exc}")

        # Record FAMILY_PIVOT in NonfeedCandidateLedger
        ledger = getattr(self, "_nonfeed_ledger", None)
        if ledger is not None:
            for res in pdns_results:
                try:
                    domain = res.get("domain", "")
                    outcome = res.get("outcome", None)
                    if outcome and hasattr(outcome, "result_count"):
                        count = getattr(outcome, "result_count", 0) or 0
                        if count > 0:
                            ledger.add_pivot_discovered(
                                pivot_type="ct_to_passivedns",
                                ioc_value=domain,
                                source_hint=f"ct_domain:{domain}",
                                reason=f"pdns_results={count}",
                            )
                            ledger.add_pivot_stored(
                                pivot_type="ct_to_passivedns",
                                ioc_value=domain,
                                stored_count=count,
                            )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass

        # Record source_family_outcomes pivot_source=ct, pivot_phase=active
        if pdns_results:
            _sfos = list(getattr(self._result, "source_family_outcomes_list", []) or [])
            for res in pdns_results:
                outcome = res.get("outcome", None)
                if outcome is None:
                    continue
                _sfos.append({
                    "family": "passive_dns",
                    "lane": "PASSIVE_DNS",
                    "attempted": getattr(outcome, "attempted", True),
                    "accepted": getattr(outcome, "result_count", 0) or 0,
                    "terminal_state": "pivot_ct_domain",
                    "raw_count": 0,
                    "accepted_count": getattr(outcome, "result_count", 0) or 0,
                    "error": getattr(outcome, "error", None),
                    "timeout": getattr(outcome, "timeout", False),
                    "skipped": False,
                    "pivot_source": "ct",
                    "pivot_phase": "active",
                    "pivot_domains": pivot_domains,
                })
            self._result.source_family_outcomes_list = _sfos

        log.debug(
            f"[R8] CT→PDNS active pivot done: {len(pdns_results)} domains with results, "
            f"errors={len(errors)}"
        )

    # ── Sprint F207J-A: Lane findings accumulation into scheduler truth ──────────

    def _accumulate_lane_findings(
        self,
        outcomes: tuple,
        query: str,
    ) -> None:
        """
        Sprint F207J-A: Accumulate accepted lane findings into scheduler truth.
        [F207K-A] Extended with bridge rejection tracking.

        Populates:
          - _result.lane_*_accepted_findings counters
          - _lane_verdicts accumulator (for feed_verdict analog per lane)
          - _all_findings (bounded at 500, same cap as feed findings)
          - _lane_rejections (source_family, rejection_reason, rejected_count, samples)

        Also updates source_family_outcomes in the diagnostic report.

        Args:
            outcomes: Tuple of AcquisitionLaneOutcome from run_enabled_acquisition_lanes.
            query: Sprint query string (used for _all_findings entry).
        """
        _LANE_SOURCE_MAP = {
            AcquisitionLane.CT: "ct",
            AcquisitionLane.WAYBACK: "wayback_archive",
            AcquisitionLane.PASSIVE_DNS: "passive_dns",
            AcquisitionLane.BLOCKCHAIN: "blockchain",
            AcquisitionLane.IPFS: "ipfs",
        }

        for outcome in outcomes:
            if not getattr(outcome, "attempted", False):
                continue
            lane_name = getattr(outcome, "lane", None)
            accepted = getattr(outcome, "accepted_findings", 0) or 0
            produced = getattr(outcome, "produced_items", 0) or 0
            error = getattr(outcome, "error", None)
            duration = getattr(outcome, "duration_s", 0.0) or 0.0
            source_family = getattr(outcome, "source_family", None) or "unknown"

            # [F207K-A] Extract bridge rejection data
            rejected_count = getattr(outcome, "rejected_count", 0) or 0
            rejection_reasons = getattr(outcome, "rejection_reasons", ()) or ()
            sample_rejections = getattr(outcome, "sample_rejections", ()) or ()

            # Update per-lane accepted count on SprintSchedulerResult
            if lane_name == AcquisitionLane.CT:
                self._result.lane_ct_accepted_findings += accepted
            elif lane_name == AcquisitionLane.WAYBACK:
                self._result.lane_wayback_accepted_findings += accepted
            elif lane_name == AcquisitionLane.PASSIVE_DNS:
                self._result.lane_pdns_accepted_findings += accepted
            elif lane_name == AcquisitionLane.BLOCKCHAIN:
                self._result.lane_blockchain_accepted_findings += accepted
            elif lane_name == AcquisitionLane.IPFS:
                self._result.lane_ipfs_accepted_findings += accepted

            # Sprint F229: Populate wayback/pdns telemetry from AcquisitionLaneOutcome
            wayback_raw = getattr(outcome, "wayback_raw_count", 0) or 0
            passive_dns_raw = getattr(outcome, "passive_dns_raw_count", 0) or 0
            candidate_findings = getattr(outcome, "candidate_findings", ()) or ()
            if lane_name == AcquisitionLane.WAYBACK:
                self._result.wayback_attempted = True
                self._result.wayback_raw_count += wayback_raw
                self._result.wayback_candidates_built += len(candidate_findings)
                self._result.wayback_accepted_count += accepted
            elif lane_name == AcquisitionLane.PASSIVE_DNS:
                self._result.passive_dns_attempted = True
                self._result.passive_dns_raw_count += passive_dns_raw
                self._result.passive_dns_candidates_built += len(candidate_findings)
                self._result.passive_dns_accepted_count += accepted

            # Accumulate into _lane_verdicts (same shape as _feed_verdicts)
            # Shape: (verdict_tag, signal, fallback_use, fallback_waste, quality)
            if accepted > 0:
                verdict_tag = _LANE_SOURCE_MAP.get(lane_name, "unknown_lane")
                quality = 1 if not error else 0
                self._lane_verdicts.append((
                    verdict_tag,
                    accepted,
                    0,
                    0,
                    quality,
                ))
                # [F207K-A] Bounded accumulation of CanonicalFinding candidates from bridge
                candidate_findings = getattr(outcome, "candidate_findings", ()) or ()
                remaining = 500 - len(self._all_findings)
                if candidate_findings and remaining > 0:
                    for cf in candidate_findings[:remaining]:
                        try:
                            sf_type = getattr(cf, "source_type", verdict_tag) or verdict_tag
                            conf = getattr(cf, "confidence", 0.5) or 0.5
                            ts_val = getattr(cf, "ts", 0.0) or 0.0
                            desc = getattr(cf, "payload_text", f"bridge finding") or f"bridge finding"
                            self._all_findings.append({
                                "type": f"lane_{sf_type}",
                                "source": sf_type,
                                "matched_patterns": produced,
                                "accepted_findings": accepted,
                                "severity": "medium",
                                "confidence": conf,
                                "description": str(desc)[:200] if desc else f"bridge finding from {verdict_tag}",
                                "ts": ts_val,
                            })
                        except Exception:
                            continue
                elif remaining > 0:
                    self._all_findings.append({
                        "type": f"lane_{verdict_tag}",
                        "source": verdict_tag,
                        "matched_patterns": produced,
                        "accepted_findings": accepted,
                        "severity": "medium",
                        "confidence": quality * 0.8,
                        "description": (
                            f"{accepted} accepted findings from {verdict_tag} lane "
                            f"in {duration:.1f}s"
                        ),
                    })

            # [F207K-A] Record rejections: source_family, rejection_reason, rejected_count, sample
            if rejected_count > 0 and source_family != "unknown":
                if not hasattr(self, "_lane_rejections") or self._lane_rejections is None:
                    self._lane_rejections = []
                    self._lane_rejections_total_seen = 0
                    self._lane_rejections_dropped = 0
                verdict_tag = _LANE_SOURCE_MAP.get(lane_name, "unknown_lane")
                reason_counts: dict[str, int] = {}
                for reason in rejection_reasons:
                    reason_counts[reason] = reason_counts.get(reason, 0) + 1
                for reason, count in reason_counts.items():
                    self._lane_rejections_total_seen += count
                    self._lane_rejections.append({
                        "source_family": source_family,
                        "rejection_reason": reason,
                        "rejected_count": count,
                        "sample": list(sample_rejections[:3]),
                        "verdict_tag": verdict_tag,
                        "lane_name": str(lane_name) if lane_name else "unknown",
                    })
                    if len(self._lane_rejections) > MAX_LANE_REJECTIONS:
                        self._lane_rejections_dropped += len(self._lane_rejections) - MAX_LANE_REJECTIONS
                        self._lane_rejections = self._lane_rejections[-MAX_LANE_REJECTIONS:]

    # ── Sprint R1B: CT Lane Canonical Finding Ingest ─────────────────────────

    async def _ingest_ct_lane_candidates(
        self,
        outcomes: tuple,
        duckdb_store: Any,
    ) -> None:
        """
        Sprint R1B: Ingest CT lane CanonicalFinding candidates via DuckDBShadowStore.

        Bridges the gap between the acquisition lane's ct_results_to_findings() output
        (which produces CanonicalFinding dicts in candidate_findings) and the canonical
        storage path (async_ingest_findings_batch).

        Flow per CT outcome with candidates:
          1. Extract candidate_findings from CT AcquisitionLaneOutcome
          2. Call duckdb_store.async_ingest_findings_batch(candidates)
          3. Record storage results in NonfeedCandidateLedger (stored / quarantine / provider_failed)
          4. Update _result.lane_ct_accepted_findings with accepted count

        Fail-soft: storage errors never crash the sprint.
        CancelledError: re-raised to caller (GHOST_INVARIANTS I6).
        M1/UMA: no MLX model load in this path.

        Args:
            outcomes:   Tuple of AcquisitionLaneOutcome from run_enabled_acquisition_lanes.
            duckdb_store: DuckDBShadowStore instance for canonical storage.
        """
        if not duckdb_store:
            return
        for outcome in outcomes:
            if getattr(outcome, "source_family", None) != "ct":
                continue
            if not getattr(outcome, "attempted", False):
                continue
            candidates = getattr(outcome, "candidate_findings", ()) or ()
            if not candidates:
                continue

            # Record bridge rejections (quarantined) before storage
            rejection_reasons = getattr(outcome, "rejection_reasons", ()) or ()
            sample_rejections = getattr(outcome, "sample_rejections", ()) or ()
            for rej in rejection_reasons[:5]:
                try:
                    reason_str = getattr(rej, "reject_reason", str(rej)) or "bridge_rejection"
                    domain_str = getattr(rej, "domain", "") or ""
                    self._nonfeed_ledger.add_ct_quarantine(
                        domain=domain_str,
                        reject_reason=reason_str,
                        source_url="",
                        query=getattr(outcome, "ct_query", "") or "",
                    )
                except Exception:
                    pass  # fail-soft ledger write

            # Call canonical storage
            try:
                storage_results = await duckdb_store.async_ingest_findings_batch(list(candidates))

                # Record storage outcomes in ledger
                accepted_count = 0
                for i, result in enumerate(storage_results):
                    is_accepted = (
                        getattr(result, "lmdb_success", False) if hasattr(result, "lmdb_success")
                        else result.get("lmdb_success", False) if isinstance(result, dict) else False
                    )
                    candidate = candidates[i] if i < len(candidates) else None
                    candidate_id = (
                        getattr(candidate, "finding_id", "")[:32]
                        if candidate else ""
                    )
                    domain = (
                        getattr(candidate, "query", "") if candidate else ""
                    )
                    if is_accepted:
                        accepted_count += 1
                        try:
                            self._nonfeed_ledger.add_public_event(
                                stage="stored",
                                candidate_id=candidate_id,
                                reason="ct_stored",
                                accepted=True,
                                sample_url="",
                                sample_value=domain[:64] if domain else candidate_id[:16],
                            )
                        except Exception:
                            pass  # fail-soft
                    else:
                        try:
                            self._nonfeed_ledger.add_public_event(
                                stage="quarantine",
                                candidate_id=candidate_id,
                                reason="ct_quarantine",
                                accepted=False,
                                sample_url="",
                                sample_value=domain[:64] if domain else candidate_id[:16],
                            )
                        except Exception:
                            pass  # fail-soft

                # Update accepted count on result (set by _accumulate_lane_findings; augment)
                if accepted_count > 0:
                    self._result.lane_ct_accepted_findings = (
                        getattr(self._result, "lane_ct_accepted_findings", 0) + accepted_count
                    )

            except asyncio.CancelledError:
                raise  # [I6] propagate CancelledError
            except Exception:
                pass  # fail-soft: storage error never crashes sprint

    # ── F202B: Identity Stitching Sidecar ────────────────────────────────────

    async def _run_identity_stitching_sidecar(
        self,
        findings: list,
        store: Any,
        query: str,
    ) -> None:
        """
        F202B: Run identity stitching on accepted findings.

        Sidecar runs after findings are stored — does NOT block finding acceptance.
        Derived identity findings are ingested via async_ingest_findings_batch.

        Fail-soft: errors never crash the sprint.

        Args:
            findings: List of CanonicalFinding that were accepted and stored
            store: DuckDBShadowStore instance for async_ingest_findings_batch
            query: Original sprint query (for derived finding query field)
        """
        if not findings or store is None:
            return

        try:
            from hledac.universal.intelligence.entity_signal_extractor import (
                extract_entities_from_findings,
            )
            from hledac.universal.intelligence.identity_stitching_canonical import (
                create_identity_stitching_adapter,
            )
        except Exception:
            return  # Fail-soft: missing dependencies

        try:
            # 1. Extract entity profiles from findings (bounded: MAX_PROFILES=500)
            profiles = extract_entities_from_findings(findings)
            if not profiles:
                return

            # 2. Create adapter lazily (imports heavy stitching engine)
            if self._identity_adapter is None:
                self._identity_adapter = create_identity_stitching_adapter()

            # 3. Run stitching (bounded: MAX_COMPARISONS=2000)
            candidates = self._identity_adapter.extract_and_stitch(profiles)
            if not candidates:
                return

            # 3a. F203B: Run attribution confidence scoring on candidates
            if len(candidates) > 1:
                try:
                    from hledac.universal.intelligence.attribution_scorer import (
                        create_attribution_scorer,
                    )
                    scorer = create_attribution_scorer()
                    candidates = self._identity_adapter.score_and_enrich_candidates(
                        candidates, scorer
                    )
                except Exception:
                    pass  # Fail-soft: attribution scoring is optional enhancement

            # 4. Upsert identity edges to graph (advisory, fail-soft)
            #    F203B: edges now use attribution_confidence from signals if available
            try:
                self._identity_adapter.upsert_identity_edges(candidates)
            except Exception:
                pass  # Advisory only

            # 5. Convert to derived findings
            derived_findings = self._identity_adapter.to_derived_findings(
                candidates, query
            )
            if not derived_findings:
                return

            # F203D: Record identity stitching chain step (fail-soft)
            try:
                from knowledge.evidence_chain import get_global_builder
                builder = get_global_builder()
                root_ids = [getattr(f, "finding_id", "") or "" for f in findings if getattr(f, "finding_id", "")]
                output_ids = [getattr(df, "finding_id", "") or "" for df in derived_findings if getattr(df, "finding_id", "")]
                if root_ids and output_ids:
                    # One identity step: all roots → all derived findings
                    builder.record_identity(
                        root_finding_id=root_ids[0],
                        input_ids=root_ids,
                        output_id=f"identity-stitched-{len(output_ids)}",
                        confidence=float(sum(getattr(c, "confidence", 0.5) for c in candidates) / max(len(candidates), 1)),
                        reason=f"Linked {len(profiles)} profiles → {len(candidates)} identity candidates → {len(derived_findings)} derived findings",
                    )
                    self._result.chain_steps_recorded += 1
            except Exception:
                pass  # Fail-soft: chain recording must never crash sidecar

            # F203D: Record attribution scoring chain step (fail-soft)
            if len(candidates) > 1:
                try:
                    from knowledge.evidence_chain import get_global_builder
                    builder = get_global_builder()
                    root_ids = [getattr(f, "finding_id", "") or "" for f in findings if getattr(f, "finding_id", "")]
                    if root_ids:
                        builder.record_attribution(
                            root_finding_id=root_ids[0],
                            input_ids=root_ids,
                            output_id=f"attribution-scored-{len(candidates)}",
                            confidence=float(sum(getattr(c, "confidence", 0.5) for c in candidates) / max(len(candidates), 1)),
                            reason=f"Attribution scoring applied to {len(candidates)} identity candidates",
                        )
                        self._result.chain_steps_recorded += 1
                except Exception:
                    pass  # Fail-soft

            # 6. Ingest derived findings via async_ingest_findings_batch
            try:
                results = await store.async_ingest_findings_batch(derived_findings)
                stored = sum(
                    1 for r in results
                    if isinstance(r, dict) and r.get("accepted")
                )
                self._result.identity_findings_produced += stored
            except Exception:
                pass  # Fail-soft: derived findings are advisory

            self._result.identity_candidates_found = len(candidates)

        except Exception:
            pass  # Fail-soft: sidecar must never crash sprint

    # ── F202C: Asset Exposure Correlator Sidecar ─────────────────────────────

    async def _run_exposure_correlator_sidecar(
        self,
        findings: list,
        store: Any,
        query: str,
    ) -> None:
        """
        F202C: Run asset exposure correlation on accepted findings.

        Sidecar runs after findings are stored — does NOT block finding acceptance.
        Derived exposure findings are ingested via async_ingest_findings_batch.

        Fail-soft: errors never crash the sprint.

        Args:
            findings: List of CanonicalFinding that were accepted and stored
            store: DuckDBShadowStore instance for async_ingest_findings_batch
            query: Original sprint query (for derived finding query field)
        """
        if not findings or store is None:
            return

        try:
            from hledac.universal.intelligence.exposure_correlator import (
                create_exposure_correlator_adapter,
            )
        except Exception:
            return  # Fail-soft: missing dependencies

        try:
            # 1. Create adapter lazily (imports heavy correlation logic)
            if self._exposure_adapter is None:
                self._exposure_adapter = create_exposure_correlator_adapter()

            # 2. Correlate signals into exposure findings
            derived_findings = self._exposure_adapter.correlate(findings, query)
            if not derived_findings:
                return

            # F203D: Record exposure correlation chain step (fail-soft)
            try:
                from knowledge.evidence_chain import get_global_builder
                builder = get_global_builder()
                root_ids = [getattr(f, "finding_id", "") or "" for f in findings if getattr(f, "finding_id", "")]
                output_ids = [getattr(df, "finding_id", "") or "" for df in derived_findings if getattr(df, "finding_id", "")]
                if root_ids and output_ids:
                    builder.record_exposure(
                        root_finding_id=root_ids[0],
                        input_ids=root_ids,
                        output_id=f"exposure-correlated-{len(output_ids)}",
                        confidence=0.75,
                        reason=f"Correlated {len(findings)} findings → {len(derived_findings)} exposure findings",
                    )
                    self._result.chain_steps_recorded += 1
            except Exception:
                pass  # Fail-soft: chain recording must never crash sidecar

            # 3. Ingest derived findings via async_ingest_findings_batch
            try:
                results = await store.async_ingest_findings_batch(derived_findings)
                stored = sum(
                    1 for r in results
                    if isinstance(r, dict) and r.get("accepted")
                )
                self._result.exposure_findings_produced = stored
            except Exception:
                pass  # Fail-soft: derived findings are advisory

            # 4. Track correlated assets count
            stats = self._exposure_adapter.get_stats()
            self._result.correlated_assets_count = stats.get("assets_registered", 0)

        except Exception:
            pass  # Fail-soft: sidecar must never crash sprint

    # ── F202D: Leak Sentinel Sidecar ─────────────────────────────────────────

    async def _run_leak_sentinel_sidecar(
        self,
        findings: list,
        store: Any,
        query: str,
    ) -> None:
        """
        F202D: Run leak and secret sentinel on accepted findings.

        Sidecar runs after findings are stored — does NOT block finding acceptance.
        Derived leak findings are ingested via async_ingest_findings_batch.

        Fail-soft: errors never crash the sprint.

        Args:
            findings: List of CanonicalFinding that were accepted and stored
            store: DuckDBShadowStore instance for async_ingest_findings_batch
            query: Original sprint query (for derived finding query field)
        """
        if not findings or store is None:
            return

        try:
            from hledac.universal.intelligence.leak_sentinel import (
                create_leak_sentinel_adapter,
            )
        except Exception:
            return  # Fail-soft: missing dependency

        try:
            # 1. Create adapter lazily
            if self._leak_sentinel_adapter is None:
                self._leak_sentinel_adapter = create_leak_sentinel_adapter()

            # 2. Run bounded leak scan
            derived_findings = await self._leak_sentinel_adapter.scan(query)
            if not derived_findings:
                return

            # F203D: Record leak sentinel chain step (fail-soft)
            try:
                from knowledge.evidence_chain import get_global_builder
                builder = get_global_builder()
                root_ids = [getattr(f, "finding_id", "") or "" for f in findings if getattr(f, "finding_id", "")]
                output_ids = [getattr(df, "finding_id", "") or "" for df in derived_findings if getattr(df, "finding_id", "")]
                if root_ids and output_ids:
                    builder.record_leak(
                        root_finding_id=root_ids[0],
                        input_ids=root_ids,
                        output_id=f"leak-detected-{len(output_ids)}",
                        confidence=0.8,
                        reason=f"Leak scan on query → {len(derived_findings)} leak findings",
                    )
                    self._result.chain_steps_recorded += 1
            except Exception:
                pass  # Fail-soft: chain recording must never crash sidecar

            # 3. Ingest derived findings via async_ingest_findings_batch
            try:
                results = await store.async_ingest_findings_batch(derived_findings)
                stored = sum(
                    1 for r in results
                    if isinstance(r, dict) and r.get("accepted")
                )
                self._result.leak_findings_produced = stored
            except Exception:
                pass  # Fail-soft: derived findings are advisory

        except Exception:
            pass  # Fail-soft: sidecar must never crash sprint

    # ── F202E: Temporal Archaeology Sidecar ───────────────────────────────────

    async def _run_temporal_archaeology_sidecar(
        self,
        findings: list,
        store: Any,
        query: str,
    ) -> None:
        """
        F202E: Run temporal archaeology on accepted findings.

        Sidecar runs after findings are stored — does NOT block finding acceptance.
        Synthesizes timeline from CT timestamps, archive events, document metadata,
        and finding timestamps. Derived timeline findings are ingested via
        async_ingest_findings_batch.

        Fail-soft: errors never crash the sprint.

        Args:
            findings: List of CanonicalFinding that were accepted and stored
            store: DuckDBShadowStore instance for async_ingest_findings_batch
            query: Original sprint query (for derived finding query field)
        """
        if not findings or store is None:
            return

        try:
            from hledac.universal.intelligence.temporal_archaeologist_adapter import (
                create_temporal_archaeologist_adapter,
            )
        except Exception:
            return  # Fail-soft: missing dependencies

        try:
            # Create adapter
            adapter = create_temporal_archaeologist_adapter()

            # Synthesize timeline from CT findings
            ct_findings = [f for f in findings if getattr(f, "source_type", "") == "ct_log"]
            result = adapter.synthesize_timeline(
                ct_findings=ct_findings,
                entity_id=query[:64],
            )

            timeline = result.timeline
            derived_findings = result.derived_findings

            if not derived_findings:
                return

            # F203D: Record temporal archaeology chain step (fail-soft)
            try:
                from knowledge.evidence_chain import get_global_builder
                builder = get_global_builder()
                root_ids = [getattr(f, "finding_id", "") or "" for f in ct_findings if getattr(f, "finding_id", "")]
                output_ids = [getattr(df, "finding_id", "") or "" for df in derived_findings if getattr(df, "finding_id", "")]
                if root_ids and output_ids:
                    builder.record_temporal(
                        root_finding_id=root_ids[0],
                        input_ids=root_ids,
                        output_id=f"timeline-synthesized-{len(output_ids)}",
                        confidence=0.7,
                        reason=f"Synthesized {len(timeline)} timeline events from {len(ct_findings)} CT findings → {len(derived_findings)} timeline findings",
                    )
                    self._result.chain_steps_recorded += 1
            except Exception:
                pass  # Fail-soft: chain recording must never crash sidecar

            # Ingest derived findings via async_ingest_findings_batch
            try:
                results = await store.async_ingest_findings_batch(derived_findings)
                stored = sum(
                    1 for r in results
                    if isinstance(r, dict) and r.get("accepted")
                )
                self._result.timeline_findings_produced = stored
            except Exception:
                pass  # Fail-soft: derived findings are advisory

        except Exception:
            pass  # Fail-soft: sidecar must never crash sprint

    # ── F202I: Evidence Triage Sidecar ─────────────────────────────────────

    async def _run_evidence_triage_sidecar(
        self,
        findings: list,
        store: Any,
        query: str,
    ) -> None:
        """
        F202I: Count document findings with triage facets.

        Document findings already have triage facets embedded by DocumentExtractor
        via _build_document_envelope. This sidecar counts them for observability.

        Fail-soft: errors never crash the sprint.

        Args:
            findings: List of CanonicalFinding that were accepted and stored
            store: DuckDBShadowStore instance (unused — findings already stored)
            query: Original sprint query (unused)
        """
        if not findings:
            return

        try:
            import json
            triage_count = 0
            for finding in findings:
                if not hasattr(finding, "source_type") or finding.source_type != "document":
                    continue
                if not hasattr(finding, "payload_text") or not finding.payload_text:
                    continue
                # Check if payload_text contains triage envelope
                try:
                    payload = json.loads(finding.payload_text)
                    if isinstance(payload, dict) and "triage" in payload:
                        triage_count += 1
                except Exception:
                    pass
            self._result.evidence_triage_findings_count = triage_count
        except Exception:
            pass  # Fail-soft: sidecar must never crash sprint

    # ── F204D: Target Memory Update ───────────────────────────────────────────

    async def _run_target_memory_update(
        self,
        findings: list[Any],
        store: Any,
        query: str,
    ) -> None:
        """
        F204D: Update cross-sprint target memory after findings are accepted.

        Sidecar runs after findings are accepted and sidecar bus completes.
        Extracts entity/exposure/pivot facets from findings and merges into
        target memory via duckdb_store.

        RAM guard: skip if RSS > high_water (85% threshold).
        Fail-soft: errors never crash the sprint.

        Args:
            findings: List of CanonicalFinding that were accepted and stored
            store: DuckDBShadowStore instance for async_upsert_target_memory
            query: Original sprint query (used as target context)
        """
        if not findings or store is None:
            return

        # RAM guard — skip under memory pressure
        try:
            import psutil
        except Exception:
            return

        try:
            process = psutil.Process()
            mem_info = process.memory_info()
            rss_mb = mem_info.rss / 1024**2
            vm = psutil.virtual_memory()
            high_water = vm.percent * 0.85  # 85% threshold
            if rss_mb > high_water:
                return  # Skip merge under memory pressure
        except Exception:
            pass

        # Build entity/exposure/pivot facets from findings
        entity_facets: dict[str, Any] = {}
        exposure_facets: dict[str, Any] = {}
        pivot_facets: dict[str, Any] = {}

        for finding in findings:
            target_id = getattr(finding, "target_id", None) or getattr(finding, "entity_id", None)
            if not target_id:
                continue

            # Extract entity facets
            if hasattr(finding, "entity_type"):
                if target_id not in entity_facets:
                    entity_facets[target_id] = {"types": set(), "count": 0}
                entity_facets[target_id]["types"].add(getattr(finding, "entity_type", "unknown"))
                entity_facets[target_id]["count"] += 1

            # Extract exposure facets
            if hasattr(finding, "source_type") and getattr(finding, "source_type", None) == "exposure":
                if target_id not in exposure_facets:
                    exposure_facets[target_id] = {"signals": [], "count": 0}
                exposure_facets[target_id]["signals"].append(getattr(finding, "signal_type", "unknown"))
                exposure_facets[target_id]["count"] += 1

            # Extract pivot facets
            if hasattr(finding, "suggested_pivots"):
                pivots = getattr(finding, "suggested_pivots", [])
                for pivot in pivots[:5]:  # Max 5 pivots per finding
                    pivot_key = f"{pivot.get('pivot_type', '')}:{pivot.get('ioc_value', '')}"
                    if target_id not in pivot_facets:
                        pivot_facets[target_id] = {"pivots": [], "count": 0}
                    pivot_facets[target_id]["pivots"].append(pivot)
                    pivot_facets[target_id]["count"] += 1

            # F204H: Extract RIR/ASN facets from rir_correlation findings
            if hasattr(finding, "source_type") and getattr(finding, "source_type", None) == "rir_correlation":
                import json as _json
                payload_text = getattr(finding, "payload_text", None) or ""
                try:
                    rir_data = _json.loads(payload_text) if isinstance(payload_text, str) else {}
                except Exception:
                    rir_data = {}
                asn = rir_data.get("asn", "") or ""
                org = rir_data.get("org", "") or ""
                netblock = rir_data.get("netblock", "") or ""
                country = rir_data.get("country", "") or ""
                ioc_type = rir_data.get("ioc_type", "") or ""
                ioc_value_from_payload = rir_data.get("ioc_value", "") or getattr(finding, "ioc_value", "") or ""
                if target_id not in exposure_facets:
                    exposure_facets[target_id] = {"signals": [], "rir_asns": {}, "count": 0}
                rir_asns = exposure_facets[target_id].setdefault("rir_asns", {})
                if asn:
                    rir_asns[asn] = {"org": org, "netblock": netblock, "country": country,
                                      "ioc_type": ioc_type, "ioc_value": ioc_value_from_payload}
                exposure_facets[target_id]["count"] += 1

        # Convert sets to lists for JSON
        for tid in entity_facets:
            entity_facets[tid]["types"] = list(entity_facets[tid]["types"])[:MAX_MEMORY_ENTITIES]

        # Apply bounds
        for tid in list(exposure_facets.keys()):
            exposure_facets[tid]["signals"] = exposure_facets[tid]["signals"][:MAX_MEMORY_EXPOSURES]
            # F204H: bound rir_asns facet to 100 entries
            if "rir_asns" in exposure_facets[tid]:
                rir_asns = exposure_facets[tid]["rir_asns"]
                if len(rir_asns) > 100:
                    exposure_facets[tid]["rir_asns"] = dict(list(rir_asns.items())[:100])

        for tid in list(pivot_facets.keys()):
            pivot_facets[tid]["pivots"] = pivot_facets[tid]["pivots"][:MAX_MEMORY_PIVOTS]

        # Create update for each target
        now = _time.time()
        for target_id in set(entity_facets.keys()) | set(exposure_facets.keys()) | set(pivot_facets.keys()):
            update = TargetMemoryUpdate(
                target_id=target_id,
                sprint_id=self.sprint_id or "",
                finding_count=len(findings),
                entity_facets=entity_facets.get(target_id, {}),
                exposure_facets=exposure_facets.get(target_id, {}),
                pivot_facets=pivot_facets.get(target_id, {}),
                observed_ts=now,
            )

            # Lazy init of service
            if self._target_memory_service is None:
                self._target_memory_service = TargetMemoryService()

            merged = self._target_memory_service.merge_update(update)

            # Persist via duckdb_store — F206H FIX: pass merged TargetMemory
            # (previously passed update which caused silent failure due to type mismatch)
            try:
                await store.async_upsert_target_memory(merged)
            except Exception:
                pass  # Fail-soft: target memory is non-critical advisory

    async def _run_sprint_diff_sidecar(
        self,
        findings: list,
        store: Any,
        query: str,
    ) -> None:
        """
        F203A: Compute cross-sprint diff for target.

        Sidecar runs after findings are stored — does NOT block finding acceptance.
        Reads previous findings for the same target from DuckDB target_profiles,
        computes diff (new/disappeared/changed), updates profile, ingests diff
        findings via async_ingest_findings_batch.

        Fail-soft: errors never crash the sprint.

        Args:
            findings: List of CanonicalFinding that were accepted and stored
            store: DuckDBShadowStore instance for async_ingest_findings_batch
            query: Original sprint query (used as target_id)
        """
        if not findings or store is None:
            return

        target_id = query[:128]  # bounded target_id from query

        try:
            from hledac.universal.knowledge.sprint_diff_engine import SprintDiffEngine
        except Exception:
            return  # Fail-soft: missing dependency

        try:
            # 1. Get previous findings for this target
            try:
                prev_findings_raw = await store.async_get_previous_findings_for_target(
                    target_id, limit=1000
                )
            except Exception:
                prev_findings_raw = []

            # 2. Serialize previous findings to dicts
            previous_findings: list[dict] = []
            for f in prev_findings_raw:
                try:
                    previous_findings.append({
                        "finding_id": getattr(f, "finding_id", "") or "",
                        "source_type": getattr(f, "source_type", "") or "",
                        "ioc_type": getattr(f, "ioc_type", "") or "",
                        "ioc_value": getattr(f, "ioc_value", "") or "",
                        "confidence": getattr(f, "confidence", 0.5) or 0.5,
                        "ts": getattr(f, "ts", 0.0) or 0.0,
                        "payload_text": getattr(f, "payload_text", "") or "",
                    })
                except Exception:
                    continue

            # 3. Serialize current findings to dicts
            current_findings: list[dict] = []
            for f in findings:
                try:
                    current_findings.append({
                        "finding_id": getattr(f, "finding_id", "") or "",
                        "source_type": getattr(f, "source_type", "") or "",
                        "ioc_type": getattr(f, "ioc_type", "") or "",
                        "ioc_value": getattr(f, "ioc_value", "") or "",
                        "confidence": getattr(f, "confidence", 0.5) or 0.5,
                        "ts": getattr(f, "ts", 0.0) or 0.0,
                        "payload_text": getattr(f, "payload_text", "") or "",
                    })
                except Exception:
                    continue

            # 4. Create diff engine and compute diff
            engine = SprintDiffEngine()
            current_sprint_id = self.sprint_id or f"unknown-{id(self)}"
            previous_sprint_id = prev_findings_raw[0].sprint_id if prev_findings_raw else None  # type: ignore[attr-defined]

            diff_result = engine.compute_diff(
                current_findings=current_findings,
                previous_findings=previous_findings if previous_findings else None,
                target_id=target_id,
                current_sprint_id=current_sprint_id,
                previous_sprint_id=previous_sprint_id,
            )

            # 5. Build derived diff findings for canonical ingest
            class _DiffFinding:
                """Minimal CanonicalFinding-like object with __slots__ for efficiency."""
                __slots__ = ('finding_id', 'source_type', 'query', 'target_id',
                             'ioc_type', 'ioc_value', 'confidence', 'ts', 'payload_text')
                def __init__(self, **kw):
                    for k, v in kw.items():
                        setattr(self, k, v)

            derived_findings: list[Any] = []
            ts_now = _time.time()

            # Add new findings as derived findings
            for nf in diff_result.new_findings[:50]:  # bounded
                try:
                    finding_id = f"diff-new-{nf.get('finding_id', 'unknown')[:32]}"
                    payload = {
                        "diff_action": "new",
                        "target_id": target_id,
                        "previous_sprint_id": diff_result.previous_sprint_id,
                        "current_sprint_id": diff_result.current_sprint_id,
                        **nf,
                    }
                    derived_findings.append(_DiffFinding(
                        finding_id=finding_id,
                        source_type="sprint_diff",
                        query=query,
                        target_id=target_id,
                        ioc_type=nf.get("ioc_type") or "unknown",
                        ioc_value=nf.get("ioc_value") or "unknown",
                        confidence=nf.get("confidence", 0.5),
                        ts=ts_now,
                        payload_text=str(payload),
                    ))
                except Exception:
                    continue

            # Add disappeared findings as derived findings
            for df in diff_result.disappeared_findings[:50]:  # bounded
                try:
                    finding_id = f"diff-gone-{df.get('finding_id', 'unknown')[:32]}"
                    payload = {
                        "diff_action": "disappeared",
                        "target_id": target_id,
                        "previous_sprint_id": diff_result.previous_sprint_id,
                        "current_sprint_id": diff_result.current_sprint_id,
                        **df,
                    }
                    derived_findings.append(_DiffFinding(
                        finding_id=finding_id,
                        source_type="sprint_diff",
                        query=query,
                        target_id=target_id,
                        ioc_type=df.get("ioc_type") or "unknown",
                        ioc_value=df.get("ioc_value") or "unknown",
                        confidence=df.get("confidence", 0.5),
                        ts=ts_now,
                        payload_text=str(payload),
                    ))
                except Exception:
                    continue

            # F203D: Record sprint diff chain steps (fail-soft)
            try:
                from knowledge.evidence_chain import get_global_builder
                builder = get_global_builder()
                all_root_ids = [getattr(f, "finding_id", "") or "" for f in findings if getattr(f, "finding_id", "")]
                if all_root_ids:
                    root_id = all_root_ids[0]
                    new_ids = [f"diff-new-{nf.get('finding_id', 'unknown')[:32]}" for nf in diff_result.new_findings[:50]]
                    gone_ids = [f"diff-gone-{df.get('finding_id', 'unknown')[:32]}" for df in diff_result.disappeared_findings[:50]]
                    if new_ids:
                        builder.record_diff(
                            root_finding_id=root_id,
                            input_ids=all_root_ids,
                            output_id=f"diff-new-{len(new_ids)}",
                            confidence=0.75,
                            reason=f"Sprint diff: {len(new_ids)} new findings appeared vs previous sprint",
                        )
                        self._result.chain_steps_recorded += 1
                    if gone_ids:
                        builder.record_diff(
                            root_finding_id=root_id,
                            input_ids=all_root_ids,
                            output_id=f"diff-gone-{len(gone_ids)}",
                            confidence=0.75,
                            reason=f"Sprint diff: {len(gone_ids)} findings disappeared since previous sprint",
                        )
                        self._result.chain_steps_recorded += 1
            except Exception:
                pass  # Fail-soft: chain recording must never crash sidecar

            # 6. Ingest derived findings via async_ingest_findings_batch
            if derived_findings:
                try:
                    results = await store.async_ingest_findings_batch(derived_findings)
                    stored = sum(
                        1 for r in results
                        if isinstance(r, dict) and r.get("accepted")
                    )
                    self._result.sprint_diff_findings_produced = stored
                except Exception:
                    pass  # Fail-soft

            # 7. Update target profile in DuckDB
            try:
                from hledac.universal.knowledge.sprint_diff_engine import TargetProfileSummary
                prev_profile = None
                try:
                    prev_profile_raw = await store.async_get_target_profile(target_id)
                    if prev_profile_raw:
                        prev_profile = TargetProfileSummary(
                            target_id=prev_profile_raw.target_id,
                            first_seen=prev_profile_raw.first_seen,
                            last_seen=prev_profile_raw.last_seen,
                            cumulative_finding_count=prev_profile_raw.cumulative_finding_count,
                            entity_summary_json=prev_profile_raw.entity_summary_json,
                        )
                except Exception:
                    prev_profile = None

                new_profile = engine.build_target_profile(
                    current_findings=current_findings,
                    previous_profile=prev_profile,
                    target_id=target_id,
                    current_ts=ts_now,
                )
                await store.async_upsert_target_profile(new_profile)
            except Exception:
                pass  # Fail-soft: profile update is non-critical

        except Exception:
            pass  # Fail-soft: diff sidecar must never crash sprint

    # ── F203C: Kill Chain Tagging Sidecar ─────────────────────────────────

    async def _run_kill_chain_tagging_sidecar(
        self,
        findings: list,
        store: Any,
        query: str,
    ) -> None:
        """
        F203C: Tag findings with MITRE ATT&CK kill chain phases.

        Sidecar runs after findings are stored — does NOT block finding acceptance.
        Tags findings via regex/lookup patterns, stores kill-chain-tagged findings
        via async_ingest_findings_batch.

        Fail-soft: errors never crash the sprint.

        Args:
            findings: List of CanonicalFinding that were accepted and stored.
            store: DuckDBShadowStore instance for async_ingest_findings_batch.
            query: Original sprint query.
        """
        if not findings or store is None:
            return

        try:
            from hledac.universal.intelligence.kill_chain_tagger import (
                create_kill_chain_tagger,
            )
        except Exception:
            return  # Fail-soft: missing dependency

        try:
            tagger = create_kill_chain_tagger()
            tagged_results: dict[str, list] = {}  # finding_id -> list of tag dicts
            tagged_count = 0

            for finding in findings:
                fid = getattr(finding, "finding_id", None)
                if not fid:
                    continue
                tags = tagger.tag_finding(finding)
                if tags:
                    tagged_results[str(fid)] = [tag.to_dict() for tag in tags]
                    tagged_count += len(tags)

            if not tagged_results:
                return

            # F203D: Record kill chain tagging chain step (fail-soft)
            try:
                from knowledge.evidence_chain import get_global_builder
                builder = get_global_builder()
                root_ids = [getattr(f, "finding_id", "") or "" for f in findings if getattr(f, "finding_id", "")]
                output_ids = [f"kct-{fid[:32]}" for fid in tagged_results.keys()]
                if root_ids and output_ids:
                    builder.record_killchain(
                        root_finding_id=root_ids[0],
                        input_ids=root_ids,
                        output_id=f"killchain-tagged-{len(output_ids)}",
                        confidence=0.7,
                        reason=f"Tagged {len(tagged_results)} findings with {tagged_count} ATT&CK technique labels",
                    )
                    self._result.chain_steps_recorded += 1
            except Exception:
                pass  # Fail-soft: chain recording must never crash sidecar

            # Store tagged findings as derived findings for the canonical write path
            # Each tagged finding becomes a synthetic finding with kill chain tags
            derived_findings: list[Any] = []
            ts_now = _time.time()

            class _KCTFinding:
                """Minimal finding-like object with __slots__ for efficiency."""
                __slots__ = (
                    "finding_id", "source_type", "query", "target_id",
                    "ioc_type", "ioc_value", "confidence", "ts", "payload_text",
                )

                def __init__(self, **kw: Any) -> None:
                    for k, v in kw.items():
                        setattr(self, k, v)

            for fid, tags_list in tagged_results.items():
                try:
                    # Find original finding to get ioc_type, ioc_value
                    orig = next(
                        (f for f in findings if getattr(f, "finding_id", "") == fid),
                        None,
                    )
                    ioc_type = getattr(orig, "ioc_type", "unknown") if orig else "unknown"
                    ioc_value = getattr(orig, "ioc_value", fid) if orig else fid
                    confidence = getattr(orig, "confidence", 0.5) if orig else 0.5

                    derived_findings.append(
                        _KCTFinding(
                            finding_id=f"kct-{fid[:32]}",
                            source_type="killchain_tag",
                            query=query,
                            target_id=query[:128],
                            ioc_type=ioc_type,
                            ioc_value=ioc_value,
                            confidence=confidence,
                            ts=ts_now,
                            payload_text=str({"kill_chain_tags": tags_list}),
                        )
                    )
                except Exception:
                    continue

            if derived_findings:
                try:
                    results = await store.async_ingest_findings_batch(derived_findings)
                    stored = sum(
                        1 for r in results
                        if isinstance(r, dict) and r.get("accepted")
                    )
                    self._result.kill_chain_tags_produced = stored
                except Exception:
                    pass  # Fail-soft

        except Exception:
            pass  # Fail-soft: kill chain sidecar must never crash sprint

    # ── F203I: Streaming Embedding Sidecar ─────────────────────────────────

    async def _run_embedding_sidecar(
        self,
        findings: list,
        store: Any,
        query: str,
    ) -> None:
        """
        F203I: Run streaming embedding on accepted findings for ANN indexing.

        Sidecar runs after findings are stored — does NOT block finding acceptance.
        Uses StreamingEmbedder to embed findings in small batches, reducing peak
        RSS on M1 8GB. Indexed embeddings go to LanceDB ANN for fast dedup.

        Guardrails:
        - Model lifecycle via brain.model_lifecycle.get_model_lifecycle_status()
        - FETCH_SEMAPHORE=3 while model loaded
        - RAM guard blocks at >85% high_water / is_critical / is_emergency
        - prewarm() called after bulk embedding for faster dedup queries

        Fail-soft: errors never crash the sprint.

        Args:
            findings: List of CanonicalFinding that were accepted and stored.
            store: DuckDBShadowStore instance for async_ingest_findings_batch.
            query: Original sprint query.
        """
        if not findings or store is None:
            return

        try:
            from hledac.universal.intelligence.streaming_embedder import StreamingEmbedder

            embedder = StreamingEmbedder()

            # Collect embeddable findings (those with payload_text)
            embeddable = []
            for f in findings:
                text = getattr(f, "payload_text", None) or getattr(f, "query", "") or ""
                if len(text) >= 16:  # Skip very short texts
                    embeddable.append(f)

            if not embeddable:
                return

            # Stream embeddings in batches
            async for ids, embeddings in embedder.embed_findings(embeddable, batch_size=16):
                if ids and embeddings is not None and embeddings.shape[0] > 0:
                    try:
                        # Add to LanceDB ANN index
                        from hledac.universal.knowledge.ann_index import get_ann_index

                        ann = get_ann_index()
                        for idx, finding_id in enumerate(ids):
                            emb = embeddings[idx]
                            if emb.shape[0] == 256:
                                import hashlib

                                key = hashlib.blake2b(finding_id.encode(), digest_size=32).hexdigest()
                                text_hash = hashlib.sha256(finding_id.encode()).hexdigest()
                                ann.upsert(key, emb, text_hash)

                        # Optional: also add to VectorStore for RAG
                        try:
                            from hledac.universal.knowledge.vector_store import get_vector_store

                            vs = get_vector_store()
                            await vs.add_vectors_streaming(ids, embeddings, index_type="text", batch_size=16)
                        except Exception:
                            pass  # Non-critical: ANN is primary
                    except Exception:
                        pass  # Fail-soft: indexing must not crash sprint

            # Prewarm ANN after bulk embedding for faster dedup queries
            try:
                from hledac.universal.knowledge.ann_index import get_ann_index

                ann = get_ann_index()
                ann.prewarm(top_k=128)
            except Exception:
                pass  # Non-critical

        except Exception:
            pass  # Fail-soft: embedding sidecar must never crash sprint

    # ── F203F: Wayback Diff Sidecar ─────────────────────────────────────────

    async def _run_wayback_diff_sidecar(
        self,
        findings: list,
        store: Any,
        query: str,
    ) -> None:
        """
        F203F: Mine Wayback CDX for domain/URL changes.

        Sidecar runs after findings are stored — does NOT block finding acceptance.
        Extracts domains and URLs from accepted findings, queries Wayback CDX,
        detects changes (added/changed), and ingests diff findings via
        async_ingest_findings_batch.

        Fail-soft: errors never crash the sprint.

        Args:
            findings: List of CanonicalFinding that were accepted and stored
            store: DuckDBShadowStore instance for async_ingest_findings_batch
            query: Original sprint query (for derived finding query field)
        """
        if not findings or store is None:
            return

        try:
            from hledac.universal.intelligence.wayback_diff_miner import (
                WaybackDiffMiner,
            )
        except Exception:
            return  # Fail-soft: missing dependency

        try:
            # Extract domains/URLs from findings
            targets: list[str] = []
            for f in findings:
                ioc_value = getattr(f, "ioc_value", "") or ""
                ioc_type = getattr(f, "ioc_type", "") or ""
                if ioc_type in ("domain", "url") and ioc_value:
                    targets.append(ioc_value)
                elif hasattr(f, "url"):
                    url = getattr(f, "url", "") or ""
                    if url:
                        targets.append(url)

            if not targets:
                return

            # Mine Wayback for changes
            miner = WaybackDiffMiner()
            try:
                result = await miner.mine(targets)
            finally:
                await miner.close()

            if not result.change_events:
                return

            # Convert to CanonicalFinding and ingest
            findings_out = result.to_findings(query=query, sprint_id=self.sprint_id or "")
            if not findings_out:
                return

            try:
                results = await store.async_ingest_findings_batch(findings_out)
                stored = sum(
                    1 for r in results
                    if isinstance(r, dict) and r.get("accepted")
                )
                self._result.wayback_diff_findings_produced = stored
            except Exception:
                pass  # Fail-soft

        except Exception:
            pass  # Fail-soft: wayback diff sidecar must never crash sprint

    # ── F204H: RIR/ASN/WHOIS Correlator Sidecar ─────────────────────────────

    async def _run_rir_correlator_sidecar(
        self,
        findings: list,
        store: Any,
        query: str,
    ) -> None:
        """
        F204H: RIR/ASN/WHOIS correlation on accepted findings.

        Sidecar runs after findings are stored — does NOT block finding acceptance.
        Extracts IP addresses and domains from findings, correlates ASN/org/netblock/
        country via ip-api.com HTTP batch API, ingests derived findings via
        async_ingest_findings_batch. Also merges ASN/org facets into target_memory
        via async_upsert_target_memory.

        Fail-soft: errors never crash the sprint.

        Args:
            findings: List of CanonicalFinding that were accepted and stored
            store: DuckDBShadowStore instance for async_ingest_findings_batch
            query: Original sprint query (for derived finding query field)
        """
        if not findings or store is None:
            return

        try:
            from hledac.universal.intelligence.rir_correlator import (
                create_rir_correlator_adapter,
            )
        except Exception:
            return  # Fail-soft: missing dependency

        try:
            # 1. Correlate RIR signals from findings
            adapter = create_rir_correlator_adapter()
            derived_findings = adapter.correlate(findings, query)
            if not derived_findings:
                return

            # 2. Ingest derived findings via async_ingest_findings_batch
            try:
                results = await store.async_ingest_findings_batch(derived_findings)
                stored = sum(
                    1 for r in results
                    if isinstance(r, dict) and r.get("accepted")
                )
                self._result.rir_correlation_produced = stored
            except Exception:
                pass  # Fail-soft: derived findings are advisory

            # 3. Merge RIR facets into target_memory
            import json as _json

            rir_asn_facets: dict[str, dict[str, Any]] = {}
            for df in derived_findings:
                target_id = getattr(df, "target_id", None) or query[:128]
                if not target_id:
                    continue
                payload_text = getattr(df, "payload_text", None) or ""
                try:
                    rir_data = _json.loads(payload_text) if isinstance(payload_text, str) else {}
                except Exception:
                    rir_data = {}
                asn = rir_data.get("asn", "") or ""
                org = rir_data.get("org", "") or ""
                netblock = rir_data.get("netblock", "") or ""
                country = rir_data.get("country", "") or ""
                ioc_type = rir_data.get("ioc_type", "") or ""
                ioc_value = getattr(df, "ioc_value", "") or ""

                if target_id not in rir_asn_facets:
                    rir_asn_facets[target_id] = {"asns": {}, "count": 0}
                if asn:
                    rir_asn_facets[target_id]["asns"][asn] = {
                        "org": org,
                        "netblock": netblock,
                        "country": country,
                        "ioc_type": ioc_type,
                        "ioc_value": ioc_value,
                    }
                rir_asn_facets[target_id]["count"] += 1

            # Bound the ASN facets
            for tid in rir_asn_facets:
                asns = rir_asn_facets[tid].get("asns", {})
                if len(asns) > 100:
                    rir_asn_facets[tid]["asns"] = dict(list(asns.items())[:100])

            # Update target_memory with RIR facets
            if rir_asn_facets:
                now = _time.time()
                for target_id, rir_facet_data in rir_asn_facets.items():
                    # Merge into existing exposure_facets (deep merge for rir_asns key)
                    existing_memory = None
                    if self._target_memory_service is not None:
                        existing_memory = self._target_memory_service.get(target_id)

                    exposure_facets = {}
                    if existing_memory is not None:
                        exposure_facets = dict(existing_memory.exposure_facets)

                    if target_id not in exposure_facets:
                        exposure_facets[target_id] = {}
                    if "rir_asns" not in exposure_facets[target_id]:
                        exposure_facets[target_id]["rir_asns"] = {}
                    # Deep merge RIR ASNs
                    existing_asns = exposure_facets[target_id].get("rir_asns", {})
                    new_asns = rir_facet_data.get("asns", {})
                    merged_asns = {**existing_asns, **new_asns}
                    exposure_facets[target_id]["rir_asns"] = dict(
                        list(merged_asns.items())[:100]
                    )
                    exposure_facets[target_id]["count"] = (
                        exposure_facets[target_id].get("count", 0) + rir_facet_data.get("count", 0)
                    )

                    update = TargetMemoryUpdate(
                        target_id=target_id,
                        sprint_id=self.sprint_id or "",
                        finding_count=rir_facet_data.get("count", 0),
                        entity_facets={},
                        exposure_facets=exposure_facets,
                        pivot_facets={},
                        observed_ts=now,
                    )

                    if self._target_memory_service is None:
                        self._target_memory_service = TargetMemoryService()

                    merged = self._target_memory_service.merge_update(update)

                    # Persist via duckdb_store — pass merged TargetMemory (not update)
                    try:
                        await store.async_upsert_target_memory(merged)
                    except Exception:
                        pass  # Fail-soft: target memory is non-critical advisory

        except Exception:
            pass  # Fail-soft: sidecar must never crash sprint

    # ── F204I: Social Identity Surface Sidecar ─────────────────────────────────

    async def _run_social_identity_surface_sidecar(
        self,
        findings: list,
        store: Any,
        query: str,
    ) -> None:
        """
        F204I: Social identity surface miner.

        Extracts usernames, display names, profile URLs, bio links, PGP/email
        hints from accepted findings without invasive scraping. Social facets
        are stored via async_ingest_findings_batch with source_type="social_identity_surface"
        and may be used by AttributionConfidenceScorer.

        Fail-soft: errors never crash the sprint.

        Args:
            findings: List of CanonicalFinding that were accepted and stored
            store: DuckDBShadowStore instance for async_ingest_findings_batch
            query: Original sprint query
        """
        if not findings or store is None:
            return

        try:
            from hledac.universal.intelligence.social_identity_miner import (
                create_social_identity_miner_adapter,
            )
        except Exception:
            return  # Fail-soft: missing dependency

        try:
            miner = create_social_identity_miner_adapter()
            await miner.mine(findings, store, query)
        except Exception:
            pass  # Fail-soft: sidecar must never crash sprint

    # ── F206D: Run All Advisories via SprintAdvisoryRunner ────────────────────

    async def _run_advisory_runner(self) -> None:
        """
        F206D: Run all 4 advisory steps via SprintAdvisoryRunner.

        Canonical teardown entry point for all advisory orchestration.
        Each step is fail-soft; CancelledError propagates to caller.

        Runner order:
          1. pivot_planner  → planned_pivots
          2. pivot_executor → executed_pivots (gated by acquisition strategy)
          3. resource_governor → governor_recorded
          4. analyst_brief → brief_generated
        """
        runner = SprintAdvisoryRunner(
            scheduler=self,
            duckdb_store=getattr(self, "_duckdb_store", None),
            governor=getattr(self, "_governor", None),
            analyst_workbench=getattr(self, "_analyst_workbench", None),
        )

        # Sprint F206BK: Gate pivot_executor via acquisition strategy
        snapshot = getattr(self, "_acquisition_plan", None)
        if not is_lane_enabled(snapshot, AcquisitionLane.PIVOT_EXECUTOR):
            reason = lane_skip_reason(snapshot, AcquisitionLane.PIVOT_EXECUTOR) or "unknown"
            log.debug(
                f"[F206BK] pivot_executor skipped by acquisition strategy: {reason}"
            )
            self._result.acquisition_lanes_skipped += 1

        await runner.run_all_advisories()

        # Sprint R5: CT → PassiveDNS one-hop pivot (after all other advisories)
        await self._run_ct_to_passivedns_pivot_advisory()

    # ── F202G: Pivot Planner Advisory ───────────────────────────────────────

    async def _run_pivot_planner_advisory(self) -> None:
        """
        F202G: Run pivot planner on accepted findings for advisory ordering.

        Delegates to SprintAdvisoryRunner for the actual work.
        Kept as thin wrapper for backward compatibility.
        """
        runner = SprintAdvisoryRunner(
            scheduler=self,
            duckdb_store=getattr(self, "_duckdb_store", None),
            governor=getattr(self, "_governor", None),
            analyst_workbench=getattr(self, "_analyst_workbench", None),
        )
        await runner._run_pivot_planner_advisory(AdvisoryRunOutcome())

    # ── F204C: Pivot Executor Advisory ──────────────────────────────────────────

    async def _run_pivot_executor_advisory(self) -> None:
        """
        F204C: Execute top pivots from PivotPlanner via AutonomousPivotExecutor.

        Delegates to SprintAdvisoryRunner for the actual work.
        Kept as thin wrapper for backward compatibility.
        """
        runner = SprintAdvisoryRunner(
            scheduler=self,
            duckdb_store=getattr(self, "_duckdb_store", None),
            governor=getattr(self, "_governor", None),
            analyst_workbench=getattr(self, "_analyst_workbench", None),
        )
        await runner._run_pivot_executor_advisory(AdvisoryRunOutcome())


    # ── F202J: Resource Governor Advisory ─────────────────────────────────

    async def _run_resource_governor_advisory(self) -> None:
        """
        F202J: Apply resource governor decision at TEARDOWN.

        Delegates to SprintAdvisoryRunner for the actual work.
        Kept as thin wrapper for backward compatibility.
        """
        runner = SprintAdvisoryRunner(
            scheduler=self,
            duckdb_store=getattr(self, "_duckdb_store", None),
            governor=getattr(self, "_governor", None),
            analyst_workbench=getattr(self, "_analyst_workbench", None),
        )
        await runner._run_resource_governor_advisory(AdvisoryRunOutcome())

    # ── F204E: Analyst Brief Advisory ─────────────────────────────────────────

    async def _run_analyst_brief_advisory(self) -> None:
        """
        F204E: Generate analyst brief at TEARDOWN.

        Delegates to SprintAdvisoryRunner for the actual work.
        Kept as thin wrapper for backward compatibility.
        """
        runner = SprintAdvisoryRunner(
            scheduler=self,
            duckdb_store=getattr(self, "_duckdb_store", None),
            governor=getattr(self, "_governor", None),
            analyst_workbench=getattr(self, "_analyst_workbench", None),
        )
        await runner._run_analyst_brief_advisory(AdvisoryRunOutcome())

    # ── Sprint R5: CT → PassiveDNS One-Hop Pivot Advisory ─────────────────────

    async def _run_ct_to_passivedns_pivot_advisory(self) -> None:
        """
        Sprint R5: CT accepted domains → PassiveDNS one-hop pivot.

        One-hop pivot from CT lane accepted findings to PassiveDNS lookup.
        No recursive pivoting (pivot depth = 1).
        No new queue framework.
        No stealth/browser.

        Flow:
          1. Extract CT accepted domains from acquisition lane outcomes
          2. Deduplicate (max 10 via dict.fromkeys)
          3. Guard: skip if UMA critical/emergency
          4. For each domain: call PassiveDNS (monkeypatched in tests)
          5. Record FAMILY_PIVOT in NonfeedCandidateLedger
          6. Record source_family_outcomes pivot_source=ct

        GHOST_INVARIANTS:
          - gather(return_exceptions=True)
          - Manual CancelledError filter + error collection after gather
          - CancelledError re-raised
          - No MLX model load
          - No asyncio.run() in async context
          - Bounded: max 10 pivot domains
          - Fail-soft: pivot error never crashes sprint
        """
        # Guard: skip under UMA critical/emergency
        try:
            governor = getattr(self, "_governor", None)
            if governor is not None:
                snap = await governor.evaluate()
                uma_state = getattr(snap, "uma_state", "ok") or "ok"
                if uma_state in ("critical", "emergency"):
                    log.debug(
                        f"[R5] CT→PDNS pivot skipped: uma_state={uma_state}"
                    )
                    return
        except Exception:
            pass  # Fail-soft: governor evaluation is best-effort

        # Step 1: Get CT accepted findings from acquisition lane outcomes
        ct_findings: list = []
        try:
            outcomes = getattr(self._result, "acquisition_lane_outcomes", ()) or ()
            for outcome in outcomes:
                if getattr(outcome, "source_family", None) != "ct":
                    continue
                if not getattr(outcome, "attempted", False):
                    continue
                candidates = getattr(outcome, "candidate_findings", ()) or ()
                accepted = getattr(outcome, "accepted_count", 0) or 0
                if accepted > 0 and candidates:
                    ct_findings.extend(candidates)
        except Exception:
            pass  # fail-soft

        if not ct_findings:
            log.debug("[R5] CT→PDNS pivot: no CT accepted findings")
            return

        # Step 2: Select deduplicated domains (max 10 hard cap, default 5)
        from hledac.universal.runtime.acquisition_strategy import (
            select_ct_domains_for_passivedns_pivot,
        )
        pivot_domains = select_ct_domains_for_passivedns_pivot(
            ct_findings,
            max_pivots=5,
        )
        if not pivot_domains:
            log.debug("[R5] CT→PDNS pivot: no domains extracted from CT findings")
            return

        log.debug(f"[R5] CT→PDNS pivot: {len(pivot_domains)} domains: {pivot_domains[:3]}...")

        # Step 3: Run PassiveDNS for each domain (bounded, fail-soft)
        store = getattr(self, "_duckdb_store", None)
        pdns_results: list = []
        errors: list = []

        from hledac.universal.security.passive_dns import (
            call_lookup_passive_dns as _pdns_lookup,
            PassiveDNSOutcome,
        )

        async def _run_pdns_for_domain(domain: str) -> tuple[str, list[str], PassiveDNSOutcome]:
            """Run PassiveDNS for one domain, return (domain, ips, outcome)."""
            try:
                ips, outcome = await _pdns_lookup(domain)
                return domain, ips, outcome
            except Exception as exc:
                return domain, [], None

        try:
            gather_results = await asyncio.gather(
                *[_run_pdns_for_domain(d) for d in pivot_domains],
                return_exceptions=True,
            )

            # Check gathered results
            for i, result in enumerate(gather_results):
                if isinstance(result, BaseException):
                    if isinstance(result, asyncio.CancelledError):
                        raise result
                    errors.append(f"domain_{i}_error:{result}")
                    continue
                domain, ips, outcome = result
                if outcome is not None:
                    pdns_results.append({
                        "domain": domain,
                        "ips": ips,
                        "outcome": outcome,
                        "pivot_source": "ct",
                    })
                else:
                    errors.append(f"domain_{domain}_none")

        except asyncio.CancelledError:
            raise  # [I6] propagate CancelledError
        except Exception:
            pass  # fail-soft: PDNS errors never crash pivot

        # Step 4: Record FAMILY_PIVOT in NonfeedCandidateLedger
        ledger = getattr(self, "_nonfeed_ledger", None)
        if ledger is not None:
            for res in pdns_results:
                try:
                    domain = res.get("domain", "")
                    ips = res.get("ips", []) or []
                    outcome = res.get("outcome", None)
                    if outcome and hasattr(outcome, "result_count"):
                        count = getattr(outcome, "result_count", 0) or 0
                        if count > 0:
                            ledger.add_pivot_discovered(
                                pivot_type="ct_to_passivedns",
                                ioc_value=domain,
                                source_hint=f"ct_domain:{domain}",
                                reason=f"pdns_results={count}",
                            )
                except Exception:
                    pass  # fail-soft

        # Step 5: Record source_family_outcomes pivot source
        # Append pivot-driven PDNS outcomes to _result for the diagnostic report
        if pdns_results:
            _sfos = list(getattr(self._result, "source_family_outcomes_list", []) or [])
            for res in pdns_results:
                outcome = res.get("outcome", None)
                if outcome is None:
                    continue
                _sfos.append({
                    "family": "passive_dns",
                    "lane": "PASSIVE_DNS",
                    "attempted": getattr(outcome, "attempted", True),
                    "accepted": getattr(outcome, "result_count", 0) or 0,
                    "terminal_state": "pivot_ct_domain",
                    "raw_count": 0,
                    "accepted_count": getattr(outcome, "result_count", 0) or 0,
                    "error": getattr(outcome, "error", None),
                    "timeout": getattr(outcome, "timeout", False),
                    "skipped": False,
                    "pivot_source": "ct",
                    "pivot_domains": pivot_domains,
                })
            self._result.source_family_outcomes_list = _sfos  # type: ignore[attr-defined]

        log.debug(
            f"[R5] CT→PDNS pivot done: {len(pdns_results)} domains with results, "
            f"errors={len(errors)}"
        )

    # ── F206I: Source health summary for export teardown ─────────────────────

    # Bounds: no more than 100 source entries regardless of data availability
    MAX_SOURCE_HEALTH_ENTRIES: Final[int] = 100
    _POSTURE_ORDER: Final[dict[str, int]] = {
        "hot": 0, "warm": 1, "lukewarm": 2, "marginal": 3, "cold": 4, "unknown": 5
    }
    # F206I: MAX_BREAKER_DOMAINS mirrors MAX_TRACKED_DOMAINS from circuit_breaker
    MAX_BREAKER_DOMAINS: Final[int] = MAX_TRACKED_DOMAINS

    def _get_source_health_summary(self) -> dict:
        """
        F206I: Build a bounded source health summary from per-source economics.

        Reads _source_economics (in-memory, per-sprint) and returns a
        compact summary dict for the diagnostic report. Non-persisting.

        Bounds:
        - MAX_SOURCE_HEALTH_ENTRIES=100 (most-healthy first)
        - Each entry is a small dict with posture and cooldown info

        Fail-soft: returns empty dict on any error.

        GHOST_INVARIANTS:
        - No asyncio.gather / _check_gathered (sync method)
        - No asyncio.run() or loop.run_until_complete()
        - No model/MLX imports
        - No canonical write path (read-only)
        """
        try:
            if not self._source_economics:
                return {}
            # Sort: hot > warm > lukewarm > marginal > cold > unknown
            sorted_sources = sorted(
                self._source_economics.values(),
                key=lambda e: (
                    self._POSTURE_ORDER.get(e.recent_health_posture, 5),
                    -e.last_signal_cycle,
                ),
            )
            entries = []
            for econ in sorted_sources[: self.MAX_SOURCE_HEALTH_ENTRIES]:
                entries.append({
                    "source": econ.source,
                    "posture": econ.recent_health_posture,
                    "last_signal_cycle": econ.last_signal_cycle,
                    "silent_streak": econ.silent_streak,
                    "in_cooldown": econ.cooldown_until_cycle is not None,
                })
            total_tracked = len(self._source_economics)
            return {
                "entries": entries,
                "total_tracked": total_tracked,
                "max_entries": self.MAX_SOURCE_HEALTH_ENTRIES,
            }
        except Exception:
            return {}

    # ── F198A: Graph stats summary for export teardown ───────────────────────

    def _get_graph_signal(self) -> dict:
        """
        F198A: Read graph signal at teardown without blocking sprint.

        Returns graph node/edge stats as a dict, or empty dict on error.
        Non-blocking: called inside _build_diagnostic_report which is already
        in the export teardown path (not on the critical sprint path).
        """
        try:
            from hledac.universal.knowledge import graph_service
            stats = graph_service.graph_stats()
            if stats:
                return {
                    "graph_nodes": stats.get("nodes", 0),
                    "graph_edges": stats.get("edges", 0),
                    "graph_pgq_available": stats.get("pgq_available", False),
                }
        except Exception:
            pass
        return {}

    # Sprint F206E: Windup scorecard — read-only extraction from dormant windup_engine.py donor
    # MAX bound: no more than 32 scorecard keys regardless of data availability
    MAX_WINDUP_SCORECARD_KEYS: int = 32

    def _get_windup_scorecard(self) -> dict:
        """
        F206E: Extract read-only windup scorecard fields from active pipeline data.

        Reads bounded diagnostic fields from windup_engine.py scorecard WITHOUT
        activating the dormant run_windup() path. No model load, no GNN import.

        Safe read-only sources:
        - Circuit breaker states (transport.circuit_breaker)
        - Phase durations (from result timing fields)
        - Graph stats (from graph_service, already via _get_graph_signal)
        - Peak RSS (from result.peak_rss_gib or psutil)

        Fail-soft: returns empty dict on any error.

        GHOST_INVARIANTS:
        - No asyncio.run() or loop.run_until_complete()
        - No model/MLX imports on hot path
        - No GNN inference
        - Bounded: MAX_WINDUP_SCORECARD_KEYS=32
        """
        try:
            scorecard: dict = {}

            # 1. Circuit breaker open domains (read-only, fail-soft)
            try:
                # Uses module-level import from hledac.universal.transport.circuit_breaker
                cb_states = get_all_breaker_states()
                if cb_states:
                    # Bound: only open/half_open circuits are interesting for diagnostics
                    open_domains = {
                        d: s for d, s in cb_states.items()
                        if s in ("open", "half_open")
                    }
                    if open_domains:
                        scorecard["cb_open_domains"] = open_domains
                    scorecard["cb_tracked_count"] = len(cb_states)
            except Exception:
                pass

            # 2. Phase durations from timing fields already tracked in result
            phase_durations: dict = {}
            if self._result.pre_loop_elapsed_s is not None:
                phase_durations["warmup_s"] = round(self._result.pre_loop_elapsed_s, 2)
            if (
                self._result.entered_active_at_monotonic is not None
                and self._result.first_cycle_started_at_monotonic is not None
            ):
                active_dur = round(
                    self._result.first_cycle_started_at_monotonic
                    - self._result.entered_active_at_monotonic,
                    2,
                )
                phase_durations["active_s"] = max(0.0, active_dur)
            # Windup duration not available in active pipeline (run_windup() is dormant)
            if phase_durations:
                scorecard["phase_durations"] = phase_durations

            # 3. Graph stats (re-use _get_graph_signal for consistency)
            graph_signal = self._get_graph_signal()
            if graph_signal:
                scorecard["graph_nodes"] = graph_signal.get("graph_nodes", 0)
                scorecard["graph_edges"] = graph_signal.get("graph_edges", 0)
                scorecard["graph_pgq_available"] = graph_signal.get("graph_pgq_available", False)

            # 4. Peak RSS from result (set during sprint)
            if self._result.peak_rss_gib > 0:
                scorecard["peak_rss_mb"] = round(self._result.peak_rss_gib * 1024, 1)

            # 5. Accepted findings count (from result)
            if self._result.accepted_findings > 0:
                scorecard["accepted_findings"] = self._result.accepted_findings

            # 6. Sidecar findings counts (from result — additive sidecar metrics)
            sidecar_counts: dict = {}
            if self._result.identity_findings_produced > 0:
                sidecar_counts["identity"] = self._result.identity_findings_produced
            if self._result.exposure_findings_produced > 0:
                sidecar_counts["exposure"] = self._result.exposure_findings_produced
            if self._result.timeline_findings_produced > 0:
                sidecar_counts["timeline"] = self._result.timeline_findings_produced
            if self._result.leak_findings_produced > 0:
                sidecar_counts["leak"] = self._result.leak_findings_produced
            if self._result.evidence_triage_findings_count > 0:
                sidecar_counts["evidence_triage"] = self._result.evidence_triage_findings_count
            if self._result.forensics_enriched_ct_findings > 0:
                sidecar_counts["forensics"] = self._result.forensics_enriched_ct_findings
            if self._result.multimodal_enriched_findings > 0:
                sidecar_counts["multimodal"] = self._result.multimodal_enriched_findings
            if sidecar_counts:
                scorecard["sidecar_findings"] = sidecar_counts

            # 7. Branch timeout tracking (F195B)
            if self._result.branch_timeout_count > 0:
                scorecard["branch_timeouts"] = self._result.branch_timeout_count

            # 8. Budget violations (F204J)
            if self._result.budget_violations > 0:
                scorecard["budget_violations"] = self._result.budget_violations

            # Bound: enforce MAX_WINDUP_SCORECARD_KEYS
            if len(scorecard) > self.MAX_WINDUP_SCORECARD_KEYS:
                # Prune to bound — keep priority fields
                priority_keys = [
                    "cb_open_domains", "phase_durations", "graph_nodes",
                    "graph_edges", "peak_rss_mb", "accepted_findings",
                    "sidecar_findings", "branch_timeouts", "budget_violations",
                    "graph_pgq_available", "cb_tracked_count",
                ]
                pruned: dict = {}
                for k in priority_keys:
                    if k in scorecard:
                        pruned[k] = scorecard[k]
                        if len(pruned) >= self.MAX_WINDUP_SCORECARD_KEYS:
                            break
                scorecard = pruned

            return scorecard
        except Exception:
            return {}

    # ── F206I: Circuit breaker coverage summary ───────────────────────────────

    def _get_circuit_breaker_summary(self) -> dict:
        """
        F206I: Build a bounded circuit breaker state summary for the diagnostic report.

        Reads the shared domain circuit breaker registry (get_all_breaker_snapshots)
        and returns a compact summary. Non-persisting, in-memory only.

        Bounds:
        - MAX_TRACKED_DOMAINS=500 (from circuit_breaker module)
        - MAX_BREAKER_DOMAINS=500 (local alias)
        - Each snapshot is a small dict: domain, state, failure_count, retry_after_s

        Fail-soft: returns empty dict on any error.

        GHOST_INVARIANTS:
        - No asyncio.gather / _check_gathered (sync method)
        - No asyncio.run() or loop.run_until_complete()
        - No canonical write path (read-only)
        - Circuit breaker itself does not persist
        """
        try:
            snapshots = get_all_breaker_snapshots()
            if not snapshots:
                return {"total_tracked": 0, "open_count": 0, "half_open_count": 0}
            open_count = sum(1 for s in snapshots if s.state == "open")
            half_open_count = sum(1 for s in snapshots if s.state == "half_open")
            # Bound: include all closed + open + half_open in summary (up to MAX_BREAKER_DOMAINS)
            entries = []
            for snap in snapshots[: self.MAX_BREAKER_DOMAINS]:
                entries.append({
                    "domain": snap.domain,
                    "state": snap.state,
                    "failure_count": snap.failure_count,
                    "last_failure_kind": snap.last_failure_kind,
                    "recovery_timeout_s": round(snap.recovery_timeout_s, 1),
                })
            return {
                "total_tracked": len(snapshots),
                "open_count": open_count,
                "half_open_count": half_open_count,
                "entries": entries,
                "max_entries": self.MAX_BREAKER_DOMAINS,
            }
        except Exception:
            return {}

    def _build_work_items(
        self, sources: Sequence[str]
    ) -> list[SourceWork]:
        """Build and tier-sort work items from source list."""
        items = []
        for url in sources:
            tier = self._config.tier_of(url)
            items.append(SourceWork(
                feed_url=url,
                source=url,
                tier=tier,
                max_entries=self._config.max_entries_per_cycle,
            ))
        # Sort: high tier first
        items.sort(key=lambda w: _TIER_ORDER.index(w.tier))
        return items

    def _prune_work_items(
        self, items: list[SourceWork]
    ) -> list[SourceWork]:
        """Drop ARCHIVE and OTHER tier items when in prune mode."""
        return [w for w in items if w.tier not in (SourceTier.ARCHIVE, SourceTier.OTHER)]

    # ── Sprint F195C: Multimodal enrichment ─────────────────────────────────

    async def _enrich_findings_multimodal(self, findings: list) -> None:
        """
        Enrich PDF/image findings with multimodal analysis before storage.

        Fail-safe: enrichment errors are silent — never crash or abort the sprint.
        Enrichment is best-effort: absence of multimodal data is not an error.
        """
        if not findings:
            return
        enricher = self._multimodal_enricher
        lmdb_env = self._multimodal_lmdb_env
        if enricher is None or lmdb_env is None:
            return

        try:
            import json
            semaphore = asyncio.Semaphore(3)

            async def enrich_one(finding) -> None:
                async with semaphore:
                    try:
                        result = await enricher.enrich(finding)
                        if result is not None:
                            fid = getattr(finding, "finding_id", None)
                            if fid:
                                payload = json.dumps(result).encode()
                                with lmdb_env.begin(write=True) as txn:
                                    txn.put(fid.encode(), payload)
                                self._result.multimodal_enriched_findings += 1
                    except Exception:
                        pass  # Fail-safe: never crash

            raw_results = await asyncio.gather(*[enrich_one(f) for f in findings], return_exceptions=True)
            _check_gathered(raw_results, log, "multimodal_enrichment")
        except Exception:
            pass  # Fail-safe: never crash

    # ── Sprint F216E: Feed Dominance Budget ────────────────────────────────

    def _feed_dominance_should_fetch(
        self,
        work: "_FeedWorkItem",
        nonfeed_terminal: bool,
    ) -> tuple[bool, str]:
        """F216E+F227D: Determine if a feed source should be fetched given current budget state.

        F227D: Added mission_intent and nonfeed_unresolved to support mission-aware cap.
        F230D: Added acquisition_profile for nonfeed_diagnostic profile cap.

        Returns (should_fetch, reason):
          - (True, "")       — source should run normally
          - (False, reason)  — source should be skipped due to budget cap
        """
        budget = None
        mission_intent = None
        acquisition_profile = None
        if self._acquisition_plan is not None:
            budget = getattr(self._acquisition_plan, "feed_dominance_budget", None)
            mission_intent = getattr(self._acquisition_plan, "mission_intent", None)
            nd = getattr(self._acquisition_plan, "nonfeed_plan_debug", None)
            if nd is not None:
                acquisition_profile = getattr(nd, "acquisition_profile", None)

        # F227D: Evaluate mission-aware cap even when base budget is not active
        # (mission caps are evaluated via budget.cap_feeding with mission_intent)
        nonfeed_unresolved = not nonfeed_terminal

        if budget is None:
            return True, ""

        # F227D: Check mission-aware cap first (active for domain/infra/person/wallet missions)
        # F230D: Also check nonfeed_diagnostic profile cap here
        if mission_intent is not None and nonfeed_unresolved:
            mission_cap_reason = budget.cap_feeding(
                feed_accepted_so_far=self._result.accepted_findings,
                nonfeed_accepted_so_far=(
                    self._result.lane_ct_accepted_findings
                    + self._result.lane_wayback_accepted_findings
                    + self._result.lane_pdns_accepted_findings
                    + self._result.lane_blockchain_accepted_findings
                ),
                feed_per_source=self._feed_accepted_per_source,
                mission_intent=mission_intent,
                nonfeed_unresolved=nonfeed_unresolved,
                acquisition_profile=acquisition_profile,
            )
            if mission_cap_reason[0]:
                # F227D: Record cap reason for telemetry
                if mission_cap_reason[1].startswith("feed_cap_active:mission:"):
                    self._result.feed_budget_reason = mission_cap_reason[1]
                    # F227D: Annotate nonfeed_plan_debug with mission cap telemetry
                    if self._acquisition_plan is not None and self._acquisition_plan.nonfeed_plan_debug is not None:
                        nd = self._acquisition_plan.nonfeed_plan_debug
                        nd.mission_feed_cap_reason = mission_cap_reason[1]
                        nd.feed_cap_applied_by_mission = True
                        nd.feed_cap_mission_intent = mission_intent
                return False, mission_cap_reason[1]

        # F230D: nonfeed_diagnostic profile cap — evaluated when profile is active
        if acquisition_profile == AcquisitionProfile.NONFEED_DIAGNOSTIC and nonfeed_unresolved:
            profile_cap_reason = budget.cap_feeding(
                feed_accepted_so_far=self._result.accepted_findings,
                nonfeed_accepted_so_far=(
                    self._result.lane_ct_accepted_findings
                    + self._result.lane_wayback_accepted_findings
                    + self._result.lane_pdns_accepted_findings
                    + self._result.lane_blockchain_accepted_findings
                ),
                feed_per_source=self._feed_accepted_per_source,
                mission_intent=None,
                nonfeed_unresolved=nonfeed_unresolved,
                acquisition_profile=acquisition_profile,
            )
            if profile_cap_reason[0]:
                self._result.feed_budget_reason = profile_cap_reason[1]
                if self._acquisition_plan is not None and self._acquisition_plan.nonfeed_plan_debug is not None:
                    nd = self._acquisition_plan.nonfeed_plan_debug
                    nd.feed_cap_reason = profile_cap_reason[1]
                    nd.feed_cap_applied_by_mission = True
                return False, profile_cap_reason[1]

        # Fall through to base budget check only when base budget is active
        if not budget.is_active():
            return True, ""

        if nonfeed_terminal:
            return True, ""

        if self._feed_budget_triggered:
            return False, "feed_budget_triggered:global_suppressed"

        budget_reason = budget.cap_feeding(
            feed_accepted_so_far=self._result.accepted_findings,
            nonfeed_accepted_so_far=(
                self._result.lane_ct_accepted_findings
                + self._result.lane_wayback_accepted_findings
                + self._result.lane_pdns_accepted_findings
                + self._result.lane_blockchain_accepted_findings
            ),
            feed_per_source=self._feed_accepted_per_source,
            nonfeed_unresolved=nonfeed_unresolved,
            acquisition_profile=acquisition_profile,
        )
        if budget_reason[0]:
            return False, budget_reason[1]

        return True, ""

    def _feed_dominance_record_result(
        self,
        feed_url: str,
        accepted_count: int,
        suppressed: bool,
        reason: str,
    ) -> None:
        """F216E: Record feed result into budget telemetry.

        F230D: Also records nonfeed_budget telemetry when nonfeed_diagnostic profile active.
        """
        if accepted_count > 0:
            self._feed_accepted_per_source[feed_url] = (
                self._feed_accepted_per_source.get(feed_url, 0) + accepted_count
            )

        if suppressed:
            self._result.feed_suppressed_by_budget += accepted_count
            # F230D: Track nonfeed budget suppression separately
            if "nonfeed_profile" in reason:
                self._result.feed_suppressed_by_nonfeed_budget += accepted_count
                self._result.feed_suppression_count += 1
                self._result.feed_suppression_reason = reason

        if not self._feed_budget_triggered and reason:
            self._feed_budget_triggered = True
            self._result.feed_budget_active = True
            self._result.feed_budget_reason = reason
            self._result.feed_accepted_before_cap = self._result.accepted_findings
            self._result.feed_budget_per_source = dict(self._feed_accepted_per_source)
            top = sorted(
                self._feed_accepted_per_source.items(),
                key=lambda x: x[1],
                reverse=True,
            )[:10]
            self._result.top_feed_source_counts = tuple(top)

            budget = None
            if self._acquisition_plan is not None:
                budget = getattr(self._acquisition_plan, "feed_dominance_budget", None)
            if budget and budget.max_feed_per_source > 0:
                for src, cnt in top:
                    if cnt >= budget.max_feed_per_source:
                        self._result.max_per_source_applied = src
                        break

            # F230D: Also activate nonfeed budget telemetry if reason contains nonfeed_profile
            if "nonfeed_profile" in reason:
                self._result.nonfeed_budget_active = True
                nd = getattr(self._acquisition_plan, "nonfeed_plan_debug", None) if self._acquisition_plan else None
                if nd is not None:
                    self._result.nonfeed_budget_expected_lanes = getattr(nd, "nonfeed_profile_expected_lanes", ())
                    # Compute terminal vs unresolved lanes
                    _expected = list(self._result.nonfeed_budget_expected_lanes)
                    _terminal = []
                    _unresolved = []
                    for lane in _expected:
                        _count = getattr(self._result, f"lane_{lane.lower()}_accepted_findings", 0)
                        if _count > 0:
                            _terminal.append(lane)
                        else:
                            _unresolved.append(lane)
                    self._result.nonfeed_budget_terminal_lanes = tuple(_terminal)
                    self._result.nonfeed_budget_unresolved_lanes = tuple(_unresolved)

    # ── Sprint F195C: Forensics enrichment ─────────────────────────────────

    async def _enrich_ct_findings_forensics(self, findings: list) -> None:
        """
        Enrich CT findings with forensics analysis before storage.

        Fail-safe: enrichment errors are silent — never crash or abort the sprint.
        Enrichment is best-effort: absence of forensics data is not an error.
        """
        if not findings:
            return
        enricher = self._forensics_enricher
        lmdb_env = self._forensics_lmdb_env
        if enricher is None or lmdb_env is None:
            return

        try:
            import json
            semaphore = asyncio.Semaphore(3)

            async def enrich_one(finding) -> None:
                async with semaphore:
                    try:
                        result = await enricher.enrich(finding)
                        if result is not None:
                            fid = getattr(finding, "finding_id", None)
                            if fid:
                                payload = json.dumps(result).encode()
                                with lmdb_env.begin(write=True) as txn:
                                    txn.put(fid.encode(), payload)
                                self._result.forensics_enriched_ct_findings += 1
                    except Exception:
                        pass  # Fail-safe: never crash

            raw_results = await asyncio.gather(*[enrich_one(f) for f in findings], return_exceptions=True)
            _check_gathered(raw_results, log, "forensics_enrichment")
        except Exception:
            pass  # Fail-safe: never crash

    def _process_result(self, feed_url: str, result) -> None:
        """Accumulate result stats and dedup."""
        # Accumulate per-source stats
        self._entries_per_source[feed_url] = (
            self._entries_per_source.get(feed_url, 0) + result.fetched_entries
        )
        self._hits_per_source[feed_url] = (
            self._hits_per_source.get(feed_url, 0) + result.matched_patterns
        )
        # Also update _result directly so it's available even without _build_diagnostic_report
        self._result.entries_per_source[feed_url] = self._entries_per_source[feed_url]
        self._result.hits_per_source[feed_url] = self._hits_per_source[feed_url]
        self._result.total_pattern_hits += result.matched_patterns
        self._result.accepted_findings += result.accepted_findings
        # Sprint F216A: Emit FEED accepted event when findings are accepted
        if result.accepted_findings > 0:
            self._emit_source_family_event(
                family="FEED",
                event="accepted",
                count=result.accepted_findings,
            )
        # Sprint 8VD §F: Track finding count for scorecard
        self._finding_count += result.accepted_findings
        # Sprint 8VN §C: Accumulate feed economics verdict (additive, fail-soft)
        if hasattr(result, 'feed_economics_verdict'):
            verdict = result.feed_economics_verdict
            if verdict and isinstance(verdict, (list, tuple)) and len(verdict) == 5:
                self._feed_verdicts.append(tuple(verdict))
        # Sprint F169E: Feed branch blocker aggregation — fail-soft, additive
        _zsr = getattr(result, 'zero_signal_reason', None)
        _stage = getattr(result, 'signal_stage', 'unknown')
        if _zsr:
            self._result.feed_zero_yield_detected = True
            match _zsr:
                case "empty_fetch":
                    self._result.feed_inaccessible_detected = True
                case "content_empty":
                    self._result.feed_content_empty_detected = True
                case "no_pattern_hits_with_content":
                    self._result.feed_no_pattern_with_content = True
                case "findings_build_loss":
                    self._result.findings_build_loss_detected = True
                    self._result.feed_no_signal_sources.append(feed_url)
            # Bounded: max 20 sources in blocker list
            if len(self._result.feed_no_signal_sources) < 20:
                if feed_url not in self._result.feed_no_signal_sources:
                    self._result.feed_no_signal_sources.append(feed_url)
        # Sprint 8VN: Accumulate findings for correlation + hypothesis seams
        # Bounded to 500 to stay M1 8GB safe
        if hasattr(result, 'matched_patterns') and result.matched_patterns > 0:
            finding_entry = {
                "type": "pattern_hit",
                "source": feed_url,
                "matched_patterns": result.matched_patterns,
                "accepted_findings": result.accepted_findings,
                "severity": "medium",
                "confidence": 0.6,
                "description": f"{result.matched_patterns} pattern hits from {feed_url}",
            }
            # Sprint 8VN: bounded accumulation — cap at 500 to prevent OOM
            if len(self._all_findings) < 500:
                self._all_findings.append(finding_entry)
        # Sprint F160C: Update source economics from pipeline result signals
        # Uses signal_stage, feed_confidence_score, winning_source_breakdown
        self._update_source_economics(feed_url, result, self._result.cycles_started)
        # Sprint F199A: Collect per-source quality feedback for reward-driven weight adaptation
        # Bounded accumulation: max 200 feed_urls tracked
        if len(self._source_quality_feedback) < 200:
            fb = self._source_quality_feedback.setdefault(feed_url, {"fetched": 0, "accepted": 0})
            fb["fetched"] = fb.get("fetched", 0) + getattr(result, 'fetched_entries', 0)
            fb["accepted"] = fb.get("accepted", 0) + getattr(result, 'accepted_findings', 0)
        # Sprint F200A: Record outcome for prefetch oracle (advisory only, fail-soft)
        if self._prefetch_oracle is not None:
            try:
                self._prefetch_oracle.record_outcome(
                    feed_url=feed_url,
                    fetched=getattr(result, 'fetched_entries', 0),
                    accepted=getattr(result, 'accepted_findings', 0),
                    cycle=self._result.cycles_started,
                    seen_new_urls=getattr(result, 'matched_patterns', 0),
                )
            except Exception:
                pass  # Advisory only — never affect scheduler
        # Sprint F216E: Record feed result into budget telemetry
        self._feed_dominance_record_result(
            feed_url=feed_url,
            accepted_count=result.accepted_findings,
            suppressed=(getattr(result, "error", "") == "feed_budget_cap_suppressed"),
            reason=getattr(result, "feed_budget_cap_reason", ""),
        )

    # ── Dedup ─────────────────────────────────────────────────────────────

    # ── Persistent dedup (Sprint 8RA) ───────────────────────────────────

    async def _load_dedup(self) -> None:
        """Load existing hashes from LMDB at BOOT. Idempotent."""
        db_path = _get_dedup_lmdb_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._dedup_env = lmdb.open(
                str(db_path),
                map_size=100 * 1024 * 1024,  # 100MB max
                max_dbs=1,
            )
            with self._dedup_env.begin() as txn:
                cursor = txn.cursor()
                count = 0
                for key, _ in cursor:
                    self._dedup_seen.add(key.decode())
                    count += 1
            # Sprint 8RA: Bound dedup set to prevent unbounded growth
            if len(self._dedup_seen) > 500_000:
                # Trim to 400k to leave headroom
                excess = list(self._dedup_seen)
                self._dedup_seen = set(excess[-400_000:])
                log.warning(f"Dedup set trimmed to 400k entries (was {count})")
            log.info(f"Dedup LMDB loaded: {count} existing hashes")
        except Exception as exc:
            log.warning(f"Dedup LMDB open failed: {exc} — continuing without persistence")
            self._dedup_env = None

    async def _flush_dedup(self) -> None:
        """Flush in-memory hashes to LMDB. Called at WINDUP."""
        if self._dedup_env is None or not self._dedup_seen:
            return
        try:
            ts_bytes = struct.pack("d", _time.time())
            with self._dedup_env.begin(write=True) as txn:
                for key in self._dedup_seen:
                    txn.put(key.encode(), ts_bytes, overwrite=True)
            log.info(f"Dedup flushed: {len(self._dedup_seen)} hashes")
        except Exception as exc:
            log.warning(f"Dedup flush failed: {exc}")

    async def _close_dedup(self) -> None:
        """Close LMDB at TEARDOWN. Calls flush first."""
        await self._flush_dedup()
        if self._dedup_env is not None:
            try:
                self._dedup_env.close()
            except Exception as exc:
                log.warning(f"Dedup LMDB close failed: {exc}")
            self._dedup_env = None
        # Sprint 8RA: Close DuckDB read connection
        if self._duckdb_read_con is not None:
            try:
                self._duckdb_read_con.close()
            except Exception:
                pass
            self._duckdb_read_con = None

    # ── Sprint F195C: Forensics enrichment ─────────────────────────────────

    async def _init_forensics(self) -> None:
        """Initialize forensics enricher and LMDB. Fail-safe — never raises."""
        try:
            from forensics.enrichment_service import ForensicsEnricher
            self._forensics_enricher = ForensicsEnricher()
            await self._forensics_enricher.initialize()
        except Exception as exc:
            log.debug("Forensics enricher init failed: %s", exc)
            self._forensics_enricher = None

        try:
            db_path = _get_forensics_lmdb_path()
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self._forensics_lmdb_env = lmdb.open(
                str(db_path),
                map_size=50 * 1024 * 1024,  # 50MB max for enrichment data
                max_dbs=1,
            )
        except Exception as exc:
            log.debug("Forensics LMDB open failed: %s", exc)
            self._forensics_lmdb_env = None

    async def _flush_forensics(self) -> None:
        """Flush forensics LMDB. Called at WINDUP. No-op if not initialized."""
        pass  # LMDB write-only env auto-flushes; nothing to do

    async def _close_forensics(self) -> None:
        """Close forensics enricher and LMDB at TEARDOWN."""
        if self._forensics_enricher is not None:
            try:
                await self._forensics_enricher.close()
            except Exception as exc:
                log.debug("Forensics enricher close failed: %s", exc)
            self._forensics_enricher = None
        if self._forensics_lmdb_env is not None:
            try:
                self._forensics_lmdb_env.close()
            except Exception as exc:
                log.debug("Forensics LMDB close failed: %s", exc)
            self._forensics_lmdb_env = None

    # ── Sprint F195C: Multimodal enrichment ─────────────────────────────────

    async def _init_multimodal(self) -> None:
        """Initialize multimodal enricher and LMDB. Fail-safe — never raises."""
        try:
            from multimodal.analyzer import MultimodalEnricher
            self._multimodal_enricher = MultimodalEnricher(
                governor=self._governor,
                embedding_dim=1280,
                batch_size=4,
            )
            await self._multimodal_enricher.initialize()
        except Exception as exc:
            log.debug("Multimodal enricher init failed: %s", exc)
            self._multimodal_enricher = None

        try:
            db_path = _get_multimodal_lmdb_path()
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self._multimodal_lmdb_env = lmdb.open(
                str(db_path),
                map_size=50 * 1024 * 1024,  # 50MB max
                max_dbs=1,
            )
        except Exception as exc:
            log.debug("Multimodal LMDB open failed: %s", exc)
            self._multimodal_lmdb_env = None

    async def _flush_multimodal(self) -> None:
        """Flush multimodal LMDB. Called at WINDUP. No-op if not initialized."""
        pass  # LMDB write-only env auto-flushes; nothing to do

    async def _close_multimodal(self) -> None:
        """Close multimodal enricher and LMDB at TEARDOWN."""
        if self._multimodal_enricher is not None:
            try:
                await self._multimodal_enricher.close()
            except Exception as exc:
                log.debug("Multimodal enricher close failed: %s", exc)
            self._multimodal_enricher = None
        if self._multimodal_lmdb_env is not None:
            try:
                self._multimodal_lmdb_env.close()
            except Exception as exc:
                log.debug("Multimodal LMDB close failed: %s", exc)
            self._multimodal_lmdb_env = None

    # ── Sprint F205H: Metrics Registry ────────────────────────────────────

    async def _init_metrics_registry(self) -> None:
        """
        Initialize MetricsRegistry fail-soft using config export_dir or default path.

        No absolute paths outside paths.py. Run dir is derived from export_dir
        (if set) or ~/.hledac/runs (default fallback). Metrics file lives under
        run_dir/logs/metrics.jsonl.
        """
        try:
            from hledac.universal.metrics_registry import MetricsRegistry

            # Derive run_dir from config export_dir or use default
            export_dir = self._config.export_dir
            if export_dir:
                run_dir = Path(export_dir)
            else:
                run_dir = Path.home() / ".hledac" / "runs"
            run_dir.mkdir(parents=True, exist_ok=True)

            correlation = {
                "run_id": self.sprint_id or "default",
                "branch_id": None,
                "provider_id": None,
                "action_id": None,
            }
            self._metrics_registry = MetricsRegistry(
                run_dir=run_dir,
                run_id=self.sprint_id or "default",
                correlation=correlation,
            )
            self._metrics_initialized = True
            log.debug(f"[F205H] MetricsRegistry initialized: run_dir={run_dir}")
        except Exception as exc:
            self._metrics_registry = None
            self._metrics_initialized = False
            log.debug(f"[F205H] MetricsRegistry init failed (non-fatal): {exc}")

    def _tick_metrics_on_cycle_end(self) -> None:
        """
        Tick metrics at cycle completion — captures RSS, open FDs.

        Called once per cycle (not in tight loop). Fail-soft: noop if registry
        not initialized. No model load, no model inference.
        """
        if not self._metrics_initialized or self._metrics_registry is None:
            return
        try:
            self._metrics_registry.tick()
        except Exception:
            pass

    def _get_metrics_summary(self) -> dict | None:
        """
        Get metrics summary for sprint report embedding.

        Returns lightweight state snapshot: counters/gauges count,
        last_rss_mb, persist_available. Fail-soft: returns None if registry
        not initialized.
        """
        if not self._metrics_initialized or self._metrics_registry is None:
            return None
        try:
            summary = self._metrics_registry.get_summary()
            return {
                "counter_count": summary.get("counter_count", 0),
                "gauge_count": summary.get("gauge_count", 0),
                "last_rss_mb": summary.get("gauges", {}).get("memory_rss_mb", 0.0),
                "persist_available": summary.get("persist_available", False),
                "closed": summary.get("closed", False),
            }
        except Exception:
            return None

    async def _close_metrics_registry(self) -> None:
        """
        Close metrics registry at TEARDOWN — force flush prevents tail-loss.

        CancelledError is re-raised per GHOST_INVARIANTS.
        """
        if self._metrics_registry is None:
            return
        try:
            self._metrics_registry.close()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.debug(f"[F205H] MetricsRegistry close failed: {exc}")
        finally:
            self._metrics_registry = None

    async def _prewarm_hermes_for_sprint(self) -> None:
        """
        P12: Mode-aware Hermes prewarm policy.

        Aggressive mode: prewarm blocks until Hermes is loaded, unless RSS > 4GB
        (hard headroom rule — skip fail-soft, ToT is skipped for that run).

        Stable mode: current safe behavior via ModelManager memory guards
        (soft pressure clear + hard admission gate — no RSS 4GB pre-check).

        Bounded lifecycle: loaded once at BOOT/WARMUP, released at TEARDOWN.
        Fail-soft: memory pressure on load skips ToT, does not abort sprint.

        F203J: Quantization budget respected via QuantizationSelector advisory
        in ModelManager._load_model_async. Budget is logged here for visibility.
        """
        # P12 prewarm: RSS headroom check for aggressive mode
        # Hard headroom rule: if RSS > 4GB before prewarm, skip Hermes fail-soft
        RSS_PREWARM_HEADROOM_GB = 4.0
        if self._config.aggressive_mode:
            from hledac.universal.brain.model_manager import _get_current_rss_gb
            rss_before = _get_current_rss_gb()
            if rss_before > RSS_PREWARM_HEADROOM_GB:
                log.debug(
                    f"[P12] Skipping Hermes prewarm — RSS {rss_before:.2f}GB "
                    f"> {RSS_PREWARM_HEADROOM_GB}GB headroom threshold"
                )
                self._hermes_engine = None
                self._memory_manager = None
                return

        # F206AE: Lazy advisory gate — Hermes load is EXPENSIVE on M1 8GB and
        # the loaded engine is NOT used in canonical acquisition sprint.
        # Gate: HLEDAC_ENABLE_HERMES_SYNTHESIS=1 (default disabled)
        # If disabled, Hermes is NOT loaded; _hermes_engine stays None.
        # ModelManager remains load authority — gate is purely advisory skip.
        hermes_synthesis_enabled = os.environ.get("HLEDAC_ENABLE_HERMES_SYNTHESIS") == "1"
        hermes_load_skipped_reason = None
        if not hermes_synthesis_enabled:
            hermes_load_skipped_reason = "disabled_env"
            self._hermes_engine = None
            self._memory_manager = None
            log.debug(
                "[F206AE] Hermes load skipped — HLEDAC_ENABLE_HERMES_SYNTHESIS != '1', "
                f"reason={hermes_load_skipped_reason}"
            )
        else:
            # Shared load path via ModelManager (canonical lifecycle owner)
            # ModelManager enforces M1 8GB memory admission + F203J QuantizationSelector
            await self._load_hermes_for_sprint()

            # F203J: Advisory budget check after Hermes load — QuantizationSelector
            # result is logged for visibility; actual load authority stays in ModelManager
            # Only run when Hermes is actually being loaded (gate passed)
            try:
                from hledac.universal.core.resource_governor import sample_uma_status
                from hledac.universal.brain.quantization_selector import QuantizationSelector
                uma = sample_uma_status()
                selector = QuantizationSelector()
                budget = selector.select(uma, requested_model="hermes")
                log.debug(
                    f"[F203J] Hermes prewarm budget: quant={budget.quantization}, "
                    f"tokens={budget.max_tokens}, latency={budget.max_latency_ms}ms, "
                    f"reason={budget.reason}"
                )
            except Exception as e:
                log.debug("[F203J] QuantizationSelector prewarm advisory error: %s", e)

    async def _load_hermes_for_sprint(self) -> None:
        """
        P12: Load Hermes engine at sprint start via ModelManager (canonical lifecycle owner).

        Bounded lifecycle: loaded once at BOOT/WARMUP, released at TEARDOWN.
        Fail-soft: memory pressure on load skips ToT, does not abort sprint.

        M1 8GB invariant: ModelManager enforces bounded admission and RSS guards
        (hard fail-fast via _check_memory_admission + soft pressure via _check_memory_pressure).
        """
        from hledac.universal.brain.model_manager import get_model_manager

        # Load Hermes via ModelManager — handles mlx_lm.load internally
        # ModelManager enforces M1 8GB memory admission (hard fail-fast via _check_memory_admission)
        try:
            self._hermes_engine = await get_model_manager().load_model("hermes")
        except RuntimeError as e:
            # ModelManager raised — memory pressure, skip ToT gracefully
            log.debug(f"[P12] Skipping Hermes load — ModelManager blocked: {e}")
            self._hermes_engine = None
        except Exception as e:
            log.debug(f"[P12] Hermes load failed: {e}")
            self._hermes_engine = None

        # Initialize memory manager (session history for RAG context)
        try:
            from hledac.universal.memory.memory_manager import MemoryManager
            self._memory_manager = MemoryManager()
        except Exception as e:
            log.debug(f"[P12] MemoryManager init failed: {e}")
            self._memory_manager = None

    async def _unload_hermes_at_teardown(self) -> None:
        """
        P12: Unload Hermes engine at sprint teardown via ModelManager.

        Bounded lifecycle: loaded at BOOT/WARMUP, released at TEARDOWN.
        Uses ModelManager as canonical unload authority.
        """
        from hledac.universal.brain.model_manager import get_model_manager

        if self._hermes_engine is None:
            return

        try:
            await get_model_manager().release_model("hermes")
            log.debug("[P12] Hermes unloaded via ModelManager")
        except Exception as e:
            log.debug(f"[P12] Hermes unload failed: {e}")
        finally:
            self._hermes_engine = None

    def is_duplicate(self, source_type: str, url: str, title: str = "") -> bool:
        """Check if (source_type, url, title) was already seen in any sprint."""
        if self._dedup_env is None:
            return False
        key = xxhash.xxh64(f"{source_type}:{url}:{title}".encode()).hexdigest()
        return key in self._dedup_seen

    def mark_seen(self, source_type: str, url: str, title: str = "",
                  sprint_id: str = "") -> None:
        """Mark a finding as seen. Flush happens at WINDUP."""
        if self._dedup_env is None:
            return
        key = xxhash.xxh64(f"{source_type}:{url}:{title}".encode()).hexdigest()
        self._dedup_seen.add(key)
        self._dedup_dirty = True

    def request_early_windup(self) -> None:
        """Sprint 8RA: Request early wind-down (called from UMA CRITICAL callback)."""
        # Trigger lifecycle windup if available
        if hasattr(self, '_lifecycle') and self._lifecycle is not None:
            self._lifecycle.request_windup()
        else:
            # Fallback: set stop flag to exit at next cycle
            self._stop_requested = True

    def request_immediate_abort(self) -> None:
        """Sprint 8RA: Request immediate abort (called from UMA EMERGENCY callback)."""
        self._stop_requested = True
        self._result.aborted = True
        self._result.abort_reason = "uma_emergency"
        if hasattr(self, '_lifecycle') and self._lifecycle is not None:
            self._lifecycle.request_abort("uma_emergency")

    def is_new_entry(self, entry_hash: str) -> bool:
        """Return True if entry_hash has not been seen in this sprint."""
        if not entry_hash:
            return True  # empty hash = always new (backwards compat)
        if entry_hash in self._seen_hashes:
            self._result.duplicate_entry_hashes_skipped += 1
            return False
        self._seen_hashes[entry_hash] = True
        self._result.unique_entry_hashes_seen += 1
        return True

    # ── Lifecycle helpers ──────────────────────────────────────────────────

    async def _sleep_or_abort(self, seconds: float, adapter: _LifecycleAdapter) -> None:
        """
        Sleep in short chunks so wind-down can be detected promptly.
        Calls adapter.tick() during sleep to advance phase machine.
        """
        elapsed = 0.0
        step = min(seconds, 1.0)
        while elapsed < seconds:
            await asyncio.sleep(step)
            elapsed += step
            # Advance lifecycle phase machine via adapter
            adapter.tick()
            # Check abort frequently
            if adapter._abort_requested or adapter.is_terminal():
                return

    # ── Sprint F207Q-A: Pre-windup barrier result accessor for diagnostic report ─

    def _get_prewindup_barrier_report(self) -> dict | None:
        """
        Sprint F207Q-A: Read prewindup barrier telemetry for diagnostic report.

        Returns dict under acquisition_strategy.prewindup_barrier key.
        Fails soft: returns None if barrier was never checked.
        """
        if not getattr(self._result, "prewindup_barrier_checked", False):
            return None
        return {
            "required_lanes": list(getattr(self._result, "prewindup_barrier_required_lanes", ())),
            "satisfied": getattr(self._result, "prewindup_barrier_satisfied", False),
            "attempted_lanes": list(getattr(self._result, "prewindup_barrier_attempted_lanes", ())),
            "skipped_lanes": dict(getattr(self._result, "prewindup_barrier_skipped_lanes", {})),
            "errors": dict(getattr(self._result, "prewindup_barrier_errors", {})),
            "duration_s": round(getattr(self._result, "prewindup_barrier_duration_s", 0.0), 3),
            "windup_delayed": getattr(self._result, "windup_delayed_for_nonfeed", False),
        }

    def _final_phase(self, lifecycle) -> None:
        """Mark teardown on lifecycle."""
        # Sprint F206C: Delegated to runner.teardown()
        if hasattr(self, "_runner") and self._runner is not None:
            self._runner.teardown()
        else:
            self._final_phase_fallback(lifecycle)

    def _final_phase_fallback(self, lifecycle) -> None:
        """Fallback for direct calls to _final_phase (e.g. tests)."""
        try:
            from hledac.universal.runtime.sprint_lifecycle import SprintPhase
            phase = lifecycle.current_phase
            if phase == SprintPhase.WINDUP:
                lifecycle.mark_export_started()
                lifecycle.mark_teardown_started()
            elif phase not in (SprintPhase.EXPORT, SprintPhase.TEARDOWN):
                lifecycle.request_abort("scheduler_final_phase")
                lifecycle.mark_teardown_started()
        except Exception:
            pass  # teardown is best-effort

    # ── Partial Export (aggressive mode) ──────────────────────────────────

    async def _maybe_export_partial(self, lifecycle) -> None:
        """
        Write a partial JSON artifact if the findings interval has been reached.

        Called every cycle in aggressive mode.  Also callable on early windup
        or abort to ensure the latest partial survives.
        """
        if not self._config.aggressive_mode:
            return
        interval = self._config.partial_export_findings_interval
        if interval <= 0:
            return
        delta = self._finding_count - self._last_partial_finding_count
        if delta < interval:
            return

        # Build minimal handoff dict from current scheduler state
        try:
            from hledac.universal.export.sprint_exporter import export_partial_sprint

            runtime_truth = {
                "is_meaningful": self._finding_count > 0,
                "accepted_findings": self._finding_count,
                "cycles_completed": self._result.cycles_completed,
                "aggressive_mode": True,
            }
            scorecard = {
                "cycles_started": self._result.cycles_started,
                "cycles_completed": self._result.cycles_completed,
                "total_pattern_hits": self._result.total_pattern_hits,
            }
            handoff_dict = {
                "sprint_id": self.sprint_id or "unknown",
                "runtime_truth": runtime_truth,
                "scorecard": scorecard,
            }

            await export_partial_sprint(
                store=self._duckdb_store,
                handoff=handoff_dict,
                sprint_id=self.sprint_id or "unknown",
                finding_count=self._finding_count,
            )
            self._last_partial_finding_count = self._finding_count
            log.debug(
                f"[PARTIAL-EXPORT] triggered at finding_count={self._finding_count}"
            )
        except Exception as ex:
            log.warning(f"[PARTIAL-EXPORT] _maybe_export_partial failed (non-fatal): {ex}")

    # ── Export ────────────────────────────────────────────────────────────

    async def _run_export(self, lifecycle) -> None:
        """Run all four exporters; failure is fail-soft."""
        (
            rend_md,
            rend_jsonld,
            rend_stix,
            rend_cti_stix,
            collect_cti_inputs,
        ) = _import_exporters()

        # Build minimal diagnostic report from result
        report = await self._build_diagnostic_report(lifecycle)

        export_dir = self._config.export_dir

        for render_fn, suffix in [
            (rend_md, "md"),
            (rend_jsonld, "jsonld"),
            (rend_stix, "stix.json"),
        ]:
            try:
                path = render_fn(report, export_dir or None)
                self._result.export_paths.append(str(path))
            except Exception as exc:
                # Fail-soft: export error must not prevent teardown
                # but we still record it
                self._result.export_paths.append(f"EXPORT_ERROR:{suffix}:{exc}")

        # Sprint F204F: CTI STIX export — wired alongside diagnostic STIX
        await self._run_cti_export(rend_cti_stix, collect_cti_inputs, report, export_dir)

    async def _run_cti_export(
        self,
        render_cti_stix_to_path: Any,
        collect_cti_inputs: Any,
        report: dict[str, Any],
        export_dir: str | None,
    ) -> None:
        """
        Sprint F204F: Run CTI STIX export with fail-soft error handling.

        GHOST_INVARIANTS:
        - asyncio.gather with return_exceptions=True
        - _check_gathered() after gather
        - asyncio.CancelledError re-raise
        - Large serialization (>1000 objects) via run_in_executor
        - RAM guard: MAX_STIX_OBJECTS=500
        - Fail-soft: EXPORT_ERROR logged, not raised
        """
        try:
            cti_inputs = await collect_cti_inputs(report, self._duckdb_store)
        except Exception as exc:
            self._result.export_paths.append(f"EXPORT_ERROR:cti_stix:{exc}")
            return

        try:
            # Large serialization via run_in_executor if findings exceed 1000
            if len(cti_inputs.findings) > 1000:
                loop = asyncio.get_running_loop()
                path = await loop.run_in_executor(
                    None,
                    lambda: render_cti_stix_to_path(
                        findings=list(cti_inputs.findings),
                        identity_candidates=list(cti_inputs.identity_candidates),
                        attribution_scores=cti_inputs.attribution_scores,
                        killchain_tags=cti_inputs.killchain_tags,
                        evidence_chains=list(cti_inputs.evidence_chains),
                        path=export_dir,
                    ),
                )
            else:
                path = render_cti_stix_to_path(
                    findings=list(cti_inputs.findings),
                    identity_candidates=list(cti_inputs.identity_candidates),
                    attribution_scores=cti_inputs.attribution_scores,
                    killchain_tags=cti_inputs.killchain_tags,
                    evidence_chains=list(cti_inputs.evidence_chains),
                    path=export_dir,
                )
            self._result.export_paths.append(str(path))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._result.export_paths.append(f"EXPORT_ERROR:cti_stix:{exc}")

    # ── F208G-A: PUBLIC Yield Taxonomy — stage counters builder ────────────────

    def _build_public_stage_counters(self) -> dict:
        """
        F208G-A: Build public_stage_counters dict from _public_pipeline_result.

        This aggregates all F208G-A public_* telemetry fields from the stored
        PipelineRunResult into a single dict for propagation to acquisition_report
        and source_family_outcomes.

        Returns an empty dict if _public_pipeline_result is None (PUBLIC skipped).
        """
        if self._public_pipeline_result is None:
            return {}
        pr = self._public_pipeline_result
        return {
            # Discovery/fetch overview
            "public_discovered": getattr(pr, 'public_discovered', 0) or 0,
            "public_fetch_candidate_count": getattr(pr, 'public_fetch_candidate_count', 0) or 0,
            "public_fetch_attempted": getattr(pr, 'public_fetch_attempted', 0) or 0,
            "public_fetch_success": getattr(pr, 'public_fetch_success', 0) or 0,
            "public_fetch_failed": getattr(pr, 'public_fetch_failed', 0) or 0,
            # Acceptance/rejection
            "public_acceptance_attempted": getattr(pr, 'public_acceptance_attempted', 0) or 0,
            "public_acceptance_accepted": getattr(pr, 'public_acceptance_accepted', 0) or 0,
            "public_acceptance_rejected": getattr(pr, 'public_acceptance_rejected', 0) or 0,
            "public_acceptance_reject_reasons": getattr(pr, 'public_acceptance_reject_reasons', {}) or {},
            # Terminal classification
            "public_terminal_classified_count": getattr(pr, 'public_terminal_classified_count', 0) or 0,
            "public_unclassified_count": getattr(pr, 'public_unclassified_count', 0) or 0,
            "public_terminal_reason_counts": getattr(pr, 'public_terminal_reason_counts', {}) or {},
            # Skipped reason breakdown
            "public_skipped_duplicate": getattr(pr, 'public_skipped_duplicate', 0) or 0,
            "public_skipped_unsupported_scheme": getattr(pr, 'public_skipped_unsupported_scheme', 0) or 0,
            "public_skipped_memory_gate": getattr(pr, 'public_skipped_memory_gate', 0) or 0,
            "public_skipped_quality_gate": getattr(pr, 'public_skipped_quality_gate', 0) or 0,
            "public_skipped_browser_unavailable": getattr(pr, 'public_skipped_browser_unavailable', 0) or 0,
            "public_skipped_xml_or_feed": getattr(pr, 'public_skipped_xml_or_feed', 0) or 0,
            "public_skipped_timeout": getattr(pr, 'public_skipped_timeout', 0) or 0,
            "public_skipped_fetch_error": getattr(pr, 'public_skipped_fetch_error', 0) or 0,
            "public_skipped_url_sample": getattr(pr, 'public_skipped_url_sample', ()) or (),
            # Rejected reason breakdown
            "public_rejected_no_pattern_match": getattr(pr, 'public_rejected_no_pattern_match', 0) or 0,
            "public_rejected_low_information": getattr(pr, 'public_rejected_low_information', 0) or 0,
            "public_rejected_duplicate": getattr(pr, 'public_rejected_duplicate', 0) or 0,
            "public_rejected_storage_rejected": getattr(pr, 'public_rejected_storage_rejected', 0) or 0,
            "public_rejected_url_samples": getattr(pr, 'public_rejected_url_samples', ()) or (),
            # Accepted samples
            "public_accepted_url_sample": getattr(pr, 'public_accepted_url_sample', ()) or (),
        }

    async def _build_diagnostic_report(self, lifecycle) -> dict:
        """Build a diagnostic report dict for exporters."""
        # Sprint F350D: Use truthful sprint_id — NOT synthetic time-based run_id.
        # sprint_id is set during run() from lifecycle.sprint_id attribute.
        run_id = self.sprint_id or f"8bk_sprint_{int(_time.time())}"
        report = {
            "run_id": run_id,
            "phase": lifecycle.current_phase.name,
            "cycles_started": self._result.cycles_started,
            "cycles_completed": self._result.cycles_completed,
            "unique_entry_hashes": self._result.unique_entry_hashes_seen,
            "duplicates_skipped": self._result.duplicate_entry_hashes_skipped,
            "pattern_hits": self._result.total_pattern_hits,
            "accepted_findings": self._result.accepted_findings,
            "aborted": self._result.aborted,
            "abort_reason": self._result.abort_reason,
            "stop_requested": self._result.stop_requested,
            "lifecycle_snapshot": lifecycle.snapshot(),
            "entries_per_source": dict(self._entries_per_source),
            "hits_per_source": dict(self._hits_per_source),
        }
        # Sprint F205H: Append metrics registry summary (read-only, fail-soft)
        metrics_summary = self._get_metrics_summary()
        if metrics_summary:
            report["metrics_registry"] = metrics_summary
        # Sprint F198A: Append cross-sprint graph signal (read-only, non-blocking)
        graph_signal = self._get_graph_signal()
        if graph_signal:
            report["graph_signal"] = graph_signal
        # Sprint 8VM: Append shadow pre-decision readiness preview (read-only, diagnostic)
        shadow_preview = self._build_shadow_readiness_preview()
        if shadow_preview:
            report["shadow_pre_decision"] = shadow_preview
        # Sprint 8VN: Embed correlation + hypothesis intelligence into report
        intel = self.compute_sprint_intelligence()
        if intel.get("correlation"):
            report["correlation_summary"] = intel["correlation"]
        if intel.get("hypothesis_pack"):
            report["hypothesis_pack_summary"] = intel["hypothesis_pack"]
        if intel.get("branch_value"):
            report["branch_value"] = intel["branch_value"]
        # Sprint F206E: Append windup scorecard (read-only, from dormant windup_engine donor)
        windup_scorecard = self._get_windup_scorecard()
        if windup_scorecard:
            report["windup_scorecard"] = windup_scorecard
        # Sprint F206I: Append source health summary (read-only, from per-sprint economics)
        source_health = self._get_source_health_summary()
        if source_health:
            report["source_health_summary"] = source_health
        # Sprint F206I: Append circuit breaker coverage summary (read-only, from transport registry)
        circuit_state = self._get_circuit_breaker_summary()
        if circuit_state:
            report["circuit_breaker_state"] = circuit_state
        # Sprint F206BG: Append acquisition strategy snapshot (read-only, built at sprint start)
        # Sprint F206BK: Add enforcement fields
        if self._acquisition_plan is not None:
            _lanes_list = [
                {
                    "lane": p.lane,
                    "enabled": p.enabled,
                    "reason": p.reason,
                    "max_items": p.max_items,
                    "timeout_s": p.timeout_s,
                    "concurrency": p.concurrency,
                    "risk_level": p.risk_level,
                }
                for p in self._acquisition_plan.plans
            ]
            _skipped = [
                p.lane for p in self._acquisition_plan.plans if not p.enabled
            ]
            _executed = [p.lane for p in self._acquisition_plan.plans if p.enabled]
            report["acquisition_strategy"] = {
                "uma_state": self._acquisition_plan.uma_state,
                "swap_detected": self._acquisition_plan.swap_detected,
                "aggressive_mode": self._acquisition_plan.aggressive_mode,
                "stealth_ready": self._acquisition_plan.stealth_ready,
                "transport_degraded": self._acquisition_plan.transport_degraded,
                "enforced": True,
                "skipped_lanes": _skipped,
                "executed_lanes": _executed,
                "lanes": _lanes_list,
                # [F207L] Nonfeed lane planning debug snapshot for KPI diagnosis
                "nonfeed_plan_debug": (
                    {
                        "domain_detected": self._acquisition_plan.nonfeed_plan_debug.domain_detected,
                        "wallet_detected": self._acquisition_plan.nonfeed_plan_debug.wallet_detected,
                        "enabled_nonfeed_lanes": list(self._acquisition_plan.nonfeed_plan_debug.enabled_nonfeed_lanes),
                        "disabled_nonfeed_lanes": list(self._acquisition_plan.nonfeed_plan_debug.disabled_nonfeed_lanes),
                        "disabled_reasons": list(self._acquisition_plan.nonfeed_plan_debug.disabled_reasons),
                        "scheduled_nonfeed_lanes": list(self._acquisition_plan.nonfeed_plan_debug.scheduled_nonfeed_lanes),
                        "hardware_skipped_lanes": list(self._acquisition_plan.nonfeed_plan_debug.hardware_skipped_lanes),
                        "nonfeed_execution_scheduled": self._acquisition_plan.nonfeed_plan_debug.nonfeed_execution_scheduled,
                        "nonfeed_execution_skip_reason": self._acquisition_plan.nonfeed_plan_debug.nonfeed_execution_skip_reason,
                    }
                    if self._acquisition_plan.nonfeed_plan_debug is not None
                    else None
                ),
                # [F207Q-A] Pre-windup barrier telemetry for live KPI diagnosis
                "prewindup_barrier": self._get_prewindup_barrier_report() if hasattr(self, "_get_prewindup_barrier_report") else None,
            }
        # Sprint F208H: Surface terminality fields at TOP LEVEL so validator can find them
        report["acquisition_terminality_checked"] = getattr(
            self._result, "acquisition_terminality_checked", False
        )
        report["acquisition_terminality_satisfied"] = getattr(
            self._result, "acquisition_terminality_satisfied", False
        )
        report["acquisition_terminality_missing_lanes"] = list(
            getattr(self._result, "acquisition_terminality_missing_lanes", ()) or ()
        )
        # Sprint F207S-A: Windup guard callsite observation telemetry (top-level key)
        # F208M-B: callback_not_executed_reason added
        report["windup_guard_observation"] = {
            "call_count": getattr(self._result, "windup_guard_call_count", 0),
            "callback_supplied_count": getattr(self._result, "windup_guard_callback_supplied_count", 0),
            "callback_executed_count": getattr(self._result, "windup_guard_callback_executed_count", 0),
            "last_reason": getattr(self._result, "windup_guard_last_reason", ""),
            "last_phase": getattr(self._result, "windup_guard_last_phase", ""),
            "last_allowed": getattr(self._result, "windup_guard_last_allowed", None),
            "callback_not_executed_reason": getattr(self._result, "windup_guard_last_callback_not_executed_reason", ""),
        }
        # Sprint F207T-A: Return guard telemetry for mandatory nonfeed terminal state
        report["return_guard"] = {
            "checked": getattr(self._result, "return_guard_checked", False),
            "required_lanes": list(getattr(self._result, "return_guard_required_lanes", ())),
            "satisfied": getattr(self._result, "return_guard_satisfied", False),
            "delayed_for_nonfeed": getattr(self._result, "return_guard_delayed_for_nonfeed", False),
            "block_reason": getattr(self._result, "return_guard_block_reason", ""),
            "attempted_lanes": list(getattr(self._result, "return_guard_attempted_lanes", ())),
            "skipped_lanes": dict(getattr(self._result, "return_guard_skipped_lanes", {})),
            "errors": dict(getattr(self._result, "return_guard_errors", {})),
        }
        # Sprint F207V-A: Scheduler exit path tracer
        report["scheduler_exit"] = {
            "exit_path": getattr(self._result, "scheduler_exit_path", None),
            "exit_reason": getattr(self._result, "scheduler_exit_reason", None),
            "exit_phase": getattr(self._result, "scheduler_exit_phase", None),
            "exit_cycle": getattr(self._result, "scheduler_exit_cycle", None),
            "elapsed_s": getattr(self._result, "scheduler_exit_elapsed_s", None),
            "guard_checked": getattr(self._result, "scheduler_exit_guard_checked", False),
            "guard_required": list(getattr(self._result, "scheduler_exit_guard_required", ())),
            "guard_satisfied": getattr(self._result, "scheduler_exit_guard_satisfied", None),
        }
        # Sprint F216E: Feed dominance budget telemetry
        report["feed_dominance_budget"] = {
            "active": getattr(self._result, "feed_budget_active", False),
            "reason": getattr(self._result, "feed_budget_reason", ""),
            "feed_accepted_before_cap": getattr(self._result, "feed_accepted_before_cap", 0),
            "feed_suppressed_by_budget": getattr(self._result, "feed_suppressed_by_budget", 0),
            "top_feed_source_counts": list(getattr(self._result, "top_feed_source_counts", ()) or ()),
            "max_per_source_applied": getattr(self._result, "max_per_source_applied", ""),
        }
        # Sprint F208B: Acquisition terminality consumer report
        # Merge terminality into the existing acquisition_strategy block
        _term_rep = getattr(self._result, "acquisition_terminality_report", {}) or {}
        if "acquisition_strategy" in report:
            report["acquisition_strategy"]["terminality"] = _term_rep
            # F208F: also surface individual terminality fields so live_sprint_measurement can find them
            report["acquisition_strategy"]["acquisition_terminality_checked"] = getattr(
                self._result, "acquisition_terminality_checked", False
            )
            report["acquisition_strategy"]["acquisition_terminality_satisfied"] = getattr(
                self._result, "acquisition_terminality_satisfied", False
            )
            report["acquisition_strategy"]["acquisition_terminality_missing_lanes"] = list(
                getattr(self._result, "acquisition_terminality_missing_lanes", ()) or ()
            )
        else:
            report["acquisition_strategy"] = {
                "terminality": _term_rep,
                "acquisition_terminality_checked": getattr(self._result, "acquisition_terminality_checked", False),
                "acquisition_terminality_satisfied": getattr(self._result, "acquisition_terminality_satisfied", False),
                "acquisition_terminality_missing_lanes": list(getattr(self._result, "acquisition_terminality_missing_lanes", ()) or ()),
            }
        # Sprint F207A: Append multi-source acquisition lane outcomes
        if self._lane_outcomes:
            _outcomes_list = [o.to_dict() if hasattr(o, "to_dict") else dict(o) for o in self._lane_outcomes]
            _planned = [p.lane for p in (self._acquisition_plan.plans if self._acquisition_plan else [])]
            _attempted = [o["lane"] for o in _outcomes_list if o.get("attempted")]
            _skipped_lanes = [l for l in _planned if l not in _attempted and l not in (_executed if self._acquisition_plan else [])]
            _lane_errors = [o["error"] for o in _outcomes_list if o.get("error")]
            report["acquisition_lanes"] = {
                "planned": _planned,
                "attempted": _attempted,
                "skipped": _skipped_lanes,
                "outcomes": _outcomes_list,
                "total_optional_findings": sum(o.get("accepted_findings", 0) for o in _outcomes_list),
                "lane_errors": _lane_errors,
            }
        # Sprint F207G: Canonical per-source-family outcome breakdown
        # Unifies CT, Wayback, PassiveDNS, feed balance into one explainable section
        _sfo: dict[str, dict] = {}
        for _fam, _lane in [
            ("ct", AcquisitionLane.CT),
            ("wayback", AcquisitionLane.WAYBACK),
            ("passive_dns", AcquisitionLane.PASSIVE_DNS),
            ("blockchain", AcquisitionLane.BLOCKCHAIN),
            ("feed", "FEED"),
            ("public", AcquisitionLane.PUBLIC),
        ]:
            _raw: dict | None = None
            if _lane == "FEED":
                _raw = getattr(self, "_feed_verdicts", []) or None
                # F215E: FEED accepted_count must come from result-level counts, not _feed_verdicts
                # (verdict tuples carry signal/quality, not accepted_count).
                # Net FEED = total - public - CT_log
                if _raw is not None:
                    _feed_accepted = (
                        (self._result.accepted_findings or 0)
                        - (self._result.public_accepted_findings or 0)
                        - (self._result.ct_log_accepted_findings or 0)
                    )
                    _feed_accepted = max(0, _feed_accepted)
                    if isinstance(_raw, list) and len(_raw) > 0:
                        # Attach accepted_count to first verdict for normalize_source_family_outcome
                        # Pass as a dict so normalize can read accepted_count and raw_count
                        _first = _raw[0]
                        if isinstance(_first, tuple):
                            _raw = {
                                "verdict": _first,
                                "accepted_count": _feed_accepted,
                                "raw_count": _first[1] if len(_first) > 1 else 0,
                                "attempted": True,
                            }
            elif _lane == AcquisitionLane.PUBLIC:
                # Sprint F207H: Consume public pipeline outcome directly
                _raw = getattr(self, "_public_outcome", None)
            elif self._lane_outcomes:
                for _o in self._lane_outcomes:
                    if hasattr(_o, "lane") and _o.lane == _lane:
                        _raw = _o
                        break
            _sfo[_fam] = normalize_source_family_outcome(_fam, _raw)
        # Academic is always skipped unless explicitly enabled
        _sfo["academic"] = normalize_source_family_outcome("academic", None)
        report["source_family_outcomes"] = _sfo

        # Sprint F210A: Terminality SSOT — recompute from canonical source family outcomes.
        # _finalize_result_truth() is called BEFORE all nonfeed lanes complete, so its
        # terminality snapshot can be stale: CT/PUBLIC appear in source_family_outcomes
        # as attempted but still appear in missing_lanes from the early snapshot.
        # This fix recomputes terminality from the EXACT same outcomes used for
        # source_family_outcomes so the two can never disagree.
        try:
            _term_uma = "ok"
            _term_swap = False
            if self._governor is not None:
                try:
                    _snap = await self._governor.evaluate()
                    _term_uma = getattr(_snap, "uma_state", "ok")
                    _term_swap = getattr(_snap, "swap_detected", False)
                except Exception:
                    pass
            _mlt = required_terminal_lanes(
                snapshot=self._acquisition_plan,
                query=self._query,
                uma_state=_term_uma,
                swap_detected=_term_swap,
            )
            _canonical_outcomes = self._final_source_family_outcomes_for_terminality()
            _fresh_term = terminality_report(
                required_lanes=_mlt,
                observed_outcomes=_canonical_outcomes,
            )
            # Update acquisition_strategy terminality with the fresh computation
            if "acquisition_strategy" in report:
                report["acquisition_strategy"]["terminality"] = _fresh_term
                report["acquisition_strategy"]["acquisition_terminality_checked"] = True
                report["acquisition_strategy"]["acquisition_terminality_satisfied"] = (
                    len(_fresh_term.get("missing_lanes", [])) == 0
                )
                report["acquisition_strategy"]["acquisition_terminality_missing_lanes"] = list(
                    _fresh_term.get("missing_lanes", [])
                )
            # Also update top-level terminality fields (used by validators)
            report["acquisition_terminality_checked"] = True
            report["acquisition_terminality_satisfied"] = (
                len(_fresh_term.get("missing_lanes", [])) == 0
            )
            report["acquisition_terminality_missing_lanes"] = list(
                _fresh_term.get("missing_lanes", [])
            )
        except Exception:
            # Fail-safe: if terminality recomputation fails, keep the already-set values
            # (they were set by _finalize_result_truth before this report build)
            pass

        # Sprint F208H/F215C: Surface terminality and guard fields at TOP LEVEL for validator consumption.
        # PUBLIC terminal state (active300 domain queries).
        # F215C: normalize_source_family_outcome now always derives terminal_state (including
        # NEVER_SCHEDULED when _public_outcome was never set), so the fallback correctly
        # represents the "never in plan" case.
        report["public_terminal_state"] = (
            _sfo.get("public", {}).get("terminal_state") or "NEVER_SCHEDULED"
        )
        # CT terminal state (active300 domain queries)
        report["ct_terminal_state"] = (
            _sfo.get("ct", {}).get("terminal_state") or "NEVER_ATTEMPTED"
        )
        # Scheduler exit path at top level (validator check)
        report["scheduler_exit_path"] = getattr(self._result, "scheduler_exit_path", None)
        # Return guard checked at top level (validator check)
        report["return_guard_checked"] = getattr(self._result, "return_guard_checked", False)
        # Windup guard fields at top level (validator check)
        _wg_last_reason = getattr(self._result, "windup_guard_last_reason", "") or ""
        _wg_irrelevant = frozenset({"not_applicable", "no_lanes_ran", "disabled", "skipped"})
        report["windup_guard_call_count"] = getattr(self._result, "windup_guard_call_count", 0)
        report["windup_guard_reason"] = _wg_last_reason
        report["windup_guard_not_applicable"] = (
            _wg_last_reason.lower() in _wg_irrelevant
        )

        # Sprint F208F: Canonical acquisition_report — wired using build_acquisition_report()
        # so live_sprint_measurement.py can find it at report["acquisition_report"] first
        try:
            from hledac.universal.runtime.acquisition_strategy import (
                build_acquisition_report,
            )
            # Build windup guard observation dict
            # F208M-B: callback_not_executed_reason added
            _wg_obs = {
                "call_count": getattr(self._result, "windup_guard_call_count", 0),
                "callback_supplied_count": getattr(self._result, "windup_guard_callback_supplied_count", 0),
                "callback_executed_count": getattr(self._result, "windup_guard_callback_executed_count", 0),
                "last_reason": getattr(self._result, "windup_guard_last_reason", ""),
                "last_phase": getattr(self._result, "windup_guard_last_phase", ""),
                "last_allowed": getattr(self._result, "windup_guard_last_allowed", None),
                "callback_not_executed_reason": getattr(self._result, "windup_guard_last_callback_not_executed_reason", ""),
            }
            # Build return guard dict
            _rg_dict = {
                "checked": getattr(self._result, "return_guard_checked", False),
                "required_lanes": list(getattr(self._result, "return_guard_required_lanes", ())),
                "satisfied": getattr(self._result, "return_guard_satisfied", False),
                "delayed_for_nonfeed": getattr(self._result, "return_guard_delayed_for_nonfeed", False),
                "block_reason": getattr(self._result, "return_guard_block_reason", ""),
                "attempted_lanes": list(getattr(self._result, "return_guard_attempted_lanes", ())),
                "skipped_lanes": dict(getattr(self._result, "return_guard_skipped_lanes", {})),
                "errors": dict(getattr(self._result, "return_guard_errors", {})),
            }
            # Build prewindup_barrier dict
            _pwb = self._get_prewindup_barrier_report() if hasattr(self, "_get_prewindup_barrier_report") else None
            # Build scheduler_exit dict (same as report["scheduler_exit"])
            _se_dict = {
                "exit_path": getattr(self._result, "scheduler_exit_path", None),
                "exit_reason": getattr(self._result, "scheduler_exit_reason", None),
                "exit_phase": getattr(self._result, "scheduler_exit_phase", None),
                "exit_cycle": getattr(self._result, "scheduler_exit_cycle", None),
                "elapsed_s": getattr(self._result, "scheduler_exit_elapsed_s", None),
                "guard_checked": getattr(self._result, "scheduler_exit_guard_checked", False),
                "guard_required": list(getattr(self._result, "scheduler_exit_guard_required", ())),
                "guard_satisfied": getattr(self._result, "scheduler_exit_guard_satisfied", None),
            }
            # Build terminality dict
            _term_rep = getattr(self._result, "acquisition_terminality_report", {}) or {}
            # Build nonfeed_plan_debug
            _nd = None
            if self._acquisition_plan is not None and self._acquisition_plan.nonfeed_plan_debug is not None:
                nd = self._acquisition_plan.nonfeed_plan_debug
                _nd = {
                    "domain_detected": nd.domain_detected,
                    "wallet_detected": nd.wallet_detected,
                    "enabled_nonfeed_lanes": list(nd.enabled_nonfeed_lanes),
                    "disabled_nonfeed_lanes": list(nd.disabled_nonfeed_lanes),
                    "disabled_reasons": list(nd.disabled_reasons),
                    "scheduled_nonfeed_lanes": list(nd.scheduled_nonfeed_lanes),
                    "hardware_skipped_lanes": list(nd.hardware_skipped_lanes),
                    "nonfeed_execution_scheduled": nd.nonfeed_execution_scheduled,
                    "nonfeed_execution_skip_reason": nd.nonfeed_execution_skip_reason,
                    # F216B: Nonfeed diagnostic profile telemetry
                    "acquisition_profile": getattr(nd, "acquisition_profile", "default"),
                    "feed_cap_reason": getattr(nd, "feed_cap_reason", None),
                    "nonfeed_priority_enabled": getattr(nd, "nonfeed_priority_enabled", False),
                    "nonfeed_profile_expected_lanes": list(getattr(nd, "nonfeed_profile_expected_lanes", ()) or ()),
                    # F216F: Pivot executor telemetry
                    "pivot_executor_enabled": getattr(nd, "pivot_executor_enabled", False),
                    "pivot_candidates_count": getattr(nd, "pivot_candidates_count", 0),
                    "pivot_candidate_types": list(getattr(nd, "pivot_candidate_types", ()) or ()),
                    "pivot_scheduled_lanes": list(getattr(nd, "pivot_scheduled_lanes", ()) or ()),
                    "pivot_skip_reason": getattr(nd, "pivot_skip_reason", None),
                    "pivot_errors": list(getattr(nd, "pivot_errors", ()) or ()),
                    # F225A: Mission intent telemetry
                    "mission_intent": getattr(nd, "mission_intent", "unknown"),
                    "mission_target_kind": getattr(nd, "mission_target_kind", "unknown"),
                    "mission_required_lanes": list(getattr(nd, "mission_required_lanes", ()) or ()),
                    "mission_optional_lanes": list(getattr(nd, "mission_optional_lanes", ()) or ()),
                    "mission_reason": getattr(nd, "mission_reason", ""),
                    # F226A: Mission runtime wiring
                    "mission_runtime_applied": getattr(nd, "mission_runtime_applied", False),
                    "mission_lane_priority": list(getattr(nd, "mission_lane_priority", ()) or ()),
                    "mission_pivot_boost_applied": getattr(nd, "mission_pivot_boost_applied", False),
                    # F227D: Mission-aware feed cap telemetry
                    "mission_feed_cap_reason": getattr(nd, "mission_feed_cap_reason", None),
                    "feed_cap_applied_by_mission": getattr(nd, "feed_cap_applied_by_mission", False),
                    "feed_cap_mission_intent": getattr(nd, "feed_cap_mission_intent", None),
                }
            # source_family_outcomes as list of dicts (normalize_source_family_outcome returns dict)
            _sfo_list = list(_sfo.values())

            # F228C: Compute nonfeed surface completeness telemetry from _sfo dict
            # _sfo is keyed by family name (wayback, passive_dns, etc.)
            # nonfeed_profile_expected_lanes uses UPPERCASE lane constants (CT, WAYBACK, etc.)
            # _sfo entries use lowercase family names (ct, wayback, etc.)
            _sfo_by_family = {entry.get("family", ""): entry for entry in _sfo_list}
            _expected = list(_nd.get("nonfeed_profile_expected_lanes", ()) or []) if _nd else (nonfeed_expected_lanes or [])
            # Map uppercase lane constants to lowercase family names for comparison
            _FAM_MAP = {
                "CT": "ct", "WAYBACK": "wayback", "PASSIVE_DNS": "passive_dns",
                "BLOCKCHAIN": "blockchain", "PUBLIC": "public",
                "PIVOT_EXECUTOR": "pivot_executor",
            }
            _surf_wayback = _sfo_by_family.get("wayback", {})
            _surf_pdns = _sfo_by_family.get("passive_dns", {})
            _surf_ct = _sfo_by_family.get("ct", {})
            _surf_public = _sfo_by_family.get("public", {})
            # A lane is "surfaced" if it was attempted OR explicitly skipped (not absent)
            _surfaced_lower = {
                _FAM_MAP.get(fam.upper(), fam.lower())
                for fam, entry in _sfo_by_family.items()
                if entry.get("attempted") or entry.get("skipped")
            }
            _missing = [e for e in _expected if _FAM_MAP.get(e, e.lower()) not in _surfaced_lower]
            _surf_complete = len(_missing) == 0

            # Sprint F229A: Extract bootstrap ordering telemetry from public outcome
            _pub_bootstrap_order = _surf_public.get("public_bootstrap_order", "disabled")
            _pub_bootstrap_prevented = _surf_public.get("public_bootstrap_prevented_discovery_timeout", False)
            _pub_bootstrap_fetch_att = _surf_public.get("public_bootstrap_first_fetch_attempted", False)

            report["acquisition_report"] = build_acquisition_report(
                plan=self._acquisition_plan,
                terminality=_term_rep,
                nonfeed_plan_debug=_nd,
                source_family_outcomes=_sfo_list,
                return_guard=_rg_dict,
                prewindup_barrier=_pwb,
                scheduler_exit=_se_dict,
                windup_guard_observation=_wg_obs,
                # F223A: Pass explicit acquisition_profile from config
                acquisition_profile=(
                    self._config.acquisition_profile
                    if self._config.acquisition_profile is not None
                    else "default"
                ),
                # F228C: Nonfeed surface completeness telemetry
                nonfeed_expected_lanes=_expected,
                nonfeed_missing_expected_lanes=_missing,
                wayback_terminal_state=_surf_wayback.get("terminal_state", ""),
                passive_dns_terminal_state=_surf_pdns.get("terminal_state", ""),
                nonfeed_surface_complete=_surf_complete,
                # Sprint F229A: Bootstrap ordering telemetry
                public_bootstrap_order=_pub_bootstrap_order,
                public_bootstrap_prevented_discovery_timeout=_pub_bootstrap_prevented,
                public_bootstrap_first_fetch_attempted=_pub_bootstrap_fetch_att,
                # F208G-A: PUBLIC Yield Taxonomy — stage counters for acquisition_report
                public_stage_counters=self._build_public_stage_counters(),
            )
        except Exception:
            pass  # fail-soft: acquisition_report is diagnostic only

        return report

    # ── Sprint 8RC: IOC-aware prioritisation ───────────────────────────────

    # Base tier weights (B.1 invariant)
    _BASE_TIER_WEIGHTS: dict[str, float] = {
        "structured_ti": 1.0,
        "clearnet": 0.8,
        "academic": 0.6,
        "dark": 1.2,
    }

    async def load_source_weights(self, store: Any) -> None:
        """
        Load hit-rate history from DuckDB and set source weights.

        Bounds: 0.3 – 2.5 (30% floor, 250% ceiling, B.6).
        Falls back to defaults on any error.
        """
        try:
            rows = await store.async_query_sprint_source_stats()
            if not rows:
                return
            max_rate = max(r["avg_hit_rate"] for r in rows) or 1.0
            for row in rows:
                src = row["source_type"]
                raw = row["avg_hit_rate"] / max_rate * 1.5
                # B.6: ±20% per sprint cap → clamp to [0.3, 2.5]
                clipped = max(0.3, min(2.5, raw))
                self._source_weights[src] = clipped
                log.debug(f"Source weight {src}: {clipped:.2f}")
        except Exception as e:
            log.warning(f"Source weight load failed: {e} — using defaults")

    # ── Sprint F199A: Reward-driven source weight adaptation ─────────────

    def _adapt_source_weights_from_feedback(self) -> None:
        """
        F199A: Adapt _source_weights from per-source quality feedback collected during the sprint.

        Called at teardown (in run() after cycles complete). Updates each feed_url's weight
        based on accepted/total ratio signal collected via _process_result().

        Adaptation rule (B.6 bounds ±20% per sprint → clamp to [0.3, 2.5]):
          - accepted/total >= 0.7 → reward: +10%
          - accepted/total >= 0.4 → reward: +5%
          - accepted/total >= 0.15 → reward: 0 (neutral)
          - accepted/total < 0.15 → penalty: -5%
          - no signal (total=0) → no change

        Signal is per-feed_url (feed_url as key), not per-source_type.
        For scoring, feed_url maps to source_type via _config.tier_of(feed_url).name.
        """
        for feed_url, fb in self._source_quality_feedback.items():
            total = fb.get("fetched", 0)
            accepted = fb.get("accepted", 0)
            if total == 0:
                continue

            ratio = accepted / total
            # Derive source_type from feed_url via tier config
            source_type = self._config.tier_of(feed_url).name.lower()

            current = self._source_weights.get(source_type, 1.0)
            if ratio >= 0.7:
                delta = 1.10  # +10%
            elif ratio >= 0.4:
                delta = 1.05  # +5%
            elif ratio >= 0.15:
                delta = 1.00  # neutral
            else:
                delta = 0.95  # -5%

            new_weight = current * delta
            # B.6: clamp to [0.3, 2.5]
            new_weight = max(0.3, min(2.5, new_weight))
            self._source_weights[source_type] = new_weight
            log.debug(
                f"[F199A] Source weight adaptation: {source_type} "
                f"({accepted}/{total}={ratio:.2%}) {current:.3f} → {new_weight:.3f}"
            )

    def score_source(
        self, source_type: str, ioc_graph_stats: dict | None = None
    ) -> float:
        """
        Compute priority score per B.1 formula.

        score(source) = base_tier_weight(source)
                      × hit_rate_multiplier(source)
                      × novelty_bonus(source)
        """
        base = self._BASE_TIER_WEIGHTS.get(source_type, 0.7)
        hit_mult = self._source_weights.get(source_type, 1.0)
        novelty = self._novelty_bonuses.get(source_type, 1.0)
        return base * hit_mult * novelty

    def prioritize_sources(
        self, candidates: list[str], ioc_graph_stats: dict | None = None
    ) -> list[str]:
        """
        Sort candidates by score — highest first.
        Returns list of source_type strings ordered by priority.
        """
        scored = [
            (src, self.score_source(src, ioc_graph_stats))
            for src in candidates
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        log.debug(
            f"Source priorities: {[(s, f'{sc:.2f}') for s, sc in scored[:5]]}"
        )
        return [s for s, _ in scored]

    def set_novelty_bonus(self, source_type: str, has_bonus: bool) -> None:
        """Set novelty bonus: 1.5 if source added new IOC types this sprint."""
        self._novelty_bonuses[source_type] = 1.5 if has_bonus else 1.0

    # ── Sprint 8VB: Adaptive Timeout ───────────────────────────────────

    def _update_latency_ema(self, domain: str, latency: float) -> None:
        """Update EMA for domain fetch latency. Bounded to _MAX_FETCH_LATENCY_EMA entries."""
        prev = self._fetch_latency_ema.get(domain, latency)
        self._fetch_latency_ema[domain] = (
            0.3 * latency + 0.7 * prev
        )
        # F196C: deque(maxlen=1000) auto-evicts oldest entry when full
        if domain not in self._fetch_latency_ema_order:
            self._fetch_latency_ema_order.append(domain)
        # Eviction is automatic via deque maxlen

    def get_adaptive_timeout(self, domain: str) -> float:
        """Get adaptive timeout based on EMA latency. Clamped to [5, 30]s."""
        ema = self._fetch_latency_ema.get(domain, 10.0)
        return max(5.0, min(30.0, ema * 3.0))

    async def log_source_hit(
        self,
        store: Any,
        sprint_id: str,
        source_type: str,
        findings_count: int,
        ioc_count: int,
    ) -> None:
        """Record a source hit for hit-rate tracking."""
        hit_rate = findings_count / max(1, findings_count + 1)
        try:
            await store.async_record_source_hit(
                sprint_id, _time.time(), source_type,
                findings_count, ioc_count, hit_rate,
            )
        except Exception as e:
            log.warning(f"source_hit_log insert failed: {e}")

    # ── Sprint 8TB: Agentic Pivot Loop ──────────────────────────────────

    def inject_ioc_graph(self, ioc_graph: Any) -> None:
        """Inject IOCGraph reference for pivot operations."""
        self._pivot_ioc_graph = ioc_graph

    def inject_policy_manager(self, policy_manager: Any) -> None:
        """Inject SprintPolicyManager reference (opt-in RL layer)."""
        self._policy_manager = policy_manager
        # Bidirectional wiring: allow policy manager to delegate quality feedback
        # adaptation back to this scheduler's _adapt_source_weights_from_feedback
        if hasattr(policy_manager, "inject_scheduler"):
            policy_manager.inject_scheduler(self)

    def inject_prefetch_oracle(self, oracle: Any) -> None:
        """
        Inject PrefetchOracleIntegration reference (advisory prefetch ordering).

        F200A: oracle is ADVISORY ONLY — scheduler retains all authority.
        Oracle suggests sort scores; scheduler multiplies them into economics sort key.
        All oracle calls are fail-soft — exception or None oracle → no-op.
        """
        self._prefetch_oracle = oracle

    def inject_pivot_planner(self, planner: Any) -> None:
        """
        Inject PivotPlanner reference (F202G advisory pivot ordering).

        F202G: planner is ADVISORY ONLY — scheduler retains all authority.
        Planner generates pivot suggestions from findings; scheduler uses them
        as advisory ordering input, NOT as new sprint owner.
        All planner calls are fail-soft — exception or None planner → no-op.
        """
        self._pivot_planner = planner

    def inject_analyst_workbench(self, workbench: Any) -> None:
        """
        F204E: Inject AnalystWorkbench reference for sprint brief generation.

        Workbench is used at TEARDOWN to generate a model-free analyst brief
        summarizing sprint results: what changed, strongest evidence,
        next best pivots, and open questions.

        All workbench calls are fail-soft — exception or None workbench → no-op brief.
        """
        self._analyst_workbench = workbench

    def get_analyst_brief(self) -> Any:
        """
        F204E: Return the last generated analyst brief.

        Returns None if no brief was generated or brief generation failed.
        """
        return getattr(self, "_analyst_brief", None)

    def get_planned_pivots(self) -> list:
        """
        F202G: Return last planned pivots for diagnostics.

        Returns empty list if no pivots were planned or planner failed.
        """
        return getattr(self, "_planned_pivots", [])

    def enqueue_pivot(
        self,
        ioc_value: str,
        ioc_type: str,
        confidence: float,
        degree: float = 1.0,
        task_type: str | None = None,
    ) -> None:
        """
        Enqueue a pivot task. Called on every new IOC hit from buffer_ioc.
        Silently drops if queue is full (M1 8GB constraint).

        Sprint 8VI §B.4: RL-adaptive priority — for generic_pivot task types,
        blend EMA reward with base priority.
        """
        if self._pivot_queue.full():
            return
        # Multi-pivot: enqueue ALL applicable task types per IOC
        task_types_list: list[str]
        if task_type is not None:
            # Single explicit task type
            task_types_list = [task_type]
        else:
            task_types_list = {
                # Sprint 8TB original
                "cve": ["cve_to_github", "cve_to_academic"],
                "ipv4": ["ip_to_ct", "ip_to_greynoise", "shodan_enrich"],
                "ipv6": ["ip_to_ct"],
                "domain": ["domain_to_dns", "domain_to_wayback", "domain_to_pdns",
                           "domain_to_ct", "ahmia_search", "rdap_lookup"],
                "md5": ["hash_to_mb"],
                "sha256": ["hash_to_mb"],
                "sha1": ["hash_to_mb"],
                # Sprint 8VB: Maximum OSINT Coverage
                "url": ["wayback_search", "commoncrawl_search", "paste_keyword_search",
                        "github_dork", "multi_engine_search"],
                # Sprint 8VI §C: hypothesis feedback
                "hypothesis": ["multi_engine_search", "rdap_lookup"],
            }.get(ioc_type, [])
        if not task_types_list:
            return

        base_priority = confidence * max(1.0, float(degree))
        for tt in task_types_list:
            # Sprint 8VI §B.4: RL-adaptive priority blend
            effective = self._get_adaptive_priority(tt, base_priority=base_priority)
            priority = -effective
            task = PivotTask(priority, ioc_type, ioc_value, tt)
            try:
                self._pivot_queue.put_nowait(task)
                self._pivot_stats["total"] += 1
            except asyncio.QueueFull:
                pass

    def enqueue_hypothesis_pivot(
        self,
        ioc_value: str,
        ioc_type: str = "hypothesis",
        confidence: float = 0.7,
        depth: int = 1,
    ) -> bool:
        """
        Enqueue a pivot task driven by hypothesis/ToT output.

        Sprint F193B: Bounded hypothesis → finding feedback loop.
        Enforces:
        - max_hypothesis_depth: iteration depth cap (default 3)
        - max_hypothesis_queries: total query count cap (default 10)

        Returns True if enqueued, False if dropped due to cap.
        """
        # Sprint F193B: Enforce depth cap
        if depth > self._config.max_hypothesis_depth:
            log.debug(f"[F193B] Hypothesis pivot dropped: depth {depth} > max {self._config.max_hypothesis_depth}")
            return False

        # Sprint F193B: Enforce query count cap
        if self._hypothesis_query_count >= self._config.max_hypothesis_queries:
            log.debug(f"[F193B] Hypothesis pivot dropped: query count {self._hypothesis_query_count} >= max {self._config.max_hypothesis_queries}")
            return False

        # Enqueue with "hypothesis" ioc_type which maps to multi_engine_search, rdap_lookup
        self.enqueue_pivot(
            ioc_value=ioc_value,
            ioc_type=ioc_type,
            confidence=confidence,
            degree=float(depth),
            task_type=None,  # Use ioc_type mapping for task types
        )
        self._hypothesis_query_count += 1
        self._hypothesis_depth = max(self._hypothesis_depth, depth)
        log.debug(f"[F193B] Hypothesis pivot enqueued: {ioc_value} (depth={depth}, total_queries={self._hypothesis_query_count})")
        return True

    async def _drain_pivot_queue(self, max_tasks: int = 5) -> int:
        """
        Drain up to max_tasks from pivot queue. Max 8s total deadline.
        Called at end of each ACTIVE cycle.
        """
        processed = 0
        deadline = _time.monotonic() + 8.0
        while processed < max_tasks:
            if _time.monotonic() > deadline:
                break
            try:
                task = self._pivot_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            try:
                await asyncio.wait_for(
                    self._execute_pivot(task),
                    timeout=6.0,
                )
                self._pivot_stats["processed"] += 1
            except (asyncio.TimeoutError, Exception) as e:
                self._pivot_stats["errors"] += 1
                log.debug(f"pivot {task.task_type} {task.ioc_value}: {e}")
            processed += 1
        return processed

    async def _execute_pivot(self, task: PivotTask) -> None:
        """Dispatch pivot task to appropriate intelligence client."""
        from hledac.universal.intelligence.exposure_clients import (
            GitHubCodeSearchClient,
            MalwareBazaarClient,
        )
        from hledac.universal.intelligence.network_reconnaissance import (
            PassiveDNSClient,
        )
        from hledac.universal.paths import CACHE_ROOT
        from hledac.universal.tool_registry import get_task_handler

        # Sprint 8VF: Registry dispatch — OSINT handlers registered via @register_task
        handler = get_task_handler(task.task_type)
        if handler is not None:
            await handler(task, self)
            return

            # Sprint 8VF: Inline lifecycle handlers only (max 5 branches)
            # Sprint 8VF §E.3: hypothesis_probe — keyword extraction from natural language
            # Sprint 8VI §C: Hypothesis → DuckPGQ confirmed_by feedback
        elif task.task_type == "hypothesis_probe":
                words = task.ioc_value.split()
                queries = sorted(
                    {w.lower() for w in words if len(w) > 5},
                    key=len, reverse=True
                )[:3]
                count_before = getattr(self, "_finding_count", 0)
                for sq in queries:
                    self.enqueue_pivot(
                        ioc_value=sq,
                        ioc_type="url",
                        confidence=0.7,
                    )
                count_after = getattr(self, "_finding_count", 0)
                hyp_found = count_after - count_before
                # Sprint 8VI §C: Feedback — successful hypotheses strengthen edges
                if hyp_found > 0 and hasattr(self, "_ioc_graph") and self._ioc_graph is not None:
                    try:
                        for ioc_entry in self._recent_iocs[-hyp_found:]:
                            ioc_val = ioc_entry.get("value") or ioc_entry.get("ioc", "")
                            if ioc_val:
                                self._ioc_graph.add_relation(
                                    task.ioc_value[:100],
                                    ioc_val,
                                    rel_type="confirmed_by",
                                    weight=0.8,
                                    evidence="hypothesis_probe",
                                )
                    except Exception:
                        pass

            # Sprint 8VF §C: Sprint lifecycle inline handlers (only these stay as elif)
        elif task.task_type == "sprint_windup":
                # Signal windup — nothing to do in pivot
                pass

        else:
                # Sprint 8VF: OSINT handlers moved to @register_task registry
                # (ti_feed_adapter, duckduckgo_adapter). Remaining types are either
                # unregistered or lifecycle-only.
                log.debug(f"[DISPATCH] Unknown task type: {task.task_type}")

    async def _buffer_ioc_pivot(
        self, ioc_type: str, ioc_value: str, confidence: float
    ) -> None:
        """Wrapper: buffer IOC to graph and enqueue for further pivoting."""
        # Sprint 8VE B.3: Lazy IOC graph init
        if not hasattr(self, "_ioc_graph"):
            from hledac.universal.graph.quantum_pathfinder import DuckPGQGraph
            self._ioc_graph = DuckPGQGraph()

        entry = {"ioc": ioc_value, "ioc_type": ioc_type, "source": "pivot"}
        domain = None
        try:
            from urllib.parse import urlparse
            domain = urlparse(ioc_value).netloc
        except Exception:
            pass
        if domain:
            entry["domain"] = domain
            entry["rel_type"] = "seen_at"
        if entry.get("ioc"):
            self._ioc_graph.add_relation(
                entry["ioc"], domain or ioc_value,
                rel_type=entry.get("rel_type", "pivot"),
                evidence=entry.get("source", "")
            )

        # Also buffer to pivot_ioc_graph if set
        if self._pivot_ioc_graph is not None:
            await self._pivot_ioc_graph.buffer_ioc(ioc_type, ioc_value, confidence)
            # Re-enqueue for further pivot (with degree+1)
            degree = 2
            self.enqueue_pivot(ioc_value, ioc_type, confidence * 0.9, degree)

    # ── Sprint 8UC B.4: Speculative prefetch ─────────────────────────────

    async def _speculative_prefetch(
        self,
        n: int = 3,
    ) -> None:
        """Spustit top-n pivot tasků spekulativně jako background tasks."""
        if self._pivot_queue.empty():
            return

        # Sprint 8RA: Bound _speculative_results to prevent unbounded growth
        if len(self._speculative_results) > 500:
            keys = list(self._speculative_results.keys())
            for k in keys[:250]:
                del self._speculative_results[k]

        # Peek top-n z heap (min-heap: nejnižší = nejvyšší priorita)
        peeked = []
        try:
            with self._pivot_queue.mutex:
                peeked = list(self._pivot_queue.queue)[:n]
        except AttributeError:
            # Fallback for queues without mutex
            # NON-DESTRUCTIVE: get item, re-enqueue immediately to preserve queue
            peeked = []
            for _ in range(min(n, self._pivot_queue.qsize())):
                try:
                    item = self._pivot_queue.get_nowait()
                    peeked.append(item)
                    self._pivot_queue.put_nowait(item)
                except asyncio.QueueEmpty:
                    break
                except asyncio.QueueFull:
                    break

        for pivot_task in peeked[:n]:
            task_key = f"{pivot_task.task_type}:{pivot_task.ioc_value}"
            if task_key in self._speculative_results:
                continue

            async def _speculative_run(pt=pivot_task, key=task_key):
                try:
                    result = await self._execute_pivot(pt)
                    self._speculative_results[key] = result or {}
                    log.debug(f"Speculative hit: {key}")
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    log.debug(f"Speculative miss {key}: {e}")

            task = asyncio.create_task(_speculative_run(), name="sprint:speculative_run")
            self._bg_tasks.add(task)
            task.add_done_callback(self._bg_tasks.discard)

    # ── Sprint 8UC B.5: OODA agentic loop ────────────────────────────────

    async def _run_ooda_cycle(
        self,
        ioc_graph,
    ) -> None:
        """Jeden OODA cyklus — 60s interval."""
        log.info("OODA: cycle start")

        # OBSERVE
        try:
            node_count = ioc_graph.node_count() if ioc_graph and hasattr(ioc_graph, "node_count") else 0
            log.debug(f"OODA Observe: {node_count} IOC nodes")
        except Exception:
            node_count = 0

        # ORIENT — PageRank top-k
        top_nodes: list = []
        try:
            if ioc_graph and hasattr(ioc_graph, "pagerank"):
                top_nodes = await asyncio.get_running_loop().run_in_executor(
                    None, ioc_graph.pagerank, 10)
            elif ioc_graph and hasattr(ioc_graph, "get_top_nodes"):
                top_nodes = ioc_graph.get_top_nodes(10)
        except Exception as e:
            log.debug(f"OODA Orient PageRank: {e}")

        # DECIDE — nodes s pr_score > 0.05 dostávají priority boost
        decided_seeds: list = []
        for node in top_nodes[:5]:
            if len(node) >= 3:
                value, ioc_type, pr_score = node[0], node[1], float(node[2])
            else:
                continue
            if pr_score > 0.05:
                confidence = min(0.95, 0.75 + pr_score)
                decided_seeds.append((value, ioc_type, confidence))

        # ACT — enqueue pivot tasks (sync, no await needed)
        acted = 0
        for value, ioc_type, confidence in decided_seeds:
            try:
                self.enqueue_pivot(value, ioc_type, confidence, degree=2)
                acted += 1
            except Exception as e:
                log.debug(f"OODA Act enqueue {value}: {e}")

        self._pivot_stats["ooda_cycles"] = self._pivot_stats.get("ooda_cycles", 0) + 1
        self._pivot_stats["ooda_last_acted"] = acted
        log.info(f"OODA: acted on {acted} nodes")

    # ── Sprint 8VD §B: Arrow / Parquet columnar buffer ────────────────────

    def _resolve_arrow_batch_hard_cap(self) -> int:
        """Resolve Arrow batch hard cap from env or return M1-safe default.

        F214OPT-D: Prevents unbounded Arrow batch growth after flush failure.
        Default is max(2 * _ARROW_FLUSH_N, 2000) = 2000 entries (~10MB range).
        Env override: HLEDAC_ARROW_BATCH_HARD_CAP (min 100, max 50000).
        """
        try:
            raw = os.environ.get("HLEDAC_ARROW_BATCH_HARD_CAP", "")
            if raw:
                val = int(raw)
                return max(100, min(val, 50000))
        except (ValueError, TypeError):
            pass
        return max(2 * self._ARROW_FLUSH_N, 2000)

    async def _maybe_flush_to_parquet(self) -> None:
        """Flush Arrow batch to Parquet when N or S threshold is hit.

        F214OPT-D: On flush failure, batch is truncated to HARD_CAP to prevent
        unbounded growth. Failed entries are dropped (oldest first) and counted.
        """
        import time as _time
        now = _time.monotonic()
        if (
            len(self._arrow_batch) < self._ARROW_FLUSH_N
            and now - self._arrow_last_flush < self._ARROW_FLUSH_S
        ):
            return
        if not self._arrow_batch:
            return

        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError:
            log.warning("[8VD-PARQUET] pyarrow not available — skipping flush")
            return

        batch = self._arrow_batch[:]
        # F214OPT-D: Do NOT clear batch until flush succeeds — preserves data on failure

        schema = pa.schema([
            ("url",        pa.string()),
            ("title",      pa.string()),
            ("snippet",    pa.string()),
            ("source",     pa.string()),
            ("ioc",        pa.string()),
            ("ioc_type",   pa.string()),
            ("confidence", pa.float32()),
            ("timestamp",  pa.timestamp("ms", tz="UTC")),
            ("sprint_id",  pa.string()),
        ])
        rows = {k: [r.get(k) for r in batch] for k in schema.names}
        table = pa.table(rows, schema=schema)

        from hledac.universal.paths import get_sprint_parquet_dir
        sid = self.sprint_id or getattr(self, "sprint_id", "unknown")
        path = get_sprint_parquet_dir(sid) / f"batch_{int(now * 1000)}.parquet"

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None, lambda: pq.write_table(table, path, compression="snappy")
            )
            log.info(f"[8VD-PARQUET] flushed {len(batch)} rows → {path}")
            # F214OPT-D: SUCCESS — clear batch only after successful write
            self._arrow_batch.clear()
            self._arrow_last_flush = now
        except Exception as exc:
            # F214OPT-D: Flush failed — record error, enforce HARD_CAP, keep partial
            self._result.arrow_last_flush_error = str(exc)[:256]
            batch_len = len(self._arrow_batch)
            hard_cap = self._ARROW_BATCH_HARD_CAP
            if batch_len > hard_cap:
                drop_count = batch_len - hard_cap
                self._arrow_batch = self._arrow_batch[drop_count:]
                self._result.arrow_batch_dropped_after_flush_failure += drop_count
                log.warning(
                    f"[8VD-PARQUET] flush failed ({exc}), dropped {drop_count} oldest "
                    f"entries to enforce HARD_CAP={hard_cap}, {len(self._arrow_batch)} remain"
                )
            else:
                # Keep batch intact — it will retry on next call
                log.warning(f"[8VD-PARQUET] flush failed ({exc}), batch intact ({batch_len}), will retry")

    def buffer_finding(self, finding: dict) -> None:
        """Buffer a finding into the Arrow batch."""
        self._arrow_batch.append(finding)
        # Kick off async flush without awaiting
        try:
            _t = asyncio.create_task(self._maybe_flush_to_parquet(), name="sprint:flush_arrow")
            self._bg_tasks.add(_t)
            _t.add_done_callback(self._bg_tasks.discard)
        except RuntimeError:
            pass  # No running loop in sync context
        # Sprint 8VF §B.3: IOC extraction — regex PRIMARY, spaCy SECONDARY
        _text = " ".join(filter(None, [
            finding.get("snippet", ""),
            finding.get("content", ""),
            finding.get("title", ""),
        ])).strip()
        if len(_text) > 10:
            try:
                from hledac.universal.brain.ane_embedder import extract_iocs_from_text
                for ioc in extract_iocs_from_text(_text[:2_000]):
                    ioc_entry = {
                        **ioc,
                        "source": "ner_extracted",
                        "parent_url": finding.get("url", ""),
                    }
                    self.buffer_ioc(ioc_entry)
            except Exception:
                pass  # NER is enrichment — never crashes the pipeline

    def buffer_ioc(self, ioc: dict) -> None:
        """
        Buffer an IOC into the Arrow batch.

        Sprint 8VI §D: IOCScorer final_score zapojeno.
        Sprint 8VI §C: Recent IOC ring buffer pro hypothesis feedback.
        """
        # Sprint 8VI §D: IOCScorer zapojení
        ioc_entry = dict(ioc)
        if hasattr(self, "_ioc_scorer") and self._ioc_scorer is not None:
            try:
                score = self._ioc_scorer.final_score(ioc_entry)
                ioc_entry["confidence"] = score
            except Exception:
                pass

        # Sprint 8VI §C: Ring buffer — max 100 recent IOCs
        recent = getattr(self, "_recent_iocs", [])
        recent.append(ioc_entry)
        self._recent_iocs = recent[-100:]

        # Sprint 8VI §C: Hypothesis → DuckPGQ confirmed_by hrany
        # (handled in _execute_pivot after finding confirmation)

        self._arrow_batch.append(ioc_entry)
        try:
            _t = asyncio.create_task(self._maybe_flush_to_parquet(), name="sprint:flush_arrow_ioc")
            self._bg_tasks.add(_t)
            _t.add_done_callback(self._bg_tasks.discard)
        except RuntimeError:
            pass

    # ── Sprint 8VD §B.5: DuckDB singleton helpers ───────────────────────────

    def _get_duckdb_con(self):
        """Singleton DuckDB connection — initialized once."""
        if self._duckdb_read_con is None:
            import duckdb
            self._duckdb_read_con = duckdb.connect()
        return self._duckdb_read_con

    def query_sprint_results(self, sql: str) -> list[dict]:
        """DuckDB vectorized query over Parquet files. Zero-copy style."""
        return self._get_duckdb_con().execute(sql).fetchdf().to_dict("records")

    # ── Sprint 8VD §D: Polars lazy dedup + ranking ────────────────────────

    def deduplicate_and_rank_findings(self, sprint_id: str | None = None) -> str:
        """
        Polars LazyFrame streaming dedup — M1 8GB RAM safe.
        Uses Polars 1.x .collect(engine='streaming') API.
        """
        import polars as pl
        from hledac.universal.paths import get_sprint_parquet_dir
        sid = sprint_id or self.sprint_id or "*"
        store_dir = get_sprint_parquet_dir(sid)
        glob = str(store_dir / "batch_*.parquet")
        out = str(store_dir / "ranked.parquet")

        (
            pl.scan_parquet(glob)
            .filter(
                pl.col("url").is_not_null() | pl.col("ioc").is_not_null()
            )
            .with_columns([
                pl.col("confidence").fill_null(0.5),
                pl.col("source").cast(pl.Categorical),
            ])
            .group_by(["url", "ioc"])
            .agg([
                pl.col("title").first(),
                pl.col("source").first(),
                pl.col("confidence").max(),
                pl.len().alias("hit_count"),
            ])
            .sort("hit_count", descending=True)
            .collect(engine="streaming")
            .write_parquet(out, compression="snappy")
        )
        return out

    # ── Sprint 8VD §C: Memory pressure loop ────────────────────────────────

    async def _memory_pressure_loop(self) -> None:
        """Background task — adjusts concurrency based on memory pressure."""
        from hledac.universal.resource_allocator import get_recommended_concurrency
        import asyncio as _asyncio

        while True:
            try:
                limits = get_recommended_concurrency()
                self._fetch_semaphore = _asyncio.Semaphore(limits["fetch"])
                log.info(
                    f"[MEM] fetch_limit={limits['fetch']} "
                    f"ml_jobs={limits['ml_jobs']}"
                )
                interval = 10 if limits["fetch"] <= 2 else 30
            except Exception as e:
                log.warning(f"[MEM] pressure check failed: {e}")
                interval = 30
            await _asyncio.sleep(interval)

    # ── Sprint 8VM: Shadow Pre-Decision Consumer ───────────────────────────
    # Read-only seam: consumes existing shadow/pre-decision layer
    # WITHOUT creating new scheduler framework, mutable state, or execution path

    def consume_shadow_pre_decision(self) -> Any:
        """
        Sprint 8VM: Read-only shadow pre-decision consumer.

        Collects shadow inputs from current scheduler state,
        runs parity check and pre-decision composition,
        and returns PreDecisionSummary.

        Caching: stores result in _shadow_pd_summary to avoid recomputation.
        Cache is cleared in _reset_result().

        THIS IS DIAGNOSTIC ONLY — all hard boundaries enforced:
        - Does NOT execute any tools (no execute_with_limits calls)
        - Does NOT activate any providers
        - Does NOT write to any ledgers as runtime truth
        - Does NOT modify scheduler mutable state
        - Does NOT create new scheduler framework
        - Does NOT dispatch or enqueue work
        - Returns PreDecisionSummary artifact, NOT a truth store

        Injection point: called from _build_diagnostic_report() at export time.
        The method is also available for ad-hoc calls during sprint for
        diagnostic purposes only.

        Returns None if shadow mode is not active.
        """
        from hledac.universal.runtime.shadow_inputs import RuntimeMode

        # Only run when shadow mode is explicitly enabled
        if not RuntimeMode.is_shadow_mode():
            return None

        # Return cached value if already computed this sprint
        if self._shadow_pd_summary is not None:
            return self._shadow_pd_summary

        lc = None
        if self._lc_adapter is not None:
            lc = self._lc_adapter._lc
        if lc is None:
            return None

        # Collect lifecycle snapshot
        try:
            now_mono = _time.monotonic()
            # Derive thermal state from latency EMA (read-only heuristic)
            thermal = "nominal"
            if self._fetch_latency_ema:
                max_ema = max(self._fetch_latency_ema.values()) if self._fetch_latency_ema else 10.0
                if max_ema > 20.0:
                    thermal = "critical"
                elif max_ema > 15.0:
                    thermal = "throttled"
                elif max_ema > 10.0:
                    thermal = "fair"

            lifecycle_bundle = collect_lifecycle_snapshot(
                lc, now_mono, thermal,
                windup_synthesis_mode="synthesis",
                windup_error=False,
                windup_engine=self._synthesis_engine or "unknown",
            )
        except Exception:
            return None

        # Collect graph summary (may be None if no graph injected yet)
        try:
            graph_bundle = collect_graph_summary(self._ioc_graph)
        except Exception:
            from hledac.universal.runtime.shadow_inputs import GraphSummaryBundle
            graph_bundle = GraphSummaryBundle()

        # Collect model/control facts from scheduler config
        try:
            mc_bundle = collect_model_control_facts(
                analyzer_result=None,
                raw_profile={
                    "tools": [],
                    "sources": list(self._config.source_tier_map.keys()),
                    "privacy_level": "STANDARD",
                    "use_tor": False,
                    "depth": "STANDARD",
                    "use_tot": False,
                    "tot_mode": "standard",
                    "models_needed": [],
                },
            )
        except Exception:
            from hledac.universal.runtime.shadow_inputs import ModelControlFactsBundle
            mc_bundle = ModelControlFactsBundle()

        # Export handoff facts (synthesized from scheduler state)
        export_facts = {
            "sprint_id": self.sprint_id or "unknown",
            "synthesis_engine": self._synthesis_engine or "unknown",
            "gnn_predictions": 0,
            "top_nodes_count": 0,
            "ranked_parquet_present": False,
            "phase_durations": {},
        }

        try:
            parity = run_shadow_parity(
                lifecycle_bundle=lifecycle_bundle,
                graph_bundle=graph_bundle,
                model_control_bundle=mc_bundle,
                export_handoff_facts=export_facts,
                branch_decision=None,
                provider_recommend=None,
                correlation=None,
                runtime_mode=RuntimeMode.get_current(),
            )
        except Exception:
            return None

        # Sprint F3.13: Collect provider runtime facts (read-only)
        # COMPAT path: get_model_lifecycle_status() reads _lifecycle_state module shadow-state
        # The lifecycle_status dict is passed through to collect_provider_runtime_facts()
        # which derives STABLE/COMPAT/UNKNOWN stability from the inputs.
        # STABLE path would require ModelManager injection (not yet available;
        # COMPAT is sufficient for diagnostic purposes).
        try:
            # Sprint F350M: Canonical import path — F350N §H4 import truth fix
            from hledac.universal.brain.model_lifecycle import get_model_lifecycle_status
            lifecycle_status = get_model_lifecycle_status()
        except Exception:
            lifecycle_status = None
        try:
            runtime_facts = collect_provider_runtime_facts(model_manager=None, lifecycle_status=lifecycle_status)
        except Exception:
            from hledac.universal.runtime.shadow_inputs import ProviderRuntimeFactsBundle
            runtime_facts = ProviderRuntimeFactsBundle()

        try:
            pd_summary = compose_pre_decision(parity, runtime_facts=runtime_facts)
        except Exception:
            return None

        # Tool readiness preview — DIAGNOSTIC ONLY, no dispatch, no execute_with_limits
        # Sprint F350D: NO full ToolRegistry init — heavyweight for M1 8GB shadow path.
        # Shadow path uses metadata-only preview (count/category heuristics, no registry init).
        try:
            # Sprint F350D: Use metadata-only heuristic — lightweight, no registry materialization.
            # Tool count is estimated from source_tier_map size + known pipeline tools.
            # This avoids the cold-import cost and memory of full registry init.
            estimated_tool_count = 12  # known built-in pipeline tools
            source_types = list(self._config.source_tier_map.keys())
            has_network_tools = any(
                s in source_types for s in
                ["cisa_kev", "threatfox_ioc", "urlhaus_recent", "feodo_ip", "openphish_feed"]
            )
            has_high_memory_tools = False  # unknown without registry init — deferred
            # Attach as read-only diagnostic annotations to pd_summary
            pd_summary._tool_readiness_preview = {
                "tool_count": estimated_tool_count,
                "tool_names": [],  # unknown without registry init — deferred
                "has_network_tools": has_network_tools,
                "has_high_memory_tools": has_high_memory_tools,
                "tool_cards_sample": [],  # deferred without registry init
                "_deferred_registry": True,  # marker: full registry not materialized
            }
        except Exception:
            # ToolRegistry unavailable — skip, this is diagnostic only
            pass

        # Sprint F3.11: Dispatch parity preview — DIAGNOSTIC ONLY
        # Read-only task candidate analysis, no execute_with_limits, no dispatch
        try:
            from hledac.universal.runtime.shadow_pre_decision import preview_dispatch_parity

            # Default task candidates for dispatch parity preview
            # These represent the pivot task types from _execute_pivot()
            task_candidates = [
                "cve_to_github", "cve_to_academic",
                "ip_to_ct", "ip_to_greynoise", "shodan_enrich",
                "domain_to_dns", "domain_to_wayback", "domain_to_pdns",
                "domain_to_ct", "ahmia_search", "rdap_lookup",
                "hash_to_mb",
                "wayback_search", "commoncrawl_search", "paste_keyword_search",
                "github_dork", "multi_engine_search",
                "hypothesis_probe",
            ]

            # Available capabilities from model_control facts (heuristic)
            available_caps: set = set()
            if mc_bundle.tools:
                # Map tools to capabilities heuristically
                for tool in mc_bundle.tools:
                    if tool in ("web_search", "academic_search"):
                        available_caps.add("reranking")
                    if tool == "entity_extraction":
                        available_caps.add("entity_linking")

            # Control mode from lifecycle
            ctrl_mode = lifecycle_bundle.control_phase.mode if hasattr(lifecycle_bundle, 'control_phase') else "normal"

            # Sprint F350E: registry is metadata-only deferred — never materialized in shadow path.
            # Shadow path uses source_tier_map as lightweight heuristic (avoids cold-import cost).
            registry_tools: Optional[list[str]] = None  # deferred: no full registry init in shadow path

            dispatch_preview = preview_dispatch_parity(
                task_candidates=task_candidates,
                available_capabilities=available_caps,
                control_mode=ctrl_mode,
                registry_tools=registry_tools,
            )

            # Sprint F9: Attach execution context readiness (capability/correlation/audit separation)
            # This is READ-ONLY — does not call execute_with_limits or activate anything
            try:
                from hledac.universal.runtime.shadow_pre_decision import (
                    build_execution_context_readiness,
                )
                # Correlation context from scheduler run (run_id present in sprint context)
                correlation_context: Optional[Dict[str, Any]] = None
                if hasattr(self, "_run_id") and self._run_id:
                    correlation_context = {"run_id": self._run_id}

                exec_logger_available = hasattr(self, "_tool_exec_logger") and self._tool_exec_logger is not None

                execution_context = build_execution_context_readiness(
                    dispatch_preview=dispatch_preview,
                    correlation_context=correlation_context,
                    exec_logger_available=exec_logger_available,
                )
                dispatch_preview.execution_context = execution_context
            except Exception:
                # Execution context unavailable — skip, this is diagnostic only
                pass

            pd_summary.dispatch_parity = dispatch_preview
        except Exception:
            # Dispatch preview unavailable — skip, this is diagnostic only
            pass

        # Cache for repeated calls within the same sprint
        self._shadow_pd_summary = pd_summary
        return pd_summary

    def evaluate_advisory_gate(self) -> None:
        """
        Sprint 8VQ: Evaluate advisory gate at WINDUP entry — DIAGNOSTIC ONLY.

        Reads from cached PreDecisionSummary (computed by consume_shadow_pre_decision)
        and composes AdvisoryGateSnapshot. Does NOT:
        - Influence dispatch or source ordering
        - Activate providers or tools
        - Write to any ledgers as runtime truth
        - Create new scheduler framework

        Stores ephemeral result in _advisory_gate_snapshot (cleared in _reset_result).
        Output goes into diagnostic report via _build_shadow_readiness_preview().
        """
        from hledac.universal.runtime.shadow_pre_decision import compose_advisory_gate

        pd = self.consume_shadow_pre_decision()
        if pd is None:
            self._advisory_gate_snapshot = None
            return

        try:
            self._advisory_gate_snapshot = compose_advisory_gate(pd)
        except Exception:
            self._advisory_gate_snapshot = None

    def _build_shadow_readiness_preview(self) -> dict[str, Any]:
        """
        Sprint 8VM + 8VQ: Build a machine-readable shadow readiness preview dict.

        Called from _build_diagnostic_report() when shadow mode is active.
        This is a READ-ONLY summary extracted from PreDecisionSummary
        for diagnostic/logging purposes — NOT a truth store.
        """
        pd = self.consume_shadow_pre_decision()
        if pd is None:
            return {}

        result: dict[str, Any] = {
            "runtime_mode": pd.runtime_mode,
            "parity_timestamp_monotonic": pd.parity_timestamp_monotonic,
            "lifecycle_readiness": {
                "phase": pd.lifecycle.workflow_phase,
                "is_active": pd.lifecycle.is_active,
                "is_windup": pd.lifecycle.is_windup,
                "can_accept_work": pd.lifecycle.can_accept_work,
                "should_prune": pd.lifecycle.should_prune,
                "phase_conflict": pd.lifecycle.phase_conflict,
            },
            "graph_readiness": {
                "backend": pd.graph.backend,
                "readiness": pd.graph.readiness,
                "nodes": pd.graph.nodes,
                "edges": pd.graph.edges,
            },
            "export_readiness": {
                "readiness": pd.export_readiness.readiness,
                "synthesis_engine": pd.export_readiness.synthesis_engine,
            },
            "model_control_readiness": {
                "readiness": pd.model_control.readiness,
                "tools_count": pd.model_control.tools_count,
            },
            "diff_taxonomy": [d.name for d in pd.diff_taxonomy],
            "blockers": pd.blockers,
            "unknowns": pd.unknowns,
            "compat_seams": pd.compat_seams,
        }

        # Sprint 8VQ: Decision gate readiness
        if pd.decision_gate is not None:
            result["decision_gate"] = {
                "gate_status": pd.decision_gate.gate_status,
                "blocker_count": pd.decision_gate.blocker_count,
                "unknown_count": pd.decision_gate.unknown_count,
                "compat_seam_count": pd.decision_gate.compat_seam_count,
                "is_proceed_allowed": pd.decision_gate.is_proceed_allowed,
                "defer_to_provider": pd.decision_gate.defer_to_provider,
                "blocker_categories": pd.decision_gate.blocker_categories,
                "unknown_categories": pd.decision_gate.unknown_categories,
            }

        # Sprint 8VQ: Tool readiness preview (read-only, no dispatch)
        if pd.tool_readiness is not None:
            result["tool_readiness"] = {
                "readiness": pd.tool_readiness.readiness,
                "tool_count": pd.tool_readiness.tool_count,
                "has_network_tools": pd.tool_readiness.has_network_tools,
                "has_high_memory_tools": pd.tool_readiness.has_high_memory_tools,
                "control_mode": pd.tool_readiness.control_mode,
                "pruned_tool_count": pd.tool_readiness.pruned_tool_count,
                "resource_constraint": pd.tool_readiness.resource_constraint,
                "can_execute": pd.tool_readiness.can_execute,
                "defer_reason": pd.tool_readiness.defer_reason,
            }

        # Sprint 8VQ: Windup readiness preview
        if pd.windup_readiness is not None:
            result["windup_readiness"] = {
                "readiness": pd.windup_readiness.readiness,
                "is_windup_phase": pd.windup_readiness.is_windup_phase,
                "synthesis_mode": pd.windup_readiness.synthesis_mode,
                "synthesis_engine": pd.windup_readiness.synthesis_engine,
                "has_export_data": pd.windup_readiness.has_export_data,
                "export_data_quality": pd.windup_readiness.export_data_quality,
                "defer_reason": pd.windup_readiness.defer_reason,
            }

        # Sprint 8VQ: Provider activation note (deferred/unknown only)
        if pd.provider_note is not None:
            result["provider_activation_note"] = {
                "status": pd.provider_note.status,
                "deferral_reason": pd.provider_note.deferral_reason,
                "has_recommendation": pd.provider_note.has_recommendation,
                "recommendation": pd.provider_note.recommendation,
                "next_phase_hint": pd.provider_note.next_phase_hint,
            }

        # Legacy: tool_readiness_preview from consumer seam (if still attached)
        if hasattr(pd, "_tool_readiness_preview"):
            result["tool_readiness_preview"] = pd._tool_readiness_preview

        # Sprint 8VQ: Advisory gate snapshot (computed at WINDUP entry, diagnostic only)
        if self._advisory_gate_snapshot is not None:
            ag = self._advisory_gate_snapshot
            result["advisory_gate"] = {
                "gate_outcome": ag.gate_outcome,
                "gate_status": ag.gate_status,
                "blocker_count": ag.blocker_count,
                "unknown_count": ag.unknown_count,
                "compat_seam_count": ag.compat_seam_count,
                "blocker_reasons": ag.blocker_reasons,
                "unknown_reasons": ag.unknown_reasons,
                "compat_seam_reasons": ag.compat_seam_reasons,
                "defer_to_provider": ag.defer_to_provider,
                "gate_evaluated_at_monotonic": ag.gate_evaluated_at_monotonic,
                "gate_evaluated_at_wall": ag.gate_evaluated_at_wall,
            }

        # Sprint F3.11: Dispatch parity preview — diagnostic only, no execute_with_limits
        if pd.dispatch_parity is not None:
            result["dispatch_parity"] = {
                "readiness": pd.dispatch_parity.readiness,
                "dispatch_path": pd.dispatch_parity.dispatch_path,
                "canonical_count": pd.dispatch_parity.canonical_count,
                "runtime_only_count": pd.dispatch_parity.runtime_only_count,
                "satisfied_count": pd.dispatch_parity.satisfied_count,
                "blocked_count": pd.dispatch_parity.blocked_count,
                "runtime_only_handlers": pd.dispatch_parity.runtime_only_handlers,
                "blockers": pd.dispatch_parity.blockers,
                "pruned_tools": pd.dispatch_parity.pruned_tools,
                "will_be_pruned": pd.dispatch_parity.will_be_pruned,
                "control_mode": pd.dispatch_parity.control_mode,
            }

            # Sprint F9: Execution context readiness — separated capability/correlation/audit
            # Exposed as separate section for clarity and future F9 cutover readiness
            if pd.dispatch_parity.execution_context is not None:
                ec = pd.dispatch_parity.execution_context
                result["execution_context"] = {
                    "capability_ready": ec.capability_ready,
                    "capability_missing": ec.capability_missing,
                    "correlation_ready": ec.correlation_ready,
                    "run_id_present": ec.run_id_present,
                    "branch_id_present": ec.branch_id_present,
                    "provider_id_present": ec.provider_id_present,
                    "action_id_present": ec.action_id_present,
                    "correlation_note": ec.correlation_note,
                    "audit_ready": ec.audit_ready,
                    "exec_logger_note": ec.exec_logger_note,
                    "canonical_tool_dispatch": ec.canonical_tool_dispatch,
                    "runtime_only_compat_dispatch": ec.runtime_only_compat_dispatch,
                    "blocker_matrix": ec.blocker_matrix,
                }

        # Sprint F3.5-F3.6: Provider readiness preview — diagnostic only, no activation
        if pd.provider_readiness is not None:
            result["provider_readiness"] = {
                "readiness": pd.provider_readiness.readiness,
                "has_recommendation": pd.provider_readiness.has_recommendation,
                "recommendation": pd.provider_readiness.recommendation,
                "lifecycle_ready": pd.provider_readiness.lifecycle_ready,
                "control_ready": pd.provider_readiness.control_ready,
                "thermal_safe": pd.provider_readiness.thermal_safe,
                "has_facts": pd.provider_readiness.has_facts,
                "blockers": pd.provider_readiness.blockers,
                "unknowns": pd.provider_readiness.unknowns,
                "next_phase_hint": pd.provider_readiness.next_phase_hint,
                "deferred_reasons": pd.provider_readiness.deferred_reasons,
            }

        # Sprint F3.13: Provider runtime facts — standalone top-level section
        # Exposes runtime_facts bundle directly for diagnostic access and downstream sprints.
        # This is distinct from provider_readiness.runtime_* fields which are embedded
        # per-dimension facts. The top-level runtime_facts provides the full bundle
        # for cases where the complete fact set is needed.
        if pd.runtime_facts is not None:
            result["runtime_facts"] = pd.runtime_facts.to_dict()

        return result

    # ── Sprint 8VN: Correlation + Hypothesis seams ──────────────────────────

    def compute_sprint_intelligence(self) -> dict[str, Any]:
        """
        Sprint 8VN: Lazy fail-soft computation of correlation + hypothesis seams.

        Returns a dict with:
        - correlation: from correlate_findings() — full second-order condensation
        - hypothesis_pack: from build_hypothesis_pack() — operator shortlist + actionability
        - branch_value: feed vs public branch value comparison
        - signal_path: dominant signal path, next pivot, corroboration health
        - feed_verdict: aggregated feed economics verdict across cycles
        - public_verdict: aggregated public branch verdict across cycles

        All computation is bounded and M1 8GB safe:
        - correlation: max 500 findings
        - hypothesis: max 200 finding texts
        - feed/public verdict accumulation: max 10 entries each
        - no model dependency
        - fail-soft throughout
        """
        findings = getattr(self, "_all_findings", []) or []

        result: dict[str, Any] = {
            "correlation": None,
            "hypothesis_pack": None,
            "branch_value": None,
            "signal_path": None,
            "feed_verdict": None,
            "public_verdict": None,
        }

        # ── Sprint F207J-A: Lane verdict (CT/WAYBACK/PASSIVE_DNS/BLOCKCHAIN) ──
        # Computed BEFORE the findings-based early return — lane_verdict does not
        # depend on _all_findings and must appear in intelligence even for empty sprints.
        try:
            lane_vlist: list[tuple[str, int, int, int, int]] = getattr(self, '_lane_verdicts', []) or []
            if lane_vlist:
                verdict_tags: dict[str, int] = {}
                total_signal = 0
                total_quality = 0
                for tag, sig, fb_use, fb_waste, qual in lane_vlist:
                    verdict_tags[tag] = verdict_tags.get(tag, 0) + sig
                    total_signal += sig
                    total_quality += qual
                dominant_tag = max(verdict_tags, key=verdict_tags.get) if verdict_tags else "none"
                avg_quality = total_quality / len(lane_vlist) if lane_vlist else 0.0
                result["lane_verdict"] = {
                    "dominant_tag": dominant_tag,
                    "cycle_count": len(lane_vlist),
                    "total_signal_strength": total_signal,
                    "tag_distribution": verdict_tags,
                    "avg_quality": avg_quality,
                    "ct_findings": self._result.lane_ct_accepted_findings,
                    "wayback_findings": self._result.lane_wayback_accepted_findings,
                    "pdns_findings": self._result.lane_pdns_accepted_findings,
                    "blockchain_findings": self._result.lane_blockchain_accepted_findings,
                    "ipfs_findings": self._result.lane_ipfs_accepted_findings,
                    # Sprint F214D: CT Bridge Loss Audit telemetry
                    "ct_loss_stage": getattr(self._result, 'ct_loss_stage', 'no_loss'),
                    "ct_bridge_invoked": getattr(self._result, 'ct_bridge_invoked', False),
                    "ct_raw_count": getattr(self._result, 'ct_raw_count', 0),
                    "ct_raw_sample_count": getattr(self._result, 'ct_raw_sample_count', 0),
                    "ct_candidates_built": getattr(self._result, 'ct_candidates_built', 0),
                    "ct_bridge_rejections_count": getattr(self._result, 'ct_bridge_rejections_count', 0),
                    "ct_candidates_accumulated": getattr(self._result, 'ct_candidates_accumulated', 0),
                    "ct_candidates_stored": getattr(self._result, 'ct_candidates_stored', 0),
                    "ct_storage_rejected": getattr(self._result, 'ct_storage_rejected', 0),
                    # Sprint F226C: CT bridge acceptance diagnostics
                    "ct_bridge_candidate_count": getattr(self._result, 'ct_candidate_count', 0),
                    "ct_bridge_valid_domain_count": getattr(self._result, 'ct_valid_domain_count', 0),
                    "ct_bridge_build_success_count": getattr(self._result, 'ct_bridge_build_success_count', 0),
                    "ct_bridge_quality_rejected_count": getattr(self._result, 'ct_bridge_quality_rejected_count', 0),
                    # F231B: CT expansion clue summary — domain expansion evidence visible even when accepted=0
                    "ct_expansion_clues_count": getattr(self._result, 'ct_expansion_clues_count', 0),
                    "ct_valid_public_domains": getattr(self._result, 'ct_valid_public_domains', 0),
                    "ct_wildcard_domains": getattr(self._result, 'ct_wildcard_domains', 0),
                    "ct_private_reserved_domains": getattr(self._result, 'ct_private_reserved_domains', 0),
                    "ct_duplicate_candidates": getattr(self._result, 'ct_duplicate_candidates', 0),
                    "ct_loss_stage": getattr(self._result, 'ct_loss_stage', 'no_loss'),
                    # Sprint F216G: Quality gate rejection summaries by category
                    "quality_rejection_summary_by_family": getattr(self._result, 'quality_rejection_summary_by_family', {}),
                    "duplicate_rejection_summary_by_family": getattr(self._result, 'duplicate_rejection_summary_by_family', {}),
                    "low_information_by_family": getattr(self._result, 'low_information_by_family', {}),
                    "quality_rejection_ledger_size": len(getattr(self._result, 'quality_rejection_ledger', ())),
                    # F231C: Wayback advisory evidence surface
                    "wayback_advisory_clues_count": getattr(self._result, 'wayback_advisory_clues_count', 0),
                    "wayback_changed_url_count": getattr(self._result, 'wayback_changed_url_count', 0),
                    "wayback_added_url_count": getattr(self._result, 'wayback_added_url_count', 0),
                    "wayback_digest_changed_count": getattr(self._result, 'wayback_digest_changed_count', 0),
                    "wayback_unchanged_rejected": getattr(self._result, 'wayback_unchanged_rejected', 0),
                    # F231C: PassiveDNS advisory evidence surface
                    "passive_dns_advisory_clues_count": getattr(self._result, 'passive_dns_advisory_clues_count', 0),
                    "passive_dns_private_ip_rejected": getattr(self._result, 'passive_dns_private_ip_rejected', 0),
                    "passive_dns_empty_ip_rejected": getattr(self._result, 'passive_dns_empty_ip_rejected', 0),
                }
        except Exception:
            result["lane_verdict"] = None

        if not findings:
            return result

        # ── Correlation seam (second-order condensation) ────────────────────
        try:
            correlate_fn = _import_correlate_findings()
            corr = correlate_fn(findings[:500])
            result["correlation"] = {
                "risk_score": round(corr.risk_score, 3),
                "verdict": corr.verdict,
                "anomaly_count": corr.anomaly_count,
                "top_themes": list(corr.top_themes[:5]),
                "theme_count": len(corr.themes),
                # Sprint 8VN §C: second-order — actionable condensation
                "signal_quality": getattr(corr, 'signal_quality', "weak"),
                "cross_source_confidence": round(getattr(corr, 'cross_source_confidence', 0.0), 3),
                "campaign_confidence": round(getattr(corr, 'campaign_confidence', 0.0), 3),
                "dominant_cluster": getattr(corr, 'dominant_cluster', None),
                "so_what": getattr(corr, 'so_what', ""),
                "what_matters_first": getattr(corr, 'what_matters_first', ""),
                "operator_shortlist": [
                    {"action": item.get("action", ""), "target": item.get("target", "")[:80],
                     "rationale": item.get("rationale", "")}
                    for item in (getattr(corr, 'operator_shortlist', None) or [])[:3]
                    if isinstance(item, dict)
                ],
                "confidence_note": getattr(corr, 'confidence_note', ""),
                "corroborated_iocs_count": len(getattr(corr, 'corroborated_iocs', []) or []),
                "top_priority_pivots_count": len(getattr(corr, 'top_priority_pivots', []) or []),
            }
        except Exception:
            result["correlation"] = None

        # ── Hypothesis pack seam (operator shortlist) ───────────────────────
        try:
            HypEng = _import_hypothesis_engine()
            eng = HypEng()
            finding_texts: list[str] = []
            for f in findings[:200]:
                desc = f.get("description", "")
                src = f.get("source", "")
                finding_texts.append(f"[{src}] {desc}" if (src and desc) else (desc or ""))
            if finding_texts:
                pack = eng.build_hypothesis_pack(finding_texts)
                result["hypothesis_pack"] = {
                    "hypothesis_count": len(pack.hypotheses),
                    "query_count": len(pack.suggested_queries),
                    "ioc_follow_ups": len(pack.ioc_follow_ups),
                    "source_hints_count": len(pack.source_hints),
                    "provenance": pack.provenance,
                    "signal_quality": getattr(pack, 'signal_quality', "weak"),
                    "what_matters_first": getattr(pack, 'what_matters_first', ""),
                    "confidence_note": getattr(pack, 'confidence_note', ""),
                    "top_queries": [
                        {"query": q.get("query", ""), "rationale": q.get("rationale", "")[:80]}
                        for q in (pack.suggested_queries or [])[:5]
                        if isinstance(q, dict)
                    ],
                    # Sprint F187E: operator_shortlist is now a @property on HypothesisPack
                    # returning scheduler-consumable shape directly: action, target, rationale
                    "operator_shortlist": [
                        {"action": item.get("action", ""),
                         "target": item.get("target", "")[:80],
                         "rationale": item.get("rationale", "")}
                        for item in (getattr(pack, 'operator_shortlist', None) or [])[:3]
                        if isinstance(item, dict)
                    ],
                    # Sprint F191D: discarded_as_redundant — what was dropped and why
                    # Bounded: max 3 items, fail-soft, memory-cheap
                    "discarded_as_redundant": [
                        {"action_type": item.get("action_type", ""),
                         "query": item.get("query", "")[:120],
                         "reason_discarded": item.get("reason_discarded", ""),
                         "pivot_type": item.get("pivot_type", ""),
                         "priority": item.get("priority", 0.0)}
                        for item in (getattr(pack, 'discarded_as_redundant', lambda max_items=3: [])(
                            max_items=3) or [])[:3]
                        if isinstance(item, dict)
                    ],
                }
        except Exception:
            result["hypothesis_pack"] = None

        # ── Feed branch verdict (aggregated across cycles) ─────────────────
        try:
            feed_vlist: list[tuple[str, int, int, int, int]] = getattr(self, '_feed_verdicts', []) or []
            if feed_vlist:
                verdict_tags: dict[str, int] = {}
                total_signal = 0
                total_fallback_waste = 0
                for tag, sig, fb_use, fb_waste, qual in feed_vlist:
                    verdict_tags[tag] = verdict_tags.get(tag, 0) + 1
                    total_signal += sig
                    total_fallback_waste += fb_waste
                dominant_tag = max(verdict_tags, key=verdict_tags.get) if verdict_tags else ""
                avg_quality = round(
                    sum(v[4] for v in feed_vlist) / len(feed_vlist), 2
                ) if feed_vlist else 0.0
                result["feed_verdict"] = {
                    "dominant_tag": dominant_tag,
                    "cycle_count": len(feed_vlist),
                    "total_signal_strength": total_signal,
                    "total_fallback_waste": total_fallback_waste,
                    "avg_quality": avg_quality,
                    "tag_distribution": verdict_tags,
                }
        except Exception:
            result["feed_verdict"] = None

        # ── Public branch verdict (aggregated across cycles) ───────────────
        try:
            pub_vlist: list[dict] = getattr(self, '_public_verdicts', []) or []
            if pub_vlist:
                waste_ratios = [v.get("waste_ratio", 0.0) for v in pub_vlist if "waste_ratio" in v]
                value_ratios = [v.get("value_ratio", 0.0) for v in pub_vlist if "value_ratio" in v]
                corroborations = [v.get("corroboration_vs_burn", 0.0) for v in pub_vlist if "corroboration_vs_burn" in v]
                next_actions = [v.get("public_next_action", "") for v in pub_vlist if "public_next_action" in v]
                confidence_notes = [v.get("public_confidence_note", "") for v in pub_vlist if "public_confidence_note" in v]
                # Sprint F150L: additional economics signals
                squandered_hits = [v.get("discovery_squandered", 0) for v in pub_vlist if "discovery_squandered" in v]
                noise_ratios = [v.get("noise_fetch_ratio", 0.0) for v in pub_vlist if "noise_fetch_ratio" in v]
                dominant_action = max(set(next_actions), key=next_actions.count) if next_actions else ""
                dominant_conf = max(set(confidence_notes), key=confidence_notes.count) if confidence_notes else ""
                result["public_verdict"] = {
                    "cycle_count": len(pub_vlist),
                    "avg_waste_ratio": round(sum(waste_ratios) / len(waste_ratios), 3) if waste_ratios else 0.0,
                    "avg_value_ratio": round(sum(value_ratios) / len(value_ratios), 3) if value_ratios else 0.0,
                    "avg_corroboration_vs_burn": round(sum(corroborations) / len(corroborations), 3) if corroborations else 0.0,
                    "avg_discovery_squandered": round(sum(squandered_hits) / len(squandered_hits), 2) if squandered_hits else 0.0,
                    "total_discovery_squandered": sum(squandered_hits),
                    "avg_noise_fetch_ratio": round(sum(noise_ratios) / len(noise_ratios), 3) if noise_ratios else 0.0,
                    "dominant_next_action": dominant_action,
                    "dominant_confidence_note": dominant_conf,
                    "action_distribution": {a: next_actions.count(a) for a in set(next_actions)},
                }
        except Exception:
            result["public_verdict"] = None

        # ── Sprint F207J-A: Lane verdict (CT/WAYBACK/PASSIVE_DNS/BLOCKCHAIN) ──
        try:
            lane_vlist: list[tuple[str, int, int, int, int]] = getattr(self, '_lane_verdicts', []) or []
            if lane_vlist:
                verdict_tags: dict[str, int] = {}
                total_signal = 0
                total_quality = 0
                for tag, sig, fb_use, fb_waste, qual in lane_vlist:
                    verdict_tags[tag] = verdict_tags.get(tag, 0) + sig
                    total_signal += sig
                    total_quality += qual
                dominant_tag = max(verdict_tags, key=verdict_tags.get) if verdict_tags else "none"
                avg_quality = total_quality / len(lane_vlist) if lane_vlist else 0.0
                result["lane_verdict"] = {
                    "dominant_tag": dominant_tag,
                    "cycle_count": len(lane_vlist),
                    "total_signal_strength": total_signal,
                    "tag_distribution": verdict_tags,
                    "avg_quality": avg_quality,
                    "ct_findings": self._result.lane_ct_accepted_findings,
                    "wayback_findings": self._result.lane_wayback_accepted_findings,
                    "pdns_findings": self._result.lane_pdns_accepted_findings,
                    "blockchain_findings": self._result.lane_blockchain_accepted_findings,
                    "ipfs_findings": self._result.lane_ipfs_accepted_findings,
                    # Sprint F214D: CT Bridge Loss Audit telemetry
                    "ct_loss_stage": getattr(self._result, 'ct_loss_stage', 'no_loss'),
                    "ct_bridge_invoked": getattr(self._result, 'ct_bridge_invoked', False),
                    "ct_raw_count": getattr(self._result, 'ct_raw_count', 0),
                    "ct_raw_sample_count": getattr(self._result, 'ct_raw_sample_count', 0),
                    "ct_candidates_built": getattr(self._result, 'ct_candidates_built', 0),
                    "ct_bridge_rejections_count": getattr(self._result, 'ct_bridge_rejections_count', 0),
                    "ct_candidates_accumulated": getattr(self._result, 'ct_candidates_accumulated', 0),
                    "ct_candidates_stored": getattr(self._result, 'ct_candidates_stored', 0),
                    "ct_storage_rejected": getattr(self._result, 'ct_storage_rejected', 0),
                    # Sprint F226C: CT bridge acceptance diagnostics
                    "ct_bridge_candidate_count": getattr(self._result, 'ct_candidate_count', 0),
                    "ct_bridge_valid_domain_count": getattr(self._result, 'ct_valid_domain_count', 0),
                    "ct_bridge_build_success_count": getattr(self._result, 'ct_bridge_build_success_count', 0),
                    "ct_bridge_quality_rejected_count": getattr(self._result, 'ct_bridge_quality_rejected_count', 0),
                    # F231B: CT expansion clue summary — domain expansion evidence visible even when accepted=0
                    "ct_expansion_clues_count": getattr(self._result, 'ct_expansion_clues_count', 0),
                    "ct_valid_public_domains": getattr(self._result, 'ct_valid_public_domains', 0),
                    "ct_wildcard_domains": getattr(self._result, 'ct_wildcard_domains', 0),
                    "ct_private_reserved_domains": getattr(self._result, 'ct_private_reserved_domains', 0),
                    "ct_duplicate_candidates": getattr(self._result, 'ct_duplicate_candidates', 0),
                    "ct_loss_stage": getattr(self._result, 'ct_loss_stage', 'no_loss'),
                    # F231C: Wayback advisory evidence surface
                    "wayback_advisory_clues_count": getattr(self._result, 'wayback_advisory_clues_count', 0),
                    "wayback_changed_url_count": getattr(self._result, 'wayback_changed_url_count', 0),
                    "wayback_added_url_count": getattr(self._result, 'wayback_added_url_count', 0),
                    "wayback_digest_changed_count": getattr(self._result, 'wayback_digest_changed_count', 0),
                    "wayback_unchanged_rejected": getattr(self._result, 'wayback_unchanged_rejected', 0),
                    # F231C: PassiveDNS advisory evidence surface
                    "passive_dns_advisory_clues_count": getattr(self._result, 'passive_dns_advisory_clues_count', 0),
                    "passive_dns_private_ip_rejected": getattr(self._result, 'passive_dns_private_ip_rejected', 0),
                    "passive_dns_empty_ip_rejected": getattr(self._result, 'passive_dns_empty_ip_rejected', 0),
                }
        except Exception:
            result["lane_verdict"] = None

        # ── Signal path + branch mix health ────────────────────────────────
        try:
            corr = result.get("correlation") or {}
            sig_quality = corr.get("signal_quality", "weak")
            cross_conf = corr.get("cross_source_confidence", 0.0)
            camp_conf = corr.get("campaign_confidence", 0.0)
            # Sprint F207J-A: Include lane findings in total findings count
            feed_f = self._result.accepted_findings or 0
            pub_f = self._result.public_accepted_findings or 0
            lane_f = (
                self._result.lane_ct_accepted_findings
                + self._result.lane_wayback_accepted_findings
                + self._result.lane_pdns_accepted_findings
                + self._result.lane_blockchain_accepted_findings
            )
            total_findings = feed_f + pub_f + lane_f

            # Dominant signal path
            if sig_quality == "strong":
                dominant_path = "corroborated" if cross_conf > 0.5 else "high_confidence"
            elif sig_quality == "mixed":
                dominant_path = "multi_source" if cross_conf > 0.3 else "degraded"
            else:
                dominant_path = "weak_noisy"

            # Next pivot derived from correlation
            top_pivots_count = corr.get("top_priority_pivots_count", 0)
            next_pivot = "pivot_immediately" if (top_pivots_count > 0 and sig_quality != "weak") else "hold_pivoting"

            # Corroboration score
            corroboration_score = round(cross_conf * 0.6 + camp_conf * 0.4, 3)

            # Branch mix health
            if total_findings == 0:
                branch_mix_health = "empty"
            elif feed_f == 0 and pub_f == 0:
                branch_mix_health = "empty"
            elif feed_f == 0:
                branch_mix_health = "public_only" if pub_f > 3 else "public_sparse"
            elif pub_f == 0:
                branch_mix_health = "feed_only" if feed_f > 3 else "feed_sparse"
            else:
                ratio = feed_f / pub_f
                if ratio > 5:
                    branch_mix_health = "feed_heavy"
                elif ratio < 0.2:
                    branch_mix_health = "public_heavy"
                elif sig_quality == "strong":
                    branch_mix_health = "healthy_balanced"
                else:
                    branch_mix_health = "balanced_low_yield"

            result["signal_path"] = {
                "dominant_signal_path": dominant_path,
                "next_pivot_recommendation": next_pivot,
                "corroboration_score": corroboration_score,
                "branch_mix_health": branch_mix_health,
                "is_noisy": sig_quality == "weak" and cross_conf < 0.2,
                "is_corroborated": cross_conf > 0.4,
                "campaign_signal": camp_conf > 0.3,
            }
        except Exception:
            result["signal_path"] = None

        # ── Branch value comparison ────────────────────────────────────────
        try:
            feed_f = self._result.accepted_findings or 0
            pub_f = self._result.public_accepted_findings or 0
            feed_h = self._result.total_pattern_hits or 0
            pub_h = self._result.public_matched_patterns or 0
            total = feed_f + pub_f
            if total > 0:
                feed_pct = round(feed_f / total * 100, 1)
                pub_pct = round(pub_f / total * 100, 1)
            else:
                feed_pct = pub_pct = 0.0
            if pub_f > feed_f * 1.5:
                branch_verdict = "public_dominant"
                recommendation = "expand_public_branch"
            elif feed_f > pub_f * 1.5:
                branch_verdict = "feed_dominant"
                recommendation = "expand_feed_branch"
            else:
                branch_verdict = "balanced"
                recommendation = "maintain_both"
            result["branch_value"] = {
                "feed_findings": feed_f,
                "public_findings": pub_f,
                "feed_pattern_hits": feed_h,
                "public_pattern_hits": pub_h,
                "feed_pct": feed_pct,
                "public_pct": pub_pct,
                "branch_verdict": branch_verdict,
                "recommendation": recommendation,
            }
        except Exception:
            result["branch_value"] = None

        # ── Sprint F150L: Second-order condensed summary ──────────────────
        # Derived entirely from already-computed sections above.
        # purely additive, fail-soft, bounded, no new model/persistence deps.
        try:
            sig_path = result.get("signal_path") or {}
            br_val = result.get("branch_value") or {}
            feed_v = result.get("feed_verdict") or {}
            pub_v = result.get("public_verdict") or {}
            corr = result.get("correlation") or {}
            hyp = result.get("hypothesis_pack") or {}

            dominant_signal = sig_path.get("dominant_signal_path", "unknown")
            branch_verdict = br_val.get("branch_verdict", "unknown")
            corroboration_score = sig_path.get("corroboration_score", 0.0)
            is_noisy = sig_path.get("is_noisy", False)
            is_corroborated = sig_path.get("is_corroborated", False)
            campaign_signal = sig_path.get("campaign_signal", False)
            branch_mix = sig_path.get("branch_mix_health", "unknown")
            next_pivot = sig_path.get("next_pivot_recommendation", "unknown")
            avg_corr_vs_burn = pub_v.get("avg_corroboration_vs_burn", 0.0)
            avg_noise = pub_v.get("avg_noise_fetch_ratio", 0.0)
            dominant_action = pub_v.get("dominant_next_action", "")
            dominant_conf = pub_v.get("dominant_confidence_note", "")
            feed_tag = feed_v.get("dominant_tag", "")
            feed_avg_qual = feed_v.get("avg_quality", 0.0)
            risk_score = corr.get("risk_score", 0.0)
            hyp_count = hyp.get("hypothesis_count", 0)
            # Sprint F186F: hypothesis fallback for what_matters_first when correlation is absent
            what_matters = corr.get("what_matters_first") or hyp.get("what_matters_first") or ""
            op_shortlist = corr.get("operator_shortlist", []) or hyp.get("operator_shortlist", [])
            first_action = op_shortlist[0].get("action", "") if op_shortlist else ""
            backup_action = op_shortlist[1].get("action", "") if len(op_shortlist) > 1 else ""

            # Sprint posture: corroborated / mixed / noisy / depleted
            total_findings = (br_val.get("feed_findings", 0) or 0) + (br_val.get("public_findings", 0) or 0)
            if total_findings == 0:
                posture = "depleted"
            elif is_noisy and avg_noise > 0.4:
                posture = "noisy"
            elif is_corroborated and corroboration_score > 0.35:
                posture = "corroborated"
            elif campaign_signal and avg_corr_vs_burn > 0.35:
                posture = "mixed"
            elif dominant_signal in ("corroborated", "high_confidence"):
                posture = "corroborated"
            elif dominant_signal == "weak_noisy":
                posture = "noisy"
            else:
                posture = "mixed"

            # Sprint F151A: Additive derived fields — bounded, derived-only, fail-soft
            # export_ready: findings accumulated and verdict computed → ready for export
            export_ready = total_findings > 0 and bool(corr or hyp)
            # proof_grade: evidence quality heuristic from corroboration + noise
            if is_corroborated and corroboration_score > 0.4 and avg_noise < 0.3:
                proof_grade = "strong"
            elif is_corroborated and corroboration_score > 0.25:
                proof_grade = "moderate"
            elif total_findings > 0:
                proof_grade = "weak"
            else:
                proof_grade = "none"
            # operator_ready: operator shortlist populated with actionable next step
            operator_ready = bool(op_shortlist and first_action)
            # decision_pressure: high when posture != "corroborated" but findings > 0
            decision_pressure = "high" if posture in ("noisy", "mixed") and total_findings > 0 else "low"

            # Sprint F155: Second-order branch conversion health
            # Derived: is_corroborated × corroboration_score × (1 - avg_noise)
            avg_noise = pub_v.get("avg_noise_fetch_ratio", 0.0)
            branch_conversion_health = round(
                (1.0 if is_corroborated else 0.0) * corroboration_score * (1.0 - avg_noise), 3
            )

            # Sprint F155: Second-order discovery efficiency
            # Derived: total_findings / (1 + squandered) — ratio of usable signal vs. waste
            total_squandered = pub_v.get("total_discovery_squandered", 0) or 0
            discovery_efficiency = round(
                total_findings / (1 + total_squandered), 3
            ) if total_findings > 0 else 0.0

            result["sprint_verdict"] = {
                "posture": posture,
                "dominant_signal": dominant_signal,
                "branch_verdict": branch_verdict,
                "branch_mix": branch_mix,
                "corroboration_score": corroboration_score,
                "is_corroborated": is_corroborated,
                "campaign_signal": campaign_signal,
                "next_pivot": next_pivot,
                "dominant_action": dominant_action,
                "first_action": first_action,
                "backup_action": backup_action,
                "intel_what_matters": what_matters,  # Sprint F186F: corr → hyp fallback
                "confidence": dominant_conf,
                "feed_tag": feed_tag,
                "feed_avg_quality": feed_avg_qual,
                "risk_score": risk_score,
                "hypothesis_count": hyp_count,
                "total_findings": total_findings,
                # Sprint F151A: additive derived
                "export_ready": export_ready,
                "proof_grade": proof_grade,
                "operator_ready": operator_ready,
                "decision_pressure": decision_pressure,
                # Sprint F155: second-order derived (from pub_v + sig_path)
                "branch_conversion_health": branch_conversion_health,
                "discovery_efficiency": discovery_efficiency,
            }
        except Exception:
            # Second-order condensation is purely additive — never crashes
            pass

        return result

    # ── Internal reset ────────────────────────────────────────────────────

    def _reset_result(self) -> None:
        self._seen_hashes.clear()
        self._entries_per_source.clear()
        self._hits_per_source.clear()
        self._stop_requested = False
        self._result = SprintSchedulerResult()
        # Sprint 8VD: Clear Arrow batch state
        self._arrow_batch.clear()
        self._arrow_last_flush = 0.0
        # Sprint 8RA: Close DuckDB read connection
        if self._duckdb_read_con is not None:
            try:
                self._duckdb_read_con.close()
            except Exception:
                pass
            self._duckdb_read_con = None
        # Sprint 8VM: Clear shadow pre-decision summary
        self._shadow_pd_summary = None
        # Sprint 8VQ: Clear advisory gate snapshot
        self._advisory_gate_snapshot = None
        # Sprint F199A: Clear per-source quality feedback for new sprint
        self._source_quality_feedback.clear()
        # Sprint F200A: Clear prefetch oracle state for new sprint
        if self._prefetch_oracle is not None:
            try:
                self._prefetch_oracle.reset()
            except Exception:
                pass  # Advisory only — never affect scheduler
        # Sprint 8VN: Clear intelligence caches and findings accumulator
        self._all_findings.clear()
        # Sprint F216E: Clear feed dominance budget state for new sprint
        self._feed_accepted_per_source.clear()
        self._feed_budget_triggered = False
        self._correlation_cache = None
        self._hypothesis_pack_cache = None
        self._branch_value_summary = None
        # Sprint 8VN §C: Clear branch verdict accumulators
        self._feed_verdicts.clear()
        self._public_verdicts.clear()
        # Sprint F207H: Reset public pipeline outcome
        self._public_outcome = None
        # Sprint F216C: Reset PipelineRunResult reference for stage machine
        self._public_pipeline_result = None
        # Sprint F210A: Reset query for new sprint
        self._query = ""
        # Sprint F207M-A: Reset nonfeed pre-dispatch guard for new sprint
        self._nonfeed_predispatch_done = False
        # Sprint F207S-B: Reset scheduler-owned barrier delayed flag for new sprint
        self._prewindup_barrier_delayed = False
        # Sprint F160C: Clear per-sprint source economics
        self._source_economics.clear()
        # Sprint F203D: Reset evidence chain builder for new sprint
        try:
            from hledac.universal.knowledge.evidence_chain import reset_global_builder
            reset_global_builder()
        except Exception:
            pass
        # Sprint F195C: Reset cross-sprint entity memory idempotency tracker
        try:
            from hledac.universal.knowledge.graph_service import reset_session
            reset_session()
        except Exception:
            pass
        # Sprint F202J: Reset governor telemetry (but keep singleton instance)
        self._governor = None  # Will be re-initialized on next run()
        # Sprint F205F: Reset sidecar dispatcher tracking
        # F206S: hasattr guard needed — _sidecar_dispatcher is set in run() after _reset_result() is called
        if hasattr(self, "_sidecar_dispatcher") and self._sidecar_dispatcher is not None:
            self._sidecar_dispatcher.reset()
        # Sprint F204J: Mission budget tracking
        # F205F: sidecars_skipped written by SidecarDispatcher via result_sink.
        # Fallback if dispatcher was never called.
        _existing_skipped = getattr(self._result, "sidecars_skipped", None)
        if _existing_skipped is not None:
            self._result.sidecars_skipped = _existing_skipped
        self._peak_rss_gib = 0.0
        # Sprint F207A: Reset multi-source lane outcomes for new sprint
        self._lane_outcomes = ()
        # Sprint R8: Reset active-cycle CT→PDNS pivot flag
        self._ct_pdns_active_done = False
        # Sprint F207K-A: Reset lane rejection tracking
        self._lane_rejections = []
        self._lane_rejections_total_seen = 0
        self._lane_rejections_dropped = 0
        self._result.acquisition_lane_outcomes = ()
        # Sprint F207J-A: Clear lane verdict accumulators
        self._lane_verdicts.clear()
        self._result.lane_ct_accepted_findings = 0
        self._result.lane_wayback_accepted_findings = 0
        self._result.lane_pdns_accepted_findings = 0
        self._result.lane_blockchain_accepted_findings = 0
        self._result.lane_ipfs_accepted_findings = 0
        # Sprint F229: Clear wayback/pdns telemetry
        self._result.wayback_attempted = False
        self._result.wayback_raw_count = 0
        self._result.wayback_candidates_built = 0
        self._result.wayback_accepted_count = 0
        self._result.passive_dns_attempted = False
        self._result.passive_dns_raw_count = 0
        self._result.passive_dns_candidates_built = 0
        self._result.passive_dns_accepted_count = 0
        # Sprint F216A: Clear event log for new sprint
        self._result.source_family_events.clear()

    # -------------------------------------------------------------------------
    # Sprint F216A: Source family event log helpers
    # -------------------------------------------------------------------------

    def _emit_source_family_event(
        self,
        family: str,
        event: str,
        count: int = 0,
        reason: str = "",
        terminal_state: str = "",
    ) -> None:
        """
        Emit a bounded source-family lifecycle event for diagnostics.

        Caps at MAX_SOURCE_FAMILY_EVENTS (200), dropping oldest when full.
        Event dict contains: family, event, count, reason, terminal_state, ts_monotonic
        """
        evt = {
            "family": family,
            "event": event,
            "count": count,
            "reason": reason,
            "terminal_state": terminal_state,
            "ts_monotonic": _time.monotonic(),
        }
        events = self._result.source_family_events
        if len(events) >= self._result.MAX_SOURCE_FAMILY_EVENTS:
            events.pop(0)
        events.append(evt)


# ---------------------------------------------------------------------------
# Convenience top-level function
# ---------------------------------------------------------------------------

async def async_run_tiered_feed_sprint_once(
    sources: Sequence[str],
    config: Optional[SprintSchedulerConfig] = None,
    lifecycle: Optional[object] = None,
    now_monotonic: Optional[float] = None,
    query: str = "",
    duckdb_store: Any = None,
) -> SprintSchedulerResult:
    """
    One-shot tiered feed sprint.

    Creates its own lifecycle if none provided.
    """
    if config is None:
        config = SprintSchedulerConfig()
    if lifecycle is None:
        from hledac.universal.runtime.sprint_lifecycle import SprintLifecycleManager
        lifecycle = SprintLifecycleManager(
            sprint_duration_s=config.sprint_duration_s,
            windup_lead_s=config.windup_lead_s,
        )

    scheduler = SprintScheduler(config)
    return await scheduler.run(lifecycle, sources, now_monotonic, query, duckdb_store)
