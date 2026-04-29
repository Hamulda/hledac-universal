"""
Test 12: FetchResult telemetry contains selected_transport="curl_cffi" and tls_impersonate.

Tests that fetch_via_curl_cffi returns proper telemetry structure.
Since curl_cffi may or may not be installed, we test both paths:
- curl_cffi unavailable → error result with telemetry
- curl_cffi available → tests the code path (telemetry fields present in error result)
"""

import asyncio
import sys


def test_fetch_result_has_all_telemetry_fields_on_error():
    """Error result always has selected_transport, tls_impersonate, failure_stage, network_error_kind."""
    for mod in list(sys.modules.keys()):
        if "curl_cffi" in mod:
            del sys.modules[mod]

    from hledac.universal.transport.curl_cffi_fetch import fetch_via_curl_cffi

    async def run():
        # Use unreachable address to trigger a real network error path
        result = await fetch_via_curl_cffi(
            "http://127.0.0.1:9/",  # Connection refused
            timeout_s=1.0,
        )

        # All telemetry fields must be present even in error case
        assert "selected_transport" in result
        assert "tls_impersonate" in result
        assert "failure_stage" in result
        assert "network_error_kind" in result
        assert result["selected_transport"] == "curl_cffi"
        assert result["success"] is False
        # failure_stage and network_error_kind should be populated for real network errors
        assert result["failure_stage"] in {"resolve", "connect", "tls", "response", "read", "unknown"}
        assert result["network_error_kind"] in {"timeout", "connection_refused", "dns_failure", "connection_reset", "too_many_redirects", "other"}

    asyncio.run(run())


def test_fetch_via_curl_cffi_returns_dict():
    """fetch_via_curl_cffi returns a dict with expected top-level keys."""
    for mod in list(sys.modules.keys()):
        if "curl_cffi" in mod:
            del sys.modules[mod]

    from hledac.universal.transport.curl_cffi_fetch import fetch_via_curl_cffi

    async def run():
        result = await fetch_via_curl_cffi("http://127.0.0.1:9/", timeout_s=0.5)

        # Must be a dict
        assert isinstance(result, dict)
        # Must have all required FetchResult-compatible fields
        for field in ["url", "final_url", "content", "status_code", "content_type",
                      "headers", "success", "error", "selected_transport",
                      "tls_impersonate", "failure_stage", "network_error_kind"]:
            assert field in result, f"Missing field: {field}"
        # content must be bytes
        assert isinstance(result["content"], bytes)

    asyncio.run(run())
