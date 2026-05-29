"""
Sprint F235: Censys Intelligence Lane

External intelligence lane for Censys (certificate transparency, port scans).
High-value unindexed data: Censys has comprehensive internet-wide scanning data
that Google doesn't index — certificates, TLS banners, host attributes.

Pattern: AcquisitionLane via BGPLane-style async class.
Fail-soft: if CENSYS_API_ID/CENSYS_SECRET absent → return [] with warning log.
Rate limit: TokenBucket "censys_api" (0.4 req/sec for free tier — Censys free is 0.4/s).

GHOST_INVARIANTS:
  - External API calls go directly to Censys (specialized TI source, not general web)
  - API keys never logged / never in payload_text / never exported
  - Rate limiting via TokenBucket (not sleep)
  - Always returns CanonicalFinding list (empty on failure)
"""

from __future__ import annotations

import base64
import logging
import os
import time

import aiohttp
from hledac.universal.knowledge.duckdb_store import CanonicalFinding
from hledac.universal.utils.rate_limiters import get_limiter

logger = logging.getLogger(__name__)

CENSYS_SEARCH_API = "https://search.censys.io/api/v1/search/ipv4"
CENSYS_VIEW_API = "https://search.censys.io/api/v1/view/ipv4"
RATE_LIMIT_KEY = "censys_api"


def _get_credentials() -> tuple[str | None, str | None]:
    api_id = os.environ.get("CENSYS_API_ID") or None
    api_secret = os.environ.get("CENSYS_SECRET") or None
    return api_id, api_secret


def _build_findings(query: str, raw_results: list[dict], ts_now: float) -> list[CanonicalFinding]:
    findings = []
    for host in raw_results:
        ip = host.get("ip", "") or ""
        protocols = host.get("protocols", []) or []
        tags = host.get("tags", []) or []
        metadata = host.get("metadata", {}) or {}

        if not ip:
            continue

        # Censys tags and protocols give high confidence
        confidence = 0.9  # verified external source
        if not protocols and not tags:
            confidence = 0.85

        protocol_str = ",".join(protocols[:5]) if protocols else ""
        tags_str = ",".join(tags[:5]) if tags else ""

        finding = CanonicalFinding(
            finding_id=f"censys_{ip}_{int(ts_now * 1000)}",
            query=f"censys:{query}",
            source_type="censys_intel",
            confidence=confidence,
            ts=ts_now,
            provenance=("censys_intel", query, ip, ",".join(protocols[:3])),
            payload_text=f"{ip} protocols={protocol_str} tags={tags_str} metadata={str(metadata)[:200]}",
        )
        findings.append(finding)

    return findings


async def search_censys_lane(
    query: str,
    limit: int = 20,
    api_id: str | None = None,
    api_secret: str | None = None,
) -> tuple[list[CanonicalFinding], list[dict]]:
    """
    Search Censys and return CanonicalFindings.

    Args:
        query: Censys search query (e.g. "services.tls.certificates.leaf_data.subject.common_name: example.com")
        limit: Maximum results (default 20)
        api_id: Optional API ID (uses env CENSYS_API_ID if not passed)
        api_secret: Optional API secret (uses env CENSYS_SECRET if not passed)

    Returns:
        Tuple of (findings, raw_results) — raw_results preserved for pivot side effect.
    """
    bucket = get_limiter(RATE_LIMIT_KEY)
    await bucket.acquire()

    id_, secret = api_id or _get_credentials()

    if not id_ or not secret:
        logger.warning("[CENSYS] No API credentials — skipping Censys lane")
        return [], []

    auth = base64.b64encode(f"{id_}:{secret}".encode()).decode()

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            async with session.get(
                CENSYS_SEARCH_API,
                params={"q": query, "per_page": min(limit, 50)},
                headers={"Authorization": f"Basic {auth}"},
            ) as resp:
                if resp.status == 401:
                    logger.warning("[CENSYS] API credentials invalid or required")
                    return [], []
                if resp.status == 403:
                    logger.warning("[CENSYS] API forbidden — check quota")
                    return [], []
                if resp.status == 429:
                    logger.warning("[CENSYS] Rate limit hit")
                    return [], []
                if resp.status != 200:
                    logger.warning(f"[CENSYS] API error: {resp.status}")
                    return [], []

                data = await resp.json()
                results = data.get("results", []) if isinstance(data, dict) else []

                ts_now = time.time()
                raw_results = []

                for entry in results:
                    ip = entry.get("ip", "")
                    if not ip:
                        continue
                    raw_results.append(entry)
                    if len(raw_results) >= limit:
                        break

                findings = _build_findings(query, raw_results, ts_now)
                logger.debug(f"[CENSYS] query='{query}' → {len(findings)} findings")
                return findings, raw_results

    except Exception as e:
        logger.warning(f"[CENSYS] search error: {e}")
        return [], []


# ── CensysLane adapter ───────────────────────────────────────────────────────


class CensysLane:
    """
    Censys intelligence lane — certificate transparency and host enumeration.

    query(target) → list[CanonicalFinding]
      target: domain, cert keyword, or Censys search query

    fail-soft: returns [] if no API credentials or on error
    """

    __slots__ = ("_stats",)

    def __init__(self) -> None:
        self._stats = {
            "queries": 0,
            "findings": 0,
            "errors": 0,
        }

    async def query(self, target: str) -> list[CanonicalFinding]:
        """Query Censys for target (domain, cert keyword, or search query)."""
        self._stats["queries"] += 1
        findings, _ = await search_censys_lane(target, limit=20)
        self._stats["findings"] += len(findings)
        if not findings:
            self._stats["errors"] += 1
        return findings

    def get_stats(self) -> dict:
        return self._stats.copy()


__all__ = [
    "CensysLane",
    "search_censys_lane",
]
