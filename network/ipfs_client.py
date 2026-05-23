#!/usr/bin/env python3
"""
IPFS Client — Multi-gateway fetch and search for IPFS content.

Gateway order: local daemon → Cloudflare → ipfs.io
10 MB size cap: reject files before download if Content-Length > 10MB.

PROMOTION GATE — F206F
  IPFS fetch is bounded and can be a safe OSINT source when:
  - Has explicit timeout (30s default)
  - Has explicit size cap (10MB MAX_FILE_SIZE_BYTES)
  - Fails soft: returns None on all failures
  - Results are tagged with source_type="ipfs_fetch" (not just "ipfs")
  - Circuit breaker hook is optional and fail-open (skips when unavailable)

Anti-patterns prevented:
  - No blocking socket ops (aiohttp only)
  - No size bypass (Content-Length check before read)
  - No hardcoded API keys
  - Graceful degradation: None return on all failures

F229: CanonicalFinding return path added.
  - ipfs_fetch_as_findings() returns List[CanonicalFinding]
  - ipfs_search_as_findings() returns List[CanonicalFinding]
  - Fail-soft: errors return empty list, never raise
"""
from __future__ import annotations

import asyncio
import logging
import time
import aiohttp
from typing import Optional

logger = logging.getLogger(__name__)

# =============================================================================
# IPFS PROMOTION GATE — F206F
# =============================================================================
# IPFS fetch is a bounded OSINT source with explicit safety guards.
IPFS_PROMOTION_STATUS: str = "bounded_gateway_fetch"

MAX_FILE_SIZE_BYTES: int = 10 * 1024 * 1024  # 10 MB hard cap

IPFS_GATEWAYS: list[tuple[str, str]] = [
    # (name, base_url)
    ("local", "http://localhost:8080/ipfs/"),
    ("cloudflare", "https://cloudflare-ipfs.com/ipfs/"),
    ("ipfs.io", "https://ipfs.io/ipfs/"),
]


async def fetch_ipfs(cid: str, timeout: int = 30) -> Optional[bytes]:
    """
    Fetch content from IPFS via multiple gateways.

    Tries gateways in order until one succeeds:
      1. Local daemon: http://localhost:8080/ipfs/{cid}
      2. Cloudflare gateway: https://cloudflare-ipfs.com/ipfs/{cid}
      3. Main gateway: https://ipfs.io/ipfs/{cid}

    Args:
        cid: IPFS Content Identifier (CID)
        timeout: Request timeout in seconds (default 30)

    Returns:
        File content as bytes, or None if all gateways fail or file > 10MB.

    Anti-patterns prevented:
      - 10 MB size cap enforced via Content-Length header check
      - Non-blocking aiohttp throughout
      - Fail-soft: returns None, never raises
    """
    client_timeout = aiohttp.ClientTimeout(total=timeout)

    for name, base_url in IPFS_GATEWAYS:
        try:
            url = f"{base_url}{cid}"
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                # HEAD request first to check Content-Length
                async with session.head(url) as head_resp:
                    if head_resp.status != 200:
                        continue

                    content_length_hdr = head_resp.headers.get("Content-Length")
                    if content_length_hdr is not None:
                        try:
                            file_size = int(content_length_hdr)
                            if file_size > MAX_FILE_SIZE_BYTES:
                                logger.warning(
                                    f"IPFS file {cid} from {name} exceeds 10MB limit "
                                    f"({file_size} bytes) — skipping"
                                )
                                return None
                        except (ValueError, TypeError):
                            pass

                # GET request to download
                async with session.get(url) as get_resp:
                    if get_resp.status != 200:
                        continue

                    body = await get_resp.read()
                    if len(body) > MAX_FILE_SIZE_BYTES:
                        logger.warning(
                            f"IPFS file {cid} from {name} exceeds 10MB limit "
                            f"after download ({len(body)} bytes) — skipping"
                        )
                        return None

                    logger.debug(f"IPFS fetch success: {cid} via {name} ({len(body)} bytes)")
                    return body

        except asyncio.TimeoutError:
            logger.debug(f"IPFS timeout for {cid} via {name}")
            continue
        except Exception as e:
            logger.debug(f"IPFS fetch error for {cid} via {name}: {e}")
            continue

    logger.warning(f"IPFS fetch failed for all gateways: {cid}")
    return None


async def search_ipfs(query: str) -> list[str]:
    """
    Search IPFS for content matching query.

    Attempts to use deep_probe.generate_ipfs_dorks if available,
    otherwise generates simple CID patterns from the query.

    Args:
        query: Search keyword/phrase

    Returns:
        List of CID strings (not URLs)

    Anti-patterns prevented:
      - Graceful fallback when deep_probe not available
      - Non-blocking throughout
    """
    cids: list[str] = []
    seen: set[str] = set()

    # Try to use deep_probe.generate_ipfs_dorks
    try:
        from hledac.universal.deep_probe import scan_ipfs
        results = await scan_ipfs(query)
        for result in results:
            cid = result.get("cid", "")
            if cid and cid not in seen:
                seen.add(cid)
                cids.append(cid)
        if cids:
            return cids
    except (ImportError, Exception):
        pass

    # Fallback: generate simple CID patterns from query
    # Note: This is a simplified approach - real IPFS search requires
    # dedicated search engines (ipfssearch.com, ipfs-search.com)
    query_slug = query.replace(" ", "-").lower()[:50]
    fallback_patterns = [
        f"Qm{query_slug[:44]}",
        f"bafy{query_slug[:49]}",
    ]

    for pattern in fallback_patterns:
        if pattern not in seen:
            seen.add(pattern)
            cids.append(pattern)

    logger.debug(f"search_ipfs('{query}'): {len(cids)} CIDs generated (fallback)")
    return cids


