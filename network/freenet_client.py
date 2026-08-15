"""
Freenet / Hyphanet Client — Decentralized censorship-resistant content mining.

Freenet (now Hyphanet) is a peer-to-peer platform for censorship-resistant

communication and publishing. Content is accessed via:
  - FProxy HTTP gateway at http://127.0.0.1:8888/
  - URI schemes: freenet:USK@..., freenet:CHK@..., freenet:SSK@...
  - FMS (Freenet Message System) for forum-like discussion

Key types:
  - USK (Updatable Subspace Key): versioned content, mutable sites
  - CHK (Content Hash Key): immutable content, fixed hash
  - SSK (Signed Subspace Key): signed mutable content

Architecture (M1 8GB-safe):
  - HTTP access via FProxy at 127.0.0.1:8888
  - Health check with 60s TTL cache
  - Content size cap: 10MB per fetch (Freenet can serve large files)
  - Fail-soft: returns empty list, never raises
  - Concurrency: 1 fetch slot (Freenet FProxy is single-threaded)
  - Gate: HLEDAC_ENABLE_FREENET=1

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
from hledac.universal.utils.asyncx import parallel_ok
from core import aclose

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────
FREENET_HOST: Final[str] = "127.0.0.1"
FREENET_FPROXY_PORT: Final[int] = 8888
FREENET_FPROXY_URL: Final[str] = f"http://{FREENET_HOST}:{FREENET_FPROXY_PORT}"
FREENET_TIMEOUT: Final[int] = 60  # Freenet is slow — 60s default
FREENET_MAX_SIZE: Final[int] = 10 * 1024 * 1024  # 10 MB
FREENET_CONCURRENCY: Final[int] = 1

# ── Freenet key patterns ──────────────────────────────────────────────────────
# USK: Updatable Subspace Key — mutable sites with versioning
USK_PATTERN = re.compile(r"\b(USK@[A-Za-z0-9~\-_]{40,44}/[A-Za-z0-9.\-_]+/\d+/)")
# CHK: Content Hash Key — immutable content files
CHK_PATTERN = re.compile(r"\b(CHK@[A-Za-z0-9~\-_]{40,60})")
# SSK: Signed Subspace Key — signed mutable content
SSK_PATTERN = re.compile(r"\b(SSK@[A-Za-z0-9~\-_]{40,60})")
# Generic Freenet URI
FREENET_URI_PATTERN = re.compile(
    r"freenet:(USK|CHK|SSK|KSK)@[A-Za-z0-9~\-_./]+", re.IGNORECASE,
)

# ── Known Freenet sites / freesites — OSINT entry points ──────────────────────
KNOWN_FREESITES: list[dict] = [
    {
        "name": "Freenet Project Index",
        "key": "USK@0I8g3~I2eVoFpQNnOJ4BtN6Jh2a0g~s-zR-OJdPXDgE,5mc~Q~8T2AQ7JKH0z1YezZn3vSi9ofT-jGBjIoj0Sg8,AQACAAE/index/0/",
        "description": "Official Freenet project index page",
    },
    {
        "name": "Enzo's Index",
        "key": "USK@k0jS~8wI5uPQhMKmPmhDvKxF1q~uAorB4zUCXGWjUEs,4mpHojASyQZsuRiX8fySyPBYJ0M~oD5EGQosCpvz0So,AQACAAE/sites/0/",
        "description": "Major Freenet site directory — links to most freesites",
    },
    {
        "name": "Freenet Wiki",
        "key": "USK@z1TZUz3~Fku~x9Mh8AA4~xJr6jTicJMicGQ~pP4Qxg8,zqrx4~4HX~nJvu4JMNqZKMOC2Q2xt~7QRjMJvGJxHjM,AQACAAE/wiki/0/",
        "description": "Freenet community wiki",
    },
    {
        "name": "FMS — Freenet Message System",
        "key": "USK@w3UWm72~wG6~g7MwEi64u5mTx10KRoFPfMaoOM4DxM0,sQ1fP2pkfY3~Ern0VzN~7UHOeIWM11g5kn9C~i7RrDo,AQACAAE/fms/138/",
        "description": "Decentralized forum/message board — potential OSINT goldmine",
    },
    {
        "name": "Sone — Social Network",
        "key": "USK@nwa8~P2vBvbh~dZ7kK~zvPJ~d7vHY0uiCplb~gm0W8g,GtqPwf3~lC~cI5nBq~jihZPpDdOvTBQRAVcBNkOGB58,AQACAAE/sone/17/",
        "description": "Microblogging/social network plugin — identity intelligence source",
    },
    {
        "name": "FlogHelper — Blog Platform",
        "key": "USK@QRZ~MnD4~NzwLLQxmpTbVK31M-oWb8FeM7uv6owFF0k,yMmMXy6u69dYpXUWhC9KJOxd1vDahJD4ZmkJ~ILkAvw,AQACAAE/floghelper/5/",
        "description": "Decentralized blogging — opinions, leaks, whistleblowing",
    },
]

# ── Health check cache ─────────────────────────────────────────────────────────
_freenet_available: bool | None = None
_freenet_check_time: float = 0.0
_FREENET_CHECK_TTL: Final[float] = 60.0


async def is_freenet_available() -> bool:
    """Check if Freenet/Hyphanet FProxy is running and accessible.

    Uses cached result with 60-second TTL to avoid excessive probes.
    Returns True if FProxy responds on port 8888, False otherwise.
    """
    global _freenet_available, _freenet_check_time
    now = time.monotonic()
    if now - _freenet_check_time < _FREENET_CHECK_TTL:
        return _freenet_available if _freenet_available is not None else False

    if os.getenv("HLEDAC_ENABLE_FREENET", "0").lower() not in ("1", "true", "yes", "on"):
        _freenet_available = False
        _freenet_check_time = now
        return False

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            resp = await client.get(f"{FREENET_FPROXY_URL}/")
            _freenet_available = resp.status_code < 500
    except Exception:
        _freenet_available = False

    _freenet_check_time = now
    return _freenet_available or False


# ── Key normalization ─────────────────────────────────────────────────────────


def _normalize_freenet_key(key: str) -> str:
    """Normalize a Freenet key to its FProxy URL form.

    Handles:
      - Raw key: "USK@..." -> "/freenet:USK@..."
      - freenet: URI: "freenet:USK@..." -> "/freenet:USK@..."
      - Full URL: "http://127.0.0.1:8888/freenet:USK@..." -> path extraction
    """
    key = key.strip()
    if key.startswith(FREENET_FPROXY_URL):
        key = key[len(FREENET_FPROXY_URL):]
    if key.startswith("freenet:"):
        return f"/{key}"
    if not key.startswith("/"):
        key = f"/{key}"
    return key


def extract_freenet_keys(text: str) -> list[str]:
    """Extract Freenet keys (USK, CHK, SSK) from text content.

    Used for link discovery during content crawling — enables following
    inter-freesite links to discover new content.

    Args:
        text: Raw text content (HTML, plain text, etc.)

    Returns:
        Deduplicated list of Freenet key strings found.
    """
    keys: list[str] = []
    seen: set[str] = set()
    for pattern in (USK_PATTERN, CHK_PATTERN, SSK_PATTERN, FREENET_URI_PATTERN):
        for match in pattern.finditer(text):
            key = match.group(0)
            if key not in seen:
                seen.add(key)
                keys.append(key)
    return keys


# ── Content fetching ──────────────────────────────────────────────────────────


async def fetch_freesite(
    key: str,
    timeout: int = FREENET_TIMEOUT,
    max_size: int = FREENET_MAX_SIZE,
) -> str | None:
    """Fetch content from a Freenet freesite via FProxy.

    Args:
        key: Freenet key (USK@..., CHK@..., SSK@... or freenet:USK@...)
        timeout: Request timeout in seconds (Freenet is slow — default 60s)
        max_size: Maximum response size in bytes

    Returns:
        Response text as string, or None if fetch failed.
    """
    if not await is_freenet_available():
        return None

    path = _normalize_freenet_key(key)
    url = f"{FREENET_FPROXY_URL}{path}"

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                content_length = resp.headers.get("Content-Length")
                if content_length:
                    if int(content_length) > max_size:
                        logger.warning(
                            "Freenet response too large: %s bytes for %s",
                            content_length, key[:60],
                        )
                        return None
                content = resp.text
                if len(content.encode("utf-8")) > max_size:
                    logger.warning(
                        "Freenet response too large after decode: %s", key[:60],
                    )
                    return None
                return content
            logger.debug(
                "Freenet fetch failed: status %s for %s",
                resp.status_code, key[:60],
            )
            return None
    except (httpx.TimeoutException, asyncio.TimeoutError):
        logger.debug("Freenet fetch timeout: %s", key[:60])
        return None
    except Exception as e:
        logger.debug("Freenet fetch error %s: %s", key[:60], e)
        return None


async def fetch_freesite_json(
    key: str,
    timeout: int = FREENET_TIMEOUT,
) -> dict | None:
    """Fetch JSON content from a Freenet freesite.

    Some Freenet plugins (Sone, FMS) serve JSON APIs.

    Args:
        key: Freenet key pointing to JSON content
        timeout: Request timeout in seconds

    Returns:
        Parsed JSON dict, or None if fetch failed.
    """
    if not await is_freenet_available():
        return None

    path = _normalize_freenet_key(key)
    url = f"{FREENET_FPROXY_URL}{path}"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.json()
    except Exception as e:
        logger.debug("Freenet JSON fetch error %s: %s", key[:60], e)
    return None


# ── Freesite enumeration ──────────────────────────────────────────────────────


class FreenetSiteEnumerator:
    """Enumerate Freenet freesites from known keys and link discovery.

    Scans known freesite keys and follows inter-freesite links to
    discover new content. Bounded by max_sites (default 30) and
    max_depth (default 1 — no recursive crawling on M1 8GB).

    M1 8GB-safe: single FProxy slot, bounded depth, timeout-gated.
    """

    __slots__ = ("_max_sites", "_max_depth", "_seen_keys")

    def __init__(self, max_sites: int = 30, max_depth: int = 1) -> None:
        self._max_sites = max_sites
        self._max_depth = max_depth
        self._seen_keys: set[str] = set()

    async def enumerate(self, keyword: str = "") -> list[dict]:
        """Enumerate Freenet freesites, optionally filtered by keyword.

        Args:
            keyword: Optional filter — only return sites where name/description
                     or content matches keyword (case-insensitive).

        Returns:
            List of dicts with {name, key, description, content_preview,
            discovered_keys}.
        """
        results: list[dict] = []

        seed_sites = KNOWN_FREESITES
        if keyword:
            kw = keyword.lower()
            seed_sites = [
                s for s in KNOWN_FREESITES
                if kw in s["name"].lower() or kw in s.get("description", "").lower()
            ]

        from hledac.universal.core.concurrency import (
            ConcurrencyCategory,
            get_semaphore,
        )
        sem = get_semaphore(ConcurrencyCategory.SCRAPE_GENERAL)

        async def _probe_site(site: dict) -> dict | None:
            async with sem:
                key = site["key"]
                if key in self._seen_keys:
                    return None
                self._seen_keys.add(key)
                try:
                    content = await fetch_freesite(key)
                    if content:
                        title = site["name"]
                        if "<title" in content.lower():
                            title_match = re.search(
                                r"<title[^>]*>([^<]+)", content, re.IGNORECASE,
                            )
                            if title_match:
                                title = title_match.group(1).strip()[:200]
                        discovered = extract_freenet_keys(content)
                        for dk in discovered:
                            if (
                                dk not in self._seen_keys
                                and len(self._seen_keys) < self._max_sites
                            ):
                                self._seen_keys.add(dk)
                        return {
                            "name": title,
                            "key": key,
                            "description": site.get("description", ""),
                            "content_preview": content[:4096],
                            "discovered_keys": discovered[:20],
                        }
                except Exception as e:
                    logger.debug("Freenet probe error for %s: %s", site["name"], e)
                return None

        tasks = [_probe_site(s) for s in seed_sites]
        found = await parallel_ok(*tasks, label="freenet_client:enumerate")
        for item in found:
            if isinstance(item, dict) and item:
                results.append(item)

        return results[:self._max_sites]


# ── Finding conversion ────────────────────────────────────────────────────────


async def freenet_to_findings(query: str) -> list[CanonicalFinding]:
    """Fetch Freenet/Hyphanet content and return as CanonicalFinding list.

    Args:
        query: Original search query / keyword

    Returns:
        List of CanonicalFinding with source_type="freenet_content".
        Returns empty list if HLEDAC_ENABLE_FREENET is not "1" or daemon unavailable.
    """
    if os.getenv("HLEDAC_ENABLE_FREENET", "0").lower() not in ("1", "true", "yes", "on"):
        return []
    if not await is_freenet_available():
        return []

    findings: list[CanonicalFinding] = []
    try:
        enumerator = FreenetSiteEnumerator(max_sites=20, max_depth=1)
        sites = await enumerator.enumerate(keyword=query)
        for site in sites:
            key_short = site["key"][:40] if len(site["key"]) > 40 else site["key"]
            finding_id = f"freenet-{key_short}-{int(time.time() * 1000)}"
            finding = CanonicalFinding(
                finding_id=finding_id,
                query=query,
                source_type="freenet_content",
                confidence=0.65,
                ts=time.time(),
                provenance=(f"freenet:{site['key']}",),
                payload_text=site.get("content_preview", "")[:4096],
            )
            findings.append(finding)
    except Exception as e:
        logger.debug("Freenet to findings failed: %s", e)

    return findings


async def search_freenet(keyword: str, max_results: int = 20) -> list[dict]:
    """Search across known Freenet freesites for keyword matches.

    Performs distributed content grep — Freenet has no centralized index.
    Uses known entry points + link discovery.

    Args:
        keyword: Search keyword
        max_results: Maximum number of results to return

    Returns:
        List of dicts with {key, name, snippet, confidence}.
    """
    if not await is_freenet_available():
        return []

    results: list[dict] = []
    kw = keyword.lower()
    enumerator = FreenetSiteEnumerator(max_sites=30, max_depth=1)
    sites = await enumerator.enumerate()

    for site in sites:
        content = site.get("content_preview", "")
        if content and kw in content.lower():
            idx = content.lower().find(kw)
            start = max(0, idx - 100)
            end = min(len(content), idx + len(keyword) + 100)
            snippet = content[start:end].replace("\n", " ").strip()
            results.append({
                "key": site["key"],
                "name": site.get("name", ""),
                "snippet": snippet,
                "confidence": 0.6,
            })
            if len(results) >= max_results:
                break

    return results


# ── Test helpers ──────────────────────────────────────────────────────────────


def reset_freenet_health_cache() -> None:
    """Reset the health check cache. TEST-ONLY."""
    global _freenet_available, _freenet_check_time
    _freenet_available = None
    _freenet_check_time = 0.0
