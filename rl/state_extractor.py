"""
Extrækce stavu pro MARL agenty.
Stav obsahuje globální informace (z grafu, scheduleru) a lokální informace z aktuálního běhu.

Podporuje dva režimy:
  1. extract(result: SprintSchedulerResult) — RL F257: čte přímo z výsledků sprintu
  2. extract_from_dicts(thread_state, global_state) — původní rozhraní pro dict-based input
"""

from __future__ import annotations

try:
    import mlx.core as mx
    import numpy as np
    MLX_AVAILABLE = True
except ImportError:
    MLX_AVAILABLE = False
    mx = None
    np = None

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from hledac.universal.runtime.sprint_scheduler import SprintSchedulerResult


# F265LANE: Known lane names for telemetry extraction
_KNOWN_LANES = ("PUBLIC", "CT", "WAYBACK", "DOH", "PASSIVE_DNS")


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

    def __init__(self, state_dim: int = 27, gnn_predictor: Optional = None):
        self.state_dim = state_dim
        self.gnn_predictor = gnn_predictor
        self._reward_ema = 0.0
        self._ema_alpha = 0.1
        # F265LANE: Per-lane sprint history for recency computation
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

            # Acceptance ratio as proxy for source_quality_avg
            acceptance_ratio = (
                findings_accepted / float(max(total_findings, 1))
                if total_findings > 0 else 0.0
            )

            # F265LANE: Extract lane performance from acquisition_lane_outcomes
            lane_yields = []
            lane_qualities = []
            lanes_in_result: set[str] = set()

            outcomes = getattr(result, 'acquisition_lane_outcomes', None) or ()
            for outcome in outcomes:
                lane_name = getattr(outcome, 'lane', None) or ""
                if lane_name not in _KNOWN_LANES:
                    continue
                lanes_in_result.add(lane_name)

                accepted = getattr(outcome, 'accepted_findings', 0) or 0
                rejected = getattr(outcome, 'rejected_count', 0) or 0
                duration = getattr(outcome, 'duration_s', 0.0) or 0.0

                # Yield: findings per second (cap at 10 findings/s)
                yield_val = accepted / max(duration, 0.001)
                lane_yields.append(min(yield_val / 10.0, 1.0))

                # Quality: acceptance ratio (cap at 1.0)
                quality = accepted / max(accepted + rejected, 1)
                lane_qualities.append(quality)

            # Pad lane vectors to full length (missing lanes = 0)
            while len(lane_yields) < len(_KNOWN_LANES):
                lane_yields.append(0.0)
            while len(lane_qualities) < len(_KNOWN_LANES):
                lane_qualities.append(0.0)

            # F265LANE: Recency vector — increment all, set used lanes to 0
            self._sprint_count += 1
            for lane in _KNOWN_LANES:
                if lane not in lanes_in_result:
                    self._sprints_since_lane[lane] = self._sprints_since_lane.get(lane, 0) + 1
                else:
                    self._sprints_since_lane[lane] = 0

            recency_vec = [
                min(self._sprints_since_lane.get(lane, 0) / 20.0, 1.0)
                for lane in _KNOWN_LANES
            ]

            features = [
                min(findings_accepted / 50.0, 1.0),          # [0] findings_accepted_norm
                min(runtime / 3600.0, 1.0),                  # [1] runtime_seconds_norm
                min(cycles / 50.0, 1.0),                     # [2] cycles_completed_norm
                acceptance_ratio,                            # [3] acceptance_ratio
                min(new_iocs / 100.0, 1.0),                 # [4] new_iocs_norm
                getattr(result, 'source_quality_avg', acceptance_ratio),  # [5] source_quality_avg
                min(queue_size / 200.0, 1.0),                # [6] queue_size_norm
                min(memory_pressure, 1.0),                  # [7] memory_pressure_norm
                min(graph_entropy, 1.0),                     # [8] graph_entropy_norm
                min(time_since_finding / 300.0, 1.0),       # [9] time_since_last_finding_norm
                min(resource_conc, 1.0),                    # [10] resource_concurrency_norm
                self._reward_ema,                            # [11] reward_ema
                # F265LANE: Lane vectors [12-26]
                *lane_yields,      # [12-16] Lane yield vector
                *lane_qualities,  # [17-21] Lane quality vector
                *recency_vec,     # [22-26] Lane recency vector
            ]

            # Update EMA for reward tracking
            last_reward = getattr(result, 'last_reward', None)
            if last_reward is not None:
                self._reward_ema = self._ema_alpha * last_reward + (1 - self._ema_alpha) * self._reward_ema

            # GNN embedding (pokud k dispozici)
            if self.gnn_predictor is not None:
                try:
                    graph_emb = self.gnn_predictor.get_graph_embedding()
                    features.extend(graph_emb.tolist())
                except AttributeError:
                    pass

            # Zarovnání na state_dim
            if len(features) < self.state_dim:
                features += [0.0] * (self.state_dim - len(features))
            else:
                features = features[:self.state_dim]

            if MLX_AVAILABLE:
                return mx.array(features)
            return np.array(features, dtype=np.float32)

        except Exception:
            # Fail-soft: return zero vector
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
            features = [
                thread_state.get('entity_centrality', 0.0),
                thread_state.get('novelty', 0.0),
                float(thread_state.get('depth', 0)),
                float(thread_state.get('contradiction', 0)),
                float(thread_state.get('source_type', 0)),
                global_state.get('queue_size', 0) / 200.0,
                min(global_state.get('memory_pressure', 0.0), 1.0),
                min(global_state.get('graph_entropy', 0.0), 1.0),
                global_state.get('avg_reward', 0.0) / 100.0,
                global_state.get('num_pending_tasks', 0) / 50.0,
                min(global_state.get('time_since_last_finding', 0.0) / 300.0, 1.0),
                min(global_state.get('resource_concurrency', 0.0), 1.0),
            ]

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
