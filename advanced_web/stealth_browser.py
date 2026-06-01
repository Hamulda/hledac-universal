"""
StealthBrowser — async browser for scraping JS-heavy or bot-protected sites.

Interface expected by research_coordinator.py:
    browser = StealthBrowser()
    content = await browser.fetch(url, depth=depth)

Returns dict with: url, content, title, links, status, js_rendered
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

logger = logging.getLogger(__name__)

# M1 8GB: max 3 concurrent browser tabs
_MAX_CONCURRENT_TABS = 3
_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_TABS)

# Real Chrome user-agents from 2025-2026
_CHROME_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 11.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 12_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]

_FETCH_TIMEOUT = 30  # seconds


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
            import nodriver  # noqa: F401
            return True
        except ImportError:
            logger.debug("nodriver not available, using httpx fallback")
            return False

    async def fetch(self, url: str, depth: int = 1) -> dict[str, Any]:
        """
        Fetch URL with optional same-domain crawl depth.

        Args:
            url: Target URL
            depth: If >1, follow same-domain links up to depth hops

        Returns:
            dict with: url, content, title, links, status, js_rendered
        """
        try:
            async with _semaphore:
                if depth > 1:
                    return await self._crawl(url, depth)
                return await self._fetch_single(url)
        except Exception as e:
            logger.error(f"StealthBrowser.fetch failed for {url}: {e}")
            return self._error_result(url, str(e))

    async def _fetch_single(self, url: str) -> dict[str, Any]:
        """Fetch single URL."""
        if self._nodriver_available:
            return await self._fetch_nodriver(url)
        return await self._fetch_httpx(url)

    async def _fetch_nodriver(self, url: str) -> dict[str, Any]:
        """Fetch using nodriver CDP."""
        import nodriver as uc

        tab = None
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

            return {
                "url": url,
                "content": content,
                "title": title or "",
                "links": links,
                "status": status,
                "js_rendered": js_rendered,
            }
        except Exception as e:
            logger.warning(f"nodriver fetch failed for {url}: {e}")
            # Fallback to httpx
            return await self._fetch_httpx(url)
        finally:
            if tab:
                try:
                    tab.close()
                except Exception:
                    pass
            if browser:
                try:
                    browser.stop()
                except Exception:
                    pass

    async def _fetch_httpx(self, url: str) -> dict[str, Any]:
        """Fallback fetch using httpx + BeautifulSoup."""
        import httpx
        from bs4 import BeautifulSoup

        headers = {"User-Agent": random.choice(_CHROME_UAS)}
        try:
            with httpx.Client(
                headers=headers,
                timeout=_FETCH_TIMEOUT,
                follow_redirects=True,
            ) as client:
                response = client.get(url)
                status = response.status_code
                html = response.text

            soup = BeautifulSoup(html, "html.parser")
            title = soup.title.string if soup.title else ""

            links = []
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if href.startswith("http"):
                    links.append(href)

            return {
                "url": url,
                "content": html,
                "title": title or "",
                "links": links,
                "status": status,
                "js_rendered": False,
            }
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

    async def _crawl(self, url: str, depth: int) -> dict[str, Any]:
        """Crawl URL with same-domain link following."""
        visited: set[str] = set()
        content_parts: list[str] = []
        all_links: set[str] = set()

        async def crawl_page(current_url: str, current_depth: int):
            if current_depth > depth or current_url in visited:
                return
            visited.add(current_url)

            result = await self._fetch_single(current_url)
            if result.get("status") == 200:
                content_parts.append(f"\n\n<!-- From: {current_url} -->\n{result.get('content', '')}")
                all_links.update(result.get("links", []))

                if current_depth < depth:
                    tasks = []
                    for link in list(all_links)[:10]:  # Max 10 links per page
                        if link not in visited:
                            tasks.append(crawl_page(link, current_depth + 1))
                    if tasks:
                        await asyncio.gather(*tasks, return_exceptions=True)

        try:
            await crawl_page(url, 1)
        except Exception as e:
            logger.error(f"Crawl failed: {e}")

        combined = "\n".join(content_parts)
        return {
            "url": url,
            "content": combined,
            "title": f"Crawled: {url}",
            "links": list(all_links),
            "status": 200 if content_parts else 0,
            "js_rendered": self._nodriver_available,
        }

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
        """Cleanup browser resources."""
        if self._session:
            try:
                await self._session.close()
            except Exception:
                pass
            self._session = None
