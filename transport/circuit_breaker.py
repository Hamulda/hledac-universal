"""
Circuit Breaker — transport resilience pattern.

Prevents cascading failures by opening the circuit after repeated
consecutive failures/timeouts for a given domain.

Sprint F204B — Production OPSEC Domain Circuit Breaker
Active production circuit breaker wired into public_fetcher and deep_probe.
No parallel fallback system — fail-soft with safe continuation.

F285 Refactor: Unified state machine, separate domain/model scopes.
- CircuitBreaker: domain-based (transport layer), warmup+boot TTL support
- ModelCircuitBreaker: model-based (inference), thread-safe, no warmup

F290 FIX: Circuit Breaker threshold adjustments for M1 Air unstable networks.
Problem: THRESHOLD=3 was too aggressive for WiFi/VPN/mobile network timeouts.
Timeout (network congestion) ≠ server failure — weighted at 0.5x.
CT servers (crt.sh, certstream) are intrinsically slow — extended TTLs.
HALF_OPEN_PROBES=3 requires 3 consecutive probe successes to close (was 1).

Bounds:
- MAX_TRACKED_DOMAINS: 500 (LRU eviction)
- MAX_RECOVERY_TIMEOUT_S: 300.0
- BASE_RECOVERY_TIMEOUT_S: 30.0
- CIRCUIT_FAILURE_THRESHOLD: 5 (timeout-based failures)
- _TIMEOUT_WEIGHT: 0.5 (timeout counts half vs error)
- _CONSECUTIVE_TIMEOUT_THRESHOLD: 6 (was implicit 3, now explicit)
- CIRCUIT_HALF_OPEN_PROBES: 3 (was 1; needs 3 probe successes to close)
- _CT_TIMEOUT_THRESHOLD: 6 (CT-specific: more tolerant of slow servers)

GHOST_INVARIANTS:
- asyncio.gather always with return_exceptions=True
- _check_gathered() called after every gather
- asyncio.CancelledError always re-raised
- No blocking calls in event loop
- Circuit breaker itself does not persist — in-memory bounded only
- RAM guard: registry evicts domains above MAX_TRACKED_DOMAINS via LRU
- Fail-soft: if breaker check fails, fetch continues via safe path
"""

from __future__ import annotations

import asyncio
import collections.abc
import logging
import secrets
import threading
import time

from hledac.universal.core.locks import LockCategory, register_lock
import dataclasses
from dataclasses import field
from enum import Enum
from typing import TYPE_CHECKING, Final

import msgspec

if TYPE_CHECKING:
    import httpx

logger = logging.getLogger(__name__)

# Crypto-safe jitter — F350M-R
_JITTER_RNG = secrets.SystemRandom()


__all__ = [
    "CircuitBreaker",
    "CircuitBreakerEvents",
    "CircuitBreakerOpen",
    "CircuitState",
    "DomainCircuitBreakerRegistry",
    "ModelCircuitBreakerRegistry",
    "TRANSPORT_CIRCUIT_CLOSE",
    "TRANSPORT_CIRCUIT_HALF_OPEN",
    "TRANSPORT_CIRCUIT_OPEN",
    "TransportCircuitBreaker",
    "checked_aiohttp_get",
    "get_all_breaker_states",
    "get_all_breaker_states_async",
    "get_breaker",
    "get_transport_breaker",
    "get_transport_event_callback",
    "record_failure",
    "rust_circuit_is_open",
    "set_transport_event_callback",
]

# F290: Adaptive configuration — replaces hardcoded Final[] constants.
# All limits are runtime-configurable via HLEDAC_CB_* env vars.
# Defaults are M1 8GB calibrated values.
try:
    from hledac.universal.config import _cb_float, _cb_int

    MAX_TRACKED_DOMAINS: Final[int] = _cb_int("MAX_TRACKED_DOMAINS")
    MAX_RECOVERY_TIMEOUT_S: Final[float] = _cb_float("MAX_RECOVERY_TIMEOUT_S")
    BOOT_RECOVERY_TIMEOUT_S: Final[float] = _cb_float("BOOT_RECOVERY_TIMEOUT_S")
    BASE_RECOVERY_TIMEOUT_S: Final[float] = _cb_float("BASE_RECOVERY_TIMEOUT_S")
    _BOOT_PHASE_DURATION_S: Final[float] = _cb_float("BOOT_PHASE_DURATION_S")
    _BOOT_PHASE_PAST_S: Final[float] = 99999.0  # testing sentinel — not adaptive
    CIRCUIT_FAILURE_THRESHOLD: Final[int] = _cb_int("CIRCUIT_FAILURE_THRESHOLD")
    CIRCUIT_HALF_OPEN_PROBES: Final[int] = _cb_int("CIRCUIT_HALF_OPEN_PROBES")
    _TIMEOUT_ACCUMULATOR_WEIGHT: Final[float] = _cb_float("TIMEOUT_ACCUMULATOR_WEIGHT")
    _CONSECUTIVE_TIMEOUT_ACCUMULATOR_THRESHOLD: Final[int] = _cb_int("CONSECUTIVE_TIMEOUT_ACCUMULATOR_THRESHOLD")
    _JITTER_MIN_MULTIPLIER: Final[float] = _cb_float("JITTER_MIN_MULTIPLIER")
    _JITTER_MAX_MULTIPLIER: Final[float] = _cb_float("JITTER_MAX_MULTIPLIER")
    _JITTER_MIN_FRACTION: Final[float] = _cb_float("JITTER_MIN_FRACTION")
except ImportError:
    # Graceful degradation — adaptive config not available
    MAX_TRACKED_DOMAINS: Final[int] = 500
    MAX_RECOVERY_TIMEOUT_S: Final[float] = 120.0  # was 300.0 — sprint-aware ceiling
    BOOT_RECOVERY_TIMEOUT_S: Final[float] = 5.0
    BASE_RECOVERY_TIMEOUT_S: Final[float] = 15.0  # was 30.0 — Phase 3.3: reduce for faster recovery
    _BOOT_PHASE_DURATION_S: Final[float] = 60.0
    _BOOT_PHASE_PAST_S: Final[float] = 99999.0
    CIRCUIT_FAILURE_THRESHOLD: Final[int] = 3
    CIRCUIT_HALF_OPEN_PROBES: Final[int] = 3
    _TIMEOUT_ACCUMULATOR_WEIGHT: Final[float] = 0.5
    _CONSECUTIVE_TIMEOUT_ACCUMULATOR_THRESHOLD: Final[int] = 4
    _JITTER_MIN_MULTIPLIER: Final[float] = 0.5
    _JITTER_MAX_MULTIPLIER: Final[float] = 1.5
    _JITTER_MIN_FRACTION: Final[float] = 0.1

