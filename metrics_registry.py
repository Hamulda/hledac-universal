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
import json
import logging
import os
from collections import deque
try:
    import orjson as _orjson
    _ORJSON_AVAILABLE = True
except ImportError:
    _orjson = None
    _ORJSON_AVAILABLE = False
try:
    import psutil
    _PSUTIL_AVAILABLE = True
except ImportError:
    psutil = None
    _PSUTIL_AVAILABLE = False
from dataclasses import dataclass
import msgspec
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from hledac.universal.utils._patterns import safe_close  # F320: DRY close helper
logger = logging.getLogger(__name__)
METRIC_NAMES = frozenset(['orchestrator_rss_mb', 'orchestrator_frontier_size', 'orchestrator_evidence_ring_len', 'orchestrator_tool_exec_events', 'orchestrator_budget_remaining_tokens', 'orchestrator_budget_remaining_time', 'orchestrator_budget_remaining_api_calls', 'cache_http_size', 'cache_snapshot_size', 'cache_frontier_size', 'memory_open_fds', 'memory_rss_mb', 'memory_vms_mb', 'mlx_cache_hits', 'mlx_cache_misses', 'mlx_cache_size_bytes', 'mlx_active_memory_bytes', 'mlx_peak_memory_bytes', 'mlx_cache_fragmentation_ratio', 'mlx_kernel_compilation_time_ms', 'mlx_kernel_cache_hit_rate', 'model_load_duration_ms', 'model_unload_count', 'model_load_failures', 'action_latency_ms', 'thermal_throttle_events', 'thermal_recovery_events', 'memory_zone_normal_seconds', 'memory_zone_high_seconds', 'circuit_breaker_state_transitions', 'circuit_breaker_open_count', 'circuit_breaker_half_open_count', 'circuit_breaker_closed_count', 'circuit_breaker_recovery_success', 'circuit_breaker_open_duration_s', 'circuit_breaker_closed_duration_s', 'memory_zone_critical_seconds', 'dark_surface_pivots_attempted', 'dark_surface_pivots_successful', 'cover_traffic_fired', 'alert_warning_circuit_breaker_open_over_30s', 'memory_pressure_vs_finding_yield', 'windup_entry_count', 'sprint_budget_elapsed_ms', 'sprint_budget_remaining_ms', 'sprint_budget_phase', 'sprint_phase_duration_avg_ms', 'sprint_phase_duration_p50_ms', 'sprint_phase_duration_p95_ms', 'duckdb_ingest_latency_ms', 'duckdb_query_latency_ms', 'bounded_gather_tasks_gathered', 'bounded_gather_tasks_errors', 'bounded_gather_errors_suppressed', 'memory_layer_pressure_pct', 'fetch_coordinator_active', 'fetch_coordinator_blocked_domains', 'fetch_coordinator_circuit_open'])

class MetricSnapshot(msgspec.Struct, gc=False):
    """A single metric snapshot"""
    ts: datetime
    name: str
    value: float
    labels: dict[str, str] | None = None
    correlation: dict[str, str | None] | None = None

_GRAMMAR_KEYS = frozenset(['run_id', 'branch_id', 'provider_id', 'action_id'])


