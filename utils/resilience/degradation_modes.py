"""
Degradation Modes - Explicit Degradation State Machine

Defines the degradation state machine from HEALTHY to EMERGENCY,
with clear transitions, severity thresholds, and required actions.

State Machine:
    HEALTHY ──[≥3 HIGH or ≥1 CRITICAL]──► DEGRADED
    DEGRADED ──[≥5 HIGH or ≥2 CRITICAL]──► IO_ONLY
    IO_ONLY ──[≥2 CRITICAL]──► EMERGENCY
    * Any mode can reset to HEALTHY after 60s with no failures

Severity Thresholds:
    - LOW: 1 failure (logged, no state change)
    - MEDIUM: 3 failures (logged, no state change)
    - HIGH: 5 failures OR 1 critical component failure
    - CRITICAL: DuckDB ingest failure, graph failure, export failure

Degradation Actions:
    HEALTHY: Normal operation, all paths active
    DEGRADED: Sidecars may return [], graph operations degraded, log WARNING
    IO_ONLY: Skip MLX inference, skip non-critical sidecars, log ERROR
    EMERGENCY: Propagate all errors, halt acquisition, log CRITICAL
"""
from __future__ import annotations
import asyncio
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional
from collections.abc import Callable
from _core import aclose

class DegradedMode(IntEnum):
    """
    Sprint degradation state. Higher values = more degraded.

    Transitions:
        HEALTHY (0) → DEGRADED (1) → IO_ONLY (2) → EMERGENCY (3)

    Degradation Actions by Mode:
        HEALTHY: Full operation, all paths active
        DEGRADED: Best-effort sidecars, degraded graph ops, WARNING logs
        IO_ONLY: Skip MLX inference, skip non-critical sidecars, ERROR logs
        EMERGENCY: Propagate all errors, halt acquisition, CRITICAL logs
    """
    HEALTHY = 0
    DEGRADED = 1
    IO_ONLY = 2
    EMERGENCY = 3

    @property
    def label(self) -> str:
        return ['✅ HEALTHY', '⚠️ DEGRADED', '🔶 IO_ONLY', '🔴 EMERGENCY'][self.value]

    def should_skip_sidecars(self) -> bool:
        """Sidecars should be skipped in IO_ONLY and EMERGENCY modes."""
        return self.value >= DegradedMode.IO_ONLY.value

    def should_skip_mlx_inference(self) -> bool:
        """MLX inference should be skipped in IO_ONLY and EMERGENCY."""
        return self.value >= DegradedMode.IO_ONLY.value

    def should_propagate_errors(self) -> bool:
        """Errors should propagate (fail-closed) in EMERGENCY."""
        return self.value >= DegradedMode.EMERGENCY.value

    def log_level(self) -> int:
        """Get appropriate log level for this mode."""
        levels = [10, 30, 40, 50]
        return levels[self.value]

@dataclass(frozen=True, slots=True)
class DegradationThresholds:
    """
    Thresholds for degradation mode transitions.

    Defaults are tuned for 30min sprints on M1 8GB.
    Adjust based on sprint duration and hardware constraints.
    """
    high_severity_to_degraded: int = 3
    high_severity_to_io_only: int = 5
    critical_to_degraded: int = 1
    critical_to_io_only: int = 2
    critical_to_emergency: int = 2
    recovery_timeout: float = 60.0
_DEFAULT_THRESHOLDS = DegradationThresholds()

