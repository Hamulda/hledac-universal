"""
runtime/cli_session.py — Shared aiohttp.ClientSession for CLI/probe scripts
==========================================================================

Reusable async context manager for one-shot CLI tools and probe scripts.
Wraps lazy session creation + idempotent close via the session_runtime
infrastructure, but exposes a clean async-context-manager API.

Usage:
    async with cli_session() as session:
        async with session.get(url) as resp:
            text = await resp.text()

Architecture:
    - Delegates to network.session_runtime.async_get_aiohttp_session()
      for the actual session lifecycle (lazy init, idempotent close,
      ContextVar isolation per async task).
    - HLEDAC_ENABLE_AIOHTTP_FALLBACK=1 must be set to enable aiohttp path.
    - On M1 8GB: bounded TCPConnector(limit=25, limit_per_host=8).

Invariants:
    [I1] No network side-effect at import time
    [I2] Session created on first `async with cli_session()`
    [I3] Same session returned on nested `async with` calls within same task
    [I4] close() is idempotent — safe to call multiple times
    [I5] After close, next `async with` creates fresh session
    [I6] Raises if aiohttp fallback is disabled (HLEDAC_ENABLE_AIOHTTP_FALLBACK=0)
"""

import os
from typing import TYPE_CHECKING

# Gate — aiohttp is only used when explicitly enabled (curl_cffi is primary)
_AIOHTTP_FALLBACK_ENABLED: bool = os.environ.get("HLEDAC_ENABLE_AIOHTTP_FALLBACK", "0") == "1"

if TYPE_CHECKING:
    import aiohttp


class CliSessionUnavailable(RuntimeError):
    """Raised when cli_session() is used but HLEDAC_ENABLE_AIOHTTP_FALLBACK=0."""
    pass


class _CliSessionContextManager:
    """Async context manager wrapping session_runtime's lazy session."""

    __slots__ = ("_session",)

    def __init__(self) -> None:
        self._session: "aiohttp.ClientSession | None" = None

    async def __aenter__(self) -> "aiohttp.ClientSession":
        if not _AIOHTTP_FALLBACK_ENABLED:
            raise CliSessionUnavailable(
                "cli_session() requires HLEDAC_ENABLE_AIOHTTP_FALLBACK=1"
            )
        from aiohttp import ClientSession
        from hledac.universal.network.session_runtime import async_get_aiohttp_session

        self._session = await async_get_aiohttp_session()
        return self._session

    async def __aexit__(self, _exc_type, _exc_val, _exc_tb) -> None:
        # Idempotent — session_runtime.close_aiohttp_session_async() is safe to call twice
        from hledac.universal.network.session_runtime import close_aiohttp_session_async
        await close_aiohttp_session_async()
        self._session = None


def cli_session_cm() -> _CliSessionContextManager:
    """
    Factory for the async context manager.

    Usage:
        async with cli_session_cm() as session:
            async with session.get(url) as resp:
                ...

    This factory exists to make the API explicit and self-documenting.
    """
    return _CliSessionContextManager()
