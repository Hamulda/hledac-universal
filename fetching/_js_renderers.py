"""JS Renderers — extracted from public_fetcher.py (ISSUE-014 REFACTOR).

Provides JavaScript rendering via Camoufox, nodriver, and Playwright.
Optimized for M1 8GB with adaptive memory-aware concurrency.

"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import threading
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    pass

from hledac.universal.core.env_config import ENV
from core import aclose

# --- Camoufox lock (lazy initialization) ---
_CAMOUFOX_LOCK: asyncio.Lock | None = None
_CAMOUFOX_LOCK_INIT: bool = False

# --- JS Renderer Semaphore (lazy initialization) ---
_JS_RENDERER_SEMAPHORE: asyncio.Semaphore | None = None

# M1 8GB adaptive max bytes
MAX_BYTES_HARD_PRESSURE: Final[int] = 5000000

# --- Camoufox configuration ---
_CAMOUFOX_OS_ROTATION: tuple[str, ...] = ('macos', 'windows', 'linux')
_CAMOUFOX_MAX_RETRIES: int = 3

# --- nodriver configuration ---
_NODRIVER_MAX_RETRIES: int = 2


def _get_camoufox_lock() -> asyncio.Lock:
    """Lazily create camoufox lock in the current event loop.

    ISSUE-014 FIX: asyncio.Lock() at module import time causes "no running event loop"
    errors on macOS. This function creates the lock lazily on first async access.
    """
    global _CAMOUFOX_LOCK, _CAMOUFOX_LOCK_INIT
    if _CAMOUFOX_LOCK is None or not _CAMOUFOX_LOCK_INIT:
        _CAMOUFOX_LOCK = asyncio.Lock()
        _CAMOUFOX_LOCK_INIT = True
    return _CAMOUFOX_LOCK


def _get_js_renderer_semaphore() -> asyncio.Semaphore:
    """F226A: Lazily-initialized, per-event-loop JS renderer Semaphore.

    F-02 NOTE: Uses get_semaphore (fixed OK limit) rather than
    ConcurrencyBudgetRegistry dynamic adjustment. The BrowserPool.max_active=2
    provides the M1 8GB hard cap; ConcurrencyBudgetRegistry.JS_RENDERER
    category is registered for future dynamic UMA-aware adjustment.

    Thread-safe via functools.lru_cache internals (one lock, acquired once).
    Note: asyncio.Semaphore is created in the calling event loop context.
    """
    global _JS_RENDERER_SEMAPHORE
    if _JS_RENDERER_SEMAPHORE is not None:
        return _JS_RENDERER_SEMAPHORE
    from hledac.universal.core.concurrency import ConcurrencyCategory, get_semaphore
    _JS_RENDERER_SEMAPHORE = get_semaphore(ConcurrencyCategory.JS_RENDERER)
    return _JS_RENDERER_SEMAPHORE


def _check_chrome_binary_exists() -> bool:
    """Check if Chrome/Chromium binary is available on the system (macOS + Linux)."""
    candidates = [
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
        '/Applications/Chromium.app/Contents/MacOS/Chromium',
        '/usr/bin/google-chrome',
        '/usr/bin/google-chrome-stable',
        '/usr/bin/chromium',
        '/usr/bin/chromium-browser',
    ]
    from pathlib import Path
    return any(Path(p).exists() and os.access(p, os.X_OK) for p in candidates)


class _JSRendererCapability:
    """Thread-safe JS renderer capability tracker.

    F-GLOBAL: Encapsulates _js_renderer_capability dict and
    _js_renderer_capability_lock.

    Tracks availability of camoufox, nodriver, and playwright.
    Uses threading.Lock for thread-safe access.
    Cached after first check — use reset() to force re-check.
    """
    __slots__ = ('_capability', '_lock')

    def __init__(self) -> None:
        self._capability: dict[str, str | None] = {
            'camoufox': None, 'nodriver': None, 'playwright': None
        }
        self._lock = threading.Lock()

    def get(self) -> dict[str, str | None]:
        """Get current capability snapshot (copy)."""
        with self._lock:
            return dict(self._capability)

    def reset(self) -> None:
        """Reset all capabilities to unknown (force re-check)."""
        with self._lock:
            self._capability = {'camoufox': None, 'nodriver': None, 'playwright': None}

    def mark_unavailable(self, name: str, reason: str) -> None:
        """Mark a renderer as unavailable with a reason string."""
        if name in self._capability:
            with self._lock:
                self._capability[name] = reason

    def check_and_update(self) -> dict[str, str | None]:
        """Run capability checks and update cached state.

        Returns capability dict with reasons for unavailability.
        """
        with self._lock:
            self._check_camoufox()
            self._check_nodriver()
            self._check_playwright()
            return dict(self._capability)

    def _check_camoufox(self) -> None:
        """Check camoufox availability."""
        if self._capability['camoufox'] is not None:
            return
        try:
            import camoufox
            _ = camoufox.Session
            self._capability['camoufox'] = None
        except Exception as e:  # noqa: BLE001 — best-effort
            self._capability['camoufox'] = f'camoufox_unavailable: {e}'

    def _check_nodriver(self) -> None:
        """Check nodriver availability."""
        if self._capability['nodriver'] is not None:
            return
        if not _check_chrome_binary_exists():
            self._capability['nodriver'] = 'chrome_binary_missing'
            return
        try:
            import nodriver
            _ = nodriver.start
            self._capability['nodriver'] = None
        except Exception as e:  # noqa: BLE001 — best-effort
            self._capability['nodriver'] = f'nodriver_unavailable: {e}'

    def _check_playwright(self) -> None:
        """Check playwright availability."""
        if self._capability['playwright'] is not None:
            return
        if not ENV.get_bool('HLEDAC_ENABLE_HEAVY_BROWSER'):
            self._capability['playwright'] = 'heavy_browser_disabled'
            return
        try:
            import playwright
            _ = playwright.async_api
            self._capability['playwright'] = None
        except Exception as e:  # noqa: BLE001 — best-effort
            self._capability['playwright'] = f'playwright_unavailable: {e}'

    def is_any_available(self) -> bool:
        """Check if any JS renderer is available."""
        with self._lock:
            return any(v is None for v in self._capability.values())


# Singleton instance
_js_renderer_cap = _JSRendererCapability()


def get_js_renderer_capability() -> dict[str, str | None]:
    """
    Return capability dict for all JS renderers.
    Values: None = available, str = unavailable reason.
    Cached after first call per renderer.

    F-GLOBAL: Delegates to _js_renderer_cap singleton.
    """
    return _js_renderer_cap.check_and_update()


def all_js_renderers_unavailable() -> bool:
    """Return True if all JS renderers are unavailable.

    Checks the cached capability dict directly without triggering re-detection.
    None = available (renderer has no unavailable reason).
    Str = unavailable reason.
    """
    cap = _js_renderer_cap.get()
    return all(v is not None for v in cap.values())


def reset_js_renderer_capability_cache() -> None:
    """
    Reset JS renderer capability cache.

    Use this for tests, diagnostics, or long-running runtime refresh.
    Does NOT trigger browser startup or heavy imports — only resets
    the cached capability dict so the next get_js_renderer_capability()
    call re-detects from scratch.
    """
    _js_renderer_cap.reset()


def refresh_js_renderer_capability() -> dict[str, str | None]:
    """
    Force re-detect JS renderer capabilities and return current state.

    Unlike reset_js_renderer_capability_cache(), this also returns
    the freshly-detected capability dict.
    """
    reset_js_renderer_capability_cache()
    return get_js_renderer_capability()


# --- UMA check ---
try:
    from hledac.universal.utils.uma_budget import is_uma_critical as _is_uma_critical
except Exception:  # noqa: BLE001 — best-effort

    def _is_uma_critical() -> bool:
        return False


def compute_effective_max_bytes(requested: int) -> int:
    """
    F226A: Adaptive body cap honoring caller request, hard cap, and UMA pressure.

    Behavior:
    - Clamps requested to [1, MAX_BYTES_HARD].
    - On UMA critical, further halves the cap to MAX_BYTES_HARD_PRESSURE (5MB).
    - Fail-soft: if UMA sampler throws, falls back to MAX_BYTES_HARD (10MB).

    Why this matters: 25 in-flight × 10MB = 250MB just for fetch bodies on M1 8GB.
    Under pressure, halving brings that to 125MB, leaving headroom for browser
    processes (300-500MB) and MLX (2GB).
    """
    try:
        from hledac.universal.fetching.public_fetcher import MAX_BYTES_HARD
        hard = MAX_BYTES_HARD_PRESSURE if _is_uma_critical() else MAX_BYTES_HARD
    except Exception:  # noqa: BLE001 — best-effort; UMA check failure falls back to non-pressure cap
        hard = MAX_BYTES_HARD
    if requested <= 0:
        return hard
    return min(max(requested, 1), hard)


# --- TOR SOCKS Proxy ---
TOR_SOCKS_PROXY: Final[str] = os.environ.get('TOR_SOCKS_PROXY_URL', 'socks5h://127.0.0.1:9050')


# ==== Renderer Implementations ====

async def fetch_with_camoufox(url: str, timeout: float = 15.0) -> str:
    """
    Fetch JS-heavy page via Camoufox (Firefox-based anti-detect).
    Max 1 instance, protected by _CAMOUFOX_LOCK singleton.
    M1-optimized: headless, WebGL spoofed for Apple M1.

    F202H: Uses opsec_policy.get_renderer_policy() for M1 conflict guard —
    replaces inline is_embedding_context_active() check with centralized policy.
    """
    try:
        from hledac.universal.embedding_pipeline import is_embedding_context_active
        from hledac.universal.runtime.opsec_policy import OPSECContext, get_renderer_policy
        has_model = is_embedding_context_active()
        ctx = OPSECContext(has_model_context=has_model)
        policy = get_renderer_policy(ctx)
        if not policy.allowed:
            return ''
    except Exception as e:  # noqa: BLE001 — best-effort
        pass
    try:
        from camoufox.async_api import AsyncCamoufox
    except ImportError:
        return ''
    async with _get_js_renderer_semaphore():
        return await _camoufox_locked(url, timeout)


async def _camoufox_locked(url: str, timeout: float) -> str:
    """
    F226A: Camoufox body inside the original _CAMOUFOX_LOCK + outer JS semaphore.
    P2-4: Added os-rotation retry — each OS variant generates a different
    auto-generated fingerprint, so dark web sites that block one fingerprint
    may accept another. Retries up to 3 OS variants before giving up.
    """
    try:
        from camoufox.async_api import AsyncCamoufox
    except ImportError:
        return ''
    async with _get_camoufox_lock():
        last_error = ''
        for attempt in range(_CAMOUFOX_MAX_RETRIES):
            os_choice = _CAMOUFOX_OS_ROTATION[attempt % len(_CAMOUFOX_OS_ROTATION)]
            try:
                async with AsyncCamoufox(
                    headless=True,
                    os=os_choice,
                    webgl_config=('Apple', 'Apple M1, or similar')
                ) as browser:
                    page = await browser.new_page()
                    try:
                        await page.goto(url, wait_until='networkidle', timeout=timeout * 1000)
                        html = await page.content()
                    finally:
                        await page.close()
                    return html
            except Exception as e:  # noqa: BLE001 — best-effort
                last_error = str(e)
                continue
        return ''


async def fetch_with_nodriver(url: str, url_kind: str = '', url_host: str = '') -> str:
    """
    F265C: Primary JS fetch via nodriver (direct CDP, no WebDriver).
    On M1, nodriver is more stable than Camoufox — used as first choice.
    Requires Chrome binary present. Returns "" with telemetry on failure.

    B1: url_kind/url_host params — caller pre-classified via classify_url_cached.
    """
    if not _check_chrome_binary_exists():
        return ''
    if _is_uma_critical():
        return ''
    try:
        import nodriver as uc
    except ImportError:
        _js_renderer_cap.mark_unavailable('nodriver', 'nodriver_unavailable')
        return ''
    async with _get_js_renderer_semaphore():
        return await _nodriver_locked(url, url_kind, url_host)


async def _nodriver_locked(url: str, url_kind: str = '', url_host: str = '') -> str:
    """
    F226A: nodriver body wrapped inside the shared _JS_RENDERER_SEMAPHORE.
    F-02: Replaced per-call uc.start() cold-start with BrowserPool.
    """
    from hledac.universal.utils.browser_pool import acquire_browser, release_browser
    from hledac.universal.fetching._url_ops import classify_url_cached

    if not url_kind or not url_host:
        _url_kind_batch = classify_url_cached([url])
        url_kind = _url_kind_batch[0][0] if _url_kind_batch else 'clearnet'
    _is_onion = url_kind == 'onion'

    _tor_proxy: str | None = TOR_SOCKS_PROXY if _is_onion else None

    page = None
    last_error = ''
    for attempt in range(_NODRIVER_MAX_RETRIES):
        browser = None
        try:
            browser = await acquire_browser(tor_proxy=_tor_proxy)
            page = await browser.get(url)
            try:
                await asyncio.sleep(2)
                html = await page.get_content()
            finally:
                if page is not None:
                    try:
                        await page.close()
                    except Exception:  # noqa: BLE001 — best-effort
                        pass
            return html
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — best-effort
            last_error = str(e)
            await asyncio.sleep(0.2)
        finally:
            if browser is not None:
                try:
                    await release_browser(browser, tor_proxy=_tor_proxy)
                except Exception:  # noqa: BLE001 — best-effort
                    pass
    return ''


async def fetch_with_playwright(url: str, timeout: float = 15.0) -> str:
    """
    F265C: Playwright fallback — last resort after nodriver fails.
    Requires HLEDAC_ENABLE_HEAVY_BROWSER=1 AND playwright installed.
    Returns "" with telemetry on any failure.
    """
    if not ENV.get_bool('HLEDAC_ENABLE_HEAVY_BROWSER'):
        return ''
    try:
        importlib.util.find_spec('playwright')
    except ImportError:
        return ''
    async with _get_js_renderer_semaphore():
        return await _playwright_locked(url, timeout)


async def _playwright_locked(url: str, timeout: float) -> str:
    """
    F265C: Playwright body wrapped inside the shared _JS_RENDERER_SEMAPHORE.
    Chromium via playwright — fails soft on all errors.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return ''
    browser = None
    page = None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            try:
                await page.goto(url, wait_until='networkidle', timeout=timeout * 1000)
                html = await page.content()
            finally:
                if page is not None:
                    await page.close()
            return html
    except asyncio.CancelledError:
        if browser is not None:
            await browser.close()
        raise
    except Exception as e:  # noqa: BLE001 — best-effort
        return ''
    finally:
        if browser is not None:
            await browser.close()


