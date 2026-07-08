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
import random
import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Final

import aiohttp
import msgspec

logger = logging.getLogger(__name__)


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
    "checked_aiohttp_get",
    "get_breaker",
    "get_transport_event_callback",
    "record_failure",
    "set_transport_event_callback",
]

# F290: Adaptive configuration — replaces hardcoded Final[] constants.
# All limits are runtime-configurable via HLEDAC_CB_* env vars.
# Defaults are M1 8GB calibrated values.
try:
    from hledac.universal.config import _cb_int, _cb_float

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


class CircuitBreakerSnapshot(msgspec.Struct, frozen=True, gc=False):
    """Immutable snapshot of circuit breaker state for diagnostics."""
    domain: str
    state: str
    failure_count: int
    recovery_timeout_s: float
    opened_at_monotonic: float
    last_failure_kind: str
    warmup_failure_count: int = 0


class CircuitDecision(msgspec.Struct, frozen=True, gc=False):
    """Decision returned when checking a domain circuit breaker."""
    allowed: bool
    domain: str
    state: str
    retry_after_s: float
    reason: str


@dataclass
class CircuitBreaker:
    """Domain-based circuit breaker for transport layer.

    Features: warmup failure tracking, boot-phase TTL shortcuts,
    sprint-budget-aware recovery timeout.
    """
    domain: str
    failure_threshold: int = CIRCUIT_FAILURE_THRESHOLD
    recovery_timeout: float = BASE_RECOVERY_TIMEOUT_S
    _state: CBState = field(default=CBState.CLOSED, init=False)
    _failure_count: int = field(default=0, init=False)
    _warmup_failure_count: int = field(default=0, init=False)
    _warmup_last_failure_time: float = field(default=0.0, init=False)
    _last_failure_time: float = field(default=0.0, init=False)
    _consecutive_timeouts: int = field(default=0, init=False)
    _opened_at_monotonic: float = field(default=0.0, init=False)
    _last_failure_kind: str = field(default="", init=False)
    _half_open_probes: int = field(default=0, init=False)
    # Sprint F4: Track state entry time for duration metrics
    _state_entered_at_monotonic: float = field(default_factory=time.monotonic, init=False)

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
            raw = random.uniform(
                _JITTER_MIN_MULTIPLIER * self.recovery_timeout,
                _JITTER_MAX_MULTIPLIER * self.recovery_timeout,
            )
            # Floor = 10% of timeout to avoid sub-millisecond jitter for very small timeouts
            floor = _JITTER_MIN_FRACTION * self.recovery_timeout
            return max(raw, floor)
        except Exception:
            # Fail-safe: M1 deterministic fallback — use base timeout
            return self.recovery_timeout

    def check_circuit(self) -> CircuitDecision:
        """Check circuit state and return decision."""
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
                    retry_after_s=0.0,
                    reason="circuit_half_open_recovery_probe",
                )
            # F285-JITTER: return jittered retry_after to stagger incoming requests
            remaining = self.recovery_timeout - (time.monotonic() - self._last_failure_time)
            jittered_after = self._jittered_retry_after() if remaining > 0 else 0.0

            # F-Alert: Check if circuit has been open > 30s
            try:
                from hledac.universal.monitoring.alert_manager import (
                    check_circuit_breaker_alert,
                )
                asyncio.get_running_loop().create_task(
                    check_circuit_breaker_alert(
                        domain=self.domain,
                        is_open=True,
                        recovery_timeout=self.recovery_timeout,
                    )
                )
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
            _emit_transport_event(TRANSPORT_CIRCUIT_CLOSE, self.domain)

    def record_failure(self, is_timeout: bool = False, failure_kind: str = "", *, is_warmup: bool = False, sprint_remaining_s: float | None = None):
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
            if prev != CBState.OPEN:
                self._record_state_duration(prev, CBState.OPEN)
                try:
                    _metrics_safe_increment("circuit_breaker_state_transitions")
                    _metrics_safe_increment("circuit_breaker_open_count")
                except Exception:  # noqa: BLE001
                    pass
                _emit_transport_event(TRANSPORT_CIRCUIT_OPEN, self.domain)

    def mark_warmup_done(self) -> None:
        """Reset warmup failure tracking after warmup phase completes."""
        self._warmup_failure_count = 0
        self._warmup_last_failure_time = 0.0

    def get_state(self) -> str:
        return self._state.value

    def get_snapshot(self) -> CircuitBreakerSnapshot:
        """Return immutable snapshot of current state."""
        return CircuitBreakerSnapshot(
            domain=self.domain,
            state=self._state.value,
            failure_count=self._failure_count,
            warmup_failure_count=self._warmup_failure_count,
            recovery_timeout_s=self.recovery_timeout,
            opened_at_monotonic=self._opened_at_monotonic,
            last_failure_kind=self._last_failure_kind,
        )


