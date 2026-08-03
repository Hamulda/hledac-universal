"""
ZeroNet Client — Decentralized P2P website mining via ZeroNet JSON API.

ZeroNet is a decentralized web platform using Bitcoin cryptography and
BitTorrent network. Sites have Bitcoin-style addresses (e.g., 1ZeroMe...,
1Talk..., 1Search...) and are accessed via local proxy at 127.0.0.1:43110.

Architecture (M1 8GB-safe):
  - HTTP JSON API access at http://127.0.0.1:43110/
  - Health check with 60s TTL cache
  - Known site index for enumeration
  - Content size cap: 2MB per site fetch
  - Fail-soft: returns empty list, never raises
  - Concurrency: 2 fetch slots (semaphore from ConcurrencyBudgetRegistry)
  - Gate: HLEDAC_ENABLE_ZERONET=1

F230: Alternative Protocol Stack integration.
ISSUE-005: Missing ZeroNet & Freenet/Hyphanet Content Mining.
"""

import asyncio
import logging
import os
import re
import time
from typing import Final

import httpx
from hledac.universal.knowledge.duckdb_store import CanonicalFinding
from hledac.universal.utils.async_helpers import parallel_ok

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────
ZERONET_HOST: Final[str] = "127.0.0.1"
ZERONET_PORT: Final[int] = 43110
ZERONET_BASE_URL: Final[str] = f"http://{ZERONET_HOST}:{ZERONET_PORT}"
ZERONET_TIMEOUT: Final[int] = 30
ZERONET_MAX_SIZE: Final[int] = 2 * 1024 * 1024  # 2 MB — M1 8GB safe
ZERONET_CONCURRENCY: Final[int] = 2
SITE_ADDRESS_PATTERN = re.compile(r"\b1[A-Za-z0-9]{32,34}\b")

# ── Known ZeroNet sites — censorship-resistant OSINT data sources ──────────────
KNOWN_ZERONET_SITES: list[dict] = [
    {
        "name": "ZeroMe",
        "address": "1HeLLo4uzjaLetFx6NH3PMwFP3qbRbTf3D",
        "description": "Decentralized social network (Twitter-like)",
    },
    {
        "name": "ZeroTalk",
        "address": "1TaLkFrMwvbNsooF4ioKAY9EuxTBTjipT",
        "description": "Decentralized forum",
    },
    {
        "name": "ZeroMail",
        "address": "1MaiL5gfBM1cyb4a8e3iiL8L5gXmoAJu27",
        "description": "Decentralized encrypted email",
    },
    {
        "name": "ZeroBlog",
        "address": "1BLogC9LN4oPDcruNz3qo1ysa133E9AGg8",
        "description": "Decentralized blogging platform",
    },
    {
        "name": "ZeroSites",
        "address": "1SiTEs9g4L2NVUVgNHvL4E6FGqMkM8UNq",
        "description": "ZeroNet site directory",
    },
    {
        "name": "ZeroSearch",
        "address": "1SearchPd3khzMAtsg3U5N8Djm6fyHq98",
        "description": "ZeroNet search engine",
    },
    {
        "name": "KaffieSearch",
        "address": "1K2FD8LkYJGqY5erGD3HCDykUuvjeG8J2z",
        "description": "ZeroNet search engine v2 (alternative)",
    },
    {
        "name": "ZeroNet Dev Center",
        "address": "1DocsYf2tZVVMEMJFHiAdsM2ZENGM7D6R",
        "description": "ZeroNet documentation hub",
    },
    {
        "name": "ZeroPaste",
        "address": "1PasteLdYCWxMGbs6nAfcTnHXixmpmHYp",
        "description": "Decentralized pastebin — potential leak source",
    },
    {
        "name": "ZeroUp",
        "address": "1UploadrW8gVQMb6YnVgqDmZ4kNuhxzA3Q",
        "description": "Decentralized file upload — potential data exfil",
    },
]

# ── Health check cache ─────────────────────────────────────────────────────────
_zeronet_available: bool | None = None
_zeronet_check_time: float = 0.0
_ZERONET_CHECK_TTL: Final[float] = 60.0


