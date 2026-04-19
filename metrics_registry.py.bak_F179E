"""
MetricsRegistry - Prometheus-style lightweight metrics
===================================================

Simple metrics collection without external dependencies.
Tracks runtime metrics for debugging RAM constraints.

M1 8GB Optimization:
- Bounded counters/gauges stored in memory
- Periodic flush to disk JSONL
- Ring buffer for recent snapshots
- No raw strings or large payloads
"""

from __future__ import annotations

import json
import logging
import os
from collections import deque

# psutil is optional — lazy import with fail-soft fallback
try:
    import psutil
    _PSUTIL_AVAILABLE = True
except ImportError:
    psutil = None  # type: ignore[assignment]
    _PSUTIL_AVAILABLE = False
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# Bounded metric names (no arbitrary labels)
METRIC_NAMES = frozenset([
    # Orchestrator metrics
    "orchestrator_rss_mb",
    "orchestrator_frontier_size",
    "orchestrator_evidence_ring_len",
    "orchestrator_tool_exec_events",
    "orchestrator_budget_remaining_tokens",
    "orchestrator_budget_remaining_time",
    "orchestrator_budget_remaining_api_calls",
    # Cache metrics
    "cache_http_size",
    "cache_snapshot_size",
    "cache_frontier_size",
    # Memory metrics
    "memory_open_fds",
    "memory_rss_mb",
    "memory_vms_mb",
    # MLX metrics
    "mlx_cache_hits",
    "mlx_cache_misses",
    "mlx_cache_size_bytes",
    "mlx_active_memory_bytes",
    "mlx_peak_memory_bytes",
    "mlx_cache_fragmentation_ratio",
    "mlx_kernel_compilation_time_ms",
    "mlx_kernel_cache_hit_rate",
    "model_load_duration_ms",
    "model_unload_count",
    "model_load_failures",
    "action_latency_ms",
    "thermal_throttle_events",
    "thermal_recovery_events",
    "memory_zone_normal_seconds",
    "memory_zone_high_seconds",
    "memory_zone_critical_seconds",
])


@dataclass
class MetricSnapshot:
    """A single metric snapshot"""
    ts: datetime
    name: str
    value: float
    labels: Optional[Dict[str, str]] = None  # Only if bounded
    correlation: Optional[Dict[str, Optional[str]]] = None  # run_id, branch_id, provider_id, action_id


