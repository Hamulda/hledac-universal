"""
Rust-backed circuit breaker — ISSUE-41.

Lock-free per-domain circuit breaker using AtomicU32 + DashMap in Rust.
Python fallback when Rust extension is unavailable.

Architecture:
- Hot path: Rust circuit_breaker_is_open(domain) — lock-free atomic check
- Python side: wraps Rust with fail-safe fallback to Python CircuitBreaker
- All state in Rust DashMap — no Python dict serialization overhead

Usage:
    from rust_extensions import circuit_breaker_python as cb

    if cb.is_open("example.com"):
        return "circuit open"
    cb.record_success("example.com")
    cb.record_failure("example.com", is_timeout=False)

Invariant: Rust-side circuit breaker is always-on (always available).
If import fails, we fall back to Python-only implementation.
"""

from __future__ import annotations

from typing import Final

# ---------------------------------------------------------------------------
# Rust Import (lazy, fail-safe)
# ---------------------------------------------------------------------------

_circuit_breaker_rust = None

try:
    from hledac_rust_extensions import (
        circuit_breaker_is_open,
        circuit_breaker_record_success,
        circuit_breaker_record_failure,
        circuit_breaker_half_open_probe,
        circuit_breaker_clear_all,
        circuit_breaker_get_stats,
    )

    _circuit_breaker_rust = {
        "is_open": circuit_breaker_is_open,
        "record_success": circuit_breaker_record_success,
        "record_failure": circuit_breaker_record_failure,
        "half_open_probe": circuit_breaker_half_open_probe,
        "clear_all": circuit_breaker_clear_all,
        "get_stats": circuit_breaker_get_stats,
    }
except ImportError:
    # Rust extension not available — will use Python fallback
    _circuit_breaker_rust = None


# ---------------------------------------------------------------------------
# Constants (M1 8GB calibrated)
# ---------------------------------------------------------------------------

_FAILURE_THRESHOLD: Final[int] = 5
_HALF_OPEN_PROBES: Final[int] = 3
_RECOVERY_TIMEOUT_S: Final[float] = 30.0


# ---------------------------------------------------------------------------
# Public API (matches transport.circuit_breaker domain_breaker_* interface)
# ---------------------------------------------------------------------------

def is_open(domain: str) -> bool:
    """Check if circuit is OPEN (blocked) for domain.

    Hot path: called on every fetch.
    Lock-free in Rust when available.
    """
    if not domain:
        return False  # Empty domain = no circuit

    if _circuit_breaker_rust is not None:
        return _circuit_breaker_rust["is_open"](domain)

    # Python fallback — delegate to transport.circuit_breaker
    return _python_fallback_is_open(domain)


def record_success(domain: str) -> None:
    """Record successful request — resets failure count."""
    if not domain:
        return

    if _circuit_breaker_rust is not None:
        _circuit_breaker_rust["record_success"](domain)
        return

    _python_fallback_record_success(domain)


def record_failure(domain: str, is_timeout: bool = False) -> None:
    """Record failure — opens circuit after threshold failures."""
    if not domain:
        return

    if _circuit_breaker_rust is not None:
        _circuit_breaker_rust["record_failure"](domain, is_timeout)
        return

    _python_fallback_record_failure(domain, is_timeout)


def half_open_probe(domain: str) -> bool:
    """Record successful probe in HALF_OPEN state.

    Returns True if circuit should now be CLOSED.
    """
    if not domain:
        return False

    if _circuit_breaker_rust is not None:
        return _circuit_breaker_rust["half_open_probe"](domain)

    return _python_fallback_half_open_probe(domain)


def clear_all() -> None:
    """Clear all circuit breaker state (testing)."""
    if _circuit_breaker_rust is not None:
        _circuit_breaker_rust["clear_all"]()
        return

    _python_fallback_clear_all()


def get_stats(domain: str) -> tuple[int, int, int]:
    """Get circuit breaker stats.

    Returns (state, failure_count, last_failure_age_seconds).
    state: 0=CLOSED, 1=OPEN, 2=HALF_OPEN
    """
    if not domain:
        return (0, 0, 0)

    if _circuit_breaker_rust is not None:
        return _circuit_breaker_rust["get_stats"](domain)

    return _python_fallback_get_stats(domain)


