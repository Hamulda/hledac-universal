"""
TemporalSignalRuntime — lazy runtime holder for TemporalSignalLayer.

Provides:
- get_temporal_signal_layer()  — lazily create/destroy TemporalSignalLayer
- reset_temporal_signal_layer() — reset for new run (called by pipeline teardown)
- get_temporal_signal_summary(k) — bounded top-K summary

No heavy imports in hot-path. No global unbounded state.
"""
from __future__ import annotations

import time
from typing import Any

# Lazy import only — no module-level heavy deps
_layer: "TemporalSignalLayer | None" = None
_reset_ts: float = 0.0

DEFAULT_MAX_KEYS = 4096


def get_temporal_signal_layer(max_keys: int = DEFAULT_MAX_KEYS) -> "TemporalSignalLayer":
    """
    Lazily get (or create) the TemporalSignalLayer singleton for this run.

    Returns the existing layer if already created this session.
    """
    global _layer
    if _layer is None:
        from hledac.universal.layers.temporal_signal_layer import TemporalSignalLayer
        _layer = TemporalSignalLayer(max_keys=max_keys)
    return _layer


def reset_temporal_signal_layer() -> None:
    """Reset the layer — call at run teardown for fresh state per-sprint."""
    global _layer, _reset_ts
    if _layer is not None:
        _layer.reset()
    _reset_ts = time.time()


def get_temporal_signal_summary(k: int = 20) -> dict[str, Any]:
    """
    Bounded top-K summary of the current temporal signal state.

    Returns {} if no layer has been initialized yet.
    """
    if _layer is None:
        return {}
    try:
        top_scores = _layer.get_top_scores(k=min(k, 20))
        edge_candidates = _layer.get_edge_candidates(k=min(k, 10))
        return {
            "observed_events": sum(s.event_count for s in _layer._states.values()),  # type: ignore[attr-defined]
            "state_size": _layer.get_state_size(),
            "top_scores": [
                {
                    "key": s.key,
                    "family": s.family,
                    "anomaly_score": s.anomaly_score,
                    "burst_score": s.burst_score,
                    "periodicity_score": s.periodicity_score,
                    "change_point_score": s.change_point_score,
                    "source_synchrony_score": s.source_synchrony_score,
                    "reason": s.reason,
                }
                for s in top_scores[:k]
            ],
            "edge_candidates": [
                {
                    "src_key": c.src_key,
                    "dst_key": c.dst_key,
                    "edge_type": c.edge_type,
                    "score": c.score,
                    "reason": c.reason,
                }
                for c in edge_candidates[:k]
            ],
        }
    except Exception:
        # Fail-soft — temporal scoring is advisory only
        return {}
