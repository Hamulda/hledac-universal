"""
Paywall bypass – detekce a fallback na archive.is / 12ft.io.
Sprint 46: Access to Unreachable Data (Sessions + Paywall + OSINT + Darknet)
Sprint 49: ClientSession pool for connection reuse


Performance: Patterns pre-compiled at module level (Sprint 79a optimization).
"""

from __future__ import annotations

import asyncio
import logging
import re
import httpx
from _core import aclose

__all__ = ['PaywallBypass']

# ---------------------------------------------------------------------------
# Pre-compiled regex patterns (module level for M1 8GB optimization)
# Compiled once at import time, reused across all PaywallBypass instances.
# ---------------------------------------------------------------------------
_COMPILED_PATTERNS: dict[str, re.Pattern[str]] = {
    'nytimes': re.compile(r'<div[^>]+class=["\']gateway["\']|subscribe\s+to\s+continue', re.I),
    'wsj': re.compile(r'<section[^>]+class=["\']wsj-paywall["\']|wsj.*subscriber\s+exclusive', re.I),
    'medium': re.compile(r'member-only story|medium\.com.*signin', re.I),
    'ft': re.compile(r'ft\.com.*paywall|financial-times.*subscription', re.I),
    'economist': re.compile(r'economist\.com.*premium|subscribers?\s+only', re.I),
    'bloomberg': re.compile(r'bloomberg\.com.*paywall|subscription\s+required', re.I),
}


class PaywallBypass:
    """Detects paywalls and bypasses via archive services."""
    __slots__ = ('_lock', '_session')

    def __init__(self):
        # Reference pre-compiled module-level patterns (no re-compilation per instance)
        self._session: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

    async def _get_session(self) -> httpx.AsyncClient:
        """S49-D: Get or create reusable session."""
        async with self._lock:
            if self._session is None or self._session.is_closed:
                self._session = httpx.AsyncClient(
                    limits=httpx.Limits(max_connections=25, max_keepalive_connections=10),
                    http2=True,
    )
            return self._session

    def detect(self, html: str) -> str | None:
        """Vrátí název paywallu nebo None."""
        if not html:
            return None
        for name, pattern in _COMPILED_PATTERNS.items():
            if pattern.search(html):
                return name
        return None

    async def fetch_via_archive(self, url: str) -> str | None:
        """Zkusí načíst z archive.is."""
        archive_url = f'https://archive.is/latest/{url}'
        try:
            session = await self._get_session()
            async with session.get(archive_url, timeout=httpx.Timeout(15)) as response:
                if response.status_code == 200:
                    return await response.text()
        except Exception as e:
            logger.warning(f'[PAYWALL] archive.is failed: {e}')
        return None

    async def fetch_via_12ft(self, url: str) -> str | None:
        """Zkusí načíst přes 12ft.io."""
        proxy_url = f'https://12ft.io/proxy?q={url}'
        try:
            session = await self._get_session()
            async with session.get(proxy_url, timeout=httpx.Timeout(15)) as response:
                if response.status_code == 200:
                    return await response.text()
        except Exception as e:
            logger.warning(f'[PAYWALL] 12ft.io failed: {e}')
        return None

    async def close(self) -> None:
        """S49-D: Cleanup session on shutdown."""
        if self._session and (not self._session.is_closed):
            await self._session.aclose()

    async def bypass(self, url: str, html: str) -> dict[str, str] | None:
        """
        Attempt to bypass paywall using available methods.
        Tries 12ft.io first (faster), then archive.is as fallback.
        Returns dict with content and bypass method, or None.
        """
        detected = self.detect(html)
        if not detected and len(html) > 5000:
            return None
        # F-S79a: 12ft.io first (lower latency), archive.is as fallback
        content = await self.fetch_via_12ft(url)
        if content:
            return {'content': content, 'bypassed': '12ft.io', 'paywall': detected}
        content = await self.fetch_via_archive(url)
        if content:
            return {'content': content, 'bypassed': 'archive.is', 'paywall': detected}
        return None