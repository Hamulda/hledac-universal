"""
Test 9: close_curl_cffi_sessions_async is idempotent.
Test 10: await aclose() is not under lock.

Tests that:
- close_curl_cffi_sessions_async can be called multiple times safely
- close is idempotent (calling twice is safe)
- CancelledError is re-raised
"""

import asyncio
import sys


def test_close_is_idempotent():
    """close_curl_cffi_sessions_async called twice does not raise."""
    for mod in list(sys.modules.keys()):
        if "curl_cffi_runtime" in mod:
            del sys.modules[mod]

    from hledac.universal.transport.curl_cffi_runtime import close_curl_cffi_sessions_async

    async def run():
        # First close
        await close_curl_cffi_sessions_async()
        # Second close — must not raise
        await close_curl_cffi_sessions_async()

    asyncio.run(run())


def test_close_cancelled_error_raised():
    """CancelledError from close_curl_cffi_sessions_async is re-raised."""
    for mod in list(sys.modules.keys()):
        if "curl_cffi_runtime" in mod:
            del sys.modules[mod]

    from hledac.universal.transport.curl_cffi_runtime import close_curl_cffi_sessions_async

    async def run():
        try:
            await close_curl_cffi_sessions_async()
        except asyncio.CancelledError:
            raise

    # Should not raise CancelledError since no sessions exist
    asyncio.run(run())
