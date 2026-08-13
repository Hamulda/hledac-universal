"""
utils/resilience - Sprint Health & Failure Resilience Module

Provides centralized failure tracking, degradation modes, and circuit-breaker
patterns to replace pervasive silent exception masking with explicit
degradation states and orchestrator visibility.

Architecture:
- FailureRegistry: Per-sprint failure tracking with severity classification
- SprintHealthLedger: Central orchestrator for sprint health state
- DegradationMode: Explicit degradation state machine
- CircuitBreaker: Fast-fail patterns for critical paths
- HealthIndicators: Metrics for sprint health reporting
- Circuit breaker decorators: Easy integration for existing code

Usage:
    from utils.resilience import FailureRegistry, SprintHealthLedger, DegradedMode

    # In sprint initialization:
    health = SprintHealthLedger.start_sprint(sprint_id="sprint_001")

    # In critical operations:
    try:
        await duckdb_ingest(data)
    except Exception as e:
        FailureRegistry.record(
            component="duckdb_ingest",
            severity=FailureSeverity.HIGH,
            error=e,
            context={"sprint_id": health.sprint_id}
        )
        if health.degradation_mode >= DegradedMode.IO_ONLY:
            raise  # Propagate on EMERGENCY
        return []  # Safe fallback on lower modes

    # Or use decorators:
    from utils.resilience.decorators import with_circuit_breaker, degradation_aware

    @with_circuit_breaker("duckdb_ingest", severity=FailureSeverity.CRITICAL)
    async def critical_operation():
        ...
"""

from utils.resilience.degradation_modes import (
    DegradedMode,
    DegradationState,
    ModeTransition,
    get_degradation_action,
    FailureSeverity,
    SeverityMapper,
)
from utils.resilience.failure_registry import (
    FailureEntry,
    FailureRegistry,
    SprintHealthLedger,
    get_ledger,
    get_current_ledger,
)
from utils.resilience.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpen,
    CircuitState,
    CircuitBreakers,
    CircuitBreakerConfig,
)
from utils.resilience.health_indicators import (
    HealthScore,
    HealthReporter,
    format_health_status,
    format_completion_summary,
    check_alerts,
    check_alerts_async,
)

try:
    from utils.resilience.decorators import (
        with_circuit_breaker,
        circuit_protected,
        degradation_aware,
        get_circuit,
        register_circuit,
        get_all_circuit_status,
        reset_all_circuits,
    )
except ImportError:
    # decorators module is optional
    pass

__all__ = [
    # Core
    "FailureRegistry",
    "SprintHealthLedger",
    "get_ledger",
    "get_current_ledger",
    # Degradation
    "DegradedMode",
    "DegradationState",
    "ModeTransition",
    "get_degradation_action",
    "FailureSeverity",
    "SeverityMapper",
    # Circuit Breaker
    "CircuitBreaker",
    "CircuitBreakerOpen",
    "CircuitState",
    "CircuitBreakers",
    "CircuitBreakerConfig",
    # Health Indicators
    "HealthScore",
    "HealthReporter",
    "format_health_status",
    "format_completion_summary",
    "check_alerts",
    "check_alerts_async",
    # Types
    "FailureEntry",
]
