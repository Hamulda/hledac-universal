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


class StateExtractor:
    """
    Builder 12-dim observation vector from sprint state.

    Feature vector layout (12 dim):
      [0] findings_accepted_norm     — normalized findings accepted (0-1, cap 50)
      [1] runtime_seconds_norm        — normalized runtime (0-1, cap 3600s)
      [2] cycles_completed_norm      — normalized cycles (0-1, cap 50)
      [3] acceptance_ratio            — accepted/total findings (0-1)
      [4] new_iocs_norm              — normalized new IOC count (0-1, cap 100)
      [5] source_quality_avg         — avg source quality (0-1)
      [6] queue_size_norm            — normalized pending count (0-1, cap 200)
      [7] memory_pressure_norm        — normalized RAM pressure (0-1)
      [8] graph_entropy_norm          — normalized graph entropy (0-1)
      [9] time_since_last_finding_norm — normalized time (0-1, cap 300s)
      [10] resource_concurrency_norm — normalized concurrency (0-1)
      [11] reward_ema                — exponential moving avg reward (bounded)
    """

    def __init__(self, state_dim: int = 12, gnn_predictor: Optional = None):
        self.state_dim = state_dim
        self.gnn_predictor = gnn_predictor
        self._reward_ema = 0.0
        self._ema_alpha = 0.1

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
            runtime = getattr(result, 'runtime_seconds', 0) or 0
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
