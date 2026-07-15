"""
Alerting Infrastructure for Anti-Pattern Detection.

Sprint F-Alert — 2026-06-29
Monitors 4 critical anti-patterns:
1. Sprint with 0 findings after 60s
2. DuckDB lock contention > 5/sec
3. Circuit breaker open > 30s
4. Memory delta > 1GB/sprint

Always-on, bounded, fail-safe. M1 8GB UMA safe.
"""
import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from collections.abc import Callable
import psutil
from metrics_registry import get_metrics_registry
logger = logging.getLogger(__name__)

class AlertSeverity(Enum):
    WARNING = 'warning'
    CRITICAL = 'critical'
    INFO = 'info'

@dataclass(frozen=True, slots=True)
class Alert:
    """Structured alert — immutable, hashable."""
    alert_id: str
    severity: AlertSeverity
    message: str
    metric_value: float
    threshold: float
    timestamp: float = field(default_factory=time.time)
    tags: tuple[str, ...] = ()

    def __hash__(self) -> int:
        return hash(self.alert_id)
_ALERT_REGISTRY: dict[str, float] = {}
_ALERT_REGISTRY_MAX_AGE_S = 300.0

def _should_fire_alert(alert_id: str, cooldown_s: float=60.0) -> bool:
    """Deduplication: fire only if no similar alert in cooldown window."""
    now = time.time()
    last_fired = _ALERT_REGISTRY.get(alert_id, 0.0)
    if now - last_fired < cooldown_s:
        return False
    _ALERT_REGISTRY[alert_id] = now
    return True

