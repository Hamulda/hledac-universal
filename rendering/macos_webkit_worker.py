#!/usr/bin/env python3
# rendering/macos_webkit_worker.py
# Sprint F214AC: macOS WKWebView Subprocess JS Renderer — worker process
"""Standalone subprocess worker for macOS WKWebView JS rendering.

This script runs as an isolated subprocess per render. It receives a JSON
payload via stdin, creates a WKWebView, loads the URL, waits for JS execution,
and returns the rendered HTML as JSON on stdout.

Usage:
    python -m hledac.universal.rendering.macos_webkit_worker

The worker:
    - Receives JSON on stdin: {action, url, timeout_s, max_bytes, user_agent}
    - action="render": full render
    - action="capability_check": just probe PyObjC/WebKit availability
    - Returns JSON on stdout: {ok, html, reason, elapsed_ms, rendered_bytes}
    - Exits after each render (no persistent daemon)

Invariant: non-persistent, no shared cookies, no screenshots, no disk storage.
"""

from __future__ import annotations

import json
import sys
import time


def _build_response(
    ok: bool,
    reason: str,
    html: str | None = None,
    elapsed_ms: float = 0.0,
    rendered_bytes: int = 0,
) -> bytes:
    """Serialize a render response to JSON bytes."""
    return json.dumps(
        {
            "ok": ok,
            "html": html,
            "reason": reason,
            "elapsed_ms": elapsed_ms,
            "rendered_bytes": rendered_bytes,
        },
        ensure_ascii=False,
    ).encode("utf-8")


def _do_capability_check() -> bytes:
    """Check if PyObjC and WebKit are importable, return status as JSON."""
    try:
        # Try importing the WebKit framework via PyObjC
        from objc import dyld_framework
        import os

        # Attempt to load WebKit — this fails if PyObjC or WebKit is missing
        path = "/System/Library/Frameworks/WebKit.framework"
        if not os.path.exists(path):
            return _build_response(False, "macos_webkit_unavailable")

        # Use NSClassFromString to probe WKWebView without fully initializing
        dyld_framework("/System/Library/Frameworks/AppKit.framework", "AppKit")
        dyld_framework(path, "WebKit")

        from objc import nil
        from Foundation import NSBundle
        bundle = NSBundle.bundleWithPath_(path)
        wk_class = bundle.classNamed_("WKWebView")
        if wk_class is None or wk_class == nil:
            return _build_response(False, "macos_webkit_pyobjc_missing")

        # WKWebView is available
        return _build_response(True, "macos_webkit_success")
    except Exception as e:
        # Any import/framework error means WebKit is not available
        return _build_response(False, "macos_webkit_pyobjc_missing")


def _nsclass(name: str):
    """Short-hand for NSClassFromString — look up a Cocoa class by name."""
    from objc import nil
    from objc._objc import _loadBundle
    from Foundation import NSBundle

    bundle = NSBundle.bundleWithPath_("/System/Library/Frameworks/WebKit.framework")
    if bundle is None:
        bundle = NSBundle.mainBundle()
    return bundle.classNamed_(name)


def _run_loop_until_condition(
    condition_fn,  # callable returning bool
    timeout_s: float,
    poll_interval: float = 0.05,
) -> bool:
    """Pump NSRunLoop until condition_fn() returns True or deadline passes.

    Uses NSRunLoop.currentRunLoop().runMode_beforeDate_() to pump the run loop
    during the wait, allowing async callbacks (like WKWebView navigation)
    to complete.
    """
    from Foundation import NSRunLoop, NSDate

    deadline = time.monotonic() + timeout_s
    run_loop = NSRunLoop.currentRunLoop()

    while time.monotonic() < deadline:
        if condition_fn():
            return True
        # Run the run loop for a short period, allowing events to be processed
        # This pumps the run loop without blocking indefinitely
        run_loop.runMode_beforeDate_(
            getattr(NSRunLoop, "NSDefaultRunLoopMode", "kCFRunLoopDefaultMode"),
            NSDate.dateWithTimeIntervalSinceNow_(poll_interval),
        )
    return False