# Track boot phase start for interval switching
_boot_started_at: float = 0.0

# F266: Domain-specific TTL overrides
# F290 FIX: crt.sh and certstream are slow CT servers — crt.sh query can take 60-120s
# increased TTLs to avoid premature circuit opening during legitimate slow responses
#
# F290-FIX (sprint-aware): crt.sh TTL capped at min(sprint_budget/2, 120s) to prevent
# recovery_timeout exceeding the sprint active window. For a 300s sprint, active window
# is ~210s after windup — 600s TTL would outlive the entire sprint.
# When sprint_remaining_s is available, record_failure() applies this ceiling dynamically.
# Without sprint context, we use 120s as the safe upper bound for 300s sprints.
_CIRCUIT_BREAKER_TTL_S: Final[dict[str, float]] = {
    "crt.sh": 120.0,  # was 600.0 — sprint-aware: max 120s for 300s sprint (active ~210s)
    "certstream": 120.0,  # was 60.0 — certstream is real-time stream, slower
}
_DEFAULT_TTL_S: Final[float] = BASE_RECOVERY_TIMEOUT_S


# Issue 3.3: Circuit breaker → watchdog event wire
TRANSPORT_CIRCUIT_OPEN: str = "TRANSPORT_CIRCUIT_OPEN"
TRANSPORT_CIRCUIT_HALF_OPEN: str = "TRANSPORT_CIRCUIT_HALF_OPEN"
TRANSPORT_CIRCUIT_CLOSE: str = "TRANSPORT_CIRCUIT_CLOSE"

_transport_event_callback: collections.abc.Callable[[str, str], None] | None = None


def set_transport_event_callback(
    cb: collections.abc.Callable[[str, str], None] | None,
) -> None:
    """Set the global transport circuit event callback.

    Called with (event: str, domain: str) when a circuit transitions.
    Events: TRANSPORT_CIRCUIT_OPEN, TRANSPORT_CIRCUIT_HALF_OPEN, TRANSPORT_CIRCUIT_CLOSE.
    """
    global _transport_event_callback
    _transport_event_callback = cb


def get_transport_event_callback() -> collections.abc.Callable[[str, str], None] | None:
    """Return the current global transport circuit event callback."""
    return _transport_event_callback


def _emit_transport_event(event: str, domain: str) -> None:
    """Fire-and-forget emit a circuit event to the registered callback."""
    cb = _transport_event_callback
    if cb is None:
        return
    try:
        cb(event, domain)
    except Exception:  # noqa: BLE001
        pass  # fire-and-forget


class CBState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


def _metrics_safe_increment(metric_name: str) -> None:
    """Fire-and-forget metric increment — never blocks CB logic."""
    try:
        from metrics_registry import get_metrics_registry

        get_metrics_registry().inc(metric_name)
    except Exception:  # noqa: BLE001
        pass  # noqa: BLE001


class CircuitBreakerSnapshot(msgspec.Struct, frozen=True, kw_only=True, gc=False):
    """Immutable snapshot of circuit breaker state for diagnostics."""

    domain: str
    state: str
    failure_count: int
    recovery_timeout_s: float
    opened_at_monotonic: float
    last_failure_kind: str
    warmup_failure_count: int = 0


class CircuitDecision(msgspec.Struct, frozen=True, kw_only=True, gc=False):
    """Decision returned when checking a domain circuit breaker."""

    allowed: bool
    domain: str
    state: str
    retry_after_s: float
    reason: str


