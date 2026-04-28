"""
transport/curl_cffi_fetch.py

Fetch adapter for curl_cffi stealth lane.
Returns FetchResult-compatible dict with full telemetry.

No network side effects on import.
Streaming/chunked if AsyncSession supports it; hard cap at max_bytes otherwise.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 10.0
DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 10MB hard cap


async def fetch_via_curl_cffi(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_bytes: int = DEFAULT_MAX_BYTES,
    profile: str = "chrome110",
) -> Dict[str, Any]:
    """
    Fetch URL via curl_cffi stealth lane.

    Returns FetchResult-compatible dict:
        url, final_url, content (bytes), status_code, content_type,
        headers, success, error, selected_transport, tls_impersonate,
        failure_stage, network_error_kind

    Failure stages: "resolve", "connect", "tls", "response", "read", "unknown"
    Network error kinds: "timeout", "connection_refused", "dns_failure",
                         "connection_reset", "too_many_redirects", "other"

    CancelledError is re-raised.
    """
    from .curl_cffi_runtime import async_get_curl_cffi_session, is_curl_cffi_available

    # Check availability first
    available, avail_reason = is_curl_cffi_available()
    if not available:
        return _make_error_result(
            url,
            error=f"curl_cffi_not_available: {avail_reason}",
            failure_stage="unknown",
            network_error_kind="other",
            selected_transport="curl_cffi",
            tls_impersonate=profile,
        )

    # Get session (lazy, cached, bounded)
    try:
        ok, session, used_profile = await async_get_curl_cffi_session(profile)
        if not ok or session is None:
            return _make_error_result(
                url,
                error=f"session_creation_failed: {used_profile}",
                failure_stage="unknown",
                network_error_kind="other",
                selected_transport="curl_cffi",
                tls_impersonate=used_profile,
            )
    except asyncio.CancelledError:
        raise
    except Exception as e:
        return _make_error_result(
            url,
            error=f"session_error: {e}",
            failure_stage="unknown",
            network_error_kind="other",
            selected_transport="curl_cffi",
            tls_impersonate=profile,
        )

    # Perform request
    try:
        response = await session.get(
            url,
            headers=headers,
            timeout=timeout_s,
        )

        # Read body with hard cap at max_bytes
        # F206K: Use bytearray.extend() — O(1) amortized vs bytes += O(n²)
        content_bytes = bytearray()
        async for chunk in response.iter_content(chunk_size=65536):
            content_bytes.extend(chunk)
            if len(content_bytes) > max_bytes:
                del content_bytes[max_bytes:]  # truncate in-place
                logger.debug(f"curl_cffi body truncated to {max_bytes} bytes for {url}")
                break

        content_type = ""
        if response.headers:
            content_type = response.headers.get("content-type", "")

        return {
            "url": url,
            "final_url": url,
            "content": bytes(content_bytes),  # bytearray → bytes for response contract
            "status_code": response.status_code,
            "content_type": content_type,
            "headers": dict(response.headers) if response.headers else {},
            "success": True,
            "error": None,
            "selected_transport": "curl_cffi",
            "tls_impersonate": used_profile,
            "failure_stage": None,
            "network_error_kind": None,
        }

    except asyncio.TimeoutError:
        return _make_error_result(
            url,
            error="timeout",
            failure_stage="response",
            network_error_kind="timeout",
            selected_transport="curl_cffi",
            tls_impersonate=used_profile,
        )
    except ConnectionRefusedError:
        return _make_error_result(
            url,
            error="connection_refused",
            failure_stage="connect",
            network_error_kind="connection_refused",
            selected_transport="curl_cffi",
            tls_impersonate=used_profile,
        )
    except asyncio.CancelledError:
        raise
    except Exception as e:
        error_str = str(e).lower()
        if "timeout" in error_str:
            network_kind = "timeout"
            failure_stage = "response"
        elif "dns" in error_str or "name or service not known" in error_str:
            network_kind = "dns_failure"
            failure_stage = "resolve"
        elif "connection reset" in error_str:
            network_kind = "connection_reset"
            failure_stage = "connect"
        else:
            network_kind = "other"
            failure_stage = "unknown"

        return _make_error_result(
            url,
            error=str(e),
            failure_stage=failure_stage,
            network_error_kind=network_kind,
            selected_transport="curl_cffi",
            tls_impersonate=used_profile,
        )


def _make_error_result(
    url: str,
    error: str,
    failure_stage: str,
    network_error_kind: str,
    selected_transport: str,
    tls_impersonate: str,
) -> Dict[str, Any]:
    """Build an error result dict."""
    return {
        "url": url,
        "final_url": url,
        "content": b"",
        "status_code": 0,
        "content_type": "",
        "headers": {},
        "success": False,
        "error": error,
        "selected_transport": selected_transport,
        "tls_impersonate": tls_impersonate,
        "failure_stage": failure_stage,
        "network_error_kind": network_error_kind,
    }
