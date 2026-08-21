"""
Failure Registry & Sprint Health Ledger

Provides centralized failure tracking with:
- Per-sprint failure registry with severity classification
- Degradation mode state machine
- Orchestrator-visible health status
- Failure aggregation and reporting

Architecture:
    SprintHealthLedger (global singleton per sprint)
        └── FailureRegistry (per component)
              └── FailureEntry[] (individual failures)

Usage:
    ledger = SprintHealthLedger.start_sprint(sprint_id="sprint_001")

    # Record failure
    FailureRegistry.record(
        component="duckdb_ingest",
        severity=FailureSeverity.HIGH,
        error=exc,
        context={"batch_id": "batch_42"}
    )

    # Query health
    health = ledger.get_health_summary()
    mode = ledger.degradation_state.mode
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from hledac.universal.utils.asyncx import safe_create_task
from hledac.universal.utils.resilience.degradation_modes import (
    DegradationState,
    DegradationThresholds,
    DegradedMode,
    FailureSeverity,
    ModeTransition,
    get_degradation_action,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FailureEntry:
    """
    Immutable record of a single failure event.

    Stored in FailureRegistry for post-sprint analysis.
    """

    id: str
    timestamp: float
    component: str
    severity: FailureSeverity
    error_type: str
    error_message: str
    stack_trace: str | None
    context: dict[str, Any]
    failure_path: tuple[str, ...]

    @classmethod
    def create(
        cls,
        component: str,
        severity: FailureSeverity,
        error: BaseException,
        context: dict[str, Any] | None = None,
        failure_path: tuple[str, ...] | None = None,
    ) -> FailureEntry:
        """Factory to create a FailureEntry from an exception."""
        import traceback

        return cls(
            id=str(uuid.uuid7())[:8],
            timestamp=time.monotonic(),
            component=component,
            severity=severity,
            error_type=type(error).__name__,
            error_message=str(error),
            stack_trace=traceback.format_exc(limit=10) if logger.isEnabledFor(logging.DEBUG) else None,
            context=context or {},
            failure_path=failure_path or (),
        )


@dataclass(slots=True)
class ComponentHealth:
    """Health status for a single component."""

    name: str
    total_failures: int = 0
    last_failure_ts: float = 0.0
    last_error_type: str | None = None
    last_error_msg: str | None = None
    is_circuit_open: bool = False
    severity_counts: dict[FailureSeverity, int] = field(default_factory=lambda: dict.fromkeys(FailureSeverity, 0))

    def record_failure(self, entry: FailureEntry) -> None:
        """Record a failure entry against this component."""
        self.total_failures += 1
        self.last_failure_ts = entry.timestamp
        self.last_error_type = entry.error_type
        self.last_error_msg = entry.error_message
        self.severity_counts[entry.severity] = self.severity_counts.get(entry.severity, 0) + 1


class FailureRegistry:
    """
    Per-sprint failure registry with component-level tracking.

    Thread-safe for async access via asyncio.Lock.
    """

    __slots__ = ("_components", "_failures", "_lock", "_on_failure_callbacks", "sprint_id")

    def __init__(self, sprint_id: str) -> None:
        self.sprint_id = sprint_id
        self._failures: list[FailureEntry] = []
        self._components: dict[str, ComponentHealth] = {}
        self._lock = asyncio.Lock()
        self._on_failure_callbacks: list[callable] = []

    def on_failure(self, callback: callable) -> None:
        """Register callback to be called on each failure record."""
        self._on_failure_callbacks.append(callback)

    async def record(
        self,
        component: str,
        severity: FailureSeverity,
        error: BaseException,
        context: dict[str, Any] | None = None,
        failure_path: tuple[str, ...] | None = None,
    ) -> FailureEntry:
        """
        Record a failure event.

        This is the primary API for recording failures.
        Creates a FailureEntry, updates component health, and triggers callbacks.
        """
        entry = FailureEntry.create(
            component=component, severity=severity, error=error, context=context, failure_path=failure_path
        )
        async with self._lock:
            self._failures.append(entry)
            if component not in self._components:
                self._components[component] = ComponentHealth(name=component)
            self._components[component].record_failure(entry)
        for cb in self._on_failure_callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(entry)
                else:
                    cb(entry)
            except Exception:
                pass
        log_level = [logging.DEBUG, logging.DEBUG, logging.WARNING, logging.ERROR][severity.value]
        logger.log(
            log_level,
            "[%s] %s failure in %s: %s - %s",
            self.sprint_id,
            severity.label,
            component,
            entry.error_type,
            entry.error_message,
        )
        return entry

    async def get_component_health(self, component: str) -> ComponentHealth | None:
        """Get health status for a specific component."""
        async with self._lock:
            return self._components.get(component)

    async def get_all_components(self) -> dict[str, ComponentHealth]:
        """Get health status for all components."""
        async with self._lock:
            return dict(self._components)

    async def get_failures(
        self, component: str | None = None, min_severity: FailureSeverity | None = None, limit: int = 100
    ) -> list[FailureEntry]:
        """Query failures with optional filters."""
        async with self._lock:
            failures = self._failures
            if component:
                failures = [f for f in failures if f.component == component]
            if min_severity:
                failures = [f for f in failures if f.severity.value >= min_severity.value]
            return failures[-limit:]

    async def get_summary(self) -> dict[str, Any]:
        """Get registry summary for reporting."""
        async with self._lock:
            high_critical = [f for f in self._failures if f.severity.value >= FailureSeverity.HIGH.value]
            return {
                "sprint_id": self.sprint_id,
                "total_failures": len(self._failures),
                "high_critical_count": len(high_critical),
                "components_affected": len(self._components),
                "component_details": {
                    name: {
                        "total": h.total_failures,
                        "last_failure_ts": h.last_failure_ts,
                        "last_error": h.last_error_type,
                        "high_critical": sum(
                            h.severity_counts[s] for s in [FailureSeverity.HIGH, FailureSeverity.CRITICAL]
                        ),
                    }
                    for name, h in self._components.items()
                },
            }


class SprintHealthLedger:
    """
    Central orchestrator for sprint health state.

    Maintains:
    - FailureRegistry per sprint
    - DegradationState with mode transitions
    - Transition history
    - Health summary for reporting

    Usage:
        ledger = SprintHealthLedger.start_sprint("sprint_001")
        # ... during sprint ...
        health = ledger.get_health_summary()
        mode = ledger.degradation_state.mode
        ledger.end_sprint()
    """

    _current: SprintHealthLedger | None = None
    _lock = asyncio.Lock()
    __slots__ = ("_active", "_degradation_state", "_registry", "_transitions", "end_time", "sprint_id", "start_time")

    def __init__(self, sprint_id: str, thresholds: DegradationThresholds | None = None) -> None:
        self.sprint_id = sprint_id
        self.start_time = time.monotonic()
        self.end_time: float | None = None
        self._registry = FailureRegistry(sprint_id)
        self._degradation_state = DegradationState(mode=DegradedMode.HEALTHY, thresholds=thresholds)
        self._transitions: list[ModeTransition] = []
        self._active = True
        self._degradation_state.on_transition(self._on_mode_transition)
        self._registry.on_failure(self._on_failure)

    @classmethod
    async def start_sprint(cls, sprint_id: str, thresholds: DegradationThresholds | None = None) -> SprintHealthLedger:
        """Start a new sprint with health tracking."""
        async with cls._lock:
            if cls._current is not None:
                await cls._current.end_sprint()
            cls._current = cls(sprint_id, thresholds)
            logger.info("[%s] Sprint health ledger started", sprint_id)
            return cls._current

    @classmethod
    def start_sprint_sync(cls, sprint_id: str, thresholds: DegradationThresholds | None = None) -> SprintHealthLedger:
        """Synchronous version for non-async contexts."""
        if cls._current is not None and cls._current._active:
            cls._current.end_sprint_sync()
        cls._current = cls(sprint_id, thresholds)
        logger.info("[%s] Sprint health ledger started", sprint_id)
        return cls._current

    @classmethod
    def get_current(cls) -> SprintHealthLedger | None:
        """Get the current active sprint ledger."""
        return cls._current

    @classmethod
    def get_ledger(cls) -> SprintHealthLedger:
        """Get current ledger, raising if none active."""
        if cls._current is None or not cls._current._active:
            raise RuntimeError("No active sprint health ledger")
        return cls._current

    def _on_failure(self, entry: FailureEntry) -> None:
        """Handle failure - update degradation mode.

        Uses safe_create_task with automatic error logging to avoid compounding errors.
        """
        try:
            asyncio.get_running_loop()
            safe_create_task(
                self._degradation_state.record_failure(entry.severity), name="failure_registry:degradation"
            )
        except RuntimeError:
            logger.debug("[DEGRADATION] No event loop for failure recording, degradation state unchanged")

    def _on_mode_transition(self, old: DegradedMode, new: DegradedMode) -> None:
        """Handle degradation mode transition."""
        self._transitions.append(
            ModeTransition(
                timestamp=time.monotonic(),
                old_mode=old,
                new_mode=new,
                trigger_counts=dict(self._degradation_state._failure_counts),
            )
        )
        logger.warning("[%s] ⚠️ DEGRADATION MODE CHANGE: %s → %s", self.sprint_id, old.label, new.label)

    async def record_failure(
        self, component: str, severity: FailureSeverity, error: BaseException, context: dict[str, Any] | None = None
    ) -> FailureEntry:
        """Record a failure and update degradation state."""
        if not self._active:
            raise RuntimeError(f"Sprint {self.sprint_id} has ended")
        return await self._registry.record(component, severity, error, context)

    @property
    def degradation_state(self) -> DegradationState:
        """Current degradation state."""
        return self._degradation_state

    @property
    def degradation_mode(self) -> DegradedMode:
        """Current degradation mode."""
        return self._degradation_state.mode

    @property
    def is_healthy(self) -> bool:
        """Check if sprint is in HEALTHY mode."""
        return self.degradation_mode == DegradedMode.HEALTHY

    @property
    def is_emergency(self) -> bool:
        """Check if sprint is in EMERGENCY mode."""
        return self.degradation_mode == DegradedMode.EMERGENCY

    def get_component_action(self, component: str) -> str:
        """Get recommended action for a component given current mode."""
        return get_degradation_action(self.degradation_mode, component)

    async def get_health_summary(self) -> dict[str, Any]:
        """Get complete health summary for reporting."""
        elapsed = time.monotonic() - self.start_time
        registry_summary = await self._registry.get_summary()
        return {
            "sprint_id": self.sprint_id,
            "elapsed_seconds": elapsed,
            "status": "ACTIVE" if self._active else "ENDED",
            "degradation": self._degradation_state.to_dict(),
            "registry": registry_summary,
            "transitions": [
                {"timestamp": t.timestamp, "from": t.old_mode.name, "to": t.new_mode.name} for t in self._transitions
            ],
            "health_indicators": self._compute_health_indicators(),
        }

    def _compute_health_indicators(self) -> dict[str, Any]:
        """Compute derived health indicators."""
        mode = self.degradation_mode
        return {
            "can_continue": mode.value < DegradedMode.EMERGENCY.value,
            "sidecars_enabled": not mode.should_skip_sidecars(),
            "mlx_enabled": not mode.should_skip_mlx_inference(),
            "errors_should_propagate": mode.should_propagate_errors(),
            "requires_attention": mode.value >= DegradedMode.DEGRADED.value,
            "critical_infrastructure_intact": mode.value < DegradedMode.EMERGENCY.value,
        }

    async def end_sprint(self) -> None:
        """End the current sprint and finalize health ledger."""
        async with self._lock:
            self.end_time = time.monotonic()
            self._active = False
            summary = await self.get_health_summary()
            logger.info(
                "[%s] Sprint ended. Duration: %.1fs, Mode: %s, Failures: %d",
                self.sprint_id,
                summary["elapsed_seconds"],
                self.degradation_mode.label,
                summary["registry"]["total_failures"],
            )
            if self._current is self:
                SprintHealthLedger._current = None

    def end_sprint_sync(self) -> None:
        """Synchronous end sprint for non-async contexts."""
        self.end_time = time.monotonic()
        self._active = False
        if self._current is self:
            SprintHealthLedger._current = None

    @asynccontextmanager
    async def sprint_context(self) -> AsyncGenerator[SprintHealthLedger]:
        """Context manager for sprint lifecycle."""
        try:
            yield self
        finally:
            await self.end_sprint()

    @asynccontextmanager
    async def failure_context(
        self, component: str, severity: FailureSeverity = FailureSeverity.MEDIUM
    ) -> AsyncGenerator[None]:
        """
        Context manager for recording failures within a block.

        Usage:
            async with ledger.failure_context("duckdb_ingest", FailureSeverity.HIGH):
                await risky_operation()
        """
        try:
            yield
        except BaseException as e:
            await self.record_failure(component, severity, e)
            raise


def get_ledger() -> SprintHealthLedger:
    """Get the current sprint health ledger (raises if none)."""
    return SprintHealthLedger.get_ledger()


def get_current_ledger() -> SprintHealthLedger | None:
    """Get the current sprint health ledger (returns None if none)."""
    return SprintHealthLedger.get_current()