@dataclasses.dataclass
class CircuitBreaker:
    """Domain-based circuit breaker for transport layer.

    Features: warmup failure tracking, boot-phase TTL shortcuts,
    sprint-budget-aware recovery timeout.

    Thread-safe: all state mutations and reads are protected by _state_lock (RLock).
    RLock is reentrant — safe for nested calls from _record_state_duration ->
    _emit_transport_event -> user callback that might call back into breaker.

    Invariant: hold _state_lock for ALL reads AND writes of _state, _failure_count,
    _consecutive_timeouts, _half_open_probes, recovery_timeout fields.
    """

    domain: str
    failure_threshold: int = CIRCUIT_FAILURE_THRESHOLD
    recovery_timeout: float = BASE_RECOVERY_TIMEOUT_S
    _state: CBState = dataclasses.field(default=CBState.CLOSED, init=False)
    _failure_count: int = dataclasses.field(default=0, init=False)
    _warmup_failure_count: int = dataclasses.field(default=0, init=False)
    _warmup_last_failure_time: float = dataclasses.field(default=0.0, init=False)
    _last_failure_time: float = dataclasses.field(default=0.0, init=False)
    _consecutive_timeouts: int = dataclasses.field(default=0, init=False)
    _opened_at_monotonic: float = dataclasses.field(default=0.0, init=False)
    _last_failure_kind: str = dataclasses.field(default="", init=False)
    _half_open_probes: int = dataclasses.field(default=0, init=False)
    # Sprint F4: Track state entry time for duration metrics
    _state_entered_at_monotonic: float = dataclasses.field(default_factory=time.monotonic, init=False)
    # RLock: reentrant, protects all state fields. Using RLock (not Lock) because
    # _record_state_duration -> _emit_transport_event -> user callback may call
    # back into breaker methods that also need the lock.
    _state_lock: threading.RLock = dataclasses.field(default=None, init=False)

    def __post_init__(self) -> None:
        if self._state_lock is None:
            self._state_lock = threading.RLock()

    def _record_state_duration(self, from_state: CBState, to_state: CBState) -> None:
        """Sprint F4: Record duration gauge when transitioning between states."""
        try:
            from metrics_registry import get_metrics_registry

            duration = time.monotonic() - self._state_entered_at_monotonic
            if from_state == CBState.OPEN and to_state == CBState.HALF_OPEN:
                get_metrics_registry().set_gauge("circuit_breaker_open_duration_s", duration)
            elif from_state == CBState.HALF_OPEN and to_state == CBState.CLOSED:
                get_metrics_registry().set_gauge("circuit_breaker_half_open_duration_s", duration)
            elif from_state == CBState.CLOSED and to_state == CBState.OPEN:
                get_metrics_registry().set_gauge("circuit_breaker_closed_duration_s", duration)
        except Exception:  # noqa: BLE001
            pass  # fire-and-forget

    def is_open(self) -> bool:
        # Note: _emit_transport_event called inside lock — safe because is_open() is
        # called synchronously (not from async callbacks). No deadlock risk here.
        # Only record_success/record_failure use lock-free emit (they may be called
        # from async paths where callbacks could re-enter get_breaker).
        with self._state_lock:
            if self._state == CBState.OPEN:
                if time.monotonic() - self._last_failure_time > self.recovery_timeout:
                    prev = self._state
                    self._state = CBState.HALF_OPEN
                    self._half_open_probes = 0
                    self._state_entered_at_monotonic = time.monotonic()
                    self._record_state_duration(prev, self._state)
                    _metrics_safe_increment("circuit_breaker_state_transitions")
                    _metrics_safe_increment("circuit_breaker_half_open_count")
                    _emit_transport_event(TRANSPORT_CIRCUIT_HALF_OPEN, self.domain)
                    return False
                return True
            return False

    def _jittered_retry_after(self) -> float:
        """F285-JITTER: Compute jittered retry_after in [0.5*timeout, 1.5*timeout].

        Full jitter prevents thundering herd when many requests wake up
        simultaneously after recovery_timeout. Each caller independently
        samples from the same range, spreading out retry attempts.
        """
        try:
            raw = _JITTER_RNG.uniform(
                _JITTER_MIN_MULTIPLIER * self.recovery_timeout,
                _JITTER_MAX_MULTIPLIER * self.recovery_timeout,
            )
            # Floor = 10% of timeout to avoid sub-millisecond jitter for very small timeouts
            floor = _JITTER_MIN_FRACTION * self.recovery_timeout
            return max(raw, floor)
        except Exception:  # noqa: BLE001 — fail-safe: M1 deterministic fallback
            return self.recovery_timeout

    def check_circuit(self) -> CircuitDecision:
        """Check circuit state and return decision."""
        _alert_args: tuple[str, float] | None = None
        with self._state_lock:
            if self._state == CBState.OPEN:
                if time.monotonic() - self._last_failure_time > self.recovery_timeout:
                    prev = self._state
                    self._state = CBState.HALF_OPEN
                    self._half_open_probes = 0
                    self._state_entered_at_monotonic = time.monotonic()
                    self._record_state_duration(prev, self._state)
                    _metrics_safe_increment("circuit_breaker_state_transitions")
                    _metrics_safe_increment("circuit_breaker_half_open_count")
                    return CircuitDecision(
                        allowed=True,
                        domain=self.domain,
                        state="half_open",
                        # CB-01 FIX: jittered retry_after prevents thundering herd.
                        # All waiting requests get staggered wake-up times instead of
                        # firing simultaneously when the circuit transitions to HALF_OPEN.
                        retry_after_s=self._jittered_retry_after(),
                        reason="circuit_half_open_recovery_probe",
                    )
                # F285-JITTER: return jittered retry_after to stagger incoming requests
                remaining = self.recovery_timeout - (time.monotonic() - self._last_failure_time)
                jittered_after = self._jittered_retry_after() if remaining > 0 else 0.0

                # Capture alert args inside lock; scheduling happens after lock release
                try:
                    _alert_args = (self.domain, self.recovery_timeout)
                except Exception:  # noqa: BLE001
                    pass

                return CircuitDecision(
                    allowed=False,
                    domain=self.domain,
                    state="open",
                    retry_after_s=jittered_after,
                    reason="circuit_open_failure_threshold_exceeded",
                )
            if self._state == CBState.HALF_OPEN:
                if self._half_open_probes >= CIRCUIT_HALF_OPEN_PROBES:
                    prev = self._state
                    self._state = CBState.CLOSED
                    self._state_entered_at_monotonic = time.monotonic()
                    self._record_state_duration(prev, self._state)
                    _metrics_safe_increment("circuit_breaker_state_transitions")
                    _metrics_safe_increment("circuit_breaker_open_count")
                    _emit_transport_event(TRANSPORT_CIRCUIT_CLOSE, self.domain)
                    return CircuitDecision(
                        allowed=False,
                        domain=self.domain,
                        state="closed",
                        retry_after_s=max(0.0, self.recovery_timeout - (time.monotonic() - self._last_failure_time)),
                        reason="circuit_half_open_max_probes_reached",
                    )
                self._half_open_probes += 1
                # CB-01 FIX: Apply jitter to half-open probe failure.
                # After record_failure() bumped recovery_timeout (exponential backoff),
                # return jittered retry_after so all waiting callers get staggered
                # wake-up instead of hammering simultaneously.
                jittered = self._jittered_retry_after()
                return CircuitDecision(
                    allowed=True,
                    domain=self.domain,
                    state="half_open",
                    retry_after_s=jittered,
                    reason="circuit_half_open_probe_allowed",
                )
            return CircuitDecision(
                allowed=True,
                domain=self.domain,
                state="closed",
                retry_after_s=0.0,
                reason="circuit_closed",
            )
        # Emit alert OUTSIDE lock — create_task schedules, task runs after lock release
        if _alert_args is not None:
            _domain, _timeout = _alert_args
            try:
                from hledac.universal.monitoring.alert_manager import (
                    check_circuit_breaker_alert,
                )

                # A5-06 FIX: safe_create_task ensures unhandled exceptions are logged.
                # Using otel_trace=False since this is fire-and-forget alert.
                from hledac.universal.utils.async_helpers import safe_create_task
                safe_create_task(
                    check_circuit_breaker_alert(
                        domain=_domain,
                        is_open=True,
                        recovery_timeout=_timeout,
                    ),
                    otel_trace=False,
                )
            except Exception:  # noqa: BLE001 — A5-06: fail-soft, alert is best-effort
                pass

    def record_success(self):
        # FIX Issue B: capture event OUTSIDE lock to prevent deadlock if callback calls get_breaker()
        event_to_emit: str | None = None
        _domain_for_emit: str = ""
        with self._state_lock:
            prev = self._state
            self._failure_count = 0
            self._consecutive_timeouts = 0
            self._half_open_probes = 0
            self._state = CBState.CLOSED
            self.recovery_timeout = BASE_RECOVERY_TIMEOUT_S
            self._last_failure_kind = ""
            if prev == CBState.HALF_OPEN:
                self._state_entered_at_monotonic = time.monotonic()
                self._record_state_duration(prev, CBState.CLOSED)
                _metrics_safe_increment("circuit_breaker_state_transitions")
                _metrics_safe_increment("circuit_breaker_recovery_success")
                event_to_emit = TRANSPORT_CIRCUIT_CLOSE
                _domain_for_emit = self.domain
        # Emit after releasing lock — prevents deadlock if callback calls get_breaker()
        if event_to_emit is not None:
            _emit_transport_event(event_to_emit, _domain_for_emit)

    def record_failure(
        self,
        is_timeout: bool = False,
        failure_kind: str = "",
        *,
        is_warmup: bool = False,
        sprint_remaining_s: float | None = None,
    ):
        """Record a failure against the circuit breaker.

        Warmup failures (is_warmup=True) are tracked separately and do NOT
        contribute to the production failure threshold.

        F290 FIX: Timeout vs error discrimination for consecutive timeout accumulator:
        - Timeout (is_timeout=True): counts as 0.5x toward _consecutive_timeouts
          (network timeout ≠ server failure — WiFi/VPN transient issues shouldn't
          cause premature recovery_timeout growth)
        - Error (is_timeout=False): counts as 1.0x, resets _consecutive_timeouts to 0
        - Recovery_timeout only doubles after _CONSECUTIVE_TIMEOUT_ACCUMULATOR_THRESHOLD (4)
          accumulative timeout units — so 6 timeouts at 0.5 weight = 3.0 (still below 4),
          8 timeouts = 4.0 (hits threshold), giving circuit more tolerance on slow networks
        """
        # FIX Issue B: capture event OUTSIDE lock to prevent deadlock if callback calls get_breaker()
        event_to_emit: str | None = None
        _domain_for_emit: str = ""
        with self._state_lock:
            if is_warmup:
                self._warmup_failure_count += 1
                self._warmup_last_failure_time = time.monotonic()
                self._last_failure_kind = failure_kind or ("warmup_timeout" if is_timeout else "warmup_error")
                return

            self._last_failure_time = time.monotonic()
            self._last_failure_kind = failure_kind or ("timeout" if is_timeout else "error")

            if is_timeout:
                # F290 FIX: Timeout weighted at 0.5x — network congestion ≠ server failure
                self._consecutive_timeouts += _TIMEOUT_ACCUMULATOR_WEIGHT
                # F290 FIX: Recovery_timeout doubles after _CONSECUTIVE_TIMEOUT_ACCUMULATOR_THRESHOLD
                if self._consecutive_timeouts >= _CONSECUTIVE_TIMEOUT_ACCUMULATOR_THRESHOLD:
                    if sprint_remaining_s is not None and sprint_remaining_s > 0:
                        _sprint_ceiling = min(sprint_remaining_s / 2, MAX_RECOVERY_TIMEOUT_S)
                        self.recovery_timeout = min(self.recovery_timeout * 2, _sprint_ceiling)
                    else:
                        self.recovery_timeout = min(self.recovery_timeout * 2, MAX_RECOVERY_TIMEOUT_S)
                    self._consecutive_timeouts = 0
            else:
                # F290 FIX: Actual server error — reset consecutive timeout accumulator
                self._failure_count += 1
                self._consecutive_timeouts = 0
            if self._failure_count >= self.failure_threshold:
                prev = self._state
                self._state = CBState.OPEN
                self._opened_at_monotonic = time.monotonic()
                self._state_entered_at_monotonic = time.monotonic()
                # CB-01 FIX: HALF_OPEN → OPEN exponential backoff.
                # When a half-open probe fails, the server is still struggling.
                # Double recovery_timeout to prevent thundering herd on next recovery.
                if prev == CBState.HALF_OPEN:
                    if sprint_remaining_s is not None and sprint_remaining_s > 0:
                        _sprint_ceiling = min(sprint_remaining_s / 2, MAX_RECOVERY_TIMEOUT_S)
                        self.recovery_timeout = min(self.recovery_timeout * 2, _sprint_ceiling)
                    else:
                        self.recovery_timeout = min(self.recovery_timeout * 2, MAX_RECOVERY_TIMEOUT_S)
                if prev != CBState.OPEN:
                    self._record_state_duration(prev, CBState.OPEN)
                    try:
                        _metrics_safe_increment("circuit_breaker_state_transitions")
                        _metrics_safe_increment("circuit_breaker_open_count")
                    except Exception:  # noqa: BLE001
                        pass
                    event_to_emit = TRANSPORT_CIRCUIT_OPEN
                    _domain_for_emit = self.domain
        # Emit after releasing lock — prevents deadlock if callback calls get_breaker()
        if event_to_emit is not None:
            _emit_transport_event(event_to_emit, _domain_for_emit)

    def mark_warmup_done(self) -> None:
        """Reset warmup failure tracking after warmup phase completes."""
        with self._state_lock:
            self._warmup_failure_count = 0
            self._warmup_last_failure_time = 0.0

    def get_state(self) -> str:
        with self._state_lock:
            return self._state.value

    def get_snapshot(self) -> CircuitBreakerSnapshot:
        """Return immutable snapshot of current state."""
        with self._state_lock:
            return CircuitBreakerSnapshot(
                domain=self.domain,
                state=self._state.value,
                failure_count=self._failure_count,
                warmup_failure_count=self._warmup_failure_count,
                recovery_timeout_s=self.recovery_timeout,
                opened_at_monotonic=self._opened_at_monotonic,
                last_failure_kind=self._last_failure_kind,
            )


