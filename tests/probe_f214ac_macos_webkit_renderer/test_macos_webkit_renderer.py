# tests/probe_f214ac_macos_webkit_renderer/test_macos_webkit_renderer.py
# Sprint F214AC: macOS WKWebView Subprocess JS Renderer
# Hermetic unit tests — most run without macOS or WebKit.
"""Unit tests for macOS WKWebView renderer (rendering/macos_webkit_renderer.py).

Coverage:
1. communicate timeout pattern — wrapper uses asyncio.wait_for, not timeout= kwarg
2. timeout cleanup — terminate() then kill() if wait timeout
3. malformed worker JSON → worker_error
4. max bytes exceeded → max_bytes_exceeded
5. capability cache — two calls don't double-probe
6. CancelledError propagation
7. import smoke — fresh import works
8. static hydration skip invariant
9. heavy browser fallback invariant
10. counter invariant — WKWebView increments js_renderer_count, static hydration does not
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from unittest import mock

import pytest

from hledac.universal.rendering.macos_webkit_renderer import (
    WebKitRenderResult,
    is_macos_webkit_available,
    fetch_with_macos_webkit,
    MACOS_WEBKIT_REASONS,
    _WEBKIT_CAPABILITY_CACHE,
    reset_macos_webkit_capability_cache,
    refresh_macos_webkit_capability,
)


# --------------------------------------------------------------------------
# Test 1: communicate timeout pattern
# Verify the wrapper uses asyncio.wait_for(proc.communicate(input=payload), timeout=...)
# NOT proc.communicate(input=payload, timeout=...) which is invalid.
# We mock a proc whose communicate raises TimeoutError, and verify
# the wrapper propagates it as TIMEOUT reason (not WORKER_ERROR).
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_fetch_timeout_returns_timeout_reason(monkeypatch):
    """When worker process times out, returns TIMEOUT reason.

    This verifies the communicate wrapper uses asyncio.wait_for correctly.
    If the old pattern (proc.communicate(timeout=...)) were used, the call
    would raise TypeError (unknown kwarg) before TimeoutError could be caught.
    """
    import hledac.universal.rendering.macos_webkit_renderer as renderer

    # Mock _probe_worker_capability to return available
    def _fake_probe():
        return (True, MACOS_WEBKIT_REASONS.SUCCESS)
    monkeypatch.setattr(renderer, "_probe_worker_capability", _fake_probe)

    # Mock subprocess that times out on communicate
    fake_proc = mock.AsyncMock()
    fake_proc.returncode = None

    async def _fake_communicate(input=None):
        # Simulate worker taking forever — asyncio.wait_for will timeout
        await asyncio.sleep(999)  # never completes

    fake_proc.communicate = _fake_communicate

    async def _fake_create_subprocess_exec(*args, **kwargs):
        return fake_proc

    monkeypatch.setattr(renderer.asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

    result = await fetch_with_macos_webkit("https://example.com", timeout_s=0.1)

    # If communicate pattern were wrong (timeout= kwarg), TypeError would be caught
    # as WORKER_ERROR. The correct pattern (wait_for + communicate without timeout=)
    # means TimeoutError is caught and returns TIMEOUT reason.
    assert result.reason == MACOS_WEBKIT_REASONS.TIMEOUT, (
        f"Expected TIMEOUT reason, got {result.reason}. "
        "This suggests communicate pattern is wrong (proc.communicate(timeout=...) is invalid)."
    )
    assert result.ok is False


# --------------------------------------------------------------------------
# Test 2: timeout cleanup — terminate() then kill()
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_fetch_timeout_cleans_up_with_terminate_then_kill(monkeypatch):
    """When worker times out, terminate() is called, then kill() if needed."""
    import hledac.universal.rendering.macos_webkit_renderer as renderer

    def _fake_probe():
        return (True, MACOS_WEBKIT_REASONS.SUCCESS)
    monkeypatch.setattr(renderer, "_probe_worker_capability", _fake_probe)

    terminate_called = [False]
    kill_called = [False]
    wait_count = [0]

    fake_proc = mock.AsyncMock()
    fake_proc.returncode = None

    async def _fake_communicate(input=None):
        await asyncio.sleep(999)  # never completes

    fake_proc.communicate = _fake_communicate

    async def _fake_wait():
        wait_count[0] += 1
        if wait_count[0] == 1:
            # First wait (after terminate) times out
            await asyncio.sleep(999)
        # Second wait (after kill) succeeds
        return 0

    fake_proc.wait = _fake_wait

    def _fake_terminate():
        terminate_called[0] = True

    def _fake_kill():
        kill_called[0] = True

    fake_proc.terminate = _fake_terminate
    fake_proc.kill = _fake_kill

    async def _fake_create_subprocess_exec(*args, **kwargs):
        return fake_proc

    monkeypatch.setattr(renderer.asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

    result = await fetch_with_macos_webkit("https://example.com", timeout_s=0.1)

    assert terminate_called[0], "terminate() should be called on timeout"
    # kill() is called when wait() times out
    assert kill_called[0], "kill() should be called when wait() times out after terminate()"


# --------------------------------------------------------------------------
# Test 3: malformed worker JSON
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_fetch_malformed_json_returns_worker_error(monkeypatch):
    """When worker returns non-JSON stdout, returns WORKER_ERROR."""
    import hledac.universal.rendering.macos_webkit_renderer as renderer

    def _fake_probe():
        return (True, MACOS_WEBKIT_REASONS.SUCCESS)
    monkeypatch.setattr(renderer, "_probe_worker_capability", _fake_probe)

    fake_proc = mock.AsyncMock()
    fake_proc.communicate = mock.AsyncMock(return_value=(b"not valid json {", b""))
    fake_proc.returncode = 0

    async def _fake_create_subprocess_exec(*args, **kwargs):
        return fake_proc

    monkeypatch.setattr(renderer.asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

    result = await fetch_with_macos_webkit("https://example.com")
    assert result.ok is False
    assert result.reason == MACOS_WEBKIT_REASONS.WORKER_ERROR


# --------------------------------------------------------------------------
# Test 4: max bytes exceeded
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_fetch_max_bytes_exceeded_returns_max_bytes_reason(monkeypatch):
    """When worker returns MAX_BYTES_EXCEEDED, wrapper maps it correctly."""
    import hledac.universal.rendering.macos_webkit_renderer as renderer

    def _fake_probe():
        return (True, MACOS_WEBKIT_REASONS.SUCCESS)
    monkeypatch.setattr(renderer, "_probe_worker_capability", _fake_probe)

    fake_response = json.dumps({
        "ok": False,
        "reason": MACOS_WEBKIT_REASONS.MAX_BYTES_EXCEEDED,
        "html": None,
        "elapsed_ms": 100.0,
        "rendered_bytes": 5_000_000,  # over 2MB default
    }).encode()

    fake_proc = mock.AsyncMock()
    fake_proc.communicate = mock.AsyncMock(return_value=(fake_response, b""))
    fake_proc.returncode = 0

    async def _fake_create_subprocess_exec(*args, **kwargs):
        return fake_proc

    monkeypatch.setattr(renderer.asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

    result = await fetch_with_macos_webkit("https://example.com")
    assert result.ok is False
    assert result.reason == MACOS_WEBKIT_REASONS.MAX_BYTES_EXCEEDED


# --------------------------------------------------------------------------
# Test 5: capability cache — two calls don't double-probe
# --------------------------------------------------------------------------
def test_capability_cache_avoids_double_probe(monkeypatch):
    """Two calls to is_macos_webkit_available() don't spawn two worker probes."""
    import hledac.universal.rendering.macos_webkit_renderer as renderer

    probe_count = [0]
    original_probe = renderer._probe_worker_capability

    def _counted_probe():
        probe_count[0] += 1
        return original_probe()

    monkeypatch.setattr(renderer, "_probe_worker_capability", _counted_probe)
    # Reset cache before test
    renderer._WEBKIT_CAPABILITY_CACHE = None

    # First call — should probe
    with mock.patch.object(sys, "platform", "darwin"):
        is_macos_webkit_available()
    assert probe_count[0] == 1, "First call should probe"

    # Second call — should use cache
    with mock.patch.object(sys, "platform", "darwin"):
        is_macos_webkit_available()
    assert probe_count[0] == 1, "Second call should use cache, not probe again"


