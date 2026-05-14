# rendering/macos_webkit_renderer.py
# Sprint F214AC: macOS WKWebView Subprocess JS Renderer
# Isolated subprocess, fail-soft, macOS-only, no Chrome/Chromium/Playwright.
"""Async wrapper for macOS WKWebView JS rendering via isolated subprocess.

This module provides a lightweight JS renderer that uses the native macOS
WebKit (WKWebView) instead of Chrome/Chromium/Playwright. It runs as an
isolated subprocess per render — no persistent daemon, no browser pool.

Platform constraints:
    - macOS only (darwin). On other platforms, is_macos_webkit_available() returns False.
    - PyObjC/WebKit is optional — checked via worker capability probe.
    - Max 1 concurrent render via module-level semaphore.
    - Timeout default 10s, max_bytes default 2MB.

Integration order:
    1. normal HTTP fetch
    2. static hydration extractor
    3. macOS WKWebView renderer  ← this module
    4. camoufox/nodriver only if explicitly enabled and available
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from dataclasses import dataclass
from typing import Final

# Semaphore: max 1 concurrent WKWebView render (M1 8GB-safe)
_WEBKIT_SEMAPHORE: Final[asyncio.Semaphore] = asyncio.Semaphore(1)

# Timeout and size defaults
_DEFAULT_TIMEOUT_S: Final[float] = 10.0
_DEFAULT_MAX_BYTES: Final[int] = 2_000_000

# Process-level capability cache — avoids worker probe subprocess per render
# Format: tuple[bool, str] | None — None means uncached
_WEBKIT_CAPABILITY_CACHE: tuple[bool, str] | None = None


def reset_macos_webkit_capability_cache() -> None:
    """Reset the capability cache — forces next render to re-probe worker."""
    global _WEBKIT_CAPABILITY_CACHE
    _WEBKIT_CAPABILITY_CACHE = None


def refresh_macos_webkit_capability() -> tuple[bool, str]:
    """Force a fresh capability probe, update cache, return result."""
    global _WEBKIT_CAPABILITY_CACHE
    _WEBKIT_CAPABILITY_CACHE = _probe_worker_capability()
    return _WEBKIT_CAPABILITY_CACHE


# --------------------------------------------------------------------------
# Reason taxonomy — used in WebKitRenderResult.reason and telemetry
# --------------------------------------------------------------------------
class MACOS_WEBKIT_REASONS:
    UNAVAILABLE = "macos_webkit_unavailable"
    NON_DARWIN = "macos_webkit_non_darwin"
    PYOBJC_MISSING = "macos_webkit_pyobjc_missing"
    TIMEOUT = "macos_webkit_timeout"
    WORKER_ERROR = "macos_webkit_worker_error"
    EMPTY = "macos_webkit_empty"
    SUCCESS = "macos_webkit_success"
    MAX_BYTES_EXCEEDED = "macos_webkit_max_bytes_exceeded"


@dataclass(frozen=True)
class WebKitRenderResult:
    """Result of a WKWebView render attempt.

    Always returned (never raises) — callers check .ok before using .html.
    """

    html: str | None
    ok: bool
    reason: str
    elapsed_ms: float
    rendered_bytes: int = 0


def is_macos_webkit_available() -> tuple[bool, str]:
    """Check if macOS WKWebView renderer is available on this platform.

    Returns:
        (True, "macos_webkit_success") if platform is Darwin and the worker
            subprocess can be spawned and responds to a capability probe.
        (False, reason) with one of MACOS_WEBKIT_REASONS values on any failure:
            - macos_webkit_non_darwin      : sys.platform != "darwin"
            - macos_webkit_pyobjc_missing  : PyObjC or WebKit not importable in worker

    This does NOT import PyObjC in the parent process — the check is
    delegated to the worker subprocess to avoid loading WebKit in the main process.
    """
    global _WEBKIT_CAPABILITY_CACHE

    if sys.platform != "darwin":
        return (False, MACOS_WEBKIT_REASONS.NON_DARWIN)

    # Use cache if available, otherwise probe and cache
    if _WEBKIT_CAPABILITY_CACHE is not None:
        return _WEBKIT_CAPABILITY_CACHE
    _WEBKIT_CAPABILITY_CACHE = _probe_worker_capability()
    return _WEBKIT_CAPABILITY_CACHE


def _probe_worker_capability() -> tuple[bool, str]:
    """Probe the worker subprocess for its capability.

    Spawns a minimal worker, sends a capability-check payload, returns
    (True, "macos_webkit_success") or (False, reason).

    This is called from is_macos_webkit_available() only — never from the
    hot render path.
    """
    try:
        proc = None

        async def _probe() -> tuple[bool, str]:
            nonlocal proc
            import os

            worker_path = os.path.join(
                os.path.dirname(__file__),
                "macos_webkit_worker.py",
            )
            if not os.path.isfile(worker_path):
                return (False, MACOS_WEBKIT_REASONS.UNAVAILABLE)

            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "hledac.universal.rendering.macos_webkit_worker",
                "--capability-check",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # Payload: just the action field — worker replies with its status
            payload = json.dumps({"action": "capability_check"}).encode()
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(input=payload),
                    timeout=5.0,
                )
            except asyncio.TimeoutError:
                if proc:
                    proc.terminate()
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=2.0)
                    except asyncio.TimeoutError:
                        proc.kill()
                        await proc.wait()
                return (False, MACOS_WEBKIT_REASONS.PYOBJC_MISSING)

            if proc.returncode != 0:
                # Worker failed — likely missing PyObjC/WebKit
                stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
                if "ModuleNotFoundError" in stderr_text or "ImportError" in stderr_text:
                    return (False, MACOS_WEBKIT_REASONS.PYOBJC_MISSING)
                return (False, MACOS_WEBKIT_REASONS.WORKER_ERROR)

            try:
                result = json.loads(stdout_bytes.decode("utf-8", errors="replace"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return (False, MACOS_WEBKIT_REASONS.WORKER_ERROR)

            if result.get("ok"):
                return (True, MACOS_WEBKIT_REASONS.SUCCESS)
            return (False, result.get("reason", MACOS_WEBKIT_REASONS.WORKER_ERROR))

        # Run probe — use existing loop if available (M1-safe), else fresh loop
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop — create a fresh one
            return asyncio.run(_probe())

        # Running loop exists — run probe in a separate thread to avoid
        # "cannot call running event loop" error when called from within
        # an already-running async context (e.g. inside fetch_with_macos_webkit)
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(asyncio.run, _probe())
            return future.result()

    except Exception:
        return (False, MACOS_WEBKIT_REASONS.UNAVAILABLE)


async def fetch_with_macos_webkit(
    url: str,
    *,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    user_agent: str | None = None,
) -> WebKitRenderResult:
    """Render a URL using macOS WKWebView via isolated subprocess.

    This is the main entry point — called from public_fetcher after static
    hydration was attempted but was insufficient.

    Args:
        url: URL to load in WKWebView.
        timeout_s: Seconds to wait for load finish (default 10).
        max_bytes: Max HTML bytes to return (default 2MB, hard cap 10MB).
        user_agent: Optional custom User-Agent string.

    Returns:
        WebKitRenderResult with .ok, .reason, .html, .elapsed_ms, .rendered_bytes.

    Never raises — all exceptions are caught and returned as failed WebKitRenderResult.

    Concurrency: guarded by module-level semaphore — max 1 concurrent render.
    """
    # Bounded max_bytes (prevent runaway allocation on M1)
    max_bytes = min(max_bytes, 10_000_000)

    # ---- Fast path: check availability first (cached check, O(1)) ------------
    avail, avail_reason = is_macos_webkit_available()
    if not avail:
        return WebKitRenderResult(
            html=None,
            ok=False,
            reason=avail_reason,
            elapsed_ms=0.0,
            rendered_bytes=0,
        )

    # ---- Semaphore gate: max 1 concurrent render ----------------------------
    async with _WEBKIT_SEMAPHORE:
        t0 = time.monotonic()

        try:
            proc = None

            async def _render() -> WebKitRenderResult:
                nonlocal proc

                import os

                worker_path = os.path.join(
                    os.path.dirname(__file__),
                    "macos_webkit_worker.py",
                )
                if not os.path.isfile(worker_path):
                    return WebKitRenderResult(
                        html=None,
                        ok=False,
                        reason=MACOS_WEBKIT_REASONS.UNAVAILABLE,
                        elapsed_ms=(time.monotonic() - t0) * 1000,
                        rendered_bytes=0,
                    )

                proc = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "hledac.universal.rendering.macos_webkit_worker",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )

                payload = json.dumps(
                    {
                        "action": "render",
                        "url": url,
                        "timeout_s": timeout_s,
                        "max_bytes": max_bytes,
                        "user_agent": user_agent,
                    },
                    ensure_ascii=False,
                ).encode("utf-8")

                try:
                    stdout_bytes, stderr_bytes = await asyncio.wait_for(
                        proc.communicate(input=payload),
                        timeout=timeout_s + 5.0,
                    )
                except asyncio.TimeoutError:
                    # Timeout on wait_for — worker is still alive, terminate it
                    if proc:
                        proc.terminate()
                        try:
                            await asyncio.wait_for(proc.wait(), timeout=2.0)
                        except asyncio.TimeoutError:
                            proc.kill()
                            await proc.wait()
                    elapsed_ms = (time.monotonic() - t0) * 1000
                    return WebKitRenderResult(
                        html=None,
                        ok=False,
                        reason=MACOS_WEBKIT_REASONS.TIMEOUT,
                        elapsed_ms=elapsed_ms,
                        rendered_bytes=0,
                    )

                # Check exit code
                if proc.returncode != 0:
                    stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
                    elapsed_ms = (time.monotonic() - t0) * 1000
                    # Detect PyObjC import failure from stderr
                    if "ModuleNotFoundError" in stderr_text or "ImportError" in stderr_text:
                        return WebKitRenderResult(
                            html=None,
                            ok=False,
                            reason=MACOS_WEBKIT_REASONS.PYOBJC_MISSING,
                            elapsed_ms=elapsed_ms,
                            rendered_bytes=0,
                        )
                    # Try to parse stderr as JSON — worker may have returned a
                    # structured failure (e.g. max_bytes exceeded) before exiting
                    try:
                        err_result = json.loads(stderr_text)
                        err_reason = err_result.get("reason", "")
                        if err_reason == MACOS_WEBKIT_REASONS.MAX_BYTES_EXCEEDED:
                            return WebKitRenderResult(
                                html=None,
                                ok=False,
                                reason=MACOS_WEBKIT_REASONS.MAX_BYTES_EXCEEDED,
                                elapsed_ms=elapsed_ms,
                                rendered_bytes=err_result.get("rendered_bytes", 0),
                            )
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        pass
                    return WebKitRenderResult(
                        html=None,
                        ok=False,
                        reason=MACOS_WEBKIT_REASONS.WORKER_ERROR,
                        elapsed_ms=elapsed_ms,
                        rendered_bytes=0,
                    )

                # Parse JSON response
                try:
                    result = json.loads(stdout_bytes.decode("utf-8", errors="replace"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    elapsed_ms = (time.monotonic() - t0) * 1000
                    return WebKitRenderResult(
                        html=None,
                        ok=False,
                        reason=MACOS_WEBKIT_REASONS.WORKER_ERROR,
                        elapsed_ms=elapsed_ms,
                        rendered_bytes=0,
                    )

                elapsed_ms = result.get("elapsed_ms", (time.monotonic() - t0) * 1000)
                ok_flag = result.get("ok", False)
                html = result.get("html") or None
                rendered_bytes = result.get("rendered_bytes", 0)
                reason = result.get("reason", MACOS_WEBKIT_REASONS.SUCCESS if ok_flag else MACOS_WEBKIT_REASONS.WORKER_ERROR)

                # When ok=False (e.g. max_bytes exceeded), pass through reason even if html is None
                if not ok_flag:
                    return WebKitRenderResult(
                        html=None,
                        ok=False,
                        reason=reason,
                        elapsed_ms=elapsed_ms,
                        rendered_bytes=rendered_bytes,
                    )

                # Handle empty HTML
                if not html or not html.strip():
                    return WebKitRenderResult(
                        html=None,
                        ok=False,
                        reason=MACOS_WEBKIT_REASONS.EMPTY,
                        elapsed_ms=elapsed_ms,
                        rendered_bytes=rendered_bytes,
                    )

                return WebKitRenderResult(
                    html=html,
                    ok=True,
                    reason=reason,
                    elapsed_ms=elapsed_ms,
                    rendered_bytes=rendered_bytes,
                )

            return await _render()

        except asyncio.CancelledError:
            # Propagate cancellation — never swallow it
            if proc:
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
            raise
        except Exception:
            # Catch everything else including CancelledError (already re-raised above),
            # KeyboardInterrupt, etc.
            elapsed_ms = (time.monotonic() - t0) * 1000
            return WebKitRenderResult(
                html=None,
                ok=False,
                reason=MACOS_WEBKIT_REASONS.WORKER_ERROR,
                elapsed_ms=elapsed_ms,
                rendered_bytes=0,
            )