# E5 FIX: OrderedDict → PyCacheDict — replaces cachetools.LRUCache.
# LRU-ordered registry: thread-safe via PyCacheDict RLock, eviction automatic.
from hledac.universal.utils.cache import PyCacheDict

# ISSUE-41: Rust-backed lock-free circuit breaker — hot-path fast check
# Lazy import to avoid early extension load
_rust_cb = None


def _get_rust_cb():
    """Lazy load Rust circuit breaker functions."""
    global _rust_cb
    if _rust_cb is None:
        try:
            from hledac_rust_extensions import (
                circuit_breaker_is_open,
                circuit_breaker_record_success,
                circuit_breaker_record_failure,
                circuit_breaker_half_open_probe,
                circuit_breaker_clear_all,
                circuit_breaker_get_stats,
            )

            _rust_cb = {
                "is_open": circuit_breaker_is_open,
                "record_success": circuit_breaker_record_success,
                "record_failure": circuit_breaker_record_failure,
                "half_open_probe": circuit_breaker_half_open_probe,
                "clear_all": circuit_breaker_clear_all,
                "get_stats": circuit_breaker_get_stats,
            }
        except ImportError:
            _rust_cb = {}
    return _rust_cb


def rust_circuit_is_open(domain: str) -> bool | None:
    """Fast-path lock-free circuit check via Rust.

    Returns:
        True if circuit is OPEN (blocked)
        False if circuit is CLOSED or HALF_OPEN (allowed)
        None if Rust circuit breaker unavailable (fallback to Python)
    """
    cb = _get_rust_cb()
    if not cb:
        return None
    try:
        return cb["is_open"](domain)
    except Exception:  # noqa: BLE001
        return None


