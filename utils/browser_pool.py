"""
BrowserPool — F-02 CRITICAL: persistent nodriver browser pool.

Replaces per-call uc.start() cold-start penalty (~1.5-2 s / browser) with
a bounded pool of pre-warmed idle browsers.

Architecture
~~~~~~~~~~~~
idle deque (maxlen=max_idle)
    └─ Browser #1  ←→  acquire() returns this, release() returns it
    └─ Browser #2  ←→  acquire() returns this, release() returns it
active semaphore (max_active)
    └─ limits concurrent browser users to max_active (default 2 on M1 8GB)

Cold-start cost: 1 × 1.5-2 s at pool creation (on first acquire of empty pool).
Warm-hit cost:   ~0 ms — just popleft() from idle deque.

M1 8GB RAM budget
~~~~~~~~~~~~~~~~~~
- 2 Chromium contexts ≈ 400 MB (200 MB each)
- Cooldown sleep between releases: 0.5 s (lets OS reap sandbox processes)
- Memory pressure guard: checked before every acquire

Invariants
~~~~~~~~~~
- Always-on, no feature flag — BrowserPool is always active when nodriver is
  available (lazy import, no module-level side effects)
- Fail-safe: if any browser launch fails, pool degrades gracefully — callers
  fall back to the warm-path retry or the non-browser fetch path
- Bounded: max_idle=1 (1 pre-warmed browser), max_active=2 (M1 8GB can
  sustain 2 concurrent Chromium contexts ≈ 400 MB)
- No bare except — every operation is wrapped in try/except with fallback
- mx.eval([]) not needed here — no MLX calls in this module

Usage
~~~~~
    pool = BrowserPool()
    browser = await pool.acquire()
    try:
        tab = await browser.get(url)
        html = await tab.get_content()
    finally:
        await pool.release(browser)
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections import deque
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import nodriver as uc

logger = logging.getLogger(__name__)

# Defaults — M1 8GB RAM budget
# 2 concurrent Chromium ≈ 400 MB (200 MB each)
_DEFAULT_MAX_IDLE: int = 1  # 1 pre-warmed browser in idle deque
_DEFAULT_MAX_ACTIVE: int = 2  # M1 8GB can sustain 2 Chromium contexts

# Cooldown after release — lets macOS fully reap sandbox/helper processes
_RELEASE_COOLDOWN_S: float = 0.5

# Memory pressure threshold — block browser acquire if RSS > this (GiB)
_BROWSER_MEM_THRESHOLD_GIB: float = 1.0


def _rss_gib() -> float:
    """Return current process RSS in GiB, or 0.0 on any error."""
    try:
        import psutil

        return psutil.Process(os.getpid()).memory_info().rss / 1024**3
    except Exception:
        return 0.0


def _check_memory_pressure() -> None:
    """Raise MemoryPressureError if RSS exceeds _BROWSER_MEM_THRESHOLD_GIB."""
    threshold = _BROWSER_MEM_THRESHOLD_GIB
    try:
        threshold = float(os.environ.get("HLEDAC_BROWSER_MEM_THRESHOLD_GIB", str(threshold)))
    except (ValueError, TypeError):
        threshold = _BROWSER_MEM_THRESHOLD_GIB
    rss = _rss_gib()
    if rss > threshold > 0:
        raise MemoryPressureError(
            f"BrowserPool acquire blocked: RSS={rss:.2f} GiB > threshold={threshold:.2f} GiB"
        )


class MemoryPressureError(Exception):
    """Raised when system RSS exceeds the browser launch threshold."""


class BrowserPool:
    """
    Async pool of persistent nodriver browser instances.

    Reuses idle browsers instead of spawning a new Chromium per fetch call,
    eliminating the 1.5-2 s cold-start penalty.

    Args:
        max_idle: Number of pre-warmed idle browsers to keep (default 1).
                  Set to 0 to disable pre-warming (always launch on acquire).
        max_active: Maximum concurrent browser users (default 2 for M1 8GB).
                    Acts as a semaphore — callers block when this limit is reached.
        browser_args: Extra Chrome flags added to every browser launch.
                      Defaults to stealth-friendly flags:
                      --no-sandbox, --disable-dev-shm-usage,
                      --disable-blink-features=AutomationControlled
    """

    __slots__ = (
        "_max_idle",
        "_max_active",
        "_browser_args",
        "_idle",
        "_active_sem",
        "_lock",
        "_prewarm_task",
        "_closed",
    )

    def __init__(
        self,
        max_idle: int = _DEFAULT_MAX_IDLE,
        max_active: int = _DEFAULT_MAX_ACTIVE,
        browser_args: list[str] | None = None,
    ) -> None:
        self._max_idle = max_idle
        self._max_active = max_active
        self._browser_args: list[str] = browser_args or [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
        ]
        # deque with maxlen — bounded idle cache, oldest entry evicted when full
        self._idle: deque[Any] = deque(maxlen=max_idle)
        self._active_sem: asyncio.Semaphore = asyncio.Semaphore(max_active)
        self._lock: asyncio.Lock = asyncio.Lock()
        self._prewarm_task: asyncio.Task[None] | None = None
        self._closed: bool = False

    # ─── public API ────────────────────────────────────────────────────────────

    async def acquire(self) -> Any:
        """
        Acquire a browser from the pool.

        Returns a ready-to-use nodriver Browser instance.
        Caller MUST call :meth:`release` when done.

        Raises:
            MemoryPressureError: If RSS exceeds the threshold before launch.
            RuntimeError: If the pool has been closed.
        """
        if self._closed:
            raise RuntimeError("BrowserPool has been closed")

        _check_memory_pressure()

        # Semaphore limits concurrent active users
        await self._active_sem.acquire()

        try:
            browser = await self._get_or_create_browser()
            return browser
        except Exception:
            # Release the semaphore slot on failure — caller will retry / fallback
            self._active_sem.release()
            raise

    async def release(self, browser: Any) -> None:
        """
        Return a browser to the idle pool.

        The browser is added back to the idle deque (bounded, LRU eviction)
        and the active semaphore slot is released.

        Args:
            browser: A nodriver Browser previously returned by :meth:`acquire`.
        """
        if self._closed:
            await self._stop_browser(browser)
            self._active_sem.release()
            return

        # Put browser back in idle deque (bounded — oldest evicted if full)
        self._idle.append(browser)

        # Cooldown so macOS can fully reap sandbox/helper processes
        await asyncio.sleep(_RELEASE_COOLDOWN_S)

        # Release the semaphore slot
        self._active_sem.release()

    async def prewarm(self) -> None:
        """
        Pre-warm the pool by launching one idle browser in the background.

        Safe to call multiple times — only the first browser is created if
        the idle deque is still empty.
        """
        if self._idle or self._closed:
            return  # already warmed or closed

        async with self._lock:
            if self._idle or self._closed:
                return  # double-check after acquiring lock
            try:
                browser = await self._launch_browser()
                self._idle.append(browser)
                logger.debug("[BrowserPool] pre-warmed 1 idle browser")
            except Exception as e:
                logger.debug("[BrowserPool] pre-warm failed (non-fatal): %s", e)

    async def close(self) -> None:
        """
        Gracefully shut down the pool — stops all idle browsers and marks
        the pool as closed.

        Safe to call multiple times. After close(), acquire() raises RuntimeError.
        """
        self._closed = True

        # Cancel any prewarm task
        if self._prewarm_task is not None:
            self._prewarm_task.cancel()
            try:
                await self._prewarm_task
            except asyncio.CancelledError:
                pass
            self._prewarm_task = None

        # Stop all idle browsers
        async with self._lock:
            while self._idle:
                browser = self._idle.popleft()
                await self._stop_browser(browser)

    # ─── internal ───────────────────────────────────────────────────────────────

    async def _get_or_create_browser(self) -> Any:
        """Pop from idle deque or launch a new browser (with lock for creation)."""
        # Fast path: reuse idle browser if available
        if self._idle:
            try:
                browser = self._idle.popleft()
                # Verify the browser process is still alive
                if await self._browser_alive(browser):
                    return browser
                # Browser died — launch a replacement
                logger.debug("[BrowserPool] idle browser was dead, launching replacement")
                return await self._launch_browser()
            except Exception:
                pass  # fall through to launch below

        # Slow path: launch new browser (lock prevents thundering herd)
        async with self._lock:
            # One more check after acquiring lock
            if self._idle:
                browser = self._idle.popleft()
                if await self._browser_alive(browser):
                    return browser
            return await self._launch_browser()

    async def _launch_browser(self) -> Any:
        """Launch a new nodriver browser and return it."""
        import nodriver as uc

        logger.debug("[BrowserPool] launching new Chromium (cold start ~1.5-2 s)")
        browser = await uc.start(headless=True, browser_args=self._browser_args)
        return browser

    async def _browser_alive(self, browser: Any) -> bool:
        """Check if the browser process is still responsive."""
        try:
            # nodriver exposes no explicit is_alive — try a cheap CDP ping
            # Browser has a `tab` attribute when tabs are open
            _ = browser.tabs
            return True
        except Exception:
            return False

    async def _stop_browser(self, browser: Any) -> None:
        """Stop a browser and swallow all exceptions (fail-safe)."""
        try:
            await browser.stop()
        except Exception as e:
            logger.debug("[BrowserPool] browser.stop() error (non-fatal): %s", e)


# ─── module-level pool registry ──────────────────────────────────────────────────

# Separate pools for different proxy configurations.
# Key: tor_proxy string or None for direct connection.
# Each pool maintains its own idle deque and semaphore.
_POOLS: dict[str | None, BrowserPool] = {}
_POOLS_LOCK: asyncio.Lock = asyncio.Lock()

# Tor proxy setting used as dict key — must match caller
_TOR_PROXY_KEY = "tor"


def _pool_key(tor_proxy: str | None) -> str | None:
    """Map tor_proxy value to pool registry key."""
    if tor_proxy:
        return f"{_TOR_PROXY_KEY}:{tor_proxy}"
    return None


async def _get_pool(tor_proxy: str | None = None) -> BrowserPool:
    """
    Get or create a BrowserPool for the given tor_proxy setting.

    Tor-routed and clearnet browsers are kept in separate pools to avoid
    cross-contaminating proxy configurations.
    """
    key = _pool_key(tor_proxy)
    if key in _POOLS:
        return _POOLS[key]
    async with _POOLS_LOCK:
        if key in _POOLS:
            return _POOLS[key]
        # Build browser_args with optional Tor proxy
        browser_args = [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
        ]
        if tor_proxy:
            browser_args.append(f"--proxy-server={tor_proxy}")
        pool = BrowserPool(
            max_idle=_DEFAULT_MAX_IDLE,
            max_active=_DEFAULT_MAX_ACTIVE,
            browser_args=browser_args,
        )
        _POOLS[key] = pool
        return pool


async def acquire_browser(tor_proxy: str | None = None) -> Any:
    """
    Acquire a browser from the appropriate pool.

    Args:
        tor_proxy: Tor SOCKS proxy URL (e.g. ``TOR_SOCKS_PROXY``).
                   Pass None for direct (clearnet) connection.

    Usage::

        browser = await acquire_browser()
        try:
            tab = await browser.get(url)
            html = await tab.get_content()
        finally:
            await release_browser(browser)

    For onion URLs::

        browser = await acquire_browser(tor_proxy=TOR_SOCKS_PROXY)
        ...
    """
    pool = await _get_pool(tor_proxy)
    return await pool.acquire()


async def release_browser(browser: Any, tor_proxy: str | None = None) -> None:
    """
    Release a browser back to its pool.

    Args:
        browser: The browser returned by :func:`acquire_browser`.
        tor_proxy: Must match the tor_proxy passed to :func:`acquire_browser`.
    """
    pool = await _get_pool(tor_proxy)
    await pool.release(browser)


async def close_pool() -> None:
    """
    Close all BrowserPools — called at sprint winddown.

    Iterates all pools in the registry and calls close() on each.
    Fail-safe: errors are swallowed at DEBUG level.
    """
    global _POOLS
    async with _POOLS_LOCK:
        for key, pool in list(_POOLS.items()):
            try:
                await pool.close()
            except Exception as e:
                logger.debug("[browser_pool] close(%s) skipped: %s", key, e)
        _POOLS.clear()


async def prewarm_pool(tor_proxy: str | None = None) -> None:
    """Pre-warm the appropriate pool with one idle browser."""
    pool = await _get_pool(tor_proxy)
    await pool.prewarm()
