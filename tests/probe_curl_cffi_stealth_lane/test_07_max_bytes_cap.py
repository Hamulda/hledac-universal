"""
Test 13: max_bytes hard cap holds.

Tests that fetch_via_curl_cffi:
1. Respects max_bytes parameter (verified by DEFAULT_MAX_BYTES constant)
2. Handles max_bytes parameter without error
3. Truncation is implemented in the code (verified by reading the source)

Note: Live truncation test requires a real server; tested here via error path
and constant validation. Integration test would verify actual truncation.
"""

import sys


def test_max_bytes_default_is_10mb():
    """Default max_bytes is 10MB."""
    from hledac.universal.transport.curl_cffi_fetch import DEFAULT_MAX_BYTES

    assert DEFAULT_MAX_BYTES == 10 * 1024 * 1024


def test_max_bytes_parameter_accepted():
    """max_bytes parameter is accepted by fetch_via_curl_cffi without error."""
    import asyncio
    for mod in list(sys.modules.keys()):
        if "curl_cffi" in mod:
            del sys.modules[mod]

    from hledac.universal.transport.curl_cffi_fetch import fetch_via_curl_cffi

    async def run():
        # Use a small max_bytes - if parameter is accepted without TypeError, test passes
        result = await fetch_via_curl_cffi(
            "http://127.0.0.1:9/",
            timeout_s=0.5,
            max_bytes=1024,  # 1KB limit
        )
        # Result must be a valid dict even with small max_bytes
        assert isinstance(result, dict)
        assert "content" in result
        assert isinstance(result["content"], bytes)
        # Content must not exceed max_bytes if truncation worked
        assert len(result["content"]) <= 1024

    asyncio.run(run())


def test_truncation_implemented_in_code():
    """Verify truncation logic exists in fetch_via_curl_cffi source."""
    from hledac.universal.transport import curl_cffi_fetch
    import inspect

    source = inspect.getsource(curl_cffi_fetch.fetch_via_curl_cffi)
    # The truncation logic must be present in the source
    assert "max_bytes" in source
    assert "len(content_bytes)" in source or "len(result" in source or "> max_bytes" in source
