"""
Canonical HTTP utilities — circuit-breaker protected JSON fetch.
Moved from compat/core_http.py (F350M-R A-01).
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from hledac.universal.transport.circuit_breaker import (
    domain_breaker_check,
    domain_breaker_record_failure,
    domain_breaker_record_success,
)
from hledac.universal.transport.session_pool import session_pool

logger = logging.getLogger(__name__)


async def fetch_json(url: str, timeout: float = 30.0, **kwargs: Any) -> dict[str, Any]:
    """Async JSON fetcher via session_pool — circuit-breaker protected."""
    from urllib.parse import urlparse

    domain = urlparse(url).netloc
    decision = domain_breaker_check(domain)
    if not decision.allowed:
        raise httpx.HTTPError(f"circuit_breaker_open:{decision.reason}")

    client = await session_pool.httpx()
    try:
        resp = await client.get(url, timeout=timeout, **kwargs)
        resp.raise_for_status()
        domain_breaker_record_success(domain)
        return resp.json()
    except Exception as e:
        kind = getattr(e, "response", None)
        failure_kind = f"{type(e).__name__}:{getattr(kind, 'status_code', 0)}" if kind else type(e).__name__
        domain_breaker_record_failure(domain, failure_kind=failure_kind)
        raise


async def safe_fetch(url: str, timeout: float = 30.0, **kwargs: Any) -> dict[str, Any] | None:
    """Async text fetcher — returns None on failure, circuit-breaker protected."""
    from urllib.parse import urlparse

    domain = urlparse(url).netloc
    decision = domain_breaker_check(domain)
    if not decision.allowed:
        logger.debug(f"safe_fetch skipped (CB open) for {url}")
        return None

    client = await session_pool.httpx()
    try:
        resp = await client.get(url, timeout=timeout, **kwargs)
        resp.raise_for_status()
        domain_breaker_record_success(domain)
        return resp.json()
    except Exception as e:
        kind = getattr(e, "response", None)
        failure_kind = f"{type(e).__name__}:{getattr(kind, 'status_code', 0)}" if kind else type(e).__name__
        domain_breaker_record_failure(domain, failure_kind=failure_kind)
        logger.warning(f"safe_fetch failed for {url}: {e}")
        return None
