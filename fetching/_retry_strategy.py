"""Retry Strategy — extracted from public_fetcher.py (ISSUE-014 REFACTOR).

Provides tenacity-based retry logic with:
- Decorrelated jitter backoff

- Retry-After header support
- Circuit breaker integration
- Blitz mode optimization
- M1 8GB optimized (minimal memory allocation)
"""
from __future__ import annotations

import contextvars
import secrets
from typing import TYPE_CHECKING, Final

from tenacity import RetryCallState as _TenacityRetryCallState
from tenacity import retry, retry_if_exception_type, stop_after_attempt

if TYPE_CHECKING:
    import httpx

# Context variable for passing circuit-breaker state into tenacity callbacks.
# ISSUE-7: avoids closure capture of mutable objects across tenacity retry boundaries.
_cb_domain_var: contextvars.ContextVar[str] = contextvars.ContextVar('_cb_domain', default='')
_cb_breaker_var: contextvars.ContextVar["CircuitBreaker | None"] = contextvars.ContextVar('_cb_breaker', default=None)  # type: ignore[valid-type]

# CircuitBreaker type hint (forward reference to avoid circular import)
CircuitBreaker = "httpx.CircuitBreaker"

# --- Module-level state for decorrelated jitter chain across tenacity retries. ---
# Reset before each top-level fetch call via reset_jitter_state().
_tenacity_prev_sleep: float = 0.0

# BLITZ-15: Blitz mode backoff cap — 1.0 s max (vs 8.0 s default).
_BLITZ_BACKOFF_CAP_S: Final[float] = 1.0
_DEFAULT_BACKOFF_CAP_S: Final[float] = 8.0

# Retryable HTTP status codes
RETRYABLE_STATUS_CODES: Final[frozenset[int]] = frozenset({429, 502, 503, 504, 520})

# Transient error patterns that warrant a retry (substring match on lowercased error string).
RETRYABLE_ERROR_PATTERNS: tuple[str, ...] = (
    'timed out',
    'timeout',
    'ttfb_timeout',
    'connection refused',
    'connection reset',
    'connection aborted',
    'broken pipe',
    'no route to host',
    'host is unreachable',
    'network is unreachable',
    'temporary failure in name resolution',
    'name or service not known',
    'getaddrinfo failed',
    'eof occurred',
    'incomplete chunked read',
    'peer closed connection',
    'connection reset by peer',
    'curl error',
    'server disconnected',
    'handshake failure',
)

# Crypto-safe jitter — reused across retries (F350M-R)
_JITTER_RNG = secrets.SystemRandom()


def reset_jitter_state() -> None:
    """Reset jitter state before a new fetch call (ISSUE-7)."""
    global _tenacity_prev_sleep
    _tenacity_prev_sleep = 0.0


def _resolve_backoff_cap_s() -> float:
    """Return the current backoff cap: 1.0 s in blitz mode, 8.0 s otherwise."""
    from hledac.universal.core.telemetry.context_state import is_blitz_mode as _is_blitz

    return _BLITZ_BACKOFF_CAP_S if _is_blitz() else _DEFAULT_BACKOFF_CAP_S


def _tenacity_wait_jitter(retry_state: _TenacityRetryCallState) -> float:
    """Tenacity wait generator: Retry-After header → backoff → jitter.

    ISSUES-7: Replaces manual retry loop with tenacity decorator.
    Uses decorrelated jitter (same formula as existing _compute_backoff_seconds)
    but accepts tenacity's RetryCallState so @retry can drive it.

    BLITZ-15: Backoff cap is 1.0 s in blitz mode (8.0 s default).

    prev_sleep is carried via module-level _tenacity_prev_sleep to maintain
    the decorrelated jitter chain across retries. Reset via reset_jitter_state().
    """
    global _tenacity_prev_sleep
    attempt = retry_state.attempt_number
    exc = retry_state.outcome.exception() if retry_state.outcome is not None else None
    retry_after: float | None = None
    if isinstance(exc, _RetryableStatus):
        retry_after = exc.retry_after
    cap_s = _resolve_backoff_cap_s()
    # Fallback: geometric backoff capped (matches _compute_backoff_seconds)
    if retry_after is None or retry_after <= 0:
        retry_after = min(2.0 ** (attempt + 1), cap_s)
    else:
        retry_after = min(retry_after, 60.0)
    # Decorrelated jitter: same formula as _compute_backoff_seconds
    jittered = min(cap_s, _JITTER_RNG.uniform(0.0, max(retry_after, _tenacity_prev_sleep) * 3.0))
    _tenacity_prev_sleep = jittered
    return jittered


