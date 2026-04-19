"""
runtime/telemetry.py — Minimal runtime telemetry seam
======================================================

Role: SprintMetrics collector + fail-soft structured logging helper.
Authority: session-scoped phase/module/elapsed_ms events.
Boundary: stdlib only, no OTEL, no structlog, no Prometheus.

Fail-soft invariants:
  - TelemetryError never propagates to caller
  - TelemetryLogger methods are all void (return None)
  - Bounded event history (maxlen ring buffer)

Telemetry authority:
  - session_id, phase, component, event, elapsed_ms fields
  - phase transition snapshots from SprintLifecycleManager
  - component/module tagging for sprint-level attribution

NOT telemetry authority:
  - MetricsRegistry gauges/counters (those live in metrics_registry.py)
  - System memory/RSS metrics (metrics_registry.py owns those)
  - MLX/cache metrics (metrics_registry.py owns those)
"""

from __future__ import annotations

import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

# ── Dataclasses ────────────────────────────────────────────────────────────────

TELEMETRY_EVENT_FIELDS = frozenset([
    "session_id", "phase", "component", "event", "elapsed_ms"
])


@dataclass
class SprintEvent:
    """
    A single sprint telemetry event.

    All fields are primitives — no Path, no handles, no open resources.
    JSON-serializable for persistence safety.
    """
    session_id: str
    phase: str
    component: str
    event: str
    elapsed_ms: float
    ts: str = field(default="")

    def __post_init__(self) -> None:
        if not self.ts:
            self.ts = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "phase": self.phase,
            "component": self.component,
            "event": self.event,
            "elapsed_ms": self.elapsed_ms,
            "ts": self.ts,
        }


# ── JsonFormatter ─────────────────────────────────────────────────────────────

class JsonFormatter(logging.Formatter):
    """
    Stdlib logging JSON formatter — fail-soft, no external deps.

    Formats log records as JSON objects with consistent field names.
    Used by TelemetryLogger to emit structured log entries.
    """

    def format(self, record: logging.LogRecord) -> str:
        try:
            obj = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
            # Attach sprint context if present on the record
            for attr in ("session_id", "phase", "component", "event", "elapsed_ms"):
                if hasattr(record, attr):
                    obj[attr] = getattr(record, attr)
            return json.dumps(obj, separators=(",", ":"))
        except Exception:
            # Fail-soft: never let formatting errors propagate
            return json.dumps({
                "ts": datetime.now(timezone.utc).isoformat(),
                "level": "ERROR",
                "logger": record.name,
                "message": "TelemetryFormatter: format error",
            })


# ── TelemetryLogger ────────────────────────────────────────────────────────────

class TelemetryLogger:
    """
    Fail-soft structured logging wrapper over stdlib logging.

    Guarantees:
      - All methods return None (void semantics)
      - Errors are swallowed, never propagate to caller
      - Emits JSON-structured logs via JsonFormatter

    Usage:
      logger = TelemetryLogger(session_id="run-001")
      logger.log_phase_transition("BOOT", "WARMUP", "sprint_lifecycle", 0.0)
      logger.log_event("ACTIVE", "fetch", "url_discovered", 1500.0)
      logger.log_sprint_finalize("TEARDOWN", "sprint_lifecycle", 1800000.0)
    """

    MAX_EVENT_HISTORY = 100  # Bounded ring buffer

    def __init__(
        self,
        session_id: str,
        component: str = "runtime",
        logger_name: str = "hledac.telemetry",
    ) -> None:
        self._session_id = session_id
        self._component = component
        self._logger = logging.getLogger(logger_name)
        self._events: deque[SprintEvent] = deque(maxlen=self.MAX_EVENT_HISTORY)
        # Ensure JSON formatter (idempotent — handler may exist already)
        self._ensure_json_handler()

    def _ensure_json_handler(self) -> None:
        """Add JsonFormatter handler if none exists on this logger."""
        if self._logger.handlers:
            return
        try:
            h = logging.StreamHandler()
            h.setFormatter(JsonFormatter())
            self._logger.addHandler(h)
            self._logger.setLevel(logging.INFO)
        except Exception:
            # Fail-soft: logging setup error is silent
            pass

    # ── Public void API ─────────────────────────────────────────────────────

    def log_phase_transition(
        self,
        from_phase: str,
        to_phase: str,
        component: Optional[str] = None,
        elapsed_ms: float = 0.0,
    ) -> None:
        """Record a sprint phase transition."""
        try:
            comp = component or self._component
            evt = SprintEvent(
                session_id=self._session_id,
                phase=to_phase,
                component=comp,
                event=f"phase:{from_phase}->{to_phase}",
                elapsed_ms=elapsed_ms,
            )
            self._events.append(evt)
            self._emit_log_record(evt)
        except Exception:
            pass

    def log_event(
        self,
        phase: str,
        component: str,
        event: str,
        elapsed_ms: float = 0.0,
    ) -> None:
        """Record a named sprint event within a phase."""
        try:
            evt = SprintEvent(
                session_id=self._session_id,
                phase=phase,
                component=component,
                event=event,
                elapsed_ms=elapsed_ms,
            )
            self._events.append(evt)
            self._emit_log_record(evt)
        except Exception:
            pass

    def log_sprint_finalize(
        self,
        final_phase: str,
        component: Optional[str] = None,
        total_elapsed_ms: float = 0.0,
    ) -> None:
        """Record sprint finalization event."""
        try:
            comp = component or self._component
            evt = SprintEvent(
                session_id=self._session_id,
                phase=final_phase,
                component=comp,
                event="sprint_finalize",
                elapsed_ms=total_elapsed_ms,
            )
            self._events.append(evt)
            self._emit_log_record(evt)
        except Exception:
            pass

    def get_events(self) -> list[dict]:
        """Return a list of event dicts from the ring buffer."""
        try:
            return [e.to_dict() for e in self._events]
        except Exception:
            return []

    def _emit_log_record(self, evt: SprintEvent) -> None:
        """Emit a structured log record with sprint context."""
        try:
            record = logging.LogRecord(
                name=self._logger.name,
                level=logging.INFO,
                pathname="",
                lineno=0,
                msg=evt.event,
                args=(),
                exc_info=None,
            )
            record.session_id = evt.session_id
            record.phase = evt.phase
            record.component = evt.component
            record.event = evt.event
            record.elapsed_ms = evt.elapsed_ms
            self._logger.handle(record)
        except Exception:
            pass