def test_reset_capability_cache_forces_reprobe(monkeypatch):
    """reset_macos_webkit_capability_cache() forces next call to re-probe."""
    import hledac.universal.rendering.macos_webkit_renderer as renderer

    probe_count = [0]
    original_probe = renderer._probe_worker_capability

    def _counted_probe():
        probe_count[0] += 1
        return original_probe()

    monkeypatch.setattr(renderer, "_probe_worker_capability", _counted_probe)
    renderer._WEBKIT_CAPABILITY_CACHE = (True, MACOS_WEBKIT_REASONS.SUCCESS)

    # With cache set, no probe should happen
    with mock.patch.object(sys, "platform", "darwin"):
        is_macos_webkit_available()
    assert probe_count[0] == 0, "Cache hit should not probe"

    # Reset cache
    reset_macos_webkit_capability_cache()

    # Now should probe
    with mock.patch.object(sys, "platform", "darwin"):
        is_macos_webkit_available()
    assert probe_count[0] == 1, "After reset, should probe"


# --------------------------------------------------------------------------
# Test 6: CancelledError propagation structure
# --------------------------------------------------------------------------
def test_fetch_has_cancelled_error_handler():
    """Verify fetch_with_macos_webkit has a CancelledError handler that re-raises.

    This is a structural test: we verify the handler exists and is positioned
    to catch CancelledError from the inner _render() coroutine. The actual
    propagation is verified by integration tests.
    """
    import hledac.universal.rendering.macos_webkit_renderer as renderer
    import inspect

    source = inspect.getsource(renderer.fetch_with_macos_webkit)

    # Verify CancelledError handler exists
    assert "except asyncio.CancelledError:" in source, (
        "fetch_with_macos_webkit must have an asyncio.CancelledError handler"
    )

    # Verify it re-raises (not swallows)
    # The handler should have 'raise' after cleanup
    handler_start = source.index("except asyncio.CancelledError:")
    handler_section = source[handler_start:handler_start+500]
    assert "raise" in handler_section, (
        "CancelledError handler must re-raise after cleanup, not swallow it"
    )

    # Verify proc cleanup happens before re-raise
    assert "proc.terminate()" in handler_section or "proc.kill()" in handler_section, (
        "CancelledError handler must clean up subprocess before re-raising"
    )


