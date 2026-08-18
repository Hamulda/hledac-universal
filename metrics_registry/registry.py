"""
metrics_registry/registry.py — Main MetricsRegistry Class
=======================================================

The main MetricsRegistry class with all metrics operations.

This file is the original metrics_registry.py refactored into a package.
It contains:
- MetricsRegistry: Main metrics class with bounded counters/gauges
- Factory functions: create_metrics_registry, get_metrics_registry

Design:
- In-memory counters/gauges bounded by LRUCache/TTLCache
- Async flush to disk JSONL (non-blocking)
- Ring buffer for recent snapshots (maxlen=100)
- No raw strings or large payloads

M1 8GB Safety:
- MAX_SNAPSHOTS <= 1024 enforced via assertion
- LRUCache max_size=128 prevents unbounded growth
- TTL expiration prevents stale metric accumulation
- Background thread decouples I/O from collection

Backward Compatible:
    from metrics_registry import MetricsRegistry  # New way
    import metrics_registry                        # Old way (redirects to package)

Sprint ISSUE-12 (2026-08-14) - Original implementation
Sprint ISSUE-16 (2026-08-18) - Package refactor
"""

from __future__ import annotations

import logging
import os
import time
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from utils.cache import LRUCache, TTLCache

from ._core import (
    FLUSH_EVENTS,
    FLUSH_SECONDS,
    MAX_SNAPSHOTS,
    MAX_SPRINT_EVENTS,
    METRIC_NAMES,
    MetricSnapshot,
    _AsyncBatchFlusher,
    _BoundedCounter,
    _GRAMMAR_KEYS,
    _MAX_COUNTER_CACHE_SIZE,
    _MAX_GAUGE_CACHE_SIZE,
    _TTL_SECONDS,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ── Memory Pressure Listener ──────────────────────────────────────────────────


class MetricsRegistryPressureListener:
    """
    ISSUE-12: MemoryPressureListener for metrics registry.

    Implements the MemoryPressureListener protocol to receive
    memory pressure updates and record them to the metrics registry
    for OtelBridge correlation.
    """

    __slots__ = ('_registry',)

    def __init__(self, registry: MetricsRegistry) -> None:
        self._registry = registry

    @property
    def listener_priority(self) -> int:
        """Lowest priority (3) - metrics are not evicted on pressure."""
        return 3

    @property
    def listener_name(self) -> str:
        return 'metrics_registry'

    def on_soft_warn(self) -> None:
        """ELEVATED pressure - record to metrics."""
        self._record_pressure('soft_warn')

    def on_warn(self) -> None:
        """HIGH pressure - record to metrics."""
        self._record_pressure('warn')

    def on_critical(self) -> None:
        """CRITICAL pressure - record to metrics."""
        self._record_pressure('critical')

    def on_recovery(self) -> None:
        """Recovery - record to metrics."""
        self._record_pressure('recovery')

    def _record_pressure(self, level: str) -> None:
        """Record pressure level to metrics registry."""
        try:
            self._registry.set_gauge('memory_pressure_listener_event', float(hash(level) % 1000))
        except Exception:
            pass


# ── Main Registry ──────────────────────────────────────────────────────────────


class MetricsRegistry:
    """
    Lightweight metrics registry with bounded storage and async flush.

    ISSUE-12 Fixes:
    1. Bounded LRUCache for counters/gauges (max 128 entries each)
    2. TTL-based expiration for stale metrics (5 min default)
    3. Async batch-flush via background thread
    4. Pipeline stage stats integration with OtelBridge

    Design:
    - In-memory counters/gauges bounded by LRUCache
    - Async flush to disk JSONL (non-blocking)
    - Ring buffer for recent snapshots (maxlen=100)
    - No raw strings or large payloads

    M1 8GB Safety:
    - MAX_SNAPSHOTS <= 1024 enforced via assertion
    - LRUCache max_size=128 prevents unbounded growth
    - TTL expiration prevents stale metric accumulation
    - Background thread decouples I/O from collection
    """

    __slots__ = (
        '_closed', '_correlation', '_event_count',
        '_last_flush', '_last_persist_failure', '_persist_available',
        '_persist_file', '_run_dir', '_run_id', '_snapshots',
        '_sprint_events', '_flusher', '_stage_stats', '_ttl_seconds',
        '_counter_cache', '_gauge_cache',
        # ISSUE-12 FIX: Legacy dicts removed - use _counter_cache/_gauge_cache
        # ISSUE-16: Lazy area registry
        '_lazy_area_registry',
        # Backward compatibility via properties below
    )

    # FIX: Add backward-compatible properties for observability.py
    @property
    def _counters(self) -> dict[str, int]:
        """FIX: Backward-compatible access to counters (delegates to _counter_cache)."""
        return {k: c.value for k, c in self._counter_cache.items()}

    @property
    def _gauges(self) -> dict[str, float]:
        """FIX: Backward-compatible access to gauges (delegates to _gauge_cache)."""
        return dict(self._gauge_cache)

    def __init__(
        self,
        run_dir: Path,
        run_id: str = 'default',
        correlation: dict[str, str | None] | None = None,
        *,
        ttl_seconds: float = _TTL_SECONDS,
        counter_cache_size: int = _MAX_COUNTER_CACHE_SIZE,
        gauge_cache_size: int = _MAX_GAUGE_CACHE_SIZE,
    ) -> None:
        """
        Initialize metrics registry.

        Args:
            run_dir: Directory for metrics JSONL
            run_id: Run identifier
            correlation: Optional correlation dict with keys:
                branch_id, provider_id, action_id
            ttl_seconds: TTL for counter/gauge entries (default 5 min)
            counter_cache_size: Max counter entries (default 128)
            gauge_cache_size: Max gauge entries (default 128)
        """
        # Guard: MAX_SNAPSHOTS must be bounded
        assert MAX_SNAPSHOTS <= 1024, (
            f'MAX_SNAPSHOTS must be <= 1024, got {MAX_SNAPSHOTS}'
        )

        self._run_dir = run_dir
        self._run_id = run_id
        self._ttl_seconds = ttl_seconds

        if correlation is None:
            self._correlation = {'run_id': run_id}
        else:
            self._correlation = {k: correlation.get(k) for k in _GRAMMAR_KEYS}
            self._correlation['run_id'] = run_id

        # Bounded storage with TTL (ISSUE-12 fix)
        # TTLCache for counters: automatic TTL expiration, no manual cleanup needed
        self._counter_cache: TTLCache[str, _BoundedCounter] = TTLCache(
            max_size=counter_cache_size,
            ttl=ttl_seconds,
        )
        self._gauge_cache = LRUCache[str, float](
            max_size=gauge_cache_size,
            thread_safe=True,
        )

        self._snapshots: deque = deque(maxlen=MAX_SNAPSHOTS)
        self._sprint_events: deque = deque(maxlen=MAX_SPRINT_EVENTS)
        self._event_count = 0
        self._last_flush = datetime.now(UTC)
        self._closed = False

        # Persistence
        self._persist_available = True
        self._last_persist_failure: str | None = None
        metrics_dir = run_dir / 'logs'
        metrics_dir.mkdir(parents=True, exist_ok=True)
        persist_file_path = metrics_dir / 'metrics.jsonl'

        # Start async flusher (ISSUE-12)
        self._flusher = _AsyncBatchFlusher(persist_file_path)
        self._persist_file = None  # Not used with async flusher
        self._flusher.start()

        # ISSUE-12: Register with MemoryPressureBroadcaster for live memory correlation
        self._register_memory_pressure_listener()

        logger.info(f'MetricsRegistry initialized: run_id={run_id}, ttl={ttl_seconds}s')

    # ── ISSUE-12: Memory Pressure Integration ─────────────────────────────────

    def _register_memory_pressure_listener(self) -> None:
        """
        ISSUE-12: Register with MemoryPressureBroadcaster for live memory correlation.

        This enables the live dashboard showing "stage latency vs M1 memory pressure"
        by correlating stage timing metrics with memory pressure events.
        """
        try:
            from hledac.universal._core.memory_pressure import MemoryPressureBroadcaster
            broadcaster = MemoryPressureBroadcaster.get_instance()

            class _MetricsPressureListener:
                """Listener that records pressure events to metrics registry."""
                __slots__ = ('_registry',)

                def __init__(self, registry: MetricsRegistry) -> None:
                    self._registry = registry

                @property
                def listener_priority(self) -> int:
                    return 3  # Lowest priority - metrics are not evicted

                @property
                def listener_name(self) -> str:
                    return 'metrics_registry'

                def on_soft_warn(self) -> None:
                    self._record('soft_warn')

                def on_warn(self) -> None:
                    self._record('warn')

                def on_critical(self) -> None:
                    self._record('critical')

                def on_recovery(self) -> None:
                    self._record('recovery')

                def _record(self, level: str) -> None:
                    try:
                        self._registry.set_gauge('memory_pressure_listener_event', float(hash(level) % 1000))
                    except Exception:
                        pass

            broadcaster.register(_MetricsPressureListener(self))
            logger.debug('[ISSUE-12] Registered with MemoryPressureBroadcaster')
        except ImportError:
            # Memory pressure module not available
            pass
        except Exception:
            # Fail soft - don't block initialization
            pass

    # ── Metric validation ──────────────────────────────────────────────────────

    def _validate_metric_name(self, name: str) -> bool:
        """Validate metric name is in bounded set (exact match or stage pattern)."""
        if name in METRIC_NAMES:
            return True
        # ISSUE-12 FIX: Allow dynamic stage metric names (e.g., stage_latency_ms_discovery)
        # Stage metrics follow pattern: stage_latency_ms_{name}, stage_items_in_{name}, etc.
        if name.startswith(('stage_latency_ms_', 'stage_items_in_', 'stage_items_out_', 'stage_errors_')):
            return True
        return False

    # ── Counter operations ────────────────────────────────────────────────────

    def inc(self, name: str, delta: int = 1) -> None:
        """
        Increment a counter.

        ISSUE-12: Uses bounded LRUCache instead of unbounded dict.

        Args:
            name: Metric name
            delta: Amount to increment
        """
        if self._closed:
            return
        if not self._validate_metric_name(name):
            logger.warning(f'Invalid metric name: {name}')
            return

        # Use bounded cache with TTL (ISSUE-12 fix)
        now = time.monotonic()
        if name in self._counter_cache:
            counter = self._counter_cache[name]
            counter.value += delta
            counter.last_update = now
        else:
            self._counter_cache[name] = _BoundedCounter(value=delta, last_update=now)

        self._event_count += 1
        self._maybe_flush()

    def set_gauge(self, name: str, value: float) -> None:
        """
        Set a gauge value.

        ISSUE-12: Uses bounded LRUCache instead of unbounded dict.

        Args:
            name: Metric name
            value: Gauge value
        """
        if self._closed:
            return
        if not self._validate_metric_name(name):
            logger.warning(f'Invalid metric name: {name}')
            return

        # Use bounded cache with TTL (ISSUE-12 fix)
        self._gauge_cache[name] = value

        self._event_count += 1
        self._maybe_flush()

    # ── Flush logic ───────────────────────────────────────────────────────────

    def _maybe_flush(self) -> None:
        """Flush to disk if cadence met."""
        if self._event_count >= FLUSH_EVENTS:
            self.flush()

    def _expire_stale_entries(self) -> None:
        """
        ISSUE-12: Expire stale entries based on TTL.

        With TTLCache, expiration is automatic. This method now triggers
        the TTL check which is O(n) but only runs during flush (every 60s).
        """
        # TTLCache automatically expires entries on access.
        # Trigger eviction by iterating (TTLCache._evict_expired is O(n) but bounded)
        if len(self._counter_cache) > 0:
            # Access one key to trigger TTLCache's automatic eviction check
            try:
                # This triggers _evict_expired in TTLCache.get()
                _ = self._counter_cache.get(next(iter(self._counter_cache)))
            except (KeyError, StopIteration):
                pass

    def tick(self) -> None:
        """
        Tick metrics - call periodically from research loop.
        Captures current system metrics.

        F200G fix: psutil is optional; skip if not available.
        """
        if self._closed:
            return
        try:
            import psutil
            process = psutil.Process(os.getpid())
            mem_info = process.memory_info()
            self.set_gauge('memory_rss_mb', mem_info.rss / (1024 * 1024))
            self.set_gauge('memory_vms_mb', mem_info.vms / (1024 * 1024))
            self.set_gauge('memory_open_fds', float(process.num_fds()))
        except ImportError:
            pass
        except Exception:
            pass

    def flush(self, force: bool = False) -> None:
        """
        Flush metrics to disk (async via background thread).

        ISSUE-12: Uses async batch-flush instead of synchronous writes.

        Args:
            force: If True, always flush regardless of thresholds.
        """
        if self._closed and not force:
            return

        now = datetime.now(UTC)
        if not force:
            elapsed = (now - self._last_flush).total_seconds()
            if elapsed < FLUSH_SECONDS and self._event_count < FLUSH_EVENTS:
                return

        # Expire stale entries
        self._expire_stale_entries()

        # Build metric batch
        metrics: list[dict[str, Any]] = []
        for name in list(self._counter_cache.keys()):
            counter = self._counter_cache[name]
            m: dict[str, Any] = {
                'ts': now.isoformat(),
                'name': name,
                'type': 'counter',
                'value': counter.value,
            }
            if self._correlation:
                m['correlation'] = self._correlation
            metrics.append(m)

        for name, value in list(self._gauge_cache.items()):
            m: dict[str, Any] = {
                'ts': now.isoformat(),
                'name': name,
                'type': 'gauge',
                'value': value,
            }
            if self._correlation:
                m['correlation'] = self._correlation
            metrics.append(m)

        # Add to snapshots ring buffer
        for m in metrics:
            self._snapshots.append(m)

        # ISSUE-12: Async batch-flush via background thread
        if metrics:
            self._flusher.enqueue(metrics)

        self._last_flush = now
        self._event_count = 0
        logger.debug(f'Flushed {len(metrics)} metrics (async)')

    # ── Summary ────────────────────────────────────────────────────────────────

    def get_summary(self) -> dict[str, Any]:
        """
        Get metrics summary — observer ledger truth.

        Returns lightweight state snapshot for debugging and monitoring.
        No execution authority, no policy, no audit chain.
        """
        # Get flusher stats
        flusher_stats = self._flusher.stats if self._flusher else {}

        return {
            'run_id': self._run_id,
            'closed': self._closed,
            'persist_available': self._persist_available,
            'degraded_ram_only': self._persist_available is False,
            'last_persist_failure': self._last_persist_failure,
            'counter_count': len(self._counter_cache),
            'gauge_count': len(self._gauge_cache),
            'snapshot_count': len(self._snapshots),
            'sprint_event_count': len(self._sprint_events),
            'counters': {k: c.value for k, c in self._counter_cache.items()},
            'gauges': dict(self._gauge_cache),
            'flusher': flusher_stats,
            # ISSUE-12: Cache efficiency stats
            'counter_cache_stats': self._counter_cache.stats,
            'gauge_cache_stats': self._gauge_cache.stats,
        }

    # ── Sprint events ────────────────────────────────────────────────────────

    def ingest_sprint_event(self, event: dict[str, object]) -> None:
        """
        Ingest a sprint telemetry event from runtime/telemetry.py.

        Fail-soft: errors are swallowed. No validation required.

        Args:
            event: Dict with keys: session_id, phase, component, event, elapsed_ms, ts
        """
        try:
            if self._closed:
                return
            required = {'session_id', 'phase', 'component', 'event', 'elapsed_ms'}
            if not required.issubset(event.keys()):
                return
            self._sprint_events.append(event)
        except Exception:
            pass

    # ── Bounded gather ────────────────────────────────────────────────────────

    def record_bounded_gather(
        self,
        ctx: str,
        total_tasks: int,
        ok_count: int,
        error_count: int,
        suppressed_count: int,
    ) -> None:
        """
        Record bounded_gather execution stats.

        Aggregates per-context stats into global counters.
        ISSUE-12: Uses LRUCache for bounded storage.
        """
        try:
            if self._closed:
                return

            # Use LRUCache for bounded storage (ISSUE-12)
            for name, delta in [
                ('bounded_gather_tasks_gathered', ok_count),
                ('bounded_gather_tasks_errors', error_count),
                ('bounded_gather_errors_suppressed', suppressed_count),
            ]:
                if delta > 0:
                    self.inc(name, delta)

            self._event_count += 3
            self._maybe_flush()
        except Exception:
            pass

    # ── Fetch coordinator ─────────────────────────────────────────────────────

    def record_fetch_telemetry(self, blocked_domains: int, circuit_open: bool) -> None:
        """Record fetch coordinator telemetry."""
        try:
            if self._closed:
                return
            self.set_gauge('fetch_coordinator_blocked_domains', float(blocked_domains))
            self.set_gauge('fetch_coordinator_circuit_open', 1.0 if circuit_open else 0.0)
        except Exception:
            pass

    # ── Sprint budget ─────────────────────────────────────────────────────────

    def record_sprint_budget(
        self,
        elapsed_ms: float,
        remaining_ms: float,
        phase: str,
        phase_avg_ms: float | None = None,
        phase_p50_ms: float | None = None,
        phase_p95_ms: float | None = None,
    ) -> None:
        """Record sprint budget consumption metrics."""
        try:
            if self._closed:
                return
            self.set_gauge('sprint_budget_elapsed_ms', elapsed_ms)
            self.set_gauge('sprint_budget_remaining_ms', remaining_ms)
            self.set_gauge('sprint_budget_phase', float(hash(phase) % 1000))
            if phase_avg_ms is not None:
                self.set_gauge('sprint_phase_duration_avg_ms', phase_avg_ms)
            if phase_p50_ms is not None:
                self.set_gauge('sprint_phase_duration_p50_ms', phase_p50_ms)
            if phase_p95_ms is not None:
                self.set_gauge('sprint_phase_duration_p95_ms', phase_p95_ms)
        except Exception:
            pass

    # ── ISSUE-12: Pipeline stage stats ───────────────────────────────────────

    def record_stage_timing(
        self,
        stage_name: str,
        latency_ms: float,
        items_in: int = 0,
        items_out: int = 0,
        error: bool = False,
    ) -> None:
        """
        ISSUE-12: Record pipeline stage timing for OtelBridge correlation.

        This wires stage stats to the OTel bridge for live dashboard
        showing "stage latency vs M1 memory pressure".

        Args:
            stage_name: Name of the pipeline stage
            latency_ms: Stage execution time in milliseconds
            items_in: Number of items input to stage
            items_out: Number of items output from stage
            error: Whether stage resulted in an error
        """
        try:
            if self._closed:
                return

            # Record in metrics (bounded)
            self.set_gauge(f'stage_latency_ms_{stage_name}', latency_ms)
            self.set_gauge(f'stage_items_in_{stage_name}', float(items_in))
            self.set_gauge(f'stage_items_out_{stage_name}', float(items_out))
            if error:
                self.inc(f'stage_errors_{stage_name}')

            # Also record to OtelBridge if available (for live dashboard)
            self._record_to_otel_bridge(stage_name, latency_ms, items_in, items_out, error)

        except Exception:
            pass

    def _record_to_otel_bridge(
        self,
        stage_name: str,
        latency_ms: float,
        items_in: int,
        items_out: int,
        error: bool,
    ) -> None:
        """
        ISSUE-12: Wire stage timing to OtelBridge for live dashboard.

        Uses the existing OtelBridge.histogram_record() for latency
        and gauge_set() for throughput correlation with M1 memory pressure.

        FIX-13: Thread-safe - uses bridge's own locking internally.
        """
        try:
            from hledac.universal._core.python_otel_bridge import get_otel_bridge
            bridge = get_otel_bridge()
            if bridge is None:
                return

            # FIX-13: Thread-safe - record_stage_timing uses internal locking
            bridge.record_stage_timing(
                stage_name=stage_name,
                latency_ms=latency_ms,
                items_in=items_in,
                items_out=items_out,
                error=error,
            )

        except ImportError:
            pass
        except Exception:
            pass

    # ── Memory pressure correlation ──────────────────────────────────────────

    def record_memory_pressure(self, pressure: float, available_gib: float, rss_gib: float) -> None:
        """
        ISSUE-12: Record memory pressure for stage latency correlation.

        Called periodically to capture M1 memory pressure alongside
        stage timing for the live dashboard.

        Args:
            pressure: Memory pressure ratio (0.0-1.0)
            available_gib: Available memory in GiB
            rss_gib: RSS memory in GiB
        """
        try:
            if self._closed:
                return
            self.set_gauge('m1_memory_pressure', pressure)
            self.set_gauge('m1_memory_available_gib', available_gib)
            self.set_gauge('m1_memory_rss_gib', rss_gib)

            # FIX-10: Wire to OtelBridge properly via record_memory_pressure
            try:
                from hledac.universal._core.python_otel_bridge import get_otel_bridge
                bridge = get_otel_bridge()
                if bridge is not None:
                    bridge.record_memory_pressure(pressure, available_gib, rss_gib)
            except ImportError:
                pass
            except Exception:
                pass

        except Exception:
            pass

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def close(self) -> None:
        """Close and flush - force=True to prevent tail-loss."""
        if self._closed:
            return
        self._closed = True
        self.flush(force=True)

        # Stop async flusher
        if self._flusher:
            self._flusher.stop()

        logger.info(f'MetricsRegistry closed: run_id={self._run_id}')

    def __enter__(self) -> "MetricsRegistry":
        return self

    def __exit__(self, _exc_type, _exc_val, _exc_tb) -> None:
        self.close()


# ── Module-level API ──────────────────────────────────────────────────────────


def create_metrics_registry(
    run_dir: Path,
    run_id: str = 'default',
    **kwargs,
) -> MetricsRegistry:
    """Create a MetricsRegistry instance."""
    return MetricsRegistry(run_dir=run_dir, run_id=run_id, **kwargs)


_metrics_registry_singleton: MetricsRegistry | None = None


def get_metrics_registry() -> MetricsRegistry:
    """Get or create the module-level singleton MetricsRegistry."""
    global _metrics_registry_singleton
    if _metrics_registry_singleton is None:
        _metrics_registry_singleton = MetricsRegistry(
            run_dir=Path('/tmp/hledac_metrics'),
            run_id='default',
        )
    return _metrics_registry_singleton
