"""
runtime/cli_session.py — Shared httpx.AsyncClient for CLI/probe scripts
========================================================================


Reusable async context manager for one-shot CLI tools and probe scripts.
Wraps lazy session creation + idempotent close via the session_runtime
infrastructure, exposing a clean async-context-manager API.

Usage:
    async with cli_session_cm() as session:
        async with session.get(url) as resp:
            text = await resp.text()

Architecture:
    - Delegates to network.session_runtime.async_get_httpx_session()
      for the actual session lifecycle (lazy init, idempotent close,
      ContextVar isolation per async task).
    - On M1 8GB: transport.session_pool manages connection limits.

F4XX (Issue 19): migrated from aiohttp.ClientSession to httpx.AsyncClient.
All aiohttp references removed — httpx is the sole async HTTP client.

Invariants:
    [I1] No network side-effect at import time
    [I2] Session created on first `async with cli_session_cm()`
    [I3] Same session returned on nested `async with` calls within same task
    [I4] close() is idempotent — safe to call multiple times
    [I5] After close, next `async with` creates fresh session
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from _core import aclose

if TYPE_CHECKING:
    import httpx


class _CliSessionContextManager:
    """Async context manager wrapping session_runtime's lazy httpx session."""

    __slots__ = ("_session",)

    def __init__(self) -> None:
        self._session: httpx.AsyncClient | None = None

    async def __aenter__(self) -> httpx.AsyncClient:
        from hledac.universal.network.session_runtime import async_get_httpx_session

        self._session = await async_get_httpx_session()
        return self._session

    async def __aexit__(self, _exc_type, _exc_val, _exc_tb) -> None:
        # Idempotent — session_runtime.close_httpx_session_async() is safe to call twice
        from hledac.universal.network.session_runtime import (
            close_httpx_session_async,
        )

        await close_httpx_session_async()
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
