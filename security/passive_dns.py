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
"""
from __future__ import annotations

import asyncio
import logging
import aiohttp

logger = logging.getLogger(__name__)

DOH_ENDPOINTS: dict[str, str] = {
    "cloudflare": "https://cloudflare-dns.com/dns-query",
    "google": "https://dns.google/resolve",
}

CIRCL_PDNS_URL: str = "https://www.circl.lu/pdns/query"
CIRCL_RATE_LIMIT_SLEEP: float = 2.0  # 30 req/min → 2s between requests


async def resolve_doh(domain: str, provider: str = "cloudflare") -> list[str]:
    """
    Resolve hostname via DNS-over-HTTPS (DoH).

    Args:
        domain: Domain name to resolve (e.g. "example.com")
        provider: DoH provider — "cloudflare" (default) or "google"

    Returns:
        List of IP addresses (A records), or [] on failure.

    Anti-patterns prevented:
      - Non-blocking aiohttp
      - Graceful degradation: [] return on any error
      - Accept: application/dns-json header
    """
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

    try:
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


async def lookup_passive_dns(domain: str) -> list[str]:
    """
    Lookup passive DNS records via CIRCL PDNS API (keyless, rate-limited).

    Args:
        domain: Domain to query (e.g. "example.com")

    Returns:
        List of IP addresses seen for this domain, or [] if unavailable.

    Anti-patterns prevented:
      - CIRCL is free but rate-limited: 30 req/min → sleep between calls
      - Never blocks pipeline: returns [] if CIRCL is down
      - Non-blocking aiohttp
      - Graceful degradation: [] return with WARNING on any failure
    """
    url = f"{CIRCL_PDNS_URL}/{domain}"
    timeout = aiohttp.ClientTimeout(total=15)
    ips: list[str] = []
    seen: set[str] = set()

    try:
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
]
