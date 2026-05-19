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