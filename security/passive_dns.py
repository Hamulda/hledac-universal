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
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

import aiohttp

from hledac.universal.network.session_runtime import async_get_aiohttp_session
from hledac.universal.transport.circuit_breaker import checked_aiohttp_get

logger = logging.getLogger(__name__)


# F207F: PassiveDNS outcome schema
@dataclass(frozen=True)
class PassiveDNSOutcome:
    """
    Normalized PassiveDNS adapter outcome — F207F.

    Fields:
        attempted:     True if network call was made.
        query:        Domain/IP that was queried.
        result_count: IP records returned (0 if not attempted or on error).
        error:        Error tag string or None on success.
        timeout:      True if call timed out.
        duration_s:   Wall-clock seconds for the call.
        skip_reason:  Reason for skip or None if attempted.
    """
    attempted: bool = False
    query: str = ""
    result_count: int = 0
    error: str | None = None
    timeout: bool = False
    duration_s: float = 0.0
    skip_reason: str | None = None

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


def _is_ip_address(value: str) -> bool:
    """Return True if value looks like an IP address (v4 or v6)."""
    if re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", value):
        return True
    if ":" in value and re.match(r"^[0-9a-fA-F:]+$", value):
        return True
    return False


def _looks_like_domain(value: str) -> bool:
    """Return True if value looks like a domain name."""
    if not value or len(value) > 253:
        return False
    if "." not in value:
        return False
    if _is_ip_address(value):
        return False
    parts = value.split(".")
    if len(parts) < 2:
        return False
    tld = parts[-1]
    if len(tld) < 1 or len(tld) > 63:
        return False
    if not re.match(r"^[a-z0-9.\-_]+$", tld):
        return False
    return True


