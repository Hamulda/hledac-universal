"""
Sprint F235: Shodan Intelligence Lane

External intelligence lane for Shodan (device/IP fingerprints, banners, vulns).
High-value unindexed data: Shodan captures service banners, port data, vulnerabilities,
geolocation, and ASN context that Google/Censys don't surface.

Pattern: AcquisitionLane via BGPLane-style async class.
Fail-soft: if SHODAN_API_KEY absent → return [] with warning log.
Rate limit: TokenBucket "shodan_api" (1 req/sec for free tier).

GHOST_INVARIANTS:
  - External API calls go directly to Shodan (specialized TI source, not general web)
  - API key never logged / never in payload_text / never exported
  - Rate limiting via TokenBucket (not sleep)
  - Always returns CanonicalFinding list (empty on failure)
"""

from __future__ import annotations

import logging
import os
import time
from typing import List, Optional

import aiohttp

from hledac.universal.knowledge.duckdb_store import CanonicalFinding
from hledac.universal.utils.rate_limiters import get_limiter

logger = logging.getLogger(__name__)

SHODAN_SEARCH_API = "https://api.shodan.io/shodan/host/search"
RATE_LIMIT_KEY = "shodan_api"


def _get_api_key() -> Optional[str]:
    return os.environ.get("SHODAN_API_KEY") or None


def _build_findings(query: str, raw_results: List[dict], ts_now: float) -> List[CanonicalFinding]:
    findings = []
    for host in raw_results:
        ip = host.get("ip", "") or ""
        port = host.get("port", 0)
        banner = host.get("banner", "") or ""
        hostnames = host.get("hostnames", []) or []

        if not ip:
            continue

        confidence = 0.9  # verified external source
        if len(banner) < 50:
            confidence = 0.85
        elif len(banner) > 300:
            confidence = 0.92

        hostname_str = ",".join(hostnames) if hostnames else ""

        finding = CanonicalFinding(
            finding_id=f"shodan_{ip}_{port}_{int(ts_now * 1000)}",
            query=f"shodan:{query}",
            source_type="shodan_intel",
            confidence=confidence,
            ts=ts_now,
            provenance=("shodan_intel", query, ip, str(port)),
            payload_text=f"{ip}:{port} {banner[:300]}{'...' if len(banner) > 300 else ''} hostnames={hostname_str}",
        )
        findings.append(finding)

    return findings


async def search_shodan_lane(
    query: str,
    limit: int = 20,
    api_key: Optional[str] = None,
) -> tuple[List[CanonicalFinding], List[dict]]:
    """
    Search Shodan and return CanonicalFindings.

    Args:
        query: Shodan search query (e.g. "apache", "8.8.8.8")
        limit: Maximum results (default 20, max 100)
        api_key: Optional API key (uses env SHODAN_API_KEY if not passed)

    Returns:
        Tuple of (findings, raw_results) — raw_results preserved for pivot side effect.
    """
    bucket = get_limiter(RATE_LIMIT_KEY)
    await bucket.acquire()

    key = api_key or _get_api_key()

    if not key:
        logger.warning("[SHODAN] No API key — skipping Shodan lane")
        return [], []

    params = {"key": key, "query": query, "per_page": min(limit, 100)}

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            async with session.get(SHODAN_SEARCH_API, params=params) as resp:
                if resp.status == 401:
                    logger.warning("[SHODAN] API key required or invalid")
                    return [], []
                if resp.status == 429:
                    logger.warning("[SHODAN] Rate limit hit")
                    return [], []
                if resp.status != 200:
                    logger.warning(f"[SHODAN] API error: {resp.status}")
                    return [], []

                data = await resp.json()
                matches = data.get("matches", []) if isinstance(data, dict) else []

                ts_now = time.time()
                raw_results = []
                seen_ips = set()

                for host in matches:
                    ip = host.get("ip_str", host.get("ip", ""))
                    if not ip or ip in seen_ips:
                        continue
                    seen_ips.add(ip)

                    normalized = {
                        "ip": ip,
                        "port": host.get("port", 0),
                        "banner": host.get("data", host.get("banner", "")),
                        "hostnames": host.get("hostnames", []),
                        "vulns": host.get("vulns", {}),
                        "tags": host.get("tags", []),
                    }
                    raw_results.append(normalized)

                    if len(raw_results) >= limit:
                        break

                findings = _build_findings(query, raw_results, ts_now)
                logger.debug(f"[SHODAN] query='{query}' → {len(findings)} findings")
                return findings, raw_results

    except Exception as e:
        logger.warning(f"[SHODAN] search error: {e}")
        return [], []


# ── ShodanLane adapter ───────────────────────────────────────────────────────


class ShodanLane:
    """
    Shodan intelligence lane — IP/device fingerprint enrichment.

    query(target) → list[CanonicalFinding]
      target: IP address, CIDR, or Shodan search query

    fail-soft: returns [] if no API key or on error
    """

    __slots__ = ("_stats",)

    def __init__(self) -> None:
        self._stats = {
            "queries": 0,
            "findings": 0,
            "errors": 0,
        }

    async def query(self, target: str) -> List[CanonicalFinding]:
        """Query Shodan for target (IP, CIDR, or keyword)."""
        self._stats["queries"] += 1
        findings, _ = await search_shodan_lane(target, limit=20)
        self._stats["findings"] += len(findings)
        if not findings:
            self._stats["errors"] += 1
        return findings

    def get_stats(self) -> dict:
        return self._stats.copy()


__all__ = [
    "ShodanLane",
    "search_shodan_lane",
]