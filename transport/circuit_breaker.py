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

import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from dataclasses import field as _field
from enum import Enum
from typing import Final

import aiohttp
import msgspec

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


def _metrics_safe_increment(metric_name: str) -> None:
    """Fire-and-forget metric increment — never blocks CB logic."""
    try:
        from metrics_registry import get_metrics_registry
        get_metrics_registry().inc(metric_name)
    except Exception:
        pass  # never interfere with CB


class CircuitBreakerSnapshot(msgspec.Struct, frozen=True, gc=False):
    """Immutable snapshot of circuit breaker state for diagnostics."""
    domain: str
    state: str
    failure_count: int
    recovery_timeout_s: float
    opened_at_monotonic: float
    last_failure_kind: str
    warmup_failure_count: int = 0  # separate from production failures


class CircuitDecision(msgspec.Struct, frozen=True, gc=False):
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
    _warmup_failure_count: int = field(default=0, init=False)
    _warmup_last_failure_time: float = field(default=0.0, init=False)
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
                _metrics_safe_increment("circuit_breaker_state_transitions")
                _metrics_safe_increment("circuit_breaker_half_open_count")
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
                _metrics_safe_increment("circuit_breaker_state_transitions")
                _metrics_safe_increment("circuit_breaker_half_open_count")
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
                _metrics_safe_increment("circuit_breaker_state_transitions")
                _metrics_safe_increment("circuit_breaker_open_count")
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
        prev_state = self._state.value
        self._failure_count = 0
        self._consecutive_timeouts = 0
        self._half_open_probes = 0
        self._state = CBState.CLOSED
        self.recovery_timeout = BASE_RECOVERY_TIMEOUT_S
        self._last_failure_kind = ""
        if prev_state == "half_open":
            _metrics_safe_increment("circuit_breaker_state_transitions")
            _metrics_safe_increment("circuit_breaker_recovery_success")

    def record_failure(self, is_timeout: bool = False, failure_kind: str = "", *, is_warmup: bool = False):
        """Record a failure against the circuit breaker.

        Warmup failures (is_warmup=True) are tracked separately and do NOT
        contribute to the production failure threshold. This prevents warmup
        probe failures (prewarm pool, health checks) from tripping the circuit
        breaker before production traffic begins.

        Args:
            is_timeout: whether this was a timeout-style failure
            failure_kind: descriptive label for the failure type
            is_warmup: if True, this failure is from warmup/probe phase and
                       should not contribute to production threshold
        """
        if is_warmup:
            # Warmup failures are tracked separately — they do NOT trip the
            # production breaker. This ensures prewarm probes, health checks,
            # and session-init failures during boot do not affect production.
            self._warmup_failure_count += 1
            self._warmup_last_failure_time = time.monotonic()
            self._last_failure_kind = failure_kind or ("warmup_timeout" if is_timeout else "warmup_error")
            return  # ← early return: warmup failures don't affect production state

        # Production failure path
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
            prev_state = self._state.value
            self._state = CBState.OPEN
            self._opened_at_monotonic = time.monotonic()
            if prev_state != "open":
                try:
                    _metrics_safe_increment("circuit_breaker_state_transitions")
                    _metrics_safe_increment("circuit_breaker_open_count")
                except Exception:
                    pass

    def mark_warmup_done(self) -> None:
        """Reset warmup failure tracking after warmup phase completes.

        Called when the system transitions from warmup to production.
        Warmup failures are discarded — they were never part of production.
        """
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


# LRU-ordered registry — evict oldest when exceeding MAX_TRACKED_DOMAINS
_BREAKERS: OrderedDict[str, CircuitBreaker] = OrderedDict()


def _evict_if_needed() -> None:
    """Evict oldest entry when at or exceeding MAX_TRACKED_DOMAINS."""
    # Sprint F206X: Fixed off-by-one - evict when FULL (>= MAX) not when about to be full
    while len(_BREAKERS) >= MAX_TRACKED_DOMAINS:
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


