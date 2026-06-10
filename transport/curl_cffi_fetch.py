"""
transport/curl_cffi_fetch.py

Fetch adapter for curl_cffi stealth lane.
Returns FetchResult-compatible dict with full telemetry.

No network side effects on import.
Streaming/chunked if AsyncSession supports it; hard cap at max_bytes otherwise.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import os
import threading
from typing import Any

from hledac.universal.utils.encoding import decode_response_bytes, parse_charset_from_content_type

from .body_limiter import read_body_with_cap

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 10.0
DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 10MB hard cap

# Tor SOCKS5H proxy — DNS resolved by Tor, not localhost
_TOR_CURL_PROXY: str = os.environ.get("TOR_SOCKS_PROXY_URL", "socks5h://127.0.0.1:9050")

# I2P SOCKS5H proxy — default 4447 is I2P SAM bridge port (standard install)
# F260: I2P has no NEWNYM equivalent — circuit rotation is intentionally absent
_I2P_CURL_PROXY: str = os.environ.get("I2P_SOCKS_PROXY_URL", "socks5h://127.0.0.1:4447")

# Tor-specific circuit tracking for curl_cffi Tor fetcher
_tor_curl_request_count: int = 0

# --- JA3 / TLS fingerprint rotation pool (Sprint F265A) ---------------------------
# Cycle through distinct browser-family TLS profiles so the JA3 fingerprint
# varies across sequential requests — defeats naïve per-host fingerprint
# correlation. All entries are valid `curl_cffi` `tls_impersonate` identifiers
# (verified against curl_cffi >= 0.7.x; uses native rustls-ffi impersonation).
#
# Coverage: 2 × Chrome, 2 × Safari, 2 × Firefox — satisfies the "≥3 distinct
# browser families" contract enforced by tests/test_f265a_transport_audit.py
# TestJA3ProfileCycling.test_pool_contains_at_least_three_browser_families.
_JA3_ROTATION_POOL: list[str] = [
    "chrome124",    # Chromium 124, May 2024
    "chrome120",    # Chromium 120, Dec 2023
    "safari17_0",   # Safari 17.0 (macOS Sonoma)
    "safari15_3",   # Safari 15.3 (older Apple stack)
    "firefox120",   # Firefox 120 ESR lineage
    "firefox117",   # Firefox 117 ESR
]

# Bounded, thread-safe round-robin iterator. The lock is required because
# `itertools.cycle.__next__` is not strictly atomic across threads on
# CPython 3.12+ (GIL release points around internal state). In practice
# the lock is uncontended — single-flight curl_cffi calls dominate.
_ja3_lock = threading.Lock()
_ja3_iter: itertools.cycle[str] = itertools.cycle(_JA3_ROTATION_POOL)

# Debug log gate — opt-in via env var. Read lazily at call time so tests can
# toggle either by patching `curl_cffi_fetch.HLEDAC_DEBUG_JA3` (direct) or by
# patching `os.environ` (process-wide). Defaults to OFF in production to keep
# the hot path zero-cost.
HLEDAC_DEBUG_JA3: bool = os.environ.get("HLEDAC_DEBUG_JA3", "0") == "1"


def next_ja3_profile() -> str:
    """Return the next JA3/TLS profile from the rotation pool (thread-safe).

    Round-robin over `_JA3_ROTATION_POOL`. Callers can override per-request
    by passing an explicit `profile=...` argument — this function is the
    default-fallback path used when no caller preference is given.
    """
    with _ja3_lock:
        return next(_ja3_iter)


def reset_ja3_cycle() -> None:
    """Reset the JA3 rotation cycle back to the start (for tests).

    Idempotent. Thread-safe — acquires the same lock as `next_ja3_profile`
    so concurrent tests cannot observe a torn iterator mid-reset.
    """
    global _ja3_iter
    with _ja3_lock:
        _ja3_iter = itertools.cycle(_JA3_ROTATION_POOL)


def _ja3_log(*, profile: str, url: str, used_profile: str) -> None:
    """Optional debug logger for JA3 profile selection (no-op when disabled).

    Reads `HLEDAC_DEBUG_JA3` at call time so it can be toggled by either
    `os.environ["HLEDAC_DEBUG_JA3"]=1` (process-level) or by patching the
    module attribute directly (per-test). Never raises — debug logger must
    stay on the zero-cost path in production.
    """
    try:
        # Resolve via module global so test-time `patch.object` works;
        # fall back to env for fresh subprocesses / multi-process harnesses.
        enabled = bool(HLEDAC_DEBUG_JA3) or os.environ.get("HLEDAC_DEBUG_JA3", "0") == "1"
        if not enabled:
            return
        logger.debug(
            "JA3 rotation: requested=%s used=%s url=%s",
            profile, used_profile, url,
        )
    except Exception:
        # Logger must never raise — production hot path.
        pass


def decode_curl_cffi_result(result: dict, *, max_bytes: int = 5 * 1024 * 1024) -> str | None:
    """F261 helper: decode raw bytes from a curl_cffi result dict to str.

    Uses the bounded encoding chain from utils.encoding (charset_normalizer →
    chardet → UTF-8 → latin-1). Honours the http_charset_hint produced by
    fetch_via_curl_cffi when present.

    Returns decoded str, or None when the dict has no content / is an error.
    Never raises — bounded by max_bytes.
    """
    if not isinstance(result, dict):
        return None
    if not result.get("success"):
        return None
    content = result.get("content", b"")
    if not content:
        return None
    try:
        return decode_response_bytes(
            content,
            http_charset=result.get("http_charset_hint"),
            max_bytes=max_bytes,
        )
    except Exception as e:
        logger.debug("decode_curl_cffi_result failed (fail-soft): %s", e)
        return None


async def fetch_via_tor_curl_cffi(
    url: str,
    headers: dict[str, str] | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_bytes: int = DEFAULT_MAX_BYTES,
    profile: str = "chrome124",
    tor_manager: Any = None,
    circuit_rotation_count: int = 50,
) -> dict[str, Any]:
    """
    Fetch URL via curl_cffi through Tor SOCKS5H proxy with circuit rotation.

    Args:
        url: URL to fetch (.onion only)
        headers: Optional HTTP headers
        timeout_s: Request timeout
        max_bytes: Max bytes to read
        profile: TLS profile for JA3 fingerprint
        tor_manager: TorManager instance for circuit rotation (NEWNYM via stem)
        circuit_rotation_count: Rotate circuit every N requests (default 50)
    """
    global _tor_curl_request_count
    _tor_curl_request_count += 1
    count = _tor_curl_request_count

    # Rotate circuit if threshold reached
    if tor_manager is not None and count >= circuit_rotation_count:
        _tor_curl_request_count = 0
        try:
            await tor_manager.rotate_circuit()
        except Exception as e:
            logger.warning(f"[TOR] circuit rotation failed: {e}")

    # Tor SOCKS5H proxy — DNS resolved by Tor, not localhost
    proxies = {"https": _TOR_CURL_PROXY}
    return await fetch_via_curl_cffi(
        url=url,
        headers=headers,
        timeout_s=timeout_s,
        max_bytes=max_bytes,
        profile=profile,
        proxies=proxies,
    )


async def fetch_via_i2p_curl_cffi(
    url: str,
    headers: dict[str, str] | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_bytes: int = DEFAULT_MAX_BYTES,
    profile: str = "chrome124",
) -> dict[str, Any]:
    """
    Fetch URL via curl_cffi through I2P SOCKS5H proxy.

    F260: I2P has no NEWNYM equivalent — circuit rotation is intentionally
    absent. I2P tunnels are inherently e2e and short-lived; explicit rotation
    would break the destination lookup. Documented invariant: this function
    takes no `tor_manager` / `circuit_rotation_count` parameters.

    Args:
        url: URL to fetch (.i2p or .b32.i2p only)
        headers: Optional HTTP headers
        timeout_s: Request timeout
        max_bytes: Max bytes to read
        profile: TLS profile for JA3 fingerprint
    """
    proxies = {"https": _I2P_CURL_PROXY}
    return await fetch_via_curl_cffi(
        url=url,
        headers=headers,
        timeout_s=timeout_s,
        max_bytes=max_bytes,
        profile=profile,
        proxies=proxies,
    )


async def fetch_via_curl_cffi(
    url: str,
    headers: dict[str, str] | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_bytes: int = DEFAULT_MAX_BYTES,
    profile: str = "chrome124",
    proxies: dict[str, str] | None = None,
    http_version: Any = None,
) -> dict[str, Any]:
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
        # Log the resolved JA3 profile (requested vs actually used) so operators
        # can verify fingerprint rotation in production via HLEDAC_DEBUG_JA3=1.
        _ja3_log(profile=profile, url=url, used_profile=used_profile)
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
        kwargs = {"headers": headers, "timeout": timeout_s}
        if proxies:
            kwargs["proxies"] = proxies
        if http_version is not None:
            # F260+: opportunistic HTTP/3 (QUIC) upgrade.
            # Caller passes HttpVersion.v3 from curl_cffi.requests when server
            # advertised h3 via Alt-Svc. Fail-soft: any error propagates as
            # normal fetch error and caller falls back to HTTP/1.1/HTTP/2.
            kwargs["http_version"] = http_version
        response = await session.get(url, **kwargs)

        # Read body with hard cap at max_bytes
        # Uses shared body_limiter helper (same pattern: bytearray + del cap)
        chunks = response.iter_content(chunk_size=65536)
        content_bytes, _truncated = await read_body_with_cap(chunks, max_bytes)
        if _truncated:
            logger.debug(f"curl_cffi body truncated to {max_bytes} bytes for {url}")

        content_type = ""
        if response.headers:
            content_type = response.headers.get("content-type", "")

        # F261: STORAGE-FIX-4 wiring — extract charset hint for downstream decoding
        # (decode_response_bytes uses this as priority 0 before charset_normalizer).
        http_charset_hint = parse_charset_from_content_type(content_type)

        return {
            "url": url,
            "final_url": url,
            "content": bytes(content_bytes),  # bytearray → bytes for response contract
            "status_code": response.status_code,
            "content_type": content_type,
            "http_charset_hint": http_charset_hint,  # F261: STORAGE-FIX-4
            "headers": dict(response.headers) if response.headers else {},
            "success": True,
            "error": None,
            "selected_transport": "curl_cffi",
            "tls_impersonate": used_profile,
            "failure_stage": None,
            "network_error_kind": None,
        }

    except TimeoutError:
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
) -> dict[str, Any]:
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


# ---------------------------------------------------------------------------
# F265B: conditional cache wrapper for the curl_cffi stealth lane.
#
# Closes the F261 gap: hishel covers the httpx path, but every fetch
# through fetch_via_curl_cffi bypassed the cache entirely. SERP pages
# (Bing, DDG, Google Scholar) that we fetched 30 s ago are almost
# identical to the live page; paying 1-3 s RTT for them is wasteful.
#
# This wrapper:
#   1. Looks up the URL in conditional_cache.
#   2. If hit + fresh: sends the request with If-None-Match /
#      If-Modified-Since headers.
#   3. On 200: updates the cache, returns the body.
#   4. On 304: returns the cached body (0 bytes transferred) — this
#      is the fast path that saves 1-3 s per cache hit.
#   5. On error: returns the result normally (cache is best-effort).
#
# Telemetry is recorded in conditional_cache.get_stats(); the sprint
# dashboard surfaces hit_rate, 304_count, store_count.
# ---------------------------------------------------------------------------
async def fetch_via_curl_cffi_cached(
    url: str,
    headers: dict[str, str] | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_bytes: int = DEFAULT_MAX_BYTES,
    profile: str = "chrome124",
    proxies: dict[str, str] | None = None,
    http_version: Any = None,
    *,
    ttl_s: int = 3600,
    _force_refresh: bool = False,
) -> dict[str, Any]:
    """fetch_via_curl_cffi with conditional-GET (304) shortcut.

    Args:
        url: Same as fetch_via_curl_cffi.
        headers: Caller headers; ``If-None-Match`` / ``If-Modified-Since``
            from the cache are MERGED in (caller wins on conflict).
        timeout_s: Same as fetch_via_curl_cffi.
        max_bytes: Same as fetch_via_curl_cffi.
        profile: TLS profile (passed through).
        proxies: Same as fetch_via_curl_cffi.
        http_version: HttpVersion.v3 from http3_lane (passed through).
        ttl_s: Cache freshness window in seconds. Default 1h.
        _force_refresh: Skip the cache entirely (always send no
            validators). For tests and one-off live fetches.

    Returns:
        Same FetchResult dict as fetch_via_curl_cffi. On 304, the
        ``content`` field is the cached bytes and the result carries
        ``conditional_304=True`` so callers can log the hit.
    """
    # Lazy import to keep the module-load footprint minimal.
    from .conditional_cache import (
        conditional_headers_for,
        lookup as _cc_lookup,
        record_conditional_result as _cc_record,
        store as _cc_store,
    )

    merged_headers: dict[str, str] = dict(headers) if headers else {}
    sent_conditional = False

    if not _force_refresh:
        try:
            cache_headers = conditional_headers_for(url, ttl_s=ttl_s)
            if cache_headers:
                # Caller's explicit headers win on conflict (e.g. an
                # operator forcing no-cache should not be silently
                # overridden by the cache layer).
                for k, v in cache_headers.items():
                    if k not in merged_headers:
                        merged_headers[k] = v
                sent_conditional = True
        except Exception:  # noqa: BLE001
            # Cache lookup failed — fall through to live fetch.
            pass

    result = await fetch_via_curl_cffi(
        url=url,
        headers=merged_headers,
        timeout_s=timeout_s,
        max_bytes=max_bytes,
        profile=profile,
        proxies=proxies,
        http_version=http_version,
    )

    if not result.get("success"):
        # Don't pollute telemetry on failures. The 304 path requires
        # the request to actually reach the server.
        return result

    status = int(result.get("status_code", 0) or 0)
    if sent_conditional:
        try:
            _cc_record(url, sent=True, response_status=status)
        except Exception:  # noqa: BLE001
            pass

    if status == 304:
        # 304: serve the cached body, do not touch the network body.
        try:
            entry = _cc_lookup(url)
            if entry is not None and entry.body:
                result["content"] = bytes(entry.body)
                result["final_url"] = url
                # Surface 304 in the result so callers can log/distinguish.
                result["conditional_304"] = True
                return result
        except Exception:  # noqa: BLE001
            pass
        # If the cache lookup failed post-304 (race / corruption), fall
        # through and return whatever the server sent. 304 should never
        # carry a body, so the body is empty anyway.
        result["conditional_304"] = True
        return result

    if 200 <= status < 300:
        # 2xx: persist for next time. Extract etag + last_modified +
        # content-type from response headers.
        try:
            resp_headers = result.get("headers") or {}
            etag = ""
            last_modified = ""
            content_type = ""
            for k, v in resp_headers.items():
                kl = k.lower()
                if kl == "etag":
                    etag = str(v)
                elif kl == "last-modified":
                    last_modified = str(v)
                elif kl == "content-type":
                    content_type = str(v)
            body_bytes = result.get("content", b"") or b""
            sha_hex = ""
            try:
                import hashlib

                sha_hex = hashlib.sha256(body_bytes).hexdigest()
            except Exception:  # noqa: BLE001
                pass
            _cc_store(
                url,
                etag=etag,
                last_modified=last_modified,
                body=body_bytes,
                sha256=sha_hex,
                status_code=status,
                content_type=content_type,
            )
        except Exception:  # noqa: BLE001
            # Cache store failed — the live response is still returned.
            pass
    return result