class MetricsRegistry:
    """
    Lightweight metrics registry with disk flush.

    Design:
    - In-memory counters/gauges (tiny)
    - Periodic flush to disk JSONL
    - Ring buffer for recent snapshots (maxlen)
    - No raw strings or large payloads
    """
    FLUSH_EVENTS = 100
    FLUSH_SECONDS = 60
    # Bounded ring buffer — MUST remain <= 1024 to cap _snapshots memory at ~100KB.
    # M1 8GB: 100 snapshots × ~200 bytes × 2 planes = ~40KB max, well under budget.
    # Guard: assert in __init__ prevents unbounded growth if subclassed/overridden.
    MAX_SNAPSHOTS: int = 100
    __slots__ = tuple(('_closed', '_correlation', '_counters', '_event_count', '_gauges', '_last_flush', '_last_persist_failure', '_persist_available', '_persist_file', '_run_dir', '_run_id', '_snapshots', '_sprint_events'))

    def __init__(self, run_dir: Path, run_id: str='default', correlation: dict[str, str | None] | None=None):
        """
        Initialize metrics registry.

        Args:
            run_dir: Directory for metrics JSONL
            run_id: Run identifier
            correlation: Optional correlation dict with keys:
                branch_id, provider_id, action_id
                (run_id is taken from run_id parameter)
        """
        # B3 guard: MAX_SNAPSHOTS must be bounded to prevent unbounded _snapshots growth.
        # Subclass override to None/unbounded value would cause memory leak on M1 8GB.
        assert isinstance(self.MAX_SNAPSHOTS, int) and self.MAX_SNAPSHOTS <= 1024, (
            f'MAX_SNAPSHOTS must be int <= 1024, got {self.MAX_SNAPSHOTS!r}'
        )
        self._run_dir = run_dir
        self._run_id = run_id
        if correlation is None:
            self._correlation = {'run_id': run_id}
        else:
            self._correlation = {k: correlation.get(k) for k in _GRAMMAR_KEYS}
            self._correlation['run_id'] = run_id
        self._last_flush = datetime.now(UTC)
        self._counters: dict[str, int] = {}
        self._gauges: dict[str, float] = {}
        self._snapshots: deque = deque(maxlen=self.MAX_SNAPSHOTS)
        self._event_count = 0
        self._sprint_events: deque = deque(maxlen=100)
        self._closed = False
        self._persist_available = True
        self._last_persist_failure: str | None = None
        self._persist_file = self._init_persist_file()
        self._persist_available = self._persist_file is not None
        logger.info(f'MetricsRegistry initialized: run_id={run_id}')

    def _init_persist_file(self) -> Any | None:
        """Initialize persistence file. Fail-soft: returns None on failure, caller tracks degraded state."""
        metrics_dir = self._run_dir / 'logs'
        metrics_dir.mkdir(parents=True, exist_ok=True)
        metrics_file = metrics_dir / 'metrics.jsonl'
        try:
            return open(metrics_file, 'ab')
        except Exception as e:
            self._last_persist_failure = str(e)
            logger.warning(f'Failed to open metrics file: {e}')
            return None

    def _validate_metric_name(self, name: str) -> bool:
        """Validate metric name is in bounded set (exact match only — no arbitrary prefixes)"""
        return name in METRIC_NAMES

    def inc(self, name: str, delta: int=1) -> None:
        """
        Increment a counter.

        Args:
            name: Metric name
            delta: Amount to increment
        """
        if self._closed:
            return
        if not self._validate_metric_name(name):
            logger.warning(f'Invalid metric name: {name}')
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
            logger.warning(f'Invalid metric name: {name}')
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
        try:
            process = psutil.Process(os.getpid())
            mem_info = process.memory_info()
            self.set_gauge('memory_rss_mb', mem_info.rss / (1024 * 1024))
            self.set_gauge('memory_vms_mb', mem_info.vms / (1024 * 1024))
            self.set_gauge('memory_open_fds', process.num_fds())
        except Exception:  # noqa: BLE001
            pass

    def flush(self, force: bool=False) -> None:
        """
        Flush metrics to disk.

        Args:
            force: If True, always flush regardless of time/event thresholds.
        """
        if getattr(self, '_closed', False) and (not force):
            return
        now = datetime.now(UTC)
        if not force:
            elapsed = (now - self._last_flush).total_seconds()
            if elapsed < self.FLUSH_SECONDS and self._event_count < self.FLUSH_EVENTS:
                return
        metrics = []
        for name, value in self._counters.items():
            m: dict[str, Any] = {'ts': now.isoformat(), 'name': name, 'type': 'counter', 'value': value}
            if self._correlation:
                m['correlation'] = self._correlation
            metrics.append(m)
        for name, value in self._gauges.items():
            m: dict[str, Any] = {'ts': now.isoformat(), 'name': name, 'type': 'gauge', 'value': value}
            if self._correlation:
                m['correlation'] = self._correlation
            metrics.append(m)
        for m in metrics:
            self._snapshots.append(m)
        if self._persist_file:
            try:
                for m in metrics:
                    if _ORJSON_AVAILABLE and _orjson is not None:
                        line = _orjson.dumps(m, option=_orjson.OPT_APPEND_NEWLINE)
                        self._persist_file.write(line)
                    else:
                        line = json.dumps(m)
                        self._persist_file.write(line.encode('utf-8') + b'\n')
                self._persist_file.flush()
                os.fsync(self._persist_file.fileno())
            except Exception as e:
                logger.error(f'Failed to flush metrics: {e}')
        self._last_flush = now
        self._event_count = 0
        logger.debug(f'Flushed {len(metrics)} metrics to disk')

    def get_summary(self) -> dict[str, Any]:
        """
        Get metrics summary — observer ledger truth.

        Returns lightweight state snapshot for debugging and monitoring.
        No execution authority, no policy, no audit chain.
        """
        # Single NymTransport lookup per get_summary() call to avoid double-init
        nym_address: str | None = None
        nym_circuit_open: bool = False
        try:
            from hledac.universal.transport.nym_transport import NymTransport
            nym = NymTransport()
            nym_address = getattr(nym, 'nym_address', None)
            nym_circuit_open = getattr(nym, 'circuit_breaker_open', False)
        except Exception:  # noqa: BLE001
            pass
        return {'run_id': self._run_id, 'closed': self._closed, 'persist_available': getattr(self, '_persist_available', None), 'degraded_ram_only': getattr(self, '_persist_available', True) is False, 'last_persist_failure': getattr(self, '_last_persist_failure', None), 'counter_count': len(self._counters), 'gauge_count': len(self._gauges), 'snapshot_count': len(self._snapshots), 'sprint_event_count': len(self._sprint_events), 'counters': dict(self._counters), 'gauges': dict(self._gauges), 'nym_address': nym_address, 'nym_circuit_open': nym_circuit_open}

    def ingest_sprint_event(self, event: dict[str, object]) -> None:
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
            required = {'session_id', 'phase', 'component', 'event', 'elapsed_ms'}
            if not required.issubset(event.keys()):
                return
            self._sprint_events.append(event)
        except Exception:  # noqa: BLE001
            pass

    def record_bounded_gather(self, ctx: str, total_tasks: int, ok_count: int, error_count: int, suppressed_count: int) -> None:
        """
        Record bounded_gather execution stats.

        Aggregates per-context stats into global counters for dashboard visibility.
        Fail-soft: all errors swallowed.

        Args:
            ctx: Context label (e.g. "discovery.sources", "pattern.scan")
            total_tasks: Total number of tasks submitted
            ok_count: Successful results count
            error_count: Exception count (not suppressed)
            suppressed_count: Suppressed exception count (logged to DEBUG)
        """
        try:
            if self._closed:
                return
            self._counters['bounded_gather_tasks_gathered'] = self._counters.get('bounded_gather_tasks_gathered', 0) + ok_count
            self._counters['bounded_gather_tasks_errors'] = self._counters.get('bounded_gather_tasks_errors', 0) + error_count
            self._counters['bounded_gather_errors_suppressed'] = self._counters.get('bounded_gather_errors_suppressed', 0) + suppressed_count
            self._event_count += 3
            self._maybe_flush()
        except Exception:  # noqa: BLE001
            pass

    def record_fetch_telemetry(self, blocked_domains: int, circuit_open: bool) -> None:
        """
        Record fetch coordinator telemetry.

        Args:
            blocked_domains: Number of currently blocked domains
            circuit_open: True if any circuit breaker is open
        """
        try:
            if self._closed:
                return
            self._gauges['fetch_coordinator_blocked_domains'] = float(blocked_domains)
            self._gauges['fetch_coordinator_circuit_open'] = 1.0 if circuit_open else 0.0
            self._event_count += 2
            self._maybe_flush()
        except Exception:  # noqa: BLE001
            pass

    def record_sprint_budget(self, elapsed_ms: float, remaining_ms: float, phase: str, phase_avg_ms: float | None=None, phase_p50_ms: float | None=None, phase_p95_ms: float | None=None) -> None:
        """
        Record sprint budget consumption metrics.

        Args:
            elapsed_ms: Sprint elapsed time in milliseconds
            remaining_ms: Remaining sprint time in milliseconds
            phase: Current sprint phase name
            phase_avg_ms: Average phase duration
            phase_p50_ms: Median phase duration
            phase_p95_ms: 95th percentile phase duration
        """
        try:
            if self._closed:
                return
            self._gauges['sprint_budget_elapsed_ms'] = elapsed_ms
            self._gauges['sprint_budget_remaining_ms'] = remaining_ms
            self._gauges['sprint_budget_phase'] = float(hash(phase) % 1000)
            if phase_avg_ms is not None:
                self._gauges['sprint_phase_duration_avg_ms'] = phase_avg_ms
            if phase_p50_ms is not None:
                self._gauges['sprint_phase_duration_p50_ms'] = phase_p50_ms
            if phase_p95_ms is not None:
                self._gauges['sprint_phase_duration_p95_ms'] = phase_p95_ms
            self._event_count += 1
            self._maybe_flush()
        except Exception:  # noqa: BLE001
            pass

    def close(self) -> None:
        """Close and flush - force=True to prevent tail-loss of pending metrics.
        F320: Refactored to use safe_close helper."""
        if self._closed:
            return
        self._closed = True
        self.flush(force=True)
        # F320: Use safe_close for DRY error handling
        safe_close(self._persist_file, logger=logger, context="Metrics")
        self._persist_file = None

    def __enter__(self) -> MetricsRegistry:
        return self

    def __exit__(self, _exc_type, _exc_val, _exc_tb) -> None:
        self.close()

def create_metrics_registry(run_dir: Path, run_id: str='default') -> MetricsRegistry:
    """Create a MetricsRegistry instance"""
    return MetricsRegistry(run_dir=run_dir, run_id=run_id)
_metrics_registry_singleton: MetricsRegistry | None = None

def get_metrics_registry() -> MetricsRegistry:
    """Get or create the module-level singleton MetricsRegistry.

    For use by circuit breaker and other components that need
    fire-and-forget metrics without managing lifecycle.
    """
    global _metrics_registry_singleton
    if _metrics_registry_singleton is None:
        _metrics_registry_singleton = MetricsRegistry(run_dir=Path('/tmp/hledac_metrics'), run_id='default')
    return _metrics_registry_singleton