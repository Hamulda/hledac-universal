"""
Transport Circuit Breaker — light-weight two-state circuit breaker for transport layer.
=====================================================================================



F320: Issue 3.1 — Transport State Machines

Problem:
  - NymTransport had a custom circuit breaker implementation (circuit_breaker_open,
    circuit_breaker_failures, circuit_breaker_timeout, etc.) duplicated.
  - I2PTransport and TorTransport use on_phase_boundary() for session/circuit refresh
    which is their correct pattern (expensive operation, not per-request).
  - A shared two-state circuit breaker (CLOSED/OPEN) was needed for NymTransport.

Solution:
  - TransportCircuitBreaker: light-weight, two-state (CLOSED/OPEN only, no HALF_OPEN).
  - Timeout-based recovery (OPEN → CLOSED after recovery_timeout).
  - State is per-transport-instance, NOT per-domain (unlike CircuitBreaker in circuit_breaker.py).

DIFFERENCE FROM circuit_breaker.py:
  - circuit_breaker.py: Domain-based CB with HALF_OPEN, sprint-aware, thread-safe,
    warmup tracking, boot-phase TTL, metrics. For HTTP domain resilience.
  - _circuit.py: Transport-instance CB, two-state only, timeout recovery.
    For NymTransport websocket health. Nym's circuit is its websocket connection.

Usage:
    cb = TransportCircuitBreaker(failure_threshold=3, recovery_timeout=60.0)

    # Before sending:
    if not cb.can_execute():
        return CircuitOpenError()

    # After success:
    cb.record_success()

    # After failure:
    cb.record_failure()
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


class CircuitOpenError(RuntimeError):
    """Raised when circuit is open and cannot execute."""
    pass


@dataclass
class CircuitState:
    """Immutable state snapshot for debugging/telemetry."""
    failure_count: int
    last_failure: float | None
    opened_at: float | None
    is_open: bool


@dataclass
class TransportCircuitBreaker:
    """
    Light-weight two-state circuit breaker for transport layer.

    States: CLOSED (healthy) → OPEN (failing, will retry after timeout)

    This is intentionally SIMPLER than CircuitBreaker in circuit_breaker.py:
    - No HALF_OPEN state (Nym's websocket doesn't need probe requests)
    - No domain tracking (per-transport-instance, not per-domain)
    - No warmup/boot phase tracking (Nym has its own startup sequence)
    - No sprint-awareness (NymTransport lifecycle is independent)

    Thread-safety: NOT thread-safe. NymTransport is async-only and runs in
    a single event loop. For multi-threaded use, wrap with asyncio.Lock.

    Invariant: All mutations happen in async context (NymTransport event loop).
    """
    failure_threshold: int = 3
    recovery_timeout: float = 60.0

    _failure_count: int = field(default=0, init=False)
    _last_failure: float = field(default=0.0, init=False)
    _opened_at: float | None = field(default=None, init=False)

    def can_execute(self) -> bool:
        """
        Check if circuit allows execution.

        Returns True if circuit is CLOSED or if recovery_timeout has elapsed
        since opening (self-healing after timeout).
        """
        if self._opened_at is None:
            return True
        # Self-healing: if timeout has passed, allow retry
        if time.monotonic() - self._opened_at >= self.recovery_timeout:
            return True
        return False

    def record_success(self) -> None:
        """Reset circuit to closed state on success."""
        self._failure_count = 0
        self._opened_at = None
        self._last_failure = 0.0

    def record_failure(self) -> None:
        """
        Record a failure. Opens circuit if threshold reached.

        Does NOT set _opened_at if already open (preserves original open time
        for recovery_timeout calculation).
        """
        now = time.monotonic()
        self._failure_count += 1
        self._last_failure = now

        if self._failure_count >= self.failure_threshold:
            if self._opened_at is None:
                self._opened_at = now
            # If already open, keep original _opened_at for correct timeout

    def reset(self) -> None:
        """
        Force reset to closed state. Used at phase boundaries.
        """
        if self._opened_at is not None or self._failure_count > 0:
            logger.debug('[TransportCircuit] Reset: failures=%d, was_open=%s',
                        self._failure_count, self._opened_at is not None)
        self._failure_count = 0
        self._opened_at = None
        self._last_failure = 0.0

    @property
    def state(self) -> CircuitState:
        """Return current state for debugging/telemetry."""
        return CircuitState(
            failure_count=self._failure_count,
            last_failure=self._last_failure if self._last_failure > 0 else None,
            opened_at=self._opened_at,
            is_open=self._opened_at is not None,
        )

    def __repr__(self) -> str:
        if self._opened_at is None:
            return f'TransportCircuitBreaker(CLOSED, failures={self._failure_count})'
        elapsed = time.monotonic() - self._opened_at
        remaining = max(0, self.recovery_timeout - elapsed)
        return (f'TransportCircuitBreaker(OPEN, failures={self._failure_count}, '
                f'recovery_in={remaining:.1f}s)')
