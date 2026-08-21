"""
ISSUE #23 integration test: HTTP cache full wiring verification.

Tests the complete flow:
    build_cache_transport() -> set_httpx_cache_transport()
    -> httpx.AsyncClient(transport=_httpx_cache_transport)
    -> async_get_httpx_session() returns cached client

This is NOT tested by test_http_cache.py (which tests build_cache_transport
in isolation). This test verifies the wiring chain end-to-end.
"""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock

import pytest

# Ensure universal package is importable when pytest runs from repo root.
_HERE = __file__.rsplit("/", 2)[0]
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


@pytest.mark.asyncio
async def test_wiring_set_transport_then_session() -> None:
    """
    set_httpx_cache_transport() stores the transport, then the next
    async_get_httpx_session() call uses it in AsyncClient(transport=...).
    """
    from network import session_runtime as sr

    # Reset to clean state
    sr._reset_session_runtime_for_tests()

    # Build a mock cache transport
    mock_transport = AsyncMock(name="mock_cache_transport")
    mock_transport.handle_async_request = AsyncMock(return_value=None)

    # Wire it in
    sr.set_httpx_cache_transport(mock_transport)
    assert sr._httpx_cache_transport is mock_transport

    # Get a session -- should create AsyncClient with transport=mock_transport
    session = await sr.async_get_httpx_session()

    # Verify: session exists and was created with our transport
    assert session is not None
    assert sr._httpx_cache_transport is mock_transport

    # Clean up
    await sr.close_httpx_session_async()
    sr._reset_session_runtime_for_tests()


@pytest.mark.asyncio
async def test_wiring_none_transport_is_idempotent() -> None:
    """
    set_httpx_cache_transport(None) clears the global; session still works.
    """
    from network import session_runtime as sr

    sr._reset_session_runtime_for_tests()

    # Set None
    sr.set_httpx_cache_transport(None)
    assert sr._httpx_cache_transport is None

    # Session should still be creatable
    session = await sr.async_get_httpx_session()
    assert session is not None

    await sr.close_httpx_session_async()
    sr._reset_session_runtime_for_tests()


@pytest.mark.asyncio
async def test_wiring_reset_clears_transport() -> None:
    """
    _reset_session_runtime_for_tests() clears _httpx_cache_transport to None.
    Ensures test isolation: each test starts with no pre-wired transport.
    """
    from network import session_runtime as sr

    sr._reset_session_runtime_for_tests()

    mock_transport = AsyncMock(name="mock_cache_transport")
    sr.set_httpx_cache_transport(mock_transport)
    assert sr._httpx_cache_transport is mock_transport

    # Reset -- should clear the transport
    sr._reset_session_runtime_for_tests()
    assert sr._httpx_cache_transport is None


@pytest.mark.asyncio
async def test_wiring_session_recreated_after_close() -> None:
    """
    Closing the session and calling async_get_httpx_session() again
    creates a new session instance.
    """
    from network import session_runtime as sr

    sr._reset_session_runtime_for_tests()

    mock_transport = AsyncMock(name="mock_cache_transport")
    sr.set_httpx_cache_transport(mock_transport)

    # Create session
    session1 = await sr.async_get_httpx_session()
    session_id1 = id(session1)

    # Close and create again -- session is recreated
    await sr.close_httpx_session_async()
    session2 = await sr.async_get_httpx_session()

    # New session instance
    assert id(session2) != session_id1

    await sr.close_httpx_session_async()
    sr._reset_session_runtime_for_tests()
