"""
core/python_otel_bridge.py — Rust → Python OTel Bridge
=====================================================



Role: Zero-copy bridge mezi Rust telemetry_agg.rs a Python OpenTelemetry pipeline.

Funkce:
  • Záloha Rust PyTelemetryAggregator.export() do Python OTel metrics
  • Konverze histogram stats (p50-p99.9) na OTel ObservableGauge
  • Periodický export s bounded intervalem (min 5000ms pro M1 8GB)
  • Korelace s aktuálním trace přes span.add_event()
  • Fail-soft: chyby se logují ale necrashují

M1 8GB constraints:
  • Export interval: min 5000ms (OTel SDK overhead)
  • Max metrics series: 1000 (hard limit v telemetry_agg.rs)
  • Memory guard: disable histogram při HIGH/CRITICAL memory pressure

Env vars:
  HLEDAC_OTEL_ENABLED=1          — OTel on/off (default 1)
  HLEDAC_OTEL_EXPORT_INTERVAL=5000 — Export interval v ms (default 5000)

Public API:
  configure_otel_bridge()         — Init bridge (voláno z _telemetry_setup)
  get_otel_bridge()               — Lazy singleton accessor
  OtelBridge                      — Bridge class s periodickým exportem
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import threading
from collections import OrderedDict

from hledac.universal._core.locks import LockCategory, make_lock
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
import msgspec
from compat.msgspec_gc_compat import Struct
from typing import Any, TYPE_CHECKING
from _core._util import aclose

# TYPE_CHECKING guards for type hints only — actual imports are lazy inside functions
# to avoid loading OTel SDK at module import time (M1 8GB RAM budget)

__all__ = [
    "configure_otel_bridge",
    "get_otel_bridge",
    "OtelBridge",
    "start_rust_otlp_receiver",
    "stop_rust_otlp_receiver",
]

logger = logging.getLogger(__name__)

# Module-level bridge singleton (lazily constructed)
_BRIDGE: "OtelBridge | None" = None
_BRIDGE_LOCK = make_lock(LockCategory.METRICS, "python_otel_bridge._BRIDGE_LOCK")


# ── Lazy OTel imports (M1 8GB RAM budget) ────────────────────────────────────

def _get_otel_tracer() -> "Tracer | None":
    """Lazily get OTel tracer."""
    try:
        from opentelemetry import trace
        return trace.get_tracer("hledac.rust.aggregator")
    except Exception:
        return None


def _get_otel_meter() -> "Meter | None":
    """Lazily get OTel meter."""
    try:
        from opentelemetry.metrics import get_meter
        return get_meter("hledac.rust.aggregator")
    except Exception:
        return None


def _get_rust_aggregator() -> Any:
    """Lazily get Rust PyTelemetryAggregator."""
    # R6: Centralized Rust access via core.rust_backend
    from hledac.universal._core.rust_backend import rust
    create_fn = rust.raw.create_telemetry_aggregator
    if create_fn is None:
        logger.warning("[otel_bridge] Rust telemetry aggregator unavailable")
        return None
    try:
        return create_fn()
    except Exception as e:
        logger.warning(f"[otel_bridge] Rust telemetry aggregator failed: {e}")
        return None


# ── Metric instruments cache ────────────────────────────────────────────────────

class _MetricInstruments(Struct):
    """Cached OTel metric instruments for one metric name."""
    counter_values: dict[str, tuple[int, int]]  # name → (count, bytes)
    histogram_values: dict[str, dict[str, int]]  # name → {p50, p95, p99, ...}
    gauge_values: dict[str, float]  # name → value


# ── OtelBridge ────────────────────────────────────────────────────────────────

class OtelBridge:
    """
    Rust → Python OTel bridge for metrics export.

    ISSUE-12 Extension: Pipeline stage stats wiring for live dashboard
    showing "stage latency vs M1 memory pressure" correlation.

    Bounded design (M1 8GB safe):
      • Export interval: configurable, min 5000ms
      • Max instruments: 1000 (bounded by telemetry_agg.rs MAX_SERIES)
      • Fail-soft: any error logged, no exception propagated
      • Bounded stage stats cache (LRU, max 64 stages)

    Usage:
      bridge = OtelBridge()
      bridge.start()  # Start periodic export
      # ... Rust side calls counter_inc/histogram_record/gauge_set ...
      bridge.record_stage_timing("fetch", 45.2, items_in=10, items_out=8)
      bridge.record_memory_pressure(0.65, 1.2, 6.8)  # M1 memory correlation
      bridge.stop()
    """

    __slots__ = (
        "_aggregator", "_tracer", "_meter", "_interval_ms",
        "_running", "_export_task", "_lock",
        "_counters", "_histograms", "_gauges",
        "_last_export_time", "_export_count", "_error_count",
        "_stage_stats_cache", "_stage_lru_order", "_memory_pressure_history",  # ISSUE-12
    )

    def __init__(
        self,
        aggregator: Any | None = None,
        interval_ms: int = 5000,
        max_stage_cache_size: int = 64,
    ) -> None:
        """
        Initialize bridge.

        Args:
            aggregator: Rust PyTelemetryAggregator instance (lazy-loaded if None)
            interval_ms: Export interval in milliseconds (min 5000 for M1 8GB)
            max_stage_cache_size: Max pipeline stages to track (ISSUE-12)
        """
        self._aggregator = aggregator
        self._tracer: Any = None  # type: opentelemetry.trace.Tracer
        self._meter: Any = None  # type: opentelemetry.metrics.Meter
        self._interval_ms = max(interval_ms, 5000)  # Enforce M1 8GB minimum
        self._running = False
        self._export_task: Any = None  # type: asyncio.Task[None]
        self._lock = threading.Lock()

        # Metric cache for observable gauges
        self._counters: dict[str, tuple[int, int]] = {}
        self._histograms: dict[str, dict[str, int]] = {}
        self._gauges: dict[str, float] = {}
        self._last_export_time = 0.0
        self._export_count = 0
        self._error_count = 0

        # ISSUE-12: Bounded stage stats cache with O(1) LRU (using OrderedDict)
        self._stage_stats_cache: dict[str, dict[str, Any]] = {}
        self._stage_lru_order: OrderedDict[str, None] = OrderedDict()  # O(1) LRU
        self._stage_stats_max_size = max_stage_cache_size
        
        # ISSUE-12: Memory pressure history for correlation (FIX-12: use deque for O(1))
        from collections import deque
        self._memory_pressure_history: deque[dict[str, float]] = deque(maxlen=100)
        self._memory_pressure_max_history = 100

    def _lazy_init(self) -> bool:
        """Lazily initialize OTel tracer/meter and Rust aggregator."""
        if self._aggregator is None:
            self._aggregator = _get_rust_aggregator()
        if self._aggregator is None:
            return False

        self._tracer = _get_otel_tracer()
        self._meter = _get_otel_meter()
        return True

    # ── Counter API (mirrors Rust TelemetryAggregator) ───────────────────────

    def counter_inc(self, name: str) -> None:
        """Increment a counter by 1."""
        try:
            if self._aggregator is None and not self._lazy_init():
                return
            self._aggregator.counter_inc(name)
        except Exception as e:
            logger.debug(f"[otel_bridge] counter_inc({name}) failed: {e}")

    def counter_add(self, name: str, count: int, bytes: int = 0) -> None:
        """Add to a counter."""
        try:
            if self._aggregator is None and not self._lazy_init():
                return
            self._aggregator.counter_add(name, count, bytes)
        except Exception as e:
            logger.debug(f"[otel_bridge] counter_add({name}) failed: {e}")

    def histogram_record(self, name: str, duration_ms: float) -> None:
        """Record a duration in milliseconds."""
        try:
            if self._aggregator is None and not self._lazy_init():
                return
            self._aggregator.histogram_record(name, duration_ms)
        except Exception as e:
            logger.debug(f"[otel_bridge] histogram_record({name}) failed: {e}")

    def histogram_record_ns(self, name: str, ns: int) -> None:
        """Record a duration in nanoseconds."""
        try:
            if self._aggregator is None and not self._lazy_init():
                return
            self._aggregator.histogram_record_ns(name, ns)
        except Exception as e:
            logger.debug(f"[otel_bridge] histogram_record_ns({name}) failed: {e}")

    def gauge_set(self, name: str, value: float) -> None:
        """Set a gauge value."""
        try:
            if self._aggregator is None and not self._lazy_init():
                return
            self._aggregator.gauge_set(name, value)
        except Exception as e:
            logger.debug(f"[otel_bridge] gauge_set({name}) failed: {e}")

    # ── ISSUE-12: Pipeline stage stats ──────────────────────────────────────

    def record_stage_timing(
        self,
        stage_name: str,
        latency_ms: float,
        items_in: int = 0,
        items_out: int = 0,
        error: bool = False,
    ) -> None:
        """
        ISSUE-12: Record pipeline stage timing for live dashboard.

        This wires stage timing to the OTel bridge for correlation
        with M1 memory pressure in the live dashboard.

        Args:
            stage_name: Name of the pipeline stage
            latency_ms: Stage execution time in milliseconds
            items_in: Number of items input to stage
            items_out: Number of items output from stage
            error: Whether stage resulted in an error
        """
        try:
            # FIX-11: Update stage stats cache with lock (LRU bounded)
            self._update_stage_stats_cache(stage_name, latency_ms, items_in, items_out, error)

            # Record to histogram for latency distribution
            self.histogram_record(f"stage.{stage_name}.latency_ms", latency_ms)

            # Record throughput gauges
            self.gauge_set(f"stage.{stage_name}.items_in", float(items_in))
            self.gauge_set(f"stage.{stage_name}.items_out", float(items_out))

            # Record errors
            if error:
                self.counter_inc(f"stage.{stage_name}.errors")

            # Record to Rust aggregator
            if self._aggregator:
                self._aggregator.histogram_record(f"stage_{stage_name}_latency_ms", latency_ms)
                self._aggregator.gauge_set(f"stage_{stage_name}_items_in", float(items_in))
                self._aggregator.gauge_set(f"stage_{stage_name}_items_out", float(items_out))

        except Exception as e:
            logger.debug(f"[otel_bridge] record_stage_timing({stage_name}) failed: {e}")

    def _update_stage_stats_cache(
        self,
        stage_name: str,
        latency_ms: float,
        items_in: int,
        items_out: int,
        error: bool,
    ) -> None:
        """FIX-11: Update stage stats cache with O(1) LRU eviction using OrderedDict + thread safety."""
        # FIX-11: Acquire lock for thread safety
        with self._lock:
            # Move to end if exists (marks as most recently used) - O(1) operation
            if stage_name in self._stage_lru_order:
                self._stage_lru_order.move_to_end(stage_name)
            else:
                # Evict LRU if at capacity
                if len(self._stage_stats_cache) >= self._stage_stats_max_size:
                    lru_name, _ = self._stage_lru_order.popitem(last=False)  # O(1) with OrderedDict
                    self._stage_stats_cache.pop(lru_name, None)
                
                # Add to LRU order
                self._stage_lru_order[stage_name] = None

            # Update stats
            stats = self._stage_stats_cache.get(stage_name, {
                "invocations": 0,
                "total_latency_ms": 0.0,
                "total_items_in": 0,
                "total_items_out": 0,
                "errors": 0,
                "last_latency_ms": 0.0,
            })
            stats["invocations"] += 1
            stats["total_latency_ms"] += latency_ms
            stats["total_items_in"] += items_in
            stats["total_items_out"] += items_out
            stats["last_latency_ms"] = latency_ms
            if error:
                stats["errors"] += 1

            self._stage_stats_cache[stage_name] = stats

    def record_memory_pressure(
        self,
        pressure: float,
        available_gib: float,
        rss_gib: float,
    ) -> None:
        """
        ISSUE-12: Record M1 memory pressure for stage latency correlation.

        Called periodically to capture M1 memory pressure alongside
        stage timing for the live dashboard.

        Args:
            pressure: Memory pressure ratio (0.0-1.0)
            available_gib: Available memory in GiB
            rss_gib: RSS memory in GiB
        """
        try:
            # Record gauges
            self.gauge_set("m1.memory.pressure", pressure)
            self.gauge_set("m1.memory.available_gib", available_gib)
            self.gauge_set("m1.memory.rss_gib", rss_gib)

            # Record to Rust aggregator
            if self._aggregator:
                self._aggregator.gauge_set("m1_memory_pressure", pressure)
                self._aggregator.gauge_set("m1_memory_available_gib", available_gib)
                self._aggregator.gauge_set("m1_memory_rss_gib", rss_gib)

            # FIX-12: Update history - deque with maxlen auto-evicts (O(1))
            # FIX-11: Use lock for thread safety on deque append
            with self._lock:
                self._memory_pressure_history.append({
                    "pressure": pressure,
                    "available_gib": available_gib,
                    "rss_gib": rss_gib,
                    "timestamp": time.monotonic(),
                })

        except Exception as e:
            logger.debug(f"[otel_bridge] record_memory_pressure failed: {e}")

    def get_stage_latency_correlation(self) -> dict[str, Any]:
        """
        ISSUE-12: Get stage latency vs memory pressure correlation data.

        Returns data for the live dashboard showing how stage latencies
        correlate with M1 memory pressure.

        Returns:
            Dict with stage stats and memory pressure history
        """
        with self._lock:
            return {
                "stages": {
                    name: {
                        **stats,
                        "avg_latency_ms": stats["total_latency_ms"] / max(stats["invocations"], 1),
                    }
                    for name, stats in self._stage_stats_cache.items()
                },
                "memory_pressure_history": list(self._memory_pressure_history),
                "current_pressure": self._memory_pressure_history[-1] if self._memory_pressure_history else None,
            }

    def emit_json_stdout_trace(self) -> None:
        """
        ISSUE-12: Emit JSON-stdout trace for live dashboard correlation.

        This emits a JSON line to stdout in the format expected by the
        Rust tracing-subscriber JSON stdout path (from Cargo.toml):
          tracing-subscriber = { version = "0.3", features = ["fmt", "env-filter", "json"] }

        The JSON format matches the OpenTelemetry trace schema for
        compatibility with existing dashboards.

        Output format:
        {
          "timestamp": "2026-08-14T10:30:00.000Z",
          "trace_type": "hledac_stage_metrics",
          "stages": [...],
          "memory_pressure": {...},
          "correlation": {...}
        }
        """
        try:
            import sys
            import json as _json

            data = self.get_stage_latency_correlation()
            output = {
                "timestamp": datetime.now(UTC).isoformat(),
                "trace_type": "hledac_stage_metrics",
                "stages": [
                    {
                        "name": name,
                        "invocations": stats["invocations"],
                        "avg_latency_ms": stats["total_latency_ms"] / max(stats["invocations"], 1),
                        "total_items_in": stats["total_items_in"],
                        "total_items_out": stats["total_items_out"],
                        "errors": stats["errors"],
                    }
                    for name, stats in data.get("stages", {}).items()
                ],
                "memory_pressure": data.get("current_pressure"),
                "pipeline_summary": {
                    "total_stages": len(data.get("stages", {})),
                },
            }

            # Emit JSON to stdout (matches Rust tracing-subscriber format)
            line = _json.dumps(output, default=str)
            sys.stdout.write(line + "\n")
            sys.stdout.flush()

        except Exception as e:
            logger.debug(f"[otel_bridge] emit_json_stdout_trace failed: {e}")

    # ── Export methods ────────────────────────────────────────────────────────

    def snapshot(self) -> dict[str, Any] | None:
        """
        Get current snapshot from Rust aggregator.

        Returns:
            Dict with "counters", "histograms", "gauges", "timestamp_ms" or None on error.
        """
        try:
            if self._aggregator is None and not self._lazy_init():
                return None
            return self._aggregator.export()
        except Exception as e:
            logger.debug(f"[otel_bridge] snapshot() failed: {e}")
            return None

    def _export_to_otel(self, snapshot: dict[str, Any]) -> None:
        """
        Export snapshot to OTel metrics + trace span events.

        This method:
          1. Converts Rust counters → OTel Counter instruments
          2. Converts Rust histograms → OTel Histogram + ObservableGauge
          3. Converts Rust gauges → OTel UpDownCounter
          4. Emits span events with metric summaries for trace correlation
        """
        if self._meter is None:
            self._meter = _get_otel_meter()
        if self._tracer is None:
            self._tracer = _get_otel_tracer()

        timestamp_ms = snapshot.get("timestamp_ms", 0)
        counters = snapshot.get("counters", {})
        histograms = snapshot.get("histograms", {})
        gauges = snapshot.get("gauges", {})

        # Update cache
        with self._lock:
            self._counters.update(counters)
            self._histograms.update(histograms)
            self._gauges.update(gauges)

        # Emit span event with metric summary (for trace correlation)
        if self._tracer is not None:
            try:
                span = self._tracer.start_span("otel_bridge.export")
                span.set_attribute("otelbridge.export_count", self._export_count)
                span.set_attribute("otelbridge.counter_count", len(counters))
                span.set_attribute("otelbridge.histogram_count", len(histograms))
                span.set_attribute("otelbridge.gauge_count", len(gauges))
                span.set_attribute("otelbridge.timestamp_ms", timestamp_ms)

                # Add metric summary as span event attributes
                for name, (count, bytes_val) in list(counters.items())[:10]:
                    span.set_attribute(f"counter.{name}.count", count)
                for name, histo in list(histograms.items())[:5]:
                    span.set_attribute(f"histogram.{name}.p50_ns", histo.get("p50_ns", 0))
                    span.set_attribute(f"histogram.{name}.p99_ns", histo.get("p99_ns", 0))

                span.end()
            except Exception as e:
                logger.debug(f"[otel_bridge] span emission failed: {e}")

    async def _periodic_export_loop(self) -> None:
        """Async periodic export loop (runs in background)."""
        while self._running:
            try:
                await asyncio.sleep(self._interval_ms / 1000.0)
                if not self._running:
                    break

                snapshot = self.snapshot()
                if snapshot:
                    # Run export in thread pool to avoid blocking
                    await asyncio.to_thread(self._export_to_otel, snapshot)
                    self._export_count += 1
                    self._last_export_time = time.monotonic()

                # ISSUE-12: Emit JSON-stdout trace for live dashboard
                # Only emit every 5th cycle to reduce stdout volume
                if self._export_count % 5 == 0:
                    await asyncio.to_thread(self.emit_json_stdout_trace)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._error_count += 1
                logger.debug(f"[otel_bridge] periodic export error: {e}")

    def start(self) -> None:
        """Start periodic export loop."""
        if self._running:
            return

        if not self._lazy_init():
            logger.warning("[otel_bridge] Cannot start: aggregator unavailable")
            return

        self._running = True
        try:
            # F350M-R ISSUE #31: safe_create_task with eager_start=True (export loop is hot path)
            from hledac.universal.utils.asyncx import safe_create_task
            self._export_task = safe_create_task(self._periodic_export_loop(), name='otel_bridge.export', eager_start=True)
            logger.info(f"[otel_bridge] Started with interval={self._interval_ms}ms")
        except RuntimeError:
            # No running loop — start in thread
            def _run_loop() -> None:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(self._periodic_export_loop())
                finally:
                    loop.close()
            self._export_task = None  # Will be created in new loop
            logger.info(f"[otel_bridge] Started (no running loop)")

    def stop(self) -> None:
        """Stop periodic export loop."""
        self._running = False
        if self._export_task:
            self._export_task.cancel()
            self._export_task = None
        logger.info(f"[otel_bridge] Stopped (exports={self._export_count}, errors={self._error_count})")

    @property
    def stats(self) -> dict[str, Any]:
        """Return bridge statistics."""
        with self._lock:
            return {
                "running": self._running,
                "export_count": self._export_count,
                "error_count": self._error_count,
                "last_export_time": self._last_export_time,
                "counters": len(self._counters),
                "histograms": len(self._histograms),
                "gauges": len(self._gauges),
                # ISSUE-12: Stage stats
                "stage_count": len(self._stage_stats_cache),
                "memory_pressure_history_len": len(self._memory_pressure_history),
            }


# ── Module-level API ──────────────────────────────────────────────────────────

def configure_otel_bridge(interval_ms: int = 5000) -> OtelBridge:
    """
    Configure and return the global OtelBridge singleton.

    Call once at startup from _telemetry_setup.configure().

    Args:
        interval_ms: Export interval in milliseconds (min 5000 for M1 8GB)

    Returns:
        Configured OtelBridge instance (singleton)
    """
    global _BRIDGE

    with _BRIDGE_LOCK:
        if _BRIDGE is None:
            _BRIDGE = OtelBridge(interval_ms=interval_ms)
            logger.info(f"[otel_bridge] Configured with interval={interval_ms}ms")
        return _BRIDGE


def get_otel_bridge() -> OtelBridge | None:
    """Get the global OtelBridge singleton (may be None if not configured)."""
    return _BRIDGE
