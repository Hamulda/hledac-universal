"""
httpx HTTP/2 Client Surface — Transport Capability Layer 2026
================================================================

Sprint F206K: Optional HTTPX HTTP/2 clearnet lane.

AUTHORITY (F206K):
  This module provides the LAZY HTTPX client singleton surface.
  HTTPX is optional — project imports and runs even if HTTP/2 is not available.
  HTTP/2 is activated only when:
    1. httpx >= 0.28.0 is installed (bundles h2 internally)
    2. Transport policy selects HTTPX H2 lane
    3. Target is clearnet (no Tor/I2P/Freenet)

TRANSPORT WORLD CLASSIFICATION (F206K):
  - HTTPX H2 WORLD: HTTP/2-capable httpx for clearnet API/same-host batch
  - aiohttp WORLD: plain TCPConnector (existing hot-path)
  - aiohttp_socks WORLD: ProxyConnector for Tor/I2P (existing darknet path)
  - curl_cffi WORLD: JA3 fingerprint spoofing — SEPARATE plane, not unified

ISSUE #42 FIX:
  httpx >= 0.28.0 bundles h2 internally — no separate `h2` package needed.
  The old `import h2` check is obsolete. HTTP/2 is enabled by default.

INVARIANTS:
  [H2-I1] Lazy import — httpx NOT imported at module level
  [H2-I2] Lazy init — client created on first await, not at import
  [H2-I3] Idempotent — repeated awaits return same instance
  [H2-I4] Fail-soft disabled — httpx unavailable → _httpx_h2_enabled = False
  [H2-I5] Connector limits delegated to session_pool (unified_transport.py)
  [H2-I6] No top-level network side effects at import time
  [H2-I7] CancelledError propagates (not swallowed)
  [H2-I8] HTTPX client closed ONLY via close_httpx_client_async()
"""



import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx  # used only in annotations — actual import is lazy

logger = logging.getLogger(__name__)

# =============================================================================
# Capability Detection — fail-soft, no hard import
# =============================================================================

_httpx_h2_enabled: bool = False
_httpx_import_error: str | None = None


def _check_httpx_h2_capability() -> bool:
    """
    Check if httpx with HTTP/2 support is available.
    Called lazily on first use — not at import time.

    ISSUE #42 FIX: httpx >= 0.28.0 bundles h2 internally.
    No separate `h2` package needed. HTTP/2 is enabled by default.

    Returns:
        True if httpx >= 0.28.0 is installed
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

    # httpx available — verify version >= 0.28.0 (bundles h2 internally)
    try:
        version = httpx.__version__
        major, minor = map(int, version.split('.')[:2])
        # Pre-1.0 versioning: (major, minor) < (0, 28) means too old
        if (major, minor) < (0, 28):
            _httpx_import_error = f"httpx {version} too old (>=0.28.0 required for bundled HTTP/2)"
            logger.debug(f"[HTTPX] {_httpx_import_error}")
            return False
    except (ValueError, IndexError) as e:
        _httpx_import_error = f"httpx version parse error: {version!r} ({e})"
        logger.debug(f"[HTTPX] {_httpx_import_error}")
        return False

    # Version OK — HTTP/2 is bundled and enabled by default in httpx >= 0.28.0
    _httpx_h2_enabled = True
    logger.debug(f"[HTTPX] HTTP/2 capability detected (httpx={version}, h2 bundled)")
    return True


# =============================================================================
# Lazy HTTPX Client Singleton
# =============================================================================

# F4.3: Delegates to transport.session_pool for unified pool management.
# Kept as facade for backward compatibility with existing call sites.

_httpx_client_instance: httpx.AsyncClient | None = None
_httpx_client_lock: asyncio.Lock = asyncio.Lock()
_httpx_client_closed: bool = False


async def async_get_httpx_client() -> httpx.AsyncClient:
    """
    Get or create the lazy HTTPX AsyncClient instance (HTTP/2 capable).

    F4.3: Delegates to transport.session_pool.httpx() for unified pool.
    Kept as facade for backward compatibility.

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

    # F4.3: Use session_pool for unified httpx singleton
    from .session_pool import session_pool as _pool

    async with _httpx_client_lock:
        if _httpx_client_instance is None or _httpx_client_closed:
            _httpx_client_instance = await _pool.httpx()
            _httpx_client_closed = False
            logger.debug("[HTTPX] httpx.AsyncClient via session_pool (HTTP/2, lazy)")
        return _httpx_client_instance


def is_httpx_h2_enabled() -> bool:
    """
    Check if HTTPX HTTP/2 lane is available.
    Can be called at any time — no side effects.
    Cached after first successful check.
    """
    if _httpx_h2_enabled or _httpx_import_error is not None:
        return _httpx_h2_enabled
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

    F4.3: Also closes via session_pool for unified lifecycle.

    Idempotent: safe to call multiple times.
    After close, next async_get_httpx_client() await creates a fresh instance.

    Invariants:
        [H2-I4] idempotent — multiple calls are safe
        [H2-I5] after close, next await creates new instance
    """
    global _httpx_client_instance, _httpx_client_closed

    # F4.3: Also close via session_pool
    from .session_pool import close_httpx as _pool_close

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
            pass

    # Close outside lock — await must not hold the lock
    if client is not None:
        try:
            await client.aclose()
            logger.debug("[HTTPX] httpx.AsyncClient closed")
        except Exception as e:
            logger.warning(f"[HTTPX] close error: {e}")

    # F4.3: Sync session_pool state
    try:
        await _pool_close()
    except Exception:
        pass  # session_pool tracks its own state


__all__ = [
    "async_get_httpx_client",
    "is_httpx_h2_enabled",
    "get_httpx_capability_reason",
    "close_httpx_client_async",
]
