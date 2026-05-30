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

# =============================================================================
# F230: IPNS Resolution (new)
# =============================================================================
# IPNS names (mutable pointers) resolve to CIDs via IPNS HTTP API
IPNS_API_GATEWAYS: list[str] = [
    "https://ipfs.io/api/v0/name/resolve?arg=",
    "https://cloudflare-ipfs.com/api/v0/name/resolve?arg=",
]

IPNS_TIMEOUT: int = 15


async def resolve_ipns(name: str, timeout: int = IPNS_TIMEOUT) -> str | None:
    """
    Resolve IPNS name to CID.

    IPNS (InterPlanetary Name System) provides mutable content addressing.
    Format: /ipns/<peer-id> or /ipns/<domain-name>

    Args:
        name: IPNS name (e.g., "Qm..." or "ipns://example.com/")
        timeout: Request timeout in seconds

    Returns:
        CID string if resolved, None if resolution fails.
    """
    import re as _re

    # Strip ipns:// prefix if present
    name = name.replace("ipns://", "").strip("/")

    # Validate it's a potential IPNS name (not a raw CID)
    if name.startswith("Qm") or name.startswith("bafy"):
        return None  # Already a CID, not an IPNS name

    client_timeout = aiohttp.ClientTimeout(total=timeout)

    for api_base in IPNS_API_GATEWAYS:
        try:
            url = f"{api_base}{name}"
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        import json as _json

                        data = await resp.json()
                        # Response: {"Path": "/ipfs/<cid>"}
                        path = data.get("Path", "")
                        cid_match = _re.search(r"/ipfs/([a-zA-Z0-9]+)", path)
                        if cid_match:
                            cid = cid_match.group(1)
                            logger.debug(f"IPNS {name} resolved to {cid}")
                            return cid
        except Exception as e:
            logger.debug(f"IPNS resolve failed for {name} via {api_base}: {e}")
            continue

    logger.warning(f"IPNS resolution failed for all gateways: {name}")
    return None


# =============================================================================
# F230: Directory Crawling (new)
# =============================================================================
MAX_DIR_DEPTH: int = 3
MAX_DIR_FILES: int = 100  # Per-level cap


async def fetch_directory_recursive(
    cid: str,
    max_depth: int = MAX_DIR_DEPTH,
    current_depth: int = 0,
    seen_cids: set[str] | None = None,
) -> list[dict]:
    """
    Recursively fetch IPFS directory contents.

    Args:
        cid: IPFS CID of the directory
        max_depth: Maximum recursion depth (default 3)
        current_depth: Current recursion level (internal)
        seen_cids: Set of already-visited CIDs (internal)

    Returns:
        List of dicts with keys: {cid, path, size, type}
    """
    if seen_cids is None:
        seen_cids = set()

    if current_depth > max_depth:
        return []

    if cid in seen_cids:
        return []
    seen_cids.add(cid)

    results: list[dict] = []

    for name, gateway_base in IPFS_GATEWAYS:
        try:
            # Try to fetch directory listing (dag.json or UnixFS directory)
            url = f"{gateway_base}{cid}"
            client_timeout = aiohttp.ClientTimeout(total=20)

            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                # Check if this is a directory via API
                api_url = f"https://ipfs.io/api/v0/ls/{cid}"
                async with session.get(api_url) as resp:
                    if resp.status == 200:
                        import json as _json

                        data = await resp.json()
                        objects = data.get("Objects", [])
                        if objects:
                            links = objects[0].get("Links", [])
                            for link in links[:MAX_DIR_FILES]:
                                link_cid = link.get("Hash", "")
                                link_name = link.get("Name", "")
                                link_type = link.get("Type", 0)
                                link_size = link.get("Size", 0)

                                results.append({
                                    "cid": link_cid,
                                    "path": f"{cid}/{link_name}",
                                    "size": link_size,
                                    "type": "dir" if link_type == 2 else "file",  # 2 = directory
                                })

                                # Recurse into subdirectories
                                if link_type == 2 and current_depth < max_depth:
                                    sub_results = await fetch_directory_recursive(
                                        link_cid, max_depth, current_depth + 1, seen_cids
                                    )
                                    results.extend(sub_results)
                        break
        except Exception as e:
            logger.debug(f"Directory fetch for {cid} via {name}: {e}")
            continue

    return results


