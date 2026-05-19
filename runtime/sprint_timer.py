# runtime/sprint_timer.py
# Sprint F238E Phase A: Minimal fail-soft timer infrastructure for sprint runtime instrumentation.
#
# Scope (first commit):
#   - 3 phases: prelude, graph_accum, export
#   - time.monotonic() only — no time.time()
#   - fail-soft: metrics error must NEVER alter sprint behavior
#   - no file I/O, no network, no model load in hot path
#   - bounded events list (maxlen=500)
#   - small dict payloads only
#
# Forbidden in first commit:
#   - _await_coro() instrumentation
#   - per-lane measurement
#   - event-loop-lag sampling
#   - psutil RSS sampling

from __future__ import annotations

import contextlib
import time
from collections import deque
from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:
    pass


# Timer event labels for the 3 instrumented phases
_PHASE_PRELUDE_START = "prelude_start"
_PHASE_PRELUDE_END = "prelude_end"
_PHASE_GRAPH_ACCUM_START = "graph_accum_start"
_PHASE_GRAPH_ACCUM_END = "graph_accum_end"
_PHASE_EXPORT_START = "export_start"
_PHASE_EXPORT_END = "export_end"

# Bounded events list max length
_MAX_TIMER_EVENTS: int = 500

# Sprint F240A: Canonical phase labels for lane timing extraction
_CANONICAL_PHASES: tuple[str, ...] = (
    "memory_preflight",
    "profile_reality_check",
    "acquisition_plan_build",
    "mandatory_prelude",
    "runtime_pivot_seed_extraction",
    "planner_actions_consumption",
    "nonfeed_prelude_gather",
    "public_lane",
    "ct_lane",
    "doh_lane",
    "wayback_lane",
    "passive_dns_lane",
    "graph_accumulation",
    "pivot_planning",
    "export",
    "investigation_packet_build",
    "next_sprint_seeds_generation",
)


class SprintTimer:
    """
    Fail-soft wall-time timer with structured output.

    All methods guard with try/except — metrics collection must never alter
    sprint behavior or raise exceptions into the caller's execution path.

    Events are stored as dicts: {"label": str, "elapsed_s": float, "metadata": dict}
    """

    __slots__ = ("_events", "_emit_fn")

    def __init__(
        self,
        emit_fn: Optional[Callable[[str, float, dict[str, Any]], None]] = None,
        *,
        maxlen: int = _MAX_TIMER_EVENTS,
    ) -> None:
        self._events: deque[dict[str, Any]] = deque(maxlen=maxlen)
        self._emit_fn = emit_fn  # (label, elapsed_s, metadata) -> None

    # ── context manager ───────────────────────────────────────────────────────

    @contextlib.contextmanager
    def phase(self, label: str, **metadata: Any) -> Any:
        """
        Context manager for timing a named phase.

        Usage:
            with timer.phase("prelude_start"):
                ...

        Records (label + "_end", elapsed_s, metadata) on exit.
        Fail-soft: any exception in emit is swallowed.
        """
        t0 = time.monotonic()
        try:
            yield
        finally:
            t1 = time.monotonic()
            end_label = label if label.endswith("_end") else f"{label}_end"
            elapsed = t1 - t0
            event: dict[str, Any] = {
                "label": end_label,
                "elapsed_s": elapsed,
                "metadata": dict(metadata),
            }
            self._events.append(event)
            if self._emit_fn is not None:
                try:
                    self._emit_fn(end_label, elapsed, metadata)
                except Exception:
                    pass  # fail-soft: never propagate

    # ── gauge ──────────────────────────────────────────────────────────────────

    def gauge(self, label: str, val: float, **extra: Any) -> None:
        """
        Record a point-in-time metric (gauge).

        Fail-soft: any exception is swallowed.
        """
        event: dict[str, Any] = {
            "label": label,
            "elapsed_s": 0.0,
            "metadata": {"val": val, "type": "gauge", **(extra or {})},
        }
        self._events.append(event)
        if self._emit_fn is not None:
            try:
                self._emit_fn(label, 0.0, {"val": val, "type": "gauge", **(extra or {})})
            except Exception:
                pass  # fail-soft: never propagate

    # ── event record ──────────────────────────────────────────────────────────

    def record(self, label: str, elapsed_s: float = 0.0, **metadata: Any) -> None:
        """
        Record a completed timer event with explicit elapsed time.

        Fail-soft: any exception is swallowed.
        """
        event: dict[str, Any] = {
            "label": label,
            "elapsed_s": elapsed_s,
            "metadata": dict(metadata),
        }
        self._events.append(event)
        if self._emit_fn is not None:
            try:
                self._emit_fn(label, elapsed_s, metadata)
            except Exception:
                pass  # fail-soft: never propagate

    # ── accessors ─────────────────────────────────────────────────────────────

    @property
    def events(self) -> list[dict[str, Any]]:
        """Return a list view of recorded events (newest first for display)."""
        return list(self._events)

    def clear(self) -> None:
        """Clear all recorded events."""
        self._events.clear()


