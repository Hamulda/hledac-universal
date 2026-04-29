"""
Test 11: Profile fallback chrome136 → chrome110.

Tests that when a profile is not available, the fallback chain works:
chrome136 → chrome120 → chrome110 → safari17_0
"""

import asyncio
import sys
from unittest.mock import patch


def test_profile_fallback_order():
    """Profile fallback chain is chrome136 → chrome120 → chrome110 → safari17_0."""
    for mod in list(sys.modules.keys()):
        if "curl_cffi_runtime" in mod:
            del sys.modules[mod]

    from hledac.universal.transport.curl_cffi_runtime import _PROFILE_FALLBACK_ORDER

    assert _PROFILE_FALLBACK_ORDER == ["chrome136", "chrome120", "chrome110", "safari17_0"]


def test_async_get_curl_cffi_session_falls_back_on_unavailable_profile():
    """When curl_cffi available but session creation fails, tries fallback profiles."""
    for mod in list(sys.modules.keys()):
        if "curl_cffi_runtime" in mod:
            del sys.modules[mod]

    from hledac.universal.transport.curl_cffi_runtime import async_get_curl_cffi_session

    async def run():
        # Mock curl_cffi as available but all profiles fail
        with patch("hledac.universal.transport.curl_cffi_runtime.is_curl_cffi_available", return_value=(True, "ok")):
            with patch("hledac.universal.transport.curl_cffi_runtime._get_or_create_session") as mock_get:
                mock_get.side_effect = RuntimeError("profile unavailable")
                ok, session, used_profile = await async_get_curl_cffi_session("chrome136")
                assert ok is False
                assert "session_creation_failed" in used_profile

    asyncio.run(run())


def test_async_get_curl_cffi_session_unknown_profile_uses_fallback_chain():
    """Unknown profile uses full fallback chain."""
    for mod in list(sys.modules.keys()):
        if "curl_cffi_runtime" in mod:
            del sys.modules[mod]

    from hledac.universal.transport.curl_cffi_runtime import async_get_curl_cffi_session

    async def run():
        with patch("hledac.universal.transport.curl_cffi_runtime.is_curl_cffi_available", return_value=(True, "ok")):
            with patch("hledac.universal.transport.curl_cffi_runtime._get_or_create_session") as mock_get:
                mock_get.side_effect = RuntimeError("unavailable")
                ok, session, reason = await async_get_curl_cffi_session("unknown_browser")
                assert ok is False

    asyncio.run(run())
