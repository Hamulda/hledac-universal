#!/usr/bin/env python3
"""
I2P Eepsite Client — Access I2P HTTP proxy for hidden services.

I2P is an anonymizing network with "eepsites" (hidden services).
Access via HTTP proxy at localhost:4444 (if I2P daemon is running).

F230: Alternative Protocol Stack integration.

Key features:
  - Health check: is_i2p_available()
  - Fetch eepsites via I2P HTTP proxy
  - Known eepsites index
  - Fail gracefully if I2P not running
  - Return list[CanonicalFinding] with source_type="i2p_content"
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

logger = logging.getLogger(__name__)

# =============================================================================
# Constants
# =============================================================================
I2P_PROXY_HOST: str = "127.0.0.1"
I2P_PROXY_PORT: int = 4444  # HTTP proxy (browser-like)
I2P_SOCKS_PORT: int = 7654  # SOCKS5 proxy (lower-level anonymity)
I2P_PROXY_URL: str = f"http://{I2P_PROXY_HOST}:{I2P_PROXY_PORT}"
I2P_SOCKS_URL: str = f"socks5://{I2P_PROXY_HOST}:{I2P_SOCKS_PORT}"

# I2P default timeout
I2P_TIMEOUT: int = 30
I2P_MAX_SIZE: int = 2 * 1024 * 1024  # 2MB cap

# Known eepsites (sample)
KNOWN_EEPSITES: list[dict] = [
    {"name": "I2P Wiki", "url": "http://i2pwiki.i2p", "description": "I2P documentation"},
    {"name": "NotBob", "url": "http://notbob.i2p", "description": "I2P community forum"},
    {"name": "I2P Stats", "url": "http://stats.i2p", "description": "Network statistics"},
    {"name": "Zeronet", "url": "http://127.0.0.1:43110", "description": "Decentralized websites"},
    {"name": "I2P Forum", "url": "http://forum.i2p", "description": "I2P discussion"},
]

# Cached availability status (per proxy type)
_i2p_http_available: bool | None = None
_i2p_socks_available: bool | None = None
_i2p_check_time: float = 0
_I2P_CHECK_TTL: float = 60.0  # Re-check every 60 seconds


# =============================================================================
# Availability Check
# =============================================================================
async def is_i2p_available(proxy_type: str = "http") -> bool:
    """
    Check if I2P proxy is running and accessible.

    Args:
        proxy_type: "http" (port 4444) or "socks5" (port 7654)

    Uses cached result with 60-second TTL to avoid excessive probes.

    Returns:
        True if I2P proxy is available, False otherwise
    """
    global _i2p_http_available, _i2p_socks_available, _i2p_check_time

    # Check cache
    now = time.monotonic()
    if now - _i2p_check_time < _I2P_CHECK_TTL:
        if proxy_type == "http":
            return _i2p_http_available if _i2p_http_available is not None else False
        else:
            return _i2p_socks_available if _i2p_socks_available is not None else False

    # Environment override
    if os.getenv("HLEDAC_I2P_FORCE_UNAVAILABLE", "").lower() in ("1", "true", "yes"):
        _i2p_http_available = False
        _i2p_socks_available = False
        _i2p_check_time = now
        return False

    try:
        import aiohttp

        client_timeout = aiohttp.ClientTimeout(total=5)

        # Check HTTP proxy (port 4444)
        try:
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                async with session.get(f"{I2P_PROXY_URL}/") as resp:
                    if resp.status < 500:
                        _i2p_http_available = True
                    else:
                        _i2p_http_available = False
        except Exception:
            _i2p_http_available = False

        # Check SOCKS5 proxy (port 7654) via aiohttp_socks
        try:
            import aiohttp_socks
            connector = aiohttp_socks.SocksConnector.from_url(I2P_SOCKS_URL, rdns=True)
            async with aiohttp.ClientSession(timeout=client_timeout, connector=connector) as session:
                # Try to connect through SOCKS5
                async with session.get("http://i2pwiki.i2p", timeout=aiohttp.ClientTimeout(total=3)) as resp:
                    _i2p_socks_available = resp.status < 500
        except Exception:
            _i2p_socks_available = False

    except ImportError:
        # aiohttp_socks not available, only HTTP proxy can be checked
        if proxy_type == "socks5":
            _i2p_socks_available = False
        logger.debug("aiohttp_socks not available, SOCKS5 check skipped")

    except Exception as e:
        logger.debug(f"I2P proxy check error: {e}")
        _i2p_http_available = False
        _i2p_socks_available = False

    _i2p_check_time = now

    if proxy_type == "http":
        return _i2p_http_available or False
    else:
        return _i2p_socks_available or False


# =============================================================================
# Eepsite Fetching
# =============================================================================
async def fetch_eepsite(
    url: str,
    timeout: int = I2P_TIMEOUT,
    max_size: int = I2P_MAX_SIZE,
) -> str | None:
    """
    Fetch content from an I2P eepsite via HTTP proxy.

    Args:
        url: Eepsite URL (e.g., "http://i2pwiki.i2p/")
        timeout: Request timeout in seconds
        max_size: Maximum response size in bytes

    Returns:
        Response text as string, or None if fetch failed
    """
    if not await is_i2p_available():
        return None

    # Normalize URL
    if not url.startswith("http"):
        url = f"http://{url}"

    try:
        import aiohttp

        client_timeout = aiohttp.ClientTimeout(total=timeout)
        async with aiohttp.ClientSession(timeout=client_timeout) as session:
            # Route through I2P HTTP proxy
            async with session.get(url, proxy=I2P_PROXY_URL) as resp:
                if resp.status == 200:
                    # Check content length
                    content_length = resp.headers.get("Content-Length")
                    if content_length:
                        if int(content_length) > max_size:
                            logger.warning(f"I2P response too large: {content_length} bytes")
                            return None

                    content = await resp.text()

                    # Double-check size after decode
                    if len(content.encode("utf-8")) > max_size:
                        logger.warning(f"I2P response too large after decode")
                        return None

                    return content
                else:
                    logger.debug(f"I2P fetch failed: status {resp.status} for {url}")
                    return None

    except asyncio.TimeoutError:
        logger.debug(f"I2P fetch timeout: {url}")
        return None
    except Exception as e:
        logger.debug(f"I2P fetch error {url}: {e}")
        return None


async def fetch_eepsite_socks5(
    url: str,
    timeout: int = I2P_TIMEOUT,
    max_size: int = I2P_MAX_SIZE,
) -> str | None:
    """
    Fetch content from an I2P eepsite via SOCKS5 proxy.

    This uses the lower-level SOCKS5 protocol (port 7654) for better anonymity.
    Falls back to HTTP proxy if aiohttp_socks is not available.

    Args:
        url: Eepsite URL (e.g., "http://i2pwiki.i2p/")
        timeout: Request timeout in seconds
        max_size: Maximum response size in bytes

    Returns:
        Response text as string, or None if fetch failed
    """
    if not await is_i2p_available(proxy_type="socks5"):
        return None

    # Normalize URL
    if not url.startswith("http"):
        url = f"http://{url}"

    try:
        import aiohttp
        import aiohttp_socks

        connector = aiohttp_socks.SocksConnector.from_url(I2P_SOCKS_URL, rdns=True)
        client_timeout = aiohttp.ClientTimeout(total=timeout)

        async with aiohttp.ClientSession(timeout=client_timeout, connector=connector) as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    content_length = resp.headers.get("Content-Length")
                    if content_length and int(content_length) > max_size:
                        logger.warning(f"I2P SOCKS5 response too large: {content_length}")
                        return None

                    content = await resp.text()
                    if len(content.encode("utf-8")) > max_size:
                        logger.warning("I2P SOCKS5 response too large after decode")
                        return None

                    return content
                else:
                    logger.debug(f"I2P SOCKS5 fetch failed: status {resp.status} for {url}")
                    return None

    except ImportError:
        logger.debug("aiohttp_socks not available for SOCKS5 fetch")
        return None
    except asyncio.TimeoutError:
        logger.debug(f"I2P SOCKS5 fetch timeout: {url}")
        return None
    except Exception as e:
        logger.debug(f"I2P SOCKS5 fetch error {url}: {e}")
        return None


# =============================================================================
# Known Eepsites Discovery
# =============================================================================
async def discover_eepsites() -> list[dict]:
    """
    Fetch content from known I2P eepsites.

    Returns:
        List of dicts with {url, content, title}
    """
    discovered: list[dict] = []

    if not await is_i2p_available():
        return discovered

    sem = asyncio.Semaphore(2)  # M1 memory: max 2 concurrent

    async def fetch_one(eepsite: dict) -> dict | None:
        async with sem:
            try:
                content = await fetch_eepsite(eepsite["url"])
                if content:
                    # Extract title
                    title = eepsite["name"]
                    if "<title" in content.lower():
                        import re
                        title_match = re.search(r"<title[^>]*>([^<]+)", content, re.IGNORECASE)
                        if title_match:
                            title = title_match.group(1).strip()

                    return {
                        "url": eepsite["url"],
                        "name": eepsite["name"],
                        "content": content[:10000],  # Cap content
                        "title": title,
                    }
            except Exception:
                pass
            return None

    import asyncio

    tasks = [fetch_one(e) for e in KNOWN_EEPSITES]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for result in results:
        if isinstance(result, dict) and result:
            discovered.append(result)

    return discovered


# =============================================================================
# As CanonicalFindings
# =============================================================================
async def i2p_to_findings(query: str) -> list:
    """
    Fetch I2P content and return as CanonicalFinding list.

    Args:
        query: Original search query

    Returns:
        List of CanonicalFinding
    """
    if os.getenv("HLEDAC_ENABLE_ALT_PROTOCOLS", "0") != "1":
        return []

    if not await is_i2p_available():
        return []

    from hledac.universal.knowledge.duckdb_store import CanonicalFinding

    findings: list = []

    try:
        eepsites = await discover_eepsites()

        for site in eepsites:
            finding = CanonicalFinding(
                finding_id=f"i2p-{int(time.time() * 1000)}",
                query=query,
                source_type="i2p_content",
                confidence=0.65,  # I2P has lower confidence (anonymity)
                ts=time.time(),
                provenance=(site["url"],),
                payload_text=site.get("content", "")[:4096] if site.get("content") else None,
            )
            findings.append(finding)

    except Exception as e:
        logger.debug(f"I2P to findings failed: {e}")

    return findings


# =============================================================================
# Router Console Access
# =============================================================================
async def get_i2p_router_info() -> dict | None:
    """
    Get I2P router information from console.

    Returns:
        Dict with router stats, or None if unavailable
    """
    if not await is_i2p_available():
        return None

    try:
        import aiohttp
        import json

        client_timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=client_timeout) as session:
            # I2P router console JSON stats
            async with session.get(
                f"{I2P_PROXY_URL}/?page=stats",
                proxy=I2P_PROXY_URL,
            ) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    # Try to parse as JSON if possible
                    try:
                        return json.loads(text)
                    except Exception:
                        # Return raw text info
                        return {"raw": text[:1000]}
    except Exception:
        pass

    return None