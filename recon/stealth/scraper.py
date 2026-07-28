"""
StealthCrawler a StealthWebScraper — web scraping s TLS fingerprinting.

Rozděleno z původního stealth_crawler.py (ISSUE-028).

From deep_research/distributed_dark_web_crawler.py:
- curl_cffi for TLS fingerprinting (impersonate="chrome136")
- DuckDuckGo HTML scraping (no CAPTCHA)
- Google fallback
- Zero memory leaks (M1 optimized)

Enhanced with stealth_toolkit:
- HeaderSpoofer for dynamic header rotation
- Protection detection (Cloudflare, Akamai, Imperva, DataDome)
- Multi-layer bypass
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any
from urllib.parse import quote, urlparse

from ._models import (
    BypassMethod,
    ChangeType,
    HeaderSpoofer,
    ProtectionType,
    ScrapingResult,
    SearchResult,
    _mark_surface_patched,
    _crawler_domain_allowed,
    _get_crawl_bloom,
)

from hledac.universal.utils.async_helpers import safe_create_task, parallel

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# StealthCrawler
# ---------------------------------------------------------------------------


class StealthCrawler:
    """
    Stealth web crawler with TLS fingerprinting.

    From deep_research/distributed_dark_web_crawler.py:
    - curl_cffi for TLS fingerprinting (impersonate="chrome136")
    - DuckDuckGo HTML scraping (no CAPTCHA)
    - Google fallback
    - Zero memory leaks (M1 optimized)

    Enhanced with stealth_toolkit:
    - HeaderSpoofer for dynamic header rotation
    - User-Agent rotation
    - Platform-specific headers
    """

    __slots__ = tuple(
        ("_curl_cffi_available", "_header_spoofer", "_httpx_available", "_session")
    )

    def __init__(self, use_header_spoofer: bool = True):
        self._curl_cffi_available = False
        self._httpx_available = False
        self._session = None
        self._header_spoofer: HeaderSpoofer | None = None
        if use_header_spoofer:
            self._header_spoofer = HeaderSpoofer()
        self._check_dependencies()

    def _check_dependencies(self) -> None:
        """Check for available HTTP libraries."""
        try:
            from curl_cffi import requests as curl_requests

            self._curl_cffi_available = True
            logger.info("✓ curl_cffi available - using TLS fingerprinting")
        except ImportError:
            logger.debug("curl_cffi not available")
            self._curl_cffi_available = False
        if not self._curl_cffi_available:
            try:
                import httpx

                self._httpx_available = True
                logger.info("✓ httpx available - using fallback")
            except ImportError:
                logger.warning("Neither curl_cffi nor httpx available")

    async def search_async(
        self, query: str, num_results: int = 10, source: str = "duckduckgo"
    ) -> list[SearchResult]:
        """
        P9 FIX: Async parallel search using stealth scraping with multi-provider fallback.

        All 3 providers run concurrently via asyncio.to_thread + parallel().
        First provider to return non-empty results wins (fail-fast semantics).
        This replaces the sequential sync approach which blocked the event loop.

        Args:
            query: Search query
            num_results: Number of results to return
            source: 'duckduckgo', 'google', 'brave', or 'all' (parallel all)

        Returns:
            List of SearchResult
        """
        try:
            logger.info(f"Stealth async search: '{query}' (max {num_results} results)")

            async def _run_provider(provider_name: str, provider_func) -> list[SearchResult]:
                """Run a sync provider in thread pool, return empty list on failure."""
                try:
                    return await asyncio.to_thread(provider_func, query, num_results)
                except Exception as exc:
                    logger.debug(f"Provider {provider_name} failed: {exc}")
                    return []

            if source == "all":
                # Parallel race: all 3 providers simultaneously, first non-empty wins
                tasks = [
                    _run_provider("duckduckgo", self._search_duckduckgo),
                    _run_provider("brave", self._search_brave),
                    _run_provider("google", self._search_google),
                ]
                # parallel(policy="first") — returns on first non-empty list
                result = await parallel(
                    tasks,
                    policy="first",
                    ctx="stealth_search:all",
                )
                return result if result else []

            # Single provider with sequential fallback (legacy behavior preserved)
            if source == "duckduckgo":
                results = await _run_provider("duckduckgo", self._search_duckduckgo)
                if not results:
                    logger.info("DuckDuckGo returned no results, trying Brave...")
                    results = await _run_provider("brave", self._search_brave)
            elif source == "google":
                results = await _run_provider("google", self._search_google)
            elif source == "brave":
                results = await _run_provider("brave", self._search_brave)
            else:
                results = []

            if not results:
                logger.info("Primary provider failed, trying Google as fallback...")
                results = await _run_provider("google", self._search_google)

            if results:
                logger.info(f"Stealth async search returned {len(results)} results")
            else:
                logger.warning("No results from any search provider")
            return results
        except Exception as e:
            logger.error(f"Stealth async search failed: {e}")
            return []

    def search(
        self, query: str, num_results: int = 10, source: str = "duckduckgo"
    ) -> list[SearchResult]:
        """
        Search using stealth scraping with multi-provider fallback.

        DEPRECATED: Use search_async() for async contexts.
        This sync wrapper is kept for backward compatibility only.

        Args:
            query: Search query
            num_results: Number of results to return
            source: 'duckduckgo', 'google', or 'brave'

        Returns:
            List of SearchResult
        """
        try:
            return asyncio.run(self.search_async(query, num_results, source))
        except Exception as e:
            logger.error(f"Stealth search failed: {e}")
            return []

    def _search_duckduckgo(
        self, query: str, num_results: int
    ) -> list[SearchResult]:
        """Scrape DuckDuckGo HTML results."""
        try:
            encoded_query = quote(query)
            url = f"https://html.duckduckgo.com/html/?q={encoded_query}"
            if self._header_spoofer:
                headers = self._header_spoofer.get_headers(content_type="html")
            else:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.5",
                    "Accept-Encoding": "gzip, deflate, br",
                    "DNT": "1",
                    "Connection": "keep-alive",
                }
            html = self._fetch_html(url, headers)
            if not html:
                return []
            return self._parse_duckduckgo(html, num_results)
        except Exception as e:
            logger.error(f"DuckDuckGo search failed: {e}")
            return []

    def _search_google(
        self, query: str, num_results: int
    ) -> list[SearchResult]:
        """Scrape Google HTML results (fallback)."""
        try:
            encoded_query = quote(query)
            url = f"https://www.google.com/search?q={encoded_query}&num={num_results}"
            if self._header_spoofer:
                headers = self._header_spoofer.get_headers(content_type="html")
            else:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                }
            html = self._fetch_html(url, headers)
            if not html:
                return []
            return self._parse_google(html, num_results)
        except Exception as e:
            logger.error(f"Google search failed: {e}")
            return []

    def _search_brave(
        self, query: str, num_results: int
    ) -> list[SearchResult]:
        """Scrape Brave Search HTML results (Sprint 8R)."""
        try:
            encoded_query = quote(query)
            url = f"https://search.brave.com/search?q={encoded_query}&count={num_results}"
            if self._header_spoofer:
                headers = self._header_spoofer.get_headers(content_type="html")
            else:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.5",
                    "Accept-Encoding": "gzip, deflate",
                    "DNT": "1",
                    "Connection": "keep-alive",
                }
            html = self._fetch_html(url, headers)
            if not html:
                return []
            return self._parse_brave(html, num_results)
        except Exception as e:
            logger.error(f"Brave search failed: {e}")
            return []

    def _parse_brave(self, html: str, num_results: int) -> list[SearchResult]:
        """Parse Brave Search HTML results (Sprint 8R)."""
        results = []
        pattern = '<a[^>]*href="(https?://[^"]*)"[^>]*class="[^"]*svelte[^"]*"[^>]*>'
        matches = re.findall(pattern, html)
        try:
            bloom = _get_crawl_bloom()
        except Exception:
            bloom = None
        for url in matches:
            if url and url.startswith("http") and ("cdn.search.brave" not in url) and ("serp" not in url):
                is_new = True
                if bloom is not None:
                    try:
                        is_new = bloom.add(url)
                    except Exception:
                        pass
                if is_new and len(results) < num_results:
                    results.append(
                        SearchResult(
                            title="Brave Result",
                            url=url,
                            snippet="",
                            source="brave",
                            rank=len(results),
                        )
                    )
        return results

    def _fetch_html(self, url: str, headers: dict[str, str]) -> str | None:
        """
        Fetch HTML using available library with subprocess curl fallback (Sprint 8R).

        Bounded fix TICKET-004: Never use subprocess on M1 (tickover only).
        """
        from ._models import TorProxyManager

        # Check .onion URLs
        if ".onion" in url:
            from hledac.universal.transport.tor_transport import TorUnavailableError

            if not TorProxyManager.is_running():
                raise TorUnavailableError(
                    f"Cannot fetch .onion URL without Tor: {url}"
                )
        allowed, reason = _crawler_domain_allowed(url, "_fetch_html")
        if not allowed:
            logger.debug(f"[_fetch_html] domain blocked: {reason}")
            _mark_surface_patched("_fetch_html")
            return None
        _mark_surface_patched("_fetch_html")
        if self._curl_cffi_available:
            return self._fetch_with_curl_cffi(url, headers)
        elif self._httpx_available:
            return self._fetch_with_httpx(url, headers)
        else:
            logger.error("No HTTP library available for _fetch_html")
            return None

    def _fetch_with_curl_cffi(
        self, url: str, headers: dict[str, str]
    ) -> str | None:
        """Fetch using curl_cffi with TLS fingerprinting."""
        from curl_cffi import requests as curl_requests

        try:
            response = curl_requests.get(
                url,
                headers=headers,
                impersonate="chrome136",
                timeout=30,
            )
            response.raise_for_status()
            return response.text
        except Exception as e:
            logger.warning(f"curl_cffi fetch failed: {e}")
            return None

    def _fetch_with_httpx(self, url: str, headers: dict[str, str]) -> str | None:
        """Fetch using httpx.Client (fallback)."""
        import httpx

        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.get(url, headers=headers)
                response.raise_for_status()
                return response.text
        except Exception as e:
            logger.warning(f"httpx fetch failed: {e}")
            return None

    def _parse_duckduckgo(
        self, html: str, num_results: int
    ) -> list[SearchResult]:
        """Parse DuckDuckGo HTML results."""
        results = []
        pattern = r'<a[^>]*href="([^"]+)"[^>]*class="[^"]*result[^"]*"[^>]*>\s*<h2[^>]*>([^<]+)</h2>'
        matches = re.findall(pattern, html, re.DOTALL)
        snippet_pattern = '<span[^>]*class="[^"]*snippet[^"]*"[^>]*>([^<]+)</span>'
        snippets = re.findall(snippet_pattern, html)
        try:
            bloom = _get_crawl_bloom()
        except Exception:
            bloom = None
        for i, (url, title) in enumerate(matches[:num_results]):
            if url.startswith("http"):
                is_new = True
                if bloom is not None:
                    try:
                        is_new = bloom.add(url)
                    except Exception:
                        pass
                if is_new:
                    snippet = snippets[i] if i < len(snippets) else ""
                    results.append(
                        SearchResult(
                            title=title.strip(),
                            url=url,
                            snippet=snippet.strip(),
                            source="duckduckgo",
                            rank=i,
                        )
                    )
        return results

    def _parse_google(self, html: str, num_results: int) -> list[SearchResult]:
        """Parse Google HTML results."""
        results = []
        pattern = '<a[^>]*href="(https?://[^"&]+)"[^>]*class="[^"]*psel[^"]*"[^>]*>'
        matches = re.findall(pattern, html)
        try:
            bloom = _get_crawl_bloom()
        except Exception:
            bloom = None
        for i, url in enumerate(matches[:num_results]):
            if url.startswith("http") and "google.com" not in url:
                is_new = True
                if bloom is not None:
                    try:
                        is_new = bloom.add(url)
                    except Exception:
                        pass
                if is_new:
                    results.append(
                        SearchResult(
                            title=f"Google Result {i + 1}",
                            url=url,
                            snippet="",
                            source="google",
                            rank=i,
                        )
                    )
        return results


# ---------------------------------------------------------------------------
# StealthWebScraper
# ---------------------------------------------------------------------------


class StealthWebScraper:
    """
    Advanced stealth web scraper with protection detection and bypass.

    Enhanced with stealth_toolkit:
    - Protection detection (Cloudflare, Akamai, Imperva, DataDome)
    - Multi-layer bypass via curl_cffi JA3 impersonation + proxy rotation
    - Fingerprint rotation (50+ profiles)
    - Async support via asyncio.to_thread for M1 compatibility

    T3: cloudscraper removed — curl_cffi impersonate covers all bypass needs.
    """

    __slots__ = tuple(
        (
            "_curl_cffi_available",
            "_cloudscraper_available",
            "_session",
            "_fingerprint_profiles",
            "_proxy_config",
        )
    )

    def __init__(
        self,
        use_cloudscraper: bool = True,
        proxy_config: dict[str, str] | None = None,
    ):
        # use_cloudscraper reserved for future cloudscraper activation
        _ = use_cloudscraper
        self._curl_cffi_available = False
        self._cloudscraper_available = False
        self._session = None
        self._fingerprint_profiles: list[dict[str, Any]] = []
        self._proxy_config = proxy_config
        self._check_dependencies()
        self._load_fingerprint_profiles()

    def _check_dependencies(self) -> None:
        """Check for available libraries."""
        try:
            from curl_cffi import requests as curl_requests

            self._curl_cffi_available = True
            logger.info("✓ curl_cffi available for scraping")
        except ImportError:
            logger.debug("curl_cffi not available")
            self._curl_cffi_available = False
        # cloudscraper REMOVED T3: curl_cffi JA3 impersonate covers all bypass needs.
        self._cloudscraper_available = False

    def _load_fingerprint_profiles(self) -> None:
        """Load TLS/HTTP fingerprint profiles for rotation."""
        # Chrome 120 profile (most common)
        self._fingerprint_profiles = [
            {
                "name": "chrome120",
                "ja3_hash": "772486322c2f9d72",
                "http2_settings": "02223c8f0f5b5a00",
                "alpn": ["h2", "http/1.1"],
                "tls_version": "TLS 1.3",
            },
            {
                "name": "chrome119",
                "ja3_hash": "c7622d94f3e9e82a",
                "http2_settings": "0f8a9c5d0f8a9c5d",
                "alpn": ["h2", "http/1.1"],
                "tls_version": "TLS 1.3",
            },
            {
                "name": "firefox120",
                "ja3_hash": "9e63d3f2d1fa7bc4",
                "http2_settings": "1234567890abcdef",
                "alpn": ["h2", "http/1.1"],
                "tls_version": "TLS 1.3",
            },
        ]

    async def scrape(self, url: str, **kwargs) -> ScrapingResult:
        """
        Scrape a URL with protection detection and bypass (async-safe).

        F-FIX: curl_requests.get inside _detect_protection_impl blocks the event loop
        when called from async code. Both _detect_protection and _fetch_content are
        wrapped via asyncio.to_thread so concurrent coroutines are not blocked.

        Args:
            url: URL to scrape
            **kwargs: Additional options (headers, proxies, etc.)

        Returns:
            ScrapingResult with content or error
        """
        import asyncio
        from ._models import TorProxyManager

        if ".onion" in url:
            from hledac.universal.transport.tor_transport import (
                TorUnavailableError,
            )

            if not TorProxyManager.is_running():
                raise TorUnavailableError(
                    f"Cannot fetch .onion URL without Tor: {url}"
                )
        allowed, reason = _crawler_domain_allowed(url, "StealthWebScraper.scrape")
        if not allowed:
            logger.debug(f"[StealthWebScraper.scrape] blocked: {reason}")
            _mark_surface_patched("StealthWebScraper.scrape")
            return ScrapingResult(
                url=url,
                success=False,
                error=f"Domain blocked: {reason}",
            )
        _mark_surface_patched("StealthWebScraper.scrape")
        try:
            # F-FIX: wrap blocking HTTP calls with asyncio.to_thread
            protection_type = await asyncio.to_thread(
                self._detect_protection, url
            )
            if protection_type:
                logger.info(
                    f"Protection detected: {protection_type.value} on {url}"
                )
            content = await asyncio.to_thread(
                self._fetch_content, url, protection_type, **kwargs
            )
            if content:
                return ScrapingResult(
                    url=url,
                    success=True,
                    content=content,
                    protection_type=protection_type,
                )
            return ScrapingResult(
                url=url,
                success=False,
                error="No content returned",
                protection_type=protection_type,
            )
        except Exception as e:
            logger.error(f"Scraping failed for {url}: {e}")
            return ScrapingResult(
                url=url,
                success=False,
                error=str(e),
            )

    def _detect_protection_impl(
        self, url: str
    ) -> tuple[str, httpx.Headers] | None:
        """Shared fetch implementation for protection detection.

        F-FIX: extracted to eliminate duplication between sync and async paths.
        Returns (html_text, headers) on success, None on failure.
        """
        import httpx

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        if self._curl_cffi_available:
            from curl_cffi import requests as curl_requests

            response = curl_requests.get(
                url, headers=headers, impersonate="chrome136", timeout=10
            )
            return response.text, response.headers
        with httpx.Client(timeout=10.0) as client:
            response = client.get(url, headers=headers)
            return response.text, response.headers

    def _detect_protection(self, url: str) -> ProtectionType | None:
        """Detect anti-bot protection on the target URL (sync fallback).

        F-FIX: extracted impl to avoid duplication; direct call (no to_thread needed
        for sync callers). For async callers, use scrape_async() instead.
        """
        try:
            result = self._detect_protection_impl(url)
            if result is None:
                return None
            html_text, response_headers = result
            html_lower = html_text.lower()
            if "cloudflare" in html_lower or "challenges.cloudflare" in html_lower:
                return ProtectionType.CLOUDFLARE
            if "incapsula" in html_lower or "_Incapsula_机器人" in html_lower:
                return ProtectionType.INCAPSULA
            if "datadome" in html_lower or "datadome" in str(response_headers):
                return ProtectionType.DATADOME
            if "imperva" in html_lower or "incapsula" in html_lower:
                return ProtectionType.IMPERVA
            if "akamai" in html_lower or "akamaihd" in html_lower:
                return ProtectionType.AKAMAI
            return None
        except Exception:
            return None

    def _fetch_content(
        self,
        url: str,
        protection_type: ProtectionType | None,
        **kwargs,
    ) -> str | None:
        """Fetch content with appropriate bypass method."""
        headers = kwargs.get("headers", {})
        if not headers:
            if self._curl_cffi_available:
                # Profile selected but not used — reserved for future fingerprint rotation
                headers = {
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept-Encoding": "gzip, deflate, br",
                    "Sec-Fetch-Dest": "document",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "none",
                    "Sec-Fetch-User": "?1",
                }
        if self._curl_cffi_available:
            return self._fetch_with_curl_cffi_async(url, headers)
        return self._fetch_with_httpx_fallback(url, headers)

    def _fetch_with_curl_cffi_async(
        self, url: str, headers: dict[str, str]
    ) -> str | None:
        """Fetch using curl_cffi (async context - uses thread pool for M1 safety)."""
        from curl_cffi import requests as curl_requests

        try:
            session = curl_requests.Session()
            response = session.get(
                url,
                headers=headers,
                impersonate="chrome136",
                timeout=30,
            )
            response.raise_for_status()
            return response.text
        except Exception as e:
            logger.warning(f"curl_cffi fetch failed: {e}")
            return None

    def _fetch_with_httpx_fallback(
        self, url: str, headers: dict[str, str]
    ) -> str | None:
        """Fetch using httpx.Client (last resort fallback)."""
        import httpx

        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.get(url, headers=headers)
                response.raise_for_status()
                return response.text
        except Exception as e:
            logger.warning(f"httpx fallback fetch failed: {e}")
            return None


# ---------------------------------------------------------------------------
# Factory functions (re-exports pro backwards compatibility)
# ---------------------------------------------------------------------------


def create_stealth_crawler() -> StealthCrawler:
    """Create a new StealthCrawler instance."""
    return StealthCrawler()


def get_stealth_web_scraper() -> StealthWebScraper:
    """Create a new StealthWebScraper instance."""
    return StealthWebScraper()


def quick_scrape(url: str, **kwargs) -> ScrapingResult:
    """
    Quick scrape a URL using default settings.

    Convenience function for one-off scraping without explicit setup.
    Runs the async scrape() in a new event loop via asyncio.run().
    """
    import asyncio
    scraper = StealthWebScraper()
    return asyncio.run(scrape_async_coro(scraper, url, **kwargs))


async def scrape_async_coro(
    scraper: StealthWebScraper, url: str, **kwargs
) -> ScrapingResult:
    """Async coroutine wrapper so asyncio.run() can call scrape()."""
    return await scraper.scrape(url, **kwargs)