class AlertManager:
    """
    Centralized alerting for sprint anti-patterns.

    Bounded: max 1000 alerts in memory, oldest evicted.
    Fail-safe: all operations wrapped in try/except.
    """
    MAX_ALERTS = 1000
    __slots__ = tuple(('_alerts', '_handlers', '_lock', '_metrics'))

    def __init__(self) -> None:
        self._alerts: deque[Alert] = deque(maxlen=self.MAX_ALERTS)
        self._handlers: list[Callable[[Alert], None]] = []
        self._lock: asyncio.Lock | None = None
        try:
            self._metrics = get_metrics_registry()
        except Exception:
            self._metrics = None

    def _get_lock(self) -> asyncio.Lock:
        """ISSUE-014 FIX: Lazily create lock in the current event loop."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def register_handler(self, handler: Callable[[Alert], None]) -> None:
        """Register an alert handler (e.g., dashboard, webhook)."""
        self._handlers.append(handler)

    async def emit(self, alert_id: str, severity: AlertSeverity, message: str, metric_value: float, threshold: float, cooldown_s: float=60.0, tags: tuple[str, ...]=()) -> None:
        """Emit an alert if not deduplicated."""
        if not _should_fire_alert(alert_id, cooldown_s):
            return
        alert = Alert(alert_id=alert_id, severity=severity, message=message, metric_value=metric_value, threshold=threshold, tags=tags)
        async with self._get_lock():
            self._alerts.append(alert)
        if self._metrics:
            try:
                self._metrics.inc(f'alert_{severity.value}_{alert_id}')
            except Exception:
                pass
        for handler in self._handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    asyncio.get_running_loop().create_task(handler(alert))
                else:
                    handler(alert)
            except Exception as e:
                logger.debug('Alert handler error: %s', e)
        logger.log(logging.WARNING if severity == AlertSeverity.CRITICAL else logging.INFO, '[ALERT] %s | %s | value=%.2f threshold=%.2f | %s', alert_id, severity.value.upper(), metric_value, threshold, message)

    def get_recent_alerts(self, limit: int=50) -> list[Alert]:
        """Get recent alerts (newest last)."""
        alerts = list(self._alerts)
        return alerts[-limit:]

    def clear(self) -> None:
        """Clear all alerts."""
        self._alerts.clear()
_alert_manager: AlertManager | None = None

def get_alert_manager() -> AlertManager:
    """Get or create global AlertManager."""
    global _alert_manager
    if _alert_manager is None:
        _alert_manager = AlertManager()
    return _alert_manager
_ZERO_FINDINGS_ALERT_COOLDOWN_S = 120.0

async def check_zero_findings_alert(elapsed_s: float, consecutive_empty_cycles: int, total_findings: int) -> None:
    """
    Alert if sprint has 0 findings after 60s wall-clock.

    Threshold: 60s elapsed AND 0 findings
    Cooldown: 120s (to avoid spam during investigation)
    """
    am = get_alert_manager()
    if elapsed_s >= 60.0 and total_findings == 0 and (consecutive_empty_cycles >= 2):
        await am.emit(alert_id='zero_findings_after_60s', severity=AlertSeverity.WARNING, message=f'Sprint running {elapsed_s:.0f}s with 0 findings ({consecutive_empty_cycles} empty cycles)', metric_value=float(consecutive_empty_cycles), threshold=2.0, cooldown_s=_ZERO_FINDINGS_ALERT_COOLDOWN_S, tags=('sprint', 'empty', 'investigation_required'))
_LOCK_CONTENTION_WINDOW_S = 1.0
_LOCK_CONTENTION_ALERT_COOLDOWN_S = 60.0

class LockContentionTracker:
    """
    Tracks DuckDB lock acquisition attempts per second.

    Bounded: uses sliding window, max 100 samples.
    Thread-safe for async use.
    """
    __slots__ = tuple(('_samples', '_total_attempts', '_total_failures'))

    def __init__(self) -> None:
        self._samples: deque[tuple[float, int]] = deque(maxlen=100)
        self._total_attempts: int = 0
        self._total_failures: int = 0

    def record_attempt(self, acquired: bool) -> None:
        """Record a lock acquisition attempt."""
        now = time.monotonic()
        self._samples.append((now, 1 if acquired else 0))
        self._total_attempts += 1
        if not acquired:
            self._total_failures += 1

    def get_contention_rate(self) -> float:
        """
        Get lock contention rate per second (failures/sec).

        Uses sliding window of _LOCK_CONTENTION_WINDOW_S seconds.
        """
        now = time.monotonic()
        cutoff = now - _LOCK_CONTENTION_WINDOW_S
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()
        if not self._samples:
            return 0.0
        failures = sum((1 for _, acquired in self._samples if not acquired))
        window_duration = now - self._samples[0][0] if self._samples else 1.0
        return failures / max(window_duration, 0.1)

    def reset(self) -> None:
        """Reset all counters."""
        self._samples.clear()
        self._total_attempts = 0
        self._total_failures = 0

async def check_lock_contention_alert(tracker: LockContentionTracker) -> None:
    """
    Alert if DuckDB lock contention > 5 failures/sec.

    Threshold: 5.0 failures/sec
    Cooldown: 60s
    """
    rate = tracker.get_contention_rate()
    if rate <= 5.0:
        return
    am = get_alert_manager()
    await am.emit(alert_id='duckdb_lock_contention_high', severity=AlertSeverity.CRITICAL, message=f'DuckDB lock contention at {rate:.1f} failures/sec (>{5.0}/sec threshold) — consider scaling to subprocess', metric_value=rate, threshold=5.0, cooldown_s=_LOCK_CONTENTION_ALERT_COOLDOWN_S, tags=('duckdb', 'lock', 'contention', 'subprocess'))
_CB_OPEN_ALERT_COOLDOWN_S = 60.0
_cb_open_since: dict[str, float] = {}
_cb_open_since_lock = asyncio.Lock()

async def check_circuit_breaker_alert(domain: str, is_open: bool, recovery_timeout: float) -> None:
    """
    Alert if circuit breaker has been open > 30% of recovery_timeout.

    F290-FIX: Fixed 30s threshold was too aggressive for domains with high
    recovery_timeout (e.g. crt.sh at 120s). Now uses dynamic threshold:
    alert_threshold = max(10.0, recovery_timeout * 0.3).
    This ensures alerts fire at 30% into recovery, not at a fixed 30s regardless
    of the actual timeout — prevents alert spam for slow-but-working CT servers.
    Cooldown: 60s per domain.
    """
    global _cb_open_since
    _alert_threshold = max(10.0, recovery_timeout * 0.3)
    now = time.monotonic()
    async with _cb_open_since_lock:
        if is_open:
            if domain not in _cb_open_since:
                _cb_open_since[domain] = now
            open_duration = now - _cb_open_since[domain]
            if open_duration >= _alert_threshold:
                _alert_id = 'circuit_breaker_open_over_30s'
                _alert_severity = AlertSeverity.WARNING
                _alert_message = f"Circuit breaker for '{domain}' open for {open_duration:.0f}s (recovery_timeout={recovery_timeout:.0f}s, threshold={_alert_threshold:.0f}s) — provider issues"
                _alert_metric = open_duration
                _alert_cooldown = _CB_OPEN_ALERT_COOLDOWN_S
                _alert_tags: tuple[str, ...] = ('circuit_breaker', 'provider', domain)
            else:
                _alert_id = ''
                _alert_severity = AlertSeverity.INFO
                _alert_message = ''
                _alert_metric = 0.0
                _alert_threshold = 0.0
                _alert_cooldown = 0.0
                _alert_tags = ()
        else:
            _cb_open_since.pop(domain, None)
            _alert_id = ''
            _alert_severity = AlertSeverity.INFO
            _alert_message = ''
            _alert_metric = 0.0
            _alert_threshold = 0.0
            _alert_cooldown = 0.0
            _alert_tags = ()
    if _alert_id:
        am = get_alert_manager()
        await am.emit(alert_id=_alert_id, severity=_alert_severity, message=_alert_message, metric_value=_alert_metric, threshold=_alert_threshold, cooldown_s=_alert_cooldown, tags=_alert_tags)

def reset_circuit_breaker_tracking() -> None:
    """Reset all circuit breaker open tracking (called at sprint start)."""
    global _cb_open_since
    _cb_open_since.clear()
_MEMORY_DELTA_ALERT_COOLDOWN_S = 180.0

class MemoryDeltaTracker:
    """
    Tracks per-sprint memory delta.

    Records RSS at sprint start and computes delta at sprint end.
    Uses psutil for RSS measurement.
    """
    __slots__ = tuple(('_peak_rss_during_sprint', '_sprint_start_rss_mb', '_sprint_start_time'))

    def __init__(self) -> None:
        self._sprint_start_rss_mb: float = 0.0
        self._sprint_start_time: float = 0.0
        self._peak_rss_during_sprint: float = 0.0

    def sprint_start(self) -> None:
        """Record sprint start metrics."""
        try:
            process = psutil.Process()
            self._sprint_start_rss_mb = process.memory_info().rss / (1024 * 1024)
            self._sprint_start_time = time.monotonic()
            self._peak_rss_during_sprint = self._sprint_start_rss_mb
        except Exception:
            self._sprint_start_rss_mb = 0.0
            self._sprint_start_time = time.monotonic()
            self._peak_rss_during_sprint = 0.0

    def record_rss(self, current_rss_mb: float) -> None:
        """Record current RSS (call periodically during sprint)."""
        if current_rss_mb > self._peak_rss_during_sprint:
            self._peak_rss_during_sprint = current_rss_mb

    def get_delta_gb(self) -> float:
        """Get memory delta in GB (peak - start)."""
        return (self._peak_rss_during_sprint - self._sprint_start_rss_mb) / 1024.0

    def get_peak_gb(self) -> float:
        """Get peak RSS in GB during sprint."""
        return self._peak_rss_during_sprint / 1024.0

async def check_memory_delta_alert(tracker: MemoryDeltaTracker, current_rss_mb: float) -> None:
    """
    Alert if memory delta > 1GB during sprint.

    Threshold: 1.0 GB delta from sprint start
    Cooldown: 180s (per sprint)
    """
    tracker.record_rss(current_rss_mb)
    delta_gb = tracker.get_delta_gb()
    if delta_gb <= 1.0:
        return
    am = get_alert_manager()
    await am.emit(alert_id='memory_delta_over_1gb', severity=AlertSeverity.CRITICAL, message=f'Memory delta {delta_gb:.2f} GB > 1 GB threshold (possible memory leak) — investigate during winddown', metric_value=delta_gb, threshold=1.0, cooldown_s=_MEMORY_DELTA_ALERT_COOLDOWN_S, tags=('memory', 'leak', 'sprint'))
_lock_tracker: LockContentionTracker | None = None

def get_lock_contention_tracker() -> LockContentionTracker:
    """Get or create global lock contention tracker."""
    global _lock_tracker
    if _lock_tracker is None:
        _lock_tracker = LockContentionTracker()
    return _lock_tracker
_memory_tracker: MemoryDeltaTracker | None = None

def get_memory_delta_tracker() -> MemoryDeltaTracker:
    """Get or create global memory delta tracker."""
    global _memory_tracker
    if _memory_tracker is None:
        _memory_tracker = MemoryDeltaTracker()
    return _memory_tracker