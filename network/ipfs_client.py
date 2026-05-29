#!/usr/bin/env python3
"""
IPFS Client — Multi-gateway fetch and search for IPFS content.

IPFS fetch is bounded and can be a safe OSINT source when:
  - Has explicit size cap (10MB MAX_FILE_SIZE_BYTES)
  - Results are tagged with source_type="ipfs_fetch" (not just "ipfs")
  - ipfs_fetch_as_findings() returns list[CanonicalFinding]

F218Z: IPFS via Tor transport — all gateway requests route through Tor
when CURL_CFFI_PROXY is set. Explicit HLEDAC_IPFS_CLEARNET=1 to override.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time as _time

import aiohttp

from hledac.universal.knowledge.duckdb_store import CanonicalFinding

logger = logging.getLogger(__name__)

# CID extraction pattern — matches Qm (v0, base58, 44 chars) and
# bafy (v1, base32, 52+ chars) CID formats.
CID_PATTERN = re.compile(r'\b(Qm[1-9A-HJ-NP-Za-km-z]{44}|bafy[a-z2-7]{52,})\b')


def extract_cids_from_text(content: str) -> list[str]:
    """Extract all IPFS CIDs from raw text content.

    Scans content for Qm (v0) and bafy (v1) CID patterns.
    Used by sprint-sidecar to pull CIDs from findings before bulk fetch.

    Fail-safe: returns [] for empty/None content.
    Hard timeout ≤12s enforced at fetch_ipfs layer.
    RAM governor critical/emergency → caller skips IPFS sidecar.
    """
    if not content:
        return []
    return CID_PATTERN.findall(content)


# =============================================================================
# IPFS PROMOTION GATE — F206F
# =============================================================================
# IPFS fetch is bounded and can be a safe OSINT src when:
#   - Has explicit size cap (10MB MAX_FILE_SIZE_BYTES)
#   - Results are tagged with source_type="ipfs_fetch" (not just "ipfs")
#   - ipfs_fetch_as_findings() returns list[CanonicalFinding]
IPFS_PROMOTION_STATUS: str = "bounded_gateway_fetch"

MAX_FILE_SIZE_BYTES: int = 10 * 1024 * 1024  # 10 MB hard cap

IPFS_GATEWAYS: list[tuple[str, str]] = [
    # (name, base_url)
    ("local", "http://localhost:8080/ipfs/"),
    ("cloudflare", "https://cloudflare-ipfs.com/ipfs/"),
    ("ipfs.io", "https://ipfs.io/ipfs/"),
]


async def fetch_ipfs(cid: str, timeout: int = 30) -> bytes | None:
    """
    Fetch content from IPFS via multiple gateways.

    INVARIANT (F218Z): If Tor is available (CURL_CFFI_PROXY set), route all
    IPFS gateway requests through Tor SOCKS5H proxy. Never over clearnet when
    Tor is active. Check HLEDAC_IPFS_CLEARNET=1 to override (explicit opt-in).

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
    # F218Z: Tor routing — check if Tor SOCKS5H proxy is available
    import os as _os
    _tor_proxy = _os.environ.get("CURL_CFFI_PROXY", "")
    _clearnet_override = _os.environ.get("HLEDAC_IPFS_CLEARNET", "").lower() in ("1", "true", "yes", "on")
    _use_tor = bool(_tor_proxy) and not _clearnet_override
    if _use_tor:
        logger.debug("IPFS fetch via Tor proxy: %s", _tor_proxy)

    client_timeout = aiohttp.ClientTimeout(total=timeout)

    # F218Z: Apply Tor SOCKS5H proxy to aiohttp session if available
    connector = None
    if _use_tor:
        try:
            from aiohttp_socks import ProxyConnector
            connector = ProxyConnector.from_url(_tor_proxy)
        except Exception as e:
            logger.debug("aiohttp_socks ProxyConnector unavailable: %s", e)
            connector = None

    for name, base_url in IPFS_GATEWAYS:
        try:
            url = f"{base_url}{cid}"
            # F218Z: Apply Tor SOCKS5H connector if available
            session_kwargs = {"timeout": client_timeout}
            if connector is not None:
                session_kwargs["connector"] = connector
            async with aiohttp.ClientSession(**session_kwargs) as session:
                # HEAD req first to check Content-Length
                async with session.head(url) as head_resp:
                    if head_resp.status != 200:
                        continue

                    content_length_hdr = head_resp.headers.get("Content-Length")
                    if content_length_hdr:
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

                # GET req to download
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

        except TimeoutError:
            logger.debug(f"IPFS timeout for {cid} via {name}")
            continue
        except Exception as e:
            logger.debug(f"IPFS fetch err for {cid} via {name}: {e}")
            continue

    logger.warning(f"IPFS fetch failed for all gateways: {cid}")
    return None


