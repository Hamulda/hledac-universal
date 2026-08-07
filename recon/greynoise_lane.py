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

import logging
import os
import time
from typing import Any

import asyncio
import httpx

from hledac.universal.knowledge.duckdb_store import CanonicalFinding
from hledac.universal.transport.circuit_breaker import (
    domain_breaker_check,
    domain_breaker_record_failure,
    domain_breaker_record_success,
)
from hledac.universal.utils.async_helpers import bounded_parallel_map
from hledac.universal.utils.rate_limiters import get_limiter

from hledac.universal.security.secrets_scrubber import redact_greynoise_key, safe_error_log

logger = logging.getLogger(__name__)

GREYNOISE_COMMUNITY_API = "https://api.greynoise.io/v3/community/{ip}"
GREYNOISE_FULL_API = "https://api.greynoise.io/v3/query/ip"
RATE_LIMIT_KEY = "greynoise_api"

# [FINAL]-019: Anti-correlation jitter for SIEM fingerprint defense.
# Gaussian sigma = 0.6s gives decorrelated inter-request intervals.
_GREYNOISE_JITTER_SIGMA_S: float = float(os.environ.get('HLEDAC_GREYNOISE_JITTER_SIGMA_S', '0.6'))

# F266: Circuit breaker — domain for GreyNoise API
_CB_DOMAIN = "api.greynoise.io"


def _try_domain_breaker_check(domain: str) -> Any:
    """Fail-soft domain circuit breaker check — returns None if CB unavailable."""
    try:
        return domain_breaker_check(domain)
    except Exception:
        return None


def _record_greynoise_success() -> None:
    """Record GreyNoise API success to circuit breaker."""
    try:
        domain_breaker_record_success(_CB_DOMAIN)
    except Exception:  # noqa: BLE001
        pass


def _record_greynoise_failure(is_timeout: bool = False, kind: str = "") -> None:
    """Record GreyNoise API failure to circuit breaker."""
    try:
        domain_breaker_record_failure(_CB_DOMAIN, is_timeout=is_timeout, failure_kind=kind)
    except Exception:  # noqa: BLE001
        pass


def _get_api_key() -> str | None:
    return os.environ.get("GREYNOISE_API_KEY") or None


def _build_findings(ip: str, raw_result: dict, ts_now: float) -> list[CanonicalFinding]:
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
        payload_text=f"{ip} classification={classification} tags={tags_str} asn={asn} first_seen={first_seen} last_seen={last_seen} metadata={metadata_str}",  # noqa: E501
    )
    findings.append(finding)

    return findings


async def query_greynoise_ip(
    ip: str,
    api_key: str | None = None,
    use_community: bool = False,
) -> tuple[list[CanonicalFinding], dict]:
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

    # [FINAL]-019: Gaussian jitter — decorrelates request bursts.
    # BLITZ mode (short sprint) skips jitter per is_blitz_mode().
    _sigma = _GREYNOISE_JITTER_SIGMA_S
    if _sigma > 0:
        try:
            from hledac.universal.core.telemetry.context_state import is_blitz_mode
            if not is_blitz_mode():
                import random as _rng
                await asyncio.sleep(abs(_rng.gauss(0.0, _sigma)))
        except Exception:  # noqa: BLE001
            pass  # fail-soft: jitter is best-effort

    key = api_key or _get_api_key()

    if not key and not use_community:
        logger.warning("[GREYNOISE] No API key — skipping GreyNoise lane (try community API)")
        return [], {}

    # F266: Circuit breaker preflight
    decision = _try_domain_breaker_check(_CB_DOMAIN)
    if decision is not None and not decision.allowed:
        logger.debug(f"[GREYNOISE] circuit breaker open for {_CB_DOMAIN}: {decision.reason}")
        return [], {}

    try:
        # Use community API as fallback when no key; use full API when key available
        if use_community and not key:
            logger.debug("[GREYNOISE] Using community API (no key)")
            url = GREYNOISE_COMMUNITY_API.format(ip=ip)
            _sess = httpx.AsyncClient()
            async with _sess as sess:
                async with sess.get(url, timeout=httpx.Timeout(15.0)) as resp:
                    if resp.status_code == 404:
                        ts_now = time.time()
                        _record_greynoise_success()
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
                    if resp.status_code == 429:
                        logger.warning("[GREYNOISE] Community API rate limit hit")
                        _record_greynoise_failure(kind="rate_limit")
                        return [], {}
                    if resp.status_code != 200:
                        logger.warning(f"[GREYNOISE] Community API error: {resp.status_code}")
                        _record_greynoise_failure(kind="http_error")
                        return [], {}

                    data = await resp.json()
                    ts_now = time.time()
                    findings = _build_findings(ip, data, ts_now)
                    _record_greynoise_success()
                    return findings, data

        else:
            headers: dict[str, str] = {"key": key or "", "Accept": "application/json"}
            _sess = httpx.AsyncClient()
            async with _sess as sess:
                async with sess.get(
                    GREYNOISE_FULL_API.format(ip=ip),
                    headers=headers,
                ) as resp:
                    if resp.status_code == 401:
                        # [FINAL]-019-09: safe_error_log ensures API key doesn't leak
                        safe_error_log(logger, "[GREYNOISE] API key required or invalid")
                        _record_greynoise_failure(kind="auth_error")
                        return [], {}
                    if resp.status_code == 429:
                        logger.warning("[GREYNOISE] Rate limit hit")
                        _record_greynoise_failure(kind="rate_limit")
                        return [], {}
                    if resp.status_code != 200:
                        logger.warning(f"[GREYNOISE] API error: {resp.status_code}")
                        _record_greynoise_failure(kind="http_error")
                        return [], {}

                    data = await resp.json()
                    ts_now = time.time()
                    findings = _build_findings(ip, data, ts_now)
                    _record_greynoise_success()
                    return findings, data

    except Exception as e:
        # [FINAL]-019-09: safe_error_log ensures API key doesn't leak in error message
        safe_error_log(logger, f"[GREYNOISE] query error for {ip}: {e}")
        _record_greynoise_failure(kind="exception")
        return [], {}


async def search_greynoise_lane(
    target: str,
    limit: int = 20,
    api_key: str | None = None,
) -> tuple[list[CanonicalFinding], list[dict]]:
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

    async def _query_one(ip: str) -> tuple[list[CanonicalFinding], dict]:
        return await query_greynoise_ip(ip, api_key=key)

    results = await bounded_parallel_map(
        ips,
        _query_one,
        concurrency=5,
        ctx="greynoise_lane",
        # [FINAL]-019-02: pre-semaphore jitter breaks the bounded_parallel_map
        # burst (N tasks created simultaneously, semaphore only throttles entry).
        # Small sigma=0.05s — just decorrelates creation timestamps.
        # API-level jitter in query_greynoise_ip adds full decorrelation (0.6s).
        jitter_sigma_s=0.05,
        jitter_max_s=0.2,
    )

    all_findings = []
    all_raw = []
    for result in results:
        if result is not None:
            findings, raw = result
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

    async def query(self, target: str) -> list[CanonicalFinding]:
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