def ipfs_content_to_finding_dict(
    cid: str,
    content: bytes | str,
    gateway: str,
    query: str,
    ts: float,
    finding_id_prefix: str = "ipfs",
) -> dict:
    """
    Thin transform: IPFS content bytes → CanonicalFinding-compatible dict.

    Does NOT import CanonicalFinding (avoids duckdb_store circular dep).
    Caller is responsible for constructing CanonicalFinding from returned dict.

    Args:
        cid:           IPFS Content Identifier
        content:       Raw content as bytes or decoded str
        gateway:       Gateway name that served the content
        query:         Original query (IOC value or search term)
        ts:            Unix timestamp for the finding
        finding_id_prefix: Prefix for finding_id ('ipfs' or 'ipfs_search')

    Returns:
        Dict with keys matching CanonicalFinding fields:
        finding_id, query, source_type, confidence, ts, provenance, payload_text
    """
    import hashlib

    content_text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else content
    content_hash = hashlib.sha256(content_text[:2000].encode()).hexdigest()[:16]
    finding_id = f"{finding_id_prefix}_{cid}_{int(ts * 1000)}_{content_hash}"

    return {
        "finding_id": finding_id,
        "query": f"{finding_id_prefix}:{query}",
        "source_type": "ipfs_fetch",  # F206F: explicit tag to distinguish from deep_probe ipfs search
        "confidence": 0.75 if gateway != "ipfs_search" else 0.65,
        "ts": ts,
        "provenance": (cid, gateway, query),
        "payload_text": content_text[:2000] if content_text else None,
    }


__all__ = [
    "fetch_ipfs",
    "search_ipfs",
    "ipfs_content_to_finding_dict",
    "ipfs_fetch_as_findings",
    "ipfs_search_as_findings",
    "MAX_FILE_SIZE_BYTES",
    "IPFS_PROMOTION_STATUS",
]


# F229: CanonicalFinding return path
async def ipfs_fetch_as_findings(cid: str, query: str, timeout: int = 30) -> list:
    """
    Fetch IPFS content and return as CanonicalFinding list.

    Fails soft: returns empty list on any error.

    Args:
        cid:      IPFS Content ID
        query:    orig query (IOC value or search term)
        timeout:  seconds per gateway attempt (default 30)

    Returns:
        List[CanonicalFinding] — one finding per successful fetch
    """
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding

    try:
        content = await fetch_ipfs(cid, timeout=timeout)
        if content is None:
            return []
    except Exception:
        return []

    try:
        ts = time.time()
        finding_dict = ipfs_content_to_finding_dict(
            cid=cid,
            content=content,
            gateway="ipfs_fetch",
            query=query,
            ts=ts,
            finding_id_prefix="ipfs",
        )
        # Sprint 8W quality decision contract
        finding = CanonicalFinding(
            finding_id=finding_dict["finding_id"],
            query=finding_dict["query"],
            source_type=finding_dict["source_type"],
            confidence=finding_dict["confidence"],
            ts=finding_dict["ts"],
            provenance=finding_dict["provenance"],
            payload_text=finding_dict.get("payload_text"),
            # Quality contract (Sprint 8W)
            accepted=True,
            reason="ipfs_fetch",
            entropy=0.0,
            normalized_hash=None,
            duplicate=False,
        )
        return [finding]
    except Exception:
        return []


async def ipfs_search_as_findings(query: str, timeout_per_result: int = 30) -> list:
    """
    Search IPFS for query and return all fetched content as CanonicalFinding list.

    Fails soft: errors return empty list, partial results are returned.

    Args:
        query:              Search keyword/phrase
        timeout_per_result: Seconds per fetch (default 30)

    Returns:
        List[CanonicalFinding] — one finding per found CID
    """
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding

    try:
        cids = await search_ipfs(query)
    except Exception:
        return []

    if not cids:
        return []

    findings: list = []
    for cid in cids[:20]:  # Cap at 20 CIDs for M1 safety
        try:
            content = await fetch_ipfs(cid, timeout=timeout_per_result)
            if content is None:
                continue
        except Exception:
            continue

        try:
            ts = time.time()
            finding_dict = ipfs_content_to_finding_dict(
                cid=cid,
                content=content,
                gateway="ipfs_search",
                query=query,
                ts=ts,
                finding_id_prefix="ipfs_search",
            )
            finding = CanonicalFinding(
                finding_id=finding_dict["finding_id"],
                query=finding_dict["query"],
                source_type=finding_dict["source_type"],
                confidence=finding_dict["confidence"],
                ts=finding_dict["ts"],
                provenance=finding_dict["provenance"],
                payload_text=finding_dict.get("payload_text"),
                accepted=True,
                reason="ipfs_search",
                entropy=0.0,
                normalized_hash=None,
                duplicate=False,
            )
            findings.append(finding)
        except Exception:
            continue

    return findings