class MetricsRegistry:
    """
    Lightweight metrics registry with disk flush.

    Design:
    - In-memory counters/gauges (tiny)
    - Periodic flush to disk JSONL
    - Ring buffer for recent snapshots (maxlen)
    - No raw strings or large payloads
    """

    # Flush cadence
    FLUSH_EVENTS = 100  # Flush every N events
    FLUSH_SECONDS = 60  # Or every N seconds

    # Ring buffer size
    MAX_SNAPSHOTS = 100

    def __init__(
        self,
        run_dir: Path,
        run_id: str = "default",
        correlation: Optional[Dict[str, Optional[str]]] = None,
    ):
        """
        Initialize metrics registry.

        Args:
            run_dir: Directory for metrics JSONL
            run_id: Run identifier
            correlation: Optional correlation dict with keys:
                branch_id, provider_id, action_id
                (run_id is taken from run_id parameter)
        """
        self._run_dir = run_dir
        self._run_id = run_id
        # Merge run_id into correlation if no correlation provided — ensures run_id
        # propagates to persisted JSONL (tiny local patch for F200B drift).
        # Grammar normalization: only shared RunCorrelation keys survive.
        _GRAMMAR_KEYS = frozenset(["run_id", "branch_id", "provider_id", "action_id"])
        if correlation is None:
            self._correlation = {"run_id": run_id}
        else:
            # Normalize to shared grammar keys only; merge run_id from __init__.
            self._correlation = {k: correlation.get(k) for k in _GRAMMAR_KEYS}
            self._correlation["run_id"] = run_id
        self._last_flush = datetime.utcnow()

        # Counters (integers)
        self._counters: Dict[str, int] = {}

        # Gauges (floats)
        self._gauges: Dict[str, float] = {}

        # Ring buffer for recent snapshots
        self._snapshots: deque = deque(maxlen=self.MAX_SNAPSHOTS)

        # Event count for flush cadence
        self._event_count = 0

        # Sprint event ring buffer (telemetry handoff from runtime/telemetry.py)
        self._sprint_events: deque = deque(maxlen=100)

        # Closed state — prevents post-close mutation drift (F200E)
        self._closed = False

        # Persist state tracking for degraded mode visibility (F200K)
        self._persist_available = True
        self._last_persist_failure: Optional[str] = None

        # Persist file — fail-soft, caller tracks degraded state
        self._persist_file = self._init_persist_file()
        self._persist_available = self._persist_file is not None

        logger.info(f"MetricsRegistry initialized: run_id={run_id}")

    def _init_persist_file(self) -> Optional[Any]:
        """Initialize persistence file. Fail-soft: returns None on failure, caller tracks degraded state."""
        metrics_dir = self._run_dir / "logs"
        metrics_dir.mkdir(parents=True, exist_ok=True)
        metrics_file = metrics_dir / "metrics.jsonl"

        try:
            return open(metrics_file, "ab")
        except Exception as e:
            # Capture failure message — caller (_persist_available, _last_persist_failure)
            # is already initialized before this call
            self._last_persist_failure = str(e)
            logger.warning(f"Failed to open metrics file: {e}")
            return None

    def _validate_metric_name(self, name: str) -> bool:
        """Validate metric name is in bounded set (exact match only — no arbitrary prefixes)"""
        return name in METRIC_NAMES

    def inc(self, name: str, delta: int = 1) -> None:
        """
        Increment a counter.

        Args:
            name: Metric name
            delta: Amount to increment
        """
        if self._closed:
            return
        if not self._validate_metric_name(name):
            logger.warning(f"Invalid metric name: {name}")
            return

        self._counters[name] = self._counters.get(name, 0) + delta
        self._event_count += 1
        self._maybe_flush()

    def set_gauge(self, name: str, value: float) -> None:
        """
        Set a gauge value.

        Args:
            name: Metric name
            value: Gauge value
        """
        if self._closed:
            return
        if not self._validate_metric_name(name):
            logger.warning(f"Invalid metric name: {name}")
            return

        self._gauges[name] = value
        self._event_count += 1
        self._maybe_flush()

    def _maybe_flush(self) -> None:
        """Flush to disk if cadence met"""
        if self._event_count >= self.FLUSH_EVENTS:
            self.flush()

    def tick(self) -> None:
        """
        Tick metrics - call periodically from research loop.
        Captures current system metrics.

        F200G fix: psutil is optional; skip if not available.
        F200E fix: post-close tick is no-op.
        """
        if self._closed:
            return
        if not _PSUTIL_AVAILABLE:
            return

        # Process memory
        try:
            process = psutil.Process(os.getpid())  # type: ignore[union-attr]
            mem_info = process.memory_info()
            self.set_gauge("memory_rss_mb", mem_info.rss / (1024 * 1024))
            self.set_gauge("memory_vms_mb", mem_info.vms / (1024 * 1024))
        except Exception:
            pass

        # Open file descriptors (Unix)
        try:
            process = psutil.Process(os.getpid())  # type: ignore[union-attr]
            self.set_gauge("memory_open_fds", process.num_fds())
        except Exception:
            pass

    def flush(self, force: bool = False) -> None:
        """
        Flush metrics to disk.

        Args:
            force: If True, always flush regardless of time/event thresholds.
        """
        # Post-close flush guard — force=True bypasses for close() semantics
        if getattr(self, '_closed', False) and not force:
            return
        now = datetime.utcnow()

        # Check time-based flush (skip if not forced and thresholds not met)
        if not force:
            elapsed = (now - self._last_flush).total_seconds()
            if elapsed < self.FLUSH_SECONDS and self._event_count < self.FLUSH_EVENTS:
                return

        # Collect metrics
        metrics = []
        for name, value in self._counters.items():
            m = {
                "ts": now.isoformat(),
                "name": name,
                "type": "counter",
                "value": value,
            }
            if self._correlation:
                m["correlation"] = self._correlation
            metrics.append(m)
        for name, value in self._gauges.items():
            m = {
                "ts": now.isoformat(),
                "name": name,
                "type": "gauge",
                "value": value,
            }
            if self._correlation:
                m["correlation"] = self._correlation
            metrics.append(m)

        # Add to ring buffer
        for m in metrics:
            self._snapshots.append(m)

        # Persist
        if self._persist_file:
            try:
                for m in metrics:
                    line = json.dumps(m, separators=(',', ':'))
                    self._persist_file.write(line.encode('utf-8') + b'\n')
                self._persist_file.flush()
                os.fsync(self._persist_file.fileno())
            except Exception as e:
                logger.error(f"Failed to flush metrics: {e}")

        self._last_flush = now
        self._event_count = 0

        logger.debug(f"Flushed {len(metrics)} metrics to disk")

    def get_summary(self) -> Dict[str, Any]:
        """
        Get metrics summary — observer ledger truth.

        Returns lightweight state snapshot for debugging and monitoring.
        No execution authority, no policy, no audit chain.
        """
        return {
            "run_id": self._run_id,
            # Lifecycle truth
            "closed": self._closed,
            # Persistence truth
            "persist_available": getattr(self, '_persist_available', None),
            "degraded_ram_only": getattr(self, '_persist_available', True) is False,
            "last_persist_failure": getattr(self, '_last_persist_failure', None),
            # Counts
            "counter_count": len(self._counters),
            "gauge_count": len(self._gauges),
            "snapshot_count": len(self._snapshots),
            "sprint_event_count": len(self._sprint_events),
            # Live data (ring buffer contains recent snapshots)
            "counters": dict(self._counters),
            "gauges": dict(self._gauges),
        }

    def ingest_sprint_event(self, event: Dict[str, object]) -> None:
        """
        Ingest a sprint telemetry event from runtime/telemetry.py.

        Fail-soft: errors are swallowed. No validation required — events
        are already structured by SprintEvent.to_dict().

        Args:
            event: Dict with keys: session_id, phase, component, event, elapsed_ms, ts
        """
        try:
            if self._closed:
                return
            # Validate required fields present
            required = {"session_id", "phase", "component", "event", "elapsed_ms"}
            if not required.issubset(event.keys()):
                return
            self._sprint_events.append(event)
        except Exception:
            pass

    def close(self) -> None:
        """Close and flush - force=True to prevent tail-loss of pending metrics."""
        if self._closed:
            return
        self._closed = True
        self.flush(force=True)
        if self._persist_file:
            try:
                self._persist_file.flush()
                self._persist_file.close()
            except Exception as e:
                logger.error(f"Error closing metrics: {e}")
            finally:
                self._persist_file = None

    def __enter__(self) -> "MetricsRegistry":
        return self

    def __exit__(self, _exc_type, _exc_val, _exc_tb) -> None:
        self.close()


# Convenience function
def create_metrics_registry(
    run_dir: Path,
    run_id: str = "default"
) -> MetricsRegistry:
    """Create a MetricsRegistry instance"""
    return MetricsRegistry(run_dir=run_dir, run_id=run_id)
