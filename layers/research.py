"""
Research Layer - Deep Research and Temporal Analysis
====================================================

Consolidated from:
- research_layer.py: ResearchLayer (GhostDirector, depth maximizer, hunter)
- temporal_signal_layer.py: TemporalSignalLayer (OSINT temporal intelligence)

Features:
- GhostDirector integration for autonomous investigation
- Deep research with citation following
- URL hunting and content extraction
- Temporal signal scoring (burst detection, periodicity)
- Change-point detection (Page-Hinkley + BOCPD-lite)

M1 8GB: Uses __slots__ for memory efficiency, no numpy/pandas/mlx.
"""
from __future__ import annotations

import heapq
import itertools
import logging
import math
import time
from collections import deque
from collections.abc import Iterable
from typing import Any

import msgspec
from compat.msgspec_gc_compat import Struct
from hledac.universal.project_types import (
    DeepResearchConfig,
    ExplorationNode,
    ExplorationStrategy,
    GhostMission,
)

logger = logging.getLogger(__name__)

__all__ = [
    'ResearchLayer',
    'TemporalSignalLayer',
    'TemporalEvent',
    'TemporalScore',
    'TemporalEdgeCandidate',
    '_KeyState',
    'event_from_finding_like',
]

# ─── Temporal Signal Layer ──────────────────────────────────────────────────


DEFAULT_MAX_KEYS = 4096
DEFAULT_RING_SIZE = 32
DEFAULT_HALF_LIFE_S = 900.0
DEFAULT_SYNCHRONY_WINDOW_S = 300.0
DEFAULT_BOCPD_MAX_RUN = 32
CONFIRMATION_BOOST_MAX = 1.5
CONFIRMATION_BOOST_MIN = 0.5
CONFIRMATION_DECAY = 0.05
CONFIRMATION_GROWTH = 0.1


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b != 0.0 and not math.isnan(b) else default


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


class TemporalEvent(Struct, frozen=True, gc=False):
    """Temporal event for OSINT signal tracking."""
    ts: float
    key: str
    family: str = 'generic'
    source: str = ''
    weight: float = 1.0
    labels: tuple[str, ...] = ()


class TemporalScore(Struct, frozen=True, gc=False):
    """Temporal score for a key."""
    key: str
    family: str
    event_count: int
    anomaly_score: float
    burst_score: float
    periodicity_score: float
    change_point_score: float
    source_synchrony_score: float
    rate_score: float
    cv_isi: float
    mean_gap_s: float
    autocorr_lag1: float
    reason: str


class TemporalEdgeCandidate(Struct, frozen=True, gc=False):
    """Temporal edge candidate for graph construction."""
    src_key: str
    dst_key: str
    edge_type: str
    score: float
    window_start: float
    window_end: float
    reason: str


class _KeyState(Struct, gc=False):
    """Internal state for temporal tracking."""
    last_ts: float = 0.0
    event_count: int = 0
    ewma_rate: float = 0.0
    ewma_gap: float = 0.0
    gap_variance: float = 0.0
    ring_gaps: deque[float] = deque(maxlen=DEFAULT_RING_SIZE)
    ring_sources: deque[str] = deque(maxlen=DEFAULT_RING_SIZE)
    last_score: TemporalScore | None = None
    confirmation_weight: float = 1.0
    last_updated: float = 0.0
    ph_cumsum: float = 0.0
    ph_mean: float = 0.0
    bocpd_run_length: int = 0
    bocpd_log_odds: float = 0.0


