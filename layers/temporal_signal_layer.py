"""
Temporal Signal Layer — OSINT temporal intelligence without neuromorphic crypto.

Bounded event-driven layer for:
- burst detection
- periodicity / check-in scoring
- change-point detection (Page-Hinkley + BOCPD-lite)
- source synchrony (Jaccard sliding window)
- temporal edge candidates
- feedback loop from confirmations

Design: cascade L0 cheap deterministic → L1 bounded temporal scoring.
No numpy, no pandas, no mlx, no model loading. Pure Python. M1 8GB safe.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterable

# No numpy, no pandas, no mlx — pure Python only

DEFAULT_MAX_KEYS = 4096
DEFAULT_RING_SIZE = 32
DEFAULT_HALF_LIFE_S = 900.0
DEFAULT_SYNCHRONY_WINDOW_S = 300.0
DEFAULT_BOCPD_MAX_RUN = 32

CONFIRMATION_BOOST_MAX = 1.5
CONFIRMATION_BOOST_MIN = 0.5
CONFIRMATION_DECAY = 0.05
CONFIRMATION_GROWTH = 0.1


@dataclass(frozen=True, slots=True)
class TemporalEvent:
    ts: float
    key: str
    family: str = "generic"
    source: str = ""
    weight: float = 1.0
    labels: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TemporalScore:
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


@dataclass(frozen=True, slots=True)
class TemporalEdgeCandidate:
    src_key: str
    dst_key: str
    edge_type: str
    score: float
    window_start: float
    window_end: float
    reason: str


@dataclass
class _KeyState:
    last_ts: float = 0.0
    event_count: int = 0
    ewma_rate: float = 0.0
    ewma_gap: float = 0.0
    gap_variance: float = 0.0
    ring_gaps: deque[float] = field(default_factory=lambda: deque(maxlen=DEFAULT_RING_SIZE))  # type: ignore[type-arg]
    ring_sources: deque[str] = field(default_factory=lambda: deque(maxlen=DEFAULT_RING_SIZE))  # type: ignore[type-arg]
    last_score: TemporalScore | None = None
    confirmation_weight: float = 1.0
    last_updated: float = 0.0
    # Page-Hinkley state
    ph_cumsum: float = 0.0
    ph_mean: float = 0.0
    # BOCPD-lite
    bocpd_run_length: int = 0
    bocpd_log_odds: float = 0.0


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b != 0.0 and not math.isnan(b) else default


def _clamp(value: float, lo: float, hi: float) -> float:
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


def _compute_cv(gaps: list[float]) -> float:
    if len(gaps) < 2:
        return 0.0
    mean_g = sum(gaps) / len(gaps)
    if mean_g <= 0:
        return 0.0
    variance = sum((g - mean_g) ** 2 for g in gaps) / len(gaps)
    std_g = math.sqrt(variance)
    return _safe_div(std_g, mean_g)


def _compute_autocorr_lag1(gaps: list[float]) -> float:
    n = len(gaps)
    if n < 4:
        return 0.0
    mean_g = sum(gaps) / n
    if mean_g <= 0:
        return 0.0
    var_g = sum((g - mean_g) ** 2 for g in gaps)
    if var_g == 0.0:
        return 0.0
    cov = sum(gaps[i] * gaps[i + 1] for i in range(n - 1)) / (n - 1) - mean_g * mean_g
    return _safe_div(cov, var_g)


# Source synchrony window — bounded per window
_SYNCHRONY_WINDOW_S = DEFAULT_SYNCHRONY_WINDOW_S
_MAX_SOURCES_PER_WINDOW = 64
_MAX_KEYS_PER_SOURCE_SET = 128


class TemporalSignalLayer:
    """
    Bounded temporal signal scoring layer.

    Observe events → receive TemporalScore per key.
    Observe many events → list of TemporalScore.
    Feedback: observe_confirmation(key, confirmed, source) adjusts future weights.
    Snapshot/replay for persistence.
    """

    def __init__(
        self,
        max_keys: int = DEFAULT_MAX_KEYS,
        ring_size: int = DEFAULT_RING_SIZE,
        half_life_s: float = DEFAULT_HALF_LIFE_S,
        synchrony_window_s: float = DEFAULT_SYNCHRONY_WINDOW_S,
        bocpd_max_run: int = DEFAULT_BOCPD_MAX_RUN,
    ):
        self._max_keys = max_keys
        self._ring_size = ring_size
        self._half_life_s = half_life_s
        self._synchrony_window_s = synchrony_window_s
        self._bocpd_max_run = bocpd_max_run

        self._states: dict[str, _KeyState] = {}
        self._lru_order: list[str] = []  # simple list for LRU eviction
        self._edge_candidates: deque[TemporalEdgeCandidate] = deque(maxlen=256)

        # Source synchrony window — sliding window of source → set(keys)
        self._sync_window: deque[tuple[float, str, frozenset[str]]] = deque(maxlen=256)

    # ─── Event observation ───────────────────────────────────────────────────

    def observe(self, event: TemporalEvent) -> TemporalScore:
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

        # Move to end of LRU order (most recent)
        if key in self._lru_order:
            self._lru_order.remove(key)
        self._lru_order.append(key)

        family = event.family
        weight = event.weight * state.confirmation_weight

        event_count = state.event_count + 1
        reason = ""

        # ── Gap metrics ──────────────────────────────────────────────────────
        gap_s = 0.0
        if event_count > 1:
            gap_s = ts - state.last_ts
            if gap_s > 0:
                state.ring_gaps.append(gap_s)
                state.ring_sources.append(event.source)

        # EWMA gap / rate
        alpha = 0.5
        if state.event_count == 0:
            state.ewma_gap = gap_s if gap_s > 0 else 0.1
            state.ewma_rate = 1.0 / state.ewma_gap if state.ewma_gap > 0 else 1.0
        else:
            prev_gap = state.ewma_gap
            if gap_s > 0:
                state.ewma_gap = alpha * gap_s + (1 - alpha) * prev_gap
                state.ewma_rate = 1.0 / state.ewma_gap if state.ewma_gap > 0 else 1.0

        # ── Burst score ──────────────────────────────────────────────────────
        burst_score = 0.0
        if gap_s > 0 and state.ewma_gap > 0:
            gap_ratio = state.ewma_gap / gap_s
            burst_score = _clamp((gap_ratio - 1.0) / (gap_ratio + 1.0), 0.0, 1.0)

        # Also score bursty if many events in short window
        synchrony_window = self._synchrony_window_s
        recent_count = sum(
            1
            for w_ts, _, _ in self._sync_window
            if ts - synchrony_window <= w_ts <= ts
        )
        if recent_count >= 3:
            burst_score = max(burst_score, _clamp(recent_count / 10.0, 0.0, 1.0))

        # ── Periodicity score ────────────────────────────────────────────────
        periodicity_score = 0.0
        cv_isi = 0.0
        autocorr_lag1 = 0.0
        mean_gap_s = state.ewma_gap

        if len(state.ring_gaps) >= 4:
            gaps_list = list(state.ring_gaps)
            cv_isi = _compute_cv(gaps_list)
            autocorr_lag1 = _compute_autocorr_lag1(gaps_list)

            # Periodicity: low CV (stable gaps) + high autocorr
            # CV-like jitter tolerance: score stays high unless CV is very high
            # Perfect regularity: CV≈0 → periodicity=1.0 directly
            if cv_isi < 0.01:
                periodicity_score = 1.0
            else:
                cv_penalty = _clamp(cv_isi / 2.0, 0.0, 1.0)
                # Add baseline so perfect regularity scores 1.0 not 0.5
                periodicity_score = (1.0 - cv_penalty) * 0.5 + (0.5 + autocorr_lag1 * 0.5)
                periodicity_score = _clamp(periodicity_score, 0.0, 1.0)
            mean_gap_s = sum(gaps_list) / len(gaps_list)

        # ── Change-point score (Page-Hinkley + BOCPD-lite) ───────────────────
        change_point_score = 0.0

        if event_count >= 2 and gap_s > 0:
            # Page-Hinkley drift detector
            ph_alpha = 0.01  # drift threshold
            if state.ph_mean == 0.0:
                state.ph_mean = gap_s
            else:
                delta = gap_s - state.ph_mean
                state.ph_mean = 0.5 * delta + state.ph_mean
                state.ph_cumsum = state.ph_cumsum + delta - ph_alpha
                # Normalized CUSUM
                if state.ph_mean > 0:
                    state.ph_cumsum = max(0.0, state.ph_cumsum + delta - ph_alpha)
                else:
                    state.ph_cumsum = min(0.0, state.ph_cumsum + delta + ph_alpha)

            # Normalize to [0, 1]
            change_point_score = _clamp(abs(state.ph_cumsum) / 100.0, 0.0, 1.0)

            # BOCPD-lite: bounded run-length approximation
            # log-odds of change vs no-change, bounded per-event O(bocpd_max_run)
            if state.bocpd_run_length < self._bocpd_max_run:
                # Pre-change: run length increases
                state.bocpd_run_length += 1
                # Log-odds update: increase probability of change as run grows
                state.bocpd_log_odds = math.log(state.bocpd_run_length + 1)
            else:
                # Restart detection — burst of length bocpd_max_run = likely change
                state.bocpd_run_length = 0
                state.bocpd_log_odds = 5.0  # strong change signal

            # Combine PH + BOCPD
            bocpd_score = _safe_div(state.bocpd_log_odds, 10.0)
            change_point_score = max(change_point_score, _clamp(bocpd_score, 0.0, 1.0))

        # ── Source synchrony score ───────────────────────────────────────────
        source_synchrony_score = 0.0
        self._sync_window.append((ts, event.source, frozenset([key])))
        # Jaccard over source sets in synchrony window
        if len(self._sync_window) >= 2:
            window_start = ts - self._synchrony_window_s
            active_sources: dict[str, set[str]] = {}
            for w_ts, src, keys in self._sync_window:
                if w_ts >= window_start:
                    if src not in active_sources:
                        active_sources[src] = set()
                    active_sources[src].update(keys)
                    if len(active_sources[src]) > _MAX_KEYS_PER_SOURCE_SET:
                        active_sources[src] = set(list(active_sources[src])[: _MAX_KEYS_PER_SOURCE_SET])

            if len(active_sources) >= 2:
                sources = list(active_sources.keys())
                jaccard_scores = []
                for i in range(min(len(sources), 8)):
                    for j in range(i + 1, min(len(sources), 8)):
                        set_i = active_sources[sources[i]]
                        set_j = active_sources[sources[j]]
                        if set_i and set_j:
                            inter = len(set_i & set_j)
                            union = len(set_i | set_j)
                            jaccard_scores.append(_safe_div(inter, union))

                if jaccard_scores:
                    source_synchrony_score = sum(jaccard_scores) / len(jaccard_scores)

        # ── Rate score ─────────────────────────────────────────────────────
        rate_score = 0.0
        if state.last_ts > 0 and state.ewma_rate > 0:
            current_rate = 1.0 / gap_s if gap_s > 0 else 0.0
            rate_ratio = _safe_div(current_rate, state.ewma_rate)
            rate_score = _clamp((rate_ratio - 0.5) / 2.0, 0.0, 1.0)

        # ── Anomaly score ───────────────────────────────────────────────────
        anomaly_score = (
            burst_score * 0.3
            + _safe_div(change_point_score, 3.0) * 0.3
            + (1.0 - periodicity_score) * 0.2
            + rate_score * 0.2
        )
        anomaly_score = _clamp(anomaly_score, 0.0, 1.0)

        # ── Reason string ───────────────────────────────────────────────────
        if event_count < 2:
            reason = "insufficient_history"
        else:
            reasons = []
            if burst_score > 0.6:
                reasons.append("burst")
            if periodicity_score > 0.6:
                reasons.append("periodic")
            if change_point_score > 0.6:
                reasons.append("change_point")
            if source_synchrony_score > 0.5:
                reasons.append("source_synchrony")
            if anomaly_score > 0.7:
                reasons.append("anomaly")
            if not reasons:
                reasons.append("normal")
            reason = "|".join(reasons)

        # Update state
        state.last_ts = ts
        state.event_count = event_count
        state.last_score = TemporalScore(
            key=key,
            family=family,
            event_count=event_count,
            anomaly_score=anomaly_score,
            burst_score=burst_score,
            periodicity_score=periodicity_score,
            change_point_score=change_point_score,
            source_synchrony_score=source_synchrony_score,
            rate_score=rate_score,
            cv_isi=cv_isi,
            mean_gap_s=mean_gap_s,
            autocorr_lag1=autocorr_lag1,
            reason=reason,
        )

        # Generate temporal edge candidates
        self._update_edge_candidates(state, ts, burst_score, source_synchrony_score)

        return state.last_score

    def observe_many(self, events: Iterable[TemporalEvent]) -> list[TemporalScore]:
        results = []
        for event in events:
            results.append(self.observe(event))
        return results

    def observe_confirmation(self, key: str, confirmed: bool, source: str = "") -> None:
        """Feedback loop — confirmed=True boosts weight, confirmed=False decays."""
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

    # ─── Top scores ─────────────────────────────────────────────────────────

    def get_top_scores(self, k: int = 20) -> list[TemporalScore]:
        all_scores = [s for s in self._states.values() if s.last_score is not None]
        all_scores.sort(key=lambda s: s.last_score.anomaly_score, reverse=True)
        return [s.last_score for s in all_scores[:k]]

    def get_edge_candidates(self, k: int = 50) -> list[TemporalEdgeCandidate]:
        sorted_candidates = sorted(self._edge_candidates, key=lambda c: c.score, reverse=True)
        return sorted_candidates[:k]

    # ─── Snapshot / replay ─────────────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        states_serializable = {}
        for k, v in self._states.items():
            states_serializable[k] = {
                "last_ts": v.last_ts,
                "event_count": v.event_count,
                "ewma_rate": v.ewma_rate,
                "ewma_gap": v.ewma_gap,
                "gap_variance": v.gap_variance,
                "ring_gaps": list(v.ring_gaps),
                "ring_sources": list(v.ring_sources),
                "confirmation_weight": v.confirmation_weight,
                "last_updated": v.last_updated,
                "ph_cumsum": v.ph_cumsum,
                "ph_mean": v.ph_mean,
                "bocpd_run_length": v.bocpd_run_length,
                "bocpd_log_odds": v.bocpd_log_odds,
            }

        edge_candidates_serializable = [
            {
                "src_key": c.src_key,
                "dst_key": c.dst_key,
                "edge_type": c.edge_type,
                "score": c.score,
                "window_start": c.window_start,
                "window_end": c.window_end,
                "reason": c.reason,
            }
            for c in self._edge_candidates
        ]

        return {
            "max_keys": self._max_keys,
            "ring_size": self._ring_size,
            "half_life_s": self._half_life_s,
            "synchrony_window_s": self._synchrony_window_s,
            "bocpd_max_run": self._bocpd_max_run,
            "states": states_serializable,
            "lru_order": self._lru_order,
            "edge_candidates": edge_candidates_serializable,
            "sync_window": [
                (w_ts, src, tuple(keys))
                for w_ts, src, keys in self._sync_window
            ],
        }

    @classmethod
    def from_snapshot(cls, snapshot: dict[str, Any]) -> "TemporalSignalLayer":
        layer = cls(
            max_keys=snapshot.get("max_keys", DEFAULT_MAX_KEYS),
            ring_size=snapshot.get("ring_size", DEFAULT_RING_SIZE),
            half_life_s=snapshot.get("half_life_s", DEFAULT_HALF_LIFE_S),
            synchrony_window_s=snapshot.get("synchrony_window_s", DEFAULT_SYNCHRONY_WINDOW_S),
            bocpd_max_run=snapshot.get("bocpd_max_run", DEFAULT_BOCPD_MAX_RUN),
        )
        for key, state_data in snapshot.get("states", {}).items():
            state = _KeyState(
                last_ts=state_data["last_ts"],
                event_count=state_data["event_count"],
                ewma_rate=state_data["ewma_rate"],
                ewma_gap=state_data["ewma_gap"],
                gap_variance=state_data["gap_variance"],
                ring_gaps=deque(state_data["ring_gaps"], maxlen=layer._ring_size),
                ring_sources=deque(state_data["ring_sources"], maxlen=layer._ring_size),
                confirmation_weight=state_data["confirmation_weight"],
                last_updated=state_data["last_updated"],
                ph_cumsum=state_data.get("ph_cumsum", 0.0),
                ph_mean=state_data.get("ph_mean", 0.0),
                bocpd_run_length=state_data.get("bocpd_run_length", 0),
                bocpd_log_odds=state_data.get("bocpd_log_odds", 0.0),
            )
            # Rebuild last_score
            if state.event_count > 0:
                state.last_score = TemporalScore(
                    key=key,
                    family="",
                    event_count=state.event_count,
                    anomaly_score=0.0,
                    burst_score=0.0,
                    periodicity_score=0.0,
                    change_point_score=0.0,
                    source_synchrony_score=0.0,
                    rate_score=0.0,
                    cv_isi=0.0,
                    mean_gap_s=state.ewma_gap,
                    autocorr_lag1=0.0,
                    reason="restored",
                )
            layer._states[key] = state

        layer._lru_order = list(snapshot.get("lru_order", []))
        layer._edge_candidates = deque(
            [
                TemporalEdgeCandidate(
                    src_key=c["src_key"],
                    dst_key=c["dst_key"],
                    edge_type=c["edge_type"],
                    score=c["score"],
                    window_start=c["window_start"],
                    window_end=c["window_end"],
                    reason=c["reason"],
                )
                for c in snapshot.get("edge_candidates", [])
            ],
            maxlen=256,
        )
        layer._sync_window = deque(
            [(w_ts, src, frozenset(keys)) for w_ts, src, keys in snapshot.get("sync_window", [])],
            maxlen=256,
        )
        return layer

    def reset(self) -> None:
        self._states.clear()
        self._lru_order.clear()
        self._edge_candidates.clear()
        self._sync_window.clear()

    def get_state_size(self) -> int:
        return len(self._states)

    # ─── Internal helpers ───────────────────────────────────────────────────

    def _ensure_capacity(self) -> None:
        while len(self._states) >= self._max_keys and self._lru_order:
            oldest = self._lru_order.pop(0)
            self._states.pop(oldest, None)

    def _update_edge_candidates(
        self, state: _KeyState, ts: float, burst_score: float, source_synchrony_score: float
    ) -> None:
        # Generate co-burst temporal edge if high burst score
        if burst_score > 0.6:
            for other_key, other_state in self._states.items():
                if other_key == state.last_score.key if state.last_score else False:
                    continue
                if other_state.last_score and other_state.last_score.burst_score > 0.4:
                    # Co-burst in similar time window
                    window_start = ts - self._synchrony_window_s
                    candidate = TemporalEdgeCandidate(
                        src_key=state.last_score.key,
                        dst_key=other_key,
                        edge_type="co_burst",
                        score=(burst_score + other_state.last_score.burst_score) / 2.0,
                        window_start=window_start,
                        window_end=ts,
                        reason="co_burst",
                    )
                    self._edge_candidates.append(candidate)

        # Generate source-synchrony edges
        if source_synchrony_score > 0.4:
            recent_keys = set()
            window_start = ts - self._synchrony_window_s
            for w_ts, src, keys in self._sync_window:
                if w_ts >= window_start:
                    recent_keys |= keys
            if len(recent_keys) >= 2:
                recent_list = list(recent_keys)
                for i in range(min(len(recent_list), 4)):
                    for j in range(i + 1, min(len(recent_list), 4)):
                        candidate = TemporalEdgeCandidate(
                            src_key=recent_list[i],
                            dst_key=recent_list[j],
                            edge_type="source_synchrony",
                            score=source_synchrony_score,
                            window_start=window_start,
                            window_end=ts,
                            reason="source_synchrony",
                        )
                        self._edge_candidates.append(candidate)


# ─── Helper ─────────────────────────────────────────────────────────────────

def event_from_finding_like(obj: Any) -> TemporalEvent | None:
    """
    Fail-soft conversion from finding-like object/dict.

    Looks for fields: timestamp/ts/created_at, domain/url/key/entity,
    source_family/source/family, confidence/weight.
    Returns None on conversion error (no crash).
    """
    try:
        # Support both objects (getattr) and dicts (__getitem__)
        def get_field(name: str, default: Any = None) -> Any:
            if isinstance(obj, dict):
                return obj.get(name, default)
            return getattr(obj, name, default)

        ts = get_field("timestamp")
        if ts is None:
            ts = get_field("ts")
        if ts is None:
            ts = get_field("created_at")
        if ts is None:
            return None

        key = get_field("domain")
        if key is None:
            key = get_field("url")
        if key is None:
            key = get_field("key")
        if key is None:
            key = get_field("entity")
        if key is None:
            return None

        family = get_field("source_family")
        if family is None:
            family = get_field("source")
        if family is None:
            family = get_field("family", "generic")

        source = get_field("source", "") or ""

        weight = get_field("confidence")
        if weight is None:
            weight = get_field("weight", 1.0)
        if weight is None:
            weight = 1.0

        labels = get_field("labels")
        if labels is None:
            labels = ()
        elif not isinstance(labels, tuple):
            labels = tuple(labels) if labels else ()

        return TemporalEvent(
            ts=float(ts),
            key=str(key),
            family=str(family),
            source=str(source),
            weight=float(weight),
            labels=labels,
        )
    except (ValueError, TypeError, AttributeError, KeyError):
        return None
