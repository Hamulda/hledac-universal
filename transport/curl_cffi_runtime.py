"""
transport/curl_cffi_runtime.py

Backward-compat re-export alias for Issue 3.5 consolidation.
All canonical implementation lives in transport/curl_cffi_fetch.py.

.. deprecated:: 2.x
    All session management, JA3 rotation, and fetch functions moved to
    ``curl_cffi_fetch.py``. This module is retained for backward compatibility
    with external callers that import from ``curl_cffi_runtime`` and will be
    removed in v3.0.

To migrate:
    from hledac.universal.transport.curl_cffi_fetch import (
        is_curl_cffi_available,
        async_get_curl_cffi_session,
        async_get_curl_cffi_session_for_host,
        close_curl_cffi_sessions_async,
        get_curl_cffi_runtime_status,
    )
"""

# Re-export all public symbols from the canonical module
from hledac.universal.transport.curl_cffi_fetch import (  # noqa: F401, E402
    async_get_curl_cffi_session,
    async_get_curl_cffi_session_for_host,
    close_curl_cffi_sessions_async,
    get_curl_cffi_runtime_status,
    is_curl_cffi_available,
)

__all__ = [
    "is_curl_cffi_available",
    "async_get_curl_cffi_session",
    "async_get_curl_cffi_session_for_host",
    "close_curl_cffi_sessions_async",
    "get_curl_cffi_runtime_status",
]
