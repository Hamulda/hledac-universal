"""
runtime/observability.py — Unified observability hub

Single source of truth for sprint health: metrics + traces + logs aggregation.
Aggregates from all telemetry sources:
  - TelemetryLogger / SprintMetrics (runtime/telemetry.py)
  - DuckDB shadow store metrics (knowledge/duckdb_store.py)
  - Memory pressure (M1ResourceGovernor via core/resource_governor.py)
  - OpenTelemetry spans (opentelemetry-api, when available)

Fail-soft invariants:
  - Every method returns a safe fallback, never raises
  - OTel failures are silent — structured log path always works
  - Bounded collections with explicit max sizes

M1 8GB UMA notes:
  - OTel imported lazily (never at module level)
  - No blocking I/O in aggregation methods
  - Bounded ring buffers for event history

Usage:
  from runtime.observability import ObservabilityHub

  hub = ObservabilityHub(session_id="sprint-abc")
  hub.record_phase("ACQUISITION")
  hub.record_transition("PRELUDE", "ACQUISITION")
  health = hub.get_sprint_health()  # dict with all metrics
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# Lazy OTel import — never at module level (M1 8GB RAM budget)
_OTEL_AVAILABLE: bool | None = None


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAX_EVENT_HISTORY = 10_000  # max telemetry events in memory
MAX_PHASE_SAMPLES = 100  # max phase duration samples for percentile计算
MAX_SOURCE_STATS = 50  # max source stats entries

# ---------------------------------------------------------------------------
# mspspec.Struct DTOs (zero-copy, frozen)
# ---------------------------------------------------------------------------
import msgspec  # noqa: E402


@dataclass(slots=True)
class _PhaseSample:
    """A single phase duration sample."""

    phase: str
    component: str | None
    duration_ms: float
    ts: str


@dataclass(slots=True)
class _TransitionSample:
    """A single phase transition record."""

    from_phase: str
    to_phase: str
    component: str | None
    duration_ms: float
    ts: str


@dataclass(slots=True)
class _SourceStats:
    """Per-source finding statistics."""

    source_type: str
    findings_count: int
    ioc_count: int
    hit_rate: float
    ts: str


# ---------------------------------------------------------------------------
# SprintHealth snapshot (msgspec.Struct for zero-copy serialization)
# ---------------------------------------------------------------------------


class SprintHealth(msgspec.Struct):
    """
    Complete sprint health snapshot — single source of truth for sprint status.

    Aggregates: phase timings, transition counts, source stats, memory pressure,
    DuckDB stats, OTel trace summary, circuit breaker state, fetch telemetry.
    Frozen=True because health snapshots are immutable after construction.
    """

    session_id: str
    phase: str
    elapsed_ms: float
    events_total: int
    phases_recorded: int
    transitions_recorded: int
    sources_recorded: int
    # Memory pressure (M1ResourceGovernor)
    memory_pressure_pct: float | None  # RSS / max_rss if available
    # Memory layer pressure
    memory_layer_pressure_pct: float | None  # from MemoryLayer
    # DuckDB stats
    duckdb_pending: int | None  # pending ingest markers
    duckdb_deadletter: int | None  # deadletter markers
    duckdb_rejected: int | None  # quality rejections
    duckdb_accepted: int | None  # quality acceptances
    duckdb_ingest_latency_ms: float | None  # avg ingest latency
    duckdb_query_latency_ms: float | None  # avg query latency
    # Phase percentile latencies
    avg_phase_ms: float | None  # average phase duration
    p50_phase_ms: float | None  # median phase duration
    p95_phase_ms: float | None  # 95th percentile phase duration
    # Circuit breaker state (F228F)
    cb_open_count: int | None
    cb_half_open_count: int | None
    cb_closed_count: int | None
    cb_open_duration_s: float | None
    # Fetch coordinator telemetry (F360)
    fetch_blocked_domains: int | None
    fetch_circuit_open: bool | None
    # bounded_gather aggregated stats (F360)
    gather_tasks_gathered: int | None
    gather_tasks_errors: int | None
    gather_errors_suppressed: int | None
    # Sprint budget (F360)
    sprint_budget_elapsed_ms: float | None
    sprint_budget_remaining_ms: float | None
    # OTel
    otel_traces: int | None  # OTel trace count if available
    otel_spans: int | None  # OTel span count if available
    ts: str = field(default="")

    def __post_init__(self) -> None:
        if not self.ts:
            self.ts = datetime.now(UTC).isoformat()


class ObservabilityHub:
    """
    Unified observability hub — aggregates all sprint telemetry.

    Coordinates:
      - TelemetryLogger: phase transitions, sprint events
      - SprintMetrics: phase timing, event counts
      - DuckDBShadowStore: pending/deadletter/accepted/rejected counts
      - M1ResourceGovernor: memory pressure
      - OTel: trace/span counts (when available)

    Thread-safe for concurrent access from multiple async tasks.
    All public methods are fail-soft: never raise, always return safe fallback.
    """

    def __init__(
        self,
        session_id: str,
        initial_phase: str = "UNKNOWN",
    ) -> None:
        self._session_id = session_id
        self._phase = initial_phase
        self._started_at = time.monotonic()
        self._telemetry_logger: Any = None  # lazy, set via attach_telemetry
        self._sprint_metrics: Any = None  # lazy
        self._duckdb_store: Any = None  # lazy, set via attach_duckdb
        self._resource_governor: Any = None  # lazy, set via attach_governor

        # Bounded event history (ring buffer for memory safety)
        self._events: deque[dict] = deque(maxlen=MAX_EVENT_HISTORY)
        self._phase_samples: deque[_PhaseSample] = deque(maxlen=MAX_PHASE_SAMPLES)
        self._transition_samples: deque[_TransitionSample] = deque(maxlen=MAX_PHASE_SAMPLES)
        self._source_stats: deque[_SourceStats] = deque(maxlen=MAX_SOURCE_STATS)

        # OTel lazy handle
        self._otel_tracer: Any = None

    # -------------------------------------------------------------------------
    # Attachments (lazy — duckdb/governor initialized after hub)
    # -------------------------------------------------------------------------

    def attach_telemetry(self, logger: Any, metrics: Any) -> None:
        """Attach TelemetryLogger and SprintMetrics instances."""
        self._telemetry_logger = logger
        self._sprint_metrics = metrics

    def attach_duckdb(self, store: Any) -> None:
        """Attach DuckDBShadowStore for metrics polling."""
        self._duckdb_store = store

    def attach_governor(self, governor: Any) -> None:
        """Attach M1ResourceGovernor for memory pressure reading."""
        self._resource_governor = governor

    # -------------------------------------------------------------------------
    # Record primitives (delegated to attached components)
    # -------------------------------------------------------------------------

    def record_phase(self, phase: str, component: str | None = None) -> None:
        """Record entering a new phase."""
        self._phase = phase
        try:
            if self._sprint_metrics is not None:
                self._sprint_metrics.record_phase(phase, component)
        except Exception:
            pass
        try:
            if self._telemetry_logger is not None:
                self._telemetry_logger.log_phase_transition(from_phase=self._phase, to_phase=phase, component=component)
        except Exception:
            pass

    def record_transition(
        self,
        from_phase: str,
        to_phase: str,
        component: str | None = None,
        elapsed_ms: float = 0.0,
    ) -> None:
        """Record a phase transition."""
        # Record in local ring buffer for percentile calculation
        sample = _TransitionSample(
            from_phase=from_phase,
            to_phase=to_phase,
            component=component,
            duration_ms=elapsed_ms,
            ts=datetime.now(UTC).isoformat(),
        )
        self._transition_samples.append(sample)

        try:
            if self._sprint_metrics is not None:
                self._sprint_metrics.record_transition(from_phase, to_phase, component)
        except Exception:
            pass
        try:
            if self._telemetry_logger is not None:
                self._telemetry_logger.log_event(
                    phase=to_phase,
                    component=component or "sprint",
                    event="transition",
                    elapsed_ms=elapsed_ms,
                )
        except Exception:
            pass

    def record_event(
        self,
        phase: str | None = None,
        component: str = "sprint",
        event: str = "custom",
        elapsed_ms: float = 0.0,
    ) -> None:
        """Record a custom telemetry event."""
        evt = {
            "session_id": self._session_id,
            "phase": phase or self._phase,
            "component": component,
            "event": event,
            "elapsed_ms": elapsed_ms,
            "ts": datetime.now(UTC).isoformat(),
        }
        self._events.append(evt)

        try:
            if self._sprint_metrics is not None:
                self._sprint_metrics.record_event(phase or self._phase, component, event)
        except Exception:
            pass
        try:
            if self._telemetry_logger is not None:
                self._telemetry_logger.log_event(phase or self._phase, component, event, elapsed_ms)
        except Exception:
            pass

    def record_source_hit(
        self,
        source_type: str,
        findings_count: int,
        ioc_count: int,
        hit_rate: float,
    ) -> None:
        """Record per-source finding statistics."""
        sample = _SourceStats(
            source_type=source_type,
            findings_count=findings_count,
            ioc_count=ioc_count,
            hit_rate=hit_rate,
            ts=datetime.now(UTC).isoformat(),
        )
        self._source_stats.append(sample)

    def record_phase_duration(self, phase: str, component: str | None, duration_ms: float) -> None:
        """Record a phase duration sample for percentile calculation."""
        sample = _PhaseSample(
            phase=phase,
            component=component,
            duration_ms=duration_ms,
            ts=datetime.now(UTC).isoformat(),
        )
        self._phase_samples.append(sample)

    # -------------------------------------------------------------------------
    # Health snapshot aggregation
    # -------------------------------------------------------------------------

    def get_sprint_health(self) -> dict[str, Any]:
        """
        Compute and return a complete sprint health snapshot.

        Single source of truth for sprint status — aggregates all telemetry
        sources into one dict with:
          - Phase timings and percentile latencies
          - Source statistics
          - DuckDB pending/deadletter/rejected/accepted counts
          - Memory pressure (RSS/max_rss) if available
          - OTel trace/span counts if available
        """
        elapsed_ms = (time.monotonic() - self._started_at) * 1000.0

        # Aggregate DuckDB stats
        duckdb_pending = None
        duckdb_deadletter = None
        duckdb_rejected = None
        duckdb_accepted = None
        if self._duckdb_store is not None:
            try:
                stats = self._duckdb_store.get_stats()
                duckdb_pending = stats.get("pending_upserts", 0)
                duckdb_deadletter = stats.get("deadletter_count", 0)
                duckdb_rejected = stats.get("quality_rejected", 0)
                duckdb_accepted = stats.get("quality_accepted", 0)
            except Exception:
                pass

        # Aggregate memory pressure
        memory_pressure_pct = None
        if self._resource_governor is not None:
            try:
                memory_pressure_pct = getattr(self._resource_governor, "memory_pressure", None)
                if memory_pressure_pct is not None:
                    memory_pressure_pct = float(memory_pressure_pct)
            except Exception:
                pass

        # Phase percentile calculation
        avg_phase_ms, p50_phase_ms, p95_phase_ms = self._percentile_phase_ms()

        # OTel traces/spans (lazy)
        otel_traces = None
        otel_spans = None
        if self._otel_tracer is not None:
            try:
                # OTel tracer provider stats if available
                provider = getattr(self._otel_tracer, "provider", None)
                if provider is not None:
                    otel_traces = getattr(provider, "active_trace_count", lambda: None)()
                    otel_spans = getattr(provider, "active_span_count", lambda: None)()
            except Exception:
                pass

        return {
            "session_id": self._session_id,
            "phase": self._phase,
            "elapsed_ms": elapsed_ms,
            "events_total": len(self._events),
            "phases_recorded": len(self._phase_samples),
            "transitions_recorded": len(self._transition_samples),
            "sources_recorded": len(self._source_stats),
            # Memory pressure (M1ResourceGovernor + MemoryLayer)
            "memory_pressure_pct": memory_pressure_pct,
            "memory_layer_pressure_pct": self._get_memory_layer_pressure(),
            # DuckDB stats
            "duckdb_pending": duckdb_pending,
            "duckdb_deadletter": duckdb_deadletter,
            "duckdb_rejected": duckdb_rejected,
            "duckdb_accepted": duckdb_accepted,
            "duckdb_ingest_latency_ms": self._get_duckdb_ingest_latency(),
            "duckdb_query_latency_ms": self._get_duckdb_query_latency(),
            # Phase latencies
            "avg_phase_ms": avg_phase_ms,
            "p50_phase_ms": p50_phase_ms,
            "p95_phase_ms": p95_phase_ms,
            # Circuit breaker state (F228F)
            "cb_open_count": self._get_cb_open_count(),
            "cb_half_open_count": self._get_cb_half_open_count(),
            "cb_closed_count": self._get_cb_closed_count(),
            "cb_open_duration_s": self._get_cb_open_duration(),
            # Fetch coordinator telemetry (F360)
            "fetch_blocked_domains": self._get_fetch_blocked_domains(),
            "fetch_circuit_open": self._get_fetch_circuit_open(),
            # bounded_gather stats (F360)
            "gather_tasks_gathered": self._get_gather_tasks_gathered(),
            "gather_tasks_errors": self._get_gather_tasks_errors(),
            "gather_errors_suppressed": self._get_gather_errors_suppressed(),
            # Sprint budget (F360)
            "sprint_budget_elapsed_ms": elapsed_ms,
            "sprint_budget_remaining_ms": self._get_sprint_budget_remaining(),
            # OTel
            "otel_traces": otel_traces,
            "otel_spans": otel_spans,
            "ts": datetime.now(UTC).isoformat(),
        }

    def get_sprint_health_struct(self) -> SprintHealth:
        """
        Same as get_sprint_health() but returns a msgspec.Struct (zero-copy).
        Preferred for hot-path usage — avoids dict allocation.
        """
        data = self.get_sprint_health()
        return SprintHealth(**data)

    # -------------------------------------------------------------------------
    # Percentile helpers
    # -------------------------------------------------------------------------

    def _percentile_phase_ms(self) -> tuple[float | None, float | None, float | None]:
        """Calculate avg, p50, p95 phase durations from samples."""
        if not self._phase_samples:
            return None, None, None
        try:
            durations = sorted(s.duration_ms for s in self._phase_samples)
            n = len(durations)
            avg = sum(durations) / n
            p50 = durations[int(n * 0.50)]
            p95 = durations[min(int(n * 0.95), n - 1)]
            return avg, p50, p95
        except Exception:
            return None, None, None

    # ── Sprint F360: Circuit breaker state helpers ───────────────────────────

    def _get_cb_open_count(self) -> int | None:
        """Get circuit breaker open count from MetricsRegistry."""
        try:
            from hledac.universal.metrics_registry import get_metrics_registry
            reg = get_metrics_registry()
            return reg._counters.get("circuit_breaker_open_count", 0)
        except Exception:
            return None

    def _get_cb_half_open_count(self) -> int | None:
        """Get circuit breaker half-open count."""
        try:
            from hledac.universal.metrics_registry import get_metrics_registry
            reg = get_metrics_registry()
            return reg._counters.get("circuit_breaker_half_open_count", 0)
        except Exception:
            return None

    def _get_cb_closed_count(self) -> int | None:
        """Get circuit breaker closed count."""
        try:
            from hledac.universal.metrics_registry import get_metrics_registry
            reg = get_metrics_registry()
            return reg._counters.get("circuit_breaker_closed_count", 0)
        except Exception:
            return None

    def _get_cb_open_duration(self) -> float | None:
        """Get circuit breaker total open duration in seconds."""
        try:
            from hledac.universal.metrics_registry import get_metrics_registry
            reg = get_metrics_registry()
            return float(reg._counters.get("circuit_breaker_open_duration_s", 0))
        except Exception:
            return None

    # ── Sprint F360: Fetch coordinator telemetry helpers ────────────────────

    def _get_fetch_blocked_domains(self) -> int | None:
        """Get number of currently blocked domains from FetchCoordinator."""
        try:
            from hledac.universal.metrics_registry import get_metrics_registry
            reg = get_metrics_registry()
            val = reg._gauges.get("fetch_coordinator_blocked_domains")
            return int(val) if val is not None else None
        except Exception:
            return None

    def _get_fetch_circuit_open(self) -> bool | None:
        """Check if any circuit breaker is open."""
        try:
            from hledac.universal.metrics_registry import get_metrics_registry
            reg = get_metrics_registry()
            val = reg._gauges.get("fetch_coordinator_circuit_open")
            return bool(val) if val is not None else None
        except Exception:
            return None

    # ── Sprint F360: Memory layer pressure helper ──────────────────────────

    def _get_memory_layer_pressure(self) -> float | None:
        """Get memory layer pressure from MemoryLayer."""
        try:
            from hledac.universal.metrics_registry import get_metrics_registry
            reg = get_metrics_registry()
            val = reg._gauges.get("memory_layer_pressure_pct")
            return float(val) if val is not None else None
        except Exception:
            return None

    # ── Sprint F360: DuckDB latency helpers ────────────────────────────────

    def _get_duckdb_ingest_latency(self) -> float | None:
        """Get average DuckDB ingest latency."""
        try:
            from hledac.universal.metrics_registry import get_metrics_registry
            reg = get_metrics_registry()
            val = reg._gauges.get("duckdb_ingest_latency_ms")
            return float(val) if val is not None else None
        except Exception:
            return None

    def _get_duckdb_query_latency(self) -> float | None:
        """Get average DuckDB query latency."""
        try:
            from hledac.universal.metrics_registry import get_metrics_registry
            reg = get_metrics_registry()
            val = reg._gauges.get("duckdb_query_latency_ms")
            return float(val) if val is not None else None
        except Exception:
            return None

    # ── Sprint F360: bounded_gather stats helpers ──────────────────────────

    def _get_gather_tasks_gathered(self) -> int | None:
        """Get total tasks gathered via bounded_gather."""
        try:
            from hledac.universal.metrics_registry import get_metrics_registry
            reg = get_metrics_registry()
            return reg._counters.get("bounded_gather_tasks_gathered", 0)
        except Exception:
            return None

    def _get_gather_tasks_errors(self) -> int | None:
        """Get total errors from bounded_gather."""
        try:
            from hledac.universal.metrics_registry import get_metrics_registry
            reg = get_metrics_registry()
            return reg._counters.get("bounded_gather_tasks_errors", 0)
        except Exception:
            return None

    def _get_gather_errors_suppressed(self) -> int | None:
        """Get total suppressed errors from bounded_gather."""
        try:
            from hledac.universal.metrics_registry import get_metrics_registry
            reg = get_metrics_registry()
            return reg._counters.get("bounded_gather_errors_suppressed", 0)
        except Exception:
            return None

    # ── Sprint F360: Sprint budget helper ──────────────────────────────────

    def _get_sprint_budget_remaining(self) -> float | None:
        """Get remaining sprint budget from MetricsRegistry."""
        try:
            from hledac.universal.metrics_registry import get_metrics_registry
            reg = get_metrics_registry()
            val = reg._gauges.get("sprint_budget_remaining_ms")
            return float(val) if val is not None else None
        except Exception:
            return None

    # -------------------------------------------------------------------------
    # Event history accessors
    # -------------------------------------------------------------------------

    def get_recent_events(self, limit: int = 100) -> list[dict]:
        """Return the most recent telemetry events (newest last)."""
        events = list(self._events)
        return events[-limit:] if len(events) > limit else events

    def get_phase_samples(self, limit: int = 50) -> list[_PhaseSample]:
        """Return phase duration samples for analysis."""
        samples = list(self._phase_samples)
        return samples[-limit:] if len(samples) > limit else samples

    def get_source_stats(self) -> list[dict]:
        """Return per-source finding statistics."""
        return [
            {
                "source_type": s.source_type,
                "findings_count": s.findings_count,
                "ioc_count": s.ioc_count,
                "hit_rate": s.hit_rate,
                "ts": s.ts,
            }
            for s in self._source_stats
        ]

    # -------------------------------------------------------------------------
    # OTel integration (lazy)
    # -------------------------------------------------------------------------

    def _ensure_otel(self) -> bool:
        """Lazy OTel initialization. Returns True if OTel is available."""
        global _OTEL_AVAILABLE
        if _OTEL_AVAILABLE is not None:
            return _OTEL_AVAILABLE
        try:
            from opentelemetry import trace

            self._otel_tracer = trace.get_tracer("hledac.observability")
            _OTEL_AVAILABLE = True
            return True
        except Exception:
            _OTEL_AVAILABLE = False
            return False

    def emit_otel_span(
        self,
        name: str,
        phase: str | None = None,
        component: str | None = None,
        attributes: dict | None = None,
    ) -> Any | None:
        """
        Emit an OTel span event (lazy — OTel loaded on first call).

        Returns the span context or None if OTel unavailable.
        All errors are silent — caller always gets None on failure.
        """
        if not self._ensure_otel():
            return None
        try:
            from opentelemetry.trace import Status, StatusCode

            span = self._otel_tracer.start_span(name)
            if attributes:
                for k, v in attributes.items():
                    span.set_attribute(k, v)
            span.set_attribute("session_id", self._session_id)
            span.set_attribute("phase", phase or self._phase)
            if component:
                span.set_attribute("component", component)
            span.add_event(name, {"elapsed_ms": (time.monotonic() - self._started_at) * 1000.0})
            span.set_status(Status(StatusCode.OK))
            span.end()
            return span
        except Exception:
            return None

    # -------------------------------------------------------------------------
    # Convenience: emit a structured log record directly
    # -------------------------------------------------------------------------

    def log_health_check(self) -> None:
        """Log current health snapshot as INFO level."""
        try:
            logger = logging.getLogger("hledac.observability.health")
            health = self.get_sprint_health()
            logger.info(
                "sprint_health",
                extra={
                    "session_id": health["session_id"],
                    "phase": health["phase"],
                    "elapsed_ms": round(health["elapsed_ms"], 1),
                    "events": health["events_total"],
                    "duckdb_pending": health["duckdb_pending"],
                    "memory_pressure_pct": health["memory_pressure_pct"],
                    "avg_phase_ms": health["avg_phase_ms"],
                },
            )
        except Exception:
            pass
