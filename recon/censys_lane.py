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

import base64
import logging
import time

import httpx

from hledac.universal._core.env_config import ENV
from hledac.universal.knowledge.duckdb_store import CanonicalFinding

# DRY: Shared search lane utilities (DRY-2026-08-07)
from hledac.universal.recon.search_lane_utils import (
    apply_jitter,
    circuit_breaker_check,
    record_failure,
    record_success,
)
from hledac.universal.security.secrets_scrubber import safe_error_log
from hledac.universal.utils.rate_limiters import get_limiter

logger = logging.getLogger(__name__)

CENSYS_SEARCH_API = "https://search.censys.io/api/v1/search/ipv4"
CENSYS_VIEW_API = "https://search.censys.io/api/v1/view/ipv4"
RATE_LIMIT_KEY = "censys_api"

# [FINAL]-019: Anti-correlation jitter for SIEM fingerprint defense.
# Censys free tier is 0.4 req/s (2.5s between requests). Sigma = 0.6s
# keeps well within rate limits while decorrelating bursts.
_CENSYS_JITTER_SIGMA_S: float = ENV.get_float("HLEDAC_CENSYS_JITTER_SIGMA_S", default=0.6)

# F266: Circuit breaker — domain for Censys API
_CB_DOMAIN = "search.censys.io"


def _get_credentials() -> tuple[str | None, str | None]:
    """Resolve Censys credentials via the canonical HLEDAC_* names.

    L2 fix: this lane previously read ``CENSYS_SECRET`` while
    ``recon/exposure_clients.py`` read ``CENSYS_API_SECRET``, so a secret set
    under one name was invisible to the other lane. Both are now aliases of
    ``HLEDAC_CENSYS_API_SECRET``.
    """
    api_id = ENV.get_api_key("HLEDAC_CENSYS_API_ID") or None
    api_secret = ENV.get_api_key("HLEDAC_CENSYS_API_SECRET") or None
    return api_id, api_secret


def _build_findings(
    query: str, raw_results: list[dict], ts_now: float, api_id: str | None = None, api_secret: str | None = None
) -> list[CanonicalFinding]:
    """Build CanonicalFinding list from normalized Censys results.

    Args:
        query: Search query
        raw_results: Normalized host results from Censys API
        ts_now: Current timestamp
        api_id: Optional API ID to redact from any future payloads (defense-in-depth)
        api_secret: Optional API secret to redact from any future payloads (defense-in-depth)
    """
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
    # F266: Circuit breaker preflight (DRY: search_lane_utils)
    decision = circuit_breaker_check(_CB_DOMAIN)
    if decision is not None and not decision.allowed:
        logger.debug(f"[CENSYS] circuit breaker open for {_CB_DOMAIN}: {decision.reason}")
        return [], []

    bucket = get_limiter(RATE_LIMIT_KEY)
    await bucket.acquire()

    # [FINAL]-019: Gaussian jitter (DRY: search_lane_utils)
    await apply_jitter(_CENSYS_JITTER_SIGMA_S, "CENSYS")

    id_, secret = api_id or _get_credentials()

    if not id_ or not secret:
        logger.warning("[CENSYS] No API credentials — skipping Censys lane")
        return [], []

    auth = base64.b64encode(f"{id_}:{secret}".encode()).decode()

    try:
        _sess = httpx.AsyncClient()
        async with _sess as sess:
            async with sess.get(
                CENSYS_SEARCH_API,
                params={"q": query, "per_page": min(limit, 50)},
                headers={"Authorization": f"Basic {auth}"},
            ) as resp:
                if resp.status_code == 401:
                    logger.warning("[CENSYS] API credentials invalid or required")
                    record_failure(_CB_DOMAIN, failure_kind="auth_error")
                    return [], []
                if resp.status_code == 403:
                    logger.warning("[CENSYS] API forbidden — check quota")
                    record_failure(_CB_DOMAIN, failure_kind="forbidden")
                    return [], []
                if resp.status_code == 429:
                    logger.warning("[CENSYS] Rate limit hit")
                    record_failure(_CB_DOMAIN, failure_kind="rate_limit")
                    return [], []
                if resp.status_code != 200:
                    logger.warning(f"[CENSYS] API error: {resp.status}")
                    record_failure(_CB_DOMAIN, failure_kind="http_error")
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

                findings = _build_findings(query, raw_results, ts_now, api_id=id_, api_secret=secret)
                record_success(_CB_DOMAIN)
                logger.debug(f"[CENSYS] query='{query}' → {len(findings)} findings")
                return findings, raw_results

    except Exception as e:
        # [FINAL]-019-09: safe_error_log ensures credentials don't leak in error message
        safe_error_log(logger, f"[CENSYS] search error: {e}")
        record_failure(_CB_DOMAIN, failure_kind="exception")
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