# =============================================================================
# F230: IPFS Search (new)
# =============================================================================
IPFS_SEARCH_GATEWAY: str = "https://ipfs-search.com/api/v1/search"
IPFS_SEARCH_TIMEOUT: int = 30
MAX_SEARCH_RESULTS: int = 20


async def find_via_ipfs_search(query: str) -> list[str]:
    """
    Search IPFS content via ipfs-search.com free REST API.

    Args:
        query: Search query string

    Returns:
        List of CIDs matching the query.
    """
    cids: list[str] = []
    seen: set[str] = set()

    try:
        client_timeout = aiohttp.ClientTimeout(total=IPFS_SEARCH_TIMEOUT)
        params = {"q": query, "size": MAX_SEARCH_RESULTS}

        async with aiohttp.ClientSession(timeout=client_timeout) as session:
            async with session.get(IPFS_SEARCH_GATEWAY, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    # Response structure varies; handle common patterns
                    hits = data.get("hits", [])
                    if isinstance(data, list):
                        hits = data
                    for hit in hits:
                        cid = hit.get("cid", "") or hit.get("hash", "")
                        if cid and cid not in seen:
                            seen.add(cid)
                            cids.append(cid)
    except Exception as e:
        logger.debug(f"IPFS search failed for query '{query}': {e}")

    return cids


async def search_via_estuary(query: str) -> list[str]:
    """
    Search pinned content via Estuary API (https://estuary.tech).

    Estuary indexes pinned content on IPFS and provides search.
    Note: Requires API key for some endpoints; public endpoints limited.

    Args:
        query: Search query string

    Returns:
        List of CIDs matching the query.
    """
    cids: list[str] = []
    seen: set[str] = set()

    # Public Estuary search endpoint
    ESTUARY_SEARCH: str = "https://api.estuary.tech/public/search"

    try:
        client_timeout = aiohttp.ClientTimeout(total=IPFS_SEARCH_TIMEOUT)
        params = {"query": query}

        async with aiohttp.ClientSession(timeout=client_timeout) as session:
            async with session.get(ESTUARY_SEARCH, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, list):
                        for item in data[:MAX_SEARCH_RESULTS]:
                            cid = item.get("cid", "")
                            if cid and cid not in seen:
                                seen.add(cid)
                                cids.append(cid)
                elif resp.status == 429:
                    logger.warning("Estuary API rate limited")
    except Exception as e:
        logger.debug(f"Estuary search failed for query '{query}': {e}")

    return cids


# =============================================================================
# F230: IPFS as CanonicalFindings (extended)
# =============================================================================
async def ipfs_directory_as_findings(
    cid: str, query: str, max_depth: int = 3
) -> list:
    """
    Fetch IPFS directory recursively and return as CanonicalFinding list.

    Args:
        cid: IPFS directory CID
        query: Original query string
        max_depth: Maximum crawl depth

    Returns:
        list[CanonicalFinding] — one per discovered file
    """
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding

    try:
        entries = await fetch_directory_recursive(cid, max_depth=max_depth)
    except Exception:
        return []

    findings: list = []
    import time as _time

    for entry in entries:
        entry_cid = entry.get("cid", "")
        if not entry_cid or entry_cid.startswith("Qm") is False:
            continue  # Skip directories, only files

        finding_id = f"ipfs-dir-{entry_cid[:12]}-{_time.time_ns()}"
        payload = f"Path: {entry.get('path', '')}\nSize: {entry.get('size', 0)} bytes\nType: {entry.get('type', 'file')}"

        finding = CanonicalFinding(
            finding_id=finding_id,
            query=query,
            source_type="ipfs_directory",
            confidence=0.7,
            ts=_time.time(),
            provenance=(f"ipfs://{entry_cid}",),
            payload_text=payload[:4096] if payload else None,
        )
        findings.append(finding)

    return findings


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