class _RetryableStatus(Exception):
    """Signals a retryable HTTP status that tenacity can retry via retry_if_exception_type."""

    __slots__ = ('status_code', 'retry_after', 'circuit_breaker_domain', 'is_timeout')

    def __init__(
        self,
        status_code: int,
        retry_after: float | None = None,
        circuit_breaker_domain: str = '',
        is_timeout: bool = False,
    ) -> None:
        super().__init__(status_code, retry_after, circuit_breaker_domain, is_timeout)
        self.status_code = status_code
        self.retry_after = retry_after
        self.circuit_breaker_domain = circuit_breaker_domain
        self.is_timeout = is_timeout


def _is_retryable_status_exception(exc: BaseException) -> bool:
    """Tenacity predicate: retry only on _RetryableStatus (HTTP retryable codes)."""
    return isinstance(exc, _RetryableStatus)


def _tenacity_before_sleep(retry_state: _TenacityRetryCallState) -> None:
    """Tenacity before_sleep: record circuit-breaker failure before retry delay.

    ISSUES-7: Called by tenacity AFTER a retryable failure but BEFORE the wait delay.
    Reads circuit-breaker state from context variables set by async_fetch_public_text.
    """
    exc = retry_state.outcome.exception() if retry_state.outcome is not None else None
    if not isinstance(exc, _RetryableStatus):
        return
    cb = _cb_breaker_var.get()
    cb_domain = _cb_domain_var.get()
    if cb is not None:
        cb.record_failure(failure_kind=str(exc.status_code), is_timeout=exc.is_timeout)
    if cb_domain:
        try:
            from hledac.universal.transport.circuit_breaker import rust_circuit_record_failure

            rust_circuit_record_failure(cb_domain, is_timeout=exc.is_timeout)
        except Exception:  # noqa: BLE001 — best-effort; Rust CB unavailable is non-fatal
            pass


def _tenacity_after(retry_state: _TenacityRetryCallState) -> None:
    """Tenacity after: record circuit-breaker success on final success (ISSUE-7).

    Reads circuit-breaker state from context variables set by async_fetch_public_text.
    """
    outcome_ok = retry_state.outcome.exception() is None if retry_state.outcome is not None else False
    if not outcome_ok:
        return
    cb = _cb_breaker_var.get()
    cb_domain = _cb_domain_var.get()
    if cb is not None:
        cb.record_success()
    if cb_domain:
        try:
            from hledac.universal.transport.circuit_breaker import rust_circuit_record_success

            rust_circuit_record_success(cb_domain)
        except Exception:  # noqa: BLE001 — best-effort; Rust CB unavailable is non-fatal
            pass


def _compute_backoff_seconds(
    retry_after: float | None,
    attempt: int,
    *,
    jitter: bool = True,
    _prev_sleep: float = 0.0,
    blitz_backoff_cap_s: float | None = None,
) -> float:
    """Return bounded backoff in seconds.

    Uses Retry-After if available, otherwise exponential backoff.
    BLITZ-15: Backoff cap is 1.0 s in blitz mode (8.0 s default).
    Attempt 0 = no backoff (first failure already counted).

    When ``jitter`` is True (default), applies decorrelated jitter (AWS
    Architecture Blog "Exponential Backoff and Jitter"): samples
    ``Uniform(0, max(base, _prev_sleep) * 3)``. The optional
    ``_prev_sleep`` carries state across consecutive retries so successive
    sleep durations are de-correlated (callers may pass it as a kwargs).
    """
    cap_s = blitz_backoff_cap_s if blitz_backoff_cap_s is not None else _resolve_backoff_cap_s()
    if retry_after is not None and retry_after > 0:
        base = min(retry_after, 60.0)
    else:
        base = min(2.0 ** (attempt + 1), cap_s)
    if jitter:
        return min(cap_s, _JITTER_RNG.uniform(0.0, max(base, _prev_sleep) * 3.0))
    return base


