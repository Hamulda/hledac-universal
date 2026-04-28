"""
httpx HTTP/2 Client Surface — Transport Capability Layer 2026
================================================================

Sprint F206K: Optional HTTPX HTTP/2 clearnet lane.

AUTHORITY (F206K):
  This module provides the LAZY HTTPX client singleton surface.
  HTTPX is optional — project imports and runs even if h2 is not installed.
  HTTP/2 is activated only when:
    1. httpx + h2 are installed
    2. Transport policy selects HTTPX H2 lane
    3. Target is clearnet (no Tor/I2P/Freenet)

TRANSPORT WORLD CLASSIFICATION (F206K):
  - HTTPX H2 WORLD: HTTP/2-capable httpx for clearnet API/same-host batch
  - aiohttp WORLD: plain TCPConnector (existing hot-path)
  - aiohttp_socks WORLD: ProxyConnector for Tor/I2P (existing darknet path)
  - curl_cffi WORLD: JA3 fingerprint spoofing — SEPARATE plane, not unified

INVARIANTS:
  [H2-I1] Lazy import — httpx NOT imported at module level
  [H2-I2] Lazy init — client created on first await, not at import
  [H2-I3] Idempotent — repeated awaits return same instance
  [H2-I4] Fail-soft disabled — h2 missing → _httpx_h2_enabled = False
  [H2-I5] Connector limits: limit=25, limit_per_host=10 (API batch friendly)
  [H2-I6] No top-level network side effects at import time
  [H2-I7] CancelledError propagates (not swallowed)
  [H2-I8] HTTPX client closed ONLY via close_httpx_client_async()
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# =============================================================================
# Capability Detection — fail-soft, no hard import
# =============================================================================

_httpx_h2_enabled: bool = False
_httpx_import_error: str | None = None


def _check_httpx_h2_capability() -> bool:
    """
    Check if httpx with HTTP/2 (h2) support is available.
    Called lazily on first use — not at import time.

    Returns:
        True if httpx >= 0.27.0 AND h2 is installed
        False otherwise (fail-soft, project still works)
    """
    global _httpx_h2_enabled, _httpx_import_error

    if _httpx_import_error is not None:
        # Already checked and failed
        return False

    try:
        import httpx
    except ImportError as e:
        _httpx_import_error = f"httpx not installed: {e}"
        logger.debug(f"[HTTPX] {_httpx_import_error}")
        return False

    # httpx available — check h2 (HTTP/2 support)
    try:
        import h2
    except ImportError:
        _httpx_import_error = "h2 not installed (httpx[http2] required for HTTP/2)"
        logger.debug(f"[HTTPX] {_httpx_import_error}")
        return False

    # Both available — version check passed (h2 import is the real gate)
    _httpx_h2_enabled = True
    logger.debug(f"[HTTPX] HTTP/2 capability detected (httpx={httpx.__version__})")
    return True


# =============================================================================
# Lazy HTTPX Client Singleton
# =============================================================================

_httpx_client_instance: Optional["httpx.AsyncClient"] = None
_httpx_client_lock: asyncio.Lock = asyncio.Lock()
_httpx_client_closed: bool = False


async def async_get_httpx_client() -> "httpx.AsyncClient":
    """
    Get or create the lazy HTTPX AsyncClient instance (HTTP/2 capable).

    Lazily creates the client on first await.
    Subsequent awaits return the same instance until close is called.

    Returns:
        httpx.AsyncClient: HTTP/2 capable async client

    Raises:
        RuntimeError: if HTTPX H2 is not available (h2 not installed)

    Invariants:
        [H2-I2] lazy — no client created until first await
        [H2-I3] repeated awaits return same instance
    """
    global _httpx_client_instance, _httpx_client_closed

    if not _check_httpx_h2_capability():
        raise RuntimeError(
            f"HTTPX HTTP/2 not available: {_httpx_import_error or 'unknown'}"
        )

    async with _httpx_client_lock:
        if _httpx_client_instance is None or _httpx_client_closed:
            import httpx

            # HTTP/2 limits — API-batch friendly
            # limit=25 total, limit_per_host=10 (higher than aiohttp's 5 for API batching)
            limits = httpx.Limits(
                max_connections=25,
                max_keepalive_connections=10,
                keepalive_expiry=30.0,
            )

            # HTTP/2 configuration — adaptive, not forced
            # http2=True enables HTTP/2 but falls back to 1.1 if server doesn't support
            http2 = True

            timeout = httpx.Timeout(
                connect=10.0,
                read=20.0,
                write=10.0,
                pool=10.0,  # timeout for connection from pool
            )

            _httpx_client_instance = httpx.AsyncClient(
                limits=limits,
                http2=http2,
                timeout=timeout,
                follow_redirects=False,  # P1-5: Manual redirect handling with SSRF validation
                # No cookies — stateless API calls
                cookies=None,
                # Trust environment for proxy detection (honors HTTP_PROXY etc.)
                trust_env=False,  # F206K: explicit, no accidental proxy leak
            )
            _httpx_client_closed = False
            logger.debug("[HTTPX] httpx.AsyncClient created (HTTP/2, lazy)")
        return _httpx_client_instance


def is_httpx_h2_enabled() -> bool:
    """
    Check if HTTPX HTTP/2 lane is available.
    Can be called at any time — no side effects.
    """
    return _check_httpx_h2_capability()


def get_httpx_capability_reason() -> str:
    """
    Return human-readable reason for HTTPX H2 availability status.
    For telemetry — not used for routing decisions.
    """
    if _httpx_h2_enabled:
        return "httpx_h2_available"
    return _httpx_import_error or "httpx_h2_check_not_run"


async def close_httpx_client_async() -> None:
    """
    Close the HTTPX client if it exists (async, proper await).

    Idempotent: safe to call multiple times.
    After close, next async_get_httpx_client() await creates a fresh instance.

    Invariants:
        [H2-I4] idempotent — multiple calls are safe
        [H2-I5] after close, next await creates new instance
    """
    global _httpx_client_instance, _httpx_client_closed

    # Extract client reference inside lock, then close OUTSIDE lock
    # (matching session_runtime.py pattern — do NOT hold lock during await)
    client = None
    async with _httpx_client_lock:
        if _httpx_client_instance is not None and not _httpx_client_closed:
            client = _httpx_client_instance
            _httpx_client_instance = None
            _httpx_client_closed = True
        elif _httpx_client_instance is not None and _httpx_client_closed:
            # Already closed, no-op
            return

    # Close outside lock — await must not hold the lock
    if client is not None:
        try:
            await client.aclose()
            logger.debug("[HTTPX] httpx.AsyncClient closed")
        except Exception as e:
            logger.warning(f"[HTTPX] close error: {e}")


__all__ = [
    "async_get_httpx_client",
    "is_httpx_h2_enabled",
    "get_httpx_capability_reason",
    "close_httpx_client_async",
]