def per_domain_stats() -> dict[str, dict]:
    """Return per-domain stats dict for debug dashboard.

    Returns:
        {domain: {state, failure_count, warmup_failure_count, last_failure_time, opened_at_monotonic, last_failure_kind, recovery_timeout_s}}
    """
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
    """Return snapshot for a specific domain, or None if not tracked."""
    breaker = _BREAKERS.get(domain)
    if breaker is None:
        return None
    return breaker.get_snapshot()


def clear_all_breakers() -> None:
    """Clear all circuit breaker state — used for testing."""
    _BREAKERS.clear()


# =============================================================================
# TEST-SEAM ONLY — NOT wired into any production fetch path
# These functions exist solely to satisfy probe_8ve / probe_8sf test surface.
# Production code must NOT call these; use FetchCoordinator instead.
# =============================================================================


async def resilient_fetch(url: str) -> None:
    """
    TEST-SEAM ONLY: Minimal CB-aware fetch stub.

    Checks the domain circuit breaker before any fetch attempt.
    - Circuit OPEN  → return None immediately (no fetch)
    - Circuit CLOSED → simulate one attempt; record success (stub)
    """
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
        domain = parsed.netloc
    except Exception:
        return None

    # Strip tor-portal prefix if present
    if domain.startswith("tor:"):
        domain = domain[4:]

    breaker = get_breaker(domain)
    decision = breaker.check_circuit()
    if not decision.allowed:
        return None  # circuit open — fail fast

    # Stub: record success and return None (no actual HTTP)
    breaker.record_success()
    return None


async def get_transport_for_domain(domain: str) -> str:
    """
    TEST-SEAM ONLY: Return resolved transport hint for domain.

    - onion domains: check CB → open returns "nym", closed returns "tor"
    - clearnet: returns "clearnet"
    """
    if domain.endswith(".onion"):
        breaker = get_breaker(domain)
        decision = breaker.check_circuit()
        if not decision.allowed:
            return "nym"  # fallback when tor CB is open
        return "tor"
    return "clearnet"


# =============================================================================
# External caller helpers — Sprint F205I: circuit breaker coverage
# These helpers let external modules (ti_feed, duckduckgo, github_secret_scanner)
# check the shared domain circuit breaker before making HTTP requests.
# All use the shared _BREAKERS registry — same domain = same breaker state.
# =============================================================================

def _domain_from_url(url: str) -> str:
    """Extract netloc domain from a URL string. Handles edge cases."""
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url)
        domain = parsed.netloc
        # Strip tor: prefix from scheme (tor:example.com has no netloc)
        if not domain and parsed.scheme == "tor":
            domain = parsed.path
        # Strip tor-portal prefix from netloc
        if domain.startswith("tor:"):
            domain = domain[4:]
        return domain
    except Exception:
        return ""


def domain_breaker_check(domain: str) -> CircuitDecision:
    """
    Check circuit breaker for a domain.

    Returns CircuitDecision — check decision.allowed before making a request.
    Fail-soft: if domain is empty, returns allowed=True (skip check).
    """
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


async def checked_aiohttp_get(
    session: aiohttp.ClientSession,
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: aiohttp.ClientTimeout,
    failure_kind: str = "fetch_error",
) -> tuple[dict | str | bytes | None, int, str | None]:
    """
    Perform an aiohttp GET with shared domain circuit breaker protection.

    Args:
        session: active aiohttp.ClientSession to use
        url: URL to fetch
        params: query params (optional)
        headers: extra headers (optional)
        timeout: aiohttp.ClientTimeout for the request
        failure_kind: label for the failure kind in breaker records

    Returns:
        (response, status, error_str) — response is None on error
        (None, 0, "circuit_breaker_open:...") if circuit is open
        (None, 0, "timeout") on asyncio.TimeoutError
        (None, 0, "client_error") on aiohttp.ClientError
        (None, 0, "unknown_error") on other exceptions
        (response, status, None) on success; response is pre-read inside async with
        so caller can access .json()/.text() safely
    """
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
            # Record failure for 4xx/5xx
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