# ── SprintMetrics collector ───────────────────────────────────────────────────

class SprintMetrics:
    """
    Lightweight sprint metrics collector.

    Wraps a TelemetryLogger and provides a simple record/finalize interface
    for sprint-level instrumentation.

    Not a metrics plane — emits telemetry events, not numeric gauges.
    Pairs with MetricsRegistry for a complete observability picture
    (telemetry = phase/events, metrics = RAM/counters).

    Fail-soft: all methods are void.
    """

    def __init__(
        self,
        session_id: str,
        component: str = "sprint",
    ) -> None:
        self._session_id = session_id
        self._component = component
        self._started_at: Optional[float] = None
        self._telemetry = TelemetryLogger(
            session_id=session_id,
            component=component,
        )

    def record_phase(
        self,
        phase: str,
        component: Optional[str] = None,
    ) -> None:
        """Record entering a phase."""
        try:
            elapsed = self._elapsed_ms()
            comp = component or self._component
            self._telemetry.log_event(
                phase=phase,
                component=comp,
                event="phase_entered",
                elapsed_ms=elapsed,
            )
        except Exception:
            pass

    def record_transition(
        self,
        from_phase: str,
        to_phase: str,
        component: Optional[str] = None,
    ) -> None:
        """Record a phase transition."""
        try:
            elapsed = self._elapsed_ms()
            comp = component or self._component
            self._telemetry.log_phase_transition(
                from_phase=from_phase,
                to_phase=to_phase,
                component=comp,
                elapsed_ms=elapsed,
            )
        except Exception:
            pass

    def record_event(
        self,
        phase: str,
        component: str,
        event: str,
    ) -> None:
        """Record a named event within a sprint phase."""
        try:
            elapsed = self._elapsed_ms()
            self._telemetry.log_event(
                phase=phase,
                component=component,
                event=event,
                elapsed_ms=elapsed,
            )
        except Exception:
            pass

    def start(self) -> None:
        """Mark sprint start time."""
        try:
            self._started_at = time.monotonic()
            self._telemetry.log_event(
                phase="BOOT",
                component=self._component,
                event="sprint_started",
                elapsed_ms=0.0,
            )
        except Exception:
            pass

    def finalize(self, final_phase: str = "TEARDOWN") -> None:
        """Record sprint finalization."""
        try:
            elapsed = self._elapsed_ms()
            self._telemetry.log_sprint_finalize(
                final_phase=final_phase,
                component=self._component,
                total_elapsed_ms=elapsed,
            )
        except Exception:
            pass

    def get_telemetry_events(self) -> list[dict]:
        """Return collected telemetry events."""
        try:
            return self._telemetry.get_events()
        except Exception:
            return []

    def _elapsed_ms(self) -> float:
        """Compute elapsed ms since sprint start."""
        if self._started_at is None:
            return 0.0
        try:
            return (time.monotonic() - self._started_at) * 1000.0
        except Exception:
            return 0.0
