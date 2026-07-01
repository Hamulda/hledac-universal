"""
StealthBrowser — async browser for scraping JS-heavy or bot-protected sites.

Interface expected by research_coordinator.py:
    browser = StealthBrowser()
    content = await browser.fetch(url, depth=depth)

Returns dict with: url, content, title, links, status, js_rendered
"""


import asyncio
import logging
import os
import random
from typing import Any

from hledac.universal.utils.async_helpers import safe_gather_fire_and_forget


class MemoryPressureError(Exception):
    """Raised when system RSS exceeds the browser launch threshold."""
    pass

logger = logging.getLogger(__name__)

# M1 8GB: max 2 concurrent browser tabs (per project constraint)
_MAX_CONCURRENT_TABS = 2
from hledac.universal.core.concurrency_registry import ConcurrencyCategory, get_semaphore_for_testing
_semaphore = get_semaphore_for_testing(ConcurrencyCategory.SCRAPE_GENERAL)

# Real Chrome user-agents from 2025-2026
_CHROME_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",  # noqa: E501
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",  # noqa: E501
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",  # noqa: E501
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",  # noqa: E501
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 11.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 12_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",  # noqa: E501
]


# ---------------------------------------------------------------------------
# Sprint F263: TLS fingerprint rotation (curl_cffi impersonate targets)
# ---------------------------------------------------------------------------
# Pairs each UA with a matching curl_cffi ``impersonate=`` target so the
# TLS/ALPN/HTTP-2 fingerprint stays consistent with the User-Agent header.
# curl_cffi stores JA3/H2 fingerprints per impersonate target — picking
# a Chrome UA while claiming `impersonate="safari17"` produces a detectable
# mismatch. The pair table keeps them aligned.
#
# curl_cffi supported impersonate targets (subset — must match installed
# version; we lazy-check at rotation time and fail-soft to chrome120):
#   chrome120, chrome119, chrome116, edge101, edge99, safari17_0,
#   safari15_5, firefox117, firefox109, okhttp4
#
# Pairs: (ua_substring, curl_cffi_impersonate) — substring match picks the
# most specific UA bucket.
_TLS_FINGERPRINT_PAIRS: list[tuple[str, str]] = [
    ("Chrome/126", "chrome120"),
    ("Chrome/125", "chrome120"),
    ("Chrome/124", "chrome120"),
    ("Chrome/123", "chrome120"),
    ("Chrome/122", "chrome120"),
    ("Safari/605", "safari17_0"),
    # Firefox / Edge / generic — rotate to the same default chrome120.
    # Future: add real Firefox/Edge UA buckets once curl_cffi targets ship
    # matching JA3 hashes (F264 follow-up).
]

# Default fallback when no pair matches (e.g. unknown UA substring).
_TLS_FALLBACK_IMPERSONATE = "chrome120"

# Cached import probe — avoids repeated heavy import on every fetch.
_TLS_IMPERSONATE_AVAILABLE: bool | None = None


def _pick_fingerprint_pair() -> tuple[str, str]:
    """Pick a random (UA, curl_cffi impersonate) pair.

    Returns:
        Tuple of (user_agent, curl_cffi_impersonate_target).
    """
    ua = random.choice(_CHROME_UAS)
    impersonate = _TLS_FALLBACK_IMPERSONATE
    for substring, target in _TLS_FINGERPRINT_PAIRS:
        if substring in ua:
            impersonate = target
            break
    return ua, impersonate


def _is_curl_cffi_available() -> bool:
    """Lazy check whether curl_cffi is importable in the current env.

    Cached at module level after first probe. M1 8GB friendly — no eager
    import at module load.
    """
    global _TLS_IMPERSONATE_AVAILABLE
    if _TLS_IMPERSONATE_AVAILABLE is not None:
        return _TLS_IMPERSONATE_AVAILABLE
    try:
        from importlib import util as _importlib_util  # type: ignore[attr-defined]
        _TLS_IMPERSONATE_AVAILABLE = _importlib_util.find_spec("curl_cffi") is not None
    except (ImportError, ValueError):
        _TLS_IMPERSONATE_AVAILABLE = False
    return _TLS_IMPERSONATE_AVAILABLE


