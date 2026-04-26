"""
Circuit Breaker — transport resilience pattern.

Prevents cascading failures by opening the circuit after repeated
consecutive failures/timeouts for a given domain.

Sprint F204B — Production OPSEC Domain Circuit Breaker
Active production circuit breaker wired into public_fetcher and deep_probe.
No parallel fallback system — fail-soft with safe continuation.

Bounds:
- MAX_TRACKED_DOMAINS: 500 (LRU eviction)
- MAX_RECOVERY_TIMEOUT_S: 300.0
- BASE_RECOVERY_TIMEOUT_S: 30.0
- CIRCUIT_FAILURE_THRESHOLD: 3
- CIRCUIT_HALF_OPEN_PROBES: 1

GHOST_INVARIANTS:
- asyncio.gather always with return_exceptions=True
- _check_gathered() called after every gather
- asyncio.CancelledError always re-raised
- No blocking calls in event loop
- Canonical write path always async_ingest_findings_batch()
- Circuit breaker itself does not persist — in-memory bounded only
- Model lifecycle exclusively via brain.model_lifecycle
- RAM guard: registry evicts domains above MAX_TRACKED_DOMAINS via LRU
- Fail-soft: if breaker check fails, fetch continues via safe path
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum
from typing import Final

logger = logging.getLogger(__name__)

# Bounds
MAX_TRACKED_DOMAINS: Final[int] = 500
MAX_RECOVERY_TIMEOUT_S: Final[float] = 300.0
BASE_RECOVERY_TIMEOUT_S: Final[float] = 30.0
CIRCUIT_FAILURE_THRESHOLD: Final[int] = 3
CIRCUIT_HALF_OPEN_PROBES: Final[int] = 1


class CBState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True)
class CircuitBreakerSnapshot:
    """Immutable snapshot of circuit breaker state for diagnostics."""
    domain: str
    state: str
    failure_count: int
    recovery_timeout_s: float
    opened_at_monotonic: float
    last_failure_kind: str


@dataclass(frozen=True)
class CircuitDecision:
    """Decision returned when checking a domain circuit breaker."""
    allowed: bool
    domain: str
    state: str
    retry_after_s: float
    reason: str


@dataclass
class CircuitBreaker:
    domain: str
    failure_threshold: int = CIRCUIT_FAILURE_THRESHOLD
    recovery_timeout: float = BASE_RECOVERY_TIMEOUT_S
    _state: CBState = field(default=CBState.CLOSED, init=False)
    _failure_count: int = field(default=0, init=False)
    _last_failure_time: float = field(default=0.0, init=False)
    _consecutive_timeouts: int = field(default=0, init=False)
    _opened_at_monotonic: float = field(default=0.0, init=False)
    _last_failure_kind: str = field(default="", init=False)
    _half_open_probes: int = field(default=0, init=False)

    def is_open(self) -> bool:
        if self._state == CBState.OPEN:
            if time.monotonic() - self._last_failure_time > self.recovery_timeout:
                self._state = CBState.HALF_OPEN
                self._half_open_probes = 0
                return False
            return True
        return False

    def check_circuit(self) -> CircuitDecision:
        """
        Check circuit state and return decision.
        For HALF_OPEN state, allows up to CIRCUIT_HALF_OPEN_PROBES probes before opening again.
        """
        if self._state == CBState.OPEN:
            if time.monotonic() - self._last_failure_time > self.recovery_timeout:
                self._state = CBState.HALF_OPEN
                self._half_open_probes = 0
                return CircuitDecision(
                    allowed=True,
                    domain=self.domain,
                    state="half_open",
                    retry_after_s=0.0,
                    reason="circuit_half_open_recovery_probe",
                )
            return CircuitDecision(
                allowed=False,
                domain=self.domain,
                state="open",
                retry_after_s=max(0.0, self.recovery_timeout - (time.monotonic() - self._last_failure_time)),
                reason="circuit_open_failure_threshold_exceeded",
            )
        if self._state == CBState.HALF_OPEN:
            if self._half_open_probes >= CIRCUIT_HALF_OPEN_PROBES:
                return CircuitDecision(
                    allowed=False,
                    domain=self.domain,
                    state="half_open",
                    retry_after_s=max(0.0, self.recovery_timeout - (time.monotonic() - self._last_failure_time)),
                    reason="circuit_half_open_max_probes_reached",
                )
            self._half_open_probes += 1
            return CircuitDecision(
                allowed=True,
                domain=self.domain,
                state="half_open",
                retry_after_s=0.0,
                reason="circuit_half_open_probe_allowed",
            )
        return CircuitDecision(
            allowed=True,
            domain=self.domain,
            state="closed",
            retry_after_s=0.0,
            reason="circuit_closed",
        )

    def record_success(self):
        self._failure_count = 0
        self._consecutive_timeouts = 0
        self._half_open_probes = 0
        self._state = CBState.CLOSED
        self.recovery_timeout = BASE_RECOVERY_TIMEOUT_S
        self._last_failure_kind = ""

    def record_failure(self, is_timeout: bool = False, failure_kind: str = ""):
        self._failure_count += 1
        self._last_failure_time = time.monotonic()
        self._last_failure_kind = failure_kind or ("timeout" if is_timeout else "error")
        if is_timeout:
            self._consecutive_timeouts += 1
            if self._consecutive_timeouts >= CIRCUIT_FAILURE_THRESHOLD:
                self.recovery_timeout = min(
                    self.recovery_timeout * 2, MAX_RECOVERY_TIMEOUT_S
                )
                self._consecutive_timeouts = 0
        else:
            self._consecutive_timeouts = 0
        if self._failure_count >= self.failure_threshold:
            self._state = CBState.OPEN
            self._opened_at_monotonic = time.monotonic()

    def get_state(self) -> str:
        return self._state.value

    def get_snapshot(self) -> CircuitBreakerSnapshot:
        """Return immutable snapshot of current state."""
        return CircuitBreakerSnapshot(
            domain=self.domain,
            state=self._state.value,
            failure_count=self._failure_count,
            recovery_timeout_s=self.recovery_timeout,
            opened_at_monotonic=self._opened_at_monotonic,
            last_failure_kind=self._last_failure_kind,
        )


# LRU-ordered registry — evict oldest when exceeding MAX_TRACKED_DOMAINS
_BREAKERS: OrderedDict[str, CircuitBreaker] = OrderedDict()


def _evict_if_needed() -> None:
    """Evict oldest entry when at or exceeding MAX_TRACKED_DOMAINS."""
    while len(_BREAKERS) >= MAX_TRACKED_DOMAINS - 1:
        _BREAKERS.popitem(last=False)  # pop oldest (FIFO)


def get_breaker(domain: str) -> CircuitBreaker:
    """Canonical domain circuit breaker accessor with LRU eviction."""
    if domain in _BREAKERS:
        _BREAKERS.move_to_end(domain)
    else:
        _evict_if_needed()
        _BREAKERS[domain] = CircuitBreaker(domain=domain)
    return _BREAKERS[domain]


def get_all_breaker_states() -> dict[str, str]:
    return {d: b.get_state() for d, b in _BREAKERS.items()}


def get_all_breaker_snapshots() -> list[CircuitBreakerSnapshot]:
    """Return list of snapshots for all tracked breakers."""
    return [b.get_snapshot() for b in _BREAKERS.values()]


def get_snapshot(domain: str) -> CircuitBreakerSnapshot | None:
    """Return snapshot for a specific domain, or None if not tracked."""
    breaker = _BREAKERS.get(domain)
    if breaker is None:
        return None
    return breaker.get_snapshot()


def clear_all_breakers() -> None:
    """Clear all circuit breaker state — used for testing."""
    _BREAKERS.clear()
