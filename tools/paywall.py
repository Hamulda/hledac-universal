"""
Paywall bypass – detekce a fallback na archive.is / 12ft.io.
Sprint 46: Access to Unreachable Data (Sessions + Paywall + OSINT + Darknet)
Sprint 49: ClientSession pool for connection reuse
"""

import asyncio
import logging
import re

import aiohttp

logger = logging.getLogger(__name__)


class PaywallBypass:
    """Detects paywalls and bypasses via archive services."""

    def __init__(self):
        self.patterns = {
            'nytimes': re.compile(r'<div[^>]+class=["\']gateway["\']|subscribe\s+to\s+continue', re.I),
            'wsj': re.compile(r'<section[^>]+class=["\']wsj-paywall["\']|wsj.*subscriber\s+exclusive', re.I),
            'medium': re.compile(r'member-only story|medium\.com.*signin', re.I),
            'ft': re.compile(r'ft\.com.*paywall|financial-times.*subscription', re.I),
            'economist': re.compile(r'economist\.com.*premium|subscribers?\s+only', re.I),
            'bloomberg': re.compile(r'bloomberg\.com.*paywall|subscription\s+required', re.I),
        }
        # S49-D: ClientSession pool for connection reuse
        self._session: aiohttp.ClientSession | None = None
        self._lock = asyncio.Lock()

    async def _get_session(self) -> aiohttp.ClientSession:
        """S49-D: Get or create reusable session."""
        async with self._lock:
            if self._session is None or self._session.closed:
                connector = aiohttp.TCPConnector(limit=10, limit_per_host=3)
                self._session = aiohttp.ClientSession(connector=connector)
            return self._session

    def detect(self, html: str) -> str | None:
        """Vrátí název paywallu nebo None."""
        if not html:
            return None
        for name, pattern in self.patterns.items():
            if pattern.search(html):
                return name
        return None

    async def fetch_via_archive(self, url: str) -> str | None:
        """Zkusí načíst z archive.is."""
        archive_url = f"https://archive.is/latest/{url}"
        try:
            # S49-D: Use shared session for connection reuse
            session = await self._get_session()
            async with session.get(archive_url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    return await resp.text()
        except Exception as e:
            logger.warning(f"[PAYWALL] archive.is failed: {e}")
        return None

    async def fetch_via_12ft(self, url: str) -> str | None:
        """Zkusí načíst přes 12ft.io."""
        proxy_url = f"https://12ft.io/proxy?q={url}"
        try:
            # S49-D: Use shared session for connection reuse
            session = await self._get_session()
            async with session.get(proxy_url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    return await resp.text()
        except Exception as e:
            logger.warning(f"[PAYWALL] 12ft.io failed: {e}")
        return None

    async def close(self) -> None:
        """S49-D: Cleanup session on shutdown."""
        if self._session and not self._session.closed:
            await self._session.close()

    async def bypass(self, url: str, html: str) -> dict[str, str] | None:
        """
        Attempt to bypass paywall using available methods.
        Returns dict with content and bypass method, or None.
        """
        # Check if paywall detected
        detected = self.detect(html)
        if not detected and len(html) > 5000:
            # No paywall detected and content is substantial
            return None

        # Try archive.is first
        content = await self.fetch_via_archive(url)
        if content:
            return {'content': content, 'bypassed': 'archive.is', 'paywall': detected}

        # Try 12ft.io
        content = await self.fetch_via_12ft(url)
        if content:
            return {'content': content, 'bypassed': '12ft.io', 'paywall': detected}

        return None