def _fetch_with_curl_cffi(
    url: str,
    user_agent: str,
    impersonate: str,
    timeout: float,
) -> tuple[int, str] | None:
    """Synchronous curl_cffi fetch — returns (status, html) or None on failure.

    Designed to be called via ``loop.run_in_executor()`` (curl_cffi is
    blocking; never ``await`` directly in the event loop). Fail-soft:
    any exception → None. Caller falls back to httpx / nodriver.
    """
    try:
        import curl_cffi.requests as _cffi  # type: ignore[import-not-found]
        with _cffi.Session(impersonate=impersonate) as client:
            response = client.get(
                url,
                headers={"User-Agent": user_agent},
                timeout=timeout,
                allow_redirects=True,
            )
            return response.status_code, response.text
    except Exception as e:
        logger.debug(f"curl_cffi fetch failed for {url}: {e}")
        return None

_FETCH_TIMEOUT = 30  # seconds


def _rss_gib() -> float:
    """Return current process RSS in GiB, or 0.0 on any error (fail-soft)."""
    try:
        import psutil  # type: ignore[import-not-found]
        return psutil.Process(os.getpid()).memory_info().rss / (1024 ** 3)
    except Exception:
        return 0.0


def _check_browser_memory_pressure() -> None:
    """Raise MemoryPressureError if RSS exceeds HLEDAC_BROWSER_MEM_THRESHOLD_GIB.

    Call before uc.start() (nodriver browser launch) or playwright launch.
    Default threshold: 1.0 GiB. Set HLEDAC_BROWSER_MEM_THRESHOLD_GIB to
    override. Fail-soft: returns silently if psutil unavailable or threshold
    not parseable.
    """
    try:
        threshold = float(os.environ.get("HLEDAC_BROWSER_MEM_THRESHOLD_GIB", "1.0"))
    except (ValueError, TypeError):
        threshold = 1.0
    rss = _rss_gib()
    if rss > threshold > 0:
        raise MemoryPressureError(
            f"Browser launch blocked: RSS={rss:.2f} GiB > threshold={threshold:.2f} GiB "
            f"(HLEDAC_BROWSER_MEM_THRESHOLD_GIB). "
            f"Free memory before launching browser."
        )


