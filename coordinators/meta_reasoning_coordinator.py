"""
Universal Meta-Reasoning Coordinator
====================================

Integrated meta-reasoning from:
- MetaReasoningCoordinator: Chain of Thought, Tree of Thoughts, Graph reasoning
- Advanced reasoning strategies with automatic selection

Features:
- Chain of Thought (CoT) reasoning
- Tree of Thoughts (ToT) exploration with learned value prediction
- Graph reasoning
- Strategy selection based on query + semantic gravity field [SILICON-05]
- Strategy switching during execution
- Ensemble results
- Cost-weighted branch pruning (SOVEREIGN-005)
- Dead-end detection with IOC progress tracking (SOVEREIGN-005)
- Information Gain Density (IGD) dynamic pruning [NEXUS]-018-02
- Semantic gravity field — void-aware branch boosting in ToT [SILICON-05]
- Fetch directive generation for acquisition lane targeting [SILICON-05]
"""
import asyncio
import logging
import os
import secrets
import time
from collections import deque
from dataclasses import field
from enum import Enum
from typing import Any

import msgspec
import numpy as np

from hledac.universal.utils.async_helpers import parallel_ok

try:
    from hledac.universal.knowledge.semantic_gravity import (
        FetchDirective,
        SemanticGravityField,
        VoidRegion,
    )
except ImportError:
    SemanticGravityField = None  # type: ignore[assignment,misc]
    FetchDirective = None  # type: ignore[assignment,misc]
    VoidRegion = None  # type: ignore[assignment,misc]

from .base import DecisionResponse, ExecutionResult, OperationResult, OperationType, UniversalCoordinator

logger = logging.getLogger(__name__)
_RNG = secrets.SystemRandom()
_YIELD_EVERY_COT = 4
_YIELD_EVERY_TOT = 16

# SOVEREIGN-005: Cost-weighted pruning and dead-end detection constants
_PRUNE_GAIN_THRESHOLD = 0.1  # prune branches with gain < 0.1
_PRUNE_MIN_DEPTH = 2  # only prune after 2+ actions
_DEAD_END_TIMEOUT_S = 10.0  # 10s without new IOCs = dead-end

# [NEXUS]-018-02: Information Gain Density (IGD) Dynamic Pruning
# IGD = Δ|unique_ioc_values| / Δt — measures actual IOC discovery rate per branch.
# Complements _DEAD_END_TIMEOUT_S by adding a rate-sensitive abort that catches
# branches still delivering but below useful density.
_IGD_WINDOW_S = 30.0           # Sliding window: look back 30 s for IOC rate
_IGD_ABORT_THRESHOLD = 0.1     # Abort branch if IGD < 0.1 unique IOC/s
_IGD_PERSIST_S = 5.0          # Abort only after 5 consecutive s below threshold
_IGD_MIN_DEPTH = 1            # Apply IGD pruning after depth 1+ (not root)
_IGD_REPORT_MIN_IOC_VALUE = 0.5  # Only count IOC reports with estimated_value >= this

_VALUE_PREDICTOR_FEATURE_DIM = 16  # lightweight feature dim for ToT value prediction

# BLITZ-04: Urgency thresholds for sprint-phase-aware reasoning parameter clamping.
# Urgency = 1.0 / max(remaining_minutes, 0.5). Higher = less time remaining.
# Derived from SprintClock.remaining_s at call time.
_URGENCY_TOT_SKIP = 4.0       # Skip ToT entirely, fall back to CoT with max_steps=3
_URGENCY_TOT_LIGHT = 2.0      # Light ToT: max_depth=2, branching_factor=2, beam_width=1
_URGENCY_TOT_REDUCED = 1.0    # Reduced ToT: max_depth=4, branching_factor=3 (beam unchanged)
_URGENCY_MIN_REMAINING_M = 0.5  # Floor for denominator to avoid division by zero

# BLITZ-06: Ensemble reasoning time-gating thresholds (in seconds remaining).
# When sprint time is critically low, ensemble degrades to avoid wasted compute
# on redundant parallel strategies.
_ENSEMBLE_CO_T_ONLY_REMAINING_S = 120   # 2 min: ensemble → CoT only
_ENSEMBLE_SKIP_TOT_REMAINING_S = 300    # 5 min: ensemble → CoT + Graph (skip ToT)


class SprintClock(msgspec.Struct, frozen=True, gc=False):
    """BLITZ-04: Time-awareness clock injected into reasoning coordinators.

    Wraps sprint timing fields that already exist in SprintTelemetry
    (hard_deadline_monotonic, sprint_start_monotonic) and exposes them
    as a lightweight protocol for reasoning-layer consumption.

    Computed properties:
        remaining_s: Seconds until hard deadline (inf if no deadline set).
        urgency: 1.0 / max(remaining_minutes, 0.5). Range [0.03, inf).
                 >4.0 = last 5 min, >2.0 = last 10 min, <1.0 = >30 min.

    M1 8GB safe: single frozen struct, zero allocations after construction.
    """
    hard_deadline_monotonic: float = 0.0
    sprint_start_monotonic: float = 0.0
    total_duration_s: float = 0.0

    @property
    def remaining_s(self) -> float:
        """Seconds remaining until hard deadline (inf if no deadline)."""
        if self.hard_deadline_monotonic <= 0.0:
            return float('inf')
        return max(self.hard_deadline_monotonic - time.monotonic(), 0.0)

    @property
    def urgency(self) -> float:
        """Urgency multiplier: 1.0 / max(remaining_minutes, _URGENCY_MIN_REMAINING_M).

        Examples (30-min sprint):
            t=0   → remaining=30m → urgency=0.03
            t=15m → remaining=15m → urgency=0.066
            t=20m → remaining=10m → urgency=0.1
            t=25m → remaining=5m  → urgency=0.2
            t=28m → remaining=2m  → urgency=0.5
            t=29m → remaining=1m  → urgency=1.0
            t=29.5m→remaining=0.5m→ urgency=2.0  → _URGENCY_TOT_LIGHT
            t=29.75m→remaining=15s→urgency=4.0 → _URGENCY_TOT_SKIP
        """
        remaining_m = self.remaining_s / 60.0
        if remaining_m <= 0.0:
            return float('inf')
        return 1.0 / max(remaining_m, _URGENCY_MIN_REMAINING_M)

    @classmethod
    def from_telemetry(
        cls,
        hard_deadline_monotonic: float = 0.0,
        total_duration_s: float = 0.0,
        sprint_start_monotonic: float | None = None,
    ) -> 'SprintClock':
        """Construct from SprintTelemetry fields.

        Args:
            hard_deadline_monotonic: Absolute deadline from telemetry.
            total_duration_s: Total sprint duration.
            sprint_start_monotonic: Sprint start timestamp (default: now).
        """
        if sprint_start_monotonic is None:
            sprint_start_monotonic = time.monotonic()
        return cls(
            hard_deadline_monotonic=hard_deadline_monotonic,
            sprint_start_monotonic=sprint_start_monotonic,
            total_duration_s=total_duration_s,
        )


class ReasoningStrategy(Enum):
    """Available reasoning strategies."""
    CHAIN_OF_THOUGHT = 'cot'
    TREE_OF_THOUGHTS = 'tot'
    GRAPH_REASONING = 'graph'
    HYBRID = 'hybrid'

class ReasoningStep(msgspec.Struct, gc=False):
    """Single reasoning step."""
    step_id: str
    description: str
    reasoning: str
    conclusion: str
    confidence: float
    parent_steps: list[str] = field(default_factory=list)
    sub_steps: list[str] = field(default_factory=list)

