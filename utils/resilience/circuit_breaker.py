"""
Circuit Breaker Pattern Implementation

Provides fast-fail patterns for critical paths that:
- Open circuit after N consecutive failures
- Half-open state to test recovery
- Grace period before attempting reset
- Integration with FailureRegistry for visibility

Usage:
    cb = CircuitBreaker(
        name="duckdb_ingest",
        failure_threshold=3,
        recovery_timeout=30.0,
    )

    async with cb:
        await duckdb_ingest(data)
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"      # Normal operation
    OPEN = "open"         # Failing fast
    HALF_OPEN = "half_open"  # Testing recovery


class CircuitBreakerOpen(Exception):
    """
    Exception raised when circuit breaker is OPEN.

    Raised immediately on attempt to execute when circuit is open.
    Contains metadata about why the circuit opened.
    """

    def __init__(
        self,
        circuit_name: str,
        failure_count: int,
        last_failure: str,
        recovery_timeout: float,
    ) -> None:
        self.circuit_name = circuit_name
        self.failure_count = failure_count
        self.last_failure = last_failure
        self.recovery_timeout = recovery_timeout
        super().__init__(
            f"Circuit '{circuit_name}' is OPEN. "
            f"{failure_count} failures, last: {last_failure}. "
            f"Retry after {recovery_timeout:.0f}s."
        )


@dataclass
class CircuitBreakerConfig:
    """
    Configuration for circuit breaker behavior.

    Defaults tuned for 30min sprints on M1 8GB.
    """
    # Number of consecutive failures to open circuit
    failure_threshold: int = 3
    # Seconds to wait before attempting recovery
    recovery_timeout: float = 30.0
    # Number of successes in half-open to close circuit
    success_threshold: int = 2
    # Time window for failure counting (seconds)
    failure_window: float = 60.0


@dataclass
class CircuitMetrics:
    """Runtime metrics for circuit breaker."""
    total_calls: int = 0
    total_failures: int = 0
    total_successes: int = 0
    total_rejections: int = 0
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    last_failure_ts: float = 0.0
    last_success_ts: float = 0.0
    opened_at: Optional[float] = None
    closed_at: Optional[float] = None

    @property
    def is_open(self) -> bool:
        return self.opened_at is not None and self.closed_at is None


class CircuitBreaker:
    """
    Circuit breaker for critical path protection.

    States:
        CLOSED: Normal operation, all calls pass through
        OPEN: Fast-fail, calls rejected immediately
        HALF_OPEN: Recovery testing, limited calls allowed

    Transitions:
        CLOSED → OPEN: After failure_threshold consecutive failures
        OPEN → HALF_OPEN: After recovery_timeout seconds
        HALF_OPEN → CLOSED: After success_threshold consecutive successes
        HALF_OPEN → OPEN: On any failure
    """

    def __init__(
        self,
        name: str,
        config: Optional[CircuitBreakerConfig] = None,
        on_open: Optional[Callable[["CircuitBreaker"], None]] = None,
        on_close: Optional[Callable[["CircuitBreaker"], None]] = None,
    ) -> None:
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self._state = CircuitState.CLOSED
        self._metrics = CircuitMetrics()
        self._lock = asyncio.Lock()
        self._failure_timestamps: list[float] = []
        self._on_open_callbacks = [on_open] if on_open else []
        self._on_close_callbacks = [on_close] if on_close else []

    @property
    def state(self) -> CircuitState:
        return self._state

    @property
    def metrics(self) -> CircuitMetrics:
        return self._metrics

    def on_open(self, callback: Callable[["CircuitBreaker"], None]) -> None:
        """Register callback for circuit open events."""
        self._on_open_callbacks.append(callback)

    def on_close(self, callback: Callable[["CircuitBreaker"], None]) -> None:
        """Register callback for circuit close events."""
        self._on_close_callbacks.append(callback)

    async def can_execute(self) -> bool:
        """Check if execution is allowed in current state."""
        async with self._lock:
            if self._state == CircuitState.CLOSED:
                return True

            if self._state == CircuitState.OPEN:
                # Check if recovery timeout has elapsed
                if self._metrics.opened_at is not None:
                    elapsed = time.monotonic() - self._metrics.opened_at
                    if elapsed >= self.config.recovery_timeout:
                        await self._transition_to(CircuitState.HALF_OPEN)
                        return True
                return False

            # HALF_OPEN: allow limited execution
            return True

    def _record_failure(self) -> None:
        """Record a failure for circuit state logic."""
        now = time.monotonic()
        self._metrics.total_failures += 1
        self._metrics.consecutive_failures += 1
        self._metrics.consecutive_successes = 0
        self._metrics.last_failure_ts = now
        self._failure_timestamps.append(now)

        # Clean old timestamps outside window
        cutoff = now - self.config.failure_window
        self._failure_timestamps = [ts for ts in self._failure_timestamps if ts > cutoff]

    def _record_success(self) -> None:
        """Record a success for circuit state logic."""
        now = time.monotonic()
        self._metrics.total_successes += 1
        self._metrics.consecutive_successes += 1
        self._metrics.consecutive_failures = 0
        self._metrics.last_success_ts = now

    async def _transition_to(self, new_state: CircuitState) -> None:
        """Execute state transition with callbacks."""
        old_state = self._state
        self._state = new_state

        if new_state == CircuitState.OPEN:
            self._metrics.opened_at = time.monotonic()
            logger.warning(
                "Circuit '%s' OPENED after %d consecutive failures",
                self.name,
                self._metrics.consecutive_failures,
            )
            for cb in self._on_open_callbacks:
                try:
                    cb(self)
                except Exception:
                    pass

        elif new_state == CircuitState.CLOSED:
            self._metrics.closed_at = time.monotonic()
            self._metrics.consecutive_failures = 0
            logger.info("Circuit '%s' CLOSED after recovery", self.name)
            for cb in self._on_close_callbacks:
                try:
                    cb(self)
                except Exception:
                    pass

        elif new_state == CircuitState.HALF_OPEN:
            logger.info(
                "Circuit '%s' HALF_OPEN for recovery testing",
                self.name,
            )

    async def _check_open(self) -> None:
        """Check if we should open based on failure history."""
        failures_in_window = len(self._failure_timestamps)

        if failures_in_window >= self.config.failure_threshold:
            await self._transition_to(CircuitState.OPEN)
            raise CircuitBreakerOpen(
                circuit_name=self.name,
                failure_count=failures_in_window,
                last_failure="multiple failures in window",
                recovery_timeout=self.config.recovery_timeout,
            )

    async def _check_half_open_close(self) -> None:
        """Check if we should close from half-open."""
        if self._state == CircuitState.HALF_OPEN:
            if self._metrics.consecutive_successes >= self.config.success_threshold:
                await self._transition_to(CircuitState.CLOSED)
                self._failure_timestamps.clear()

    async def execute(
        self,
        func: Callable[..., T],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """
        Execute a function with circuit breaker protection.

        Raises:
            CircuitBreakerOpen: If circuit is open
            Exception: Propagates from func on failure

        Returns:
            Result from func
        """
        await self.can_execute()

        self._metrics.total_calls += 1

        try:
            if asyncio.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                result = func(*args, **kwargs)

            self._record_success()
            await self._check_half_open_close()
            return result

        except Exception as e:
            self._record_failure()

            # Check if we should open
            await self._check_open()

            # Re-raise original exception
            raise

    @asynccontextmanager
    async def __call__(
        self,
        component: Optional[str] = None,
        severity_on_failure: int = 2,  # FailureSeverity.HIGH
    ):
        """
        Async context manager for circuit breaker.

        Usage:
            cb = CircuitBreaker("duckdb_ingest")

            async with cb("duckdb_ingest"):
                await risky_operation()

        If SprintHealthLedger is active, records failure to registry.
        """
        await self.can_execute()

        self._metrics.total_calls += 1
        context = {"component": component or self.name}

        try:
            yield
            self._record_success()
            await self._check_half_open_close()

        except asyncio.CancelledError:
            # Cancelled tasks should not be recorded as failures
            # Re-raise to let the cancellation propagate
            raise

        except CircuitBreakerOpen:
            # Already logged, just re-raise
            raise

        except Exception as e:
            self._record_failure()
            await self._check_open()

            # Record to registry if available
            try:
                from utils.resilience import FailureRegistry, FailureSeverity, get_ledger
                ledger = get_ledger()
                task = asyncio.create_task(ledger.record_failure(
                    component=context["component"],
                    severity=FailureSeverity(self._get_severity_from_state()),
                    error=e,
                    context={"circuit": self.name, "state": self._state.value},
                ))
                # Best-effort: add done callback to log if recording fails
                def _log_on_error(t: asyncio.Task) -> None:
                    try:
                        t.result()
                    except Exception as recorded_exc:
                        logger.warning("[CIRCUIT] Failed to record failure: %s", recorded_exc)
                task.add_done_callback(_log_on_error)
            except Exception:
                pass

            raise

    def _get_severity_from_state(self) -> int:
        """Determine severity based on circuit state and failure count."""
        if self._state == CircuitState.OPEN:
            return 3  # CRITICAL
        elif self._metrics.consecutive_failures >= 2:
            return 2  # HIGH
        return 1  # MEDIUM

    def reset(self) -> None:
        """Manually reset circuit to closed state."""
        self._state = CircuitState.CLOSED
        self._metrics = CircuitMetrics()
        self._failure_timestamps.clear()

    def get_status(self) -> dict[str, Any]:
        """Get circuit breaker status for reporting."""
        return {
            "name": self.name,
            "state": self._state.value,
            "config": {
                "failure_threshold": self.config.failure_threshold,
                "recovery_timeout": self.config.recovery_timeout,
                "success_threshold": self.config.success_threshold,
            },
            "metrics": {
                "total_calls": self._metrics.total_calls,
                "total_failures": self._metrics.total_failures,
                "total_successes": self._metrics.total_successes,
                "total_rejections": self._metrics.total_rejections,
                "consecutive_failures": self._metrics.consecutive_failures,
                "consecutive_successes": self._metrics.consecutive_successes,
                "is_open": self._metrics.is_open,
            },
        }


# Pre-configured circuit breakers for common components
class CircuitBreakers:
    """Factory for pre-configured circuit breakers."""

    @staticmethod
    def duckdb_ingest() -> CircuitBreaker:
        """Circuit breaker for DuckDB ingest operations."""
        return CircuitBreaker(
            name="duckdb_ingest",
            config=CircuitBreakerConfig(
                failure_threshold=3,
                recovery_timeout=30.0,
                success_threshold=2,
            ),
        )

    @staticmethod
    def graph_operations() -> CircuitBreaker:
        """Circuit breaker for graph operations."""
        return CircuitBreaker(
            name="graph_operations",
            config=CircuitBreakerConfig(
                failure_threshold=5,
                recovery_timeout=60.0,
                success_threshold=3,
            ),
        )

    @staticmethod
    def export() -> CircuitBreaker:
        """Circuit breaker for export operations."""
        return CircuitBreaker(
            name="export",
            config=CircuitBreakerConfig(
                failure_threshold=2,
                recovery_timeout=15.0,
                success_threshold=1,
            ),
        )

    @staticmethod
    def sidecar(name: str) -> CircuitBreaker:
        """Circuit breaker for sidecar operations."""
        return CircuitBreaker(
            name=f"sidecar_{name}",
            config=CircuitBreakerConfig(
                failure_threshold=3,
                recovery_timeout=30.0,
                success_threshold=2,
            ),
        )