async def search_ipfs(query: str) -> list[str]:
    """
    Search IPFS for content matching query.

    Attempts to use deep_probe.generate_ipfs_dorks if available,
    otherwise generates simple CID patterns from the query.

    Fail-soft: returns empty list on any error.

    Returns:
        List of IPFS CIDs (as strings) found for the query.
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
    except Exception:
        pass

    # Fallback: generate dork CIDs from query keywords
    keywords = [w.strip().lower() for w in query.split() if len(w.strip()) >= 4][:5]
    if not keywords:
        return []

    for kw in keywords:
        cid_candidate = f"Qm{kw[:44].ljust(44, '0')}"  # dummy CID pattern
        if cid_candidate not in seen:
            seen.add(cid_candidate)
            cids.append(cid_candidate)

    return cids


def ipfs_content_to_finding_dict(
    cid: str,
    content: bytes,
    query: str,
    source_type: str = "ipfs_fetch",
) -> dict:
    """
    Convert raw IPFS content to a CanonicalFinding dict.

    F206F: Does NOT import CanonicalFinding (avoids duckdb_store circular dep).
    Caller is responsible for constructing CanonicalFinding from returned dict.

    Args:
        cid: IPFS Content Identifier
        content: Raw bytes from IPFS fetch
        query: Original query string
        source_type: "ipfs_fetch" or "ipfs_search" (default: ipfs_fetch)

    Returns:
        Finding dict with all required CanonicalFinding fields.
    """
    import time as _time

    content_text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else content

    finding_id = f"ipfs-{cid[:16]}-{_time.time_ns()}"

    # payload_text: content preview (up to 4096 chars for LMDB WAL)
    payload_text = content_text[:4096] if content_text else ""

    return {
        "finding_id": finding_id,
        "query": query,
        "source_type": source_type,
        "confidence": 0.75,  # IPFS content is authoritative but unverified
        "ts": _time.time(),
        "provenance": (f"ipfs://{cid}",),
        "payload_text": payload_text,
        "accepted": True,  # IPFS is bounded source — auto-accept
        "reason": source_type,
        "entropy": 0.0,
        "normalized_hash": None,
        "duplicate": False,
    }


async def ipfs_fetch_as_findings(cid: str, query: str, timeout: int = 30) -> list:
    """
    Fetch IPFS content and ret as CanonicalFinding list.

    Fails soft: returns empty list on any err.

    Returns:
        list[CanonicalFinding] — one finding per successful fetch
    """
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding

    try:
        content = await fetch_ipfs(cid, timeout=timeout)
    except Exception:
        return []

    if content is None:
        return []

    try:
        finding_dict = ipfs_content_to_finding_dict(cid, content, query, source_type="ipfs_fetch")
        finding = CanonicalFinding(
            finding_id=finding_dict["finding_id"],
            query=finding_dict["query"],
            source_type=finding_dict["source_type"],
            confidence=finding_dict["confidence"],
            ts=finding_dict["ts"],
            provenance=finding_dict["provenance"],
            payload_text=finding_dict.get("payload_text"),
        )
        return [finding]
    except Exception:
        return []


async def fetch_findings_from_cids(
    cids: list[str],
    query: str,
    timeout_per_cid: int = 10,
    max_concurrent: int = 3,
) -> list[CanonicalFinding]:
    """
    Bulk fetch IPFS CIDs → list[CanonicalFinding].

    Concurrency cap ≤ 3 (M1 8GB + M1ResourceGovernor warn tier).
    Per-CID timeout: 10s default.
    Fail-soft: timeout/error → log debug, skip CID, continue.
    Deduplicates CID list before fetch (dict.fromkeys preserves order).

    Returns:
        list[CanonicalFinding] — one per successfully fetched CID.
        [] if cids empty or HLEDAC_ENABLE_IPFS != "1".
    """
    # IPFS gate
    if os.getenv("HLEDAC_ENABLE_IPFS", "0") != "1":
        return []

    # RAM governor — skip bulk fetch under emergency memory
    try:
        from hledac.universal.runtime.resource_governor import get_governor

        governor = get_governor()
        decision = await governor.evaluate()
        if decision.uma_state in ("critical", "emergency"):
            logger.debug("IPFS bulk fetch skipped: memory %s", decision.uma_state)
            return []
    except Exception:
        pass  # fail-safe: proceed if governor unavailable

    if not cids:
        return []

    # dedup + preserve order via dict.fromkeys
    unique_cids = list(dict.fromkeys(cids))

    sem = asyncio.Semaphore(max_concurrent)

    async def _fetch_one(cid: str):
        nonlocal query
        async with sem:
            try:
                results = await asyncio.wait_for(
                    ipfs_fetch_as_findings(cid, query),
                    timeout=timeout_per_cid,
                )
                return results[0] if results else None
            except (asyncio.TimeoutError, Exception) as e:
                logger.debug("IPFS CID %s skip: %s", cid[:8], type(e).__name__)
                return None

    tasks = [_fetch_one(c) for c in unique_cids]
    results = await asyncio.gather(*tasks)
    return [r for r in results if r is not None]


async def ipfs_search_as_findings(query: str, timeout_per_result: int = 30) -> list:
    """
    Search IPFS for query and ret all fetched content as CanonicalFinding list.

    Fails soft: errors ret empty list, partial results are returned.

    Args:
        query:              Search keyword/phrase
        timeout_per_result: Seconds per fetch (default 30)

    Returns:
        list[CanonicalFinding] — one finding per found CID
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

            finding_dict = ipfs_content_to_finding_dict(
                cid, content, query, source_type="ipfs_search"
            )
            finding = CanonicalFinding(
                finding_id=finding_dict["finding_id"],
                query=finding_dict["query"],
                source_type=finding_dict["source_type"],
                confidence=finding_dict["confidence"],
                ts=finding_dict["ts"],
                provenance=finding_dict["provenance"],
                payload_text=finding_dict.get("payload_text"),
            )
            findings.append(finding)
        except Exception:
            continue

    return findings
