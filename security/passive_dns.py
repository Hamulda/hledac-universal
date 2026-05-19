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
import orjson

from hledac.universal.network.session_runtime import async_get_aiohttp_session
from hledac.universal.transport.circuit_breaker import checked_aiohttp_get

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared CIRCL PDNS parser (used by both DoH and discovery adapters)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CIRCLPDNSRecord:
    """Parsed CIRCL PDNS record — F207F."""

    ip: str
    rrname: str
    rrtype: str


def parse_circl_pdns_text(text: str, max_results: int = 50) -> list[CIRCLPDNSRecord]:
    """
    Parse CIRCL PDNS text response into structured records.

    Handles:
      - NDJSON (canonical CIRCL format): {"rrname":"...","rrtype":"A","rdata":"1.2.3.4"}
      - Legacy plain IP-per-line
      - CSV "ip,rrname,rrtype" fallback

    Skips:
      - Empty lines
      - Private/loopback IPs
      - Malformed JSON (fallback to plain IP)

    Args:
        text: Raw response text from CIRCL PDNS endpoint.
        max_results: Hard cap on records returned (default 50).

    Returns:
        List of CIRCLPDNSRecord, deduplicated by IP.
    """
    records: list[CIRCLPDNSRecord] = []
    seen_ips: set[str] = set()

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        ip: Optional[str] = None
        rrname = ""
        rrtype = ""

        # Try NDJSON first (canonical CIRCL format)
        try:
            record = orjson.loads(line)
            rdata = record.get("rdata", "")
            rrname = str(record.get("rrname", "")).strip()
            rrtype = str(record.get("rrtype", "")).strip()
            if rdata:
                ip = str(rdata).strip()
        except Exception:
            # Fallback: old plain IP-per-line or CSV "ip,rrname,rrtype"
            parts = line.split(",")
            candidate = parts[0].strip() if parts else ""
            if candidate:
                ip = candidate

        if not ip or _is_private_ip(ip):
            continue
        if ip in seen_ips:
            continue
        if len(records) >= max_results:
            break

        seen_ips.add(ip)
        records.append(CIRCLPDNSRecord(ip=ip, rrname=rrname, rrtype=rrtype))

    return records


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

# F229: Private IP filter — aligned with discovery/circl_pdns_adapter.py
_RFC1918_RE = re.compile(r"^(10\.|172\.(1[6-9]|2[0-9]|3[01])\.|192\.168\.)")
_LOCALHOST_RE = re.compile(r"^(127\.|::1|fe80:|localhost$)")
_LINKLOCAL_RE = re.compile(r"^(169\.254\.|fe80:)")


def _is_private_ip(ip: str) -> bool:
    """Return True if IP is private, loopback, or link-local."""
    if not ip:
        return True
    ip_stripped = ip.strip()
    if not ip_stripped:
        return True
    ip_lower = ip_stripped.lower()
    if _RFC1918_RE.match(ip_lower):
        return True
    if _LOCALHOST_RE.match(ip_lower):
        return True
    if _LINKLOCAL_RE.match(ip_lower):
        return True
    return False


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
    Legacy compatibility wrapper for CIRCL PDNS lookup.

    Prefer call_lookup_passive_dns() for runtime code because it returns
    PassiveDNSOutcome telemetry. This wrapper preserves the old list[str]
    contract.
    """
    ips, _ = await call_lookup_passive_dns(
        domain,
        session_provider=session_provider,
        fetch_func=fetch_func,
    )
    return ips


# F229: CIRCL PDNS record (legacy alias for CIRCLPDNSRecord)
CirclPdnsRecord = CIRCLPDNSRecord


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
    ips: list[str] = []

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

        # F229: Parse NDJSON CIRCL response using shared parser
        records = parse_circl_pdns_text(text, max_results=50)
        ips = [record.ip for record in records]

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
    "CirclPdnsRecord",
    "parse_circl_pdns_text",
    "DOH_ENDPOINTS",
    "transport_policy",
]