# ── Sprint F240A: Runtime Loop Telemetry Helper ───────────────────────────────
# Fail-soft, no network/model imports, no raw evidence.

_MAX_TELEMETRY_EVENTS = 500
_PHASE_LABELS = (
    "memory_preflight",
    "profile_reality_check",
    "acquisition_plan_build",
    "mandatory_prelude",
    "runtime_pivot_seed_extraction",
    "planner_actions_consumption",
    "nonfeed_prelude_gather",
    "public_lane",
    "ct_lane",
    "doh_lane",
    "wayback_lane",
    "passive_dns_lane",
    "graph_accumulation",
    "pivot_planning",
    "export",
    "investigation_packet_build",
    "next_sprint_seeds_generation",
)


def compute_runtime_loop_telemetry(
    timer_events: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """
    Sprint F240A: Compute runtime_loop_telemetry from timer events.

    Returns fail-soft dict:
      {
        "events": [...max 500],
        "phase_totals_s": {...},
        "slowest_phases": [...top 10],
        "lane_timings": {...},
        "timer_event_count": n,
      }

    No raw evidence, no network/model imports, no exceptions propagated.
    """
    try:
        if not timer_events:
            return {
                "events": [],
                "phase_totals_s": {},
                "slowest_phases": [],
                "lane_timings": {},
                "timer_event_count": 0,
            }

        # Bound events to max 500
        events = timer_events[-_MAX_TELEMETRY_EVENTS:] if len(timer_events) > _MAX_TELEMETRY_EVENTS else timer_events

        # ── phase_totals_s ─────────────────────────────────────────────────
        phase_totals: dict[str, float] = {}
        for e in events:
            label = e.get("label", "")
            if not label:
                continue
            # Strip _end suffix for base phase name
            base = label[:-4] if label.endswith("_end") else label
            # Normalize to canonical phase name
            base = _normalize_phase(base)
            elapsed = e.get("elapsed_s", 0.0) or 0.0
            phase_totals[base] = phase_totals.get(base, 0.0) + elapsed

        # ── slowest_phases (top 10 by elapsed, desc) ───────────────────────
        slowest = sorted(
            [{"phase": k, "elapsed_s": round(v, 4)} for k, v in phase_totals.items()],
            key=lambda x: x["elapsed_s"],
            reverse=True,
        )[:10]

        # ── lane_timings ───────────────────────────────────────────────────
        # Pattern: {lane}_start → {lane}_end pairs
        lane_timings: dict[str, dict[str, float]] = {}
        open_lanes: dict[str, float] = {}  # lane → start time
        for e in events:
            label = e.get("label", "")
            if not label or "_start" not in label and "_end" not in label:
                continue
            for candidate in _PHASE_LABELS:
                if label == f"{candidate}_start":
                    open_lanes[candidate] = e.get("elapsed_s", 0.0) or 0.0
                elif label == f"{candidate}_end":
                    start = open_lanes.pop(candidate, None)
                    elapsed = e.get("elapsed_s", 0.0) or 0.0
                    if start is not None:
                        duration = max(0.0, elapsed - start)
                    else:
                        duration = elapsed
                    lane_entry: dict[str, Any] = {
                        "elapsed_s": round(duration, 4),
                        "start_event": f"{candidate}_start",
                        "end_event": f"{candidate}_end",
                    }
                    lane_timings[candidate] = lane_entry

        return {
            "events": events,
            "phase_totals_s": {k: round(v, 4) for k, v in phase_totals.items()},
            "slowest_phases": slowest,
            "lane_timings": lane_timings,
            "timer_event_count": len(events),
        }

    except Exception:
        return {
            "events": [],
            "phase_totals_s": {},
            "slowest_phases": [],
            "lane_timings": {},
            "timer_event_count": 0,
        }


def _normalize_phase(label: str) -> str:
    """Map timer phase label to canonical phase name."""
    label = label.lower().strip()
    mapping = {
        "prelude": "mandatory_prelude",
        "prelude_start": "mandatory_prelude",
        "prelude_end": "mandatory_prelude",
        "graph_accum": "graph_accumulation",
        "graph_accum_start": "graph_accumulation",
        "graph_accum_end": "graph_accumulation",
        "export": "export",
        "export_start": "export",
        "export_end": "export",
    }
    if label in mapping:
        return mapping[label]
    # Strip _start suffix for canonical base name
    if label.endswith("_start"):
        return label[:-6]
    return label