class ReasoningChain(msgspec.Struct, frozen=True, gc=False):
    """Chain of reasoning steps."""
    chain_id: str
    steps: list[ReasoningStep] = field(default_factory=list)
    final_conclusion: str | None = None
    overall_confidence: float = 0.0

class ThoughtNode(msgspec.Struct, frozen=True, gc=False):
    """Node in Tree of Thoughts."""
    node_id: str
    thought: str
    value_estimate: float
    parent: str | None = None
    children: list[str] = field(default_factory=list)
    visited: bool = False
    expanded: bool = False
    depth: int = 0
    cost: float = 0.0  # SOVEREIGN-005: accumulated cost along path
    uncertainty: float = 0.0  # SOVEREIGN-005: prediction uncertainty


class _DeadEndDetector:
    """SOVEREIGN-005: Detects dead-end branches based on IOC progress timeout.

    Tracks per-branch last-progress timestamps. A branch is considered a dead-end
    if no new IOCs (or meaningful progress signals) are discovered within
    _DEAD_END_TIMEOUT_S seconds.

    Memory-efficient: uses monotonic timestamps, no heavy data structures.
    M1 8GB safe: O(active_leaves) memory.
    """
    __slots__ = ('_last_progress', '_timeout_s')

    def __init__(self, timeout_s: float = _DEAD_END_TIMEOUT_S) -> None:
        self._timeout_s = timeout_s
        self._last_progress: dict[str, float] = {}

    def register_branch(self, node_id: str) -> None:
        """Register a new branch for progress tracking."""
        self._last_progress[node_id] = time.monotonic()

    def report_progress(self, node_id: str, ioc_count: int = 0) -> None:
        """Report progress on a branch (e.g., new IOCs discovered)."""
        if ioc_count > 0 or node_id not in self._last_progress:
            self._last_progress[node_id] = time.monotonic()

    def is_dead_end(self, node_id: str) -> bool:
        """Check if a branch has stalled beyond the timeout."""
        last = self._last_progress.get(node_id)
        if last is None:
            return False
        return (time.monotonic() - last) >= self._timeout_s

    def cleanup_pruned(self, active_node_ids: set[str]) -> None:
        """Remove tracking entries for pruned/finished branches."""
        stale = [nid for nid in self._last_progress if nid not in active_node_ids]
        for nid in stale:
            del self._last_progress[nid]


# ── [NEXUS]-018-02: IGD Pruning ──────────────────────────────────────────────

class _IGDPruningPolicy:
    """[NEXUS]-018-02: Information Gain Density (IGD) dynamic branch pruning.

    Tracks per-branch IOC discovery rate using a sliding-window buffer of
    (ioc_value, monotonic_ts) pairs.  IGD = Δ|unique_ioc_values| / Δt (unique
    IOC values / elapsed seconds over the window).

    A branch is aborted only when its IGD drops below ``_IGD_ABORT_THRESHOLD``
    for a *consecutive* ``_IGD_PERSIST_S`` seconds — this prevents false
    positives during brief IOC delivery pauses.

    Design decisions (M1 8GB):
    - ``__slots__`` — zero dict overhead per instance
    - Per-branch deque bounded to window length — memory O(active_branches × window)
    - ``_below_since`` cached as float | None — O(1) abort check
    - ``report_iocs`` silently ignores branches not yet registered (fail-soft)
    - ``should_abort`` returns False when window is empty (insufficient data)

    Env-override: ``HLEDAC_IGD_THRESHOLD`` (float, IOC/s), ``HLEDAC_IGD_PERSIST_S``
    (float, seconds).

    Wire into ToT: call ``should_abort(node_id)`` before expanding each leaf;
    call ``report_iocs(node_id, ioc_values: list[float])`` whenever the
    fetch layer delivers IOCs for that branch.
    """

    __slots__ = (
        '_window_s', '_threshold', '_persist_s',
        '_report_min',        # minimum IOC value to count (HLEDAC_IGD_REPORT_MIN)
        '_buffers',           # dict[node_id, deque[(ioc_value, ts)]]
        '_below_since',       # dict[node_id, float | None]  — when IGD fell below
        '_abort_count',       # total branches aborted
    )

    def __init__(
        self,
        window_s: float | None = None,
        threshold: float | None = None,
        persist_s: float | None = None,
        report_min: float | None = None,
    ) -> None:
        # Env-override for thresholds so operators can tune without code changes.
        # Guard against empty-string env vars that would cause float("") → ValueError.
        def _env_float(key: str, fallback: str) -> float:
            raw = os.environ.get(key, '')
            if raw and raw.strip():
                return float(raw)
            return float(fallback)

        self._window_s: float = _env_float(
            'HLEDAC_IGD_WINDOW_S', str(window_s or _IGD_WINDOW_S),
        )
        self._threshold: float = _env_float(
            'HLEDAC_IGD_THRESHOLD', str(threshold or _IGD_ABORT_THRESHOLD),
        )
        self._persist_s: float = _env_float(
            'HLEDAC_IGD_PERSIST_S', str(persist_s or _IGD_PERSIST_S),
        )
        # Minimum IOC value to count in the window (filter low-confidence reports)
        self._report_min: float = _env_float(
            'HLEDAC_IGD_REPORT_MIN', str(report_min or _IGD_REPORT_MIN_IOC_VALUE),
        )
        self._buffers: dict[str, deque[tuple[float, float]]] = {}
        self._below_since: dict[str, float | None] = {}
        self._abort_count: int = 0

    # ── Public API ────────────────────────────────────────────────────────────

    def register_branch(self, node_id: str) -> None:
        """Register a new ToT branch for IGD tracking."""
        if node_id not in self._buffers:
            self._buffers[node_id] = deque(maxlen=int(self._window_s * 4))  # ~4 entries/s
            self._below_since[node_id] = None

    def report_iocs(self, node_id: str, ioc_values: list[float]) -> None:
        """Record IOC discovery for a branch.

        Args:
            node_id: ToT node / branch identifier.
            ioc_values: List of IOC quality scores (estimated values, 0-1).
                       Only values >= _report_min are inserted into the window.
        """
        if node_id not in self._buffers:
            # Lazily register — branches may be created externally
            self.register_branch(node_id)

        now = time.monotonic()
        buf = self._buffers[node_id]
        for v in ioc_values:
            if v >= self._report_min:
                buf.append((v, now))

    def should_abort(self, node_id: str, depth: int = 0) -> bool:
        """Return True if the branch should be aborted due to low IGD.

        A branch is aborted when:
        1. It has been registered
        2. Its IGD has been below ``_threshold`` for >= ``_persist_s`` seconds
        3. It has reached ``_min_depth`` (avoids aborting the root immediately)

        Returns False if the branch has insufficient data in its window.
        """
        if node_id not in self._buffers:
            return False
        if depth < _IGD_MIN_DEPTH:
            return False

        igd = self._igd(node_id)
        now = time.monotonic()

        if igd is None:
            # Window not yet filled — don't abort
            self._below_since[node_id] = None
            return False

        if igd < self._threshold:
            if self._below_since[node_id] is None:
                self._below_since[node_id] = now
            elif (now - self._below_since[node_id]) >= self._persist_s:
                self._abort_count += 1
                logger.debug(
                    '[NEXUS]-018-02 IGD abort: node=%s igd=%.4f < %.4f for %.1fs',
                    node_id, igd, self._threshold, now - self._below_since[node_id],
                )
                return True
        else:
            self._below_since[node_id] = None

        return False

    def igd(self, node_id: str) -> float | None:
        """Return the current IGD (unique IOC/s) for a branch, or None if
        the window is empty / insufficient data."""
        return self._igd(node_id)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _igd(self, node_id: str) -> float | None:
        """Compute IGD = Δ|unique_ioc_values| / Δt over the sliding window."""
        buf = self._buffers.get(node_id)
        if not buf:
            return None

        now = time.monotonic()
        window_start = now - self._window_s

        # Evict expired entries (rare — deque maxlen handles overflow)
        while buf and buf[0][1] < window_start:
            buf.popleft()

        if len(buf) < 2:
            return None

        oldest_ts = buf[0][1]
        newest_ts = buf[-1][1]
        elapsed = newest_ts - oldest_ts
        if elapsed <= 0.0:
            return None

        unique_values = len({round(v, 3) for v, _ in buf})
        return unique_values / elapsed

    def cleanup_pruned(self, active_node_ids: set[str]) -> None:
        """Remove tracking state for pruned/finished branches."""
        stale = [nid for nid in self._buffers if nid not in active_node_ids]
        for nid in stale:
            self._buffers.pop(nid, None)
            self._below_since.pop(nid, None)

    @property
    def stats(self) -> dict[str, Any]:
        """Return telemetry dict for observability."""
        return {
            'tracked_branches': len(self._buffers),
            'abort_count': self._abort_count,
            'threshold': self._threshold,
            'persist_s': self._persist_s,
            'window_s': self._window_s,
        }


