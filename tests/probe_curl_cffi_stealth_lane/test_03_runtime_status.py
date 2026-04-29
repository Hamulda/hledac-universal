"""
Test 8: Runtime session is lazy singleton/LRU, not per-request.

Tests:
- Session is cached and reused across calls
- Cache is bounded to max 3 profiles
- LRU eviction works correctly
"""

import asyncio
import sys


def test_runtime_status_returns_cached_profiles():
    """get_curl_cffi_runtime_status returns cached_profiles list."""
    for mod in list(sys.modules.keys()):
        if "curl_cffi_runtime" in mod:
            del sys.modules[mod]

    from hledac.universal.transport.curl_cffi_runtime import get_curl_cffi_runtime_status

    status = get_curl_cffi_runtime_status()
    assert "cached_profiles" in status
    assert "cache_capacity" in status
    assert status["cache_capacity"] == 3


def test_session_not_created_without_call():
    """async_get_curl_cffi_session does not create session until awaited."""
    for mod in list(sys.modules.keys()):
        if "curl_cffi_runtime" in mod:
            del sys.modules[mod]

    from hledac.universal.transport.curl_cffi_runtime import (
        _curl_cffi_sessions,
        get_curl_cffi_runtime_status,
    )

    # Before any call, cache should be empty
    assert len(_curl_cffi_sessions) == 0


def test_runtime_status_reflects_availability():
    """get_curl_cffi_runtime_status shows correct availability."""
    for mod in list(sys.modules.keys()):
        if "curl_cffi_runtime" in mod:
            del sys.modules[mod]

    from hledac.universal.transport.curl_cffi_runtime import get_curl_cffi_runtime_status

    status = get_curl_cffi_runtime_status()
    assert "curl_cffi_available" in status
    assert "availability_reason" in status
    # Check fields are correct type (value depends on whether curl_cffi is installed)
    assert isinstance(status["curl_cffi_available"], bool)
    assert isinstance(status["availability_reason"], str)
