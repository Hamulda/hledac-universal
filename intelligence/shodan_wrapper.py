#!/usr/bin/env python3
"""
Shodan Wrapper — Passive host discovery via Shodan API.

Free tier: 10 queries/hour with rate limiting.
Tor routing: optional SOCKS5 via aiohttp-socks.
Graceful degradation: returns [] on API key absence or rate limit.

Anti-patterns prevented:
  - No hardcoded API keys (received as param or config)
  - No blocking pipeline on rate limit (async sleep)
  - Non-blocking aiohttp only
  - Always returns valid dict shape (missing fields = empty value)
"""
from __future__ import annotations

import asyncio
import logging
import time

import aiohttp

logger = logging.getLogger(__name__)

SHODAN_FREE_API: str = "https://api.shodan.io/shodan/host/search"
SHODAN_LDNS_API: str = "https://api.shodan.io/dns/domains"
RATE_LIMIT_SLEEP: float = 360.0 / 10  # 10 queries/hour → 36s between requests


def _normalize_record(host: dict) -> dict:
    """
    Normalize Shodan record to required contract.

    Always returns keys: ip, port, banner, hostnames
    Missing fields default to empty value (not KeyError).
    """
    return {
        "ip": host.get("ip_str", host.get("ip", "")),
        "port": host.get("port", 0),
        "banner": host.get("data", host.get("banner", "")),
        "hostnames": host.get("hostnames", []),
    }


async def search_shodan(
    query: str,
    limit: int = 10,
    api_key: str | None = None,
    use_tor: bool = False,
) -> list[dict]:
    """
    Search Shodan for hosts matching query.

    Args:
        query: Shodan search query (e.g. "apache", "nginx product:Apache")
        limit: Maximum results to return (default 10)
        api_key: Shodan API key. If None, uses free tier.
        use_tor: Route via Tor SOCKS5 proxy (127.0.0.1:9050)

    Returns:
        List of dicts with keys: ip, port, banner, hostnames

    Anti-patterns prevented:
      - Rate limiting via asyncio.sleep (free tier: 10/hour)
      - Graceful degradation: 401 → empty list + WARNING
      - Non-blocking aiohttp only
      - Always valid dict shape (missing fields = empty value)
    """
    results: list[dict] = []
    seen_ips: set[str] = set()

    # Build connector (Tor if requested)
    connector: aiohttp.TCPConnector | None = None
    if use_tor:
        try:
            from aiohttp_socks import ProxyConnector
            connector = ProxyConnector.from_url("socks5://127.0.0.1:9050", rdns=True)
        except ImportError:
            logger.warning("aiohttp_socks not available for Tor routing")
            use_tor = False

    # Determine API endpoint and auth
    if api_key:
        # Paid/freemium API key
        url = SHODAN_FREE_API
        params = {"key": api_key, "query": query, "per_page": min(limit, 100)}
    else:
        # Free tier (limited)
        url = SHODAN_FREE_API
        params = {"key": "free", "query": query, "per_page": min(limit, 100)}

    client_timeout = aiohttp.ClientTimeout(total=30)

    async def _do_request() -> dict | None:
        """Perform one HTTP request to Shodan."""
        nonlocal connector
        session_kwargs: dict = {"timeout": client_timeout}
        if connector is not None:
            session_kwargs["connector"] = connector
        async with aiohttp.ClientSession(**session_kwargs) as session:
            async with session.get(url, params=params) as resp:
                if resp.status == 401:
                    logger.warning("Shodan API key required")
                    return None
                if resp.status == 429:
                    logger.warning("Shodan rate limit hit — backing off")
                    return {"error": "rate_limited"}
                if resp.status != 200:
                    logger.warning(f"Shodan API error: {resp.status}")
                    return None

                data = await resp.json()
                return data

    try:
        data = await _do_request()
        if data is None:
            return []

        if isinstance(data, dict) and data.get("error"):
            if data["error"] == "rate_limited":
                # Sleep and retry once
                await asyncio.sleep(RATE_LIMIT_SLEEP)
                data = await _do_request()
                if data is None:
                    return []

        matches = data.get("matches", []) if isinstance(data, dict) else []
        for host in matches:
            ip = (host.get("ip_str") or host.get("ip") or "")
            if ip in seen_ips:
                continue
            seen_ips.add(ip)

            normalized = _normalize_record(host)
            results.append(normalized)

            if len(results) >= limit:
                break

    except TimeoutError:
        logger.warning(f"Shodan request timeout for query: {query}")
    except Exception as e:
        logger.warning(f"Shodan search error: {e}")

    # Rate limiting: sleep between free tier requests
    if not api_key:
        await asyncio.sleep(RATE_LIMIT_SLEEP)

    logger.debug(f"search_shodan('{query}', limit={limit}): {len(results)} results")
    return results


async def search_shodan_to_findings(
    query: str,
    limit: int = 10,
    api_key: str | None = None,
    use_tor: bool = False,
) -> tuple[list[CanonicalFinding], list[dict]]:
    """
    Sprint F195G: Convert Shodan search results to CanonicalFinding list.

    Returns:
        Tuple of (findings, raw_results) — raw_results preserved for pivot side effect.

    CanonicalFinding fields:
        - source_type: "shodan_search"
        - query: the search query
        - confidence: derived from banner richness (0.65-0.85)
        - payload_text: ip:port banner snippet
    """
    raw_results = await search_shodan(query, limit=limit, api_key=api_key, use_tor=use_tor)

    from hledac.universal.knowledge.duckdb_store import CanonicalFinding

    findings: list[CanonicalFinding] = []
    ts_now = time.time()

    for host in raw_results:
        ip = host.get("ip", "")
        port = host.get("port", 0)
        banner = host.get("banner", "")
        hostnames = host.get("hostnames", [])

        if not ip:
            continue

        # Confidence based on banner content richness
        confidence = 0.65
        if len(banner) > 100:
            confidence = 0.75
        if len(banner) > 500 or hostnames:
            confidence = 0.80
        # High-value service ports get a boost on top of rich content
        if port in (22, 443, 80) and banner and len(banner) > 10:
            confidence = 0.85

        hostname_str = ",".join(hostnames) if hostnames else ""

        finding = CanonicalFinding(
            finding_id=f"shodan_{ip}_{port}_{int(ts_now * 1000)}",
            query=f"shodan_search:{query}",
            source_type="shodan_search",
            confidence=confidence,
            ts=ts_now,
            provenance=("shodan_search", query, ip, str(port)),
            payload_text=f"{ip}:{port} {banner[:200]}{'...' if len(banner) > 200 else ''} hostname={hostname_str}",
        )
        findings.append(finding)

    return findings, raw_results


__all__ = [
    "search_shodan",
    "search_shodan_to_findings",
    "RATE_LIMIT_SLEEP",
]
