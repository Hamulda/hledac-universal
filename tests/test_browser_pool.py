"""
Smoke tests for BrowserPool (F-02).

Tests cover:
1. Pool lifecycle (singleton, close)
2. acquire/release round-trip
3. Idle reuse — second acquire returns the same browser
4. Memory pressure guard
5. Tor pool isolation (separate pools for tor vs clearnet)
6. Closed pool raises RuntimeError
7. prewarm + acquire reuse
8. Tor proxy in browser_args per pool
"""
from __future__ import annotations

import asyncio
import gc

import pytest
from core import aclose


# ─── helpers ───────────────────────────────────────────────────────────────────


class _DummyBrowser:
    """Stand-in for a nodriver Browser.

    Simulates nodriver browser lifecycle:
    - `tabs` raises AttributeError after `stop()` is called
      (real nodriver Browser raises after stop — tabs become inaccessible)
    - `_stopped` tracks whether stop() was called
    """

    _instances: list[_DummyBrowser] = []

    def __init__(self, unique_id: int):
        self.unique_id = unique_id
        self._tabs: list[object] = []
        self._stopped = False
        _DummyBrowser._instances.append(self)

    @property
    def tabs(self) -> list[object]:
        """Raise if browser has been stopped (mimics real nodriver behavior)."""
        if self._stopped:
            raise AttributeError("tabs not accessible after browser.stop()")
        return self._tabs

    async def stop(self) -> None:
        self._stopped = True
        if self in _DummyBrowser._instances:
            _DummyBrowser._instances.remove(self)

    @classmethod
    def reset(cls) -> None:
        cls._instances.clear()


# Global counter for assigning unique IDs — reset per test
_launch_id: int = 0


def _make_launch_patch():
    """Patch BrowserPool._launch_browser; returns the restore callable."""
    import utils.browser_pool as bp_module

    original = bp_module.BrowserPool._launch_browser

    async def patched_launch(self):
        global _launch_id
        _launch_id += 1
        return _DummyBrowser(_launch_id)

    bp_module.BrowserPool._launch_browser = patched_launch
    return original


# ─── Pool lifecycle ───────────────────────────────────────────────────────────


def test_pool_singleton_creation():
    """First _get_pool() creates the singleton; second call returns the same."""

    async def _run():
        from utils.browser_pool import _get_pool, close_pool
        import utils.browser_pool as bp_module

        original = _make_launch_patch()
        _DummyBrowser.reset()
        global _launch_id
        _launch_id = 0

        try:
            p1 = await _get_pool()
            p2 = await _get_pool()
            assert p1 is p2
        finally:
            bp_module.BrowserPool._launch_browser = original
            await close_pool()
            gc.collect()

    asyncio.run(_run())


def test_pool_close_stops_idle_browsers():
    """close() stops all idle browsers and clears the registry."""

    async def _run():
        from utils.browser_pool import _get_pool, close_pool
        import utils.browser_pool as bp_module

        original = _make_launch_patch()
        _DummyBrowser.reset()
        global _launch_id
        _launch_id = 0

        try:
            pool = await _get_pool()
            b = await pool.acquire()
            assert not b._stopped
            await pool.release(b)
            assert len(pool._idle) == 1
            await close_pool()
            assert b._stopped
        finally:
            bp_module.BrowserPool._launch_browser = original
            gc.collect()

    asyncio.run(_run())


# ─── acquire / release round-trip ─────────────────────────────────────────────


def test_acquire_release_returns_browser():
    """acquire() returns a browser; release() returns it to the idle deque."""

    async def _run():
        import utils.browser_pool as bp_module

        original = _make_launch_patch()
        _DummyBrowser.reset()
        global _launch_id
        _launch_id = 0

        try:
            pool = bp_module.BrowserPool(max_idle=1, max_active=2)
            b = await pool.acquire()
            assert b.unique_id == 1
            assert b not in pool._idle
            await pool.release(b)
            assert b in pool._idle
        finally:
            bp_module.BrowserPool._launch_browser = original
            gc.collect()

    asyncio.run(_run())


def test_idle_browser_reused_on_second_acquire():
    """Second acquire() returns the same browser from the idle deque (no cold start)."""

    async def _run():
        import utils.browser_pool as bp_module

        original = _make_launch_patch()
        _DummyBrowser.reset()
        global _launch_id
        _launch_id = 0

        try:
            pool = bp_module.BrowserPool(max_idle=1, max_active=2)
            b1 = await pool.acquire()
            await pool.release(b1)
            b2 = await pool.acquire()
            # Same browser instance should be reused
            assert b2 is b1
            assert _launch_id == 1
        finally:
            bp_module.BrowserPool._launch_browser = original
            gc.collect()

    asyncio.run(_run())


# ─── Concurrency ─────────────────────────────────────────────────────────────


def test_max_active_blocks_excess_acquirers():
    """When max_active semaphores are exhausted, acquire() blocks the caller."""

    async def _run():
        import utils.browser_pool as bp_module

        original = _make_launch_patch()
        _DummyBrowser.reset()
        global _launch_id
        _launch_id = 0

        try:
            pool = bp_module.BrowserPool(max_idle=0, max_active=2)

            b1 = await pool.acquire()
            b2 = await pool.acquire()

            # Semaphore should be exhausted
            assert pool._active_sem.locked()

            await pool.release(b1)
            assert not pool._active_sem.locked()
        finally:
            bp_module.BrowserPool._launch_browser = original
            gc.collect()

    asyncio.run(_run())


# ─── Memory pressure ──────────────────────────────────────────────────────────


