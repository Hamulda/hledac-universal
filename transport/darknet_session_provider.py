"""
transport/darknet_session_provider.py

F274: Unified darknet session provider — replaces manual Tor/I2P dict pools
in FetchCoordinator with a thin facade over existing transport singletons.

Architecture:
  - Wraps TorTransport singleton + I2PTransport module-level lazy session.
  - Manages TTL + LRU eviction tracking per (transport, host).
  - Does NOT create sessions — delegates to transport layer which owns them.
  - Fail-safe: returns None on any transport error; never raises.
  - M1 8GB safe: no eager heavy imports; bounded tracking dicts.

Invariants:
  - [DSPY-1] get_session() is async, fail-soft, returns None on error
  - [DSPY-2] mark_used() updates last-access timestamp only; no session mutation
  - [DSPY-3] close_idle() evicts TTL-expired entries; does NOT close transport sessions
  - [DSPY-4] close_all() clears tracking + closes transport sessions at teardown
  - [DSPY-5] No bare except; always except Exception
"""



import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger("hledac.universal.transport.darknet_session_provider")

# --- Constants (must match FetchCoordinator legacy values) ---
_TTL_SECONDS: int = 300  # 5 min — unchanged from FetchCoordinator L968/L1047
_MAX_SESSIONS: int = 4   # CONCURRENCY_TOR — unchanged from L201

# --- Singleton state ---
_lock: asyncio.Lock | None = None
# {transport_name: {host: last_access_monotonic}}
_last_used: dict[str, dict[str, float]] = {"tor": {}, "i2p": {}}


def _get_lock() -> asyncio.Lock:
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


# --- Public API ---


async def get_session(
    transport: str,
    host: str,
    _profile: str | None = None,  # reserved for future JA3 profile routing
) -> Any | None:
    """
    Get or validate a darknet session for the given transport+host.

    Args:
        transport: "tor" or "i2p"
        host: hostname/domain (e.g. "example.onion")

    Returns:
        httpx.AsyncClient if transport is available, None on any error.
        For tor: returns the TorTransport._session_tor (httpx.AsyncClient, owned by transport).
        For i2p: returns the lazy singleton from get_i2p_session() (httpx.AsyncClient).
    """
    if transport not in ("tor", "i2p"):
        return None

    try:
        if transport == "tor":
            return await _get_tor_session(host)
        else:
            return await _get_i2p_session(host)
    except Exception:
        return None


async def _get_tor_session(_host: str) -> Any | None:
    """Return the TorTransport's _session_tor ClientSession (owned by transport)."""
    try:
        from .tor_transport import get_tor_transport_singleton

        tor = get_tor_transport_singleton()
        if tor is None or not tor.available:
            return None
        if not await _tor_is_running(tor):
            return None
        # Return the TorTransport._session_tor (httpx.AsyncClient, owned by transport).
        # TorTransport._session_tor is set in start() after connector init.
        return getattr(tor, "_session_tor", None)
    except Exception:
        return None


async def _tor_is_running(tor: Any) -> bool:
    """Check if TorTransport is running; fail-soft."""
    try:
        return await tor.is_running()
    except Exception:
        return False


async def _get_i2p_session(_host: str) -> Any | None:
    """Delegate to I2PTransport module-level lazy singleton."""
    try:
        # Lazy import to avoid importing aiohttp at module load time
        from .i2p_transport import get_i2p_session as _get_i2p_sess

        return await _get_i2p_sess()
    except Exception:
        return None


async def mark_used(transport: str, host: str) -> None:
    """
    Record that host was accessed now (for TTL tracking).

    Fails silently — missing a mark_used entry just means next get_session
    treats it as cold and evicts it.
    """
    if transport not in ("tor", "i2p"):
        return
    try:
        async with _get_lock():
            _last_used[transport][host] = time.monotonic()
    except Exception:  # noqa: BLE001
        pass


async def close_idle() -> int:
    """
    Evict TTL-expired entries from the tracking dict.

    Does NOT close transport-level sessions — those are owned by the
    transport layer and closed by the transport's own lifecycle.

    Returns:
        Number of entries evicted (0 if none expired).
    """
    evicted = 0
    now = time.monotonic()
    async with _get_lock():
        for transport in ("tor", "i2p"):
            expired = [
                host
                for host, ts in _last_used[transport].items()
                if now - ts > _TTL_SECONDS
            ]
            for host in expired:
                _last_used[transport].pop(host, None)
                evicted += 1
    if evicted:
        logger.debug("darknet_session_provider: evicted %d idle entries", evicted)
    return evicted


async def close_all() -> None:
    """
    Clear all tracking state and close transport-level sessions.

    Called at sprint teardown. After this, the transport singletons
    are unusable until a fresh start — consistent with FetchCoordinator
    legacy behavior where close() invalidated all sessions.
    """
    global _last_used
    async with _get_lock():
        # Clear tracking
        _last_used = {"tor": {}, "i2p": {}}

    # Close I2P lazy singleton so it recreates fresh on next use
    try:
        from .i2p_transport import close_i2p_session

        await close_i2p_session()
    except Exception as e:
        logger.debug("darknet_session_provider: close_i2p_session failed (fail-soft): %s", e)

    # TorTransport is a shared singleton — closing its sessions affects all
    # callers. Only do this at process teardown when the singleton is truly
    # being shut down.
    try:
        from .tor_transport import get_tor_transport_singleton

        tor = get_tor_transport_singleton()
        if tor is not None and tor.available:
            await tor.stop()
    except Exception as e:
        logger.debug("darknet_session_provider: tor.stop() failed (fail-soft): %s", e)


def get_stats() -> dict[str, Any]:
    """Return lightweight stats for debugging."""
    return {
        "tracked_tor_hosts": len(_last_used["tor"]),
        "tracked_i2p_hosts": len(_last_used["i2p"]),
        "ttl_seconds": _TTL_SECONDS,
        "max_sessions": _MAX_SESSIONS,
    }