# ISSUE-041: OrderedDict → cachetools.LRUCache (Python 3.14 deprecation)
# LRU-ordered registry: thread-safe via cachetools, eviction automatic
from cachetools import LRUCache

_BREAKERS: LRUCache[str, CircuitBreaker] = LRUCache(maxsize=MAX_TRACKED_DOMAINS)
# ISSUE-010 FIX preserved: atomic get_breaker() still needs lock for compound ops
_breakers_lock = threading.Lock()


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

    ISSUE-010 FIX + ISSUE-041: Thread-safe via _breakers_lock.
    cachetools.LRUCache handles eviction automatically on insert (maxsize cap).
    """
    with _breakers_lock:
        if domain in _BREAKERS:
            # LRUCache: access automatically promotes (no move_to_end call needed)
            return _BREAKERS[domain]
        # LRUCache auto-evicts oldest on insert when maxsize is reached
        ttl = _get_effective_ttl(domain)
        _BREAKERS[domain] = CircuitBreaker(domain=domain, recovery_timeout=ttl)
        return _BREAKERS[domain]


def get_all_breaker_states() -> dict[str, str]:
    return {d: b.get_state() for d, b in _BREAKERS.items()}


def get_all_breaker_snapshots() -> list[CircuitBreakerSnapshot]:
    return [b.get_snapshot() for b in _BREAKERS.values()]


def per_domain_stats() -> dict[str, dict]:
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
    breaker = _BREAKERS.get(domain)
    if breaker is None:
        return None
    return breaker.get_snapshot()


def clear_all_breakers() -> None:
    """Clear all circuit breaker state — used for testing."""
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


@dataclass
class ModelCircuitBreaker:
    """Per-model inference failure circuit breaker.

    Tracks OOM, timeout, and Metal driver failures per model_id.
    M1 8GB: failure_threshold=3 trips after 3 consecutive failures.
    recovery_timeout_s=30 allows HALF_OPEN probe after 30s.

    Thread-safe via threading.Lock for MLX inference context.
    F290: failure_threshold and recovery_timeout_s are adaptive — read from
    HLEDAC_CB_MODEL_FAILURE_THRESHOLD / HLEDAC_CB_BASE_RECOVERY_TIMEOUT_S env vars
    at instantiation time.
    """
    model_id: str
    failure_threshold: int = field(default_factory=lambda: _cb_int("CIRCUIT_FAILURE_THRESHOLD"))
    recovery_timeout_s: float = field(default_factory=lambda: _cb_float("BASE_RECOVERY_TIMEOUT_S"))
    _failure_count: int = field(default=0, init=False, repr=False)
    _last_failure_time: float = field(default=0.0, init=False, repr=False)
    _last_failure_kind: str = field(default="", init=False, repr=False)
    _state: CBState = field(default=CBState.CLOSED, init=False)

    def record_failure(self, kind: str = "unknown") -> None:
        """Record inference failure. Trips breaker at failure_threshold."""
        self._failure_count += 1
        self._last_failure_time = time.monotonic()
        self._last_failure_kind = kind
        if self._failure_count >= self.failure_threshold:
            self._state = CBState.OPEN
            logger.warning(
                f"ModelCircuitBreaker OPEN: model={self.model_id!r} "
                f"after {self._failure_count} failures, last={kind!r}"
            )

    def record_success(self) -> None:
        """Reset breaker on successful inference."""
        self._failure_count = 0
        self._state = CBState.CLOSED
        self._last_failure_kind = ""

    def reset(self) -> None:
        """Reset breaker to CLOSED state after successful inference.

        Volat ihned po úspěšném dokončení MLX inference v deephermes3_engine.py.
        Thread-safe: všechny operace na ModelCircuitBreaker běží v event loop thread.
        """
        self._failure_count = 0
        self._state = CBState.CLOSED
        self._last_failure_time = 0.0
        self._last_failure_kind = ""

    def is_open(self) -> bool:
        """True if inference is blocked. HALF_OPEN allows a probe attempt."""
        if self._state == CBState.OPEN:
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self.recovery_timeout_s:
                self._state = CBState.HALF_OPEN
            return True
        if self._state == CBState.HALF_OPEN:
            return False
        return False

    def get_snapshot(self) -> dict:
        """Structured snapshot for telemetry/scorecard."""
        return {
            "model_id": self.model_id,
            "state": self._state.value,
            "failure_count": self._failure_count,
            "last_failure_kind": self._last_failure_kind,
            "last_failure_age_s": round(time.monotonic() - self._last_failure_time, 1)
            if self._last_failure_time > 0 else None,
        }


# =============================================================================
# TEST-SEAM ONLY
# =============================================================================


async def resilient_fetch(url: str) -> None:
    """TEST-SEAM ONLY: Minimal CB-aware fetch stub."""
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
        domain = parsed.netloc
    except Exception:
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
    except Exception:
        return ""


def domain_breaker_check(domain: str) -> CircuitDecision:
    """Check circuit breaker for a domain."""
    if not domain:
        return CircuitDecision(
            allowed=True,
            domain=domain,
            state="unknown",
            retry_after_s=0.0,
            reason="empty_domain_skip",
        )
    breaker = get_breaker(domain)
    return breaker.check_circuit()


def domain_breaker_record_success(domain: str) -> None:
    """Record a successful external API call for the domain circuit breaker."""
    if not domain:
        return
    try:
        breaker = get_breaker(domain)
        breaker.record_success()
    except Exception:  # noqa: BLE001
        pass


def domain_breaker_record_failure(
    domain: str,
    is_timeout: bool = False,
    failure_kind: str = "",
) -> None:
    """Record a failed external API call for the domain circuit breaker."""
    if not domain:
        return
    try:
        breaker = get_breaker(domain)
        breaker.record_failure(is_timeout=is_timeout, failure_kind=failure_kind or "fetch_error")
    except Exception:  # noqa: BLE001
        pass


async def checked_aiohttp_get(
    session: aiohttp.ClientSession,
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: aiohttp.ClientTimeout,
    failure_kind: str = "fetch_error",
) -> tuple[dict | str | bytes | None, int, str | None]:
    """Perform an aiohttp GET with shared domain circuit breaker protection."""
    import aiohttp

    domain = _domain_from_url(url)
    decision = domain_breaker_check(domain)
    if not decision.allowed:
        return None, 0, f"circuit_breaker_open:{decision.reason}"

    try:
        async with session.get(url, params=params, headers=headers, timeout=timeout) as resp:
            if 200 <= resp.status < 400:
                try:
                    data = await resp.json(content_type=None)
                    return data, resp.status, None
                except Exception:
                    data = await resp.text()
                    return data, resp.status, None
            get_breaker(domain).record_failure(
                failure_kind=f"{failure_kind}:{resp.status}"
            )
            return None, resp.status, None
    except TimeoutError:
        get_breaker(domain).record_failure(is_timeout=True, failure_kind=f"{failure_kind}:timeout")
        return None, 0, "timeout"
    except aiohttp.ClientError:
        get_breaker(domain).record_failure(is_timeout=False, failure_kind=failure_kind)
        return None, 0, "client_error"
    except Exception:
        get_breaker(domain).record_failure(is_timeout=False, failure_kind=failure_kind)
        return None, 0, "unknown_error"


async def checked_aiohttp_post(
    session: aiohttp.ClientSession,
    url: str,
    *,
    json: dict | None = None,
    timeout: aiohttp.ClientTimeout,
    failure_kind: str = "post_error",
) -> tuple[dict | str | bytes | None, int, str | None]:
    """Perform an aiohttp POST with shared domain circuit breaker protection."""
    import aiohttp

    domain = _domain_from_url(url)
    decision = domain_breaker_check(domain)
    if not decision.allowed:
        return None, 0, f"circuit_breaker_open:{decision.reason}"

    try:
        async with session.post(url, json=json, timeout=timeout) as resp:
            if 200 <= resp.status < 400:
                try:
                    data = await resp.json(content_type=None)
                    return data, resp.status, None
                except Exception:
                    data = await resp.text()
                    return data, resp.status, None
            get_breaker(domain).record_failure(
                failure_kind=f"{failure_kind}:{resp.status}"
            )
            return None, resp.status, f"http_error:{resp.status}"
    except TimeoutError:
        get_breaker(domain).record_failure(is_timeout=True, failure_kind=f"{failure_kind}:timeout")
        return None, 0, "timeout"
    except aiohttp.ClientError:
        get_breaker(domain).record_failure(is_timeout=False, failure_kind=failure_kind)
        return None, 0, "client_error"
    except Exception:
        get_breaker(domain).record_failure(is_timeout=False, failure_kind=failure_kind)
        return None, 0, "unknown_error"