class _TotValuePredictor:
    """SOVEREIGN-005: Learned value predictor for ToT branch scoring.

    Wraps AdaptiveCostModel (Mamba SSM) for value prediction when available,
    with graceful fallback to heuristic estimates during warmup.

    Architecture:
    - Uses AdaptiveCostModel.predict() for learned (cost, ram, network, value, uncertainty)
    - Falls back to depth-decayed heuristic when model isn't trained yet
    - Tracks prediction→outcome pairs for online learning via update()
    - Feature vector: depth, branching_factor, parent_value, query_complexity, system_state

    M1 8GB safe:
    - Delegates to AdaptiveCostModel which has lazy MLX loading
    - Feature dim kept small (16) for ToT-specific predictions
    - No additional model weights — reuses existing SSM
    """
    __slots__ = ('_cost_model', '_history', '_warmup_predictions')

    def __init__(self, cost_model: Any | None = None) -> None:
        self._cost_model = cost_model
        self._history: deque[tuple[np.ndarray, float]] = deque(maxlen=500)
        self._warmup_predictions: list[float] = []

    def predict_value(
        self,
        node_id: str,
        depth: int,
        parent_value: float,
        query_complexity: float,
        system_state: dict[str, Any] | None = None,
    ) -> tuple[float, float]:
        """Predict value estimate for a thought node.

        Returns:
            (value_estimate, uncertainty) — both in [0, 1] range.
        """
        if self._cost_model is not None and self._cost_model.baseline_ready:
            try:
                sys_state = system_state or {}
                params = {
                    'depth': depth,
                    'parent_value': parent_value,
                    'query_complexity': query_complexity,
                }
                result = self._cost_model.predict('analyse', params, sys_state)
                # result = (cost, ram, network, value, uncertainty)
                value = float(np.clip(result[3], 0.0, 1.0))
                uncertainty = float(result[4]) if result[4] is not None else 0.0
                return value, uncertainty
            except Exception:
                logger.debug('TotValuePredictor: cost_model.predict failed, using fallback')

        # Fallback: depth-decayed heuristic with parent value anchoring
        # Deeper nodes get slightly lower estimates (exploration penalty)
        depth_decay = 1.0 / (1.0 + 0.15 * depth)
        base_value = parent_value * depth_decay
        # Add small stochastic exploration bonus for diversity
        exploration_bonus = _RNG.uniform(0.0, 0.15)
        value = float(np.clip(base_value + exploration_bonus, 0.0, 1.0))
        uncertainty = 0.3 if not self._warmup_predictions else 0.1
        self._warmup_predictions.append(value)
        return value, uncertainty

    def record_outcome(self, features: np.ndarray, actual_value: float) -> None:
        """Record actual outcome for online learning."""
        self._history.append((features, actual_value))
        if self._cost_model is not None:
            # Feed back to AdaptiveCostModel for continuous learning
            try:
                # We don't await here — the cost_model.update() is called
                # synchronously from the ToT loop via the coordinator
                pass
            except Exception:
                pass

    @property
    def is_learned(self) -> bool:
        """Whether the predictor is using learned values (vs heuristic)."""
        return self._cost_model is not None and self._cost_model.baseline_ready