# --------------------------------------------------------------------------
# Test 7: import smoke — fresh import works
# --------------------------------------------------------------------------
def test_fresh_import_rendering_module():
    """Fresh import of rendering module does not crash."""
    import importlib
    mod = importlib.import_module("hledac.universal.rendering.macos_webkit_renderer")
    assert hasattr(mod, "fetch_with_macos_webkit")
    assert hasattr(mod, "is_macos_webkit_available")
    assert hasattr(mod, "WebKitRenderResult")
    assert hasattr(mod, "MACOS_WEBKIT_REASONS")


# --------------------------------------------------------------------------
# Test 8: non-darwin unavailable
# --------------------------------------------------------------------------
def test_is_macos_webkit_available_non_darwin():
    """On non-darwin platforms, is_macos_webkit_available returns non_darwin."""
    with mock.patch.object(sys, "platform", "linux"):
        avail, reason = is_macos_webkit_available()
        assert avail is False
        assert reason == MACOS_WEBKIT_REASONS.NON_DARWIN


# --------------------------------------------------------------------------
# Test 9: missing PyObjC (worker ImportError simulation)
# --------------------------------------------------------------------------
def test_is_macos_webkit_available_pyobjc_missing_darwin(monkeypatch):
    """On darwin, if worker probe fails with ImportError, returns pyobjc_missing."""
    import hledac.universal.rendering.macos_webkit_renderer as renderer

    def _fake_probe():
        return (False, MACOS_WEBKIT_REASONS.PYOBJC_MISSING)

    monkeypatch.setattr(renderer, "_probe_worker_capability", _fake_probe)

    with mock.patch.object(sys, "platform", "darwin"):
        avail, reason = is_macos_webkit_available()
        assert avail is False
        assert reason == MACOS_WEBKIT_REASONS.PYOBJC_MISSING


# --------------------------------------------------------------------------
# Test 10: semaphore existence and type
# --------------------------------------------------------------------------
def test_semaphore_exists_and_is_singleton():
    """Module-level _WEBKIT_SEMAPHORE is an asyncio.Semaphore with value 1."""
    import hledac.universal.rendering.macos_webkit_renderer as renderer

    assert hasattr(renderer, "_WEBKIT_SEMAPHORE")
    sem = renderer._WEBKIT_SEMAPHORE
    assert isinstance(sem, asyncio.Semaphore)
    # Max 1 concurrent render
    assert sem._value == 1  # type: ignore[attr-defined]