class TemporalSignalLayer:
    """
    Bounded temporal signal scoring layer.

    Features:
    - Burst detection
    - Periodicity / check-in scoring
    - Change-point detection (Page-Hinkley + BOCPD-lite)
    - Source synchrony (Jaccard sliding window)
    - Temporal edge candidates
    - Feedback loop from confirmations

    M1 8GB: Pure Python, no numpy/pandas/mlx.
    """
    __slots__ = tuple((
        '_bocpd_max_run',
        '_edge_candidates',
        '_half_life_s',
        '_lru_order',
        '_max_keys',
        '_ring_size',
        '_states',
        '_sync_window',
        '_synchrony_window_s',
    ))

    def __init__(
        self,
        max_keys: int = DEFAULT_MAX_KEYS,
        ring_size: int = DEFAULT_RING_SIZE,
        half_life_s: float = DEFAULT_HALF_LIFE_S,
        synchrony_window_s: float = DEFAULT_SYNCHRONY_WINDOW_S,
        bocpd_max_run: int = DEFAULT_BOCPD_MAX_RUN,
    ) -> None:
        self._max_keys = max_keys
        self._ring_size = ring_size
        self._half_life_s = half_life_s
        self._synchrony_window_s = synchrony_window_s
        self._bocpd_max_run = bocpd_max_run
        self._states: dict[str, _KeyState] = {}
        self._lru_order: deque[str] = deque()
        self._edge_candidates: deque[TemporalEdgeCandidate] = deque(maxlen=256)
        self._sync_window: deque[tuple[float, str, frozenset[str]]] = deque(maxlen=256)

    def observe(self, event: TemporalEvent) -> TemporalScore:
        """Observe an event and return temporal score."""
        key = event.key
        ts = event.ts

        if key not in self._states:
            self._ensure_capacity()
            self._states[key] = _KeyState(
                ring_gaps=deque(maxlen=self._ring_size),
                ring_sources=deque(maxlen=self._ring_size),
            )
            self._lru_order.append(key)

        state = self._states[key]
        state.last_updated = ts

        if key in self._lru_order:
            self._lru_order.remove(key)
        self._lru_order.append(key)

        family = event.family
        event.weight * state.confirmation_weight
        event_count = state.event_count + 1
        gap_s = 0.0

        if event_count > 1:
            gap_s = ts - state.last_ts
            if gap_s > 0:
                state.ring_gaps.append(gap_s)
                state.ring_sources.append(event.source)

        # Update EWMA
        if state.event_count == 0:
            state.ewma_gap = gap_s if gap_s > 0 else 0.1
            state.ewma_rate = 1.0 / state.ewma_gap if state.ewma_gap > 0 else 1.0
        else:
            prev_gap = state.ewma_gap
            if gap_s > 0:
                state.ewma_gap = 0.5 * gap_s + 0.5 * prev_gap
                state.ewma_rate = 1.0 / state.ewma_gap if state.ewma_gap > 0 else 1.0

        # Record in sync window
        self._sync_window.append((ts, event.source, frozenset([key])))

        # Compute scores
        burst_score = self._compute_burst_score(gap_s, state.ewma_gap)
        periodicity_score, cv_isi, autocorr_lag1, mean_gap_s = self._compute_periodicity_metrics(state.ring_gaps)
        change_point_score = self._compute_change_point_score(state, gap_s, event_count)
        source_synchrony_score = self._compute_source_synchrony_score(ts)
        rate_score = self._compute_rate_score(state.last_ts, state.ewma_rate, gap_s)
        anomaly_score = self._aggregate_anomaly_score(
            burst_score, change_point_score, periodicity_score, rate_score
        )
        reason = self._build_reason_string(
            event_count, burst_score, periodicity_score,
            change_point_score, source_synchrony_score, anomaly_score
        )

        state.last_ts = ts
        state.event_count = event_count
        state.last_score = TemporalScore(
            key=key, family=family, event_count=event_count,
            anomaly_score=anomaly_score, burst_score=burst_score,
            periodicity_score=periodicity_score, change_point_score=change_point_score,
            source_synchrony_score=source_synchrony_score, rate_score=rate_score,
            cv_isi=cv_isi, mean_gap_s=mean_gap_s, autocorr_lag1=autocorr_lag1, reason=reason,
        )

        self._update_edge_candidates(state, ts, burst_score, source_synchrony_score)
        return state.last_score

    def observe_many(self, events: Iterable[TemporalEvent]) -> list[TemporalScore]:
        """Observe multiple events."""
        return [self.observe(event) for event in events]

    def observe_confirmation(self, key: str, confirmed: bool, source: str = '') -> None:
        """Feedback loop — confirmed boosts weight, unconfirmed decays."""
        if key not in self._states:
            return
        state = self._states[key]
        if confirmed:
            state.confirmation_weight = min(
                state.confirmation_weight + CONFIRMATION_GROWTH, CONFIRMATION_BOOST_MAX
            )
        else:
            state.confirmation_weight = max(
                state.confirmation_weight - CONFIRMATION_DECAY, CONFIRMATION_BOOST_MIN
            )

    def get_top_scores(self, k: int = 20) -> list[TemporalScore]:
        """Get top k anomaly scores."""
        scored = []
        for s in self._states.values():
            ls = s.last_score
            if ls is not None:
                scored.append((ls.anomaly_score, ls))
        return [score for _, score in heapq.nlargest(k, scored, key=lambda x: x[0])]

    def get_edge_candidates(self, k: int = 50) -> list[TemporalEdgeCandidate]:
        """Get top k edge candidates."""
        from operator import attrgetter
        return heapq.nlargest(k, self._edge_candidates, key=attrgetter('score'))

    def _compute_burst_score(self, gap_s: float, ewma_gap: float) -> float:
        """Compute burst score from gap timing."""
        burst_score = 0.0
        if gap_s > 0 and ewma_gap > 0:
            gap_ratio = ewma_gap / gap_s
            burst_score = _clamp((gap_ratio - 1.0) / (gap_ratio + 1.0), 0.0, 1.0)
        return burst_score

    def _compute_periodicity_metrics(
        self, ring_gaps: deque[float],
    ) -> tuple[float, float, float, float]:
        """Compute periodicity metrics from ring buffer."""
        periodicity_score = 0.0
        cv_isi = 0.0
        autocorr_lag1 = 0.0
        mean_gap_s = 0.0

        if len(ring_gaps) >= 4:
            gaps_list = list(ring_gaps)
            mean_g = sum(gaps_list) / len(gaps_list)
            if mean_g > 0:
                variance = sum((g - mean_g) ** 2 for g in gaps_list) / len(gaps_list)
                std_g = math.sqrt(variance)
                cv_isi = _safe_div(std_g, mean_g)

            # Autocorrelation
            n = len(gaps_list)
            mean_g = sum(gaps_list) / n
            var_g = sum((g - mean_g) ** 2 for g in gaps_list)
            if var_g > 0 and n >= 4:
                cov = sum(gaps_list[i] * gaps_list[i + 1] for i in range(n - 1)) / (n - 1) - mean_g * mean_g
                autocorr_lag1 = _safe_div(cov, var_g)

            if cv_isi < 0.01:
                periodicity_score = 1.0
            else:
                cv_penalty = _clamp(cv_isi / 2.0, 0.0, 1.0)
                periodicity_score = (1.0 - cv_penalty) * 0.5 + (0.5 + autocorr_lag1 * 0.5)
                periodicity_score = _clamp(periodicity_score, 0.0, 1.0)

            mean_gap_s = sum(gaps_list) / len(gaps_list)

        return periodicity_score, cv_isi, autocorr_lag1, mean_gap_s

    def _compute_change_point_score(
        self, state: _KeyState, gap_s: float, event_count: int,
    ) -> float:
        """Compute change point score using Page-Hinkley and BOCPD-lite."""
        change_point_score = 0.0
        if event_count >= 2 and gap_s > 0:
            ph_alpha = 0.01
            if state.ph_mean == 0.0:
                state.ph_mean = gap_s
            else:
                delta = gap_s - state.ph_mean
                state.ph_mean = 0.5 * delta + state.ph_mean
                state.ph_cumsum = state.ph_cumsum + delta - ph_alpha
                if state.ph_mean > 0:
                    state.ph_cumsum = max(0.0, state.ph_cumsum + delta - ph_alpha)
                else:
                    state.ph_cumsum = min(0.0, state.ph_cumsum + delta + ph_alpha)
            change_point_score = _clamp(abs(state.ph_cumsum) / 100.0, 0.0, 1.0)

            if state.bocpd_run_length < self._bocpd_max_run:
                state.bocpd_run_length += 1
                state.bocpd_log_odds = math.log(state.bocpd_run_length + 1)
            else:
                state.bocpd_run_length = 0
                state.bocpd_log_odds = 5.0

            bocpd_score = _safe_div(state.bocpd_log_odds, 10.0)
            change_point_score = max(change_point_score, _clamp(bocpd_score, 0.0, 1.0))

        return change_point_score

    def _compute_source_synchrony_score(self, ts: float) -> float:
        """Compute source synchrony score via Jaccard similarity."""
        source_synchrony_score = 0.0
        if len(self._sync_window) >= 2:
            window_start = ts - self._synchrony_window_s
            active_sources: dict[str, set[str]] = {}
            for w_ts, src, keys in self._sync_window:
                if w_ts >= window_start:
                    if src not in active_sources:
                        active_sources[src] = set()
                    active_sources[src].update(keys)

            if len(active_sources) >= 2:
                sources = list(active_sources.keys())
                jaccard_scores = []
                for idx_i, idx_j in itertools.combinations(range(min(len(sources), 8)), 2):
                    set_i = active_sources[sources[idx_i]]
                    set_j = active_sources[sources[idx_j]]
                    if set_i and set_j:
                        inter = len(set_i & set_j)
                        union = len(set_i | set_j)
                        jaccard_scores.append(_safe_div(inter, union))
                if jaccard_scores:
                    source_synchrony_score = sum(jaccard_scores) / len(jaccard_scores)
        return source_synchrony_score

    def _compute_rate_score(self, last_ts: float, ewma_rate: float, gap_s: float) -> float:
        """Compute rate score based on current vs expected rate."""
        rate_score = 0.0
        if last_ts > 0 and ewma_rate > 0:
            current_rate = 1.0 / gap_s if gap_s > 0 else 0.0
            rate_ratio = _safe_div(current_rate, ewma_rate)
            rate_score = _clamp((rate_ratio - 0.5) / 2.0, 0.0, 1.0)
        return rate_score

    def _aggregate_anomaly_score(
        self, burst_score: float, change_point_score: float,
        periodicity_score: float, rate_score: float,
    ) -> float:
        """Aggregate individual scores into anomaly score."""
        return _clamp(
            burst_score * 0.3
            + _safe_div(change_point_score, 3.0) * 0.3
            + (1.0 - periodicity_score) * 0.2
            + rate_score * 0.2,
            0.0, 1.0,
        )

    def _build_reason_string(
        self, event_count: int, burst_score: float, periodicity_score: float,
        change_point_score: float, source_synchrony_score: float, anomaly_score: float,
    ) -> str:
        """Build human-readable reason string."""
        if event_count < 2:
            return 'insufficient_history'
        reasons = []
        if burst_score > 0.6:
            reasons.append('burst')
        if periodicity_score > 0.6:
            reasons.append('periodic')
        if change_point_score > 0.6:
            reasons.append('change_point')
        if source_synchrony_score > 0.5:
            reasons.append('source_synchrony')
        if anomaly_score > 0.7:
            reasons.append('anomaly')
        return '|'.join(reasons) if reasons else 'normal'

    def _ensure_capacity(self) -> None:
        """Ensure capacity for new keys (LRU eviction)."""
        while len(self._states) >= self._max_keys and self._lru_order:
            oldest = self._lru_order.popleft()
            self._states.pop(oldest, None)

    def _update_edge_candidates(
        self, state: _KeyState, ts: float,
        burst_score: float, source_synchrony_score: float,
    ) -> None:
        """Update edge candidates from burst and synchrony."""
        if burst_score > 0.6:
            for other_key, other_state in self._states.items():
                if other_state.last_score and other_state.last_score.burst_score > 0.4:
                    if not state.last_score:
                        continue
                    window_start = ts - self._synchrony_window_s
                    candidate = TemporalEdgeCandidate(
                        src_key=state.last_score.key,
                        dst_key=other_key,
                        edge_type='co_burst',
                        score=(burst_score + other_state.last_score.burst_score) / 2.0,
                        window_start=window_start,
                        window_end=ts,
                        reason='co_burst',
                    )
                    self._edge_candidates.append(candidate)