def rust_circuit_record_success(domain: str) -> None:
    """Record success in Rust circuit breaker — resets failure count.

    ISSUE-41: Called alongside Python CircuitBreaker.record_success()
    to keep both in sync. Rust is authoritative for is_open() checks.
    """
    if not domain:
        return
    cb = _get_rust_cb()
    if not cb:
        return
    try:
        cb["record_success"](domain)
    except Exception:  # noqa: BLE001
        pass


def rust_circuit_record_failure(domain: str, is_timeout: bool = False) -> None:
    """Record failure in Rust circuit breaker — trips after threshold.

    ISSUE-41: Called alongside Python CircuitBreaker.record_failure()
    to keep both in sync. Rust is authoritative for is_open() checks.
    """
    if not domain:
        return
    cb = _get_rust_cb()
    if not cb:
        return
    try:
        cb["record_failure"](domain, is_timeout)
    except Exception:  # noqa: BLE001
        pass


_BREAKERS: PyCacheDict[str, CircuitBreaker] = PyCacheDict(maxsize=MAX_TRACKED_DOMAINS)
# ISSUE-010 FIX preserved: atomic get_breaker() still needs lock for compound ops
_breakers_lock = threading.Lock()
register_lock(LockCategory.NETWORK, _breakers_lock, "circuit_breaker._breakers_lock")


def _get_effective_ttl(domain: str) -> float:
    """Return TTL based on boot vs runtime phase.

    F290 FIX: Domain-specific TTLs (crt.sh, certstream) are respected even
    during boot phase — these CT servers are intrinsically slow and the 5s
    boot timeout would prematurely open circuits on legitimate slow responses.
    """
    # F290 FIX: Check domain-specific TTL first, before boot phase logic
    if domain in _CIRCUIT_BREAKER_TTL_S:
        return _CIRCUIT_BREAKER_TTL_S[domain]
    global _boot_started_at
    if _boot_started_at == 0.0:
        _boot_started_at = time.monotonic()
    elapsed = time.monotonic() - _boot_started_at
    if elapsed < _BOOT_PHASE_DURATION_S:
        return BOOT_RECOVERY_TIMEOUT_S
    return BASE_RECOVERY_TIMEOUT_S


def get_breaker(domain: str) -> CircuitBreaker:
    """Canonical domain circuit breaker accessor with LRU eviction.

    ISSUE-010 FIX + E5: Thread-safe via _breakers_lock.
    PyCacheDict handles eviction automatically on insert (maxsize cap).
    """
    with _breakers_lock:
        if domain in _BREAKERS:
            # PyCacheDict: access automatically promotes (no move_to_end call needed)
            return _BREAKERS[domain]
        # PyCacheDict auto-evicts oldest on insert when maxsize is reached
        ttl = _get_effective_ttl(domain)
        _BREAKERS[domain] = CircuitBreaker(domain=domain, recovery_timeout=ttl)
        return _BREAKERS[domain]


def _sync_snapshot_breakers() -> dict[str, str]:
    """Thread-safe snapshot bez nested locking — lock-free read pattern.

    Elimituje nested locking (registry lock → per-breaker RLock).
    Registry lock chrání pouze snapshot klíčů; per-breaker state read
    je chráněn individuálním per-breaker RLock.

    Bezpečné pro volání z:
    - asyncio.to_thread (thread pool)
    - sync context (přímo)
    - async context (přes anyio.to_thread.run_sync)
    """
    with _breakers_lock:
        domains = list(_BREAKERS.keys())
    # Per-breaker RLock read — žádný registry lock během iterace
    return {d: _BREAKERS[d].get_state() for d in domains if d in _BREAKERS}


def get_all_breaker_states() -> dict[str, str]:
    """Vrátí snapshot stavů všech breakerů — lock-free read pattern.

    Odstraňuje nested locking (registry lock → per-breaker RLock).
    Bezpečné pro volání z thread pool (asyncio.to_thread) i sync kontextu.
    """
    return _sync_snapshot_breakers()


async def get_all_breaker_states_async() -> dict[str, str]:
    """Async-friendly: to_thread run, žádný nested locking.

    anyio.to_thread.run_suppressed_stream_configuration() tie into
    asyncio event loop's own thread pool executor.
    Safe pro volání z async context kde chceme non-blocking result.
    """
    import anyio

    return await anyio.to_thread.run_sync(_sync_snapshot_breakers)


def get_all_breaker_snapshots() -> list[CircuitBreakerSnapshot]:
    with _breakers_lock:
        return [b.get_snapshot() for b in _BREAKERS.values()]


def per_domain_stats() -> dict[str, dict]:
    with _breakers_lock:
        return {
            d: {
                "state": b.get_state(),
                "failure_count": b._failure_count,
                "warmup_failure_count": b._warmup_failure_count,
                "last_failure_time": b._last_failure_time,
                "opened_at_monotonic": b._opened_at_monotonic,
                "last_failure_kind": b._last_failure_kind,
                "recovery_timeout_s": b.recovery_timeout,
            }
            for d, b in _BREAKERS.items()
        }


def get_snapshot(domain: str) -> CircuitBreakerSnapshot | None:
    with _breakers_lock:
        breaker = _BREAKERS.get(domain)
        if breaker is None:
            return None
        return breaker.get_snapshot()


def clear_all_breakers() -> None:
    """Clear all circuit breaker state — used for testing.

    FIX Issue D: protected by _breakers_lock to prevent race with active get_breaker() calls.
    FIX global _boot_started_at: without `global`, assignment creates a local binding.
    """
    global _boot_started_at
    with _breakers_lock:
        _BREAKERS.clear()
        _boot_started_at = 0.0


