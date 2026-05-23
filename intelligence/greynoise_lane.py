"""
Sprint F235: GreyNoise Intelligence Lane

External intelligence lane for GreyNoise (internet noise, mass scanners).
High-value unindexed data: GreyNoise identifies mass scanners, bots, and internet noise
that other sources don't classify — critical for distinguishing real threats from noise.

Pattern: AcquisitionLane via BGPLane-style async class.
Fail-soft: if GREYNOISE_API_KEY absent → return [] with warning log.
Rate limit: TokenBucket "greynoise_api" (free tier: 60 queries/min ≈ 1 req/sec).

GHOST_INVARIANTS:
  - External API calls go directly to GreyNoise (specialized TI source, not general web)
  - API key never logged / never in payload_text / never exported
  - Rate limiting via TokenBucket (not sleep)
  - Always returns CanonicalFinding list (empty on failure)
"""

from __future__ import annotations

import logging
import os
import time
from typing import List, Optional, Tuple

import aiohttp

from hledac.universal.knowledge.duckdb_store import CanonicalFinding
from hledac.universal.utils.rate_limiters import get_limiter

logger = logging.getLogger(__name__)

GREYNOISE_COMMUNITY_API = "https://api.greynoise.io/v3/community/{ip}"
GREYNOISE_FULL_API = "https://api.greynoise.io/v3/query/ip"
RATE_LIMIT_KEY = "greynoise_api"


def _get_api_key() -> Optional[str]:
    return os.environ.get("GREYNOISE_API_KEY") or None


def _build_findings(ip: str, raw_result: dict, ts_now: float) -> List[CanonicalFinding]:
    findings = []

    classification = raw_result.get("classification", "unknown")
    tags = raw_result.get("tags", []) or []
    metadata = raw_result.get("metadata", {}) or {}
    first_seen = raw_result.get("first_seen", "")
    last_seen = raw_result.get("last_seen", "")
    asn = raw_result.get("asn", "unknown")

    confidence = 0.9  # verified external source
    if classification == "benign":
        confidence = 0.85
    elif classification == "unknown":
        confidence = 0.8

    tags_str = ",".join(tags[:8]) if tags else ""
    metadata_str = str(metadata)[:150]

    finding = CanonicalFinding(
        finding_id=f"greynoise_{ip}_{int(ts_now * 1000)}",
        query=f"greynoise:{ip}",
        source_type="greynoise_intel",
        confidence=confidence,
        ts=ts_now,
        provenance=("greynoise_intel", ip, classification),
        payload_text=f"{ip} classification={classification} tags={tags_str} asn={asn} first_seen={first_seen} last_seen={last_seen} metadata={metadata_str}",
    )
    findings.append(finding)

    return findings


async def query_greynoise_ip(
    ip: str,
    api_key: Optional[str] = None,
    use_community: bool = False,
) -> Tuple[List[CanonicalFinding], dict]:
    """
    Query GreyNoise for a single IP and return CanonicalFindings.

    Args:
        ip: IP address to query
        api_key: Optional API key (uses env GREYNOISE_API_KEY if not passed)
        use_community: If True, use free community API (no key needed for community data)

    Returns:
        Tuple of (findings, raw_result) — raw_result preserved for pivot side effect.
    """
    bucket = get_limiter(RATE_LIMIT_KEY)
    await bucket.acquire()

    key = api_key or _get_api_key()

    if not key and not use_community:
        logger.warning("[GREYNOISE] No API key — skipping GreyNoise lane (try community API)")
        return [], {}

    try:
        # Use community API as fallback when no key; use full API when key available
        if use_community and not key:
            logger.debug("[GREYNOISE] Using community API (no key)")
            url = GREYNOISE_COMMUNITY_API.format(ip=ip)
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
                async with session.get(url) as resp:
                    if resp.status == 404:
                        ts_now = time.time()
                        return [
                            CanonicalFinding(
                                finding_id=f"greynoise_{ip}_{int(ts_now * 1000)}",
                                query=f"greynoise:{ip}",
                                source_type="greynoise_intel",
                                confidence=0.8,
                                ts=ts_now,
                                provenance=("greynoise_intel", ip, "not_found"),
                                payload_text=f"{ip} classification=not_found message='IP not in GreyNoise database'",
                            )
                        ], {}
                    if resp.status == 429:
                        logger.warning("[GREYNOISE] Community API rate limit hit")
                        return [], {}
                    if resp.status != 200:
                        logger.warning(f"[GREYNOISE] Community API error: {resp.status}")
                        return [], {}

                    data = await resp.json()
                    ts_now = time.time()
                    findings = _build_findings(ip, data, ts_now)
                    return findings, data

        else:
            headers = {"key": key, "Accept": "application/json"}
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
                async with session.get(
                    GREYNOISE_FULL_API.format(ip=ip),
                    headers=headers,
                ) as resp:
                    if resp.status == 401:
                        logger.warning("[GREYNOISE] API key required or invalid")
                        return [], {}
                    if resp.status == 429:
                        logger.warning("[GREYNOISE] Rate limit hit")
                        return [], {}
                    if resp.status != 200:
                        logger.warning(f"[GREYNOISE] API error: {resp.status}")
                        return [], {}

                    data = await resp.json()
                    ts_now = time.time()
                    findings = _build_findings(ip, data, ts_now)
                    return findings, data

    except Exception as e:
        logger.warning(f"[GREYNOISE] query error for {ip}: {e}")
        return [], {}


async def search_greynoise_lane(
    target: str,
    limit: int = 20,
    api_key: Optional[str] = None,
) -> Tuple[List[CanonicalFinding], List[dict]]:
    """
    Query GreyNoise for target IP(s) and return CanonicalFindings.

    Args:
        target: IP address or comma-separated IP list
        limit: Maximum results (default 20)
        api_key: Optional API key (uses env GREYNOISE_API_KEY if not passed)

    Returns:
        Tuple of (findings, raw_results) — raw_results preserved for pivot side effect.
    """
    key = api_key or _get_api_key()

    if not key:
        logger.warning("[GREYNOISE] No API key — skipping GreyNoise lane")
        return [], []

    ips = [ip.strip() for ip in target.split(",") if ip.strip()]
    if not ips:
        return [], []

    ips = ips[:limit]

    all_findings = []
    all_raw = []

    for ip in ips:
        findings, raw = await query_greynoise_ip(ip, api_key=key)
        all_findings.extend(findings)
        all_raw.append(raw)

    logger.debug(f"[GREYNOISE] target='{target}' → {len(all_findings)} findings")
    return all_findings, all_raw


# ── GreyNoiseLane adapter ────────────────────────────────────────────────────


class GreyNoiseLane:
    """
    GreyNoise intelligence lane — mass scanner / internet noise classification.

    query(target) → list[CanonicalFinding]
      target: IP address or comma-separated IP list

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
        """Query GreyNoise for target IP(s)."""
        self._stats["queries"] += 1
        findings, _ = await search_greynoise_lane(target, limit=20)
        self._stats["findings"] += len(findings)
        if not findings:
            self._stats["errors"] += 1
        return findings

    def get_stats(self) -> dict:
        return self._stats.copy()


__all__ = [
    "GreyNoiseLane",
    "search_greynoise_lane",
    "query_greynoise_ip",
]