# ─── Research Layer ──────────────────────────────────────────────────────────


class ResearchLayer:
    """
    Research layer for deep investigation with GhostDirector and temporal analysis.

    Features:
    - GhostDirector for autonomous actions
    - Deep research with citation following
    - Temporal signal scoring for OSINT
    - URL hunting and content extraction

    M1 8GB: Uses __slots__ for memory efficiency.
    """
    layer_name: str = 'research'
    _priority: int = 80  # High priority

    __slots__ = tuple((
        '_actions_executed',
        '_depth_levels_reached',
        '_depth_maximizer',
        '_explorations',
        '_ghost_director',
        '_ghost_director_shared',
        '_hunter',
        '_missions',
        '_missions_completed',
        '_temporal_layer',
        'config',
    ))

    def __init__(
        self,
        config: DeepResearchConfig | None = None,
        ghost_director: Any | None = None,
    ) -> None:
        self.config = config or DeepResearchConfig()
        self._ghost_director = ghost_director
        self._ghost_director_shared = ghost_director is not None
        self._depth_maximizer = None
        self._hunter = None
        self._missions: dict[str, GhostMission] = {}
        self._explorations: dict[str, list[ExplorationNode]] = {}
        self._missions_completed = 0
        self._actions_executed = 0
        self._depth_levels_reached = 0
        self._temporal_layer = TemporalSignalLayer()
        logger.info(
            f"ResearchLayer initialized "
            f"(GhostDirector: {'shared' if self._ghost_director_shared else 'lazy'})"
        )

    async def mount(self, ctx: Any) -> None:
        """Mount the research layer."""
        await self.initialize()
        ctx.set('research', self)
        ctx.set('temporal', self._temporal_layer)

    async def unmount(self, ctx: Any) -> None:
        """Unmount the research layer."""
        await self.cleanup()

    async def process(self, ctx: Any, data: Any) -> Any:
        """Process data through research layer."""
        return data

    async def rollback(self, ctx: Any, error: Exception) -> None:
        """Rollback on error."""
        logger.warning(f'ResearchLayer rollback: {error}')

    async def initialize(self) -> bool:
        """Initialize ResearchLayer components."""
        try:
            logger.info('🚀 Initializing ResearchLayer...')
            self._temporal_layer = TemporalSignalLayer()
            logger.info('✅ ResearchLayer initialized successfully')
            return True
        except Exception as e:
            logger.error(f'❌ ResearchLayer initialization failed: {e}')
            return False

    def create_mission(self, goal: str) -> GhostMission:
        """Create a new GhostDirector mission."""
        import uuid
        mission_id = str(uuid.uuid7())[:8]
        mission = GhostMission(
            mission_id=mission_id,
            goal=goal,
            actions=[],
            current_step=0,
            acquired_loot=[],
            anti_loop_counter=0,
        )
        self._missions[mission_id] = mission
        logger.info(f'🎯 Mission created: {mission_id} - {goal[:50]}...')
        return mission

    async def execute_mission(
        self,
        mission: GhostMission,
        max_steps: int | None = None,
    ) -> dict[str, Any]:
        """Execute a GhostDirector mission."""
        if self._ghost_director is None:
            await self._init_ghost_director()
        if self._ghost_director is None:
            logger.error('❌ GhostDirector not available')
            return {'success': False, 'error': 'GhostDirector not available'}

        max_steps = max_steps or 20
        logger.info(f'🚀 Executing mission: {mission.mission_id}')
        try:
            result = await self._ghost_director.start_investigation(mission.goal)
            self._missions_completed += 1
            self._actions_executed += result.get('actions_count', 0)
            mission.acquired_loot = result.get('loot', [])
            return {
                'success': True,
                'mission_id': mission.mission_id,
                'goal': mission.goal,
                'actions_executed': result.get('actions_count', 0),
                'loot_count': len(mission.acquired_loot),
                'findings': result.get('findings', []),
                'duration': result.get('duration', 0),
            }
        except Exception as e:
            logger.error(f'❌ Mission execution failed: {e}')
            return {'success': False, 'mission_id': mission.mission_id, 'error': str(e)}

    async def _init_ghost_director(self) -> None:
        """Lazy initialization of GhostDirector."""
        if self._ghost_director_shared and self._ghost_director is not None:
            logger.debug('Using shared GhostDirector')
            return
        if self._ghost_director is None:
            try:
                from hledac.universal.cortex.director import GhostDirector
                self._ghost_director = GhostDirector(max_steps=20)
                await self._ghost_director.initialize_drivers()
                logger.info('✅ GhostDirector initialized (local)')
            except Exception as e:
                logger.warning(f'⚠️ GhostDirector not available: {e}')
                self._ghost_director = None

    async def deep_explore(
        self,
        start_url: str,
        strategy: ExplorationStrategy | None = None,
        max_depth: int | None = None,
    ) -> list[ExplorationNode]:
        """Perform deep research exploration."""
        strategy = strategy or ExplorationStrategy(self.config.strategy)
        max_depth = max_depth or self.config.max_depth
        logger.info(f'🔍 Deep exploration: {start_url} (strategy: {strategy.value}, max_depth: {max_depth})')
        return [ExplorationNode(
            node_id='root',
            url=start_url,
            title='Root',
            depth=0,
            parent_id=None,
            children=[],
            citations=[],
            quality_score=1.0,
        )]

    # Temporal signal methods
    def observe_temporal_event(self, event: TemporalEvent) -> TemporalScore:
        """Observe a temporal event and get score."""
        return self._temporal_layer.observe(event)

    def observe_temporal_events(self, events: Iterable[TemporalEvent]) -> list[TemporalScore]:
        """Observe multiple temporal events."""
        return self._temporal_layer.observe_many(events)

    def observe_confirmation(self, key: str, confirmed: bool, source: str = '') -> None:
        """Feedback loop for temporal events."""
        self._temporal_layer.observe_confirmation(key, confirmed, source)

    def get_top_temporal_scores(self, k: int = 20) -> list[TemporalScore]:
        """Get top k anomaly scores."""
        return self._temporal_layer.get_top_scores(k)

    def get_temporal_edge_candidates(self, k: int = 50) -> list[TemporalEdgeCandidate]:
        """Get top k edge candidates."""
        return self._temporal_layer.get_edge_candidates(k)

    def get_statistics(self) -> dict[str, Any]:
        """Get research layer statistics."""
        return {
            'missions_completed': self._missions_completed,
            'actions_executed': self._actions_executed,
            'depth_levels_reached': self._depth_levels_reached,
            'active_missions': len(self._missions),
            'ghost_director_available': self._ghost_director is not None,
            'temporal_keys': len(self._temporal_layer._states),
            'config': {
                'max_depth': self.config.max_depth,
                'strategy': self.config.strategy,
                'follow_citations': self.config.follow_citations,
                'explore_tangents': self.config.explore_tangents,
            },
        }

    async def cleanup(self) -> None:
        """Cleanup resources."""
        logger.info('🧹 Cleaning up ResearchLayer...')
        if self._ghost_director and hasattr(self._ghost_director, 'cleanup'):
            try:
                await self._ghost_director.cleanup()
            except Exception as e:
                logger.warning(f'⚠️ GhostDirector cleanup error: {e}')
        if self._depth_maximizer and hasattr(self._depth_maximizer, 'stop'):
            try:
                await self._depth_maximizer.stop()
            except Exception as e:
                logger.warning(f'⚠️ DepthMaximizer cleanup error: {e}')
        if self._hunter and hasattr(self._hunter, 'cleanup'):
            try:
                await self._hunter.cleanup()
            except Exception as e:
                logger.warning(f'⚠️ Hunter cleanup error: {e}')
        self._missions.clear()
        self._explorations.clear()
        logger.info('✅ ResearchLayer cleanup complete')


__all__ = [
    'ResearchLayer',
    'TemporalSignalLayer',
    'TemporalEvent',
    'TemporalScore',
    'TemporalEdgeCandidate',
]