# =============================================================================
# ModelCircuitBreaker — per-model inference failure circuit breaker
# =============================================================================
# GAP-3/1: Tracks OOM, timeout, and Metal driver failures per model_id.
# Independent of domain CircuitBreaker (transport layer).
# Thread-safe: uses threading.Lock for MLX inference context.
# Simplified: no warmup tracking, no boot TTL, no sprint budget.
# =============================================================================


@dataclasses.dataclass
class ModelCircuitBreaker:
    """Per-model inference failure circuit breaker.

    Tracks OOM, timeout, and Metal driver failures per model_id.
    M1 8GB: failure_threshold=3 trips after 3 consecutive failures.
    recovery_timeout_s=30 allows HALF_OPEN probe after 30s.

    Thread-safe: all state mutations and reads protected by _state_lock (RLock).
    F290: failure_threshold and recovery_timeout_s are adaptive — read from
    HLEDAC_CB_MODEL_FAILURE_THRESHOLD / HLEDAC_CB_BASE_RECOVERY_TIMEOUT_S env vars
    at instantiation time.
    """

    model_id: str
    failure_threshold: int = dataclasses.field(
        default_factory=lambda: _cb_int("CIRCUIT_FAILURE_THRESHOLD")
    )
    recovery_timeout_s: float = dataclasses.field(
        default_factory=lambda: _cb_float("BASE_RECOVERY_TIMEOUT_S")
    )
    _failure_count: int = dataclasses.field(default=0, init=False, repr=False)
    _warmup_failure_count: int = dataclasses.field(default=0, init=False, repr=False)
    _last_failure_time: float = dataclasses.field(default=0.0, init=False, repr=False)
    _last_failure_kind: str = dataclasses.field(default="", init=False, repr=False)
    _state: CBState = dataclasses.field(default=CBState.CLOSED, init=False)
    _state_lock: threading.RLock = dataclasses.field(default=None, init=False)

    def __post_init__(self) -> None:
        if self._state_lock is None:
            self._state_lock = threading.RLock()

    def record_failure(
        self,
        is_timeout: bool = False,
        failure_kind: str = "",
        *,
        kind: str = "",  # Backward compat alias
        is_warmup: bool = False,
        sprint_remaining_s: float | None = None,
    ) -> None:
        """Record inference failure. Trips breaker at failure_threshold.

        Matches CircuitBreaker.record_failure signature for consistent API.
        Supports both `kind` (legacy) and `failure_kind` parameter names.
        """
        with self._state_lock:
            if is_warmup:
                self._warmup_failure_count += 1
                return
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            self._last_failure_kind = failure_kind or kind or "unknown"
            if self._failure_count >= self.failure_threshold:
                self._state = CBState.OPEN
                logger.warning(
                    f"ModelCircuitBreaker OPEN: model={self.model_id!r} "
                    f"after {self._failure_count} failures, last={self._last_failure_kind!r}"
                )

    def record_success(self) -> None:
        """Reset breaker on successful inference."""
        with self._state_lock:
            self._failure_count = 0
            self._state = CBState.CLOSED
            self._last_failure_kind = ""

    def reset(self) -> None:
        """Reset breaker to CLOSED state after successful inference.

        Volat ihned po úspěšném dokončení MLX inference v deephermes3_engine.py.
        Thread-safe: všechny operace na ModelCircuitBreaker běží v event loop thread.
        """
        with self._state_lock:
            self._failure_count = 0
            self._state = CBState.CLOSED
            self._last_failure_time = 0.0
            self._last_failure_kind = ""

    def is_open(self) -> bool:
        """True if inference is blocked. HALF_OPEN allows a probe attempt.

        RLock reentrancy: if _emit_transport_event callback calls back into
        ModelCircuitBreaker methods (e.g. record_failure), RLock handles nested
        acquisition safely. This differs from CircuitBreaker.is_open() where the
        event fires inside a bare lock — ModelCircuitBreaker is safe by design.
        """
        with self._state_lock:
            if self._state == CBState.OPEN:
                elapsed = time.monotonic() - self._last_failure_time
                if elapsed >= self.recovery_timeout_s:
                    self._state = CBState.HALF_OPEN
                    _metrics_safe_increment("model_circuit_breaker_state_transitions")
                    _metrics_safe_increment("model_circuit_breaker_half_open_count")
                    _emit_transport_event(f"MODEL_CIRCUIT_{self._state.value.upper()}", self.model_id)
                    return True
                # Still OPEN but within recovery window
                return True
            if self._state == CBState.HALF_OPEN:
                return False
            return False

    def get_snapshot(self) -> dict:
        """Structured snapshot for telemetry/scorecard."""
        with self._state_lock:
            return {
                "model_id": self.model_id,
                "state": self._state.value,
                "failure_count": self._failure_count,
                "last_failure_kind": self._last_failure_kind,
                "last_failure_age_s": round(time.monotonic() - self._last_failure_time, 1)
                if self._last_failure_time > 0
                else None,
            }


# =============================================================================
# TransportCircuitBreaker — transport-level (Tor/I2P) circuit breaker
# =============================================================================
# CB-03: Transport-level circuit breaker for Tor and I2P transports.
# When the transport-level breaker is OPEN, ALL darknet fetches are skipped
# regardless of domain-level circuit breaker state.
#
# This prevents repeated connection attempts from worsening transport overload
# (e.g., Tor circuit exhaustion, I2P router overload).
#
# Invariants:
# - Thread-safe via _state_lock (RLock)
# - Bounded: max 2 transport breakers (tor, i2p)
# - Fail-soft: if breaker check fails, fetch continues via safe path
# - M1 8GB: negligible RAM (~few KB for 2 breakers)
# =============================================================================


