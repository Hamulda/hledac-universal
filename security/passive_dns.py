#!/usr/bin/env python3
"""
Passive DNS — DoH (DNS-over-HTTPS) resolver and CIRCL PDNS lookup.

Providers:
  - cloudflare: https://cloudflare-dns.com/dns-query
  - google:     https://dns.google/resolve

Graceful degradation: returns [] on failure, never blocks pipeline.

Anti-patterns prevented:
  - No blocking socket ops (aiohttp only)
  - No hardcoded API keys (CIRCL PDNS is keyless)
  - Non-blocking: asyncio.sleep for rate limits, not blocking waits
  - Graceful degradation: [] return with WARNING log on any failure

F206AW Transport Seams:
  - Optional session_provider: inject a pre-configured aiohttp.ClientSession
  - Optional fetch_func: inject an async fetch(url, headers) -> bytes
  - Canonical circuit breaker preflight via domain_breaker_check
  - transport_policy telemetry: "injected" | "local_fallback" | "bypass_legacy"
  - NO import-time session creation
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Optional

import aiohttp

logger = logging.getLogger(__name__)

DOH_ENDPOINTS: dict[str, str] = {
    "cloudflare": "https://cloudflare-dns.com/dns-query",
    "google": "https://dns.google/resolve",
}

CIRCL_PDNS_URL: str = "https://www.circl.lu/pdns/query"
CIRCL_RATE_LIMIT_SLEEP: float = 2.0  # 30 req/min → 2s between requests

# F206AW: Transport policy for telemetry
# "injected" = uses caller-provided session/fetch_func
# "local_fallback" = uses canonical circuit breaker check then local aiohttp
# "bypass_legacy" = uses internal ephemeral sessions (original behavior)
transport_policy: str = "bypass_legacy"

# F206AW: Circuit breaker — lazily imported to avoid import-time side effects
_circuit_breaker_check: Optional[Callable[[str], Any]] = None


def _get_circuit_breaker():
    """Lazily import domain_breaker_check. Returns None if unavailable."""
    global _circuit_breaker_check
    if _circuit_breaker_check is None:
        try:
            from transport.circuit_breaker import domain_breaker_check
            _circuit_breaker_check = domain_breaker_check
        except ImportError:
            _circuit_breaker_check = None
    return _circuit_breaker_check


def _try_domain_breaker_check(domain: str) -> Any:
    """Fail-soft circuit breaker check. Returns None if breaker unavailable."""
    if not domain:
        return None
    cb = _get_circuit_breaker()
    if cb is not None:
        try:
            return cb(domain)
        except Exception:
            pass
    return None


async def resolve_doh(
    domain: str,
    provider: str = "cloudflare",
    session_provider: Optional[aiohttp.ClientSession] = None,
    fetch_func: Optional[Callable[..., Any]] = None,
) -> list[str]:
    """
    Resolve hostname via DNS-over-HTTPS (DoH).

    Args:
        domain: Domain name to resolve (e.g. "example.com")
        provider: DoH provider — "cloudflare" (default) or "google"
        session_provider: Optional pre-configured aiohttp.ClientSession.
            When provided, takes precedence over internal ephemeral session.
            Enables canonical transport seam (shared session, circuit breaker).
        fetch_func: Optional async fetch(url, headers) -> bytes.
            When provided along with session_provider, uses both for fetch.

    Returns:
        List of IP addresses (A records), or [] on failure.

    Anti-patterns prevented:
      - Non-blocking aiohttp
      - Graceful degradation: [] return on any error
      - Accept: application/dns-json header
    """
    global transport_policy

    if provider not in DOH_ENDPOINTS:
        logger.warning(f"Unknown DoH provider: {provider} — using cloudflare")
        provider = "cloudflare"

    endpoint = DOH_ENDPOINTS[provider]
    url = f"{endpoint}?name={domain}&type=A"

    headers = {
        "Accept": "application/dns-json",
    }

    timeout = aiohttp.ClientTimeout(total=15)
    ips: list[str] = []

    # F206AW: Circuit breaker preflight
    circuit_decision = _try_domain_breaker_check(domain)
    if circuit_decision is not None and not circuit_decision.allowed:
        logger.debug(
            f"DoH circuit breaker blocked {domain}: "
            f"{circuit_decision.reason} (retry in {circuit_decision.retry_after_s:.1f}s)"
        )
        return []

    # F206AW: Determine transport policy
    if session_provider is not None or fetch_func is not None:
        transport_policy = "injected"
    else:
        transport_policy = "local_fallback"

    try:
        if fetch_func is not None:
            # Use injected fetch function
            result = await fetch_func(url, headers)
            data = result if isinstance(result, dict) else {}
        elif session_provider is not None:
            # Use injected session
            async with session_provider.get(url, headers=headers, allow_redirects=True) as resp:
                if resp.status != 200:
                    logger.warning(f"DoH {provider} failed for {domain}: HTTP {resp.status}")
                    return []
                text = await resp.text()
                import json
                try:
                    data = json.loads(text)
                except (json.JSONDecodeError, Exception):
                    logger.warning(f"DoH {provider} invalid JSON for {domain}")
                    return []
        else:
            # Local fallback: ephemeral session
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=headers, allow_redirects=True) as resp:
                    if resp.status != 200:
                        logger.warning(
                            f"DoH {provider} failed for {domain}: HTTP {resp.status}"
                        )
                        return []

                    # DoH endpoints return JSON regardless of Content-Type header
                    text = await resp.text()
                    import json
                    try:
                        data = json.loads(text)
                    except (json.JSONDecodeError, Exception):
                        logger.warning(f"DoH {provider} invalid JSON for {domain}")
                        return []

        answers = data.get("Answer", []) if isinstance(data, dict) else []

        for answer in answers:
            # A records have type=1
            if answer.get("type") == 1:
                ip = answer.get("data", "")
                if ip:
                    ips.append(ip)

        if not ips:
            logger.debug(f"DoH {provider} returned no A records for {domain}")

    except asyncio.TimeoutError:
        logger.warning(f"DoH timeout for {domain}")
    except Exception as e:
        logger.warning(f"DoH error for {domain}: {e}")

    return ips


async def lookup_passive_dns(
    domain: str,
    session_provider: Optional[aiohttp.ClientSession] = None,
    fetch_func: Optional[Callable[..., Any]] = None,
) -> list[str]:
    """
    Lookup passive DNS records via CIRCL PDNS API (keyless, rate-limited).

    Args:
        domain: Domain to query (e.g. "example.com")
        session_provider: Optional pre-configured aiohttp.ClientSession.
            When provided, takes precedence over internal ephemeral session.
            Enables canonical transport seam (shared session, circuit breaker).
        fetch_func: Optional async fetch(url) -> str (plain text).
            When provided along with session_provider, uses both for fetch.

    Returns:
        List of IP addresses seen for this domain, or [] if unavailable.

    Anti-patterns prevented:
      - CIRCL is free but rate-limited: 30 req/min → sleep between calls
      - Never blocks pipeline: returns [] if CIRCL is down
      - Non-blocking aiohttp
      - Graceful degradation: [] return with WARNING on any failure
    """
    global transport_policy

    url = f"{CIRCL_PDNS_URL}/{domain}"
    timeout = aiohttp.ClientTimeout(total=15)
    ips: list[str] = []
    seen: set[str] = set()

    # F206AW: Circuit breaker preflight
    circuit_decision = _try_domain_breaker_check(domain)
    if circuit_decision is not None and not circuit_decision.allowed:
        logger.debug(
            f"CIRCL PDNS circuit breaker blocked {domain}: "
            f"{circuit_decision.reason} (retry in {circuit_decision.retry_after_s:.1f}s)"
        )
        return []

    # F206AW: Determine transport policy
    if session_provider is not None or fetch_func is not None:
        transport_policy = "injected"
    else:
        transport_policy = "local_fallback"

    try:
        if fetch_func is not None:
            # Use injected fetch function (expects plain text like CIRCL response)
            text = await fetch_func(url)
        elif session_provider is not None:
            # Use injected session
            async with session_provider.get(url) as resp:
                if resp.status == 404:
                    return []
                if resp.status != 200:
                    logger.warning(f"CIRCL PDNS returned HTTP {resp.status} for {domain}")
                    return []
                text = await resp.text()
        else:
            # Local fallback: ephemeral session
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as resp:
                    if resp.status == 404:
                        # Domain not in PDNS — not an error, just empty
                        return []
                    if resp.status != 200:
                        logger.warning(
                            f"CIRCL PDNS returned HTTP {resp.status} for {domain}"
                        )
                        return []

                    # CIRCL returns one IP per line, plain text
                    text = await resp.text()

        # Parse plain text response (used by both injected and local paths)
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            # Each line is an IP address (possibly with timestamp suffix)
            # Format: ipaddress[,timestamp]
            parts = line.split(",")
            ip = parts[0].strip()
            if ip and ip not in seen:
                seen.add(ip)
                ips.append(ip)

        if not ips:
            logger.debug(f"CIRCL PDNS returned no records for {domain}")

    except asyncio.TimeoutError:
        logger.warning(f"CIRCL PDNS timeout for {domain}")
    except Exception as e:
        logger.warning(f"CIRCL PDNS lookup error for {domain}: {e}")

    # Rate limiting: sleep before returning
    await asyncio.sleep(CIRCL_RATE_LIMIT_SLEEP)
    return ips


__all__ = [
    "resolve_doh",
    "lookup_passive_dns",
    "DOH_ENDPOINTS",
    "transport_policy",
]
