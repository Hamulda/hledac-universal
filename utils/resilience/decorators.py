"""
Circuit Breaker Decorators - Easy integration for critical paths

Provides decorators and context managers for applying circuit-breaker pattern
to existing async functions without major refactoring.

Usage:
    from hledac.universal.utils.resilience.decorators import circuit_protected, with_circuit_breaker

    # As decorator
    @with_circuit_breaker("duckdb_ingest", severity=FailureSeverity.HIGH)
    async def critical_function():
        ...

    # As context manager
    async with circuit_protected("duckdb_ingest") as cb:
        await cb.execute(critical_operation)
"""

from __future__ import annotations

import asyncio
import functools
import logging
from contextlib import asynccontextmanager
from typing import Any, Optional, TypeVar
from collections.abc import Callable

from hledac.universal.utils.asyncx import safe_create_task
from hledac.universal.utils.resilience.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerOpen,
    CircuitBreakers,
    CircuitState,
)
from hledac.universal.utils.resilience.degradation_modes import FailureSeverity
from hledac.universal.utils.sync_bridge import run_sync_async
from _core import aclose

logger = logging.getLogger(__name__)

T = TypeVar("T")


# Global registry of circuit breakers
_CIRCUIT_REGISTRY: dict[str, CircuitBreaker] = {}


def get_circuit(name: str) -> Optional[CircuitBreaker]:
    """Get circuit breaker from registry."""
    return _CIRCUIT_REGISTRY.get(name)


def register_circuit(name: str, circuit: CircuitBreaker) -> None:
    """Register a circuit breaker."""
    _CIRCUIT_REGISTRY[name] = circuit


def circuit_protected(name: str, **kwargs: Any):
    """
    Context manager for circuit-breaker protected execution.

    Usage:
        async with circuit_protected("duckdb_ingest") as cb:
            await cb.execute(my_operation)
    """
    circuit = _CIRCUIT_REGISTRY.get(name)
    if circuit is None:
        circuit = CircuitBreaker(name=name)
        _CIRCUIT_REGISTRY[name] = circuit

    return _ProtectedContext(circuit, **kwargs)


class _ProtectedContext:
    """Context manager for protected execution."""

    def __init__(self, circuit: CircuitBreaker, **kwargs: Any) -> None:
        self._circuit = circuit
        self._kwargs = kwargs

    async def __aenter__(self) -> CircuitBreaker:
        return self._circuit

    async def __aexit__(self, *args: Any) -> None:
        pass