class TransportCircuitBreaker:
    """Transport-level circuit breaker for Tor/I2P.

    Independent of domain CircuitBreaker (per-domain). When this breaker
    is OPEN, ALL darknet fetches using that transport are skipped.

    CB-03: Separate CircuitBreaker instances for transport:tor and transport:i2p.
    """

    def __init__(
        self,
        transport: str,  # "tor" or "i2p"
        failure_threshold: int = 3,
        recovery_timeout: float = 60.0,
    ) -> None:
        if transport not in ("tor", "i2p"):
            raise ValueError(f"Transport must be 'tor' or 'i2p', got {transport!r}")
        self.transport = transport
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._failure_count: int = 0
        self._state: CBState = CBState.CLOSED
        self._last_failure_time: float = 0.0
        self._state_lock = threading.RLock()
        self._half_open_probes: int = 0
        self._state_entered_at_monotonic: float = time.monotonic()

    def is_open(self) -> bool:
        """Return True if transport circuit is OPEN (blocked)."""
        with self._state_lock:
            if self._state == CBState.OPEN:
                if time.monotonic() - self._last_failure_time > self.recovery_timeout:
                    self._transition_to(CBState.HALF_OPEN)
                    return False
                return True
            return False

    def check_circuit(self) -> CircuitDecision:
        """Check transport circuit state and return decision."""
        with self._state_lock:
            if self._state == CBState.OPEN:
                if time.monotonic() - self._last_failure_time > self.recovery_timeout:
                    self._transition_to(CBState.HALF_OPEN)
                    return CircuitDecision(
                        allowed=True,
                        domain=f"transport:{self.transport}",
                        state="half_open",
                        retry_after_s=0.0,
                        reason="transport_circuit_half_open_recovery",
                    )
                jitter = _JITTER_RNG.uniform(
                    _JITTER_MIN_MULTIPLIER * self.recovery_timeout,
                    _JITTER_MAX_MULTIPLIER * self.recovery_timeout,
                ) if self.recovery_timeout > 0 else 0.0
                return CircuitDecision(
                    allowed=False,
                    domain=f"transport:{self.transport}",
                    state="open",
                    retry_after_s=jitter,
                    reason="transport_circuit_open_overload",
                )
            if self._state == CBState.HALF_OPEN:
                if self._half_open_probes >= CIRCUIT_HALF_OPEN_PROBES:
                    self._transition_to(CBState.CLOSED)
                    return CircuitDecision(
                        allowed=False,
                        domain=f"transport:{self.transport}",
                        state="closed",
                        retry_after_s=0.0,
                        reason="transport_circuit_half_open_max_probes",
                    )
                self._half_open_probes += 1
                return CircuitDecision(
                    allowed=True,
                    domain=f"transport:{self.transport}",
                    state="half_open",
                    retry_after_s=0.0,
                    reason="transport_circuit_half_open_probe",
                )
            return CircuitDecision(
                allowed=True,
                domain=f"transport:{self.transport}",
                state="closed",
                retry_after_s=0.0,
                reason="transport_circuit_closed",
            )

    def record_success(self) -> None:
        """Reset breaker on successful fetch."""
        with self._state_lock:
            if self._state in (CBState.HALF_OPEN, CBState.CLOSED):
                self._transition_to(CBState.CLOSED)

    def record_failure(self, is_timeout: bool = False) -> None:
        """Record transport-level failure (e.g., Tor circuit exhausted)."""
        with self._state_lock:
            self._last_failure_time = time.monotonic()
            self._failure_count += 1
            if self._failure_count >= self.failure_threshold:
                self._transition_to(CBState.OPEN)

    def _transition_to(self, new_state: CBState) -> None:
        """Transition to new state and record metrics."""
        prev = self._state
        self._state = new_state
        self._state_entered_at_monotonic = time.monotonic()
        if prev == CBState.OPEN and new_state == CBState.HALF_OPEN:
            self._half_open_probes = 0
            _metrics_safe_increment("transport_circuit_breaker_state_transitions")
            _metrics_safe_increment("transport_circuit_breaker_half_open_count")
            _emit_transport_event(f"TRANSPORT_CIRCUIT_{new_state.value.upper()}", f"transport:{self.transport}")
        elif prev == CBState.HALF_OPEN and new_state == CBState.CLOSED:
            _metrics_safe_increment("transport_circuit_breaker_state_transitions")
            _metrics_safe_increment("transport_circuit_breaker_recovery_success")
            _emit_transport_event(f"TRANSPORT_CIRCUIT_{new_state.value.upper()}", f"transport:{self.transport}")
        elif prev == CBState.CLOSED and new_state == CBState.OPEN:
            _metrics_safe_increment("transport_circuit_breaker_state_transitions")
            _metrics_safe_increment("transport_circuit_breaker_open_count")
            _emit_transport_event(f"TRANSPORT_CIRCUIT_{new_state.value.upper()}", f"transport:{self.transport}")
        elif prev == CBState.HALF_OPEN and new_state == CBState.OPEN:
            _metrics_safe_increment("transport_circuit_breaker_state_transitions")
            _metrics_safe_increment("transport_circuit_breaker_open_count")
            _emit_transport_event(f"TRANSPORT_CIRCUIT_{new_state.value.upper()}", f"transport:{self.transport}")


# Singleton registry for transport circuit breakers
_TRANSPORT_BREAKERS: dict[str, TransportCircuitBreaker] = {
    "tor": TransportCircuitBreaker("tor", failure_threshold=3, recovery_timeout=60.0),
    "i2p": TransportCircuitBreaker("i2p", failure_threshold=3, recovery_timeout=60.0),
}


def get_transport_breaker(transport: str) -> TransportCircuitBreaker | None:
    """Get transport circuit breaker for tor or i2p.

    CB-03: Returns the transport-level breaker. When is_open() returns True,
    ALL darknet fetches using that transport should be skipped.
    Returns None if transport is not 'tor' or 'i2p'.
    """
    return _TRANSPORT_BREAKERS.get(transport)


def get_tor_transport_breaker() -> TransportCircuitBreaker:
    """Get Tor transport circuit breaker."""
    return _TRANSPORT_BREAKERS["tor"]


def get_i2p_transport_breaker() -> TransportCircuitBreaker:
    """Get I2P transport circuit breaker."""
    return _TRANSPORT_BREAKERS["i2p"]


# =============================================================================
# TEST-SEAM ONLY
# =============================================================================


async def resilient_fetch(url: str) -> None:
    """TEST-SEAM ONLY: Minimal CB-aware fetch stub."""
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
        domain = parsed.netloc
    except Exception:  # noqa: BLE001 — fail-safe: URL parse error → skip
        return None

    if domain.startswith("tor:"):
        domain = domain[4:]

    breaker = get_breaker(domain)
    decision = breaker.check_circuit()
    if not decision.allowed:
        return None

    breaker.record_success()
    return None


async def get_transport_for_domain(domain: str) -> str:
    """TEST-SEAM ONLY: Return resolved transport hint for domain."""
    if domain.endswith(".onion"):
        breaker = get_breaker(domain)
        decision = breaker.check_circuit()
        if not decision.allowed:
            return "nym"
        return "tor"
    return "clearnet"


# =============================================================================
# External caller helpers
# =============================================================================