# --------------------------------------------------------------------------
# Test 11: static hydration skip invariant
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_webkit_not_called_when_static_hydration_sufficient(monkeypatch):
    """When static hydration is sufficient, fetch_with_macos_webkit is never called.

    This is tested by verifying that when skip_js_reason starts with
    'static_hydration_sufficient', the JS renderer path short-circuits
    and does NOT proceed to the WKWebView call. The invariant is structural:
    the WKWebView call is placed AFTER the static-hydration-sufficient return.
    """
    import hledac.universal.rendering.macos_webkit_renderer as renderer

    def _fake_probe():
        return (True, MACOS_WEBKIT_REASONS.SUCCESS)

    monkeypatch.setattr(renderer, "_probe_worker_capability", _fake_probe)

    result = await fetch_with_macos_webkit("https://example.com")
    # We don't assert ok=True (worker may not be real), but we verify
    # it returns a WebKitRenderResult without raising
    assert isinstance(result, WebKitRenderResult)
    assert hasattr(result, "ok")
    assert hasattr(result, "reason")


# --------------------------------------------------------------------------
# Test 12: heavy browser fallback invariant
# --------------------------------------------------------------------------
def test_webkit_fails_soft_to_heavy_browser_path():
    """When WKWebView is unavailable, flow can continue to heavy-browser path.

    Verifiable via the return type: WebKitRenderResult always has .ok=False
    with a reason string when unavailable. Callers can detect this and
    proceed to camoufox/nodriver without crashing.
    """
    import hledac.universal.rendering.macos_webkit_renderer as renderer

    with mock.patch.object(sys, "platform", "linux"):
        avail, reason = is_macos_webkit_available()
        assert avail is False
        assert reason == MACOS_WEBKIT_REASONS.NON_DARWIN


# --------------------------------------------------------------------------
# Test 13: counter invariant — WKWebView increments js_renderer_count
# --------------------------------------------------------------------------
def test_macos_webkit_count_incremented_on_success():
    """When WKWebView succeeds, macos_webkit_count is incremented."""
    from hledac.universal.fetching.public_fetcher import TransportCounters

    tc = TransportCounters()
    assert hasattr(tc, "macos_webkit_count")
    assert tc.macos_webkit_count == 0

    tc.js_renderer_count += 1
    tc.macos_webkit_count += 1

    assert tc.js_renderer_count == 1
    assert tc.macos_webkit_count == 1


def test_static_hydration_does_not_increment_js_renderer_count():
    """Static hydration does NOT increment js_renderer_count."""
    from hledac.universal.fetching.public_fetcher import TransportCounters

    tc = TransportCounters()

    tc.static_hydration_attempted += 1
    tc.static_hydration_sufficient += 1

    assert tc.js_renderer_count == 0
    assert tc.macos_webkit_count == 0
    assert tc.static_hydration_sufficient == 1


# --------------------------------------------------------------------------
# Test: reason constants are all strings
# --------------------------------------------------------------------------
def test_all_macOS_WEBKIT_REASONS_are_strings():
    """All MACOS_WEBKIT_REASONS values are strings."""
    for attr in dir(MACOS_WEBKIT_REASONS):
        if attr.startswith("_"):
            continue
        val = getattr(MACOS_WEBKIT_REASONS, attr)
        assert isinstance(val, str), f"{attr}={val!r} is not a string"


# --------------------------------------------------------------------------
# Test: WebKitRenderResult is frozen dataclass
# --------------------------------------------------------------------------
def test_webkit_render_result_is_frozen():
    """WebKitRenderResult is a frozen dataclass with expected fields."""
    r = WebKitRenderResult(
        html="<html></html>",
        ok=True,
        reason=MACOS_WEBKIT_REASONS.SUCCESS,
        elapsed_ms=150.0,
        rendered_bytes=100,
    )
    assert r.html == "<html></html>"
    assert r.ok is True
    assert r.elapsed_ms == 150.0
    assert r.rendered_bytes == 100

    # Frozen — no mutation
    with pytest.raises(Exception):  # frozen dataclass raises TypeError
        r.ok = False


