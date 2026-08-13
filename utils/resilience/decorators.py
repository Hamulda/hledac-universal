"""
Circuit Breaker Decorators - Easy integration for critical paths

Provides decorators and context managers for applying circuit-breaker pattern
to existing async functions without major refactoring.

Usage:
    from utils.resilience.decorators import circuit_protected, with_circuit_breaker

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
from typing import Any, Callable, Optional, TypeVar

from utils.resilience.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerOpen,
    CircuitBreakers,
    CircuitState,
)
from utils.resilience.degradation_modes import FailureSeverity

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
                # Safe event loop access for sync wrapper
                try:
                    loop = asyncio.get_running_loop()
                    # Can't use asyncio.run() in async context - defer to async wrapper
                    raise RuntimeError("Sync wrapper called from async context")
                except RuntimeError:
                    # No running loop - safe to use asyncio.run()
                    can_exec = asyncio.run(circuit.can_execute())
                
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
                        # Can't await from sync - log and continue
                        logger.warning("Circuit breaker check_open needs async context")
                    except RuntimeError:
                        # No running loop - safe to use asyncio.run()
                        asyncio.run(circuit._check_open())

                    if record_to_registry:
                        _record_failure(name, severity, e)

                    raise

        return async_wrapper if asyncio.iscoroutinefunction(fn) else sync_wrapper

    return decorator


def _record_failure(component: str, severity: FailureSeverity, error: Exception) -> None:
    """Record failure to SprintHealthLedger if available."""
    try:
        from utils.resilience import get_ledger
        ledger = get_ledger()
        task = asyncio.create_task(ledger.record_failure(
            component=component,
            severity=severity,
            error=error,
        ))
        # Best-effort: add done callback to log if recording fails
        def _log_if_failed(t: asyncio.Task) -> None:
            try:
                t.result()
            except Exception as recorded_exc:
                logger.warning("[REGISTRY] Failed to record failure: %s", recorded_exc)
        task.add_done_callback(_log_if_failed)
    except Exception:
        pass


def _record_rejection(component: str, severity: FailureSeverity) -> None:
    """Record circuit breaker rejection."""
    try:
        from utils.resilience import get_ledger
        ledger = get_ledger()
        rejection_error = RuntimeError(f"Circuit breaker {component} is OPEN")
        task = asyncio.create_task(ledger.record_failure(
            component=component,
            severity=severity,
            error=rejection_error,
        ))
        # Best-effort: add done callback to log if recording fails
        def _log_if_failed(t: asyncio.Task) -> None:
            try:
                t.result()
            except Exception as recorded_exc:
                logger.warning("[REGISTRY] Failed to record rejection: %s", recorded_exc)
        task.add_done_callback(_log_if_failed)
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
                    from utils.resilience import get_ledger
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
                    from utils.resilience import get_ledger
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