class DegradationState:
    """
    Mutable state for degradation tracking within a sprint.

    Thread-safe for async access via asyncio.Lock.
    """
    __slots__ = ('_failure_counts', '_last_failure_ts', '_lock', '_mode', '_thresholds', '_transition_callbacks')

    def __init__(self, mode: DegradedMode=DegradedMode.HEALTHY, thresholds: Optional[DegradationThresholds]=None) -> None:
        self._mode = mode
        self._thresholds = thresholds or _DEFAULT_THRESHOLDS
        self._failure_counts: dict[str, int] = {'low': 0, 'medium': 0, 'high': 0, 'critical': 0}
        self._last_failure_ts: float = 0.0
        self._transition_callbacks: list[Callable[[DegradedMode, DegradedMode], None]] = []
        self._lock = asyncio.Lock()

    @property
    def mode(self) -> DegradedMode:
        return self._mode

    @property
    def thresholds(self) -> DegradationThresholds:
        return self._thresholds

    def on_transition(self, callback: Callable[[DegradedMode, DegradedMode], None]) -> None:
        """Register a callback to be called on mode transitions."""
        self._transition_callbacks.append(callback)

    async def record_failure(self, severity: FailureSeverity) -> DegradedMode:
        """
        Record a failure and potentially trigger mode transition.

        Returns the (possibly new) degradation mode.
        """
        async with self._lock:
            self._last_failure_ts = time.monotonic()
            sev_key = severity.name.lower()
            self._failure_counts[sev_key] = self._failure_counts.get(sev_key, 0) + 1
            if self._should_recover():
                return await self._transition_to(DegradedMode.HEALTHY)
            return await self._check_escalation()

    def _should_recover(self) -> bool:
        """Check if we should recover to HEALTHY based on timeout."""
        if self._mode == DegradedMode.HEALTHY:
            return False
        elapsed = time.monotonic() - self._last_failure_ts
        return elapsed >= self._thresholds.recovery_timeout

    async def _check_escalation(self) -> DegradedMode:
        """Check if we need to escalate degradation mode."""
        current = self._mode
        high = self._failure_counts.get('high', 0)
        critical = self._failure_counts.get('critical', 0)
        t = self._thresholds
        if current == DegradedMode.HEALTHY:
            if high >= t.high_severity_to_degraded or critical >= t.critical_to_degraded:
                return await self._transition_to(DegradedMode.DEGRADED)
        elif current == DegradedMode.DEGRADED:
            if high >= t.high_severity_to_io_only or critical >= t.critical_to_io_only:
                return await self._transition_to(DegradedMode.IO_ONLY)
        elif current == DegradedMode.IO_ONLY:
            if critical >= t.critical_to_emergency:
                return await self._transition_to(DegradedMode.EMERGENCY)
        return current

    async def _transition_to(self, new_mode: DegradedMode) -> DegradedMode:
        """Execute a mode transition with callbacks."""
        if self._mode == new_mode:
            return new_mode
        old_mode = self._mode
        object.__setattr__(self, '_mode', new_mode)
        for cb in self._transition_callbacks:
            try:
                cb(old_mode, new_mode)
            except Exception:
                pass
        return new_mode

    async def reset(self) -> None:
        """Reset to HEALTHY state."""
        async with self._lock:
            self._failure_counts = {'low': 0, 'medium': 0, 'high': 0, 'critical': 0}
            self._last_failure_ts = 0.0
            object.__setattr__(self, '_mode', DegradedMode.HEALTHY)

    def to_dict(self) -> dict:
        """Serialize state for reporting."""
        return {'mode': self._mode.name, 'mode_label': self._mode.label, 'failure_counts': dict(self._failure_counts), 'last_failure_ts': self._last_failure_ts, 'recovery_available': self._should_recover()}

@dataclass(slots=True)
class ModeTransition:
    """Record of a mode transition event."""
    timestamp: float
    old_mode: DegradedMode
    new_mode: DegradedMode
    trigger_counts: dict[str, int]

