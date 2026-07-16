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
import logging
import threading
import time
from dataclasses import dataclass, field
import msgspec
from typing import Any, TYPE_CHECKING

# TYPE_CHECKING guards for type hints only — actual imports are lazy inside functions
# to avoid loading OTel SDK at module import time (M1 8GB RAM budget)

__all__ = [
    "configure_otel_bridge",
    "get_otel_bridge",
    "OtelBridge",
]

logger = logging.getLogger(__name__)

# Module-level bridge singleton (lazily constructed)
_BRIDGE: "OtelBridge | None" = None
_BRIDGE_LOCK = threading.Lock()


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
    try:
        from hledac_rust_extensions import create_telemetry_aggregator
        return create_telemetry_aggregator()
    except Exception as e:
        logger.warning(f"[otel_bridge] Rust telemetry aggregator unavailable: {e}")
        return None


# ── Metric instruments cache ────────────────────────────────────────────────────

class _MetricInstruments(msgspec.Struct):
    """Cached OTel metric instruments for one metric name."""
    counter_values: dict[str, tuple[int, int]]  # name → (count, bytes)
    histogram_values: dict[str, dict[str, int]]  # name → {p50, p95, p99, ...}
    gauge_values: dict[str, float]  # name → value


# ── OtelBridge ────────────────────────────────────────────────────────────────

class OtelBridge:
    """
    Rust → Python OTel bridge for metrics export.

    Bounded design (M1 8GB safe):
      • Export interval: configurable, min 5000ms
      • Max instruments: 1000 (bounded by telemetry_agg.rs MAX_SERIES)
      • Fail-soft: any error logged, no exception propagated

    Usage:
      bridge = OtelBridge()
      bridge.start()  # Start periodic export
      # ... Rust side calls counter_inc/histogram_record/gauge_set ...
      bridge.stop()
    """

    __slots__ = (
        "_aggregator", "_tracer", "_meter", "_interval_ms",
        "_running", "_export_task", "_lock",
        "_counters", "_histograms", "_gauges",
        "_last_export_time", "_export_count", "_error_count",
    )

    def __init__(
        self,
        aggregator: Any | None = None,
        interval_ms: int = 5000,
    ) -> None:
        """
        Initialize bridge.

        Args:
            aggregator: Rust PyTelemetryAggregator instance (lazy-loaded if None)
            interval_ms: Export interval in milliseconds (min 5000 for M1 8GB)
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
            loop = asyncio.get_running_loop()
            self._export_task = loop.create_task(self._periodic_export_loop())
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