def with_circuit_breaker(
    name: str,
    config: Optional[CircuitBreakerConfig] = None,
    severity: FailureSeverity = FailureSeverity.HIGH,
    record_to_registry: bool = True,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    Decorator to wrap a function with circuit breaker protection.

    Usage:
        @with_circuit_breaker("duckdb_ingest", severity=FailureSeverity.CRITICAL)
        async def ingest_findings(findings):
            ...

    Args:
        name: Circuit breaker name
        config: Circuit breaker configuration
        severity: Severity level for registry recording
        record_to_registry: Whether to record failures to SprintHealthLedger
    """
    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        circuit = _CIRCUIT_REGISTRY.get(name)
        if circuit is None:
            circuit = CircuitBreaker(name=name, config=config)
            _CIRCUIT_REGISTRY[name] = circuit

        if asyncio.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                # Check if we can execute
                can_exec = await circuit.can_execute()
                if not can_exec:
                    # Record rejection to registry
                    if record_to_registry:
                        _record_rejection(name, severity)
                    raise CircuitBreakerOpen(
                        circuit_name=name,
                        failure_count=0,
                        last_failure="circuit_open",
                        recovery_timeout=circuit.config.recovery_timeout,
                    )

                circuit._metrics.total_calls += 1

                try:
                    return await fn(*args, **kwargs)
                except CircuitBreakerOpen:
                    raise
                except Exception as e:
                    circuit._record_failure()
                    await circuit._check_open()

                    # Record to registry
                    if record_to_registry:
                        _record_failure(name, severity, e)

                    raise

        else:

            @functools.wraps(fn)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                # D3-FIX: Use run_sync_async() instead of asyncio.run()
                # run_sync_async() handles both running and non-running loop cases
                # using asyncio.Runner() (PEP 654) for Python 3.11+ compatibility
                try:
                    loop = asyncio.get_running_loop()
                    # Running loop - can't use asyncio.run(), use threadsafe
                    can_exec = run_sync_async(circuit.can_execute())
                except RuntimeError:
                    # No running loop - use run_sync_async (uses asyncio.Runner)
                    can_exec = run_sync_async(circuit.can_execute())

                if not can_exec:
                    if record_to_registry:
                        _record_rejection(name, severity)
                    raise CircuitBreakerOpen(
                        circuit_name=name,
                        failure_count=0,
                        last_failure="circuit_open",
                        recovery_timeout=circuit.config.recovery_timeout,
                    )

                circuit._metrics.total_calls += 1

                try:
                    return fn(*args, **kwargs)
                except CircuitBreakerOpen:
                    raise
                except Exception as e:
                    circuit._record_failure()
                    try:
                        loop = asyncio.get_running_loop()
                        # Running loop - use threadsafe submission
                        run_sync_async(circuit._check_open())
                    except RuntimeError:
                        # No running loop - use run_sync_async (uses asyncio.Runner)
                        run_sync_async(circuit._check_open())

                    if record_to_registry:
                        _record_failure(name, severity, e)

                    raise

        return async_wrapper if asyncio.iscoroutinefunction(fn) else sync_wrapper

    return decorator


def _record_failure(component: str, severity: FailureSeverity, error: Exception) -> None:
    """Record failure to SprintHealthLedger if available."""
    try:
        from hledac.universal.utils.resilience import get_ledger
        ledger = get_ledger()
        safe_create_task(
            ledger.record_failure(
                component=component,
                severity=severity,
                error=error,
            ),
            name=f"resilience:record_failure:{component}",
        )
    except Exception:
        pass


def _record_rejection(component: str, severity: FailureSeverity) -> None:
    """Record circuit breaker rejection."""
    try:
        from hledac.universal.utils.resilience import get_ledger
        ledger = get_ledger()
        rejection_error = RuntimeError(f"Circuit breaker {component} is OPEN")
        safe_create_task(
            ledger.record_failure(
                component=component,
                severity=severity,
                error=rejection_error,
            ),
            name=f"resilience:record_rejection:{component}",
        )
    except Exception:
        pass


def degradation_aware(
    component: str,
    critical: bool = False,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    Decorator that makes function degradation-aware.

    In HEALTHY/DEGRADED modes: executes normally
    In IO_ONLY mode: skips if non-critical
    In EMERGENCY mode: propagates if critical, skips if non-critical

    Usage:
        @degradation_aware("sidecar_darknet")
        async def run_sidecar():
            ...
    """
    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        severity = FailureSeverity.CRITICAL if critical else FailureSeverity.MEDIUM

        if asyncio.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    from hledac.universal.utils.resilience import get_ledger
                    ledger = get_ledger()
                    action = ledger.get_component_action(component)
                except Exception:
                    action = "proceed"

                if action == "skip":
                    logger.debug("[%s] Skipping %s due to degradation mode", ledger.sprint_id if 'ledger' in dir() else '?', component)
                    return None

                if action == "propagate":
                    # Critical path in EMERGENCY mode - fail-closed, propagate any errors
                    logger.warning("[DEGRADATION] %s in EMERGENCY mode - executing with error propagation", component)
                    return await fn(*args, **kwargs)

            return async_wrapper

        else:

            @functools.wraps(fn)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    from hledac.universal.utils.resilience import get_ledger
                    ledger = get_ledger()
                    action = ledger.get_component_action(component)
                except Exception:
                    action = "proceed"

                if action == "skip":
                    return None

                return fn(*args, **kwargs)

            return sync_wrapper

    return decorator


def get_all_circuit_status() -> dict[str, dict[str, Any]]:
    """Get status of all registered circuit breakers."""
    return {
        name: circuit.get_status()
        for name, circuit in _CIRCUIT_REGISTRY.items()
    }


def reset_all_circuits() -> None:
    """Reset all circuit breakers (for testing)."""
    for circuit in _CIRCUIT_REGISTRY.values():
        circuit.reset()