async def call_lookup_passive_dns(
    domain: str,
    session_provider: Optional[aiohttp.ClientSession] = None,
    fetch_func: Optional[Callable[..., Any]] = None,
) -> tuple[list[str], PassiveDNSOutcome]:
    """
    CIRCL PDNS lookup with normalized outcome — F207F.

    Returns (ips, outcome) so callers can measure yield without changing
    the existing list[str] contract.

    Args:
        domain:          Domain or IP to query.
        session_provider: Optional pre-configured aiohttp.ClientSession.
        fetch_func:      Optional async fetch(url) -> str (plain text).

    Returns:
        (list of IPs, PassiveDNSOutcome) tuple.
        outcome.attempted=True on every code path including skips.
        outcome.skip_reason is set when query is not a valid domain/IP.
    """
    start = time.monotonic()

    # F207F: domain/IP validation — skip non-qualifying queries
    if not domain or not domain.strip():
        elapsed = time.monotonic() - start
        outcome = PassiveDNSOutcome(
            attempted=True,
            query=domain,
            result_count=0,
            error=None,
            skip_reason="empty_query",
            duration_s=elapsed,
        )
        return [], outcome

    domain_stripped = domain.strip()
    if not _looks_like_domain(domain_stripped) and not _is_ip_address(domain_stripped):
        elapsed = time.monotonic() - start
        outcome = PassiveDNSOutcome(
            attempted=True,
            query=domain_stripped,
            result_count=0,
            error=None,
            skip_reason="not_domain_or_ip",
            duration_s=elapsed,
        )
        return [], outcome

    url = f"{CIRCL_PDNS_URL}/{domain_stripped}"
    timeout = aiohttp.ClientTimeout(total=15)
    ips: list[str] = []
    seen: set[str] = set()

    # F206AW: Circuit breaker preflight
    circuit_decision = _try_domain_breaker_check(domain_stripped)
    if circuit_decision is not None and not circuit_decision.allowed:
        elapsed = time.monotonic() - start
        outcome = PassiveDNSOutcome(
            attempted=True,
            query=domain_stripped,
            result_count=0,
            error=f"circuit_breaker:{circuit_decision.reason}",
            timeout=False,
            duration_s=elapsed,
        )
        return [], outcome

    # F206AW: Determine transport policy
    global transport_policy
    if session_provider is not None or fetch_func is not None:
        transport_policy = "injected"
    else:
        transport_policy = "local_fallback"

    try:
        if fetch_func is not None:
            text = await fetch_func(url)
        elif session_provider is not None:
            async with session_provider.get(url) as resp:
                if resp.status == 404:
                    elapsed = time.monotonic() - start
                    outcome = PassiveDNSOutcome(
                        attempted=True,
                        query=domain_stripped,
                        result_count=0,
                        error=None,
                        duration_s=elapsed,
                    )
                    await asyncio.sleep(CIRCL_RATE_LIMIT_SLEEP)
                    return [], outcome
                if resp.status != 200:
                    elapsed = time.monotonic() - start
                    outcome = PassiveDNSOutcome(
                        attempted=True,
                        query=domain_stripped,
                        result_count=0,
                        error=f"http_{resp.status}",
                        duration_s=elapsed,
                    )
                    await asyncio.sleep(CIRCL_RATE_LIMIT_SLEEP)
                    return [], outcome
                text = await resp.text()
        else:
            # F229: Align with canonical transport seam
            session = await async_get_aiohttp_session()
            http_timeout = aiohttp.ClientTimeout(total=15)

            resp, err = await checked_aiohttp_get(
                session,
                url,
                headers={"User-Agent": "Hledac/1.0 (research bot)"},
                timeout=http_timeout,
                failure_kind="circl_pdns",
            )

            if err:
                elapsed = time.monotonic() - start
                is_timeout = err == "timeout"
                outcome = PassiveDNSOutcome(
                    attempted=True,
                    query=domain_stripped,
                    result_count=0,
                    error=err,
                    timeout=is_timeout,
                    duration_s=elapsed,
                )
                await asyncio.sleep(CIRCL_RATE_LIMIT_SLEEP)
                return [], outcome

            assert resp is not None
            if resp.status == 404:
                elapsed = time.monotonic() - start
                outcome = PassiveDNSOutcome(
                    attempted=True,
                    query=domain_stripped,
                    result_count=0,
                    error=None,
                    duration_s=elapsed,
                )
                await asyncio.sleep(CIRCL_RATE_LIMIT_SLEEP)
                return [], outcome
            if resp.status != 200:
                elapsed = time.monotonic() - start
                outcome = PassiveDNSOutcome(
                    attempted=True,
                    query=domain_stripped,
                    result_count=0,
                    error=f"http_{resp.status}",
                    duration_s=elapsed,
                )
                await asyncio.sleep(CIRCL_RATE_LIMIT_SLEEP)
                return [], outcome
            text = await resp.text()

        # Parse plain text response
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(",")
            ip = parts[0].strip()
            if ip and ip not in seen:
                seen.add(ip)
                ips.append(ip)

    except asyncio.TimeoutError:
        elapsed = time.monotonic() - start
        outcome = PassiveDNSOutcome(
            attempted=True,
            query=domain_stripped,
            result_count=0,
            error="timeout",
            timeout=True,
            duration_s=elapsed,
        )
        await asyncio.sleep(CIRCL_RATE_LIMIT_SLEEP)
        return [], outcome
    except Exception as e:
        elapsed = time.monotonic() - start
        outcome = PassiveDNSOutcome(
            attempted=True,
            query=domain_stripped,
            result_count=0,
            error=str(e),
            duration_s=elapsed,
        )
        await asyncio.sleep(CIRCL_RATE_LIMIT_SLEEP)
        return [], outcome

    elapsed = time.monotonic() - start
    outcome = PassiveDNSOutcome(
        attempted=True,
        query=domain_stripped,
        result_count=len(ips),
        error=None,
        duration_s=elapsed,
    )
    await asyncio.sleep(CIRCL_RATE_LIMIT_SLEEP)
    return ips, outcome


__all__ = [
    "resolve_doh",
    "lookup_passive_dns",
    "call_lookup_passive_dns",
    "PassiveDNSOutcome",
    "DOH_ENDPOINTS",
    "transport_policy",
]