def is_retryable_status(status_code: int) -> bool:
    """Check if HTTP status code is retryable."""
    return status_code in RETRYABLE_STATUS_CODES


def extract_retry_after(headers) -> float | None:
    """Parse Retry-After header, return seconds or None."""
    ra = headers.get('Retry-After') or headers.get('retry-after')
    if ra is None:
        return None
    try:
        return float(ra)
    except (ValueError, TypeError):
        return None


def is_retryable_error(error_str: str) -> bool:
    """Check if error string matches any retryable pattern."""
    error_lower = error_str.lower()
    return any(pat in error_lower for pat in RETRYABLE_ERROR_PATTERNS)


# PHYSICS-11: TTFB (Time-To-First-Byte) kill switch default — 1.5 s is
# aggressive enough to kill unresponsive hosts while leaving headroom for
# normal latency (TCP + TLS + server processing + first chunk on non-local
# servers). After 2 TTFB timeouts on the same host in blitz mode the
# dead-host blacklist blocks it for the remainder of the sprint.
TTFB_TIMEOUT_S: Final[float] = 1.5


# --- Blitz mode dead host tracking ---
import threading

_blitz_dead_hosts: set[str] = set()
_blitz_dead_hosts_lock: threading.Lock = threading.Lock()


def mark_blitz_host_dead(host: str) -> None:
    """BLITZ-15: Mark a host as dead for the sprint duration after retry exhaustion."""
    with _blitz_dead_hosts_lock:
        _blitz_dead_hosts.add(host)


def is_blitz_host_dead(host: str) -> bool:
    """BLITZ-15: Check if a host has been marked dead in blitz mode."""
    with _blitz_dead_hosts_lock:
        return host in _blitz_dead_hosts


def reset_blitz_dead_hosts() -> None:
    """BLITZ-15: Clear dead-host tracking (called at sprint start)."""
    global _blitz_dead_hosts
    with _blitz_dead_hosts_lock:
        _blitz_dead_hosts.clear()


# --- Max retries ---
MAX_RETRIES: Final[int] = 2


def _blitz_aware_stop(retry_state: _TenacityRetryCallState) -> bool:
    """Tenacity stop function: blitz-aware max attempts.

    BLITZ-15: In blitz mode, stop after 2 total attempts (1 retry).
    In normal mode, stops after MAX_RETRIES+1 attempts (default: 3 total = 2 retries).

    Returns:
        True if retries should stop, False to continue retrying.
    """
    from hledac.universal.core.telemetry.context_state import is_blitz_mode as _is_blitz

    max_attempts = 2 if _is_blitz() else MAX_RETRIES + 1
    return retry_state.attempt_number >= max_attempts


# --- Retry decorator ---
# ISSUE-7: tenacity decorator — replaces manual for/retry loop.
# BLITZ-15: stop uses _blitz_aware_stop — 2 attempts in blitz mode, MAX_RETRIES+1 otherwise.
# wait: _tenacity_wait_jitter — decorrelated jitter with Retry-After header priority
# retry: only on _RetryableStatus (HTTP retryable status codes)
# before_sleep: record circuit-breaker failure before waiting
# after: record circuit-breaker success on final success
# reraise: re-raise if all retries exhausted (tenacity returns last exception)
retry_decorator = retry(
    stop=_blitz_aware_stop,
    wait=_tenacity_wait_jitter,
    retry=retry_if_exception_type((_RetryableStatus, TimeoutError)),
    before_sleep=_tenacity_before_sleep,
    after=_tenacity_after,
    reraise=True,
)
