from __future__ import annotations

import os
from urllib.parse import quote

import aiohttp

WAYBACK_CDX = "https://web.archive.org/cdx/search/cdx"
RDAP_DOMAIN = "https://rdap.org/domain/"
URLSCAN_SEARCH = "https://urlscan.io/api/v1/search/"

async def wayback_cdx_lookup(url_or_host: str, limit: int = 10, timeout_s: float = 8.0) -> list[dict]:
    """Compat: Wayback CDX lookup — forwarding na archive_discovery.wayback_cdx_lookup.
    AUTHORITY: archive_discovery.wayback_cdx_lookup() je canonical.
    REMOVAL CONDITION: HE-003 (F025_SOURCE_TRANSPORT) — fetch_coordinator přejde na
    archive_discovery.wayback_cdx_lookup() přímo; pak odstranit tuto vrstvu.
    """
    from hledac.universal.intelligence.archive_discovery import (
        wayback_cdx_lookup as _canonical_lookup,
    )
    return await _canonical_lookup(url_or_host, limit=limit, timeout_s=timeout_s)

async def rdap_lookup(domain: str, timeout_s: float = 8.0) -> dict | None:
    timeout = aiohttp.ClientTimeout(total=timeout_s)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(RDAP_DOMAIN + quote(domain, safe="")) as resp:
            if resp.status >= 400:
                return None
            data = await resp.json()
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
    api_key = os.environ.get("URLSCAN_API_KEY", "").strip()
    if not api_key:
        return []
    headers = {"API-Key": api_key}
    params = {"q": query, "size": str(size)}
    timeout = aiohttp.ClientTimeout(total=timeout_s)
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        async with session.get(URLSCAN_SEARCH, params=params) as resp:
            resp.raise_for_status()
            data = await resp.json()
    results = data.get("results") or []
    out = []
    for i, row in enumerate(results, 1):
        page = row.get("page") or {}
        task = row.get("task") or {}
        out.append({
            "title": page.get("title") or task.get("url") or "",
            "url": task.get("url") or page.get("url") or "",
            "snippet": f"urlscan domain={page.get('domain','')} ip={page.get('ip','')}",
            "backend": "urlscan",
            "rank": i,
            "provider": "urlscan_search",
            "source": "urlscan",
        })
    return out
