"""
Shared utilities for external intelligence search lanes (Censys, Shodan, etc.).

DRY patterns extracted from multiple search lane implementations:
- Circuit breaker integration (fail-soft)
- Gaussian jitter for SIEM fingerprint defense
- HTTP error handling helpers

Usage:
    from hledac.universal.recon.search_lane_utils import (
        circuit_breaker_check,
        record_success,
        record_failure,
        jittered_request,
    )
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from hledac.universal.transport.circuit_breaker import (
    domain_breaker_check,
    domain_breaker_record_failure,
    domain_breaker_record_success,
)

logger = logging.getLogger(__name__)


# ── Circuit Breaker Integration ─────────────────────────────────────────────────


def circuit_breaker_check(domain: str) -> Any | None:
    """
    Fail-soft domain circuit breaker check.

    Returns:
        None if CB unavailable or circuit closed (request allowed).
        Decision object with .allowed == False if circuit is open.

    CLONE SOURCE:
        - recon/censys_lane.py::_try_domain_breaker_check()
        - recon/shodan_lane.py::_try_domain_breaker_check()
    """
    try:
        return domain_breaker_check(domain)
    except Exception:  # noqa: BLE001
        return None


def record_success(domain: str) -> None:
    """
    Record successful API call to circuit breaker.

    CLONE SOURCE:
        - recon/censys_lane.py::_record_censys_success()
        - recon/shodan_lane.py::_record_shodan_success()
    """
    try:
        domain_breaker_record_success(domain)
    except Exception:  # noqa: BLE001
        pass


def record_failure(domain: str, is_timeout: bool = False, failure_kind: str = "") -> None:
    """
    Record failed API call to circuit breaker.

    CLONE SOURCE:
        - recon/censys_lane.py::_record_censys_failure()
        - recon/shodan_lane.py::_record_shodan_failure()
    """
    try:
        domain_breaker_record_failure(domain, is_timeout=is_timeout, failure_kind=failure_kind)
    except Exception:  # noqa: BLE001
        pass


# ── Gaussian Jitter ─────────────────────────────────────────────────────────────


async def apply_jitter(sigma_s: float, service_name: str = "API") -> None:
    """
    Apply Gaussian jitter to decorrelate request bursts (SIEM fingerprint defense).

    [FINAL]-019: Anti-correlation jitter pattern.
    Skipped in BLITZ mode (short sprint) per is_blitz_mode().

    Args:
        sigma_s: Standard deviation in seconds (e.g. 0.6 for Censys, 0.8 for Shodan)
        service_name: Service name for logging (e.g. "CENSYS", "SHODAN")

    CLONE SOURCE:
        - recon/censys_lane.py (Gaussian jitter block)
        - recon/shodan_lane.py (Gaussian jitter block)
    """
    if sigma_s <= 0:
        return

    try:
        from hledac.universal._core.telemetry.context_state import is_blitz_mode

        if is_blitz_mode():
            return  # Skip jitter in BLITZ mode

        import random as _rng

        await asyncio.sleep(abs(_rng.gauss(0.0, sigma_s)))
    except Exception:  # noqa: BLE001
        pass  # fail-soft: jitter is best-effort


# ── HTTP Error Mapping ──────────────────────────────────────────────────────────


def http_status_to_failure_kind(status_code: int) -> str:
    """
    Map HTTP status codes to circuit breaker failure kinds.

    Standard mapping used across search lanes:
        401 -> "auth_error"
        403 -> "forbidden"
        429 -> "rate_limit"
        _ -> "http_error"
    """
    if status_code == 401:
        return "auth_error"
    if status_code == 403:
        return "forbidden"
    if status_code == 429:
        return "rate_limit"
    return "http_error"
