"""
Extrækce stavu pro MARL agenty.
Stav obsahuje globální informace (z grafu, scheduleru) a lokální informace z aktuálního běhu.

Podporuje dva režimy:
  1. extract(result: SprintSchedulerResult) — RL F257: čte přímo z výsledků sprintu
  2. extract_from_dicts(thread_state, global_state) — původní rozhraní pro dict-based input
"""

# C1-X FIX: Import MLX_AVAILABLE from SSOT (zero-import detection)
from hledac.universal.utils.mlx_memory import MLX_AVAILABLE


# Lazy accessor for mlx.core — uses centralized get_mx() from SSOT
def _get_mx():
    """Lazy accessor for mlx.core — uses centralized get_mx() from SSOT."""
    from hledac.universal.utils.mlx_memory._core import get_mx as _get_mx_from_core

    return _get_mx_from_core()


# Initialize to None; will be set when first accessed
mx = None

# Only attempt MLX import if SSOT says it's available
if MLX_AVAILABLE:
    try:
        import mlx.core as mx
    except ImportError:
        mx = None

from typing import TYPE_CHECKING, Optional

import numpy as np

if TYPE_CHECKING:
    from hledac.universal.runtime.scheduler_result import SprintSchedulerResult
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

    __slots__ = ("_ema_alpha", "_reward_ema", "_sprint_count", "_sprints_since_lane", "gnn_predictor", "state_dim")

    def __init__(self, state_dim: int = 27, gnn_predictor: Optional = None) -> None:
        self.state_dim = state_dim
        self.gnn_predictor = gnn_predictor
        self._reward_ema = 0.0
        self._ema_alpha = 0.1
        self._sprints_since_lane: dict[str, int] = dict.fromkeys(_KNOWN_LANES, 0)
        self._sprint_count: int = 0

    def extract(self, result: SprintSchedulerResult) -> np.ndarray:
        """
        RL F257: Extract state from SprintSchedulerResult.

        This is the primary interface for sprint-based RL.

        Args:
            result: SprintSchedulerResult from completed sprint

        Returns:
            np.ndarray: 27-dim observation vector
        """
        self._sprint_count += 1

        for lane in _KNOWN_LANES:
            self._sprints_since_lane[lane] += 1
            if hasattr(result, "lanes") and result.lanes:
                if lane in result.lanes:
                    self._sprints_since_lane[lane] = 0

        # Base features
        base = np.zeros(12, dtype=np.float32)
        base[0] = min(getattr(result, "findings_accepted", 0) / 50.0, 1.0)
        base[1] = min(getattr(result, "runtime_seconds", 0) / 3600.0, 1.0)
        base[2] = min(getattr(result, "cycles_completed", 0) / 50.0, 1.0)

        total = getattr(result, "findings_accepted", 0) + getattr(result, "findings_rejected", 1)
        base[3] = getattr(result, "findings_accepted", 0) / max(total, 1)
        base[4] = min(getattr(result, "new_iocs", 0) / 100.0, 1.0)
        base[5] = getattr(result, "source_quality_avg", 0.5)
        base[6] = min(getattr(result, "queue_size", 0) / 200.0, 1.0)
        base[7] = getattr(result, "memory_pressure_norm", 0.5)
        base[8] = getattr(result, "graph_entropy_norm", 0.5)
        base[9] = min(getattr(result, "time_since_last_finding", 0) / 300.0, 1.0)
        base[10] = min(getattr(result, "resource_concurrency", 1) / 8.0, 1.0)

        # Reward = acceptance ratio * throughput
        reward = base[3] * base[1]
        self._reward_ema = self._ema_alpha * reward + (1 - self._ema_alpha) * self._reward_ema
        base[11] = self._reward_ema

        # Lane yield vector
        lane_yield = np.zeros(5, dtype=np.float32)
        if hasattr(result, "lanes") and result.lanes:
            for i, lane in enumerate(_KNOWN_LANES):
                if lane in result.lanes:
                    lane_data = result.lanes[lane]
                    duration = max(getattr(lane_data, "duration_s", 0.001), 0.001)
                    lane_yield[i] = min(getattr(lane_data, "findings_accepted", 0) / duration, 10.0)

        # Lane quality vector
        lane_quality = np.zeros(5, dtype=np.float32)
        if hasattr(result, "lanes") and result.lanes:
            for i, lane in enumerate(_KNOWN_LANES):
                if lane in result.lanes:
                    lane_data = result.lanes[lane]
                    accepted = getattr(lane_data, "findings_accepted", 0)
                    rejected = getattr(lane_data, "findings_rejected", 0)
                    total = accepted + rejected
                    lane_quality[i] = accepted / max(total, 1)

        # Lane recency vector
        lane_recency = np.zeros(5, dtype=np.float32)
        for i, lane in enumerate(_KNOWN_LANES):
            lane_recency[i] = min(self._sprints_since_lane[lane] / 20.0, 1.0)

        return np.concatenate([base, lane_yield, lane_quality, lane_recency])

    def extract_from_dicts(
        self,
        thread_state: dict,
        global_state: dict,
    ) -> np.ndarray:
        """
        Legacy dict-based state extraction.

        Args:
            thread_state: Per-thread state dict
            global_state: Global state dict

        Returns:
            np.ndarray: 27-dim observation vector
        """
        return self.extract(self._result_from_dicts(thread_state, global_state))

    def _result_from_dicts(self, thread_state: dict, global_state: dict) -> SprintSchedulerResult:
        """Convert dicts to SprintSchedulerResult-like object."""

        class _Result:
            pass

        r = _Result()
        r.findings_accepted = thread_state.get("findings_accepted", 0)
        r.findings_rejected = thread_state.get("findings_rejected", 0)
        r.runtime_seconds = thread_state.get("runtime_seconds", 0)
        r.cycles_completed = thread_state.get("cycles_completed", 0)
        r.new_iocs = thread_state.get("new_iocs", 0)
        r.source_quality_avg = thread_state.get("source_quality_avg", 0.5)
        r.queue_size = thread_state.get("queue_size", 0)
        r.memory_pressure_norm = thread_state.get("memory_pressure_norm", 0.5)
        r.graph_entropy_norm = thread_state.get("graph_entropy_norm", 0.5)
        r.time_since_last_finding = thread_state.get("time_since_last_finding", 0)
        r.resource_concurrency = thread_state.get("resource_concurrency", 1)
        r.lanes = thread_state.get("lanes", {})
        return r

    def reset(self) -> None:
        """Reset internal state (call between episodes)."""
        self._reward_ema = 0.0
        self._sprint_count = 0
        self._sprints_since_lane = dict.fromkeys(_KNOWN_LANES, 0)
