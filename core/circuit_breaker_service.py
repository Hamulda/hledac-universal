"""
P7-2: Unified Fail-Loud Circuit Breaker Service.

Replaces scattered `except Exception: logger.error + silent fallback` patterns
with structured circuit-breaker contracts that trip instead of silently degrading.

ARCHITECTURE
============
Fail-Soft (OLD): except Exception → logger.error → return default → silent degradation
Fail-Loud (NEW): circuit breaker trips → raises CircuitBreakerOpen → caller MUST handle

Circuit breaker states:
    CLOSED → normal operation, failures tracked
    OPEN   → fast-fail, caller gets CircuitBreakerOpen exception
    HALF_OPEN → probe after recovery timeout, allows single request through

USAGE
=====
    from hledac.universal.core.circuit_breaker_service import (
        CircuitBreakerService,
        CircuitBreakerOpen,
        circuit_breaker_registry,
    )

    # Get or create a breaker for a domain
    breaker = circuit_breaker_registry.get_breaker("my_service")

    # Check before operation
    if breaker.is_open():
        raise CircuitBreakerOpen(f"Circuit open for my_service")

    try:
        result = do_operation()
        breaker.record_success()
    except Exception as e:
        breaker.record_failure(e)
        raise  # Fail-loud: re-raise so caller knows

INTEGRATION POINTS
==================
Replace these patterns across the codebase:
    - utils/sync_bridge.py: rayon pool → CircuitBreakerService
    - brain/micro_model_pool.py: model load → CircuitBreakerService
    - transport/circuit_breaker.py: transport → unified service
    - knowledge/lancedb_store.py: LanceDB ops → CircuitBreakerService

P7-2: All subsystems should use this service instead of bare except + silent fallback.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = auto()   # Normal operation
    OPEN = auto()     # Fast-fail mode
    HALF_OPEN = auto()  # Recovery probe


class CircuitBreakerOpen(Exception):
    """Raised when circuit breaker is OPEN and request is blocked."""

    def __init__(self, domain: str, message: str = ""):
        self.domain = domain
        self.message = message
        super().__init__(f"CircuitBreakerOpen({domain}): {message}")


@dataclass
class CircuitBreakerConfig:
    """
    Configuration for a circuit breaker.

    P7-2 SSOT: All circuit breaker configs should use this class.
    """
    failure_threshold: int = 5        # Failures before opening
    success_threshold: int = 2        # Successes in HALF_OPEN to close
    recovery_timeout: float = 30.0     # Seconds before trying HALF_OPEN
    half_open_max_calls: int = 3      # Max calls allowed in HALF_OPEN
    name: str = ""                    # Domain name for logging


@dataclass
class CircuitBreakerStats:
    """Immutable stats snapshot for diagnostics."""
    domain: str
    state: CircuitState
    failure_count: int
    success_count: int
    last_failure_time: float | None
    last_success_time: float | None
    open_since: float | None


class CircuitBreaker:
    """
    Thread-safe circuit breaker with CLOSED → OPEN → HALF_OPEN → CLOSED lifecycle.

    P7-2: Fail-loud contract — raises CircuitBreakerOpen instead of returning defaults.
    """

    __slots__ = tuple((
        '_config', '_lock', '_state', '_failure_count', '_success_count',
        '_last_failure_time', '_last_success_time', '_open_since', '_half_open_calls',
    ))

    def __init__(self, config: CircuitBreakerConfig) -> None:
        self._config = config
        self._lock = threading.RLock()
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: float | None = None
        self._last_success_time: float | None = None
        self._open_since: float | None = None
        self._half_open_calls = 0

    @property
    def domain(self) -> str:
        """Domain name for this breaker."""
        return self._config.name

    @property
    def state(self) -> CircuitState:
        """Current circuit state (thread-safe)."""
        with self._lock:
            # Check if we should transition OPEN → HALF_OPEN
            if self._state == CircuitState.OPEN:
                if self._should_attempt_recovery():
                    self._transition_to_half_open()
            return self._state

    def is_open(self) -> bool:
        """True if circuit is OPEN (fast-fail)."""
        return self.state == CircuitState.OPEN

    def is_closed(self) -> bool:
        """True if circuit is CLOSED (normal operation)."""
        return self.state == CircuitState.CLOSED

    def record_success(self) -> None:
        """
        Record successful operation.

        P7-2: Call this on success to reset failure count and potentially close circuit.
        """
        with self._lock:
            now = time.monotonic()
            self._last_success_time = now
            self._success_count += 1

            if self._state == CircuitState.HALF_OPEN:
                if self._success_count >= self._config.success_threshold:
                    self._transition_to_closed()
            elif self._state == CircuitState.CLOSED:
                # Reset failure count on success
                self._failure_count = 0

    def record_failure(self, error: Exception | None = None) -> None:
        """
        Record failed operation.

        P7-2: Call this on failure. Opens circuit after threshold failures.
        """
        with self._lock:
            now = time.monotonic()
            self._last_failure_time = now
            self._failure_count += 1

            error_msg = str(error) if error else "unknown"
            logger.debug(
                "[CircuitBreaker] %s recorded failure #%d: %s",
                self._config.name, self._failure_count, error_msg
            )

            if self._state == CircuitState.HALF_OPEN:
                # Any failure in HALF_OPEN → immediately OPEN
                self._transition_to_open()
            elif self._state == CircuitState.CLOSED:
                if self._failure_count >= self._config.failure_threshold:
                    self._transition_to_open()

    def record_timeout(self) -> None:
        """Record a timeout failure (treated same as regular failure)."""
        self.record_failure(TimeoutError("operation timed out"))

    def _should_attempt_recovery(self) -> bool:
        """Check if enough time has passed to attempt recovery."""
        if self._open_since is None:
            return True
        elapsed = time.monotonic() - self._open_since
        return elapsed >= self._config.recovery_timeout

    def _transition_to_open(self) -> None:
        """Transition to OPEN state."""
        self._state = CircuitState.OPEN
        self._open_since = time.monotonic()
        self._half_open_calls = 0
        logger.warning(
            "[CircuitBreaker] %s OPEN (failures=%d, threshold=%d)",
            self._config.name, self._failure_count, self._config.failure_threshold
        )

    def _transition_to_half_open(self) -> None:
        """Transition to HALF_OPEN state."""
        self._state = CircuitState.HALF_OPEN
        self._success_count = 0
        self._half_open_calls = 0
        self._open_since = None
        logger.info("[CircuitBreaker] %s HALF_OPEN (recovery probe)", self._config.name)

    def _transition_to_closed(self) -> None:
        """Transition to CLOSED state."""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._open_since = None
        self._half_open_calls = 0
        logger.info("[CircuitBreaker] %s CLOSED (recovered)", self._config.name)

    def allow_request(self) -> bool:
        """
        Check if request is allowed.

        Returns True if circuit allows request, False if it should be blocked.
        Raises CircuitBreakerOpen if circuit is OPEN (fail-loud mode).
        """
        state = self.state

        if state == CircuitState.CLOSED:
            return True

        if state == CircuitState.OPEN:
            # Fail-loud: raise instead of returning False
            raise CircuitBreakerOpen(self._config.name, "circuit is open")

        # HALF_OPEN: allow limited calls
        with self._lock:
            if self._half_open_calls >= self._config.half_open_max_calls:
                raise CircuitBreakerOpen(
                    self._config.name,
                    f"half_open limit reached ({self._config.half_open_max_calls})"
                )
            self._half_open_calls += 1
            return True

    def stats(self) -> CircuitBreakerStats:
        """Get immutable stats snapshot."""
        with self._lock:
            return CircuitBreakerStats(
                domain=self._config.name,
                state=self._state,
                failure_count=self._failure_count,
                success_count=self._success_count,
                last_failure_time=self._last_failure_time,
                last_success_time=self._last_success_time,
                open_since=self._open_since,
            )

    def reset(self) -> None:
        """Reset circuit to CLOSED state (for testing or manual intervention)."""
        with self._lock:
            self._transition_to_closed()
            logger.info("[CircuitBreaker] %s manually reset to CLOSED", self._config.name)


class CircuitBreakerRegistry:
    """
    Thread-safe registry for circuit breakers by domain.

    P7-2 SSOT: Use this registry instead of creating breakers ad-hoc.
    """

    def __init__(self) -> None:
        self._breakers: dict[str, CircuitBreaker] = {}
        self._lock = threading.RLock()

    def get_breaker(
        self,
        domain: str,
        config: CircuitBreakerConfig | None = None,
    ) -> CircuitBreaker:
        """
        Get or create a circuit breaker for domain.

        Args:
            domain: Unique identifier (e.g., "lancedb", "mlx_model", "duckdb")
            config: Optional custom config. If None, uses defaults.

        Returns:
            CircuitBreaker instance for this domain.
        """
        with self._lock:
            if domain in self._breakers:
                return self._breakers[domain]

            if config is None:
                config = CircuitBreakerConfig(name=domain)

            breaker = CircuitBreaker(config)
            self._breakers[domain] = breaker
            return breaker

    def get_all_stats(self) -> list[CircuitBreakerStats]:
        """Get stats for all registered breakers."""
        with self._lock:
            return [b.stats() for b in self._breakers.values()]

    def reset_all(self) -> None:
        """Reset all breakers to CLOSED."""
        with self._lock:
            for breaker in self._breakers.values():
                breaker.reset()


# Global registry instance
circuit_breaker_registry = CircuitBreakerRegistry()


# =============================================================================
# P7-2: Decorators for fail-loud circuit breaker integration
# =============================================================================

def with_circuit_breaker(
    domain: str,
    config: CircuitBreakerConfig | None = None,
) -> Callable:
    """
    Decorator that wraps a function with circuit breaker protection.

    P7-2: Use this to make operations fail-loud instead of silently degrading.

    Usage:
        @with_circuit_breaker("my_service")
        async def my_operation():
            ...

    Note:
        The decorated function must be async or sync. The decorator handles both.
    """
    breaker = circuit_breaker_registry.get_breaker(domain, config)

    def decorator(func: Callable) -> Callable:
        import asyncio
        import functools

        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                breaker.allow_request()  # Raises CircuitBreakerOpen if open
                try:
                    result = await func(*args, **kwargs)
                    breaker.record_success()
                    return result
                except Exception as e:
                    breaker.record_failure(e)
                    raise
        else:
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                breaker.allow_request()  # Raises CircuitBreakerOpen if open
                try:
                    result = func(*args, **kwargs)
                    breaker.record_success()
                    return result
                except Exception as e:
                    breaker.record_failure(e)
                    raise

        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper

    return decorator


# =============================================================================
# P7-2: Default configs for common domains
# =============================================================================

# LanceDB operations: slower ops, higher threshold
DEFAULT_LANCEDB_CONFIG = CircuitBreakerConfig(
    name="lancedb",
    failure_threshold=3,
    success_threshold=2,
    recovery_timeout=60.0,  # 1 minute recovery
    half_open_max_calls=2,
)

# MLX model loading: expensive, lower threshold
DEFAULT_MLX_CONFIG = CircuitBreakerConfig(
    name="mlx_model",
    failure_threshold=2,
    success_threshold=1,
    recovery_timeout=120.0,  # 2 minutes (model load is expensive)
    half_open_max_calls=1,
)

# DuckDB operations: fast, higher threshold
DEFAULT_DUCKDB_CONFIG = CircuitBreakerConfig(
    name="duckdb",
    failure_threshold=5,
    success_threshold=2,
    recovery_timeout=30.0,
    half_open_max_calls=3,
)

# Transport operations: fast, high threshold
DEFAULT_TRANSPORT_CONFIG = CircuitBreakerConfig(
    name="transport",
    failure_threshold=5,
    success_threshold=3,
    recovery_timeout=30.0,
    half_open_max_calls=5,
)
