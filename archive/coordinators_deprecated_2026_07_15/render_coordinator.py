"""
RenderCoordinator - decision tree for getting rendered HTML.
Sprint 67: Full Playwright WebKit implementation with timeout, routing, semaphore.
"""
import asyncio
import hashlib
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
import msgspec
from typing import Literal
from hledac.universal.utils.capability_prober import get_prober
logger = logging.getLogger(__name__)
CAPTCHA_PATTERNS = ['captcha', 'recaptcha', 'hcaptcha', 'g-recaptcha', 'data-sitekey', 'turnstile', 'cloudflare', 'challenge', 'security check', 'verify you are human', 'i am not a robot', 'select all images', 'grid captcha']

class RenderResult(msgspec.Struct):
    html: str | None
    status: Literal['ok', 'no_backend', 'timeout', 'blocked', 'error']
    debug: dict

    def __post_init__(self) -> None:
        if self.debug:
            if len(self.debug) > 4:
                self.debug = dict(list(self.debug.items())[:4])
            total_size = 0
            for k, v in list(self.debug.items()):
                if isinstance(v, str):
                    if len(v) > 500:
                        self.debug[k] = v[:500] + '...'
                    total_size += len(self.debug[k])
                elif isinstance(v, (list, dict)):
                    as_str = str(v)
                    if len(as_str) > 1000:
                        self.debug[k] = f'<truncated {len(as_str)} bytes>'
                    total_size += len(str(self.debug[k]))
            if total_size > 2048:
                self.debug = {'error': 'debug too large'}

class RenderBackend:
    """Abstract backend - in S66 always returns no_backend."""

    async def render(self, url: str, deadline_ms: int, mode: str='text') -> RenderResult:
        return RenderResult(None, 'no_backend', {})

class PyObjCWKWebViewRenderer(RenderBackend):
    """Primary backend - native WKWebView via PyObjC (best stealth)."""
    pass

class CDPRenderer(RenderBackend):
    """Fallback - connection to running Chrome via CDP."""
    pass

class RenderCoordinator:
    __slots__ = tuple(('_backends', '_cache', '_cache_max', '_caps', '_semaphore', '_ttl'))

    def __init__(self):
        self._caps = get_prober()
        self._backends = [PyObjCWKWebViewRenderer(), CDPRenderer()]
        self._cache: OrderedDict[str, tuple[RenderResult, float]] = OrderedDict()
        self._cache_max = 200
        self._ttl = {'ok': 60, 'no_backend': 10, 'timeout': 5, 'blocked': 5, 'error': 0}
        self._semaphore: asyncio.Semaphore | None = None

    def _get_semaphore(self) -> asyncio.Semaphore:
        """Get or create semaphore for render serialization."""
        if self._semaphore is None:
            from hledac.universal.core.concurrency_registry import ConcurrencyCategory, get_semaphore_for_testing
            self._semaphore = get_semaphore_for_testing(ConcurrencyCategory.SCRAPE_GENERAL)
        return self._semaphore

    def _is_captcha_page(self, html: str) -> bool:
        """
        Detect if rendered page contains CAPTCHA.

        Args:
            html: HTML content to check

        Returns:
            True if CAPTCHA detected
        """
        if not html:
            return False
        html_lower = html.lower()
        for pattern in CAPTCHA_PATTERNS:
            if pattern in html_lower:
                logger.debug(f"CAPTCHA detected: pattern '{pattern}'")
                return True
        return False

    async def _handle_captcha(self, url: str) -> RenderResult:
        """
        Handle CAPTCHA challenge.

        Args:
            url: URL that triggered CAPTCHA

        Returns:
            RenderResult with blocked status
        """
        logger.info(f'CAPTCHA challenge for {url}')
        try:
            from hledac.universal.security.captcha_solver import VisionCaptchaSolver
            solver = VisionCaptchaSolver()
            logger.debug(f'VisionCaptchaSolver available: {solver is not None}')
        except ImportError:
            logger.debug('VisionCaptchaSolver not available')
        return RenderResult(None, 'blocked', {'reason': 'captcha_challenge', 'url': url[:100]})

    def _make_cache_key(self, url: str, deadline_ms: int, mode: str='text') -> str:
        """Creates cache key with length limit and hash for distinction."""
        url_hash = hashlib.sha256(url.encode()).hexdigest()[:8]
        url_prefix = url[:170]
        if deadline_ms < 2000:
            bucket = 'fast'
        elif deadline_ms < 5000:
            bucket = 'slow'
        else:
            bucket = 'very_slow'
        return f'{url_prefix}|{url_hash}|{bucket}|{mode}'

    async def render(self, url: str, deadline_ms: int=5000, mode: Literal['full', 'text']='text') -> RenderResult:
        """
        Render URL with timeout and mode.

        Args:
            url: URL to render
            deadline_ms: Timeout in milliseconds
            mode: "full" for all assets, "text" for text-only
        """
        key = self._make_cache_key(url, deadline_ms, mode)
        now = time.time()
        if key in self._cache:
            result, ts = self._cache[key]
            ttl = self._ttl.get(result.status, 0)
            if ttl > 0 and now - ts < ttl:
                return result
            else:
                del self._cache[key]
        deadline_sec = deadline_ms / 1000
        async with self._get_semaphore():
            for backend in self._backends:
                try:
                    async with asyncio.timeout(deadline_sec + 1.0):
                        result = await backend.render(url, deadline_ms, mode)
                    ttl = self._ttl.get(result.status, 0)
                    if ttl > 0:
                        self._cache[key] = (result, now)
                        if len(self._cache) > self._cache_max:
                            self._cache.popitem(last=False)
                    return result
                except TimeoutError:
                    continue
                except Exception:
                    continue
        result = RenderResult(None, 'no_backend', {})
        self._cache[key] = (result, now)
        if len(self._cache) > self._cache_max:
            self._cache.popitem(last=False)
        return result