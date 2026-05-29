"""
RenderCoordinator - decision tree for getting rendered HTML.
Sprint 67: Full Playwright WebKit implementation with timeout, routing, semaphore.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from hledac.universal.utils.capability_prober import get_prober

logger = logging.getLogger(__name__)

# CAPTCHA detection patterns
CAPTCHA_PATTERNS = [
    'captcha',
    'recaptcha',
    'hcaptcha',
    'g-recaptcha',
    'data-sitekey',
    'turnstile',
    'cloudflare',
    'challenge',
    'security check',
    'verify you are human',
    'i am not a robot',
    'select all images',
    'grid captcha',
]

if TYPE_CHECKING:
    from playwright.async_api import Browser, Playwright


@dataclass
class RenderResult:
    html: str | None
    status: Literal["ok", "no_backend", "timeout", "blocked", "error"]
    debug: dict  # max 4 keys, max 2KB

    def __post_init__(self):
        # Limit debug dict (security)
        if self.debug:
            if len(self.debug) > 4:
                self.debug = dict(list(self.debug.items())[:4])
            total_size = 0
            for k, v in list(self.debug.items()):
                if isinstance(v, str):
                    if len(v) > 500:
                        self.debug[k] = v[:500] + "..."
                    total_size += len(self.debug[k])
                elif isinstance(v, (list, dict)):
                    as_str = str(v)
                    if len(as_str) > 1000:
                        self.debug[k] = f"<truncated {len(as_str)} bytes>"
                    total_size += len(str(self.debug[k]))
            if total_size > 2048:
                self.debug = {"error": "debug too large"}


class RenderBackend:
    """Abstract backend - in S66 always returns no_backend."""

    async def render(self, url: str, deadline_ms: int, mode: str = "text") -> RenderResult:
        return RenderResult(None, "no_backend", {})


class PyObjCWKWebViewRenderer(RenderBackend):
    """Primary backend - native WKWebView via PyObjC (best stealth)."""
    # In S66 only stub - real implementation in future
    pass


class PlaywrightWebKitRenderer(RenderBackend):
    """Fallback - Playwright with WebKit."""
    MAX_HTML_SIZE = 4 * 1024 * 1024  # 4 MB

    def __init__(self):
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._render_count = 0
        self._max_renders_per_browser = 50  # Recreate browser every 50 renders

    async def _ensure_browser(self):
        """Ensure browser is available, create if needed or if count exceeded."""
        if self._browser is None or self._render_count >= self._max_renders_per_browser:
            await self._close_browser()
            try:
                from playwright.async_api import async_playwright
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.webkit.launch(headless=True)
                self._render_count = 0
                logger.info("Playwright WebKit browser launched")
            except Exception as e:
                logger.warning(f"Failed to launch Playwright: {e}")
                self._browser = None
                self._playwright = None
                raise

    async def _close_browser(self):
        """Close browser and playwright."""
        if self._browser:
            try:
                await self._browser.close()
            except Exception as e:
                logger.debug(f"Error closing browser: {e}")
            self._browser = None
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception as e:
                logger.debug(f"Error stopping playwright: {e}")
            self._playwright = None

    def _make_route_handler(self, blocked_types: list[str]):
        """Create route handler for blocking assets."""
        async def route_handler(route):
            if route.request.resource_type in blocked_types:
                await route.abort()
            else:
                await route.continue_()
        return route_handler

    async def render(
        self,
        url: str,
        deadline_ms: int,
        mode: Literal["full", "text"] = "text"
    ) -> RenderResult:
        """Render URL with Playwright WebKit."""
        await self._ensure_browser()

        if self._browser is None:
            return RenderResult(None, "no_backend", {"reason": "browser unavailable"})

        # Create context with stealth settings
        try:
            context = await self._browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
                locale="en-US",
                extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
                bypass_csp=True,
                java_script_enabled=True
            )
        except Exception as e:
            return RenderResult(None, "error", {"reason": f"context creation failed: {str(e)[:100]}"})

        # Single idempotent init script
        try:
            await context.add_init_script("""
                if (!window.__patched) {
                    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                    Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
                    Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
                    window.__patched = true;
                }
            """)
        except Exception as e:
            logger.debug(f"Init script failed: {e}")

        # Block assets based on mode
        blocked = ["image", "media", "font"]
        if mode == "text":
            blocked.append("stylesheet")
        route_handler = self._make_route_handler(blocked)
        try:
            await context.route("**/*", route_handler)
        except Exception as e:
            logger.debug(f"Route setup failed: {e}")

        page = await context.new_page()
        start_time = time.time()

        try:
            await page.goto(url, timeout=deadline_ms, wait_until="domcontentloaded")
            html = await page.content()

            # Sprint 71: Check for CAPTCHA
            if self._is_captcha_page(html):
                return await self._handle_captcha(url)

            if len(html) > self.MAX_HTML_SIZE:
                html = html[:self.MAX_HTML_SIZE] + "\n<!-- truncated -->"

            self._render_count += 1
            elapsed_ms = (time.time() - start_time) * 1000

            return RenderResult(html, "ok", {"elapsed_ms": round(elapsed_ms)})

        except asyncio.CancelledError:
            return RenderResult(None, "timeout", {"reason": "cancelled"})
        except Exception as e:
            error_str = str(e)[:100]
            if "timeout" in error_str.lower():
                return RenderResult(None, "timeout", {"reason": error_str})
            return RenderResult(None, "error", {"reason": error_str})
        finally:
            # Always close page and context
            try:
                await page.close()
            except Exception as e:
                logger.debug(f"Error closing page: {e}")
            try:
                await context.close()
            except Exception as e:
                logger.debug(f"Error closing context: {e}")


class CDPRenderer(RenderBackend):
    """Fallback - connection to running Chrome via CDP."""
    pass


class RenderCoordinator:
    def __init__(self):
        self._caps = get_prober()
        self._backends = [
            PyObjCWKWebViewRenderer(),
            PlaywrightWebKitRenderer(),
            CDPRenderer(),
        ]
        self._cache: OrderedDict[str, tuple[RenderResult, float]] = OrderedDict()
        self._cache_max = 200
        self._ttl = {
            "ok": 60,
            "no_backend": 10,
            "timeout": 5,
            "blocked": 5,
            "error": 0,  # errors not cached (TTL 0 means no caching)
        }
        # Semaphore for serialization (max 1 concurrent render)
        self._semaphore: asyncio.Semaphore | None = None

    def _get_semaphore(self) -> asyncio.Semaphore:
        """Get or create semaphore for render serialization."""
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(1)
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
        logger.info(f"CAPTCHA challenge for {url}")

        # Try to import and use VisionCaptchaSolver
        try:
            from hledac.universal.captcha_solver import VisionCaptchaSolver
            solver = VisionCaptchaSolver()
            logger.debug(f"VisionCaptchaSolver available: {solver is not None}")
        except ImportError:
            logger.debug("VisionCaptchaSolver not available")

        return RenderResult(
            None,
            "blocked",
            {"reason": "captcha_challenge", "url": url[:100]}
        )

    def _make_cache_key(self, url: str, deadline_ms: int, mode: str = "text") -> str:
        """Creates cache key with length limit and hash for distinction."""
        url_hash = hashlib.sha256(url.encode()).hexdigest()[:8]
        url_prefix = url[:170]  # Reserve space for mode
        if deadline_ms < 2000:
            bucket = "fast"
        elif deadline_ms < 5000:
            bucket = "slow"
        else:
            bucket = "very_slow"
        return f"{url_prefix}|{url_hash}|{bucket}|{mode}"

    async def render(
        self,
        url: str,
        deadline_ms: int = 5000,
        mode: Literal["full", "text"] = "text"
    ) -> RenderResult:
        """
        Render URL with timeout and mode.

        Args:
            url: URL to render
            deadline_ms: Timeout in milliseconds
            mode: "full" for all assets, "text" for text-only
        """
        key = self._make_cache_key(url, deadline_ms, mode)
        now = time.time()

        # 1. Try cache (only if TTL > 0)
        if key in self._cache:
            result, ts = self._cache[key]
            ttl = self._ttl.get(result.status, 0)
            if ttl > 0 and now - ts < ttl:
                return result
            else:
                del self._cache[key]

        # 2. Try backends with semaphore and timeout
        deadline_sec = deadline_ms / 1000
        async with self._get_semaphore():
            for backend in self._backends:
                try:
                    result = await asyncio.wait_for(
                        backend.render(url, deadline_ms, mode),
                        timeout=deadline_sec + 1.0  # Small buffer
                    )
                    # Cache only if TTL > 0
                    ttl = self._ttl.get(result.status, 0)
                    if ttl > 0:
                        self._cache[key] = (result, now)
                        if len(self._cache) > self._cache_max:
                            self._cache.popitem(last=False)
                    return result
                except TimeoutError:
                    # Try next backend
                    continue
                except Exception:
                    continue

        # 3. No backend succeeded
        result = RenderResult(None, "no_backend", {})
        self._cache[key] = (result, now)
        if len(self._cache) > self._cache_max:
            self._cache.popitem(last=False)
        return result
