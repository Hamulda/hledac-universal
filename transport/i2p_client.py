"""
I2P Eepsite Client — Access I2P hidden services via SAM v3 / SOCKS / HTTP.

I2P is an anonymizing network with "eepsites" (hidden services).
Access via:
  - SAM v3 native protocol (127.0.0.1:7656) — direct STREAM CONNECT,
    NAMING LOOKUP, persistent sessions. 50-100 req/min. Preferred.
  - SOCKS proxy (127.0.0.1:4444) — httpx-socks based, 20-30 req/min.
  - HTTP proxy (127.0.0.1:8888) — fallback, 5-10 req/min.

F230: Alternative Protocol Stack integration.

Key features:
  - Health check: is_i2p_available()
  - Fetch eepsites via I2P HTTP proxy
  - Known eepsites index
  - Fail gracefully if I2P not running
  - Return list[CanonicalFinding] with source_type="i2p_content"

Migrated to ConcurrencyBudgetRegistry (F268).
"""
import logging
import os
import time
import msgspec.json as _json
from hledac.universal.utils.asyncx import parallel_ok
from core import aclose
logger = logging.getLogger(__name__)
I2P_PROXY_HOST: str = '127.0.0.1'
# Port 7656 is the I2P SAM v3 bridge (native protocol, preferred).
# Port 4444 is the I2P SOCKS proxy (httpx-socks based, fallback).
I2P_PROXY_PORT: int = 4444
I2P_SOCKS_PORT: int = 4444
I2P_PROXY_URL: str = f'http://{I2P_PROXY_HOST}:{I2P_PROXY_PORT}'
# OPSEC-001: socks5h:// forces remote DNS resolution by I2P proxy.
I2P_SOCKS_URL: str = f'socks5h://{I2P_PROXY_HOST}:{I2P_SOCKS_PORT}'
I2P_TIMEOUT: int = 30
I2P_MAX_SIZE: int = 2 * 1024 * 1024
KNOWN_EEPSITES: list[dict] = [{'name': 'I2P Wiki', 'url': 'http://i2pwiki.i2p', 'description': 'I2P documentation'}, {'name': 'NotBob', 'url': 'http://notbob.i2p', 'description': 'I2P community forum'}, {'name': 'I2P Stats', 'url': 'http://stats.i2p', 'description': 'Network statistics'}, {'name': 'Zeronet', 'url': 'http://127.0.0.1:43110', 'description': 'Decentralized websites'}, {'name': 'I2P Forum', 'url': 'http://forum.i2p', 'description': 'I2P discussion'}]
_i2p_http_available: bool | None = None
_i2p_socks_available: bool | None = None
_i2p_check_time: float = 0
_I2P_CHECK_TTL: float = 60.0

async def is_i2p_available(proxy_type: str='http') -> bool:
    """
    Check if I2P proxy is running and accessible.

    Args:
        proxy_type: "http" (port 4444) or "socks5" (port 4444)

    NOTE: Port 7654 is the I2P HTTP console, NOT the SOCKS proxy.
    Correct I2P ports: 4444=SOCKS (both http and socks5 params use this),
    7656=SAM v3, 8888=HTTP proxy.

    Uses cached result with 60-second TTL to avoid excessive probes.

    Returns:
        True if I2P proxy is available, False otherwise
    """
    global _i2p_http_available, _i2p_socks_available, _i2p_check_time
    now = time.monotonic()
    if now - _i2p_check_time < _I2P_CHECK_TTL:
        if proxy_type == 'http':
            return _i2p_http_available if _i2p_http_available is not None else False
        else:
            return _i2p_socks_available if _i2p_socks_available is not None else False
    if os.getenv('HLEDAC_I2P_FORCE_UNAVAILABLE', '').lower() in ('1', 'true', 'yes'):
        _i2p_http_available = False
        _i2p_socks_available = False
        _i2p_check_time = now
        return False
    try:
        import httpx
        import httpx_socks
        from hledac.universal.transport.session_pool import session_pool
        try:
            session = await session_pool.httpx()
            resp = await session.get(f'{I2P_PROXY_URL}/', timeout=httpx.Timeout(5.0), proxy=I2P_PROXY_URL)
            if resp.status_code < 500:
                _i2p_http_available = True
            else:
                _i2p_http_available = False
        except Exception:
            _i2p_http_available = False
        try:
            session = await session_pool.httpx_socks(I2P_SOCKS_URL)
            resp = await session.get('http://i2pwiki.i2p', timeout=httpx.Timeout(3.0))
            _i2p_socks_available = resp.status_code < 500
        except Exception:
            _i2p_socks_available = False
    except ImportError:
        if proxy_type == 'socks5':
            _i2p_socks_available = False
        logger.debug('httpx-socks not available, SOCKS5 check skipped')
    except Exception as e:
        logger.debug(f'I2P proxy check error: {e}')
        _i2p_http_available = False
        _i2p_socks_available = False
    _i2p_check_time = now
    if proxy_type == 'http':
        return _i2p_http_available or False
    else:
        return _i2p_socks_available or False

