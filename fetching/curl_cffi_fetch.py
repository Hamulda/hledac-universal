"""
fetching/curl_cffi_fetch.py

ISSUE-0.2 FIX: Primary transport wrapper for curl_cffi with JA3 profile rotation.

This module provides the canonical fetch interface using curl_cffi with:
- JA3/TLS fingerprint rotation (6 browser profiles)
- CAPS-based capability checking (never falls back to httpx without JA3)
- Per-host session caching with LRU eviction
- HTTP/3 Alt-Svc support via prewarm pool

Architecture:
  FetchCoordinator (coordinators/)
      ↓
  fetching/curl_cffi_fetch.py    ← THIS MODULE (CAPS check + JA3)
      ↓
  transport/curl_cffi_fetch.py   ← canonical implementation

Fallback chain (ISSUE-0.2):
  curl_cffi available → use JA3 spoofing ✓
  curl_cffi unavailable → FAIL FAST (no silent httpx fallback)
  Lightpanda failure → curl_cffi → FAIL FAST (never plain httpx)
"""

from __future__ import annotations

import logging
from typing import Any

from hledac.universal.core.capabilities import CAPS, CURL_CFFI

# Re-export all public symbols from canonical implementation
# This maintains backward compatibility while adding CAPS enforcement
from hledac.universal.transport.curl_cffi_fetch import (
    fetch_via_curl_cffi_cached,
    fetch_via_i2p_curl_cffi,
    is_curl_cffi_available,
    async_get_curl_cffi_session,
    async_get_curl_cffi_session_for_host,
    close_curl_cffi_sessions_async,
    get_curl_cffi_runtime_status,
    next_ja3_profile,
    reset_ja3_cycle,
    HLEDAC_DEBUG_JA3,
    _JA3_ROTATION_POOL,
    _blocking_altsvc_probe_for_url,
    # [NEXUS]-018-01: WebKit HTTP/2 telemetry
    get_webkit_transport_telemetry,
    _reset_webkit_transport_telemetry,
)

logger = logging.getLogger(__name__)


def is_curl_cffi_capable() -> tuple[bool, str]:
    """
    Check if curl_cffi is available via CAPS capability registry.

    Returns:
        (is_available: bool, reason: str)
        - (True, "ok") if CURL_CFFI capability resolves
        - (False, "cap_unavailable") if CAPS.require returns None
        - (False, "cap_not_registered") if CURL_CFFI not in CAPS
    """
    try:
        cap_result = CAPS.require(CURL_CFFI)
        if cap_result is not None:
            return (True, "ok")
        # CAPS returned None — capability resolved but import failed
        return (False, f"cap_resolved_but_unavailable")
    except Exception as e:  # noqa: BLE001
        logger.debug(f"CAPS.require(CURL_CFFI) raised: {e}")
        return (False, f"cap_check_failed: {e}")


def require_curl_cffi() -> Any:
    """
    Require curl_cffi via CAPS. Returns the curl_cffi module or None.

    This is the ONLY correct way to check curl_cffi availability in the
    fetch transport stack. Using is_curl_cffi_available() directly bypasses
    the CAPS registry and can lead to the httpx fallback without JA3.
    """
    return CAPS.require(CURL_CFFI)


async def fetch_via_curl_cffi_with_caps_check(
    url: str,
    headers: dict[str, str] | None = None,
    timeout_s: float = 10.0,
    max_bytes: int = 10 * 1024 * 1024,
    profile: str = "chrome136",
    **kwargs: Any,
) -> dict[str, Any] | None:
    """
    Fetch via curl_cffi with CAPS-based availability check.

    This is the RECOMMENDED fetch function for FetchCoordinator.
    It ensures JA3 spoofing is always used when curl_cffi is available.

    Returns:
        Fetch result dict on success, None on failure.
        Never returns a result without JA3 spoofing.

    Raises:
        No JA3 spoofing available: logs error and returns None.
        Caller should handle None and decide fallback path.
    """
    cap_result = require_curl_cffi()
    if cap_result is None:
        logger.warning(
            "[ISSUE-0.2] curl_cffi not available via CAPS — "
            " refusing to fall back to httpx (no JA3 spoofing). "
            " Set HLEDAC_ENABLE_CURL_CFFI=1 or install curl_cffi."
        )
        return None

    try:
        return await fetch_via_curl_cffi_cached(
            url=url,
            headers=headers,
            timeout_s=timeout_s,
            max_bytes=max_bytes,
            profile=profile,
            **kwargs,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ISSUE-0.2] curl_cffi fetch failed: {e}")
        return None


__all__ = [
    # From transport/curl_cffi_fetch (re-exported)
    "fetch_via_curl_cffi_cached",
    "fetch_via_i2p_curl_cffi",
    "is_curl_cffi_available",
    "async_get_curl_cffi_session",
    "async_get_curl_cffi_session_for_host",
    "close_curl_cffi_sessions_async",
    "get_curl_cffi_runtime_status",
    "next_ja3_profile",
    "reset_ja3_cycle",
    "HLEDAC_DEBUG_JA3",
    "_JA3_ROTATION_POOL",
    "_blocking_altsvc_probe_for_url",
    # ISSUE-0.2 additions
    "is_curl_cffi_capable",
    "require_curl_cffi",
    "fetch_via_curl_cffi_with_caps_check",
    # [NEXUS]-018-01: WebKit HTTP/2 telemetry
    "get_webkit_transport_telemetry",
    "_reset_webkit_transport_telemetry",
]