def test_memory_pressure_blocks_acquire():
    """When RSS > threshold, acquire() raises MemoryPressureError."""

    async def _run():
        import utils.browser_pool as bp_module
        import utils.browser_pool as bp

        original = _make_launch_patch()
        _DummyBrowser.reset()
        global _launch_id
        _launch_id = 0

        orig_rss = bp._rss_gib

        def fake_rss() -> float:
            return 999.0

        bp._rss_gib = fake_rss

        try:
            pool = bp_module.BrowserPool()
            with pytest.raises(bp_module.MemoryPressureError):
                await pool.acquire()
        finally:
            bp_module.BrowserPool._launch_browser = original
            bp._rss_gib = orig_rss
            gc.collect()

    asyncio.run(_run())


# ─── Tor pool isolation ──────────────────────────────────────────────────────


def test_tor_pool_is_separate_from_clearnet():
    """Clearnet pool and Tor pool are distinct — different browsers, different args."""

    async def _run():
        from utils.browser_pool import _get_pool, close_pool
        import utils.browser_pool as bp_module

        original = _make_launch_patch()
        _DummyBrowser.reset()
        global _launch_id
        _launch_id = 0

        try:
            clearnet = await _get_pool(tor_proxy=None)
            tor = await _get_pool(tor_proxy="socks5://localhost:9050")
            assert clearnet is not tor

            b_clear = await clearnet.acquire()
            b_tor = await tor.acquire()
            # Different pools → different browser instances
            assert b_clear is not b_tor
        finally:
            bp_module.BrowserPool._launch_browser = original
            await close_pool()
            gc.collect()

    asyncio.run(_run())


# ─── prewarm ────────────────────────────────────────────────────────────────


def test_prewarm_launches_idle_browser():
    """prewarm() adds one browser to the idle deque without blocking."""

    async def _run():
        import utils.browser_pool as bp_module

        original = _make_launch_patch()
        _DummyBrowser.reset()
        global _launch_id
        _launch_id = 0

        try:
            pool = bp_module.BrowserPool()
            assert len(pool._idle) == 0

            await pool.prewarm()
            assert len(pool._idle) == 1
            assert _launch_id == 1

            # Second prewarm is no-op
            await pool.prewarm()
            assert len(pool._idle) == 1
            assert _launch_id == 1

            # Acquire should reuse the prewarmed browser
            b = await pool.acquire()
            assert _launch_id == 1
        finally:
            bp_module.BrowserPool._launch_browser = original
            gc.collect()

    asyncio.run(_run())


# ─── closed pool ──────────────────────────────────────────────────────────────


def test_closed_pool_raises_on_acquire():
    """After close(), acquire() raises RuntimeError."""

    async def _run():
        import utils.browser_pool as bp_module

        original = _make_launch_patch()
        _DummyBrowser.reset()
        global _launch_id
        _launch_id = 0

        try:
            pool = bp_module.BrowserPool()
            await pool.close()
            with pytest.raises(RuntimeError, match="closed"):
                await pool.acquire()
        finally:
            bp_module.BrowserPool._launch_browser = original
            gc.collect()

    asyncio.run(_run())


# ─── browser_args preserved per pool ─────────────────────────────────────────


def test_tor_proxy_in_browser_args():
    """Pool for tor_proxy has --proxy-server in browser_args; clearnet does not."""

    async def _run():
        from utils.browser_pool import _get_pool, close_pool
        import utils.browser_pool as bp_module

        original = _make_launch_patch()
        _DummyBrowser.reset()
        global _launch_id
        _launch_id = 0

        try:
            tor_pool = await _get_pool(tor_proxy="socks5://localhost:9050")
            assert any("--proxy-server=" in a for a in tor_pool._browser_args)
            assert any("socks5://localhost:9050" in a for a in tor_pool._browser_args)

            clear_pool = await _get_pool(tor_proxy=None)
            assert not any("--proxy-server=" in a for a in clear_pool._browser_args)
        finally:
            bp_module.BrowserPool._launch_browser = original
            await close_pool()
            gc.collect()

    asyncio.run(_run())


# ─── dead browser replacement ─────────────────────────────────────────────────


def test_dead_browser_replaced_on_acquire():
    """If idle browser is dead, _get_or_create_browser launches a replacement."""

    async def _run():
        import utils.browser_pool as bp_module

        original = _make_launch_patch()
        _DummyBrowser.reset()
        global _launch_id
        _launch_id = 0

        try:
            pool = bp_module.BrowserPool(max_idle=1, max_active=2)

            b1 = await pool.acquire()
            await pool.release(b1)
            assert _launch_id == 1

            # Simulate browser death by marking it stopped
            b1._stopped = True

            b2 = await pool.acquire()
            # New browser should have been launched
            assert b2 is not b1
            assert _launch_id == 2
        finally:
            bp_module.BrowserPool._launch_browser = original
            gc.collect()

    asyncio.run(_run())


# ─── close_pool closes all pools ──────────────────────────────────────────────


def test_close_pool_closes_all():
    """close_pool() closes every pool in the registry."""

    async def _run():
        from utils.browser_pool import _get_pool, close_pool
        import utils.browser_pool as bp_module

        original = _make_launch_patch()
        _DummyBrowser.reset()
        global _launch_id
        _launch_id = 0

        try:
            pool_a = await _get_pool(tor_proxy=None)
            pool_b = await _get_pool(tor_proxy="socks5://localhost:9050")

            ba = await pool_a.acquire()
            bb = await pool_b.acquire()
            await pool_a.release(ba)
            await pool_b.release(bb)

            await close_pool()

            assert ba._stopped
            assert bb._stopped
        finally:
            bp_module.BrowserPool._launch_browser = original
            gc.collect()

    asyncio.run(_run())