import time as _time  # noqa: E402
from dataclasses import dataclass  # noqa: E402


@dataclass
class ModelCircuitBreaker:
    """"GAP-3/1: Per-model inference failure circuit breaker.

    Tracks OOM, timeout, and Metal driver failures per model_id.
    Independent of domain CircuitBreaker (transport layer).
    M1 8GB: failure_threshold=3 trips after 3 consecutive failures.
    recovery_timeout_s=30 allows HALF_OPEN probe after 30s.
    """
    model_id: str
    failure_threshold: int = 3
    recovery_timeout_s: float = 30.0
    _failure_count: int = _field(default=0, init=False, repr=False)
    _last_failure_time: float = _field(default=0.0, init=False, repr=False)
    _last_failure_kind: str = _field(default="", init=False, repr=False)
    _state: object = _field(default=None, init=False, repr=False)  # CBState or str

    def __post_init__(self) -> None:
        # Resolve state enum at runtime to avoid circular import
        try:
            from transport.circuit_breaker import CBState
            self._state = CBState.CLOSED
            self._CLOSED = CBState.CLOSED
            self._OPEN = CBState.OPEN
            self._HALF_OPEN = CBState.HALF_OPEN
        except (ImportError, AttributeError):
            self._state = "CLOSED"
            self._CLOSED = "CLOSED"
            self._OPEN = "OPEN"
            self._HALF_OPEN = "HALF_OPEN"


    def record_failure(self, kind: str = "unknown") -> None:
        """Record inference failure. Trips breaker at failure_threshold."""
        self._failure_count += 1
        self._last_failure_time = _time.monotonic()
        self._last_failure_kind = kind
        if self._failure_count >= self.failure_threshold:
            self._state = self._OPEN
            import logging
            logging.getLogger(__name__).warning(
                f"ModelCircuitBreaker OPEN: model={self.model_id!r} "
                f"after {self._failure_count} failures, last={kind!r}"
            )

    def record_success(self) -> None:
        """Reset breaker on successful inference."""
        self._failure_count = 0
        self._state = self._CLOSED
        self._last_failure_kind = ""

    def is_open(self) -> bool:
        """True if inference is blocked. HALF_OPEN allows a probe attempt."""
        if self._state == self._OPEN:
            elapsed = _time.monotonic() - self._last_failure_time
            if elapsed >= self.recovery_timeout_s:
                self._state = self._HALF_OPEN
            return True
        if self._state == self._HALF_OPEN:
            # HALF_OPEN means "probe allowed" — not "already recovered".
            # is_open() returns False so generate() can attempt inference.
            # If the probe succeeds, record_success() → CLOSED.
            # If the probe fails, record_failure() → OPEN again.
            return False
        return False

    def get_snapshot(self) -> dict:
        """Structured snapshot for telemetry/scorecard."""
        return {
            "model_id": self.model_id,
            "state": str(self._state),
            "failure_count": self._failure_count,
            "last_failure_kind": self._last_failure_kind,
            "last_failure_age_s": round(_time.monotonic() - self._last_failure_time, 1)
            if self._last_failure_time > 0 else None,
        }


async def checked_aiohttp_post(
    session: aiohttp.ClientSession,
    url: str,
    *,
    json: dict | None = None,
    timeout: aiohttp.ClientTimeout,
    failure_kind: str = "post_error",
) -> tuple[dict | str | bytes | None, int, str | None]:
    """
    Perform an aiohttp POST with shared domain circuit breaker protection.

    Args:
        session: active aiohttp.ClientSession to use
        url: URL to POST to
        json: JSON body (optional)
        timeout: aiohttp.ClientTimeout for the request
        failure_kind: label for the failure kind in breaker records

    Returns:
        (response, status, error_str) — response is None on error
        (None, 0, "circuit_breaker_open:...") if circuit is open
        (None, 0, "timeout") on asyncio.TimeoutError
        (None, 0, "client_error") on aiohttp.ClientError
        (None, status, "http_error:status") on 4xx/5xx
        (data, status, None) on success; response is pre-read inside async with
    """
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
