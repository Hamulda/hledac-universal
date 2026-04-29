"""
Test 1: Import curl_cffi modules without curl_cffi installed doesn't crash.

Tests that transport/curl_cffi_runtime and transport/curl_cffi_fetch
can be imported without curl_cffi being installed.
"""

import sys


def test_curl_cffi_runtime_imports_without_crash():
    """transport/curl_cffi_runtime imports without curl_cffi installed."""
    # Remove from cache to force fresh import
    for mod in list(sys.modules.keys()):
        if "curl_cffi" in mod:
            del sys.modules[mod]

    # Must not raise ImportError
    from hledac.universal.transport import curl_cffi_runtime

    # is_curl_cffi_available must work and return a bool with a reason
    available, reason = curl_cffi_runtime.is_curl_cffi_available()
    assert isinstance(available, bool)
    assert isinstance(reason, str)


def test_curl_cffi_fetch_imports_without_crash():
    """transport/curl_cffi_fetch imports without curl_cffi installed."""
    for mod in list(sys.modules.keys()):
        if "curl_cffi" in mod:
            del sys.modules[mod]

    from hledac.universal.transport import curl_cffi_fetch

    # fetch_via_curl_cffi must exist and be callable
    assert callable(curl_cffi_fetch.fetch_via_curl_cffi)


def test_curl_cffi_transport_imports_without_crash():
    """transport/curl_cffi_transport imports without curl_cffi installed."""
    for mod in list(sys.modules.keys()):
        if "curl_cffi_transport" in mod:
            del sys.modules[mod]

    from hledac.universal.transport import curl_cffi_transport

    assert callable(curl_cffi_transport.should_use_curl_cffi)