def _do_render(payload: dict) -> bytes:
    """Execute a WKWebView render from the payload dict."""
    url = payload.get("url", "")
    timeout_s = float(payload.get("timeout_s", 10.0))
    max_bytes = min(int(payload.get("max_bytes", 2_000_000)), 10_000_000)

    if not url:
        return _build_response(False, "macos_webkit_worker_error", elapsed_ms=0.0)

    t0 = time.monotonic()

    try:
        from objc import nil, dyld_framework
        from Foundation import (
            NSURL,
            NSURLRequest,
            NSBundle,
            NSRunLoop,
            NSDate,
        )
        from WebKit import WKWebView, WKWebViewConfiguration, WKUserContentController

        # Load frameworks (WKWebView requires AppKit + WebKit)
        dyld_framework("/System/Library/Frameworks/AppKit.framework", "AppKit")
        dyld_framework("/System/Library/Frameworks/WebKit.framework", "WebKit")

        # Create WKWebView — non-persistent session (no shared cookies)
        config = WKWebViewConfiguration()

        # Non-persistent data store: no cookies, no cache persisted to disk
        try:
            from WebKit import WKWebsiteDataStore, WKProcessPool
            config.setWebsiteDataStore_(WKWebsiteDataStore.nonPersistentDataStore())
            config.setProcessPool_(WKProcessPool.alloc().init())
        except Exception:
            # If WKProcessPool not available, fall back to fresh pool via setProcessPool_(None)
            # This isolates the process pool but doesn't set non-persistent data store
            try:
                config.setProcessPool_(None)  # fresh process pool = isolated
            except Exception:
                pass  # fail-soft: continue without explicit isolation

        # User-Agent override
        user_agent = payload.get("user_agent")
        if user_agent:
            config.setDefaultWebpagePreferences_(None)

        webview = WKWebView.alloc().initWithFrame_configuration_(
            ((0, 0), (800, 600)),
            config,
        )

        # Set custom User-Agent if provided
        if user_agent:
            webview.setCustomUserAgent_(user_agent)

        # Load URL
        nsurl = NSURL.URLWithString_(url)
        if nsurl is None or nsurl == nil:
            return _build_response(False, "macos_webkit_worker_error", elapsed_ms=0.0)

        request = NSURLRequest.requestWithURL_(nsurl)
        webview.loadRequest_(request)

        # Wait for load finish using run-loop pump (not time.sleep)
        # This allows WKWebView navigation callbacks to be processed
        def is_not_loading() -> bool:
            return webview.isLoading() is False

        _run_loop_until_condition(is_not_loading, timeout_s, poll_interval=0.05)

        # Give a tiny settle window via run loop (not time.sleep)
        _run_loop_until_condition(lambda: True, 0.1, poll_interval=0.01)

        # Evaluate JS via completion handler pattern (async, not sync polling)
        js_code = "document.documentElement.outerHTML"

        # State shared between completion handler and run loop
        html_result = [None]  # list to allow mutation in closure
        error_result = [None]
        completion_received = [False]

        # Completion handler: called when JS evaluation completes
        def completion_handler(result, error):
            if error is not None and error != nil:
                error_result[0] = error
            elif result is not None and result != nil:
                if isinstance(result, tuple) and len(result) == 2:
                    html_result[0] = result[0]
                else:
                    html_result[0] = result
            completion_received[0] = True

        # Initiate async JS evaluation
        # evaluateJavaScript_completionHandler_ returns immediately;
        # completion_handler is called asynchronously with (value, error)
        webview.evaluateJavaScript_completionHandler_(js_code, completion_handler)

        # Pump run loop until completion or timeout
        deadline = time.monotonic() + 2.0  # short timeout for JS eval
        run_loop = NSRunLoop.currentRunLoop()
        while not completion_received[0] and time.monotonic() < deadline:
            run_loop.runMode_beforeDate_(
                getattr(NSRunLoop, "NSDefaultRunLoopMode", "kCFRunLoopDefaultMode"),
                NSDate.dateWithTimeIntervalSinceNow_(0.05),
            )

        html = html_result[0]
        error = error_result[0]

        elapsed_ms = (time.monotonic() - t0) * 1000

        # Surface any JS evaluation error
        if error is not None and error != nil:
            return _build_response(
                False, "macos_webkit_worker_error",
                elapsed_ms=elapsed_ms,
                rendered_bytes=0,
            )

        if html is None or html == "":
            return _build_response(
                False, "macos_webkit_empty",
                elapsed_ms=elapsed_ms,
                rendered_bytes=0,
            )

        # Truncate if over max_bytes (fail-soft)
        html_str = str(html)
        rendered_bytes = len(html_str.encode("utf-8"))
        if rendered_bytes > max_bytes:
            return _build_response(
                False, "macos_webkit_max_bytes_exceeded",
                html=None,
                elapsed_ms=elapsed_ms,
                rendered_bytes=rendered_bytes,
            )

        return _build_response(
            True, "macos_webkit_success",
            html=html_str[:max_bytes],
            elapsed_ms=elapsed_ms,
            rendered_bytes=min(rendered_bytes, max_bytes),
        )

    except Exception as e:
        elapsed_ms = (time.monotonic() - t0) * 1000
        # Surface module-level errors cleanly (not full traceback)
        exc_name = type(e).__name__
        if "ModuleNotFoundError" in exc_name or "ImportError" in exc_name:
            return _build_response(
                False, "macos_webkit_pyobjc_missing",
                elapsed_ms=elapsed_ms,
            )
        return _build_response(
            False, "macos_webkit_worker_error",
            elapsed_ms=elapsed_ms,
        )


def main() -> None:
    """Read JSON from stdin, execute requested action, write JSON to stdout."""
    # Read all of stdin (body is small JSON, < 1KB)
    try:
        stdin_data = sys.stdin.buffer.read()
    except Exception:
        sys.stdout.buffer.write(_build_response(False, "macos_webkit_worker_error"))
        sys.stdout.buffer.flush()
        return

    if not stdin_data:
        sys.stdout.buffer.write(_build_response(False, "macos_webkit_worker_error"))
        sys.stdout.buffer.flush()
        return

    try:
        payload = json.loads(stdin_data.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        sys.stdout.buffer.write(_build_response(False, "macos_webkit_worker_error"))
        sys.stdout.buffer.flush()
        return

    action = payload.get("action", "")

    if action == "capability_check":
        response = _do_capability_check()
    elif action == "render":
        response = _do_render(payload)
    else:
        response = _build_response(False, "macos_webkit_worker_error")

    sys.stdout.buffer.write(response)
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
