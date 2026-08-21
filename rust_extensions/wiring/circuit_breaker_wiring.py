"""
Circuit Breaker Rust Integration Wiring
======================================

Wires rust_extensions/src/circuit_breaker.rs to:
- fetching/ network operations
- transport/ session management

Purpose:
- Per-domain circuit breaker for fault tolerance
- Lock-free state machine (parking_lot::RwLock + AHashMap)
- PyO3 GIL-safe for async/ThreadPoolExecutor contexts

Integration Point:
- Network fetch operations with domain tracking
- Rate limiting and failure detection

Usage:
    from rust_extensions.wiring.circuit_breaker_wiring import circuit_breaker_wired
    
    allowed, reason = circuit_breaker_wired.should_allow_request("example.com")
    if allowed:
        try:
            result = await fetch(url)
            circuit_breaker_wired.record_success("example.com")
        except Exception as e:
            circuit_breaker_wired.record_failure("example.com")
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:

logger = logging.getLogger(__name__)

from rust_extensions.integrations import get_circuit_breaker

_circuit_breaker = get_circuit_breaker()

def circuit_breaker_wired():
    """Get the wired circuit breaker integration."""
    return _circuit_breaker

def should_allow_request(domain: str) -> tuple[bool, str]:
    """
    Check if request to domain should be allowed.

    Returns:
        (allowed, reason) tuple.
        reason: "circuit_closed", "circuit_half_open_recovery_probe",
               "circuit_open_failure_threshold_exceeded", etc.
    """
    return _circuit_breaker.should_allow_request(domain)

def record_success(domain: str) -> None:
    """Record successful request for domain."""
    _circuit_breaker.record_success(domain)

def record_failure(domain: str, is_timeout: bool = False) -> None:
    """
    Record failed request for domain.

    Args:
        domain: Domain that failed
        is_timeout: True if failure was due to timeout
    """
    _circuit_breaker.record_failure(domain, is_timeout)

def get_domain_state(domain: str) -> dict:
    """
    Get detailed state for a domain.

    Returns:
        Dict with state, failure_count, last_failure_time, etc.
    """
    return _circuit_breaker.get_domain_state(domain)

class CircuitBreakerContext:
    """
    Context manager for circuit breaker protection.

    Usage:
        async with CircuitBreakerContext("example.com") as cb:
            if not cb.allowed:
                raise CircuitOpenError(domain)
            result = await fetch(url)
    """

    __slots__ = ("_domain", "_allowed", "_reason")

    def __init__(self, domain: str) -> None:
        self._domain = domain
        self._allowed = True
        self._reason = ""

    def __enter__(self) -> "CircuitBreakerContext":
        self._allowed, self._reason = should_allow_request(self._domain)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type is not None:
            # Exception occurred - record failure
            is_timeout = "timeout" in str(exc_val).lower()
            record_failure(self._domain, is_timeout)
        elif self._allowed:
            # Success
            record_success(self._domain)

    @property
    def allowed(self) -> bool:
        """Check if request is allowed."""
        return self._allowed

    @property
    def reason(self) -> str:
        """Get the circuit state reason."""
        return self._reason

if _circuit_breaker.available:
    logger.info("[CircuitBreaker] Rust circuit_breaker.rs integration: ENABLED")
else:
    logger.info("[CircuitBreaker] Rust circuit_breaker.rs integration: DISABLED (using Python fallback)")

def get_aimd_window() -> float:
    """
    Get current AIMD Layer 2 window size.

    Returns the adaptive concurrency limit derived from circuit breaker failures.
    This value can be used to size semaphore acquisition for HTTP fetches.
    """
    try:
        from hledac_rust_extensions import circuit_breaker_aimd_get_window
        return circuit_breaker_aimd_get_window()
    except (ImportError, AttributeError):
        # Fallback: return reasonable default
        return 10.0

def reset_aimd() -> None:
    """Reset AIMD Layer 2 state (for testing)."""
    try:
        from hledac_rust_extensions import circuit_breaker_aimd_reset
        circuit_breaker_aimd_reset()
    except (ImportError, AttributeError):
        pass