async def is_zeronet_available() -> bool:
    """Check if ZeroNet daemon is running and accessible.

    Uses cached result with 60-second TTL to avoid excessive probes.
    Returns True if ZeroNet proxy responds, False otherwise.
    """
    global _zeronet_available, _zeronet_check_time
    now = time.monotonic()
    if now - _zeronet_check_time < _ZERONET_CHECK_TTL:
        return _zeronet_available if _zeronet_available is not None else False

    if os.getenv("HLEDAC_ENABLE_ZERONET", "0").lower() not in ("1", "true", "yes", "on"):
        _zeronet_available = False
        _zeronet_check_time = now
        return False

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            resp = await client.get(f"{ZERONET_BASE_URL}/")
            _zeronet_available = resp.status_code < 500
    except Exception:
        _zeronet_available = False

    _zeronet_check_time = now
    return _zeronet_available or False


# ── Content fetching ──────────────────────────────────────────────────────────


async def fetch_zeronet_site(
    address: str,
    inner_path: str = "",
    timeout: int = ZERONET_TIMEOUT,
    max_size: int = ZERONET_MAX_SIZE,
) -> str | None:
    """Fetch content from a ZeroNet site.

    Args:
        address: ZeroNet site address (e.g., "1HeLLo4uzjaLetFx6NH3PMwFP3qbRbTf3D")
        inner_path: Inner file path within the site (default: "" = index.html)
        timeout: Request timeout in seconds
        max_size: Maximum response size in bytes

    Returns:
        Response text as string, or None if fetch failed.
    """
    if not await is_zeronet_available():
        return None

    url = f"{ZERONET_BASE_URL}/{address}"
    if inner_path:
        url = f"{url}/{inner_path.lstrip('/')}"

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                content_length = resp.headers.get("Content-Length")
                if content_length:
                    if int(content_length) > max_size:
                        logger.warning(
                            "ZeroNet response too large: %s bytes for %s",
                            content_length, address,
                        )
                        return None
                content = resp.text
                if len(content.encode("utf-8")) > max_size:
                    logger.warning(
                        "ZeroNet response too large after decode: %s", address,
                    )
                    return None
                return content
            logger.debug(
                "ZeroNet fetch failed: status %s for %s", resp.status_code, address,
            )
            return None
    except (httpx.TimeoutException, asyncio.TimeoutError):
        logger.debug("ZeroNet fetch timeout: %s", address)
        return None
    except Exception as e:
        logger.debug("ZeroNet fetch error %s: %s", address, e)
        return None


async def fetch_zeronet_json(
    address: str,
    inner_path: str,
    timeout: int = ZERONET_TIMEOUT,
) -> dict | None:
    """Fetch JSON content from a ZeroNet site (e.g., data.json files).

    Args:
        address: ZeroNet site address
        inner_path: Path to JSON file within the site
        timeout: Request timeout in seconds

    Returns:
        Parsed JSON dict, or None if fetch failed.
    """
    if not await is_zeronet_available():
        return None

    url = f"{ZERONET_BASE_URL}/{address}/{inner_path.lstrip('/')}"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                content_length = resp.headers.get("Content-Length")
                if content_length and int(content_length) > ZERONET_MAX_SIZE:
                    return None
                return resp.json()
    except Exception as e:
        logger.debug("ZeroNet JSON fetch error %s/%s: %s", address, inner_path, e)
    return None


# ── Site enumeration ──────────────────────────────────────────────────────────