async def teardown_browser_pool() -> None:
    """
    Teardown nodriver BrowserPool + camoufox shared state at sprint winddown.

    F-02: BrowserPool.close() stops all idle Chromium instances and marks the
    pool as closed. BrowserPool is a lazy singleton — created on first use,
    closed here at winddown.

    Called from sprint_scheduler run_winddown(). Fail-soft — any error is
    swallowed at DEBUG level. Must be idempotent (safe to call multiple times).

    Resets:
    - BrowserPool singleton: closes all idle browsers, clears global reference
    - _JS_RENDERER_SEMAPHORE: released and cleared so next sprint re-initializes
      in the correct event loop
    - _js_renderer_capability: reset to None so next sprint re-probes availability
    - yields cooldown to let any in-flight browser.stop() calls finish
    """
    # F-02: Close BrowserPool (stops Chromium, clears singleton)
    try:
        from hledac.universal.utils.browser_pool import close_pool
        await close_pool()
    except Exception as _e:  # noqa: BLE001 — best-effort
        pass

    # Legacy semaphore teardown (camoufox still uses it)
    global _JS_RENDERER_SEMAPHORE
    try:
        _sem = _JS_RENDERER_SEMAPHORE
        if _sem is not None:
            try:
                for _ in range(_sem._value + 1):
                    await asyncio.sleep(0)
            except Exception:  # noqa: BLE001 — best-effort
                pass
            _JS_RENDERER_SEMAPHORE = None
    except Exception:  # noqa: BLE001 — best-effort
        pass

    try:
        _js_renderer_cap.reset()
    except Exception:  # noqa: BLE001 — best-effort
        pass
