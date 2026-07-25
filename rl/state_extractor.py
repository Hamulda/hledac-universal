"""
Extrækce stavu pro MARL agenty.
Stav obsahuje globální informace (z grafu, scheduleru) a lokální informace z aktuálního běhu.

Podporuje dva režimy:
  1. extract(result: SprintSchedulerResult) — RL F257: čte přímo z výsledků sprintu
  2. extract_from_dicts(thread_state, global_state) — původní rozhraní pro dict-based input
"""
try:
    import mlx.core as mx
    import numpy as np
    MLX_AVAILABLE = True
except ImportError:
    MLX_AVAILABLE = False
    mx = None
    np = None
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from hledac.universal.runtime.scheduler_result import SprintSchedulerResult
_KNOWN_LANES = ('PUBLIC', 'CT', 'WAYBACK', 'DOH', 'PASSIVE_DNS')

class StateExtractor:
    """
    Builder 27-dim observation vector from sprint state (F265LANE).

    Feature vector layout (27 dim):
      [0-11] Base features (12 dim, backward compatible):
        [0] findings_accepted_norm     — normalized findings accepted (0-1, cap 50)
        [1] runtime_seconds_norm      — normalized runtime (0-1, cap 3600s)
        [2] cycles_completed_norm     — normalized cycles (0-1, cap 50)
        [3] acceptance_ratio           — accepted/total findings (0-1)
        [4] new_iocs_norm             — normalized new IOC count (0-1, cap 100)
        [5] source_quality_avg        — avg source quality (0-1)
        [6] queue_size_norm           — normalized pending count (0-1, cap 200)
        [7] memory_pressure_norm       — normalized RAM pressure (0-1)
        [8] graph_entropy_norm         — normalized graph entropy (0-1)
        [9] time_since_last_finding_norm — normalized time (0-1, cap 300s)
        [10] resource_concurrency_norm — normalized concurrency (0-1)
        [11] reward_ema               — exponential moving avg reward (bounded)
      [12-16] Lane yield vector (5 dim): yield = accepted_findings / max(duration_s, 0.001)
        [12] PUBLIC_yield, [13] CT_yield, [14] WAYBACK_yield, [15] DOH_yield, [16] PASSIVE_DNS_yield
      [17-21] Lane quality vector (5 dim): quality = accepted / max(accepted + rejected, 1)
        [17] PUBLIC_quality, [18] CT_quality, [19] WAYBACK_quality, [20] DOH_quality, [21] PASSIVE_DNS_quality
      [22-26] Lane recency vector (5 dim): sprints since last used (0-1, cap 20 sprints)
        [22] sprints_since_PUBLIC, [23] sprints_since_CT, [24] sprints_since_WAYBACK,
        [25] sprints_since_DOH, [26] sprints_since_PASSIVE_DNS
    """
    __slots__ = tuple(('_ema_alpha', '_reward_ema', '_sprint_count', '_sprints_since_lane', 'gnn_predictor', 'state_dim'))

    def __init__(self, state_dim: int=27, gnn_predictor: Optional=None):
        self.state_dim = state_dim
        self.gnn_predictor = gnn_predictor
        self._reward_ema = 0.0
        self._ema_alpha = 0.1
        self._sprints_since_lane: dict[str, int] = dict.fromkeys(_KNOWN_LANES, 0)
        self._sprint_count: int = 0

    def extract(self, result: SprintSchedulerResult) -> np.ndarray:
        """
        Extract 12-dim observation from SprintSchedulerResult fields.

        Fails softly — returns zero vector on any AttributeError.
        Uses real SprintSchedulerResult fields:
          - findings_accepted, findings_total, runtime_seconds
          - cycles_completed, new_iocs, pending_count
          - memory_pressure, graph_entropy, time_since_last_finding
          - resource_concurrency, source_quality_avg, last_reward
        """
        try:
            findings_accepted = getattr(result, 'findings_accepted', 0) or 0
            total_findings = getattr(result, 'findings_total', 0) or 0
            runtime = getattr(result, 'actual_duration_s', 0) or 0
            cycles = getattr(result, 'cycles_completed', 0) or 0
            new_iocs = getattr(result, 'new_iocs', 0) or 0
            queue_size = getattr(result, 'pending_count', 0) or 0
            memory_pressure = getattr(result, 'memory_pressure', 0.0) or 0.0
            graph_entropy = getattr(result, 'graph_entropy', 0.0) or 0.0
            time_since_finding = getattr(result, 'time_since_last_finding', 0.0) or 0.0
            resource_conc = getattr(result, 'resource_concurrency', 0.0) or 0.0
            acceptance_ratio = findings_accepted / float(max(total_findings, 1)) if total_findings > 0 else 0.0
            lane_yields = []
            lane_qualities = []
            lanes_in_result: set[str] = set()
            outcomes = getattr(result, 'acquisition_lane_outcomes', None) or ()
            for outcome in outcomes:
                lane_name = getattr(outcome, 'lane', None) or ''
                if lane_name not in _KNOWN_LANES:
                    continue
                lanes_in_result.add(lane_name)
                accepted = getattr(outcome, 'accepted_findings', 0) or 0
                rejected = getattr(outcome, 'rejected_count', 0) or 0
                duration = getattr(outcome, 'duration_s', 0.0) or 0.0
                yield_val = accepted / max(duration, 0.001)
                lane_yields.append(min(yield_val / 10.0, 1.0))
                quality = accepted / max(accepted + rejected, 1)
                lane_qualities.append(quality)
            while len(lane_yields) < len(_KNOWN_LANES):
                lane_yields.append(0.0)
            while len(lane_qualities) < len(_KNOWN_LANES):
                lane_qualities.append(0.0)
            self._sprint_count += 1
            for lane in _KNOWN_LANES:
                if lane not in lanes_in_result:
                    self._sprints_since_lane[lane] = self._sprints_since_lane.get(lane, 0) + 1
                else:
                    self._sprints_since_lane[lane] = 0
            recency_vec = [min(self._sprints_since_lane.get(lane, 0) / 20.0, 1.0) for lane in _KNOWN_LANES]
            features = [min(findings_accepted / 50.0, 1.0), min(runtime / 3600.0, 1.0), min(cycles / 50.0, 1.0), acceptance_ratio, min(new_iocs / 100.0, 1.0), getattr(result, 'source_quality_avg', acceptance_ratio), min(queue_size / 200.0, 1.0), min(memory_pressure, 1.0), min(graph_entropy, 1.0), min(time_since_finding / 300.0, 1.0), min(resource_conc, 1.0), self._reward_ema, *lane_yields, *lane_qualities, *recency_vec]
            last_reward = getattr(result, 'last_reward', None)
            if last_reward is not None:
                self._reward_ema = self._ema_alpha * last_reward + (1 - self._ema_alpha) * self._reward_ema
            if self.gnn_predictor is not None:
                try:
                    graph_emb = self.gnn_predictor.get_graph_embedding()
                    features.extend(graph_emb.tolist())
                except AttributeError:
                    pass
            if len(features) < self.state_dim:
                features += [0.0] * (self.state_dim - len(features))
            else:
                features = features[:self.state_dim]
            if MLX_AVAILABLE:
                return mx.array(features)
            return np.array(features, dtype=np.float32)
        except Exception:
            if MLX_AVAILABLE:
                return mx.zeros(self.state_dim)
            return np.zeros(self.state_dim, dtype=np.float32)

    def extract_next(self, result: SprintSchedulerResult) -> np.ndarray:
        """Alias for extract — next state = current observation in batch setting."""
        return self.extract(result)

    def extract_from_dicts(self, thread_state: dict, global_state: dict) -> np.ndarray:
        """
        Původní dict-based rozhraní — zachováno pro zpětnou kompatibilitu.

        Preferované použití: extract(result) čte přímo z SprintSchedulerResult.
        """
        try:
            features = [thread_state.get('entity_centrality', 0.0), thread_state.get('novelty', 0.0), float(thread_state.get('depth', 0)), float(thread_state.get('contradiction', 0)), float(thread_state.get('source_type', 0)), global_state.get('queue_size', 0) / 200.0, min(global_state.get('memory_pressure', 0.0), 1.0), min(global_state.get('graph_entropy', 0.0), 1.0), global_state.get('avg_reward', 0.0) / 100.0, global_state.get('num_pending_tasks', 0) / 50.0, min(global_state.get('time_since_last_finding', 0.0) / 300.0, 1.0), min(global_state.get('resource_concurrency', 0.0), 1.0)]
            if MLX_AVAILABLE:
                return mx.array(features)
            return np.array(features, dtype=np.float32)
        except Exception:
            if MLX_AVAILABLE:
                return mx.zeros(self.state_dim)
            return np.zeros(self.state_dim, dtype=np.float32)

    def extract_from_result(self, result: SprintSchedulerResult) -> np.ndarray:
        """Alias for extract — accepts SprintSchedulerResult for QMIX inference."""
        return self.extract(result)