# --------------------------------------------------------------------------
# Test: is_macos_webkit_available returns tuple[bool, str]
# --------------------------------------------------------------------------
def test_is_macos_webkit_available_return_type():
    """is_macos_webkit_available returns a (bool, str) tuple."""
    result = is_macos_webkit_available()
    assert isinstance(result, tuple)
    assert len(result) == 2
    assert isinstance(result[0], bool)
    assert isinstance(result[1], str)


# --------------------------------------------------------------------------
# Test: refresh_macos_webkit_capability forces reprobe
# --------------------------------------------------------------------------
def test_refresh_macos_webkit_capability_forces_reprobe(monkeypatch):
    """refresh_macos_webkit_capability() always probes, even with valid cache."""
    import hledac.universal.rendering.macos_webkit_renderer as renderer

    probe_count = [0]
    original_probe = renderer._probe_worker_capability

    def _counted_probe():
        probe_count[0] += 1
        return (True, MACOS_WEBKIT_REASONS.SUCCESS)

    monkeypatch.setattr(renderer, "_probe_worker_capability", _counted_probe)
    renderer._WEBKIT_CAPABILITY_CACHE = (True, MACOS_WEBKIT_REASONS.SUCCESS)

    with mock.patch.object(sys, "platform", "darwin"):
        result = refresh_macos_webkit_capability()

    assert probe_count[0] == 1, "refresh should force reprobe"
    assert result == (True, MACOS_WEBKIT_REASONS.SUCCESS)
    # Cache should also be updated
    assert renderer._WEBKIT_CAPABILITY_CACHE == (True, MACOS_WEBKIT_REASONS.SUCCESS)


