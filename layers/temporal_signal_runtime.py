"""
TemporalSignalRuntime — lazy runtime holder for TemporalSignalLayer.

Provides:
- get_temporal_signal_layer()  — lazily create/destroy TemporalSignalLayer
- reset_temporal_signal_layer() — reset for new run (called by pipeline teardown)
- get_temporal_signal_summary(k) — bounded top-K summary
- is_temporal_store_enabled() — check HLEDAC_ENABLE_TEMPORAL_STORE env flag
- get_temporal_signal_store() — lazy singleton store (None if env disabled)
- load_temporal_signal_snapshot() — restore layer from store if enabled
- save_temporal_signal_snapshot() — save layer to store if enabled
- close_temporal_signal_store() — checkpoint and close store

No heavy imports in hot-path. No global unbounded state.
"""
from __future__ import annotations

import os
import time
from typing import Any

# Lazy import only — no module-level heavy deps
_layer: "TemporalSignalLayer | None" = None
_reset_ts: float = 0.0
_store: "TemporalSignalStore | None" = None
_store_enabled: bool | None = None

DEFAULT_MAX_KEYS = 4096


def is_temporal_store_enabled() -> bool:
    """Return True when HLEDAC_ENABLE_TEMPORAL_STORE=1 is set."""
    global _store_enabled
    if _store_enabled is None:
        _store_enabled = os.environ.get("HLEDAC_ENABLE_TEMPORAL_STORE", "0") == "1"
    return _store_enabled


def get_temporal_signal_store() -> "TemporalSignalStore | None":
    """
    Lazily create (or return) the module-level TemporalSignalStore singleton.

    Returns None if HLEDAC_ENABLE_TEMPORAL_STORE is not set.
    The store is initialized once and reused across calls.
    """
    global _store
    if not is_temporal_store_enabled():
        return None
    if _store is None:
        from hledac.universal.layers.temporal_signal_store import TemporalSignalStore
        _store = TemporalSignalStore()
        _store.initialize()
    return _store


def load_temporal_signal_snapshot() -> bool:
    """
    Attempt to restore TemporalSignalLayer from a persisted store snapshot.

    Returns True if a snapshot was found and restored, False otherwise.
    When store is disabled or load fails, this is a no-op — the layer will
    start fresh (per-run behavior unchanged).
    """
    global _layer
    store = get_temporal_signal_store()
    if store is None:
        return False
    snapshot = store.load_snapshot()
    if snapshot is None:
        return False
    try:
        from hledac.universal.layers.temporal_signal_layer import TemporalSignalLayer
        if _layer is None:
            _layer = TemporalSignalLayer.from_snapshot(snapshot)
        else:
            # Layer already created — build a new one from snapshot and merge state
            restored = TemporalSignalLayer.from_snapshot(snapshot)
            # Transfer states from restored into existing layer
            for key, state in restored._states.items():
                if key not in _layer._states:
                    _layer._states[key] = state
                    _layer._lru_order.append(key)
            # Merge edge candidates (deduplicated by src_dst pair)
            existing_edges = {
                (c.src_key, c.dst_key, c.edge_type) for c in _layer._edge_candidates
            }
            for candidate in restored._edge_candidates:
                key = (candidate.src_key, candidate.dst_key, candidate.edge_type)
                if key not in existing_edges:
                    _layer._edge_candidates.append(candidate)
                    existing_edges.add(key)
            # Sync window merge
            existing_sync = set(_layer._sync_window)
            for item in restored._sync_window:
                if item not in existing_sync:
                    _layer._sync_window.append(item)
                    existing_sync.add(item)
        return True
    except Exception:
        return False


def save_temporal_signal_snapshot() -> bool:
    """
    Save the current TemporalSignalLayer state to the store.

    Returns True if saved successfully, False otherwise.
    Fails silently — store errors never propagate.
    """
    global _layer
    store = get_temporal_signal_store()
    if store is None or _layer is None:
        return False
    try:
        snapshot = _layer.snapshot()
        store.save_snapshot(snapshot)
        return True
    except Exception:
        return False


def close_temporal_signal_store() -> None:
    """Close the store and checkpoint WAL. Safe to call even if store was never opened."""
    global _store
    if _store is not None:
        try:
            _store.close()
        except Exception:
            pass
        finally:
            _store = None


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


def _clamp_hint(value: float) -> float:
    """Clamp value to [0.0, 1.0]."""
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def build_temporal_priority_hints(k: int = 10) -> list[dict]:
    """
    Advisory temporal priority hints — bounded top-K priority-ranked hints.

    Each hint is a dict with advisory_only=True so consumers know this is
    guidance, not a directive. No scheduler mutation, no graph write,
    no storage schema change.

    Priority formula (Sprint F206R):
        priority_hint = clamp(
            anomaly_score * 0.45
            + burst_score * 0.20
            + change_point_score * 0.20
            + source_synchrony_score * 0.15,
            0, 1
        )

    Reasons are derived from component scores:
        - burst_cluster     : burst_score > 0.6
        - periodic_checkin : periodicity_score > 0.6
        - dormant_wakeup   : change_point_score > 0.6
        - source_synchrony  : source_synchrony_score > 0.5
        - rate_spike       : anomaly_score > 0.7 (and no other reason)

    Returns [] if no layer initialized or on any error (fail-soft).
    Deterministic stable sort by priority_hint descending.
    """
    if _layer is None:
        return []
    try:
        top_scores = _layer.get_top_scores(k=min(k, 20))
        hints: list[dict] = []
        for s in top_scores:
            # Priority formula
            priority_hint = _clamp_hint(
                s.anomaly_score * 0.45
                + s.burst_score * 0.20
                + s.change_point_score * 0.20
                + s.source_synchrony_score * 0.15
            )

            # Derive reason tag (in priority order)
            # Note: periodicity_score influences the reason tag (periodic_checkin)
            # but does NOT contribute to priority_hint — per Sprint F206R spec.
            # priority_hint uses: anomaly*0.45 + burst*0.20 + change*0.20 + source*0.15
            reasons: list[str] = []
            if s.burst_score > 0.6:
                reasons.append("burst_cluster")
            if s.periodicity_score > 0.6:
                reasons.append("periodic_checkin")
            if s.change_point_score > 0.6:
                reasons.append("dormant_wakeup")
            if s.source_synchrony_score > 0.5:
                reasons.append("source_synchrony")
            if not reasons and s.anomaly_score > 0.7:
                reasons.append("rate_spike")
            if not reasons:
                reasons.append("normal")

            # Prefer most specific reason
            reason = reasons[0]

            hints.append({
                "key": s.key,
                "family": s.family,
                "priority_hint": priority_hint,
                "reason": reason,
                "anomaly_score": s.anomaly_score,
                "burst_score": s.burst_score,
                "periodicity_score": s.periodicity_score,
                "change_point_score": s.change_point_score,
                "source_synchrony_score": s.source_synchrony_score,
                "advisory_only": True,
            })

        # Stable sort by priority_hint descending (deterministic)
        hints.sort(key=lambda h: h["priority_hint"], reverse=True)
        return hints[:k]

    except Exception:
        # Fail-soft — temporal hints are advisory only
        return []