class ZeroNetSiteEnumerator:
    """Enumerate ZeroNet sites from known addresses and content discovery.

    Scans known ZeroNet site addresses and extracts site references
    from fetched content (inter-site linking via Bitcoin-style addresses).

    M1 8GB-safe: bounded at max_sites (default 50), semaphore-gated I/O.
    """

    __slots__ = ("_max_sites", "_seen")

    def __init__(self, max_sites: int = 50) -> None:
        self._max_sites = max_sites
        self._seen: set[str] = set()

    async def enumerate(self, keyword: str = "") -> list[dict]:
        """Enumerate ZeroNet sites, optionally filtered by keyword.

        Args:
            keyword: Optional filter — only return sites where name/description
                     matches keyword (case-insensitive substring match).

        Returns:
            List of dicts with {name, address, description, content_preview}.
        """
        results: list[dict] = []

        seed_sites = KNOWN_ZERONET_SITES
        if keyword:
            kw = keyword.lower()
            seed_sites = [
                s for s in KNOWN_ZERONET_SITES
                if kw in s["name"].lower() or kw in s.get("description", "").lower()
            ]

        from hledac.universal.core.concurrency import (
            ConcurrencyCategory,
            get_semaphore,
        )
        sem = get_semaphore(ConcurrencyCategory.SCRAPE_GENERAL)

        async def _probe_site(site: dict) -> dict | None:
            async with sem:
                address = site["address"]
                if address in self._seen:
                    return None
                self._seen.add(address)
                try:
                    content = await fetch_zeronet_site(address)
                    if content:
                        title = site["name"]
                        if "<title" in content.lower():
                            title_match = re.search(
                                r"<title[^>]*>([^<]+)", content, re.IGNORECASE,
                            )
                            if title_match:
                                title = title_match.group(1).strip()[:200]
                        # Discover cross-linked sites
                        found_addresses = SITE_ADDRESS_PATTERN.findall(content)
                        for addr in found_addresses:
                            if (
                                addr not in self._seen
                                and len(self._seen) < self._max_sites
                            ):
                                self._seen.add(addr)
                        return {
                            "name": title,
                            "address": address,
                            "description": site.get("description", ""),
                            "content_preview": content[:4096],
                        }
                except Exception as e:
                    logger.debug("ZeroNet probe error for %s: %s", address, e)
                return None

        tasks = [_probe_site(s) for s in seed_sites]
        found = await parallel_ok(*tasks, label="zeronet_client:enumerate")
        for item in found:
            if isinstance(item, dict) and item:
                results.append(item)

        return results[:self._max_sites]


# ── Finding conversion ────────────────────────────────────────────────────────


async def zeronet_to_findings(query: str) -> list[CanonicalFinding]:
    """Fetch ZeroNet content and return as CanonicalFinding list.

    Args:
        query: Original search query / keyword

    Returns:
        List of CanonicalFinding with source_type="zeronet_content".
        Returns empty list if HLEDAC_ENABLE_ZERONET is not "1" or daemon unavailable.
    """
    if os.getenv("HLEDAC_ENABLE_ZERONET", "0").lower() not in ("1", "true", "yes", "on"):
        return []
    if not await is_zeronet_available():
        return []

    findings: list[CanonicalFinding] = []
    try:
        enumerator = ZeroNetSiteEnumerator(max_sites=30)
        sites = await enumerator.enumerate(keyword=query)
        for site in sites:
            finding_id = f"zeronet-{site['address'][:12]}-{int(time.time() * 1000)}"
            finding = CanonicalFinding(
                finding_id=finding_id,
                query=query,
                source_type="zeronet_content",
                confidence=0.7,
                ts=time.time(),
                provenance=(f"zeronet://{site['address']}",),
                payload_text=site.get("content_preview", "")[:4096],
            )
            findings.append(finding)
    except Exception as e:
        logger.debug("ZeroNet to findings failed: %s", e)

    return findings


async def search_zeronet_sites(keyword: str, max_results: int = 20) -> list[dict]:
    """Search across known ZeroNet sites for keyword matches.

    Performs distributed content grep across known entry points —
    no centralized index exists for ZeroNet (by design).

    Args:
        keyword: Search keyword
        max_results: Maximum number of results to return

    Returns:
        List of dicts with {address, name, snippet, confidence}.
    """
    if not await is_zeronet_available():
        return []

    results: list[dict] = []
    kw = keyword.lower()
    enumerator = ZeroNetSiteEnumerator(max_sites=50)
    sites = await enumerator.enumerate()

    for site in sites:
        content = site.get("content_preview", "")
        if content and kw in content.lower():
            idx = content.lower().find(kw)
            start = max(0, idx - 80)
            end = min(len(content), idx + len(keyword) + 80)
            snippet = content[start:end].replace("\n", " ").strip()
            results.append({
                "address": site["address"],
                "name": site.get("name", ""),
                "snippet": snippet,
                "confidence": 0.65,
            })
            if len(results) >= max_results:
                break

    return results


# ── Test helpers ──────────────────────────────────────────────────────────────


def reset_zeronet_health_cache() -> None:
    """Reset the health check cache. TEST-ONLY."""
    global _zeronet_available, _zeronet_check_time
    _zeronet_available = None
    _zeronet_check_time = 0.0