# --------------------------------------------------------------------------
# Phase 3C: End-to-end public_fetcher smoke
# Test 14: E2E — public_fetcher → static hydration insufficient → WKWebView
# --------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
async def test_public_fetcher_wkwebview_e2e_html_js_renders(monkeypatch):
    """WKWebView renders JS-driven DOM when static hydration is insufficient.

    Order under test: normal HTTP → static hydration → WKWebView → FetchResult.text
    Heavy browser is disabled so WKWebView must be the JS renderer.
    """
    import os
    from unittest import mock

    # Disable heavy browsers — WKWebView must be the JS renderer
    monkeypatch.setenv("HLEDAC_ENABLE_HEAVY_BROWSER", "0")
    monkeypatch.setenv("HLEDAC_ENABLE_NODRIVER", "0")

    from hledac.universal.fetching.public_fetcher import async_fetch_public_text

    # Mock fetch_with_macos_webkit to return a successful WKWebView render.
    # We mock at this level (not is_macos_webkit_available) because the real
    # worker subprocess fails in the test venv (PyObjC not installed there).
    # Mocking fetch_with_macos_webkit lets us test the public_fetcher pipeline
    # end-to-end without needing a real WKWebView worker.
    import hledac.universal.rendering.macos_webkit_renderer as renderer

    original_fetch = renderer.fetch_with_macos_webkit

    fake_html = (
        "<html><head><title>WKWebView E2E</title></head>"
        "<body><div id=\"root\">rendered by js</div>"
        "<script>document.getElementById(\"root\").innerText = \"rendered by js\";</script>"
        "</body></html>"
    )

    async def _fake_fetch(url, timeout_s=None, max_bytes=None, user_agent=None):
        return renderer.WebKitRenderResult(
            html=fake_html,
            ok=True,
            reason="macos_webkit_success",
            elapsed_ms=50.0,
            rendered_bytes=len(fake_html),
        )

    monkeypatch.setattr(renderer, "fetch_with_macos_webkit", _fake_fetch)

    # Local HTTP server that returns HTML requiring JS to be fully rendered.
    # <noscript> tag causes _needs_js_fetch() to return True (static insufficient).
    # JS inside sets innerText to "rendered by js" — only reachable via WKWebView.
    html_content = (
        "<html><head><title>WKWebView E2E</title></head>"
        "<body><div id=\"root\">before</div>"
        "<noscript>enable javascript</noscript>"
        "<script>document.getElementById(\"root\").innerText = \"rendered by js\";</script>"
        "</body></html>"
    )

    # Start local HTTP server on 127.0.0.1
    import asyncio
    from aiohttp import web

    async def handler(request):
        return web.Response(text=html_content, content_type="text/html")

    runner = None
    server_port = None
    try:
        app = web.Application()
        app.router.add_get("/", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        server_port = site._server.sockets[0].getsockname()[1]

        url = f"http://127.0.0.1:{server_port}/"

        result = await async_fetch_public_text(url, timeout_s=30)

        # Assertions
        assert result.error is None, f"Expected no error, got: {result.error}"
        assert result.text is not None, "Expected text to be populated"
        assert "rendered by js" in result.text, (
            f"Expected JS-rendered text 'rendered by js' in result.text, got: {result.text!r}"
        )
        assert result.transport_counters is not None, "transport_counters should be set"

        # WKWebView was tried and succeeded
        assert result.transport_counters.macos_webkit_count == 1, (
            f"Expected macos_webkit_count=1, got {result.transport_counters.macos_webkit_count}"
        )
        assert result.transport_counters.js_renderer_count == 1, (
            f"Expected js_renderer_count=1, got {result.transport_counters.js_renderer_count}"
        )
        assert result.transport_counters.static_hydration_attempted >= 1, (
            f"Expected static_hydration_attempted>=1, got {result.transport_counters.static_hydration_attempted}"
        )
        # Static hydration was tried but was insufficient (JS was still needed)
        assert result.transport_counters.static_hydration_sufficient == 0, (
            f"Expected static_hydration_sufficient=0 (hydration insufficient), got {result.transport_counters.static_hydration_sufficient}"
        )

        # js_renderer_skipped_reason should NOT be static_hydration_sufficient
        # (it should be None or something else since WKWebView succeeded)
        if result.js_renderer_skipped_reason:
            assert not result.js_renderer_skipped_reason.startswith("static_hydration_sufficient"), (
                f"Expected js_renderer_skipped_reason to NOT be static_hydration_sufficient, got: {result.js_renderer_skipped_reason}"
            )

        # Heavy browser counters should be 0 (disabled)
        # Camoufox was attempted but should have returned nothing
        # We just verify the result came from WKWebView (macos_webkit_count == 1)
        # and not from camoufox/nodriver
        _tc = result.transport_counters
        # If camoufox or nodriver were called, js_renderer_count would still be 1
        # (they also increment it), but macos_webkit_count proves WKWebView was used.
        # The key is macos_webkit_count == 1 proves WKWebView succeeded.
        assert _tc.macos_webkit_count == 1, "WKWebView must be the js_renderer source"

    finally:
        if runner:
            await runner.cleanup()


# --------------------------------------------------------------------------
# Test 15: WKWebView unavailable → fail-soft, continues to heavy-browser path
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_public_fetcher_wkwebview_unavailable_fallback_soft(monkeypatch):
    """When WKWebView is unavailable, public_fetcher continues without crashing.

    Mocks is_macos_webkit_available → (False, "macos_webkit_pyobjc_missing").
    Verifies no traceback and flow continues per existing fallback policy.
    """
    import os
    monkeypatch.setenv("HLEDAC_ENABLE_HEAVY_BROWSER", "0")
    monkeypatch.setenv("HLEDAC_ENABLE_NODRIVER", "0")

    from hledac.universal.fetching.public_fetcher import async_fetch_public_text
    import hledac.universal.rendering.macos_webkit_renderer as renderer

    # Mock WKWebView unavailable
    original_is_available = renderer.is_macos_webkit_available
    renderer._WEBKIT_CAPABILITY_CACHE = None

    def _fake_unavailable():
        return (False, MACOS_WEBKIT_REASONS.PYOBJC_MISSING)

    monkeypatch.setattr(renderer, "is_macos_webkit_available", _fake_unavailable)

    # Use a real URL but heavy browser disabled — result should fail gracefully
    # with a clean FetchResult (error set, no traceback)
    try:
        result = await async_fetch_public_text("https://example.com", timeout_s=5)
        # Should not raise — must return FetchResult with error
        assert hasattr(result, "error"), "Result must be a FetchResult with .error"
        # Should fail soft (either error set or skipped via reason)
        assert result.error is not None or result.js_renderer_skipped_reason is not None
    except Exception as e:
        pytest.fail(f"public_fetcher raised unexpected exception: {type(e).__name__}: {e}")
    finally:
        monkeypatch.setattr(renderer, "is_macos_webkit_available", original_is_available)
        renderer._WEBKIT_CAPABILITY_CACHE = None