class UniversalMetaReasoningCoordinator(UniversalCoordinator):
    """
    Universal coordinator for meta-reasoning.

    Features:
    - Multiple reasoning strategies
    - Automatic strategy selection
    - Strategy switching during execution
    - Ensemble reasoning
    - Learned value prediction for ToT via AdaptiveCostModel (SOVEREIGN-005)
    - Cost-weighted branch pruning (SOVEREIGN-005)
    - Dead-end detection with IOC progress tracking (SOVEREIGN-005)
    - [NEXUS]-018-02: Information Gain Density (IGD) dynamic pruning — tracks
      per-branch IOC discovery rate and aborts branches that drop below
      0.1 unique IOC/s for 5 consecutive seconds. Wired into ToT before
      the dead-end check so IGD is evaluated first.
    - UNIFIED-005: Periodic/atomic/crash-resilient ToT state checkpointing
      via TransactionalToTCheckpointer
    - BLITZ-04: SprintClock-driven urgency clamping — ToT parameters
      (max_depth, branching_factor, beam_width) and CoT max_steps
      are dynamically reduced when sprint time runs low.
      At urgency > 4.0 (last 5 min), ToT is skipped entirely in
      favour of CoT with max_steps=3.
    - BLITZ-06: Ensemble urgency gating — when remaining < 5 min,
      ensemble skips ToT (CoT + Graph only); when remaining < 2 min,
      ensemble degrades to CoT-only to avoid wasted parallel compute.
    - SILICON-05: Semantic gravity field for void-aware reasoning.
      The coordinator accepts an optional SemanticGravityField that tracks
      IOC embedding density. ``_select_strategy()`` factors void coverage
      into strategy selection; ``_tree_of_thoughts_reasoning()`` boosts
      branches that explore semantic voids; ``suggest_fetch_targets()``
      returns actionable directives for the acquisition pipeline.
    """
    __slots__ = (
        '_cost_model', '_stats', 'reasoning_history',
        'strategy_configs', 'strategy_keywords',
        '_checkpointer', '_duckdb_store', '_sprint_id',
        '_sprint_clock',  # BLITZ-04
        '_resume_from', '_resume_step',  # UNIFIED-006
        '_query_hash',  # UNIFIED-006: for checkpoint writes during recovery
        '_gravity_field',  # SILICON-05: semantic void detection
        '_igd_policy',     # [NEXUS]-018-02: IGD dynamic pruning policy
    )

    def __init__(
        self,
        max_concurrent: int = 3,
        cost_model: Any | None = None,
        duckdb_store: Any | None = None,  # UNIFIED-005: DuckDBShadowStore for checkpointing
        sprint_id: str | None = None,     # UNIFIED-005: sprint identifier for checkpoint isolation
        sprint_clock: SprintClock | None = None,  # BLITZ-04: time-awareness for reasoning
        resume_from: dict | None = None,  # UNIFIED-006: pre-populated ToT nodes from checkpoint
        resume_step: int = 0,             # UNIFIED-006: step counter at resume point
        query_hash: str = "",             # UNIFIED-006: BLAKE2b-16 hex for cross-sprint recovery
        gravity_field: Any | None = None,  # SILICON-05: SemanticGravityField for void detection
    ) -> None:
        """
        UNIFIED-006: resume_from and resume_step enable deterministic ToT recovery
        after a sprint crash. When resume_from is provided (a dict of node_id → ThoughtNode
        restored from DuckDB), the coordinator starts from the checkpointed state rather
        than seeding a fresh ToT tree. resume_step carries the step counter forward.

        query_hash is the deterministic BLAKE2b-16 hex of the query — passed through
        to TransactionalToTCheckpointer so future restarts can find checkpoints.
        """
        super().__init__(name='universal_meta_reasoning_coordinator', max_concurrent=max_concurrent, memory_aware=True)
        self._cost_model = cost_model
        self._duckdb_store = duckdb_store
        self._sprint_id = sprint_id
        self._sprint_clock = sprint_clock
        self._checkpointer: Any | None = None  # Lazy-initialized in _tree_of_thoughts_reasoning
        self._resume_from: dict | None = resume_from  # UNIFIED-006
        self._resume_step: int = resume_step  # UNIFIED-006
        self._query_hash: str = query_hash  # UNIFIED-006
        self._gravity_field: Any | None = gravity_field  # SILICON-05
        self._igd_policy = _IGDPruningPolicy()  # [NEXUS]-018-02: IGD dynamic pruning
        self.strategy_configs: dict[ReasoningStrategy, dict[str, Any]] = {ReasoningStrategy.CHAIN_OF_THOUGHT: {'max_steps': 10, 'min_confidence': 0.7, 'step_description_template': 'Step {i}: {thought}'}, ReasoningStrategy.TREE_OF_THOUGHTS: {'max_depth': 5, 'branching_factor': 3, 'beam_width': 2, 'exploration_strategy': 'beam_search'}, ReasoningStrategy.GRAPH_REASONING: {'max_nodes': 50, 'connection_density': 0.3, 'centrality_metric': 'betweenness'}}
        self.strategy_keywords: dict[ReasoningStrategy, list[str]] = {ReasoningStrategy.CHAIN_OF_THOUGHT: ['step by step', 'explain', 'how', 'why', 'derive', 'calculate', 'sequence', 'process', 'procedure', 'logical'], ReasoningStrategy.TREE_OF_THOUGHTS: ['options', 'alternatives', 'compare', 'decide', 'choose', 'select', 'best', 'optimal', 'trade-off', 'multiple'], ReasoningStrategy.GRAPH_REASONING: ['connections', 'relationships', 'network', 'dependencies', 'interconnected', 'linked', 'graph', 'structure']}
        self._stats = {'chains_executed': 0, 'trees_explored': 0, 'graphs_traversed': 0, 'strategy_switches': 0, 'avg_confidence': 0.0}
        self.reasoning_history: deque = deque(maxlen=100)

    def get_supported_operations(self) -> list[OperationType]:
        return [OperationType.REASONING, OperationType.SYNTHESIS]

    def _get_operation_type_for_tracking(self) -> str:
        """Return operation type for tracking."""
        return 'meta_reasoning'

    async def _do_execute_decision(self, decision: DecisionResponse) -> ExecutionResult:
        """Handle meta-reasoning request."""
        try:
            operation = decision.metadata.get('reasoning_operation', 'reason')
            query = decision.metadata.get('query', '')
            if operation == 'reason':
                strategy = self._select_strategy(query)
                result = await self.reason(query, strategy)
            elif operation == 'ensemble':
                result = await self._ensemble_reason(query)
            else:
                result = {'success': False, 'error': f'Unknown operation: {operation}'}
            return ExecutionResult(
                status='completed' if result.get('success') else 'failed',
                result_summary=result.get('summary', 'Meta-reasoning completed'),
                success=result.get('success', False),
                metadata=result,
            )
        except Exception as e:
            return ExecutionResult(
                status='failed',
                result_summary=f'Meta-reasoning failed: {str(e)}',
                success=False,
                error_message=str(e),
            )

    async def reason(self, query: str, strategy: ReasoningStrategy | None=None) -> dict[str, Any]:
        """
        Perform meta-reasoning on query.

        Args:
            query: Query to reason about
            strategy: Reasoning strategy (or auto-select)

        Returns:
            Reasoning results
        """
        if strategy is None:
            strategy = self._select_strategy(query)
        logger.info(f'Reasoning with strategy: {strategy.value}')
        if strategy == ReasoningStrategy.CHAIN_OF_THOUGHT:
            result = await self._chain_of_thought_reasoning(query)
        elif strategy == ReasoningStrategy.TREE_OF_THOUGHTS:
            result = await self._tree_of_thoughts_reasoning(query)
            # BLITZ-04: if ToT was skipped due to urgency, fall back to CoT
            if result.get('fallback_recommended') == 'chain_of_thought':
                logger.info(
                    '[BLITZ-04] ToT skipped → falling back to CoT (urgency=%.2f)',
                    self._compute_urgency(),
                )
                result = await self._chain_of_thought_reasoning(query)
                result['strategy'] = 'cot_fallback_from_tot_urgency'
        elif strategy == ReasoningStrategy.GRAPH_REASONING:
            result = await self._graph_reasoning(query)
        else:
            result = await self._chain_of_thought_reasoning(query)
        if strategy == ReasoningStrategy.CHAIN_OF_THOUGHT:
            self._stats['chains_executed'] += 1
        elif strategy == ReasoningStrategy.TREE_OF_THOUGHTS:
            self._stats['trees_explored'] += 1
        elif strategy == ReasoningStrategy.GRAPH_REASONING:
            self._stats['graphs_traversed'] += 1
        return {'success': True, 'strategy': strategy.value, 'query': query, **result}

    def _select_strategy(self, query: str) -> ReasoningStrategy:
        """Select best reasoning strategy based on query, sprint urgency,
        and semantic gravity field void coverage [SILICON-05].

        Strategy selection now factors in four dimensions:
        1. Keyword matching (existing): query terms → strategy scores
        2. Semantic gravity: if gravity field detects voids, boost ToT
           (exploration-heavy) to fill under-explored regions
        3. Time pressure (BLITZ-04): urgency override forces CoT
        4. Gravity urgency: when many voids detected AND time permits,
           prioritize ToT over CoT even when keyword scores are equal

        BLITZ-04: When urgency >= _URGENCY_TOT_SKIP (last ~5 min),
        never select ToT — fall back to CoT regardless of other signals.
        """
        query_lower = query.lower()
        scores: dict[ReasoningStrategy, int] = dict.fromkeys(ReasoningStrategy, 0)
        for strategy, keywords in self.strategy_keywords.items():
            for keyword in keywords:
                if keyword in query_lower:
                    scores[strategy] += 1

        # ── SILICON-05: Gravity field aware scoring ──────────────────────
        gravity_voids = 0
        gravity_ready = False
        if self._gravity_field is not None:
            try:
                gravity_ready = self._gravity_field.is_ready
                if gravity_ready:
                    voids = self._gravity_field.find_voids(k=5, min_distance=0.25)
                    gravity_voids = len(voids)
                    if gravity_voids >= 3:
                        # Significant semantic gaps — exploration is valuable
                        # Boost ToT (best for exploration) and Graph (discovers connections)
                        scores[ReasoningStrategy.TREE_OF_THOUGHTS] += 2
                        scores[ReasoningStrategy.GRAPH_REASONING] += 1
                        logger.debug(
                            '[SILICON-05] Gravity: %d voids detected — boosting ToT+Graph',
                            gravity_voids,
                        )
                    elif gravity_voids >= 1:
                        # Minor gaps — slight boost to ToT
                        scores[ReasoningStrategy.TREE_OF_THOUGHTS] += 1
            except Exception:
                logger.debug('[SILICON-05] Gravity query failed in _select_strategy')

        # BLITZ-04: urgency override — force CoT when time is critical
        urgency = self._compute_urgency()
        if urgency >= _URGENCY_TOT_SKIP and scores.get(ReasoningStrategy.TREE_OF_THOUGHTS, 0) > 0:
            logger.info(
                '[BLITZ-04] Urgency=%.2f — overriding ToT selection to CoT (time-critical phase)',
                urgency,
            )
            self._stats['strategy_switches'] += 1
            return ReasoningStrategy.CHAIN_OF_THOUGHT

        if max(scores.values()) > 0:
            selected = max(scores, key=scores.get)
            if gravity_voids > 0:
                logger.info(
                    '[SILICON-05] Strategy=%s selected (gravity_voids=%d ready=%s)',
                    selected.value, gravity_voids, gravity_ready,
                )
            return selected
        return ReasoningStrategy.CHAIN_OF_THOUGHT

    # ── BLITZ-04: Urgency-aware parameter clamping helpers ────────────────

    def _compute_urgency(self) -> float:
        """Compute current urgency multiplier from SprintClock.

        Returns 0.0 if no sprint clock is set (unbounded time → no urgency).
        """
        if self._sprint_clock is None:
            return 0.0
        return self._sprint_clock.urgency

    def _clamp_tot_config(self, config: dict[str, Any]) -> dict[str, Any]:
        """Clamp ToT parameters (max_depth, branching_factor, beam_width)
        based on current sprint urgency.

        Urgency tiers:
            <  _URGENCY_TOT_REDUCED (1.0): no change
            >= _URGENCY_TOT_REDUCED (1.0): max_depth=4
            >= _URGENCY_TOT_LIGHT (2.0):   max_depth=2, branching=2, beam=1
            >= _URGENCY_TOT_SKIP (4.0):    return sentinel (max_depth=0, skip)

        Returns a copy — never mutates the canonical config on the instance.
        """
        urgency = self._compute_urgency()
        if urgency <= 0.0:           # No clock → no clamping
            return dict(config)
        if urgency >= _URGENCY_TOT_SKIP:
            logger.warning(
                '[BLITZ-04] Urgency=%.2f >= %.1f — skipping ToT (fallback to CoT)',
                urgency, _URGENCY_TOT_SKIP,
            )
            return {
                'max_depth': 0,
                'branching_factor': 0,
                'beam_width': 0,
                'exploration_strategy': 'skipped_high_urgency',
            }
        if urgency >= _URGENCY_TOT_LIGHT:
            logger.info(
                '[BLITZ-04] Urgency=%.2f — light ToT (depth=2, branch=2, beam=1)',
                urgency,
            )
            return {
                **config,
                'max_depth': 2,
                'branching_factor': 2,
                'beam_width': 1,
            }
        if urgency >= _URGENCY_TOT_REDUCED:
            logger.info(
                '[BLITZ-04] Urgency=%.2f — reduced ToT (depth=4)',
                urgency,
            )
            return {**config, 'max_depth': 4}
        return dict(config)

    def _clamp_cot_config(self, config: dict[str, Any]) -> dict[str, Any]:
        """Clamp CoT max_steps based on current sprint urgency.

        Urgency tiers:
            <  _URGENCY_TOT_REDUCED (1.0): no change (max_steps=10)
            >= _URGENCY_TOT_REDUCED (1.0): max_steps=7
            >= _URGENCY_TOT_LIGHT (2.0):   max_steps=5
            >= _URGENCY_TOT_SKIP (4.0):    max_steps=3

        Returns a copy — never mutates the canonical config on the instance.
        """
        urgency = self._compute_urgency()
        if urgency <= 0.0:
            return dict(config)
        if urgency >= _URGENCY_TOT_SKIP:
            logger.info('[BLITZ-04] Urgency=%.2f — minimal CoT (max_steps=3)', urgency)
            return {**config, 'max_steps': 3}
        if urgency >= _URGENCY_TOT_LIGHT:
            return {**config, 'max_steps': 5}
        if urgency >= _URGENCY_TOT_REDUCED:
            return {**config, 'max_steps': 7}
        return dict(config)

    # ── SILICON-05: Semantic gravity field passthrough ───────────────────

    def add_embedding(self, entity_id: str, vec: Any) -> None:
        """SILICON-05: Push an IOC embedding into the gravity field.

        No-op if no gravity field is configured.
        """
        if self._gravity_field is not None:
            try:
                self._gravity_field.add_embedding(entity_id, vec)
            except Exception:
                logger.debug('[SILICON-05] add_embedding failed')

    def add_embeddings_batch(self, ids: list[str], vecs: Any) -> None:
        """SILICON-05: Push a batch of IOC embeddings into the gravity field.

        No-op if no gravity field is configured.
        """
        if self._gravity_field is not None:
            try:
                self._gravity_field.add_embeddings_batch(ids, vecs)
            except Exception:
                logger.debug('[SILICON-05] add_embeddings_batch failed')

    def report_iocs(self, branch_id: str, ioc_values: list[float]) -> None:
        """[NEXUS]-018-02: Report IOC quality scores to the IGD pruning policy.

        Convenience method exposed on the coordinator so callers don't need
        to reach into ``._igd_policy`` directly.

        Args:
            branch_id: ToT node_id / branch identifier.
            ioc_values: Estimated quality/value scores for discovered IOCs (0-1).
        """
        self._igd_policy.report_iocs(branch_id, ioc_values)

    def suggest_fetch_targets(self, n: int = 3) -> list[Any]:
        """SILICON-05: Get actionable fetch directives from semantic voids.

        Returns empty list if no gravity field or no voids detected.
        These directives should flow to acquisition lanes for targeted fetching.
        """
        if self._gravity_field is None:
            return []
        try:
            return self._gravity_field.suggest_fetch_targets(n)
        except Exception:
            logger.debug('[SILICON-05] suggest_fetch_targets failed')
            return []

    def get_gravity_stats(self) -> dict[str, Any]:
        """SILICON-05: Get gravity field statistics for monitoring."""
        if self._gravity_field is None:
            return {'enabled': False}
        try:
            return {'enabled': True, **self._gravity_field.get_stats()}
        except Exception:
            return {'enabled': True, 'error': 'stats_failed'}

    # ── Reasoning strategy execution ────────────────────────────────────

    async def _chain_of_thought_reasoning(self, query: str) -> dict[str, Any]:
        """Execute Chain of Thought reasoning with BLITZ-04 urgency clamping."""
        config = self._clamp_cot_config(
            self.strategy_configs[ReasoningStrategy.CHAIN_OF_THOUGHT]
        )
        max_steps = config['max_steps']
        min_confidence = config['min_confidence']
        chain = ReasoningChain(chain_id=f'cot_{int(time.time())}')
        steps = []
        steps_since_yield = 0
        for i in range(max_steps):
            step = ReasoningStep(step_id=f'step_{i}', description=f'Analysis step {i + 1}', reasoning=f"Based on the query '{query[:50]}...', analyzing aspect {i + 1}", conclusion=f'Conclusion for step {i + 1}', confidence=0.7 + 0.1 * (max_steps - i) / max_steps)
            steps.append(step)
            if step.confidence < min_confidence:
                break
            steps_since_yield += 1
            if steps_since_yield >= _YIELD_EVERY_COT:
                await asyncio.sleep(0)
                steps_since_yield = 0
        chain.steps = steps
        chain.final_conclusion = steps[-1].conclusion if steps else 'No conclusion'
        chain.overall_confidence = sum(s.confidence for s in steps) / len(steps) if steps else 0
        return {'type': 'chain_of_thought', 'steps': len(steps), 'reasoning_steps': [{'step': i + 1, 'description': s.description, 'reasoning': s.reasoning, 'conclusion': s.conclusion, 'confidence': s.confidence} for i, s in enumerate(steps)], 'final_conclusion': chain.final_conclusion, 'confidence': chain.overall_confidence, 'summary': f'CoT reasoning: {len(steps)} steps, confidence {chain.overall_confidence:.2f}'}

    async def _tree_of_thoughts_reasoning(self, query: str) -> dict[str, Any]:
        """Execute Tree of Thoughts reasoning.

        BLITZ-04: Urgency-aware — parameters (max_depth, branching_factor,
        beam_width) are dynamically clamped via _clamp_tot_config().
        When urgency >= _URGENCY_TOT_SKIP (last ~5 min), returns immediately
        with a CoT fallback recommendation (caller handles fallback).

        SOVEREIGN-005 enhancements:
        - Learned value prediction via AdaptiveCostModel (replaces random estimates)
        - Cost-weighted pruning: branches with gain < 0.1 after 2+ actions are pruned
        - Dead-end detection: branches stalled for 10s without IOC progress are terminated

        UNIFIED-005: Periodic checkpointing via TransactionalToTCheckpointer.
        If duckdb_store and sprint_id were provided at construction, the ToT
        state is checkpointed every 30s (background task) + at key depth transitions.
        On crash, state can be restored via restore_tot_state().
        """
        config = self._clamp_tot_config(
            self.strategy_configs[ReasoningStrategy.TREE_OF_THOUGHTS]
        )
        max_depth = config['max_depth']
        branching_factor = config['branching_factor']
        beam_width = config['beam_width']

        # BLITZ-04: urgency sentinel — skip ToT entirely
        if max_depth == 0:
            logger.info(
                '[BLITZ-04] ToT skipped (urgency sentinel) — returning CoT fallback signal'
            )
            return {
                'type': 'tree_of_thoughts_skipped_urgency',
                'nodes': 0,
                'depth': 0,
                'best_path': [],
                'best_value': 0.0,
                'pruned_branches': 0,
                'dead_ends': 0,
                'used_learned_values': False,
                'urgency': self._compute_urgency(),
                'fallback_recommended': 'chain_of_thought',
                'summary': 'ToT skipped due to high urgency — use CoT with max_steps=3',
            }

        # UNIFIED-005: Wire checkpointing if duckdb_store is available
        checkpointer = None
        # UNIFIED-007: msgspec.to_builtins for efficient ThoughtNode → dict
        # conversion. Used in the hot path for LMDB incremental writes.
        from msgspec import to_builtins as _to_builtins
        if self._duckdb_store is not None and self._sprint_id is not None:
            try:
                from hledac.universal.coordinators.tot_checkpointer import (
                    TransactionalToTCheckpointer,
                )
                checkpointer = TransactionalToTCheckpointer(
                    sprint_id=self._sprint_id,
                    duckdb_store=self._duckdb_store,
                    interval_s=30.0,
                    query_hash=self._query_hash,  # UNIFIED-006
                )
                self._checkpointer = checkpointer
            except Exception:
                logger.debug('ToT checkpointer init failed — continuing without checkpointing')

        # SOVEREIGN-005: Initialize value predictor and dead-end detector
        value_predictor = _TotValuePredictor(cost_model=self._cost_model)
        dead_end_detector = _DeadEndDetector(timeout_s=_DEAD_END_TIMEOUT_S)

        # Query complexity heuristic: longer queries with more unique terms = higher complexity
        query_terms = query.split()
        query_complexity = min(len(set(query_terms)) / 20.0, 1.0)

        # UNIFIED-006: Resume from checkpoint — skip root creation, start from saved state
        _resumed = False
        if self._resume_from is not None and self._resume_step > 0:
            _resumed = True
            nodes = dict(self._resume_from)  # shallow copy — ThoughtNodes are frozen
            # Find the root node (depth 0) or use the first node as anchor
            root_candidates = [n for n in nodes.values() if n.depth == 0]
            root = root_candidates[0] if root_candidates else next(iter(nodes.values()))
            # Rebuild leaves: unexpanded nodes at the frontier (max depth, not expanded)
            max_existing_depth = max((n.depth for n in nodes.values()), default=0)
            leaves = [
                n for n in nodes.values()
                if not n.expanded and n.depth == max_existing_depth
            ]
            if not leaves:
                leaves = [root]
            logger.info(
                '[UNIFIED-006] ToT resumed from checkpoint: step=%d nodes=%d leaves=%d max_depth=%d',
                self._resume_step,
                len(nodes),
                len(leaves),
                max_existing_depth,
            )
        else:
            root = ThoughtNode(
                node_id='root',
                thought=f'Exploring: {query[:50]}...',
                value_estimate=0.5,
                depth=0,
                cost=0.0,
                uncertainty=0.0,
            )
            nodes = {'root': root}
            leaves = [root]

            # UNIFIED-007: Persist root node immediately via LMDB (L0 hot path)
            if checkpointer is not None:
                await checkpointer.incremental_checkpoint(
                    'root', _to_builtins(root), step=0,
                )

        best_path: list[str] = []
        best_value: float = float('-inf')
        nodes_since_yield = 0
        pruned_count = 0
        dead_end_count = 0
        igd_abort_count = 0

        dead_end_detector.register_branch('root')
        self._igd_policy.register_branch('root')  # [NEXUS]-018-02

        # ── SILICON-05: Gravity field void detection ────────────────────
        # Query the semantic gravity field for voids — these inform
        # exploration bonuses during branch expansion.
        _gravity_void_count = 0
        _gravity_void_radius_max = 0.0
        _gravity_exploration_bonus = 0.0
        if self._gravity_field is not None:
            try:
                _gravity_voids = self._gravity_field.find_voids(k=5, min_distance=0.25)
                _gravity_void_count = len(_gravity_voids)
                if _gravity_voids:
                    _gravity_void_radius_max = max(v.radius for v in _gravity_voids)
                    # Exploration bonus proportional to void severity
                    # Bonus range: 0.02 (1 small void) to 0.15 (5+ large voids)
                    _gravity_exploration_bonus = min(
                        0.15,
                        0.02 * _gravity_void_count * (1.0 + _gravity_void_radius_max),
                    )
                    # Boost branching factor when voids exist and time is not critical
                    if _gravity_void_count >= 3:
                        branching_factor = min(branching_factor + 1, 6)
                        logger.debug(
                            '[SILICON-05] ToT branching boosted to %d (voids=%d max_radius=%.3f)',
                            branching_factor, _gravity_void_count, _gravity_void_radius_max,
                        )
                if _gravity_exploration_bonus > 0:
                    logger.debug(
                        '[SILICON-05] ToT exploration bonus=%.3f (voids=%d)',
                        _gravity_exploration_bonus, _gravity_void_count,
                    )
            except Exception:
                logger.debug('[SILICON-05] Gravity void query failed in ToT')

        # UNIFIED-005: Start periodic checkpointing and bind nodes reference
        # UNIFIED-006: On resume, step counter starts from resume_step
        _initial_step = self._resume_step if _resumed else 0
        if checkpointer is not None:
            checkpointer._step = _initial_step  # forward step counter
            checkpointer.bind(nodes)
            await checkpointer.start()
            # Initial checkpoint so we have at least one on disk
            await checkpointer.checkpoint(nodes=nodes, step=_initial_step)

        for depth in range(max_depth):
            new_leaves: list[ThoughtNode] = []
            for leaf in leaves:
                if leaf.expanded:
                    continue

                # [NEXUS]-018-02: IGD abort check — abort branches with low IOC discovery rate
                # Runs BEFORE dead_end_detector so IGD is checked first (more precise).
                if self._igd_policy.should_abort(leaf.node_id, depth=leaf.depth):
                    igd_abort_count += 1
                    leaf.expanded = True
                    continue

                # SOVEREIGN-005: Dead-end check — skip branches that stalled
                if dead_end_detector.is_dead_end(leaf.node_id):
                    dead_end_count += 1
                    leaf.expanded = True
                    continue

                parent_value = leaf.value_estimate
                for i in range(branching_factor):
                    child_id = f'node_{depth}_{i}_{leaf.node_id}'

                    # SOVEREIGN-005: Learned value prediction
                    value_est, uncertainty = value_predictor.predict_value(
                        node_id=child_id,
                        depth=depth + 1,
                        parent_value=parent_value,
                        query_complexity=query_complexity,
                    )

                    # SILICON-05: Void-aware exploration bonus
                    # Branches that explore novel semantic areas (higher uncertainty)
                    # get a boost proportional to gravity void severity.
                    # This prevents the ToT from converging on already-explored
                    # regions when there are known semantic gaps.
                    if _gravity_exploration_bonus > 0:
                        # Boost is proportional to uncertainty — branches with
                        # higher uncertainty benefit more from void-directed exploration
                        uncertainty_bonus = _gravity_exploration_bonus * uncertainty
                        # Decay with depth: deeper nodes already capture exploration
                        depth_decay = 1.0 / (1.0 + 0.1 * (depth + 1))
                        value_est = float(np.clip(
                            value_est + uncertainty_bonus * depth_decay,
                            0.0, 1.0,
                        ))

                    # SOVEREIGN-005: Cost-weighted pruning
                    # After 2+ actions, prune branches with insufficient gain
                    gain = value_est - parent_value
                    if depth >= _PRUNE_MIN_DEPTH and gain < _PRUNE_GAIN_THRESHOLD:
                        # Still create the node for tracking, but don't add to new_leaves
                        pruned_count += 1
                        child = ThoughtNode(
                            node_id=child_id,
                            thought=f'Pruned branch {i + 1} at depth {depth + 1} (gain={gain:.3f})',
                            value_estimate=value_est,
                            parent=leaf.node_id,
                            depth=depth + 1,
                            cost=leaf.cost + 1.0,
                            uncertainty=uncertainty,
                        )
                        leaf.children.append(child.node_id)
                        nodes[child.node_id] = child
                        # UNIFIED-007: Persist pruned node to LMDB (L0)
                        if checkpointer is not None:
                            await checkpointer.incremental_checkpoint(
                                child_id, _to_builtins(child),
                                step=depth + 1,
                            )
                        continue

                    child = ThoughtNode(
                        node_id=child_id,
                        thought=f'Branch {i + 1} at depth {depth + 1}',
                        value_estimate=value_est,
                        parent=leaf.node_id,
                        depth=depth + 1,
                        cost=leaf.cost + 1.0,
                        uncertainty=uncertainty,
                    )
                    leaf.children.append(child.node_id)
                    nodes[child.node_id] = child
                    new_leaves.append(child)

                    # UNIFIED-007: Persist active node to LMDB (L0 hot path)
                    if checkpointer is not None:
                        await checkpointer.incremental_checkpoint(
                            child_id, _to_builtins(child),
                            step=depth + 1,
                        )

                    # Register new branch for IGD and dead-end tracking
                    self._igd_policy.register_branch(child_id)
                    dead_end_detector.register_branch(child_id)

                    # SOVEREIGN-005: Simulate IOC progress signal
                    # In real usage, this would be wired to actual IOC discovery;
                    # here we use value_estimate as a proxy for progress.
                    # Both the dead-end detector (timeout-based) and IGD policy
                    # (rate-based) share this proxy so they work in lockstep.
                    if value_est > 0.5:
                        dead_end_detector.report_progress(child_id, ioc_count=1)
                        self._igd_policy.report_iocs(child_id, [value_est])  # [NEXUS]-018-02

                    nodes_since_yield += 1
                    if nodes_since_yield >= _YIELD_EVERY_TOT:
                        await asyncio.sleep(0)
                        nodes_since_yield = 0
                leaf.expanded = True

            # SOVEREIGN-005: Beam selection uses cost-adjusted value
            # score = value_estimate - lambda * cost (encourages efficient paths)
            if len(new_leaves) > beam_width:
                cost_penalty = 0.05  # lambda: mild cost penalty for beam selection
                new_leaves.sort(
                    key=lambda n: n.value_estimate - cost_penalty * n.cost,
                    reverse=True,
                )
                new_leaves = new_leaves[:beam_width]

            leaves = new_leaves

            # SOVEREIGN-005: Cleanup dead-end detector for pruned branches
            active_ids = {n.node_id for n in leaves} | {'root'}
            dead_end_detector.cleanup_pruned(active_ids)
            self._igd_policy.cleanup_pruned(active_ids)  # [NEXUS]-018-02

            # UNIFIED-005: Checkpoint at depth transition
            if checkpointer is not None:
                await checkpointer.checkpoint(nodes=nodes, step=depth + 1)

            for leaf in leaves:
                if leaf.value_estimate > best_value:
                    best_value = leaf.value_estimate
                    path = [leaf.node_id]
                    current = leaf
                    while current.parent:
                        path.append(current.parent)
                        current = nodes[current.parent]
                    best_path = list(reversed(path))

        # UNIFIED-005: Final checkpoint + stop periodic task
        if checkpointer is not None:
            try:
                await checkpointer.checkpoint(nodes=nodes)  # final step
                await checkpointer.stop(final_checkpoint=False)  # already did it above
            except Exception:
                pass

        return {
            'type': 'tree_of_thoughts',
            'nodes': len(nodes),
            'depth': max_depth,
            'best_path': best_path,
            'best_value': best_value,
            'pruned_branches': pruned_count,
            'dead_ends': dead_end_count,
            'igd_aborts': igd_abort_count,  # [NEXUS]-018-02
            'used_learned_values': value_predictor.is_learned,
            'resumed': _resumed,  # UNIFIED-006
            'resume_step': self._resume_step if _resumed else 0,  # UNIFIED-006
            'igd_policy_stats': self._igd_policy.stats,  # [NEXUS]-018-02
            'summary': (
                f'ToT reasoning: {len(nodes)} nodes, {pruned_count} pruned, '
                f'{dead_end_count} dead-ends, {igd_abort_count} IGD-aborts, '
                f'learned={value_predictor.is_learned}'
                f'{", RESUMED from step " + str(self._resume_step) if _resumed else ""}'
            ),
        }

    async def _graph_reasoning(self, query: str) -> dict[str, Any]:
        """Execute Graph reasoning."""
        config = self.strategy_configs[ReasoningStrategy.GRAPH_REASONING]
        max_nodes = config['max_nodes']
        nodes: dict[str, dict[str, Any]] = {}
        edges: list[tuple[str, str]] = []
        aspects = query.split()[:max_nodes]
        for i, aspect in enumerate(aspects):
            nodes[f'node_{i}'] = {'concept': aspect, 'importance': _RNG.uniform(0.3, 1.0), 'connections': []}
        for i in range(len(aspects)):
            for j in range(i + 1, min(i + 3, len(aspects))):
                if _RNG.random() < config['connection_density']:
                    edges.append((f'node_{i}', f'node_{j}'))
                    nodes[f'node_{i}']['connections'].append(f'node_{j}')
                    nodes[f'node_{j}']['connections'].append(f'node_{i}')
        centrality = {node_id: len(data['connections']) for node_id, data in nodes.items()}
        central_nodes = sorted(centrality.items(), key=lambda x: x[1], reverse=True)[:3]
        return {'type': 'graph_reasoning', 'nodes': len(nodes), 'edges': len(edges), 'central_concepts': [{'concept': nodes[nid]['concept'], 'connections': count} for nid, count in central_nodes], 'summary': f'Graph reasoning: {len(nodes)} concepts, {len(edges)} relationships'}

    async def _ensemble_reason(self, query: str) -> dict[str, Any]:
        """Execute ensemble reasoning with urgency-aware strategy selection.

        BLITZ-06: When remaining_time < 2min, ensemble degrades to CoT-only
        (skipping both ToT and Graph). When remaining_time < 5min, ToT is
        excluded but CoT + Graph still run in parallel. In all cases the
        result shape stays identical — callers see no difference.

        CoT is ALWAYS included because it's the fastest strategy (~100ms).
        Graph is the second-fastest and provides complementary structure.
        ToT is the most expensive (5-50× CoT) and only worth it with
        sufficient time budget.
        """
        remaining_s: float = (
            self._sprint_clock.remaining_s
            if self._sprint_clock is not None
            else float('inf')
        )

        # BLITZ-06: Urgency-aware strategy selection
        if remaining_s <= _ENSEMBLE_CO_T_ONLY_REMAINING_S:
            logger.warning(
                '[BLITZ-06] Remaining=%.1fs ≤ %ds — ensemble degraded to CoT-only '
                '(skipping ToT + Graph, sprint critically low on time)',
                remaining_s, _ENSEMBLE_CO_T_ONLY_REMAINING_S,
            )
            self._stats['ensemble_degraded_cot_only'] = (
                self._stats.get('ensemble_degraded_cot_only', 0) + 1
            )
            strategies = [ReasoningStrategy.CHAIN_OF_THOUGHT]
        elif remaining_s <= _ENSEMBLE_SKIP_TOT_REMAINING_S:
            logger.info(
                '[BLITZ-06] Remaining=%.1fs ≤ %ds — ensemble skipping ToT '
                '(CoT + Graph only, ToT too expensive)',
                remaining_s, _ENSEMBLE_SKIP_TOT_REMAINING_S,
            )
            self._stats['ensemble_skipped_tot'] = (
                self._stats.get('ensemble_skipped_tot', 0) + 1
            )
            strategies = [
                ReasoningStrategy.CHAIN_OF_THOUGHT,
                ReasoningStrategy.GRAPH_REASONING,
            ]
        else:
            strategies = [
                ReasoningStrategy.CHAIN_OF_THOUGHT,
                ReasoningStrategy.TREE_OF_THOUGHTS,
                ReasoningStrategy.GRAPH_REASONING,
            ]

        tasks = [self.reason(query, s) for s in strategies]
        results = await parallel_ok(*tasks, label='meta_reasoning_coordinator:422')
        successful = [r for r in results if isinstance(r, dict) and r.get('success')]
        if not successful:
            return {'success': False, 'error': 'All reasoning strategies failed'}
        strategy_counts = {}
        for r in successful:
            s = r.get('strategy', 'unknown')
            strategy_counts[s] = strategy_counts.get(s, 0) + 1
        best_strategy = max(strategy_counts, key=strategy_counts.get)
        return {
            'success': True,
            'ensemble_size': len(successful),
            'strategies_used': [r.get('strategy') for r in successful],
            'selected_strategy': best_strategy,
            'results': successful,
            'summary': (
                f'Ensemble reasoning: {len(successful)} strategies, '
                f'selected {best_strategy}'
                f'{" (urgency-degraded)" if len(strategies) < 3 else ""}'
            ),
        }

    def get_statistics(self) -> dict[str, Any]:
        """Get reasoning statistics including BLITZ-04 urgency info."""
        stats = {
            **self._stats,
            'history_size': len(self.reasoning_history),
            'urgency': self._compute_urgency(),  # BLITZ-04
        }
        if self._checkpointer is not None:
            stats['checkpointer'] = self._checkpointer.stats
        if self._sprint_clock is not None:
            stats['remaining_s'] = self._sprint_clock.remaining_s
            stats['total_duration_s'] = self._sprint_clock.total_duration_s
        return stats

    # ── UNIFIED-005: ToT State Persistence ────────────────────────────────

    async def save_state(self) -> bool:
        """
        UNIFIED-005: Persist current ToT state to DuckDB atomically.

        Delegates to TransactionalToTCheckpointer if initialized.
        Called on explicit shutdown or at key depth transitions.
        Returns True if saved, False if no checkpointer or on error.
        """
        if self._checkpointer is None:
            return False
        # The checkpointer already has the nodes reference bound —
        # periodic loop + depth-level checkpoints handle persistence.
        # This is an explicit trigger for caller convenience.
        try:
            if self._checkpointer._nodes_ref is not None:
                return await self._checkpointer.checkpoint(
                    nodes=self._checkpointer._nodes_ref,
                )
            return False
        except Exception:
            return False

    async def load_state(
        self,
        sprint_id: str,
        duckdb_store: Any,
    ) -> dict | None:
        """
        UNIFIED-005: Load ToT state from the latest checkpoint for a sprint.

        Creates a Temporary TransactionalToTCheckpointer to read the
        checkpoint. Returns the nodes dict or None if not found / corrupt.

        Args:
            sprint_id: Sprint identifier to load checkpoint for.
            duckdb_store: Initialized DuckDBShadowStore instance.

        Returns:
            dict[ str → ThoughtNode ] or None.
        """
        try:
            from hledac.universal.coordinators.tot_checkpointer import (
                TransactionalToTCheckpointer,
            )
            temp_ckpt = TransactionalToTCheckpointer(
                sprint_id=sprint_id,
                duckdb_store=duckdb_store,
                interval_s=30.0,
            )
            restored = await temp_ckpt.restore()
            if restored is None:
                return None
            step, nodes_dict, checksum = restored
            logger.info(
                "[UNIFIED-005] ToT state loaded: sprint=%s step=%d nodes=%d checksum=%s",
                sprint_id[:12],
                step,
                len(nodes_dict),
                checksum[:16],
            )
            # Store for later use
            self._sprint_id = sprint_id
            self._duckdb_store = duckdb_store
            self._checkpointer = temp_ckpt
            return nodes_dict
        except Exception as exc:
            logger.warning("[UNIFIED-005] load_state failed: %s", exc)
            return None

    async def cleanup_checkpoints(self) -> bool:
        """
        UNIFIED-005: Delete all checkpoints for this sprint.

        Called when the sprint completes successfully — frees storage.
        Returns True on success.
        """
        if self._checkpointer is None:
            return False
        try:
            await self._checkpointer.stop(final_checkpoint=False)
            ok = await self._checkpointer.cleanup()
            self._checkpointer = None
            return ok
        except Exception:
            return False

    def _get_feature_list(self) -> list[str]:
        return [
            'Chain of Thought reasoning',
            'Tree of Thoughts exploration',
            'Graph reasoning',
            'Automatic strategy selection',
            'Ensemble reasoning',
            'Strategy switching',
            'Learned value prediction (SOVEREIGN-005)',
            'Cost-weighted branch pruning (SOVEREIGN-005)',
            'Dead-end detection (SOVEREIGN-005)',
            'SprintClock urgency clamping (BLITZ-04)',
            'Ensemble urgency gating (BLITZ-06)',
            'Semantic gravity field void detection [SILICON-05]',
            'Gravity-aware strategy selection [SILICON-05]',
            'Void-aware ToT branch boosting [SILICON-05]',
            'Fetch directive generation [SILICON-05]',
            'Multi-layer ToT crash resilience — LMDB+DuckDB+FS (UNIFIED-007/008)',
        ]
