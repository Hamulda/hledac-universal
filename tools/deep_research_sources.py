"""
Deep research sources — web intelligence gathering utilities.

Active call-sites:
  - coordinators/fetch_coordinator.py:2618 (wayback_cdx_lookup, urlscan_search)
  - tests/archive/probes/probe_8bh/test_wayback_client_8bh.py

Deprecation status:
  - TECH-DEBT-001: HE-003 (F025_SOURCE_TRANSPORT)
    Status: PENDING — wayback_cdx_lookup shim still actively used by fetch_coordinator
    Tracking: migrate fetch_coordinator.py → recon.archive_discovery.wayback_cdx_lookup()
    Priority: MEDIUM (adds indirection overhead)
"""

from __future__ import annotations

import os
from urllib.parse import quote

import httpx
from _core import aclose

__all__ = ['wayback_cdx_lookup', 'rdap_lookup', 'urlscan_search']

WAYBACK_CDX = "https://web.archive.org/cdx/search/cdx"
RDAP_DOMAIN = "https://rdap.org/domain/"
URLSCAN_SEARCH = "https://urlscan.io/api/v1/search/"


async def wayback_cdx_lookup(
    url_or_host: str, limit: int = 10, timeout_s: float = 8.0
) -> list[dict]:
    """Compat: Wayback CDX lookup — forwarding na archive_discovery.wayback_cdx_lookup.
    AUTHORITY: archive_discovery.wayback_cdx_lookup() je canonical.
    DEPRECATION: TECH-DEBT-001 — pending removal after fetch_coordinator migration.
    """
    from hledac.universal.intel.archive_discovery import (
        wayback_cdx_lookup as _canonical_lookup,
    )

    return await _canonical_lookup(url_or_host, limit=limit, timeout_s=timeout_s)


async def rdap_lookup(domain: str, timeout_s: float = 8.0) -> dict | None:
    """RDAP lookup for domain registration data.
    F-01: Uses canonical session pool for connection reuse.
    Returns None on error (consistent with single-item lookups).
    """
    try:
        from hledac.universal.transport.session_pool import session_pool
    except Exception:
        return None
    try:
        session = await session_pool.httpx()
        response = await session.get(
            RDAP_DOMAIN + quote(domain, safe=""),
            timeout=httpx.Timeout(timeout_s),
    )
        if response.status_code >= 400:
            return None
        data: dict = await response.json()
    except Exception:
        return None
    return {
        "ldhName": data.get("ldhName"),
        "handle": data.get("handle"),
        "port43": data.get("port43"),
        "status": data.get("status"),
        "links": data.get("links"),
        "events": data.get("events"),
        "nameservers": data.get("nameservers"),
    }


async def urlscan_search(query: str, size: int = 10, timeout_s: float = 8.0) -> list[dict]:
    """URLScan.io search for threat intelligence.
    F-01: Uses canonical session pool; requires URLSCAN_API_KEY env var.
    Returns empty list on error (consistent with multi-item searches).
    """
    api_key = os.environ.get("URLSCAN_API_KEY", "").strip()
    if not api_key:
        return []
    headers = {"API-Key": api_key}
    params = {"q": query, "size": str(size)}
    try:
        from hledac.universal.transport.session_pool import session_pool
    except Exception:
        return []
    try:
        session = await session_pool.httpx()
        response = await session.get(
            URLSCAN_SEARCH,
            params=params,
            headers=headers,
            timeout=httpx.Timeout(timeout_s),
    )
        response.raise_for_status()
        data: dict = await response.json()
    except Exception:
        return []
    results = data.get("results") or []
    out = []
    for i, row in enumerate(results, 1):
        page = row.get("page") or {}
        task = row.get("task") or {}
        out.append(
            {
                "title": page.get("title") or task.get("url") or "",
                "url": task.get("url") or page.get("url") or "",
                "snippet": f"urlscan domain={page.get('domain', '')} ip={page.get('ip', '')}",
                "backend": "urlscan",
                "rank": i,
                "provider": "urlscan_search",
                "source": "urlscan",
            }
    )
    return out