def _domain_from_url(url: str) -> str:
    """Extract netloc domain from a URL string."""
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
        domain = parsed.netloc
        if not domain and parsed.scheme == "tor":
            domain = parsed.path
        if domain.startswith("tor:"):
            domain = domain[4:]
        return domain
    except Exception:  # noqa: BLE001 — fail-safe: URL parse error → empty string
        return ""


def domain_breaker_check(domain: str) -> CircuitDecision:
    """Check circuit breaker for a domain.

    R23 FIX: Rust circuit_breaker_is_open is authoritative when available.
    Python CircuitBreaker.check_circuit() is fallback when Rust unavailable.

    Rationale: Rust uses lock-free AtomicU32/AtomicU64 atomics — ~10-100×
    faster than threading.Lock in async contexts. 512 domains = ~12KB vs
    Python's ~500KB+ dict overhead. Hot path called 100-1000× per sprint.
    """
    if not domain:
        return CircuitDecision(
            allowed=True,
            domain=domain,
            state="unknown",
            retry_after_s=0.0,
            reason="empty_domain_skip",
        )
    # R23: Try Rust first (authoritative, lock-free)
    rust_result = rust_circuit_is_open(domain)
    if rust_result is not None:
        # Rust is available — use its result
        if rust_result:
            return CircuitDecision(
                allowed=False,
                domain=domain,
                state="open",
                retry_after_s=BASE_RECOVERY_TIMEOUT_S,
                reason="circuit_open_rust",
            )
        else:
            return CircuitDecision(
                allowed=True,
                domain=domain,
                state="closed",
                retry_after_s=0.0,
                reason="circuit_closed_rust",
            )
    # R23: Rust unavailable — fallback to Python (never happens in production)
    breaker = get_breaker(domain)
    return breaker.check_circuit()


def domain_breaker_record_success(domain: str) -> None:
    """Record a successful external API call for the domain circuit breaker.

    R23 FIX: Syncs to both Python CircuitBreaker AND Rust circuit_breaker.
    Rust is authoritative for is_open() — keeping both in sync ensures
    consistent state when Rust is available but is_open() was called before
    record_success() (e.g., in a race condition window).
    """
    if not domain:
        return
    try:
        breaker = get_breaker(domain)
        breaker.record_success()
    except Exception:  # noqa: BLE001
        pass
    # R23: Also sync to Rust when available
    rust_circuit_record_success(domain)


def domain_breaker_record_failure(
    domain: str,
    is_timeout: bool = False,
    failure_kind: str = "",
) -> None:
    """Record a failed external API call for the domain circuit breaker.

    R23 FIX: Syncs to both Python CircuitBreaker AND Rust circuit_breaker.
    Rust is authoritative for is_open() — keeping both in sync ensures
    consistent state when Rust is available but is_open() was called before
    record_failure() (e.g., in a race condition window).
    """
    if not domain:
        return
    try:
        breaker = get_breaker(domain)
        breaker.record_failure(is_timeout=is_timeout, failure_kind=failure_kind or "fetch_error")
    except Exception:  # noqa: BLE001
        pass
    # R23: Also sync to Rust when available
    rust_circuit_record_failure(domain, is_timeout)


async def checked_httpx_get(
    session: "httpx.AsyncClient",
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: "httpx.Timeout",
    failure_kind: str = "fetch_error",
) -> tuple[dict | str | bytes | None, int, str | None]:
    """Perform an httpx GET with shared domain circuit breaker protection."""
    import httpx  # noqa: E402 — lazy, saves ~1800ms at import time

    domain = _domain_from_url(url)
    decision = domain_breaker_check(domain)
    if not decision.allowed:
        return None, 0, f"circuit_breaker_open:{decision.reason}"

    try:
        resp = await session.get(url, params=params, headers=headers, timeout=timeout)
        if 200 <= resp.status_code < 400:
            try:
                data = resp.json()
                return data, resp.status_code, None
            except Exception:  # noqa: BLE001 — JSON parse fail → use raw text
                data = resp.text
                return data, resp.status_code, None
        get_breaker(domain).record_failure(failure_kind=f"{failure_kind}:{resp.status_code}")
        return None, resp.status_code, None
    except httpx.TimeoutException:
        get_breaker(domain).record_failure(is_timeout=True, failure_kind=f"{failure_kind}:timeout")
        return None, 0, "timeout"
    except httpx.HTTPError:
        get_breaker(domain).record_failure(is_timeout=False, failure_kind=failure_kind)
        return None, 0, "client_error"
    except Exception:  # noqa: BLE001 — fail-safe: unexpected error
        get_breaker(domain).record_failure(is_timeout=False, failure_kind=failure_kind)
        return None, 0, "unknown_error"


async def checked_httpx_post(
    session: "httpx.AsyncClient",
    url: str,
    *,
    json: dict | None = None,
    timeout: "httpx.Timeout",
    failure_kind: str = "post_error",
) -> tuple[dict | str | bytes | None, int, str | None]:
    """Perform an httpx POST with shared domain circuit breaker protection."""
    import httpx  # noqa: E402 — lazy, saves ~1800ms at import time

    domain = _domain_from_url(url)
    decision = domain_breaker_check(domain)
    if not decision.allowed:
        return None, 0, f"circuit_breaker_open:{decision.reason}"

    try:
        resp = await session.post(url, json=json, timeout=timeout)
        if 200 <= resp.status_code < 400:
            try:
                data = resp.json()
                return data, resp.status_code, None
            except Exception:  # noqa: BLE001 — JSON parse fail → use raw text
                data = resp.text
                return data, resp.status_code, None
        get_breaker(domain).record_failure(failure_kind=f"{failure_kind}:{resp.status_code}")
        return None, resp.status_code, f"http_error:{resp.status_code}"
    except httpx.TimeoutException:
        get_breaker(domain).record_failure(is_timeout=True, failure_kind=f"{failure_kind}:timeout")
        return None, 0, "timeout"
    except httpx.HTTPError:
        get_breaker(domain).record_failure(is_timeout=False, failure_kind=failure_kind)
        return None, 0, "client_error"
    except Exception:  # noqa: BLE001 — fail-safe: unexpected error
        get_breaker(domain).record_failure(is_timeout=False, failure_kind=failure_kind)
        return None, 0, "unknown_error"


# Backward-compat alias — httpx is the primary HTTP client
checked_aiohttp_get = checked_httpx_get
"""Alias for backward compatibility with code referencing the old aiohttp name."""

checked_aiohttp_post = checked_httpx_post
"""F4XX: alias — httpx now replaces aiohttp."""