def get_degradation_action(mode: DegradedMode, operation: str) -> str:
    """
    Get the recommended action for an operation given current degradation mode.

    Args:
        mode: Current degradation mode
        operation: Operation name (e.g., "duckdb_ingest", "sidecar_x", "mlx_inference")

    Returns:
        Action recommendation: "proceed", "skip", "degraded", "propagate"
    """
    is_critical = SeverityMapper.is_critical_path(operation)
    sidecar_patterns = ('sidecar.', 'sidecar_', 'enrichment.', 'enrichment_', 'fetch.', 'fetch_', 'live_feed.', 'live_feed_')
    is_sidecar = any((operation.startswith(p) for p in sidecar_patterns))
    mlx_patterns = ('mlx_', 'llm_', 'synthesis_', 'inference_')
    is_mlx = any((operation.startswith(p) for p in mlx_patterns))
    if mode == DegradedMode.HEALTHY:
        return 'proceed'
    elif mode == DegradedMode.DEGRADED:
        if is_critical:
            return 'proceed'
        elif is_sidecar:
            return 'degraded'
        return 'proceed'
    elif mode == DegradedMode.IO_ONLY:
        if is_critical:
            return 'proceed'
        elif is_mlx or is_sidecar:
            return 'skip'
        return 'skip'
    elif mode == DegradedMode.EMERGENCY:
        if is_critical:
            return 'propagate'
        return 'skip'
    return 'proceed'

class FailureSeverity(IntEnum):
    """
    Failure severity levels for degradation tracking.

    LOW: Transient failures (network blip, minor timeout) - logged only
    MEDIUM: Recurring non-critical failures - logged, may accumulate
    HIGH: Significant component failure or multiple medium failures
    CRITICAL: Core component failure (DuckDB, Graph, Export, Lifecycle)
    """
    LOW = 0
    MEDIUM = 1
    HIGH = 2
    CRITICAL = 3

    @property
    def label(self) -> str:
        labels = {0: '🔵 LOW', 1: '🟡 MEDIUM', 2: '🟠 HIGH', 3: '🔴 CRITICAL'}
        return labels[self.value]

    def is_critical(self) -> bool:
        """Check if this severity requires immediate action."""
        return self.value >= FailureSeverity.HIGH.value

class SeverityMapper:
    """
    Auto-maps operation scopes to appropriate severity levels.

    Usage:
        severity = SeverityMapper.get_severity("duckdb.ingest")  # Returns CRITICAL
        severity = SeverityMapper.get_severity("sidecar.darknet")  # Returns MEDIUM

    This ensures critical paths always get appropriate severity even when
    decorators are used without explicit severity parameter.
    
    Supports both dot notation ("duckdb.ingest") and underscore notation ("duckdb_ingest").
    """
    CRITICAL_SCOPES: set[str] = {'duckdb.ingest', 'duckdb.export', 'duckdb.query', 'duckdb_ingest', 'duckdb_export', 'duckdb_query', 'export', 'lifecycle_transition', 'lifecycle_transition.shutdown', 'lifecycle_transition.abort'}
    HIGH_SCOPES: set[str] = {'graph', 'graph.operations', 'graph_service', 'llm_synthesis', 'mlx_inference'}
    SIDEKAR_PATTERNS: tuple[str, ...] = ('sidecar.', 'sidecar_', 'enrichment.', 'enrichment_', 'fetch.', 'fetch_', 'live_feed.', 'live_feed_')

    @classmethod
    def get_severity(cls, scope: str, explicit_severity: int | None=None) -> FailureSeverity:
        """
        Get severity for a scope, with explicit override taking precedence.

        Args:
            scope: dot-namespaced or underscore scope (e.g., "duckdb.ingest", "duckdb_ingest")
            explicit_severity: override from decorator parameter (default: None)

        Returns:
            FailureSeverity appropriate for the scope
        """
        if explicit_severity is not None:
            try:
                return FailureSeverity(explicit_severity)
            except ValueError:
                pass
        if scope in cls.CRITICAL_SCOPES:
            return FailureSeverity.CRITICAL
        if scope in cls.HIGH_SCOPES:
            return FailureSeverity.HIGH
        for pattern in cls.SIDEKAR_PATTERNS:
            if scope.startswith(pattern):
                return FailureSeverity.MEDIUM
        return FailureSeverity.MEDIUM

    @classmethod
    def is_critical_path(cls, scope: str) -> bool:
        """Check if scope represents a critical infrastructure path."""
        return scope in cls.CRITICAL_SCOPES

    @classmethod
    def should_propagate_in_emergency(cls, scope: str) -> bool:
        """Check if errors should propagate in EMERGENCY mode for this scope."""
        return scope in cls.CRITICAL_SCOPES