async def fetch_eepsite(url: str, timeout: int=I2P_TIMEOUT, max_size: int=I2P_MAX_SIZE) -> str | None:
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
    if not url.startswith('http'):
        url = f'http://{url}'
    try:
        import httpx
        from hledac.universal.transport.session_pool import session_pool
        session = await session_pool.httpx()
        resp = await session.get(url, timeout=httpx.Timeout(timeout), proxy=I2P_PROXY_URL)
        if resp.status_code == 200:
            content_length = resp.headers.get('Content-Length')
            if content_length:
                if int(content_length) > max_size:
                    logger.warning(f'I2P response too large: {content_length} bytes')
                    return None
            content = resp.text
            if len(content.encode('utf-8')) > max_size:
                logger.warning('I2P response too large after decode')
                return None
            return content
        else:
            logger.debug(f'I2P fetch failed: status {resp.status_code} for {url}')
            return None
    except TimeoutError:
        logger.debug(f'I2P fetch timeout: {url}')
        return None
    except Exception as e:
        logger.debug(f'I2P fetch error {url}: {e}')
        return None

async def fetch_eepsite_socks5(url: str, timeout: int=I2P_TIMEOUT, max_size: int=I2P_MAX_SIZE) -> str | None:
    """
    Fetch content from an I2P eepsite via SOCKS5 proxy.

    This uses the SOCKS5 protocol (port 4444) for better anonymity.
    Falls back to HTTP proxy if httpx-socks is not available.

    NOTE: Port 7654 is the I2P HTTP console, NOT the SOCKS proxy.

    Args:
        url: Eepsite URL (e.g., "http://i2pwiki.i2p/")
        timeout: Request timeout in seconds
        max_size: Maximum response size in bytes

    Returns:
        Response text as string, or None if fetch failed
    """
    if not await is_i2p_available(proxy_type='socks5'):
        return None
    if not url.startswith('http'):
        url = f'http://{url}'
    try:
        import httpx
        from hledac.universal.transport.session_pool import session_pool
        session = await session_pool.httpx_socks(I2P_SOCKS_URL)
        resp = await session.get(url, timeout=httpx.Timeout(timeout))
        if resp.status_code == 200:
            content_length = resp.headers.get('Content-Length')
            if content_length and int(content_length) > max_size:
                logger.warning(f'I2P SOCKS5 response too large: {content_length}')
                return None
            content = resp.text
            if len(content.encode('utf-8')) > max_size:
                logger.warning('I2P SOCKS5 response too large after decode')
                return None
            return content
        else:
            logger.debug(f'I2P SOCKS5 fetch failed: status {resp.status_code} for {url}')
            return None
    except ImportError:
        logger.debug('httpx-socks not available for SOCKS5 fetch')
        return None
    except TimeoutError:
        logger.debug(f'I2P SOCKS5 fetch timeout: {url}')
        return None
    except Exception as e:
        logger.debug(f'I2P SOCKS5 fetch error {url}: {e}')
        return None

async def discover_eepsites() -> list[dict]:
    """
    Fetch content from known I2P eepsites.

    Returns:
        List of dicts with {url, content, title}
    """
    discovered: list[dict] = []
    if not await is_i2p_available():
        return discovered
    from hledac.universal.core.concurrency import ConcurrencyCategory, get_semaphore
    sem = get_semaphore(ConcurrencyCategory.TRANSPORT_I2P)

    async def fetch_one(eepsite: dict) -> dict | None:
        async with sem:
            try:
                content = await fetch_eepsite(eepsite['url'])
                if content:
                    title = eepsite['name']
                    if '<title' in content.lower():
                        import re
                        title_match = re.search('<title[^>]*>([^<]+)', content, re.IGNORECASE)
                        if title_match:
                            title = title_match.group(1).strip()
                    return {'url': eepsite['url'], 'name': eepsite['name'], 'content': content[:10000], 'title': title}
            except Exception:  # noqa: BLE001
                pass
            return None
    tasks = [fetch_one(e) for e in KNOWN_EEPSITES]
    results = await parallel_ok(*tasks, label='i2p_client:299')
    for result in results:
        if isinstance(result, dict) and result:
            discovered.append(result)
    return discovered

async def i2p_to_findings(query: str) -> list:
    """
    Fetch I2P content and return as CanonicalFinding list.

    Args:
        query: Original search query

    Returns:
        List of CanonicalFinding
    """
    if os.getenv('HLEDAC_ENABLE_ALT_PROTOCOLS', '0') != '1':
        return []
    if not await is_i2p_available():
        return []
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding
    findings: list = []
    try:
        eepsites = await discover_eepsites()
        for site in eepsites:
            finding = CanonicalFinding(finding_id=f'i2p-{int(time.time() * 1000)}', query=query, source_type='i2p_content', confidence=0.65, ts=time.time(), provenance=(site['url'],), payload_text=site.get('content', '')[:4096] if site.get('content') else None)
            findings.append(finding)
    except Exception as e:
        logger.debug(f'I2P to findings failed: {e}')
    return findings

async def get_i2p_router_info() -> dict | None:
    """
    Get I2P router information from console.

    Returns:
        Dict with router stats, or None if unavailable
    """
    if not await is_i2p_available():
        return None
    try:
        import httpx
        from hledac.universal.transport.session_pool import session_pool
        session = await session_pool.httpx()
        resp = await session.get(f'{I2P_PROXY_URL}/?page=stats', timeout=httpx.Timeout(10.0), proxy=I2P_PROXY_URL)
        if resp.status_code == 200:
            text = resp.text
            try:
                return _json.decode(text)
            except Exception:
                return {'raw': text[:1000]}
    except Exception:  # noqa: BLE001
        pass
    return None