# ---------------------------------------------------------------------------
# Python Fallback (delegates to transport.circuit_breaker)
# ---------------------------------------------------------------------------

_python_fallback_breaker_cache: dict[str, "_PythonFallbackState"] = {}


class _PythonFallbackState:
    """Minimal Python fallback state for circuit breaker."""

    __slots__ = ("failure_count", "last_failure", "state", "half_open_probes", "recovery_timeout")

    failure_count: int
    last_failure: float
    state: int  # 0=CLOSED, 1=OPEN, 2=HALF_OPEN
    half_open_probes: int
    recovery_timeout: float

    def __init__(self) -> None:
        self.failure_count = 0
        self.last_failure = 0.0
        self.state = 0  # CLOSED
        self.half_open_probes = 0
        self.recovery_timeout = _RECOVERY_TIMEOUT_S


def _python_fallback_is_open(domain: str) -> bool:
    """Python fallback for is_open."""
    import time

    if domain not in _python_fallback_breaker_cache:
        _python_fallback_breaker_cache[domain] = _PythonFallbackState()

    s = _python_fallback_breaker_cache[domain]
    if s.state == 0:  # CLOSED
        return False
    if s.state == 1:  # OPEN
        elapsed = time.monotonic() - s.last_failure
        if elapsed >= s.recovery_timeout:
            s.state = 2  # HALF_OPEN
            s.half_open_probes = 0
            return False
        return True
    # HALF_OPEN
    return False


def _python_fallback_record_success(domain: str) -> None:
    """Python fallback for record_success."""
    import time

    if domain not in _python_fallback_breaker_cache:
        return
    s = _python_fallback_breaker_cache[domain]
    s.failure_count = 0
    s.half_open_probes = 0
    s.state = 0  # CLOSED
    s.recovery_timeout = _RECOVERY_TIMEOUT_S
    _python_fallback_breaker_cache.pop(domain, None)


def _python_fallback_record_failure(domain: str, _is_timeout: bool) -> None:
    """Python fallback for record_failure."""
    import time

    if domain not in _python_fallback_breaker_cache:
        _python_fallback_breaker_cache[domain] = _PythonFallbackState()

    s = _python_fallback_breaker_cache[domain]
    s.last_failure = time.monotonic()
    s.failure_count += 1

    if s.failure_count >= _FAILURE_THRESHOLD:
        s.state = 1  # OPEN


def _python_fallback_half_open_probe(domain: str) -> bool:
    """Python fallback for half_open_probe."""
    if domain not in _python_fallback_breaker_cache:
        return False

    s = _python_fallback_breaker_cache[domain]
    s.half_open_probes += 1

    if s.half_open_probes >= _HALF_OPEN_PROBES:
        s.failure_count = 0
        s.half_open_probes = 0
        s.state = 0  # CLOSED
        s.recovery_timeout = _RECOVERY_TIMEOUT_S
        _python_fallback_breaker_cache.pop(domain, None)
        return True
    return False


def _python_fallback_clear_all() -> None:
    """Python fallback for clear_all."""
    _python_fallback_breaker_cache.clear()


def _python_fallback_get_stats(domain: str) -> tuple[int, int, int]:
    """Python fallback for get_stats."""
    import time

    if domain not in _python_fallback_breaker_cache:
        return (0, 0, 0)

    s = _python_fallback_breaker_cache[domain]
    age = int(time.monotonic() - s.last_failure) if s.last_failure > 0 else 0
    return (s.state, s.failure_count, age)


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

def is_rust_available() -> bool:
    """Return True if Rust circuit breaker is available."""
    return _circuit_breaker_rust is not None


def get_all_stats() -> dict[str, tuple[int, int, int]]:
    """Get stats for all tracked domains (Python fallback only)."""
    if _circuit_breaker_rust is not None:
        # Rust doesn't expose iteration — return empty for now
        # Could add a get_all_stats() function to Rust if needed
        return {}

    return {domain: _python_fallback_get_stats(domain) for domain in _python_fallback_breaker_cache}