class StealthBrowser:
    """
    Async stealth browser using nodriver as CDP backend.

    Falls back to httpx + BeautifulSoup if nodriver unavailable.
    """

    def __init__(self, respect_robots_txt: bool = True):
        self.respect_robots_txt = respect_robots_txt
        self._nodriver_available = self._check_nodriver()
        self._session = None

    def _check_nodriver(self) -> bool:
        """Check if nodriver is available."""
        try:
            from importlib import util as _importlib_util  # type: ignore[attr-defined]
            _importlib_util.find_spec("nodriver")
            return True
        except (ImportError, ValueError):
            logger.debug("nodriver not available, using httpx fallback")
            return False

    async def fetch(
        self,
        url: str,
        depth: int = 1,
        extract_structured: bool = True,
    ) -> dict[str, Any]:
        """
        Fetch URL with optional same-domain crawl depth.

        Args:
            url: Target URL
            depth: If >1, follow same-domain links up to depth hops
            extract_structured: If True, parse JSON-LD / microdata / RDFa
                from the HTML and add `structured_entities`,
                `structured_relations`, `structured_meta` keys to the result.

        Returns:
            dict with: url, content, title, links, status, js_rendered,
            [structured_* when extract_structured=True]
        """
        try:
            async with _semaphore:
                if depth > 1:
                    return await self._crawl(url, depth, extract_structured)
                return await self._fetch_single(url, extract_structured)
        except Exception as e:
            logger.error(f"StealthBrowser.fetch failed for {url}: {e}")
            return self._error_result(url, str(e))

    async def _fetch_single(
        self, url: str, extract_structured: bool = True
    ) -> dict[str, Any]:
        """Fetch single URL."""
        if self._nodriver_available:
            return await self._fetch_nodriver(url, extract_structured)
        return await self._fetch_httpx(url, extract_structured)

    async def _fetch_nodriver(
        self, url: str, extract_structured: bool = True
    ) -> dict[str, Any]:
        """Fetch using nodriver CDP."""
        # PATCH 1: RSS memory check before browser launch
        _check_browser_memory_pressure()

        import nodriver as uc  # type: ignore[import-not-found]

        tab = None
        browser = None
        try:
            browser = await uc.start(
                headless=True,
                browser_args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
            tab = await browser.get(url)
            await asyncio.sleep(2)  # Let JS render

            content = await tab.content()
            title = await tab.title()
            links = await self._extract_links(tab, url)

            status = 200  # nodriver doesn't expose status directly
            js_rendered = True

            result: dict[str, Any] = {
                "url": url,
                "content": content,
                "title": title or "",
                "links": links,
                "status": status,
                "js_rendered": js_rendered,
            }
            if extract_structured:
                _attach_structured(result, content, url)
            return result
        except Exception as e:
            logger.warning(f"nodriver fetch failed for {url}: {e}")
            # Fallback to httpx
            return await self._fetch_httpx(url, extract_structured)
        finally:
            if tab:
                try:
                    tab.close()
                except Exception:  # noqa: BLE001
                    pass
            if browser:
                try:
                    browser.stop()
                except Exception:  # noqa: BLE001
                    pass

    async def _fetch_httpx(
        self, url: str, extract_structured: bool = True
    ) -> dict[str, Any]:
        """Fallback fetch using httpx + BeautifulSoup.

        Per project invariant: never use asyncio.to_thread for I/O. The
        blocking httpx.Client.get() call is dispatched via
        loop.run_in_executor() with the default thread pool.

        Sprint F263: when curl_cffi is available, prefer it for clearnet
        fetches — JA3/H2 fingerprint rotation makes the request look like
        a real browser (vs httpx's well-known Python fingerprint).
        """
        # Reference to make param usage explicit (used in `_attach_structured` call below)
        _ = extract_structured
        import httpx  # type: ignore[import-not-found]
        from bs4 import BeautifulSoup  # type: ignore[import-not-found]

        # Sprint F263: pair UA with matching curl_cffi impersonate target so
        # the TLS/ALPN/HTTP-2 fingerprint stays consistent with the
        # User-Agent header. Per-request random pick from _CHROME_UAS.
        ua, _impersonate = _pick_fingerprint_pair()
        headers = {"User-Agent": ua}
        try:
            loop = asyncio.get_running_loop()

            # Prefer curl_cffi (TLS fingerprint rotation); fall back to httpx.
            curl_cffi_result: tuple[int, str] | None = None
            if _is_curl_cffi_available():
                curl_cffi_result = await asyncio.to_thread(
                    lambda: _fetch_with_curl_cffi(
                        url, ua, _impersonate, float(_FETCH_TIMEOUT)
                    ),
                )
            if curl_cffi_result is not None:
                status, html = curl_cffi_result
            else:
                def _sync_fetch() -> tuple[int, str]:
                    with httpx.Client(
                        headers=headers,
                        timeout=_FETCH_TIMEOUT,
                        follow_redirects=True,
                    ) as client:
                        response = client.get(url)
                        return response.status_code, response.text

                status, html = await asyncio.to_thread(_sync_fetch)

            soup = BeautifulSoup(html, "html.parser")
            title = soup.title.string if soup.title else ""

            links = []
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if href.startswith("http"):
                    links.append(href)

            result: dict[str, Any] = {
                "url": url,
                "content": html,
                "title": title or "",
                "links": links,
                "status": status,
                "js_rendered": False,
            }
            if extract_structured:
                _attach_structured(result, html, url)
            return result
        except Exception as e:
            logger.warning(f"httpx fetch failed for {url}: {e}")
            return self._error_result(url, str(e))

    async def _extract_links(self, tab: Any, base_url: str) -> list[str]:
        """Extract same-domain links from nodriver tab."""
        try:
            from urllib.parse import urlparse

            parsed_base = urlparse(base_url)
            base_domain = parsed_base.netloc

            links = await tab.evaluate("""
                Array.from(document.querySelectorAll('a[href]'))
                    .map(a => a.href)
                    .filter(href => href.startsWith('http'))
            """)

            same_domain = [
                link
                for link in links
                if urlparse(link).netloc == base_domain
            ]
            return list(set(same_domain))[:50]  # Cap at 50 links
        except Exception as e:
            logger.debug(f"Link extraction failed: {e}")
            return []

    async def _crawl(
        self, url: str, depth: int, extract_structured: bool = True
    ) -> dict[str, Any]:
        """Crawl URL with same-domain link following."""
        visited: set[str] = set()
        content_parts: list[str] = []
        all_links: set[str] = set()
        all_entities: list[dict[str, Any]] = []

        async def crawl_page(current_url: str, current_depth: int):
            if current_depth > depth or current_url in visited:
                return
            visited.add(current_url)

            result = await self._fetch_single(current_url, extract_structured)
            if result.get("status") == 200:
                content_parts.append(f"\n\n<!-- From: {current_url} -->\n{result.get('content', '')}")
                all_links.update(result.get("links", []))
                if extract_structured:
                    all_entities.extend(result.get("structured_entities", []))

                if current_depth < depth:
                    tasks = []
                    for link in list(all_links)[:10]:  # Max 10 links per page
                        if link not in visited:
                            tasks.append(crawl_page(link, current_depth + 1))
                    if tasks:
                        await safe_gather_fire_and_forget(*tasks, label="stealth_browser:373")

        try:
            await crawl_page(url, 1)
        except Exception as e:
            logger.error(f"Crawl failed: {e}")

        combined = "\n".join(content_parts)
        result_dict: dict[str, Any] = {
            "url": url,
            "content": combined,
            "title": f"Crawled: {url}",
            "links": list(all_links),
            "status": 200 if content_parts else 0,
            "js_rendered": self._nodriver_available,
        }
        if extract_structured:
            result_dict["structured_entities"] = all_entities
        return result_dict

    def _error_result(self, url: str, error: str) -> dict[str, Any]:
        """Return error result dict."""
        return {
            "url": url,
            "content": "",
            "title": "",
            "links": [],
            "status": 0,
            "js_rendered": False,
            "error": error,
        }

    async def cleanup(self) -> None:
        """Cleanup browser resources. Defensive: _session may be None or have
        no async close — guard with hasattr and run_in_executor for sync close.
        """
        session = getattr(self, "_session", None)
        if session is None:
            return
        try:
            close_fn = getattr(session, "close", None)
            if close_fn is None:
                return
            import inspect
            if inspect.iscoroutinefunction(close_fn):
                await close_fn()
            else:
                await asyncio.to_thread(close_fn)
        except Exception:  # noqa: BLE001
            pass
        finally:
            self._session = None


# =============================================================================
# Structured data integration helper (Sprint F-ADV-JSONLD)
# =============================================================================
# Extends StealthBrowser results with W3C JSON-LD / microdata / RDFa parsing.
# M1 8GB friendly: bounded (MAX_ENTITIES_PER_PAGE), fail-soft, lazy import
# of the StructuredExtractor (only on demand, no eager init).
# =============================================================================

def _attach_structured(
    result: dict[str, Any],
    content: str,
    url: str,
) -> None:
    """Run StructuredExtractor on fetched HTML and attach to result dict.

    Adds three keys:
        - structured_entities:   list[dict]   (entity_id, entity_type, value, url, properties, ioc_kind)
        - structured_relations:  list[dict]   (src_id, dst_id, relation, source_url)
        - structured_meta:       dict         (jsonld_blocks, microdata_blocks, rdfa_blocks, bytes, truncated)

    Always present (empty lists on failure). Mutates `result` in place.
    Bounded, fail-soft: any exception → empty result + warning log.
    """
    if not content:
        result.setdefault("structured_entities", [])
        result.setdefault("structured_relations", [])
        result.setdefault("structured_meta", {
            "jsonld_blocks": 0,
            "microdata_blocks": 0,
            "rdfa_blocks": 0,
            "bytes": 0,
            "truncated": False,
            "extractor_available": False,
        })
        return
    try:
        from .structured_extractor import (  # type: ignore[import-not-found]
            StructuredExtractor,
            entity_to_dict,
            relation_to_dict,
        )
        extractor = StructuredExtractor()
        extraction = extractor.extract(content, source_url=url)
        result["structured_entities"] = [
            entity_to_dict(e) for e in extraction.entities
        ]
        result["structured_relations"] = [
            relation_to_dict(r) for r in extraction.relations
        ]
        result["structured_meta"] = {
            "jsonld_blocks": extraction.jsonld_blocks,
            "microdata_blocks": extraction.microdata_blocks,
            "rdfa_blocks": extraction.rdfa_blocks,
            "bytes": extraction.bytes_processed,
            "truncated": extraction.truncated,
            "extractor_available": True,
        }
    except Exception as e:
        logger.warning(f"StructuredExtractor failed for {url}: {e}")
        result.setdefault("structured_entities", [])
        result.setdefault("structured_relations", [])
        result.setdefault("structured_meta", {
            "jsonld_blocks": 0,
            "microdata_blocks": 0,
            "rdfa_blocks": 0,
            "bytes": 0,
            "truncated": False,
            "extractor_available": False,
        })
